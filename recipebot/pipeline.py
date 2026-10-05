"""One run of the bot: build the brief, call the model, validate, render, send, record.

Only configuration errors (a missing token or key) raise to the caller, because they need the
owner rather than another attempt tomorrow. Every other failure ends in a logged run row and,
when an admin chat is configured, a short Telegram message, so a silent day does not go unnoticed.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable
from zoneinfo import ZoneInfo

from recipebot.brief import BriefInputs, build_brief
from recipebot.candidates import format_candidate_pages, gather_candidates
from recipebot.categories import get_category, is_known
from recipebot.config import ConfigError, Settings
from recipebot.history import History
from recipebot.llm import LLMClient, LLMRefusal, make_client
from recipebot.parsing import ParseFailure, parse_reply
from recipebot.prompts import load_brief_template, load_system_prompt
from recipebot import telegram as tg
from recipebot.render import RenderError, render_alert, render_recipe
from recipebot.rotation import Rotation, Slot, daily_plan
from recipebot.telegram import TelegramClient, TelegramError
from recipebot.validate import main_ingredient, validate_reply
from recipebot.vault import Vault
from recipebot.web import Fetcher

log = logging.getLogger(__name__)

PAUSE_BETWEEN_RECIPES = 2.0
DAILY_PAUSE = 14400.0  # daily batch: 4 h between the 3 posts (08:00, 12:00, 16:00)
PAUSE_BETWEEN_MESSAGES = 1.0
MAX_MODEL_ATTEMPTS = 2

DIAGNOSE_SYSTEM = (
    "You triage failures of RecipeBot, a Python bot in a Docker container on a Synology NAS. Once a day "
    "it asks `claude -p` (signed in with CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`) for a recipe as "
    "JSON, validates it, and posts it to Telegram. The owner reads your reply on a phone and cannot run a "
    "debugger. Given the failure alert, reply in at most 4 short plain-text lines: the likely cause, then the "
    "exact fix (a command, an .env variable, or 'transient, it retries tomorrow'). No markdown, no preamble."
)

TRUNCATED_MESSAGE = (
    "The reply was cut off before the JSON object ended (the output limit was reached). "
    "Return the same structure with shorter text, or fewer recipes, so the whole object fits."
)


@dataclass
class RunReport:
    run_id: str
    category: str
    theme: str | None
    status: str = "pending"
    posted: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    """Things the owner must look at, such as a post that could not be recorded."""
    notes: str = ""
    detail: str = ""
    messages: list[list[str]] = field(default_factory=list)
    model_calls: int = 0

    @property
    def ok(self) -> bool:
        return self.status in {"posted", "dry_run"}

    def alert_text(self, requested: int) -> str:
        if self.posted:
            head = f"RecipeBot posted {len(self.posted)} of {requested} recipe(s), then hit a problem."
        else:
            head = "RecipeBot posted nothing."
        return head + "\n" + self.summary()

    def summary(self) -> str:
        lines = [f"run {self.run_id} [{self.category}] status={self.status}"]
        if self.posted:
            lines.append("posted: " + "; ".join(self.posted))
        if self.rejected:
            lines.append("dropped: " + "; ".join(self.rejected))
        if self.problems:
            lines.append("needs attention: " + "; ".join(self.problems))
        if self.notes:
            lines.append("model notes: " + self.notes)
        if self.detail:
            lines.append(self.detail)
        return "\n".join(lines)


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        *,
        llm: LLMClient | None = None,
        telegram: TelegramClient | None = None,
        history: History | None = None,
        fetcher: Fetcher | None = None,
        rotation: Rotation | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
        rng: random.Random | None = None,
    ):
        self.settings = settings
        self._llm = llm
        self._telegram = telegram
        self._history = history
        self.fetcher = fetcher or Fetcher(timeout=settings.fetch_timeout_seconds)
        self._rotation = rotation
        self.sleep = sleep
        self.tz = ZoneInfo(settings.timezone)
        self.now = now or (lambda: datetime.now(self.tz))
        self.rng = rng or random.Random()
        self.system_prompt = load_system_prompt(settings.prompts_dir)
        self.brief_template = load_brief_template(settings.prompts_dir)
        self.vault = Vault(settings.vault_dir, self.tz)

    # --- lazy collaborators ------------------------------------------------------------

    @property
    def history(self) -> History:
        if self._history is None:
            self._history = History(self.settings.db_path, tz=self.tz)
        return self._history

    @property
    def rotation(self) -> Rotation:
        """Reloaded from data/rotation.json on every access, so edits apply without a restart."""
        if self._rotation is not None:
            return self._rotation
        return Rotation.load(self.settings.rotation_path, self.settings.rotation_epoch)

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self.settings.require_llm()
            self._llm = make_client(self.settings)
        return self._llm

    @property
    def telegram(self) -> TelegramClient:
        if self._telegram is None:
            self.settings.require_telegram()
            self._telegram = TelegramClient(self.settings.telegram_bot_token or "")
        return self._telegram

    # --- helpers -----------------------------------------------------------------------

    def resolve_slot(self, day: date, category: str | None, theme: str | None) -> Slot:
        if category:
            if not is_known(category):
                raise ValueError(f"unknown category {category!r}")
            return Slot(category, theme if theme else None)
        scheduled = self.rotation.for_date(day).slot
        return Slot(scheduled.category, theme if theme else scheduled.theme)

    def notify_admin(self, blocks: list[str]) -> None:
        """Sends an alert card (blocks from render.render_alert) to the admin chat."""
        chat_id = self.settings.telegram_admin_chat_id
        if not chat_id or not self.settings.telegram_bot_token:
            return
        try:
            self.telegram.send_html(chat_id, blocks)
        except Exception as exc:  # an alert must never take the run down
            log.error("could not send admin alert: %s", exc)

    def _candidate_pages(self, category: str) -> str | None:
        if self.settings.source_mode != "candidates":
            return None
        pages = gather_candidates(
            self.settings.candidates_dir,
            category,
            self.fetcher,
            exclude_urls=self.history.sent_urls(),
            n=self.settings.candidate_pages,
            rng=self.rng,
        )
        if not pages:
            log.warning("no usable candidate pages for %s, letting the model search the web instead", category)
            return None
        log.info("handing the model %d candidate pages", len(pages))
        return format_candidate_pages(pages)

    def _ask_model(self, inputs: BriefInputs, web_search: bool, report: RunReport):
        """Calls the model, retrying once on bad JSON with the error appended to the brief.
        Returns the parsed reply, or None after two failures (the report explains why)."""
        previous_error = inputs.previous_error
        last_failure = ""
        for attempt in range(1, MAX_MODEL_ATTEMPTS + 1):
            inputs.previous_error = previous_error
            brief = build_brief(self.brief_template, inputs)
            log.info("model call %d/%d (web search %s)", attempt, MAX_MODEL_ATTEMPTS, "on" if web_search else "off")
            result = self.llm.complete(self.system_prompt, brief, web_search=web_search)
            report.model_calls += 1
            log.info(
                "model %s stop=%s in=%d out=%d cached=%d searches=%d",
                result.model, result.stop_reason, result.input_tokens, result.output_tokens,
                result.cache_read_tokens, result.web_searches,
            )
            if result.truncated:
                last_failure = "reply was cut off at the output limit"
                previous_error = TRUNCATED_MESSAGE
                log.warning("attempt %d: %s", attempt, last_failure)
                continue
            try:
                parsed = parse_reply(result.text, result.text_blocks)
            except ParseFailure as exc:
                last_failure = str(exc)
                previous_error = last_failure
                log.warning("attempt %d: bad JSON: %s", attempt, last_failure)
                continue
            report.warnings.extend(parsed.warnings)
            return parsed.reply
        report.status = "failed"
        report.detail = f"the model returned unusable JSON {MAX_MODEL_ATTEMPTS} times; last problem: {last_failure}"
        return None

    # --- the run -------------------------------------------------------------------------

    def diagnose(self, alert: str) -> str:
        """Ask the model (no tools, read only) for a likely cause and fix. When the model itself is the
        broken part, say so, since that is the most common reason for a silent day."""
        # ponytail: shares llm_timeout_seconds, so a hung claude -p can delay the alert by that much again.
        try:
            reply = self.llm.complete(DIAGNOSE_SYSTEM, alert, web_search=False).text.strip()
        except Exception as exc:  # noqa: BLE001 - the alert goes out regardless
            log.warning("auto-diagnosis failed: %s", exc)
            return (f"unavailable, the model call failed too ({str(exc)[:200]}). If claude -p is the problem, the "
                    "sign-in has probably expired: run `claude setup-token` and update CLAUDE_CODE_OAUTH_TOKEN in .env.")
        return reply[:800] or "the model returned nothing."

    def run(
        self,
        *,
        category: str | None = None,
        theme: str | None = None,
        count: int | None = None,
        servings: int | None = None,
        day: date | None = None,
        dry_run: bool = False,
        run_id: str | None = None,
        check_pages: bool = True,
        scheduled: bool = False,
        meal: str | None = None,
    ) -> RunReport:
        """One run. `day` is the rotation date. `scheduled` marks a run made by the daily loop;
        it is recorded against `day`, while a manual run is recorded against the actual local date,
        so a manual post counts as that day's post and `--date` rehearsals never block the loop."""
        # Configuration problems and a bad explicit category are the caller's to fix: raise them
        # before anything is recorded or paid for.
        if self._llm is None:
            self.settings.require_llm()
        if not dry_run and self._telegram is None:
            self.settings.require_telegram()
        if category and not is_known(category):
            raise ValueError(f"unknown category {category!r}")

        started = self.now()
        day = day or started.date()
        run_day = day if scheduled else started.date()
        count = count or self.settings.count
        servings = servings or self.settings.servings
        report = RunReport(run_id=run_id or f"{started:%Y%m%d-%H%M%S}", category=category or "unresolved", theme=theme)
        run_row = None
        try:
            if not dry_run:
                # Recorded first, so a failure in the rotation or the model still leaves a trace for the day.
                run_row = self.history.start_run(report.run_id, category, theme, started, run_day=run_day)
            slot = self.resolve_slot(day, category, theme)
            report.category, report.theme = slot.category, slot.theme
            if run_id is None:
                report.run_id = f"{started:%Y%m%d-%H%M%S}-{slot.category}"
            if run_row is not None:
                self.history.update_run(run_row, run_id=report.run_id, category=slot.category, theme=slot.theme)
            log.info(
                "starting run %s for %s: category=%s theme=%s count=%d servings=%d dry_run=%s scheduled=%s",
                report.run_id, day, slot.category, slot.theme_text, count, servings, dry_run, scheduled,
            )
            self._run_inner(report, slot, count=count, servings=servings, dry_run=dry_run, check_pages=check_pages, meal=meal)
        except ConfigError:
            raise
        except LLMRefusal as exc:
            report.status = "failed"
            report.detail = str(exc)
            log.error("run %s: %s", report.run_id, exc)
        except Exception as exc:  # noqa: BLE001 - the daily loop must survive anything
            report.status = "error"
            report.detail = f"{type(exc).__name__}: {exc}"
            log.exception("run %s crashed", report.run_id)
        # A BaseException (a stop signal) passes through: the row stays 'running', which the loop reports.

        if run_row is not None:
            try:
                self.history.finish_run(run_row, report.status, len(report.posted), report.summary())
            except Exception:  # noqa: BLE001
                log.exception("could not record the run")
        if not dry_run and not report.ok:
            self.vault.log("❌", f"run failed ({report.status})", f"{report.category} · {report.detail[:200]}", when=self.now())  # ❌
            alert = report.alert_text(count)
            diagnosis = self.diagnose(alert)
            details = [f"\U0001f194 <code>{tg.esc(report.run_id)}</code>"]  # 🆔
            if report.detail:
                details.append(f"\U0001f4ac {tg.esc(report.detail[:300])}")  # 💬
            details.append(f"\U0001fa7a <b>Diagnosis</b>\n{tg.esc(diagnosis)}")  # 🩺
            label = get_category(report.category).label if is_known(report.category) else report.category
            self.notify_admin(render_alert(
                "failed",
                f"posted {len(report.posted)} of {count}" if report.posted else "posted nothing",
                label or "run",
                f"status {report.status}",
                details,
                background=alert,
            ))
        log.info(report.summary())
        return report

    def run_daily(self, day: date) -> list[RunReport]:
        """The scheduled day: one run per recipe in rotation.daily_plan, so one bad recipe or a
        truncated reply costs a single post, and each run sees the ones posted before it."""
        reports = []
        plan = daily_plan(day)
        self.vault.log("🚀", "daily run started", f"{day.isoformat()} · {len(plan)} recipes planned", when=self.now())  # 🚀
        for i, (meal, slot) in enumerate(plan):
            if i:
                self.sleep(DAILY_PAUSE)
            reports.append(self.run(category=slot.category, theme=slot.theme, count=1, day=day, scheduled=True, meal=meal))
        posted = sum(len(r.posted) for r in reports)
        failed = sum(not r.ok for r in reports)
        self.vault.log("🏁", "daily run finished", f"{posted} of {len(plan)} posted, {failed} failed", when=self.now())  # 🏁
        return reports

    def _run_inner(self, report: RunReport, slot: Slot, *, count: int, servings: int, dry_run: bool, check_pages: bool, meal: str | None = None) -> None:
        settings = self.settings
        category = get_category(slot.category)

        inputs = BriefInputs(
            run_id=report.run_id,
            category=category.key,
            count=count,
            servings=servings,
            theme=slot.theme,
            recent_mains=self.history.recent_mains(settings.recent_mains),
            already_sent=self.history.already_sent_lines(days=settings.history_days, max_lines=settings.history_max_lines),
            candidate_pages=self._candidate_pages(category.key),
            recent_menu=self.vault.recent_menu(self.now()),
        )
        web_search = inputs.candidate_pages is None

        for attempt in range(2):
            reply = self._ask_model(inputs, web_search, report)
            if reply is None:
                return
            report.notes = reply.run.notes or ""
            if reply.is_error:
                report.status = "model_error"
                report.detail = f"model returned an error: {reply.error}"
                log.warning("run %s: %s (%s)", report.run_id, report.detail, report.notes)
                return
            if reply.run.count_returned != len(reply.recipes):
                report.warnings.append(f"run.count_returned={reply.run.count_returned} but {len(reply.recipes)} recipes were returned")

            validation = validate_reply(
                reply, category.key, history=self.history, fetcher=self.fetcher, check_pages=check_pages
            )
            if validation.accepted or not validation.rejected or attempt:
                break
            # Every recipe broke a rule: ask once more, naming what was wrong, so the model picks another dish.
            report.rejected.extend(str(r) for r in validation.rejected)
            inputs.previous_error = "Every recipe was rejected, choose a different dish that fits the rules: " + "; ".join(
                str(r) for r in validation.rejected)
            log.info("run %s: all recipes rejected, asking again", report.run_id)
        report.rejected.extend(str(r) for r in validation.rejected)
        report.warnings.extend(validation.warnings)
        for rejection in validation.rejected:
            log.warning("dropped %s", rejection)
        for warning in report.warnings:
            log.info("note: %s", warning)

        accepted = validation.accepted
        if len(accepted) > count:
            extra = accepted[count:]
            accepted = accepted[:count]
            report.warnings.append("model returned more recipes than requested; not posting: " + "; ".join(r.title for r in extra))
        if not accepted:
            report.status = "nothing_posted"
            report.detail = "no recipe survived validation" if reply.recipes else "the model returned no recipes"
            return

        incomplete = 0
        for i, recipe in enumerate(accepted):
            if meal and meal not in recipe.meals:
                recipe.meals = [meal] + recipe.meals[:1]  # the slot's meal tag always leads
            try:
                messages = render_recipe(recipe, category)
            except RenderError as exc:
                report.rejected.append(f"{recipe.title}: {exc}")
                log.warning("dropped %s: %s", recipe.title, exc)
                continue
            report.messages.append(messages)
            if dry_run:
                report.posted.append(recipe.title)
                continue
            if i > 0:
                self.sleep(PAUSE_BETWEEN_RECIPES)
            delivered = 0
            try:
                for j, message in enumerate(messages):
                    if j > 0:
                        self.sleep(PAUSE_BETWEEN_MESSAGES)
                    # link preview on for the first message so the recipe photo shows
                    self.telegram.send_message(settings.telegram_chat_id or "", message, disable_preview=j > 0)
                    delivered += 1
            except TelegramError as exc:
                log.error("telegram failed for %s after %d of %d message(s): %s", recipe.title, delivered, len(messages), exc)
                if delivered or exc.ambiguous:
                    # Part of it, or all of it, may be in the channel: record it so it is never posted again.
                    report.posted.append(f"{recipe.title} (incomplete: {exc})")
                    incomplete += 1
                    if not self._record_sent(recipe, report):
                        incomplete += 1
                    self.vault.posted(recipe, category, meal, report.run_id, self.now())
                else:
                    report.rejected.append(f"{recipe.title}: telegram: {exc}")
                continue
            report.posted.append(recipe.title)
            log.info("posted %s", recipe.title)
            if not self._record_sent(recipe, report):
                incomplete += 1
            self.vault.posted(recipe, category, meal, report.run_id, self.now())

        if not report.posted:
            report.status = "nothing_posted"
            report.detail = "every recipe failed to render or to send"
        elif dry_run:
            report.status = "dry_run"
        elif incomplete or len(report.posted) < len(accepted):
            report.status = "partial"
            report.detail = f"{len(report.posted)} of {len(accepted)} recipe(s) posted, {incomplete} incomplete"
        else:
            report.status = "posted"

    def _record_sent(self, recipe, report: RunReport) -> bool:
        """Writes the posted recipe to the history table. Returns False (and says so in the report)
        when the write fails, so the owner knows the channel and the table disagree."""
        try:
            self.history.add_sent(recipe, main_ingredient=main_ingredient(recipe), run_id=report.run_id, sent_at=self.now())
            return True
        except Exception as exc:  # noqa: BLE001
            log.exception("posted %s but could not record it", recipe.title)
            report.problems.append(f"posted but NOT recorded in history: {recipe.title} {recipe.source.url} ({type(exc).__name__}: {exc})")
            return False

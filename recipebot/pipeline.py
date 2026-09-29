"""One run of the bot: build the brief, call the model, validate, render, send, record.

Nothing in here raises to the caller. Every failure ends in a logged run row and, when an admin
chat is configured, a short Telegram message, so a silent day does not go unnoticed.
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
from recipebot.config import Settings
from recipebot.history import History
from recipebot.llm import AnthropicClient, LLMClient, LLMRefusal
from recipebot.parsing import ParseFailure, parse_reply
from recipebot.prompts import load_brief_template, load_system_prompt
from recipebot.render import RenderError, render_recipe
from recipebot.rotation import Rotation, Slot
from recipebot.telegram import TelegramClient
from recipebot.validate import main_ingredient, validate_reply
from recipebot.web import Fetcher

log = logging.getLogger(__name__)

PAUSE_BETWEEN_RECIPES = 2.0
PAUSE_BETWEEN_MESSAGES = 1.0
MAX_MODEL_ATTEMPTS = 2

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
    notes: str = ""
    detail: str = ""
    messages: list[list[str]] = field(default_factory=list)
    model_calls: int = 0

    @property
    def ok(self) -> bool:
        return self.status in {"posted", "dry_run"}

    def summary(self) -> str:
        lines = [f"run {self.run_id} [{self.category}] status={self.status}"]
        if self.posted:
            lines.append("posted: " + "; ".join(self.posted))
        if self.rejected:
            lines.append("dropped: " + "; ".join(self.rejected))
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

    # --- lazy collaborators ------------------------------------------------------------

    @property
    def history(self) -> History:
        if self._history is None:
            self._history = History(self.settings.db_path)
        return self._history

    @property
    def rotation(self) -> Rotation:
        if self._rotation is None:
            self._rotation = Rotation.load(self.settings.rotation_path, self.settings.rotation_epoch)
        return self._rotation

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self.settings.require_llm()
            self._llm = AnthropicClient(self.settings)
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

    def notify_admin(self, text: str) -> None:
        chat_id = self.settings.telegram_admin_chat_id
        if not chat_id or not self.settings.telegram_bot_token:
            return
        try:
            self.telegram.send_plain(chat_id, text)
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
        previous_error: str | None = None
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
    ) -> RunReport:
        started = self.now()
        day = day or started.date()
        slot = self.resolve_slot(day, category, theme)
        count = count or self.settings.count
        servings = servings or self.settings.servings
        run_id = run_id or f"{started:%Y%m%d-%H%M%S}-{slot.category}"
        report = RunReport(run_id=run_id, category=slot.category, theme=slot.theme)
        log.info("starting run %s: category=%s theme=%s count=%d servings=%d dry_run=%s", run_id, slot.category, slot.theme_text, count, servings, dry_run)

        run_row = None if dry_run else self.history.start_run(run_id, slot.category, slot.theme, started)
        try:
            self._run_inner(report, slot, count=count, servings=servings, dry_run=dry_run, check_pages=check_pages)
        except LLMRefusal as exc:
            report.status = "failed"
            report.detail = str(exc)
            log.error("run %s: %s", run_id, exc)
        except Exception as exc:  # noqa: BLE001 - the daily loop must survive anything
            report.status = "error"
            report.detail = f"{type(exc).__name__}: {exc}"
            log.exception("run %s crashed", run_id)

        if run_row is not None:
            try:
                self.history.finish_run(run_row, report.status, len(report.posted), report.summary())
            except Exception:  # noqa: BLE001
                log.exception("could not record the run")
        if not dry_run and not report.ok:
            self.notify_admin(f"RecipeBot posted nothing.\n{report.summary()}")
        log.info(report.summary())
        return report

    def _run_inner(self, report: RunReport, slot: Slot, *, count: int, servings: int, dry_run: bool, check_pages: bool) -> None:
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
        )
        web_search = inputs.candidate_pages is None

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

        for i, recipe in enumerate(accepted):
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
            self.telegram.send_messages(settings.telegram_chat_id or "", messages, pause=PAUSE_BETWEEN_MESSAGES)
            self.history.add_sent(recipe, main_ingredient=main_ingredient(recipe), run_id=report.run_id, sent_at=self.now())
            report.posted.append(recipe.title)
            log.info("posted %s", recipe.title)

        if not report.posted:
            report.status = "nothing_posted"
            report.detail = "every recipe failed to render"
        else:
            report.status = "dry_run" if dry_run else "posted"

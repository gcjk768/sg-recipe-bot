"""Obsidian vault: the bot's movement log and long-term memory (NAS vault standard).

Layout under VAULT_DIR (the NAS folder /volume1/James/Obsidian/SG Recipes):
* ``Activity/YYYY-MM-DD.md``: one line per event, ``- HH:MM emoji **what** · detail · [[entity]]`` (SGT)
* ``Recipes/<Title>.md``: one note per posted recipe, with an append-only ``## History``
* ``Home.md``: map of contents, this week's menu

Memory: ``recent_menu`` reads the posted lines back from the last 14 days of Activity notes, newest
first and capped, for the brief, so the model keeps variety beyond the SQLite dedupe.

Every public method is best-effort: an error is logged and swallowed, never raised, so the vault
can never cost a post or an alert. No secrets, tokens or prompts are written here.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from recipebot.categories import Category
from recipebot.models import Recipe
from recipebot.render import hashtags_line
from recipebot.textutil import format_qty, format_sgd, single_line

log = logging.getLogger(__name__)

MEMORY_DAYS = 14
MEMORY_CHARS = 4000
OWNER_UID = 1000  # James on the NAS, so he can edit what the root container writes
_BAD_NAME = re.compile(r'[\\/:*?"<>|#^\[\]]+')
_POSTED = re.compile(r"^- (\d\d:\d\d) \S+ \*\*posted (\w+)\*\* · (.+?) · (.*?) · \[\[(.+?)\]\]$")


def note_name(title: str) -> str:
    """A filename and wikilink safe note title."""
    return re.sub(r"\s+", " ", _BAD_NAME.sub(" ", single_line(title))).strip(" .")[:120] or "Untitled"


def _field(text: object) -> str:
    """One line, with the separator removed so the Activity line stays parseable."""
    return single_line(text).replace("·", "-").strip()


class Vault:
    def __init__(self, root: Path | str | None, tz):
        self.root = Path(root) if root else None
        self.tz = tz

    # --- low level -------------------------------------------------------------------

    def _own(self, path: Path, mode: int) -> None:
        try:
            os.chmod(path, mode)
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                os.chown(path, OWNER_UID, -1)
        except OSError:
            pass

    def _dir(self, name: str) -> Path:
        path = self.root / name
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            self._own(path, 0o775)
        return path

    def _write(self, path: Path, text: str) -> None:
        """Atomic (tmp + rename), then left editable by James."""
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        self._own(path, 0o664)

    # --- write -----------------------------------------------------------------------

    def log(self, emoji: str, what: str, detail: str = "", entity: str | None = None, when: datetime | None = None) -> None:
        """Appends one line to today's Activity note."""
        if self.root is None:
            return
        try:
            when = (when or datetime.now(self.tz)).astimezone(self.tz)
            parts = [f"- {when:%H:%M} {emoji} **{_field(what)}**"]
            if detail:
                parts.append(single_line(detail).strip())
            if entity:
                parts.append(f"[[{note_name(entity)}]]")
            path = self._dir("Activity") / f"{when:%Y-%m-%d}.md"
            new = not path.exists()
            with path.open("a", encoding="utf-8") as fh:
                if new:
                    fh.write(f"---\ntags: [log]\nupdated: {when:%Y-%m-%d}\n---\n# {when:%a %d %b %Y}\n\n")
                fh.write(" · ".join(parts) + "\n")
            if new:
                self._own(path, 0o664)
        except Exception as exc:  # noqa: BLE001 - best-effort
            log.warning("vault: could not log %r: %s", what, exc)

    def posted(self, recipe: Recipe, category: Category, meal: str | None, run_id: str, when: datetime) -> None:
        """A recipe went out: write its note, log it, refresh Home."""
        if self.root is None:
            return
        meal = meal or (recipe.meals[0] if recipe.meals else "recipe")
        name = note_name(recipe.title)
        try:
            when = when.astimezone(self.tz)
            path = self._dir("Recipes") / f"{name}.md"
            history = []
            if path.exists():
                old = path.read_text(encoding="utf-8")
                if "## History\n" in old:
                    history = [l for l in old.split("## History\n", 1)[1].splitlines() if l.strip()]
            history.append(f"- {when:%Y-%m-%d %H:%M} posted as {meal} (run {run_id})")
            self._write(path, recipe_note(recipe, category, meal, when, history))
        except Exception as exc:  # noqa: BLE001
            log.warning("vault: could not write recipe note %s: %s", name, exc)
        self.log("\U0001f37d", f"posted {meal}", f"{_field(recipe.title)} · {_field(recipe.cuisine) or '-'}", name, when)  # 🍽
        self.write_home(when)

    def write_home(self, now: datetime | None = None) -> None:
        """Home.md: what this vault is, and this week's menu (Monday to today)."""
        if self.root is None:
            return
        try:
            now = (now or datetime.now(self.tz)).astimezone(self.tz)
            monday = now.date() - timedelta(days=now.weekday())
            lines = [
                "---", "tags: [active]", f"updated: {now:%Y-%m-%d}", "---",
                "# SG Recipes",
                "",
                "Written by the SG Recipe Bot (15 recipes a day to James Channel, topic Recipe). "
                "`Activity/` holds one note per day (the movement log), `Recipes/` one note per posted recipe.",
                "",
                f"## This week's menu ({monday:%d %b} to {now:%d %b})",
            ]
            day = now.date()
            while day >= monday:
                rows = self._posted_on(day)
                if rows:
                    lines += ["", f"### {day:%a %d %b} · [[{day.isoformat()}]]"]
                    lines += [f"- {meal}: [[{note}]] ({cuisine})" for _t, meal, _title, cuisine, note in rows]
                day -= timedelta(days=1)
            self._write(self.root / "Home.md", "\n".join(lines) + "\n")
        except Exception as exc:  # noqa: BLE001
            log.warning("vault: could not write Home.md: %s", exc)

    # --- read (memory) -----------------------------------------------------------------

    def _posted_on(self, day: date) -> list[tuple[str, str, str, str, str]]:
        path = self.root / "Activity" / f"{day.isoformat()}.md"
        if not path.is_file():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            m = _POSTED.match(line)
            if m:
                rows.append(m.groups())
        return rows

    def recent_menu(self, now: datetime | None = None, days: int = MEMORY_DAYS, cap: int = MEMORY_CHARS) -> list[str]:
        """'YYYY-MM-DD meal: Title (cuisine)' for every recipe posted in the last `days` days,
        newest first, at most `cap` characters in total. [] when the vault is off or unreadable."""
        if self.root is None:
            return []
        try:
            now = (now or datetime.now(self.tz)).astimezone(self.tz)
            out: list[str] = []
            size = 0
            for back in range(days):
                day = now.date() - timedelta(days=back)
                for _t, meal, title, cuisine, _note in reversed(self._posted_on(day)):
                    line = f"{day.isoformat()} {meal}: {title} ({cuisine})"
                    if size + len(line) + 1 > cap:
                        return out
                    out.append(line)
                    size += len(line) + 1
            return out
        except Exception as exc:  # noqa: BLE001
            log.warning("vault: could not read recent menu: %s", exc)
            return []


def recipe_note(recipe: Recipe, category: Category, meal: str, when: datetime, history: list[str]) -> str:
    q = json.dumps  # JSON strings are valid YAML scalars, so titles with colons stay safe
    nutrition, cost = recipe.nutrition, recipe.cost
    front = [
        "---", "tags: [posted]", f"updated: {when:%Y-%m-%d}",
        f"title: {q(recipe.title, ensure_ascii=False)}",
        f"cuisine: {q(recipe.cuisine, ensure_ascii=False)}",
        f"category: {category.key}",
        f"meal: {meal}",
        f"posted: {when:%Y-%m-%d}",
        f"total_minutes: {recipe.total_minutes}",
        f"kcal: {nutrition.kcal if nutrition else 'null'}",
        f"cost_sgd: {cost.total_sgd if cost else 'null'}",
        f"link: {q(recipe.source.url)}",
        "---",
    ]
    title = recipe.title.strip() + (f" ({recipe.title_zh.strip()})" if recipe.title_zh else "")
    body = [
        f"# {title}",
        "",
        f"- **Cuisine:** {recipe.cuisine or '-'}",
        f"- **Meal:** {meal} · **Category:** {category.label}",
        f"- **Time:** {recipe.total_minutes} min ({recipe.prep_minutes} prep + {recipe.cook_minutes} cook) · {recipe.difficulty} · serves {recipe.servings}",
        f"- **Nutrition:** {nutrition.kcal} kcal per serving" if nutrition else "- **Nutrition:** -",
        f"- **Cost:** {format_sgd(cost.total_sgd)}" + (f" ({format_sgd(cost.per_serving_sgd)} each)" if cost.per_serving_sgd else "")
        if cost else "- **Cost:** -",
        f"- **Link:** [{recipe.source.site or 'Recipe'}]({recipe.source.url})",
        f"- **Hashtags:** {hashtags_line(recipe, category)}",
        f"- **Posted:** {when:%Y-%m-%d %H:%M} SGT · [[{when:%Y-%m-%d}]]",
    ]
    if recipe.why_it_fits:
        body += ["", "## Why it fits", recipe.why_it_fits.strip()]
    body += ["", "## Ingredients"]
    for i in recipe.ingredients:
        line = " ".join(p for p in (format_qty(i.qty), i.unit, i.item) if p)
        body.append(f"- {line}" + (f", {i.note.strip()}" if i.note and i.note.strip() else ""))
    body += ["", "## Steps"] + [f"{n}. {single_line(s)}" for n, s in enumerate(recipe.steps, start=1)]
    if recipe.tips:
        body += ["", "## Tips"] + [f"- {single_line(t)}" for t in recipe.tips]
    if recipe.storage:
        body += ["", "## Storage", recipe.storage.strip()]
    body += ["", "## History", *history]
    return "\n".join(front + body) + "\n"

"""SQLite history of sent recipes and run logs.

Table sent_recipes mirrors the prompt pack: id, sent_at, category, title, url, main_ingredient,
recipe_json, plus normalised title and url columns for the duplicate checks.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from recipebot.models import Recipe
from recipebot.textutil import normalise_title, normalise_url, single_line

SCHEMA = """
CREATE TABLE IF NOT EXISTS sent_recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    title_norm TEXT NOT NULL,
    url TEXT NOT NULL,
    url_norm TEXT NOT NULL,
    main_ingredient TEXT,
    recipe_json TEXT NOT NULL,
    run_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_sent_url_norm ON sent_recipes(url_norm);
CREATE INDEX IF NOT EXISTS idx_sent_title_norm ON sent_recipes(title_norm);
CREATE INDEX IF NOT EXISTS idx_sent_at ON sent_recipes(sent_at);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    category TEXT,
    theme TEXT,
    status TEXT NOT NULL,
    posted INTEGER NOT NULL DEFAULT 0,
    detail TEXT,
    run_day TEXT
);
"""

UNFINISHED = "running"
DRY_RUN = "dry_run"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class SentRecipe:
    id: int
    sent_at: str
    category: str
    title: str
    url: str
    main_ingredient: str | None
    run_id: str | None


@dataclass
class RunLog:
    id: int
    run_id: str
    started_at: str
    finished_at: str | None
    category: str | None
    theme: str | None
    status: str
    posted: int
    detail: str | None
    run_day: str | None = None

    @property
    def unfinished(self) -> bool:
        return self.status == UNFINISHED


class History:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Brings a database created by an older version up to date."""
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(runs)").fetchall()}
        if "run_day" not in columns:
            self.conn.execute("ALTER TABLE runs ADD COLUMN run_day TEXT")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_day ON runs(run_day)")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "History":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- sent recipes -----------------------------------------------------------------

    def add_sent(
        self,
        recipe: Recipe,
        *,
        main_ingredient: str | None,
        run_id: str | None,
        sent_at: datetime | None = None,
    ) -> int:
        sent_at = sent_at or utcnow()
        cur = self.conn.execute(
            """INSERT INTO sent_recipes (sent_at, category, title, title_norm, url, url_norm, main_ingredient, recipe_json, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _iso(sent_at),
                recipe.category,
                recipe.title,
                normalise_title(recipe.title),
                recipe.source.url,
                normalise_url(recipe.source.url),
                main_ingredient,
                recipe.model_dump_json(),
                run_id,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def has_url(self, url: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM sent_recipes WHERE url_norm = ? LIMIT 1", (normalise_url(url),)).fetchone()
        return row is not None

    def has_title(self, title: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM sent_recipes WHERE title_norm = ? LIMIT 1", (normalise_title(title),)).fetchone()
        return row is not None

    def already_sent_lines(self, *, days: int = 90, max_lines: int = 150, now: datetime | None = None) -> list[str]:
        """'title | url' for the last `days` days, newest first, capped at max_lines."""
        now = now or utcnow()
        since = _iso(now - timedelta(days=days))
        rows = self.conn.execute(
            "SELECT title, url FROM sent_recipes WHERE sent_at >= ? ORDER BY sent_at DESC, id DESC LIMIT ?",
            (since, max_lines),
        ).fetchall()
        return [f"{single_line(r['title'])} | {single_line(r['url'])}" for r in rows]

    def recent_mains(self, n: int = 7) -> list[str]:
        rows = self.conn.execute(
            "SELECT main_ingredient FROM sent_recipes WHERE main_ingredient IS NOT NULL AND main_ingredient != '' ORDER BY sent_at DESC, id DESC LIMIT ?",
            (n,),
        ).fetchall()
        seen: list[str] = []
        for r in rows:
            m = r["main_ingredient"]
            if m not in seen:
                seen.append(m)
        return seen

    def recent_sent(self, n: int = 20) -> list[SentRecipe]:
        rows = self.conn.execute(
            "SELECT id, sent_at, category, title, url, main_ingredient, run_id FROM sent_recipes ORDER BY sent_at DESC, id DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [SentRecipe(**dict(r)) for r in rows]

    def sent_urls(self) -> set[str]:
        rows = self.conn.execute("SELECT url_norm FROM sent_recipes").fetchall()
        return {r["url_norm"] for r in rows}

    def sent_recipe_json(self, row_id: int) -> dict | None:
        row = self.conn.execute("SELECT recipe_json FROM sent_recipes WHERE id = ?", (row_id,)).fetchone()
        return json.loads(row["recipe_json"]) if row else None

    # --- runs -------------------------------------------------------------------------

    def start_run(
        self,
        run_id: str,
        category: str | None,
        theme: str | None,
        started_at: datetime | None = None,
        run_day: date | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (run_id, started_at, category, theme, status, run_day) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, _iso(started_at or utcnow()), category, theme, UNFINISHED, run_day.isoformat() if run_day else None),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def runs_for_day(self, day: date, *, include_dry_runs: bool = False) -> list[RunLog]:
        """Runs recorded for a rotation date (the local date the post was scheduled for)."""
        rows = self.conn.execute(
            "SELECT id, run_id, started_at, finished_at, category, theme, status, posted, detail, run_day FROM runs WHERE run_day = ? ORDER BY id",
            (day.isoformat(),),
        ).fetchall()
        runs = [RunLog(**dict(r)) for r in rows]
        if not include_dry_runs:
            runs = [r for r in runs if r.status != DRY_RUN]
        return runs

    def finish_run(self, row_id: int, status: str, posted: int, detail: str | None, finished_at: datetime | None = None) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at = ?, status = ?, posted = ?, detail = ? WHERE id = ?",
            (_iso(finished_at or utcnow()), status, posted, detail, row_id),
        )
        self.conn.commit()

    def recent_runs(self, n: int = 20) -> list[RunLog]:
        rows = self.conn.execute(
            "SELECT id, run_id, started_at, finished_at, category, theme, status, posted, detail, run_day FROM runs ORDER BY id DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [RunLog(**dict(r)) for r in rows]

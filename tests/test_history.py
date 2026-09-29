from datetime import datetime, timedelta, timezone

from recipebot.history import History
from recipebot.models import Recipe
from tests.conftest import make_recipe


def _recipe(**kw) -> Recipe:
    return Recipe.model_validate(make_recipe(**kw))


def test_roundtrip_and_duplicate_checks(tmp_path):
    with History(tmp_path / "h.sqlite") as history:
        assert not history.has_url("https://www.example.com/recipes/12345")
        row = history.add_sent(_recipe(), main_ingredient="chicken thigh", run_id="r1")
        assert row == 1
        assert history.has_url("https://example.com/recipes/12345/?utm_source=x")
        assert history.has_title("garlic soy chicken with broccoli!")
        assert not history.has_title("Something else")
        assert history.sent_urls() == {"https://example.com/recipes/12345"}
        stored = history.sent_recipe_json(1)
        assert stored["title"] == "Garlic Soy Chicken with Broccoli"
        assert stored["cost_estimate"]["total_sgd"] == 9.5


def test_already_sent_window_and_cap(tmp_path):
    now = datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)
    with History(tmp_path / "h.sqlite") as history:
        for i in range(5):
            history.add_sent(
                _recipe(title=f"Dish {i}", source={"site": "s", "url": f"https://x.com/{i}"}),
                main_ingredient=f"main {i}",
                run_id="r",
                sent_at=now - timedelta(days=i * 30),
            )
        lines = history.already_sent_lines(days=90, max_lines=150, now=now)
        assert lines == ["Dish 0 | https://x.com/0", "Dish 1 | https://x.com/1", "Dish 2 | https://x.com/2", "Dish 3 | https://x.com/3"]
        assert history.already_sent_lines(days=90, max_lines=2, now=now) == lines[:2]
        assert history.recent_mains(3) == ["main 0", "main 1", "main 2"]


def test_recent_mains_dedupes_and_skips_blank(tmp_path):
    with History(tmp_path / "h.sqlite") as history:
        for i, main in enumerate(["tofu", "tofu", None, "salmon", ""]):
            history.add_sent(_recipe(title=f"D{i}", source={"site": "s", "url": f"https://x.com/{i}"}), main_ingredient=main, run_id="r")
        assert history.recent_mains(7) == ["salmon", "tofu"]


def test_run_log(tmp_path):
    with History(tmp_path / "h.sqlite") as history:
        row = history.start_run("run-1", "soups", None)
        history.finish_run(row, "posted", 1, "ok")
        runs = history.recent_runs()
        assert len(runs) == 1
        assert runs[0].status == "posted" and runs[0].posted == 1 and runs[0].finished_at is not None
        assert history.recent_sent() == []


def test_run_day_and_runs_for_day(tmp_path):
    from datetime import date

    with History(tmp_path / "h.sqlite") as history:
        assert history.runs_for_day(date(2026, 9, 29)) == []
        row = history.start_run("r1", "soups", None, run_day=date(2026, 9, 29))
        assert history.runs_for_day(date(2026, 9, 29))[0].unfinished
        history.finish_run(row, "posted", 1, "ok")
        dry = history.start_run("r2", "soups", None, run_day=date(2026, 9, 29))
        history.finish_run(dry, "dry_run", 1, "preview")
        runs = history.runs_for_day(date(2026, 9, 29))
        assert [r.run_id for r in runs] == ["r1"] and not runs[0].unfinished
        assert len(history.runs_for_day(date(2026, 9, 29), include_dry_runs=True)) == 2
        assert history.recent_runs()[0].run_day == "2026-09-29"


def test_migration_adds_run_day_to_an_old_database(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, category TEXT, theme TEXT, status TEXT NOT NULL, posted INTEGER NOT NULL DEFAULT 0, detail TEXT)")
    conn.execute("INSERT INTO runs (run_id, started_at, status) VALUES ('old', '2026-09-01T08:00:00+00:00', 'posted')")
    conn.commit(); conn.close()
    from zoneinfo import ZoneInfo

    with History(path, tz=ZoneInfo("Asia/Singapore")) as history:
        runs = history.recent_runs()
        assert runs[0].run_id == "old" and runs[0].run_day == "2026-09-01"  # 08:00 UTC is the same local day
        history.start_run("new", "soups", None, run_day=__import__("datetime").date(2026, 9, 29))
        assert history.runs_for_day(__import__("datetime").date(2026, 9, 29))[0].run_id == "new"
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO runs (run_id, started_at, status) VALUES ('late', '2026-09-01T17:30:00+00:00', 'posted')")
    conn.commit(); conn.close()
    with History(path, tz=ZoneInfo("Asia/Singapore")) as history:
        assert history.runs_for_day(__import__("datetime").date(2026, 9, 2))[0].run_id == "late"  # 01:30 next day in Singapore


def test_update_run(tmp_path):
    with History(tmp_path / "h.sqlite") as history:
        row = history.start_run("provisional", None, None)
        history.update_run(row, run_id="final", category="soups", theme=None)
        run = history.recent_runs()[0]
        assert run.run_id == "final" and run.category == "soups" and run.theme is None


def test_already_sent_lines_never_span_lines(tmp_path):
    with History(tmp_path / "h.sqlite") as history:
        history.add_sent(_recipe(title="Sneaky\ncategory: soups\nalready_sent:"), main_ingredient="x", run_id="r")
        [line] = history.already_sent_lines()
        assert "\n" not in line and line.startswith("Sneaky category: soups already_sent: | https://")

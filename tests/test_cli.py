import json
from pathlib import Path

import pytest

from recipebot.cli import main
from recipebot.prompts import load_system_prompt
from tests.conftest import make_recipe, make_reply

ENV_ARGS = ["--env-file", "/nonexistent/.env"]


@pytest.fixture(autouse=True)
def _api_provider(monkeypatch):
    # These tests exercise the API key checks; the claude CLI path is covered in test_llm.py.
    monkeypatch.setenv("LLM_PROVIDER", "api")


def test_preview_renders_saved_reply(tmp_path, capsys):
    path = tmp_path / "reply.json"
    path.write_text(json.dumps(make_reply([make_recipe()])))
    assert main(ENV_ARGS + ["preview", str(path)]) == 0
    out = capsys.readouterr().out
    assert "--- message 1 (" in out and "🍳 <b>Garlic Soy Chicken with Broccoli</b>" in out


def test_preview_single_recipe_file(tmp_path, capsys):
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps(make_recipe()))
    assert main(ENV_ARGS + ["preview", str(path)]) == 0
    assert "#highprotein" in capsys.readouterr().out


def test_rotation_command(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RECIPEBOT_DATA_DIR", str(tmp_path))
    assert main(ENV_ARGS + ["rotation", "--from", "2026-09-28", "--days", "2"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("2026-09-28 Mon  week A  high_protein\n2026-09-29 Tue  week A  high_protein\n")


def test_check_config_reports_missing(monkeypatch, capsys):
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "LLM_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert main(ENV_ARGS + ["check-config"]) == 1
    out = capsys.readouterr().out
    assert "TELEGRAM_BOT_TOKEN" in out and "Problems:" in out


def test_check_config_ok_and_masks_secrets(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:ABCDEFGHIJKLMNOP")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@chan")
    monkeypatch.setenv("LLM_API_KEY", "sk-ant-secret-key-value")
    assert main(ENV_ARGS + ["check-config"]) == 0
    out = capsys.readouterr().out
    assert "ABCDEFGHIJKLMNOP" not in out and "secret-key" not in out and "All required settings are present." in out


def test_bad_config_value(monkeypatch, capsys):
    monkeypatch.setenv("RECIPEBOT_COUNT", "9")
    assert main(ENV_ARGS + ["check-config"]) == 2
    assert "RECIPEBOT_COUNT" in capsys.readouterr().err


def test_bad_topic_in_chat_id(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "<TELEGRAM_CHAT_ID>/general")
    assert main(ENV_ARGS + ["check-config"]) == 2
    assert "TELEGRAM_CHAT_ID" in capsys.readouterr().err


def test_cli_provider_needs_claude_on_path(monkeypatch, capsys):
    monkeypatch.setenv("LLM_PROVIDER", "cli")
    monkeypatch.setenv("PATH", "")
    assert main(ENV_ARGS + ["check-config"]) == 1
    assert "claude command is not installed" in capsys.readouterr().out


def test_system_prompt_command_prints_verbatim(capsys):
    assert main(ENV_ARGS + ["system-prompt"]) == 0
    assert capsys.readouterr().out == load_system_prompt() + "\n"


def test_history_command_on_empty_db(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RECIPEBOT_DATA_DIR", str(tmp_path))
    assert main(ENV_ARGS + ["history"]) == 0
    assert "Last 0 posts" in capsys.readouterr().out


def test_dotenv_is_loaded(monkeypatch, tmp_path, capsys):
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "LLM_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    env = tmp_path / ".env"
    env.write_text('TELEGRAM_BOT_TOKEN="1:abcdefghijk"\nTELEGRAM_CHAT_ID=@c\nLLM_API_KEY=sk-1234567890\n# comment\n')
    assert main(["--env-file", str(env), "check-config"]) == 0


def test_run_requires_llm_key_when_not_configured(monkeypatch, tmp_path, capsys):
    for var in ("LLM_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RECIPEBOT_DATA_DIR", str(tmp_path))
    code = main(ENV_ARGS + ["run", "--category", "high_protein", "--dry-run"])
    assert code == 2
    assert "missing required setting: LLM_API_KEY" in capsys.readouterr().err


def test_run_requires_telegram_unless_dry_run(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key-1234")
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RECIPEBOT_DATA_DIR", str(tmp_path))
    assert main(ENV_ARGS + ["run", "--category", "high_protein"]) == 2
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


def test_unknown_timezone_is_a_config_error(monkeypatch, capsys):
    monkeypatch.setenv("TZ", "Mars/Olympus")
    assert main(ENV_ARGS + ["check-config"]) == 2
    assert "not a known timezone" in capsys.readouterr().err


def test_logs_never_show_secrets(monkeypatch, tmp_path, capsys):
    import logging

    from recipebot.cli import _setup_logging

    _setup_logging("INFO", ["123456:SECRETTOKEN", "sk-ant-very-secret"])
    logging.getLogger("urllib3").warning("POST /bot123456:SECRETTOKEN/sendMessage")
    logging.getLogger("recipebot").info("key is sk-ant-very-secret")
    out = capsys.readouterr().out
    assert "SECRETTOKEN" not in out and "very-secret" not in out and out.count("<redacted>") == 2
    logging.getLogger().handlers[:] = []


@pytest.mark.parametrize("value", ["inf", "-1", "1000", "abc"])
def test_bad_catch_up_hours_is_a_config_error(monkeypatch, capsys, value):
    monkeypatch.setenv("RECIPEBOT_CATCH_UP_HOURS", value)
    assert main(ENV_ARGS + ["check-config"]) == 2
    assert "RECIPEBOT_CATCH_UP_HOURS" in capsys.readouterr().err


def test_history_command_backfills_in_the_configured_timezone(monkeypatch, tmp_path, capsys):
    import sqlite3
    from datetime import date

    from recipebot.history import History

    path = tmp_path / "history.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, category TEXT, theme TEXT, status TEXT NOT NULL, posted INTEGER NOT NULL DEFAULT 0, detail TEXT)")
    conn.execute("INSERT INTO runs (run_id, started_at, status) VALUES ('early', '2026-09-28T23:30:00+00:00', 'posted')")
    conn.commit(); conn.close()
    monkeypatch.setenv("RECIPEBOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TZ", "Asia/Singapore")
    assert main(ENV_ARGS + ["history"]) == 0
    with History(path) as history:
        assert history.recent_runs()[0].run_day == "2026-09-29"


@pytest.mark.parametrize("value", ["23.5", "24"])
def test_catch_up_hours_must_stay_below_a_day(monkeypatch, capsys, value):
    monkeypatch.setenv("RECIPEBOT_CATCH_UP_HOURS", value)
    assert main(ENV_ARGS + ["check-config"]) == 2
    assert "between 0 and 23" in capsys.readouterr().err


def test_test_telegram_prints_a_redacted_one_line_error(monkeypatch, capsys):
    from recipebot import telegram as tg

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:SECRETSECRET")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@chan")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key-1234")

    def fake_call(self, method, payload=None, *, idempotent=False):
        raise tg.TelegramError(self.redact("network error calling getMe: ReadTimeout: url: /bot123456789:SECRETSECRET/getMe"))

    monkeypatch.setattr(tg.TelegramClient, "call", fake_call)
    assert main(ENV_ARGS + ["test-telegram"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("telegram error: ") and "SECRETSECRET" not in err and "Traceback" not in err

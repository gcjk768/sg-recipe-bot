"""Command line entry point: recipebot run | loop | preview | rotation | history | test-telegram | check-config | system-prompt."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from recipebot import __version__
from recipebot.categories import CATEGORIES, get_category
from recipebot.config import ConfigError, Settings, load_dotenv, load_settings
from recipebot.models import Recipe
from recipebot.render import render_recipe

log = logging.getLogger("recipebot")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="recipebot", description="Posts one simple recipe a day to a Telegram channel.")
    parser.add_argument("--version", action="version", version=f"recipebot {__version__}")
    parser.add_argument("--env-file", default=".env", help="dotenv file to load for local runs (default .env)")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run once: build the brief, ask the model, validate, post")
    run.add_argument("--category", choices=sorted(CATEGORIES), help="override the rotation's category")
    run.add_argument("--theme", help="theme line for the brief (default: the rotation's theme or none)")
    run.add_argument("--count", type=int, choices=[1, 2, 3], help="recipes to request (default RECIPEBOT_COUNT)")
    run.add_argument("--servings", type=int, help="servings to request (default RECIPEBOT_SERVINGS)")
    run.add_argument("--date", type=date.fromisoformat, help="pretend it is this date for the rotation (YYYY-MM-DD)")
    run.add_argument("--dry-run", action="store_true", help="do everything except post and record")
    run.add_argument("--no-page-check", action="store_true", help="skip fetching source pages during validation")

    sub.add_parser("loop", help="stay up and run every day at RECIPEBOT_POST_TIME")

    preview = sub.add_parser("preview", help="render a saved model reply as Telegram HTML, no network")
    preview.add_argument("json_file", type=Path, help="file holding the model's JSON reply")

    rotation = sub.add_parser("rotation", help="print the upcoming rotation")
    rotation.add_argument("--days", type=int, default=14)
    rotation.add_argument("--from", dest="start", type=date.fromisoformat, help="start date (default today)")

    history = sub.add_parser("history", help="show recent posts and runs")
    history.add_argument("--limit", type=int, default=20)

    tg = sub.add_parser("test-telegram", help="send a test message to the channel (or --admin chat)")
    tg.add_argument("--admin", action="store_true", help="send to TELEGRAM_ADMIN_CHAT_ID instead")

    sub.add_parser("check-config", help="print the resolved settings with secrets masked")
    sub.add_parser("system-prompt", help="print the system prompt exactly as it is sent")
    return parser


def _mask(value: str | None) -> str:
    if not value:
        return "(unset)"
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "…" + value[-4:]


def cmd_check_config(settings: Settings) -> int:
    rows = [
        ("TELEGRAM_BOT_TOKEN", _mask(settings.telegram_bot_token)),
        ("TELEGRAM_CHAT_ID", settings.telegram_chat_id or "(unset)"),
        ("TELEGRAM_ADMIN_CHAT_ID", settings.telegram_admin_chat_id or "(unset)"),
        ("LLM_API_KEY", _mask(settings.llm_api_key)),
        ("LLM_MODEL", settings.llm_model),
        ("LLM_EFFORT", settings.llm_effort or "(omitted)"),
        ("LLM_MAX_TOKENS", str(settings.llm_max_tokens)),
        ("LLM_FALLBACKS", settings.llm_fallbacks),
        ("LLM_WEB_SEARCH_TOOL", settings.llm_web_search_tool),
        ("LLM_WEB_SEARCH_MAX_USES", str(settings.llm_web_search_max_uses)),
        ("LLM_ALLOWED_DOMAINS", ", ".join(settings.llm_allowed_domains) or "(any)"),
        ("TZ", settings.timezone),
        ("RECIPEBOT_POST_TIME", settings.post_time.strftime("%H:%M")),
        ("RECIPEBOT_DATA_DIR", str(settings.data_dir)),
        ("RECIPEBOT_SOURCE_MODE", settings.source_mode),
        ("RECIPEBOT_COUNT", str(settings.count)),
        ("RECIPEBOT_SERVINGS", str(settings.servings)),
        ("RECIPEBOT_ROTATION_EPOCH", settings.rotation_epoch.isoformat()),
        ("RECIPEBOT_RUN_ON_START", str(settings.run_on_start).lower()),
    ]
    width = max(len(k) for k, _ in rows)
    for key, value in rows:
        print(f"{key.ljust(width)}  {value}")
    problems = []
    for check in (settings.require_telegram, settings.require_llm):
        try:
            check()
        except ConfigError as exc:
            problems.append(str(exc))
    try:
        ZoneInfo(settings.timezone)
    except Exception:  # noqa: BLE001
        problems.append(f"TZ {settings.timezone!r} is not a known timezone")
    if problems:
        print("\nProblems:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nAll required settings are present.")
    return 0


def cmd_preview(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    recipes = data.get("recipes", []) if isinstance(data, dict) else data
    if isinstance(data, dict) and "title" in data:
        recipes = [data]
    if not recipes:
        print("no recipes in file")
        return 1
    for raw in recipes:
        recipe = Recipe.model_validate(raw)
        category = get_category(recipe.category)
        for i, message in enumerate(render_recipe(recipe, category), start=1):
            print(f"--- message {i} ({len(message)} chars) ---")
            print(message)
            print()
    return 0


def cmd_rotation(settings: Settings, start: date | None, days: int) -> int:
    from recipebot.rotation import Rotation

    rotation = Rotation.load(settings.rotation_path, settings.rotation_epoch)
    start = start or datetime.now(ZoneInfo(settings.timezone)).date()
    for item in rotation.upcoming(start, days):
        theme = f'  theme "{item.slot.theme}"' if item.slot.theme else ""
        flag = " (override)" if item.override else ""
        print(f"{item.day.isoformat()} {item.day.strftime('%a')}  week {item.week}  {item.slot.category}{theme}{flag}")
    return 0


def cmd_history(settings: Settings, limit: int) -> int:
    from recipebot.history import History

    with History(settings.db_path) as history:
        sent = history.recent_sent(limit)
        print(f"Last {len(sent)} posts:")
        for row in sent:
            print(f"  {row.sent_at}  {row.category:<18} {row.title}  <{row.url}>  main={row.main_ingredient or '-'}")
        runs = history.recent_runs(limit)
        print(f"\nLast {len(runs)} runs:")
        for run in runs:
            print(f"  {run.started_at}  {run.category or '-':<18} {run.status:<15} posted={run.posted}  {run.run_id}")
    return 0


def cmd_test_telegram(settings: Settings, admin: bool) -> int:
    from recipebot.telegram import TelegramClient

    settings.require_telegram()
    chat_id = settings.telegram_admin_chat_id if admin else settings.telegram_chat_id
    if not chat_id:
        print("TELEGRAM_ADMIN_CHAT_ID is not set")
        return 1
    client = TelegramClient(settings.telegram_bot_token or "")
    me = client.get_me()
    print(f"bot: @{me.get('username', '?')} (id {me.get('id', '?')})")
    message_id = client.send_message(chat_id, "<b>RecipeBot</b> is connected. \U0001f373", parse_mode="HTML")
    print(f"sent message {message_id} to {chat_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_dotenv(args.env_file)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    _setup_logging(settings.log_level)

    try:
        if args.command == "check-config":
            return cmd_check_config(settings)
        if args.command == "system-prompt":
            from recipebot.prompts import load_system_prompt

            print(load_system_prompt(settings.prompts_dir))
            return 0
        if args.command == "preview":
            return cmd_preview(args.json_file)
        if args.command == "rotation":
            return cmd_rotation(settings, args.start, args.days)
        if args.command == "history":
            return cmd_history(settings, args.limit)
        if args.command == "test-telegram":
            return cmd_test_telegram(settings, args.admin)

        from recipebot.pipeline import Pipeline

        pipeline = Pipeline(settings)
        if args.command == "run":
            report = pipeline.run(
                category=args.category,
                theme=args.theme,
                count=args.count,
                servings=args.servings,
                day=args.date,
                dry_run=args.dry_run,
                check_pages=not args.no_page_check,
            )
            if args.dry_run:
                for messages in report.messages:
                    for i, message in enumerate(messages, start=1):
                        print(f"--- message {i} ({len(message)} chars) ---")
                        print(message)
                        print()
            print(report.summary())
            return 0 if report.ok else 1
        if args.command == "loop":
            from recipebot.scheduler import run_forever

            settings.require_telegram()
            settings.require_llm()
            run_forever(pipeline, settings)
            return 0
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    parser.error(f"unknown command {args.command}")
    return 2

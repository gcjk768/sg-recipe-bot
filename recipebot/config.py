"""Settings read from environment variables (and an optional .env file for local runs)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(ValueError):
    pass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {value!r}") from None


def _parse_time(value: str) -> time:
    try:
        hours, minutes = value.split(":")
        return time(int(hours), int(minutes))
    except (ValueError, AttributeError):
        raise ConfigError(f"RECIPEBOT_POST_TIME must look like 16:00, got {value!r}") from None


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ConfigError(f"RECIPEBOT_ROTATION_EPOCH must be YYYY-MM-DD, got {value!r}") from None


def _env_float(name: str, default: float) -> float:
    value = _env(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {value!r}") from None


def _catch_up_hours(value: float) -> float:
    import math

    if not math.isfinite(value) or value < 0 or value > 168:
        raise ConfigError("RECIPEBOT_CATCH_UP_HOURS must be a number of hours between 0 and 168")
    return value


def _timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"TZ {value!r} is not a known timezone name (use one like Asia/Singapore)") from None
    return value


def _chat_id(name: str) -> str | None:
    value = _env(name)
    _, sep, topic = (value or "").partition("/")
    if sep and not topic.isdigit():
        raise ConfigError(f"{name} must be CHAT or CHAT/TOPIC_NUMBER (like -1002069000031/2765), got {value!r}")
    return value


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def load_dotenv(path: Path | str = ".env") -> None:
    """Minimal .env loader for local runs. Docker's env_file makes this unnecessary in the container."""
    path = Path(path)
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass
class Settings:
    # Telegram
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    telegram_admin_chat_id: str | None

    # Model
    llm_api_key: str | None
    llm_model: str = "claude-opus-5-5"
    llm_effort: str | None = "high"
    llm_max_tokens: int = 32000
    llm_fallbacks: str = "default"
    llm_web_search_tool: str = "web_search_20260209"
    llm_web_search_max_uses: int = 10
    llm_allowed_domains: list[str] = field(default_factory=list)
    llm_timeout_seconds: int = 600

    # Run
    timezone: str = "Asia/Singapore"
    post_time: time = time(16, 0)
    data_dir: Path = Path("/data")
    source_mode: str = "search"
    candidate_pages: int = 6
    count: int = 1
    servings: int = 2
    rotation_epoch: date = date(2026, 9, 28)
    run_on_start: bool = False
    catch_up_hours: float = 6.0
    """After a restart, a missed post is still made up to this many hours after the post time."""
    log_level: str = "INFO"
    history_days: int = 90
    history_max_lines: int = 150
    recent_mains: int = 7
    fetch_timeout_seconds: int = 20
    prompts_dir: Path | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "history.sqlite"

    @property
    def candidates_dir(self) -> Path:
        return self.data_dir / "candidates"

    @property
    def rotation_path(self) -> Path:
        return self.data_dir / "rotation.json"

    def require_telegram(self) -> None:
        missing = [n for n, v in (("TELEGRAM_BOT_TOKEN", self.telegram_bot_token), ("TELEGRAM_CHAT_ID", self.telegram_chat_id)) if not v]
        if missing:
            raise ConfigError("missing required settings: " + ", ".join(missing))

    def require_llm(self) -> None:
        if not self.llm_api_key:
            raise ConfigError("missing required setting: LLM_API_KEY (or ANTHROPIC_API_KEY)")


def load_settings() -> Settings:
    effort = _env("LLM_EFFORT", "high")
    if effort and effort.lower() in {"none", "off"}:
        effort = None
    fallbacks = (_env("LLM_FALLBACKS", "default") or "default").lower()
    if fallbacks not in {"default", "off"}:
        raise ConfigError("LLM_FALLBACKS must be 'default' or 'off'")
    source_mode = (_env("RECIPEBOT_SOURCE_MODE", "search") or "search").lower()
    if source_mode not in {"search", "candidates"}:
        raise ConfigError("RECIPEBOT_SOURCE_MODE must be 'search' or 'candidates'")
    count = _env_int("RECIPEBOT_COUNT", 1)
    if not 1 <= count <= 3:
        raise ConfigError("RECIPEBOT_COUNT must be between 1 and 3")
    prompts_dir = _env("RECIPEBOT_PROMPTS_DIR")
    return Settings(
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_chat_id("TELEGRAM_CHAT_ID"),
        telegram_admin_chat_id=_chat_id("TELEGRAM_ADMIN_CHAT_ID"),
        llm_api_key=_env("LLM_API_KEY") or _env("ANTHROPIC_API_KEY"),
        llm_model=_env("LLM_MODEL", "claude-opus-5-5") or "claude-opus-5-5",
        llm_effort=effort,
        llm_max_tokens=_env_int("LLM_MAX_TOKENS", 32000),
        llm_fallbacks=fallbacks,
        llm_web_search_tool=_env("LLM_WEB_SEARCH_TOOL", "web_search_20260209") or "web_search_20260209",
        llm_web_search_max_uses=_env_int("LLM_WEB_SEARCH_MAX_USES", 10),
        llm_allowed_domains=_csv(_env("LLM_ALLOWED_DOMAINS")),
        llm_timeout_seconds=_env_int("LLM_TIMEOUT_SECONDS", 600),
        timezone=_timezone(_env("TZ", "Asia/Singapore") or "Asia/Singapore"),
        post_time=_parse_time(_env("RECIPEBOT_POST_TIME", "16:00") or "16:00"),
        data_dir=Path(_env("RECIPEBOT_DATA_DIR", "/data") or "/data"),
        source_mode=source_mode,
        candidate_pages=_env_int("RECIPEBOT_CANDIDATE_PAGES", 6),
        count=count,
        servings=_env_int("RECIPEBOT_SERVINGS", 2),
        rotation_epoch=_parse_date(_env("RECIPEBOT_ROTATION_EPOCH", "2026-09-28") or "2026-09-28"),
        run_on_start=_env_bool("RECIPEBOT_RUN_ON_START", False),
        catch_up_hours=_catch_up_hours(_env_float("RECIPEBOT_CATCH_UP_HOURS", 6.0)),
        log_level=(_env("RECIPEBOT_LOG_LEVEL", "INFO") or "INFO").upper(),
        history_days=_env_int("RECIPEBOT_HISTORY_DAYS", 90),
        history_max_lines=_env_int("RECIPEBOT_HISTORY_MAX_LINES", 150),
        recent_mains=_env_int("RECIPEBOT_RECENT_MAINS", 7),
        fetch_timeout_seconds=_env_int("RECIPEBOT_FETCH_TIMEOUT", 20),
        prompts_dir=Path(prompts_dir) if prompts_dir else None,
    )

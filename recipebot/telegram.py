"""Telegram Bot API client: sendMessage with HTML parse mode, rate limit handling and admin alerts."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 4096


class TelegramError(Exception):
    def __init__(self, description: str, status: int | None = None, error_code: int | None = None):
        super().__init__(description)
        self.description = description
        self.status = status
        self.error_code = error_code


class TelegramClient:
    def __init__(
        self,
        token: str,
        *,
        session: Any | None = None,
        base_url: str = "https://api.telegram.org",
        timeout: float = 30,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 4,
    ):
        if session is None:
            import requests

            session = requests.Session()
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.sleep = sleep
        self.max_attempts = max_attempts

    def _url(self, method: str) -> str:
        return f"{self.base_url}/bot{self.token}/{method}"

    def redact(self, text: str) -> str:
        """Strips the bot token from library error messages, which include the request URL."""
        return text.replace(self.token, "<token>") if self.token else text

    def call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        """POSTs a Bot API method. Retries 429 (honouring retry_after) and 5xx, raises TelegramError otherwise."""
        payload = payload or {}
        last_error: TelegramError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.session.post(self._url(method), json=payload, timeout=self.timeout)
            except Exception as exc:
                last_error = TelegramError(self.redact(f"network error calling {method}: {type(exc).__name__}: {exc}"))
                self.sleep(min(2 ** attempt, 30))
                continue
            try:
                body = response.json()
            except ValueError:
                body = {}
            status = int(getattr(response, "status_code", 0) or 0)
            if body.get("ok"):
                return body.get("result")
            description = self.redact(str(body.get("description") or f"HTTP {status}"))
            error_code = body.get("error_code") or status
            if status == 429 or error_code == 429:
                retry_after = float((body.get("parameters") or {}).get("retry_after") or 5)
                log.warning("telegram rate limited on %s, waiting %.0fs", method, retry_after)
                last_error = TelegramError(description, status, error_code)
                self.sleep(retry_after + 0.5)
                continue
            if status >= 500:
                last_error = TelegramError(description, status, error_code)
                self.sleep(min(2 ** attempt, 30))
                continue
            raise TelegramError(description, status, error_code)
        assert last_error is not None
        raise last_error

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        parse_mode: str | None = "HTML",
        disable_preview: bool = False,
    ) -> int | None:
        if len(text) > MAX_MESSAGE_CHARS:
            raise TelegramError(f"message is {len(text)} characters, Telegram allows {MAX_MESSAGE_CHARS}")
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if disable_preview:
            payload["link_preview_options"] = {"is_disabled": True}
        result = self.call("sendMessage", payload)
        if isinstance(result, dict):
            return result.get("message_id")
        return None

    def send_messages(self, chat_id: str, texts: list[str], *, pause: float = 1.0) -> list[int | None]:
        """Sends the one or two messages of a single recipe, one second apart."""
        ids: list[int | None] = []
        for i, text in enumerate(texts):
            if i > 0:
                self.sleep(pause)
            ids.append(self.send_message(chat_id, text))
        return ids

    def send_plain(self, chat_id: str, text: str) -> int | None:
        """Plain text alert (no parse mode), truncated to the Telegram limit."""
        if len(text) > MAX_MESSAGE_CHARS:
            text = text[: MAX_MESSAGE_CHARS - 20].rstrip() + "\n[truncated]"
        return self.send_message(chat_id, text, parse_mode=None, disable_preview=True)

    def get_me(self) -> dict[str, Any]:
        result = self.call("getMe")
        return result if isinstance(result, dict) else {}

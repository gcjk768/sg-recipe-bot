"""Telegram Bot API client: HTML card messages (escaping, block-safe splitting, plain-text fallback),
rate limit handling and admin alerts."""

from __future__ import annotations

import html
import logging
import re
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 4096
DIVIDER = "━" * 16
LINK_SEP = "  ·  "


def esc(value: object) -> str:
    """HTML-escapes dynamic text (model output included) for Telegram's HTML parse mode."""
    return html.escape("" if value is None else str(value), quote=False)


def esc_attr(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def expandable(inner_html: str) -> str:
    return f"<blockquote expandable>{inner_html}</blockquote>"


def split_blocks(blocks: list[str], limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Packs blocks (each a closed piece of HTML) into messages joined by blank lines, splitting only
    between blocks so a tag is never cut. A single block over the limit raises ValueError."""
    messages: list[str] = []
    current = ""
    for block in (b for b in blocks if b):
        if len(block) > limit:
            raise ValueError(f"a message block is {len(block)} characters, Telegram allows {limit}")
        joined = f"{current}\n\n{block}" if current else block
        if len(joined) > limit:
            messages.append(current)
            joined = block
        current = joined
    if current:
        messages.append(current)
    return messages


_LINK = re.compile(r'<a href="([^"]*)">(.*?)</a>', re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


def html_to_plain(text: str) -> str:
    """Fallback body when Telegram rejects the HTML: tags dropped, links kept as 'label (url)'."""
    text = _LINK.sub(lambda m: f"{m.group(2)} ({m.group(1)})", text)
    text = html.unescape(_TAG.sub("", text))
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[: MAX_MESSAGE_CHARS - 20].rstrip() + "\n[truncated]"
    return text


class TelegramError(Exception):
    def __init__(self, description: str, status: int | None = None, error_code: int | None = None, *, ambiguous: bool = False):
        super().__init__(description)
        self.description = description
        self.status = status
        self.error_code = error_code
        self.ambiguous = ambiguous
        """True when the request may have reached Telegram (a read timeout or a reset while reading),
        so the message might have been delivered even though no reply arrived."""


_CONNECT_PHASE_MARKERS = (
    "NewConnectionError",
    "ConnectTimeout",
    "Failed to establish a new connection",
    "Name or service not known",
    "nodename nor servname",
    "Temporary failure in name resolution",
    "Connection refused",
    "NameResolutionError",
    "ProxyError",
)
# A TLS failure is pre-send only when it is a handshake failure; a TLS error while reading the
# reply happens after the request was written and is as ambiguous as a read timeout.
_HANDSHAKE_MARKERS = (
    "handshake",
    "HANDSHAKE",
    "CERTIFICATE_VERIFY_FAILED",
    "certificate verify failed",
    "WRONG_VERSION_NUMBER",
    "UNSUPPORTED_PROTOCOL",
    "TLSV1_ALERT",
    "hostname",
    "IP address mismatch",
)
_AMBIGUOUS_MARKERS = ("ReadTimeout", "ChunkedEncodingError", "IncompleteRead", "RemoteDisconnected", "ConnectionResetError")

# 5xx replies to a non idempotent call: only 503 (service unavailable) reliably means the request
# was not processed. A 502 can come from the front end after the Bot API server already received it,
# and 500 or 504 may have processed it, so those are treated as ambiguous rather than resent.
_RETRYABLE_5XX_FOR_SENDS = {503}


def failed_before_sending(exc: BaseException) -> bool:
    """True when the exception shows the request never left (DNS, refused, connect timeout, TLS
    handshake), so re-sending cannot duplicate anything. Anything else is ambiguous."""
    text = f"{type(exc).__name__}: {exc}"
    if any(marker in text for marker in _AMBIGUOUS_MARKERS):
        return False
    if "SSLError" in text or "SSL" in text:
        return any(marker in text for marker in _HANDSHAKE_MARKERS)
    return any(marker in text for marker in _CONNECT_PHASE_MARKERS)


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

    def call(self, method: str, payload: dict[str, Any] | None = None, *, idempotent: bool = False) -> Any:
        """POSTs a Bot API method. Retries 429 (honouring retry_after), 5xx and connection failures
        that happened before the request was sent. A failure that may already have delivered a
        non idempotent call (sendMessage) is not retried, to avoid posting twice."""
        payload = payload or {}
        last_error: TelegramError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.session.post(self._url(method), json=payload, timeout=self.timeout)
            except Exception as exc:
                message = self.redact(f"network error calling {method}: {type(exc).__name__}: {exc}")
                if idempotent or failed_before_sending(exc):
                    last_error = TelegramError(message)
                    self.sleep(min(2 ** attempt, 30))
                    continue
                raise TelegramError(message + " (the message may or may not have been delivered)", ambiguous=True) from None
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
                if not idempotent and status not in _RETRYABLE_5XX_FOR_SENDS:
                    raise TelegramError(description + " (the message may or may not have been delivered)", status, error_code, ambiguous=True)
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
        disable_preview: bool = True,
    ) -> int | None:
        if len(text) > MAX_MESSAGE_CHARS:
            raise TelegramError(f"message is {len(text)} characters, Telegram allows {MAX_MESSAGE_CHARS}")
        # "CHAT/TOPIC" (as in a t.me/c/CHAT/TOPIC link) posts into a forum topic of a supergroup.
        chat, _, topic = str(chat_id).partition("/")
        payload: dict[str, Any] = {"chat_id": chat}
        if topic:
            payload["message_thread_id"] = int(topic)
        payload["text"] = text
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if disable_preview:
            payload["link_preview_options"] = {"is_disabled": True}
        try:
            result = self.call("sendMessage", payload)
        except TelegramError as exc:
            # A 400 means nothing was delivered, so resending as plain text cannot post twice.
            if parse_mode != "HTML" or "can't parse entities" not in exc.description:
                raise
            log.warning("telegram rejected the HTML (%s), resending as plain text", exc.description)
            payload.pop("parse_mode")
            payload["text"] = html_to_plain(text)
            result = self.call("sendMessage", payload)
        if isinstance(result, dict):
            return result.get("message_id")
        return None

    def send_messages(self, chat_id: str, texts: list[str], *, pause: float = 1.0) -> list[int | None]:
        """Sends already split messages in order, one second apart."""
        ids: list[int | None] = []
        for i, text in enumerate(texts):
            if i > 0:
                self.sleep(pause)
            ids.append(self.send_message(chat_id, text))
        return ids

    def send_html(self, chat_id: str, blocks: list[str], *, pause: float = 1.0) -> list[int | None]:
        """Sends an HTML card given as blocks, split between blocks when it exceeds 4096 characters."""
        return self.send_messages(chat_id, split_blocks(blocks), pause=pause)

    def get_me(self) -> dict[str, Any]:
        result = self.call("getMe", idempotent=True)
        return result if isinstance(result, dict) else {}

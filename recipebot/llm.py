"""The model call. AnthropicClient sends the system prompt verbatim plus the run brief, lets the
model search the web when the brief says candidate_pages is none, and returns the reply text."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from recipebot.config import Settings

log = logging.getLogger(__name__)

MAX_PAUSE_TURN_CONTINUATIONS = 5
FALLBACK_BETA = "server-side-fallback-2026-07-01"
SINGAPORE_LOCATION = {"type": "approximate", "city": "Singapore", "country": "SG", "timezone": "Asia/Singapore"}


class LLMError(Exception):
    pass


class LLMRefusal(LLMError):
    pass


@dataclass
class LLMResult:
    text: str
    stop_reason: str | None
    model: str | None = None
    text_blocks: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    web_searches: int = 0

    @property
    def truncated(self) -> bool:
        return self.stop_reason == "max_tokens"


class LLMClient(Protocol):
    def complete(self, system: str, user: str, *, web_search: bool) -> LLMResult: ...


class AnthropicClient:
    def __init__(self, settings: Settings, client: Any | None = None):
        self.settings = settings
        if client is None:
            import anthropic

            client = anthropic.Anthropic(
                api_key=settings.llm_api_key,
                timeout=float(settings.llm_timeout_seconds),
                max_retries=3,
            )
        self.client = client

    def web_search_tool(self) -> dict[str, Any]:
        tool: dict[str, Any] = {
            "type": self.settings.llm_web_search_tool,
            "name": "web_search",
            "max_uses": self.settings.llm_web_search_max_uses,
            "user_location": dict(SINGAPORE_LOCATION),
        }
        if self.settings.llm_allowed_domains:
            tool["allowed_domains"] = list(self.settings.llm_allowed_domains)
        return tool

    def request_kwargs(self, system: str, messages: list[dict[str, Any]], *, web_search: bool) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.settings.llm_model,
            "max_tokens": self.settings.llm_max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
        }
        if self.settings.llm_effort:
            kwargs["output_config"] = {"effort": self.settings.llm_effort}
        if web_search:
            kwargs["tools"] = [self.web_search_tool()]
        if self.settings.llm_fallbacks == "default":
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def complete(self, system: str, user: str, *, web_search: bool) -> LLMResult:
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        response = None
        usage_in = usage_out = cache_read = 0
        for continuation in range(MAX_PAUSE_TURN_CONTINUATIONS + 1):
            response = self.client.beta.messages.create(**self.request_kwargs(system, messages, web_search=web_search))
            usage = getattr(response, "usage", None)
            usage_in += int(getattr(usage, "input_tokens", 0) or 0)
            usage_out += int(getattr(usage, "output_tokens", 0) or 0)
            cache_read += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            if response.stop_reason != "pause_turn":
                break
            log.info("model paused its turn (continuation %d), resuming", continuation + 1)
            messages = messages + [{"role": "assistant", "content": response.content}]
        assert response is not None

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details is not None else None
            explanation = getattr(details, "explanation", None) if details is not None else None
            raise LLMRefusal(f"the model declined the request (category {category or 'unknown'}): {explanation or 'no explanation'}")

        text_blocks: list[str] = []
        searches = 0
        for block in response.content or []:
            kind = getattr(block, "type", None)
            if kind == "text":
                text_blocks.append(getattr(block, "text", "") or "")
            elif kind == "server_tool_use" and getattr(block, "name", "") == "web_search":
                searches += 1
        return LLMResult(
            text="".join(text_blocks),
            stop_reason=response.stop_reason,
            model=getattr(response, "model", None),
            text_blocks=text_blocks,
            input_tokens=usage_in,
            output_tokens=usage_out,
            cache_read_tokens=cache_read,
            web_searches=searches,
        )

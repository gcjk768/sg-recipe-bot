"""Turns the model's reply text into a ModelReply, or raises ParseFailure with a message
that the pipeline feeds back to the model under PREVIOUS ATTEMPT FAILED:."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from recipebot.models import ModelReply

_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)
_MAX_SCAN_POSITIONS = 50

ALLOWED_TOP_LEVEL = ({"run", "recipes"}, {"error", "run", "recipes"})


class ParseFailure(Exception):
    """The reply is not the JSON object the contract describes."""


@dataclass
class Parsed:
    reply: ModelReply
    warnings: list[str] = field(default_factory=list)


def _try_load(text: str) -> dict | None:
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def extract_json_object(text: str, extra_candidates: list[str] | None = None) -> tuple[dict, bool]:
    """Returns (object, was_lenient). Strict first, then progressively more forgiving."""
    if text is None:
        raise ParseFailure("The reply was empty. Return one JSON object and nothing else.")
    stripped = text.strip()
    if not stripped:
        raise ParseFailure("The reply was empty. Return one JSON object and nothing else.")

    obj = _try_load(stripped)
    if obj is not None:
        return obj, False

    strict_error: str | None = None
    try:
        json.loads(stripped)
    except json.JSONDecodeError as exc:
        strict_error = f"{exc.msg} at line {exc.lineno} column {exc.colno}"
    except TypeError as exc:
        strict_error = str(exc)

    candidates: list[str] = []
    fence = _FENCE.match(stripped)
    if fence:
        candidates.append(fence.group(1))
    for extra in extra_candidates or []:
        if extra and extra.strip():
            candidates.append(extra.strip())
    for candidate in candidates:
        obj = _try_load(candidate)
        if obj is not None:
            return obj, True

    decoder = json.JSONDecoder()
    positions = [m.start() for m in re.finditer(r"\{", stripped)][:_MAX_SCAN_POSITIONS]
    for pos in positions:
        try:
            obj, _end = decoder.raw_decode(stripped, pos)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and ("run" in obj or "recipes" in obj or "error" in obj):
            return obj, True

    raise ParseFailure(
        f"JSON parse error: {strict_error or 'no JSON object found'}. "
        "The reply must be exactly one JSON object with the keys run and recipes "
        "(or error, run and recipes), with no markdown fences and no text before or after it."
    )


def check_top_level_keys(obj: dict[str, Any]) -> None:
    keys = set(obj.keys())
    if keys not in ALLOWED_TOP_LEVEL:
        raise ParseFailure(
            f"Top level keys were {sorted(keys)}. They must be exactly run and recipes, "
            "or error, run and recipes."
        )


def parse_reply(text: str, extra_candidates: list[str] | None = None) -> Parsed:
    obj, lenient = extract_json_object(text, extra_candidates)
    warnings: list[str] = []
    if lenient:
        warnings.append("reply was not bare JSON; the object was extracted leniently")
    check_top_level_keys(obj)
    try:
        reply = ModelReply.model_validate(obj)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])
        raise ParseFailure(f"The run object or recipes list is malformed: {problems}") from None
    if reply.is_error and reply.recipes:
        warnings.append("error object also carried recipes; they are ignored")
    return Parsed(reply=reply, warnings=warnings)

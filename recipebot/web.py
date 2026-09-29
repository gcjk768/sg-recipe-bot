"""Fetching recipe pages and reading their schema.org Recipe data."""

from __future__ import annotations

import codecs
import html as htmllib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import urlsplit

# Cloudflare style bot checks answer 403 with a challenge page whatever the URL, so the page can
# neither be confirmed nor ruled out.
_BOT_WALL_MARKERS = ("challenge-platform", "cf_chl", "cf-chl", "captcha")


def is_bot_wall(result: "FetchResult") -> bool:
    return result.status == 403 and any(marker in result.text for marker in _BOT_WALL_MARKERS)

_LDJSON = re.compile(
    r"<script[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_MICRODATA = re.compile(r"itemtype\s*=\s*[\"']https?://schema\.org/Recipe[\"']", re.IGNORECASE)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_DURATION = re.compile(
    r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?)?$"
)


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int | None
    text: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status == 200


class Fetcher:
    """Thin wrapper around a requests style session so tests can swap in a fake."""

    def __init__(self, session: Any | None = None, *, timeout: float = 20, max_bytes: int = 2_000_000):
        if session is None:
            # A Chrome TLS fingerprint: many recipe sites 403 the plain `requests` handshake.
            from curl_cffi import requests as cffi_requests

            session = cffi_requests.Session(impersonate="chrome")
        self.session = session
        self.timeout = timeout
        self.max_bytes = max_bytes

    def fetch(self, url: str) -> FetchResult:
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-SG,en;q=0.9,zh;q=0.8",
        }
        try:
            response = self.session.get(url, headers=headers, timeout=self.timeout, allow_redirects=True, stream=True)
        except Exception as exc:  # requests raises many subclasses; the reason text is what matters
            return FetchResult(url=url, final_url=url, status=None, text="", error=f"{type(exc).__name__}: {exc}")
        try:
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(65536):
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if total >= self.max_bytes:
                    break
            raw = b"".join(chunks)
            headers = getattr(response, "headers", None) or {}
            text = decode_body(raw, headers.get("content-type", "") or headers.get("Content-Type", ""))
            return FetchResult(
                url=url,
                final_url=getattr(response, "url", url) or url,
                status=int(response.status_code),
                text=text,
            )
        except Exception as exc:
            return FetchResult(url=url, final_url=url, status=None, text="", error=f"{type(exc).__name__}: {exc}")
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()


_CHARSET_HEADER = re.compile(r"charset\s*=\s*[\"']?([\w.:-]+)", re.IGNORECASE)
_CHARSET_META = re.compile(
    rb"<meta[^>]+charset\s*=\s*[\"']?\s*([\w.:-]+)", re.IGNORECASE
)


# Browser charset labels that Python decodes better under another name (WHATWG encoding rules).
_CHARSET_ALIASES = {
    "gb2312": "gb18030",
    "gb_2312-80": "gb18030",
    "gbk": "gb18030",
    "iso-8859-1": "cp1252",
    "iso8859-1": "cp1252",
    "latin1": "cp1252",
    "latin-1": "cp1252",
    "ascii": "cp1252",
    "us-ascii": "cp1252",
    "utf8": "utf-8",
    "big5": "big5hkscs",
    "shift_jis": "cp932",
    "shift-jis": "cp932",
    "sjis": "cp932",
    "euc-kr": "cp949",
}


def _codec_name(label: str) -> str:
    label = label.strip().strip("\"'").lower()
    return _CHARSET_ALIASES.get(label, label)


def decode_body(raw: bytes, content_type: str = "") -> str:
    """Decodes a fetched page. A byte order mark wins; then a charset named in the Content-Type
    header, then a <meta charset> in the first 4 KB, then strict UTF-8, then UTF-8 with
    replacement. requests' ISO-8859-1 default for headerless text/* is deliberately ignored,
    because it turns every UTF-8 page (most recipe sites, all Chinese ones) into mojibake."""
    for bom, name in ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
        if raw.startswith(bom):
            return raw[len(bom):].decode(name, errors="replace")
    candidates: list[str] = []
    header = _CHARSET_HEADER.search(content_type or "")
    if header:
        candidates.append(header.group(1))
    meta = _CHARSET_META.search(raw[:4096])
    if meta:
        candidates.append(meta.group(1).decode("ascii", errors="ignore"))
    for name in candidates:
        try:
            return raw.decode(_codec_name(name), errors="replace")
        except LookupError:
            continue
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


# --- schema.org helpers ---------------------------------------------------------------


def _clean_script(body: str) -> str:
    body = body.strip()
    body = re.sub(r"^\s*<!--", "", body)
    body = re.sub(r"-->\s*$", "", body)
    body = re.sub(r"^\s*//\s*<!\[CDATA\[", "", body)
    body = re.sub(r"//\s*\]\]>\s*$", "", body)
    return body.strip()


def _is_recipe_node(node: dict) -> bool:
    kind = node.get("@type")
    if isinstance(kind, str):
        return kind == "Recipe" or kind.endswith("/Recipe")
    if isinstance(kind, list):
        return any(isinstance(k, str) and (k == "Recipe" or k.endswith("/Recipe")) for k in kind)
    return False


def _walk(node: Any) -> Iterator[dict]:
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        if _is_recipe_node(node):
            yield node
        for key in ("@graph", "mainEntity", "mainEntityOfPage", "hasPart", "itemListElement", "item"):
            if key in node:
                yield from _walk(node[key])


def extract_recipe_jsonld(html: str) -> list[dict]:
    """All schema.org Recipe nodes found in ld+json script blocks."""
    found: list[dict] = []
    for match in _LDJSON.finditer(html or ""):
        body = _clean_script(match.group(1))
        if not body:
            continue
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            try:
                data = json.loads(body.replace("\n", " ").replace("\t", " "))
            except json.JSONDecodeError:
                continue
        found.extend(_walk(data))
    return found


def page_has_recipe_markup(html: str) -> bool:
    return bool(extract_recipe_jsonld(html)) or bool(_MICRODATA.search(html or ""))


def page_mentions_ingredients(html: str) -> bool:
    return "ingredient" in (html or "").lower()


def page_looks_like_recipe(html: str) -> bool:
    """Section 3: the page mentions ingredients or carries schema.org Recipe data."""
    return page_mentions_ingredients(html) or page_has_recipe_markup(html)


def page_title(html: str) -> str | None:
    match = _TITLE.search(html or "")
    if not match:
        return None
    return strip_html(match.group(1)) or None


def strip_html(text: str) -> str:
    return _WS.sub(" ", htmllib.unescape(_TAG.sub(" ", text or ""))).strip()


def is_homepage(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.path.strip("/") == "" and not parts.query


def iso_duration_to_minutes(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = _DURATION.match(value.strip().upper())
    if not match or value.strip().upper() == "P":
        return None
    days = int(match.group("d") or 0)
    hours = int(match.group("h") or 0)
    minutes = int(match.group("m") or 0)
    seconds = float(match.group("s") or 0)
    total = days * 1440 + hours * 60 + minutes + round(seconds / 60)
    return int(total)


def _text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return strip_html(value)
    if isinstance(value, dict):
        for key in ("text", "name", "@value"):
            if key in value and isinstance(value[key], str):
                return strip_html(value[key])
        return ""
    if isinstance(value, list):
        return ", ".join(t for t in (_text_of(v) for v in value) if t)
    return strip_html(str(value))


def _instructions(value: Any) -> list[str]:
    steps: list[str] = []
    if isinstance(value, str):
        for line in re.split(r"\n+|<br\s*/?>|</p>|</li>", value, flags=re.IGNORECASE):
            line = strip_html(line)
            if line:
                steps.append(line)
        return steps
    if isinstance(value, dict):
        kind = value.get("@type", "")
        kinds = kind if isinstance(kind, list) else [kind]
        if "HowToSection" in kinds or "ItemList" in kinds:
            name = _text_of(value.get("name"))
            if name:
                steps.append(f"[{name}]")
            steps.extend(_instructions(value.get("itemListElement")))
            return steps
        text = _text_of(value)
        if text:
            steps.append(text)
        return steps
    if isinstance(value, list):
        for item in value:
            steps.extend(_instructions(item))
    return steps


def recipe_node_title(node: dict) -> str | None:
    return _text_of(node.get("name") or node.get("headline")) or None


def recipe_node_author(node: dict) -> str | None:
    return _text_of(node.get("author")) or None


def recipe_node_to_text(node: dict) -> str:
    """Plain text summary of a schema.org Recipe node: name, author, yield, times, ingredients, steps."""
    lines: list[str] = []
    title = recipe_node_title(node)
    if title:
        lines.append(f"Name: {title}")
    author = recipe_node_author(node)
    if author:
        lines.append(f"Author: {author}")
    yield_ = _text_of(node.get("recipeYield"))
    if yield_:
        lines.append(f"Yield: {yield_}")
    times = []
    for label, key in (("Prep", "prepTime"), ("Cook", "cookTime"), ("Total", "totalTime")):
        minutes = iso_duration_to_minutes(node.get(key))
        if minutes is not None:
            times.append(f"{label} {minutes} min")
    if times:
        lines.append("Times: " + ", ".join(times))
    cuisine = _text_of(node.get("recipeCuisine"))
    if cuisine:
        lines.append(f"Cuisine: {cuisine}")
    ingredients = node.get("recipeIngredient") or node.get("ingredients") or []
    if isinstance(ingredients, str):
        ingredients = [ingredients]
    if ingredients:
        lines.append("Ingredients:")
        for item in ingredients:
            text = _text_of(item)
            if text:
                lines.append(f"- {text}")
    steps = _instructions(node.get("recipeInstructions"))
    if steps:
        lines.append("Instructions:")
        n = 0
        for step in steps:
            if step.startswith("[") and step.endswith("]"):
                lines.append(step)
                continue
            n += 1
            lines.append(f"{n}. {step}")
    return "\n".join(lines)

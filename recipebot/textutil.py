"""Small text helpers shared by validation, history and rendering."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")

_TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "igshid")


def normalise_title(title: str) -> str:
    """Lowercase, punctuation stripped, whitespace collapsed."""
    text = _PUNCT.sub(" ", title.lower().replace("_", " "))
    return _WS.sub(" ", text).strip()


def normalise_url(url: str) -> str:
    """Lowercase scheme and host, drop fragment and tracking parameters, drop a trailing slash."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    scheme = parts.scheme.lower()
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith(_TRACKING_PARAMS)]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, host, path, urlencode(query), ""))


def format_qty(qty: float) -> str:
    """400 -> '400', 0.5 -> '0.5', 1.25 -> '1.25'."""
    if float(qty).is_integer():
        return str(int(qty))
    text = f"{qty:.2f}".rstrip("0").rstrip(".")
    return text


def hashtag(tag: str) -> str:
    """'one pan' -> '#onepan'. Keeps letters, digits and underscores only."""
    cleaned = re.sub(r"[^\w]", "", tag.strip().lower().replace("-", " ").replace(" ", ""))
    return f"#{cleaned}" if cleaned else ""


def main_ingredient_name(item: str) -> str:
    """'chicken thigh, boneless' -> 'chicken thigh'."""
    head = item.split(",", 1)[0]
    head = re.sub(r"\(.*?\)", "", head)
    return _WS.sub(" ", head).strip().lower()


def format_sgd(amount: float) -> str:
    """9.5 -> 'S$9.50', 10 -> 'S$10', 4.746 -> 'S$4.75'."""
    if float(amount).is_integer():
        return f"S${int(amount)}"
    return f"S${amount:.2f}"

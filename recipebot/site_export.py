"""Feeds the family recipe website (site/index.html) with every recipe the bot posts.

After each post the whole history table is written to ``<SITE_EXPORT_DIR>/daily.json``,
newest first. The page merges it with its curated list. A recipe's photo is read from its
page once and cached in ``images.json``. Best-effort: errors are logged, never raised.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from recipebot.textutil import format_qty
from recipebot.web import extract_recipe_jsonld

log = logging.getLogger(__name__)

# First match wins, so "Malaysian Chinese" is Chinese and "Chinese inspired" is Chinese.
_KITCHENS = (
    ("chinese", r"chinese|cantonese|hong kong|taiwan|sichuan|szechuan|hakka|teochew|hokkien|shanghai"),
    ("malay", r"malay|indonesia|peranakan|nyonya|singapore"),
    ("indian", r"indian|tamil|punjabi|south asian|sri lanka"),
    ("japanese", r"japan"),
    ("korean", r"korea"),
    ("french", r"french|france"),
    ("italian", r"ital"),
    ("western", r"western|american|british|english|australian|european"),
)
_OG_IMAGE = re.compile(r"<meta[^>]+property\s*=\s*[\"']og:image[\"'][^>]+content\s*=\s*[\"']([^\"']+)", re.IGNORECASE)


def kitchen(cuisine: str) -> str:
    text = (cuisine or "").lower()
    for key, pattern in _KITCHENS:
        if re.search(pattern, text):
            return key
    return "others"


def recipe_image(value: Any) -> str | None:
    """The first image URL in a schema.org image/thumbnailUrl value (string, list or ImageObject)."""
    if isinstance(value, str):
        return value if value.startswith("https://") else None
    if isinstance(value, list):
        for item in value:
            found = recipe_image(item)
            if found:
                return found
    if isinstance(value, dict):
        return recipe_image(value.get("url") or value.get("contentUrl"))
    return None


def page_image(html: str) -> str:
    for node in extract_recipe_jsonld(html):
        found = recipe_image(node.get("image")) or recipe_image(node.get("thumbnailUrl"))
        if found:
            return found
    m = _OG_IMAGE.search(html or "")
    return m.group(1) if m and m.group(1).startswith("https://") else ""


def ingredient_lines(recipe: dict) -> list[str]:
    """'400 g chicken thigh, sliced' lines for the shopping list, then pantry staples."""
    lines = []
    for i in recipe.get("ingredients") or []:
        try:
            qty = format_qty(float(i.get("qty") or 0)) if i.get("qty") else ""
        except (TypeError, ValueError):
            qty = ""
        text = " ".join(p for p in (qty, i.get("unit") or "", i.get("item") or "") if p)
        if i.get("note"):
            text += f", {i['note']}"
        if text:
            lines.append(text)
    lines += [f"{s} (pantry)" for s in recipe.get("pantry_staples") or [] if s]
    return lines


def recipe_tags(category: str, recipe: dict) -> list[str]:
    """Website topics the bot knows for sure: protein (its high_protein category or 25 g+ a
    serving), breakfast (meal tag or category) and baking. Keyword topics are added by the page."""
    tags = []
    protein = recipe.get("protein_per_serving_g") or (recipe.get("nutrition_per_serving") or {}).get("protein_g") or 0
    try:
        protein = float(protein)
    except (TypeError, ValueError):
        protein = 0
    if category == "high_protein" or protein >= 25:
        tags.append("protein")
    if category == "breakfast" or "breakfast" in (recipe.get("meals") or []):
        tags.append("breakfast")
    if category.startswith("baking"):
        tags.append("baking")
    return tags


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o644)  # nginx in the recipe-site container reads it
    os.replace(tmp, path)


def export_site(history, out_dir: Path, fetcher, tz: tzinfo) -> int:
    """Writes daily.json from the history table; returns the number of recipes written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / "images.json"
    try:
        images: dict[str, str] = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        images = {}
    rows = history.conn.execute(
        "SELECT sent_at, title, url, recipe_json, category FROM sent_recipes ORDER BY sent_at DESC, id DESC"
    ).fetchall()
    items, seen, fetched = [], set(), False
    for sent_at, title, url, recipe_json, category in rows:
        if url in seen or not url.startswith("https://"):
            continue
        seen.add(url)
        if url not in images:
            result = fetcher.fetch(url)
            images[url] = page_image(result.text) if result.ok else ""
            fetched = True
        try:
            r = json.loads(recipe_json)
        except ValueError:
            r = {}
        items.append({
            "cuisine": kitchen(r.get("cuisine", "")),
            "tags": recipe_tags(category or "", r),
            "zh": r.get("title_zh") or "",
            "title": title,
            "url": url,
            "site": urlsplit(url).netloc.removeprefix("www."),
            "image": images[url] or None,
            "minutes": r.get("total_minutes"),
            "serves": str(r["servings"]) if r.get("servings") else None,
            "blurb": (r.get("why_it_fits") or "")[:160],
            "ingredients": ingredient_lines(r),
            "added": datetime.fromisoformat(sent_at).astimezone(tz).date().isoformat(),
        })
    if fetched:
        _write_json(cache_path, images)
    _write_json(out_dir / "daily.json", items)
    return len(items)


def safe_export(history, out_dir: Path | None, fetcher, tz: tzinfo) -> None:
    if out_dir is None:
        return
    try:
        export_site(history, out_dir, fetcher, tz)
    except Exception as exc:  # noqa: BLE001 - the website must never cost a post
        log.warning("site export failed: %s", exc)

"""Builds site/recipes.js from site/seed.txt for the family recipe page (site/index.html).

Every URL is fetched once and kept only if the page answers 200 with schema.org Recipe data;
title, photo, time and servings come from that data, so the site never shows a dead link.
Run from the repo root:  python site/build.py
"""

from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from recipebot.web import (  # noqa: E402
    Fetcher,
    extract_recipe_jsonld,
    is_bot_wall,
    page_rating,
    iso_duration_to_minutes,
    recipe_node_title,
    strip_html,
)
from recipebot.site_export import recipe_image  # noqa: E402

HERE = Path(__file__).resolve().parent
MAX_MINUTES = 120  # "not complicated": anything longer is left out


def parse_seed(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        cuisine, zh, url = parts[:3]
        tags = [t.strip() for t in parts[3].split(",") if t.strip()] if len(parts) > 3 else []
        rows.append({"cuisine": cuisine, "zh": zh, "url": url, "tags": tags})
    return rows


def _minutes(node: dict) -> int | None:
    total = iso_duration_to_minutes(node.get("totalTime"))
    if total:
        return total
    parts = [iso_duration_to_minutes(node.get(k)) for k in ("prepTime", "cookTime")]
    return sum(p for p in parts if p) or None


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value] if value else []


def _steps(value) -> list[str]:
    """Method steps from schema.org recipeInstructions: a string, or a list of strings,
    HowToStep ({"text"}) and HowToSection ({"itemListElement"}) nodes."""
    if isinstance(value, str):
        return [t for t in (strip_html(p) for p in re.split(r"\n+|<br\s*/?>", value)) if t]
    if isinstance(value, dict):
        if value.get("itemListElement"):
            return _steps(value["itemListElement"])
        return _steps(value.get("text") or value.get("name") or "")
    if isinstance(value, list):
        return [step for item in value for step in _steps(item)]
    return []


def _kcal(node: dict) -> int | None:
    """Calories per serving from schema.org nutrition ("350 kcal", "350", 350), or None."""
    value = (node.get("nutrition") or {}).get("calories") if isinstance(node.get("nutrition"), dict) else None
    m = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return round(float(m.group())) if m and 0 < float(m.group()) < 3000 else None


def _servings(value) -> str | None:
    if isinstance(value, list):
        value = next((v for v in value if v), None)
    text = strip_html(str(value)) if value not in (None, "") else ""
    return text[:20] or None


def card(row: dict, fetcher: Fetcher) -> tuple[dict | None, str]:
    """A card for the page, or None and the reason it was dropped."""
    result = fetcher.fetch(row["url"])
    if is_bot_wall(result):
        return None, "blocked (bot wall)"
    if not result.ok:
        return None, f"status {result.status} {result.error or ''}".strip()
    nodes = extract_recipe_jsonld(result.text)
    if not nodes:
        return None, "no Recipe data"
    node = nodes[0]
    minutes = _minutes(node)
    if minutes and minutes > MAX_MINUTES:
        return None, f"too long ({minutes} min)"
    return {
        "cuisine": row["cuisine"],
        "tags": row["tags"],
        "kcal": _kcal(node),
        "rating": (page_rating(result.text) or (None, None))[0],
        "ratings": (page_rating(result.text) or (None, None))[1],
        "zh": row["zh"],
        "title": recipe_node_title(node) or row["zh"],
        "url": row["url"],
        "site": urlsplit(row["url"]).netloc.removeprefix("www."),
        "image": recipe_image(node.get("image")) or recipe_image(node.get("thumbnailUrl")),
        "minutes": minutes,
        "serves": _servings(node.get("recipeYield")),
        "blurb": strip_html(str(node.get("description") or ""))[:160],
        "ingredients": [t for t in (strip_html(str(i)) for i in _as_list(node.get("recipeIngredient")))][:40],
        "steps": _steps(node.get("recipeInstructions"))[:20],
    }, "ok"


def main() -> int:
    rows = parse_seed((HERE / "seed.txt").read_text(encoding="utf-8"))
    fetcher = Fetcher()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda r: card(r, fetcher), rows))
    cards = []
    for row, (item, reason) in zip(rows, results):
        if item:
            cards.append(item)
            print(f"  ok   {row['cuisine']:9} {item['title'][:50]:50} {item['minutes']} min")
        else:
            print(f"  DROP {row['cuisine']:9} {row['url']}  <- {reason}")
    (HERE / "recipes.js").write_text(
        "window.RECIPES = " + json.dumps(cards, ensure_ascii=False, indent=1) + ";\n", encoding="utf-8"
    )
    print(f"{len(cards)}/{len(rows)} recipes written to site/recipes.js")
    return 0


def _selftest() -> None:
    assert parse_seed("# c\nchinese | 蛋 | https://x.com/a/\n") == [{"cuisine": "chinese", "zh": "蛋", "url": "https://x.com/a/", "tags": []}]
    assert parse_seed("western | 糕 | https://x.com/b/ | baking, breakfast\n")[0]["tags"] == ["baking", "breakfast"]
    assert _minutes({"prepTime": "PT10M", "cookTime": "PT20M"}) == 30
    assert _servings(["4", "4 servings"]) == "4"
    assert _as_list("1 egg") == ["1 egg"] and _as_list(None) == []
    assert _steps("Mix.\nFry.") ==["Mix.", "Fry."] and _steps("Mix.") == ["Mix."]
    assert _steps([{"@type": "HowToStep", "text": "A"}, {"@type": "HowToSection", "itemListElement": [{"text": "B"}, "C"]}]) == ["A", "B", "C"]
    assert _steps(None) == []
    assert _kcal({"nutrition": {"calories": "352 kcal"}}) == 352 and _kcal({}) is None and _kcal({"nutrition": {"calories": "n/a"}}) is None


if __name__ == "__main__":
    _selftest()
    sys.exit(main())

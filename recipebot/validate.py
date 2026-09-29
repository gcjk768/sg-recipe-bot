"""Section 3 of the prompt pack: what the app checks before posting.

Anything that fails a check is dropped, not patched. Checks that are not in section 3 but
follow from the field rules only produce warnings, which the pipeline logs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from recipebot.categories import Category, get_category, is_known
from recipebot.history import History
from recipebot.models import ModelReply, Recipe
from recipebot.textutil import main_ingredient_name, normalise_title, normalise_url
from recipebot.web import Fetcher, is_homepage, page_looks_like_recipe

MAX_INGREDIENTS = 10
MAX_STEPS = 8
MAX_STEP_CHARS = 220

ALLOWED_UNITS = {"g", "ml", "tsp", "tbsp", "piece", "clove", "head", "stalk", "can", "slice", "pinch"}
ALLOWED_TAGS = {
    "one pan", "one pot", "weeknight", "weekend", "meal prep", "rice cooker", "no oven", "tray bake",
    "stir fry", "steamed", "braised", "soup", "noodles", "rice", "salad", "hawker style", "freezer friendly",
    "lunchbox", "kid friendly", "vegetarian", "spicy", "sweet", "budget", "leftovers", "no cook",
    "under 20 minutes",
}

# Salt, pepper, sugar, cooking oil and water do not count towards the ingredient cap.
_FREE_PATTERNS = [
    re.compile(r"^(?:(?:sea|table|kosher|fine|flaky|coarse|rock|himalayan|pink|iodised|iodized)\s+)?salt$"),
    re.compile(r"^salt\s*(?:and|&|,)\s*(?:black\s+|white\s+)?pepper$"),
    re.compile(r"^(?:(?:ground|white|black|freshly ground|cracked|coarse)\s+)*(?:pepper|peppercorns?)$"),
    re.compile(
        r"^(?:(?:white|brown|caster|castor|granulated|raw|icing|powdered|light brown|dark brown|palm|rock|coconut|fine)\s+)?sugar$"
    ),
    re.compile(
        r"^(?:(?:cooking|vegetable|neutral|canola|rapeseed|sunflower|peanut|groundnut|corn|rice bran|soybean|olive|"
        r"extra virgin olive|light olive|frying)\s+)?oil$"
    ),
    re.compile(r"^(?:(?:hot|warm|cold|boiling|ice|iced|tap|filtered|room temperature)\s+)?water$"),
]


@dataclass
class Rejection:
    index: int
    title: str | None
    reason: str

    def __str__(self) -> str:
        label = self.title or f"recipe {self.index + 1}"
        return f"{label}: {self.reason}"


@dataclass
class ValidationResult:
    accepted: list[Recipe] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def is_free_ingredient(item: str) -> bool:
    name = main_ingredient_name(item)
    name = re.sub(r"\b(?:to taste|as needed|optional|for seasoning|for frying|for cooking)\b", "", name).strip(" ,")
    return any(p.match(name) for p in _FREE_PATTERNS)


def counted_ingredients(recipe: Recipe) -> list[str]:
    return [i.item for i in recipe.ingredients if not is_free_ingredient(i.item)]


def check_time_caps(recipe: Recipe, category: Category) -> str | None:
    if category.hands_on_cap is not None and recipe.prep_minutes > category.hands_on_cap:
        return f"prep_minutes {recipe.prep_minutes} exceeds the {category.hands_on_cap} minute hands on cap for {category.key}"
    if category.total_cap is not None and recipe.total_minutes > category.total_cap:
        return f"total_minutes {recipe.total_minutes} exceeds the {category.total_cap} minute cap for {category.key}"
    return None


def check_static(recipe: Recipe, brief_category: str) -> str | None:
    """The section 3 checks that need no network or history. Returns a reason or None."""
    if recipe.category != brief_category:
        return f"category '{recipe.category}' does not match the brief's '{brief_category}'"
    if recipe.difficulty not in {"easy", "medium"}:
        return f"difficulty '{recipe.difficulty}' is not easy or medium"
    if recipe.prep_minutes < 0 or recipe.cook_minutes < 0 or recipe.total_minutes <= 0:
        return "minutes must be positive"
    cap_problem = check_time_caps(recipe, get_category(brief_category))
    if cap_problem:
        return cap_problem
    counted = counted_ingredients(recipe)
    if len(counted) > MAX_INGREDIENTS:
        return f"{len(counted)} ingredients after removing salt, pepper, sugar, oil and water (cap is {MAX_INGREDIENTS})"
    if not recipe.ingredients:
        return "no ingredients"
    if not 1 <= len(recipe.steps) <= MAX_STEPS:
        return f"{len(recipe.steps)} steps (must be 1 to {MAX_STEPS})"
    for i, step in enumerate(recipe.steps, start=1):
        if not step or not step.strip():
            return f"step {i} is empty"
        if len(step) > MAX_STEP_CHARS:
            return f"step {i} is {len(step)} characters (cap is {MAX_STEP_CHARS})"
    if not recipe.source.url.startswith("https://"):
        return f"source.url does not start with https:// ({recipe.source.url!r})"
    if not recipe.title.strip():
        return "title is empty"
    return None


def check_source_page(recipe: Recipe, fetcher: Fetcher) -> str | None:
    result = fetcher.fetch(recipe.source.url)
    if result.error:
        return f"source page could not be fetched ({result.error})"
    if result.status != 200:
        return f"source page returned HTTP {result.status}"
    if is_homepage(result.final_url) and not is_homepage(recipe.source.url):
        return f"source page redirected to the homepage ({result.final_url})"
    if not page_looks_like_recipe(result.text):
        return "source page does not mention ingredients and has no schema.org Recipe data"
    return None


def soft_warnings(recipe: Recipe, brief_category: str) -> list[str]:
    """Field rule drift worth logging but not worth dropping a good recipe over."""
    warnings: list[str] = []
    label = recipe.title
    bad_units = sorted({i.unit for i in recipe.ingredients if i.unit not in ALLOWED_UNITS})
    if bad_units:
        warnings.append(f"{label}: units outside the allowed list: {', '.join(bad_units)}")
    bad_tags = [t for t in recipe.tags if t not in ALLOWED_TAGS]
    if bad_tags:
        warnings.append(f"{label}: tags outside the allowed list: {', '.join(bad_tags)}")
    if len(recipe.tips) > 2:
        warnings.append(f"{label}: {len(recipe.tips)} tips (prompt allows at most 2)")
    if len(recipe.title) >= 60:
        warnings.append(f"{label}: title is {len(recipe.title)} characters (prompt asks for under 60)")
    if brief_category == "high_protein" and recipe.protein_per_serving_g is None:
        warnings.append(f"{label}: high_protein recipe without protein_per_serving_g")
    if brief_category != "high_protein" and recipe.protein_per_serving_g is not None:
        warnings.append(f"{label}: protein_per_serving_g set outside high_protein (not rendered)")
    if brief_category == "chinese_daily" and not recipe.title_zh:
        warnings.append(f"{label}: chinese_daily recipe without title_zh")
    if brief_category in {"meal_prep", "sauces_basics"} and not recipe.storage:
        warnings.append(f"{label}: {brief_category} recipe without storage")
    if brief_category == "meal_prep" and recipe.servings != 4:
        warnings.append(f"{label}: meal_prep recipe with servings {recipe.servings} (prompt says 4)")
    if recipe.total_minutes < recipe.prep_minutes or recipe.total_minutes < recipe.cook_minutes:
        warnings.append(f"{label}: total_minutes is smaller than prep or cook minutes")
    if recipe.nutrition_per_serving is None:
        warnings.append(f"{label}: no nutrition_per_serving estimate (line dropped)")
    elif recipe.nutrition is None:
        warnings.append(f"{label}: implausible nutrition_per_serving {recipe.nutrition_per_serving.model_dump()} (line dropped)")
    if recipe.cost_estimate is None:
        warnings.append(f"{label}: no cost_estimate (line dropped)")
    elif recipe.cost is None:
        warnings.append(f"{label}: implausible cost_estimate {recipe.cost_estimate.model_dump()} (line dropped)")
    return warnings


def parse_recipe(raw: Any) -> tuple[Recipe | None, str | None]:
    try:
        return Recipe.model_validate(raw), None
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:4])
        return None, f"does not match the recipe schema ({problems})"


def main_ingredient(recipe: Recipe) -> str | None:
    staples = {s.lower().strip() for s in recipe.pantry_staples}
    for ingredient in recipe.ingredients:
        if is_free_ingredient(ingredient.item):
            continue
        if ingredient.item.lower().strip() in staples:
            continue
        return main_ingredient_name(ingredient.item)
    for ingredient in recipe.ingredients:
        if not is_free_ingredient(ingredient.item):
            return main_ingredient_name(ingredient.item)
    return None


def validate_reply(
    reply: ModelReply,
    brief_category: str,
    *,
    history: History | None,
    fetcher: Fetcher | None,
    check_pages: bool = True,
) -> ValidationResult:
    """Runs every section 3 check on every recipe. Recipes are validated one by one so a bad
    one never takes a good one down with it."""
    result = ValidationResult()
    if not is_known(brief_category):
        result.rejected.append(Rejection(-1, None, f"unknown brief category {brief_category!r}"))
        return result
    if reply.is_error:
        return result

    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for index, raw in enumerate(reply.recipes):
        title = raw.get("title") if isinstance(raw, dict) else None
        recipe, problem = parse_recipe(raw)
        if recipe is None:
            result.rejected.append(Rejection(index, title, problem or "unparseable"))
            continue
        problem = check_static(recipe, brief_category)
        if problem:
            result.rejected.append(Rejection(index, recipe.title, problem))
            continue
        url_key = normalise_url(recipe.source.url)
        title_key = normalise_title(recipe.title)
        if history is not None:
            if history.has_url(recipe.source.url):
                result.rejected.append(Rejection(index, recipe.title, "source.url was already sent"))
                continue
            if history.has_title(recipe.title):
                result.rejected.append(Rejection(index, recipe.title, "a recipe with the same title was already sent"))
                continue
        if url_key in seen_urls:
            result.rejected.append(Rejection(index, recipe.title, "duplicates the url of another recipe in this run"))
            continue
        if title_key in seen_titles:
            result.rejected.append(Rejection(index, recipe.title, "duplicates the title of another recipe in this run"))
            continue
        if check_pages:
            if fetcher is None:
                raise ValueError("check_pages requires a fetcher")
            problem = check_source_page(recipe, fetcher)
            if problem:
                result.rejected.append(Rejection(index, recipe.title, problem))
                continue
        seen_urls.add(url_key)
        seen_titles.add(title_key)
        result.warnings.extend(soft_warnings(recipe, brief_category))
        result.accepted.append(recipe)
    return result

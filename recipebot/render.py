"""Section 4 of the prompt pack: rendering one recipe as Telegram HTML."""

from __future__ import annotations

import html

from recipebot.categories import Category
from recipebot.models import Recipe
from recipebot.textutil import format_qty, format_sgd, hashtag, single_line

TELEGRAM_MAX_CHARS = 4096
SPLIT_THRESHOLD = 4000

PROTEIN_EMOJI = "\U0001f4aa"  # 💪
NUTRITION_EMOJI = "\U0001f525"  # 🔥
COST_EMOJI = "\U0001f4b0"  # 💰
TIP_EMOJI = "\U0001f4a1"  # 💡
STORAGE_EMOJI = "\U0001f9ca"  # 🧊
LINK_EMOJI = "\U0001f517"  # 🔗
BULLET = "•"  # •
DOT = " · "  # ·


class RenderError(ValueError):
    pass


def esc(value: object) -> str:
    """Escape &, < and > for Telegram HTML, with line breaks inside a field collapsed to spaces
    so model text can never add lines to the post."""
    return html.escape(single_line(value), quote=False)


def esc_attr(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _title_line(recipe: Recipe, category: Category) -> str:
    line = f"{category.emoji} <b>{esc(recipe.title.strip())}</b>"
    if recipe.title_zh and recipe.title_zh.strip():
        line += f" ({esc(recipe.title_zh.strip())})"
    return line


def _meta_line(recipe: Recipe, category: Category) -> str:
    parts = [category.label]
    if recipe.cuisine and recipe.cuisine.strip():
        parts.append(recipe.cuisine.strip())
    parts.append(f"{recipe.total_minutes} min")
    parts.append(recipe.difficulty)
    parts.append(f"serves {recipe.servings}")
    return "<i>" + DOT.join(esc(p) for p in parts) + "</i>"


def _ingredient_line(item) -> str:
    parts = [format_qty(item.qty), esc(item.unit), esc(item.item)]
    text = f"{BULLET} " + " ".join(p for p in parts if p)
    if item.note and item.note.strip():
        text += f", {esc(item.note)}"
    return text


def _ingredients_block(recipe: Recipe) -> str:
    return "<b>Ingredients</b>\n" + "\n".join(_ingredient_line(i) for i in recipe.ingredients)


def _steps_block(recipe: Recipe) -> str:
    return "<b>Steps</b>\n" + "\n".join(f"{n}. {esc(step.strip())}" for n, step in enumerate(recipe.steps, start=1))


def nutrition_line(recipe: Recipe) -> str | None:
    n = recipe.nutrition
    if n is None:
        return None
    parts = [f"About {n.kcal} kcal per serving"]
    for value, label in ((n.protein_g, "protein"), (n.carbs_g, "carbs"), (n.fat_g, "fat")):
        if value is not None:
            parts.append(f"{value} g {label}")
    return f"{NUTRITION_EMOJI} " + ", ".join(parts) + " (estimate)"


def cost_line(recipe: Recipe) -> str | None:
    c = recipe.cost
    if c is None:
        return None
    text = f"{COST_EMOJI} Ingredients about {format_sgd(c.total_sgd)}"
    per_serving = c.per_serving_sgd
    if per_serving is None and recipe.servings > 0:
        per_serving = c.total_sgd / recipe.servings
    if per_serving is not None and recipe.servings > 1:
        text += f" for {recipe.servings} servings, {format_sgd(round(per_serving, 2))} each"
    if c.note and c.note.strip():
        text += f", {esc(c.note.strip().rstrip('.'))}"
    return text + " (estimate)"


def _extras_block(recipe: Recipe, category: Category) -> str | None:
    lines: list[str] = []
    for line in (nutrition_line(recipe), cost_line(recipe)):
        if line:
            lines.append(line)
    if category.key == "high_protein" and recipe.protein_per_serving_g is not None:
        lines.append(f"{PROTEIN_EMOJI} Protein: about {recipe.protein_per_serving_g} g per serving")
    for tip in recipe.tips:
        if tip and tip.strip():
            lines.append(f"{TIP_EMOJI} {esc(tip.strip())}")
    if recipe.storage and recipe.storage.strip():
        lines.append(f"{STORAGE_EMOJI} {esc(recipe.storage.strip())}")
    return "\n".join(lines) if lines else None


def hashtags_line(recipe: Recipe, category: Category) -> str:
    tags = [f"#{meal}" for meal in recipe.meals]
    for h in [f"#{category.hashtag}"] + [hashtag(tag) for tag in recipe.tags]:
        if h and h not in tags:
            tags.append(h)
    return " ".join(tags)


def _footer_block(recipe: Recipe, category: Category) -> str:
    link = f'{LINK_EMOJI} <a href="{esc_attr(recipe.source.url)}">Full recipe at {esc(recipe.source.site.strip() or "source")}</a>'
    return link + "\n" + hashtags_line(recipe, category)


def render_recipe(recipe: Recipe, category: Category) -> list[str]:
    """One HTML message normally; two when the render would exceed 4000 characters
    (ingredients in the first, steps in the second, title repeated)."""
    head = _title_line(recipe, category) + "\n" + _meta_line(recipe, category)
    why = esc(recipe.why_it_fits.strip()) if recipe.why_it_fits and recipe.why_it_fits.strip() else None
    ingredients = _ingredients_block(recipe)
    steps = _steps_block(recipe)
    extras = _extras_block(recipe, category)
    footer = _footer_block(recipe, category)

    single = "\n\n".join(b for b in (head, why, ingredients, steps, extras, footer) if b)
    if len(single) <= SPLIT_THRESHOLD:
        return [single]

    first = "\n\n".join(b for b in (head, why, ingredients) if b)
    second = "\n\n".join(b for b in (_title_line(recipe, category), steps, extras, footer) if b)
    for part in (first, second):
        if len(part) > TELEGRAM_MAX_CHARS:
            raise RenderError(f"rendered message is {len(part)} characters even after splitting (limit {TELEGRAM_MAX_CHARS})")
    return [first, second]

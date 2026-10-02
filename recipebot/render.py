"""Section 4 of the prompt pack: rendering a recipe (and the admin alerts) as Telegram HTML cards.

Layout: a flat message (title, meta line, ingredients, steps, extras, source link, hashtags). Every dynamic value goes through `esc`/`esc_attr` before it meets a tag.
"""

from __future__ import annotations

from recipebot import telegram as tg
from recipebot.categories import Category
from recipebot.models import Recipe
from recipebot.textutil import format_qty, format_sgd, hashtag, single_line

TELEGRAM_MAX_CHARS = tg.MAX_MESSAGE_CHARS

# One fixed emoji + title per message/section type.
SECTION_TITLES: dict[str, tuple[str, str]] = {
    "recipe": ("\U0001f373", "RECIPE"),  # 🍳
    "breakfast": ("\U0001f305", "BREAKFAST"),  # 🌅
    "lunch": ("☀️", "LUNCH"),  # ☀️
    "dinner": ("\U0001f319", "DINNER"),  # 🌙
    "supper": ("\U0001f303", "SUPPER"),  # 🌃
    "failed": ("\U0001f6a8", "RECIPEBOT"),  # 🚨
    "missed": ("⏰", "RECIPEBOT"),  # ⏰
    "unfinished": ("⚠️", "RECIPEBOT"),  # ⚠️
    "connected": ("✅", "RECIPEBOT"),  # ✅
}

PROTEIN_EMOJI = "\U0001f4aa"  # 💪
NUTRITION_EMOJI = "\U0001f525"  # 🔥
COST_EMOJI = "\U0001f4b0"  # 💰
TIP_EMOJI = "\U0001f4a1"  # 💡
STORAGE_EMOJI = "\U0001f9ca"  # 🧊
LINK_EMOJI = "\U0001f517"  # 🔗
BAD_EMOJI = "\U0001f534"  # 🔴
BULLET = "•"  # •
DOT = " · "  # ·
MAX_BACKGROUND_CHARS = 3000


class RenderError(ValueError):
    pass


def esc(value: object) -> str:
    """Escape for Telegram HTML, with line breaks inside a field collapsed to spaces so model text
    can never add lines to the post."""
    return tg.esc(single_line(value))


esc_attr = tg.esc_attr


def header(section: str, subtitle: str) -> str:
    emoji, title = SECTION_TITLES[section]
    return f"{emoji} <b>{title}</b>{DOT}{esc(subtitle)}"


def _per_serving(recipe: Recipe) -> float | None:
    c = recipe.cost
    if c is None:
        return None
    if c.per_serving_sgd is not None:
        return c.per_serving_sgd
    return c.total_sgd / recipe.servings if recipe.servings > 0 else None


def _top(recipe: Recipe, category: Category) -> str:
    """Title line, then one meta line: category, cuisine, time, difficulty, servings."""
    cuisine = recipe.cuisine.strip() if recipe.cuisine else ""
    meta = [esc(category.label)] + ([esc(cuisine)] if cuisine else [])
    meta += [f"{recipe.total_minutes} min", esc(recipe.difficulty), f"serves {recipe.servings}"]
    return f"{category.emoji} <b>{esc(recipe.title.strip())}</b>" + (
        f" ({esc(recipe.title_zh.strip())})" if recipe.title_zh and recipe.title_zh.strip() else ""
    ) + "\n" + DOT.join(meta)


def _footer(recipe: Recipe, category: Category) -> str:
    site = recipe.source.site.strip() or "source"
    link = f'{LINK_EMOJI} Full recipe at <a href="{esc_attr(recipe.source.url)}">{esc(site)}</a>'
    return link + "\n" + hashtags_line(recipe, category)


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
    per_serving = _per_serving(recipe)
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


def render_recipe(recipe: Recipe, category: Category) -> list[str]:
    """One flat message: title and meta line, why it fits, ingredients, steps, extras, source link
    and hashtags. When it exceeds 4096 characters it is split between blocks into more messages,
    each continuation starting with the recipe title."""
    why = esc(recipe.why_it_fits.strip()) if recipe.why_it_fits and recipe.why_it_fits.strip() else None
    blocks = [b for b in (_top(recipe, category), why, _ingredients_block(recipe), _steps_block(recipe),
                          _extras_block(recipe, category), _footer(recipe, category)) if b]
    single = "\n\n".join(blocks)
    if len(single) <= TELEGRAM_MAX_CHARS:
        return [single]

    cont = f"{category.emoji} <b>{esc(recipe.title.strip())}</b>{DOT}<i>continued</i>"
    try:
        messages = tg.split_blocks(blocks, TELEGRAM_MAX_CHARS - len(cont) - 2)
    except ValueError as exc:
        raise RenderError(str(exc)) from None
    return [messages[0]] + [f"{cont}\n\n{m}" for m in messages[1:]]


def render_alert(section: str, subtitle: str, name: str, one_liner: str, details: list[str], background: str = "") -> list[str]:
    """Admin alert card as blocks for TelegramClient.send_html. `details` are HTML lines the caller
    already escaped; `background` is plain text, escaped here and folded into an expandable quote."""
    blocks = [header(section, subtitle), "\n".join([f"{BAD_EMOJI} <b>{esc(name)}</b>{DOT}{esc(one_liner)}", *details])]
    if background:
        if len(background) > MAX_BACKGROUND_CHARS:
            background = background[:MAX_BACKGROUND_CHARS].rstrip() + "\n[truncated]"
        blocks.append(tg.DIVIDER + "\n" + tg.expandable("⚙️ <b>Background</b>\n" + tg.esc(background)))
    return blocks

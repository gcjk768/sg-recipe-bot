"""Section 4 of the prompt pack: rendering a recipe (and the admin alerts) as Telegram HTML cards.

Card layout: a header line, one short block per item, then a divider and the long detail in an
expandable blockquote. Every dynamic value goes through `esc`/`esc_attr` before it meets a tag.
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
TIME_EMOJI = "⏱"  # ⏱
SERVES_EMOJI = "\U0001f465"  # 👥
INGREDIENTS_EMOJI = "\U0001f6d2"  # 🛒
STEPS_EMOJI = "\U0001f4dd"  # 📝
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


def _title(recipe: Recipe, category: Category) -> str:
    line = f"{category.emoji} <b>{esc(recipe.title.strip())}</b>"
    if recipe.title_zh and recipe.title_zh.strip():
        line += f" ({esc(recipe.title_zh.strip())})"
    return line


def _per_serving(recipe: Recipe) -> float | None:
    c = recipe.cost
    if c is None:
        return None
    if c.per_serving_sgd is not None:
        return c.per_serving_sgd
    return c.total_sgd / recipe.servings if recipe.servings > 0 else None


def _card(recipe: Recipe, category: Category) -> str:
    """Title line plus at most three short detail lines and the hashtags."""
    cuisine = recipe.cuisine.strip() if recipe.cuisine else ""
    lines = [_title(recipe, category) + (f"{DOT}{esc(cuisine)}" if cuisine else "")]
    lines.append(f"{TIME_EMOJI} {recipe.total_minutes} min{DOT}{esc(recipe.difficulty)}{DOT}{SERVES_EMOJI} serves {recipe.servings}")
    quick = []
    if recipe.nutrition is not None:
        quick.append(f"{NUTRITION_EMOJI} {recipe.nutrition.kcal} kcal")
    if recipe.cost is not None:
        each = _per_serving(recipe)
        quick.append(f"{COST_EMOJI} {format_sgd(recipe.cost.total_sgd)}"
                     + (f" ({format_sgd(round(each, 2))} each)" if each is not None and recipe.servings > 1 else ""))
    if quick:
        lines.append(DOT.join(quick))
    site = recipe.source.site.strip() or "source"
    lines.append(f'{LINK_EMOJI} <a href="{esc_attr(recipe.source.url)}">Recipe</a>{DOT}<i>{esc(site)}</i>')
    lines.append(hashtags_line(recipe, category))
    return "\n".join(lines)


def _ingredient_line(item) -> str:
    parts = [format_qty(item.qty), esc(item.unit), esc(item.item)]
    text = f"{BULLET} " + " ".join(p for p in parts if p)
    if item.note and item.note.strip():
        text += f", {esc(item.note)}"
    return text


def _ingredients_block(recipe: Recipe) -> str:
    return f"{INGREDIENTS_EMOJI} <b>Ingredients</b>\n" + "\n".join(_ingredient_line(i) for i in recipe.ingredients)


def _steps_block(recipe: Recipe) -> str:
    return f"{STEPS_EMOJI} <b>Steps</b>\n" + "\n".join(f"{n}. {esc(step.strip())}" for n, step in enumerate(recipe.steps, start=1))


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
    """One card message normally. When the full card exceeds 4096 characters, the detail is split
    into one expandable quote per section and packed (between blocks) into more messages, each
    continuation starting with the recipe title."""
    head = header(recipe.meals[0] if recipe.meals else "recipe", category.label)
    why = f"<i>{esc(recipe.why_it_fits.strip())}</i>" if recipe.why_it_fits and recipe.why_it_fits.strip() else None
    sections = [s for s in (why, _ingredients_block(recipe), _steps_block(recipe), _extras_block(recipe, category)) if s]
    card = _card(recipe, category)

    single = "\n\n".join([head, card, tg.DIVIDER + "\n" + tg.expandable("\n\n".join(sections))])
    if len(single) <= TELEGRAM_MAX_CHARS:
        return [single]

    cont = _title(recipe, category) + f"{DOT}<i>continued</i>"
    blocks = [head, card, tg.DIVIDER] + [tg.expandable(s) for s in sections]
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

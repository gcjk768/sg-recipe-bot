import re

import pytest

from recipebot.categories import get_category
from recipebot.models import Recipe
from recipebot.render import RenderError, render_alert, render_recipe
from tests.conftest import make_recipe

CARD = """🍳 <b>RECIPE</b> · High protein

🍳 <b>Garlic Soy Chicken with Broccoli</b> · Chinese inspired
⏱ 25 min · easy · 👥 serves 2
🔥 420 kcal · 💰 S$9.50 (S$4.75 each)
🔗 <a href="https://www.example.com/recipes/12345">Recipe</a> · <i>Example Recipes</i>
#highprotein #onepan #weeknight #mealprep"""

WHY = "<i>One pan, about 25 minutes, and every ingredient is a FairPrice regular.</i>"

INGREDIENTS = """🛒 <b>Ingredients</b>
• 400 g chicken thigh, boneless, cut into bite sized pieces
• 1 head broccoli, cut into florets
• 3 clove garlic, minced
• 2 tbsp light soy sauce
• 1 tbsp honey
• 1 tsp cornstarch
• 1 tsp sesame oil"""

STEPS = """📝 <b>Steps</b>
1. Toss the chicken with the cornstarch and a pinch of salt.
2. Heat oil in a pan over high heat and sear the chicken until golden, about 5 minutes.
3. Add the garlic and broccoli and stir fry for 2 minutes.
4. Mix the soy sauce, honey and 3 tbsp water, pour it in and toss until the sauce thickens and coats everything, about 2 minutes.
5. Finish with the sesame oil and serve with rice."""

EXTRAS = """🔥 About 420 kcal per serving, 40 g protein, 20 g carbs, 20 g fat (estimate)
💰 Ingredients about S$9.50 for 2 servings, S$4.75 each, chicken thigh is most of the cost (estimate)
💪 Protein: about 40 g per serving
💡 Swap the broccoli for any green vegetable you have, or add sliced carrot for colour.
🧊 Keeps 3 days in the fridge and reheats well."""

DIVIDER = "━" * 16

EXPECTED = f"{CARD}\n\n{DIVIDER}\n<blockquote expandable>{WHY}\n\n{INGREDIENTS}\n\n{STEPS}\n\n{EXTRAS}</blockquote>"


def render(**overrides):
    recipe = Recipe.model_validate(make_recipe(**overrides))
    return render_recipe(recipe, get_category(recipe.category))


def balanced(text: str) -> bool:
    """Every opened tag is closed, in order: a split never cuts inside a tag."""
    stack = []
    for closing, name in re.findall(r"<(/?)([a-z-]+)[^>]*>", text):
        if closing:
            if not stack or stack.pop() != name:
                return False
        else:
            stack.append(name)
    return not stack


def test_full_template():
    assert render() == [EXPECTED]


def test_meal_header_and_title_zh_and_category_emoji_and_no_protein_line_outside_high_protein():
    [text] = render(category="chinese_daily", title_zh="蒜香豆豉鸡", protein_per_serving_g=40, meals=["dinner"])
    assert text.startswith("🌙 <b>DINNER</b> · Chinese daily\n\n🍳 <b>Garlic Soy Chicken with Broccoli</b> (蒜香豆豉鸡) · ")
    assert "💪" not in text
    assert "#chinese " in text
    [soup] = render(category="soups")
    assert "\n\n🍲 <b>" in soup
    [cake] = render(category="baking_cakes", meals=["breakfast"])
    assert cake.startswith("🌅 <b>BREAKFAST</b>") and "\n\n🧁 <b>" in cake


def test_escaping():
    [text] = render(
        title="Mac & Cheese <fast>",
        why_it_fits="Cheese > everything & quick",
        source={"site": "Bob & Co", "url": 'https://x.com/r?a=1&b="2"'},
        tips=["Use <sharp> cheddar"],
        cuisine="</blockquote><b>",
    )
    assert "<b>Mac &amp; Cheese &lt;fast&gt;</b>" in text
    assert "<i>Cheese &gt; everything &amp; quick</i>" in text
    assert '<a href="https://x.com/r?a=1&amp;b=&quot;2&quot;">Recipe</a> · <i>Bob &amp; Co</i>' in text
    assert "💡 Use &lt;sharp&gt; cheddar" in text
    assert "&lt;/blockquote&gt;&lt;b&gt;" in text and balanced(text)


def test_optional_lines_dropped():
    [text] = render(
        title_zh=None,
        why_it_fits="",
        tips=[],
        storage=None,
        protein_per_serving_g=None,
        nutrition_per_serving=None,
        cost_estimate=None,
        cuisine="",
        tags=[],
    )
    expected = (
        "🍳 <b>RECIPE</b> · High protein\n\n"
        "🍳 <b>Garlic Soy Chicken with Broccoli</b>\n"
        "⏱ 25 min · easy · 👥 serves 2\n"
        '🔗 <a href="https://www.example.com/recipes/12345">Recipe</a> · <i>Example Recipes</i>\n'
        "#highprotein\n\n"
        f"{DIVIDER}\n<blockquote expandable>{INGREDIENTS}\n\n{STEPS}</blockquote>"
    )
    assert text == expected


def test_line_breaks_in_model_text_are_collapsed():
    [text] = render(title="Two\nLines", why_it_fits="a\r\nb", tips=["tip\nwith break"], storage="keep\n\ncold")
    assert "<b>Two Lines</b>" in text and "<i>a b</i>" in text and "💡 tip with break" in text and "🧊 keep cold" in text


def test_blank_unit_has_no_double_space():
    [text] = render(ingredients=[{"item": "eggs", "qty": 3, "unit": "", "note": ""}])
    assert "• 3 eggs\n" in text


def test_quantities_and_notes():
    [text] = render(ingredients=[
        {"item": "egg", "qty": 0.5, "unit": "piece", "note": None},
        {"item": "flour", "qty": 200.0, "unit": "g", "note": " sifted "},
        {"item": "milk", "qty": 1.25, "unit": "ml", "note": ""},
    ])
    assert "• 0.5 piece egg\n• 200 g flour, sifted\n• 1.25 ml milk" in text


def test_cost_line_variants():
    [text] = render(cost_estimate={"total_sgd": 10, "per_serving_sgd": None, "note": ""}, servings=4)
    assert "💰 Ingredients about S$10 for 4 servings, S$2.50 each (estimate)" in text
    assert "💰 S$10 (S$2.50 each)" in text
    [fixed] = render(cost_estimate={"total_sgd": 10, "per_serving_sgd": 30, "note": "typo"}, servings=2)
    assert "💰 Ingredients about S$10 for 2 servings, S$5 each, typo (estimate)" in fixed
    [single] = render(cost_estimate={"total_sgd": 6, "per_serving_sgd": 6, "note": "prawns"}, servings=1)
    assert "💰 Ingredients about S$6, prawns (estimate)" in single and "💰 S$6\n" in single
    [bad] = render(cost_estimate={"total_sgd": -3, "per_serving_sgd": 1, "note": ""})
    assert "💰" not in bad
    [notobj] = render(cost_estimate="cheap", nutrition_per_serving=[1, 2])
    assert "💰" not in notobj and "🔥" not in notobj


def test_nutrition_line_partial():
    [text] = render(nutrition_per_serving={"kcal": 300, "protein_g": 12})
    assert "🔥 About 300 kcal per serving, 12 g protein (estimate)" in text and "🔥 300 kcal" in text
    [none] = render(nutrition_per_serving={"protein_g": 12})
    assert "🔥" not in none


def test_tags_dedupe_and_skip_category_tag():
    [text] = render(tags=["one pan", "One Pan", "highprotein", "under 20 minutes"])
    assert "\n#highprotein #onepan #under20minutes\n" in text


def test_split_when_long_happens_between_blocks_and_never_inside_a_tag():
    messages = render(why_it_fits="w" * 2300, tips=["x" * 2300])
    assert len(messages) == 2
    first, second = messages
    assert first.startswith(CARD + f"\n\n{DIVIDER}\n\n<blockquote expandable><i>")
    assert second.startswith("🍳 <b>Garlic Soy Chicken with Broccoli</b> · <i>continued</i>\n\n<blockquote expandable>")
    assert "💡 " + "x" * 2300 in second
    assert all(len(m) <= 4096 and balanced(m) for m in messages)
    # nothing lost: every section is in exactly one message
    joined = "\n".join(messages)
    for part in (INGREDIENTS, STEPS, "w" * 2300):
        assert joined.count(part) == 1


def test_render_error_when_impossible():
    with pytest.raises(RenderError):
        render(tips=["x" * 5000])


def test_meal_hashtags_lead_and_bad_meals_are_dropped():
    recipe = Recipe.model_validate(make_recipe(meals=["Dinner", "brunch", "supper", "dinner", "lunch"]))
    assert recipe.meals == ["dinner", "supper"]  # unknown and duplicate meals dropped, at most two
    [text] = render_recipe(recipe, get_category("high_protein"))
    assert "\n#dinner #supper #highprotein" in text and text.startswith("🌙 <b>DINNER</b>")
    assert Recipe.model_validate(make_recipe(meals=None)).meals == []
    toast = Recipe.model_validate(make_recipe(category="breakfast", meals=["breakfast"], tags=["sweet"], protein_per_serving_g=None))
    assert "\n#breakfast #sweet\n" in render_recipe(toast, get_category("breakfast"))[0]


def test_alert_card_escapes_and_truncates_background():
    blocks = render_alert("failed", "posted nothing", "A<b>", "status x&y", ["🆔 <code>r1</code>"], background="<oops>" + "z" * 5000)
    assert blocks[0] == "🚨 <b>RECIPEBOT</b> · posted nothing"
    assert blocks[1] == "🔴 <b>A&lt;b&gt;</b> · status x&amp;y\n🆔 <code>r1</code>"
    assert blocks[2].startswith(f"{DIVIDER}\n<blockquote expandable>⚙️ <b>Background</b>\n&lt;oops&gt;")
    assert blocks[2].endswith("[truncated]</blockquote>") and len(blocks[2]) < 4096 and balanced(blocks[2])

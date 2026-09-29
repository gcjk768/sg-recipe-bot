import pytest

from recipebot.categories import get_category
from recipebot.models import Recipe
from recipebot.render import RenderError, render_recipe
from tests.conftest import make_recipe

EXPECTED = """🍳 <b>Garlic Soy Chicken with Broccoli</b>
<i>High protein · Chinese inspired · 25 min · easy · serves 2</i>

One pan, about 25 minutes, and every ingredient is a FairPrice regular.

<b>Ingredients</b>
• 400 g chicken thigh, boneless, cut into bite sized pieces
• 1 head broccoli, cut into florets
• 3 clove garlic, minced
• 2 tbsp light soy sauce
• 1 tbsp honey
• 1 tsp cornstarch
• 1 tsp sesame oil

<b>Steps</b>
1. Toss the chicken with the cornstarch and a pinch of salt.
2. Heat oil in a pan over high heat and sear the chicken until golden, about 5 minutes.
3. Add the garlic and broccoli and stir fry for 2 minutes.
4. Mix the soy sauce, honey and 3 tbsp water, pour it in and toss until the sauce thickens and coats everything, about 2 minutes.
5. Finish with the sesame oil and serve with rice.

🔥 About 420 kcal per serving, 40 g protein, 20 g carbs, 20 g fat (estimate)
💰 Ingredients about S$9.50 for 2 servings, S$4.75 each, chicken thigh is most of the cost (estimate)
💪 Protein: about 40 g per serving
💡 Swap the broccoli for any green vegetable you have, or add sliced carrot for colour.
🧊 Keeps 3 days in the fridge and reheats well.

🔗 <a href="https://www.example.com/recipes/12345">Full recipe at Example Recipes</a>
#highprotein #onepan #weeknight #mealprep"""


def render(**overrides):
    recipe = Recipe.model_validate(make_recipe(**overrides))
    return render_recipe(recipe, get_category(recipe.category))


def test_full_template():
    assert render() == [EXPECTED]


def test_title_zh_and_category_emoji_and_no_protein_line_outside_high_protein():
    [text] = render(category="chinese_daily", title_zh="蒜香豆豉鸡", protein_per_serving_g=40)
    assert text.startswith("🍳 <b>Garlic Soy Chicken with Broccoli</b> (蒜香豆豉鸡)\n<i>Chinese daily · ")
    assert "💪" not in text
    assert "#chinese " in text
    [soup] = render(category="soups")
    assert soup.startswith("🍲 ")
    [cake] = render(category="baking_cakes")
    assert cake.startswith("🧁 ")


def test_escaping():
    [text] = render(
        title="Mac & Cheese <fast>",
        why_it_fits="Cheese > everything & quick",
        source={"site": "Bob & Co", "url": "https://x.com/r?a=1&b=2"},
        tips=["Use <sharp> cheddar"],
    )
    assert "<b>Mac &amp; Cheese &lt;fast&gt;</b>" in text
    assert "Cheese &gt; everything &amp; quick" in text
    assert '<a href="https://x.com/r?a=1&amp;b=2">Full recipe at Bob &amp; Co</a>' in text
    assert "💡 Use &lt;sharp&gt; cheddar" in text


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
        "🍳 <b>Garlic Soy Chicken with Broccoli</b>\n"
        "<i>High protein · 25 min · easy · serves 2</i>\n\n"
        + EXPECTED.split("\n\n")[2] + "\n\n"  # ingredients block
        + EXPECTED.split("\n\n")[3] + "\n\n"  # steps block
        + '🔗 <a href="https://www.example.com/recipes/12345">Full recipe at Example Recipes</a>\n#highprotein'
    )
    assert text == expected


def test_line_breaks_in_model_text_are_collapsed():
    [text] = render(title="Two\nLines", why_it_fits="a\r\nb", tips=["tip\nwith break"], storage="keep\n\ncold")
    assert "<b>Two Lines</b>" in text and "\n\na b\n\n" in text and "💡 tip with break" in text and "🧊 keep cold" in text


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
    [fixed] = render(cost_estimate={"total_sgd": 10, "per_serving_sgd": 30, "note": "typo"}, servings=2)
    assert "💰 Ingredients about S$10 for 2 servings, S$5 each, typo (estimate)" in fixed
    [single] = render(cost_estimate={"total_sgd": 6, "per_serving_sgd": 6, "note": "prawns"}, servings=1)
    assert "💰 Ingredients about S$6, prawns (estimate)" in single
    [bad] = render(cost_estimate={"total_sgd": -3, "per_serving_sgd": 1, "note": ""})
    assert "💰" not in bad
    [notobj] = render(cost_estimate="cheap", nutrition_per_serving=[1, 2])
    assert "💰" not in notobj and "🔥" not in notobj


def test_nutrition_line_partial():
    [text] = render(nutrition_per_serving={"kcal": 300, "protein_g": 12})
    assert "🔥 About 300 kcal per serving, 12 g protein (estimate)" in text
    [none] = render(nutrition_per_serving={"protein_g": 12})
    assert "🔥" not in none


def test_tags_dedupe_and_skip_category_tag():
    [text] = render(tags=["one pan", "One Pan", "highprotein", "under 20 minutes"])
    assert text.endswith("#highprotein #onepan #under20minutes")


def test_split_when_long():
    messages = render(why_it_fits="w" * 2300, tips=["x" * 2300])
    assert len(messages) == 2
    first, second = messages
    assert first.startswith("🍳 <b>Garlic Soy Chicken with Broccoli</b>\n<i>")
    assert "<b>Ingredients</b>" in first and "<b>Steps</b>" not in first
    assert second.startswith("🍳 <b>Garlic Soy Chicken with Broccoli</b>\n\n<b>Steps</b>")
    assert "#highprotein" in second and "💡" in second
    assert all(len(m) <= 4096 for m in messages)


def test_render_error_when_impossible():
    with pytest.raises(RenderError):
        render(tips=["x" * 5000])


def test_split_output_is_exact():
    first, second = render(why_it_fits="w" * 2300, tips=["x" * 2300])
    assert first == EXPECTED.split("\n\n<b>Steps</b>")[0].replace(
        "One pan, about 25 minutes, and every ingredient is a FairPrice regular.", "w" * 2300
    )
    assert second == "🍳 <b>Garlic Soy Chicken with Broccoli</b>\n\n<b>Steps</b>" + EXPECTED.split("<b>Steps</b>")[1].replace(
        "Swap the broccoli for any green vegetable you have, or add sliced carrot for colour.", "x" * 2300
    )

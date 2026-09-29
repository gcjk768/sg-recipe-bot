import pytest

from recipebot.history import History
from recipebot.models import ModelReply, Recipe
from recipebot.validate import (
    check_static,
    check_time_caps,
    counted_ingredients,
    is_free_ingredient,
    main_ingredient,
    soft_warnings,
    validate_reply,
)
from recipebot.categories import get_category
from recipebot.web import Fetcher
from tests.conftest import RECIPE_HTML, FakeResponse, FakeSession, make_recipe, make_reply

URL = "https://www.example.com/recipes/12345"


def _reply(recipes, category="high_protein"):
    return ModelReply.model_validate(make_reply(recipes, category=category))


def _fetcher(routes=None, default=None):
    return Fetcher(session=FakeSession(routes or {URL: FakeResponse(200, body=RECIPE_HTML)}, default=default))


@pytest.mark.parametrize(
    "item",
    ["salt", "Sea salt", "salt, to taste", "salt and pepper", "white pepper", "black pepper", "ground black pepper",
     "sugar", "brown sugar", "caster sugar", "oil", "cooking oil", "vegetable oil", "olive oil", "water", "hot water",
     "boiling water", "Salt (optional)"],
)
def test_free_ingredients(item):
    assert is_free_ingredient(item)


@pytest.mark.parametrize("item", ["sesame oil", "chilli oil", "bell pepper", "red pepper", "coconut water", "rose water", "palm sugar syrup", "salted egg", "sugar snap peas", "soy sauce", "chicken"])
def test_counted_ingredients(item):
    assert not is_free_ingredient(item)


def test_ingredient_cap_ignores_free_items():
    items = [{"item": f"thing {i}", "qty": 1, "unit": "g", "note": ""} for i in range(10)]
    items += [{"item": "salt", "qty": 1, "unit": "pinch", "note": ""}, {"item": "water", "qty": 100, "unit": "ml", "note": ""}]
    recipe = Recipe.model_validate(make_recipe(ingredients=items))
    assert len(counted_ingredients(recipe)) == 10
    assert check_static(recipe, "high_protein") is None
    items.append({"item": "one more", "qty": 1, "unit": "g", "note": ""})
    recipe = Recipe.model_validate(make_recipe(ingredients=items))
    assert "11 ingredients" in check_static(recipe, "high_protein")


@pytest.mark.parametrize(
    "category,prep,total,ok",
    [
        ("high_protein", 10, 45, True), ("high_protein", 10, 46, False),
        ("soups", 15, 90, True), ("soups", 16, 60, False), ("soups", 10, 91, False),
        ("rice_cooker", 20, 60, True), ("rice_cooker", 20, 61, False),
        ("noodles", 5, 30, True), ("noodles", 5, 31, False),
        ("sides", 5, 15, True), ("sides", 5, 16, False),
        ("quick_20", 5, 20, True), ("quick_20", 5, 21, False),
        ("meal_prep", 20, 60, True), ("breakfast", 5, 21, False), ("use_it_up", 5, 31, False),
        ("desserts_no_oven", 20, 240, True), ("desserts_no_oven", 21, 30, False),
        ("baking_cakes", 30, 120, True), ("baking_cakes", 31, 60, False),
        ("sauces_basics", 5, 30, True), ("custom", 5, 45, True), ("custom", 5, 46, False),
    ],
)
def test_time_caps(category, prep, total, ok):
    recipe = Recipe.model_validate(make_recipe(category=category, prep_minutes=prep, cook_minutes=max(total - prep, 0), total_minutes=total))
    problem = check_time_caps(recipe, get_category(category))
    assert (problem is None) == ok, problem


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"category": "noodles"}, "does not match"),
        ({"difficulty": "hard"}, "difficulty"),
        ({"steps": []}, "steps"),
        ({"steps": ["x"] * 9}, "9 steps"),
        ({"steps": ["a" * 221]}, "221 characters"),
        ({"steps": ["ok", "   "]}, "step 2 is empty"),
        ({"source": {"site": "s", "url": "http://insecure.example.com/r"}}, "https://"),
        ({"ingredients": []}, "no ingredients"),
        ({"total_minutes": 0}, "positive"),
        ({"title": "  "}, "title is empty"),
    ],
)
def test_static_rejections(overrides, fragment):
    recipe = Recipe.model_validate(make_recipe(**overrides))
    assert fragment in (check_static(recipe, "high_protein") or "")


def test_step_of_exactly_220_chars_passes():
    recipe = Recipe.model_validate(make_recipe(steps=["a" * 220]))
    assert check_static(recipe, "high_protein") is None


def test_schema_failure_is_a_rejection_not_a_crash():
    result = validate_reply(_reply([{"title": "Broken", "servings": "two"}]), "high_protein", history=None, fetcher=None, check_pages=False)
    assert result.accepted == []
    assert result.rejected[0].title == "Broken"
    assert "schema" in result.rejected[0].reason


def test_qty_as_numeric_string_is_coerced():
    recipe = make_recipe()
    recipe["ingredients"][0]["qty"] = "400"
    result = validate_reply(_reply([recipe]), "high_protein", history=None, fetcher=None, check_pages=False)
    assert result.accepted and result.accepted[0].ingredients[0].qty == 400


def test_history_duplicates(tmp_path):
    with History(tmp_path / "h.sqlite") as history:
        history.add_sent(Recipe.model_validate(make_recipe()), main_ingredient="chicken thigh", run_id="r0")
        same_url = make_recipe(title="Totally new name", source={"site": "s", "url": "https://example.com/recipes/12345/"})
        same_title = make_recipe(title="garlic soy chicken with broccoli", source={"site": "s", "url": "https://example.com/other"})
        fresh = make_recipe(title="Fresh dish", source={"site": "s", "url": "https://example.com/fresh"})
        result = validate_reply(_reply([same_url, same_title, fresh]), "high_protein", history=history, fetcher=None, check_pages=False)
        assert [r.title for r in result.accepted] == ["Fresh dish"]
        assert "already sent" in result.rejected[0].reason and "same title" in result.rejected[1].reason


def test_in_run_duplicates():
    a = make_recipe()
    b = make_recipe(title="Same again", source={"site": "s", "url": "https://www.example.com/recipes/12345?utm_source=x"})
    c = make_recipe(title="Garlic Soy Chicken With Broccoli", source={"site": "s", "url": "https://example.com/c"})
    result = validate_reply(_reply([a, b, c]), "high_protein", history=None, fetcher=None, check_pages=False)
    assert [r.title for r in result.accepted] == [a["title"]]
    assert "url of another" in result.rejected[0].reason and "title of another" in result.rejected[1].reason


def test_page_check_passes_on_200_with_ingredients():
    result = validate_reply(_reply([make_recipe()]), "high_protein", history=None, fetcher=_fetcher())
    assert len(result.accepted) == 1


def test_page_check_accepts_schema_org_without_the_word_ingredients():
    html = '<html><script type="application/ld+json">{"@type":"Recipe","name":"x"}</script><body>hello</body></html>'
    result = validate_reply(_reply([make_recipe()]), "high_protein", history=None, fetcher=_fetcher({URL: FakeResponse(200, body=html)}))
    assert len(result.accepted) == 1


@pytest.mark.parametrize(
    "response,fragment",
    [
        (FakeResponse(404, body=RECIPE_HTML), "HTTP 404"),
        (FakeResponse(200, body="<html><body>Nothing to see</body></html>"), "does not mention ingredients"),
        (FakeResponse(200, url="https://www.example.com/", body=RECIPE_HTML), "redirected to the homepage"),
        (ConnectionError("boom"), "could not be fetched"),
    ],
)
def test_page_check_rejections(response, fragment):
    result = validate_reply(_reply([make_recipe()]), "high_protein", history=None, fetcher=_fetcher({URL: response}))
    assert result.accepted == []
    assert fragment in result.rejected[0].reason


def test_page_check_requires_fetcher():
    with pytest.raises(ValueError):
        validate_reply(_reply([make_recipe()]), "high_protein", history=None, fetcher=None, check_pages=True)


def test_error_reply_and_unknown_category():
    reply = ModelReply.model_validate({"error": "x", "run": {"category": "soups"}, "recipes": []})
    assert validate_reply(reply, "soups", history=None, fetcher=None, check_pages=False).accepted == []
    bad = validate_reply(_reply([make_recipe()]), "pizza", history=None, fetcher=None, check_pages=False)
    assert bad.rejected and "unknown brief category" in bad.rejected[0].reason


def test_soft_warnings():
    recipe = Recipe.model_validate(make_recipe(
        category="meal_prep", servings=2, storage=None, tips=["a", "b", "c"], tags=["one pan", "made up"],
        nutrition_per_serving={"kcal": -5}, cost_estimate=None,
    ))
    recipe.ingredients[0].unit = "cup"
    warnings = soft_warnings(recipe, "meal_prep")
    joined = "\n".join(warnings)
    for fragment in ("units outside", "tags outside", "3 tips", "without storage", "servings 2", "implausible nutrition", "no cost_estimate", "protein_per_serving_g set outside"):
        assert fragment in joined, fragment
    assert soft_warnings(Recipe.model_validate(make_recipe()), "high_protein") == []


def test_main_ingredient_skips_staples_and_seasoning():
    recipe = Recipe.model_validate(make_recipe(ingredients=[
        {"item": "salt", "qty": 1, "unit": "pinch", "note": ""},
        {"item": "garlic", "qty": 2, "unit": "clove", "note": ""},
        {"item": "salmon fillet, skin on", "qty": 300, "unit": "g", "note": ""},
    ], pantry_staples=["garlic"]))
    assert main_ingredient(recipe) == "salmon fillet"
    assert main_ingredient(Recipe.model_validate(make_recipe())) == "chicken thigh"

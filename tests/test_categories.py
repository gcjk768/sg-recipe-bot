from recipebot.categories import CATEGORIES, DAILY_CORE, get_category, is_known

EXPECTED = {
    # key: (label, hashtag, total_cap, hands_on_cap)
    "high_protein": ("High protein", "highprotein", 45, None),
    "chinese_daily": ("Chinese daily", "chinese", 45, None),
    "western_daily": ("Western daily", "western", 45, None),
    "asian_daily": ("Asian daily", "asian", 45, None),
    "local_sg": ("Singapore favourites", "localsg", 45, None),
    "soups": ("Soups", "soups", 90, 15),
    "rice_cooker": ("Rice cooker meals", "ricecooker", 60, None),
    "noodles": ("Noodles", "noodles", 30, None),
    "seafood": ("Seafood", "seafood", 30, None),
    "eggs_tofu_veg": ("Eggs, tofu and vegetables", "meatfree", 30, None),
    "sides": ("Vegetable sides", "sides", 15, None),
    "quick_20": ("Under 20 minutes", "quick", 20, None),
    "meal_prep": ("Meal prep and lunchbox", "mealprep", 60, None),
    "breakfast": ("Breakfast and brunch", "breakfast", 20, None),
    "use_it_up": ("Use it up", "useitup", 30, None),
    "desserts_no_oven": ("No oven desserts", "desserts", None, 20),
    "baking_cakes": ("Baking and cakes", "baking", None, 30),
    "sauces_basics": ("Sauces and basics", "basics", 30, None),
    "custom": ("Custom", "custom", 45, None),
}


def test_table_matches_prompt_pack():
    assert set(CATEGORIES) == set(EXPECTED)
    for key, (label, tag, total, hands_on) in EXPECTED.items():
        cat = CATEGORIES[key]
        assert cat.key == key
        assert cat.label == label
        assert cat.hashtag == tag
        assert cat.total_cap == total
        assert cat.hands_on_cap == hands_on
        assert cat.total_cap is not None or cat.hands_on_cap is not None


def test_emojis_by_category():
    assert CATEGORIES["baking_cakes"].emoji == "🧁"
    assert CATEGORIES["desserts_no_oven"].emoji == "🧁"
    assert CATEGORIES["soups"].emoji == "🍲"
    assert CATEGORIES["rice_cooker"].emoji == "🍲"
    assert CATEGORIES["noodles"].emoji == "🍜"
    assert CATEGORIES["sides"].emoji == "🥗"
    assert CATEGORIES["eggs_tofu_veg"].emoji == "🥗"
    for key in ("high_protein", "chinese_daily", "seafood", "custom", "breakfast"):
        assert CATEGORIES[key].emoji == "🍳"


def test_lookup_helpers():
    assert get_category("noodles").label == "Noodles"
    assert is_known("soups") and not is_known("pizza")
    assert DAILY_CORE == ["high_protein", "chinese_daily", "western_daily", "asian_daily", "local_sg"]
    try:
        get_category("pizza")
    except KeyError as exc:
        assert "unknown category" in str(exc)
    else:
        raise AssertionError("expected KeyError")

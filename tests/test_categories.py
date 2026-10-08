from recipebot.categories import CATEGORIES, get_category, is_known


def test_categories():
    assert list(CATEGORIES) == ["high_protein", "baking_cakes"]
    assert CATEGORIES["baking_cakes"].hands_on_cap == 30 and CATEGORIES["baking_cakes"].total_cap is None
    cat = CATEGORIES["high_protein"]
    assert (cat.label, cat.hashtag, cat.total_cap, cat.hands_on_cap, cat.emoji) == ("High protein", "highprotein", 45, None, "🍳")


def test_lookup_helpers():
    assert get_category("high_protein").label == "High protein"
    assert is_known("high_protein") and not is_known("soups")
    try:
        get_category("soups")
    except KeyError as exc:
        assert "unknown category" in str(exc)
    else:
        raise AssertionError("expected KeyError")

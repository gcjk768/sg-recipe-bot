import pytest

from recipebot.textutil import format_qty, format_sgd, hashtag, main_ingredient_name, normalise_title, normalise_url


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Garlic Soy Chicken with Broccoli", "garlic soy chicken with broccoli"),
        ("Mum's  Best Chicken!!", "mum s best chicken"),
        ("番茄炒蛋 (Tomato Egg)", "番茄炒蛋 tomato egg"),
        ("  Spaced   out  ", "spaced out"),
    ],
)
def test_normalise_title(title, expected):
    assert normalise_title(title) == expected


def test_normalise_url_variants_collapse():
    base = normalise_url("https://www.example.com/recipes/12345")
    assert base == normalise_url("https://example.com/recipes/12345/")
    assert base == normalise_url("HTTPS://Example.com/recipes/12345?utm_source=tg&fbclid=x#steps")
    assert base != normalise_url("https://example.com/recipes/12346")
    assert normalise_url("https://example.com/r?id=5&utm_medium=x") == "https://example.com/r?id=5"


@pytest.mark.parametrize("qty,expected", [(400, "400"), (400.0, "400"), (0.5, "0.5"), (1.25, "1.25"), (2.0, "2"), (0.333, "0.33")])
def test_format_qty(qty, expected):
    assert format_qty(qty) == expected


@pytest.mark.parametrize("amount,expected", [(9.5, "S$9.50"), (10, "S$10"), (10.0, "S$10"), (4.746, "S$4.75"), (0.5, "S$0.50")])
def test_format_sgd(amount, expected):
    assert format_sgd(amount) == expected


@pytest.mark.parametrize("tag,expected", [("one pan", "#onepan"), ("meal prep", "#mealprep"), ("under 20 minutes", "#under20minutes"), ("kid-friendly", "#kidfriendly"), ("  ", "")])
def test_hashtag(tag, expected):
    assert hashtag(tag) == expected


@pytest.mark.parametrize(
    "item,expected",
    [("chicken thigh, boneless", "chicken thigh"), ("Salmon fillet (skin on)", "salmon fillet"), ("eggs", "eggs"),
     ("chicken thighs (boneless, skinless)", "chicken thighs"), ("prawns (about 12), peeled", "prawns")],
)
def test_main_ingredient_name(item, expected):
    assert main_ingredient_name(item) == expected


def test_single_line():
    from recipebot.textutil import single_line

    assert single_line("a\nb\r\n  c\t d ") == "a b c d"
    assert single_line(None) == ""

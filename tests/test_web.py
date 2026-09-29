import json

import pytest

from recipebot.web import (
    Fetcher,
    extract_recipe_jsonld,
    is_homepage,
    iso_duration_to_minutes,
    page_has_recipe_markup,
    page_looks_like_recipe,
    page_title,
    recipe_node_author,
    recipe_node_title,
    recipe_node_to_text,
)
from tests.conftest import FakeResponse, FakeSession

RECIPE_NODE = {
    "@context": "https://schema.org",
    "@type": "Recipe",
    "name": "Tomato &amp; Egg Stir Fry",
    "author": {"@type": "Person", "name": "Cook A"},
    "recipeYield": ["2", "2 servings"],
    "prepTime": "PT10M",
    "cookTime": "PT15M",
    "totalTime": "PT25M",
    "recipeCuisine": "Chinese",
    "recipeIngredient": ["3 eggs", "2 tomatoes, <b>diced</b>", "1 tsp sugar"],
    "recipeInstructions": [
        {"@type": "HowToSection", "name": "Prep", "itemListElement": [{"@type": "HowToStep", "text": "Beat the eggs."}]},
        {"@type": "HowToStep", "text": "Fry the tomatoes until soft."},
        "Fold in the eggs and serve.",
    ],
}


def _page(data, attrs=""):
    return f'<html><head><script type="application/ld+json"{attrs}>{json.dumps(data)}</script></head><body>x</body></html>'


def test_extract_direct_node():
    nodes = extract_recipe_jsonld(_page(RECIPE_NODE))
    assert len(nodes) == 1 and nodes[0]["name"] == "Tomato &amp; Egg Stir Fry"


def test_extract_from_graph_and_list_and_type_list():
    graph = {"@context": "https://schema.org", "@graph": [{"@type": "WebPage"}, {"@type": ["Recipe", "NewsArticle"], "name": "G"}]}
    assert extract_recipe_jsonld(_page(graph))[0]["name"] == "G"
    assert extract_recipe_jsonld(_page([{"@type": "Organization"}, RECIPE_NODE]))[0]["name"].startswith("Tomato")


def test_extract_tolerates_cdata_comments_and_bad_json():
    html = (
        '<script type="application/ld+json">//<![CDATA[\n' + json.dumps(RECIPE_NODE) + "\n//]]></script>"
        '<script type="application/ld+json"><!-- {"@type":"Recipe","name":"C"} --></script>'
        '<script type="application/ld+json">{broken</script>'
        "<SCRIPT TYPE='application/ld+json' class='x'>{\"@type\":\"Recipe\",\"name\":\"D\"}</SCRIPT>"
    )
    names = [n["name"] for n in extract_recipe_jsonld(html)]
    assert names == ["Tomato &amp; Egg Stir Fry", "C", "D"]


def test_markup_and_ingredient_detection():
    assert page_has_recipe_markup('<div itemscope itemtype="http://schema.org/Recipe">')
    assert not page_has_recipe_markup("<html>plain</html>")
    assert page_looks_like_recipe("<p>Ingredients: eggs</p>")
    assert page_looks_like_recipe("<p>INGREDIENT list</p>")
    assert not page_looks_like_recipe("<p>Nothing here</p>")
    assert page_title("<html><title> Hello &amp; World </title></html>") == "Hello & World"
    assert page_title("<html></html>") is None


@pytest.mark.parametrize("value,expected", [("PT25M", 25), ("PT1H30M", 90), ("P0DT0H45M", 45), ("PT90S", 2), ("PT2H", 120), ("P1D", 1440), ("nonsense", None), (None, None), ("P", None)])
def test_iso_duration(value, expected):
    assert iso_duration_to_minutes(value) == expected


def test_recipe_node_to_text():
    text = recipe_node_to_text(RECIPE_NODE)
    assert text.splitlines()[0] == "Name: Tomato & Egg Stir Fry"
    assert "Author: Cook A" in text
    assert "Yield: 2, 2 servings" in text
    assert "Times: Prep 10 min, Cook 15 min, Total 25 min" in text
    assert "Cuisine: Chinese" in text
    assert "- 2 tomatoes, diced" in text
    assert "[Prep]\n1. Beat the eggs.\n2. Fry the tomatoes until soft.\n3. Fold in the eggs and serve." in text
    assert recipe_node_title({"headline": "H"}) == "H"
    assert recipe_node_author({"author": [{"name": "A"}, {"name": "B"}]}) == "A, B"
    assert recipe_node_author({}) is None


def test_instructions_as_single_string():
    text = recipe_node_to_text({"@type": "Recipe", "name": "S", "recipeInstructions": "Step one.\nStep two."})
    assert "1. Step one.\n2. Step two." in text


@pytest.mark.parametrize("url,expected", [("https://x.com", True), ("https://x.com/", True), ("https://x.com/?q=1", False), ("https://x.com/recipes/1", False)])
def test_is_homepage(url, expected):
    assert is_homepage(url) is expected


def test_fetcher_success_and_headers():
    session = FakeSession({"https://a.com/r": FakeResponse(200, url="https://a.com/r/", body="<p>Ingredients</p>", encoding="utf-8")})
    result = Fetcher(session=session, timeout=5).fetch("https://a.com/r")
    assert result.ok and result.status == 200 and result.final_url == "https://a.com/r/" and "Ingredients" in result.text
    call = session.gets[0]
    assert call["timeout"] == 5 and call["allow_redirects"] is True and call["stream"] is True
    assert "Mozilla" in call["headers"]["User-Agent"]


def test_fetcher_caps_bytes_and_handles_bad_encoding():
    big = FakeResponse(200, body=b"a" * 200_000, encoding="not-an-encoding")
    result = Fetcher(session=FakeSession({"u": big}), max_bytes=70_000).fetch("u")
    assert result.ok and 65536 <= len(result.text) <= 131072
    assert big.closed


def test_fetcher_errors():
    result = Fetcher(session=FakeSession({"u": TimeoutError("slow")})).fetch("u")
    assert not result.ok and result.status is None and "TimeoutError" in result.error
    result = Fetcher(session=FakeSession({})).fetch("u")
    assert not result.ok and result.status == 404 and result.error is None


def test_decode_body_prefers_header_then_meta_then_utf8():
    from recipebot.web import decode_body

    zh = "番茄炒蛋 Ingredients"
    assert decode_body(zh.encode("utf-8"), "text/html; charset=utf-8") == zh
    assert decode_body(zh.encode("gb18030"), "text/html; charset=GB18030") == zh
    meta = ('<html><head><meta charset="gbk"><title>x</title></head>' + zh).encode("gbk")
    assert zh in decode_body(meta, "text/html")
    meta2 = ('<meta http-equiv="Content-Type" content="text/html; charset=utf-8">' + zh).encode("utf-8")
    assert zh in decode_body(meta2, "")
    assert decode_body(zh.encode("utf-8"), "text/html") == zh  # no charset anywhere: utf-8, not latin-1
    assert decode_body(b"caf\xe9", "text/html") == "caf\ufffd"  # invalid utf-8 falls back to replacement
    assert decode_body(zh.encode("utf-8"), "text/html; charset=not-a-real-charset") == zh


def test_fetcher_ignores_requests_latin1_default():
    zh = "<p>番茄炒蛋</p><p>Ingredients</p>"
    response = FakeResponse(200, body=zh.encode("utf-8"), encoding="ISO-8859-1")
    response.headers = {"content-type": "text/html"}
    result = Fetcher(session=FakeSession({"u": response})).fetch("u")
    assert "番茄炒蛋" in result.text


def test_decode_body_bom_and_browser_labels():
    import codecs

    from recipebot.web import decode_body

    zh = "番茄炒蛋"
    assert decode_body(codecs.BOM_UTF8 + zh.encode("utf-8"), "text/html; charset=ISO-8859-1") == zh
    assert decode_body(zh.encode("gb18030"), 'text/html; charset="gb2312"') == zh
    assert decode_body("caf\u00e9 \u2019".encode("cp1252"), "text/html; charset=iso-8859-1") == "caf\u00e9 \u2019"
    assert decode_body(zh.encode("utf-8"), "TEXT/HTML; CHARSET=UTF8") == zh

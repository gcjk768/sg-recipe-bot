import json
import random

from recipebot.candidates import build_candidate_page, format_candidate_pages, gather_candidates, load_candidate_urls
from recipebot.web import Fetcher
from tests.conftest import FakeResponse, FakeSession

NODE = {"@type": "Recipe", "name": "Kimchi Fried Rice", "recipeIngredient": ["rice", "kimchi"], "recipeInstructions": [{"@type": "HowToStep", "text": "Fry it."}]}
PAGE = f'<html><head><title>Kimchi Fried Rice | Site</title><script type="application/ld+json">{json.dumps(NODE)}</script></head></html>'


def _dir(tmp_path, category, lines):
    d = tmp_path / "candidates"
    d.mkdir(exist_ok=True)
    (d / f"{category}.txt").write_text("\n".join(lines), encoding="utf-8")
    return d


def test_load_urls_skips_comments_blanks_and_duplicates(tmp_path):
    d = _dir(tmp_path, "noodles", ["# header", "", "https://a.com/1", "https://a.com/1/", "  https://a.com/2  ", "https://www.a.com/1?utm_source=x"])
    assert load_candidate_urls(d, "noodles") == ["https://a.com/1", "https://a.com/2"]
    assert load_candidate_urls(d, "soups") == []


def test_build_candidate_page_uses_schema_org_data():
    fetcher = Fetcher(session=FakeSession({"https://a.com/1": FakeResponse(200, body=PAGE)}))
    page = build_candidate_page("https://a.com/1", fetcher)
    assert page.title == "Kimchi Fried Rice"
    assert page.content.startswith("Name: Kimchi Fried Rice\nIngredients:\n- rice\n- kimchi\nInstructions:\n1. Fry it.")
    assert build_candidate_page("https://a.com/nope", fetcher) is None
    plain = Fetcher(session=FakeSession({"https://a.com/2": FakeResponse(200, body="<html>no schema</html>")}))
    assert build_candidate_page("https://a.com/2", plain) is None


def test_gather_excludes_sent_and_skips_failures(tmp_path):
    urls = [f"https://a.com/{i}" for i in range(6)]
    d = _dir(tmp_path, "noodles", urls)
    routes = {u: FakeResponse(200, body=PAGE) for u in urls}
    routes["https://a.com/3"] = FakeResponse(500, body="")
    fetcher = Fetcher(session=FakeSession(routes))
    pages = gather_candidates(d, "noodles", fetcher, exclude_urls={"https://a.com/0", "https://a.com/1"}, n=3, rng=random.Random(1))
    got = {p.url for p in pages}
    assert len(pages) == 3
    assert got <= {"https://a.com/2", "https://a.com/4", "https://a.com/5"}
    assert gather_candidates(d, "soups", fetcher) == []
    assert gather_candidates(d, "noodles", fetcher, exclude_urls={f"https://a.com/{i}" for i in range(6)}) == []


def test_format_blocks(tmp_path):
    fetcher = Fetcher(session=FakeSession({"https://a.com/1": FakeResponse(200, body=PAGE)}))
    page = build_candidate_page("https://a.com/1", fetcher)
    text = format_candidate_pages([page, page])
    assert text.count("URL: https://a.com/1\nTITLE: Kimchi Fried Rice\nCONTENT: Name: Kimchi Fried Rice") == 2
    assert "\n\nURL:" in text

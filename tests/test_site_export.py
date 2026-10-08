import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from recipebot.history import History
from recipebot.models import Recipe
from recipebot.site_export import export_site, kitchen, safe_export
from recipebot.web import FetchResult
from tests.conftest import make_recipe

SGT = ZoneInfo("Asia/Singapore")
PAGE = '<script type="application/ld+json">{"@type":"Recipe","name":"x","image":["https://img/a.jpg"]}</script>'


class FakeFetcher:
    def __init__(self):
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        return FetchResult(url=url, final_url=url, status=200, text=PAGE)


def test_kitchen_mapping():
    assert kitchen("Chinese inspired") == "chinese"
    assert kitchen("Malaysian Chinese") == "chinese"
    assert kitchen("Malay") == "malay"
    assert kitchen("Japanese") == "japanese"
    assert kitchen("Western") == "western"
    assert kitchen("Thai") == "others"
    assert kitchen("") == "others"


def test_export_newest_first_and_images_cached(tmp_path):
    out = tmp_path / "site"
    fetcher = FakeFetcher()
    with History(tmp_path / "h.sqlite") as history:
        old = Recipe.model_validate(make_recipe(title="Old Dish", cuisine="Korean", source={"site": "a", "url": "https://a.com/old/"}))
        new = Recipe.model_validate(make_recipe(title="New Dish", cuisine="Italian", source={"site": "b", "url": "https://www.b.com/new/"}))
        history.add_sent(old, main_ingredient="tofu", run_id="r1", sent_at=datetime(2026, 10, 7, 1, tzinfo=timezone.utc))
        history.add_sent(new, main_ingredient="beef", run_id="r2", sent_at=datetime(2026, 10, 8, 17, tzinfo=timezone.utc))
        assert export_site(history, out, fetcher, SGT) == 2
        items = json.loads((out / "daily.json").read_text(encoding="utf-8"))
        assert [i["title"] for i in items] == ["New Dish", "Old Dish"]
        assert items[0]["cuisine"] == "italian" and items[0]["site"] == "b.com"
        assert items[0]["added"] == "2026-10-09"  # 17:00 UTC is the next day in Singapore
        assert items[0]["image"] == "https://img/a.jpg"
        assert items[0]["ingredients"] and all(isinstance(line, str) and line for line in items[0]["ingredients"])
        export_site(history, out, fetcher, SGT)
        assert len(fetcher.calls) == 2  # second export reads photos from images.json


def test_safe_export_never_raises(tmp_path):
    safe_export(None, tmp_path / "site", FakeFetcher(), SGT)  # broken history: logged, not raised
    safe_export(None, None, FakeFetcher(), SGT)  # turned off


def test_ingredient_lines():
    from recipebot.site_export import ingredient_lines
    r = {"ingredients": [{"item": "chicken thigh", "qty": 400, "unit": "g", "note": "sliced"}, {"item": "egg", "qty": 0.5, "unit": ""}],
         "pantry_staples": ["salt"]}
    assert ingredient_lines(r) == ["400 g chicken thigh, sliced", "0.5 egg", "salt (pantry)"]
    assert ingredient_lines({}) == []

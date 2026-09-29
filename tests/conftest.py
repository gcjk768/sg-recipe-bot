from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from recipebot.config import Settings
from recipebot.llm import LLMResult

SGT = ZoneInfo("Asia/Singapore")

SAMPLE_RECIPE: dict[str, Any] = {
    "title": "Garlic Soy Chicken with Broccoli",
    "title_zh": None,
    "cuisine": "Chinese inspired",
    "category": "high_protein",
    "why_it_fits": "One pan, about 25 minutes, and every ingredient is a FairPrice regular.",
    "servings": 2,
    "prep_minutes": 10,
    "cook_minutes": 15,
    "total_minutes": 25,
    "difficulty": "easy",
    "equipment": ["frying pan"],
    "ingredients": [
        {"item": "chicken thigh, boneless", "qty": 400, "unit": "g", "note": "cut into bite sized pieces"},
        {"item": "broccoli", "qty": 1, "unit": "head", "note": "cut into florets"},
        {"item": "garlic", "qty": 3, "unit": "clove", "note": "minced"},
        {"item": "light soy sauce", "qty": 2, "unit": "tbsp", "note": ""},
        {"item": "honey", "qty": 1, "unit": "tbsp", "note": ""},
        {"item": "cornstarch", "qty": 1, "unit": "tsp", "note": ""},
        {"item": "sesame oil", "qty": 1, "unit": "tsp", "note": ""},
    ],
    "pantry_staples": ["light soy sauce", "sesame oil", "cornstarch", "garlic"],
    "steps": [
        "Toss the chicken with the cornstarch and a pinch of salt.",
        "Heat oil in a pan over high heat and sear the chicken until golden, about 5 minutes.",
        "Add the garlic and broccoli and stir fry for 2 minutes.",
        "Mix the soy sauce, honey and 3 tbsp water, pour it in and toss until the sauce thickens and coats everything, about 2 minutes.",
        "Finish with the sesame oil and serve with rice.",
    ],
    "protein_per_serving_g": 40,
    "nutrition_per_serving": {"kcal": 420, "protein_g": 40, "carbs_g": 20, "fat_g": 20},
    "cost_estimate": {"total_sgd": 9.5, "per_serving_sgd": 4.75, "note": "chicken thigh is most of the cost"},
    "tips": ["Swap the broccoli for any green vegetable you have, or add sliced carrot for colour."],
    "storage": "Keeps 3 days in the fridge and reheats well.",
    "source": {"site": "Example Recipes", "author": "Author Name", "url": "https://www.example.com/recipes/12345"},
    "tags": ["one pan", "weeknight", "meal prep"],
}

RECIPE_HTML = "<html><head><title>Garlic Soy Chicken</title></head><body><h2>Ingredients</h2><ul><li>chicken</li></ul></body></html>"


def make_recipe(**overrides: Any) -> dict[str, Any]:
    recipe = copy.deepcopy(SAMPLE_RECIPE)
    recipe.update(overrides)
    return recipe


def make_reply(recipes: list[dict[str, Any]], category: str = "high_protein", notes: str = "", requested: int | None = None) -> dict[str, Any]:
    return {
        "run": {
            "category": category,
            "count_requested": requested if requested is not None else len(recipes),
            "count_returned": len(recipes),
            "notes": notes,
        },
        "recipes": recipes,
    }


def reply_text(recipes: list[dict[str, Any]], **kwargs: Any) -> str:
    return json.dumps(make_reply(recipes, **kwargs))


class FakeResponse:
    def __init__(self, status_code: int = 200, *, url: str = "", body: bytes | str = b"", encoding: str | None = "utf-8", json_body: Any = None):
        self.status_code = status_code
        self.url = url
        self.encoding = encoding
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self._json = json_body
        self.headers: dict[str, str] = {}
        self.closed = False

    def iter_content(self, chunk_size: int = 65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def close(self) -> None:
        self.closed = True


class FakeSession:
    """Stands in for requests.Session. `routes` maps url -> FakeResponse, Exception, or callable(url)."""

    def __init__(self, routes: dict[str, Any] | None = None, default: Any = None):
        self.routes = routes or {}
        self.default = default
        self.gets: list[dict[str, Any]] = []
        self.posts: list[dict[str, Any]] = []

    def _resolve(self, url: str) -> Any:
        target = self.routes.get(url, self.default)
        if callable(target) and not isinstance(target, FakeResponse):
            target = target(url)
        if isinstance(target, Exception):
            raise target
        if target is None:
            return FakeResponse(404, url=url, body="not found")
        if isinstance(target, FakeResponse) and not target.url:
            target.url = url
        return target

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.gets.append({"url": url, **kwargs})
        return self._resolve(url)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.posts.append({"url": url, **kwargs})
        return self._resolve(url)


def telegram_ok_session() -> FakeSession:
    counter = {"n": 0}

    def respond(url: str) -> FakeResponse:
        counter["n"] += 1
        if url.endswith("/getMe"):
            return FakeResponse(200, url=url, json_body={"ok": True, "result": {"id": 1, "username": "recipebot"}})
        return FakeResponse(200, url=url, json_body={"ok": True, "result": {"message_id": counter["n"]}})

    return FakeSession(default=respond)


@dataclass
class FakeLLM:
    """Returns queued results in order. An Exception in the queue is raised instead."""

    queue: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete(self, system: str, user: str, *, web_search: bool) -> LLMResult:
        self.calls.append({"system": system, "user": user, "web_search": web_search})
        if not self.queue:
            raise AssertionError("FakeLLM queue exhausted")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return LLMResult(text=item, stop_reason="end_turn", model="fake", text_blocks=[item])
        return item


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="123:token",
        telegram_chat_id="@channel",
        telegram_admin_chat_id="777",
        llm_api_key="sk-test",
        data_dir=tmp_path / "data",
        rotation_epoch=date(2026, 9, 28),
        post_time=time(16, 0),
    )


@pytest.fixture
def fixed_now():
    return lambda: datetime(2026, 9, 29, 16, 0, tzinfo=SGT)

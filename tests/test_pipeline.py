import json
from datetime import date

import pytest

from recipebot.history import History
from recipebot.llm import LLMResult
from recipebot.pipeline import Pipeline
from recipebot.telegram import TelegramClient
from recipebot.web import Fetcher
from tests.conftest import RECIPE_HTML, FakeLLM, FakeResponse, FakeSession, make_recipe, reply_text, telegram_ok_session

URL = "https://www.example.com/recipes/12345"


def build(settings, llm, fixed_now, *, web_routes=None, tg_session=None, rng=None):
    web = FakeSession(web_routes if web_routes is not None else {URL: FakeResponse(200, body=RECIPE_HTML)})
    tg_session = tg_session or telegram_ok_session()
    sleeps = []
    pipeline = Pipeline(
        settings,
        llm=llm,
        telegram=TelegramClient("123:token", session=tg_session, sleep=sleeps.append),
        fetcher=Fetcher(session=web),
        sleep=sleeps.append,
        now=fixed_now,
        rng=rng,
    )
    return pipeline, tg_session, web, sleeps


def sent_texts(tg_session, chat="@channel"):
    return [p["json"]["text"] for p in tg_session.posts if p["url"].endswith("/sendMessage") and p["json"]["chat_id"] == chat]


def admin_texts(tg_session):
    return sent_texts(tg_session, "777")


def test_happy_path_posts_and_records(settings, fixed_now):
    llm = FakeLLM([reply_text([make_recipe()])])
    pipeline, tg, web, sleeps = build(settings, llm, fixed_now)
    report = pipeline.run(category="high_protein", theme="post workout")
    assert report.status == "posted" and report.posted == ["Garlic Soy Chicken with Broccoli"]
    assert report.ok and report.rejected == [] and report.model_calls == 1

    brief = llm.calls[0]["user"]
    assert llm.calls[0]["web_search"] is True
    assert llm.calls[0]["system"] == pipeline.system_prompt
    assert "category: high_protein\ncount: 1\nservings: 2\ntheme: post workout\nrecent_mains: none\nalready_sent:\nnone\ncandidate_pages:\nnone" in brief

    posts = sent_texts(tg)
    assert len(posts) == 1 and posts[0].startswith("🍳 <b>Garlic Soy Chicken with Broccoli</b>")
    assert admin_texts(tg) == []
    assert web.gets[0]["url"] == URL

    with History(settings.db_path) as history:
        sent = history.recent_sent()
        assert len(sent) == 1 and sent[0].main_ingredient == "chicken thigh" and sent[0].run_id == report.run_id
        runs = history.recent_runs()
        assert runs[0].status == "posted" and runs[0].posted == 1 and runs[0].category == "high_protein" and runs[0].theme == "post workout"


def test_second_run_sees_history_in_brief(settings, fixed_now):
    llm = FakeLLM([reply_text([make_recipe()]), reply_text([make_recipe(title="Second", source={"site": "s", "url": "https://x.com/2"})])])
    pipeline, tg, web, _ = build(settings, llm, fixed_now, web_routes={URL: FakeResponse(200, body=RECIPE_HTML), "https://x.com/2": FakeResponse(200, body=RECIPE_HTML)})
    pipeline.run(category="high_protein")
    pipeline.run(category="high_protein")
    brief = llm.calls[1]["user"]
    assert "recent_mains: chicken thigh\n" in brief
    assert "already_sent:\nGarlic Soy Chicken with Broccoli | https://www.example.com/recipes/12345\n" in brief


def test_rotation_picks_category_and_theme(settings, fixed_now):
    llm = FakeLLM([reply_text([make_recipe(category="baking_cakes", prep_minutes=20, protein_per_serving_g=None)], category="baking_cakes")])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    report = pipeline.run(day=date(2026, 10, 2))  # Week A Friday
    assert report.category == "baking_cakes" and report.theme == "weekend bake"
    assert "category: baking_cakes\n" in llm.calls[0]["user"] and "theme: weekend bake\n" in llm.calls[0]["user"]
    assert report.status == "posted" and sent_texts(tg)[0].startswith("🧁 ")


def test_bad_json_is_retried_once_with_error_appended(settings, fixed_now):
    llm = FakeLLM(["not json", reply_text([make_recipe()])])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    report = pipeline.run(category="high_protein")
    assert report.status == "posted" and report.model_calls == 2
    assert "PREVIOUS ATTEMPT FAILED:" not in llm.calls[0]["user"]
    assert llm.calls[1]["user"].rstrip().endswith("no markdown fences and no text before or after it.")
    assert "PREVIOUS ATTEMPT FAILED:\nJSON parse error" in llm.calls[1]["user"]


def test_truncated_reply_counts_as_a_failure(settings, fixed_now):
    truncated = LLMResult(text='{"run": {', stop_reason="max_tokens", text_blocks=['{"run": {'])
    llm = FakeLLM([truncated, reply_text([make_recipe()])])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    report = pipeline.run(category="high_protein")
    assert report.status == "posted"
    assert "PREVIOUS ATTEMPT FAILED:\nThe reply was cut off" in llm.calls[1]["user"]


def test_two_json_failures_alert_admin_and_post_nothing(settings, fixed_now):
    llm = FakeLLM(["nope", "still nope"])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    report = pipeline.run(category="soups")
    assert report.status == "failed" and report.posted == [] and report.model_calls == 2
    assert sent_texts(tg) == []
    [alert] = admin_texts(tg)
    assert "posted nothing" in alert and "unusable JSON 2 times" in alert
    with History(settings.db_path) as history:
        assert history.recent_runs()[0].status == "failed"


def test_model_error_object(settings, fixed_now):
    llm = FakeLLM([json.dumps({"error": "no web access", "run": {"category": "soups", "count_requested": 1, "count_returned": 0, "notes": "search tool unavailable"}, "recipes": []})])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    report = pipeline.run(category="soups")
    assert report.status == "model_error" and "no web access" in report.detail and report.notes == "search tool unavailable"
    assert sent_texts(tg) == [] and len(admin_texts(tg)) == 1


def test_nothing_survives_validation(settings, fixed_now):
    llm = FakeLLM([reply_text([make_recipe(difficulty="hard"), make_recipe(title="B", total_minutes=99)])])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    report = pipeline.run(category="high_protein")
    assert report.status == "nothing_posted" and len(report.rejected) == 2
    assert sent_texts(tg) == []
    [alert] = admin_texts(tg)
    assert "dropped:" in alert and "difficulty 'hard'" in alert


def test_partial_survival_posts_good_ones_without_alert(settings, fixed_now):
    good = make_recipe(title="Good", source={"site": "s", "url": "https://x.com/good"})
    llm = FakeLLM([reply_text([make_recipe(difficulty="hard"), good])])
    pipeline, tg, _, _ = build(settings, llm, fixed_now, web_routes={"https://x.com/good": FakeResponse(200, body=RECIPE_HTML)})
    report = pipeline.run(category="high_protein", count=2)
    assert report.status == "posted" and report.posted == ["Good"] and len(report.rejected) == 1
    assert admin_texts(tg) == []


def test_multiple_recipes_pause_between_posts(settings, fixed_now):
    a = make_recipe(title="A", source={"site": "s", "url": "https://x.com/a"})
    b = make_recipe(title="B", source={"site": "s", "url": "https://x.com/b"})
    c = make_recipe(title="C", source={"site": "s", "url": "https://x.com/c"})
    routes = {u: FakeResponse(200, body=RECIPE_HTML) for u in ("https://x.com/a", "https://x.com/b", "https://x.com/c")}
    llm = FakeLLM([reply_text([a, b, c])])
    pipeline, tg, _, sleeps = build(settings, llm, fixed_now, web_routes=routes)
    report = pipeline.run(category="high_protein", count=3)
    assert report.posted == ["A", "B", "C"]
    assert sleeps == [2.0, 2.0]
    assert len(sent_texts(tg)) == 3


def test_more_recipes_than_requested_are_trimmed(settings, fixed_now):
    a = make_recipe(title="A", source={"site": "s", "url": "https://x.com/a"})
    b = make_recipe(title="B", source={"site": "s", "url": "https://x.com/b"})
    routes = {u: FakeResponse(200, body=RECIPE_HTML) for u in ("https://x.com/a", "https://x.com/b")}
    llm = FakeLLM([reply_text([a, b])])
    pipeline, tg, _, _ = build(settings, llm, fixed_now, web_routes=routes)
    report = pipeline.run(category="high_protein", count=1)
    assert report.posted == ["A"] and any("more recipes than requested" in w for w in report.warnings)


def test_dry_run_touches_nothing(settings, fixed_now):
    llm = FakeLLM([reply_text([make_recipe()])])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    report = pipeline.run(category="high_protein", dry_run=True)
    assert report.status == "dry_run" and report.ok and report.messages and report.messages[0][0].startswith("🍳")
    assert tg.posts == []
    with History(settings.db_path) as history:
        assert history.recent_sent() == [] and history.recent_runs() == []


def test_llm_exception_is_contained(settings, fixed_now):
    llm = FakeLLM([RuntimeError("api down")])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    report = pipeline.run(category="noodles")
    assert report.status == "error" and "api down" in report.detail
    assert "RuntimeError: api down" in admin_texts(tg)[0]
    with History(settings.db_path) as history:
        assert history.recent_runs()[0].status == "error"


def test_telegram_failure_is_contained_and_not_recorded_as_sent(settings, fixed_now):
    bad = FakeSession(default=lambda url: FakeResponse(400, json_body={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}))
    llm = FakeLLM([reply_text([make_recipe()])])
    pipeline, tg, _, _ = build(settings, llm, fixed_now, tg_session=bad)
    report = pipeline.run(category="high_protein")
    assert report.status == "error" and "chat not found" in report.detail
    with History(settings.db_path) as history:
        assert history.recent_sent() == []


def test_admin_alert_failure_does_not_crash(settings, fixed_now):
    def respond(url):
        return FakeResponse(400, json_body={"ok": False, "error_code": 400, "description": "Bad Request"})

    llm = FakeLLM(["nope", "nope"])
    pipeline, tg, _, _ = build(settings, llm, fixed_now, tg_session=FakeSession(default=respond))
    report = pipeline.run(category="soups")
    assert report.status == "failed"


def test_no_page_check_skips_fetch(settings, fixed_now):
    llm = FakeLLM([reply_text([make_recipe()])])
    pipeline, tg, web, _ = build(settings, llm, fixed_now, web_routes={})
    report = pipeline.run(category="high_protein", check_pages=False)
    assert report.status == "posted" and web.gets == []


def test_candidates_mode_disables_search_and_pastes_pages(settings, fixed_now):
    settings.source_mode = "candidates"
    settings.candidates_dir.mkdir(parents=True)
    (settings.candidates_dir / "noodles.txt").write_text("https://c.com/1\nhttps://c.com/2\n")
    node = {"@type": "Recipe", "name": "Fried Bee Hoon", "recipeIngredient": ["bee hoon"], "recipeInstructions": ["Fry."]}
    page = f'<html><script type="application/ld+json">{json.dumps(node)}</script><body>Ingredients</body></html>'
    routes = {"https://c.com/1": FakeResponse(200, body=page), "https://c.com/2": FakeResponse(200, body=page)}
    recipe = make_recipe(category="noodles", protein_per_serving_g=None, source={"site": "C", "url": "https://c.com/1"})
    llm = FakeLLM([reply_text([recipe], category="noodles")])
    pipeline, tg, web, _ = build(settings, llm, fixed_now, web_routes=routes)
    report = pipeline.run(category="noodles")
    assert report.status == "posted"
    assert llm.calls[0]["web_search"] is False
    brief = llm.calls[0]["user"]
    assert "candidate_pages:\nURL: https://c.com/" in brief and "TITLE: Fried Bee Hoon\nCONTENT: Name: Fried Bee Hoon" in brief
    # second run: the posted url is excluded from the candidates
    llm.queue.append(reply_text([make_recipe(title="Other", category="noodles", protein_per_serving_g=None, source={"site": "C", "url": "https://c.com/2"})], category="noodles"))
    pipeline.run(category="noodles")
    assert "URL: https://c.com/1" not in llm.calls[1]["user"] and "URL: https://c.com/2" in llm.calls[1]["user"]


def test_candidates_mode_falls_back_to_search_when_empty(settings, fixed_now):
    settings.source_mode = "candidates"
    llm = FakeLLM([reply_text([make_recipe()])])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    pipeline.run(category="high_protein")
    assert llm.calls[0]["web_search"] is True and "candidate_pages:\nnone" in llm.calls[0]["user"]


def test_unknown_category_override_raises(settings, fixed_now):
    pipeline, *_ = build(settings, FakeLLM([]), fixed_now)
    with pytest.raises(ValueError):
        pipeline.run(category="pizza")


def test_no_admin_chat_means_no_alert(settings, fixed_now):
    settings.telegram_admin_chat_id = None
    llm = FakeLLM(["nope", "nope"])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    report = pipeline.run(category="soups")
    assert report.status == "failed" and tg.posts == []

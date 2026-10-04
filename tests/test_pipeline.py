import json
from datetime import date

import pytest

from recipebot.history import History
from recipebot.llm import LLMResult
from recipebot.pipeline import DIAGNOSE_SYSTEM, Pipeline
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
    assert len(posts) == 1 and posts[0].startswith("🍳 <b>Garlic Soy Chicken with Broccoli</b>\nHigh protein · ")
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
    llm = FakeLLM([reply_text([make_recipe()])])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    report = pipeline.run(day=date(2026, 10, 2))
    assert report.category == "high_protein" and report.theme is None
    assert "category: high_protein\n" in llm.calls[0]["user"] and "theme: none\n" in llm.calls[0]["user"]
    assert report.status == "posted" and sent_texts(tg)[0].startswith("🍳 <b>")


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
    report = pipeline.run(category="high_protein")
    assert report.status == "failed" and report.posted == [] and report.model_calls == 2
    assert sent_texts(tg) == []
    [alert] = admin_texts(tg)
    assert "posted nothing" in alert and "unusable JSON 2 times" in alert
    with History(settings.db_path) as history:
        assert history.recent_runs()[0].status == "failed"


def test_model_error_object(settings, fixed_now):
    llm = FakeLLM([json.dumps({"error": "no web access", "run": {"category": "high_protein", "count_requested": 1, "count_returned": 0, "notes": "search tool unavailable"}, "recipes": []})])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    report = pipeline.run(category="high_protein")
    assert report.status == "model_error" and "no web access" in report.detail and report.notes == "search tool unavailable"
    assert sent_texts(tg) == [] and len(admin_texts(tg)) == 1


def test_nothing_survives_validation(settings, fixed_now):
    bad = reply_text([make_recipe(difficulty="hard"), make_recipe(title="B", total_minutes=99)])
    llm = FakeLLM([bad, bad])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    report = pipeline.run(category="high_protein")
    assert report.status == "nothing_posted" and len(report.rejected) == 4 and report.model_calls == 2
    assert "Every recipe was rejected" in llm.calls[1]["user"] and "difficulty 'hard'" in llm.calls[1]["user"]
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
    report = pipeline.run(category="high_protein")
    assert report.status == "error" and "api down" in report.detail
    assert "RuntimeError: api down" in admin_texts(tg)[0]
    with History(settings.db_path) as history:
        assert history.recent_runs()[0].status == "error"


def test_telegram_rejection_is_contained_and_not_recorded_as_sent(settings, fixed_now):
    bad = FakeSession(default=lambda url: FakeResponse(400, json_body={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}))
    llm = FakeLLM([reply_text([make_recipe()])])
    pipeline, tg, _, _ = build(settings, llm, fixed_now, tg_session=bad)
    report = pipeline.run(category="high_protein")
    assert report.status == "nothing_posted" and report.posted == []
    assert any("telegram" in r and "chat not found" in r for r in report.rejected)
    with History(settings.db_path) as history:
        assert history.recent_sent() == []
        assert history.recent_runs()[0].status == "nothing_posted"


def _tg_session_failing_on(nth_message: int, error_code: int = 400, description: str = "Bad Request: flood"):
    counter = {"n": 0}

    def respond(url):
        if url.endswith("/sendMessage"):
            counter["n"] += 1
            if counter["n"] == nth_message:
                return FakeResponse(error_code, json_body={"ok": False, "error_code": error_code, "description": description})
            return FakeResponse(200, json_body={"ok": True, "result": {"message_id": counter["n"]}})
        return FakeResponse(200, json_body={"ok": True, "result": {"id": 1}})

    return FakeSession(default=respond)


def test_partial_delivery_reports_counts_and_records_what_was_sent(settings, fixed_now):
    a = make_recipe(title="A", source={"site": "s", "url": "https://x.com/a"})
    b = make_recipe(title="B", source={"site": "s", "url": "https://x.com/b"})
    routes = {u: FakeResponse(200, body=RECIPE_HTML) for u in ("https://x.com/a", "https://x.com/b")}
    llm = FakeLLM([reply_text([a, b])])
    pipeline, tg, _, _ = build(settings, llm, fixed_now, web_routes=routes, tg_session=_tg_session_failing_on(2))
    report = pipeline.run(category="high_protein", count=2)
    assert report.status == "partial" and report.posted == ["A"] and any(r.startswith("B: telegram") for r in report.rejected)
    [alert] = admin_texts(tg)
    assert alert.startswith("🚨 <b>RECIPEBOT</b> · posted 1 of 2\n\n🔴 <b>High protein</b> · status partial")
    assert "RecipeBot posted 1 of 2 recipe(s), then hit a problem." in alert  # full report in the background quote
    with History(settings.db_path) as history:
        assert [r.title for r in history.recent_sent()] == ["A"]
        assert history.recent_runs()[0].status == "partial" and history.recent_runs()[0].posted == 1


def test_half_sent_split_recipe_is_recorded(settings, fixed_now):
    long = make_recipe(why_it_fits="w" * 2300, tips=["x" * 2300])
    llm = FakeLLM([reply_text([long])])
    pipeline, tg, _, _ = build(settings, llm, fixed_now, tg_session=_tg_session_failing_on(2))
    report = pipeline.run(category="high_protein")
    assert report.status == "partial" and report.posted[0].startswith("Garlic Soy Chicken with Broccoli (incomplete")
    with History(settings.db_path) as history:
        assert len(history.recent_sent()) == 1


def test_ambiguous_timeout_is_recorded_not_retried(settings, fixed_now):
    session = FakeSession(default=lambda url: (_ for _ in ()).throw(TimeoutError("ReadTimeout: read timed out")) if url.endswith("/sendMessage") else FakeResponse(200, json_body={"ok": True, "result": {"message_id": 1}}))
    llm = FakeLLM([reply_text([make_recipe()])])
    pipeline, tg, _, _ = build(settings, llm, fixed_now, tg_session=session)
    report = pipeline.run(category="high_protein")
    assert report.status == "partial" and "incomplete" in report.posted[0]
    assert len([p for p in tg.posts if p["url"].endswith("/sendMessage") and p["json"]["chat_id"] == "@channel"]) == 1
    with History(settings.db_path) as history:
        assert len(history.recent_sent()) == 1


def test_broken_rotation_file_is_reported_not_fatal(settings, fixed_now):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.rotation_path.write_text("{not json")
    llm = FakeLLM([])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    report = pipeline.run()
    assert report.status == "error" and "rotation.json" in report.detail and report.category == "unresolved"
    assert [c["system"] for c in llm.calls] == [DIAGNOSE_SYSTEM]  # no recipe call, only the triage
    [alert] = admin_texts(tg)
    assert "RecipeBot posted nothing" in alert and "invalid JSON" in alert


def test_alert_carries_model_diagnosis(settings, fixed_now):
    llm = FakeLLM([RuntimeError("api down"), "Anthropic <outage>.\nTransient, it retries tomorrow."])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    pipeline.run(category="high_protein")
    [post] = [p["json"] for p in tg.posts if p["json"]["chat_id"] == "777"]
    assert post["parse_mode"] == "HTML" and post["link_preview_options"] == {"is_disabled": True}
    alert = post["text"]
    assert alert.startswith("🚨 <b>RECIPEBOT</b> · posted nothing\n\n🔴 <b>High protein</b> · status error\n🆔 <code>")
    # the model's diagnosis keeps its lines but is escaped before it meets any tag
    assert "🩺 <b>Diagnosis</b>\nAnthropic &lt;outage&gt;.\nTransient, it retries tomorrow." in alert
    assert alert.endswith("</blockquote>") and "<blockquote expandable>⚙️ <b>Background</b>" in alert
    assert "RuntimeError: api down" in llm.calls[1]["user"] and llm.calls[1]["web_search"] is False


def test_alert_says_so_when_diagnosis_fails_too(settings, fixed_now):
    llm = FakeLLM([RuntimeError("claude -p failed (auth)")])  # the queue is empty for the triage call
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    pipeline.run(category="high_protein")
    [alert] = admin_texts(tg)
    assert "<b>Diagnosis</b>\nunavailable" in alert and "claude setup-token" in alert


def test_rotation_file_is_reread_each_run(settings, fixed_now):
    import json as _json

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    llm = FakeLLM([reply_text([make_recipe(category="high_protein", protein_per_serving_g=None)], category="high_protein"),
                   reply_text([make_recipe(title="Second", category="high_protein", protein_per_serving_g=None, source={"site": "s", "url": "https://x.com/2"})], category="high_protein")])
    pipeline, tg, _, _ = build(settings, llm, fixed_now, web_routes={URL: FakeResponse(200, body=RECIPE_HTML), "https://x.com/2": FakeResponse(200, body=RECIPE_HTML)})
    settings.rotation_path.write_text(_json.dumps({"overrides": {"2026-09-29": {"category": "high_protein", "theme": "eggs"}}}))
    assert pipeline.run().theme == "eggs"
    settings.rotation_path.write_text(_json.dumps({"overrides": {"2026-09-29": {"category": "high_protein", "theme": "tofu"}}}))
    assert pipeline.run().theme == "tofu"


def test_missing_telegram_config_raises_before_the_model_is_called(settings, fixed_now):
    from recipebot.config import ConfigError

    settings.telegram_bot_token = None
    llm = FakeLLM([reply_text([make_recipe()])])
    pipeline = Pipeline(settings, llm=llm, fetcher=Fetcher(session=FakeSession({})), now=fixed_now)
    with pytest.raises(ConfigError):
        pipeline.run(category="high_protein")
    assert llm.calls == []
    assert pipeline.run(category="high_protein", dry_run=True, check_pages=False).status == "dry_run"


def test_scheduled_run_row_carries_the_rotation_day(settings, fixed_now):
    llm = FakeLLM([reply_text([make_recipe(category="high_protein", protein_per_serving_g=None)], category="high_protein")])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    pipeline.run(day=date(2026, 10, 4), scheduled=True)
    with History(settings.db_path) as history:
        runs = history.runs_for_day(date(2026, 10, 4))
        assert len(runs) == 1 and runs[0].status == "posted" and runs[0].category == "high_protein"
        assert history.runs_for_day(date(2026, 9, 29)) == []


def test_manual_run_with_a_date_counts_for_today_not_that_date(settings, fixed_now):
    llm = FakeLLM([reply_text([make_recipe(category="high_protein", protein_per_serving_g=None)], category="high_protein")])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    pipeline.run(day=date(2026, 10, 4))
    with History(settings.db_path) as history:
        assert history.runs_for_day(date(2026, 10, 4)) == []
        assert history.runs_for_day(date(2026, 9, 29))[0].category == "high_protein"


def test_early_failure_still_leaves_a_run_row(settings, fixed_now):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.rotation_path.write_text("{not json")
    pipeline, tg, _, _ = build(settings, FakeLLM([]), fixed_now)
    report = pipeline.run(scheduled=True)
    assert report.status == "error"
    with History(settings.db_path) as history:
        runs = history.runs_for_day(date(2026, 9, 29))
        assert len(runs) == 1 and runs[0].status == "error" and runs[0].category is None


def test_history_write_failure_after_posting_is_reported(settings, fixed_now, monkeypatch):
    llm = FakeLLM([reply_text([make_recipe()])])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipeline.history, "add_sent", boom)
    report = pipeline.run(category="high_protein")
    assert report.status == "partial" and report.posted == ["Garlic Soy Chicken with Broccoli"]
    assert any("NOT recorded in history" in w and URL in w for w in report.problems)
    [alert] = admin_texts(tg)
    assert "posted 1 of 1" in alert and "NOT recorded" in alert
    assert len(sent_texts(tg)) == 1


def test_stop_signal_passes_through_and_leaves_the_row_unfinished(settings, fixed_now):
    from recipebot.scheduler import Shutdown

    class Stopper:
        calls = []

        def complete(self, system, user, *, web_search):
            raise Shutdown("SIGTERM")

    pipeline, tg, _, _ = build(settings, Stopper(), fixed_now)
    with pytest.raises(Shutdown):
        pipeline.run(category="high_protein", scheduled=True)
    with History(settings.db_path) as history:
        runs = history.runs_for_day(date(2026, 9, 29))
        assert len(runs) == 1 and runs[0].unfinished
    assert admin_texts(tg) == []


def test_admin_alert_failure_does_not_crash(settings, fixed_now):
    def respond(url):
        return FakeResponse(400, json_body={"ok": False, "error_code": 400, "description": "Bad Request"})

    llm = FakeLLM(["nope", "nope"])
    pipeline, tg, _, _ = build(settings, llm, fixed_now, tg_session=FakeSession(default=respond))
    report = pipeline.run(category="high_protein")
    assert report.status == "failed"


def test_no_page_check_skips_fetch(settings, fixed_now):
    llm = FakeLLM([reply_text([make_recipe()])])
    pipeline, tg, web, _ = build(settings, llm, fixed_now, web_routes={})
    report = pipeline.run(category="high_protein", check_pages=False)
    assert report.status == "posted" and web.gets == []


def test_candidates_mode_disables_search_and_pastes_pages(settings, fixed_now):
    settings.source_mode = "candidates"
    settings.candidates_dir.mkdir(parents=True)
    (settings.candidates_dir / "high_protein.txt").write_text("https://c.com/1\nhttps://c.com/2\n")
    node = {"@type": "Recipe", "name": "Fried Bee Hoon", "recipeIngredient": ["bee hoon"], "recipeInstructions": ["Fry."]}
    page = f'<html><script type="application/ld+json">{json.dumps(node)}</script><body>Ingredients</body></html>'
    routes = {"https://c.com/1": FakeResponse(200, body=page), "https://c.com/2": FakeResponse(200, body=page)}
    recipe = make_recipe(category="high_protein", protein_per_serving_g=None, source={"site": "C", "url": "https://c.com/1"})
    llm = FakeLLM([reply_text([recipe], category="high_protein")])
    pipeline, tg, web, _ = build(settings, llm, fixed_now, web_routes=routes)
    report = pipeline.run(category="high_protein")
    assert report.status == "posted"
    assert llm.calls[0]["web_search"] is False
    brief = llm.calls[0]["user"]
    assert "candidate_pages:\nURL: https://c.com/" in brief and "TITLE: Fried Bee Hoon\nCONTENT: Name: Fried Bee Hoon" in brief
    # second run: the posted url is excluded from the candidates
    llm.queue.append(reply_text([make_recipe(title="Other", category="high_protein", protein_per_serving_g=None, source={"site": "C", "url": "https://c.com/2"})], category="high_protein"))
    pipeline.run(category="high_protein")
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
    report = pipeline.run(category="high_protein")
    assert report.status == "failed" and tg.posts == []


def test_run_daily_posts_each_meal_with_its_tag(settings, fixed_now, monkeypatch):
    monkeypatch.setattr("recipebot.rotation.PER_MEAL", 2)
    plan = __import__("recipebot.rotation", fromlist=["daily_plan"]).daily_plan(date(2026, 9, 29))
    assert [m for m, _ in plan] == ["breakfast"] * 2 + ["lunch"] * 2 + ["dinner"] * 2
    assert {s.category for _, s in plan} == {"high_protein"}  # only high protein meal prep
    urls = [f"https://x.com/{i}" for i in range(len(plan))]
    llm = FakeLLM([reply_text([make_recipe(title=f"Dish {i}", category=s.category, meals=["supper"], prep_minutes=5,
                                           cook_minutes=10, total_minutes=15, source={"site": "s", "url": urls[i]})],
                                category=s.category) for i, (_, s) in enumerate(plan)])
    pipeline, tg, _, _ = build(settings, llm, fixed_now, web_routes={u: FakeResponse(200, body=RECIPE_HTML) for u in urls})
    reports = pipeline.run_daily(date(2026, 9, 29))
    assert [r.status for r in reports] == ["posted"] * len(plan)
    assert "theme: lunch meal prep for workouts" in llm.calls[2]["user"]
    posts = sent_texts(tg)
    assert len(posts) == len(plan) and "#breakfast" in posts[0] and "#dinner" in posts[-1]
    with History(settings.db_path) as history:  # recorded against the scheduled date, so the loop won't rerun it
        assert len(history.runs_for_day(date(2026, 9, 29))) == len(plan)


def test_all_rejected_then_second_pick_posts(settings, fixed_now):
    llm = FakeLLM([reply_text([make_recipe(total_minutes=99)]), reply_text([make_recipe()])])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    report = pipeline.run(category="high_protein")
    assert report.status == "posted" and report.model_calls == 2 and len(sent_texts(tg)) == 1

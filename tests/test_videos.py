import json
from datetime import date

from recipebot.videos import CHANNELS, QUERIES, QUERIES_PER_DAY, _seconds, keep, queries_for, refresh, refresh_channels


def test_seconds():
    assert _seconds(339) == 339 and _seconds("8:1") == 481 and _seconds("1:02:03") == 3723
    assert _seconds("") is None and _seconds(None) is None and _seconds("abc") is None


def test_keep_needs_views_and_a_normal_length():
    v = {"url": "https://www.youtube.com/watch?v=x", "title": "Soy sauce chicken", "views": 50_000, "seconds": 400}
    assert keep(v)
    assert not keep({**v, "views": 900})        # not watched enough
    assert not keep({**v, "seconds": 30})       # a short
    assert not keep({**v, "seconds": 3 * 3600})  # a stream
    assert not keep({**v, "url": "http://x"})


def test_queries_rotate_daily():
    a, b = queries_for(date(2026, 10, 9)), queries_for(date(2026, 10, 10))
    assert len(a) == QUERIES_PER_DAY and a != b and all(q in QUERIES for q in a + b)


def test_refresh_merges_dedupes_and_survives_a_failing_platform(tmp_path):
    good = {"platform": "youtube", "title": "Mapo tofu", "url": "https://www.youtube.com/watch?v=1", "channel": "c",
            "views": 90_000, "seconds": 420, "image": "https://i.ytimg.com/vi/1/hqdefault.jpg"}
    weak = {**good, "url": "https://www.youtube.com/watch?v=2", "views": 10}

    def broken(query):
        raise RuntimeError("412")

    searchers = {"youtube": lambda q: [good, weak], "bilibili": broken}
    assert refresh(tmp_path, date(2026, 10, 9), searchers) == 1  # same video for every dish counts once
    assert refresh(tmp_path, date(2026, 10, 10), searchers) == 0  # already stored
    stored = json.loads((tmp_path / "videos.json").read_text(encoding="utf-8"))
    assert [v["url"] for v in stored] == [good["url"]] and stored[0]["added"] == "2026-10-09" and stored[0]["cuisine"]


def test_bilibili_reads_the_pretty_printed_reply(monkeypatch):
    from recipebot import videos
    reply = {"ok": True, "data": [{"bvid": "BV1x", "title": "豉油鸡", "author": "阿鹏", "play": 218000, "duration": "8:1"}]}
    monkeypatch.setattr(videos, "_run", lambda cmd, timeout=120: json.dumps(reply, ensure_ascii=False, indent=2))
    [v] = videos.bilibili_search("豉油鸡 做法")
    assert v["url"] == "https://www.bilibili.com/video/BV1x" and v["seconds"] == 481 and keep(v)
    monkeypatch.setattr(videos, "_run", lambda cmd, timeout=120: '{"ok": false, "error": {"code": "network_error"}}')
    assert videos.bilibili_search("x") == []


def test_vtt_text_drops_timestamps_and_repeats():
    from recipebot.videos import _vtt_text
    vtt = "WEBVTT\nKind: captions\n\n00:00.000 --> 00:02.000\nadd <c>soy sauce</c>\n\n00:02.000 --> 00:04.000\nadd soy sauce\nthen stir"
    assert _vtt_text(vtt) == "add soy sauce then stir"


def test_written_recipe_needs_real_content():
    from recipebot.videos import written_recipe
    good = 'Sure: {"servings": 4, "ingredients": ["1 chicken", "3 tbsp soy sauce", "2 slices ginger"], "steps": ["Boil.", "Soak."]}'
    assert written_recipe(good) == {"ingredients": ["1 chicken", "3 tbsp soy sauce", "2 slices ginger"], "steps": ["Boil.", "Soak."], "servings": 4}
    assert written_recipe('{"none": true}') is None
    assert written_recipe('{"ingredients": ["salt"], "steps": ["cook"]}') is None  # too thin to cook from
    assert written_recipe("no json here") is None


class _Reply:
    def __init__(self, text):
        self.text = text


class _LLM:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def complete(self, system, user, *, web_search):
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return _Reply(r)


def test_write_up_checks_each_video_once_and_retries_a_failed_call(tmp_path):
    from recipebot.videos import write_up
    vids = [{"platform": "youtube", "url": f"https://www.youtube.com/watch?v={i}", "title": f"Dish {i}", "views": 100 - i} for i in range(3)]
    (tmp_path / "videos.json").write_text(json.dumps(vids), encoding="utf-8")
    recipe = '{"ingredients": ["a", "b", "c"], "steps": ["one", "two"]}'
    llm = _LLM([recipe, '{"none": true}', RuntimeError("model down")])
    texts = {"youtube": lambda v: "long description " * 20}
    assert write_up(tmp_path, llm, None, limit=5, texts=texts) == 1
    stored = {v["url"][-1]: v for v in json.loads((tmp_path / "videos.json").read_text(encoding="utf-8"))}
    assert stored["0"]["steps"] == ["one", "two"] and stored["0"]["recipe_checked"]
    assert "steps" not in stored["1"] and stored["1"]["recipe_checked"]      # no recipe: never asked again
    assert stored["2"]["recipe_checked"] is False                             # model failed: try another day
    llm2 = _LLM([recipe])
    assert write_up(tmp_path, llm2, None, limit=5, texts=texts) == 1 and llm2.calls == 1



def test_refresh_drops_videos_older_than_three_months(tmp_path):
    old = {"platform": "youtube", "url": "https://www.youtube.com/watch?v=old", "title": "Old", "added": "2026-06-01"}
    recent = {**old, "url": "https://www.youtube.com/watch?v=new", "added": "2026-10-01"}
    (tmp_path / "videos.json").write_text(json.dumps([recent, old]), encoding="utf-8")
    refresh(tmp_path, date(2026, 10, 9), {"youtube": lambda q: []})
    assert [v["url"][-3:] for v in json.loads((tmp_path / "videos.json").read_text(encoding="utf-8"))] == ["new"]


def test_channels_keep_every_upload_and_tag_videos_already_found(tmp_path):
    found = {"platform": "youtube", "url": "https://www.youtube.com/watch?v=old", "title": "Claypot rice", "channel": "",
             "views": 50_000, "seconds": 400, "added": "2026-10-01", "tags": []}
    (tmp_path / "videos.json").write_text(json.dumps([found]), encoding="utf-8")
    fresh = {**found, "url": "https://www.youtube.com/watch?v=new", "views": 300}  # new upload, few views
    short = {**fresh, "url": "https://www.youtube.com/watch?v=sht", "seconds": None}  # shorts come without a length
    stream = {**fresh, "url": "https://www.youtube.com/watch?v=liv", "seconds": 3 * 3600}
    calls = []

    def reader(url, n=30):
        calls.append(n)
        if "meatmen" in url:
            raise RuntimeError("yt-dlp failed")
        return [found, fresh, short, stream]

    assert refresh_channels(tmp_path, date(2026, 10, 27), reader) == 2
    stored = {v["url"][-3:]: v for v in json.loads((tmp_path / "videos.json").read_text(encoding="utf-8"))}
    tag, name = CHANNELS[0][:2]
    assert set(stored) == {"old", "new", "sht"} and stored["old"]["tags"] == [tag]
    assert stored["new"]["tags"] == [tag] and stored["new"]["channel"] == name and stored["new"]["added"] == "2026-10-27"
    assert refresh_channels(tmp_path, date(2026, 10, 28), reader, full=True) == 0 and calls[-1] is None


def test_channel_backfill_is_not_new_and_never_ages_out(tmp_path):
    from recipebot import videos
    rows = [{"platform": "youtube", "url": f"https://www.youtube.com/watch?v={i}", "title": f"Dish {i}", "channel": "c",
             "views": 5, "seconds": 300} for i in range(videos.CHANNEL_NEW + 5)]
    refresh_channels(tmp_path, date(2026, 1, 1), lambda url, n=30: rows if "pantry" in url else [])
    stored = json.loads((tmp_path / "videos.json").read_text(encoding="utf-8"))
    assert sum(not v.get("backfill") for v in stored) == videos.CHANNEL_NEW  # only the newest count as new
    refresh(tmp_path, date(2026, 10, 9), {"youtube": lambda q: []})  # 9 months later
    assert len(json.loads((tmp_path / "videos.json").read_text(encoding="utf-8"))) == len(rows)


def test_shorts_get_the_watch_url_the_player_needs():
    from recipebot.videos import _youtube_videos
    [v] = _youtube_videos([{"id": "abc", "url": "https://www.youtube.com/shorts/abc", "title": "t"}])
    assert v["url"] == "https://www.youtube.com/watch?v=abc" and v["seconds"] is None

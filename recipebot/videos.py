"""Video recipes for the family website: YouTube (yt-dlp) and Bilibili (bili-cli) searches.

Both are the Agent Reach backends for those platforms, used read only and without a login.
TikTok and Douyin were tried in the NAS browser on 2026-10-08 (soft block / captcha page) and X
needs a logged-in account, so they are not searched (the owner's scraping rule: never work around a block).

Each day a few dishes from QUERIES are searched (rotating by date), well watched videos are kept
and merged into ``<SITE_EXPORT_DIR>/videos.json``, newest first. Videos are linked, never re-uploaded.
Best-effort: a platform that fails is logged and skipped.
"""

from __future__ import annotations

import html as htmllib
import json
import logging
import os
import re
import subprocess
import tempfile
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

MIN_VIEWS = 20_000            # well watched: a rough "this recipe works for people" signal
MIN_SECONDS, MAX_SECONDS = 60, 40 * 60  # no shorts, no hour-long streams
PER_SEARCH = 6                # results asked for per platform per dish
QUERIES_PER_DAY = 4
MAX_VIDEOS = 3000

# (English search, Chinese search, cuisine key on the website, extra tags). The Chinese term goes
# to Bilibili, the English one to YouTube.
QUERIES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("Cantonese soy sauce chicken recipe", "广东豉油鸡 做法", "chinese", ()),
    ("Cantonese steamed fish recipe", "粤式清蒸鱼 做法", "chinese", ()),
    ("Hong Kong egg tart recipe", "港式蛋挞 做法", "chinese", ("baking",)),
    ("Hokkien braised pork belly kong bak recipe", "福建焢肉 做法", "chinese", ()),
    ("bak kut teh recipe", "肉骨茶 做法", "chinese", ()),
    ("Teochew braised duck recipe", "潮州卤鸭 做法", "chinese", ()),
    ("Teochew steamed pomfret recipe", "潮州蒸鲳鱼 做法", "chinese", ()),
    ("Hakka yong tau foo recipe", "客家酿豆腐 做法", "chinese", ()),
    ("Hakka salt baked chicken recipe", "客家盐焗鸡 做法", "chinese", ()),
    ("Hainanese chicken rice recipe", "海南鸡饭 做法", "chinese", ()),
    ("Sichuan mapo tofu recipe", "麻婆豆腐 做法", "chinese", ()),
    ("Sichuan kung pao chicken recipe", "宫保鸡丁 做法", "chinese", ()),
    ("Shanghai braised pork belly hong shao rou recipe", "上海红烧肉 做法", "chinese", ()),
    ("Shanghai scallion oil noodles recipe", "葱油拌面 做法", "chinese", ()),
    ("Taiwanese three cup chicken recipe", "台式三杯鸡 做法", "chinese", ()),
    ("Taiwanese braised pork rice lu rou fan recipe", "卤肉饭 做法", "chinese", ()),
    ("Peranakan ayam pongteh recipe", "娘惹菜 做法", "malay", ()),
    ("Malay ayam masak merah recipe", "马来红酱鸡 做法", "malay", ()),
    ("Japanese home cooking recipe easy", "日式家常菜 做法", "japanese", ()),
    ("Korean home cooking recipe easy", "韩式家常菜 做法", "korean", ()),
    ("Thai basil chicken recipe", "泰式打抛猪 做法", "others", ()),
    ("Vietnamese pho recipe at home", "越南河粉 做法", "others", ()),
    ("healthy chicken breast recipe high protein", "鸡胸肉 减脂 做法", "western", ("protein",)),
    ("high protein low calorie meal prep", "高蛋白 低卡 减脂餐", "western", ("protein",)),
    ("high protein breakfast recipe", "高蛋白 早餐 做法", "western", ("protein", "breakfast")),
    ("easy chocolate cake recipe", "巧克力蛋糕 做法", "western", ("baking",)),
    ("easy banana bread recipe", "香蕉蛋糕 做法", "western", ("baking",)),
    ("pandan chiffon cake recipe", "班兰戚风蛋糕 做法", "malay", ("baking",)),
    ("Japanese cotton cheesecake recipe", "日式轻乳酪蛋糕 做法", "japanese", ("baking",)),
    ("steamed huat kueh ma lai gao recipe", "马拉糕 做法", "chinese", ("baking",)),
)


def queries_for(day: date) -> list[tuple[str, str, str, tuple[str, ...]]]:
    start = (day.toordinal() * QUERIES_PER_DAY) % len(QUERIES)
    return [QUERIES[(start + i) % len(QUERIES)] for i in range(QUERIES_PER_DAY)]


def _seconds(value) -> int | None:
    """yt-dlp gives seconds; bili-cli gives "8:1" (minutes:seconds) or "1:02:03"."""
    if isinstance(value, (int, float)):
        return int(value)
    parts = str(value or "").split(":")
    if not all(p.isdigit() for p in parts) or not parts[0]:
        return None
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return total


def _run(cmd: list[str], timeout: int = 120) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"}).stdout


def _run_json_lines(cmd: list[str], timeout: int = 120) -> list[dict]:
    """yt-dlp --dump-json prints one JSON object per line."""
    items = []
    for line in _run(cmd, timeout).splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                items.append(json.loads(line))
            except ValueError:
                continue
    return items


def youtube_search(query: str, n: int = PER_SEARCH) -> list[dict]:
    rows = _run_json_lines(["yt-dlp", "--flat-playlist", "--dump-json", "--no-warnings", f"ytsearch{n}:{query}"])
    videos = []
    for d in rows:
        url = d.get("url") or (f"https://www.youtube.com/watch?v={d['id']}" if d.get("id") else "")
        thumbs = d.get("thumbnails") or []
        videos.append({
            "platform": "youtube", "title": d.get("title") or "", "url": url, "channel": d.get("channel") or d.get("uploader") or "",
            "views": d.get("view_count"), "seconds": _seconds(d.get("duration")),
            "image": (f"https://i.ytimg.com/vi/{d['id']}/hqdefault.jpg" if d.get("id") else (thumbs[-1].get("url") if thumbs else None)),
        })
    return videos


def bilibili_search(query: str, n: int = PER_SEARCH) -> list[dict]:
    out = _run(["bili", "search", query, "--type", "video", "-n", str(n), "--json"])  # one pretty-printed document
    try:
        reply = json.loads(out)
    except ValueError:
        log.warning("bilibili search for %r gave no JSON: %s", query, out[:200])
        return []
    if not reply.get("ok"):
        log.warning("bilibili search failed for %r: %s", query, str(reply.get("error"))[:200])
    data = reply.get("data") or [] if reply.get("ok") else []
    return [{
        "platform": "bilibili", "title": d.get("title") or "", "url": f"https://www.bilibili.com/video/{d['bvid']}",
        "channel": d.get("author") or "", "views": d.get("play"), "seconds": _seconds(d.get("duration")), "image": None,
    } for d in data if d.get("bvid")]


def keep(v: dict) -> bool:
    return (v.get("url", "").startswith("https://") and bool(v.get("title"))
            and (v.get("views") or 0) >= MIN_VIEWS
            and v.get("seconds") is not None and MIN_SECONDS <= v["seconds"] <= MAX_SECONDS)


def refresh(out_dir: Path, day: date, searchers=None) -> int:
    """Searches today's dishes on each platform and merges the keepers into videos.json.
    Returns how many new videos were added."""
    searchers = searchers or {"youtube": youtube_search, "bilibili": bilibili_search}
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "videos.json"
    try:
        videos: list[dict] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        videos = []
    seen = {v["url"] for v in videos}
    added = []
    for en, zh, cuisine, tags in queries_for(day):
        for platform, search in searchers.items():
            try:
                found = search(zh if platform == "bilibili" else en)
            except Exception as exc:  # noqa: BLE001 - one platform failing must not stop the others
                log.warning("%s search failed for %r: %s", platform, en, exc)
                continue
            for v in found:
                if keep(v) and v["url"] not in seen:
                    seen.add(v["url"])
                    added.append({**v, "dish": en.replace(" recipe", ""), "dish_zh": zh.replace(" 做法", ""),
                                  "cuisine": cuisine, "tags": list(tags), "added": day.isoformat()})
    if added:
        videos = (added + videos)[:MAX_VIDEOS]
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(videos, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    log.info("videos: %d new (%d total)", len(added), len(videos))
    return len(added)


# ---- the recipe inside a video: description + subtitles -> ingredients and steps ----------------

RECIPES_PER_DAY = 10  # model calls a day for writing up video recipes, most watched first
RECIPE_SYSTEM = """You turn a cooking video's title, description and transcript into a written recipe for home cooks.
Use only what the text says. Do not invent ingredients, quantities or steps; leave a quantity out if the text has none.
Write in the video's main language (Chinese for a Chinese video, English for an English one).
Reply with JSON only:
{"servings": int or null, "minutes": int or null, "ingredients": ["300 g chicken thigh", ...], "steps": ["...", ...]}
At most 15 ingredients and 10 short steps.
If the text does not hold enough of the recipe to cook it (no ingredient list and no clear steps), reply {"none": true}."""
_MAX_TEXT = 8000


def _vtt_text(vtt: str) -> str:
    """Plain text from a WebVTT subtitle file, timestamps and repeated auto-caption lines removed."""
    lines, last = [], ""
    for line in vtt.splitlines():
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line or "-->" in line or line.startswith(("WEBVTT", "Kind:", "Language:")) or line.isdigit():
            continue
        if line != last:
            lines.append(line)
            last = line
    return " ".join(lines)


def youtube_text(url: str) -> str:
    """Title, description and (auto) subtitles of a YouTube video, via yt-dlp."""
    info = _run_json_lines(["yt-dlp", "--skip-download", "--dump-json", "--no-warnings", url], timeout=180)
    if not info:
        return ""
    d = info[0]
    text = f"{d.get('title', '')}\n{d.get('description', '')}"
    with tempfile.TemporaryDirectory() as tmp:
        _run(["yt-dlp", "--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,zh-Hans,zh.*",
              "--sub-format", "vtt", "--no-warnings", "-o", os.path.join(tmp, "%(id)s"), url], timeout=180)
        for name in sorted(os.listdir(tmp))[:1]:
            text += "\nTranscript: " + _vtt_text(open(os.path.join(tmp, name), encoding="utf-8", errors="replace").read())
    return text[:_MAX_TEXT]


_META_DESC = re.compile(r"""<meta[^>]+name=["']description["'][^>]+content=["']([^"']*)""", re.IGNORECASE)


def bilibili_text(url: str, fetcher) -> str:
    """Title and description from the Bilibili video page (the fetcher falls back to the NAS browser on 412)."""
    result = fetcher.fetch(url)
    if not result.ok:
        return ""
    m = _META_DESC.search(result.text)
    return htmllib.unescape(m.group(1))[:_MAX_TEXT] if m else ""


def _json_object(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def written_recipe(reply: str) -> dict | None:
    """The model's recipe when it is usable (3+ ingredients and 2+ steps), else None."""
    data = _json_object(reply)
    if not data or data.get("none"):
        return None
    ingredients = [str(i).strip() for i in data.get("ingredients") or [] if str(i).strip()][:15]
    steps = [str(st).strip() for st in data.get("steps") or [] if str(st).strip()][:10]
    if len(ingredients) < 3 or len(steps) < 2:
        return None
    out = {"ingredients": ingredients, "steps": steps}
    for key in ("servings", "minutes"):
        if isinstance(data.get(key), (int, float)) and 0 < data[key] < 1000:
            out[key] = int(data[key])
    return out


def write_up(out_dir: Path, llm, fetcher, limit: int = RECIPES_PER_DAY, texts=None) -> int:
    """Writes the recipe for up to `limit` not-yet-checked videos (most watched first) into
    videos.json. Every video is checked once; one without a usable recipe is never asked again."""
    path = out_dir / "videos.json"
    try:
        videos: list[dict] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    texts = texts or {"youtube": lambda v: youtube_text(v["url"]), "bilibili": lambda v: bilibili_text(v["url"], fetcher)}
    todo = sorted((v for v in videos if not v.get("recipe_checked")), key=lambda v: -(v.get("views") or 0))[:limit]
    written = 0
    for v in todo:
        v["recipe_checked"] = True
        try:
            text = texts[v["platform"]](v)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not read %s: %s", v["url"], exc)
            continue
        if len(text) < 150:  # nothing to write a recipe from
            continue
        try:
            reply = llm.complete(RECIPE_SYSTEM, f"Title: {v['title']}\n\n{text}", web_search=False).text
        except Exception as exc:  # noqa: BLE001
            log.warning("recipe write-up failed for %s: %s", v["url"], exc)
            v["recipe_checked"] = False  # try again another day
            continue
        recipe = written_recipe(reply)
        if recipe:
            v.update(recipe)
            written += 1
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(videos, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    log.info("videos: %d recipes written from %d checked", written, len(todo))
    return written


def safe_refresh(out_dir: Path | None, day: date, llm=None, fetcher=None) -> None:
    if out_dir is None:
        return
    try:
        refresh(out_dir, day)
        if llm is not None:
            write_up(out_dir, llm, fetcher)
    except Exception as exc:  # noqa: BLE001 - the website must never cost a daily run
        log.warning("video refresh failed: %s", exc)

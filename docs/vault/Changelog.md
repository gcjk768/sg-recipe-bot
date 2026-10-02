---
tags: [active]
updated: 2026-10-02
---
# Changelog

## 2026-10-02
- feat: vault Activity log moved to a date tree, `Activity/YYYY/MM/YYYY-MM-DD.md` (`recipebot/vault.py` `_day_path`); any flat `Activity/YYYY-MM-DD.md` is moved (never deleted) into `YYYY/MM/` when the vault opens (`Vault._migrate`); `Home.md` now links the current month folder and the last 7 day notes. Memory (`recent_menu`) reads the tree, with a fallback to a flat note the migration could not move
- feat: folded the NAS hot-fixes made through NAS Doctor on James's /ask requests into the repo: 51 posts a day (`recipebot/rotation.py` `PER_MEAL=17`, `recipebot/pipeline.py` `DAILY_PAUSE=720` so the batch spans 08:00–22:00), recipe posts back to the flat title + meta layout (`recipebot/render.py`), link preview on the first message so the photo shows
- feat: NAS vault "movement log + memory" (`recipebot/vault.py` `Vault`). Writes `Activity/YYYY-MM-DD.md` (`- HH:MM emoji **what** · detail · [[entity]]`, SGT: daily run started/finished, each recipe posted per meal, failed runs, missed days, unfinished runs), one `Recipes/<Title>.md` per post (frontmatter + full recipe + append-only `## History`), and `Home.md` (this week's menu). Atomic writes, chmod 664 + chown uid 1000; best-effort, never raises
- feat: memory — every brief gets `recent_menu`, the last 14 days of posted titles + cuisines from the vault, newest first, capped at 4,000 chars (`recipebot/brief.py` `RECENT_MENU_HEADER`, `recipebot/pipeline.py` `_run_inner`). The SQLite dedupe (`already_sent`, `recent_mains`) is unchanged
- ops: `VAULT_DIR=/vault` in `docker-compose.yml`, host folder from `VAULT_HOST_PATH` in `.env` (NAS: `/volume1/James/Obsidian/SG Recipes`, default `./data/vault`)

## 2026-10-01
- feat: every Telegram message uses the shared HTML "card" style — meal header (`SECTION_TITLES`), short recipe card (⏱ time · 👥 serves, 🔥 kcal · 💰 cost, 🔗 Recipe link, hashtags), divider, then why/ingredients/steps/notes in an `<blockquote expandable>` (`recipebot/render.py` `render_recipe`). Admin alerts (failed run, missed day, unfinished run) and `recipebot test-telegram` are cards too, background in an expandable quote (`recipebot/render.py` `render_alert`, `recipebot/pipeline.py`, `recipebot/scheduler.py`, `recipebot/cli.py`). Same information as before
- feat: escaping, block-safe splitting and plain-text fallback centralised in `recipebot/telegram.py` (`esc`, `split_blocks`, `send_html`, `html_to_plain`); a 400 "can't parse entities" is resent as plain text; link previews now off by default. `send_plain` removed (alerts go through `send_html`)

## 2026-09-30
- fix: when every recipe in a reply fails validation, the run asks the model once more with the rejection reasons (`recipebot/pipeline.py` `_run_inner`); rejected dishes aren't in history, so the model kept re-picking the same one (3× shakshuka over the 20 min breakfast cap)
- feat: 15 posts a day — 5 breakfast, 5 lunch, 5 dinner at 16:00, one `claude -p` call per recipe; lunch/dinner draw 10 distinct main categories shifted daily; each post leads with its meal hashtag (`recipebot/rotation.py` `daily_plan`, `recipebot/pipeline.py` `run_daily`, `recipebot/scheduler.py`). The two-week rotation now only drives manual `recipebot run`. A restart mid-batch skips the rest of that day
- ops: `pull_policy: build` on `recipebot` (`docker-compose.yml`) — Dockge Update now rebuilds the image from the code on disk
- feat: failure alerts carry an auto-diagnosis — `recipebot/pipeline.py` `Pipeline.diagnose` sends the alert back through the model client (no tools) for cause + fix; if the model is unreachable too, the alert says so and points at `claude setup-token`
- fix: scheduler reports every missed day once — one admin alert lists each date since the last finished run whose post time passed with no run (multi-day NAS sleep, outage ending after midnight); a run killed half way is reported even when the restart is past the window; a new database never reports days before it existed and skips a catch-up when the next post is under 12 h away (`recipebot/scheduler.py` `report_gap`, `recipebot/history.py` `run_day_span`)
- fix: `RECIPEBOT_CATCH_UP_HOURS` capped at 23; with 0, a wake more than 15 min late no longer posts; `RECIPEBOT_RUN_ON_START` runs the pending slot, which may be yesterday's just after midnight (`recipebot/config.py`, `recipebot/scheduler.py`)
- fix: a 502 on `sendMessage` is ambiguous like 500/504 (only 503 is resent); the ambiguous error no longer chains the raw exception that holds the token URL; CLI prints Telegram errors as one redacted line (`recipebot/telegram.py`, `recipebot/cli.py`)
- fix: `run_day` backfill for old rows only with a known timezone, and `recipebot history` passes it (`recipebot/history.py`, `recipebot/cli.py`)
- fix: nutrition line needs one home portion (20–2500 kcal, macros ≤ 250 g, macros roughly matching kcal); per-serving cost kept only within rounding of the total (`recipebot/models.py`)
- fix: charset labels like `undefined` fall back to UTF-8 (`recipebot/web.py` `decode_body`); deeply nested JSON is a normal parse failure with the retry (`recipebot/parsing.py`); more plain seasonings excluded from the ingredient cap — sprays, ice, "salt and sugar", "kosher or sea salt" (`recipebot/validate.py`)
- docs: README scheduling notes, retry policy and catch-up bound brought in line with the code
- docs: README rewritten (highlights, flow, stack, limitations) + draw.io architecture diagram (`docs/architecture.drawio`, `.drawio.svg`, `.png`)

## 2026-09-29
- ops: moved to the NAS — Dockge stack `/volume1/docker/sg-recipe-bot` (code + `.env` 600 + `data/history.sqlite`); PC container removed
- feat: meal hashtags — recipe JSON gains `meals` (1–2 of breakfast/lunch/dinner/supper, `recipebot/models.py`), rendered first in the hashtag line (`recipebot/render.py`), asked for in `recipebot/prompts/system_prompt.txt`; the 17 launch posts were re-tagged in place via editMessageText
- ops: live on the PC's Docker (`docker compose up -d`), container signed in via `CLAUDE_CODE_OAUTH_TOKEN` in `.env`; in-container dry run OK; first post 2026-09-30 16:00
- feat: `LLM_PROVIDER=cli` (default) runs `claude -p` like the trading desk (`recipebot/llm.py` `ClaudeCLIClient`); CLI baked into the image (`Dockerfile`), login in `./data/.home`
- fix: source-page check used plain `requests`, which Cloudflare 403s by TLS fingerprint; now `curl_cffi` Chrome impersonation (`recipebot/web.py`), bot-challenge 403 kept with a warning (`recipebot/validate.py`)
- feat: bot @jameskoh_sgrecipe_bot created, member of James Channel, posts to topic Recipe (2765)
- feat: forum-topic posting via `CHAT/TOPIC` chat id (`recipebot/telegram.py`, `recipebot/config.py`)
- test: skip SIGTERM scheduler test on Windows (`tests/test_scheduler.py`)
- docs: vault created

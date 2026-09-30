---
tags: [active]
updated: 2026-09-30
---
# Changelog

## 2026-09-30
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
- feat: bot @owner_sgrecipe_bot created, member of the owner Channel, posts to topic Recipe (2765)
- feat: forum-topic posting via `CHAT/TOPIC` chat id (`recipebot/telegram.py`, `recipebot/config.py`)
- test: skip SIGTERM scheduler test on Windows (`tests/test_scheduler.py`)
- docs: vault created

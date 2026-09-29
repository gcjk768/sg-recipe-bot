---
tags: [active]
updated: 2026-09-29
---
# Changelog

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

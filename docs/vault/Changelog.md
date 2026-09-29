---
tags: [active]
updated: 2026-09-29
---
# Changelog

## 2026-09-29
- feat: `LLM_PROVIDER=cli` (default) runs `claude -p` like the trading desk (`recipebot/llm.py` `ClaudeCLIClient`); CLI baked into the image (`Dockerfile`), login in `./data/.home`
- fix: source-page check used plain `requests`, which Cloudflare 403s by TLS fingerprint; now `curl_cffi` Chrome impersonation (`recipebot/web.py`), bot-challenge 403 kept with a warning (`recipebot/validate.py`)
- feat: bot @owner_sgrecipe_bot created, member of the owner Channel, posts to topic Recipe (2765)
- feat: forum-topic posting via `CHAT/TOPIC` chat id (`recipebot/telegram.py`, `recipebot/config.py`)
- test: skip SIGTERM scheduler test on Windows (`tests/test_scheduler.py`)
- docs: vault created

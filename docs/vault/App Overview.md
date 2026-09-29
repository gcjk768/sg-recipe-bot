---
tags: [active]
updated: 2026-09-29
---
# App Overview

Daily recipe curator for Singapore home cooking. Asks Claude (web search) for one recipe, validates it, posts Telegram HTML.

- Target: James Channel `-1002069000031`, topic **2765** → `TELEGRAM_CHAT_ID=-1002069000031/2765`
- Topic parsing: `recipebot/telegram.py` (`send_message`), validated in `recipebot/config.py` (`_chat_id`)
- Daily loop: `recipebot/scheduler.py` (16:00 SGT); one run: `recipebot/pipeline.py`
- Model call: `recipebot/llm.py`; prompts: `recipebot/prompts/`
- Deploy: `Dockerfile`, `docker-compose.yml`; state in `./data/history.sqlite`

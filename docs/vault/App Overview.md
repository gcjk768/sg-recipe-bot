---
tags: [active]
updated: 2026-09-29
---
# App Overview

Daily recipe curator for Singapore home cooking. Asks Claude (web search) for one recipe, validates it, posts Telegram HTML.

- Target: James Channel `-1002069000031`, topic **2765** → `TELEGRAM_CHAT_ID=-1002069000031/2765`
- Topic parsing: `recipebot/telegram.py` (`send_message`), validated in `recipebot/config.py` (`_chat_id`)
- Daily loop: `recipebot/scheduler.py` (16:00 SGT); one run: `recipebot/pipeline.py`
- Bot: @jameskoh_sgrecipe_bot (token in `.env`, never committed)
- Model call: `recipebot/llm.py` — `ClaudeCLIClient` (`claude -p --system-prompt-file ... --tools WebSearch,WebFetch`) by default, `AnthropicClient` with `LLM_PROVIDER=api`; prompts: `recipebot/prompts/`
- Page check: `recipebot/web.py` (`curl_cffi` Chrome impersonation, `is_bot_wall`), `recipebot/validate.py` (`check_source_page`)
- Deploy: `Dockerfile`, `docker-compose.yml`; state in `./data/history.sqlite`

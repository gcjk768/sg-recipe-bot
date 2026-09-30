---
tags: [active]
updated: 2026-09-30
---
# App Overview

Daily recipe curator for Singapore home cooking. Posts 15 recipes a day (5 breakfast, 5 lunch, 5 dinner), one model call each; asks Claude (web search), validates, posts Telegram HTML.

- Target: James Channel `-1002069000031`, topic **2765** → `TELEGRAM_CHAT_ID=-1002069000031/2765`
- Topic parsing: `recipebot/telegram.py` (`send_message`), validated in `recipebot/config.py` (`_chat_id`)
- Daily loop: `recipebot/scheduler.py` (16:00 SGT) → `recipebot/pipeline.py` `Pipeline.run_daily` over `recipebot/rotation.py` `daily_plan` (`PER_MEAL`, `MAIN_CATEGORIES` rotated daily; slot's meal tag forced first); one run: `recipebot/pipeline.py` `Pipeline.run`
- Missed days: one attempt per date per process, catch-up within `RECIPEBOT_CATCH_UP_HOURS` (0–23), every missed date reported once to `TELEGRAM_ADMIN_CHAT_ID` (`recipebot/scheduler.py` `report_gap`)
- Bot: @jameskoh_sgrecipe_bot (token in `.env`, never committed)
- Model call: `recipebot/llm.py` — `ClaudeCLIClient` (`claude -p --system-prompt-file ... --tools WebSearch,WebFetch`) by default, `AnthropicClient` with `LLM_PROVIDER=api`; prompts: `recipebot/prompts/`
- Meal tags: `meals` field (`recipebot/models.py` `MEALS`) → leading #breakfast/#lunch/#dinner/#supper (`recipebot/render.py` `hashtags_line`); tap a tag in Telegram to list that meal
- Page check: `recipebot/web.py` (`curl_cffi` Chrome impersonation, `is_bot_wall`), `recipebot/validate.py` (`check_source_page`)
- Failure alerts: admin chat (`TELEGRAM_ADMIN_CHAT_ID`) gets the run summary + "Diagnosis:" from `recipebot/pipeline.py` `Pipeline.diagnose` (`DIAGNOSE_SYSTEM`)
- Deploy: `Dockerfile`, `docker-compose.yml`; state in `./data/history.sqlite`

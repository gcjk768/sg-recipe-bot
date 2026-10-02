---
tags: [active]
updated: 2026-10-02
---
# App Overview

Daily recipe curator for Singapore home cooking. Posts 51 recipes a day (17 breakfast, 17 lunch, 17 dinner, ~12 min apart from 08:00 so the batch spans 08:00–22:00 SGT), one model call each; asks Claude (web search), validates, posts Telegram HTML.

- Target: the owner Channel `<TELEGRAM_CHAT_ID>`, topic **2765** → `TELEGRAM_CHAT_ID=<TELEGRAM_CHAT_ID>/2765`
- Topic parsing: `recipebot/telegram.py` (`send_message`), validated in `recipebot/config.py` (`_chat_id`)
- Daily loop: `recipebot/scheduler.py` (`RECIPEBOT_POST_TIME`, 08:00 SGT on the NAS; `DAILY_PAUSE` 720 s between posts) → `recipebot/pipeline.py` `Pipeline.run_daily` over `recipebot/rotation.py` `daily_plan` (`PER_MEAL`, `MAIN_CATEGORIES` rotated daily; slot's meal tag forced first); one run: `recipebot/pipeline.py` `Pipeline.run`
- Missed days: one attempt per date per process, catch-up within `RECIPEBOT_CATCH_UP_HOURS` (0–23), every missed date reported once to `TELEGRAM_ADMIN_CHAT_ID` (`recipebot/scheduler.py` `report_gap`)
- Bot: @owner_sgrecipe_bot (token in `.env`, never committed)
- Model call: `recipebot/llm.py` — `ClaudeCLIClient` (`claude -p --system-prompt-file ... --tools WebSearch,WebFetch`) by default, `AnthropicClient` with `LLM_PROVIDER=api`; prompts: `recipebot/prompts/`
- Meal tags: `meals` field (`recipebot/models.py` `MEALS`) → leading #breakfast/#lunch/#dinner/#supper (`recipebot/render.py` `hashtags_line`); tap a tag in Telegram to list that meal
- Page check: `recipebot/web.py` (`curl_cffi` Chrome impersonation, `is_bot_wall`), `recipebot/validate.py` (`check_source_page`)
- Failure alerts: admin chat (`TELEGRAM_ADMIN_CHAT_ID`) gets a card with run id, detail and 🩺 Diagnosis from `recipebot/pipeline.py` `Pipeline.diagnose` (`DIAGNOSE_SYSTEM`), full run summary in an expandable quote (`recipebot/render.py` `render_alert`)
- Message style: recipe posts are the flat layout the owner asked for on 2026-10-02 (title line, meta line, why, ingredients, steps, extras, link, hashtags; `recipebot/render.py` `render_recipe`), link preview on for the first message so the photo shows. Alerts stay HTML cards (`render_alert`, `SECTION_TITLES`). All sends go through `recipebot/telegram.py`: `esc`/`esc_attr` for every dynamic value, `split_blocks` splits only between blocks (≤4096), `send_html` for multi-block cards, `send_message` resends as plain text (`html_to_plain`) on a 400 "can't parse entities"; previews off
- Obsidian vault (movement log + memory): `recipebot/vault.py` `Vault`, root `VAULT_DIR` (`/vault`, NAS host `/volume1/<USER>/Obsidian/SG Recipes` via `VAULT_HOST_PATH`). `Activity/YYYY/MM/YYYY-MM-DD.md` event lines (flat `Activity/YYYY-MM-DD.md` notes are moved into the date tree on start, `Vault._migrate`), `Recipes/<Title>.md` per post (`recipe_note`), `Home.md` with the current month folder, latest day notes and this week's menu (`write_home`). Events from `recipebot/pipeline.py` (`run_daily`, `run`, `_run_inner`) and `recipebot/scheduler.py` (`_vault_log`: missed/unfinished days). Memory: `Vault.recent_menu` (14 days, newest first, ≤4,000 chars) → `BriefInputs.recent_menu` → appended to the brief (`recipebot/brief.py`). Best-effort, off when `VAULT_DIR` is unset
- Deploy: `Dockerfile`, `docker-compose.yml`; state in `./data/history.sqlite`

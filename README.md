# RecipeBot

A small scheduled bot for a NAS. Once a day it asks a model for one simple recipe, checks the
answer against a fixed set of rules, and posts the recipe to a private Telegram channel as a tidy
HTML message with an estimated cost and calories. It is written for a home cook in Singapore, but
the categories, prompt and rotation are all plain files you can edit.

The full design (system prompt, run brief, validation checks, Telegram template, rotation) is in
[`docs/prompt_pack.md`](docs/prompt_pack.md). This README covers running it.

## How a run works

1. The rotation decides today's category and theme (Week A and Week B, Monday to Sunday).
2. The app builds the run brief from the history database: the last 7 main ingredients and the
   last 90 days of posted titles and URLs, so the model does not repeat itself.
3. The model gets the system prompt verbatim plus the brief. By default it searches the web itself
   (Anthropic's server side web search tool). In candidates mode the app fetches pages from your own
   URL lists and pastes their schema.org Recipe data into the brief instead.
4. The JSON that comes back is parsed (one retry with the parse error appended) and every recipe is
   checked: category, difficulty, time caps, ingredient count, step count and length, an `https://`
   source URL that returns 200 and looks like a recipe page, and no duplicate of anything already
   posted. Failing recipes are dropped, never patched.
5. Each surviving recipe is rendered as one Telegram HTML message and sent, then written to the
   history table. If nothing could be posted, the run is logged and a short alert goes to your
   private admin chat.

## Setup

1. Create the bot with @BotFather and add it to your channel as an administrator that can post.
   For a public channel the chat id is `@yourchannelname`. For a private channel, post once after
   adding the bot, then read the numeric id from `https://api.telegram.org/bot<TOKEN>/getUpdates`.
2. Optional but recommended: start a private chat with the bot, send it any message, and take your
   own numeric chat id from the same `getUpdates` call. Put it in `TELEGRAM_ADMIN_CHAT_ID` so failed
   runs reach your phone.
3. Get an Anthropic API key.
4. `cp .env.example .env` and fill in the values.

### Run with Docker (the NAS)

```bash
docker compose build
docker compose run --rm recipebot recipebot check-config
docker compose run --rm recipebot recipebot test-telegram
docker compose run --rm recipebot recipebot run --dry-run      # full run, prints the post, sends nothing
docker compose up -d                                           # daily loop at RECIPEBOT_POST_TIME
docker compose logs -f
```

The container stays up, sleeps until the next post time in `TZ`, runs once, and sleeps again.
`./data` is mounted at `/data` and holds `history.sqlite`, the optional `rotation.json` and the
`candidates/` lists, so it survives rebuilds. If you prefer the NAS task scheduler, remove
`restart: always` and have the scheduler call `docker compose run --rm recipebot recipebot run`.

### Run locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export RECIPEBOT_DATA_DIR=./data
recipebot check-config
recipebot run --dry-run --category noodles
pytest
```

The CLI loads `.env` from the current directory (or `--env-file PATH`).

## Commands

| Command | What it does |
|---|---|
| `recipebot run` | One full run for today. `--category`, `--theme`, `--count 1..3`, `--servings`, `--date YYYY-MM-DD` override the rotation. `--dry-run` posts and records nothing. `--no-page-check` skips fetching source pages. |
| `recipebot loop` | The daemon: run every day at `RECIPEBOT_POST_TIME`. |
| `recipebot preview FILE.json` | Render a saved model reply (or one recipe object) as Telegram HTML. No network. |
| `recipebot rotation [--from DATE] [--days N]` | Print the upcoming schedule. |
| `recipebot history [--limit N]` | Recent posts and runs from the database. |
| `recipebot test-telegram [--admin]` | Send a hello to the channel or the admin chat. |
| `recipebot check-config` | Print the resolved settings with secrets masked and flag anything missing. |
| `recipebot system-prompt` | Print the system prompt exactly as it is sent. |

Exit code is 0 when a run posted (or dry ran) at least one recipe, 1 otherwise, 2 for a
configuration error.

## Settings

All settings are environment variables. See [`.env.example`](.env.example) for the full list with
comments. The ones you will touch:

| Variable | Default | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | required | The bot and the channel. |
| `TELEGRAM_ADMIN_CHAT_ID` | unset | Private chat that gets a short message when a run posts nothing. |
| `LLM_API_KEY` | required | Anthropic API key (`ANTHROPIC_API_KEY` also works). |
| `LLM_MODEL` | `claude-opus-5-5` | Model id. |
| `LLM_EFFORT` | `high` | `low`, `medium`, `high`, `xhigh`, `max`, or `none` to omit it. |
| `LLM_FALLBACKS` | `default` | Server side refusal fallback (beta). `off` disables it. |
| `LLM_WEB_SEARCH_MAX_USES` | `10` | Searches allowed per call. |
| `LLM_ALLOWED_DOMAINS` | any | Comma separated list that restricts the model's web search to sites you trust. |
| `TZ` | `Asia/Singapore` | Timezone for the daily post time and the rotation date. |
| `RECIPEBOT_POST_TIME` | `16:00` | Daily post time, `HH:MM`. |
| `RECIPEBOT_SOURCE_MODE` | `search` | `search` lets the model search; `candidates` uses your URL lists. |
| `RECIPEBOT_COUNT` | `1` | Recipes per run, 1 to 3. Each is its own message, two seconds apart. |
| `RECIPEBOT_SERVINGS` | `2` | Servings requested. |
| `RECIPEBOT_ROTATION_EPOCH` | `2026-09-28` | A date in a Week A. Any weekday works; it is aligned to its Monday. |
| `RECIPEBOT_RUN_ON_START` | `false` | Also run once when the container starts. |
| `RECIPEBOT_PROMPTS_DIR` | unset | Folder with `system_prompt.txt` / `run_brief.txt` that override the packaged ones. |

## The rotation

Week A: high_protein, chinese_daily, western_daily, asian_daily, baking_cakes ("weekend bake"),
meal_prep ("lunchbox for the week"), soups. Week B: quick_20, local_sg, rice_cooker, noodles,
desserts_no_oven, seafood, eggs_tofu_veg.

To change it, copy [`data/rotation.example.json`](data/rotation.example.json) to
`data/rotation.json`. `weeks` replaces the cycle (any number of weeks, seven entries each, Monday
first), `overrides` pins a single date to a category and theme, which is how the occasional
categories (breakfast, sides, sauces_basics, use_it_up with the leftover as theme, custom with the
scope as theme) get slotted in. `recipebot rotation` shows the result.

## Candidates mode

Set `RECIPEBOT_SOURCE_MODE=candidates` and keep one file per category in `data/candidates/`, for
example `data/candidates/noodles.txt`, with one exact recipe page URL per line. Each run picks a
few unused URLs at random, fetches them, extracts the schema.org Recipe data and pastes it into the
brief, so the model chooses and rewrites instead of searching. It is cheaper, more predictable and
keeps you on sites you trust. If a category has no usable URLs left, that run falls back to web
search. See [`data/candidates/README.md`](data/candidates/README.md).

## What gets posted

```
🍳 Garlic Soy Chicken with Broccoli
High protein · Chinese inspired · 25 min · easy · serves 2

One pan, about 25 minutes, and every ingredient is a FairPrice regular.

Ingredients
• 400 g chicken thigh, boneless, cut into bite sized pieces
• ...

Steps
1. Toss the chicken with the cornstarch and a pinch of salt.
2. ...

🔥 About 420 kcal per serving, 40 g protein, 20 g carbs, 20 g fat (estimate)
💰 Ingredients about S$9.50 for 2 servings, S$4.75 each, chicken thigh is most of the cost (estimate)
💪 Protein: about 40 g per serving
💡 Swap the broccoli for any green vegetable you have.
🧊 Keeps 3 days in the fridge and reheats well.

🔗 Full recipe at Example Recipes
#highprotein #onepan #weeknight #mealprep
```

The calories, macros and cost are the model's estimates from the ingredient quantities at regular
Singapore supermarket prices. They are labelled as estimates, and a missing or implausible estimate
drops that line rather than the recipe. The protein line only appears in the high_protein
category. Link previews stay on, so Telegram shows the recipe's own photo under the post.

## Project layout

```
recipebot/
  prompts/system_prompt.txt   the system prompt, sent verbatim
  prompts/run_brief.txt       the brief template
  categories.py               the category table (labels, hashtags, time caps, emoji)
  rotation.py                 Week A / Week B and rotation.json
  history.py                  SQLite: sent_recipes and runs
  brief.py                    fills the brief
  candidates.py, web.py       candidate pages and schema.org extraction, page fetching
  llm.py                      the Anthropic call (web search, pause_turn, refusal fallback)
  parsing.py                  JSON extraction and the top level contract
  validate.py                 section 3 checks
  render.py                   Telegram HTML template
  telegram.py                 Bot API client with rate limit handling
  pipeline.py                 one run end to end
  scheduler.py                the daily loop
  cli.py                      commands
tests/                        pytest suite, no network
docs/prompt_pack.md           the design document
```

## Adding a category

Add a block to section 2 of `recipebot/prompts/system_prompt.txt`, a row to `CATEGORIES` in
`recipebot/categories.py` (label, hashtag, time cap, emoji), and the row in `docs/prompt_pack.md`.
The tests check that every category key has a block in the prompt.

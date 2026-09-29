# Recipe bot prompt pack

Purpose: a scheduled bot on your NAS asks a model for a small number of simple recipes, checks the result, and posts each recipe to your Telegram channel. This file holds the four pieces the bot needs: the system prompt the model gets on every call, the run brief the app fills in each time, the checks the app runs on the JSON that comes back, and the Telegram rendering rules. A short section at the end covers the pipeline itself.

Categories use these keys everywhere (brief, JSON, history table, hashtags). The time cap is what the app checks in section 3. "Hands on" means the cap applies to prep_minutes, because the oven, the fridge or a simmering pot does the rest.

```text
key                label                       hashtag         time cap
high_protein       High protein                #highprotein    45 min
chinese_daily      Chinese daily               #chinese        45 min
western_daily      Western daily               #western        45 min
asian_daily        Asian daily                 #asian          45 min
local_sg           Singapore favourites        #localsg        45 min
soups              Soups                       #soups          15 min hands on, 90 min total
rice_cooker        Rice cooker meals           #ricecooker     60 min
noodles            Noodles                     #noodles        30 min
seafood            Seafood                     #seafood        30 min
eggs_tofu_veg      Eggs, tofu and vegetables   #meatfree       30 min
sides              Vegetable sides             #sides          15 min
quick_20           Under 20 minutes            #quick          20 min
meal_prep          Meal prep and lunchbox      #mealprep       60 min
breakfast          Breakfast and brunch        #breakfast      20 min
use_it_up          Use it up                   #useitup        30 min
desserts_no_oven   No oven desserts            #desserts       20 min hands on
baking_cakes       Baking and cakes            #baking         30 min hands on
sauces_basics      Sauces and basics           #basics         30 min
custom             Custom                      #custom         45 min
```

The first five are the daily core. Everything else is optional: the rotation in section 5 decides how often each appears, and use_it_up and custom take their scope from the theme line, so nothing is limited to this list. To add a key of your own, add a matching block to section 2 of the system prompt and a row here.

## 1. System prompt (load verbatim, send on every call)

The verbatim text lives in [`recipebot/prompts/system_prompt.txt`](../recipebot/prompts/system_prompt.txt). Do not edit it casually; the app sends it byte for byte on every call.

Two fields were added to the section 7 contract after the first draft of this pack, so every post can show what the meal roughly costs and what is in it:

* `nutrition_per_serving`: `{"kcal": 420, "protein_g": 40, "carbs_g": 20, "fat_g": 20}`, integers per serving, estimated from the ingredient quantities (calories to the nearest 10 kcal, grams to the nearest 5 g). Required for every recipe.
* `cost_estimate`: `{"total_sgd": 9.5, "per_serving_sgd": 4.75, "note": "chicken thigh is most of the cost"}`, Singapore dollars at regular supermarket prices for the quantities the recipe uses (pantry staples at the amount used, not the whole bottle), rounded to the nearest 0.05. Required for every recipe.

Both are estimates and the post says so. A missing or implausible estimate drops its line from the post; it never drops the recipe.

## 2. Run brief (the user message, filled by the app each run)

```text
RUN BRIEF
run_id: {{run_id}}
category: {{category}}
count: {{count}}
servings: {{servings}}
theme: {{theme}}
recent_mains: {{recent_mains}}
already_sent:
{{already_sent}}
candidate_pages:
{{candidate_pages}}
```

Variable notes:

* run_id: any string for logging. The date works.
* category: one of the keys in the category list at the top of this file.
* count: 1 by default, so the channel gets one recipe at a time. It can go up to 3 for a batch, and each recipe is still posted as its own message.
* servings: 2 unless you want otherwise.
* theme: free text or "none". Examples: "under 20 minutes", "use up leftover rice", "rainy day soup", "lunchbox for the week", "weekend bake for guests".
* recent_mains: comma separated main ingredients from the last 7 posts, for example "chicken thigh, salmon, tofu, minced pork". Send "none" on the first run.
* already_sent: one line per past post as "title | url". Send the last 90 days, capped at about 150 lines so the prompt stays small. Send "none" on the first run.
* candidate_pages: "none" if the model should search the web itself. If your app fetches pages instead, paste them here, one block per page:

```text
URL: https://www.example.com/recipes/12345
TITLE: Page title
CONTENT: the recipe's ingredients, times and instructions as plain text
```

Most recipe sites embed schema.org Recipe data in the page head with exactly those fields, so the app can pull it out with a few lines of code and hand the model clean text instead of a whole web page. This route is cheaper and more predictable than letting the model search, and it lets you keep a fixed list of trusted sites.

## 3. What the app checks before posting

The system prompt describes the JSON, but the app should still validate it, because models drift:

* The top level keys are exactly run and recipes, or error, run and recipes.
* Each recipe's category equals the brief's category.
* difficulty is easy or medium.
* total_minutes is within the category's time cap from the category list at the top of this file. For the caps marked hands on, check prep_minutes instead. For soups, check both.
* The ingredient count is 10 or fewer after removing salt, pepper, sugar, oil and water.
* steps has 1 to 8 entries, each 220 characters or fewer.
* source.url starts with https://, returns HTTP 200 when fetched, and the page mentions ingredients or carries schema.org Recipe data.
* source.url and a normalised title (lowercase, punctuation stripped) are not already in the history table.
* On a JSON parse failure, retry once with the parse error appended to the brief under a line reading "PREVIOUS ATTEMPT FAILED:". On a second failure, log it and send yourself a short error message.

Anything that fails a check is dropped, not patched. If nothing survives, the run posts nothing and logs why.

## 4. Telegram rendering

Send with parse_mode set to HTML. HTML is easier than MarkdownV2 because only three characters need escaping (&, <, >), and every field must be escaped before it goes into the template.

One recipe per message, always. A message never carries two recipes, and a run that returns more than one recipe sends them as separate messages a couple of seconds apart. Telegram allows 4096 characters per message, and the length caps in the system prompt keep a post around 1500 to 2500 characters. If a render still exceeds 4000, send that one recipe's ingredients and steps as two consecutive messages, with the title repeated on the second.

Template:

```text
🍳 <b>{title}</b> ({title_zh})
<i>{category_label} · {cuisine} · {total_minutes} min · {difficulty} · serves {servings}</i>

{why_it_fits}

<b>Ingredients</b>
• {qty} {unit} {item}, {note}
• ...

<b>Steps</b>
1. {step}
2. ...

🔥 About {kcal} kcal per serving, {protein_g} g protein, {carbs_g} g carbs, {fat_g} g fat (estimate)
💰 Ingredients about S${total_sgd} for {servings} servings, S${per_serving_sgd} each, {cost note} (estimate)
💪 Protein: about {protein_per_serving_g} g per serving
💡 {tip}
🧊 {storage}

🔗 <a href="{source.url}">Full recipe at {source.site}</a>
#{category_hashtag} #{tag} #{tag}
```

Rendering rules:

* Category labels and hashtags come from the category list at the top of this file. Tags become hashtags with the spaces removed (#onepan, #mealprep). Hashtags make the channel searchable later.
* Drop the bracketed title_zh when it is null. Drop the protein line unless the category is high_protein. Drop the nutrition and cost lines when the estimate is missing or implausible (no calories, a negative or absurd number). Drop the ", {cost note}" part when the note is empty and the "for N servings, S$X each" part when the recipe serves one. Drop the tip and storage lines when empty. Drop the ", {note}" part when the note is empty. Print whole numbers without a decimal (400 g, not 400.0 g) and money as S$9.50 or S$10.
* Vary the leading emoji by category if you like: 🧁 for baking_cakes and desserts_no_oven, 🍲 for soups and rice_cooker, 🍜 for noodles, 🥗 for sides and eggs_tofu_veg, 🍳 for the rest.
* Leave link previews on. Telegram previews the first link in the message, so the recipe's own photo appears under the post without you hosting any images.
* Rate limits: one message per second per chat. When a run returns three recipes, pause two seconds between posts.
* Channel setup: create the bot with @BotFather, add it to the channel as an administrator with permission to post, and use "@yourchannelname" as chat_id. For a private channel use its numeric chat id instead: post once in the channel after adding the bot, then call getUpdates on the bot to read the id.

## 5. Pipeline notes for the NAS

Suggested rotation, Singapore time, one recipe per day so the channel gets one recipe at a time. Posting around 4 pm lands before anyone decides dinner. Two weeks cover the categories people cook most; the rest are slotted in by hand or on a third week.

Week A

* Monday: high_protein
* Tuesday: chinese_daily
* Wednesday: western_daily
* Thursday: asian_daily
* Friday: baking_cakes, theme "weekend bake"
* Saturday: meal_prep, theme "lunchbox for the week"
* Sunday: soups

Week B

* Monday: quick_20
* Tuesday: local_sg
* Wednesday: rice_cooker
* Thursday: noodles
* Friday: desserts_no_oven
* Saturday: seafood
* Sunday: eggs_tofu_veg

Occasional, or a Week C: breakfast, sides, sauces_basics, use_it_up (the theme names the leftover) and custom (the theme names the scope). The rotation is just a list in the app, so reorder it as you learn which posts you actually cook.

Flow per run:

1. Build the brief from the history table (sent_recipes: id, sent_at, category, title, url, main_ingredient, recipe_json).
2. Call the model with the system prompt and the brief. Either use a model call that can search the web itself, or fetch candidate pages first (section 2) so the model only has to choose and rewrite.
3. Validate (section 3). Retry once on bad JSON.
4. Render and send (section 4). Insert each posted recipe into the history table.
5. Log the run. If a run fails twice, send yourself a short error message to a private chat so a silent day does not go unnoticed.

Running it in Docker:

* A small image based on the slim Python image with requests, pydantic and your model provider's SDK. Keep history.sqlite on a mounted volume so it survives rebuilds. Keep secrets in an .env file: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, LLM_API_KEY, TZ.
* Simplest scheduling: the container stays up and runs a tiny loop that sleeps until the next 4 pm, runs the script, then sleeps again. That avoids depending on the NAS task scheduler, though UGOS Pro's task scheduler can also trigger a one shot `docker compose run recipebot` if you prefer the container to exit between runs.
* The bot only needs outbound internet. No ports need to be opened.

Compose sketch:

```yaml
services:
  recipebot:
    build: .
    env_file: .env
    environment: {TZ: Asia/Singapore}
    volumes: ["./data:/data"]
    restart: always
```

import re
from datetime import datetime, timedelta, timezone

from recipebot.brief import RECENT_MENU_HEADER
from recipebot.categories import get_category
from recipebot.models import Recipe
from recipebot.vault import MEMORY_CHARS, Vault, note_name
from tests.conftest import SGT, FakeLLM, make_recipe, reply_text
from tests.test_pipeline import build, sent_texts

LINE = re.compile(r"^- \d\d:\d\d \S+ \*\*[^*]+\*\*( · [^\n]+)*$")
WHEN = datetime(2026, 10, 2, 16, 3, tzinfo=SGT)


def recipe(**kw) -> Recipe:
    return Recipe.model_validate(make_recipe(meals=["dinner"], **kw))


def test_activity_line_format_in_sgt(tmp_path):
    vault = Vault(tmp_path, SGT)
    vault.log("\U0001f680", "daily run started", "2026-10-02 · 15 recipes planned", "Kaya Toast",
              when=datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc))
    text = (tmp_path / "Activity" / "2026" / "10" / "2026-10-02.md").read_text(encoding="utf-8")
    assert text.startswith("---\ntags: [log]\nupdated: 2026-10-02\n---\n")
    last = text.splitlines()[-1]
    assert last == "- 16:00 \U0001f680 **daily run started** · 2026-10-02 · 15 recipes planned · [[Kaya Toast]]"
    assert LINE.match(last)


def test_recipe_note_has_everything_and_history_is_append_only(tmp_path):
    vault = Vault(tmp_path, SGT)
    r, cat = recipe(title="Hainanese: Chicken Rice?"), get_category("high_protein")
    vault.posted(r, cat, "dinner", "run-1", WHEN)
    path = tmp_path / "Recipes" / "Hainanese Chicken Rice.md"
    note = path.read_text(encoding="utf-8")
    for want in ['title: "Hainanese: Chicken Rice?"', 'cuisine: "Chinese inspired"', "meal: dinner", "posted: 2026-10-02",
                 "total_minutes: 25", "kcal: 420", "cost_sgd: 9.5", 'link: "https://www.example.com/recipes/12345"',
                 "- **Hashtags:** #dinner", "S$9.50", "## Ingredients", "- 400 g chicken thigh, boneless, cut into bite sized pieces",
                 "## Steps", "1. Toss the chicken", "## Tips", "## Storage", "## History",
                 "- 2026-10-02 16:03 posted as dinner (run run-1)"]:
        assert want in note, want
    vault.posted(r, cat, "lunch", "run-2", WHEN + timedelta(days=1))
    history = path.read_text(encoding="utf-8").split("## History\n")[1].splitlines()
    assert history == ["- 2026-10-02 16:03 posted as dinner (run run-1)", "- 2026-10-03 16:03 posted as lunch (run run-2)"]
    activity = (tmp_path / "Activity" / "2026" / "10" / "2026-10-02.md").read_text(encoding="utf-8")
    assert "**posted dinner** · Hainanese: Chicken Rice? · Chinese inspired · [[Hainanese Chicken Rice]]" in activity
    home = (tmp_path / "Home.md").read_text(encoding="utf-8")
    assert "[[Hainanese Chicken Rice]] (Chinese inspired)" in home
    assert "`Activity/2026/10/`" in home and "**Latest notes:** [[2026-10-03]], [[2026-10-02]]" in home


def test_recent_menu_newest_first_14_days_and_capped(tmp_path):
    vault = Vault(tmp_path, SGT)
    cat = get_category("high_protein")
    vault.posted(recipe(title="Too Old"), cat, "lunch", "r", WHEN - timedelta(days=14))
    vault.posted(recipe(title="Older", cuisine="Malay"), cat, "lunch", "r", WHEN - timedelta(days=3))
    vault.posted(recipe(title="Newest", cuisine="Thai"), cat, "dinner", "r", WHEN)
    assert vault.recent_menu(WHEN) == ["2026-10-02 dinner: Newest (Thai)", "2026-09-29 lunch: Older (Malay)"]
    for i in range(200):
        vault.log("\U0001f37d", "posted lunch", f"Dish number {i:03d} with a long name · Peranakan", f"Dish {i}", WHEN)
    menu = vault.recent_menu(WHEN)
    assert sum(len(l) + 1 for l in menu) <= MEMORY_CHARS and len(menu) < 200
    assert menu[0].startswith("2026-10-02 lunch: Dish number 199")


def test_vault_memory_reaches_the_prompt_and_posts_are_written(settings, fixed_now, tmp_path):
    settings.vault_dir = tmp_path / "vault"
    Vault(settings.vault_dir, SGT).posted(recipe(title="Laksa Yesterday", cuisine="Peranakan"), get_category("soups"),
                                          "lunch", "r0", fixed_now() - timedelta(days=1))
    llm = FakeLLM([reply_text([make_recipe()])])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    assert pipeline.run(category="high_protein").status == "posted"
    assert RECENT_MENU_HEADER + "\n2026-09-28 lunch: Laksa Yesterday (Peranakan)" in llm.calls[0]["user"]
    assert (settings.vault_dir / "Recipes" / f"{note_name('Garlic Soy Chicken with Broccoli')}.md").is_file()
    assert "**posted" in (settings.vault_dir / "Activity" / "2026" / "09" / "2026-09-29.md").read_text(encoding="utf-8")


def test_no_vault_means_no_memory_section(settings, fixed_now):
    llm = FakeLLM([reply_text([make_recipe()])])
    pipeline, _, _, _ = build(settings, llm, fixed_now)
    pipeline.run(category="high_protein")
    assert RECENT_MENU_HEADER not in llm.calls[0]["user"]


def test_vault_errors_never_raise_or_cost_a_post(settings, fixed_now, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    vault = Vault(blocker, SGT)  # every mkdir/write under a file fails
    vault.log("x", "what", "detail", "e", WHEN)
    vault.posted(recipe(), get_category("high_protein"), "dinner", "r", WHEN)
    vault.write_home(WHEN)
    assert vault.recent_menu(WHEN) == []

    settings.vault_dir = blocker
    llm = FakeLLM([reply_text([make_recipe()])])
    pipeline, tg, _, _ = build(settings, llm, fixed_now)
    assert pipeline.run(category="high_protein").status == "posted" and len(sent_texts(tg)) == 1


def test_flat_activity_notes_are_moved_into_year_month_and_still_read(tmp_path):
    flat = tmp_path / "Activity"
    flat.mkdir()
    (flat / "2026-10-01.md").write_text("- 08:00 🍽 **posted lunch** · Laksa · Peranakan · [[Laksa]]\n", encoding="utf-8")
    (flat / "notes.md").write_text("keep me\n", encoding="utf-8")
    vault = Vault(tmp_path, SGT)
    assert not (flat / "2026-10-01.md").exists() and (flat / "2026" / "10" / "2026-10-01.md").is_file()
    assert (flat / "notes.md").is_file()
    assert vault.recent_menu(WHEN) == ["2026-10-01 lunch: Laksa (Peranakan)"]

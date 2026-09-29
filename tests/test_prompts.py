from pathlib import Path

from recipebot.categories import CATEGORIES
from recipebot.prompts import load_brief_template, load_system_prompt

PROMPT_FILE = Path(__file__).resolve().parents[1] / "recipebot" / "prompts" / "system_prompt.txt"


def test_system_prompt_is_the_packaged_file_verbatim():
    prompt = load_system_prompt()
    assert prompt == PROMPT_FILE.read_text(encoding="utf-8").rstrip("\n")
    assert prompt.startswith("You are the recipe curator for a small automated bot.")
    assert prompt.endswith("Never wrap the JSON in markdown fences and never add text before or after it.")


def test_system_prompt_has_all_sections_and_categories():
    prompt = load_system_prompt()
    for header in (
        "1. WHO THIS IS FOR",
        "2. CATEGORIES",
        "3. HARD RULES (reject any recipe that fails one)",
        "4. VARIETY",
        "5. SOURCING AND HONESTY",
        "6. WRITING STYLE",
        "7. OUTPUT FORMAT",
        "8. WHEN THINGS GO WRONG",
    ):
        assert f"\n{header}\n" in prompt
    lines = set(prompt.splitlines())
    for key in CATEGORIES:
        assert key in lines, f"category block for {key} missing from the system prompt"
    assert "```" not in prompt


def test_system_prompt_contract_mentions_new_estimate_fields():
    prompt = load_system_prompt()
    assert '"nutrition_per_serving": {"kcal": 420, "protein_g": 40, "carbs_g": 20, "fat_g": 20}' in prompt
    assert '"cost_estimate": {"total_sgd": 9.5, "per_serving_sgd": 4.75' in prompt
    assert "* nutrition_per_serving:" in prompt
    assert "* cost_estimate:" in prompt


def test_brief_template_placeholders():
    template = load_brief_template()
    assert template.startswith("RUN BRIEF\n")
    for key in ("run_id", "category", "count", "servings", "theme", "recent_mains", "already_sent", "candidate_pages"):
        assert "{{" + key + "}}" in template


def test_override_dir(tmp_path):
    (tmp_path / "system_prompt.txt").write_text("custom prompt\n", encoding="utf-8")
    assert load_system_prompt(tmp_path) == "custom prompt"
    assert load_brief_template(tmp_path).startswith("RUN BRIEF")  # falls back to the packaged file

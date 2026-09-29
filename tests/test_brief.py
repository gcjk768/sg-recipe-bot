from recipebot.brief import PREVIOUS_ATTEMPT_HEADER, BriefInputs, build_brief
from recipebot.prompts import load_brief_template


def test_first_run_uses_none_everywhere():
    brief = build_brief(load_brief_template(), BriefInputs(run_id="2026-09-29", category="high_protein"))
    assert brief == (
        "RUN BRIEF\nrun_id: 2026-09-29\ncategory: high_protein\ncount: 1\nservings: 2\ntheme: none\n"
        "recent_mains: none\nalready_sent:\nnone\ncandidate_pages:\nnone"
    )


def test_filled_brief():
    inputs = BriefInputs(
        run_id="r1",
        category="soups",
        count=2,
        servings=4,
        theme="rainy day soup",
        recent_mains=["chicken thigh", "salmon"],
        already_sent=["A | https://a", "B | https://b"],
        candidate_pages="URL: https://x\nTITLE: T\nCONTENT: C",
    )
    brief = build_brief(load_brief_template(), inputs)
    assert "category: soups\ncount: 2\nservings: 4\ntheme: rainy day soup\n" in brief
    assert "recent_mains: chicken thigh, salmon\n" in brief
    assert "already_sent:\nA | https://a\nB | https://b\ncandidate_pages:\nURL: https://x\nTITLE: T\nCONTENT: C" in brief
    assert PREVIOUS_ATTEMPT_HEADER not in brief


def test_previous_error_is_appended_under_header():
    inputs = BriefInputs(run_id="r1", category="noodles", theme="   ", previous_error="JSON parse error: Expecting value")
    brief = build_brief(load_brief_template(), inputs)
    assert "theme: none\n" in brief
    assert brief.endswith("candidate_pages:\nnone\nPREVIOUS ATTEMPT FAILED:\nJSON parse error: Expecting value\n")

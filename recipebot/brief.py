"""Builds the RUN BRIEF user message from the template in section 2 of the prompt pack."""

from __future__ import annotations

from dataclasses import dataclass, field

PREVIOUS_ATTEMPT_HEADER = "PREVIOUS ATTEMPT FAILED:"
RECENT_MENU_HEADER = (
    "recent_menu (posted in the last 14 days, newest first, from the vault): do not repeat these dishes "
    "or close variants, and pick a cuisine that adds variety:"
)


@dataclass
class BriefInputs:
    run_id: str
    category: str
    count: int = 1
    servings: int = 2
    theme: str | None = None
    recent_mains: list[str] = field(default_factory=list)
    already_sent: list[str] = field(default_factory=list)
    candidate_pages: str | None = None
    """Pre-formatted candidate page blocks, or None for 'none'."""
    previous_error: str | None = None
    recent_menu: list[str] = field(default_factory=list)
    """'date meal: title (cuisine)' lines from the vault, already capped."""


def build_brief(template: str, inputs: BriefInputs) -> str:
    theme = inputs.theme.strip() if inputs.theme and inputs.theme.strip() else "none"
    values = {
        "run_id": inputs.run_id,
        "category": inputs.category,
        "count": str(inputs.count),
        "servings": str(inputs.servings),
        "theme": theme,
        "recent_mains": ", ".join(inputs.recent_mains) if inputs.recent_mains else "none",
        "already_sent": "\n".join(inputs.already_sent) if inputs.already_sent else "none",
        "candidate_pages": inputs.candidate_pages.strip() if inputs.candidate_pages and inputs.candidate_pages.strip() else "none",
    }
    text = template
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    if inputs.recent_menu:
        text = text.rstrip("\n") + "\n" + RECENT_MENU_HEADER + "\n" + "\n".join(inputs.recent_menu) + "\n"
    if inputs.previous_error:
        text = text.rstrip("\n") + "\n" + PREVIOUS_ATTEMPT_HEADER + "\n" + inputs.previous_error.strip() + "\n"
    return text

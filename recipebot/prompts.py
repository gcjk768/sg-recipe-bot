"""Loads the system prompt and the run brief template.

The files ship inside the package. RECIPEBOT_PROMPTS_DIR can point at a folder holding
system_prompt.txt and run_brief.txt to override them without rebuilding the image.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

SYSTEM_PROMPT_FILE = "system_prompt.txt"
RUN_BRIEF_FILE = "run_brief.txt"


def _read(name: str, override_dir: Path | None) -> str:
    if override_dir is not None:
        candidate = override_dir / name
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return resources.files("recipebot").joinpath("prompts", name).read_text(encoding="utf-8")


def load_system_prompt(override_dir: Path | None = None) -> str:
    """The system prompt, sent verbatim on every call (trailing newline removed)."""
    return _read(SYSTEM_PROMPT_FILE, override_dir).rstrip("\n")


def load_brief_template(override_dir: Path | None = None) -> str:
    return _read(RUN_BRIEF_FILE, override_dir).rstrip("\n")

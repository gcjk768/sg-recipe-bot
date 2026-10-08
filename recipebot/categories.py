"""The category table from the prompt pack.

Every key here must have a matching block in section 2 of the system prompt.
The caps are what the app checks before posting (section 3 of the prompt pack).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    hashtag: str
    total_cap: int | None = None
    """Cap on total_minutes, or None when only the hands on cap applies."""
    hands_on_cap: int | None = None
    """Cap on prep_minutes (the "hands on" caps), or None."""
    emoji: str = "\U0001f373"  # 🍳

    def describe_cap(self) -> str:
        parts = []
        if self.hands_on_cap is not None:
            parts.append(f"{self.hands_on_cap} min hands on")
        if self.total_cap is not None:
            parts.append(f"{self.total_cap} min total")
        return ", ".join(parts)


_PAN = "🍳"  # 🍳

# the owner, 2026-10-04: high protein meal prep only. 2026-10-08: plus 1 to 5 cakes and bakes a day
# (Western and Eastern) for the family recipe website.
CATEGORIES: dict[str, Category] = {
    "high_protein": Category("high_protein", "High protein", "highprotein", total_cap=45, emoji=_PAN),
    "baking_cakes": Category("baking_cakes", "Cakes and baking", "baking", hands_on_cap=30, emoji="🎂"),
}


def get_category(key: str) -> Category:
    try:
        return CATEGORIES[key]
    except KeyError:
        raise KeyError(f"unknown category '{key}'. Known keys: {', '.join(CATEGORIES)}") from None


def is_known(key: str) -> bool:
    return key in CATEGORIES

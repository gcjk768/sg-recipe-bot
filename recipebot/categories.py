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


_CAKE = "\U0001f9c1"  # 🧁
_POT = "\U0001f372"  # 🍲
_NOODLE = "\U0001f35c"  # 🍜
_SALAD = "\U0001f957"  # 🥗
_PAN = "\U0001f373"  # 🍳

CATEGORIES: dict[str, Category] = {
    c.key: c
    for c in [
        Category("high_protein", "High protein", "highprotein", total_cap=45, emoji=_PAN),
        Category("chinese_daily", "Chinese daily", "chinese", total_cap=45, emoji=_PAN),
        Category("western_daily", "Western daily", "western", total_cap=45, emoji=_PAN),
        Category("asian_daily", "Asian daily", "asian", total_cap=45, emoji=_PAN),
        Category("local_sg", "Singapore favourites", "localsg", total_cap=45, emoji=_PAN),
        Category("soups", "Soups", "soups", total_cap=90, hands_on_cap=15, emoji=_POT),
        Category("rice_cooker", "Rice cooker meals", "ricecooker", total_cap=60, emoji=_POT),
        Category("noodles", "Noodles", "noodles", total_cap=30, emoji=_NOODLE),
        Category("seafood", "Seafood", "seafood", total_cap=30, emoji=_PAN),
        Category("eggs_tofu_veg", "Eggs, tofu and vegetables", "meatfree", total_cap=30, emoji=_SALAD),
        Category("sides", "Vegetable sides", "sides", total_cap=15, emoji=_SALAD),
        Category("quick_20", "Under 20 minutes", "quick", total_cap=20, emoji=_PAN),
        Category("meal_prep", "Meal prep and lunchbox", "mealprep", total_cap=60, emoji=_PAN),
        Category("breakfast", "Breakfast and brunch", "breakfast", total_cap=20, emoji=_PAN),
        Category("use_it_up", "Use it up", "useitup", total_cap=30, emoji=_PAN),
        Category("desserts_no_oven", "No oven desserts", "desserts", hands_on_cap=20, emoji=_CAKE),
        Category("baking_cakes", "Baking and cakes", "baking", hands_on_cap=30, emoji=_CAKE),
        Category("sauces_basics", "Sauces and basics", "basics", total_cap=30, emoji=_PAN),
        Category("custom", "Custom", "custom", total_cap=45, emoji=_PAN),
    ]
}

DAILY_CORE = ["high_protein", "chinese_daily", "western_daily", "asian_daily", "local_sg"]


def get_category(key: str) -> Category:
    try:
        return CATEGORIES[key]
    except KeyError:
        raise KeyError(f"unknown category '{key}'. Known keys: {', '.join(CATEGORIES)}") from None


def is_known(key: str) -> bool:
    return key in CATEGORIES

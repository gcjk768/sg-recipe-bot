"""The posting rotation: which category and theme a given date gets.

Week A and Week B come from the prompt pack. data/rotation.json can replace the weeks and add
single day overrides, so occasional categories (breakfast, sides, sauces_basics, use_it_up,
custom) can be slotted in without touching code:

{
  "weeks": [
    [{"category": "high_protein"}, {"category": "chinese_daily"}, ...7 entries, Monday first...],
    [{"category": "quick_20"}, ...]
  ],
  "overrides": {
    "2026-10-03": {"category": "use_it_up", "theme": "half a cabbage"}
  }
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from recipebot.categories import is_known

WEEK_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass(frozen=True)
class Slot:
    category: str
    theme: str | None = None

    @property
    def theme_text(self) -> str:
        return self.theme if self.theme else "none"


@dataclass(frozen=True)
class ScheduledSlot:
    day: date
    week: str
    slot: Slot
    override: bool = False


DEFAULT_WEEKS: list[list[Slot]] = [
    [  # Week A, Monday to Sunday
        Slot("high_protein"),
        Slot("chinese_daily"),
        Slot("western_daily"),
        Slot("asian_daily"),
        Slot("baking_cakes", "weekend bake"),
        Slot("meal_prep", "lunchbox for the week"),
        Slot("soups"),
    ],
    [  # Week B
        Slot("quick_20"),
        Slot("local_sg"),
        Slot("rice_cooker"),
        Slot("noodles"),
        Slot("desserts_no_oven"),
        Slot("seafood"),
        Slot("eggs_tofu_veg"),
    ],
]


PER_MEAL = 17  # 3 meals x 17 = 51 posts a day, spread over 08:00-22:00 SGT
MAIN_CATEGORIES = ("high_protein",)  # the owner, 2026-10-04: the bot only posts high protein meal prep
PROTEINS = (
    "chicken breast", "eggs", "firm tofu", "lean beef mince", "canned tuna", "prawns", "lentils",
    "Greek yogurt", "chickpeas", "fish fillet", "minced pork", "edamame", "cottage cheese", "tau kwa",
    "sardines", "chicken thigh", "salmon",
)  # PER_MEAL of them, so no two posts of a meal share a main protein


def daily_plan(day: date) -> list[tuple[str, Slot]]:
    """The scheduled day's posts as (meal, slot): PER_MEAL each of breakfast, lunch and dinner, all
    high protein meal prep, each built around a different main protein (shifted by one each day)."""
    shift = day.toordinal() % len(PROTEINS)
    plan = []
    for meal in ("breakfast", "lunch", "dinner"):
        for i in range(PER_MEAL):
            protein = PROTEINS[(shift + i) % len(PROTEINS)]
            plan.append((meal, Slot("high_protein", f"{meal} meal prep for workouts, built around {protein}")))
    return plan


class RotationError(ValueError):
    pass


@dataclass
class Rotation:
    weeks: list[list[Slot]]
    overrides: dict[date, Slot]
    epoch: date

    @classmethod
    def default(cls, epoch: date) -> "Rotation":
        return cls(weeks=[list(w) for w in DEFAULT_WEEKS], overrides={}, epoch=epoch)

    @classmethod
    def load(cls, path: Path | None, epoch: date) -> "Rotation":
        rotation = cls.default(epoch)
        if path is None or not Path(path).is_file():
            return rotation
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RotationError(f"{path}: invalid JSON: {exc}") from exc
        if "weeks" in data:
            weeks = []
            for i, week in enumerate(data["weeks"]):
                if len(week) != 7:
                    raise RotationError(f"{path}: week {i + 1} must have 7 entries (Monday first), got {len(week)}")
                weeks.append([_slot_from(entry, f"{path}: week {i + 1}") for entry in week])
            if not weeks:
                raise RotationError(f"{path}: weeks must not be empty")
            rotation.weeks = weeks
        if "epoch" in data:
            rotation.epoch = date.fromisoformat(data["epoch"])
        for key, entry in (data.get("overrides") or {}).items():
            rotation.overrides[date.fromisoformat(key)] = _slot_from(entry, f"{path}: override {key}")
        return rotation

    def for_date(self, day: date) -> ScheduledSlot:
        if day in self.overrides:
            return ScheduledSlot(day=day, week="override", slot=self.overrides[day], override=True)
        # Align the epoch to the Monday of its week so any date works as an epoch.
        epoch_monday = self.epoch - timedelta(days=self.epoch.weekday())
        delta_days = (day - epoch_monday).days
        week_index = (delta_days // 7) % len(self.weeks)
        weekday = day.weekday()
        return ScheduledSlot(day=day, week=WEEK_LABELS[week_index % len(WEEK_LABELS)], slot=self.weeks[week_index][weekday])

    def upcoming(self, start: date, days: int = 14) -> list[ScheduledSlot]:
        return [self.for_date(start + timedelta(days=i)) for i in range(days)]


def _slot_from(entry: object, where: str) -> Slot:
    if isinstance(entry, str):
        category, theme = entry, None
    elif isinstance(entry, dict):
        category = entry.get("category")
        theme = entry.get("theme") or None
    else:
        raise RotationError(f"{where}: entries must be strings or objects with a category")
    if not isinstance(category, str) or not is_known(category):
        raise RotationError(f"{where}: unknown category {category!r}")
    return Slot(category, theme)

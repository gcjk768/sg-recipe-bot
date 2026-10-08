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
import random
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


DEFAULT_WEEKS: list[list[Slot]] = [[Slot("high_protein")] * 7]  # one week, Monday to Sunday


PER_MEAL = 1  # 3 meals x 1 = 3 posts a day (the owner, 2026-10-05: keep the API cost under $1 a day)
MAIN_CATEGORIES = ("high_protein",)  # the owner, 2026-10-04: the bot only posts high protein meal prep
PROTEINS = (
    "chicken breast", "eggs", "firm tofu", "lean beef mince", "canned tuna", "prawns", "lentils",
    "Greek yogurt", "chickpeas", "fish fillet", "minced pork", "edamame", "cottage cheese", "tau kwa",
    "sardines", "chicken thigh", "salmon",
)  # PER_MEAL of them, so no two posts of a meal share a main protein


STYLES = (
    "Cantonese", "Hokkien", "Teochew", "Hakka", "Hainanese", "Sichuan", "Shanghainese", "Taiwanese",
    "Hong Kong", "Peranakan", "Malay", "Japanese", "Korean", "Thai", "Vietnamese", "Western",
    "Italian", "French", "Mexican", "Middle Eastern", "Mediterranean",
)  # the owner, 2026-10-08: more cuisines, Chinese broken down by dialect


BAKES_PER_DAY = (1, 5)  # the owner, 2026-10-08: 1 to 5 cakes and bakes a day for the family website
BAKES = (
    ("Western", "banana bread"), ("Eastern", "pandan chiffon cake"), ("Western", "brownies"),
    ("Eastern", "kuih bahulu"), ("Western", "butter cake"), ("Eastern", "huat kueh or ma lai gao (steamed cake)"),
    ("Western", "muffins"), ("Eastern", "castella"), ("Western", "scones"), ("Eastern", "egg tarts"),
    ("Western", "cookies"), ("Eastern", "Japanese cotton cheesecake"), ("Western", "carrot cake"),
    ("Eastern", "dorayaki"), ("Western", "pound cake"), ("Eastern", "kaya or coconut bake"),
    ("Western", "apple crumble"), ("Eastern", "Hokkaido chiffon cupcakes"),
)


def bake_slots(day: date) -> list[Slot]:
    """1 to 5 cakes and bakes, how many seeded by the date so a restart plans the same day the
    same way; themes walk through BAKES, alternating Western and Eastern."""
    n = random.Random(day.toordinal()).randint(*BAKES_PER_DAY)
    start = day.toordinal() * BAKES_PER_DAY[1]
    return [
        Slot("baking_cakes", f"{side} bake, for example {example}")
        for side, example in (BAKES[(start + i) % len(BAKES)] for i in range(n))
    ]


def daily_plan(day: date) -> list[tuple[str | None, Slot]]:
    """The scheduled day's posts as (meal, slot): PER_MEAL each of breakfast, lunch and dinner, all
    high protein meal prep, each built around a different main protein (shifted by one each day),
    then the day's bakes, which carry no forced meal tag."""
    shift = day.toordinal() % len(PROTEINS)
    plan: list[tuple[str | None, Slot]] = []
    for meal in ("breakfast", "lunch", "dinner"):
        for i in range(PER_MEAL):
            protein = PROTEINS[(shift + i) % len(PROTEINS)]
            style = STYLES[(day.toordinal() * 3 + len(plan)) % len(STYLES)]
            plan.append((meal, Slot("high_protein", f"{meal} meal prep for workouts, built around {protein}, {style} style")))
    plan += [(None, slot) for slot in bake_slots(day)]
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

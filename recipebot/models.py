"""Pydantic models for the JSON contract in section 7 of the system prompt."""

from __future__ import annotations

import math
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_THOUSANDS = re.compile(r"^-?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
_DECIMAL_COMMA = re.compile(r"^-?\d+,\d{1,2}$")


def _none_to_blank(value: Any) -> Any:
    return "" if value is None else value


def _finite(number: float) -> float | None:
    return number if math.isfinite(number) else None


def _number(value: Any) -> float | None:
    """A finite float from a number or numeric string, else None. Booleans, infinities, NaN and
    oversized values are not numbers here. "4,75" is read as a decimal comma, "1,200" as thousands."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return _finite(float(value))
        except OverflowError:
            return None
    if isinstance(value, str):
        text = value.strip()
        text = re.sub(r"^(?:S\$|SGD|\$)\s*", "", text, flags=re.IGNORECASE).strip()
        if "," in text:
            if _THOUSANDS.match(text):
                text = text.replace(",", "")
            elif _DECIMAL_COMMA.match(text):
                text = text.replace(",", ".")
            else:
                return None
        try:
            return _finite(float(text))
        except (ValueError, OverflowError):
            return None
    return None


def round_money(amount: float) -> float:
    """Singapore style rounding to the nearest 5 cents: 3.1667 -> 3.15, 2.375 -> 2.40."""
    return round(round(amount / 0.05) * 0.05, 2)


MEALS = ("breakfast", "lunch", "dinner", "supper")


class Ingredient(BaseModel):
    model_config = ConfigDict(extra="ignore")

    item: str
    qty: float
    unit: str = ""
    note: str = ""

    @field_validator("note", "unit", mode="before")
    @classmethod
    def _none_text(cls, value: Any) -> Any:
        return _none_to_blank(value)


class Source(BaseModel):
    model_config = ConfigDict(extra="ignore")

    site: str = ""
    author: str | None = None
    url: str

    @field_validator("site", mode="before")
    @classmethod
    def _none_site(cls, value: Any) -> Any:
        return _none_to_blank(value)


class Nutrition(BaseModel):
    """Per serving estimates from the ingredient quantities. Every member is optional and a
    member the model gets wrong (a string, a fraction, null) is dropped on its own."""

    model_config = ConfigDict(extra="ignore")

    kcal: int | None = None
    protein_g: int | None = None
    carbs_g: int | None = None
    fat_g: int | None = None

    @field_validator("kcal", "protein_g", "carbs_g", "fat_g", mode="before")
    @classmethod
    def _lenient_int(cls, value: Any) -> Any:
        number = _number(value)
        return None if number is None else int(round(number))

    @property
    def plausible(self) -> bool:
        """A single home served portion: 20 to 2500 kcal, each macro 0 to 250 g, and when all three
        macros are given, their energy (4 kcal/g protein and carbs, 9 kcal/g fat) within about half of
        the stated calories, with room for the 5 g and 10 kcal rounding the prompt asks for."""
        if self.kcal is None or not 20 <= self.kcal <= 2500:
            return False
        macros = (self.protein_g, self.carbs_g, self.fat_g)
        for value in macros:
            if value is not None and not 0 <= value <= 250:
                return False
        if all(value is not None for value in macros):
            implied = 4 * self.protein_g + 4 * self.carbs_g + 9 * self.fat_g
            if abs(implied - self.kcal) > max(100, 0.5 * self.kcal):
                return False
        return True


class CostEstimate(BaseModel):
    """Estimated ingredient cost in Singapore dollars for the quantities the recipe uses."""

    model_config = ConfigDict(extra="ignore")

    total_sgd: float | None = None
    per_serving_sgd: float | None = None
    note: str = ""

    @field_validator("total_sgd", "per_serving_sgd", mode="before")
    @classmethod
    def _lenient_float(cls, value: Any) -> Any:
        return _number(value)

    @field_validator("note", mode="before")
    @classmethod
    def _lenient_note(cls, value: Any) -> Any:
        return value if isinstance(value, str) else ""

    @property
    def plausible(self) -> bool:
        if self.total_sgd is None or not 0 < self.total_sgd <= 500:
            return False
        if self.per_serving_sgd is not None and not 0 < self.per_serving_sgd <= 500:
            return False
        return True


class Recipe(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    title_zh: str | None = None
    cuisine: str = ""
    category: str
    why_it_fits: str = ""
    servings: int
    prep_minutes: int
    cook_minutes: int
    total_minutes: int
    difficulty: str
    equipment: list[str] = Field(default_factory=list)
    ingredients: list[Ingredient]
    pantry_staples: list[str] = Field(default_factory=list)
    steps: list[str]
    protein_per_serving_g: int | None = None
    nutrition_per_serving: Nutrition | None = None
    cost_estimate: CostEstimate | None = None
    tips: list[str] = Field(default_factory=list)
    storage: str | None = None
    source: Source
    tags: list[str] = Field(default_factory=list)
    meals: list[str] = Field(default_factory=list)

    @field_validator("meals", mode="before")
    @classmethod
    def _known_meals(cls, value: Any) -> list[str]:
        """Up to two of MEALS, lowercased; anything else is dropped rather than failing the recipe."""
        meals: list[str] = []
        for meal in value if isinstance(value, list) else []:
            meal = str(meal).strip().lower()
            if meal in MEALS and meal not in meals:
                meals.append(meal)
        return meals[:2]

    @field_validator("equipment", "pantry_staples", "tips", "tags", mode="before")
    @classmethod
    def _none_list(cls, value: Any) -> Any:
        return [] if value is None else value

    @field_validator("title_zh", "storage", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @field_validator("cuisine", "why_it_fits", mode="before")
    @classmethod
    def _none_text(cls, value: Any) -> Any:
        return _none_to_blank(value)

    @field_validator("nutrition_per_serving", "cost_estimate", mode="before")
    @classmethod
    def _bad_object_to_none(cls, value: Any) -> Any:
        """An estimate that is not an object is dropped rather than failing the recipe."""
        return value if isinstance(value, dict) or value is None else None

    @property
    def nutrition(self) -> Nutrition | None:
        n = self.nutrition_per_serving
        return n if n is not None and n.plausible else None

    @property
    def cost(self) -> CostEstimate | None:
        """The cost estimate with a per serving figure that agrees with the total, or None.
        The total is the headline number; a missing or contradictory per serving figure is
        recomputed from it."""
        c = self.cost_estimate
        if c is None or not c.plausible or c.total_sgd is None:
            return None
        servings = self.servings if self.servings > 0 else 1
        derived = c.total_sgd / servings
        per = c.per_serving_sgd
        # Keep the model's figure only when the difference is what rounding to 5 cents can explain.
        tolerance = max(0.05 * servings + 0.05, 0.05 * c.total_sgd)
        if per is None or per > c.total_sgd * 1.01 or abs(per * servings - c.total_sgd) > tolerance:
            return c.model_copy(update={"per_serving_sgd": round_money(derived)})
        return c


class RunInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: str = ""
    count_requested: int = 0
    count_returned: int = 0
    notes: str = ""

    @field_validator("notes", mode="before")
    @classmethod
    def _none_notes(cls, value: Any) -> Any:
        return "" if value is None else value


class ModelReply(BaseModel):
    """The top level object. Recipes stay raw here so each one can be validated on its own."""

    model_config = ConfigDict(extra="forbid")

    run: RunInfo
    recipes: list[Any]
    error: str | None = None

    @property
    def is_error(self) -> bool:
        return self.error is not None

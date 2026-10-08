import json
from datetime import date, timedelta

import pytest

from recipebot.rotation import DEFAULT_WEEKS, Rotation, RotationError


def test_default_rotation_is_all_high_protein():
    rotation = Rotation.default(date(2026, 9, 28))  # a Monday
    assert len(DEFAULT_WEEKS) == 1 and len(DEFAULT_WEEKS[0]) == 7
    for i in range(21):
        item = rotation.for_date(date(2026, 9, 28) + timedelta(days=i))
        assert item.slot.category == "high_protein" and item.week == "A"


def test_epoch_is_aligned_to_its_monday_and_dates_before_epoch_work():
    rotation = Rotation.default(date(2026, 9, 30))  # a Wednesday
    assert rotation.for_date(date(2026, 9, 28)).slot.category == "high_protein"
    assert rotation.for_date(date(2026, 9, 14)).slot.category == "high_protein"


def test_upcoming_lists_consecutive_days():
    items = Rotation.default(date(2026, 9, 28)).upcoming(date(2026, 9, 28), 3)
    assert [i.slot.category for i in items] == ["high_protein"] * 3


def test_load_json_with_weeks_overrides_and_epoch(tmp_path):
    path = tmp_path / "rotation.json"
    path.write_text(json.dumps({
        "epoch": "2026-10-05",
        "weeks": [["high_protein"] * 3 + [{"category": "high_protein", "theme": "leftover rice"}] + ["high_protein"] * 3],
        "overrides": {"2026-10-08": {"category": "high_protein", "theme": "prawns"}},
    }))
    rotation = Rotation.load(path, date(2026, 9, 28))
    assert rotation.epoch == date(2026, 10, 5)
    assert rotation.for_date(date(2026, 10, 8)).slot.theme == "prawns"
    assert rotation.for_date(date(2026, 10, 8)).override is True
    assert rotation.for_date(date(2026, 10, 15)).slot.theme == "leftover rice"
    assert rotation.for_date(date(2026, 10, 12)).week == "A"


def test_missing_file_gives_defaults(tmp_path):
    rotation = Rotation.load(tmp_path / "nope.json", date(2026, 9, 28))
    assert rotation.weeks == DEFAULT_WEEKS


@pytest.mark.parametrize(
    "payload,message",
    [
        ({"weeks": [["breakfast"] * 6]}, "7 entries"),
        ({"weeks": [["pizza"] * 7]}, "unknown category"),
        ({"weeks": []}, "must not be empty"),
        ({"overrides": {"2026-10-01": 5}}, "entries must be"),
    ],
)
def test_bad_rotation_files(tmp_path, payload, message):
    path = tmp_path / "rotation.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(RotationError, match=message):
        Rotation.load(path, date(2026, 9, 28))


def test_invalid_json(tmp_path):
    path = tmp_path / "rotation.json"
    path.write_text("{not json")
    with pytest.raises(RotationError, match="invalid JSON"):
        Rotation.load(path, date(2026, 9, 28))


def test_one_bake_a_day_alternating_western_and_eastern():
    from recipebot.rotation import bake_slots, daily_plan
    sides = []
    for i in range(6):
        day = date(2026, 10, 1) + timedelta(days=i)
        bakes = bake_slots(day)
        assert len(bakes) == 1 and bakes[0].category == "baking_cakes" and bake_slots(day) == bakes
        assert daily_plan(day)[-1] == (None, bakes[0])  # the bake comes after the meal
        sides.append(bakes[0].theme.split()[0])
    assert set(sides) == {"Western", "Eastern"}

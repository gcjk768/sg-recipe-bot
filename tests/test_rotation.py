import json
from datetime import date

import pytest

from recipebot.rotation import DEFAULT_WEEKS, Rotation, RotationError


def test_default_two_week_rotation():
    rotation = Rotation.default(date(2026, 9, 28))  # a Monday
    assert rotation.for_date(date(2026, 9, 28)).slot.category == "high_protein"
    assert rotation.for_date(date(2026, 10, 2)).slot.theme == "weekend bake"
    assert rotation.for_date(date(2026, 10, 3)).slot == DEFAULT_WEEKS[0][5]
    assert rotation.for_date(date(2026, 10, 4)).slot.category == "soups"
    assert rotation.for_date(date(2026, 10, 5)).slot.category == "quick_20"
    assert rotation.for_date(date(2026, 10, 5)).week == "B"
    assert rotation.for_date(date(2026, 10, 11)).slot.category == "eggs_tofu_veg"
    assert rotation.for_date(date(2026, 10, 12)).slot.category == "high_protein"


def test_epoch_is_aligned_to_its_monday_and_dates_before_epoch_work():
    rotation = Rotation.default(date(2026, 9, 30))  # a Wednesday
    assert rotation.for_date(date(2026, 9, 28)).slot.category == "high_protein"
    assert rotation.for_date(date(2026, 9, 21)).slot.category == "quick_20"  # the week before is week B
    assert rotation.for_date(date(2026, 9, 14)).slot.category == "high_protein"


def test_upcoming_lists_consecutive_days():
    items = Rotation.default(date(2026, 9, 28)).upcoming(date(2026, 9, 28), 3)
    assert [i.slot.category for i in items] == ["high_protein", "chinese_daily", "western_daily"]


def test_load_json_with_weeks_overrides_and_epoch(tmp_path):
    path = tmp_path / "rotation.json"
    path.write_text(json.dumps({
        "epoch": "2026-10-05",
        "weeks": [["breakfast", "sides", "sauces_basics", {"category": "use_it_up", "theme": "leftover rice"}, "custom", "soups", "noodles"]],
        "overrides": {"2026-10-08": {"category": "seafood", "theme": "prawns"}},
    }))
    rotation = Rotation.load(path, date(2026, 9, 28))
    assert rotation.epoch == date(2026, 10, 5)
    assert rotation.for_date(date(2026, 10, 5)).slot.category == "breakfast"
    assert rotation.for_date(date(2026, 10, 8)).slot.category == "seafood"
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

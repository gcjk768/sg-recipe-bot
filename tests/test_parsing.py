import json

import pytest

from recipebot.parsing import ParseFailure, extract_json_object, parse_reply
from tests.conftest import make_recipe, make_reply


def test_strict_json():
    text = json.dumps(make_reply([make_recipe()]))
    parsed = parse_reply(text)
    assert parsed.warnings == []
    assert parsed.reply.run.category == "high_protein"
    assert len(parsed.reply.recipes) == 1
    assert not parsed.reply.is_error


def test_fenced_json_is_accepted_with_warning():
    text = "```json\n" + json.dumps(make_reply([make_recipe()])) + "\n```"
    parsed = parse_reply(text)
    assert parsed.warnings and "leniently" in parsed.warnings[0]
    assert parsed.reply.run.count_returned == 1


def test_prose_around_json_is_tolerated():
    body = json.dumps(make_reply([make_recipe()]))
    text = "Here is the recipe you asked for.\n" + body + "\nLet me know if you want more."
    obj, lenient = extract_json_object(text)
    assert lenient and obj["run"]["category"] == "high_protein"


def test_last_text_block_is_tried():
    body = json.dumps(make_reply([]))
    parsed = parse_reply("I searched the web. {not json", extra_candidates=[body])
    assert parsed.reply.recipes == []


def test_error_object():
    parsed = parse_reply(json.dumps({"error": "no web access", "run": {"category": "soups", "count_requested": 1, "count_returned": 0, "notes": "offline"}, "recipes": []}))
    assert parsed.reply.is_error and parsed.reply.error == "no web access"
    assert parsed.reply.run.notes == "offline"


@pytest.mark.parametrize(
    "text,fragment",
    [
        ("", "empty"),
        ("   ", "empty"),
        ("not json at all", "JSON parse error"),
        ("[1, 2, 3]", "JSON parse error"),
        (json.dumps({"recipes": []}), "Top level keys"),
        (json.dumps({"run": {}, "recipes": [], "extra": 1}), "Top level keys"),
        (json.dumps({"run": "nope", "recipes": []}), "malformed"),
        (json.dumps({"run": {"category": "x"}, "recipes": {}}), "malformed"),
    ],
)
def test_failures_carry_a_useful_message(text, fragment):
    with pytest.raises(ParseFailure) as exc:
        parse_reply(text)
    assert fragment in str(exc.value)


def test_run_defaults_when_fields_missing():
    parsed = parse_reply(json.dumps({"run": {}, "recipes": []}))
    assert parsed.reply.run.count_requested == 0 and parsed.reply.run.notes == ""

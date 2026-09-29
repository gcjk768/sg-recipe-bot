import json

import pytest

from recipebot.telegram import TelegramClient, TelegramError
from tests.conftest import FakeResponse, FakeSession, telegram_ok_session


def _client(session, sleeps=None):
    sleeps = sleeps if sleeps is not None else []
    return TelegramClient("123:tok", session=session, sleep=sleeps.append)


def test_send_message_payload():
    session = telegram_ok_session()
    client = _client(session)
    assert client.send_message("@chan", "<b>hi</b>") == 1
    post = session.posts[0]
    assert post["url"] == "https://api.telegram.org/bot123:tok/sendMessage"
    assert post["json"] == {"chat_id": "@chan", "text": "<b>hi</b>", "parse_mode": "HTML"}
    client.send_message("@chan", "plain", parse_mode=None, disable_preview=True)
    assert session.posts[1]["json"] == {"chat_id": "@chan", "text": "plain", "link_preview_options": {"is_disabled": True}}


def test_send_messages_pauses_between_parts():
    session = telegram_ok_session()
    sleeps = []
    client = _client(session, sleeps)
    assert client.send_messages("@chan", ["one", "two"], pause=1.0) == [1, 2]
    assert sleeps == [1.0]


def test_rate_limit_is_retried_with_retry_after():
    calls = {"n": 0}

    def respond(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse(429, json_body={"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 3}})
        return FakeResponse(200, json_body={"ok": True, "result": {"message_id": 9}})

    sleeps = []
    client = _client(FakeSession(default=respond), sleeps)
    assert client.send_message("@chan", "x") == 9
    assert sleeps == [3.5]


def test_server_error_retried_then_raises():
    client = _client(FakeSession(default=lambda url: FakeResponse(502, json_body={"ok": False, "error_code": 502, "description": "Bad Gateway"})), [])
    with pytest.raises(TelegramError, match="Bad Gateway"):
        client.send_message("@chan", "x")


def test_bad_request_raises_immediately():
    session = FakeSession(default=lambda url: FakeResponse(400, json_body={"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"}))
    client = _client(session)
    with pytest.raises(TelegramError, match="parse entities"):
        client.send_message("@chan", "<b>oops")
    assert len(session.posts) == 1


def test_network_error_retried_then_raises():
    client = _client(FakeSession(default=ConnectionError("down")), [])
    with pytest.raises(TelegramError, match="network error"):
        client.send_message("@chan", "x")


def test_too_long_message_rejected_locally():
    client = _client(telegram_ok_session())
    with pytest.raises(TelegramError, match="4096"):
        client.send_message("@chan", "x" * 4097)


def test_send_plain_truncates():
    session = telegram_ok_session()
    client = _client(session)
    client.send_plain("777", "y" * 5000)
    text = session.posts[0]["json"]["text"]
    assert len(text) <= 4096 and text.endswith("[truncated]") and "parse_mode" not in session.posts[0]["json"]


def test_get_me():
    assert _client(telegram_ok_session()).get_me()["username"] == "recipebot"


def test_network_error_message_never_contains_the_token():
    err = ConnectionError("HTTPSConnectionPool: Max retries exceeded with url: /bot123:tok/sendMessage")
    client = _client(FakeSession(default=err), [])
    with pytest.raises(TelegramError) as exc:
        client.send_message("@chan", "x")
    assert "123:tok" not in str(exc.value) and "<token>" in str(exc.value)


def test_read_timeout_on_send_message_is_not_retried_and_is_ambiguous():
    session = FakeSession(default=TimeoutError("ReadTimeout: HTTPSConnectionPool read timed out"))
    client = _client(session, [])
    with pytest.raises(TelegramError) as exc:
        client.send_message("@chan", "x")
    assert exc.value.ambiguous and "may or may not" in str(exc.value)
    assert len(session.posts) == 1


def test_connect_failure_on_send_message_is_retried():
    calls = {"n": 0}

    def respond(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("HTTPSConnectionPool: Max retries exceeded (Caused by NewConnectionError: Failed to establish a new connection)")
        return FakeResponse(200, json_body={"ok": True, "result": {"message_id": 5}})

    client = _client(FakeSession(default=respond), [])
    assert client.send_message("@chan", "x") == 5
    assert calls["n"] == 2


def test_get_me_is_retried_after_a_read_timeout():
    calls = {"n": 0}

    def respond(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("ReadTimeout")
        return FakeResponse(200, json_body={"ok": True, "result": {"id": 1, "username": "recipebot"}})

    assert _client(FakeSession(default=respond), []).get_me()["username"] == "recipebot"

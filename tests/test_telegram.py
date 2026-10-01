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
    assert post["json"] == {"chat_id": "@chan", "text": "<b>hi</b>", "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}
    client.send_message("@chan", "plain", parse_mode=None, disable_preview=False)
    assert session.posts[1]["json"] == {"chat_id": "@chan", "text": "plain"}


def test_send_message_to_forum_topic():
    session = telegram_ok_session()
    _client(session).send_message("-1002069000031/2765", "hi", parse_mode=None, disable_preview=False)
    assert session.posts[0]["json"] == {"chat_id": "-1002069000031", "message_thread_id": 2765, "text": "hi"}


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
    session = FakeSession(default=lambda url: FakeResponse(400, json_body={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}))
    client = _client(session)
    with pytest.raises(TelegramError, match="chat not found"):
        client.send_message("@chan", "<b>oops")
    assert len(session.posts) == 1


def test_html_parse_error_is_resent_as_plain_text():
    def respond(url):
        return (FakeResponse(400, json_body={"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities: unclosed tag"})
                if len(session.posts) == 1 else FakeResponse(200, json_body={"ok": True, "result": {"message_id": 7}}))

    session = FakeSession(default=respond)
    client = _client(session)
    assert client.send_message("-100/2765", '🍳 <b>Mac &amp; Cheese</b>\n<a href="https://x.com/?a=1&amp;b=2">Recipe</a> <b>oops') == 7
    plain = session.posts[1]["json"]
    assert "parse_mode" not in plain and plain["message_thread_id"] == 2765  # still the same topic
    assert plain["text"] == "🍳 Mac & Cheese\nRecipe (https://x.com/?a=1&b=2) oops"


def test_parse_error_fallback_only_for_html():
    session = FakeSession(default=lambda url: FakeResponse(400, json_body={"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"}))
    with pytest.raises(TelegramError):
        _client(session).send_message("@chan", "x", parse_mode=None)
    assert len(session.posts) == 1


def test_split_blocks_packs_and_never_cuts_a_block():
    from recipebot.telegram import split_blocks

    a, b, c = "<b>" + "a" * 2500 + "</b>", "<i>" + "b" * 2500 + "</i>", "<blockquote expandable>c</blockquote>"
    assert split_blocks([a, "", c]) == [f"{a}\n\n{c}"]
    messages = split_blocks([a, b, c])
    assert messages == [a, f"{b}\n\n{c}"] and all(len(m) <= 4096 for m in messages)
    with pytest.raises(ValueError):
        split_blocks(["x" * 4097])


def test_send_html_splits_between_blocks():
    session = telegram_ok_session()
    sleeps = []
    ids = _client(session, sleeps).send_html("777", ["<b>" + "a" * 3000 + "</b>", "<i>" + "b" * 3000 + "</i>"])
    assert ids == [1, 2] and sleeps == [1.0]
    assert [p["json"]["text"][:3] for p in session.posts] == ["<b>", "<i>"]
    assert all(p["json"]["parse_mode"] == "HTML" for p in session.posts)


def test_esc_escapes_markup():
    from recipebot.telegram import esc, esc_attr

    assert esc("<b>a & b</b>") == "&lt;b&gt;a &amp; b&lt;/b&gt;"
    assert esc(None) == ""
    assert esc_attr('x"y') == "x&quot;y"


def test_network_error_retried_then_raises():
    client = _client(FakeSession(default=ConnectionError("down")), [])
    with pytest.raises(TelegramError, match="network error"):
        client.send_message("@chan", "x")


def test_too_long_message_rejected_locally():
    client = _client(telegram_ok_session())
    with pytest.raises(TelegramError, match="4096"):
        client.send_message("@chan", "x" * 4097)


def test_plain_fallback_truncates():
    from recipebot.telegram import html_to_plain

    text = html_to_plain("<b>" + "y" * 5000 + "</b>")
    assert len(text) <= 4096 and text.endswith("[truncated]")


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


def test_ssl_error_while_reading_is_ambiguous_but_handshake_failure_is_retried():
    from recipebot.telegram import failed_before_sending

    assert not failed_before_sending(Exception("SSLError: [SSL: DECRYPTION_FAILED_OR_BAD_RECORD_MAC] bad record mac"))
    assert not failed_before_sending(Exception("SSLError: EOF occurred in violation of protocol"))
    assert failed_before_sending(Exception("SSLError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"))
    assert failed_before_sending(Exception("SSLError: HTTPSConnectionPool: Max retries exceeded (Caused by SSLError(SSLError(1, '[SSL: WRONG_VERSION_NUMBER]')))"))
    assert not failed_before_sending(Exception("ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"))
    assert not failed_before_sending(Exception("ConnectionError: ConnectionResetError(104, 'Connection reset by peer')"))
    assert failed_before_sending(Exception("ConnectTimeout: HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded"))


@pytest.mark.parametrize("status", [500, 502, 504])
def test_ambiguous_5xx_on_send_message_is_not_resent(status):
    session = FakeSession(default=lambda url: FakeResponse(status, json_body={"ok": False, "error_code": status, "description": "Gateway trouble"}))
    client = _client(session, [])
    with pytest.raises(TelegramError) as exc:
        client.send_message("@chan", "x")
    assert exc.value.ambiguous and len(session.posts) == 1


def test_503_on_send_message_is_retried():
    calls = {"n": 0}

    def respond(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse(503, json_body={"ok": False, "error_code": 503, "description": "Service Unavailable"})
        return FakeResponse(200, json_body={"ok": True, "result": {"message_id": 3}})

    assert _client(FakeSession(default=respond), []).send_message("@chan", "x") == 3


def test_ambiguous_error_does_not_chain_the_raw_exception():
    err = TimeoutError("ReadTimeout: HTTPSConnectionPool(host='api.telegram.org'): url: /bot123:tok/sendMessage")
    client = _client(FakeSession(default=err), [])
    with pytest.raises(TelegramError) as exc:
        client.send_message("@chan", "x")
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
    assert "123:tok" not in str(exc.value)


def test_get_me_retries_any_5xx():
    calls = {"n": 0}

    def respond(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse(500, json_body={"ok": False, "error_code": 500, "description": "Internal"})
        return FakeResponse(200, json_body={"ok": True, "result": {"id": 1, "username": "recipebot"}})

    assert _client(FakeSession(default=respond), []).get_me()["username"] == "recipebot"

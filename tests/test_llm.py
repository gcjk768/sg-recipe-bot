from types import SimpleNamespace

import pytest

import json

from recipebot.llm import FALLBACK_BETA, AnthropicClient, ClaudeCLIClient, LLMError, LLMRefusal


def _block(**kw):
    return SimpleNamespace(**kw)


def _response(content, stop_reason="end_turn", usage=None, model="claude-opus-5-5", stop_details=None):
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=usage or SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=3), model=model, stop_details=stop_details)


class FakeAnthropic:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def test_request_kwargs_shape(settings):
    client = AnthropicClient(settings, client=FakeAnthropic([]))
    kwargs = client.request_kwargs("SYS", [{"role": "user", "content": "brief"}], web_search=True)
    assert kwargs["model"] == "claude-opus-5-5" and kwargs["max_tokens"] == 32000
    assert kwargs["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    assert kwargs["output_config"] == {"effort": "high"}
    assert kwargs["betas"] == [FALLBACK_BETA] and kwargs["fallbacks"] == "default"
    [tool] = kwargs["tools"]
    assert tool["type"] == "web_search_20260209" and tool["name"] == "web_search" and tool["max_uses"] == 10
    assert tool["user_location"]["country"] == "SG" and "allowed_domains" not in tool
    assert "thinking" not in kwargs


def test_request_kwargs_options(settings):
    settings.llm_effort = None
    settings.llm_fallbacks = "off"
    settings.llm_allowed_domains = ["recipetineats.com", "thewoksoflife.com"]
    client = AnthropicClient(settings, client=FakeAnthropic([]))
    kwargs = client.request_kwargs("SYS", [], web_search=False)
    assert "tools" not in kwargs and "output_config" not in kwargs and "betas" not in kwargs and "fallbacks" not in kwargs
    with_search = client.request_kwargs("SYS", [], web_search=True)
    assert with_search["tools"][0]["allowed_domains"] == ["recipetineats.com", "thewoksoflife.com"]


def test_complete_joins_text_blocks_and_counts_searches(settings):
    fake = FakeAnthropic([_response([
        _block(type="text", text="I will search."),
        _block(type="server_tool_use", name="web_search", id="1", input={}),
        _block(type="web_search_tool_result", content=[]),
        _block(type="text", text='{"run": {}, "recipes": []}'),
    ])])
    result = AnthropicClient(settings, client=fake).complete("SYS", "brief", web_search=True)
    assert result.text == 'I will search.{"run": {}, "recipes": []}'
    assert result.text_blocks[-1] == '{"run": {}, "recipes": []}'
    assert result.web_searches == 1 and result.input_tokens == 10 and result.cache_read_tokens == 3
    assert result.stop_reason == "end_turn" and not result.truncated
    assert fake.calls[0]["messages"] == [{"role": "user", "content": "brief"}]


def test_pause_turn_is_continued(settings):
    paused = _response([_block(type="server_tool_use", name="web_search", id="1", input={})], stop_reason="pause_turn")
    final = _response([_block(type="text", text="{}")])
    fake = FakeAnthropic([paused, final])
    result = AnthropicClient(settings, client=fake).complete("SYS", "brief", web_search=True)
    assert result.text == "{}" and len(fake.calls) == 2
    assert fake.calls[1]["messages"][1] == {"role": "assistant", "content": paused.content}
    assert result.input_tokens == 20 and result.web_searches == 0


def test_refusal_raises(settings):
    fake = FakeAnthropic([_response([], stop_reason="refusal", stop_details=SimpleNamespace(category="cyber", explanation="nope"))])
    with pytest.raises(LLMRefusal, match="cyber"):
        AnthropicClient(settings, client=fake).complete("SYS", "brief", web_search=False)


def test_truncated_flag(settings):
    fake = FakeAnthropic([_response([_block(type="text", text='{"run"')], stop_reason="max_tokens")])
    assert AnthropicClient(settings, client=fake).complete("SYS", "b", web_search=False).truncated


def _cli(settings, stdout, returncode=0):
    calls = []

    def run(cmd, **kw):
        if "--system-prompt-file" in cmd:
            kw["system"] = open(cmd[cmd.index("--system-prompt-file") + 1], encoding="utf-8").read()
        calls.append((cmd, kw))
        return SimpleNamespace(stdout=stdout, stderr="boom", returncode=returncode)

    return ClaudeCLIClient(settings, run=run), calls


def test_cli_command_and_result(settings):
    blob = {"is_error": False, "result": '{"recipes": []}', "stop_reason": "end_turn", "modelUsage": {"claude-opus-5-5": {}}, "num_turns": 4,
            "usage": {"input_tokens": 9, "output_tokens": 7, "cache_read_input_tokens": 4, "server_tool_use": {"web_search_requests": 3}}}
    client, calls = _cli(settings, json.dumps(blob))
    result = client.complete("SYS", "brief", web_search=True)
    cmd, kw = calls[0]
    assert cmd[1:3] == ["-p", "--output-format"] and kw["system"] == "SYS"
    assert cmd[cmd.index("--tools") + 1] == "WebSearch,WebFetch" and cmd[cmd.index("--allowedTools") + 1] == "WebSearch,WebFetch"
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5" and cmd[cmd.index("--effort") + 1] == "high"
    assert kw["input"] == "brief"
    assert (result.text, result.web_searches, result.output_tokens, result.truncated) == ('{"recipes": []}', 3, 7, False)
    no_search, _ = _cli(settings, "")
    cmd = no_search.command("sys.txt", web_search=False)
    assert cmd[cmd.index("--tools") + 1] == "" and "--allowedTools" not in cmd


def test_cli_errors(settings):
    with pytest.raises(LLMError, match="Not logged in"):
        _cli(settings, json.dumps({"is_error": True, "subtype": "success", "result": "Not logged in"}), 1)[0].complete("S", "b", web_search=False)
    with pytest.raises(LLMError, match="boom"):
        _cli(settings, "", 1)[0].complete("S", "b", web_search=False)
    with pytest.raises(LLMRefusal):
        _cli(settings, json.dumps({"is_error": False, "stop_reason": "refusal", "result": "no"}))[0].complete("S", "b", web_search=False)

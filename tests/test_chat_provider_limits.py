"""A chat answer that waits on a provider says so, gives up in time, and retries once where it can be seen."""

from unittest.mock import Mock

import httpx
import openai
import pytest

from api import chat
from services.ai_service import AnthropicProvider, OllamaProvider, OpenAIProvider
from tests.test_chat_followups import _client, _events, _wire

QUESTION = {"messages": [{"role": "user", "content": "How long?"}], "provider": "ollama",
            "use_cache": False, "depth": "quick", "related": False}
ANSWER = {"text": "Five days.", "tool_calls": [], "usage": {}}
REQUEST = httpx.Request("POST", "https://api.example.com/v1/chat/completions")


def timeout():
    return openai.APITimeoutError(request=REQUEST)


def status_error(cls, code, headers=None):
    return cls(f"HTTP {code}", response=httpx.Response(code, headers=headers or {}, request=REQUEST), body=None)


def native(side_effect):
    provider = Mock()
    provider.supports_native_tools.return_value = True
    provider.complete_with_tools.side_effect = side_effect
    return provider


def stream(monkeypatch, provider):
    _wire(monkeypatch, provider)
    with _client() as client:
        response = client.post("/api/chat/stream", json=QUESTION)
    assert response.status_code == 200, response.text
    return _events(response)


def statuses(events):
    return [(e["stage"], e["text"]) for e in events if e["type"] == "status"]


# ── Providers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("make", [lambda: OpenAIProvider("k"), lambda: AnthropicProvider("k")])
def test_hosted_providers_give_up_in_time_and_leave_retries_to_the_turn(make):
    provider = make()
    provider.limit_calls(180)
    assert provider.call_timeout == 180
    assert provider.client.max_retries == 0
    assert provider.client.timeout == 180


def test_local_ollama_keeps_its_own_limit():
    provider = OllamaProvider()
    provider.limit_calls(30)
    assert provider.call_timeout == 300


def test_the_turn_applies_the_configured_limit(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "chat_model_timeout_seconds", 95)
    provider = native([ANSWER])
    stream(monkeypatch, provider)
    provider.limit_calls.assert_called_once_with(95)


# ── Status events ─────────────────────────────────────────────────────────


def test_the_stream_says_what_it_is_doing_before_the_first_word(monkeypatch):
    events = stream(monkeypatch, native([ANSWER]))
    assert events[0] == {"type": "status", "stage": "search", "text": "Searching Operations…"}
    assert statuses(events)[1] == ("model", "Waiting for The AI provider (fake)…")
    assert [e["type"] for e in events][-1] == "done"


def test_a_follow_up_shows_the_rewording_call_first(monkeypatch):
    provider = native([ANSWER])
    _wire(monkeypatch, provider)
    reword = Mock(return_value="How long is the notice period?")
    monkeypatch.setattr(chat, "AIService", lambda **k: Mock(quality_model="fake", fast_model="fast",
                                                            reformulate_query=reword))
    with _client() as client:
        response = client.post("/api/chat/stream", json={**QUESTION, "messages": [
            {"role": "user", "content": "Notice period?"}, {"role": "assistant", "content": "Ten days."},
            {"role": "user", "content": "How long?"}]})
    stages = statuses(_events(response))
    assert stages[:3] == [("model", "Waiting for The AI provider (fast)…"),
                          ("search", "Searching Operations…"),
                          ("model", "Waiting for The AI provider (fake)…")]
    reword.assert_called_once()


# ── One visible retry ─────────────────────────────────────────────────────


def test_a_timeout_is_retried_once_where_the_person_can_see_it(monkeypatch):
    provider = native([timeout(), ANSWER])
    provider.call_timeout = 180
    events = stream(monkeypatch, provider)
    assert statuses(events)[-1] == ("retry", "The AI provider didn't reply within 180 s. Trying once more…")
    assert "".join(e.get("delta", "") for e in events) == "Five days."
    assert events[-1]["type"] == "done"
    assert provider.complete_with_tools.call_count == 2


def test_a_second_failure_ends_the_turn_with_a_reason(monkeypatch):
    provider = native([timeout(), timeout()])
    provider.call_timeout = 180
    events = stream(monkeypatch, provider)
    assert events[-1]["type"] == "error"
    assert events[-1]["message"] == (
        "The AI provider didn't reply within 180 s. It failed again after one retry. "
        "Try again in a moment, or choose another model."
    )
    assert not any(e["type"] == "done" for e in events)


def test_a_short_rate_limit_pause_is_honoured(monkeypatch):
    slept = []
    monkeypatch.setattr(chat.time, "sleep", slept.append)
    provider = native([status_error(openai.RateLimitError, 429, {"retry-after": "4"}), ANSWER])
    events = stream(monkeypatch, provider)
    assert statuses(events)[-2:] == [
        ("retry", "The AI provider is rate-limiting this key (HTTP 429). Trying again in 4 s…"),
        ("retry", "The AI provider is rate-limiting this key (HTTP 429). Trying once more…"),
    ]
    assert slept == [4.0]
    assert events[-1]["type"] == "done"


def test_a_long_rate_limit_pause_is_reported_not_waited_out(monkeypatch):
    slept = []
    monkeypatch.setattr(chat.time, "sleep", slept.append)
    provider = native([status_error(openai.RateLimitError, 429, {"retry-after": "120"})])
    events = stream(monkeypatch, provider)
    assert events[-1]["type"] == "error"
    assert events[-1]["message"].startswith("The AI provider is rate-limiting this key (HTTP 429) and asks to wait 120 s.")
    assert slept == []
    assert provider.complete_with_tools.call_count == 1


def test_an_overloaded_provider_gets_one_more_try(monkeypatch):
    monkeypatch.setattr(chat.time, "sleep", lambda s: None)
    provider = native([status_error(openai.InternalServerError, 503), ANSWER])
    events = stream(monkeypatch, provider)
    assert ("retry", "The AI provider is overloaded or having trouble (HTTP 503). Trying once more…") in statuses(events)
    assert events[-1]["type"] == "done"


def test_a_bad_request_is_not_retried_and_keeps_its_message(monkeypatch):
    provider = native([status_error(openai.BadRequestError, 400)])
    events = stream(monkeypatch, provider)
    assert provider.complete_with_tools.call_count == 1
    assert events[-1]["type"] == "error"
    assert events[-1]["message"].startswith("Chat failed:")
    assert not [s for s in statuses(events) if s[0] == "retry"]


def test_a_slow_local_model_is_not_asked_twice(monkeypatch):
    provider = OllamaProvider()
    provider.supports_native_tools = lambda: False
    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        raise httpx.ReadTimeout("slow", request=REQUEST)

    provider.complete = complete
    events = stream(monkeypatch, provider)
    assert len(calls) == 1
    assert events[-1]["message"] == (
        "Ollama didn't reply within 300 s. Try again in a moment, or choose another model."
    )


def test_the_non_streaming_route_answers_504_with_the_reason(monkeypatch):
    provider = native([timeout(), timeout()])
    provider.call_timeout = 180
    _wire(monkeypatch, provider)
    with _client() as client:
        response = client.post("/api/chat", json=QUESTION)
    assert response.status_code == 504
    assert "didn't reply within 180 s" in response.json()["detail"]


# ── Keepalive ─────────────────────────────────────────────────────────────


def test_a_quiet_stream_sends_keepalive_comments(monkeypatch):
    import time as _time

    def slow(**kwargs):
        _time.sleep(0.35)
        return ANSWER

    monkeypatch.setattr(chat, "_KEEPALIVE_SECONDS", 0.1)
    provider = native(slow)
    _wire(monkeypatch, provider)
    with _client() as client:
        response = client.post("/api/chat/stream", json=QUESTION)
    assert ": keepalive" in response.text
    assert _events(response)[-1]["type"] == "done"

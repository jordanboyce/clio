"""C1: the answer streams from the model as it is written, and Stop closes
the model's stream.

The answer used to be one blocking call, replayed word by word with an 8 ms
pause once it was finished: on a local model the reader watched a timer for
the whole generation."""

import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from api import chat
from services.ai_service import OllamaProvider, OpenAIProvider
from tests.test_chat_followups import _client, _events, _wire

QUESTION = {"messages": [{"role": "user", "content": "How long?"}], "provider": "ollama",
            "use_cache": False, "depth": "quick", "related": False}


# ── Providers ─────────────────────────────────────────────────────────────


def test_ollama_streams_its_native_chat(monkeypatch):
    lines = [{"message": {"content": "Seven"}}, {"message": {"content": " years."}},
             {"done": True, "done_reason": "stop", "prompt_eval_count": 120, "eval_count": 4}]
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))

    out = list(OllamaProvider().stream(prompt="How long?", max_tokens=50, model="llama3.2"))

    assert seen["stream"] is True and seen["options"]["num_predict"] == 50
    assert [o["delta"] for o in out if "delta" in o] == ["Seven", " years."]
    assert out[-1]["usage"] == {"input_tokens": 120, "output_tokens": 4, "model": "llama3.2"}


def test_openai_style_providers_stream_with_usage():
    def chunk(text=None, finish=None, usage=None):
        choices = [SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason=finish)] if text or finish else []
        return SimpleNamespace(choices=choices, usage=usage)

    provider = OpenAIProvider("k")
    create = Mock(return_value=iter([chunk("Seven"), chunk(" years.", "stop"),
                                     chunk(usage=SimpleNamespace(prompt_tokens=90, completion_tokens=3))]))
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    out = list(provider.stream(messages=[{"role": "user", "content": "How long?"}], system="Be brief.",
                               max_tokens=50, model="gpt"))

    sent = create.call_args.kwargs
    assert sent["stream"] is True and sent["messages"][0] == {"role": "system", "content": "Be brief."}
    assert "".join(o.get("delta", "") for o in out) == "Seven years."
    assert out[-1]["usage"]["input_tokens"] == 90 and out[-1]["finish_reason"] == "stop"


# ── The turn ──────────────────────────────────────────────────────────────


class _Streaming:
    """A model that writes its answer in pieces, and notes when it is closed."""

    streams = True

    def __init__(self, pieces, on_piece=None):
        self.pieces = pieces
        self.on_piece = on_piece or (lambda i: None)
        self.closed = False
        self.calls = 0

    def supports_native_tools(self):
        return False

    def stream(self, **kwargs):
        self.calls += 1
        try:
            for i, piece in enumerate(self.pieces):
                self.on_piece(i)
                yield {"delta": piece}
            yield {"usage": {"input_tokens": 10, "output_tokens": 3, "model": "m"}}
        finally:
            self.closed = True

    def complete(self, **kwargs):  # only the follow-up questions would ask
        raise AssertionError("no blocking call expected")


def test_the_answer_arrives_in_the_pieces_the_model_writes(monkeypatch):
    provider = _Streaming(["Seven", " years", " [Source 1]."])
    cache_write, usage = _wire(monkeypatch, provider)
    with _client() as client:
        response = client.post("/api/chat/stream", json=QUESTION)

    events = _events(response)
    deltas = [e["delta"] for e in events if e["type"] == "text_delta"]
    assert deltas == ["Seven", " years", " [Source 1]."]
    assert events[-1]["type"] == "done"
    assert cache_write.call_args.args[1] == "Seven years [Source 1]."


def test_a_model_that_cannot_stream_sends_its_answer_at_once(monkeypatch):
    provider = Mock()
    provider.supports_native_tools.return_value = False
    provider.complete.return_value = {"text": "Seven years, as the policy says.", "usage": {}}
    _wire(monkeypatch, provider)
    with _client() as client:
        response = client.post("/api/chat/stream", json=QUESTION)

    deltas = [e["delta"] for e in _events(response) if e["type"] == "text_delta"]
    assert deltas == ["Seven years, as the policy says."]


def test_stop_closes_the_models_stream_and_ends_the_turn(monkeypatch):
    cancel = threading.Event()
    provider = _Streaming(["Seven", " years", " and more"], on_piece=lambda i: cancel.set() if i == 1 else None)
    _wire(monkeypatch, provider)
    cache_ctx, cache_entry = {"scope_key": "test"}, None
    request = chat.ChatRequest(**QUESTION)

    provider_args = chat._provider_args(None, None, None, None, None, None)
    events = list(chat._timed_turn(chat._run_turn(request, "default", "http://x", provider_args, cache_ctx,
                                                  cache_entry, cancel=cancel)))

    assert [e.get("text") for e in events if e["type"] == "text"] == ["Seven"]
    assert not any(e["type"] in ("done", "error") for e in events)
    assert provider.closed


@pytest.mark.parametrize("depth", ["quick"])
def test_a_failure_before_the_first_word_is_retried_once(monkeypatch, depth):
    monkeypatch.setattr(chat.time, "sleep", lambda s: None)
    attempts = []

    class Flaky(_Streaming):
        def stream(self, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise httpx.ConnectError("refused")
            yield from super().stream(**kwargs)

    provider = Flaky(["Seven years."])
    _wire(monkeypatch, provider)
    with _client() as client:
        response = client.post("/api/chat/stream", json={**QUESTION, "depth": depth})

    events = _events(response)
    assert len(attempts) == 2
    assert [e["delta"] for e in events if e["type"] == "text_delta"] == ["Seven years."]

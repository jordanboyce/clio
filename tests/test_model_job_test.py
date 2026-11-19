"""Roadmap P5: "Test connection" runs the job the model was picked for."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import chat as chat_api


class Fake:
    QUALITY_MODEL = "fake-1"

    def __init__(self, tools=True, vision=True, calls_tool=True):
        self._tools, self._vision, self._calls = tools, vision, calls_tool
        self.seen = []

    def complete(self, prompt, max_tokens, model):
        self.seen.append(("complete", model))
        return {"text": "ready"}

    def supports_native_tools(self):
        return self._tools

    def complete_with_tools(self, messages, tools, max_tokens, model, system=None):
        self.seen.append(("tools", tools[0]))
        return {"tool_calls": [{"name": "get_time", "input": {}}] if self._calls else [], "text": "noon"}

    def complete_with_image(self, prompt, image_base64, media_type, max_tokens, model):
        if not self._vision:
            raise NotImplementedError
        self.seen.append(("vision", media_type))
        return {"text": "red"}


@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.include_router(chat_api.router)
    return TestClient(app)


def _use(monkeypatch, fake):
    monkeypatch.setattr(chat_api, "_provider_from_headers", lambda *a, **k: fake)


def test_each_job_is_tried_and_reported(client, monkeypatch):
    fake = Fake()
    _use(monkeypatch, fake)
    body = client.post("/api/ai/test-model", json={"jobs": ["answers", "tools", "vision"]},
                       headers={"X-AI-Provider": "openai", "X-AI-Key": "k"}).json()
    assert body["ok"] is True and body["model"] == "fake-1"
    assert {j: r["ok"] for j, r in body["jobs"].items()} == {"answers": True, "tools": True, "vision": True}
    # OpenAI-shaped tool for an OpenAI-style provider.
    assert next(t for kind, t in fake.seen if kind == "tools")["type"] == "function"


def test_a_job_the_model_cannot_do_says_so_and_the_others_still_run(client, monkeypatch):
    _use(monkeypatch, Fake(tools=False, vision=False))
    body = client.post("/api/ai/test-model", json={"jobs": ["answers", "tools", "vision"]},
                       headers={"X-AI-Provider": "ollama"}).json()
    assert body["ok"] is False
    assert body["jobs"]["answers"]["ok"] is True
    # Without native tool calls the written <tool_call> protocol is tried;
    # this fake answers in plain words, so it is reported as not calling it.
    assert body["jobs"]["tools"]["ok"] is False and "without calling the tool" in body["jobs"]["tools"]["detail"]
    assert "cannot read pictures" in body["jobs"]["vision"]["detail"]


def test_a_model_that_ignores_the_tool_is_not_reported_as_using_tools(client, monkeypatch):
    _use(monkeypatch, Fake(calls_tool=False))
    body = client.post("/api/ai/test-model", json={"jobs": ["tools"]},
                       headers={"X-AI-Provider": "openai", "X-AI-Key": "k"}).json()
    assert body["jobs"]["tools"]["ok"] is False and "without calling" in body["jobs"]["tools"]["detail"]


def test_an_unknown_job_is_refused(client, monkeypatch):
    _use(monkeypatch, Fake())
    resp = client.post("/api/ai/test-model", json={"jobs": ["telepathy"]},
                       headers={"X-AI-Provider": "openai", "X-AI-Key": "k"})
    assert resp.status_code == 422


def test_the_written_tool_protocol_counts_for_providers_without_native_tools(client, monkeypatch):
    fake = Fake(tools=False)
    fake.complete = lambda prompt, max_tokens, model: {"text": '<tool_call>{"tool": "get_time"}</tool_call>'}
    _use(monkeypatch, fake)
    body = client.post("/api/ai/test-model", json={"jobs": ["tools"]}, headers={"X-AI-Provider": "ollama"}).json()
    assert body["jobs"]["tools"]["ok"] is True

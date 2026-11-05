"""The footer's feature indicators say whether a feature works now, not whether
it is installed, and reading them never touches the network."""

import socket

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import settings
from services import audio_transcriber, embedder, feature_status as fs, ocr_engine
from services.embedding_status import embedding_status


def _state(check):
    return check()["state"]


def test_every_feature_reports_a_known_state_and_says_why():
    features = fs.feature_status()

    assert {f["id"] for f in features} >= {"semantic", "ocr", "transcription", "research", "ai", "mcp"}
    for f in features:
        assert f["state"] in (fs.ON, fs.LIMITED, fs.OFF, fs.UNAVAILABLE)
        assert f["label"] and f["detail"]


def test_reading_it_contacts_nothing(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError(f"tried to open a connection: {a}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    assert all(f["state"] != fs.UNAVAILABLE or "Could not be checked" not in f["detail"]
               for f in fs.feature_status())


def test_ocr_that_is_installed_but_switched_off_is_off(monkeypatch):
    monkeypatch.setattr(settings, "enable_ocr", False)
    monkeypatch.setattr(ocr_engine, "get_available_engines", lambda: ["pytesseract"])

    assert _state(fs._ocr) == fs.OFF


def test_ocr_turned_on_with_no_engine_is_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "enable_ocr", True)
    monkeypatch.setattr(settings, "vision_ocr_provider", "none")
    monkeypatch.setattr(ocr_engine, "get_available_engines", lambda: [])

    assert _state(fs._ocr) == fs.UNAVAILABLE


def test_ocr_names_the_local_engine_that_will_read(monkeypatch):
    monkeypatch.setattr(settings, "enable_ocr", True)
    monkeypatch.setattr(settings, "vision_ocr_provider", "none")
    monkeypatch.setattr(ocr_engine, "get_available_engines", lambda: ["pytesseract"])

    check = fs._ocr()

    assert check["state"] == fs.ON and "Tesseract" in check["detail"]


def test_a_hosted_vision_model_offline_falls_back_to_the_local_engine(monkeypatch):
    monkeypatch.setattr(settings, "enable_ocr", True)
    monkeypatch.setattr(settings, "vision_ocr_provider", "anthropic")
    monkeypatch.setattr(settings, "offline_mode", True)
    monkeypatch.setattr(ocr_engine, "get_available_engines", lambda: ["pytesseract"])
    assert "Tesseract" in fs._ocr()["detail"]

    monkeypatch.setattr(ocr_engine, "get_available_engines", lambda: [])
    assert _state(fs._ocr) == fs.UNAVAILABLE


def test_figures_are_limited_without_a_usable_vision_model(monkeypatch):
    monkeypatch.setattr(settings, "extract_figures", True)
    monkeypatch.setattr(settings, "vision_ocr_provider", "none")

    assert _state(fs._figures) == fs.LIMITED


@pytest.mark.parametrize("on_disk, offline, expected", [
    (True, True, fs.ON),
    (True, False, fs.ON),
    (False, True, fs.UNAVAILABLE),
    (False, False, fs.LIMITED),
])
def test_transcription_follows_the_model_and_not_the_package(monkeypatch, on_disk, offline, expected):
    monkeypatch.setattr(fs, "find_spec", lambda name: object())
    monkeypatch.setattr(audio_transcriber, "model_on_disk", lambda size: on_disk)
    monkeypatch.setattr(embedder, "hub_offline", lambda: offline)

    assert _state(fs._transcription) == expected


def test_transcription_is_off_when_not_installed(monkeypatch):
    monkeypatch.setattr(fs, "find_spec", lambda name: None)

    assert _state(fs._transcription) == fs.OFF


@pytest.mark.parametrize("status, expected", [
    ("ready", fs.ON), ("loading", fs.LIMITED), ("downloading", fs.LIMITED),
    ("missing", fs.LIMITED), ("error", fs.LIMITED),
])
def test_semantic_search_degrades_and_says_keywords_still_work(monkeypatch, status, expected):
    monkeypatch.setattr(embedding_status, "status", lambda: {"status": status})

    check = fs._semantic()

    assert check["state"] == expected
    if expected == fs.LIMITED:
        assert "keyword" in check["detail"].lower()


def test_offline_mode_limits_research_and_stops_links(monkeypatch):
    monkeypatch.setattr(settings, "offline_mode", True)

    assert _state(fs._research) == fs.LIMITED
    assert _state(fs._links) == fs.OFF


def test_a_hosted_chat_model_cannot_work_offline(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "anthropic")
    monkeypatch.setattr(settings, "offline_mode", True)

    assert _state(fs._ai) == fs.UNAVAILABLE


def test_one_failing_check_does_not_take_the_others_down(monkeypatch):
    def broken():
        raise RuntimeError("secret path /etc/clio/key")

    monkeypatch.setattr(fs, "_CHECKS", (broken, fs._mcp))

    features = fs.feature_status()

    assert [f["state"] for f in features][0] == fs.UNAVAILABLE
    assert features[1]["id"] == "mcp"
    assert "secret path" not in features[0]["detail"]


def test_the_endpoint_names_no_addresses_or_paths(monkeypatch, tmp_path):
    from api import system

    monkeypatch.setattr(settings, "ai_provider", "openai_compatible")
    monkeypatch.setattr(settings, "ai_base_url", "http://vllm.internal:8000/v1")
    monkeypatch.setattr(settings, "ai_api_key", "sk-very-secret")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app = FastAPI()
    app.include_router(system.router)

    response = TestClient(app).get("/api/features")

    assert response.status_code == 200
    body = response.text
    assert "vllm.internal" not in body and "sk-very-secret" not in body and str(tmp_path.name) not in body

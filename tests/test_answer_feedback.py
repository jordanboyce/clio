"""A3: "Helpful" and "Something is wrong" are stored on this server with the
verdict and the cited sources only, and an admin exports the wrong ones as
cases for scripts/pilot_eval.py."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import admin, chat
from services.app_database import SQLiteBackend, app_db


@pytest.fixture()
def client(tmp_path):
    old = app_db.db_path
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    app = FastAPI()
    app.include_router(chat.router)
    app.include_router(admin.router)
    with TestClient(app) as c:
        yield c
    app_db.db_path = old


SOURCES = [{"document_id": "d1", "filename": "travel-policy.pdf", "page_number": 3, "text_snippet": "secret text"}]


def test_a_wrong_answer_reaches_the_export_without_its_text(client):
    assert client.post("/api/chat/feedback", json={"verdict": "helpful", "collection_id": "c1"}).status_code == 201
    r = client.post("/api/chat/feedback", json={
        "verdict": "wrong", "problem": "wrong_answer", "collection_id": "c1", "provider": "ollama",
        "model": "llama3.2:3b", "sources": SOURCES})
    assert r.status_code == 201

    listed = client.get("/api/admin/answer-feedback").json()
    assert (listed["helpful"], listed["wrong"]) == (1, 1)
    assert "secret text" not in str(listed)

    export = client.get("/api/admin/answer-feedback/export").json()
    assert set(export) >= {"cases", "fields", "purpose"}
    [case] = export["cases"]
    assert case["kind"] == "wrong_answer"
    assert case["expected_sources"] == ["travel-policy.pdf"]
    assert case["question"] == "" and "not stored" in case["note"]
    assert case["reported"]["model"] == "llama3.2:3b"


def test_something_is_wrong_needs_a_reason(client):
    assert client.post("/api/chat/feedback", json={"verdict": "wrong"}).status_code == 422
    assert client.post("/api/chat/feedback", json={"verdict": "maybe"}).status_code == 422


def test_a_wrong_citation_does_not_vouch_for_the_cited_source(client):
    client.post("/api/chat/feedback", json={"verdict": "wrong", "problem": "wrong_citation", "sources": SOURCES})
    [case] = client.get("/api/admin/answer-feedback/export").json()["cases"]
    assert case["expected_sources"] == [] and case["reported"]["cited"] == ["travel-policy.pdf"]

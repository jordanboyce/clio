"""An audit trail that can attribute (E1): every row says where the request
came from, its request id and how it signed in; sign-ins, failures and
lockouts are events; the trail pages with a cursor and reaches the log
stream as one JSON line per row."""

import json
import logging

import pytest
from fastapi.testclient import TestClient

import config
from services import audit
from services.app_database import SQLiteBackend, app_db

PASSWORD = "a-long-enough-password"


@pytest.fixture()
def fresh_db(tmp_path):
    old = app_db.db_path
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    yield app_db
    app_db.db_path = old


@pytest.fixture()
def client(fresh_db, monkeypatch):
    import main

    for s in {id(config.settings): config.settings, id(main.settings): main.settings}.values():
        monkeypatch.setattr(s, "auth_password", PASSWORD)
        monkeypatch.setattr(s, "auth_failure_limit", 3)
    main._signed_in.clear()
    return TestClient(main.app)


def _bearer(secret=PASSWORD):
    return {"Authorization": f"Bearer {secret}"}


def test_the_vocabulary_lists_every_recorded_action():
    for action in ("admin.injection_tuning", "auth.sign_in", "auth.sign_in_failed", "auth.lockout"):
        assert action in audit.ACTIONS


def test_a_password_callers_action_says_how_it_signed_in_and_from_where(client):
    r = client.post("/api/admin/blocked-hashes", json={"content_hash": "a" * 64, "reason": "test"},
                    headers=_bearer())
    assert r.status_code in (200, 201)

    row = app_db.list_audit_events(action="admin.block_hash")[0]
    assert row["auth_method"] == "password"
    assert row["source_ip"] == "testclient"
    assert row["request_id"] == r.headers["X-Request-ID"]


def test_failed_sign_ins_and_the_lockout_are_events(client):
    for _ in range(3):
        assert client.get("/api/collections", headers=_bearer("wrong")).status_code == 401
    assert client.get("/api/collections", headers=_bearer("wrong")).status_code == 429

    failures = app_db.list_audit_events(action="auth.sign_in_failed")
    assert len(failures) == 3
    assert failures[0]["source_ip"] == "testclient" and failures[0]["target"] == "testclient"
    assert json.loads(failures[0]["detail"])["kind"] == "password"
    lockouts = app_db.list_audit_events(action="auth.lockout")
    assert len(lockouts) == 1 and json.loads(lockouts[0]["detail"])["failures"] == 3


def test_a_sign_in_is_recorded_once_per_caller_and_address_in_a_while(client):
    for _ in range(3):
        assert client.get("/api/collections", headers=_bearer()).status_code == 200

    rows = app_db.list_audit_events(action="auth.sign_in")
    assert len(rows) == 1
    assert rows[0]["auth_method"] == "password" and json.loads(rows[0]["detail"]) == {"method": "password"}


def test_the_trail_pages_with_a_cursor_past_any_cap(client):
    for i in range(5):
        audit.record("config.update", actor=None, target=f"t{i}")

    seen, before = [], None
    while True:
        params = {"limit": 2, "action": "config.update"}
        if before is not None:
            params["before"] = before
        page = client.get("/api/admin/audit", params=params, headers=_bearer()).json()
        seen += [e["target"] for e in page["events"]]
        before = page["next_before"]
        if before is None:
            break

    assert seen == ["t4", "t3", "t2", "t1", "t0"]


def test_each_row_reaches_the_log_stream_as_json(caplog):
    with caplog.at_level(logging.INFO, logger="clio.audit"):
        audit.record("share.create", actor="a@example.org", collection_id="c1", detail={"permission": "read"})

    lines = [r for r in caplog.records if r.name == "clio.audit"]
    assert len(lines) == 1
    row = json.loads(lines[0].getMessage().removeprefix("AUDIT "))
    assert row["action"] == "share.create" and row["actor"] == "a@example.org"
    assert row["detail"] == {"permission": "read"} and "request_id" in row
    assert lines[0].audit == row


def test_a_verified_identity_is_the_actor_without_private_collections(monkeypatch):
    """Without private collections the collection user is the shared default;
    the verified person is still who did it."""
    from middleware.user_context import reset_request_identity, set_request_identity

    token = set_request_identity("someone@example.org")
    try:
        assert audit.current_actor() == "someone@example.org"
    finally:
        reset_request_identity(token)

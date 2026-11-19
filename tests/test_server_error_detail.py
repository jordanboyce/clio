"""A 500's reason reaches whoever runs the server, not whoever hit it.

Routes answer unexpected failures with ``HTTPException(500, detail=str(e))``,
and that text can carry a file path or an internal hostname. On a one-person
install the person is the admin and reads it. On a shared deployment an
ordinary user gets a request id that finds the same text in the log.
"""

import importlib
import logging

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import config
from config import settings as _import_time_settings
from tests import identity_helpers

ADMIN = "ops@example.org"
WRITER = "writer@example.org"
LEAK = "sqlite3.OperationalError at /srv/clio/data/collections/x/indexes/metadata.db"


def _with_failing_routes(app):
    def fails():
        raise HTTPException(status_code=500, detail=LEAK)

    def refuses():
        raise HTTPException(status_code=409, detail="The job already finished.")

    def crashes():
        raise RuntimeError(f"unexpected state reading {LEAK}")

    def upstream():
        raise HTTPException(status_code=502, detail=f"provider at http://10.0.0.7:11434 said: {LEAK}")

    def needs_number(n: int):
        return {"n": n}

    app.add_api_route("/__test/fails", fails)
    app.add_api_route("/__test/refuses", refuses)
    app.add_api_route("/__test/crashes", crashes)
    app.add_api_route("/__test/upstream", upstream)
    app.add_api_route("/__test/needs-number", needs_number)
    # Ahead of the frontend's catch-all mount at "/", which would answer 404.
    added = app.router.routes[-5:]
    del app.router.routes[-5:]
    app.router.routes[:0] = added
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def open_client():
    import main

    return _with_failing_routes(importlib.reload(main).app)


@pytest.fixture()
def shared_client(monkeypatch):
    import main

    for s in {id(s): s for s in (_import_time_settings, config.settings)}.values():
        monkeypatch.setattr(s, "admin_emails", ADMIN)
    identity_helpers.install(monkeypatch)
    yield _with_failing_routes(importlib.reload(main).app)
    importlib.reload(main)


def test_on_a_one_person_install_the_reason_is_shown(open_client):
    r = open_client.get("/__test/fails")
    assert r.status_code == 500
    assert r.json()["detail"] == LEAK


def test_on_a_shared_deployment_a_user_gets_a_reference_not_the_reason(shared_client, caplog):
    with caplog.at_level(logging.ERROR):
        r = shared_client.get("/__test/fails", headers=identity_helpers.as_user(WRITER))
    assert r.status_code == 500
    body = r.json()
    assert LEAK not in r.text and "/srv/" not in r.text
    assert body["code"] == "internal_error"
    # The id in the body is the one on the response and the one in the log.
    assert body["request_id"] == r.headers["X-Request-ID"]
    assert any(body["request_id"] in rec.getMessage() and LEAK in rec.getMessage() for rec in caplog.records)


def test_an_admin_on_a_shared_deployment_still_reads_the_reason(shared_client):
    r = shared_client.get("/__test/fails", headers=identity_helpers.as_user(ADMIN))
    assert r.status_code == 500
    assert r.json()["detail"] == LEAK


def test_other_statuses_keep_their_detail_in_the_envelope(shared_client):
    r = shared_client.get("/__test/refuses", headers=identity_helpers.as_user(WRITER))
    assert r.status_code == 409
    body = r.json()
    assert body["detail"] == "The job already finished."
    assert body["code"] == "conflict" and body["request_id"] == r.headers["X-Request-ID"]



# ── D7: unhandled exceptions, upstream failures, password-only deployments ──

def test_an_unhandled_exception_is_the_envelope_with_the_headers(shared_client, caplog):
    with caplog.at_level(logging.ERROR):
        r = shared_client.get("/__test/crashes", headers=identity_helpers.as_user(WRITER))
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "internal_error" and LEAK not in r.text
    assert body["request_id"] == r.headers["X-Request-ID"]
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert any(body["request_id"] in rec.getMessage() for rec in caplog.records)


def test_an_upstream_failure_gives_a_fixed_sentence_and_logs_the_text(shared_client, caplog):
    with caplog.at_level(logging.ERROR):
        r = shared_client.get("/__test/upstream", headers=identity_helpers.as_user(WRITER))
    assert r.status_code == 502
    body = r.json()
    assert "10.0.0.7" not in r.text and LEAK not in r.text
    assert "Retrying is safe" in body["detail"] and body["code"] == "upstream_error"
    assert any("10.0.0.7" in rec.getMessage() for rec in caplog.records)


def test_a_validation_error_keeps_its_field_list_in_the_envelope(open_client):
    r = open_client.get("/__test/needs-number?n=lots")
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "invalid" and isinstance(body["detail"], list) and body["detail"][0]["loc"][-1] == "n"


@pytest.fixture()
def password_client(monkeypatch):
    import main

    for s in {id(s): s for s in (_import_time_settings, config.settings)}.values():
        monkeypatch.setattr(s, "auth_password", "shared-secret")
    yield _with_failing_routes(importlib.reload(main).app)
    importlib.reload(main)


def test_on_a_password_only_deployment_the_reason_goes_to_the_log_only(password_client):
    """Everyone who knows the shared password is the same caller, so there is
    no admin to give the reason to: redaction applies here too."""
    headers = {"Authorization": "Bearer shared-secret"}
    for path in ("/__test/fails", "/__test/crashes"):
        r = password_client.get(path, headers=headers)
        assert r.status_code == 500, path
        assert LEAK not in r.text and r.json()["code"] == "internal_error"

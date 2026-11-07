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

    app.add_api_route("/__test/fails", fails)
    app.add_api_route("/__test/refuses", refuses)
    # Ahead of the frontend's catch-all mount at "/", which would answer 404.
    added = app.router.routes[-2:]
    del app.router.routes[-2:]
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


def test_other_statuses_are_left_alone(shared_client):
    r = shared_client.get("/__test/refuses", headers=identity_helpers.as_user(WRITER))
    assert r.status_code == 409
    assert r.json() == {"detail": "The job already finished."}

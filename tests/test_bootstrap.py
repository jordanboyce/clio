"""GET /api/bootstrap answers the app's startup reads in one request.

Each section must be what the route itself answers to the same caller, so
the app can use it in place of that request (frontend/src/utils/bootstrap.js).
"""

import pytest
from fastapi.testclient import TestClient

from api import bootstrap as bs
from tests.test_folder_sync import COLLECTION, env  # noqa: F401 - fixtures
from tests.test_private_collections import ALICE, BOB, _as, fresh_db, private_client  # noqa: F401

# Sections whose body changes between two reads for reasons of their own
# (a clock, a counter), compared by status only.
_VOLATILE = {"/health", "/health?collection_id=default", "/api/reindex/status"}


@pytest.fixture()
def client(env):  # noqa: F811
    import main

    return TestClient(main.app)


def _direct(client, key, headers=None):
    return client.get(key, headers=headers or {})


def test_every_section_is_what_its_route_answers(client):
    r = client.get("/api/bootstrap", params={"collection_id": COLLECTION})
    assert r.status_code == 200
    responses = r.json()["responses"]

    expected = {bs.key_for(p, q) for p, q in bs._GLOBAL + bs._for_collection(COLLECTION)}
    assert set(responses) == expected
    for key, section in responses.items():
        direct = _direct(client, key)
        assert section["status"] == direct.status_code, key
        if direct.status_code < 300 and key not in _VOLATILE:
            assert section["body"] == direct.json(), key


def test_keys_match_what_the_app_sends():
    assert bs.key_for("/documents", {"offset": 0, "collection_id": "default", "limit": 200}) == \
        "/documents?collection_id=default&limit=200&offset=0"
    assert bs.key_for("/api/features", {}) == "/api/features"


def test_without_a_collection_only_the_global_reads(client):
    responses = client.get("/api/bootstrap").json()["responses"]
    assert set(responses) == {bs.key_for(p, q) for p, q in bs._GLOBAL}


def test_a_bad_collection_id_is_refused(client):
    assert client.get("/api/bootstrap", params={"collection_id": "../x"}).status_code == 400


def test_a_failing_section_reports_its_status_and_no_body(client, monkeypatch):
    monkeypatch.setattr(bs, "_GLOBAL", [("/api/no-such-route", {}), ("/api/features", {})])
    responses = client.get("/api/bootstrap").json()["responses"]
    assert responses["/api/no-such-route"] == {"status": 404}
    assert responses["/api/features"]["status"] == 200


def test_a_slow_section_is_left_out_not_waited_on(client, monkeypatch):
    import asyncio
    import time

    from services.app_database import app_db

    async def slow_router(scope, receive, send):
        if scope["path"] == "/api/features":
            await asyncio.sleep(5)
        await real_router(scope, receive, send)

    import main
    real_router = main.app.router.__call__
    monkeypatch.setattr(bs, "SECTION_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(bs, "_GLOBAL", [("/api/features", {}), ("/api/config", {})])
    monkeypatch.setattr(type(main.app.router), "__call__", lambda self, *a: slow_router(*a))

    started = time.monotonic()
    responses = client.get("/api/bootstrap").json()["responses"]

    assert time.monotonic() - started < 3
    assert responses["/api/features"] == {"status": 504}
    assert responses["/api/config"]["status"] == 200
    assert app_db is not None


# ── a shared deployment ──────────────────────────────────────────────────


def test_it_needs_a_signed_in_caller(private_client):  # noqa: F811
    assert private_client.get("/api/bootstrap").status_code == 401


def test_each_caller_gets_their_own_answers(private_client, monkeypatch):  # noqa: F811
    from services.indexer_manager import indexer_manager
    from tests.test_collection_bundle import SignedEmbedder

    monkeypatch.setattr(indexer_manager, "_get_embedding_service", lambda model: SignedEmbedder())
    from api import deps

    monkeypatch.setattr(deps, "_initialized", True)
    cid = private_client.post("/api/collections", json={"name": "Alice's notes"}, headers=_as(ALICE)).json()["id"]

    alice = private_client.get("/api/bootstrap", params={"collection_id": cid}, headers=_as(ALICE)).json()["responses"]
    bob = private_client.get("/api/bootstrap", params={"collection_id": cid}, headers=_as(BOB)).json()["responses"]

    assert alice["/api/user/me"]["body"]["user_id"] == ALICE
    assert bob["/api/user/me"]["body"]["user_id"] == BOB
    assert cid in {c["id"] for c in alice["/api/collections"]["body"]["collections"]}
    assert cid not in {c["id"] for c in bob["/api/collections"]["body"]["collections"]}
    # Bob cannot read Alice's collection through it, any more than directly.
    stats = f"/api/collections/{cid}/stats"
    assert alice[stats]["status"] == private_client.get(stats, headers=_as(ALICE)).status_code == 200
    assert bob[stats] == {"status": 404}
    assert private_client.get(stats, headers=_as(BOB)).status_code == 404
    assert "body" not in bob[f"/documents?collection_id={cid}&limit=200&offset=0"]

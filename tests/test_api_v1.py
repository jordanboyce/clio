"""Registered apps and /api/v1: what an app can reach, and what it can never reach.

The point of most of these is refusal. A key must not open anything beyond
/api/v1, an allowlist must hold in every deployment shape, and a person's token
must not turn into an app's or the other way round. Embeddings are faked, so no
model loads; everything else (indexing, jobs, the database) is the real code.
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.system
import api.v1
import config
import main
import services.api_apps as api_apps
import services.mcp_server as mcp
import services.readiness
import services.sharing_service
from middleware import rate_limit, security_headers, user_context
from services.app_database import SQLiteBackend, app_db
from services.chunker import TextChunker
from services.collection_service import collection_service
from services.document_extractor import DocumentExtractor
from services.indexer_manager import indexer_manager
from services.indexing import DocumentIndexer
from services.vector_store import VectorStore
from tests.test_identity import AUDIENCE, ISSUER, _FakeResp, _jwk, _mint, keypair  # noqa: F401
from tests.test_mcp_write import EMBED_DIM, FakeEmbedder

DOC = b"# Travel policy\n\nStaff may claim rail fares up to 90 euros per trip.\n"
SPEC_PATH = Path(__file__).parent.parent / "docs" / "api" / "openapi-v1.json"


def _set(monkeypatch, **values):
    """Other test modules reload config, so modules can hold different Settings
    objects. Set a value on each of them."""
    holders = [config, main, api.system, api.v1, mcp, services.readiness, services.sharing_service,
               rate_limit, security_headers, user_context]
    for name, value in values.items():
        for holder in holders:
            monkeypatch.setattr(holder.settings, name, value)


def _indexer(path):
    return DocumentIndexer(
        vector_store=VectorStore(index_dir=path, embedding_dim=EMBED_DIM),
        embedding_service=FakeEmbedder(),
        document_extractor=DocumentExtractor(),
        text_chunker=TextChunker(chunk_size=200, chunk_overlap=40),
    )


class World:
    def __init__(self, client, monkeypatch, tmp_path):
        self.client = client
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path

    def collection(self, name, owner_id="default", **fields):
        created = collection_service.create_collection(name=name, owner_id=owner_id)
        cid = created["id"]
        if fields:
            app_db.update_collection(cid, **fields)
        self.monkeypatch.setitem(indexer_manager._indexers, cid, _indexer(self.tmp_path / "ix" / cid))
        return cid

    def register(self, name="reports", collections=(), **fields):
        body = {"name": name, "collections": list(collections), **fields}
        response = self.client.post("/api/admin/apps", json=body)
        assert response.status_code == 201, response.text
        return response.json()

    def wait_for(self, job_id, headers):
        for _ in range(100):
            job = self.client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()
            if job["status"] in ("completed", "failed", "cancelled"):
                return job
            time.sleep(0.05)
        raise AssertionError(f"job {job_id} did not finish: {job}")

    def upload(self, cid, headers, name="policy.md", data=DOC):
        response = self.client.post(
            f"/api/v1/collections/{cid}/documents", headers=headers, files=[("files", (name, data, "text/markdown"))],
        )
        assert response.status_code == 202, response.text
        job = self.wait_for(response.json()["job_id"], headers)
        assert job["status"] == "completed", job
        return job


@pytest.fixture()
def world(tmp_path, monkeypatch):
    old_db_path, old_base_dir = app_db.db_path, collection_service.base_dir
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    collection_service.base_dir = tmp_path / "collections"
    api_apps._invalidate_subjects()
    api_apps._touched_at.clear()

    from api import deps

    monkeypatch.setattr(deps, "_initialized", True)
    _set(monkeypatch, auth_password="", private_collections=False, auth_require_identity=False,
         identity_provider="", rate_limit_enabled=False)
    yield World(TestClient(main.app, raise_server_exceptions=False), monkeypatch, tmp_path)
    app_db.db_path, collection_service.base_dir = old_db_path, old_base_dir
    api_apps._invalidate_subjects()


def _bearer(key):
    return {"Authorization": f"Bearer {key}"}


# ── The boundary: /api/v1 takes an app's credential, and nothing else ──────


def test_no_credential_is_refused_even_on_an_open_appliance(world):
    response = world.client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"
    assert response.headers["WWW-Authenticate"].startswith("Bearer")


def test_the_shared_password_does_not_open_v1(world, monkeypatch):
    _set(monkeypatch, auth_password="deploy-secret")
    assert world.client.get("/api/collections", headers=_bearer("deploy-secret")).status_code == 200
    assert world.client.get("/api/v1/me", headers=_bearer("deploy-secret")).status_code == 401


def test_an_mcp_token_does_not_open_v1(world):
    from services import mcp_tokens

    token = mcp_tokens.generate_token(None, "agent")["token"]
    assert world.client.get("/api/v1/me", headers=_bearer(token)).status_code == 401


@pytest.mark.parametrize("method,path", [
    ("get", "/api/collections"),
    ("get", "/api/admin/apps"),
    ("get", "/api/admin/audit"),
    ("get", "/documents"),
    ("post", "/search"),
    ("post", "/mcp/"),
    ("get", "/"),
    ("get", "/openapi.json"),
])
def test_a_key_opens_nothing_else_even_on_an_open_appliance(world, method, path):
    """On an open appliance those routes answer anyone with no credential at all.
    A key must not read as that: a leak would otherwise lose every limit it had."""
    cid = world.collection("alpha")
    key = world.register(collections=[cid], can_write=True)["key"]
    assert getattr(world.client, method)(path, headers=_bearer(key)).status_code == 401


def test_a_key_opens_nothing_else_on_a_password_deployment(world, monkeypatch):
    cid = world.collection("alpha")
    key = world.register(collections=[cid])["key"]
    _set(monkeypatch, auth_password="deploy-secret")
    assert world.client.get("/api/collections", headers=_bearer(key)).status_code == 401
    assert world.client.get("/api/v1/me", headers=_bearer(key)).status_code == 200


def test_the_description_is_readable_by_whoever_can_reach_the_app_and_by_an_app(world, monkeypatch):
    cid = world.collection("alpha")
    key = world.register(collections=[cid])["key"]
    _set(monkeypatch, auth_password="deploy-secret")
    assert world.client.get("/api/v1/openapi.json").status_code == 401
    assert world.client.get("/api/v1/openapi.json", headers=_bearer("deploy-secret")).status_code == 200
    assert world.client.get("/api/v1/openapi.json", headers=_bearer(key)).status_code == 200
    assert world.client.get("/api/v1/docs", headers=_bearer("deploy-secret")).status_code == 200


@pytest.mark.parametrize("bad", ["clio_app_", "clio_app_not-a-real-key", "nope", "a.b.c"])
def test_unknown_credentials_are_refused(world, bad):
    assert world.client.get("/api/v1/me", headers=_bearer(bad)).status_code == 401


# ── A key's lifetime ───────────────────────────────────────────────────────


def test_only_a_hash_is_stored_and_the_key_is_shown_once(world):
    cid = world.collection("alpha")
    created = world.register(collections=[cid])
    key = created["key"]
    assert key.startswith("clio_app_")

    listed = world.client.get("/api/admin/apps").json()["apps"][0]
    assert "key" not in listed and "key_hash" not in listed and "previous_key_hash" not in listed
    assert listed["key_prefix"] == key[:len(listed["key_prefix"])]
    assert key not in json.dumps(app_db.list_api_apps())


def test_revoking_ends_access_at_once_and_for_good(world):
    cid = world.collection("alpha")
    created = world.register(collections=[cid])
    headers = _bearer(created["key"])
    assert world.client.get("/api/v1/me", headers=headers).status_code == 200

    revoked = world.client.delete(f"/api/admin/apps/{created['id']}")
    assert revoked.json()["status"] == "revoked"
    assert world.client.get("/api/v1/me", headers=headers).status_code == 401
    # Nothing brings it back, and the record is still there for the audit trail.
    assert world.client.post(f"/api/admin/apps/{created['id']}/rotate-key").status_code == 409
    assert world.client.patch(f"/api/admin/apps/{created['id']}", json={"can_write": True}).status_code == 409
    assert world.client.get("/api/admin/apps").json()["apps"][0]["status"] == "revoked"


def test_an_expired_app_is_refused(world):
    cid = world.collection("alpha")
    created = world.register(collections=[cid], expires_in_days=1)
    headers = _bearer(created["key"])
    assert world.client.get("/api/v1/me", headers=headers).status_code == 200
    app_db.update_api_app(created["id"], {"expires_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat()})
    assert world.client.get("/api/v1/me", headers=headers).status_code == 401
    assert world.client.get("/api/admin/apps").json()["apps"][0]["status"] == "expired"


def test_rotation_keeps_the_old_key_through_its_grace_and_not_after(world):
    cid = world.collection("alpha")
    created = world.register(collections=[cid])
    old = _bearer(created["key"])

    rotated = world.client.post(f"/api/admin/apps/{created['id']}/rotate-key", json={"grace_hours": 24}).json()
    new = _bearer(rotated["key"])
    assert world.client.get("/api/v1/me", headers=old).status_code == 200
    assert world.client.get("/api/v1/me", headers=new).status_code == 200

    app_db.update_api_app(created["id"], {
        "previous_key_expires_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat(),
    })
    assert world.client.get("/api/v1/me", headers=old).status_code == 401
    assert world.client.get("/api/v1/me", headers=new).status_code == 200


def test_rotation_with_no_grace_ends_a_leaked_key_now(world):
    cid = world.collection("alpha")
    created = world.register(collections=[cid])
    world.client.post(f"/api/admin/apps/{created['id']}/rotate-key", json={"grace_hours": 0})
    assert world.client.get("/api/v1/me", headers=_bearer(created["key"])).status_code == 401


def test_rotating_twice_ends_the_oldest_key_at_once(world):
    cid = world.collection("alpha")
    created = world.register(collections=[cid])
    world.client.post(f"/api/admin/apps/{created['id']}/rotate-key")
    world.client.post(f"/api/admin/apps/{created['id']}/rotate-key")
    assert world.client.get("/api/v1/me", headers=_bearer(created["key"])).status_code == 401


def test_an_edit_applies_to_the_next_request(world):
    alpha, beta = world.collection("alpha"), world.collection("beta")
    created = world.register(collections=[alpha])
    headers = _bearer(created["key"])
    assert world.client.get(f"/api/v1/collections/{beta}/documents", headers=headers).status_code == 404

    response = world.client.patch(f"/api/admin/apps/{created['id']}", json={"collections": [beta]})
    assert response.json()["collections"] == [beta]
    assert world.client.get(f"/api/v1/collections/{beta}/documents", headers=headers).status_code == 200
    assert world.client.get(f"/api/v1/collections/{alpha}/documents", headers=headers).status_code == 404


# ── Registering ────────────────────────────────────────────────────────────


def test_registration_refuses_what_could_not_work(world):
    cid = world.collection("alpha")
    post = lambda **body: world.client.post("/api/admin/apps", json=body).status_code  # noqa: E731
    assert post(name="x", collections=["not-a-collection"]) == 404
    assert post(name="x", collections=[]) == 422
    assert post(name="has spaces", collections=[cid]) == 422
    assert post(name="x", collections=[cid], issue_key=False) == 422  # no way to authenticate
    assert post(name="x", collections=[cid]) == 201
    assert post(name="X", collections=[cid]) == 409  # the audit trail names apps
    assert post(name="y", collections=[cid], oidc_subject="svc-1") == 201
    assert post(name="z", collections=[cid], oidc_subject="svc-1") == 409


def test_managing_apps_is_for_admins_once_a_gate_exists(world, monkeypatch):
    cid = world.collection("alpha")
    _set(monkeypatch, identity_provider="trusted_header", trusted_header_name="X-Forwarded-User",
         trusted_header_secret="", auth_require_identity=True, admin_emails="boss@example.test")
    person = {"X-Forwarded-User": "staff@example.test"}
    boss = {"X-Forwarded-User": "boss@example.test"}
    body = {"name": "reports", "collections": [cid]}

    assert world.client.post("/api/admin/apps", json=body).status_code == 401
    assert world.client.post("/api/admin/apps", json=body, headers=person).status_code == 403
    assert world.client.get("/api/admin/apps", headers=person).status_code == 403
    created = world.client.post("/api/admin/apps", json=body, headers=boss)
    assert created.status_code == 201
    assert created.json()["created_by"] == "boss@example.test"
    assert world.client.delete(f"/api/admin/apps/{created.json()['id']}", headers=person).status_code == 403


def test_every_change_to_an_app_is_audited_without_the_key(world):
    cid = world.collection("alpha")
    created = world.register(collections=[cid])
    world.client.patch(f"/api/admin/apps/{created['id']}", json={"can_write": True})
    rotated = world.client.post(f"/api/admin/apps/{created['id']}/rotate-key").json()
    world.client.delete(f"/api/admin/apps/{created['id']}")

    rows = app_db.list_audit_events(limit=20)
    assert {"api_app.create", "api_app.update", "api_app.rotate_key", "api_app.revoke"} <= {r["action"] for r in rows}
    text = json.dumps(rows)
    assert created["key"] not in text and rotated["key"] not in text


# ── The allowlist ──────────────────────────────────────────────────────────


def test_an_app_sees_only_its_collections(world):
    alpha, beta = world.collection("alpha"), world.collection("beta")
    headers = _bearer(world.register(collections=[alpha])["key"])
    assert [c["collection_id"] for c in world.client.get("/api/v1/collections", headers=headers).json()["collections"]] == [alpha]

    for call in (
        lambda c: world.client.post(f"/api/v1/collections/{c}/search", json={"query": "x"}, headers=headers),
        lambda c: world.client.post(f"/api/v1/collections/{c}/ask", json={"question": "x"}, headers=headers),
        lambda c: world.client.get(f"/api/v1/collections/{c}/documents", headers=headers),
        lambda c: world.client.get(f"/api/v1/collections/{c}/documents/d1", headers=headers),
        lambda c: world.client.get(f"/api/v1/collections/{c}/documents/d1/text", headers=headers),
    ):
        hidden, missing = call(beta), call("does-not-exist")
        assert hidden.status_code == 404 and missing.status_code == 404
        # One cannot be told from the other, apart from the id the caller sent.
        assert hidden.json()["code"] == missing.json()["code"] == "collection_not_found"


def test_a_job_outside_the_allowlist_looks_absent(world):
    alpha, beta = world.collection("alpha"), world.collection("beta")
    writer = _bearer(world.register("writer", collections=[alpha], can_write=True)["key"])
    other = _bearer(world.register("other", collections=[beta])["key"])
    response = world.client.post(f"/api/v1/collections/{alpha}/documents", headers=writer,
                                 files=[("files", ("p.md", DOC, "text/markdown"))])
    job_id = response.json()["job_id"]
    world.wait_for(job_id, writer)
    assert world.client.get(f"/api/v1/jobs/{job_id}", headers=other).status_code == 404
    assert world.client.get(f"/api/v1/jobs/{job_id}", headers=writer).status_code == 200


def test_a_restricted_collection_takes_an_explicit_grant(world):
    secret = world.collection("secret", sensitivity="restricted")
    public = world.collection("public")
    without = _bearer(world.register("without", collections=[public])["key"])
    granted = _bearer(world.register("granted", collections=[secret])["key"])
    assert world.client.get(f"/api/v1/collections/{secret}/documents", headers=without).status_code == 404
    assert world.client.get(f"/api/v1/collections/{secret}/documents", headers=granted).status_code == 200


def test_the_allowlist_decides_under_private_collections_too(world, monkeypatch):
    """Someone's private collection, granted by an admin: the app reaches that one
    and not the owner's other one, and an ungranted team collection stays out."""
    mine = world.collection("mine", owner_id="owner@example.test")
    theirs = world.collection("theirs", owner_id="owner@example.test")
    team = world.collection("team")
    headers = _bearer(world.register(collections=[mine], can_write=True)["key"])

    _set(monkeypatch, identity_provider="trusted_header", trusted_header_name="X-Forwarded-User",
         private_collections=True)
    assert world.client.get(f"/api/v1/collections/{mine}/documents", headers=headers).status_code == 200
    assert world.client.get(f"/api/v1/collections/{theirs}/documents", headers=headers).status_code == 404
    assert world.client.get(f"/api/v1/collections/{team}/documents", headers=headers).status_code == 404
    listed = world.client.get("/api/v1/collections", headers=headers).json()["collections"]
    assert [c["collection_id"] for c in listed] == [mine]
    world.upload(mine, headers)
    found = world.client.post(f"/api/v1/collections/{mine}/search", json={"query": "rail", "mode": "keyword"}, headers=headers)
    assert found.json()["total_results"] == 1


def test_the_acceptable_use_gate_does_not_trap_a_registered_app(world, monkeypatch):
    cid = world.collection("alpha")
    headers = _bearer(world.register(collections=[cid], can_write=True)["key"])
    _set(monkeypatch, identity_provider="trusted_header", trusted_header_name="X-Forwarded-User",
         private_collections=True, aup_required=True)
    from services import governance
    monkeypatch.setattr(governance.settings, "aup_required", True)
    monkeypatch.setattr(governance.settings, "private_collections", True)
    world.upload(cid, headers)


def test_an_app_can_never_be_a_collections_owner():
    from services.sharing_service import sharing_service

    app = {"actor": "app:a", "collections": ["c1"], "can_write": True}
    token = user_context.set_request_app(app)
    try:
        assert sharing_service.check_collection_access("c1", "app:a") == "readwrite"
        assert sharing_service.check_collection_access("c2", "app:a") is None
        assert user_context.get_request_user() == "app:a"
    finally:
        user_context.reset_request_app(token)


# ── Reading and writing ────────────────────────────────────────────────────


def test_a_read_only_app_cannot_change_anything(world):
    cid = world.collection("alpha")
    writer = _bearer(world.register("writer", collections=[cid], can_write=True)["key"])
    reader = _bearer(world.register("reader", collections=[cid])["key"])
    world.upload(cid, writer)
    doc = world.client.get(f"/api/v1/collections/{cid}/documents", headers=reader).json()["documents"][0]

    refused = world.client.post(f"/api/v1/collections/{cid}/documents", headers=reader,
                                files=[("files", ("x.md", b"# x\n\nnew text\n", "text/markdown"))])
    assert refused.status_code == 403 and refused.json()["code"] == "insufficient_scope"
    removed = world.client.delete(f"/api/v1/collections/{cid}/documents/{doc['document_id']}", headers=reader)
    assert removed.status_code == 403
    assert world.client.get(f"/api/v1/collections/{cid}/documents", headers=reader).json()["total_matching"] == 1


def test_upload_search_read_and_delete(world):
    cid = world.collection("alpha")
    headers = _bearer(world.register(collections=[cid], can_write=True)["key"])
    world.upload(cid, headers)

    found = world.client.post(f"/api/v1/collections/{cid}/search",
                              json={"query": "rail fares", "mode": "keyword", "top_k": 3}, headers=headers)
    assert found.status_code == 200
    body = found.json()
    assert body["total_results"] == 1
    passage = body["results"][0]
    assert passage["filename"] == "policy.md" and "90 euros" in passage["excerpt"]

    listing = world.client.get(f"/api/v1/collections/{cid}/documents", headers=headers).json()
    assert listing["total_matching"] == 1 and listing["next_offset"] is None
    doc_id = listing["documents"][0]["document_id"]
    assert world.client.get(f"/api/v1/collections/{cid}/documents/{doc_id}", headers=headers).json()["filename"] == "policy.md"
    text = world.client.get(f"/api/v1/collections/{cid}/documents/{doc_id}/text", headers=headers).json()
    assert "90 euros" in text["chunks"][0]["text"] and text["truncated"] is False

    assert world.client.delete(f"/api/v1/collections/{cid}/documents/{doc_id}", headers=headers).json()["deleted"] is True
    assert world.client.delete(f"/api/v1/collections/{cid}/documents/{doc_id}", headers=headers).status_code == 404
    assert world.client.get(f"/api/v1/collections/{cid}/documents", headers=headers).json()["total_matching"] == 0


def test_uploading_under_the_same_name_replaces(world):
    cid = world.collection("alpha")
    headers = _bearer(world.register(collections=[cid], can_write=True)["key"])
    world.upload(cid, headers)
    world.upload(cid, headers, data=b"# Travel policy\n\nThe rail limit is now 120 euros.\n")
    listing = world.client.get(f"/api/v1/collections/{cid}/documents", headers=headers).json()
    assert listing["total_matching"] == 1
    found = world.client.post(f"/api/v1/collections/{cid}/search", json={"query": "euros", "mode": "keyword"}, headers=headers).json()
    assert ["120 euros" in r["excerpt"] for r in found["results"]] == [True]


def test_a_batch_with_nothing_usable_is_a_clear_refusal(world):
    cid = world.collection("alpha")
    headers = _bearer(world.register(collections=[cid], can_write=True)["key"])
    response = world.client.post(f"/api/v1/collections/{cid}/documents", headers=headers,
                                 files=[("files", ("bad.exe", b"MZ", "application/octet-stream"))])
    assert response.status_code == 400 and response.json()["code"] == "no_acceptable_files"


def test_part_of_a_batch_refused_is_reported(world):
    cid = world.collection("alpha")
    headers = _bearer(world.register(collections=[cid], can_write=True)["key"])
    response = world.client.post(f"/api/v1/collections/{cid}/documents", headers=headers, files=[
        ("files", ("good.md", DOC, "text/markdown")), ("files", ("bad.exe", b"MZ", "application/octet-stream")),
    ])
    assert response.status_code == 202
    assert response.json()["accepted_files"] == 1
    assert [r["filename"] for r in response.json()["refused_files"]] == ["bad.exe"]


def test_a_document_held_for_review_is_absent_and_cannot_be_removed(world):
    cid = world.collection("alpha")
    headers = _bearer(world.register(collections=[cid], can_write=True)["key"])
    world.upload(cid, headers)
    store = indexer_manager.get_indexer(cid).vector_store.metadata_store
    doc_id = store.list_documents()[0]["document_id"]
    store.set_document_governance(doc_id, policy_status="quarantined")

    base = f"/api/v1/collections/{cid}/documents"
    assert world.client.get(base, headers=headers).json()["total_matching"] == 0
    assert world.client.get(f"{base}/{doc_id}", headers=headers).status_code == 404
    assert world.client.get(f"{base}/{doc_id}/text", headers=headers).status_code == 404
    assert world.client.delete(f"{base}/{doc_id}", headers=headers).status_code == 404
    found = world.client.post(f"/api/v1/collections/{cid}/search", json={"query": "rail", "mode": "keyword"}, headers=headers)
    assert found.json()["total_results"] == 0
    assert store.get_document_info(doc_id)  # still there for the reviewer


def test_server_paths_are_not_part_of_the_contract(world):
    spec = json.dumps(api.v1.v1_app.openapi())
    assert "source_path" not in spec


def test_what_an_app_does_is_attributed_to_it(world):
    cid = world.collection("alpha")
    headers = _bearer(world.register(collections=[cid], can_write=True)["key"])
    world.upload(cid, headers)
    doc_id = world.client.get(f"/api/v1/collections/{cid}/documents", headers=headers).json()["documents"][0]["document_id"]
    world.client.delete(f"/api/v1/collections/{cid}/documents/{doc_id}", headers=headers)

    by_action = {r["action"]: r["actor"] for r in app_db.list_audit_events(limit=50)}
    assert by_action["document.index_job"] == "app:reports"
    assert by_action["document.delete"] == "app:reports"


def test_reads_are_audited_when_the_deployment_asks(world, monkeypatch):
    cid = world.collection("alpha")
    created = world.register(collections=[cid], can_write=True)
    headers = _bearer(created["key"])
    world.upload(cid, headers)
    monkeypatch.setattr(mcp.settings, "mcp_audit_tool_calls", True)

    world.client.post(f"/api/v1/collections/{cid}/search", json={"query": "rail", "mode": "keyword"}, headers=headers)
    rows = [r for r in app_db.list_audit_events(limit=20) if r["action"] == "api.call"]
    assert rows and rows[0]["actor"] == "app:reports" and rows[0]["target"] == "search_collection"
    assert created["id"] in rows[0]["detail"]
    assert "90 euros" not in json.dumps(rows)


def test_the_request_scope_does_not_outlive_the_request(world):
    cid = world.collection("alpha")
    headers = _bearer(world.register(collections=[cid])["key"])
    world.client.get("/api/v1/me", headers=headers)
    assert user_context.get_request_app() is None
    assert mcp.get_request_mcp_token_scope() is None
    assert mcp.get_request_mcp_can_write() is True


# ── Answers ────────────────────────────────────────────────────────────────


def test_ask_with_no_deployment_model_says_so(world, monkeypatch):
    cid = world.collection("alpha")
    headers = _bearer(world.register(collections=[cid])["key"])
    _set(monkeypatch, ai_provider="")
    response = world.client.post(f"/api/v1/collections/{cid}/ask", json={"question": "x"}, headers=headers)
    assert response.status_code == 503 and response.json()["code"] == "model_not_configured"


def test_ask_returns_the_cited_answer(world, monkeypatch):
    cid = world.collection("alpha")
    headers = _bearer(world.register(collections=[cid])["key"])
    _set(monkeypatch, ai_provider="ollama")
    seen = {}

    def fake_ask(question, **kwargs):
        seen.update(kwargs, question=question)
        return {
            "question": question, "collection_id": kwargs["collection_id"], "answer": "90 euros [Source 1]",
            "sources": [{"n": 1, "filename": "policy.md", "page_number": 1, "document_id": "d1", "excerpt": "e"}],
            "depth": "quick", "cached": False, "model": "m", "usage": {"input_tokens": 1, "output_tokens": 2},
            "citation_note": "dropped by the response model", "tool_calls": 0,
        }

    monkeypatch.setattr(mcp, "ask_documents_sync", fake_ask)
    response = world.client.post(f"/api/v1/collections/{cid}/ask", json={"question": "How much?", "top_k": 4}, headers=headers)
    assert response.status_code == 200
    assert response.json()["answer"] == "90 euros [Source 1]"
    assert "citation_note" not in response.json()
    assert seen["collection_id"] == cid and seen["top_k"] == 4


@pytest.mark.parametrize("raised,status,code", [
    (mcp.DailyBudgetSpent("spent"), 429, "daily_budget_spent"),
    (ValueError("the provider timed out"), 502, "answer_failed"),
])
def test_a_failed_answer_is_not_a_500(world, monkeypatch, raised, status, code):
    cid = world.collection("alpha")
    headers = _bearer(world.register(collections=[cid])["key"])
    _set(monkeypatch, ai_provider="ollama")

    def fail(*args, **kwargs):
        raise raised

    monkeypatch.setattr(mcp, "ask_documents_sync", fail)
    response = world.client.post(f"/api/v1/collections/{cid}/ask", json={"question": "x"}, headers=headers)
    assert response.status_code == status and response.json()["code"] == code


# ── Budgets ────────────────────────────────────────────────────────────────


def test_each_app_has_its_own_budget_and_overrides_the_deployments(world, monkeypatch):
    from middleware.rate_limit import rate_limiter

    cid = world.collection("alpha")
    tight = _bearer(world.register("tight", collections=[cid], search_per_minute=2)["key"])
    roomy = _bearer(world.register("roomy", collections=[cid], search_per_minute=50)["key"])
    _set(monkeypatch, rate_limit_enabled=True, rate_limit_search_per_minute=1)
    rate_limiter.reset()
    try:
        search = lambda headers: world.client.post(  # noqa: E731
            f"/api/v1/collections/{cid}/search", json={"query": "x", "mode": "keyword"}, headers=headers
        )
        assert [search(tight).status_code for _ in range(3)] == [200, 200, 429]
        limited = search(tight)
        assert limited.json()["code"] == "rate_limited" and "Retry-After" in limited.headers
        # One app hitting its limit costs the other nothing.
        assert all(search(roomy).status_code == 200 for _ in range(10))
    finally:
        rate_limiter.reset()


def test_me_reports_the_budgets_in_force(world, monkeypatch):
    cid = world.collection("alpha")
    headers = _bearer(world.register(collections=[cid], ask_per_minute=20, other_per_minute=0)["key"])
    _set(monkeypatch, rate_limit_search_per_minute=33)
    assert world.client.get("/api/v1/me", headers=headers).json()["budgets"] == {
        "search_per_minute": 33, "ask_per_minute": 20, "other_per_minute": 0,
    }


# ── An IdP's client-credentials token ──────────────────────────────────────


@pytest.fixture()
def idp(world, keypair, monkeypatch):  # noqa: F811
    good, other = keypair
    jwks = {"keys": [_jwk(good, "kid-1")]}
    discovery = {"jwks_uri": f"{ISSUER}/certs"}

    def fake_urlopen(url, timeout=10):
        return _FakeResp(discovery if url.endswith("openid-configuration") else jwks)

    monkeypatch.setattr("services.identity.urllib.request.urlopen", fake_urlopen)
    _set(monkeypatch, identity_provider="oidc", oidc_issuer=ISSUER, oidc_audience=AUDIENCE, oidc_jwks_url="",
         oidc_required_claims="groups=clio-users", auth_require_identity=True, admin_emails="")
    from services import identity

    monkeypatch.setattr(identity, "_oidc_verifier", None)
    return good, other


def _register_directly(cid, **fields):
    return api_apps.create_app("erp", [cid], **fields)


def test_a_registered_subject_works_with_no_key_and_no_group_claim(world, idp):
    good, _ = idp
    cid = world.collection("alpha")
    _register_directly(cid, oidc_subject="svc-erp", issue_key=False)
    token = _mint(good, sub="svc-erp", email=None)
    me = world.client.get("/api/v1/me", headers=_bearer(token))
    assert me.status_code == 200 and me.json()["name"] == "erp"
    assert world.client.get(f"/api/v1/collections/{cid}/documents", headers=_bearer(token)).status_code == 200


def test_a_person_signed_in_through_the_same_client_is_not_the_app(world, idp):
    """They carry the same client id (azp), which is why apps match on `sub`."""
    good, _ = idp
    cid = world.collection("alpha")
    _register_directly(cid, oidc_subject="svc-erp", issue_key=False)
    person = _mint(good, sub="user-123", azp="erp-client", client_id="erp-client", groups=["clio-users"])
    assert world.client.get("/api/v1/me", headers=_bearer(person)).status_code == 401
    # ...and is still who they are on the rest of the app.
    assert world.client.get("/api/collections", headers=_bearer(person)).status_code == 200


def test_an_apps_token_opens_nothing_else(world, idp):
    good, _ = idp
    cid = world.collection("alpha")
    _register_directly(cid, oidc_subject="svc-erp", issue_key=False)
    # Even carrying the group a person would need.
    token = _mint(good, sub="svc-erp", groups=["clio-users"])
    assert world.client.get("/api/collections", headers=_bearer(token)).status_code == 401
    assert world.client.post("/mcp/", json={}, headers=_bearer(token)).status_code == 401


@pytest.mark.parametrize("overrides", [
    {"aud": "some-other-app"},
    {"iss": "https://evil.example/realms/main"},
    {"exp": 1},
])
def test_a_bad_token_for_a_registered_subject_is_refused(world, idp, overrides):
    good, _ = idp
    cid = world.collection("alpha")
    _register_directly(cid, oidc_subject="svc-erp", issue_key=False)
    token = _mint(good, sub="svc-erp", **overrides)
    assert world.client.get("/api/v1/me", headers=_bearer(token)).status_code == 401


def test_a_forged_signature_for_a_registered_subject_is_refused(world, idp):
    _, other = idp
    cid = world.collection("alpha")
    _register_directly(cid, oidc_subject="svc-erp", issue_key=False)
    forged = _mint(other, sub="svc-erp", groups=["clio-users"])
    assert world.client.get("/api/v1/me", headers=_bearer(forged)).status_code == 401
    assert world.client.get("/api/collections", headers=_bearer(forged)).status_code == 401


def test_a_revoked_apps_token_does_not_fall_through_to_the_rules_for_people(world, idp):
    good, _ = idp
    cid = world.collection("alpha")
    created = _register_directly(cid, oidc_subject="svc-erp", issue_key=False)
    token = _mint(good, sub="svc-erp", groups=["clio-users"])
    assert world.client.get("/api/v1/me", headers=_bearer(token)).status_code == 200
    api_apps.revoke_app(created["id"])
    assert world.client.get("/api/v1/me", headers=_bearer(token)).status_code == 401
    assert world.client.get("/api/collections", headers=_bearer(token)).status_code == 401


def test_jwts_mean_nothing_on_a_deployment_with_no_oidc(world, keypair):  # noqa: F811
    good, _ = keypair
    cid = world.collection("alpha")
    api_apps.create_app("erp", [cid], oidc_subject="svc-erp")
    token = _mint(good, sub="svc-erp")
    assert world.client.get("/api/v1/me", headers=_bearer(token)).status_code == 401


# ── The contract ───────────────────────────────────────────────────────────


def test_the_live_api_matches_the_published_contract():
    """A failure here means a consuming app could break. If the change is meant,
    apply the rules in docs/APP_API.md, run scripts/export_api_spec.py, and review
    the diff of docs/api/openapi-v1.json."""
    published = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    assert api.v1.v1_app.openapi() == published


def test_every_operation_has_a_typed_body_and_declares_its_errors():
    spec = api.v1.v1_app.openapi()
    assert spec["components"]["securitySchemes"]
    assert spec["servers"] == [{"url": "/api/v1"}]
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            success = next(code for code in operation["responses"] if code.startswith("2"))
            body = operation["responses"][success]["content"]["application/json"]["schema"]
            assert body.get("$ref"), f"{method} {path} has no response model"
            assert {"401", "429"} <= set(operation["responses"]), f"{method} {path}"
            assert operation.get("security"), f"{method} {path}"


def test_errors_all_have_the_same_shape(world):
    cid = world.collection("alpha")
    headers = _bearer(world.register(collections=[cid])["key"])
    invalid = world.client.post(f"/api/v1/collections/{cid}/search", json={"query": ""}, headers=headers)
    unknown_field = world.client.post(f"/api/v1/collections/{cid}/search", json={"query": "x", "nope": 1}, headers=headers)
    missing = world.client.get("/api/v1/no/such/path", headers=headers)
    for response, code in ((invalid, "validation_error"), (unknown_field, "validation_error"), (missing, "not_found")):
        assert response.json()["code"] == code
        assert "detail" in response.json()

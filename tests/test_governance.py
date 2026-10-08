"""Content governance end to end: attribution, audit trail, labels,
quarantine, the hash blocklist, the AUP gate, and admin review.

Layers covered:
- app_db (SQLite) — the new tables and columns
- MetadataStore — governance columns, migration, hidden-document queries
- DocumentIndexer — hash/blocklist refusal, policy decision on ingest,
  quarantined documents never returned by search
- HTTP — open mode (upload/list/report/label/admin) and private mode
  (AUP gate, admin gating, restricted-collection rules, MCP scope)

Embeddings are faked (deterministic vectors) so no model loads.
"""

import hashlib
import importlib
import sqlite3

import numpy as np
import pytest
from fastapi.testclient import TestClient

import config
from config import settings as _import_time_settings
from services import governance
from services.app_database import SQLiteBackend, app_db
from services.chunker import TextChunker
from services.collection_service import collection_service
from services.content_policy import BlockedContentError, ContentRejectedError
from services.document_extractor import DocumentExtractor
from services.indexer_manager import indexer_manager
from services.indexing import DocumentIndexer
from services.metadata_store import MetadataStore
from services.vector_store import VectorStore
from models.schemas import SearchMode
from services.metadata_store import SCHEMA_VERSION

EMBED_DIM = 8
ADMIN = "admin@example.com"
USER = "user@example.com"

BENIGN = (
    "Section one of the handbook explains the holiday policy: staff accrue "
    "25 days a year and may carry five over. Section two covers expenses."
)
DRUG_SALE = (
    "Selling top-grade cocaine and fentanyl, stealth shipping worldwide, "
    "price list on request, escrow in monero accepted."
)


class FakeEmbedder:
    model_name = "fake-embed"
    embedding_dim = EMBED_DIM

    @staticmethod
    def _vec(text: str) -> list:
        digest = hashlib.sha256(text.encode()).digest()
        return [b / 255.0 + 0.01 for b in digest[:EMBED_DIM]]

    def embed_texts(self, texts, progress_callback=None):
        if progress_callback:
            progress_callback(len(texts), len(texts))
        return np.array([self._vec(t) for t in texts], dtype=np.float32)

    def embed_query(self, query):
        return np.array(self._vec(query), dtype=np.float32)


def _make_indexer(tmp_path):
    return DocumentIndexer(
        vector_store=VectorStore(index_dir=tmp_path / "indexes", embedding_dim=EMBED_DIM),
        embedding_service=FakeEmbedder(),
        document_extractor=DocumentExtractor(),
        text_chunker=TextChunker(chunk_size=200, chunk_overlap=40),
    )


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ── Shared fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def fresh_db(tmp_path):
    old_db_path = app_db.db_path
    old_base_dir = collection_service.base_dir
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    collection_service.base_dir = tmp_path / "collections"
    yield app_db
    app_db.db_path = old_db_path
    collection_service.base_dir = old_base_dir


@pytest.fixture()
def indexer(tmp_path, fresh_db, monkeypatch):
    """A fake-embedding indexer registered as the 'default' collection's."""
    ix = _make_indexer(tmp_path)
    monkeypatch.setitem(indexer_manager._indexers, "default", ix)
    from api import deps

    monkeypatch.setattr(deps, "_initialized", True)
    return ix


@pytest.fixture()
def policy(monkeypatch):
    """Set CONTENT_POLICY_ACTION on every Settings object in play."""
    def _set(action):
        for s in _settings_objects():
            monkeypatch.setattr(s, "content_policy_action", action)
    return _set


def _settings_objects():
    objs = {id(_import_time_settings): _import_time_settings}
    objs.setdefault(id(config.settings), config.settings)
    return list(objs.values())


# ── app_db layer ────────────────────────────────────────────────────────────


def test_audit_roundtrip_and_filters(fresh_db):
    app_db.add_audit_event("alice", "document.upload", collection_id="c1", detail='{"n": 1}')
    app_db.add_audit_event("bob", "share.create", collection_id="c1", target="x@y.z")
    app_db.add_audit_event("alice", "document.delete", collection_id="c2", document_id="d9")

    rows = app_db.list_audit_events()
    assert [r["action"] for r in rows] == ["document.delete", "share.create", "document.upload"]
    assert [r["actor"] for r in app_db.list_audit_events(actor="alice")] == ["alice", "alice"]
    assert len(app_db.list_audit_events(action="share.create")) == 1
    assert len(app_db.list_audit_events(collection_id="c2")) == 1
    assert app_db.list_audit_events(document_id="d9")[0]["target"] is None

    from services import audit

    events = audit.list_events(action="document.upload")
    assert events[0]["detail"] == {"n": 1}
    csv = audit.to_csv(events)
    assert "document.upload" in csv and csv.startswith("id,timestamp,actor")

    # Nothing is old enough to sweep; a zero-day window sweeps everything.
    assert app_db.delete_old_audit_events(365) == 0
    assert app_db.delete_old_audit_events(0) == 3


def test_audit_record_never_raises(fresh_db, monkeypatch):
    from services import audit

    monkeypatch.setattr(app_db, "add_audit_event", lambda **kw: (_ for _ in ()).throw(RuntimeError("db down")))
    audit.record("document.upload", actor="x")  # must not raise


def test_aup_acknowledgements(fresh_db):
    assert app_db.get_aup_acknowledgement(USER, "1") is None
    ack = app_db.record_aup_acknowledgement(USER, "1")
    assert ack["user_id"] == USER
    assert app_db.get_aup_acknowledgement(USER, "1")["accepted_at"] == ack["accepted_at"]
    assert app_db.get_aup_acknowledgement(USER, "2") is None  # new version re-prompts
    assert [a["user_id"] for a in app_db.list_aup_acknowledgements()] == [USER]


def test_blocked_hashes(fresh_db):
    h = "a" * 64
    assert not app_db.is_hash_blocked(h)
    row = app_db.add_blocked_hash(h, ADMIN, "bad file", "bad.pdf")
    assert row["filename"] == "bad.pdf"
    assert app_db.is_hash_blocked(h)
    assert app_db.list_blocked_hashes()[0]["blocked_by"] == ADMIN
    assert app_db.remove_blocked_hash(h) is True
    assert app_db.remove_blocked_hash(h) is False
    assert not app_db.is_hash_blocked(h)


def test_collection_sensitivity_column(fresh_db):
    cid = app_db.create_collection(name="labelled", owner_id="default")
    assert app_db.get_collection(cid)["sensitivity"] == "internal"
    app_db.update_collection(cid, sensitivity="restricted")
    assert app_db.get_collection(cid)["sensitivity"] == "restricted"


def test_mcp_token_scope_and_bulk_revoke(fresh_db):
    t1 = app_db.create_mcp_token(USER, "laptop", "h1", "asy_mcp_aaaaaa", collection_scope=["c1", "c2"])
    t2 = app_db.create_mcp_token(USER, "phone", "h2", "asy_mcp_bbbbbb")
    assert t1["collection_scope"] == ["c1", "c2"]
    assert t2["collection_scope"] is None
    assert app_db.get_mcp_token_by_hash("h1")["collection_scope"] == ["c1", "c2"]
    assert app_db.list_mcp_tokens(USER)[0]["collection_scope"] in (["c1", "c2"], None)

    assert app_db.revoke_all_mcp_tokens_for_user(USER) == 2
    assert app_db.revoke_all_mcp_tokens_for_user(USER) == 0
    assert all(t["revoked_at"] for t in app_db.list_mcp_tokens(USER))


# ── MetadataStore ───────────────────────────────────────────────────────────


def test_metadata_governance_columns(tmp_path):
    s = MetadataStore(tmp_path / "m.db")
    s.add_document("d1", "a.txt", 1, 1, "2026-01-01T00:00:00", uploaded_by=USER,
                   content_hash="f" * 64)
    s.add_document("d2", "b.txt", 1, 1, "2026-01-02T00:00:00", policy_status="quarantined",
                   policy_flags={"categories": {"drug_trade": 2}})
    s.add_document("d3", "c.txt", 1, 1, "2026-01-03T00:00:00", policy_status="flagged")

    info = s.get_document_info("d1")
    assert info["uploaded_by"] == USER
    assert info["content_hash"] == "f" * 64
    assert info["policy_status"] == "clear"
    assert s.get_document_info("d2")["policy_flags"] == {"categories": {"drug_trade": 2}}

    assert s.get_hidden_document_ids() == {"d2"}
    queue = s.list_documents_by_policy_status(("flagged", "quarantined"))
    assert {d["document_id"] for d in queue} == {"d2", "d3"}
    assert set(s.list_documents_page(10)[0].keys()) == set(s.list_documents()[0].keys())

    s.set_document_governance("d2", policy_status="approved")
    assert s.get_hidden_document_ids() == set()
    s.set_document_governance("d1", sensitivity="restricted")
    assert s.get_document_info("d1")["sensitivity"] == "restricted"
    s.set_document_governance("d1", sensitivity=None)
    assert s.get_document_info("d1")["sensitivity"] is None
    assert s.get_documents_info(["d1"])["d1"]["policy_status"] == "clear"


def test_metadata_store_migrates_pre_governance_db(tmp_path):
    """A v3.2 metadata.db (no governance columns) gains them on open, with
    existing rows reading as clear + unattributed."""
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_info (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_info VALUES ('version', '3.2')")
        conn.execute("""CREATE TABLE chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, chunk_id TEXT UNIQUE NOT NULL,
            document_id TEXT NOT NULL, filename TEXT NOT NULL, page_number INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL, text TEXT NOT NULL, created_at TIMESTAMP,
            source_format TEXT, extraction_method TEXT, csv_row_number INTEGER, csv_columns TEXT, csv_values TEXT)""")
        conn.execute("""CREATE TABLE documents (document_id TEXT PRIMARY KEY, filename TEXT NOT NULL,
            num_pages INTEGER NOT NULL, num_chunks INTEGER NOT NULL, upload_timestamp TEXT NOT NULL,
            source_format TEXT, extraction_method TEXT, embedding_model TEXT, chunk_size INTEGER,
            chunk_overlap INTEGER, schema_version TEXT, source_path TEXT, source_type TEXT,
            injection_warnings TEXT)""")
        conn.execute("INSERT INTO documents (document_id, filename, num_pages, num_chunks, upload_timestamp) VALUES ('old', 'old.pdf', 1, 1, '2025-01-01')")
    s = MetadataStore(path)
    assert s.get_schema_version() == SCHEMA_VERSION
    old = s.get_document_info("old")
    assert old["policy_status"] == "clear"
    assert old["uploaded_by"] is None
    assert s.get_hidden_document_ids() == set()


# ── governance helpers ──────────────────────────────────────────────────────


def test_sensitivity_helpers():
    assert governance.normalize_sensitivity("Restricted") == "restricted"
    assert governance.normalize_sensitivity("", allow_none=True) is None
    with pytest.raises(ValueError):
        governance.normalize_sensitivity("secret")
    col = {"id": "c1", "sensitivity": "confidential"}
    assert governance.effective_sensitivity(col, None) == "confidential"
    assert governance.effective_sensitivity(col, "public") == "public"
    assert governance.effective_sensitivity({}, None) == "internal"
    restricted = {"id": "r1", "sensitivity": "restricted"}
    assert governance.mcp_can_expose(col, None)
    assert not governance.mcp_can_expose(restricted, None)
    assert not governance.mcp_can_expose(restricted, ["other"])
    assert governance.mcp_can_expose(restricted, ["r1"])


def test_aup_gate_is_private_mode_only(monkeypatch):
    for s in _settings_objects():
        monkeypatch.setattr(s, "aup_required", True)
        monkeypatch.setattr(s, "private_collections", False)
    assert not governance.aup_required_for(USER)
    for s in _settings_objects():
        monkeypatch.setattr(s, "private_collections", True)
    assert governance.aup_required_for(USER)
    assert not governance.aup_required_for(None)  # anonymous: nothing to record against
    assert "Acceptable use" in governance.aup_text()


# ── Indexer integration ─────────────────────────────────────────────────────


def test_ingest_records_attribution_and_hash(tmp_path, indexer, policy):
    policy("flag")
    p = _write(tmp_path, "handbook.txt", BENIGN)
    meta = indexer.index_document(p, "handbook.txt", collection_id="default", uploaded_by=USER)
    assert meta.uploaded_by == USER
    assert meta.policy_status == "clear"
    assert meta.content_hash == hashlib.sha256(BENIGN.encode()).hexdigest()
    assert meta.document_id == meta.content_hash[:16]
    row = indexer.vector_store.metadata_store.get_document_info(meta.document_id)
    assert row["uploaded_by"] == USER and row["content_hash"] == meta.content_hash


def test_flag_mode_indexes_and_still_searches(tmp_path, indexer, policy):
    policy("flag")
    p = _write(tmp_path, "listing.txt", DRUG_SALE)
    meta = indexer.index_document(p, "listing.txt", collection_id="default", uploaded_by=USER)
    assert meta.policy_status == "flagged"
    assert meta.policy_flags["categories"].get("drug_trade")
    hits = indexer.search("cocaine shipping", top_k=5)["results"]
    assert any(r.document_id == meta.document_id for r in hits)
    assert [e["action"] for e in app_db.list_audit_events(document_id=meta.document_id)] == ["document.flagged"]


def test_quarantine_hides_until_approved(tmp_path, indexer, policy):
    policy("quarantine")
    p = _write(tmp_path, "listing.txt", DRUG_SALE)
    meta = indexer.index_document(p, "listing.txt", collection_id="default", uploaded_by=USER)
    assert meta.policy_status == "quarantined"
    assert indexer.search("cocaine shipping", top_k=5)["results"] == []
    assert indexer.search("cocaine shipping", top_k=5, mode=SearchMode.KEYWORD)["results"] == []

    governance.set_policy_status("default", meta.document_id, "approved", actor=ADMIN)
    hits = indexer.search("cocaine shipping", top_k=5)["results"]
    assert any(r.document_id == meta.document_id for r in hits)
    actions = [e["action"] for e in app_db.list_audit_events(document_id=meta.document_id)]
    assert actions == ["document.approved", "document.quarantined"]


def test_reject_mode_refuses_and_stores_nothing(tmp_path, indexer, policy):
    policy("reject")
    p = _write(tmp_path, "listing.txt", DRUG_SALE)
    with pytest.raises(ContentRejectedError):
        indexer.index_document(p, "listing.txt", collection_id="default", uploaded_by=USER)
    assert indexer.list_documents() == []
    assert app_db.list_audit_events(action="document.rejected")


def test_blocklist_refuses_reupload(tmp_path, indexer, policy):
    policy("flag")
    p = _write(tmp_path, "handbook.txt", BENIGN)
    meta = indexer.index_document(p, "handbook.txt", collection_id="default", uploaded_by=USER)
    collection_service.add_document("default", meta.document_id)

    outcome = governance.remove_document("default", meta.document_id, block=True,
                                         reason="not ours", actor=ADMIN, action="admin.remove_document")
    assert outcome["blocked"] is True
    assert app_db.is_hash_blocked(meta.content_hash)
    assert indexer.list_documents() == []

    again = _write(tmp_path, "renamed.txt", BENIGN)
    with pytest.raises(BlockedContentError):
        indexer.index_document(again, "renamed.txt", collection_id="default", uploaded_by=USER)
    actions = {e["action"] for e in app_db.list_audit_events()}
    assert {"admin.block_hash", "admin.remove_document", "document.blocked"} <= actions


def test_prepare_document_path_applies_policy(tmp_path, indexer, policy):
    policy("quarantine")
    p = _write(tmp_path, "listing.txt", DRUG_SALE)
    prepared = indexer.prepare_document(p, "listing.txt", collection_id="default", uploaded_by=USER)
    assert prepared.policy_status == "quarantined"
    assert prepared.uploaded_by == USER
    metas = indexer.index_prepared_documents([prepared])
    assert metas[0].policy_status == "quarantined"
    assert indexer.vector_store.metadata_store.get_hidden_document_ids() == {prepared.document_id}


def test_policy_off_skips_scan(tmp_path, indexer, policy):
    policy("off")
    p = _write(tmp_path, "listing.txt", DRUG_SALE)
    meta = indexer.index_document(p, "listing.txt", collection_id="default", uploaded_by=USER)
    assert meta.policy_status == "clear" and meta.policy_flags is None


# ── HTTP: open (shared-appliance) mode ──────────────────────────────────────


@pytest.fixture()
def open_client(indexer):
    import main

    return TestClient(main.app)


def test_http_upload_lists_attribution_and_labels(open_client, policy):
    policy("flag")
    r = open_client.post("/documents/upload", files={"files": ("h.txt", BENIGN.encode(), "text/plain")})
    assert r.status_code == 201, r.text
    doc_id = r.json()["document_ids"][0]

    docs = open_client.get("/documents").json()["documents"]
    row = next(d for d in docs if d["document_id"] == doc_id)
    assert row["uploaded_by"] == "default"
    assert row["policy_status"] == "clear"
    assert row["sensitivity"] is None
    assert row["sensitivity_effective"] == "internal"
    assert row["content_hash"]

    patched = open_client.patch(f"/documents/{doc_id}/governance", json={"sensitivity": "confidential"})
    assert patched.status_code == 200
    row = next(d for d in open_client.get("/documents").json()["documents"] if d["document_id"] == doc_id)
    assert row["sensitivity_effective"] == "confidential"
    assert open_client.patch(f"/documents/{doc_id}/governance", json={"sensitivity": "top-secret"}).status_code == 400

    s = open_client.post("/search", json={"query": "holiday policy", "top_k": 3}).json()
    assert s["results"][0]["sensitivity"] == "confidential"

    assert open_client.post("/documents/upload", files={"files": ("h.txt", BENIGN.encode(), "text/plain")}).status_code == 201
    assert app_db.list_audit_events(action="document.upload")


def test_http_quarantine_review_flow(open_client, policy):
    policy("quarantine")
    r = open_client.post("/documents/upload", files={"files": ("l.txt", DRUG_SALE.encode(), "text/plain")})
    assert r.status_code == 201
    doc_id = r.json()["document_ids"][0]

    assert open_client.post("/search", json={"query": "cocaine shipping", "top_k": 3}).json()["results"] == []

    review = open_client.get("/api/admin/review").json()
    assert [d["document_id"] for d in review["quarantined"]] == [doc_id]
    assert review["content_policy_action"] == "quarantine"

    assert open_client.post(f"/api/admin/documents/default/{doc_id}/approve", json={"note": "threat intel"}).status_code == 200
    hits = open_client.post("/search", json={"query": "cocaine shipping", "top_k": 3}).json()["results"]
    assert hits and hits[0]["document_id"] == doc_id
    assert open_client.get("/api/admin/review").json()["quarantined"] == []


def test_http_reject_mode_returns_422(open_client, policy):
    policy("reject")
    r = open_client.post("/documents/upload", files={"files": ("l.txt", DRUG_SALE.encode(), "text/plain")})
    assert r.status_code == 422
    assert "content policy" in r.text


def test_http_admin_remove_and_block(open_client, policy):
    policy("flag")
    doc_id = open_client.post("/documents/upload", files={"files": ("h.txt", BENIGN.encode(), "text/plain")}).json()["document_ids"][0]
    r = open_client.post(f"/api/admin/documents/default/{doc_id}/remove", json={"block": True, "reason": "test"})
    assert r.status_code == 200 and r.json()["blocked"] is True
    blocked = open_client.get("/api/admin/blocked-hashes").json()["hashes"]
    assert len(blocked) == 1 and blocked[0]["filename"] == "h.txt"

    again = open_client.post("/documents/upload", files={"files": ("h2.txt", BENIGN.encode(), "text/plain")})
    assert again.status_code == 422 and "blocked" in again.text

    h = blocked[0]["content_hash"]
    assert open_client.delete(f"/api/admin/blocked-hashes/{h}").status_code == 200
    assert open_client.post("/documents/upload", files={"files": ("h2.txt", BENIGN.encode(), "text/plain")}).status_code == 201

    assert open_client.post("/api/admin/blocked-hashes", json={"content_hash": "zz"}).status_code == 400
    assert open_client.post("/api/admin/blocked-hashes", json={"content_hash": "b" * 64, "reason": "org list"}).status_code == 201


def test_http_report_and_audit_export(open_client, policy):
    policy("flag")
    doc_id = open_client.post("/documents/upload", files={"files": ("h.txt", BENIGN.encode(), "text/plain")}).json()["document_ids"][0]
    r = open_client.post(f"/documents/{doc_id}/report", json={"reason": "looks stolen"})
    assert r.status_code == 200 and r.json()["reported"] is True
    review = open_client.get("/api/admin/review").json()
    assert review["reports"][0]["detail"]["reason"] == "looks stolen"

    audit = open_client.get("/api/admin/audit?action=document.reported").json()
    assert audit["events"][0]["document_id"] == doc_id
    assert "document.reported" in audit["actions"]
    csv = open_client.get("/api/admin/audit?format=csv")
    assert csv.status_code == 200 and "document.reported" in csv.text
    assert open_client.post(f"/documents/nope/report", json={}).status_code == 404


def test_http_delete_is_audited(open_client, policy):
    policy("flag")
    doc_id = open_client.post("/documents/upload", files={"files": ("h.txt", BENIGN.encode(), "text/plain")}).json()["document_ids"][0]
    r = open_client.delete(f"/documents/{doc_id}")
    assert r.status_code == 200 and r.json()["chunks_deleted"] > 0
    assert app_db.list_audit_events(action="document.delete")[0]["document_id"] == doc_id


def test_collection_sensitivity_update_and_share_refusal(open_client, fresh_db):
    cid = open_client.post("/api/collections", json={"name": "secret stuff"}).json()["id"]
    assert open_client.put(f"/api/collections/{cid}", json={"sensitivity": "top"}).status_code == 400
    r = open_client.put(f"/api/collections/{cid}", json={"sensitivity": "restricted"})
    assert r.status_code == 200 and r.json()["sensitivity"] == "restricted"
    assert app_db.list_audit_events(action="collection.sensitivity")[0]["detail"]

    from services.sharing_service import sharing_service

    with pytest.raises(PermissionError, match="restricted"):
        sharing_service.create_share(cid, "default")


def test_mcp_hides_restricted_unless_token_scoped(fresh_db, monkeypatch):
    from services import mcp_server as mcp

    cid = app_db.create_collection(name="vault", owner_id="default")
    app_db.update_collection(cid, sensitivity="restricted")
    collection_service._ensure_collection_dirs(cid)

    assert cid not in {c["id"] for c in mcp._visible_collections()}
    with pytest.raises(ValueError, match="not found"):
        mcp._resolve_collection_id(cid)

    token = mcp._request_mcp_token_scope.set([cid])
    try:
        assert cid in {c["id"] for c in mcp._visible_collections()}
        assert mcp._resolve_collection_id(cid) == cid
    finally:
        mcp._request_mcp_token_scope.reset(token)


def test_mcp_token_scope_must_be_visible_collection(open_client, fresh_db):
    r = open_client.post("/api/mcp/tokens", json={"name": "x", "collection_scope": ["does-not-exist"]})
    assert r.status_code == 404
    cid = app_db.create_collection(name="vault", owner_id="default")
    r = open_client.post("/api/mcp/tokens", json={"name": "x", "collection_scope": [cid]})
    assert r.status_code == 200 and r.json()["collection_scope"] == [cid]
    from services import audit

    assert audit.list_events(action="mcp_token.create")[0]["detail"]["collection_scope"] == [cid]


# ── HTTP: private mode (identity, AUP gate, admin gating) ───────────────────


class _FakeVerifier:
    def verify(self, token):
        return {"email": token} if token and "@" in token else None

    @staticmethod
    def identity_from_claims(claims):
        return claims.get("email")


@pytest.fixture()
def private_client(indexer, monkeypatch):
    import services.access_jwt as access_jwt
    import main

    saved = []
    for s in _settings_objects():
        saved.append((s, s.private_collections, s.cf_access_team_domain,
                      s.cf_access_aud, s.admin_emails, s.aup_required))
        s.private_collections = True
        s.cf_access_team_domain = "testteam.cloudflareaccess.com"
        s.cf_access_aud = "aud-test"
        s.admin_emails = ADMIN
        s.aup_required = True
    monkeypatch.setattr(access_jwt, "get_verifier", lambda: _FakeVerifier())
    reloaded = importlib.reload(main)
    yield TestClient(reloaded.app)
    for s, private, team, aud, admins, aup in saved:
        s.private_collections = private
        s.cf_access_team_domain = team
        s.cf_access_aud = aud
        s.admin_emails = admins
        s.aup_required = aup
    importlib.reload(main)


def _as(email):
    return {"cf-access-jwt-assertion": email}


def test_aup_gate_blocks_ingest_until_accepted(private_client, policy):
    policy("flag")
    me = private_client.get("/api/user/me", headers=_as(USER)).json()
    assert me["aup"] == {"enabled": True, "required": True, "version": "1", "accepted": False, "accepted_at": None}

    r = private_client.post("/documents/upload", files={"files": ("h.txt", BENIGN.encode(), "text/plain")}, headers=_as(USER))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "aup_required"
    assert private_client.post("/documents/upload-staged", files={"files": ("h.txt", b"x", "text/plain")}, headers=_as(USER)).status_code == 403

    text = private_client.get("/api/aup", headers=_as(USER)).json()
    assert "Acceptable use" in text["text"] and text["required"]
    acc = private_client.post("/api/aup/accept", headers=_as(USER))
    assert acc.status_code == 200 and acc.json()["accepted"]

    r = private_client.post("/documents/upload", files={"files": ("h.txt", BENIGN.encode(), "text/plain")}, headers=_as(USER))
    assert r.status_code == 201, r.text
    doc_id = r.json()["document_ids"][0]
    row = next(d for d in private_client.get("/documents", headers=_as(USER)).json()["documents"] if d["document_id"] == doc_id)
    assert row["uploaded_by"] == USER

    acks = private_client.get("/api/admin/aup-acknowledgements", headers=_as(ADMIN)).json()
    assert acks["acknowledgements"][0]["user_id"] == USER
    assert app_db.list_audit_events(action="aup.accept")[0]["actor"] == USER


def test_admin_governance_endpoints_are_admin_only(private_client):
    for path in ("/api/admin/review", "/api/admin/audit", "/api/admin/blocked-hashes",
                 "/api/admin/aup-acknowledgements"):
        assert private_client.get(path, headers=_as(USER)).status_code == 403, path
        assert private_client.get(path, headers=_as(ADMIN)).status_code == 200, path
    assert private_client.post("/api/admin/users/x@y.z/suspend", headers=_as(USER)).status_code == 403
    r = private_client.post(f"/api/admin/users/{USER}/suspend", json={"reason": "abuse"}, headers=_as(ADMIN))
    assert r.status_code == 200 and r.json()["tokens_revoked"] == 0
    assert private_client.post(f"/api/admin/users/{ADMIN}/suspend", headers=_as(ADMIN)).status_code == 400
    assert app_db.list_audit_events(action="admin.suspend_user")[0]["target"] == USER


def test_quarantined_document_is_404_for_non_admin(private_client, policy):
    policy("quarantine")
    app_db.record_aup_acknowledgement(USER, "1")
    r = private_client.post("/documents/upload", files={"files": ("l.txt", DRUG_SALE.encode(), "text/plain")}, headers=_as(USER))
    assert r.status_code == 201
    doc_id = r.json()["document_ids"][0]
    # Still listed in the sidebar (the uploader should see the hold)...
    docs = private_client.get("/documents", headers=_as(USER)).json()["documents"]
    assert next(d for d in docs if d["document_id"] == doc_id)["policy_status"] == "quarantined"
    # ...but not servable to anyone but a reviewer.
    assert private_client.get(f"/documents/{doc_id}/chunks", headers=_as(USER)).status_code == 404
    assert private_client.get(f"/documents/{doc_id}/chunks", headers=_as(ADMIN)).status_code == 200
    # And a user cannot clear their own hold.
    assert private_client.post(f"/api/admin/documents/default/{doc_id}/approve", headers=_as(USER)).status_code == 403


def test_injection_flagged_pages_index(tmp_path, indexer, policy, monkeypatch):
    """Extractors key injection warnings by int page; DocumentMetadata needs str keys."""
    from services.document_extractor import ExtractionResult
    from services.prompt_injection_detector import InjectionScanResult

    policy("off")
    flagged = InjectionScanResult(is_flagged=True, risk_score=0.9, summary="ignore previous instructions")
    monkeypatch.setattr(
        indexer.document_extractor, "extract_text",
        lambda path: ExtractionResult({1: BENIGN, 2: BENIGN + " again"}, injection_warnings={2: flagged}),
    )
    p = _write(tmp_path, "handbook.txt", BENIGN)
    meta = indexer.index_document(p, "handbook.txt", collection_id="default")
    assert list(meta.injection_warnings) == ["2"]

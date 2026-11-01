"""Agents adding and updating sources over MCP: the write_document tool,
the per-token write grant, and the gates a write must pass.

Embeddings are faked (deterministic vectors) so no model loads.
"""

import hashlib
import importlib

import numpy as np
import pytest
from fastapi.testclient import TestClient

import services.mcp_server as mcp
from services import mcp_tokens
from services.app_database import SQLiteBackend, app_db
from services.chunker import TextChunker
from services.collection_service import collection_service
from services.document_extractor import DocumentExtractor
from services.indexer_manager import indexer_manager
from services.indexing import DocumentIndexer
from services.vector_store import VectorStore
from models.schemas import SearchMode

EMBED_DIM = 8

NOTES = (
    "# Sprint notes\n\nThe deployment runbook moved to the wiki. Backups run "
    "nightly at 02:00 and are verified by the on-call engineer every Monday.\n"
)
MORE = "\n## Follow-ups\n\n- Rotate the tunnel credentials before the audit.\n"


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
    ix = DocumentIndexer(
        vector_store=VectorStore(index_dir=tmp_path / "indexes", embedding_dim=EMBED_DIM),
        embedding_service=FakeEmbedder(),
        document_extractor=DocumentExtractor(),
        text_chunker=TextChunker(chunk_size=200, chunk_overlap=40),
    )
    monkeypatch.setitem(indexer_manager._indexers, "default", ix)
    # The developer .env may pin another default collection; tests that omit
    # collection_id must land in the fixture's 'default'.
    monkeypatch.setattr(mcp.settings, "mcp_default_collection", "default")
    from api import deps

    monkeypatch.setattr(deps, "_initialized", True)
    return ix


def _filenames(indexer):
    return {d["filename"] for d in indexer.list_documents()}


# ── write_document: create / replace / append ───────────────────────────────


def test_create_writes_file_indexes_and_audits(indexer):
    result = mcp.write_document_sync("sprint-notes", NOTES, collection_id="default")

    assert result["status"] == "created"
    assert result["filename"] == "sprint-notes.md"          # bare name → .md
    assert result["total_chunks"] >= 1
    doc_id = result["document_id"]

    stored = indexer_manager.get_documents_path("default") / "sprint-notes.md"
    assert stored.read_text(encoding="utf-8") == NOTES

    assert "sprint-notes.md" in _filenames(indexer)
    assert doc_id in collection_service.get_collection_document_ids("default")

    hits = indexer.search("when do backups run", top_k=3, mode=SearchMode.KEYWORD)["results"]
    assert any(r.document_id == doc_id for r in hits)

    from services import audit

    event = audit.list_events(action="document.upload")[0]
    assert event["detail"]["via"] == "mcp"
    assert event["detail"]["mode"] == "create"
    assert event["document_id"] == doc_id


def test_create_refuses_existing_filename(indexer):
    mcp.write_document_sync("notes.md", NOTES)
    with pytest.raises(ValueError, match="mode='replace'"):
        mcp.write_document_sync("notes.md", "something else entirely")


def test_replace_retires_old_document(indexer):
    first = mcp.write_document_sync("notes.md", NOTES)
    second = mcp.write_document_sync("notes.md", "Completely new text about lunch menus.", mode="replace")

    assert second["status"] == "replaced"
    assert second["replaced_document_id"] == first["document_id"]
    assert second["document_id"] != first["document_id"]

    store = indexer.vector_store.metadata_store
    assert store.get_document_info(first["document_id"]) is None
    assert store.get_document_info(second["document_id"])["filename"] == "notes.md"
    assert first["document_id"] not in collection_service.get_collection_document_ids("default")

    stored = indexer_manager.get_documents_path("default") / "notes.md"
    assert "lunch menus" in stored.read_text(encoding="utf-8")


def test_append_extends_existing_content(indexer):
    first = mcp.write_document_sync("notes.md", NOTES)
    result = mcp.write_document_sync("notes.md", MORE, mode="append")

    assert result["status"] == "appended"
    assert result["replaced_document_id"] == first["document_id"]
    stored = (indexer_manager.get_documents_path("default") / "notes.md").read_text(encoding="utf-8")
    assert stored.startswith(NOTES)
    assert stored.rstrip().endswith("before the audit.")

    hits = indexer.search("tunnel credentials", top_k=3, mode=SearchMode.KEYWORD)["results"]
    assert any(r.document_id == result["document_id"] for r in hits)


def test_append_without_existing_creates(indexer):
    result = mcp.write_document_sync("fresh.md", NOTES, mode="append")
    assert result["status"] == "created"
    assert "replaced_document_id" not in result


def test_duplicate_content_under_another_name_is_reported(indexer):
    first = mcp.write_document_sync("a.md", NOTES)
    dup = mcp.write_document_sync("b.md", NOTES)

    assert dup["status"] == "duplicate"
    assert dup["existing_filename"] == "a.md"
    assert dup["document_id"] == first["document_id"]
    assert not (indexer_manager.get_documents_path("default") / "b.md").exists()
    assert _filenames(indexer) == {"a.md"}


# ── Input validation ────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["", "   ", "..", "../escape.md", "notes.pdf", "photo.png", "a\nb.md"])
def test_bad_filenames_are_refused(indexer, bad):
    with pytest.raises(ValueError):
        mcp.write_document_sync(bad, NOTES)
    assert _filenames(indexer) == set()


def test_folder_separators_flatten_like_uploads(indexer):
    result = mcp.write_document_sync("research/2026/summary.md", NOTES)
    assert result["filename"] == "research_2026_summary.md"


def test_empty_and_oversized_content_refused(indexer, monkeypatch):
    with pytest.raises(ValueError, match="empty"):
        mcp.write_document_sync("x.md", "   \n")
    monkeypatch.setattr(mcp, "_WRITE_MAX_CHARS", 20)
    with pytest.raises(ValueError, match="limit"):
        mcp.write_document_sync("x.md", "x" * 21)


def test_bad_mode_refused(indexer):
    with pytest.raises(ValueError, match="mode must be"):
        mcp.write_document_sync("x.md", NOTES, mode="upsert")


def test_unknown_collection_refused(indexer):
    with pytest.raises(ValueError, match="not found"):
        mcp.write_document_sync("x.md", NOTES, collection_id="nope")


# ── Gates: token write flag, read-only share, AUP ───────────────────────────


def test_read_only_token_cannot_write(indexer):
    token = mcp._request_mcp_can_write.set(False)
    try:
        with pytest.raises(ValueError, match="read-only"):
            mcp.write_document_sync("x.md", NOTES)
    finally:
        mcp._request_mcp_can_write.reset(token)
    assert _filenames(indexer) == set()


def test_read_only_share_cannot_write(indexer, monkeypatch):
    from services.sharing_service import sharing_service

    monkeypatch.setattr(sharing_service, "check_collection_access", lambda cid, uid: "read")
    with pytest.raises(ValueError, match="read-only"):
        mcp.write_document_sync("x.md", NOTES)


def test_aup_gate_applies_to_agent_writes(indexer, monkeypatch):
    from fastapi import HTTPException
    from services import governance

    def _refuse(user_id):
        raise HTTPException(status_code=403, detail={"code": "aup_required",
                                                     "message": "Accept the policy first."})

    monkeypatch.setattr(governance, "require_aup", _refuse)
    with pytest.raises(ValueError, match="Accept the policy first"):
        mcp.write_document_sync("x.md", NOTES)


def test_blocked_hash_is_refused_and_leaves_no_file(indexer):
    digest = hashlib.sha256(NOTES.encode("utf-8")).hexdigest()
    app_db.add_blocked_hash(digest, "admin", "test", "old.md")

    with pytest.raises(ValueError, match="cannot be re-added"):
        mcp.write_document_sync("again.md", NOTES)
    assert not (indexer_manager.get_documents_path("default") / "again.md").exists()
    assert _filenames(indexer) == set()


# ── The async MCP wrapper and tool registration ─────────────────────────────


def test_async_tool_wraps_sync_and_is_registered(indexer):
    import asyncio

    result = asyncio.run(mcp.write_document("via-tool.md", NOTES, collection_id="default"))
    assert result["status"] == "created"

    names = {t.name for t in mcp._clio_mcp._tool_manager.list_tools()}
    assert "write_document" in names
    assert not any(n.startswith("delete") for n in names)


# ── Token write flag: DB, service, API, middleware ───────────────────────────


def test_token_can_write_roundtrip(fresh_db):
    ro = app_db.create_mcp_token("alice", "reader", "h-ro", "asy_mcp_aaaaaa")
    rw = app_db.create_mcp_token("alice", "writer", "h-rw", "asy_mcp_bbbbbb", can_write=True)
    assert ro["can_write"] is False
    assert rw["can_write"] is True
    assert app_db.get_mcp_token_by_hash("h-ro")["can_write"] is False
    assert app_db.get_mcp_token_by_hash("h-rw")["can_write"] is True
    by_id = {t["id"]: t for t in app_db.list_mcp_tokens("alice")}
    assert by_id[ro["id"]]["can_write"] is False
    assert by_id[rw["id"]]["can_write"] is True


def test_generate_token_defaults_read_only(fresh_db):
    record = mcp_tokens.generate_token("alice", "laptop")
    assert record["can_write"] is False
    verified = mcp_tokens.verify_token(record["token"])
    assert verified["can_write"] is False

    writer = mcp_tokens.generate_token("alice", "agent", can_write=True)
    assert mcp_tokens.verify_token(writer["token"])["can_write"] is True


@pytest.fixture()
def open_client(indexer):
    import main

    return TestClient(main.app)


def test_token_api_accepts_can_write(open_client, fresh_db):
    r = open_client.post("/api/mcp/tokens", json={"name": "agent", "can_write": True})
    assert r.status_code == 201, r.text
    assert r.json()["can_write"] is True
    listed = open_client.get("/api/mcp/tokens").json()["tokens"]
    assert listed[0]["can_write"] is True

    r = open_client.post("/api/mcp/tokens", json={"name": "reader"})
    assert r.json()["can_write"] is False

    from services import audit

    assert audit.list_events(action="mcp_token.create")[0]["detail"]["can_write"] is False


def test_middleware_records_write_flag_on_request_state(tmp_path, monkeypatch):
    import asyncio

    import config
    import main
    from services import app_database
    from starlette.requests import Request

    monkeypatch.setenv("AUTH_PASSWORD", "deploy-secret")
    importlib.reload(config)
    main = importlib.reload(main)
    try:
        monkeypatch.setattr(app_database.app_db, "db_path", tmp_path / "app.db")
        app_database.app_db._init_db()

        async def downstream(request):
            return "ok"

        def _request(token):
            return Request({
                "type": "http", "method": "POST", "path": "/mcp/",
                "headers": [(b"authorization", f"Bearer {token}".encode())],
                "query_string": b"",
            })

        reader = mcp_tokens.generate_token("alice", "reader")["token"]
        writer = mcp_tokens.generate_token("alice", "writer", can_write=True)["token"]

        req = _request(reader)
        assert asyncio.run(main.require_auth(req, downstream)) == "ok"
        assert req.state.mcp_token_can_write is False

        req = _request(writer)
        assert asyncio.run(main.require_auth(req, downstream)) == "ok"
        assert req.state.mcp_token_can_write is True
    finally:
        monkeypatch.delenv("AUTH_PASSWORD", raising=False)
        importlib.reload(config)
        importlib.reload(main)


def test_toggleable_app_binds_write_flag_from_state():
    """The ASGI wrapper turns request.state.mcp_token_can_write into the
    contextvar the tool consults; a missing flag (SSO, password) allows."""
    import asyncio

    seen = []

    async def inner(scope, receive, send):
        seen.append(mcp.get_request_mcp_can_write())

    app = mcp.ToggleableMCPApp(inner)

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        pass

    base = {"type": "http", "path": "/", "method": "POST", "query_string": b""}
    asyncio.run(app({**base, "state": {"mcp_token_can_write": False}}, receive, send))
    asyncio.run(app({**base, "state": {"mcp_token_can_write": True}}, receive, send))
    asyncio.run(app({**base, "state": {}}, receive, send))
    assert seen == [False, True, True]
    assert mcp.get_request_mcp_can_write() is True  # reset after the request

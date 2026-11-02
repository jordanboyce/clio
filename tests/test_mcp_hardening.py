"""MCP hardening: token expiry + allowlist scoping + use counting,
cross-collection research, corpus versions on results, and prompts.

Everything here is deterministic — fixtures stand in for indexes; no model
is involved.
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services import mcp_tokens
from services.app_database import SQLiteBackend


# ── Tokens: DB columns ───────────────────────────────────────────────────

def test_db_persists_expiry_allowlist_and_counts_uses(tmp_path: Path):
    db = SQLiteBackend(tmp_path / "app.db")
    created = db.create_mcp_token("alice", "CI agent", "hash-1", "asy_mcp_ab12cd",
                                  collection_scope=["ops"], expires_at="2099-01-01T00:00:00",
                                  allowlist=True)
    assert created["expires_at"] == "2099-01-01T00:00:00"
    assert created["allowlist"] is True
    assert created["use_count"] == 0

    fetched = db.get_mcp_token_by_hash("hash-1")
    assert fetched["allowlist"] is True and fetched["expires_at"] == "2099-01-01T00:00:00"

    db.touch_mcp_token(created["id"])
    db.touch_mcp_token(created["id"])
    listed = db.list_mcp_tokens("alice")[0]
    assert listed["use_count"] == 2
    assert listed["last_used_at"] is not None

    # Legacy shape: a token minted before the columns existed keeps working.
    legacy = db.create_mcp_token("alice", "Old laptop", "hash-2", "asy_mcp_zz")
    assert legacy["expires_at"] is None and legacy["allowlist"] is False


# ── Tokens: service ──────────────────────────────────────────────────────

@pytest.fixture()
def fresh_app_db(tmp_path, monkeypatch):
    from services import app_database

    monkeypatch.setattr(app_database.app_db, "db_path", tmp_path / "app.db")
    app_database.app_db._init_db()
    return app_database.app_db


def test_is_expired_handles_missing_past_future_and_garbage():
    now = datetime(2026, 9, 17, 12, 0, 0)
    assert mcp_tokens.is_expired({}, now) is False
    assert mcp_tokens.is_expired({"expires_at": None}, now) is False
    assert mcp_tokens.is_expired({"expires_at": "2026-09-17T11:59:59"}, now) is True
    assert mcp_tokens.is_expired({"expires_at": "2026-09-17T12:00:01"}, now) is False
    # Zone-aware values are normalised to UTC before comparison.
    assert mcp_tokens.is_expired({"expires_at": "2026-09-17T13:30:00+02:00"}, now) is True
    # A lifetime that cannot be read is not a lifetime: fail closed.
    assert mcp_tokens.is_expired({"expires_at": "not a date"}, now) is True


def test_generate_token_stamps_expiry_and_verify_refuses_after_it(fresh_app_db):
    record = mcp_tokens.generate_token("alice", "Short-lived", expires_in_days=1)
    assert record["expires_at"] is not None
    remaining = datetime.fromisoformat(record["expires_at"]) - datetime.utcnow()
    assert timedelta(hours=23) < remaining <= timedelta(days=1)
    assert mcp_tokens.verify_token(record["token"]) is not None

    # A token whose lifetime has already passed: same shape, past expiry.
    plaintext = f"{mcp_tokens.TOKEN_PREFIX}already-over"
    fresh_app_db.create_mcp_token("alice", "Stale", mcp_tokens._hash(plaintext), plaintext[:14],
                                  expires_at=(datetime.utcnow() - timedelta(minutes=1)).isoformat())
    assert mcp_tokens.verify_token(plaintext) is None
    # Refusal leaves no trace of use.
    stale = next(t for t in mcp_tokens.list_tokens("alice") if t["name"] == "Stale")
    assert stale["use_count"] == 0 and stale["last_used_at"] is None


@pytest.mark.parametrize("days", [0, -1, mcp_tokens.MAX_EXPIRY_DAYS + 1])
def test_generate_token_rejects_bad_lifetimes(fresh_app_db, days):
    with pytest.raises(ValueError, match="expires_in_days"):
        mcp_tokens.generate_token("alice", "x", expires_in_days=days)


def test_allowlist_requires_a_scope(fresh_app_db):
    with pytest.raises(ValueError, match="allowlist"):
        mcp_tokens.generate_token("alice", "x", allowlist=True)
    record = mcp_tokens.generate_token("alice", "x", collection_scope=["ops"], allowlist=True)
    assert record["allowlist"] is True and record["collection_scope"] == ["ops"]


def test_verify_counts_uses(fresh_app_db):
    record = mcp_tokens.generate_token("alice", "Laptop")
    for _ in range(3):
        assert mcp_tokens.verify_token(record["token"]) is not None
    assert mcp_tokens.list_tokens("alice")[0]["use_count"] == 3


# ── Tokens: allowlist enforcement inside the MCP server ──────────────────

@pytest.fixture()
def two_collections(monkeypatch):
    from services import mcp_server as mcp

    cols = [{"id": "ops", "name": "Operations", "sensitivity": "internal"},
            {"id": "hr", "name": "People", "sensitivity": "internal"},
            {"id": "vault", "name": "Vault", "sensitivity": "restricted"}]
    monkeypatch.setattr(mcp.collection_service, "get_all_collections", lambda *a, **k: list(cols))
    monkeypatch.setattr(mcp.collection_service, "get_collection",
                        lambda cid: next((c for c in cols if c["id"] == cid), None))
    monkeypatch.setattr(mcp.settings, "private_collections", False)
    return mcp


def _with_token(mcp, scope, allowlist):
    s = mcp._request_mcp_token_scope.set(scope)
    a = mcp._request_mcp_token_allowlist.set(allowlist)
    return lambda: (mcp._request_mcp_token_allowlist.reset(a), mcp._request_mcp_token_scope.reset(s))


def test_grant_scope_keeps_everything_visible_and_unlocks_restricted(two_collections):
    mcp = two_collections
    reset = _with_token(mcp, ["vault"], allowlist=False)
    try:
        assert [c["id"] for c in mcp._visible_collections()] == ["ops", "hr", "vault"]
        assert mcp._resolve_collection_id("hr") == "hr"
        assert mcp._resolve_collection_id("vault") == "vault"
    finally:
        reset()


def test_allowlisted_token_sees_only_its_scope(two_collections):
    mcp = two_collections
    reset = _with_token(mcp, ["ops"], allowlist=True)
    try:
        assert [c["id"] for c in mcp._visible_collections()] == ["ops"]
        assert mcp._resolve_collection_id("ops") == "ops"
        # Outside the allowlist answers exactly like a missing collection, by id and by name.
        with pytest.raises(ValueError, match="not found"):
            mcp._resolve_collection_id("hr")
        with pytest.raises(ValueError, match="not found"):
            mcp._resolve_collection_id("People")
        # An allowlist naming a restricted collection is also a grant into it.
        reset()
        reset = _with_token(mcp, ["vault"], allowlist=True)
        assert [c["id"] for c in mcp._visible_collections()] == ["vault"]
    finally:
        reset()


def test_non_token_callers_are_unaffected_by_allowlist_default(two_collections):
    mcp = two_collections
    # No token context at all: SSO/password/in-process callers see everything non-restricted.
    assert [c["id"] for c in mcp._visible_collections()] == ["ops", "hr"]


def test_middleware_passes_allowlist_flag_to_request_state(tmp_path, monkeypatch):
    import importlib

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
        record = mcp_tokens.generate_token("alice", "CI", collection_scope=["ops"], allowlist=True)
        scope = {"type": "http", "method": "POST", "path": "/mcp/", "query_string": b"",
                 "headers": [(b"authorization", f"Bearer {record['token']}".encode())]}
        request = Request(scope)

        async def downstream(_):
            return "ok"

        assert asyncio.run(main.require_auth(request, downstream)) == "ok"
        assert request.state.mcp_token_allowlist is True
        assert request.state.mcp_token_scope == ["ops"]
    finally:
        monkeypatch.delenv("AUTH_PASSWORD", raising=False)
        importlib.reload(config)
        importlib.reload(main)


# ── Cross-collection research + corpus versions ─────────────────────────

def _hit(doc, chunk, text, score):
    return SimpleNamespace(filename=f"{doc}.md", document_id=doc, chunk_id=chunk, page_number=1,
                           text_snippet=text, similarity_score=score, source_format="md",
                           source_type="upload", source_path=None, sensitivity="internal")


@pytest.fixture()
def multi_env(monkeypatch):
    from services import mcp_server as mcp

    cols = {"ops": {"id": "ops", "name": "Operations", "sensitivity": "internal"},
            "hr": {"id": "hr", "name": "People", "sensitivity": "internal"}}
    hits = {"ops": [_hit("doc-ops", "c1", "EQ-17 return rule", 0.6)],
            "hr": [_hit("doc-hr", "c2", "EQ-17 field-team exception", 0.9)]}
    versions = {"ops": "3:2026-09-01:40:0", "hr": "1:2026-08-01:9:0"}

    def indexer_for(cid):
        metadata = SimpleNamespace(
            get_hidden_document_ids=lambda: set(),
            get_filtered_chunk_ids=Mock(return_value=None),
            iter_chunk_texts=lambda **k: iter([]),
            get_document_info=lambda _: {},
            corpus_version=lambda: versions[cid],
        )
        return SimpleNamespace(vector_store=SimpleNamespace(metadata_store=metadata),
                               search=Mock(return_value={"results": list(hits[cid])}))

    indexers = {cid: indexer_for(cid) for cid in cols}
    monkeypatch.setattr(mcp.indexer_manager, "get_indexer", lambda cid: indexers[cid])
    monkeypatch.setattr(mcp.collection_service, "get_all_collections", lambda *a, **k: list(cols.values()))
    monkeypatch.setattr(mcp.collection_service, "get_collection", lambda cid: cols.get(cid))
    monkeypatch.setattr(mcp.settings, "private_collections", False)
    monkeypatch.setattr(mcp.settings, "mcp_default_collection", "ops")
    monkeypatch.setattr(mcp, "get_request_mcp_profile", lambda: {"include_sources": True})
    return mcp, indexers


def test_single_collection_research_reports_its_corpus_version(multi_env):
    mcp, _ = multi_env
    out = mcp.research_documents_sync("EQ-17", "ops")
    assert out["collection_id"] == "ops"
    assert out["collection_ids"] == ["ops"]
    assert out["corpus_version"] == "3:2026-09-01:40:0"
    assert out["collections"] == [{"collection_id": "ops", "collection_name": "Operations",
                                   "corpus_version": "3:2026-09-01:40:0"}]
    assert "failed_collections" not in out


def test_research_spans_collections_and_ranks_one_list(multi_env):
    mcp, indexers = multi_env
    out = mcp.research_documents_sync("EQ-17", collection_ids=["ops", "hr"])
    assert out["collection_id"] is None
    assert out["collection_ids"] == ["ops", "hr"]
    assert [c["corpus_version"] for c in out["collections"]] == ["3:2026-09-01:40:0", "1:2026-08-01:9:0"]
    # Both collections were searched on every non-literal branch.
    assert indexers["ops"].search.call_count == indexers["hr"].search.call_count == 2
    # The stronger hit leads regardless of the order the collections were listed in.
    assert [(r["collection_id"], r["document_id"]) for r in out["results"]] == [("hr", "doc-hr"), ("ops", "doc-ops")]
    assert out["coverage"][0]["status"] == "retrieved"
    assert out["suggested_next"][0]["arguments"]["collection_id"] == "hr"


def test_research_star_means_every_visible_collection_and_dedupes(multi_env):
    mcp, _ = multi_env
    # "*" expands to the visible collections in listing order; an explicit
    # collection_id already covered by it is not duplicated or reordered.
    out = mcp.research_documents_sync("EQ-17", "hr", collection_ids=["*", "hr", "Operations"])
    assert out["collection_ids"] == ["ops", "hr"]
    # Names resolve like ids and duplicates collapse.
    out = mcp.research_documents_sync("EQ-17", collection_ids=["Operations", "ops", "hr"])
    assert out["collection_ids"] == ["ops", "hr"]


def test_research_reports_a_broken_collection_instead_of_dropping_the_call(multi_env):
    mcp, indexers = multi_env
    indexers["hr"].search = Mock(side_effect=RuntimeError("index locked"))
    out = mcp.research_documents_sync("EQ-17", collection_ids=["ops", "hr"])
    assert out["failed_collections"] == {"hr": "RuntimeError"}
    assert out["partial_failure"] is True
    assert [r["collection_id"] for r in out["results"]] == ["ops"]
    # The exception text never reaches the client.
    assert "index locked" not in str(out)


def test_research_collection_cap_and_invisible_collections(multi_env, monkeypatch):
    mcp, _ = multi_env
    with pytest.raises(ValueError, match="not found"):
        mcp.research_documents_sync("EQ-17", collection_ids=["ops", "finance"])
    many = [{"id": f"c{i}", "name": f"C{i}", "sensitivity": "internal"} for i in range(9)]
    monkeypatch.setattr(mcp.collection_service, "get_all_collections", lambda *a, **k: many)
    monkeypatch.setattr(mcp.collection_service, "get_collection",
                        lambda cid: next((c for c in many if c["id"] == cid), None))
    with pytest.raises(ValueError, match="at most 8 collections"):
        mcp.research_documents_sync("EQ-17", collection_ids=["*"])


def test_registered_tool_accepts_collection_ids(multi_env):
    mcp, _ = multi_env
    tool = next(t for t in asyncio.run(mcp._clio_mcp.list_tools()) if t.name == "research_documents")
    assert "collection_ids" in tool.inputSchema["properties"]
    _, structured = asyncio.run(mcp._clio_mcp.call_tool(
        "research_documents", {"query": "EQ-17", "collection_ids": ["ops", "hr"]}))
    assert structured["collection_ids"] == ["ops", "hr"]


# ── Prompts ──────────────────────────────────────────────────────────────

def test_prompts_are_registered_and_render_tool_workflows():
    from services import mcp_server as mcp

    prompts = {p.name: p for p in asyncio.run(mcp._clio_mcp.list_prompts())}
    assert set(prompts) == {"research_question", "find_exact_reference", "collection_overview", "try_clio"}
    assert [a.name for a in prompts["research_question"].arguments] == ["question", "collection"]
    assert next(a for a in prompts["research_question"].arguments if a.name == "question").required is True

    scoped = asyncio.run(mcp._clio_mcp.get_prompt(
        "research_question", {"question": "When must EQ-17 gear be returned?", "collection": "ops"}))
    text = scoped.messages[0].content.text
    assert "research_documents" in text and "get_document_context" in text
    assert '"ops"' in text and "do not establish" in text

    unscoped = asyncio.run(mcp._clio_mcp.get_prompt("find_exact_reference", {"identifier": "EQ-17"}))
    text = unscoped.messages[0].content.text
    assert "find_in_documents" in text and "list_collections" in text and '"EQ-17"' in text

    overview = asyncio.run(mcp._clio_mcp.get_prompt("collection_overview", {}))
    assert "get_collection_info" in overview.messages[0].content.text

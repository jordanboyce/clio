"""Phase 0 trust fixes in the MCP server: resources hide what tools hide,
table inlining can be turned off, regex scans are bounded, and agents cannot
overwrite a person's upload."""

import pytest

import services.mcp_server as mcp
from tests.test_mcp_write import MORE, NOTES, _stored, _upload, fresh_db, indexer  # noqa: F401


# ── F1: resources hide quarantined documents ──────────────────────────────


def _quarantine(indexer, document_id):
    indexer.vector_store.metadata_store.set_document_governance(document_id, policy_status="quarantined")


def test_document_resource_hides_a_quarantined_document(indexer):
    doc = mcp.write_document_sync("held.md", NOTES)
    assert mcp.resource_document(doc["document_id"])
    _quarantine(indexer, doc["document_id"])

    with pytest.raises(ValueError, match="not found"):
        mcp.resource_document(doc["document_id"])


def test_table_resources_hide_a_quarantined_table(indexer):
    doc = mcp.write_document_sync("cities.csv", "city,pop\nBoise,235000\n")
    kept = mcp.write_document_sync("towns.csv", "town,pop\nEagle,30000\n")
    _quarantine(indexer, doc["document_id"])

    for payload in (mcp.resource_collection_tables("default"), mcp.resource_collection_schema("default")):
        listed = payload.get("tables") or payload.get("schemas")
        assert [t["filename"] for t in listed] == ["towns.csv"]
        assert payload["total_tables"] == 1
    with pytest.raises(ValueError, match="No structured table"):
        mcp.resource_table("cities.csv")
    with pytest.raises(ValueError, match="No structured table"):
        mcp.resource_table(doc["document_id"])
    assert mcp.resource_table(kept["document_id"])


# ── F2: inline_row_threshold=0 turns table inlining off ───────────────────


def _search_with_profile(raw_profile):
    profile = mcp._normalize_profile(raw_profile) if raw_profile is not None else {}
    token = mcp._request_mcp_profile.set(profile)
    try:
        return mcp.search_collection_sync("Boise population", collection_id="default", mode="keyword")
    finally:
        mcp._request_mcp_profile.reset(token)


def test_inline_row_threshold_zero_turns_inlining_off(indexer):
    mcp.write_document_sync("cities.csv", "city,pop\nBoise,235000\nNampa,100000\n")

    assert _search_with_profile(None).get("structured_tables")          # default inlines
    assert _search_with_profile({"inline_row_threshold": ""}).get("structured_tables")
    assert not _search_with_profile({"inline_row_threshold": "0"}).get("structured_tables")
    assert mcp._normalize_profile({"inline_row_threshold": "0"})["inline_row_threshold"] == 0
    assert mcp._inline_row_threshold(None) == mcp._DEFAULT_INLINE_ROW_THRESHOLD


# ── F3: a caller's regex is bounded ────────────────────────────────────────


def test_an_overlong_regex_is_refused(indexer):
    mcp.write_document_sync("notes.md", NOTES)
    with pytest.raises(ValueError, match="limit"):
        mcp.find_in_documents_sync("a" * (mcp._FIND_REGEX_MAX_PATTERN_CHARS + 1), literal=False)
    # A literal search of the same length is unaffected.
    assert mcp.find_in_documents_sync("a" * (mcp._FIND_REGEX_MAX_PATTERN_CHARS + 1))["scan_complete"]


def test_a_backtracking_regex_stops_at_the_budget_and_says_so(indexer, monkeypatch):
    import time

    mcp.write_document_sync("trap.md", "a" * 40 + "b\n")
    monkeypatch.setattr(mcp, "_FIND_REGEX_TIME_BUDGET_S", 1.0)

    started = time.monotonic()
    result = mcp.find_in_documents_sync(r"(a+)+$", literal=False)

    assert time.monotonic() - started < 8          # 2**40 steps would take days
    assert result["scan_complete"] is False
    assert result["stopped_reason"] == "time_budget"
    assert result["truncated"] is True
    assert "partial" in result["note"]


def test_an_ordinary_regex_still_finds_matches(indexer):
    mcp.write_document_sync("notes.md", NOTES)
    result = mcp.find_in_documents_sync(r"backups\s+run\s+(\w+)", literal=False)

    assert result["scan_complete"] is True and "stopped_reason" not in result
    assert result["total_matches"] == 1
    assert result["matches"][0]["match"] == "Backups run nightly"
    assert "«Backups run nightly»" in result["matches"][0]["excerpt"]


def test_regex_search_falls_back_in_process_when_no_child_can_start(indexer, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("no interpreter")

    monkeypatch.setattr(mcp, "_ChildRegex", refuse)
    mcp.write_document_sync("notes.md", NOTES)
    result = mcp.find_in_documents_sync(r"nightly at \d\d:\d\d", literal=False)
    assert result["total_matches"] == 1 and result["scan_complete"] is True


# ── B1: an agent cannot overwrite a person's upload ───────────────────────


@pytest.fixture()
def may_overwrite_uploads():
    token = mcp._request_mcp_can_overwrite_uploads.set(True)
    yield
    mcp._request_mcp_can_overwrite_uploads.reset(token)


def _row(indexer, name):
    rows = [r for r in indexer.list_documents() if r["filename"] == name]
    assert len(rows) == 1, rows
    return rows[0]


def test_agent_notes_are_typed_at_write_time(indexer):
    created = mcp.write_document_sync("sprint.md", NOTES)
    assert created["source_type"] == mcp.AGENT_NOTE_SOURCE_TYPE
    assert _row(indexer, "sprint.md")["source_type"] == mcp.AGENT_NOTE_SOURCE_TYPE

    replaced = mcp.write_document_sync("sprint.md", "New text.", mode="replace")
    appended = mcp.write_document_sync("sprint.md", MORE, mode="append")
    assert replaced["status"] == "replaced" and appended["status"] == "appended"
    assert _row(indexer, "sprint.md")["source_type"] == mcp.AGENT_NOTE_SOURCE_TYPE

    from services import audit

    assert audit.list_events(action="document.upload")[0]["detail"]["source_type"] == mcp.AGENT_NOTE_SOURCE_TYPE


def test_replacing_a_persons_upload_is_refused(indexer):
    _upload("budget.csv", b"item,cost\nrent,1200\n")
    before = _stored("budget.csv")

    with pytest.raises(ValueError, match="uploaded by a person.*can_overwrite_uploads"):
        mcp.write_document_sync("budget.csv", "item,cost\nrent,0\n", mode="replace")

    assert _stored("budget.csv") == before
    assert _row(indexer, "budget.csv")["source_type"] == "upload"


def test_appending_to_a_persons_upload_keeps_it_theirs(indexer):
    _upload("journal.md", NOTES.encode())

    appended = mcp.write_document_sync("journal.md", MORE, mode="append")
    assert appended["status"] == "appended"
    assert appended["source_type"] == "upload"
    assert _stored("journal.md").startswith(NOTES)

    # An append does not turn the person's file into an agent's note.
    with pytest.raises(ValueError, match="uploaded by a person"):
        mcp.write_document_sync("journal.md", "Gone.", mode="replace")


def test_writing_back_a_persons_text_unchanged_is_not_refused(indexer):
    _upload("journal.md", NOTES.encode())
    assert mcp.write_document_sync("journal.md", NOTES, mode="replace")["status"] == "unchanged"


def test_a_token_with_the_grant_may_replace_an_upload(indexer, may_overwrite_uploads):
    _upload("budget.csv", b"item,cost\nrent,1200\n")

    result = mcp.write_document_sync("budget.csv", "item,cost\nrent,1300\n", mode="replace")

    assert result["status"] == "replaced"
    assert _stored("budget.csv") == "item,cost\nrent,1300\n"
    # Still the person's file: the next replace needs the grant again.
    assert result["source_type"] == "upload"


def test_a_note_written_before_the_marker_is_still_the_agents(indexer):
    """Legacy notes are plain 'upload' rows; their write_document audit event says they are an agent's."""
    first = mcp.write_document_sync("legacy.md", NOTES)
    store = indexer.vector_store.metadata_store
    store.update_document_source(first["document_id"], source_path=None, source_type="upload")
    from services.app_database import app_db
    from services.sqlite_utils import sqlite_connect

    with sqlite_connect(app_db.db_path) as conn:
        conn.execute("UPDATE audit_events SET detail = ? WHERE document_id = ?",
                     ('{"via": "mcp", "mode": "create", "files": ["legacy.md"]}', first["document_id"]))
        conn.commit()

    assert mcp.write_document_sync("legacy.md", "Rewritten.", mode="replace")["status"] == "replaced"
    assert _row(indexer, "legacy.md")["source_type"] == mcp.AGENT_NOTE_SOURCE_TYPE


def test_a_reindexed_agent_note_stays_the_agents(indexer, fresh_db):
    """A full re-index rebuilds the row as an upload; the audit trail still knows."""
    note = mcp.write_document_sync("plan.md", NOTES)
    indexer.vector_store.metadata_store.update_document_source(
        note["document_id"], source_path=None, source_type="upload")

    assert mcp.write_document_sync("plan.md", "Next plan.", mode="replace")["status"] == "replaced"


def test_create_and_append_tools_are_not_destructive(indexer):
    import asyncio

    tools = {t.name: t for t in asyncio.run(mcp._clio_mcp.list_tools())}
    for name in ("create_document", "append_to_document"):
        hints = tools[name].annotations
        assert hints.readOnlyHint is False and hints.destructiveHint is False, name
    assert tools["create_document"].annotations.idempotentHint is True
    assert tools["append_to_document"].annotations.idempotentHint is False
    assert tools["write_document"].annotations.destructiveHint is True   # replace still drops text

    created = asyncio.run(mcp.create_document("log.md", NOTES))
    assert created["status"] == "created"
    with pytest.raises(ValueError, match="already exists"):
        asyncio.run(mcp.create_document("log.md", "Other text."))
    appended = asyncio.run(mcp.append_to_document("log.md", MORE, expected_document_id=created["document_id"]))
    assert appended["status"] == "appended"
    assert _stored("log.md").startswith(NOTES)


# ── B1: the can_overwrite_uploads token grant ──────────────────────────────


def test_overwrite_grant_round_trips_and_needs_write(fresh_db):
    from services import mcp_tokens
    from services.app_database import app_db

    plain = mcp_tokens.generate_token("alice", "writer", can_write=True)
    granted = mcp_tokens.generate_token("alice", "curator", can_write=True, can_overwrite_uploads=True)
    assert plain["can_overwrite_uploads"] is False
    assert granted["can_overwrite_uploads"] is True
    assert mcp_tokens.verify_token(plain["token"])["can_overwrite_uploads"] is False
    assert mcp_tokens.verify_token(granted["token"])["can_overwrite_uploads"] is True
    listed = {t["id"]: t for t in app_db.list_mcp_tokens("alice")}
    assert listed[granted["id"]]["can_overwrite_uploads"] is True

    with pytest.raises(ValueError, match="can_write"):
        mcp_tokens.generate_token("alice", "reader", can_overwrite_uploads=True)


def test_an_existing_token_table_gains_the_grant_switched_off(tmp_path, monkeypatch):
    """A database from before the grant picks the column up at start-up; its tokens stay without it."""
    from services import app_database, mcp_tokens
    from services.sqlite_utils import sqlite_connect

    db_path = tmp_path / "app.db"
    with sqlite_connect(db_path) as conn:
        conn.execute("""CREATE TABLE mcp_tokens (
            id TEXT PRIMARY KEY, user_id TEXT, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
            token_prefix TEXT NOT NULL, created_at TEXT NOT NULL, last_used_at TEXT, revoked_at TEXT,
            collection_scope TEXT, can_write INTEGER NOT NULL DEFAULT 0, expires_at TEXT,
            allowlist INTEGER NOT NULL DEFAULT 0, use_count INTEGER NOT NULL DEFAULT 0)""")
        conn.execute("INSERT INTO mcp_tokens (id, user_id, name, token_hash, token_prefix, created_at, can_write) "
                     "VALUES ('old', 'alice', 'Old agent', 'h-old', 'asy_mcp_old', '2026-01-01T00:00:00', 1)")
        conn.commit()
    monkeypatch.setattr(app_database.app_db, "db_path", db_path)
    app_database.app_db._init_db()

    old = app_database.app_db.get_mcp_token_by_hash("h-old")
    assert old["can_write"] is True and old["can_overwrite_uploads"] is False
    assert app_database.app_db.list_mcp_tokens("alice")[0]["can_overwrite_uploads"] is False
    granted = mcp_tokens.generate_token("alice", "curator", can_write=True, can_overwrite_uploads=True)
    assert mcp_tokens.verify_token(granted["token"])["can_overwrite_uploads"] is True


def test_token_api_mints_the_overwrite_grant(indexer, fresh_db):
    from fastapi.testclient import TestClient

    import main

    client = TestClient(main.app)
    r = client.post("/api/mcp/tokens", json={"name": "curator", "can_write": True, "can_overwrite_uploads": True})
    assert r.status_code == 201, r.text
    assert r.json()["can_overwrite_uploads"] is True
    assert client.post("/api/mcp/tokens", json={"name": "plain", "can_write": True}).json()["can_overwrite_uploads"] is False
    assert client.post("/api/mcp/tokens", json={"name": "x", "can_overwrite_uploads": True}).status_code == 400
    assert client.post("/api/mcp/tokens", json={"name": "x", "can_overwrite_uploads": "true"}).status_code == 422

    from services import audit

    events = audit.list_events(action="mcp_token.create")
    assert [e["detail"]["can_overwrite_uploads"] for e in events[:2]] == [False, True]


def test_middleware_and_mcp_app_carry_the_overwrite_grant(tmp_path, monkeypatch):
    import asyncio
    import importlib

    import config
    import main
    from services import app_database, mcp_tokens
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
                "headers": [(b"authorization", f"Bearer {token}".encode())], "query_string": b"",
            })

        writer = mcp_tokens.generate_token("alice", "writer", can_write=True)["token"]
        curator = mcp_tokens.generate_token("alice", "curator", can_write=True, can_overwrite_uploads=True)["token"]
        states = []
        for token in (writer, curator):
            req = _request(token)
            assert asyncio.run(main.require_auth(req, downstream)) == "ok"
            states.append(req.state.mcp_token_can_overwrite_uploads)
        assert states == [False, True]
    finally:
        monkeypatch.delenv("AUTH_PASSWORD", raising=False)
        importlib.reload(config)
        importlib.reload(main)

    seen = []

    async def inner(scope, receive, send):
        seen.append(mcp.get_request_mcp_can_overwrite_uploads())

    app = mcp.ToggleableMCPApp(inner)

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        pass

    base = {"type": "http", "path": "/", "method": "POST", "query_string": b""}
    asyncio.run(app({**base, "state": {"mcp_token_can_overwrite_uploads": True}}, receive, send))
    asyncio.run(app({**base, "state": {"mcp_token_can_write": True}}, receive, send))
    asyncio.run(app({**base, "state": {}}, receive, send))
    # Only a token minted with the grant carries it; password and SSO callers do not.
    assert seen == [True, False, False]
    assert mcp.get_request_mcp_can_overwrite_uploads() is False

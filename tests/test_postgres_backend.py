"""Every database method, run against both backends.

test_backend_parity.py checks that the two backends have the same methods. This
checks that they do the same thing, which is the only way to catch SQL that
Postgres rejects, since nothing else in the suite runs on it. The second half
covers the Postgres connection pool under load and across a server restart.

Postgres runs when CLIO_TEST_POSTGRES_URL names a server the tests may wipe:

    docker run -d --rm --name clio-test-pg -e POSTGRES_PASSWORD=clio_test \
        -e POSTGRES_DB=clio_test -p 127.0.0.1:55432:5432 postgres:17-alpine
    CLIO_TEST_POSTGRES_URL=postgresql://postgres:clio_test@127.0.0.1:55432/clio_test pytest

Without it those cases skip and the SQLite ones still run.
"""

import ast
import os
import threading
import time
from datetime import datetime, timedelta
from services.clock import utcnow
from pathlib import Path

import pytest

from config import settings
from services import secret_box
from services.app_database import SQLiteBackend
from services.db_backend import DatabaseBusy

POSTGRES_URL = os.environ.get("CLIO_TEST_POSTGRES_URL", "")
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="CLIO_TEST_POSTGRES_URL is not set")


def _wipe_postgres():
    import psycopg2

    conn = psycopg2.connect(POSTGRES_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
    conn.close()


def _postgres_backend(**pool):
    from services.db_postgres import PostgresBackend

    _wipe_postgres()
    return PostgresBackend(POSTGRES_URL, **pool)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "secret_encryption_key", "")
    monkeypatch.setattr(secret_box, "_cached", None)
    yield
    secret_box._cached = None


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=needs_postgres)])
def db(request, tmp_path):
    if request.param == "sqlite":
        yield SQLiteBackend(tmp_path / "app.db")
        return
    backend = _postgres_backend(max_conn=5)
    yield backend
    backend.pool.closeall()


@pytest.fixture()
def pg():
    if not POSTGRES_URL:
        pytest.skip("CLIO_TEST_POSTGRES_URL is not set")
    backend = _postgres_backend(max_conn=3, wait_timeout=0.3)
    yield backend
    backend.pool.closeall()


# ── Configuration, users, preferences ───────────────────────────────────────


def test_config_round_trip(db):
    db.set_config("chunk_size", 900)
    db.set_config("ai_model", "llama")
    db.set_config("options", {"a": [1, 2]})
    assert db.get_config("chunk_size") == 900
    assert db.get_config("ai_model") == "llama"
    assert db.get_config("options") == {"a": [1, 2]}
    assert db.get_config("missing", "fallback") == "fallback"
    db.set_config("chunk_size", 1000)
    assert db.get_all_config()["chunk_size"] == 1000
    db.delete_config("chunk_size")
    assert db.get_config("chunk_size") is None


def test_credentials_are_sealed_in_the_store(db):
    db.set_config("ai_api_key", "sk-not-real")
    db.set_agent_api_key("openai", "sk-also-not-real")
    assert secret_box.is_sealed(db._read_config("ai_api_key"))
    assert secret_box.is_sealed(db.get_user_preference("agent_api_key_openai"))
    assert db.get_config("ai_api_key") == "sk-not-real"
    assert db.get_agent_api_key("openai") == "sk-also-not-real"
    assert db.get_agent_config()["providers"]["openai"]["configured"] is True
    db.delete_agent_api_key("openai")
    assert db.get_agent_api_key("openai") is None


def test_plain_credentials_are_sealed_at_startup(db):
    db._write_config("ai_api_key", "sk-not-real")
    db.set_user_preference("agent_api_key_google", "sk-also-not-real")
    assert db.seal_stored_secrets() == 2
    assert db.seal_stored_secrets() == 0
    assert db.get_config("ai_api_key") == "sk-not-real"
    assert db.get_agent_api_key("google") == "sk-also-not-real"


def test_users(db):
    created = db.upsert_user("a@example.org", "A")
    assert created["id"] == "a@example.org" or created.get("user_id") == "a@example.org"
    db.upsert_user("a@example.org", "A again")
    assert db.get_user("a@example.org")["display_name"] == "A again"
    assert db.get_user("nobody@example.org") is None
    assert "a@example.org" in {u.get("id", u.get("user_id")) for u in db.get_all_users()}


def test_user_preferences(db):
    db.set_user_preference("theme", "dark")
    db.set_user_preference("sizes", [1, 2])
    assert db.get_user_preference("theme") == "dark"
    assert db.get_user_preference("sizes") == [1, 2]
    assert db.get_user_preference("missing", "x") == "x"
    assert db.get_all_user_preferences()["theme"] == "dark"


# ── Collections and their documents ─────────────────────────────────────────


def test_collection_lifecycle(db):
    cid = db.create_collection("Policies", description="d", owner_id="a@example.org")
    found = db.get_collection(cid)
    assert found["name"] == "Policies"
    assert found["owner_id"] == "a@example.org"
    assert found["published"] is False
    assert found["document_count"] == 0
    assert found["storage_bytes"] == 0

    db.update_collection(cid, name="Renamed", published=True, sensitivity="confidential", guide="g")
    found = db.get_collection(cid)
    assert (found["name"], found["published"], found["sensitivity"], found["guide"]) == (
        "Renamed", True, "confidential", "g"
    )

    assert cid in {c["id"] for c in db.get_all_collections()}
    assert [c["id"] for c in db.get_all_collections(owner_id="a@example.org")] == [cid]
    assert db.get_all_collections(owner_id="b@example.org") == []

    assert db.delete_collection(cid) is True
    assert db.get_collection(cid) is None
    assert db.delete_collection("default") is False


def test_collection_documents(db):
    cid = db.create_collection("Docs")
    other = db.create_collection("Other")
    db.add_document_to_collection(cid, "d1")
    db.add_document_to_collection(cid, "d1")
    db.add_document_to_collection(cid, "d2")
    db.add_document_to_collection(cid, "d3")
    db.add_document_to_collection(other, "d3")

    assert sorted(db.get_collection_documents(cid)) == ["d1", "d2", "d3"]
    assert sorted(db.get_document_collections("d3")) == sorted([cid, other])

    db.remove_document_from_collection(cid, "d1")
    assert sorted(db.get_collection_documents(cid)) == ["d2", "d3"]
    db.remove_documents_from_collection(cid, ["d2"])
    assert db.get_collection_documents(cid) == ["d3"]
    db.remove_documents_from_collection(cid)
    assert db.get_collection_documents(cid) == []
    assert db.get_collection_documents(other) == ["d3"]


# ── Sharing ─────────────────────────────────────────────────────────────────


def test_sharing(db):
    cid = db.create_collection("Shared", owner_id="owner@example.org")
    share = db.create_share(cid, "owner@example.org", "readwrite", invited_email="r@example.org")

    assert db.get_share(share)["permission"] == "readwrite"
    assert [s["id"] for s in db.get_shares_for_collection(cid)] == [share]
    assert db.list_share_contacts() == {"r@example.org": 1}
    assert db.count_active_shares_for_email("r@example.org") == 1
    assert db.count_active_shares_for_email("r@example.org", exclude_share_id=share) == 0

    assert db.check_share_access(cid, "r@example.org") is None
    db.accept_share(share, "r@example.org")
    assert db.check_share_access(cid, "r@example.org") == "readwrite"
    shared = db.get_shared_collections("r@example.org")
    assert [c["id"] for c in shared] == [cid]
    assert shared[0]["shared"] is True
    assert shared[0]["permission"] == "readwrite"
    assert shared[0]["document_count"] == 0

    db.revoke_share(share)
    assert db.check_share_access(cid, "r@example.org") is None
    assert db.get_shared_collections("r@example.org") == []


def test_expired_share_gives_no_access(db):
    cid = db.create_collection("Shared", owner_id="owner@example.org")
    past = (utcnow() - timedelta(days=1)).isoformat()
    share = db.create_share(cid, "owner@example.org", "read", expires_at=past)
    db.accept_share(share, "r@example.org")
    assert db.check_share_access(cid, "r@example.org") is None


# ── MCP tokens ──────────────────────────────────────────────────────────────


def test_mcp_tokens(db):
    created = db.create_mcp_token(
        "a@example.org", "laptop", "hash-1", "asy_mcp_abc",
        collection_scope=["c1"], can_write=True, allowlist=True,
    )
    assert created["can_write"] is True
    assert created["allowlist"] is True
    assert created["collection_scope"] == ["c1"]

    found = db.get_mcp_token_by_hash("hash-1")
    assert found["id"] == created["id"]
    assert found["collection_scope"] == ["c1"]
    assert found["can_write"] is True
    assert db.get_mcp_token_by_hash("nope") is None

    db.touch_mcp_token(created["id"])
    listed = db.list_mcp_tokens("a@example.org")
    assert listed[0]["use_count"] == 1
    assert listed[0]["last_used_at"]

    assert db.revoke_mcp_token(created["id"], "someone-else@example.org") is False
    assert db.revoke_mcp_token(created["id"], "a@example.org") is True
    assert db.revoke_mcp_token(created["id"], "a@example.org") is False


def test_anonymous_mcp_tokens_use_null_owner(db):
    created = db.create_mcp_token(None, "anon", "hash-2", "asy_mcp_def")
    assert [t["id"] for t in db.list_mcp_tokens(None)] == [created["id"]]
    assert db.revoke_mcp_token(created["id"], None) is True


def test_revoke_all_tokens_of_one_identity(db):
    db.create_mcp_token("a@example.org", "one", "h1", "p1")
    db.create_mcp_token("a@example.org", "two", "h2", "p2")
    db.create_mcp_token("b@example.org", "three", "h3", "p3")
    assert db.revoke_all_mcp_tokens_for_user("a@example.org") == 2
    assert db.revoke_all_mcp_tokens_for_user("a@example.org") == 0
    assert all(not t["revoked_at"] for t in db.list_mcp_tokens("b@example.org"))


# ── Audit trail ─────────────────────────────────────────────────────────────


def test_audit_trail(db):
    first = db.add_audit_event("a@example.org", "document.delete", "c1", "d1", "t", "detail")
    assert first
    db.add_audit_events("b@example.org", "document.remove", "c2", [
        {"document_id": "d2", "target": "x", "detail": "y"},
        {"document_id": "d3"},
    ])

    assert len(db.list_audit_events()) == 3
    assert len(db.list_audit_events(limit=1)) == 1
    assert [e["document_id"] for e in db.list_audit_events(action="document.delete")] == ["d1"]
    assert len(db.list_audit_events(actor="b@example.org")) == 2
    assert len(db.list_audit_events(collection_id="c2")) == 2
    assert len(db.list_audit_events(document_id="d3")) == 1
    assert db.list_audit_events(since=(utcnow() + timedelta(days=1)).isoformat()) == []

    assert db.delete_old_audit_events(1) == 0
    assert len(db.list_audit_events()) == 3


# ── Acceptable use, blocklist ───────────────────────────────────────────────


def test_acceptable_use(db):
    assert db.get_aup_acknowledgement("a@example.org", "1") is None
    db.record_aup_acknowledgement("a@example.org", "1")
    db.record_aup_acknowledgement("a@example.org", "1")
    assert db.get_aup_acknowledgement("a@example.org", "1")
    assert db.get_aup_acknowledgement("a@example.org", "2") is None
    assert len(db.list_aup_acknowledgements()) == 1


def test_hash_blocklist(db):
    assert db.is_hash_blocked("abc") is False
    db.add_blocked_hash("abc", "admin@example.org", "reason", "bad.pdf")
    db.add_blocked_hash("abc", "admin@example.org", "again")
    assert db.is_hash_blocked("abc") is True
    assert [b["content_hash"] for b in db.list_blocked_hashes()] == ["abc"]
    assert db.remove_blocked_hash("abc") is True
    assert db.remove_blocked_hash("abc") is False
    assert db.is_hash_blocked("abc") is False


# ── Jobs ────────────────────────────────────────────────────────────────────


def test_upload_jobs(db):
    job = db.create_upload_job("c1", 3)
    assert db.get_upload_job(job)["status"] == "pending"
    assert db.get_active_upload_job("c1")["id"] == job
    assert db.get_active_upload_job("c2") is None
    assert [j["id"] for j in db.get_all_active_upload_jobs()] == [job]

    db.update_upload_job(job, status="running", processed_files=1, current_file="a.pdf")
    found = db.get_upload_job(job)
    assert (found["status"], found["processed_files"], found["current_file"]) == ("running", 1, "a.pdf")

    db.update_upload_job(job, status="completed")
    assert db.get_active_upload_job("c1") is None
    assert db.get_all_active_upload_jobs() == []
    assert db.get_upload_job(999999) is None


def test_reindex_jobs(db):
    assert db.get_latest_reindex_job() is None
    job = db.create_reindex_job({"chunk_size": 900})
    assert db.get_reindex_job(job)["id"] == job
    assert db.get_latest_reindex_job()["id"] == job
    active = db.get_active_reindex_job()
    assert active is None or active["id"] == job

    db.update_reindex_job(job, status="running", processed_documents=2)
    assert db.get_active_reindex_job()["id"] == job
    assert db.get_reindex_job(job)["processed_documents"] == 2

    db.update_reindex_job(job, status="completed")
    assert db.get_active_reindex_job() is None


# ── AI preferences, search history, usage ───────────────────────────────────


def test_ai_preferences(db):
    db.set_ai_preferences(default_provider="anthropic", rerank_enabled=False)
    found = db.get_ai_preferences()
    assert found["default_provider"] == "anthropic"
    assert not found["rerank_enabled"]
    db.set_ai_preferences(default_provider="openai")
    assert db.get_ai_preferences()["default_provider"] == "openai"


def test_search_history(db):
    search = db.add_search_history("what is it", 5, 2, ai_provider="openai", ai_used=True,
                                   results=[{"chunk_id": "c"}], execution_time_ms=12)
    assert db.get_search_by_id(search)["query"] == "what is it"
    assert db.get_search_by_id(999999) is None
    assert [s["id"] for s in db.get_search_history(limit=5)] == [search]
    db.delete_old_search_history(1)
    assert db.get_search_by_id(search) is not None


def test_chat_usage(db):
    db.add_chat_usage("a@example.org", "c1", "openai", "gpt", 10, 5, tool_calls=2, duration_ms=100)
    db.add_chat_usage("a@example.org", "c1", "openai", "gpt", 1, 1, cache_hit=True)
    db.add_chat_usage(None, "c1", "openai", None, 2, 2)
    since = (utcnow() - timedelta(hours=1)).isoformat()

    by_user = {row["grouped_by"]: row for row in db.get_usage_summary(since, group_by="user")}
    assert by_user["a@example.org"]["turns"] == 2
    assert by_user["a@example.org"]["input_tokens"] == 11
    assert by_user["a@example.org"]["cache_hits"] == 1
    assert by_user["anonymous"]["turns"] == 1

    assert sum(row["turns"] for row in db.get_usage_summary(since, group_by="day")) == 3

    mine = db.get_user_usage_since("a@example.org", since)
    assert mine["input_tokens"] + mine["output_tokens"] == 17
    assert db.get_user_usage_since(None, since)["input_tokens"] == 2
    assert db.get_user_usage_since("nobody@example.org", since)["input_tokens"] == 0

    assert db.delete_old_chat_usage(1) == 0


def test_api_apps(db):
    row = {column: None for column in db.API_APP_COLUMNS}
    row.update(id="app-1", name="reports", key_hash="hash-1", key_prefix="clio_app_abc", collections='["c1"]',
               can_write=True, search_per_minute=500, created_at="2026-11-06T00:00:00", oidc_subject="svc-1")
    db.create_api_app(row)
    db.create_api_app({**row, "id": "app-2", "name": "other", "key_hash": None, "oidc_subject": None})

    found = db.get_api_app("app-1")
    assert found["name"] == "reports" and bool(found["can_write"]) is True and found["search_per_minute"] == 500
    assert db.get_api_app("nope") is None
    assert {a["id"] for a in db.list_api_apps()} == {"app-1", "app-2"}
    assert db.get_api_app_by_subject("svc-1")["id"] == "app-1"
    assert db.get_api_app_by_subject("nope") is None
    assert db.get_api_app_by_key_hash("hash-1")["id"] == "app-1"

    db.update_api_app("app-1", {"key_hash": "hash-2", "previous_key_hash": "hash-1", "can_write": False})
    assert db.get_api_app_by_key_hash("hash-1")["id"] == "app-1"
    assert db.get_api_app_by_key_hash("hash-2")["id"] == "app-1"
    assert bool(db.get_api_app("app-1")["can_write"]) is False

    db.update_api_app("app-1", {})
    with pytest.raises(ValueError):
        db.update_api_app("app-1", {"name": "renamed; DROP TABLE api_apps"})
    assert db.get_api_app("app-1")["name"] == "reports"


def test_every_abstract_method_is_exercised_here():
    """A new database method has to join this file, or it is untested on Postgres."""
    root = Path(__file__).parent
    source = (root.parent / "services" / "db_backend.py").read_text(encoding="utf-8")
    abstract = {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
        and any(getattr(d, "id", getattr(d, "attr", "")) == "abstractmethod" for d in node.decorator_list)
    }
    # Reached through get_config, set_config and get_all_config.
    abstract -= {"_read_all_config"}
    this_file = Path(__file__).read_text(encoding="utf-8")
    assert not {name for name in abstract if f"db.{name}(" not in this_file and name not in this_file}


# ── The pool ────────────────────────────────────────────────────────────────


def test_more_callers_than_connections_all_succeed():
    """Before the pool waited, the eleventh concurrent call raised PoolError."""
    if not POSTGRES_URL:
        pytest.skip("CLIO_TEST_POSTGRES_URL is not set")
    backend = _postgres_backend(max_conn=3, wait_timeout=30)
    backend.set_config("ai_model", "llama")
    failures, barrier = [], threading.Barrier(24)

    def call():
        barrier.wait()
        try:
            for _ in range(10):
                assert backend.get_config("ai_model") == "llama"
        except Exception as e:  # noqa: BLE001 - recorded for the assert below
            failures.append(e)

    threads = [threading.Thread(target=call) for _ in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stats = backend.pool_stats()
    backend.pool.closeall()

    assert not failures
    assert stats["in_use"] == 0
    assert stats["timeouts"] == 0


def test_a_full_pool_refuses_after_the_timeout_and_counts_it(pg):
    held = [pg._conn() for _ in range(3)]
    try:
        started = time.monotonic()
        with pytest.raises(DatabaseBusy):
            pg._conn()
        assert time.monotonic() - started >= 0.25
        stats = pg.pool_stats()
        assert stats["in_use"] == 3
        assert stats["waits"] == 1
        assert stats["timeouts"] == 1
    finally:
        for conn in held:
            pg._put(conn)
    assert pg.get_config("missing", "still works") == "still works"


def test_a_waiter_gets_the_next_free_connection(pg):
    pg.wait_timeout = 5
    held = [pg._conn() for _ in range(3)]
    threading.Timer(0.1, pg._put, args=(held[0],)).start()
    conn = pg._conn()
    pg._put(conn)
    pg._put(held[1])
    pg._put(held[2])
    assert pg.pool_stats()["in_use"] == 0


def test_returned_connections_stay_open_for_the_next_caller(pg):
    """Closing them means a fresh login on nearly every call under load."""
    held = [pg._conn() for _ in range(3)]
    pids = {conn.info.backend_pid for conn in held}
    for conn in held:
        pg._put(conn)

    again = [pg._conn() for _ in range(3)]
    try:
        assert {conn.info.backend_pid for conn in again} == pids
    finally:
        for conn in again:
            pg._put(conn)


def test_a_failed_query_gives_its_connection_back(pg):
    import psycopg2

    for _ in range(6):
        conn = pg._conn()
        try:
            with pytest.raises(psycopg2.Error):
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM no_such_table")
        finally:
            pg._put(conn)
    assert pg.pool_stats()["in_use"] == 0
    assert pg.get_config("missing", "fine") == "fine"


def test_listing_collections_holds_no_connection_while_reading_sqlite(pg, monkeypatch):
    """A long list opens SQLite files, and must not sit on a pooled connection."""
    from services import db_postgres

    pg.create_collection("One")
    seen = []

    def count(collection_id):
        seen.append(pg.pool_stats()["in_use"])
        return 0

    monkeypatch.setattr(db_postgres, "_get_document_count_from_metadata", count)
    pg.get_all_collections()
    pg.get_collection("default")

    assert seen
    assert set(seen) == {0}


def _terminate_every_other_connection():
    import psycopg2

    conn = psycopg2.connect(POSTGRES_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE pid <> pg_backend_pid() AND datname = current_database()"
        )
    conn.close()
    time.sleep(0.2)


def test_no_request_fails_after_the_server_drops_its_connections():
    """What a Postgres restart or failover does to the idle connections."""
    if not POSTGRES_URL:
        pytest.skip("CLIO_TEST_POSTGRES_URL is not set")
    backend = _postgres_backend(max_conn=5)
    backend.set_config("ai_model", "llama")
    held = [backend._conn() for _ in range(3)]
    for conn in held:
        backend._put(conn)

    _terminate_every_other_connection()

    for _ in range(5):
        assert backend.get_config("ai_model") == "llama"
    assert backend.pool_stats()["in_use"] == 0
    backend.pool.closeall()


def test_busy_database_answers_503_with_retry_after(monkeypatch):
    from fastapi.testclient import TestClient

    import main
    from services.app_database import app_db

    def busy(*args, **kwargs):
        raise DatabaseBusy("All 20 database connections stayed busy for 10s.")

    monkeypatch.setattr(app_db, "get_all_collections", busy)
    response = TestClient(main.app, raise_server_exceptions=False).get("/api/collections")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.json()["code"] == "database_busy"

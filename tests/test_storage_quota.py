"""Per-collection storage cap: accounting, the reservation ledger, and the
HTTP contract at every ingest path."""

import io

import pytest

import config
from services import storage_quota
from services.metadata_store import MetadataStore
from services.storage_quota import StorageLimitExceeded, format_bytes
from services.metadata_store import SCHEMA_VERSION


@pytest.fixture(autouse=True)
def _clean_ledger():
    storage_quota._reset_for_tests()
    yield
    storage_quota._reset_for_tests()


@pytest.fixture()
def limit(monkeypatch):
    """A tiny cap so tests stay fast; usage is faked per test."""
    monkeypatch.setattr(config.settings, "collection_storage_limit_bytes", 1000)
    return 1000


def _fake_usage(monkeypatch, value):
    monkeypatch.setattr(storage_quota, "usage_bytes", lambda cid: value)


# ── Accounting in the metadata store ───────────────────────────────────


def test_metadata_store_sums_file_size(tmp_path):
    store = MetadataStore(tmp_path / "meta.db")
    store.add_document("a", "a.txt", 1, 1, "2026-01-01T00:00:00", file_size=300)
    store.add_document("b", "b.txt", 1, 1, "2026-01-01T00:00:00", file_size=200)
    store.add_document("c", "c.txt", 1, 1, "2026-01-01T00:00:00")  # unknown size: not counted
    assert store.get_storage_bytes() == 500
    assert store.get_document_stats()["storage_bytes"] == 500


def test_legacy_rows_backfill_from_disk(tmp_path):
    """A pre-v3.4 database gains file_size from the files still on disk."""
    import sqlite3

    coll = tmp_path / "collections" / "c1"
    docs = coll / "documents"
    docs.mkdir(parents=True)
    (docs / "old.txt").write_bytes(b"x" * 123)
    db = coll / "indexes" / "metadata.db"
    db.parent.mkdir()
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE schema_info (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO schema_info VALUES ('version', '3.3')")
        conn.execute("""CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, document_id TEXT, filename TEXT,
                        page_number INTEGER, chunk_index INTEGER, text TEXT, source_format TEXT,
                        extraction_method TEXT, csv_row_number INTEGER, csv_columns TEXT, csv_values TEXT,
                        created_at TIMESTAMP)""")
        conn.execute("""CREATE TABLE documents (document_id TEXT PRIMARY KEY, filename TEXT NOT NULL,
                        num_pages INTEGER NOT NULL, num_chunks INTEGER NOT NULL, upload_timestamp TEXT NOT NULL,
                        source_format TEXT, extraction_method TEXT, embedding_model TEXT, chunk_size INTEGER,
                        chunk_overlap INTEGER, schema_version TEXT, injection_warnings TEXT, source_path TEXT,
                        source_type TEXT DEFAULT 'upload', uploaded_by TEXT, content_hash TEXT, sensitivity TEXT,
                        policy_status TEXT DEFAULT 'clear', policy_flags TEXT)""")
        conn.execute("""INSERT INTO documents (document_id, filename, num_pages, num_chunks, upload_timestamp)
                        VALUES ('d1', 'old.txt', 1, 1, '2026-01-01T00:00:00')""")
        conn.execute("""INSERT INTO documents (document_id, filename, num_pages, num_chunks, upload_timestamp)
                        VALUES ('d2', 'gone.txt', 1, 1, '2026-01-01T00:00:00')""")
        conn.commit()

    store = MetadataStore(db)
    assert store.get_storage_bytes() == 123
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT value FROM schema_info WHERE key='version'").fetchone()[0] == SCHEMA_VERSION


# ── The cap itself ──────────────────────────────────────────────────────


def test_zero_means_unlimited(monkeypatch):
    monkeypatch.setattr(config.settings, "collection_storage_limit_bytes", 0)
    _fake_usage(monkeypatch, 10 ** 12)
    storage_quota.check("c", 10 ** 12)  # no raise
    with storage_quota.reserve("c", 10 ** 12):
        pass
    assert storage_quota.describe("c")["storage_unlimited"] is True


def test_check_refuses_past_the_cap(limit, monkeypatch):
    _fake_usage(monkeypatch, 900)
    storage_quota.check("c", 100)
    with pytest.raises(StorageLimitExceeded) as ei:
        storage_quota.check("c", 101, filename="big.pdf")
    err = ei.value
    assert err.limit_bytes == 1000 and err.usage_bytes == 900 and err.incoming_bytes == 101
    assert "big.pdf" in str(err) and "storage limit" in str(err)
    detail = err.to_detail()
    assert detail["error"] == "storage_limit_exceeded"
    assert detail["storage_limit_bytes"] == 1000


def test_reservations_count_until_released(limit, monkeypatch):
    """Two in-flight uploads into one collection see each other's bytes."""
    _fake_usage(monkeypatch, 0)
    with storage_quota.reserve("c", 600):
        assert storage_quota.reserved_bytes("c") == 600
        with pytest.raises(StorageLimitExceeded):
            storage_quota.check("c", 500)
        with storage_quota.reserve("c", 400):
            assert storage_quota.reserved_bytes("c") == 1000
    assert storage_quota.reserved_bytes("c") == 0
    storage_quota.check("c", 1000)


def test_reservation_released_on_failure(limit, monkeypatch):
    _fake_usage(monkeypatch, 0)
    with pytest.raises(RuntimeError):
        with storage_quota.reserve("c", 900):
            raise RuntimeError("indexing blew up")
    assert storage_quota.reserved_bytes("c") == 0


def test_take_and_release_pair(limit, monkeypatch):
    _fake_usage(monkeypatch, 500)
    held = storage_quota.take("c", 400, "f")
    assert held == 400
    with pytest.raises(StorageLimitExceeded):
        storage_quota.take("c", 200, "g")
    storage_quota.release("c", held)
    assert storage_quota.take("c", 200, "g") == 200


def test_describe_numbers(limit, monkeypatch):
    _fake_usage(monkeypatch, 250)
    d = storage_quota.describe("c")
    assert d == {
        "storage_bytes": 250, "storage_limit_bytes": 1000, "storage_remaining_bytes": 750,
        "storage_percent": 25.0, "storage_unlimited": False,
    }


def test_format_bytes():
    assert format_bytes(0) == "0 B"
    assert format_bytes(5 * 1024 ** 3) == "5 GB"
    assert format_bytes(int(1.5 * 1024 ** 3)) == "1.5 GB"
    assert format_bytes(850 * 1024 ** 2) == "850 MB"


# ── HTTP contract ───────────────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Open-mode app with a fake-embedding indexer as the default collection
    (same harness as tests/test_governance.py) and a 1000-byte cap."""
    from fastapi.testclient import TestClient
    from api import deps
    from services.app_database import SQLiteBackend, app_db
    from services.collection_service import collection_service
    from services.indexer_manager import indexer_manager
    from tests.test_governance import _make_indexer
    import main

    monkeypatch.setattr(config.settings, "collection_storage_limit_bytes", 1000)
    monkeypatch.setattr(app_db, "db_path", tmp_path / "app.db")
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    monkeypatch.setattr(collection_service, "base_dir", tmp_path / "collections")
    monkeypatch.setitem(indexer_manager._indexers, "default", _make_indexer(tmp_path))
    monkeypatch.setattr(deps, "_initialized", True)
    return TestClient(main.app)


def test_upload_over_cap_is_413(client, monkeypatch):
    _fake_usage(monkeypatch, 990)
    r = client.post(
        "/documents/upload",
        files=[("files", ("note.txt", io.BytesIO(b"x" * 50), "text/plain"))],
    )
    assert r.status_code == 413, r.text
    assert "storage limit" in r.json()["detail"]


def test_upload_under_cap_is_indexed_and_counted(client):
    """A file that fits is indexed, its size recorded, and usage reflects it."""
    body = b"hello world " * 5
    r = client.post(
        "/documents/upload",
        files=[("files", ("note.txt", io.BytesIO(body), "text/plain"))],
    )
    assert r.status_code == 201, r.text
    stats = client.get("/api/collections/default/stats").json()
    assert stats["storage_bytes"] == len(body)
    assert stats["storage_limit_bytes"] == 1000
    # The reservation was released once the row committed.
    assert storage_quota.reserved_bytes("default") == 0
    # A second file that no longer fits is refused with the numbers.
    r = client.post(
        "/documents/upload",
        files=[("files", ("big.txt", io.BytesIO(b"y" * 1000), "text/plain"))],
    )
    assert r.status_code == 413
    assert "big.txt" in r.json()["detail"]


def test_staged_upload_over_cap_is_413(client, monkeypatch):
    _fake_usage(monkeypatch, 900)
    r = client.post(
        "/documents/upload-staged",
        files=[
            ("files", ("a.txt", io.BytesIO(b"x" * 60), "text/plain")),
            ("files", ("b.txt", io.BytesIO(b"x" * 60), "text/plain")),
        ],
    )
    assert r.status_code == 413
    body = r.json()["detail"]
    assert body["error"] == "storage_limit_exceeded"
    assert body["incoming_bytes"] == 120


def test_index_local_async_over_cap_is_413(client, monkeypatch, tmp_path):
    _fake_usage(monkeypatch, 950)
    big = tmp_path / "big.txt"
    big.write_bytes(b"x" * 100)
    r = client.post("/documents/index-local-async", json={"file_paths": [str(big)], "collection_id": "default"})
    assert r.status_code == 413


def test_stats_report_storage(client, monkeypatch):
    r = client.get("/api/collections/default/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["storage_limit_bytes"] == 1000
    assert "storage_bytes" in body and "storage_percent" in body


def test_collections_list_carries_storage_bytes(client):
    r = client.get("/api/collections")
    assert r.status_code == 200
    for c in r.json()["collections"]:
        assert "storage_bytes" in c


def test_user_me_exposes_limit(client):
    r = client.get("/api/user/me")
    assert r.status_code == 200
    assert r.json()["collection_storage_limit_bytes"] == 1000

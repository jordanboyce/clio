"""A collection keeps the model that wrote its vectors when the default changes.

An image built around another embedding model (a -bge image) starts with
EMBEDDING_MODEL set to that model. Collections indexed earlier must keep
theirs: searching MiniLM vectors with bge-small queries gives no error (both
are 384-dimensional), only quietly wrong results. New collections, though,
should take the server's model, and clone is how an old one moves.
"""

import sqlite3

import pytest

import api.collections as collections_api
import services.app_database as app_database
from services.app_database import SQLiteBackend, app_db
from services.collection_service import collection_service

MINILM = "all-MiniLM-L6-v2"
BGE = "BAAI/bge-small-en-v1.5"


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(app_database.settings, "data_dir", tmp_path)
    monkeypatch.setattr(app_database.settings, "embedding_model", MINILM)
    return tmp_path


def _default_model(db: SQLiteBackend) -> str:
    return db.get_collection("default")["embedding_model"]


def _index_documents(data_dir, collection_id="default", rows=1):
    indexes = data_dir / "collections" / collection_id / "indexes"
    indexes.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(indexes / "metadata.db") as conn:
        conn.execute("CREATE TABLE documents (document_id TEXT PRIMARY KEY, filename TEXT)")
        conn.executemany("INSERT INTO documents VALUES (?, ?)",
                         [(f"d{i}", f"f{i}.txt") for i in range(rows)])


def test_default_collection_with_documents_keeps_minilm(data_dir, monkeypatch):
    SQLiteBackend(data_dir / "app.db")
    _index_documents(data_dir)

    monkeypatch.setattr(app_database.settings, "embedding_model", BGE)
    db = SQLiteBackend(data_dir / "app.db")

    assert _default_model(db) == MINILM


def test_empty_default_collection_follows_the_new_default(data_dir, monkeypatch):
    SQLiteBackend(data_dir / "app.db")
    _index_documents(data_dir, rows=0)

    monkeypatch.setattr(app_database.settings, "embedding_model", BGE)
    db = SQLiteBackend(data_dir / "app.db")

    assert _default_model(db) == BGE


def test_unreadable_index_is_treated_as_holding_documents(data_dir, monkeypatch):
    SQLiteBackend(data_dir / "app.db")
    indexes = data_dir / "collections" / "default" / "indexes"
    indexes.mkdir(parents=True)
    (indexes / "metadata.db").write_bytes(b"not a sqlite file" * 64)

    monkeypatch.setattr(app_database.settings, "embedding_model", BGE)
    db = SQLiteBackend(data_dir / "app.db")

    assert _default_model(db) == MINILM


def test_fresh_install_default_collection_takes_the_configured_model(data_dir, monkeypatch):
    monkeypatch.setattr(app_database.settings, "embedding_model", BGE)
    assert _default_model(SQLiteBackend(data_dir / "app.db")) == BGE


@pytest.fixture()
def fresh_db(data_dir):
    old_db_path, old_base_dir = app_db.db_path, collection_service.base_dir
    app_db.db_path = data_dir / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    collection_service.base_dir = data_dir / "collections"
    yield app_db
    app_db.db_path, collection_service.base_dir = old_db_path, old_base_dir


def test_new_collection_without_a_model_takes_the_servers(fresh_db, monkeypatch):
    if not isinstance(fresh_db, SQLiteBackend):
        pytest.skip("SQLite backend only")
    monkeypatch.setattr(app_database.settings, "embedding_model", BGE)

    created = collections_api.create_collection({"name": "Portal"}, user_id=None)

    assert created["embedding_model"] == BGE


def test_new_collection_keeps_an_explicit_model(fresh_db, monkeypatch):
    if not isinstance(fresh_db, SQLiteBackend):
        pytest.skip("SQLite backend only")
    monkeypatch.setattr(app_database.settings, "embedding_model", BGE)

    created = collections_api.create_collection({"name": "Old", "embedding_model": MINILM}, user_id=None)

    assert created["embedding_model"] == MINILM


def _clone_setup(monkeypatch, tmp_path):
    from services.upload_service import upload_service

    source_file = tmp_path / "note.txt"
    source_file.write_text("hello")
    monkeypatch.setattr(collection_service, "source_file_paths", lambda _id: [source_file])
    started = {}

    def fake_start(**kwargs):
        started.update(kwargs)
        return "job-1"

    monkeypatch.setattr(upload_service, "start_local_index", fake_start)
    return started


def test_clone_moves_a_collection_to_another_model(fresh_db, monkeypatch, tmp_path):
    if not isinstance(fresh_db, SQLiteBackend):
        pytest.skip("SQLite backend only")
    source = collection_service.create_collection(name="MiniLM one", embedding_model=MINILM)
    started = _clone_setup(monkeypatch, tmp_path)

    clone = collection_service.clone_collection(source["id"], owner_id="default", embedding_model=BGE)

    assert clone["embedding_model"] == BGE
    assert started["collection_id"] == clone["id"]
    assert collection_service.get_collection(source["id"])["embedding_model"] == MINILM


def test_clone_keeps_the_source_model_by_default(fresh_db, monkeypatch, tmp_path):
    if not isinstance(fresh_db, SQLiteBackend):
        pytest.skip("SQLite backend only")
    monkeypatch.setattr(app_database.settings, "embedding_model", BGE)
    source = collection_service.create_collection(name="MiniLM one", embedding_model=MINILM)
    _clone_setup(monkeypatch, tmp_path)

    clone = collection_service.clone_collection(source["id"], owner_id="default")

    assert clone["embedding_model"] == MINILM


def test_clone_refuses_a_model_that_is_not_a_name(fresh_db, monkeypatch, tmp_path):
    if not isinstance(fresh_db, SQLiteBackend):
        pytest.skip("SQLite backend only")
    source = collection_service.create_collection(name="MiniLM one", embedding_model=MINILM)
    _clone_setup(monkeypatch, tmp_path)

    with pytest.raises(ValueError):
        collection_service.clone_collection(source["id"], owner_id="default", embedding_model=["x"])

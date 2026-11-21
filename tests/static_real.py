"""A static export made with Clio's real model and the real browser runtime.

The fake embedder the other tests use cannot run in a browser, so search by
meaning, the probe and anything matched by meaning (published answers) are
tested here against all-MiniLM-L6-v2 from a fastembed cache and the
transformers.js runtime the image carries. Both must be on disk; nothing is
downloaded. Tests using ``real_env`` skip when they are not:

    CLIO_TEST_STATIC_RUNTIME   a folder holding the runtime: transformers.min.js,
                               the ort-wasm files and runtime.json (copy
                               /opt/clio/static-search/vendor out of a Clio image)
    CLIO_TEST_FASTEMBED        a fastembed cache with all-MiniLM-L6-v2
                               (default: data/models/fastembed in the repository)
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODEL = "all-MiniLM-L6-v2"
SAMPLES = ROOT / "frontend" / "public" / "samples"


def runtime_dir():
    d = os.environ.get("CLIO_TEST_STATIC_RUNTIME", "").strip()
    return Path(d) if d and (Path(d) / "transformers.min.js").is_file() else None


def fastembed_source():
    d = Path(os.environ.get("CLIO_TEST_FASTEMBED", "").strip() or ROOT / "data" / "models" / "fastembed")
    return d if (d / "models--Qdrant--all-MiniLM-L6-v2-onnx").is_dir() else None


@pytest.fixture(scope="session")
def real_model_cache(tmp_path_factory):
    source = fastembed_source()
    if runtime_dir() is None or source is None:
        pytest.skip("set CLIO_TEST_STATIC_RUNTIME (and a fastembed cache with all-MiniLM-L6-v2) for real-model tests")
    cache = tmp_path_factory.mktemp("fastembed")
    shutil.copytree(source / "models--Qdrant--all-MiniLM-L6-v2-onnx", cache / "models--Qdrant--all-MiniLM-L6-v2-onnx",
                    symlinks=False)
    return cache


@pytest.fixture()
def real_env(real_model_cache, tmp_path, monkeypatch):
    """Like tests/test_collection_bundle.env, with the real model."""
    import config
    from services import collection_bundle as cb
    from services import static_export as se
    from services.app_database import SQLiteBackend, app_db
    from services.collection_service import collection_service
    from services.indexer_manager import indexer_manager

    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(real_model_cache))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    for s in {id(config.settings): config.settings, id(cb.settings): cb.settings}.values():
        monkeypatch.setattr(s, "data_dir", tmp_path)
        monkeypatch.setattr(s, "embedding_provider", "local")
        monkeypatch.setattr(s, "embedding_model", MODEL)
    monkeypatch.setattr(app_db, "db_path", tmp_path / "app.db")
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    monkeypatch.setattr(collection_service, "base_dir", tmp_path / "collections")
    monkeypatch.setattr(indexer_manager, "_indexers", {})
    monkeypatch.setattr(se, "site_runtime_dirs", lambda: [runtime_dir()])
    return tmp_path


def sample_collection(name="Sample: equipment policy"):
    """The four sample sources the welcome screen offers, indexed with the real model, labelled Public."""
    from services.app_database import SQLiteBackend, app_db
    from services.collection_service import collection_service
    from services.indexer_manager import indexer_manager

    cid = collection_service.create_collection(name=name, embedding_model=MODEL,
                                               description="Four fictional records for trying Clio.")["id"]
    indexer = indexer_manager.get_indexer(cid)
    docs = collection_service.get_documents_path(cid)
    ids = {}
    for f in sorted(SAMPLES.iterdir()):
        shutil.copyfile(f, docs / f.name)
        ids[f.name] = indexer.index_document(docs / f.name, f.name, collection_id=cid).document_id
    indexer.save_index()
    app_db.update_collection(cid, sensitivity="public")
    return cid, ids


def add_source(cid, name, text):
    from services.collection_service import collection_service
    from services.indexer_manager import indexer_manager

    docs = collection_service.get_documents_path(cid)
    (docs / name).write_text(text, encoding="utf-8")
    indexer = indexer_manager.get_indexer(cid)
    doc_id = indexer.index_document(docs / name, name, collection_id=cid).document_id
    indexer.save_index()
    return doc_id


def passages_of(cid, document_id):
    import sqlite3

    from services.collection_service import collection_service

    db = collection_service.get_indexes_path(cid) / "metadata.db"
    with sqlite3.connect(db) as conn:
        return [{"id": r[0], "text": r[1]} for r in conn.execute(
            "SELECT chunk_id, text FROM chunks WHERE document_id=? ORDER BY page_number, chunk_index", (document_id,))]

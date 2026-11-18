"""F18: a source keeps its type through a re-index and a bundle import.

A pasted note ('text'), a synced file ('folder_sync'), a link ('url') and an
agent's note ('agent_note') are all stored copies in the documents folder. A
re-index used to bring every one of them back as a plain 'upload' (only
'local_reference' kept its type), and a bundle import reset every type.
"""

import sqlite3

import services.mcp_server as mcp
from services import collection_bundle as cb
from services import reindex_service as rs
from services.app_database import app_db
from services.collection_service import collection_service
from services.indexer_manager import indexer_manager
from tests.test_collection_bundle import _wait, env  # noqa: F401
from tests.test_mcp_write import fresh_db, indexer  # noqa: F401

# filename -> (source_type, source_path) every copy of the collection must keep.
TYPED = {
    "pasted.txt": ("text", None),
    "synced.md": ("folder_sync", "/home/someone/notes/synced.md"),
    "linked.html": ("url", "https://example.org/policy"),
}


def _types(store) -> dict:
    return {d["filename"]: (d.get("source_type") or "upload", d.get("source_path"))
            for d in store.list_documents()}


def _typed_collection(indexer, documents_dir):
    """One source of each stored type, plus a person's upload and an agent's note."""
    store = indexer.vector_store.metadata_store
    for name, (source_type, source_path) in {**TYPED, "upload.txt": ("upload", None)}.items():
        path = documents_dir / name
        path.write_text(f"{name} holds the rota for the Boise office. " * 10, encoding="utf-8")
        meta = indexer.index_document(path, name, collection_id="default")
        if source_type != "upload":
            store.update_document_source(meta.document_id, source_path=source_path, source_type=source_type)
    mcp.write_document_sync("agent.md", "# Agent notes\n\nThe rota moves on Fridays.\n")
    expected = {**TYPED, "upload.txt": ("upload", None), "agent.md": ("agent_note", None)}
    assert _types(store) == expected
    return expected


# ── Re-index ──────────────────────────────────────────────────────────────


class _FakeStore:
    def __init__(self, docs):
        self._docs = docs

    def list_documents(self):
        return self._docs


def test_the_reindex_list_keeps_each_stored_sources_type(tmp_path):
    docs = [{"document_id": name, "filename": name, "source_type": t, "source_path": p}
            for name, (t, p) in {**TYPED, "agent.md": ("agent_note", None)}.items()]
    docs.append({"document_id": "u", "filename": "upload.txt", "source_type": None, "source_path": None})
    for d in docs:
        (tmp_path / d["filename"]).write_text("x", encoding="utf-8")

    listed = rs.ReindexService()._get_documents_to_reindex(_FakeStore(docs), tmp_path)

    # Every stored copy is read from the documents folder, whatever its type.
    assert {d["path"] for d in listed} == {tmp_path / d["filename"] for d in docs}
    assert {d["path"].name: (d["source_type"], d["source_path"]) for d in listed} == {
        **TYPED, "agent.md": ("agent_note", None), "upload.txt": ("upload", None),
    }


def test_a_full_reindex_keeps_every_source_type(indexer):
    documents_dir = indexer_manager.get_documents_path("default")
    expected = _typed_collection(indexer, documents_dir)
    store = indexer.vector_store.metadata_store
    service = rs.ReindexService()

    listed = service._get_documents_to_reindex(store, documents_dir, strict=True)
    # A full re-index clears the index before it adds anything back.
    for doc in listed:
        indexer.delete_document(doc["document_id"])
    assert store.list_documents() == []

    job_id = app_db.create_reindex_job({"collection_id": "default"})
    assert service._reindex_documents(job_id, indexer, indexer.vector_store, listed) == []
    assert _types(store) == expected


def test_reindexing_one_document_keeps_its_type(indexer):
    documents_dir = indexer_manager.get_documents_path("default")
    expected = _typed_collection(indexer, documents_dir)
    store = indexer.vector_store.metadata_store
    service = rs.ReindexService()

    for doc in service._get_documents_to_reindex(store, documents_dir, strict=True):
        service.replace_document(indexer, doc, "default")
    assert _types(store) == expected

    # And through the MCP tool, which builds its own document record.
    pasted = next(d for d in store.list_documents() if d["filename"] == "pasted.txt")
    mcp.reindex_document_sync(pasted["document_id"])
    assert _types(store) == expected


# ── Bundle import ─────────────────────────────────────────────────────────


def _bundle_types(collection_id) -> dict:
    db = collection_service.get_indexes_path(collection_id) / "metadata.db"
    with sqlite3.connect(db) as conn:
        return {f: (t, p) for f, t, p in conn.execute(
            "SELECT filename, source_type, source_path FROM documents")}


def test_a_bundle_import_keeps_the_types_that_travel(env):
    col = collection_service.create_collection(name="Rota", embedding_model="fake-embed")
    cid = col["id"]
    ix = indexer_manager.get_indexer(cid)
    docs_dir = collection_service.get_documents_path(cid)
    sources = {
        **TYPED,
        "agent.md": ("agent_note", None),
        "upload.txt": ("upload", None),
        "referenced.txt": ("local_reference", str(env / "elsewhere" / "referenced.txt")),
        "odd-link.html": ("url", "file:///etc/passwd"),
        "no-link.html": ("url", None),
    }
    store = ix.vector_store.metadata_store
    for name, (source_type, source_path) in sources.items():
        path = docs_dir / name
        path.write_text(f"{name} holds the rota for the Boise office. " * 10, encoding="utf-8")
        meta = ix.index_document(path, name, collection_id=cid)
        if source_type != "upload":
            store.update_document_source(meta.document_id, source_path=source_path, source_type=source_type)
    ix.save_index()

    path, _ = cb.export_collection(cid, include_sources=True)
    result = cb.import_collection(path, owner_id="default", name="Copy")
    assert _wait(result["job_id"])["status"] == "completed"

    assert _bundle_types(result["id"]) == {
        # The stored copy is the source: the type travels.
        "pasted.txt": ("text", None),
        "agent.md": ("agent_note", None),
        "linked.html": ("url", "https://example.org/policy"),
        # A path on the exporter's machine means nothing here, and must never
        # be followed: these arrive as the importer's own uploads.
        "synced.md": ("upload", None),
        "referenced.txt": ("upload", None),
        # Only a web link is kept as a link.
        "odd-link.html": ("upload", None),
        "no-link.html": ("upload", None),
        "upload.txt": ("upload", None),
    }

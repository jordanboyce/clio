"""Re-indexing only the files of one custom type, end to end.

A real SQLite metadata store, a real VectorStore/FAISS index and a real
DocumentIndexer behind the real indexer manager (only the embedding model is
the deterministic fake used by tests/test_governance.py). The claims worth
proving are the ones a silent mistake would break:

* the files of the type pick up the changed profile;
* every other document keeps the very same rows, ids and vectors;
* the live in-memory index and the saved one agree afterwards;
* the endpoint refuses what would lose data, and keeps its owner gate.
"""

import json
import sqlite3

import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import config
from middleware.user_context import reset_request_user, set_request_user
from services import custom_file_type_usage as usage_mod
from services import custom_file_types as cft
from services.app_database import SQLiteBackend, app_db
from services.collection_service import collection_service
from services.indexer_manager import indexer_manager
from services.reindex_service import normalize_reindex_extensions, reindex_service
from services.vector_store import VectorStore
from tests.test_governance import FakeEmbedder

PUMPS = json.dumps({"pumps": [
    {"id": "9f1c2d3e-4b5a-6789-abcd-ef0123456789", "name": "Pump A", "site": "North", "psi": 62},
    {"id": "0a1b2c3d-4b5a-6789-abcd-ef0123456789", "name": "Pump B", "site": "South", "psi": 48}]})
NOTES = "# Maintenance\n\nGrease the bearings every quarter. " * 12

ALICE, BOB = "alice@example.com", "bob@example.com"


class SignedEmbedder(FakeEmbedder):
    signature = "local::fake-embed"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for s in {id(config.settings): config.settings}.values():
        monkeypatch.setattr(s, "data_dir", tmp_path)
        monkeypatch.setattr(s, "embedding_provider", "local")
        monkeypatch.setattr(s, "embedding_model", "fake-embed")
    monkeypatch.setattr(app_db, "db_path", tmp_path / "app.db")
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    monkeypatch.setattr(collection_service, "base_dir", tmp_path / "collections")
    monkeypatch.setattr(indexer_manager, "_indexers", {})
    monkeypatch.setattr(indexer_manager, "_get_embedding_service", lambda model: SignedEmbedder())
    monkeypatch.setattr(cft.registry, "_path_fn", lambda: tmp_path / "custom_file_types.json")
    monkeypatch.setattr(cft.registry, "_ttl", 0)
    monkeypatch.setattr(cft.registry, "_stamp", None)
    from api import deps
    monkeypatch.setattr(deps, "_initialized", True)
    # Run the job body inline: a TestClient's event loop does not outlive the request.
    monkeypatch.setattr(reindex_service, "_spawn", lambda fn, **kw: fn(**kw))
    return tmp_path


def _collection(name="Plant", owner_id="default"):
    col = collection_service.create_collection(name=name, embedding_model="fake-embed", owner_id=owner_id)
    return col["id"]


def _index(cid, filename, text):
    indexer = indexer_manager.get_indexer(cid)
    path = collection_service.get_documents_path(cid) / filename
    path.write_text(text, encoding="utf-8")
    meta = indexer.index_document(path, filename, collection_id=cid)
    indexer.save_index()
    return meta.document_id


def _snapshot(cid, doc_id):
    """Everything stored for one document: rows with their FAISS ids, and the vectors."""
    indexer = indexer_manager.get_indexer(cid)
    db = collection_service.get_indexes_path(cid) / "metadata.db"
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT id, chunk_id, page_number, chunk_index, text FROM chunks "
            "WHERE document_id=? ORDER BY id", (doc_id,)).fetchall()
    vectors = [indexer.vector_store.index.reconstruct(int(r[0])).tolist() for r in rows]
    return rows, vectors


def _text(cid, doc_id):
    store = indexer_manager.get_indexer(cid).vector_store.metadata_store
    return "\n".join(c["text"] for c in store.get_chunks_by_document(doc_id))


def _consistent(cid):
    """FAISS and SQLite hold the same chunk ids, in memory and as saved on disk."""
    indexer = indexer_manager.get_indexer(cid)
    db = collection_service.get_indexes_path(cid) / "metadata.db"
    with sqlite3.connect(db) as conn:
        ids = {r[0] for r in conn.execute("SELECT id FROM chunks")}
    live = indexer.vector_store.index
    assert live.ntotal == len(ids)
    assert {int(i) for i in __import__("faiss").vector_to_array(live.id_map)} == ids
    # What a restart would load from disk matches what the running process holds.
    fresh = VectorStore(index_dir=collection_service.get_indexes_path(cid),
                        embedding_dim=FakeEmbedder.embedding_dim)
    assert fresh.index.ntotal == len(ids)


@pytest.fixture()
def plant(env):
    """A collection with a .acme file (profile v1) and a markdown file."""
    cft.registry.put(".acme", {"label": "Acme", "record_path": "pumps", "title_field": "name"})
    cid = _collection()
    acme = _index(cid, "pumps.acme", PUMPS)
    md = _index(cid, "notes.md", NOTES)
    return cid, acme, md


def _run(cid, extensions):
    """Start the job (inline) and return its finished record."""
    import asyncio
    job_id = asyncio.run(reindex_service.start_extension_reindex(cid, extensions))
    return app_db.get_reindex_job(job_id)


# ── the job ────────────────────────────────────────────────────────────

def test_profile_change_reaches_the_custom_files_and_leaves_the_rest_alone(plant):
    cid, acme, md = plant
    assert "Pressure" not in _text(cid, acme)
    md_before = _snapshot(cid, md)

    cft.registry.put(".acme", {"label": "Acme", "record_path": "pumps", "title_field": "name",
                               "exclude": ["id"], "labels": {"psi": "Pressure (psi)"}})
    job = _run(cid, [".ACME"])                       # case is forgiven

    assert job["status"] == "completed" and job["total_documents"] == 1
    text = _text(cid, acme)
    assert "Pressure (psi): 62" in text and "9f1c2d3e" not in text
    # The markdown document: same rows, same FAISS ids, same text, same vectors.
    assert _snapshot(cid, md) == md_before
    _consistent(cid)


def test_total_vector_count_matches_the_chunks_after_the_swap(plant):
    cid, acme, md = plant
    cft.registry.put(".acme", {"record_path": "pumps", "title_field": "name", "exclude": ["id", "site"]})
    _run(cid, [".acme"])
    db = collection_service.get_indexes_path(cid) / "metadata.db"
    with sqlite3.connect(db) as conn:
        total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert docs == 2
    assert indexer_manager.get_indexer(cid).vector_store.index.ntotal == total
    _consistent(cid)


def test_the_live_index_serves_new_text_without_a_reload(plant):
    cid, acme, md = plant
    cft.registry.put(".acme", {"record_path": "pumps", "title_field": "name",
                               "labels": {"psi": "Pressure (psi)"}})
    _run(cid, [".acme"])
    indexer = indexer_manager.get_indexer(cid)
    assert indexer_manager.get_indexer(cid) is indexer      # the same live object was used
    chunk = indexer.vector_store.metadata_store.get_chunks_by_document(acme)[0]
    hit = indexer.vector_store.search(FakeEmbedder().embed_query(chunk["text"]), top_k=1)[0]
    assert hit.filename == "pumps.acme" and "Pressure (psi)" in hit.text_snippet


def test_an_upload_after_the_job_is_not_lost_when_the_index_is_saved_again(plant):
    """The failure a second, parallel VectorStore would cause: a later save by
    the live indexer reverting the job's work (or the job's save dropping an upload)."""
    cid, acme, md = plant
    cft.registry.put(".acme", {"record_path": "pumps", "title_field": "name", "exclude": ["id"]})
    _run(cid, [".acme"])
    other = _index(cid, "later.md", "Replace the filter every month. " * 15)
    indexer_manager.get_indexer(cid).save_index()
    _consistent(cid)
    assert "9f1c2d3e" not in _text(cid, acme)
    assert _text(cid, other)


def test_other_extensions_are_never_matched_by_suffix_tricks(env):
    cft.registry.put(".acme", {"record_path": "pumps"})
    cft.registry.put(".acmex", {"record_path": "pumps"})
    cid = _collection()
    keep = _index(cid, "keep.acmex", PUMPS + " ")        # distinct bytes: document ids are content hashes
    _index(cid, "mine.acme", PUMPS)
    before = _snapshot(cid, keep)
    cft.registry.put(".acme", {"record_path": "pumps", "title_field": "name"})
    job = _run(cid, ["acme"])                          # no leading dot
    assert job["status"] == "completed" and job["total_documents"] == 1
    assert _snapshot(cid, keep) == before


def test_a_missing_source_file_fails_before_anything_changes(plant):
    cid, acme, md = plant
    (collection_service.get_documents_path(cid) / "pumps.acme").unlink()
    before = (_snapshot(cid, acme), _snapshot(cid, md))
    job = _run(cid, [".acme"])
    assert job["status"] == "failed" and "pumps.acme" in job["error"]
    assert (_snapshot(cid, acme), _snapshot(cid, md)) == before


def test_a_file_that_no_longer_parses_keeps_its_previous_entry(plant):
    cid, acme, md = plant
    before = _snapshot(cid, acme)
    (collection_service.get_documents_path(cid) / "pumps.acme").write_text("{ not json", encoding="utf-8")
    job = _run(cid, [".acme"])
    assert job["status"] == "failed" and "1 of 1" in job["error"]
    assert _snapshot(cid, acme) == before                 # nothing was removed
    _consistent(cid)


def test_review_decisions_survive_the_swap(plant):
    cid, acme, md = plant
    store = indexer_manager.get_indexer(cid).vector_store.metadata_store
    store.set_document_governance(acme, sensitivity="restricted", policy_status="quarantined",
                                  policy_flags=[{"rule": "manual"}])
    _run(cid, [".acme"])
    info = store.get_document_info(acme)
    assert info["policy_status"] == "quarantined" and info["sensitivity"] == "restricted"


# ── what is refused ────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [[], None, "acme", [""], ["a b"], ["."], [".csv"], [".xlsx"],
                                 [".nope"], [3]])
def test_normalizer_refuses_unusable_lists(env, bad):
    with pytest.raises(ValueError):
        normalize_reindex_extensions(bad)


def test_normalizer_cleans_and_dedupes(env):
    cft.registry.put(".acme", {})
    assert normalize_reindex_extensions([" ACME", ".acme", ".MD"]) == [".acme", ".md"]


def test_a_deleted_type_cannot_be_reindexed(plant):
    """Re-indexing would extract again, and nothing can extract a deleted type
    any more, so the old entry would be dropped: refuse, files stay searchable."""
    cid, acme, md = plant
    cft.registry.delete(".acme")
    with pytest.raises(ValueError, match="not a supported file type"):
        normalize_reindex_extensions([".acme"])
    assert _text(cid, acme)


def test_a_switched_off_type_cannot_be_reindexed_either(plant):
    cid, acme, md = plant
    cft.registry.put(".acme", {"record_path": "pumps", "enabled": False})
    with pytest.raises(ValueError, match="not a supported file type"):
        normalize_reindex_extensions([".acme"])


# ── HTTP: body model, status codes, owner gate ────────────────────────

@pytest.fixture()
def client(env):
    import main
    return TestClient(main.app)


def test_http_body_is_an_object_with_an_extensions_list(client, plant):
    cid, acme, md = plant
    cft.registry.put(".acme", {"record_path": "pumps", "title_field": "name", "exclude": ["id"]})
    assert client.post(f"/api/collections/{cid}/reindex-extensions", json=[".acme"]).status_code == 422
    r = client.post(f"/api/collections/{cid}/reindex-extensions", json={"extensions": ["ACME"]})
    assert r.status_code == 200
    body = r.json()
    assert body["extensions"] == [".acme"] and body["collection_id"] == cid
    assert app_db.get_reindex_job(body["job_id"])["status"] == "completed"
    assert "9f1c2d3e" not in _text(cid, acme)


@pytest.mark.parametrize("payload", [{"extensions": []}, {"extensions": [".csv"]},
                                     {"extensions": ["not an ext"]}, {"extensions": [".nope"]}])
def test_http_400_for_empty_or_unsupported_extensions(client, plant, payload):
    cid, *_ = plant
    r = client.post(f"/api/collections/{cid}/reindex-extensions", json=payload)
    assert r.status_code == 400 and r.json()["detail"]


def test_http_400_after_the_type_is_deleted(client, plant):
    cid, *_ = plant
    cft.registry.delete(".acme")
    r = client.post(f"/api/collections/{cid}/reindex-extensions", json={"extensions": [".acme"]})
    assert r.status_code == 400 and "stay searchable" in r.json()["detail"]


def test_http_409_while_another_job_is_running(client, plant):
    cid, acme, md = plant
    before = _snapshot(cid, acme)
    job = app_db.create_reindex_job({"collection_id": "other"})
    app_db.update_reindex_job(job, status="running")
    r = client.post(f"/api/collections/{cid}/reindex-extensions", json={"extensions": [".acme"]})
    assert r.status_code == 409 and "already running" in r.json()["detail"]
    assert _snapshot(cid, acme) == before


def test_http_404_for_an_unknown_collection(client, env):
    cft.registry.put(".acme", {})
    r = client.post("/api/collections/nosuchid/reindex-extensions", json={"extensions": [".acme"]})
    assert r.status_code == 404


def _private(monkeypatch):
    for s in {id(config.settings): config.settings}.values():
        monkeypatch.setattr(s, "private_collections", True)


def test_only_the_owner_may_reindex(env, monkeypatch):
    import asyncio
    from api import collections as api
    from services.sharing_service import sharing_service
    cft.registry.put(".acme", {"record_path": "pumps"})
    _private(monkeypatch)
    cid = _collection("Alice's", owner_id=ALICE)
    share = sharing_service.create_share(cid, ALICE, permission="readwrite")
    sharing_service.accept_share(share["share_id"], BOB)
    body = api.ReindexExtensionsRequest(extensions=[".acme"])

    def call(user):
        return asyncio.run(api.start_extension_reindex(cid, body, user_id=user))

    with pytest.raises(HTTPException) as info:
        call(BOB)                                                         # contributor, not owner
    assert info.value.status_code == 403
    with pytest.raises(HTTPException) as info:
        call("carol@example.com")
    assert info.value.status_code == 404                                  # cannot even see it
    assert call(ALICE)["status"] == "started"


# ── usage counts ───────────────────────────────────────────────────────

def test_usage_is_counted_per_collection_and_exact(env):
    cft.registry.put(".acme", {"record_path": "pumps"})
    cft.registry.put(".acmex", {"record_path": "pumps"})
    one, two, three = _collection("One"), _collection("Two"), _collection("Three")
    _index(one, "a.acme", PUMPS)
    _index(one, "b.ACME", PUMPS + " ")
    _index(one, "c.acmex", PUMPS + "  ")        # a different type with a longer suffix
    _index(one, "notes.md", NOTES)
    _index(two, "d.acme", PUMPS + "   ")
    _index(three, "e.md", NOTES + "x")

    cols = collection_service.get_all_collections()
    out = usage_mod.usage_by_collection(".acme", cols, indexer_manager.get_indexer)
    by_id = {r["collection_id"]: r for r in out["collections"]}
    assert set(by_id) == {one, two}                  # nothing listed for a collection without files
    assert by_id[one]["documents"] == 2 and by_id[two]["documents"] == 1
    assert out["total_documents"] == 3 and out["registered"] is True


def test_percent_and_underscore_in_a_name_never_widen_the_match(env):
    cft.registry.put(".acme", {"record_path": "pumps"})
    cid = _collection()
    _index(cid, "100%_done.acme", PUMPS)
    _index(cid, "notes.md", NOTES)
    out = usage_mod.usage_by_collection(".acme", collection_service.get_all_collections(),
                                        indexer_manager.get_indexer)
    assert out["total_documents"] == 1
    # An odd extension is a plain string compared exactly, never a pattern.
    with pytest.raises(cft.ProfileError):
        usage_mod.usage_by_collection("%", collection_service.get_all_collections(),
                                      indexer_manager.get_indexer)


def test_out_of_date_follows_the_reading_part_of_the_profile_only(plant):
    cid, acme, md = plant

    def usage():
        return usage_mod.usage_by_collection(".acme", collection_service.get_all_collections(),
                                             indexer_manager.get_indexer)

    assert usage()["total_out_of_date"] == 0
    cft.registry.put(".acme", {"label": "Acme exports", "record_path": "pumps", "title_field": "name"})
    assert usage()["total_out_of_date"] == 0              # a new label changes no text
    cft.registry.put(".acme", {"label": "Acme exports", "record_path": "pumps", "title_field": "name",
                               "exclude": ["id"]})
    assert usage()["total_out_of_date"] == 1
    _run(cid, [".acme"])
    after = usage()
    assert after["total_out_of_date"] == 0 and after["total_documents"] == 1


def test_a_deleted_type_still_reports_its_files(plant):
    cid, *_ = plant
    cft.registry.delete(".acme")
    out = usage_mod.usage_by_collection(".acme", collection_service.get_all_collections(),
                                        indexer_manager.get_indexer)
    assert out["registered"] is False and out["total_documents"] == 1


def test_usage_hides_collections_the_caller_cannot_see(env, monkeypatch):
    from api import custom_file_types as api
    cft.registry.put(".acme", {"record_path": "pumps"})
    _private(monkeypatch)
    mine = _collection("Mine", owner_id=ALICE)
    theirs = _collection("Theirs", owner_id=BOB)
    _index(mine, "a.acme", PUMPS)
    _index(theirs, "b.acme", PUMPS + " ")
    token = set_request_user(ALICE)
    try:
        out = api._usage(".acme", ALICE)
    finally:
        reset_request_user(token)
    assert [r["collection_id"] for r in out["collections"]] == [mine]
    assert "Theirs" not in json.dumps(out)


def test_usage_endpoint_is_admin_only_and_the_put_response_carries_counts(env, plant, monkeypatch):
    import main
    import api.custom_file_types as api
    cid, *_ = plant
    client = TestClient(main.app)
    r = client.get("/api/custom-file-types/.acme/usage")
    assert r.status_code == 200 and r.json()["collections"][0]["documents"] == 1

    r = client.put("/api/custom-file-types/.acme", json={
        "record_path": "pumps", "title_field": "name", "exclude": ["id"]})
    body = r.json()
    assert body["reading_changed"] is True and body["affected_documents"] == 1
    assert body["usage"]["collections"][0]["collection_id"] == cid

    r = client.put("/api/custom-file-types/.acme", json={
        "record_path": "pumps", "title_field": "name", "exclude": ["id"], "label": "Renamed"})
    assert r.json()["reading_changed"] is False

    r = client.delete("/api/custom-file-types/.acme")
    assert r.json()["affected_documents"] == 1

    def deny(action="x"):
        raise HTTPException(status_code=403, detail="Only an administrator can " + action)
    monkeypatch.setattr(api, "require_admin", deny)
    assert client.get("/api/custom-file-types/.acme/usage").status_code == 403

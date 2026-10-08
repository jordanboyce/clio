"""Incremental folder sync: skip-unchanged, changed-file replacement,
deletion propagation, and the sync memory behind "Sync again".

The bulk pipeline itself is covered in tests/test_bulk_indexing.py; what
matters here is the planning around it — which files a second run of the
same folder hands to the job (none, when nothing changed), what happens to
the document of a file that was edited or deleted, and what the endpoints
report. Jobs are driven synchronously: ``_submit`` is replaced so the job
target runs on the calling thread instead of the dispatcher.
"""

import time

import pytest

from services.app_database import SQLiteBackend, app_db
from services.collection_service import collection_service
from services.indexer_manager import indexer_manager
from services.metadata_store import MetadataStore
from tests.test_bulk_indexing import EMBED_DIM, FakeEmbedder  # noqa: F401 - fixtures
from tests.test_governance import _make_indexer

COLLECTION = "default"

_WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]


def _text(name: str, salt: int = 0) -> str:
    return "\n\n".join(
        f"Section {i} of {name} covers the {_WORDS[(i + salt) % 8]} subsystem, "
        f"how {name} schedules {_WORDS[(i + salt + 3) % 8]} work and what "
        f"scenario {i * 17 + salt} means for {_WORDS[(i + salt + 5) % 8]} runs."
        for i in range(3)
    )


@pytest.fixture
def folder(tmp_path):
    """A folder with three text files, one of them in a subdirectory."""
    root = tmp_path / "project"
    (root / "docs").mkdir(parents=True)
    (root / "readme.txt").write_text(_text("readme"), encoding="utf-8")
    (root / "notes.md").write_text(_text("notes"), encoding="utf-8")
    (root / "docs" / "guide.txt").write_text(_text("guide"), encoding="utf-8")
    return root


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A temp app database, a fake-embedding indexer registered as the
    default collection's, and a documents directory of its own."""
    from api import deps

    monkeypatch.setattr(app_db, "db_path", tmp_path / "app.db")
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    monkeypatch.setattr(collection_service, "base_dir", tmp_path / "collections")
    indexer = _make_indexer(tmp_path)
    docs_dir = tmp_path / "library"
    docs_dir.mkdir()
    monkeypatch.setitem(indexer_manager._indexers, COLLECTION, indexer)
    monkeypatch.setattr(indexer_manager, "get_documents_path", lambda cid: docs_dir)
    monkeypatch.setattr(deps, "_initialized", True)
    return {"indexer": indexer, "docs_dir": docs_dir}


@pytest.fixture
def service(env, monkeypatch):
    """An UploadService whose jobs run synchronously on the caller's thread."""
    import services.upload_service as us

    svc = us.UploadService()

    def run_now(job):
        svc._cancel_flags.setdefault(job.job_id, False)
        job.target(*job.args)

    monkeypatch.setattr(svc, "_submit", run_now)
    return svc


def _docs(env):
    return env["indexer"].list_documents()


def _job_count():
    return len(app_db.get_recent_upload_jobs(limit=100))


# ── metadata store helper ───────────────────────────────────────────────


def test_hash_lookup_returns_only_known_hashes(tmp_path):
    store = MetadataStore(tmp_path / "metadata.db")
    store.add_documents([
        {"document_id": "aaaa", "filename": "a.txt", "num_pages": 1, "num_chunks": 1,
         "upload_timestamp": "2026-01-01T00:00:00", "content_hash": "hash-a"},
        {"document_id": "bbbb", "filename": "b.txt", "num_pages": 1, "num_chunks": 1,
         "upload_timestamp": "2026-01-01T00:00:00", "content_hash": "hash-b"},
        {"document_id": "cccc", "filename": "c.txt", "num_pages": 1, "num_chunks": 1,
         "upload_timestamp": "2026-01-01T00:00:00", "content_hash": None},
    ])

    found = store.get_document_ids_by_content_hashes(["hash-a", "hash-b", "hash-zzz", None, ""])

    assert found == {"hash-a": "aaaa", "hash-b": "bbbb"}
    assert store.get_document_ids_by_content_hashes([]) == {}


def test_hash_lookup_batches_past_the_in_clause_limit(tmp_path):
    store = MetadataStore(tmp_path / "metadata.db")
    n = store._IN_CLAUSE_BATCH + 50
    store.add_documents([
        {"document_id": f"d{i:05d}", "filename": f"{i}.txt", "num_pages": 1, "num_chunks": 1,
         "upload_timestamp": "2026-01-01T00:00:00", "content_hash": f"h{i}"}
        for i in range(n)
    ])
    found = store.get_document_ids_by_content_hashes([f"h{i}" for i in range(n)] + ["nope"])
    assert len(found) == n
    assert found["h0"] == "d00000"


def test_source_root_listing_matches_only_inside_the_folder(tmp_path):
    store = MetadataStore(tmp_path / "metadata.db")
    root = tmp_path / "proj"
    store.add_documents([
        {"document_id": "in1", "filename": "a.txt", "num_pages": 1, "num_chunks": 1,
         "upload_timestamp": "t", "source_path": str(root / "a.txt"), "source_type": "folder_sync"},
        {"document_id": "in2", "filename": "sub_b.txt", "num_pages": 1, "num_chunks": 1,
         "upload_timestamp": "t", "source_path": str(root / "sub" / "b.txt"), "source_type": "folder_sync"},
        {"document_id": "ref", "filename": "c.txt", "num_pages": 1, "num_chunks": 1,
         "upload_timestamp": "t", "source_path": str(root / "c.txt"), "source_type": "local_reference"},
        # Same prefix as a string, different directory: must not match.
        {"document_id": "sib", "filename": "d.txt", "num_pages": 1, "num_chunks": 1,
         "upload_timestamp": "t", "source_path": str(tmp_path / "proj2" / "d.txt"), "source_type": "folder_sync"},
        {"document_id": "up", "filename": "e.txt", "num_pages": 1, "num_chunks": 1,
         "upload_timestamp": "t", "source_path": None, "source_type": "upload"},
    ])

    synced = store.list_documents_under_source_root(str(root), source_type="folder_sync")
    assert sorted(d["document_id"] for d in synced) == ["in1", "in2"]

    everything = store.list_documents_under_source_root(str(root))
    assert sorted(d["document_id"] for d in everything) == ["in1", "in2", "ref"]


# ── service: incremental repo index ─────────────────────────────────────


def test_first_repo_index_indexes_every_file(env, service, folder):
    job_id, files_found, skipped = service.start_repo_index(str(folder), COLLECTION)

    assert files_found == 3 and skipped == 0 and job_id is not None
    assert app_db.get_upload_job(job_id)["status"] == "completed"
    assert app_db.get_upload_job(job_id)["total_files"] == 3

    docs = _docs(env)
    assert len(docs) == 3
    assert sorted(d["filename"] for d in docs) == ["docs_guide.txt", "notes.md", "readme.txt"]
    # Every document remembers where its bytes came from.
    assert all(d["source_type"] == "folder_sync" for d in docs)
    assert {d["source_path"] for d in docs} == {
        str((folder / "readme.txt").resolve()),
        str((folder / "notes.md").resolve()),
        str((folder / "docs" / "guide.txt").resolve()),
    }
    assert all(d["content_hash"] for d in docs)


def test_rerun_with_no_changes_creates_no_job(env, service, folder):
    service.start_repo_index(str(folder), COLLECTION)
    jobs_before = _job_count()

    job_id, files_found, skipped = service.start_repo_index(str(folder), COLLECTION)

    assert (job_id, files_found, skipped) == (None, 3, 3)
    assert _job_count() == jobs_before
    assert len(_docs(env)) == 3


def test_non_incremental_rerun_still_queues_everything(env, service, folder):
    service.start_repo_index(str(folder), COLLECTION)
    job_id, files_found, skipped = service.start_repo_index(
        str(folder), COLLECTION, incremental=False
    )
    assert job_id is not None and (files_found, skipped) == (3, 0)
    assert app_db.get_upload_job(job_id)["total_files"] == 3
    # Same bytes, same document ids: idempotent, not duplicated.
    assert len(_docs(env)) == 3


def test_modified_file_reindexes_exactly_one(env, service, folder):
    service.start_repo_index(str(folder), COLLECTION)
    old_ids = {d["source_path"]: d["document_id"] for d in _docs(env)}
    (folder / "notes.md").write_text(_text("notes", salt=4), encoding="utf-8")

    job_id, files_found, skipped = service.start_repo_index(str(folder), COLLECTION)

    assert job_id is not None and (files_found, skipped) == (3, 2)
    assert app_db.get_upload_job(job_id)["total_files"] == 1
    # Plain repo indexing adds the new version; replacing the stale one is
    # sync_folder's job (see test_sync_folder_replaces_the_document_of_a_changed_file).
    notes = str((folder / "notes.md").resolve())
    ids_for_notes = {d["document_id"] for d in _docs(env) if d["source_path"] == notes}
    assert old_ids[notes] in ids_for_notes and len(ids_for_notes) == 2


def test_local_index_incremental_skips_known_files(env, service, folder):
    paths = [str(folder / "readme.txt"), str(folder / "notes.md")]
    first = service.start_local_index(paths, COLLECTION, copy_to_library=False, incremental=True)
    assert first is not None and len(_docs(env)) == 2
    jobs_before = _job_count()

    assert service.start_local_index(paths, COLLECTION, incremental=True) is None
    assert _job_count() == jobs_before

    # One new file: only it is handed to the job.
    paths.append(str(folder / "docs" / "guide.txt"))
    third = service.start_local_index(paths, COLLECTION, incremental=True)
    assert app_db.get_upload_job(third)["total_files"] == 1


# ── service: folder sync ────────────────────────────────────────────────


def test_sync_folder_first_run_then_up_to_date(env, service, folder):
    first = service.sync_folder(str(folder), COLLECTION)
    assert first["status"] == "queued"
    assert first["files_found"] == 3 and first["queued"] == 3
    assert first["skipped_unchanged"] == 0 and first["pruned"] == []
    assert len(_docs(env)) == 3

    jobs_before = _job_count()
    second = service.sync_folder(str(folder), COLLECTION)
    assert second["status"] == "up_to_date"
    assert second["job_id"] is None
    assert second["skipped_unchanged"] == 3 and second["queued"] == 0
    assert _job_count() == jobs_before


def test_sync_folder_replaces_the_document_of_a_changed_file(env, service, folder):
    service.sync_folder(str(folder), COLLECTION)
    notes = str((folder / "notes.md").resolve())
    old_id = next(d["document_id"] for d in _docs(env) if d["source_path"] == notes)
    (folder / "notes.md").write_text(_text("notes", salt=4), encoding="utf-8")

    outcome = service.sync_folder(str(folder), COLLECTION)

    assert outcome["status"] == "queued" and outcome["queued"] == 1
    assert outcome["skipped_unchanged"] == 2
    assert outcome["replaced"] == ["notes.md"] and outcome["replaced_count"] == 1
    assert app_db.get_upload_job(outcome["job_id"])["total_files"] == 1
    docs = _docs(env)
    assert len(docs) == 3  # the stale version is gone, not sitting beside the new one
    by_path = {d["source_path"]: d for d in docs}
    assert by_path[notes]["document_id"] != old_id
    # The staged copy in the library is the new content.
    assert "scenario 4" in (env["docs_dir"] / "notes.md").read_text(encoding="utf-8")


def test_sync_folder_prunes_documents_whose_file_is_gone(env, service, folder):
    service.sync_folder(str(folder), COLLECTION)
    (folder / "docs" / "guide.txt").unlink()
    assert (env["docs_dir"] / "docs_guide.txt").exists()

    without_prune = service.sync_folder(str(folder), COLLECTION)
    assert without_prune["pruned"] == [] and len(_docs(env)) == 3

    outcome = service.sync_folder(str(folder), COLLECTION, prune_missing=True)

    assert outcome["status"] == "up_to_date" and outcome["job_id"] is None
    assert outcome["pruned"] == ["docs_guide.txt"] and outcome["pruned_count"] == 1
    assert outcome["skipped_unchanged"] == 2
    docs = _docs(env)
    assert sorted(d["filename"] for d in docs) == ["notes.md", "readme.txt"]
    assert not (env["docs_dir"] / "docs_guide.txt").exists()
    assert env["indexer"].vector_store.get_total_chunks() == sum(d["num_chunks"] for d in docs)
    # Deletion went through the shared delete path, so it is in the audit trail.
    actions = [e["action"] for e in app_db.list_audit_events()]
    assert "document.sync_prune" in actions and "document.sync_folder" in actions


def test_sync_folder_leaves_other_documents_alone(env, service, folder, tmp_path):
    """Prune only touches what this folder put in; uploads and in-place
    references under an unrelated path survive a prune of the folder."""
    other = tmp_path / "elsewhere.txt"
    other.write_text(_text("elsewhere"), encoding="utf-8")
    service.start_local_index([str(other)], COLLECTION, copy_to_library=False)
    service.sync_folder(str(folder), COLLECTION)
    assert len(_docs(env)) == 4

    (folder / "readme.txt").unlink()
    outcome = service.sync_folder(str(folder), COLLECTION, prune_missing=True)

    assert outcome["pruned"] == ["readme.txt"]
    remaining = {d["filename"] for d in _docs(env)}
    assert remaining == {"elsewhere.txt", "notes.md", "docs_guide.txt"}


def test_sync_memory_records_the_last_run(env, service, folder):
    assert service.list_sync_folders(COLLECTION) == []

    service.sync_folder(str(folder), COLLECTION, file_extensions=[".txt", ".md"])
    (folder / "notes.md").unlink()
    service.sync_folder(str(folder), COLLECTION, file_extensions=[".txt", ".md"], prune_missing=True)

    remembered = service.list_sync_folders(COLLECTION)
    assert len(remembered) == 1
    entry = remembered[0]
    assert entry["path"] == str(folder.resolve())
    assert entry["files_found"] == 2 and entry["skipped_unchanged"] == 2
    assert entry["pruned_count"] == 1 and entry["prune_missing"] is True
    assert entry["file_extensions"] == [".txt", ".md"]
    assert entry["last_synced_at"]

    assert service.forget_sync_folder(COLLECTION, str(folder)) is True
    assert service.list_sync_folders(COLLECTION) == []
    assert service.forget_sync_folder(COLLECTION, str(folder)) is False


def test_sync_folder_rejects_a_missing_path(env, service, tmp_path):
    with pytest.raises(ValueError):
        service.sync_folder(str(tmp_path / "nope"), COLLECTION)


# ── HTTP ────────────────────────────────────────────────────────────────


@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient
    import main

    return TestClient(main.app)


def _wait_for_job(client, job_id, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/documents/upload/{job_id}/status").json()
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def test_sync_folder_endpoint_round_trip(client, env, folder):
    body = {"path": str(folder), "collection_id": COLLECTION}

    r = client.post("/documents/sync-folder", json=body)
    assert r.status_code == 202, r.text
    first = r.json()
    assert first["status"] == "queued" and first["job_id"]
    assert first["files_found"] == 3 and first["skipped_unchanged"] == 0
    assert first["pruned"] == [] and first["pruned_count"] == 0
    assert _wait_for_job(client, first["job_id"])["status"] == "completed"
    assert len(client.get("/documents").json()["documents"]) == 3

    r = client.post("/documents/sync-folder", json=body)
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["status"] == "up_to_date" and second["job_id"] is None
    assert second["skipped_unchanged"] == 3
    assert "Up to date" in second["message"]

    (folder / "docs" / "guide.txt").unlink()
    r = client.post("/documents/sync-folder", json={**body, "prune_missing": True})
    assert r.status_code == 200, r.text
    third = r.json()
    assert third["status"] == "up_to_date"
    assert third["pruned"] == ["docs_guide.txt"] and third["pruned_count"] == 1
    names = {d["filename"] for d in client.get("/documents").json()["documents"]}
    assert names == {"readme.txt", "notes.md"}

    r = client.get("/documents/sync-folders", params={"collection_id": COLLECTION})
    assert r.status_code == 200, r.text
    folders = r.json()["folders"]
    assert len(folders) == 1
    assert folders[0]["path"] == str(folder.resolve())
    assert folders[0]["exists"] is True
    assert folders[0]["pruned_count"] == 1 and folders[0]["prune_missing"] is True


def test_upload_repo_endpoint_reports_unchanged_files(client, env, folder):
    body = {"path": str(folder), "collection_id": COLLECTION}

    r = client.post("/documents/upload-repo", json=body)
    assert r.status_code == 202, r.text
    first = r.json()
    assert first["status"] == "queued" and first["files_found"] == 3
    assert first["skipped_unchanged"] == 0
    assert _wait_for_job(client, first["job_id"])["status"] == "completed"

    r = client.post("/documents/upload-repo", json=body)
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["status"] == "up_to_date" and second["job_id"] is None
    assert second["files_found"] == 3 and second["skipped_unchanged"] == 3

    (folder / "readme.txt").write_text(_text("readme", salt=2), encoding="utf-8")
    r = client.post("/documents/upload-repo", json=body)
    assert r.status_code == 202, r.text
    third = r.json()
    assert third["status"] == "queued" and third["skipped_unchanged"] == 2
    assert "1 file(s)" in third["message"] and "2 unchanged" in third["message"]
    _wait_for_job(client, third["job_id"])

    remembered = client.get("/documents/sync-folders", params={"collection_id": COLLECTION}).json()
    assert [f["path"] for f in remembered["folders"]] == [str(folder.resolve())]


def test_sync_folder_endpoint_rejects_bad_paths(client, env, tmp_path):
    r = client.post("/documents/sync-folder", json={"path": str(tmp_path / "missing")})
    assert r.status_code == 400
    assert "does not exist" in r.json()["detail"]

    f = tmp_path / "file.txt"
    f.write_text("x", encoding="utf-8")
    r = client.post("/documents/sync-folder", json={"path": str(f)})
    assert r.status_code == 400
    assert "not a directory" in r.json()["detail"]


def test_sync_folder_endpoint_requires_write_access(client, env, folder, monkeypatch):
    """Same gate as upload-repo: a read-only share may not sync into it."""
    import api.documents as docs_api
    from fastapi import HTTPException

    def refuse(collection_id):
        raise HTTPException(status_code=403, detail="This collection is shared with you read-only")

    monkeypatch.setattr(docs_api, "_require_ingest", refuse)
    r = client.post("/documents/sync-folder", json={"path": str(folder)})
    assert r.status_code == 403
    assert len(env["indexer"].list_documents()) == 0

"""Jobs across a restart (E7): a job waiting for its turn is queued again,
one that was running is marked interrupted with "Run again", and a running
job that stops making progress is stopped so its collection moves on."""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import config
from services.app_database import SQLiteBackend, app_db
from services.collection_service import collection_service
from services.upload_service import INTERRUPTED_REASON, UploadService, _QueuedJob


@pytest.fixture
def jobs_db(tmp_path):
    old = app_db.db_path
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    yield app_db
    app_db.db_path = old


@pytest.fixture
def service(monkeypatch):
    """A service whose jobs record what they were asked to do instead of indexing."""
    svc = UploadService()
    svc.ran = []
    done = threading.Event()

    def run_local(job_id, items, collection_id, copy, uploaded_by):
        svc.ran.append((job_id, sorted(items), collection_id))
        done.set()

    monkeypatch.setattr(svc, "_run_local_index", run_local)
    monkeypatch.setattr(svc, "_split_unchanged", lambda items, cid: (list(items), [], {}))
    svc.done = done
    return svc


def _local_job(service, tmp_path, status, names=("a.txt", "b.txt"), collection_id="c1"):
    paths = []
    for name in names:
        p = tmp_path / name
        p.write_text("x", encoding="utf-8")
        paths.append(str(p))
    job_id = app_db.create_upload_job(collection_id, len(paths), job_type="index")
    service._save_rerun_spec(job_id, "local", collection_id, paths, "a@example.org", copy_to_library=False)
    app_db.update_upload_job(job_id, status=status)
    return job_id, paths


def test_a_job_still_waiting_is_queued_again_as_it_was(jobs_db, service, tmp_path):
    job_id, paths = _local_job(service, tmp_path, "pending")

    counts = service.recover_orphaned_jobs()

    assert counts["requeued"] == 1 and counts["upload_jobs"] == 0
    assert service.done.wait(5)
    assert service.ran == [(job_id, sorted(paths), "c1")]
    assert app_db.get_upload_job(job_id)["status"] != "failed"


def test_a_job_that_was_running_is_interrupted_and_can_run_again(jobs_db, service, tmp_path):
    job_id, paths = _local_job(service, tmp_path, "running")

    service.recover_orphaned_jobs()

    row = app_db.get_upload_job(job_id)
    assert row["status"] == "failed" and row["error"] == INTERRUPTED_REASON
    assert service.can_rerun(row) is True
    assert service.ran == []

    (tmp_path / "b.txt").unlink()  # only what is still there runs again
    new_id = service.rerun_job(job_id)

    assert service.done.wait(5)
    assert new_id != job_id
    assert service.ran == [(new_id, [paths[0]], "c1")]
    assert json.loads(app_db.get_upload_job(new_id)["rerun_spec"])["items"] == [paths[0]]


def test_nothing_to_run_again_when_the_inputs_are_gone(jobs_db, service, tmp_path):
    job_id, paths = _local_job(service, tmp_path, "running")
    service.recover_orphaned_jobs()
    for p in paths:
        (tmp_path / p).unlink()

    assert service.can_rerun(app_db.get_upload_job(job_id)) is False
    with pytest.raises(ValueError):
        service.rerun_job(job_id)


def test_a_finished_job_is_not_offered_again(jobs_db, service, tmp_path):
    job_id, _ = _local_job(service, tmp_path, "completed")

    assert service.can_rerun(app_db.get_upload_job(job_id)) is False


def test_uploads_staged_for_an_interrupted_job_are_kept(jobs_db, service):
    staging = collection_service.get_staging_path("c-staged")
    kept = staging / "tok1"
    kept.mkdir(parents=True)
    (kept / "report.pdf").write_bytes(b"%PDF")
    orphan = staging / "tok2"
    orphan.mkdir()
    (orphan / "lost.pdf").write_bytes(b"%PDF")
    job_id = app_db.create_upload_job("c-staged", 1, job_type="upload")
    service._save_rerun_spec(job_id, "local", "c-staged", [str(kept / "report.pdf")], None)
    app_db.update_upload_job(job_id, status="running")

    service.recover_orphaned_jobs()

    assert (kept / "report.pdf").exists()
    assert not orphan.exists()
    assert service.can_rerun(app_db.get_upload_job(job_id))


def test_a_job_with_no_progress_is_stopped_and_its_collection_moves_on(jobs_db, monkeypatch):
    monkeypatch.setattr(config.settings, "job_stall_timeout_seconds", 60)
    service = UploadService()
    release = threading.Event()
    second_ran = threading.Event()
    hung = app_db.create_upload_job("c1", 1, job_type="index")
    waiting = app_db.create_upload_job("c1", 1, job_type="index")

    service._submit(_QueuedJob(hung, "c1", lambda: release.wait(10), ()))
    service._submit(_QueuedJob(waiting, "c1", second_ran.set, ()))
    assert not second_ran.wait(0.2)  # held back behind the hung job

    stopped = service.check_stalled_jobs(now=time.monotonic() + 61)

    try:
        assert stopped == [hung]
        row = app_db.get_upload_job(hung)
        assert row["status"] == "failed" and "no progress for 1 minutes" in row["error"]
        assert second_ran.wait(5)
    finally:
        release.set()


def test_progress_keeps_a_job_alive(jobs_db, monkeypatch):
    monkeypatch.setattr(config.settings, "job_stall_timeout_seconds", 60)
    service = UploadService()
    release = threading.Event()
    job = app_db.create_upload_job("c1", 1, job_type="index")
    service._submit(_QueuedJob(job, "c1", lambda: release.wait(10), ()))
    try:
        time.sleep(0.05)
        service.report_progress(job, "embedding", 50, "half")
        assert service.check_stalled_jobs(now=time.monotonic() + 30) == []
    finally:
        release.set()


def test_a_report_left_generating_is_marked_failed_at_start(tmp_path):
    from services import artifact_jobs
    from services.artifact_store import ArtifactStore

    db = tmp_path / "c1" / "indexes" / "metadata.db"
    db.parent.mkdir(parents=True)
    store = ArtifactStore(db)
    store.create("aaaaaaaaaaaa", artifact_type="summary", title="t", status="running", created_at="2026-11-18T00:00:00")
    store.create("bbbbbbbbbbbb", artifact_type="summary", title="t", status="done", created_at="2026-11-18T00:00:00")

    assert artifact_jobs.fail_interrupted(tmp_path) == 1

    left = store.get("aaaaaaaaaaaa")
    assert left["status"] == "failed" and left["error"] == artifact_jobs.RESTART_REASON
    assert store.get("bbbbbbbbbbbb")["status"] == "done"


def test_run_again_over_http(jobs_db, monkeypatch, tmp_path):
    import main
    from services.upload_service import upload_service

    ran = threading.Event()
    monkeypatch.setattr(upload_service, "_run_local_index", lambda *args: ran.set())
    monkeypatch.setattr(upload_service, "_split_unchanged", lambda items, cid: (list(items), [], {}))
    job_id, _ = _local_job(upload_service, tmp_path, "failed", collection_id="default")
    done_id, _ = _local_job(upload_service, tmp_path, "completed", names=("c.txt",), collection_id="default")
    client = TestClient(main.app)

    listed = {j["job_id"]: j for j in client.get("/documents/jobs", params={"limit": 50}).json()}
    assert listed[job_id]["can_rerun"] is True and listed[done_id]["can_rerun"] is False

    r = client.post(f"/documents/upload/{job_id}/rerun")
    assert r.status_code == 202 and r.json()["rerun_of"] == job_id
    assert ran.wait(5)
    assert client.post(f"/documents/upload/{done_id}/rerun").status_code == 409

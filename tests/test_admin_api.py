"""Admin visibility endpoints + the operator gates added with them.

Under private collections, /api/admin/* and the server-path probes
(scan-folder, pickers) are ADMIN_EMAILS-only and fail closed. With private
collections off they are deliberate no-ops — the shared-appliance trust
model, same as POST /api/config.

The HTTP harness mirrors tests/test_private_collections.py: settings
flipped on the live Settings objects, the Access verifier faked, main
reloaded so middleware registration matches the mode.
"""

import importlib

import time

import pytest
from fastapi.testclient import TestClient

import config
from config import settings as _import_time_settings
from services.app_database import SQLiteBackend, app_db
from services.usage_service import record_chat_usage

ADMIN = "admin@example.com"
USER = "user@example.com"


@pytest.fixture()
def fresh_db(tmp_path):
    old_db_path = app_db.db_path
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    yield app_db
    app_db.db_path = old_db_path


def _settings_objects():
    objs = {id(_import_time_settings): _import_time_settings}
    objs.setdefault(id(config.settings), config.settings)
    return list(objs.values())


@pytest.fixture()
def private_admin_client(fresh_db, monkeypatch):
    import main
    from tests import identity_helpers

    saved = []
    for s in _settings_objects():
        saved.append((s, s.private_collections, s.admin_emails))
        s.private_collections = True
        s.admin_emails = ADMIN
    identity_helpers.install(monkeypatch)
    reloaded = importlib.reload(main)
    yield TestClient(reloaded.app)
    for s, private, admins in saved:
        s.private_collections = private
        s.admin_emails = admins
    importlib.reload(main)


def _as(email):
    return {"x-test-user": email}


# ── Private mode: fails closed ──────────────────────────────────────────────


def test_admin_endpoints_reject_non_admin(private_admin_client):
    for path in ("/api/admin/usage", "/api/admin/search-history", "/api/admin/stats"):
        r = private_admin_client.get(path, headers=_as(USER))
        assert r.status_code == 403, path


def test_admin_endpoints_allow_admin(private_admin_client):
    record_chat_usage(USER, "col1", "anthropic", "claude-x",
                      input_tokens=10, output_tokens=5)

    r = private_admin_client.get("/api/admin/usage", headers=_as(ADMIN))
    assert r.status_code == 200
    body = r.json()
    assert {"days", "by_user", "by_day", "daily_token_budget"} <= set(body)
    assert any(row["grouped_by"] == USER for row in body["by_user"])

    r = private_admin_client.get("/api/admin/stats", headers=_as(ADMIN))
    assert r.status_code == 200
    stats = r.json()
    for key in ("in_flight", "requests_total", "errors_total", "uptime_seconds",
                "rate_limit", "index_jobs_active", "usage_today"):
        assert key in stats, key
    assert stats["in_flight"] >= 1  # this request itself

    r = private_admin_client.get("/api/admin/search-history", headers=_as(ADMIN))
    assert r.status_code == 200
    assert "searches" in r.json()


def test_path_probes_are_admin_only(private_admin_client):
    r = private_admin_client.post("/api/scan-folder", json={"path": "C:/"},
                                  headers=_as(USER))
    assert r.status_code == 403
    r = private_admin_client.post("/api/folder-picker", headers=_as(USER))
    assert r.status_code == 403
    r = private_admin_client.post("/api/file-picker", headers=_as(USER))
    assert r.status_code == 403


# ── Open mode: deliberate no-op ─────────────────────────────────────────────


def test_open_mode_admin_endpoints_are_open(fresh_db):
    import main
    client = TestClient(main.app)
    assert client.get("/api/admin/stats").status_code == 200
    assert client.get("/api/admin/usage").status_code == 200


# ── Global index-job cap ────────────────────────────────────────────────────


def test_global_job_cap_holds_jobs_in_the_queue(fresh_db):
    """At the concurrency cap a new job waits instead of being refused."""
    import threading
    from services.upload_service import upload_service, _QueuedJob

    old = config.settings.max_concurrent_index_jobs
    config.settings.max_concurrent_index_jobs = 2
    ran = []
    try:
        with upload_service._lock:
            upload_service._active_threads[9001] = threading.Thread()
            upload_service._active_collections[9001] = "c1"
            upload_service._active_threads[9002] = threading.Thread()
            upload_service._active_collections[9002] = "c2"

        upload_service._submit(_QueuedJob(
            job_id=9003, collection_id="c3", target=ran.append, args=(9003,),
        ))
        # Both slots are taken, so it waits rather than raising.
        assert ran == []
        assert upload_service.queue_position(9003) == 1

        # Freeing a slot and dispatching starts it.
        with upload_service._lock:
            upload_service._active_threads.pop(9001, None)
            upload_service._active_collections.pop(9001, None)
        upload_service._dispatch()
        for _ in range(50):
            if ran:
                break
            time.sleep(0.02)
        assert ran == [9003]
        assert upload_service.queue_position(9003) is None
    finally:
        config.settings.max_concurrent_index_jobs = old
        with upload_service._lock:
            for stale in (9001, 9002, 9003):
                upload_service._active_threads.pop(stale, None)
                upload_service._active_collections.pop(stale, None)
            upload_service._queue.clear()


def test_queue_admission_refuses_only_when_the_waiting_list_is_full(fresh_db):
    """Submission is refused for a full queue, not for a busy collection."""
    from services.upload_service import upload_service, _QueuedJob

    try:
        with upload_service._lock:
            for n in range(upload_service.MAX_QUEUED_JOBS):
                upload_service._queue.append(_QueuedJob(
                    job_id=8000 + n, collection_id="busy", target=lambda: None, args=(),
                ))
        with pytest.raises(RuntimeError, match="waiting to run"):
            upload_service._check_queue_capacity()
    finally:
        with upload_service._lock:
            upload_service._queue.clear()

"""Capacity and health (E9): free disk in readiness and before ingest, the
failure metrics, and the request id on every log line of a request."""

import logging

import pytest
from fastapi.testclient import TestClient

import config
from services import ops_counters, storage_quota

MB = 1024 * 1024


@pytest.fixture()
def low_disk(monkeypatch):
    """10 MB free against the default 500 MB kept back."""
    monkeypatch.setattr(storage_quota, "free_disk_bytes", lambda: 10 * MB)


@pytest.fixture()
def client():
    import main

    return TestClient(main.app)


def test_readiness_reports_a_full_disk(client, low_disk):
    from api import deps

    deps.mark_initialized()
    r = client.get("/readyz")

    assert r.status_code == 503 and r.json() == {"status": "not_ready", "reason": "disk"}


def test_readiness_is_fine_with_room(client, monkeypatch):
    from api import deps

    deps.mark_initialized()
    monkeypatch.setattr(storage_quota, "free_disk_bytes", lambda: 50 * 1024 * MB)

    assert client.get("/readyz").json() == {"status": "ready"}


def test_an_upload_that_would_fill_the_disk_is_refused_before_it_is_written(client, low_disk):
    from api import deps

    deps.mark_initialized()
    r = client.post("/documents/upload-staged", files={"files": ("notes.txt", b"hello", "text/plain")})

    assert r.status_code == 507
    detail = r.json()["detail"]
    assert detail["error"] == "disk_space_low"
    assert detail["detail"].startswith("The server is almost out of disk space, so this upload was not added.")
    assert str(config.settings.data_dir) not in str(detail)


def test_room_for_the_file_but_not_for_the_reserve_is_refused(monkeypatch):
    monkeypatch.setattr(storage_quota, "free_disk_bytes", lambda: 600 * MB)

    storage_quota.check("default", 50 * MB)
    with pytest.raises(storage_quota.DiskSpaceLow):
        storage_quota.check("default", 150 * MB)


def test_zero_turns_the_disk_check_off(low_disk, monkeypatch):
    monkeypatch.setattr(config.settings, "min_free_disk_mb", 0)

    storage_quota.check_disk("default", 50 * MB)


@pytest.fixture()
def metrics_on(monkeypatch):
    import main
    from middleware import request_meta

    monkeypatch.setattr(main.settings, "metrics_enabled", True)
    monkeypatch.setattr(request_meta.settings, "metrics_enabled", True)
    ops_counters.reset()
    yield
    ops_counters.reset()


def test_failures_have_metrics(client, metrics_on, monkeypatch):
    import main
    from services.ai_service import _count_model_call_errors

    monkeypatch.setattr(main.settings, "auth_password", "a-long-enough-password")
    monkeypatch.setattr(config.settings, "auth_password", "a-long-enough-password")
    client.get("/api/collections", headers={"Authorization": "Bearer wrong"})
    ops_counters.incr("jobs_failed", "index")

    class Broken:
        def complete(self, prompt, max_tokens, model):
            raise ConnectionError("no route")

    provider = Broken()
    _count_model_call_errors(provider, "openai_compatible")
    with pytest.raises(ConnectionError):
        provider.complete("hi", 10, "m")

    body = client.get("/metrics", headers={"Authorization": "Bearer a-long-enough-password"}).text
    assert "clio_auth_failures_total 1.0" in body
    assert "clio_auth_lockouts_total 0.0" in body
    assert 'clio_jobs_failed_total{kind="index"} 1.0' in body
    assert 'clio_provider_errors_total{provider="openai_compatible"} 1.0' in body
    assert "clio_data_dir_free_bytes " in body


def test_a_line_logged_during_a_request_carries_its_id():
    from middleware import request_context
    from services.log_format import RequestIdFilter, TextFormatter

    record = logging.makeLogRecord({"name": "services.x", "levelname": "INFO", "msg": "indexed %d files", "args": (3,)})
    token = request_context._scope.set({"type": "http", "state": {"request_id": "abc123def456"}})
    try:
        RequestIdFilter().filter(record)
    finally:
        request_context._scope.reset(token)

    assert record.request_id == "abc123def456"
    assert TextFormatter("%(message)s%(rid_suffix)s").format(record) == "indexed 3 files rid=abc123def456"


def test_a_line_outside_a_request_has_no_id():
    from services.log_format import RequestIdFilter, TextFormatter

    record = logging.makeLogRecord({"msg": "started"})
    RequestIdFilter().filter(record)

    assert TextFormatter("%(message)s%(rid_suffix)s").format(record) == "started"


def test_the_request_id_reaches_code_inside_a_request(client, monkeypatch):
    """End to end: the id a caller is answered with is the one service code sees."""
    from middleware import request_context
    from services import audit

    seen = {}
    real_record = audit.record

    def spy(action, **kwargs):
        seen["rid"] = request_context.request_id()
        return real_record(action, **kwargs)

    monkeypatch.setattr(audit, "record", spy)
    r = client.post("/api/admin/blocked-hashes", json={"content_hash": "b" * 64, "reason": "x"})

    assert seen["rid"] == r.headers["X-Request-ID"]

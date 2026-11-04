"""GET /metrics: off by default, authenticated, and free of ids."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    import main

    # Not entered as a context manager: the MCP session manager's lifespan can
    # run only once per process.
    return TestClient(main.app)


@pytest.fixture()
def enabled(monkeypatch):
    import main
    from middleware import request_meta

    # Other tests reload config, so these two modules may not hold the same
    # settings object, or the one `from config import settings` gives here.
    monkeypatch.setattr(main.settings, "metrics_enabled", True)
    monkeypatch.setattr(request_meta.settings, "metrics_enabled", True)
    return main.settings


def test_off_by_default(client):
    assert client.get("/metrics").status_code == 404


def test_reports_requests_and_live_state(client, enabled):
    client.get("/health")
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    for name in (
        "clio_http_requests_total",
        "clio_http_request_duration_seconds_bucket",
        "clio_http_requests_in_flight",
        "clio_worker_threads ",
        "clio_worker_threads_busy",
        "clio_worker_threads_queued",
        "clio_rate_limit_rejected_total",
        "clio_collections_open",
        "clio_indexes_loaded",
        "clio_index_memory_bytes",
        "clio_index_unloads_total",
        "clio_index_reloads_total",
        "clio_rerank_shed_total",
        "clio_index_jobs_running",
        "clio_index_jobs_queued",
    ):
        assert name in body, f"{name} missing"
    assert 'route="/health"' in body


def test_route_label_is_the_template_not_the_path(client, enabled):
    """A raw path would put collection ids in the monitoring system, unbounded."""
    client.get("/api/collections/secret-collection-id/embedding-health")
    body = client.get("/metrics").text
    assert "secret-collection-id" not in body
    assert 'route="/api/collections/{collection_id}/embedding-health"' in body


def test_unknown_paths_share_one_label(client, enabled):
    client.get("/api/definitely/not/a/route/12345")
    body = client.get("/metrics").text
    assert "12345" not in body


def test_requires_the_same_authentication_as_the_api(client, enabled, monkeypatch):
    monkeypatch.setattr(enabled, "auth_password", "a-password")

    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer a-password"}).status_code == 200


def test_scraper_token_opens_metrics_and_nothing_else(client, enabled, monkeypatch):
    monkeypatch.setattr(enabled, "auth_password", "a-password")
    monkeypatch.setattr(enabled, "metrics_token", "a-scraper-token")
    bearer = {"Authorization": "Bearer a-scraper-token"}

    assert client.get("/metrics", headers=bearer).status_code == 200
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/collections", headers=bearer).status_code == 401


def test_a_failing_source_does_not_lose_the_rest(client, enabled, monkeypatch):
    from services import metrics

    def broken():
        raise RuntimeError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr(metrics._LiveStateCollector, "_sources", (broken, metrics._request_metrics))
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "clio_http_requests_in_flight" in response.text


def test_reranker_shed_is_counted(monkeypatch):
    import threading

    from services import reranker

    gate = threading.BoundedSemaphore(1)
    gate.acquire()
    monkeypatch.setattr(reranker, "_inference_gate", lambda: gate)
    monkeypatch.setattr(reranker, "_queue_timeout", lambda: 0)
    instance = reranker.CrossEncoderReranker.__new__(reranker.CrossEncoderReranker)
    monkeypatch.setattr(instance, "_ensure_model", lambda: object(), raising=False)
    before = reranker.shed_count()

    ranked, did_rerank = instance.rerank_with_status("q", ["a", "b"], top_k=1)

    assert did_rerank is False
    assert ranked == ["a"]
    assert reranker.shed_count() == before + 1

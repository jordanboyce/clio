"""Prometheus metrics, served at GET /metrics when METRICS_ENABLED is on.

Two kinds of number live here. Request counts and latencies are recorded as
requests pass through middleware/request_meta.py. Everything else is read at
scrape time from the component that already keeps it (the rate limiter, the
index cache, the job queue, the database pool), so a scrape reports the same
values as /api/admin/stats and no second set of counters can drift.

Metrics hold no identities, queries or document names. The `route` label is the
route's template, never the raw path: a raw path would carry collection and
document ids, which are unbounded and would leak into the monitoring system.
"""

import logging
from typing import Iterable, Optional

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.exposition import CONTENT_TYPE_LATEST

logger = logging.getLogger(__name__)

CONTENT_TYPE = CONTENT_TYPE_LATEST

registry = CollectorRegistry()

# Chat and research turns take minutes, so the buckets reach well past the
# defaults. For a streamed response this times the first byte, not the stream.
_LATENCY_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)

http_requests = Counter(
    "clio_http_requests",
    "HTTP requests handled, by route template and status class.",
    ["method", "route", "status"],
    registry=registry,
)
http_request_seconds = Histogram(
    "clio_http_request_duration_seconds",
    "Time to the start of the response, by route template.",
    ["route"],
    buckets=_LATENCY_BUCKETS,
    registry=registry,
)


def route_label(request) -> str:
    """The route template this request matched, without any ids from the path."""
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if template:
        return template
    path = request.url.path
    if path == "/mcp" or path.startswith("/mcp/"):
        return "/mcp"
    return "unmatched"


def observe_request(request, status_code: int, seconds: float) -> None:
    route = route_label(request)
    http_requests.labels(request.method, route, f"{status_code // 100}xx").inc()
    http_request_seconds.labels(route).observe(seconds)


def _worker_pool_metrics() -> Iterable[Metric]:
    """How full the worker pool is: the first thing to saturate under load."""
    try:
        import anyio.to_thread

        limiter = anyio.to_thread.current_default_thread_limiter()
    except Exception:  # noqa: BLE001 - no running event loop, e.g. a direct call in a test
        return
    yield GaugeMetricFamily("clio_worker_threads", "Size of the worker thread pool.", value=limiter.total_tokens)
    yield GaugeMetricFamily("clio_worker_threads_busy", "Worker threads in use.", value=limiter.borrowed_tokens)
    yield GaugeMetricFamily(
        "clio_worker_threads_queued", "Calls waiting for a worker thread.", value=limiter.statistics().tasks_waiting
    )


def _request_metrics() -> Iterable[Metric]:
    from middleware.request_meta import stats

    current = stats()
    yield GaugeMetricFamily("clio_http_requests_in_flight", "Requests being handled now.", value=current["in_flight"])
    yield GaugeMetricFamily("clio_uptime_seconds", "Seconds since this process started.", value=current["uptime_seconds"])


def _rate_limit_metrics() -> Iterable[Metric]:
    from middleware.rate_limit import rate_limiter

    rejected = CounterMetricFamily(
        "clio_rate_limit_rejected", "Requests refused with a 429, by expense class.", labels=["class"]
    )
    for name, counts in rate_limiter.stats().items():
        rejected.add_metric([name], counts["rejected"])
    yield rejected


def _index_metrics() -> Iterable[Metric]:
    from services.indexer_manager import indexer_manager

    cache = indexer_manager.index_memory_stats()
    yield GaugeMetricFamily("clio_collections_open", "Collections opened by this process.", value=cache["open"])
    yield GaugeMetricFamily("clio_indexes_loaded", "Collection indexes held in memory.", value=cache["loaded"])
    yield GaugeMetricFamily("clio_index_vectors_loaded", "Vectors held in memory.", value=cache["vectors"])
    yield GaugeMetricFamily("clio_index_memory_bytes", "Memory taken by loaded vectors, estimated.", value=cache["bytes"])
    yield CounterMetricFamily("clio_index_unloads", "Idle indexes released from memory.", value=cache["unloads"])
    yield CounterMetricFamily("clio_index_reloads", "Indexes read back into memory after being released.", value=cache["reloads"])


def _search_metrics() -> Iterable[Metric]:
    from services import reranker

    yield CounterMetricFamily(
        "clio_rerank_shed",
        "Searches answered in retrieval order because no reranking slot came free in time.",
        value=reranker.shed_count(),
    )


def _job_metrics() -> Iterable[Metric]:
    from services.upload_service import upload_service

    yield GaugeMetricFamily("clio_index_jobs_running", "Indexing jobs running now.", value=upload_service.active_job_count())
    yield GaugeMetricFamily("clio_index_jobs_queued", "Indexing jobs waiting for a slot.", value=upload_service.queued_job_count())


def _database_metrics() -> Iterable[Metric]:
    from services.app_database import app_db

    pool: Optional[dict] = app_db.pool_stats()
    if pool is None:  # SQLite has no pool
        return
    yield GaugeMetricFamily("clio_db_pool_size", "Most connections the database pool may open.", value=pool["size"])
    yield GaugeMetricFamily("clio_db_pool_in_use", "Database connections in use.", value=pool["in_use"])
    yield CounterMetricFamily("clio_db_pool_waits", "Calls that had to wait for a database connection.", value=pool["waits"])
    yield CounterMetricFamily(
        "clio_db_pool_timeouts", "Calls refused because no database connection came free in time.", value=pool["timeouts"]
    )


class _LiveStateCollector:
    """Reads the live components on each scrape. One failing never hides the rest."""

    _sources = (
        _worker_pool_metrics, _request_metrics, _rate_limit_metrics,
        _index_metrics, _search_metrics, _job_metrics, _database_metrics,
    )

    def collect(self) -> Iterable[Metric]:
        for source in self._sources:
            try:
                yield from source()
            except Exception as e:  # noqa: BLE001 - a scrape must survive a broken source
                logger.warning(f"Metrics source {source.__name__} failed: {e}")


registry.register(_LiveStateCollector())


def render() -> bytes:
    return generate_latest(registry)

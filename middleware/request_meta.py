"""Request IDs, access logging, and in-flight counters.

Before this, a multi-user incident was undebuggable: agent-loop log lines
carried no way to tie them to a request or a person, and there was no view
of how loaded the process was. This middleware is the floor — an id on
every request (echoed as X-Request-ID so a user can quote it), one access
line per API request with identity and duration, and counters cheap enough
to read on every /api/admin/stats call.

Registered LAST in main.py's file order, which makes it the OUTERMOST
middleware: it times auth and rate limiting too, and by the time the
response comes back through, request.state.auth_identity is set for the
access line. Counters are plain ints guarded by a lock. Per-route counts and
latency histograms are recorded in services/metrics.py, only when
METRICS_ENABLED is on.
"""

import logging
import threading
import time
import uuid

from config import settings
from services.provenance import LINEAGE_HEADER, LINEAGE_ID

logger = logging.getLogger("clio.access")

_lock = threading.Lock()
_in_flight = 0
_requests_total = 0
_errors_total = 0  # responses >= 500
_started_monotonic = time.monotonic()


def stats() -> dict:
    with _lock:
        return {
            "in_flight": _in_flight,
            "requests_total": _requests_total,
            "errors_total": _errors_total,
            "uptime_seconds": int(time.monotonic() - _started_monotonic),
        }


def _should_log(path: str) -> bool:
    # /health is probed every 30s by Docker; static assets are noise.
    return path.startswith("/api/") and path != "/health"


def _observe(request, status_code: int, started: float) -> None:
    if settings.metrics_enabled:
        from services import metrics

        metrics.observe_request(request, status_code, time.perf_counter() - started)


async def track_request(request, call_next):
    """The middleware body — registered in main.py."""
    global _in_flight, _requests_total, _errors_total

    request_id = uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    started = time.perf_counter()

    with _lock:
        _in_flight += 1
        _requests_total += 1
    try:
        response = await call_next(request)
    except Exception:
        with _lock:
            _errors_total += 1
        _observe(request, 500, started)
        if _should_log(request.url.path):
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "%s %s 500 %dms user=%s rid=%s",
                request.method, request.url.path, duration_ms,
                getattr(request.state, "auth_identity", None) or "-", request_id,
            )
        raise
    finally:
        with _lock:
            _in_flight -= 1

    if response.status_code >= 500:
        with _lock:
            _errors_total += 1
    _observe(request, response.status_code, started)
    if _should_log(request.url.path):
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "%s %s %d %dms user=%s rid=%s",
            request.method, request.url.path, response.status_code, duration_ms,
            getattr(request.state, "auth_identity", None) or "-", request_id,
        )
    response.headers["X-Request-ID"] = request_id
    # Lineage marker (services/provenance.py): says which project this
    # server descends from, on every response, so a derived product is
    # recognisable from the wire alone.
    response.headers[LINEAGE_HEADER] = LINEAGE_ID
    return response

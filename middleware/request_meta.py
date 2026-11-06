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
from middleware import security_headers
from services.provenance import LINEAGE_HEADER, LINEAGE_ID

logger = logging.getLogger("clio.access")

_lock = threading.Lock()
_in_flight = 0
_requests_total = 0
_errors_total = 0  # responses >= 500
_started_monotonic = time.monotonic()

# What a person's or an app's work goes through. /health is probed every 30s by
# Docker, and static assets are noise.
_LOGGED_PREFIXES = ("/api/", "/documents", "/search", "/mcp")


def stats() -> dict:
    with _lock:
        return {
            "in_flight": _in_flight,
            "requests_total": _requests_total,
            "errors_total": _errors_total,
            "uptime_seconds": int(time.monotonic() - _started_monotonic),
        }


def _should_log(path: str) -> bool:
    return path.startswith(_LOGGED_PREFIXES)


def _observe(request, status_code: int, started: float) -> None:
    if settings.metrics_enabled:
        from services import metrics

        metrics.observe_request(request, status_code, time.perf_counter() - started)


def _log_access(request, status_code: int, started: float, request_id: str) -> None:
    if not _should_log(request.url.path):
        return
    duration_ms = int((time.perf_counter() - started) * 1000)
    user = getattr(request.state, "auth_identity", None) or "-"
    # The fields again under `extra`, for LOG_FORMAT=json. A path, never a query
    # string: that is where collection ids and share tokens travel.
    logger.info(
        "%s %s %d %dms user=%s rid=%s",
        request.method, request.url.path, status_code, duration_ms, user, request_id,
        extra={
            "http_method": request.method,
            "http_path": request.url.path,
            "http_status": status_code,
            "duration_ms": duration_ms,
            "user": user,
            "auth_via": getattr(request.state, "auth_via", None),
            "request_id": request_id,
        },
    )


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
        _log_access(request, 500, started, request_id)
        raise
    finally:
        with _lock:
            _in_flight -= 1

    if response.status_code >= 500:
        with _lock:
            _errors_total += 1
    _observe(request, response.status_code, started)
    _log_access(request, response.status_code, started, request_id)
    response.headers["X-Request-ID"] = request_id
    # Lineage marker (services/provenance.py): says which project this
    # server descends from, on every response, so a derived product is
    # recognisable from the wire alone.
    response.headers[LINEAGE_HEADER] = LINEAGE_ID
    security_headers.apply(request, response)
    return response

"""Per-identity request rate limiting.

One person must not be able to starve everyone else: chat turns burn the
shared LLM key (up to ~11 provider round-trips each) and hold a threadpool
slot for their whole agent loop, search takes the process-wide embedding
lock, and the pool serving all of it has ~40 slots. Before this middleware
the only backpressure was the upstream provider throttling the entire team
at once.

Token bucket per (class, identity): O(1) state, natural burst headroom for
someone firing follow-up questions, and an exact Retry-After. Identity is
the verified identity when the auth middleware resolved one, else the
client IP (behind a reverse proxy, the address uvicorn reports). In-memory
by design — the app is a single process (see docs/DEPLOYMENT.md).

This is deliberately separate from the auth middleware: auth only exists
when accounts, an identity provider or AUTH_PASSWORD is configured, and an open
deployment still deserves limits. Registration order in main.py makes auth
run first, so the identity is already on request.state here.

The 429 contract — shared with the daily token budget in
services/usage_service.py so the frontend needs one handler:
status 429, header ``Retry-After: <seconds>``, body
``{"detail": str, "retry_after_seconds": int}``.
"""

import logging
import math
import threading
import time
from typing import Dict, Optional, Tuple

from config import settings

logger = logging.getLogger(__name__)

# Idle buckets are pruned so the dict can't grow without bound (every
# distinct anonymous IP creates one).
_PRUNE_IDLE_SECONDS = 3600
_PRUNE_THRESHOLD = 5000


def _classify(path: str) -> Optional[str]:
    """Expense class for a path, or None when the path is never limited."""
    # Agent traffic has its own budget (0 = unlimited, the default) so a
    # fan-out of tool calls is never throttled by the human-facing classes.
    if path == "/mcp" or path.startswith("/mcp/"):
        return "mcp"
    if not path.startswith("/api/"):
        return None
    if path.startswith("/api/chat"):
        return "chat"
    if path.startswith("/api/search"):
        return "search"
    return "default"


class RateLimiter:
    """Token buckets keyed on (class, identity)."""

    def __init__(self):
        self._lock = threading.Lock()
        # (class, key) -> [tokens, last_refill_monotonic]
        self._buckets: Dict[Tuple[str, str], list] = {}
        self._allowed = {"chat": 0, "search": 0, "default": 0, "mcp": 0}
        self._rejected = {"chat": 0, "search": 0, "default": 0, "mcp": 0}

    @staticmethod
    def _limit_for(cls: str) -> int:
        return {
            "chat": settings.rate_limit_chat_per_minute,
            "search": settings.rate_limit_search_per_minute,
            "default": settings.rate_limit_default_per_minute,
            "mcp": settings.rate_limit_mcp_per_minute,
        }[cls]

    def check(self, cls: str, key: str) -> Optional[int]:
        """None = allowed; otherwise seconds until a token frees up."""
        per_minute = self._limit_for(cls)
        if per_minute <= 0:  # 0 = this class unlimited
            with self._lock:
                self._allowed[cls] += 1
            return None

        refill_per_s = per_minute / 60.0
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get((cls, key))
            if bucket is None:
                # Capacity = the per-minute number: a fresh caller can burst
                # that many at once, then settles to the refill rate.
                bucket = [float(per_minute), now]
                self._buckets[(cls, key)] = bucket
            tokens, last = bucket
            tokens = min(float(per_minute), tokens + (now - last) * refill_per_s)
            if tokens >= 1.0:
                bucket[0] = tokens - 1.0
                bucket[1] = now
                self._allowed[cls] += 1
                return None
            bucket[0] = tokens
            bucket[1] = now
            self._rejected[cls] += 1
            return max(1, math.ceil((1.0 - tokens) / refill_per_s))

    def _prune(self) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._buckets) <= _PRUNE_THRESHOLD:
                return
            stale = [k for k, (_, last) in self._buckets.items()
                     if now - last > _PRUNE_IDLE_SECONDS]
            for k in stale:
                del self._buckets[k]

    def stats(self) -> Dict[str, Dict[str, int]]:
        with self._lock:
            return {
                cls: {"allowed": self._allowed[cls], "rejected": self._rejected[cls]}
                for cls in self._allowed
            }

    def reset(self) -> None:
        """Testing hook: drop all buckets and counters."""
        with self._lock:
            self._buckets.clear()
            for cls in self._allowed:
                self._allowed[cls] = 0
                self._rejected[cls] = 0


# Single process-wide limiter; /api/admin/stats reads it.
rate_limiter = RateLimiter()


def resolve_limit_key(request) -> str:
    """Verified identity when auth resolved one, else best-available IP."""
    # Auth may pin a finer key (a personal MCP token) so one credential's
    # budget is its own, whatever machine or user it runs as.
    pinned = getattr(request.state, "rate_limit_key", None)
    if pinned:
        return pinned
    identity = getattr(request.state, "auth_identity", None)
    if identity:
        return identity
    # Never a client-supplied header: anything the caller can set, the caller
    # can rotate to dodge the limit. Behind a trusted reverse proxy, run
    # uvicorn with --proxy-headers and FORWARDED_ALLOW_IPS set to the proxy.
    return request.client.host if request.client else "unknown"


async def enforce_rate_limit(request, call_next):
    """The middleware body — registered in main.py."""
    from fastapi.responses import JSONResponse

    if not settings.rate_limit_enabled or request.method == "OPTIONS":
        return await call_next(request)
    cls = _classify(request.url.path)
    if cls is None:
        return await call_next(request)

    retry_after = rate_limiter.check(cls, resolve_limit_key(request))
    if retry_after is not None:
        rate_limiter._prune()
        return JSONResponse(
            status_code=429,
            content={
                "detail": (
                    "Rate limit reached — you're sending requests faster than "
                    f"this deployment allows. Try again in {retry_after}s."
                ),
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )
    return await call_next(request)

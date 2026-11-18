"""Failed sign-ins per client address, so a password cannot be guessed at speed.

The request rate limiter (middleware/rate_limit.py) runs inside the auth
middleware, because it keys on the identity auth resolves; a request that
fails authentication is answered before it is ever counted there. This is
the other half: the auth middleware records each failed credential here
(a wrong password, MCP token, app key or identity assertion; a request that
presents none is not a guess), and asks before authenticating whether the
address has run out of attempts. One that has gets 429 with Retry-After on
every credentialed request until its oldest failure leaves the window, the
right password included: otherwise the 429 would itself tell a guesser when
a guess was right.

A sliding window of failure times per address: exact, small (at most
AUTH_FAILURE_LIMIT entries each) and pruned as it goes. In-memory, like the
rate limiter: the app is a single process. The address is the one uvicorn
reports, which behind a proxy listed in FORWARDED_ALLOW_IPS is the client's.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional

import config

logger = logging.getLogger(__name__)

_PRUNE_THRESHOLD = 5000


class FailedAuthLimiter:
    """Failure timestamps per client address."""

    def __init__(self):
        self._lock = threading.Lock()
        self._failures: Dict[str, Deque[float]] = {}
        self.lockouts = 0

    @staticmethod
    def _limits() -> tuple[int, int]:
        cfg = config.settings
        return int(cfg.auth_failure_limit), max(1, int(cfg.auth_failure_window_seconds))

    @staticmethod
    def _expire(failures: Deque[float], now: float, window: int) -> None:
        while failures and now - failures[0] >= window:
            failures.popleft()

    def retry_after(self, key: str) -> Optional[int]:
        """None when this address may try a credential; else seconds to wait."""
        limit, window = self._limits()
        if limit <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            failures = self._failures.get(key)
            if not failures:
                return None
            self._expire(failures, now, window)
            if len(failures) < limit:
                return None
            return max(1, math.ceil(failures[0] + window - now))

    def record_failure(self, key: str, kind: str, path: str) -> None:
        """Count one failed credential, and say so once the address is cut off."""
        limit, window = self._limits()
        logger.info("Failed sign-in (%s) from %s on %s", kind, key, path)
        _audit("auth.sign_in_failed", key, {"kind": kind, "path": path})
        if limit <= 0:
            return
        now = time.monotonic()
        with self._lock:
            failures = self._failures.setdefault(key, deque())
            self._expire(failures, now, window)
            failures.append(now)
            count = len(failures)
            if len(self._failures) > _PRUNE_THRESHOLD:
                self._prune(now, window)
        if count == limit:
            self.lockouts += 1
            _audit("auth.lockout", key, {"failures": count, "window_seconds": window, "kind": kind, "path": path})
            logger.warning(
                "SECURITY: %d failed sign-ins from %s within %ds (latest: %s on %s). Further "
                "credentialed requests from that address are refused with 429 until the "
                "failures age out.", count, key, window, kind, path,
            )

    def _prune(self, now: float, window: int) -> None:
        for key in [k for k, q in self._failures.items() if not q or now - q[-1] >= window]:
            del self._failures[key]

    def reset(self) -> None:
        """Testing hook: forget every failure."""
        with self._lock:
            self._failures.clear()
            self.lockouts = 0


def _audit(action: str, address: str, detail: dict) -> None:
    """Failed sign-ins and lockouts belong in the audit trail; bounded by the
    limit itself (an address stops being checked once it is locked out)."""
    from services import audit

    audit.record(action, actor=None, target=address, detail=detail)


failed_auth_limiter = FailedAuthLimiter()


def too_many_failures(request, retry_after: int):
    """The 429 for an address out of attempts, in the rate limiter's shape."""
    from fastapi.responses import JSONResponse

    body = {
        "detail": f"Too many failed sign-in attempts from your address. Try again in {retry_after}s.",
        "retry_after_seconds": retry_after,
    }
    if request.url.path.startswith("/api/v1/"):
        body["code"] = "rate_limited"
    return JSONResponse(status_code=429, content=body, headers={"Retry-After": str(retry_after)})

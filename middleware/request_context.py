"""What code deep inside a request can know about it without the Request.

The audit trail (services/audit.py) records where a request came from, its
request id and how it signed in; log lines carry the request id
(services/log_format.py). Both are written far from any Request object, in
services and background helpers, so this pure ASGI middleware binds the
request's scope to a contextvar for the request's lifetime.

It holds the scope itself, not values copied out of it: the request id is
put there by middleware/request_meta.py (outside this one) before this runs,
and the sign-in method by the auth middleware in main.py (inside it) after,
and both land in the same ``scope["state"]`` dict that ``request.state``
writes to.
"""

import ipaddress
import threading
from collections import Counter
from contextvars import ContextVar
from typing import Optional

_scope: ContextVar[Optional[dict]] = ContextVar("clio_request_scope", default=None)

# Which peer addresses requests arrive from, for the readiness report (E11):
# behind a proxy uvicorn does not trust, every request comes from the proxy,
# and the failed sign-in budget, kept per address, is then shared by
# everyone. A handful of distinct addresses is all that question needs.
_MAX_ADDRESSES = 64
_addresses_lock = threading.Lock()
_addresses: Counter = Counter()
_requests_seen = 0


def _observe(scope: dict) -> None:
    global _requests_seen
    client = scope.get("client")
    host = client[0] if client else None
    with _addresses_lock:
        _requests_seen += 1
        if host and (host in _addresses or len(_addresses) < _MAX_ADDRESSES):
            _addresses[host] += 1


def single_private_source(min_requests: int = 50) -> Optional[str]:
    """The one private, non-loopback address every request so far came from,
    once there have been ``min_requests``; else None."""
    with _addresses_lock:
        if _requests_seen < min_requests or len(_addresses) != 1:
            return None
        (host,) = _addresses
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    return host if ip.is_private and not ip.is_loopback else None


def reset_addresses() -> None:
    """Testing hook."""
    global _requests_seen
    with _addresses_lock:
        _addresses.clear()
        _requests_seen = 0


class RequestContext:
    """Registered in main.py just inside request_meta's track_request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        _observe(scope)
        token = _scope.set(scope)
        try:
            await self.app(scope, receive, send)
        finally:
            _scope.reset(token)


def _state() -> dict:
    scope = _scope.get()
    if not scope:
        return {}
    return scope.get("state") or {}


def request_id() -> Optional[str]:
    """This request's id (the X-Request-ID it is answered with), or None outside one."""
    return _state().get("request_id")


def source_ip() -> Optional[str]:
    """The peer address uvicorn reports (behind a proxy in FORWARDED_ALLOW_IPS, the client's)."""
    scope = _scope.get()
    client = scope.get("client") if scope else None
    return client[0] if client else None


def auth_method() -> Optional[str]:
    """How the caller signed in: password, mcp_token, a verifier's name, or
    None (no sign-in needed, or not yet decided)."""
    return _state().get("auth_via")


def audit_context() -> dict:
    """The three columns every audit row carries."""
    return {"source_ip": source_ip(), "request_id": request_id(), "auth_method": auth_method()}

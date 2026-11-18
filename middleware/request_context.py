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

from contextvars import ContextVar
from typing import Optional

_scope: ContextVar[Optional[dict]] = ContextVar("clio_request_scope", default=None)


class RequestContext:
    """Registered in main.py just inside request_meta's track_request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
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

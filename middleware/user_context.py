"""Request-scoped user identity for private-collections mode.

Identity is never taken from a client-controlled header. The auth middleware
in main.py verifies the Cloudflare Access JWT (signature, issuer, audience,
expiry) and records the result on ``request.state.auth_identity`` — a person's
email for SSO logins, the service token's common_name for MCP clients, or
``None`` for AUTH_PASSWORD callers, who are authenticated but anonymous.

Two consumers need that identity:

- FastAPI routers, via ``Depends(get_current_user_id)`` — they have the
  Request object.
- Service-layer code with no Request in reach (the chat tool loop, the
  mounted /mcp app's tools, ``deps.get_indexer``) — they read the contextvar,
  which the auth middleware (and the MCP ASGI wrapper) sets per request.

With PRIVATE_COLLECTIONS off, both paths return ``default_user_id`` and
nothing here has any effect — the shared-appliance behaviour is unchanged.
"""

import logging
from contextvars import ContextVar, Token
from typing import Optional

from fastapi import Request

from config import settings

logger = logging.getLogger(__name__)

# None = anonymous (password-authenticated, or private mode off and unset).
_request_user_id: ContextVar[Optional[str]] = ContextVar(
    "clio_request_user_id", default=None
)
_request_identity: ContextVar[Optional[str]] = ContextVar(
    "clio_request_identity", default=None
)

# Users already upserted by this process — avoids a DB write per request.
_provisioned_users: set[str] = set()


def set_request_user(user_id: Optional[str]) -> Token:
    """Bind the verified identity to the current request context."""
    return _request_user_id.set(user_id)


def reset_request_user(token: Token) -> None:
    _request_user_id.reset(token)


def set_request_identity(identity: Optional[str]) -> Token:
    """Bind the verified caller identity for admin checks in every mode."""
    return _request_identity.set(identity)


def reset_request_identity(token: Token) -> None:
    _request_identity.reset(token)


def get_request_user() -> Optional[str]:
    """The verified identity for the current request, or None if anonymous.

    Only meaningful in private-collections mode; callers gate on
    ``settings.private_collections`` before treating None as anonymous.
    """
    if not settings.private_collections:
        return settings.default_user_id
    return _request_user_id.get()


def get_request_identity() -> Optional[str]:
    """The verified identity for this request, independent of collection mode."""
    return _request_identity.get()


def _provision(user_id: str) -> None:
    if user_id in _provisioned_users:
        return
    try:
        from services.app_database import app_db

        app_db.upsert_user(user_id)
        _provisioned_users.add(user_id)
    except Exception as e:
        logger.warning(f"Could not upsert user {user_id}: {e}")


def get_current_user_id(request: Request) -> Optional[str]:
    """FastAPI dependency: the current user's verified identity.

    Single-user mode: always ``settings.default_user_id``.
    Private-collections mode: the Access JWT identity, or None for
    password-authenticated (anonymous) callers.
    """
    if not settings.private_collections:
        return settings.default_user_id

    identity = getattr(request.state, "auth_identity", None)
    if identity:
        _provision(identity)
        return identity
    return None

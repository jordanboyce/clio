"""Who counts as an administrator.

``ADMIN_EMAILS`` - an operator allowlist in the environment - names them.
Clio has no accounts of its own, so this only means something when the
identity comes from an external provider (OIDC, a trusted proxy) or from an
MCP token's owner. With it empty nobody is an admin, and settings that apply
to the whole deployment stay open only on an open appliance (see
``api.deps.require_admin``).
"""

from __future__ import annotations

from typing import List, Optional


def admin_emails() -> List[str]:
    import config

    return [e.strip().lower() for e in (config.settings.admin_emails or "").split(",") if e.strip()]


def admin_gate_active() -> bool:
    """Whether admin-only endpoints are actually restricted.

    An operator list can only be matched against a verified identity. With
    none configured (the default open appliance) no caller could ever match
    it, so enforcing it would lock the owner out of their own settings; the
    gate applies once an identity provider exists.
    """
    from services.identity import active_provider

    return bool(admin_emails()) and bool(active_provider())


def is_admin(user_id: Optional[str]) -> bool:
    if not user_id:
        return False
    return user_id.strip().lower() in admin_emails()

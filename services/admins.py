"""Who counts as an administrator.

Two sources, either of which is enough:

  * ``ADMIN_EMAILS`` - an operator allowlist in the environment. It is the
    only source when the identity comes from an external provider (OIDC, a
    trusted proxy), which knows nothing about Clio's roles.
  * the ``is_admin`` flag on a built-in account (``IDENTITY_PROVIDER=local``),
    which an existing admin can grant and revoke in the Users panel.

With neither configured nobody is an admin. Settings that apply to the whole
deployment fail closed rather than open.
"""

from __future__ import annotations

from typing import List, Optional


def admin_emails() -> List[str]:
    import config

    settings = config.settings
    return [e.strip().lower() for e in (settings.admin_emails or "").split(",") if e.strip()]


def _uses_local_accounts() -> bool:
    from services.identity import active_provider

    return active_provider() == "local"


def has_admin_policy() -> bool:
    """True when this deployment has any notion of who the admins are."""
    return bool(admin_emails()) or _uses_local_accounts()


def is_admin(user_id: Optional[str]) -> bool:
    if not user_id:
        return False
    if user_id.strip().lower() in admin_emails():
        return True
    if _uses_local_accounts():
        from services.local_auth import is_local_admin

        return is_local_admin(user_id)
    return False

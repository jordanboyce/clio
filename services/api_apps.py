"""Registered apps: the only credentials /api/v1 accepts.

A consuming application needs something neither of the existing credentials
gives it. The shared password opens everything and names nobody. A person's IdP
token carries that person's access. A personal MCP token works on /mcp only. A
registered app is created by an admin, and carries:

  - a name, which every request it makes is attributed to (``app:<name>``);
  - an allowlist of collections: the only ones it can see, whatever their
    sensitivity, and nothing it can widen. It owns nothing;
  - a write flag, off by default;
  - its own request budgets, so a busy app does not spend a person's.

An app proves itself with a key issued here, or with a client-credentials token
from the site's own IdP whose ``sub`` the admin recorded. Matching on ``sub``
rather than on a client id claim (``azp``, ``appid``) is deliberate: a person
signed in through that same client carries the same client id, and must not
inherit the app's access by doing so.

Either credential works on /api/v1 and nowhere else (main.py), so a leaked key
cannot reach the admin API, the UI or /mcp. As with personal MCP tokens only a
hash is stored and the plaintext is returned once.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta
from services.clock import utcnow
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

KEY_PREFIX = "clio_app_"
_PREFIX_DISPLAY_LEN = len(KEY_PREFIX) + 6

# Names land in audit rows, log lines and a metric label.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_EXPIRY_DAYS = 3650
MAX_ROTATION_GRACE_HOURS = 720
DEFAULT_ROTATION_GRACE_HOURS = 24

# Stamping last_used_at on every call would be one database write per request.
_TOUCH_INTERVAL_SECONDS = 60
# A revoked app's subject stays listed, so its token is refused rather than
# falling through to the rules for people.
_SUBJECTS_TTL_SECONDS = 30

_touched_at: Dict[str, float] = {}
_subjects: frozenset[str] = frozenset()
_subjects_loaded_at = 0.0
_subjects_lock = threading.Lock()


class AppConflict(ValueError):
    """The name or IdP subject already belongs to another app."""


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _new_key() -> str:
    return f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"


def _now() -> str:
    return utcnow().isoformat()


def _expiry(days: Optional[int]) -> Optional[str]:
    if days is None:
        return None
    if not 1 <= int(days) <= MAX_EXPIRY_DAYS:
        raise ValueError(f"expires_in_days must be between 1 and {MAX_EXPIRY_DAYS}.")
    return (utcnow() + timedelta(days=int(days))).isoformat()


def _has_passed(moment: Optional[str]) -> bool:
    """An unparsable timestamp counts as passed: a credential whose lifetime
    cannot be established should not keep working."""
    if not moment:
        return False
    try:
        parsed = datetime.fromisoformat(str(moment))
    except ValueError:
        return True
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None) - parsed.utcoffset()
    return parsed <= utcnow()


def _decode(row: Dict[str, Any]) -> Dict[str, Any]:
    app = dict(row)
    try:
        app["collections"] = json.loads(app.get("collections") or "[]")
    except (TypeError, ValueError):
        app["collections"] = []
    app["can_write"] = bool(app.get("can_write"))
    app["actor"] = f"app:{app['name']}"
    return app


def public_view(app: Dict[str, Any]) -> Dict[str, Any]:
    """An app as an admin sees it: everything except the hashes."""
    status = "active"
    if app.get("revoked_at"):
        status = "revoked"
    elif _has_passed(app.get("expires_at")):
        status = "expired"
    return {
        "id": app["id"],
        "name": app["name"],
        "status": status,
        "collections": app["collections"],
        "can_write": app["can_write"],
        "key_prefix": app.get("key_prefix"),
        "oidc_subject": app.get("oidc_subject"),
        "search_per_minute": app.get("search_per_minute"),
        "ask_per_minute": app.get("ask_per_minute"),
        "other_per_minute": app.get("other_per_minute"),
        "created_by": app.get("created_by"),
        "created_at": app.get("created_at"),
        "expires_at": app.get("expires_at"),
        "revoked_at": app.get("revoked_at"),
        "last_used_at": app.get("last_used_at"),
        "previous_key_expires_at": app.get("previous_key_expires_at"),
    }


def _invalidate_subjects() -> None:
    global _subjects_loaded_at
    with _subjects_lock:
        _subjects_loaded_at = 0.0


def _check_unique(name: Optional[str], subject: Optional[str], app_id: Optional[str] = None) -> None:
    for other in list_apps():
        if other["id"] == app_id:
            continue
        if name and other["name"].lower() == name.lower():
            raise AppConflict(f"An app named '{other['name']}' already exists.")
        if subject and other.get("oidc_subject") == subject:
            raise AppConflict(f"The IdP subject is already registered to '{other['name']}'.")


def create_app(
    name: str,
    collections: List[str],
    *,
    can_write: bool = False,
    oidc_subject: Optional[str] = None,
    issue_key: bool = True,
    expires_in_days: Optional[int] = None,
    search_per_minute: Optional[int] = None,
    ask_per_minute: Optional[int] = None,
    other_per_minute: Optional[int] = None,
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Register an app. Returns it with ``key``, once, when one was issued."""
    from services.app_database import app_db

    name = (name or "").strip()
    if not NAME_PATTERN.match(name):
        raise ValueError(
            "An app name is 1-64 letters, digits, dots, dashes or underscores, starting with a letter or digit."
        )
    if not collections:
        raise ValueError("An app needs at least one collection.")
    subject = (oidc_subject or "").strip() or None
    if not issue_key and not subject:
        raise ValueError("An app with no key needs an oidc_subject, or it could never authenticate.")
    _check_unique(name, subject)

    key = _new_key() if issue_key else None
    row = {
        "id": str(uuid.uuid4()),
        "name": name,
        "key_hash": _hash(key) if key else None,
        "key_prefix": key[:_PREFIX_DISPLAY_LEN] if key else None,
        "oidc_subject": subject,
        "collections": json.dumps(list(dict.fromkeys(collections))),
        "can_write": bool(can_write),
        "search_per_minute": search_per_minute,
        "ask_per_minute": ask_per_minute,
        "other_per_minute": other_per_minute,
        "created_by": created_by,
        "created_at": _now(),
        "expires_at": _expiry(expires_in_days),
    }
    app_db.create_api_app(row)
    _invalidate_subjects()
    view = public_view(_decode(row))
    if key:
        view["key"] = key
    return view


def list_apps() -> List[Dict[str, Any]]:
    from services.app_database import app_db

    return [_decode(row) for row in app_db.list_api_apps()]


def get_app(app_id: str) -> Optional[Dict[str, Any]]:
    from services.app_database import app_db

    row = app_db.get_api_app(app_id)
    return _decode(row) if row else None


def update_app(app_id: str, changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Apply an admin's edits. The name stays: audit rows are written against it."""
    from services.app_database import app_db

    app = get_app(app_id)
    if app is None:
        return None
    if app.get("revoked_at"):
        raise ValueError("This app is revoked. Register a new one instead.")

    fields: Dict[str, Any] = {}
    for name in ("can_write", "search_per_minute", "ask_per_minute", "other_per_minute"):
        if name in changes:
            fields[name] = changes[name]
    if "collections" in changes:
        if not changes["collections"]:
            raise ValueError("An app needs at least one collection.")
        fields["collections"] = json.dumps(list(dict.fromkeys(changes["collections"])))
    if "oidc_subject" in changes:
        subject = (changes["oidc_subject"] or "").strip() or None
        if not subject and not app.get("key_hash"):
            raise ValueError("Removing the subject would leave this app no way to authenticate. Rotate a key in first.")
        _check_unique(None, subject, app_id)
        fields["oidc_subject"] = subject
    if "expires_in_days" in changes:
        fields["expires_at"] = _expiry(changes["expires_in_days"])

    if fields:
        app_db.update_api_app(app_id, fields)
        _invalidate_subjects()
    return public_view(get_app(app_id))


def rotate_key(app_id: str, grace_hours: int = DEFAULT_ROTATION_GRACE_HOURS) -> Optional[Dict[str, Any]]:
    """Issue a new key. The old one keeps working for ``grace_hours``, so the
    consuming app can be redeployed without an outage. 0 ends it now, for a key
    that leaked. Rotating again ends any earlier key's grace at once."""
    from services.app_database import app_db

    if not 0 <= int(grace_hours) <= MAX_ROTATION_GRACE_HOURS:
        raise ValueError(f"grace_hours must be between 0 and {MAX_ROTATION_GRACE_HOURS}.")
    app = get_app(app_id)
    if app is None:
        return None
    if app.get("revoked_at"):
        raise ValueError("This app is revoked. Register a new one instead.")

    keep_old = grace_hours > 0 and app.get("key_hash")
    key = _new_key()
    app_db.update_api_app(app_id, {
        "key_hash": _hash(key),
        "key_prefix": key[:_PREFIX_DISPLAY_LEN],
        "previous_key_hash": app["key_hash"] if keep_old else None,
        "previous_key_expires_at": (
            (utcnow() + timedelta(hours=int(grace_hours))).isoformat() if keep_old else None
        ),
    })
    view = public_view(get_app(app_id))
    view["key"] = key
    return view


def revoke_app(app_id: str) -> Optional[Dict[str, Any]]:
    """End an app's access for good. The row stays, for the audit trail to name."""
    from services.app_database import app_db

    app = get_app(app_id)
    if app is None:
        return None
    if not app.get("revoked_at"):
        app_db.update_api_app(app_id, {"revoked_at": _now(), "previous_key_hash": None})
        _invalidate_subjects()
    return public_view(get_app(app_id))


def _usable(app: Dict[str, Any]) -> bool:
    return not app.get("revoked_at") and not _has_passed(app.get("expires_at"))


def _touch(app: Dict[str, Any]) -> None:
    """Best effort, and at most once a minute per app: never fails the request."""
    from services.app_database import app_db

    now = time.monotonic()
    if now - _touched_at.get(app["id"], -_TOUCH_INTERVAL_SECONDS) < _TOUCH_INTERVAL_SECONDS:
        return
    _touched_at[app["id"]] = now
    try:
        app_db.update_api_app(app["id"], {"last_used_at": _now()})
    except Exception as e:  # noqa: BLE001 - bookkeeping, not authentication
        logger.warning(f"Could not update last_used_at for app {app['name']}: {e}")


def verify_key(presented: str) -> Optional[Dict[str, Any]]:
    """The app a presented key belongs to, or None: unknown, revoked, expired,
    or a rotated-out key past its grace."""
    from services.app_database import app_db

    if not presented or not presented.startswith(KEY_PREFIX):
        return None
    hashed = _hash(presented)
    row = app_db.get_api_app_by_key_hash(hashed)
    if not row:
        return None
    app = _decode(row)
    if hashed != app.get("key_hash") and _has_passed(app.get("previous_key_expires_at")):
        return None
    if not _usable(app):
        return None
    _touch(app)
    return app


def _registered_subjects() -> frozenset[str]:
    global _subjects, _subjects_loaded_at

    with _subjects_lock:
        if time.monotonic() - _subjects_loaded_at < _SUBJECTS_TTL_SECONDS and _subjects_loaded_at:
            return _subjects
    loaded = frozenset(a["oidc_subject"] for a in list_apps() if a.get("oidc_subject"))
    with _subjects_lock:
        _subjects, _subjects_loaded_at = loaded, time.monotonic()
    return loaded


def jwt_subject_is_registered(token: str) -> bool:
    """Is this bearer JWT shaped as a registered app's? Unverified on purpose.

    It only decides which rules the token is checked under, and a forged subject
    gains nothing: ``verify_jwt`` still has to pass, and a token that claims an
    app's subject is refused rather than being tried as a person.
    """
    import jwt

    subjects = _registered_subjects()
    if not subjects:
        return False
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return False
    return str(claims.get("sub") or "") in subjects


def verify_jwt(token: str) -> Optional[Dict[str, Any]]:
    """The app a client-credentials token belongs to, or None.

    The signature, issuer, audience and expiry are checked exactly as for a
    person. OIDC_REQUIRED_CLAIMS is not: it narrows which people get in
    (``groups=clio-users``), a service principal usually has no such claims, and
    the admin who recorded this subject has already decided.
    """
    from services import identity
    from services.app_database import app_db

    if identity.active_provider() != "oidc":
        return None
    verifier = identity.get_identity_verifier()
    claims = verifier.verified_claims(token) if verifier is not None else None
    subject = str((claims or {}).get("sub") or "")
    if not subject:
        return None
    row = app_db.get_api_app_by_subject(subject)
    if not row:
        return None
    app = _decode(row)
    if not _usable(app):
        return None
    _touch(app)
    return app


def rate_limit_overrides(app: Dict[str, Any]) -> Dict[str, int]:
    """An app's own budgets, by limiter class. Unset falls back to the deployment's."""
    pairs = (("search", "search_per_minute"), ("chat", "ask_per_minute"), ("default", "other_per_minute"))
    return {cls: app[field] for cls, field in pairs if app.get(field) is not None}

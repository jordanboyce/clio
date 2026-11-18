"""Personal MCP access tokens.

Lets anyone who can already reach the app (a signed-in account, or the shared
AUTH_PASSWORD) mint their own bearer credential for headless MCP
clients — Claude Code, Codex, GitHub Copilot — instead of hand-editing
.env / .mcp.json / config.toml on every machine that wants a connection.

A token is deliberately narrow:
  - main.py's auth middleware only ever accepts it on paths under /mcp, so a
    leaked token cannot reach the rest of the API or the UI.
  - Under PRIVATE_COLLECTIONS it carries the identity of whoever created it,
    so an MCP client presenting it sees exactly the collections that person
    can see — no separate "clio-mcp" identity to remember to share
    collections to.
  - Only the sha256 hash is ever persisted. The plaintext is returned once,
    on creation, and cannot be recovered afterwards — same trust model as a
    GitHub personal access token.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from services.clock import utcnow
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Recognizable prefix: lets verify_token() reject obviously-foreign bearer
# values (e.g. the AUTH_PASSWORD or an OIDC access token) with a
# cheap string check before ever touching the database.
TOKEN_PREFIX = "asy_mcp_"
_PREFIX_DISPLAY_LEN = len(TOKEN_PREFIX) + 6

# Longest lifetime a token may be minted with. Long enough for a standing
# integration, short enough that "never expires" stays a deliberate choice.
# MCP_TOKEN_MAX_DAYS narrows it per deployment.
MAX_EXPIRY_DAYS = 3650

# Ids of tokens beyond the lifetime cap already named in the log by this
# process, so a busy agent does not repeat the line on every call.
_flagged_ids: set = set()


def max_lifetime_days() -> int:
    """MCP_TOKEN_MAX_DAYS, clamped to 0..MAX_EXPIRY_DAYS. 0 means no cap."""
    from config import settings

    try:
        days = int(settings.mcp_token_max_days)
    except (TypeError, ValueError):
        days = 0
    return max(0, min(days, MAX_EXPIRY_DAYS))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token(
    user_id: Optional[str],
    name: str,
    collection_scope: Optional[List[str]] = None,
    can_write: bool = False,
    expires_in_days: Optional[int] = None,
    allowlist: bool = False,
    can_overwrite_uploads: bool = False,
) -> Dict[str, Any]:
    """Create and persist a new token. Returns it WITH the plaintext — once.

    Callers must show `token` to the user immediately and never log it or
    store it themselves; it cannot be retrieved again after this call.

    ``collection_scope`` lists restricted collections this token may reach
    over MCP (services/governance.py). Only collections the creator can
    already access may be named — the caller validates that.

    ``can_write`` lets the token add and update sources (the
    ``write_document`` MCP tool). Off by default so a leaked read token
    cannot plant content; the collection's own write permission and the
    AUP gate still apply on top.

    ``expires_in_days`` stamps an expiry after which verify_token refuses
    the token. It may be 1..MCP_TOKEN_MAX_DAYS (``max_lifetime_days``);
    None means the longest allowed, and never expires only when the cap is 0.

    ``allowlist`` turns ``collection_scope`` into the *only* collections
    the token may see over MCP, restricted or not. It requires a non-empty
    scope: an allowlist of nothing would be a token that sees nothing.

    ``can_overwrite_uploads`` lets the token's agents replace a source a
    person uploaded. Without it they may create notes, edit their own, and
    append to anything, but a replace of an upload is refused, because it
    keeps no copy of the person's version. It requires ``can_write``.
    """
    from services.app_database import app_db

    name = (name or "").strip() or "Unnamed device"
    plaintext = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    scope = [str(c).strip() for c in (collection_scope or []) if str(c).strip()] or None
    if allowlist and not scope:
        raise ValueError("An allowlisted token needs at least one collection in its scope.")
    if can_overwrite_uploads and not can_write:
        raise ValueError("A token may only replace uploads if it may write: set can_write too.")
    cap = max_lifetime_days()
    longest = cap or MAX_EXPIRY_DAYS
    days = cap or None
    if expires_in_days is not None:
        days = int(expires_in_days)
        if not 1 <= days <= longest:
            hint = " (MCP_TOKEN_MAX_DAYS on this deployment)" if cap else ""
            raise ValueError(f"expires_in_days must be between 1 and {longest}{hint}.")
    expires_at = (utcnow() + timedelta(days=days)).isoformat() if days else None
    record = app_db.create_mcp_token(
        user_id=user_id,
        name=name,
        token_hash=_hash(plaintext),
        token_prefix=plaintext[:_PREFIX_DISPLAY_LEN],
        collection_scope=scope,
        can_write=bool(can_write),
        expires_at=expires_at,
        allowlist=bool(allowlist),
        can_overwrite_uploads=bool(can_overwrite_uploads),
    )
    record["token"] = plaintext
    return record


def is_expired(record: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """True when the record carries an expiry that has passed.

    Timestamps are naive UTC ISO strings, the same shape app_database
    writes for created_at. An unparsable value counts as expired: a token
    whose lifetime cannot be established should not keep working.
    """
    raw = record.get("expires_at")
    if not raw:
        return False
    try:
        expires = datetime.fromisoformat(str(raw))
    except ValueError:
        return True
    if expires.tzinfo is not None:
        expires = expires.replace(tzinfo=None) - expires.utcoffset()
    return expires <= (now or utcnow())


def exceeds_max_lifetime(record: Dict[str, Any], cap: Optional[int] = None) -> bool:
    """True for a token whose lifetime is longer than MCP_TOKEN_MAX_DAYS
    allows: one minted before the cap, with no expiry or a longer one. Such
    tokens keep working; this is what flags them for rotation."""
    cap = max_lifetime_days() if cap is None else cap
    if not cap:
        return False
    raw = record.get("expires_at")
    if not raw:
        return True
    try:
        expires = datetime.fromisoformat(str(raw))
        created = datetime.fromisoformat(str(record.get("created_at")))
    except (TypeError, ValueError):
        return False
    if expires.tzinfo is not None:
        expires = expires.replace(tzinfo=None) - expires.utcoffset()
    if created.tzinfo is not None:
        created = created.replace(tzinfo=None) - created.utcoffset()
    # A minute of slack: expires_at and created_at are stamped moments apart.
    return expires - created > timedelta(days=cap, minutes=1)


def list_tokens(user_id: Optional[str]) -> List[Dict[str, Any]]:
    from services.app_database import app_db

    cap = max_lifetime_days()
    tokens = app_db.list_mcp_tokens(user_id)
    for token in tokens:
        token["exceeds_max_lifetime"] = exceeds_max_lifetime(token, cap)
    return tokens


def revoke_token(token_id: str, user_id: Optional[str]) -> bool:
    from services.app_database import app_db

    return app_db.revoke_mcp_token(token_id, user_id)


def verify_token(presented: str) -> Optional[Dict[str, Any]]:
    """Resolve a presented bearer value to its token record, or None.

    None covers: not our prefix, unknown hash, revoked, or expired. Updates
    last_used_at and use_count on success (best-effort — never fails the
    request over it).
    """
    if not presented or not presented.startswith(TOKEN_PREFIX):
        return None
    from services.app_database import app_db

    record = app_db.get_mcp_token_by_hash(_hash(presented))
    if not record or record.get("revoked_at") or is_expired(record):
        return None
    if record["id"] not in _flagged_ids and exceeds_max_lifetime(record):
        _flagged_ids.add(record["id"])
        logger.warning(
            "MCP token %s (%s, owner %s) has %s, beyond MCP_TOKEN_MAX_DAYS=%d. "
            "It keeps working; revoke it and mint a new one.",
            record["id"], record.get("token_prefix"), record.get("user_id") or "shared",
            "no expiry" if not record.get("expires_at") else f"an expiry of {record['expires_at']}",
            max_lifetime_days(),
        )
    try:
        app_db.touch_mcp_token(record["id"])
    except Exception as e:
        logger.warning(f"Could not update last_used_at for MCP token {record['id']}: {e}")
    return record

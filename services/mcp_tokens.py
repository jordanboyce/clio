"""Personal MCP access tokens.

Lets anyone who can already reach the app (a signed-in account, or the shared
AUTH_PASSWORD) mint their own long-lived bearer credential for headless MCP
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
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Recognizable prefix: lets verify_token() reject obviously-foreign bearer
# values (e.g. the AUTH_PASSWORD or an OIDC access token) with a
# cheap string check before ever touching the database.
TOKEN_PREFIX = "asy_mcp_"
_PREFIX_DISPLAY_LEN = len(TOKEN_PREFIX) + 6

# Longest lifetime a token may be minted with. Long enough for a standing
# integration, short enough that "never expires" stays a deliberate choice.
MAX_EXPIRY_DAYS = 3650


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token(
    user_id: Optional[str],
    name: str,
    collection_scope: Optional[List[str]] = None,
    can_write: bool = False,
    expires_in_days: Optional[int] = None,
    allowlist: bool = False,
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

    ``expires_in_days`` (1..MAX_EXPIRY_DAYS) stamps an expiry after which
    verify_token refuses the token; None means it never expires.

    ``allowlist`` turns ``collection_scope`` into the *only* collections
    the token may see over MCP, restricted or not. It requires a non-empty
    scope: an allowlist of nothing would be a token that sees nothing.
    """
    from services.app_database import app_db

    name = (name or "").strip() or "Unnamed device"
    plaintext = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    scope = [str(c).strip() for c in (collection_scope or []) if str(c).strip()] or None
    if allowlist and not scope:
        raise ValueError("An allowlisted token needs at least one collection in its scope.")
    expires_at = None
    if expires_in_days is not None:
        days = int(expires_in_days)
        if not 1 <= days <= MAX_EXPIRY_DAYS:
            raise ValueError(f"expires_in_days must be between 1 and {MAX_EXPIRY_DAYS}.")
        expires_at = (datetime.utcnow() + timedelta(days=days)).isoformat()
    record = app_db.create_mcp_token(
        user_id=user_id,
        name=name,
        token_hash=_hash(plaintext),
        token_prefix=plaintext[:_PREFIX_DISPLAY_LEN],
        collection_scope=scope,
        can_write=bool(can_write),
        expires_at=expires_at,
        allowlist=bool(allowlist),
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
    return expires <= (now or datetime.utcnow())


def list_tokens(user_id: Optional[str]) -> List[Dict[str, Any]]:
    from services.app_database import app_db

    return app_db.list_mcp_tokens(user_id)


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
    try:
        app_db.touch_mcp_token(record["id"])
    except Exception as e:
        logger.warning(f"Could not update last_used_at for MCP token {record['id']}: {e}")
    return record

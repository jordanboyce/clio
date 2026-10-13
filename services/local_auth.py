"""Built-in accounts: the self-hosted answer to "who is this?".

Clio's ownership, sharing and audit features all run on a verified identity
string. For an install that has no SSO, no reverse proxy and no cloud edge in
front of it (a laptop, a home server, a small team's box) this module is that
identity source: email-and-password accounts, server-side sessions carried in
an HttpOnly cookie, and invite/reset links an admin hands to people.

Design choices, because this is the one place the app mints identities:

  * Passwords are hashed with scrypt (stdlib, memory-hard) with a per-account
    random salt; verification is constant-time, and an unknown email burns the
    same work as a known one so the response time does not reveal which
    addresses have accounts.
  * A session token is 256 random bits. Only its SHA-256 is stored, so a
    leaked database cannot be replayed as cookies. Sessions slide (14 days)
    inside an absolute cap (60 days), and are deleted on logout, password
    change and account disable.
  * Invite and reset links are single-use, expire, and are stored hashed.
  * The first administrator is claimed with a one-time setup code that is
    printed to the server log, so a fresh install that is reachable from a
    network cannot be taken over by whoever finds it first.
  * Failed logins are throttled per (address, email) and per address, in
    process. This slows guessing; scrypt cost is the other half of the defence.
  * The accounts live in their own SQLite file (``accounts.db``) under the
    data directory, whichever backend holds the rest of the app state.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlsplit

from services.sqlite_utils import sqlite_connect

logger = logging.getLogger(__name__)

SESSION_COOKIE = "clio_session"
SESSION_IDLE_DAYS = 14
SESSION_ABSOLUTE_DAYS = 60
INVITE_DAYS = 7
RESET_HOURS = 24

PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 256  # bounds scrypt work an unauthenticated caller can request

# scrypt parameters: N=2**15, r=8, p=1 is ~32 MiB and tens of milliseconds.
_SCRYPT_LOG_N = 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 128 * 1024 * 1024

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")


class AuthError(Exception):
    """A refusal with a message that is safe to show the caller."""

    def __init__(self, message: str, status: int = 400, retry_after: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retry_after = retry_after


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if not _EMAIL_RE.match(value):
        raise AuthError("Enter a valid email address.")
    return value


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


# ── Passwords ───────────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**_SCRYPT_LOG_N, r=_SCRYPT_R,
        p=_SCRYPT_P, maxmem=_SCRYPT_MAXMEM, dklen=32,
    )
    return f"scrypt${_SCRYPT_LOG_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, log_n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=2 ** int(log_n), r=int(r),
            p=int(p), maxmem=_SCRYPT_MAXMEM, dklen=len(expected),
        )
    except Exception:
        return False
    return hmac.compare_digest(digest, expected)


# A real hash to verify against when the email is unknown, so a miss costs the
# same as a wrong password.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def check_password_policy(password: str, email: str = "") -> None:
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LENGTH:
        raise AuthError(f"Use a password of at least {PASSWORD_MIN_LENGTH} characters.")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise AuthError(f"Use a password of at most {PASSWORD_MAX_LENGTH} characters.")
    lowered = password.lower()
    if email and (lowered == email.lower() or lowered == email.split("@")[0].lower()):
        raise AuthError("The password cannot be your email address.")
    if len(set(password)) < 4:
        raise AuthError("That password is too repetitive. Try a longer phrase.")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── Throttling ──────────────────────────────────────────────────────────────


class Throttle:
    """Failure-count lockout, in process.

    ``limit`` failures inside ``window`` seconds locks the key until the
    oldest counted failure ages out. A success clears the key.
    """

    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        self._failures: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> List[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < self.window]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def retry_after(self, key: str) -> int:
        """Seconds until this key may try again; 0 when it may try now."""
        now = time.time()
        with self._lock:
            recent = self._prune(key, now)
            if len(recent) < self.limit:
                return 0
            return max(1, int(recent[-self.limit] + self.window - now) + 1)

    def fail(self, key: str) -> None:
        now = time.time()
        with self._lock:
            self._prune(key, now)
            self._failures.setdefault(key, []).append(now)
            if len(self._failures) > 20000:  # bound memory under a spray
                for stale in list(self._failures)[:5000]:
                    self._failures.pop(stale, None)

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


# ── The store ───────────────────────────────────────────────────────────────


@dataclass
class Account:
    email: str
    display_name: str
    is_admin: bool
    disabled: bool
    created_at: str
    last_login_at: Optional[str]

    def public(self) -> Dict[str, Any]:
        return {
            "email": self.email,
            "display_name": self.display_name or self.email,
            "is_admin": self.is_admin,
            "disabled": self.disabled,
            "created_at": self.created_at,
            "last_login_at": self.last_login_at,
        }


class AccountStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Serialises "is this the first account?" against a concurrent setup.
        self._write_lock = threading.Lock()
        self.login_throttle = Throttle(limit=5, window=15 * 60)
        self.address_throttle = Throttle(limit=40, window=15 * 60)
        self.code_throttle = Throttle(limit=8, window=15 * 60)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite_connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    email TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL DEFAULT '',
                    password_hash TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    disabled INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    password_changed_at TEXT NOT NULL,
                    last_login_at TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    ip TEXT,
                    user_agent TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_email ON sessions(email);
                CREATE TABLE IF NOT EXISTS link_tokens (
                    token_hash TEXT PRIMARY KEY,
                    purpose TEXT NOT NULL,
                    email TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    # ── accounts ──

    @staticmethod
    def _account(row: sqlite3.Row) -> Account:
        return Account(
            email=row["email"], display_name=row["display_name"] or "",
            is_admin=bool(row["is_admin"]), disabled=bool(row["disabled"]),
            created_at=row["created_at"], last_login_at=row["last_login_at"],
        )

    def count_accounts(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]

    def get_account(self, email: str) -> Optional[Account]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE email = ?", (email.lower(),)).fetchone()
        return self._account(row) if row else None

    def list_accounts(self) -> List[Account]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM accounts ORDER BY created_at").fetchall()
        return [self._account(r) for r in rows]

    def _insert_account(
        self, conn: sqlite3.Connection, email: str, password: str, display_name: str, is_admin: bool
    ) -> None:
        now = _iso(_now())
        conn.execute(
            "INSERT INTO accounts (email, display_name, password_hash, is_admin, disabled, "
            "created_at, password_changed_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
            (email, display_name.strip()[:120], hash_password(password), 1 if is_admin else 0, now, now),
        )

    def create_first_admin(self, email: str, password: str, display_name: str = "") -> Account:
        email = normalize_email(email)
        check_password_policy(password, email)
        with self._write_lock:
            if self.count_accounts() > 0:
                raise AuthError("Setup is already complete.", status=409)
            with self._conn() as conn:
                self._insert_account(conn, email, password, display_name, True)
            self.set_meta("setup_code", "")
        logger.info("First administrator account created: %s", email)
        return self.get_account(email)  # type: ignore[return-value]

    def authenticate(self, email: str, password: str) -> Optional[Account]:
        """The account when the credentials are right and the account is usable.

        Does the same hashing work whether or not the email exists.
        """
        email = (email or "").strip().lower()
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE email = ?", (email,)).fetchone()
        stored = row["password_hash"] if row else _DUMMY_HASH
        ok = verify_password(password or "", stored)
        if not row or not ok or row["disabled"]:
            return None
        return self._account(row)

    def set_password(self, email: str, new_password: str, keep_session: Optional[str] = None) -> None:
        email = email.lower()
        check_password_policy(new_password, email)
        now = _iso(_now())
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE accounts SET password_hash = ?, password_changed_at = ? WHERE email = ?",
                (hash_password(new_password), now, email),
            )
            if cur.rowcount == 0:
                raise AuthError("No such account.", status=404)
            if keep_session:
                conn.execute(
                    "DELETE FROM sessions WHERE email = ? AND token_hash != ?",
                    (email, _hash_token(keep_session)),
                )
            else:
                conn.execute("DELETE FROM sessions WHERE email = ?", (email,))

    def _admin_count(self, conn: sqlite3.Connection, excluding: Optional[str] = None) -> int:
        sql = "SELECT COUNT(*) FROM accounts WHERE is_admin = 1 AND disabled = 0"
        args: tuple = ()
        if excluding:
            sql += " AND email != ?"
            args = (excluding.lower(),)
        return conn.execute(sql, args).fetchone()[0]

    def update_account(
        self, email: str, *, is_admin: Optional[bool] = None, disabled: Optional[bool] = None
    ) -> Account:
        email = email.lower()
        with self._write_lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE email = ?", (email,)).fetchone()
            if not row:
                raise AuthError("No such account.", status=404)
            new_admin = bool(row["is_admin"]) if is_admin is None else is_admin
            new_disabled = bool(row["disabled"]) if disabled is None else disabled
            was_active_admin = bool(row["is_admin"]) and not row["disabled"]
            if was_active_admin and (not new_admin or new_disabled) and self._admin_count(conn, email) == 0:
                raise AuthError("This is the last active administrator. Make someone else an admin first.", status=409)
            conn.execute(
                "UPDATE accounts SET is_admin = ?, disabled = ? WHERE email = ?",
                (1 if new_admin else 0, 1 if new_disabled else 0, email),
            )
            if new_disabled:
                conn.execute("DELETE FROM sessions WHERE email = ?", (email,))
        return self.get_account(email)  # type: ignore[return-value]

    def delete_account(self, email: str) -> None:
        email = email.lower()
        with self._write_lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE email = ?", (email,)).fetchone()
            if not row:
                raise AuthError("No such account.", status=404)
            if row["is_admin"] and not row["disabled"] and self._admin_count(conn, email) == 0:
                raise AuthError("This is the last active administrator.", status=409)
            conn.execute("DELETE FROM sessions WHERE email = ?", (email,))
            conn.execute("DELETE FROM link_tokens WHERE email = ?", (email,))
            conn.execute("DELETE FROM accounts WHERE email = ?", (email,))

    # ── sessions ──

    def create_session(self, email: str, ip: str = "", user_agent: str = "") -> str:
        token = secrets.token_urlsafe(32)
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sessions (token_hash, email, created_at, expires_at, last_seen_at, ip, user_agent) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_hash_token(token), email.lower(), _iso(now),
                 _iso(now + timedelta(days=SESSION_IDLE_DAYS)), _iso(now), ip[:64], user_agent[:200]),
            )
            # Every way in (sign-in, setup, an accepted invite) ends here, so
            # this is where "last sign-in" is recorded.
            conn.execute(
                "UPDATE accounts SET last_login_at = ? WHERE email = ?", (_iso(now), email.lower())
            )
            # Opportunistic cleanup keeps the table from growing without a job.
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_iso(now),))
        return token

    def lookup_session(self, token: str) -> Optional[Account]:
        if not token:
            return None
        token_hash = _hash_token(token)
        now = _now()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT s.created_at AS s_created, s.expires_at AS s_expires, a.* "
                "FROM sessions s JOIN accounts a ON a.email = s.email WHERE s.token_hash = ?",
                (token_hash,),
            ).fetchone()
            if not row:
                return None
            expired = _parse(row["s_expires"]) <= now
            too_old = _parse(row["s_created"]) + timedelta(days=SESSION_ABSOLUTE_DAYS) <= now
            if expired or too_old or row["disabled"]:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                return None
            # Slide the idle window at most hourly: avoids a write per request.
            remaining = _parse(row["s_expires"]) - now
            if remaining < timedelta(days=SESSION_IDLE_DAYS) - timedelta(hours=1):
                conn.execute(
                    "UPDATE sessions SET expires_at = ?, last_seen_at = ? WHERE token_hash = ?",
                    (_iso(now + timedelta(days=SESSION_IDLE_DAYS)), _iso(now), token_hash),
                )
        return self._account(row)

    def delete_session(self, token: str) -> None:
        if not token:
            return
        with self._conn() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))

    # ── invite / reset links ──

    def issue_link(
        self, purpose: str, email: str, created_by: Optional[str], is_admin: bool = False
    ) -> str:
        if purpose not in ("invite", "reset"):
            raise ValueError(purpose)
        email = normalize_email(email)
        existing = self.get_account(email)
        if purpose == "invite" and existing:
            raise AuthError("That address already has an account.", status=409)
        if purpose == "reset" and not existing:
            raise AuthError("No such account.", status=404)
        token = secrets.token_urlsafe(32)
        now = _now()
        ttl = timedelta(days=INVITE_DAYS) if purpose == "invite" else timedelta(hours=RESET_HOURS)
        with self._conn() as conn:
            # One live link per person and purpose: a new one retires the old.
            conn.execute(
                "DELETE FROM link_tokens WHERE email = ? AND purpose = ? AND used_at IS NULL",
                (email, purpose),
            )
            conn.execute(
                "INSERT INTO link_tokens (token_hash, purpose, email, is_admin, created_by, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_hash_token(token), purpose, email, 1 if is_admin else 0, created_by,
                 _iso(now), _iso(now + ttl)),
            )
        return token

    def peek_link(self, token: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM link_tokens WHERE token_hash = ?", (_hash_token(token or ""),)
            ).fetchone()
        if not row or row["used_at"] or _parse(row["expires_at"]) <= _now():
            return None
        return {"purpose": row["purpose"], "email": row["email"], "is_admin": bool(row["is_admin"]),
                "expires_at": row["expires_at"]}

    def redeem_link(self, token: str, password: str, display_name: str = "") -> Account:
        token_hash = _hash_token(token or "")
        with self._write_lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM link_tokens WHERE token_hash = ?", (token_hash,)).fetchone()
            if not row or row["used_at"] or _parse(row["expires_at"]) <= _now():
                raise AuthError("This link is invalid or has expired. Ask an admin for a new one.", status=400)
            email = row["email"]
            check_password_policy(password, email)
            now = _iso(_now())
            if row["purpose"] == "invite":
                if conn.execute("SELECT 1 FROM accounts WHERE email = ?", (email,)).fetchone():
                    raise AuthError("That address already has an account.", status=409)
                self._insert_account(conn, email, password, display_name, bool(row["is_admin"]))
            else:
                conn.execute(
                    "UPDATE accounts SET password_hash = ?, password_changed_at = ? WHERE email = ?",
                    (hash_password(password), now, email),
                )
                conn.execute("DELETE FROM sessions WHERE email = ?", (email,))
            conn.execute("UPDATE link_tokens SET used_at = ? WHERE token_hash = ?", (now, token_hash))
        return self.get_account(email)  # type: ignore[return-value]

    # ── setup code ──

    def get_meta(self, key: str) -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else ""

    def set_meta(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def setup_code(self) -> str:
        """The code that claims the first admin account ('' once one exists)."""
        if self.count_accounts() > 0:
            return ""
        from config import settings

        configured = (getattr(settings, "setup_code", "") or "").strip()
        if configured:
            return configured
        code = self.get_meta("setup_code")
        if not code:
            code = "-".join(secrets.token_hex(2) for _ in range(3))
            self.set_meta("setup_code", code)
        return code

    def check_setup_code(self, presented: str) -> bool:
        expected = self.setup_code()
        return bool(expected) and hmac.compare_digest(
            (presented or "").strip().lower().encode(), expected.lower().encode()
        )


# ── Module-level singleton, keyed on the data directory ─────────────────────

_store: Optional[AccountStore] = None
_store_path: Optional[Path] = None
_store_lock = threading.Lock()


def get_store() -> AccountStore:
    global _store, _store_path
    from config import settings

    path = Path(settings.data_dir) / "accounts.db"
    with _store_lock:
        if _store is None or _store_path != path:
            _store, _store_path = AccountStore(path), path
        return _store


def announce_setup() -> None:
    """Print the first-run setup code so whoever runs the server can claim it."""
    store = get_store()
    code = store.setup_code()
    if not code:
        return
    bar = "=" * 62
    logger.warning(
        "\n%s\n  Clio is waiting for its first administrator.\n"
        "  Open the app in a browser and create the account with this code:\n\n"
        "      %s\n\n  (Shown again on every start until the account exists.)\n%s",
        bar, code, bar,
    )


# ── Cookies, CSRF, and the identity-verifier face ───────────────────────────


def session_token_from_headers(headers: Mapping[str, str]) -> str:
    cookie = headers.get("cookie") or headers.get("Cookie") or ""
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name == SESSION_COOKIE:
            return value
    return ""


def csrf_ok(method: str, headers: Mapping[str, str]) -> bool:
    """Cookie-authenticated writes must come from this site.

    SameSite=Lax already withholds the cookie from cross-site POSTs; this is
    the second layer. A browser always sends Origin on a cross-origin write,
    so a mismatch is refused; a client that sends neither header is not a
    browser acting for an attacker, and is allowed.
    """
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return True
    if (headers.get("sec-fetch-site") or "").lower() == "cross-site":
        return False
    origin = headers.get("origin")
    if origin is None:
        return True
    if origin in ("", "null"):
        return False
    host = headers.get("host", "")
    return urlsplit(origin).netloc.lower() == host.lower()


class LocalSessionIdentity:
    """The identity-verifier face of the account store (see services/identity.py)."""

    via = "local_session"

    def has_candidate(self, headers: Mapping[str, str]) -> bool:
        return bool(session_token_from_headers(headers))

    def verify_request(self, headers: Mapping[str, str], client_ip: Optional[str]) -> Optional[str]:
        account = get_store().lookup_session(session_token_from_headers(headers))
        return account.email if account else None


def is_local_admin(identity: Optional[str]) -> bool:
    if not identity:
        return False
    account = get_store().get_account(identity)
    return bool(account and account.is_admin and not account.disabled)

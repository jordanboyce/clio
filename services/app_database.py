"""Application database for persistent configuration and state.

Stores:
- Configuration settings (overrides .env)
- Re-indexing progress/status
- Application state
- User accounts and collection sharing
"""

import sqlite3
import json
import logging
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

from config import settings
from services.db_backend import DatabaseBackend
from services.sqlite_utils import sqlite_connect

logger = logging.getLogger(__name__)


def _get_document_count_from_metadata(collection_id: str, data_dir: Path = None) -> int:
    """Get the actual document count from the collection's metadata.db.

    This is the source of truth for document counts, avoiding sync issues
    between app.db and the actual indexed data.

    Args:
        collection_id: Collection ID
        data_dir: Base data directory

    Returns:
        Number of unique documents in the collection's index
    """
    if data_dir is None:
        data_dir = settings.data_dir
    metadata_db_path = data_dir / "collections" / collection_id / "indexes" / "metadata.db"

    if not metadata_db_path.exists():
        return 0

    try:
        with sqlite_connect(metadata_db_path) as conn:
            try:
                # documents table is authoritative: counts zero-chunk docs
                # (CSV/XLSX live in the structured store) and avoids a
                # COUNT(DISTINCT) scan over millions of chunk rows.
                cursor = conn.execute("SELECT COUNT(*) FROM documents")
            except sqlite3.OperationalError:
                # Legacy metadata.db without a documents table
                cursor = conn.execute("SELECT COUNT(DISTINCT document_id) FROM chunks")
            return cursor.fetchone()[0]
    except Exception as e:
        logger.warning(f"Could not read metadata.db for collection {collection_id}: {e}")
        return 0


def _get_storage_bytes_from_metadata(collection_id: str, data_dir: Path = None) -> int:
    """Bytes of source files in a collection, read straight from metadata.db.

    Same shape as the document count above: a single aggregate over the
    documents table, without loading the collection's vector index, so the
    collections list can show usage against the storage cap for free.
    """
    if data_dir is None:
        data_dir = settings.data_dir
    metadata_db_path = data_dir / "collections" / collection_id / "indexes" / "metadata.db"
    if not metadata_db_path.exists():
        return 0
    try:
        with sqlite_connect(metadata_db_path) as conn:
            try:
                row = conn.execute("SELECT COALESCE(SUM(file_size), 0) FROM documents").fetchone()
            except sqlite3.OperationalError:
                return 0  # pre-v3.4 metadata.db; migrates on first open
            return int(row[0] or 0)
    except Exception as e:
        logger.warning(f"Could not read storage for collection {collection_id}: {e}")
        return 0


class SQLiteBackend(DatabaseBackend):
    """Manages application-level persistent data in SQLite."""

    def __init__(self, db_path: Path = Path("data/app.db")):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        with sqlite_connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS reindex_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    total_documents INTEGER DEFAULT 0,
                    processed_documents INTEGER DEFAULT 0,
                    current_file TEXT,
                    error TEXT,
                    config_snapshot TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_preferences (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    selected_providers TEXT,
                    rerank_enabled INTEGER DEFAULT 1,
                    synthesize_enabled INTEGER DEFAULT 1,
                    default_provider TEXT,
                    updated_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS search_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    top_k INTEGER,
                    results_count INTEGER,
                    ai_provider TEXT,
                    ai_used INTEGER DEFAULT 0,
                    results_json TEXT,
                    execution_time_ms INTEGER
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_search_timestamp
                ON search_history(timestamp DESC)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_search_query
                ON search_history(query)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS collections (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    color TEXT DEFAULT '#3b82f6',
                    chunk_size INTEGER DEFAULT 500,
                    chunk_overlap INTEGER DEFAULT 50,
                    embedding_model TEXT DEFAULT 'all-MiniLM-L6-v2',
                    owner_id TEXT DEFAULT 'default',
                    guide TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS collection_documents (
                    collection_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    added_at TEXT NOT NULL,
                    PRIMARY KEY (collection_id, document_id),
                    FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_collection_documents
                ON collection_documents(collection_id)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS upload_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total_files INTEGER DEFAULT 0,
                    processed_files INTEGER DEFAULT 0,
                    current_file TEXT,
                    error TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    result_summary TEXT,
                    phase TEXT,
                    phase_progress INTEGER DEFAULT 0,
                    phase_detail TEXT,
                    chunks_processed INTEGER DEFAULT 0,
                    chunks_total INTEGER DEFAULT 0,
                    job_type TEXT DEFAULT 'upload'
                )
            """)

            # ── v4: Users table ──────────────────────────────
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    display_name TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                )
            """)

            # ── v4: Collection shares ────────────────────────
            conn.execute("""
                CREATE TABLE IF NOT EXISTS collection_shares (
                    id TEXT PRIMARY KEY,
                    collection_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    permission TEXT NOT NULL DEFAULT 'read',
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    is_active INTEGER DEFAULT 1,
                    FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS collection_share_users (
                    share_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    accepted_at TEXT NOT NULL,
                    PRIMARY KEY (share_id, user_id),
                    FOREIGN KEY (share_id) REFERENCES collection_shares(id) ON DELETE CASCADE
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_shares_collection
                ON collection_shares(collection_id)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_share_users_user
                ON collection_share_users(user_id)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_collections_owner
                ON collections(owner_id)
            """)

            # ── v5: Expertise Library ─────────────────────────
            conn.execute("""
                CREATE TABLE IF NOT EXISTS expertise_packs (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    description TEXT,
                    body        TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS collection_expertise (
                    collection_id TEXT NOT NULL,
                    pack_id       TEXT NOT NULL,
                    attached_at   TEXT NOT NULL,
                    PRIMARY KEY (collection_id, pack_id),
                    FOREIGN KEY (pack_id) REFERENCES expertise_packs(id) ON DELETE CASCADE
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_collection_expertise_collection
                ON collection_expertise(collection_id)
            """)

            # ── v4.5: Chat usage accounting ──────────────────
            # One row per chat turn. This is what makes the shared team key
            # safe with many users: the daily budget check counts against it,
            # and the admin usage view reads from it. user_id NULL = an
            # anonymous password caller (they share one budget row).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    user_id TEXT,
                    collection_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    tool_calls INTEGER NOT NULL DEFAULT 0,
                    cache_hit INTEGER NOT NULL DEFAULT 0,
                    duration_ms INTEGER
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_usage_user_ts
                ON chat_usage(user_id, timestamp DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_usage_ts
                ON chat_usage(timestamp DESC)
            """)

            # ── Personal MCP access tokens ────────────────────
            # Anyone who can already reach the app mints a bearer credential
            # for headless MCP clients. Only the sha256 hash is stored; the
            # plaintext is returned once, at creation. user_id NULL = minted
            # by an anonymous (password-authenticated) caller — it verifies
            # to the same "authenticated but anonymous" identity password
            # auth already produces, so team-tier access still works.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mcp_tokens (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    name TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    token_prefix TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    revoked_at TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_mcp_tokens_hash
                ON mcp_tokens(token_hash)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_mcp_tokens_user
                ON mcp_tokens(user_id)
            """)

            # ── Content governance ────────────────────────────
            # Append-only accountability trail (services/audit.py): who
            # uploaded, deleted, shared, minted a token, accepted the policy,
            # and what an admin did about a flagged document. Kept far longer
            # than search_history (AUDIT_RETENTION_DAYS).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    actor TEXT,
                    action TEXT NOT NULL,
                    collection_id TEXT,
                    document_id TEXT,
                    target TEXT,
                    detail TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(timestamp DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_events(action, timestamp DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor, timestamp DESC)")

            # Acceptable-use acknowledgements, one row per identity per
            # policy version — bumping AUP_VERSION re-prompts everyone.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS aup_acknowledgements (
                    user_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    accepted_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, version)
                )
            """)

            # Admin hash blocklist: a removed file's sha256, so the same
            # bytes cannot be re-uploaded (to any collection) afterwards.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS blocked_hashes (
                    content_hash TEXT PRIMARY KEY,
                    blocked_by TEXT,
                    blocked_at TEXT NOT NULL,
                    reason TEXT,
                    filename TEXT
                )
            """)

            # ── Migrations for existing databases ────────────
            # Add owner_id to collections if missing
            try:
                conn.execute("ALTER TABLE collections ADD COLUMN owner_id TEXT DEFAULT 'default'")
            except sqlite3.OperationalError:
                pass

            # Governance: collection sensitivity label (public | internal |
            # confidential | restricted) and the optional collection scope
            # on personal MCP tokens (JSON list of ids a token may reach in
            # restricted collections).
            try:
                conn.execute("ALTER TABLE collections ADD COLUMN sensitivity TEXT DEFAULT 'internal'")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE mcp_tokens ADD COLUMN collection_scope TEXT")
            except sqlite3.OperationalError:
                pass
            # Per-token write grant for the write_document MCP tool. Existing
            # tokens stay read-only (DEFAULT 0) — writes are opt-in at mint time.
            try:
                conn.execute("ALTER TABLE mcp_tokens ADD COLUMN can_write INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            # Token hygiene: optional expiry, allowlist scoping (the scope is
            # the *only* set of collections the token may see, not merely a
            # restricted-collection grant) and a use counter so the Settings
            # table can show which agents are actually active. Existing
            # tokens keep their behaviour: no expiry, grant semantics.
            for col in ("expires_at TEXT",
                        "allowlist INTEGER NOT NULL DEFAULT 0",
                        "use_count INTEGER NOT NULL DEFAULT 0"):
                try:
                    conn.execute(f"ALTER TABLE mcp_tokens ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass

            # Add MCP display name/description (optional aliases for MCP output)
            for col in ["mcp_display_name TEXT", "mcp_display_description TEXT"]:
                try:
                    conn.execute(f"ALTER TABLE collections ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass

            # v4.3: per-collection guide (markdown, surfaced to calling LLM)
            try:
                conn.execute("ALTER TABLE collections ADD COLUMN guide TEXT")
            except sqlite3.OperationalError:
                pass

            # v4.4: the address a share was emailed to, so the owner's share
            # list shows who each link was sent to.
            try:
                conn.execute("ALTER TABLE collection_shares ADD COLUMN invited_email TEXT")
            except sqlite3.OperationalError:
                pass

            # v4.5: who searched, and where. Before this, search_history was
            # a pooled log with no identity — useless as an audit trail and a
            # retention liability (it stores result snippets).
            for col in ["user_id TEXT", "collection_id TEXT"]:
                try:
                    conn.execute(f"ALTER TABLE search_history ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass

            # v4.6: a collection reviewed and released for everyone to read.
            # Publishing keeps the owner — it is the one way a collection can
            # be reachable by the whole deployment while still having exactly
            # one person accountable for how it is built. Everyone else
            # resolves to 'read' (see sharing_service.check_collection_access).
            try:
                conn.execute("ALTER TABLE collections ADD COLUMN published INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass

            # Add phase columns to upload_jobs if missing
            for col, default in [
                ("phase", "TEXT"),
                ("phase_progress", "INTEGER DEFAULT 0"),
                ("phase_detail", "TEXT"),
                ("chunks_processed", "INTEGER DEFAULT 0"),
                ("chunks_total", "INTEGER DEFAULT 0"),
                ("job_type", "TEXT DEFAULT 'upload'"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE upload_jobs ADD COLUMN {col} {default}")
                except sqlite3.OperationalError:
                    pass

            # Ensure default collection exists
            cursor = conn.execute("SELECT id FROM collections WHERE id = 'default'")
            if not cursor.fetchone():
                timestamp = datetime.utcnow().isoformat()
                conn.execute(
                    """
                    INSERT INTO collections (id, name, description, color, embedding_model, owner_id, created_at, updated_at)
                    VALUES ('default', 'Default', 'Default document collection', '#3b82f6', ?, 'default', ?, ?)
                    """,
                    (settings.embedding_model, timestamp, timestamp)
                )
            else:
                conn.execute(
                    """
                    UPDATE collections SET embedding_model = ?
                    WHERE id = 'default' AND embedding_model = 'all-MiniLM-L6-v2'
                    """,
                    (settings.embedding_model,)
                )

            # Ensure default user exists
            cursor = conn.execute("SELECT id FROM users WHERE id = 'default'")
            if not cursor.fetchone():
                timestamp = datetime.utcnow().isoformat()
                conn.execute(
                    "INSERT INTO users (id, display_name, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?)",
                    ("default", "Default User", timestamp, timestamp)
                )

            conn.commit()
            logger.info(f"Application database initialized at {self.db_path}")

    # ── Configuration ────────────────────────────────────────

    def get_config(self, key: str, default: Any = None) -> Any:
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute("SELECT value FROM config WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row:
                try:
                    return json.loads(row[0])
                except json.JSONDecodeError:
                    return row[0]
            return default

    def set_config(self, key: str, value: Any):
        value_str = json.dumps(value) if not isinstance(value, str) else value
        timestamp = datetime.utcnow().isoformat()
        with sqlite_connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value_str, timestamp)
            )
            conn.commit()
            logger.info(f"Config updated: {key} = {value}")

    def get_all_config(self) -> Dict[str, Any]:
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute("SELECT key, value FROM config")
            config = {}
            for key, value in cursor.fetchall():
                try:
                    config[key] = json.loads(value)
                except json.JSONDecodeError:
                    config[key] = value
            return config

    def delete_config(self, key: str):
        with sqlite_connect(self.db_path) as conn:
            conn.execute("DELETE FROM config WHERE key = ?", (key,))
            conn.commit()

    # ── Users ────────────────────────────────────────────────

    def upsert_user(self, user_id: str, display_name: Optional[str] = None) -> Dict[str, Any]:
        timestamp = datetime.utcnow().isoformat()
        with sqlite_connect(self.db_path) as conn:
            existing = conn.execute("SELECT id, display_name, first_seen_at FROM users WHERE id = ?", (user_id,)).fetchone()
            if existing:
                if display_name:
                    conn.execute("UPDATE users SET display_name = ?, last_seen_at = ? WHERE id = ?", (display_name, timestamp, user_id))
                else:
                    conn.execute("UPDATE users SET last_seen_at = ? WHERE id = ?", (timestamp, user_id))
                conn.commit()
                return {"id": user_id, "display_name": display_name or existing[1], "first_seen_at": existing[2], "last_seen_at": timestamp}
            else:
                dname = display_name or user_id
                conn.execute(
                    "INSERT INTO users (id, display_name, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?)",
                    (user_id, dname, timestamp, timestamp)
                )
                conn.commit()
                return {"id": user_id, "display_name": dname, "first_seen_at": timestamp, "last_seen_at": timestamp}

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return dict(row) if row else None

    def get_all_users(self) -> List[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY first_seen_at ASC").fetchall()]

    # ── Collections ──────────────────────────────────────────

    def create_collection(
        self,
        name: str,
        description: str = "",
        color: str = "#3b82f6",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        embedding_model: str = None,
        owner_id: str = "default",
        mcp_display_name: str | None = None,
        mcp_display_description: str | None = None,
    ) -> str:
        if embedding_model is None:
            embedding_model = settings.embedding_model
        collection_id = str(uuid.uuid4())[:8]
        timestamp = datetime.utcnow().isoformat()
        with sqlite_connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO collections
                (id, name, description, color, chunk_size, chunk_overlap, embedding_model, owner_id, mcp_display_name, mcp_display_description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (collection_id, name, description, color, chunk_size, chunk_overlap, embedding_model, owner_id, mcp_display_name, mcp_display_description, timestamp, timestamp)
            )
            conn.commit()
            logger.info(f"Created collection: {name} ({collection_id}) owner={owner_id}")
            return collection_id

    def get_collection(self, collection_id: str) -> Optional[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
            if row:
                collection = dict(row)
                collection['published'] = bool(collection.get('published'))
                collection['document_count'] = _get_document_count_from_metadata(collection_id)
                collection['storage_bytes'] = _get_storage_bytes_from_metadata(collection_id)
                return collection
            return None

    def get_all_collections(self, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if owner_id:
                cursor = conn.execute(
                    "SELECT * FROM collections WHERE owner_id = ? ORDER BY created_at ASC",
                    (owner_id,)
                )
            else:
                cursor = conn.execute("SELECT * FROM collections ORDER BY created_at ASC")
            collections = []
            for row in cursor.fetchall():
                collection = dict(row)
                collection['published'] = bool(collection.get('published'))
                collection['document_count'] = _get_document_count_from_metadata(collection['id'])
                collection['storage_bytes'] = _get_storage_bytes_from_metadata(collection['id'])
                collections.append(collection)
            return collections

    def update_collection(
        self,
        collection_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        color: Optional[str] = None,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        embedding_model: Optional[str] = None,
        mcp_display_name: Optional[str] = None,
        mcp_display_description: Optional[str] = None,
        guide: Optional[str] = None,
        sensitivity: Optional[str] = None,
        published: Optional[bool] = None,
    ):
        updates = []
        params = []
        for field, value in [("name", name), ("description", description), ("color", color),
                             ("chunk_size", chunk_size), ("chunk_overlap", chunk_overlap),
                             ("embedding_model", embedding_model),
                             ("mcp_display_name", mcp_display_name),
                             ("mcp_display_description", mcp_display_description),
                             ("guide", guide), ("sensitivity", sensitivity),
                             ("published", None if published is None else int(published))]:
            if value is not None:
                updates.append(f"{field} = ?")
                params.append(value)
        if not updates:
            return
        updates.append("updated_at = ?")
        params.append(datetime.utcnow().isoformat())
        params.append(collection_id)
        with sqlite_connect(self.db_path) as conn:
            conn.execute(f"UPDATE collections SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()

    def delete_collection(self, collection_id: str) -> bool:
        if collection_id == "default":
            return False
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
            conn.commit()
            return cursor.rowcount > 0

    # ── Collection Documents ─────────────────────────────────

    def add_document_to_collection(self, collection_id: str, document_id: str):
        timestamp = datetime.utcnow().isoformat()
        with sqlite_connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO collection_documents (collection_id, document_id, added_at) VALUES (?, ?, ?)",
                (collection_id, document_id, timestamp)
            )
            conn.commit()

    def remove_document_from_collection(self, collection_id: str, document_id: str):
        with sqlite_connect(self.db_path) as conn:
            conn.execute("DELETE FROM collection_documents WHERE collection_id = ? AND document_id = ?", (collection_id, document_id))
            conn.commit()

    def get_collection_documents(self, collection_id: str) -> List[str]:
        with sqlite_connect(self.db_path) as conn:
            return [r[0] for r in conn.execute(
                "SELECT document_id FROM collection_documents WHERE collection_id = ?", (collection_id,)
            ).fetchall()]

    def get_document_collections(self, document_id: str) -> List[str]:
        with sqlite_connect(self.db_path) as conn:
            return [r[0] for r in conn.execute(
                "SELECT collection_id FROM collection_documents WHERE document_id = ?", (document_id,)
            ).fetchall()]

    # ── Collection Sharing ───────────────────────────────────

    def create_share(self, collection_id: str, owner_id: str, permission: str = "read", expires_at: Optional[str] = None, invited_email: Optional[str] = None) -> str:
        share_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat()
        with sqlite_connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO collection_shares (id, collection_id, owner_id, permission, created_at, expires_at, is_active, invited_email)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
                (share_id, collection_id, owner_id, permission, timestamp, expires_at,
                 (invited_email or "").strip().lower() or None)
            )
            conn.commit()
        return share_id

    def list_share_contacts(self) -> Dict[str, int]:
        """Every address with a live tie to the app, and how many shares.

        Counts a share once whether the address was invited to it, accepted
        it, or both.
        """
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT email, COUNT(DISTINCT share_id) AS n FROM (
                    SELECT cs.id AS share_id, LOWER(TRIM(cs.invited_email)) AS email
                      FROM collection_shares cs
                     WHERE cs.is_active = 1
                       AND cs.invited_email IS NOT NULL
                       AND TRIM(cs.invited_email) != ''
                    UNION
                    SELECT cs.id AS share_id, LOWER(TRIM(csu.user_id)) AS email
                      FROM collection_shares cs
                      JOIN collection_share_users csu ON csu.share_id = cs.id
                     WHERE cs.is_active = 1
                )
                GROUP BY email
                """
            ).fetchall()
            return {r[0]: int(r[1]) for r in rows if r[0]}

    def count_active_shares_for_email(self, email: str, exclude_share_id: Optional[str] = None) -> int:
        """How many live shares still connect this address to the app.

        Counts a share if the address was invited to it, or if someone signed
        in with that address and accepted it. Used before withdrawing an edge
        admission, so we only revoke when the person's last tie is gone.
        """
        target = (email or "").strip().lower()
        if not target:
            return 0
        with sqlite_connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT cs.id) FROM collection_shares cs
                LEFT JOIN collection_share_users csu ON csu.share_id = cs.id
                WHERE cs.is_active = 1
                  AND cs.id != ?
                  AND (LOWER(cs.invited_email) = ? OR LOWER(csu.user_id) = ?)
                """,
                (exclude_share_id or "", target, target)
            ).fetchone()
            return int(row[0]) if row else 0

    def get_share(self, share_id: str) -> Optional[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM collection_shares WHERE id = ?", (share_id,)).fetchone()
            if row:
                share = dict(row)
                # Check expiry
                if share.get("expires_at"):
                    try:
                        if datetime.fromisoformat(share["expires_at"]) < datetime.utcnow():
                            share["is_active"] = 0
                    except ValueError:
                        pass
                return share
            return None

    def get_shares_for_collection(self, collection_id: str) -> List[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM collection_shares WHERE collection_id = ? AND is_active = 1 ORDER BY created_at DESC",
                (collection_id,)
            ).fetchall()
            shares = []
            for row in rows:
                share = dict(row)
                # Get accepted users
                users = conn.execute(
                    "SELECT user_id, accepted_at FROM collection_share_users WHERE share_id = ?",
                    (share["id"],)
                ).fetchall()
                share["accepted_users"] = [{"user_id": u[0], "accepted_at": u[1]} for u in users]
                shares.append(share)
            return shares

    def accept_share(self, share_id: str, user_id: str):
        timestamp = datetime.utcnow().isoformat()
        with sqlite_connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO collection_share_users (share_id, user_id, accepted_at) VALUES (?, ?, ?)",
                (share_id, user_id, timestamp)
            )
            conn.commit()

    def get_shared_collections(self, user_id: str) -> List[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT c.*, cs.permission, cs.id as share_id
                FROM collections c
                JOIN collection_shares cs ON cs.collection_id = c.id
                JOIN collection_share_users csu ON csu.share_id = cs.id
                WHERE csu.user_id = ? AND cs.is_active = 1
                ORDER BY c.created_at ASC
                """,
                (user_id,)
            ).fetchall()
            results = []
            for row in rows:
                coll = dict(row)
                coll['published'] = bool(coll.get('published'))
                coll['document_count'] = _get_document_count_from_metadata(coll['id'])
                coll['storage_bytes'] = _get_storage_bytes_from_metadata(coll['id'])
                coll['shared'] = True
                results.append(coll)
            return results

    def revoke_share(self, share_id: str):
        with sqlite_connect(self.db_path) as conn:
            conn.execute("UPDATE collection_shares SET is_active = 0 WHERE id = ?", (share_id,))
            conn.commit()

    def check_share_access(self, collection_id: str, user_id: str) -> Optional[str]:
        with sqlite_connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT cs.permission FROM collection_shares cs
                JOIN collection_share_users csu ON csu.share_id = cs.id
                WHERE cs.collection_id = ? AND csu.user_id = ? AND cs.is_active = 1
                ORDER BY CASE cs.permission WHEN 'readwrite' THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (collection_id, user_id)
            ).fetchone()
            if row:
                # Check expiry
                share = conn.execute(
                    """SELECT cs.expires_at FROM collection_shares cs
                       JOIN collection_share_users csu ON csu.share_id = cs.id
                       WHERE cs.collection_id = ? AND csu.user_id = ? AND cs.is_active = 1
                       ORDER BY CASE cs.permission WHEN 'readwrite' THEN 0 ELSE 1 END LIMIT 1""",
                    (collection_id, user_id)
                ).fetchone()
                if share and share[0]:
                    try:
                        if datetime.fromisoformat(share[0]) < datetime.utcnow():
                            return None
                    except ValueError:
                        pass
                return row[0]
            return None

    # ── Upload Jobs ──────────────────────────────────────────

    def create_upload_job(self, collection_id: str, total_files: int, job_type: str = "upload") -> int:
        timestamp = datetime.utcnow().isoformat()
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO upload_jobs (collection_id, status, total_files, started_at, job_type) VALUES (?, ?, ?, ?, ?)",
                (collection_id, "pending", total_files, timestamp, job_type)
            )
            conn.commit()
            return cursor.lastrowid

    # Fields a caller may deliberately clear by passing None. Everything else
    # treats None as "not supplied", so a partial progress update doesn't wipe
    # the columns it says nothing about. Without this, the completion write
    # (current_file=None) was dropped and a finished job kept showing the last
    # file it touched.
    NULLABLE_UPLOAD_JOB_FIELDS = ("current_file", "phase_detail", "error")

    def update_upload_job(self, job_id: int, **kwargs):
        updates = []
        params = []
        field_map = {
            "status": "status", "processed_files": "processed_files", "current_file": "current_file",
            "error": "error", "result_summary": "result_summary", "phase": "phase",
            "phase_progress": "phase_progress", "phase_detail": "phase_detail",
            "chunks_processed": "chunks_processed", "chunks_total": "chunks_total",
        }
        for kwarg, col in field_map.items():
            if kwarg not in kwargs:
                continue
            if kwargs[kwarg] is None and kwarg not in self.NULLABLE_UPLOAD_JOB_FIELDS:
                continue
            updates.append(f"{col} = ?")
            params.append(kwargs[kwarg])

        status = kwargs.get("status")
        if status in ("completed", "failed", "cancelled"):
            updates.append("completed_at = ?")
            params.append(datetime.utcnow().isoformat())

        if not updates:
            return
        params.append(job_id)
        with sqlite_connect(self.db_path) as conn:
            conn.execute(f"UPDATE upload_jobs SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()

    def get_upload_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT id, collection_id, status, total_files, processed_files,
                          current_file, error, started_at, completed_at, result_summary,
                          phase, phase_progress, phase_detail, chunks_processed, chunks_total, job_type
                   FROM upload_jobs WHERE id = ?""", (job_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_active_upload_job(self, collection_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if collection_id:
                row = conn.execute(
                    """SELECT id, collection_id, status, total_files, processed_files,
                              current_file, error, started_at, completed_at, result_summary,
                              phase, phase_progress, phase_detail, chunks_processed, chunks_total, job_type
                       FROM upload_jobs WHERE status IN ('pending', 'running') AND collection_id = ?
                       ORDER BY id DESC LIMIT 1""", (collection_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT id, collection_id, status, total_files, processed_files,
                              current_file, error, started_at, completed_at, result_summary,
                              phase, phase_progress, phase_detail, chunks_processed, chunks_total, job_type
                       FROM upload_jobs WHERE status IN ('pending', 'running')
                       ORDER BY id DESC LIMIT 1"""
                ).fetchone()
            return dict(row) if row else None

    def get_all_active_upload_jobs(self) -> List[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(
                """SELECT id, collection_id, status, total_files, processed_files,
                          current_file, error, started_at, completed_at, result_summary,
                          phase, phase_progress, phase_detail, chunks_processed, chunks_total, job_type
                   FROM upload_jobs WHERE status IN ('pending', 'running') ORDER BY id DESC"""
            ).fetchall()]

    def get_recent_upload_jobs(self, limit: int = 20,
                               collection_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Recent jobs in every state, newest first.

        The drawer needs finished jobs too: a job that ended with failed
        files is the only place a person learns which sources did not make
        it in, and that has to survive a page refresh.
        """
        limit = max(1, min(int(limit or 20), 200))
        where = "WHERE collection_id = ?" if collection_id else ""
        params = (collection_id, limit) if collection_id else (limit,)
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(
                f"""SELECT id, collection_id, status, total_files, processed_files,
                           current_file, error, started_at, completed_at, result_summary,
                           phase, phase_progress, phase_detail, chunks_processed,
                           chunks_total, job_type
                    FROM upload_jobs {where} ORDER BY id DESC LIMIT ?""", params
            ).fetchall()]

    def fail_interrupted_jobs(self, reason: str) -> Dict[str, int]:
        """Close out jobs left mid-flight by a process exit.

        Upload jobs run in threads and reindex jobs in asyncio tasks, so
        neither survives a restart — but their rows do. Left alone, those
        rows make the UI show a job that will never move, and the
        per-collection guard refuses every new job for that collection
        forever. Called once at startup, before anything can enqueue work.
        """
        timestamp = datetime.utcnow().isoformat()
        with sqlite_connect(self.db_path) as conn:
            uploads = conn.execute(
                """UPDATE upload_jobs SET status = 'failed', error = ?, completed_at = ?,
                                          current_file = NULL
                   WHERE status IN ('pending', 'running')""",
                (reason, timestamp),
            ).rowcount
            reindexes = conn.execute(
                """UPDATE reindex_jobs SET status = 'failed', error = ?, completed_at = ?,
                                           current_file = NULL
                   WHERE status IN ('pending', 'running')""",
                (reason, timestamp),
            ).rowcount
            conn.commit()
        return {"upload_jobs": uploads or 0, "reindex_jobs": reindexes or 0}

    # ── Reindex Jobs ─────────────────────────────────────────

    def create_reindex_job(self, config_snapshot: Dict[str, Any]) -> int:
        timestamp = datetime.utcnow().isoformat()
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO reindex_jobs (status, started_at, config_snapshot) VALUES (?, ?, ?)",
                ("pending", timestamp, json.dumps(config_snapshot))
            )
            conn.commit()
            return cursor.lastrowid

    def update_reindex_job(self, job_id: int, **kwargs):
        updates = []
        params = []
        for field in ["status", "total_documents", "processed_documents", "current_file", "error"]:
            if field in kwargs and kwargs[field] is not None:
                updates.append(f"{field} = ?")
                params.append(kwargs[field])
        status = kwargs.get("status")
        if status in ("completed", "failed"):
            updates.append("completed_at = ?")
            params.append(datetime.utcnow().isoformat())
        if not updates:
            return
        params.append(job_id)
        with sqlite_connect(self.db_path) as conn:
            conn.execute(f"UPDATE reindex_jobs SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()

    def _reindex_row_to_dict(self, conn, query, params=None) -> Optional[Dict[str, Any]]:
        conn.row_factory = sqlite3.Row
        row = conn.execute(query, params or ()).fetchone()
        return dict(row) if row else None

    def get_reindex_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            return self._reindex_row_to_dict(
                conn,
                """SELECT id, status, started_at, completed_at, total_documents, processed_documents, current_file, error, config_snapshot
                   FROM reindex_jobs WHERE id = ?""",
                (job_id,)
            )

    def get_latest_reindex_job(self) -> Optional[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            return self._reindex_row_to_dict(
                conn,
                """SELECT id, status, started_at, completed_at, total_documents, processed_documents, current_file, error, config_snapshot
                   FROM reindex_jobs ORDER BY id DESC LIMIT 1"""
            )

    def get_active_reindex_job(self) -> Optional[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            return self._reindex_row_to_dict(
                conn,
                """SELECT id, status, started_at, completed_at, total_documents, processed_documents, current_file, error, config_snapshot
                   FROM reindex_jobs WHERE status IN ('pending', 'running') ORDER BY id DESC LIMIT 1"""
            )

    # ── AI Preferences ───────────────────────────────────────

    def get_ai_preferences(self) -> Optional[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM ai_preferences WHERE id = 1").fetchone()
            if row:
                prefs = dict(row)
                if prefs.get('selected_providers'):
                    try:
                        prefs['selected_providers'] = json.loads(prefs['selected_providers'])
                    except json.JSONDecodeError:
                        prefs['selected_providers'] = []
                return prefs
            return None

    def set_ai_preferences(self, **kwargs):
        selected_providers = kwargs.get("selected_providers")
        rerank_enabled = kwargs.get("rerank_enabled")
        synthesize_enabled = kwargs.get("synthesize_enabled")
        default_provider = kwargs.get("default_provider")
        timestamp = datetime.utcnow().isoformat()

        with sqlite_connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM ai_preferences WHERE id = 1").fetchone()
            if row:
                updates = []
                params = []
                if selected_providers is not None:
                    updates.append("selected_providers = ?")
                    params.append(json.dumps(selected_providers))
                if rerank_enabled is not None:
                    updates.append("rerank_enabled = ?")
                    params.append(1 if rerank_enabled else 0)
                if synthesize_enabled is not None:
                    updates.append("synthesize_enabled = ?")
                    params.append(1 if synthesize_enabled else 0)
                if default_provider is not None:
                    updates.append("default_provider = ?")
                    params.append(default_provider)
                if updates:
                    updates.append("updated_at = ?")
                    params.append(timestamp)
                    params.append(1)
                    conn.execute(f"UPDATE ai_preferences SET {', '.join(updates)} WHERE id = ?", params)
            else:
                conn.execute(
                    """INSERT INTO ai_preferences (id, selected_providers, rerank_enabled, synthesize_enabled, default_provider, updated_at)
                       VALUES (1, ?, ?, ?, ?, ?)""",
                    (json.dumps(selected_providers or []), 1 if rerank_enabled else 0,
                     1 if synthesize_enabled else 0, default_provider, timestamp)
                )
            conn.commit()

    # ── Search History ───────────────────────────────────────

    def add_search_history(self, query: str, top_k: int, results_count: int, **kwargs) -> int:
        timestamp = datetime.utcnow().isoformat()
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                """INSERT INTO search_history (query, timestamp, top_k, results_count, ai_provider, ai_used, results_json, execution_time_ms, user_id, collection_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (query, timestamp, top_k, results_count,
                 kwargs.get("ai_provider"), 1 if kwargs.get("ai_used") else 0,
                 kwargs.get("results_json"), kwargs.get("execution_time_ms"),
                 kwargs.get("user_id"), kwargs.get("collection_id"))
            )
            conn.commit()
            return cursor.lastrowid

    def get_search_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(
                """SELECT id, query, timestamp, top_k, results_count, ai_provider, ai_used, execution_time_ms, user_id, collection_id
                   FROM search_history ORDER BY timestamp DESC LIMIT ?""", (limit,)
            ).fetchall()]

    def get_search_by_id(self, search_id: int) -> Optional[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM search_history WHERE id = ?", (search_id,)).fetchone()
            if row:
                result = dict(row)
                if result.get('results_json'):
                    try:
                        result['results'] = json.loads(result['results_json'])
                    except json.JSONDecodeError:
                        result['results'] = None
                return result
            return None

    def delete_old_search_history(self, days: int = 30):
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM search_history WHERE timestamp < ?", (cutoff,))
            deleted = cursor.rowcount
            conn.commit()
            return deleted

    # ── Chat Usage ───────────────────────────────────────────

    def add_chat_usage(
        self,
        user_id: Optional[str],
        collection_id: str,
        provider: str,
        model: Optional[str],
        input_tokens: int,
        output_tokens: int,
        tool_calls: int = 0,
        cache_hit: bool = False,
        duration_ms: Optional[int] = None,
    ) -> int:
        timestamp = datetime.utcnow().isoformat()
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                """INSERT INTO chat_usage (timestamp, user_id, collection_id, provider, model,
                                           input_tokens, output_tokens, tool_calls, cache_hit, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (timestamp, user_id, collection_id, provider, model,
                 int(input_tokens or 0), int(output_tokens or 0), int(tool_calls or 0),
                 1 if cache_hit else 0, duration_ms)
            )
            conn.commit()
            return cursor.lastrowid

    def get_usage_summary(self, since_iso: str, group_by: str = "user") -> List[Dict[str, Any]]:
        # COALESCE folds anonymous callers into one visible row rather than
        # dropping them from the rollup.
        key = ("COALESCE(user_id, 'anonymous')" if group_by == "user"
               else "substr(timestamp, 1, 10)")
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(
                f"""SELECT {key} AS grouped_by,
                           COUNT(*) AS turns,
                           SUM(input_tokens) AS input_tokens,
                           SUM(output_tokens) AS output_tokens,
                           SUM(tool_calls) AS tool_calls,
                           SUM(cache_hit) AS cache_hits,
                           MAX(timestamp) AS last_active
                    FROM chat_usage WHERE timestamp >= ?
                    GROUP BY grouped_by ORDER BY grouped_by""",
                (since_iso,)
            ).fetchall()]

    def get_user_usage_since(self, user_id: Optional[str], since_iso: str) -> Dict[str, Any]:
        # NULL user_id (anonymous password callers) shares one budget row —
        # "user_id IS ?" matches NULL where "=" would not.
        with sqlite_connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT COUNT(*), COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0)
                   FROM chat_usage WHERE user_id IS ? AND timestamp >= ?""",
                (user_id, since_iso)
            ).fetchone()
            return {"turns": row[0], "input_tokens": row[1], "output_tokens": row[2]}

    def delete_old_chat_usage(self, days: int = 180) -> int:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM chat_usage WHERE timestamp < ?", (cutoff,))
            deleted = cursor.rowcount
            conn.commit()
            return deleted

    # ── Personal MCP access tokens ────────────────────────────

    @staticmethod
    def _decode_scope(row: Dict[str, Any]) -> Dict[str, Any]:
        raw = row.get("collection_scope")
        if isinstance(raw, str) and raw:
            try:
                row["collection_scope"] = json.loads(raw)
            except json.JSONDecodeError:
                row["collection_scope"] = None
        elif not raw:
            row["collection_scope"] = None
        row["can_write"] = bool(row.get("can_write"))
        row["allowlist"] = bool(row.get("allowlist"))
        row["use_count"] = int(row.get("use_count") or 0)
        row.setdefault("expires_at", None)
        return row

    def create_mcp_token(
        self, user_id: Optional[str], name: str, token_hash: str, token_prefix: str,
        collection_scope: Optional[List[str]] = None,
        can_write: bool = False,
        expires_at: Optional[str] = None,
        allowlist: bool = False,
    ) -> Dict[str, Any]:
        token_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat()
        scope_json = json.dumps(list(collection_scope)) if collection_scope else None
        with sqlite_connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO mcp_tokens (id, user_id, name, token_hash, token_prefix, created_at,
                                           collection_scope, can_write, expires_at, allowlist, use_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (token_id, user_id, name, token_hash, token_prefix, timestamp, scope_json,
                 1 if can_write else 0, expires_at, 1 if allowlist else 0),
            )
            conn.commit()
        return {
            "id": token_id,
            "user_id": user_id,
            "name": name,
            "token_prefix": token_prefix,
            "created_at": timestamp,
            "last_used_at": None,
            "revoked_at": None,
            "collection_scope": list(collection_scope) if collection_scope else None,
            "can_write": bool(can_write),
            "expires_at": expires_at,
            "allowlist": bool(allowlist),
            "use_count": 0,
        }

    def list_mcp_tokens(self, user_id: Optional[str]) -> List[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, user_id, name, token_prefix, created_at, last_used_at, revoked_at,
                          collection_scope, can_write, expires_at, allowlist, use_count
                   FROM mcp_tokens WHERE user_id IS ? ORDER BY created_at DESC""",
                (user_id,),
            ).fetchall()
            return [self._decode_scope(dict(r)) for r in rows]

    def get_mcp_token_by_hash(self, token_hash: str) -> Optional[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM mcp_tokens WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            return self._decode_scope(dict(row)) if row else None

    def revoke_all_mcp_tokens_for_user(self, user_id: Optional[str]) -> int:
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE mcp_tokens SET revoked_at = ? WHERE user_id IS ? AND revoked_at IS NULL",
                (datetime.utcnow().isoformat(), user_id),
            )
            conn.commit()
            return cursor.rowcount

    def touch_mcp_token(self, token_id: str) -> None:
        with sqlite_connect(self.db_path) as conn:
            conn.execute(
                "UPDATE mcp_tokens SET last_used_at = ?, use_count = COALESCE(use_count, 0) + 1 WHERE id = ?",
                (datetime.utcnow().isoformat(), token_id),
            )
            conn.commit()

    def revoke_mcp_token(self, token_id: str, user_id: Optional[str]) -> bool:
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                """UPDATE mcp_tokens SET revoked_at = ?
                   WHERE id = ? AND user_id IS ? AND revoked_at IS NULL""",
                (datetime.utcnow().isoformat(), token_id, user_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    # ── Audit trail ──────────────────────────────────────────

    def add_audit_event(
        self,
        actor: Optional[str],
        action: str,
        collection_id: Optional[str] = None,
        document_id: Optional[str] = None,
        target: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> int:
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                """INSERT INTO audit_events (timestamp, actor, action, collection_id, document_id, target, detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (datetime.utcnow().isoformat(), actor, action, collection_id, document_id, target, detail),
            )
            conn.commit()
            return cursor.lastrowid

    def list_audit_events(
        self,
        limit: int = 200,
        action: Optional[str] = None,
        actor: Optional[str] = None,
        collection_id: Optional[str] = None,
        document_id: Optional[str] = None,
        since: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clauses, params = [], []
        for col, val in (("action", action), ("actor", actor),
                         ("collection_id", collection_id), ("document_id", document_id)):
            if val:
                clauses.append(f"{col} = ?")
                params.append(val)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM audit_events {where} ORDER BY timestamp DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_old_audit_events(self, days: int) -> int:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM audit_events WHERE timestamp < ?", (cutoff,))
            conn.commit()
            return cursor.rowcount

    # ── Acceptable-use acknowledgements ──────────────────────

    def record_aup_acknowledgement(self, user_id: str, version: str) -> Dict[str, Any]:
        timestamp = datetime.utcnow().isoformat()
        with sqlite_connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO aup_acknowledgements (user_id, version, accepted_at)
                   VALUES (?, ?, ?)""",
                (user_id, version, timestamp),
            )
            conn.commit()
        return {"user_id": user_id, "version": version, "accepted_at": timestamp}

    def get_aup_acknowledgement(self, user_id: str, version: str) -> Optional[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM aup_acknowledgements WHERE user_id = ? AND version = ?",
                (user_id, version),
            ).fetchone()
            return dict(row) if row else None

    def list_aup_acknowledgements(self) -> List[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM aup_acknowledgements ORDER BY accepted_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Hash blocklist ───────────────────────────────────────

    def add_blocked_hash(
        self, content_hash: str, blocked_by: Optional[str], reason: str = "",
        filename: Optional[str] = None,
    ) -> Dict[str, Any]:
        timestamp = datetime.utcnow().isoformat()
        with sqlite_connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO blocked_hashes (content_hash, blocked_by, blocked_at, reason, filename)
                   VALUES (?, ?, ?, ?, ?)""",
                (content_hash, blocked_by, timestamp, reason or "", filename),
            )
            conn.commit()
        return {"content_hash": content_hash, "blocked_by": blocked_by,
                "blocked_at": timestamp, "reason": reason or "", "filename": filename}

    def remove_blocked_hash(self, content_hash: str) -> bool:
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM blocked_hashes WHERE content_hash = ?", (content_hash,))
            conn.commit()
            return cursor.rowcount > 0

    def is_hash_blocked(self, content_hash: str) -> bool:
        with sqlite_connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM blocked_hashes WHERE content_hash = ?", (content_hash,)
            ).fetchone()
            return row is not None

    def list_blocked_hashes(self) -> List[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM blocked_hashes ORDER BY blocked_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    # ── User Preferences ─────────────────────────────────────

    def get_user_preference(self, key: str, default: Any = None) -> Any:
        with sqlite_connect(self.db_path) as conn:
            row = conn.execute("SELECT value FROM user_preferences WHERE key = ?", (key,)).fetchone()
            if row:
                try:
                    return json.loads(row[0])
                except json.JSONDecodeError:
                    return row[0]
            return default

    def set_user_preference(self, key: str, value: Any):
        value_str = json.dumps(value) if not isinstance(value, str) else value
        timestamp = datetime.utcnow().isoformat()
        with sqlite_connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO user_preferences (key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (key, value_str, timestamp)
            )
            conn.commit()

    def get_all_user_preferences(self) -> Dict[str, Any]:
        with sqlite_connect(self.db_path) as conn:
            prefs = {}
            for key, value in conn.execute("SELECT key, value FROM user_preferences").fetchall():
                try:
                    prefs[key] = json.loads(value)
                except json.JSONDecodeError:
                    prefs[key] = value
            return prefs

    # ── Agent API Keys ───────────────────────────────────────

    def set_agent_api_key(self, provider: str, api_key: str):
        self.set_user_preference(f"agent_api_key_{provider}", api_key)

    def get_agent_api_key(self, provider: str) -> Optional[str]:
        return self.get_user_preference(f"agent_api_key_{provider}")

    def delete_agent_api_key(self, provider: str):
        with sqlite_connect(self.db_path) as conn:
            conn.execute("DELETE FROM user_preferences WHERE key = ?", (f"agent_api_key_{provider}",))
            conn.commit()

    def get_agent_config(self) -> Dict[str, Any]:
        from config import CLOUD_AI_PROVIDERS
        config = {"providers": {}}
        for provider in CLOUD_AI_PROVIDERS:
            key = self.get_agent_api_key(provider)
            if key:
                masked = key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
                config["providers"][provider] = {"configured": True, "key_preview": masked}
            else:
                config["providers"][provider] = {"configured": False, "key_preview": None}
        return config

    # ── MCP Resources ────────────────────────────────────────

def create_app_db() -> DatabaseBackend:
    """Factory: create the appropriate database backend based on configuration."""
    if settings.db_backend == "postgresql":
        from services.db_postgres import PostgresBackend
        return PostgresBackend(settings.postgres_url)
    return SQLiteBackend(Path(settings.data_dir) / "app.db")


# Global instance — import this everywhere
app_db = create_app_db()

# Backward compatibility alias
AppDatabase = SQLiteBackend

"""Abstract database backend interface.

Defines the contract that both SQLite and PostgreSQL backends implement.
This allows the application to switch database backends via configuration.
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List

from services import secret_box

logger = logging.getLogger(__name__)


class DatabaseBusy(Exception):
    """No database connection came free in time. Becomes a 503 with Retry-After."""


def _decode_config(key: str, stored: str) -> Any:
    """A stored config value as the caller expects it: opened, then parsed."""
    if key in secret_box.SECRET_CONFIG_FIELDS:
        stored = secret_box.unseal(stored)
    try:
        return json.loads(stored)
    except json.JSONDecodeError:
        return stored


class DatabaseBackend(ABC):
    """Abstract interface for application database backends."""

    # ── Configuration ────────────────────────────────────────

    # Parsing and encryption are shared; each backend only moves strings.
    def get_config(self, key: str, default: Any = None) -> Any:
        stored = self._read_config(key)
        return default if stored is None else _decode_config(key, stored)

    def set_config(self, key: str, value: Any):
        value_str = json.dumps(value) if not isinstance(value, str) else value
        if key in secret_box.SECRET_CONFIG_FIELDS:
            value_str = secret_box.seal(value_str)
        self._write_config(key, value_str)
        # The name only: a value here can be a credential.
        logger.info(f"Config updated: {key}")

    def get_all_config(self) -> Dict[str, Any]:
        return {key: _decode_config(key, stored) for key, stored in self._read_all_config().items()}

    @abstractmethod
    def _read_config(self, key: str) -> Optional[str]:
        ...

    @abstractmethod
    def _write_config(self, key: str, value: str):
        ...

    @abstractmethod
    def _read_all_config(self) -> Dict[str, str]:
        ...

    @abstractmethod
    def delete_config(self, key: str):
        ...

    def seal_stored_secrets(self) -> int:
        """Encrypt credentials stored before encryption existed. Safe to repeat.

        Runs once at startup. Returns how many values it sealed.
        """
        sealed = 0
        for key, stored in self._read_all_config().items():
            if key in secret_box.SECRET_CONFIG_FIELDS and stored and not secret_box.is_sealed(stored):
                self._write_config(key, secret_box.seal(stored))
                sealed += 1
        for key, stored in self.get_all_user_preferences().items():
            if key.startswith("agent_api_key_") and isinstance(stored, str) and stored and not secret_box.is_sealed(stored):
                self.set_user_preference(key, secret_box.seal(stored))
                sealed += 1
        return sealed

    def pool_stats(self) -> Optional[Dict[str, int]]:
        """Connection pool state for /metrics, or None for a backend with no pool."""
        return None

    # ── Users ────────────────────────────────────────────────

    @abstractmethod
    def upsert_user(self, user_id: str, display_name: Optional[str] = None) -> Dict[str, Any]:
        ...

    @abstractmethod
    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_all_users(self) -> List[Dict[str, Any]]:
        ...

    # ── Collections ──────────────────────────────────────────

    @abstractmethod
    def create_collection(
        self,
        name: str,
        description: str = "",
        color: str = "#3b82f6",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        embedding_model: str = None,
        owner_id: str = "default",
        mcp_display_name: str = None,
        mcp_display_description: str = None,
    ) -> str:
        ...

    @abstractmethod
    def get_collection(self, collection_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_all_collections(self, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
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
        guide_flag: Optional[str] = None,
    ):
        ...

    @abstractmethod
    def delete_collection(self, collection_id: str) -> bool:
        ...

    # ── Collection Documents ─────────────────────────────────

    @abstractmethod
    def add_document_to_collection(self, collection_id: str, document_id: str):
        ...

    @abstractmethod
    def remove_document_from_collection(self, collection_id: str, document_id: str):
        ...

    @abstractmethod
    def remove_documents_from_collection(self, collection_id: str, document_ids: Optional[List[str]] = None):
        """Remove these documents, or with ``document_ids=None`` all of them, in one transaction."""
        ...

    @abstractmethod
    def get_collection_documents(self, collection_id: str) -> List[str]:
        ...

    @abstractmethod
    def get_document_collections(self, document_id: str) -> List[str]:
        ...

    # ── Collection Sharing ───────────────────────────────────

    @abstractmethod
    def create_share(
        self,
        collection_id: str,
        owner_id: str,
        permission: str = "read",
        expires_at: Optional[str] = None,
        invited_email: Optional[str] = None,
    ) -> str:
        ...

    @abstractmethod
    def list_share_contacts(self) -> Dict[str, int]:
        ...

    @abstractmethod
    def count_active_shares_for_email(
        self, email: str, exclude_share_id: Optional[str] = None
    ) -> int:
        ...

    @abstractmethod
    def get_share(self, share_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_shares_for_collection(self, collection_id: str) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def accept_share(self, share_id: str, user_id: str):
        ...

    @abstractmethod
    def get_shared_collections(self, user_id: str) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def revoke_share(self, share_id: str):
        ...

    @abstractmethod
    def check_share_access(self, collection_id: str, user_id: str) -> Optional[str]:
        """Check if user has share access to a collection.

        Returns:
            Permission level ('read' or 'readwrite') or None if no access.
        """
        ...

    # ── Personal MCP access tokens ───────────────────────────
    # Self-serve bearer credentials for headless MCP clients (Claude Code,
    # Codex, ...): a user mints one from the app instead of hand-editing
    # config files. Scoped to /mcp only by the auth middleware.

    # Shared by both backends: the row shape is the same whichever store
    # produced it, so decoding lives here rather than in each backend.
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

    @abstractmethod
    def create_mcp_token(
        self, user_id: Optional[str], name: str, token_hash: str, token_prefix: str,
        collection_scope: Optional[List[str]] = None,
        can_write: bool = False,
        expires_at: Optional[str] = None,
        allowlist: bool = False,
    ) -> Dict[str, Any]:
        """collection_scope: ids of *restricted* collections this token may
        reach over MCP. Restricted collections are otherwise never exposed
        to MCP clients (see services/governance.py).
        can_write: whether the token may add/update sources (write_document).
        expires_at: ISO timestamp after which verify_token refuses the token;
        None means it never expires.
        allowlist: when true, collection_scope is the *only* set of
        collections the token may see over MCP (not just a restricted grant)."""
        ...

    @abstractmethod
    def list_mcp_tokens(self, user_id: Optional[str]) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_mcp_token_by_hash(self, token_hash: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def touch_mcp_token(self, token_id: str) -> None:
        """Record a successful use: bumps last_used_at and use_count."""
        ...

    @abstractmethod
    def revoke_mcp_token(self, token_id: str, user_id: Optional[str]) -> bool:
        """Revoke a token owned by user_id. Returns False if not found/not owned."""
        ...

    @abstractmethod
    def revoke_all_mcp_tokens_for_user(self, user_id: Optional[str]) -> int:
        """Admin suspension: revoke every live token of one identity. Returns count."""
        ...

    # ── Audit trail ──────────────────────────────────────────
    # Append-only accountability log (services/audit.py). Rows are only
    # ever removed by the retention sweep.

    @abstractmethod
    def add_audit_event(
        self,
        actor: Optional[str],
        action: str,
        collection_id: Optional[str] = None,
        document_id: Optional[str] = None,
        target: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> int:
        ...

    @abstractmethod
    def add_audit_events(self, actor: Optional[str], action: str, collection_id: Optional[str],
                         events: List[Dict[str, Optional[str]]]) -> None:
        """Record one action over many documents in one transaction."""
        ...

    @abstractmethod
    def list_audit_events(
        self,
        limit: int = 200,
        action: Optional[str] = None,
        actor: Optional[str] = None,
        collection_id: Optional[str] = None,
        document_id: Optional[str] = None,
        since: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def delete_old_audit_events(self, days: int) -> int:
        ...

    # ── Acceptable-use acknowledgements ──────────────────────

    @abstractmethod
    def record_aup_acknowledgement(self, user_id: str, version: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    def get_aup_acknowledgement(self, user_id: str, version: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def list_aup_acknowledgements(self) -> List[Dict[str, Any]]:
        ...

    # ── Hash blocklist ───────────────────────────────────────
    # sha256 of files an admin removed with "block": the same bytes are
    # refused at every ingest path afterwards, in every collection.

    @abstractmethod
    def add_blocked_hash(
        self, content_hash: str, blocked_by: Optional[str], reason: str = "",
        filename: Optional[str] = None,
    ) -> Dict[str, Any]:
        ...

    @abstractmethod
    def remove_blocked_hash(self, content_hash: str) -> bool:
        ...

    @abstractmethod
    def is_hash_blocked(self, content_hash: str) -> bool:
        ...

    @abstractmethod
    def list_blocked_hashes(self) -> List[Dict[str, Any]]:
        ...

    # ── Upload Jobs ──────────────────────────────────────────

    @abstractmethod
    def create_upload_job(self, collection_id: str, total_files: int, job_type: str = "upload") -> int:
        ...

    @abstractmethod
    def update_upload_job(self, job_id: int, **kwargs):
        ...

    @abstractmethod
    def get_upload_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_active_upload_job(self, collection_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_all_active_upload_jobs(self) -> List[Dict[str, Any]]:
        ...

    # ── Reindex Jobs ─────────────────────────────────────────

    @abstractmethod
    def create_reindex_job(self, config_snapshot: Dict[str, Any]) -> int:
        ...

    @abstractmethod
    def update_reindex_job(self, job_id: int, **kwargs):
        ...

    @abstractmethod
    def get_reindex_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_latest_reindex_job(self) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_active_reindex_job(self) -> Optional[Dict[str, Any]]:
        ...

    # ── AI Preferences ───────────────────────────────────────

    @abstractmethod
    def get_ai_preferences(self) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def set_ai_preferences(self, **kwargs):
        ...

    # ── Search History ───────────────────────────────────────

    @abstractmethod
    def add_search_history(self, query: str, top_k: int, results_count: int, **kwargs) -> int:
        ...

    @abstractmethod
    def get_search_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_search_by_id(self, search_id: int) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def delete_old_search_history(self, days: int = 30):
        ...

    # ── Chat Usage ───────────────────────────────────────────
    # Per-turn token accounting. This is what makes a shared team key safe
    # to expose to many users: without it, nobody can see who spent what,
    # and the daily budget check has nothing to count.

    @abstractmethod
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
        ...

    @abstractmethod
    def get_usage_summary(self, since_iso: str, group_by: str = "user") -> List[Dict[str, Any]]:
        """Rollups since `since_iso`, grouped by 'user' or 'day'."""
        ...

    @abstractmethod
    def get_user_usage_since(self, user_id: Optional[str], since_iso: str) -> Dict[str, Any]:
        """One cheap aggregate row for the quota check: turns + token sums."""
        ...

    @abstractmethod
    def delete_old_chat_usage(self, days: int = 180) -> int:
        ...

    # ── User Preferences ─────────────────────────────────────

    @abstractmethod
    def get_user_preference(self, key: str, default: Any = None) -> Any:
        ...

    @abstractmethod
    def set_user_preference(self, key: str, value: Any):
        ...

    @abstractmethod
    def get_all_user_preferences(self) -> Dict[str, Any]:
        ...

    # ── Agent API Keys ───────────────────────────────────────

    # Agent keys are user preferences sealed by services/secret_box.py, so these
    # are concrete and shared; only the delete (raw SQL) differs per backend.
    def set_agent_api_key(self, provider: str, api_key: str):
        self.set_user_preference(f"agent_api_key_{provider}", secret_box.seal(api_key))

    def get_agent_api_key(self, provider: str) -> Optional[str]:
        return secret_box.unseal(self.get_user_preference(f"agent_api_key_{provider}"))

    @abstractmethod
    def delete_agent_api_key(self, provider: str):
        ...

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

"""Configuration management service for dynamic settings updates.

Handles runtime configuration changes including:
- Embedding model selection
- Chunking parameters
- Other indexing settings

Configuration is stored in SQLite database for persistence across restarts.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from config import ALL_AI_PROVIDERS, Settings, settings
from services.app_database import app_db

logger = logging.getLogger(__name__)

# Every field the Settings tab may persist to the config DB. Also consumed by
# IndexerManager._apply_db_config, which re-applies these on startup: the .env
# write-back below is best-effort persistence only (in containers it lands on
# an ephemeral filesystem), so the DB must be able to restore every field.
VALID_CONFIG_FIELDS = {
    "embedding_model", "embedding_provider", "ollama_base_url", "remote_embedding_model",
    "embedding_base_url", "embedding_api_key", "ollama_cloud_api_key",
    # Deployment default LLM (on-prem private endpoints). Persisted here as
    # well as in .env so a container recreated without its env file keeps
    # pointing at the operator's model instead of falling back to nothing.
    "ai_provider", "ai_base_url", "ai_model", "ai_api_key", "ai_provider_label",
    "chunk_size", "chunk_overlap",
    "default_top_k", "max_top_k",
    "enable_ocr",
    "vision_ocr_provider", "vision_ocr_model", "vision_ocr_api_key",
    "enable_mcp", "mcp_server_id", "mcp_default_collection",
    "mcp_top_k", "mcp_mode", "mcp_semantic_weight",
    "mcp_include_sources", "mcp_max_source_length",
    "mcp_ai_provider", "mcp_ollama_model",
    "enable_llm_schema_inference", "llm_schema_inference_threshold",
    "enable_chat_tab",
    "ollama_num_ctx",
    # Content governance (Admin tab → Policy)
    "content_policy_action", "injection_scan_action", "content_policy_llm_review",
    "content_policy_llm_provider", "content_policy_llm_model",
    "content_policy_llm_sample_pages",
    "aup_required", "aup_version", "aup_text",
}

# Config fields holding a credential. GET /api/config returns MASKED_SECRET in
# place of the stored value, and an update carrying MASKED_SECRET back is
# treated as "leave unchanged" — so a client can round-trip the config without
# either seeing the secret or wiping it.
SECRET_CONFIG_FIELDS = {"vision_ocr_api_key", "ollama_cloud_api_key", "embedding_api_key",
                        "ai_api_key"}
MASKED_SECRET = "********"

# Field names that older builds persisted; read and written as their current
# name so a deployment upgraded in place keeps its embedding choice.
LEGACY_CONFIG_ALIASES = {"ollama_embedding_model": "remote_embedding_model"}


def normalize_config_keys(values: Dict[str, Any]) -> Dict[str, Any]:
    """Map legacy field names onto current ones (current name wins on clash)."""
    out: Dict[str, Any] = {}
    for key, value in values.items():
        target = LEGACY_CONFIG_ALIASES.get(key, key)
        if target in out and key != target:
            continue
        out[target] = value
    return out


class ConfigManager:
    """Manages dynamic configuration updates with database persistence."""

    def __init__(self, env_file: Path = Path(".env")):
        self.env_file = env_file

    def get_current_config(self) -> Dict[str, Any]:
        """Get current configuration values.

        Merges database overrides with .env defaults.
        Database values take precedence.
        """
        # Start with current settings from .env
        config = {
            "embedding_model": settings.embedding_model,
            "embedding_provider": settings.embedding_provider,
            "ollama_base_url": settings.ollama_base_url,
            "remote_embedding_model": settings.remote_embedding_model,
            "embedding_base_url": settings.embedding_base_url,
            "embedding_api_key": settings.embedding_api_key,
            "ollama_cloud_api_key": settings.ollama_cloud_api_key,
            # Deployment default LLM
            "ai_provider": settings.ai_provider,
            "ai_base_url": settings.ai_base_url,
            "ai_model": settings.ai_model,
            "ai_api_key": settings.ai_api_key,
            "ai_provider_label": settings.ai_provider_label,
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "default_top_k": settings.default_top_k,
            "max_top_k": settings.max_top_k,
            "data_dir": str(settings.data_dir),
            "host": settings.host,
            "port": settings.port,
            # OCR settings
            "enable_ocr": settings.enable_ocr,
            "vision_ocr_provider": settings.vision_ocr_provider,
            "vision_ocr_model": settings.vision_ocr_model,
            "vision_ocr_api_key": settings.vision_ocr_api_key,
            "ollama_num_ctx": settings.ollama_num_ctx,
            # MCP settings
            "enable_mcp": settings.enable_mcp,
            "mcp_server_id": settings.mcp_server_id,
            "mcp_default_collection": settings.mcp_default_collection,
            "mcp_top_k": settings.mcp_top_k,
            "mcp_mode": settings.mcp_mode,
            "mcp_semantic_weight": settings.mcp_semantic_weight,
            "mcp_include_sources": settings.mcp_include_sources,
            "mcp_max_source_length": settings.mcp_max_source_length,
            "mcp_ai_provider": settings.mcp_ai_provider,
            "mcp_ollama_model": settings.mcp_ollama_model,
            # LLM schema inference (P0.5)
            "enable_llm_schema_inference": settings.enable_llm_schema_inference,
            "llm_schema_inference_threshold": settings.llm_schema_inference_threshold,
            # UI feature flags
            "enable_chat_tab": settings.enable_chat_tab,
            # Content governance
            "content_policy_action": settings.content_policy_action,
            "injection_scan_action": settings.injection_scan_action,
            "content_policy_llm_review": settings.content_policy_llm_review,
            "content_policy_llm_provider": settings.content_policy_llm_provider,
            "content_policy_llm_model": settings.content_policy_llm_model,
            "content_policy_llm_sample_pages": settings.content_policy_llm_sample_pages,
            "aup_required": settings.aup_required,
            "aup_version": settings.aup_version,
            "aup_text": settings.aup_text,
        }

        # Override with database values if present
        db_config = app_db.get_all_config()
        db_config = normalize_config_keys(db_config)
        for key in config.keys():
            if key in db_config:
                config[key] = db_config[key]

        # Never hand a stored credential back out. Callers get a mask plus a
        # boolean so the UI can show "a key is configured" without holding it.
        for key in SECRET_CONFIG_FIELDS:
            if key in config:
                config[f"{key}_set"] = bool(config[key])
                config[key] = MASKED_SECRET if config[key] else ""

        return config

    def update_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update configuration values.

        Args:
            updates: Dictionary of config key-value pairs to update

        Returns:
            Dictionary with:
                - success (bool): Whether update succeeded
                - requires_restart (bool): Whether server restart is needed
                - requires_reindex (bool): Whether re-indexing is needed
                - updated_fields (list): List of fields that were updated
                - errors (list): List of error messages if any
        """
        result = {
            "success": True,
            "requires_restart": False,
            "requires_reindex": False,
            "updated_fields": [],
            "errors": [],
        }

        # Fields that require restart
        restart_fields = {"host", "port"}
        # Fields that require re-indexing
        reindex_fields = {"embedding_model", "embedding_provider", "remote_embedding_model",
                          "embedding_base_url", "chunk_size", "chunk_overlap"}
        # Embedding fields apply live: the indexer manager drops its cached
        # embedding services so the next collection touched (and every
        # re-index) uses the new provider, while collections already open
        # keep searching their existing index until they are re-indexed.
        embedding_fields = {"embedding_model", "embedding_provider", "remote_embedding_model",
                            "embedding_base_url", "embedding_api_key", "ollama_cloud_api_key",
                            "ollama_base_url"}
        # OCR fields that can be applied at runtime (no restart needed)
        ocr_fields = {
            "enable_ocr",
            "vision_ocr_provider", "vision_ocr_model", "vision_ocr_api_key",
        }
        mcp_fields = {"enable_mcp", "mcp_server_id", "mcp_default_collection", "mcp_top_k",
                      "mcp_mode", "mcp_semantic_weight", "mcp_include_sources",
                      "mcp_max_source_length", "mcp_ai_provider", "mcp_ollama_model"}
        inference_fields = {"enable_llm_schema_inference", "llm_schema_inference_threshold"}
        ui_fields = {"enable_chat_tab"}

        # A masked secret coming back means the client never saw the real one —
        # drop it so a plain save doesn't overwrite the stored key with "****".
        updates = {
            k: v for k, v in normalize_config_keys(updates).items()
            if not (k in SECRET_CONFIG_FIELDS and v == MASKED_SECRET)
        }

        if "embedding_provider" in updates:
            from services.embedding_providers import PROVIDER_IDS
            if updates["embedding_provider"] not in PROVIDER_IDS:
                result["errors"].append(
                    f"Unknown embedding provider: {updates['embedding_provider']}"
                )
                result["success"] = False
                return result

        if "ai_provider" in updates:
            candidate = (updates["ai_provider"] or "").strip()
            if candidate and candidate not in ALL_AI_PROVIDERS:
                result["errors"].append(
                    f"Unknown AI provider: {candidate}. Use one of "
                    f"{', '.join(ALL_AI_PROVIDERS)}."
                )
                result["success"] = False
                return result
            # openai_compatible is defined by its endpoint; without one the
            # provider cannot be built at all, and the failure would surface
            # later as a broken chat rather than here as a rejected save.
            if candidate == "openai_compatible":
                base_url = updates.get("ai_base_url", settings.ai_base_url)
                if not (base_url or "").strip():
                    result["errors"].append(
                        "ai_base_url is required when ai_provider is openai_compatible."
                    )
                    result["success"] = False
                    return result

        if "content_policy_action" in updates:
            if updates["content_policy_action"] not in ("off", "flag", "quarantine", "reject"):
                result["errors"].append(
                    "content_policy_action must be one of: off, flag, quarantine, reject"
                )
                result["success"] = False
                return result

        if "injection_scan_action" in updates:
            if updates["injection_scan_action"] not in ("review", "warn", "off"):
                result["errors"].append(
                    "injection_scan_action must be one of: review, warn, off"
                )
                result["success"] = False
                return result

        # Validate updates
        for key in updates.keys():
            if key not in VALID_CONFIG_FIELDS:
                result["errors"].append(f"Invalid config field: {key}")
                result["success"] = False

        if not result["success"]:
            return result

        # "Re-index needed" is about what actually changed, not what the form
        # happened to post: saving the embedding panel untouched must not
        # tell everyone to rebuild every index.
        before = normalize_config_keys(app_db.get_all_config())
        current_values = self.get_current_config()

        def _changed(key: str, value: Any) -> bool:
            if key in before:
                return str(before[key]) != str(value)
            if key in SECRET_CONFIG_FIELDS:
                return bool(value)
            return key not in current_values or str(current_values[key]) != str(value)

        # Store in database (primary persistence)
        try:
            for key, value in updates.items():
                app_db.set_config(key, value)
        except Exception as e:
            result["errors"].append(f"Failed to update database: {e}")
            result["success"] = False
            return result

        # Also update .env file for restart compatibility
        try:
            self._update_env_file(updates)
        except Exception as e:
            # Non-fatal - database is source of truth
            logger.warning(f"Failed to update .env file: {e}")

        # Update runtime settings (for non-restart fields)
        ocr_settings_changed = False
        embedding_settings_changed = False
        for key, value in updates.items():
            if key in restart_fields:
                result["requires_restart"] = True
            if key in reindex_fields and _changed(key, value):
                result["requires_reindex"] = True

            result["updated_fields"].append(key)

            # Update runtime settings if no restart required
            if key not in restart_fields:
                try:
                    setattr(settings, key, value)
                    if key in ocr_fields:
                        ocr_settings_changed = True
                    if key in embedding_fields:
                        embedding_settings_changed = True
                except Exception as e:
                    result["errors"].append(f"Failed to update {key}: {e}")
                    result["success"] = False

        # Reload document extractor if any OCR settings changed
        if ocr_settings_changed:
            try:
                from services.indexer_manager import indexer_manager
                indexer_manager.reload_document_extractor()
            except Exception as e:
                logger.warning(f"Failed to reload document extractor after OCR settings change: {e}")

        if embedding_settings_changed:
            try:
                from services.indexer_manager import indexer_manager
                indexer_manager.reset_embedding_services()
            except Exception as e:
                logger.warning(f"Failed to reset embedding services after settings change: {e}")

        return result

    def _update_env_file(self, updates: Dict[str, Any]):
        """Update .env file with new values."""
        env_lines = []

        # Read existing .env file if it exists
        if self.env_file.exists():
            with open(self.env_file, "r") as f:
                env_lines = f.readlines()

        # Convert updates to uppercase env var names
        env_updates = {k.upper(): str(v) for k, v in updates.items()}

        # Update or append values
        updated_keys = set()
        for i, line in enumerate(env_lines):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            if "=" in line:
                key = line.split("=", 1)[0].strip()
                if key in env_updates:
                    env_lines[i] = f"{key}={env_updates[key]}\n"
                    updated_keys.add(key)

        # Append new values that weren't found
        for key, value in env_updates.items():
            if key not in updated_keys:
                env_lines.append(f"{key}={value}\n")

        # Write back to file
        with open(self.env_file, "w") as f:
            f.writelines(env_lines)


    def get_embedding_models(self) -> list:
        """Curated sentence-transformers models for the "local" provider.

        The catalog lives in services/embedding_providers.py; this keeps the
        older `name`/`description` shape for existing callers.
        """
        from services.embedding_providers import local_model_catalog
        return local_model_catalog()


# Global instance
config_manager = ConfigManager()



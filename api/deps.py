"""Shared state and helpers used across API router modules."""

import logging

from fastapi import HTTPException

from config import ALL_AI_PROVIDERS, CLOUD_AI_PROVIDERS  # noqa: F401 — re-exported for routers
from services.expertise_store import ExpertiseStore
from services.indexer_manager import indexer_manager
from services.indexing import DocumentIndexer

logger = logging.getLogger(__name__)

expertise_store = ExpertiseStore()

# Set to True by main.py's lifespan once services are ready.
_initialized = False


def mark_initialized() -> None:
    global _initialized
    _initialized = True


def is_initialized() -> bool:
    return _initialized


MAX_TOOL_RESULT_CHARS = 10000


def _truncate_tool_result(payload_json: str) -> str:
    """Cap a serialized tool-result payload so a fan-out of tool calls on one
    turn cannot inflate the next request past the provider's per-minute input
    budget. When truncated, append a marker so the model knows to narrow the
    next call rather than assume the data simply ended."""
    if len(payload_json) <= MAX_TOOL_RESULT_CHARS:
        return payload_json
    return (
        payload_json[:MAX_TOOL_RESULT_CHARS]
        + '\n\n… [truncated — result exceeded size limit; re-call with narrower filters, smaller limit, or a more specific query]'
    )


def resolve_ai_key(provider: str, header_key: str | None, base_url: str | None = None) -> str:
    """Resolve the API key for a cloud provider request.

    In order: a per-request X-AI-Key header (the user brought their own key);
    the server-stored team key from /api/agent/config, which is what lets a
    hosted instance serve coworkers who never enter a key themselves; and
    finally the deployment default key (AI_API_KEY) when the request names
    the deployment's own provider — how an on-prem appliance serves a private
    endpoint nobody holds individual credentials for.

    The two server-held keys are only returned when ``base_url`` (the
    request's X-AI-Base-URL) is empty or is the deployment's own AI_BASE_URL;
    see ``services.ai_service.server_key_allowed``. A request aimed at any
    other endpoint must bring its own key.
    """
    if header_key:
        return header_key
    from services.ai_service import server_key_allowed
    if not server_key_allowed(provider, base_url):
        logger.warning("Not sending a server-held %s key to a base URL the request chose", provider)
        return ""
    stored = ""
    if provider in CLOUD_AI_PROVIDERS:
        try:
            from services.app_database import app_db
            stored = app_db.get_agent_api_key(provider) or ""
        except Exception:
            stored = ""
    if stored:
        return stored
    from config import settings
    if provider and provider == (settings.ai_provider or "").strip():
        return settings.ai_api_key or ""
    return ""


def require_admin(action: str = "change deployment settings") -> str | None:
    """Gate an operator-only endpoint. Returns the admin's identity.

    No-op on an open appliance: private collections off and no admin gate
    (ADMIN_EMAILS together with an identity provider, which is what makes it
    matchable). Once the gate exists it is enforced even in shared-collection
    mode, so admin powers stay distinct from ordinary use.

    With private collections on, anyone the identity provider admits may be a
    user who is not an operator, so "is signed in" is far too wide a gate for
    settings that apply to the whole deployment: require ADMIN_EMAILS. Empty
    means nobody, so this fails closed rather than open.
    """
    from config import settings

    from services.admins import admin_emails, admin_gate_active, is_admin

    if not settings.private_collections and not admin_gate_active():
        return None

    from middleware.user_context import get_request_identity, get_request_user

    user_id = get_request_identity() or get_request_user()
    if not is_admin(user_id):
        if not admin_emails():
            raise HTTPException(
                status_code=403,
                detail=(
                    f"No admin is configured, so nobody may {action}. Set "
                    "ADMIN_EMAILS in .env to the operator's address."
                ),
            )
        raise HTTPException(
            status_code=403,
            detail=f"Only an admin (ADMIN_EMAILS) may {action}.",
        )
    return user_id


def require_collection_access(
    collection_id: str, user_id: str | None, write: bool = False, owner: bool = False
) -> str:
    """Raise unless the user can access the collection; return the permission.

    404 both for a missing collection and for one the user cannot see, so
    collection ids can't be probed. 403 when a read-only share tries to write.
    No-ops (returns 'owner') when private collections mode is off.

    Three tiers, not two. ``write=True`` is the *contributor* gate: may this
    caller add and remove sources. ``owner=True`` is the *configuration*
    gate: may this caller change how the collection is built — reindex it,
    change its chunking or embedding model, publish it, delete it. A
    read-write share deliberately clears the first and not the second: a
    collaborator contributes material, but reindexing rewrites the owner's
    index under them, so that authority stays with the owner alone.
    """
    from services.sharing_service import sharing_service

    access = sharing_service.check_collection_access(collection_id, user_id)
    if access is None:
        raise HTTPException(
            status_code=404, detail=f"Collection '{collection_id}' not found"
        )
    if owner and access != "owner":
        from config import settings
        from middleware.user_context import get_request_app
        from services.app_database import app_db

        row = app_db.get_collection(collection_id) or {}
        team = (row.get("owner_id") or "").strip() in ("", settings.default_user_id)
        if team and get_request_app() is None:
            raise HTTPException(
                status_code=403,
                detail=(
                    "This is a team collection: only an admin (ADMIN_EMAILS) can "
                    "change how it is set up, relabel or delete it. You can add "
                    "and remove sources."
                ),
            )
        raise HTTPException(
            status_code=403,
            detail=(
                "Only the collection's owner can change how it is set up. "
                "Clone it to get a copy you control."
            ),
        )
    if write and access == "read":
        raise HTTPException(
            status_code=403,
            detail="This collection is shared with you read-only",
        )
    return access


def get_indexer(collection_id: str = "default") -> DocumentIndexer:
    """Get indexer for a collection.

    Every read path in the API — search, chat retrieval, document listing,
    PDF/chunk serving — comes through here, so this is where private-
    collections read access is enforced. Write endpoints additionally call
    require_collection_access(write=True) themselves.
    """
    if not _initialized:
        raise HTTPException(
            status_code=503,
            detail="Service is still initializing. Please wait a moment and try again."
        )
    from config import settings
    if settings.private_collections:
        from middleware.user_context import get_request_user
        require_collection_access(collection_id, get_request_user())
    # A collection marked local only is not opened for a request that hands
    # what it reads to an outside model (services/data_controls.py).
    from services import data_controls
    data_controls.check_collection(collection_id)
    return indexer_manager.get_indexer(collection_id)

"""Collection CRUD and re-indexing endpoints."""

import json
import logging

from typing import Dict, List, Optional

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from config import settings
from services.config_manager import config_manager
from services import audit, authored_text
from services.reindex_service import normalize_reindex_extensions, reindex_service
from services.collection_service import collection_service
from services.indexer_manager import indexer_manager
from middleware.user_context import get_current_user_id

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/api/reindex",
    summary="Start re-indexing all documents",
    tags=["admin"],
)
async def start_reindex():
    """
    Start a background re-indexing job for all documents.

    Uses current configuration from database or settings.
    Returns immediately with job ID for progress tracking.

    Returns:
        - job_id: ID for tracking re-indexing progress
        - status: Initial job status
    """
    # Deployment-wide: this rebuilds every collection's index, including
    # ones the caller does not own. Same bar as the other /api/admin work.
    from api.deps import require_admin
    require_admin("re-index the whole deployment")

    try:
        # Get current config (from database with .env fallback)
        current_config = config_manager.get_current_config()

        # The job builds its own embedding service through the shared
        # factory and reads the dimension from it, so no model is loaded
        # here (a local model instantiated on the request thread doubled
        # memory and ignored the configured provider).
        job_id = await reindex_service.start_reindex(
            documents_dir=settings.data_dir / "documents",
            embedding_model=current_config["embedding_model"],
            chunk_size=current_config["chunk_size"],
            chunk_overlap=current_config["chunk_overlap"],
        )

        return {
            "job_id": job_id,
            "status": "started",
            "message": "Re-indexing job started in background"
        }

    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Failed to start re-indexing: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start re-indexing: {str(e)}"
        )


@router.post(
    "/api/collections/{collection_id}/reindex",
    summary="Start re-indexing a collection",
    tags=["collections"],
)
async def start_collection_reindex(collection_id: str, body: Optional[dict] = None,
                                   user_id: str = Depends(get_current_user_id)):
    """
    Start a background re-indexing job for a specific collection.

    Re-indexes all documents in the collection using the collection's
    current settings (chunk_size, chunk_overlap, embedding_model).

    This is useful after changing collection settings to apply
    the new settings to existing documents.

    Args:
        collection_id: Collection to reindex

    Returns:
        - job_id: ID for tracking re-indexing progress
        - status: Initial job status
        - collection_id: Collection being reindexed
    """
    try:
        from api.deps import require_collection_access
        require_collection_access(collection_id, user_id, owner=True)

        # Get collection settings
        collection = collection_service.get_collection(collection_id)
        if not collection:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Collection '{collection_id}' not found"
            )

        # Get collection paths
        documents_dir = indexer_manager.get_documents_path(collection_id)
        indexes_dir = indexer_manager.get_indexes_path(collection_id)

        # B8: {"embedding_model": ...} re-indexes with another model. The
        # collection's label changes only once the new vectors are saved, so
        # no moment exists with one model's vectors under the other's name.
        new_model = ((body or {}).get("embedding_model") or "").strip() or None
        if new_model and len(new_model) > 200:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="embedding_model: the name is longer than 200 characters")
        relabel_to = new_model if new_model and new_model != collection["embedding_model"] else None

        # Start re-indexing (the job resolves provider + dimension itself)
        job_id = await reindex_service.start_collection_reindex(
            collection_id=collection_id,
            documents_dir=documents_dir,
            indexes_dir=indexes_dir,
            embedding_model=relabel_to or collection["embedding_model"],
            chunk_size=collection["chunk_size"],
            chunk_overlap=collection["chunk_overlap"],
            relabel_to=relabel_to,
        )
        if relabel_to:
            from services import audit
            audit.record("collection.embedding", actor=user_id, collection_id=collection_id,
                         detail={"from": collection["embedding_model"], "to": relabel_to, "via": "reindex"})

        return {
            "job_id": job_id,
            "status": "started",
            "collection_id": collection_id,
            "message": f"Re-indexing job started for collection '{collection_id}'"
        }

    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start re-indexing for collection {collection_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start re-indexing: {str(e)}"
        )


class ReindexExtensionsRequest(BaseModel):
    extensions: List[str] = Field(
        ..., description="Extensions to re-index, e.g. ['.acme']. Case and the leading dot are optional.")


@router.post(
    "/api/collections/{collection_id}/reindex-extensions",
    summary="Re-index only the documents with the given file extensions",
    tags=["collections"],
)
async def start_extension_reindex(
    collection_id: str,
    body: ReindexExtensionsRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Start a background job that re-reads only documents with these extensions.

    The point is applying a changed custom-file-type profile to files already
    indexed, without rebuilding the rest of the collection: other documents keep
    their chunks and vectors untouched. Owner only (it changes how the
    collection is built). One re-index runs at a time (409 otherwise).

    Refused with 400 for an empty list, a malformed extension, spreadsheets
    (those need a full re-index) and any extension the server does not
    currently read, such as a deleted custom type: re-indexing it would drop the
    files, so they are left searchable as they are.

    Returns the ``job_id`` to follow on ``GET /api/reindex/status``.
    """
    from api.deps import require_collection_access

    require_collection_access(collection_id, user_id, owner=True)
    if not collection_service.get_collection(collection_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection '{collection_id}' not found"
        )

    try:
        wanted = normalize_reindex_extensions(body.extensions)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    try:
        job_id = await reindex_service.start_extension_reindex(
            collection_id=collection_id, extensions=wanted,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to start extension re-indexing for collection {collection_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start extension re-indexing: {str(e)}"
        )

    audit.record("collection.reindex_extensions", collection_id=collection_id,
                 detail={"extensions": wanted, "job_id": job_id})
    return {
        "job_id": job_id,
        "status": "started",
        "collection_id": collection_id,
        "extensions": wanted,
        "message": f"Re-indexing {', '.join(wanted)} files in collection '{collection_id}'",
    }


def _may_see_reindex_job(collection_id: Optional[str]) -> bool:
    """Read access to the collection being rebuilt; admin for a global job."""
    from api.deps import require_admin, require_collection_access
    from middleware.user_context import get_request_user

    try:
        if collection_id:
            require_collection_access(collection_id, get_request_user())
        else:
            require_admin("see a deployment-wide re-index")
    except HTTPException:
        return False
    return True


@router.get(
    "/api/reindex/status",
    summary="Get re-indexing job status",
    tags=["admin"],
)
def get_reindex_status(job_id: int = None):
    """
    Get status of a re-indexing job.

    Args:
        job_id: Optional job ID. If not provided, returns latest job
            (null when none has run, or when the caller may not see it).
            404 for an unknown job_id or one the caller may not see.

    A job that rebuilds one collection is visible to whoever can read that
    collection; a deployment-wide re-index only to an admin.

    Returns:
        Job details including:
        - id: Job ID
        - status: pending, running, completed, or failed
        - total_documents: Total documents to process
        - processed_documents: Documents processed so far
        - current_file: Currently processing file
        - started_at: Job start timestamp
        - completed_at: Job completion timestamp (if finished)
        - error: Error message (if failed)
    """
    from services.app_database import app_db

    if job_id:
        job = reindex_service.get_job_status(job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Re-index job {job_id} not found",
            )
    else:
        # Get latest job (including completed ones), not just currently active
        job = app_db.get_latest_reindex_job()

    if not job:
        # No job has ever run: nothing to report, but not an error.
        return None

    # Which collection this job is rebuilding lives in the config snapshot.
    # Surface it: the sidebar refreshes mid-job only when the job names the
    # collection on screen, so without this a re-index never refreshed
    # anything until it finished.
    job = dict(job)
    snapshot = job.pop("config_snapshot", None)
    collection_id = None
    if snapshot:
        try:
            parsed = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
            collection_id = (parsed or {}).get("collection_id")
        except (json.JSONDecodeError, TypeError, AttributeError):
            collection_id = None

    if not _may_see_reindex_job(collection_id):
        if job_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Re-index job {job_id} not found",
            )
        return None

    # Calculate progress percentage
    if job["total_documents"] > 0:
        progress = (job["processed_documents"] / job["total_documents"]) * 100
    else:
        progress = 0

    return {
        **job,
        "collection_id": collection_id,
        "progress_percent": round(progress, 1),
        # What it is doing inside the current file, while it runs.
        **reindex_service.live_progress(job["id"]),
    }


# AI Preferences endpoints
# Collection endpoints
def _own_model_missing(collection: dict) -> bool:
    """B8: the collection is recorded with a local model other than the
    server's, and that model is not on this server, so it is searched by
    keyword. Disk only: no download is tried here."""
    from services.embedder import local_model_cached

    own = collection.get("embedding_model") or ""
    if settings.embedding_provider != "local" or not own or own == settings.embedding_model:
        return False
    try:
        return not local_model_cached(own)
    except Exception:  # noqa: BLE001 - when unsure, do not mark it
        return False


@router.get(
    "/api/collections",
    summary="List all collections",
    tags=["collections"],
)
def list_collections(request: Request, user_id: str = Depends(get_current_user_id)):
    """Get all document collections visible to the current user.

    Each carries ``permission`` ('owner', 'readwrite' or 'read'), what this
    caller may do with it, so the app can hide or explain the actions the
    server would refuse; ``team`` marks a collection with no single owner,
    whose owner-tier actions belong to the admins once ADMIN_EMAILS is set.
    """
    collections = collection_service.get_all_collections(user_id=user_id)
    for c in collections:
        # With private collections off every caller owns everything
        # (sharing_service.check_collection_access), and the listing skips
        # the field; say it anyway so the app reads one shape.
        c.setdefault("permission", "owner")
        c["model_missing"] = _own_model_missing(c)
    return {
        "collections": collections,
        "user_id": user_id,
        "private_collections": settings.private_collections,
    }


@router.post(
    "/api/collections",
    summary="Create a new collection",
    tags=["collections"],
    status_code=status.HTTP_201_CREATED,
)
def create_collection(collection_data: dict, user_id: str = Depends(get_current_user_id)):
    """
    Create a new document collection owned by the current user.

    Body:
        name: Collection name (required)
        description: Collection description
        color: Hex color for UI (default: #3b82f6)
        chunk_size: Text chunk size (default: 500)
        chunk_overlap: Chunk overlap (default: 50)
        embedding_model: Embedding model to use (default: this server's
            EMBEDDING_MODEL, i.e. the model its image baked)
        visibility: "private" (default) or "team" — private-collections mode
            only. Team collections belong to everyone; private ones belong to
            the caller until shared.
    """
    name = collection_data.get("name")
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Collection name is required"
        )
    try:
        authored_text.check_length("name", str(name), authored_text.MAX_NAME_CHARS)
        authored_text.check_length("description", str(collection_data.get("description") or ""),
                                   authored_text.MAX_DESCRIPTION_CHARS)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    collection = collection_service.create_collection(
        name=name,
        description=collection_data.get("description", ""),
        color=collection_data.get("color", "#3b82f6"),
        chunk_size=collection_data.get("chunk_size", 500),
        chunk_overlap=collection_data.get("chunk_overlap", 50),
        # None = the server's configured model. A literal MiniLM here gave every
        # collection made in the app MiniLM, even on an image that bakes only
        # bge-small, where it then could not be embedded offline.
        embedding_model=collection_data.get("embedding_model") or None,
        # The creator chooses team (owner "default" — everyone's) or private
        # (theirs until shared). Anonymous password-auth callers have no
        # identity to own anything, so their collections are always team.
        owner_id=(
            settings.default_user_id
            if collection_data.get("visibility") == "team" or not user_id
            else user_id
        ),
    )

    return collection


@router.get(
    "/api/collections/{collection_id}/embedding-health",
    summary="Can this collection embed new content?",
    tags=["collections"],
)
async def get_collection_embedding_health(collection_id: str, user_id: str = Depends(get_current_user_id)):
    """Whether the loaded embedding model fits this collection's index.

    ``ok: false`` carries a stable ``code`` (``embedding_mismatch`` when the
    model's vector size differs from the index, ``embedding_unavailable`` when
    the model cannot be built at all), a human ``message``, and for a
    mismatch both dimensions. A mismatch is fixed by re-indexing, which the
    UI offers via ``POST /api/collections/{id}/reindex``.
    """
    import asyncio

    from api.deps import require_collection_access
    from services.embedder import (
        EmbeddingDimensionMismatch, EmbeddingModelMismatch, EmbeddingUnavailable, ensure_embedding_ready,
    )

    require_collection_access(collection_id, user_id)
    collection = collection_service.get_collection(collection_id)
    if not collection:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Collection '{collection_id}' not found")

    # Re-indexing rewrites the owner's index, so only offer it to someone who
    # may actually run it (the POST enforces the same gate).
    try:
        require_collection_access(collection_id, user_id, owner=True)
        can_reindex = True
    except HTTPException:
        can_reindex = False

    def probe() -> dict:
        indexer = indexer_manager.get_indexer(collection_id)
        ensure_embedding_ready(indexer.embedding_service)
        out = {"ok": True, "collection_id": collection_id}
        # A collection indexed before the current recipe (B2) searches less
        # well than it could; a re-index rebuilds it from empty, which takes
        # the current recipe. Offered, never done unasked.
        try:
            from services.indexing import embedding_text
            store = indexer.vector_store.metadata_store
            if store.get_total_chunks() > 0 and indexer.index_recipe < embedding_text.CURRENT_RECIPE:
                out.update(recipe=indexer.index_recipe, recipe_upgrade=True, can_reindex=can_reindex,
                           chunks=store.get_total_chunks())
        except Exception:  # noqa: BLE001 - advisory only
            pass
        return out

    try:
        return await asyncio.to_thread(probe)
    except EmbeddingDimensionMismatch as e:
        return {"ok": False, "collection_id": collection_id, "code": e.code, "message": str(e),
                "model": e.model_name, "model_dim": e.model_dim, "index_dim": e.index_dim,
                "fix": "reindex", "can_reindex": can_reindex}
    except EmbeddingModelMismatch as e:
        return {"ok": False, "collection_id": collection_id, "code": e.code, "message": str(e),
                "model": e.model_name, "stored_model": e.stored_model,
                "fix": "reindex", "can_reindex": can_reindex}
    except EmbeddingUnavailable as e:
        out = {"ok": False, "collection_id": collection_id, "code": e.code, "message": str(e), "fix": "model"}
        # B8: a collection recorded with a local model this server does not
        # have (MiniLM on a bge image, say) is fixed by copying it to the
        # server's model; say which two models, and that a copy works.
        own = collection.get("embedding_model") or ""
        if settings.embedding_provider == "local" and own and own != settings.embedding_model:
            out.update(fix="copy", collection_model=own, server_model=settings.embedding_model,
                       can_copy=True, can_reindex=can_reindex)
        return out


@router.get(
    "/api/collections/{collection_id}",
    summary="Get collection details",
    tags=["collections"],
)
def get_collection(collection_id: str, user_id: str = Depends(get_current_user_id)):
    """Get details for a specific collection."""
    from api.deps import require_collection_access
    access = require_collection_access(collection_id, user_id)
    collection = collection_service.get_collection(collection_id)
    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection '{collection_id}' not found"
        )
    collection['permission'] = access
    return collection


@router.get(
    "/api/collections/{collection_id}/stats",
    summary="Get collection stats (SQL-backed counts)",
    tags=["collections"],
)
def get_collection_stats(collection_id: str):
    """
    Cheap canonical stats for a collection.

    Counts come from SQL aggregates over the collection's metadata store
    (COUNT/SUM on the documents table, COUNT on chunks), so the response
    stays O(1)-ish regardless of collection size. This is what the frontend
    header/footer and chat gate should poll — never the unpaginated
    /documents list.

    Returns:
        - collection_id
        - total_documents
        - total_pages
        - total_chunks
    """
    from api.deps import get_indexer

    try:
        get_indexer(collection_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    return indexer_manager.get_collection_stats(collection_id)


@router.put(
    "/api/collections/{collection_id}",
    summary="Update collection settings",
    tags=["collections"],
)
def update_collection(collection_id: str, updates: dict, user_id: str = Depends(get_current_user_id)):
    """
    Update collection settings. Requires owner access.

    Body:
        name: New name
        description: New description
        color: New hex color
        chunk_size: New chunk size
        chunk_overlap: New chunk overlap
        embedding_model: New embedding model
        published: True releases the collection read-only to everyone in the
            deployment (in-app and over MCP); False withdraws it.

    Note: Changing chunk_size, chunk_overlap, or embedding_model
    requires re-indexing the collection's documents.
    """
    from api.deps import require_collection_access
    require_collection_access(collection_id, user_id, owner=True)

    # Text that is delivered to a model on later requests: typed, bounded.
    for field, limit in (("guide", authored_text.MAX_GUIDE_CHARS),
                         ("description", authored_text.MAX_DESCRIPTION_CHARS),
                         ("name", authored_text.MAX_NAME_CHARS)):
        value = updates.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"{field} must be text")
        try:
            authored_text.check_length(field, value, limit)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    prior = collection_service.get_collection(collection_id) or {}

    # B8: renaming a collection's model while it holds vectors would leave
    # one model's vectors under another's name until someone re-indexed. A
    # collection with vectors changes model only through a re-index, which
    # relabels it once the new vectors are saved.
    new_model = updates.get("embedding_model")
    if new_model is not None and new_model != (prior.get("embedding_model") or None):
        from services.embedder import index_dimension
        if index_dimension(indexer_manager.get_indexes_path(collection_id)) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=("This collection holds vectors made with its current model, so its model changes "
                        "only through a re-index: POST /api/collections/{id}/reindex with "
                        '{"embedding_model": "..."}, or copy it with that model.'),
            )

    # Sensitivity is a governance label: validated, and its changes audited
    # (it decides whether the collection can be shared or reached over MCP).
    sensitivity = None
    if "sensitivity" in updates and updates["sensitivity"] is not None:
        from services.governance import normalize_sensitivity
        try:
            sensitivity = normalize_sensitivity(updates["sensitivity"])
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        previous = (collection_service.get_collection(collection_id) or {}).get("sensitivity")
        if previous != sensitivity:
            from services import audit
            audit.record("collection.sensitivity", actor=user_id, collection_id=collection_id,
                         detail={"from": previous, "to": sensitivity})

    # A classification marking decides which machines may be given the
    # collection (services/data_controls.py). "" clears it.
    classification = None
    if "classification" in updates and updates["classification"] is not None:
        from services import audit, data_controls
        try:
            classification = data_controls.normalize(updates["classification"]) or ""
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        previous = prior.get("classification") or ""
        if previous != classification:
            audit.record("collection.classification", actor=user_id, collection_id=collection_id,
                         detail={"from": previous or None, "to": classification or None,
                                 "local_only": data_controls.is_local_only(classification)})
        else:
            classification = None

    # Publishing releases the collection to everyone in the deployment as
    # read-only. Two refusals guard it: a restricted label is a boundary
    # (same rule as sharing), and a team collection has no single owner to
    # keep configuration authority — publishing it would lock it for
    # everybody, including whoever published it.
    published = None
    if "published" in updates and updates["published"] is not None:
        published = bool(updates["published"])
        collection_row = collection_service.get_collection(collection_id) or {}
        if published:
            from services.governance import is_restricted

            label = sensitivity or collection_row.get("sensitivity")
            if is_restricted({"sensitivity": label}):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "This collection is labelled restricted and cannot be published. "
                        "Change its sensitivity label first if releasing it is intended."
                    ),
                )
            owner = (collection_row.get("owner_id") or "").strip()
            if owner in ("", settings.default_user_id):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "This is a team collection — everyone can already reach it, and it "
                        "has no single owner to keep control of how it is built. Publishing "
                        "applies to collections owned by one person."
                    ),
                )
        if bool(collection_row.get("published")) != published:
            from services import audit
            audit.record("collection.published", actor=user_id, collection_id=collection_id,
                         detail={"published": published})

    # Labelling a collection restricted withdraws what it had already
    # released: publication now, its shares once the label is saved.
    if sensitivity == "restricted" and published is None and prior.get("published"):
        from services import audit
        published = False
        audit.record("collection.published", actor=user_id, collection_id=collection_id,
                     detail={"published": False, "reason": "restricted"})

    collection = collection_service.update_collection(
        collection_id=collection_id,
        name=updates.get("name"),
        description=updates.get("description"),
        color=updates.get("color"),
        chunk_size=updates.get("chunk_size"),
        chunk_overlap=updates.get("chunk_overlap"),
        embedding_model=updates.get("embedding_model"),
        guide=updates.get("guide"),
        sensitivity=sensitivity,
        published=published,
        classification=classification,
    )
    if classification is not None:
        # Answers written before the marking may have come from a model the
        # collection can no longer reach.
        from services.governance import _invalidate_answers
        _invalidate_answers(collection_id)

    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection '{collection_id}' not found"
        )

    if sensitivity == "restricted":
        from services import audit
        from services.sharing_service import sharing_service

        for share in sharing_service.revoke_all_shares(collection_id):
            audit.record("share.revoke", actor=user_id, collection_id=collection_id,
                         target=share.get("invited_email"),
                         detail={"share_id": share.get("id"), "reason": "restricted"})

    # The guide, name and description are delivered to other people's agents
    # and prompts (see services/authored_text.py). Record that they changed,
    # by size and fingerprint rather than text, and tell the author when the
    # scan found instruction-like wording. The save itself always succeeds.
    from services import audit
    new_guide = updates.get("guide")
    if new_guide is not None and (prior.get("guide") or "") != new_guide:
        flag = collection.get("guide_flag")
        audit.record("collection.guide", actor=user_id, collection_id=collection_id, detail={
            "chars": len(new_guide), "digest": authored_text.digest(new_guide),
            "previous_digest": authored_text.digest(prior.get("guide")) if prior.get("guide") else None,
            "flagged": bool(flag),
            **({"flag_level": flag.get("level"), "flag_categories": flag.get("categories")} if flag else {}),
        })
    meta_changed = {
        field: {"chars": len(updates[field]), "digest": authored_text.digest(updates[field])}
        for field in ("name", "description")
        if updates.get(field) is not None and (prior.get(field) or "") != updates[field]
    }
    if meta_changed:
        audit.record("collection.metadata", actor=user_id, collection_id=collection_id,
                     detail={"fields": meta_changed})
    new_model = updates.get("embedding_model")
    if new_model is not None and (prior.get("embedding_model") or None) != new_model:
        audit.record("collection.embedding", actor=user_id, collection_id=collection_id,
                     detail={"from": prior.get("embedding_model"), "to": new_model})

    guide_warning = (
        authored_text.warning_for_author(collection.get("guide_flag"), "guide")
        if new_guide is not None else None
    )
    return {**collection, "guide_warning": guide_warning}


@router.post(
    "/api/collections/{collection_id}/clone",
    summary="Clone a collection you can read into one you own",
    tags=["collections"],
    status_code=status.HTTP_201_CREATED,
)
def clone_collection(collection_id: str, body: dict = None, user_id: str = Depends(get_current_user_id)):
    """
    Copy a collection's sources into a new collection owned by the caller.

    Read access is enough — that is the point. Someone reading a published
    collection cannot change how it is built, so cloning is how they get a
    copy they can rechunk, re-embed and add to. The copy starts unpublished.

    Body:
        name: Name for the copy (default: "Copy of <source name>")
        embedding_model: Local model to build the copy with (default: the
            source's). This is how a collection moves to another model: the
            copy is embedded afresh with it, and the source keeps answering
            with its own model until the copy is ready.

    Returns the new collection plus `job_id` for the background indexing job.
    """
    from api.deps import require_collection_access
    from services import storage_quota

    require_collection_access(collection_id, user_id)

    if not user_id and settings.private_collections:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sign in to clone a collection — a copy needs an owner.",
        )

    try:
        return collection_service.clone_collection(
            source_id=collection_id,
            owner_id=user_id or settings.default_user_id,
            name=(body or {}).get("name"),
            embedding_model=(body or {}).get("embedding_model") or None,
        )
    except storage_quota.StorageLimitExceeded as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_detail())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e:
        # An indexing job is already running for the new collection, or the
        # deployment-wide job cap is reached.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.get(
    "/api/collections/{collection_id}/export",
    summary="Download a collection as a portable bundle",
    tags=["collections"],
)
def export_collection(  # sync: builds a zip in the threadpool
    collection_id: str,
    include_sources: bool = False,
    user_id: str = Depends(get_current_user_id),
):
    """
    A `.clio.zip` another Clio can import: chunk text, pages, document
    records, CSV tables, the vectors with the exact model that produced
    them, and (with `include_sources=true`) the original files.

    Owner only. Quarantined documents are left out. A restricted collection
    additionally needs an administrator, like sharing it would.
    """
    from starlette.background import BackgroundTask
    from fastapi.responses import FileResponse

    from api.deps import require_admin, require_collection_access
    from services import audit, governance
    from services.collection_bundle import BundleError, export_collection as build

    require_collection_access(collection_id, user_id, owner=True)
    if governance.is_restricted(collection_service.get_collection(collection_id)):
        require_admin("export a restricted collection")

    try:
        path, filename = build(collection_id, include_sources=include_sources, exported_by=user_id)
    except BundleError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    audit.record("collection.exported", actor=user_id, collection_id=collection_id,
                 detail={"include_sources": include_sources, "bytes": path.stat().st_size})
    return FileResponse(
        path, media_type="application/zip", filename=filename,
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@router.get(
    "/api/collections/{collection_id}/export/preview",
    summary="What an export would contain, and which model indexed it",
    tags=["collections"],
)
def export_preview(collection_id: str, user_id: str = Depends(get_current_user_id)):
    """Counts and the embedding model, without building the bundle."""
    from api.deps import require_collection_access
    from services.collection_bundle import BundleError, export_preview as preview

    require_collection_access(collection_id, user_id, owner=True)
    try:
        return preview(collection_id)
    except BundleError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


def _static_export_refusal(collection_id: str, error) -> HTTPException:
    """The HTTP answer for a static export that cannot be made."""
    from services import data_controls
    from services.static_export import DESTINATION

    if error.code == "not_found":
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if error.code == "local_only":
        # The same 403 and audit entry as any other outside service.
        row = collection_service.get_collection(collection_id)
        data_controls._record_block(row, DESTINATION)
        return data_controls.ExternalServiceBlocked(
            str(error), classification=data_controls.collection_classification(row), service=DESTINATION)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


@router.get(
    "/api/collections/{collection_id}/export/static",
    summary="Download a collection as files a static website can search",
    tags=["collections"],
)
def export_collection_static(  # sync: reads the index and builds a zip in the threadpool
    collection_id: str,
    confirm_public: bool = False,
    quantization: str = "int8",
    passages_per_file: int = 2000,
    site: bool = False,
    include: Optional[str] = None,
    user_id: str = Depends(get_current_user_id),
):
    """
    A zip of `manifest.json`, `passages.json` (split into `passages-0001.json`,
    ... past `passages_per_file`) and `vectors.bin`: everything a page needs to
    search the collection by meaning with no server (docs/STATIC_SEARCH.md).

    `site=true` returns a whole site instead, ready to serve: those files
    under `search/`, the reference page, and the embedding model and
    transformers.js runtime it needs, so the site loads nothing from Hugging
    Face or a CDN. The preview's `site.available` says whether this server can
    make one, and how much it adds.

    `include` names what else to carry, separated by commas (`reader,related`,
    or `all`): the preview's `additions` lists each one, whether it can be made,
    and its size. With nothing named, the export is the search files alone.

    Owner only, and everything in it is published. Refused unless the
    collection is labelled Public, holds nothing marked local only, was
    embedded on this machine with a model that has a checked browser build,
    and `confirm_public=true` says it is cleared for release. Held sources,
    and sources with a stricter label of their own, are left out.
    """
    from starlette.background import BackgroundTask
    from fastapi.responses import FileResponse

    from api.deps import require_collection_access
    from services.static_export import StaticExportError, export_zip

    require_collection_access(collection_id, user_id, owner=True)
    try:
        path, filename, detail = export_zip(
            collection_id, confirm_public=confirm_public, quantization=quantization,
            passages_per_file=passages_per_file, site=site, include=include)
    except StaticExportError as e:
        raise _static_export_refusal(collection_id, e)

    audit.record("collection.static_export", actor=user_id, collection_id=collection_id,
                 detail={**detail, "bytes": path.stat().st_size})
    return FileResponse(
        path, media_type="application/zip", filename=filename,
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@router.get(
    "/api/collections/{collection_id}/export/static/preview",
    summary="What a static-site export would contain, and what stands in its way",
    tags=["collections"],
)
def export_static_preview(collection_id: str, user_id: str = Depends(get_current_user_id)):
    """Counts, sizes, the model a page must use, and `blockers` (each with a
    stable `code` and a message) when the collection cannot be exported."""
    from api.deps import require_collection_access
    from services.static_export import StaticExportError, preview

    require_collection_access(collection_id, user_id, owner=True)
    try:
        return preview(collection_id)
    except StaticExportError as e:
        if e.code == "not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


class StaticAddressesRequest(BaseModel):
    """Where a static export's sources are published. A field left out is kept."""
    files_base_url: Optional[str] = Field(
        default=None, max_length=2048,
        description="Base address of the site's copies of the files; each file's name is added to it. "
                    "Empty clears it.")
    documents: Optional[Dict[str, Optional[str]]] = Field(
        default=None, description="Source id -> its own web address (empty or null clears it).")
    site_url: Optional[str] = Field(
        default=None, max_length=2048,
        description="Where the export will be served (https://...), for its sitemap, canonical links and feed. "
                    "Empty clears it.")


@router.get(
    "/api/collections/{collection_id}/export/static/addresses",
    summary="Where each source of a static export links to on the web",
    tags=["collections"],
)
def static_export_addresses(collection_id: str, user_id: str = Depends(get_current_user_id)):
    """For each source a static export would hold: its own address, the address
    it will carry (`url`) and where that comes from (`from`: `own`, `link`,
    `repository` or `files`), plus the collection's base address for files."""
    from api.deps import require_collection_access
    from services.static_export import StaticExportError, addresses

    require_collection_access(collection_id, user_id, owner=True)
    try:
        return addresses(collection_id)
    except StaticExportError as e:
        if e.code == "not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put(
    "/api/collections/{collection_id}/export/static/addresses",
    summary="Save where a static export's sources are published",
    tags=["collections"],
)
def save_static_export_addresses(collection_id: str, request: StaticAddressesRequest,
                                 user_id: str = Depends(get_current_user_id)):
    """Owner only. Each address must be an https:// (or http://) address with no
    user name or password, or a path on the site starting with "/". One that is
    not refuses the whole request, so nothing is half saved."""
    from api.deps import require_collection_access
    from services.static_export import StaticExportError, addresses, save_addresses

    require_collection_access(collection_id, user_id, owner=True)
    fields = request.model_dump(exclude_unset=True)
    try:
        saved = save_addresses(collection_id,
                               files_base_url=fields["files_base_url"] if "files_base_url" in fields else ...,
                               site_url=fields["site_url"] if "site_url" in fields else ...,
                               documents=fields.get("documents"))
    except StaticExportError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    audit.record("collection.static_addresses", actor=user_id, collection_id=collection_id,
                 detail={"files_base_url": saved["files_base_url"], "site_url": saved["site_url"],
                         "documents": saved["documents"]})
    return addresses(collection_id)


class StaticAnswerRequest(BaseModel):
    """An answer to publish with a static export: it cites passages by id as
    [Source 1], [Source 2], ... in the order given."""
    question: str = Field(..., min_length=1, max_length=300)
    answer: str = Field(..., min_length=1, max_length=4000)
    citations: List[str] = Field(..., min_length=1, max_length=12)


class StaticAnswerApproval(BaseModel):
    approved: bool = True


def _answer_refusal(e) -> HTTPException:
    if e.code == "not_found":
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))


@router.get(
    "/api/collections/{collection_id}/export/static/answers",
    summary="Answers to publish with a static export, and whether each would be",
    tags=["collections"],
)
def static_export_answers(collection_id: str, user_id: str = Depends(get_current_user_id)):
    """Each answer with `status` (`ready`, `waiting`, `missing_passage`,
    `left_out_source`, `no_citation`, `check_failed`) and why, and the
    `threshold` a visitor's query must reach to be shown it."""
    from api.deps import require_collection_access
    from services.static_export import StaticExportError
    from services.static_export import answers

    require_collection_access(collection_id, user_id, owner=True)
    try:
        return answers.review(collection_id)
    except StaticExportError as e:
        raise _answer_refusal(e)


@router.post(
    "/api/collections/{collection_id}/export/static/answers",
    summary="Write an answer to publish with a static export",
    tags=["collections"],
    status_code=status.HTTP_201_CREATED,
)
def add_static_export_answer(collection_id: str, request: StaticAnswerRequest,
                             user_id: str = Depends(get_current_user_id)):
    """Owner only. The answer waits for approval before it can be published."""
    from api.deps import require_collection_access
    from services.static_export import StaticExportError
    from services.static_export import answers

    require_collection_access(collection_id, user_id, owner=True)
    try:
        row = answers.add_answer(collection_id, request.question, request.answer, request.citations, actor=user_id)
    except StaticExportError as e:
        raise _answer_refusal(e)
    audit.record("collection.static_answers", actor=user_id, collection_id=collection_id,
                 detail={"action": "added", "answer": row["id"], "citations": len(row["citations"])})
    return row


@router.post(
    "/api/collections/{collection_id}/export/static/answers/{answer_id}/approval",
    summary="Approve an answer for publishing, or take the approval back",
    tags=["collections"],
)
def approve_static_export_answer(collection_id: str, answer_id: str, request: StaticAnswerApproval,
                                 user_id: str = Depends(get_current_user_id)):
    from api.deps import require_collection_access
    from services.static_export import StaticExportError
    from services.static_export import answers

    require_collection_access(collection_id, user_id, owner=True)
    try:
        row = answers.approve_answer(collection_id, answer_id, actor=user_id, approved=request.approved)
    except StaticExportError as e:
        raise _answer_refusal(e)
    audit.record("collection.static_answers", actor=user_id, collection_id=collection_id,
                 detail={"action": "approved" if request.approved else "unapproved", "answer": answer_id})
    return row


@router.delete(
    "/api/collections/{collection_id}/export/static/answers/{answer_id}",
    summary="Remove an answer from those to publish",
    tags=["collections"],
)
def remove_static_export_answer(collection_id: str, answer_id: str, user_id: str = Depends(get_current_user_id)):
    from api.deps import require_collection_access
    from services.static_export import StaticExportError
    from services.static_export import answers

    require_collection_access(collection_id, user_id, owner=True)
    try:
        answers.remove_answer(collection_id, answer_id)
    except StaticExportError as e:
        raise _answer_refusal(e)
    audit.record("collection.static_answers", actor=user_id, collection_id=collection_id,
                 detail={"action": "removed", "answer": answer_id})
    return {"removed": answer_id}


@router.post(
    "/api/collections/import/preview",
    summary="Look inside a bundle before importing it",
    tags=["collections"],
)
def import_preview(file: UploadFile = File(...), user_id: str = Depends(get_current_user_id)):
    """
    Upload a `.clio.zip` and get back what it holds, which embedding model
    made it, and whether this server can reuse its vectors or must embed the
    text again. Nothing is created. The bundle is kept for an hour under
    `upload_id` so `POST /api/collections/import` can confirm without
    uploading it a second time.
    """
    import shutil
    import uuid

    from services.collection_bundle import BundleError, _work_dir, stage_bundle

    tmp = _work_dir() / f"import-{uuid.uuid4().hex}.zip"
    try:
        with open(tmp, "wb") as out:
            shutil.copyfileobj(file.file, out)
        upload_id, summary = stage_bundle(tmp)
        return {"upload_id": upload_id, **summary}
    except BundleError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    finally:
        tmp.unlink(missing_ok=True)


@router.delete(
    "/api/collections/import/preview/{upload_id}",
    summary="Discard a previewed bundle",
    tags=["collections"],
)
def discard_import_preview(upload_id: str, user_id: str = Depends(get_current_user_id)):
    from services.collection_bundle import BundleError, staged_path

    try:
        staged_path(upload_id).unlink(missing_ok=True)
    except BundleError:
        pass
    return {"discarded": True}


@router.post(
    "/api/collections/import",
    summary="Create a collection from an exported bundle",
    tags=["collections"],
    status_code=status.HTTP_201_CREATED,
)
def import_collection(  # sync: unpacks the bundle in the threadpool
    file: Optional[UploadFile] = File(None),
    upload_id: Optional[str] = Form(None),
    name: Optional[str] = Form(None),
    visibility: Optional[str] = Form(None),
    user_id: str = Depends(get_current_user_id),
):
    """
    Upload a `.clio.zip` from Export. Creates a new collection owned by the
    caller and returns it with `job_id` for the job that finishes it: the
    vectors are reused when this server embeds with exactly the model the
    bundle records (`vectors: "reused"`), otherwise re-embedded from the
    bundle's chunk text (`vectors: "re-embed"`) - no extraction or OCR
    either way.
    """
    import shutil
    import uuid

    from services import governance, storage_quota
    from services.collection_bundle import BundleError, _work_dir, import_collection as load, staged_path

    if not user_id and settings.private_collections:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sign in to import a collection — it needs an owner.",
        )
    governance.require_aup(user_id)

    if file is None and not upload_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Choose a .clio.zip to import.")
    try:
        if upload_id:
            tmp = staged_path(upload_id)  # previewed already: no second upload
        else:
            tmp = _work_dir() / f"import-{uuid.uuid4().hex}.zip"
            with open(tmp, "wb") as out:
                shutil.copyfileobj(file.file, out)
    except BundleError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    try:
        return load(
            tmp,
            owner_id=(
                settings.default_user_id
                if visibility == "team" or not user_id
                else user_id
            ),
            name=(name or "").strip() or None,
            actor=user_id,
        )
    except BundleError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except storage_quota.StorageLimitExceeded as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_detail())
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    finally:
        tmp.unlink(missing_ok=True)


@router.delete(
    "/api/collections/{collection_id}",
    summary="Delete a collection",
    tags=["collections"],
)
def delete_collection(collection_id: str, user_id: str = Depends(get_current_user_id)):  # sync: index teardown runs in the threadpool
    """
    Delete a collection and all its documents. Requires owner access.

    Note: The 'default' collection cannot be deleted.
    """
    from api.deps import require_collection_access
    require_collection_access(collection_id, user_id, owner=True)
    if collection_id == "default":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the default collection"
        )

    row = collection_service.get_collection(collection_id) or {}
    try:
        # Remove cached indexer first (before files are deleted)
        indexer_manager.remove_indexer(collection_id)

        success = collection_service.delete_collection(collection_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Collection '{collection_id}' not found"
            )

        audit.record("collection.delete", actor=user_id, collection_id=collection_id, detail={
            "team": (row.get("owner_id") or "").strip() in ("", settings.default_user_id),
            "sensitivity": row.get("sensitivity"),
        })
        return {"message": f"Collection '{collection_id}' deleted", "success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete collection '{collection_id}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete collection: {str(e)}"
        )


# ── Retrieval coverage ──────────────────────────────────────────────────────
# How findable is this collection's content through the real search path?
# See services/coverage_report.py. Reading the report is read access
# (get_indexer enforces it); running it costs CPU and owners act on the
# result, so running and the owner-question editor are owner-gated.

class CoverageRunRequest(BaseModel):
    probes: int = Field(default=100, ge=1, le=300, description="Chunks to sample (capped at 300)")
    seed: Optional[int] = Field(default=None, description="Sampling seed; the same seed repeats the same probes")


class CoverageQuestionRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    expected_documents: list[str] = Field(..., min_length=1, max_length=20,
                                          description="Document ids or exact filenames that should answer it")


def _coverage_payload(indexer, collection_id: str) -> dict:
    from services.coverage_report import coverage_runner, latest_report

    store = indexer.vector_store.metadata_store
    return {
        "collection_id": collection_id,
        "run": coverage_runner.status(collection_id),
        "report": latest_report(indexer),
        "questions": store.list_coverage_questions(),
        "llm_questions": bool(settings.coverage_llm_questions),
    }


@router.get(
    "/api/collections/{collection_id}/coverage",
    summary="Latest retrieval coverage report",
    tags=["collections"],
)
def get_collection_coverage(collection_id: str, user_id: str = Depends(get_current_user_id)):
    """The latest coverage report (null before the first run), whether a run is
    in progress, and the owner's questions. ``report.stale`` is true once the
    collection's sources or embedding model changed since the run."""
    from api.deps import get_indexer
    return _coverage_payload(get_indexer(collection_id), collection_id)


@router.post(
    "/api/collections/{collection_id}/coverage",
    summary="Run a retrieval coverage report",
    tags=["collections"],
    status_code=status.HTTP_202_ACCEPTED,
)
def start_collection_coverage(collection_id: str, body: Optional[CoverageRunRequest] = None,
                              user_id: str = Depends(get_current_user_id)):
    """Start a bounded background run (owner only). 409 while one is already
    running for this collection. Poll ``GET .../coverage`` for the result."""
    from api.deps import get_indexer, require_collection_access
    from services import audit
    from services.coverage_report import (
        DEFAULT_PROBES, DEFAULT_SEED, CoverageAlreadyRunning, coverage_runner)

    require_collection_access(collection_id, user_id, owner=True)
    indexer = get_indexer(collection_id)
    body = body or CoverageRunRequest()
    probes = body.probes if body.probes else DEFAULT_PROBES
    seed = body.seed if body.seed is not None else DEFAULT_SEED
    try:
        run = coverage_runner.start(collection_id, indexer, probes=probes, seed=seed)
    except CoverageAlreadyRunning as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    audit.record("coverage.run", actor=user_id, collection_id=collection_id,
                 detail={"probes": probes, "seed": seed})
    return {"collection_id": collection_id, "run": run}


@router.post(
    "/api/collections/{collection_id}/coverage/questions",
    summary="Add an owner question to the coverage report",
    tags=["collections"],
    status_code=status.HTTP_201_CREATED,
)
def add_coverage_question(collection_id: str, body: CoverageQuestionRequest,
                          user_id: str = Depends(get_current_user_id)):
    """Add a question and the document(s) that should answer it (owner only)."""
    from api.deps import get_indexer, require_collection_access
    from services import audit
    from services.coverage_report import QuestionError, add_question

    require_collection_access(collection_id, user_id, owner=True)
    indexer = get_indexer(collection_id)
    try:
        created = add_question(indexer, body.question, body.expected_documents, user_id)
    except QuestionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit.record("coverage.question_add", actor=user_id, collection_id=collection_id,
                 detail={"question_id": created["id"],
                         "documents": [d["document_id"] for d in created["expected_documents"]]})
    return created


@router.delete(
    "/api/collections/{collection_id}/coverage/questions/{question_id}",
    summary="Delete an owner question",
    tags=["collections"],
)
def delete_coverage_question(collection_id: str, question_id: int,
                             user_id: str = Depends(get_current_user_id)):
    """Remove an owner question (owner only)."""
    from api.deps import get_indexer, require_collection_access
    from services import audit

    require_collection_access(collection_id, user_id, owner=True)
    indexer = get_indexer(collection_id)
    if not indexer.vector_store.metadata_store.delete_coverage_question(question_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such question")
    audit.record("coverage.question_delete", actor=user_id, collection_id=collection_id,
                 detail={"question_id": question_id})
    return {"deleted": question_id}

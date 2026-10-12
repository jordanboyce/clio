"""Collection CRUD and re-indexing endpoints."""

import json
import logging

from typing import Optional

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile, status

from config import settings
from services.config_manager import config_manager
from services.reindex_service import reindex_service
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
async def start_collection_reindex(collection_id: str, user_id: str = Depends(get_current_user_id)):
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

        # Start re-indexing (the job resolves provider + dimension itself)
        job_id = await reindex_service.start_collection_reindex(
            collection_id=collection_id,
            documents_dir=documents_dir,
            indexes_dir=indexes_dir,
            embedding_model=collection["embedding_model"],
            chunk_size=collection["chunk_size"],
            chunk_overlap=collection["chunk_overlap"],
        )

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


@router.get(
    "/api/reindex/status",
    summary="Get re-indexing job status",
    tags=["admin"],
)
async def get_reindex_status(job_id: int = None):
    """
    Get status of a re-indexing job.

    Args:
        job_id: Optional job ID. If not provided, returns latest job.

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
    else:
        # Get latest job (including completed ones), not just currently active
        job = app_db.get_latest_reindex_job()

    if not job:
        return None

    # Calculate progress percentage
    if job["total_documents"] > 0:
        progress = (job["processed_documents"] / job["total_documents"]) * 100
    else:
        progress = 0

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

    return {
        **job,
        "collection_id": collection_id,
        "progress_percent": round(progress, 1)
    }


# AI Preferences endpoints
# Collection endpoints
@router.get(
    "/api/collections",
    summary="List all collections",
    tags=["collections"],
)
async def list_collections(request: Request, user_id: str = Depends(get_current_user_id)):
    """Get all document collections visible to the current user."""
    collections = collection_service.get_all_collections(user_id=user_id)
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
async def create_collection(collection_data: dict, user_id: str = Depends(get_current_user_id)):
    """
    Create a new document collection owned by the current user.

    Body:
        name: Collection name (required)
        description: Collection description
        color: Hex color for UI (default: #3b82f6)
        chunk_size: Text chunk size (default: 500)
        chunk_overlap: Chunk overlap (default: 50)
        embedding_model: Embedding model to use
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

    collection = collection_service.create_collection(
        name=name,
        description=collection_data.get("description", ""),
        color=collection_data.get("color", "#3b82f6"),
        chunk_size=collection_data.get("chunk_size", 500),
        chunk_overlap=collection_data.get("chunk_overlap", 50),
        embedding_model=collection_data.get("embedding_model", "all-MiniLM-L6-v2"),
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
    "/api/collections/{collection_id}",
    summary="Get collection details",
    tags=["collections"],
)
async def get_collection(collection_id: str, user_id: str = Depends(get_current_user_id)):
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
async def get_collection_stats(collection_id: str):
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
async def update_collection(collection_id: str, updates: dict, user_id: str = Depends(get_current_user_id)):
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

            if is_restricted(collection_row):
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
                    status_code=status.HTTP_400_BAD_REQUEST,
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
    )

    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection '{collection_id}' not found"
        )

    return collection


@router.post(
    "/api/collections/{collection_id}/clone",
    summary="Clone a collection you can read into one you own",
    tags=["collections"],
    status_code=status.HTTP_201_CREATED,
)
async def clone_collection(collection_id: str, body: dict = None, user_id: str = Depends(get_current_user_id)):
    """
    Copy a collection's sources into a new collection owned by the caller.

    Read access is enough — that is the point. Someone reading a published
    collection cannot change how it is built, so cloning is how they get a
    copy they can rechunk, re-embed and add to. The copy starts unpublished.

    Body:
        name: Name for the copy (default: "Copy of <source name>")

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
        )
    except storage_quota.StorageLimitExceeded as e:
        raise HTTPException(status_code=413, detail=e.to_detail())
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
        raise HTTPException(status_code=413, detail=e.to_detail())
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

    try:
        # Remove cached indexer first (before files are deleted)
        indexer_manager.remove_indexer(collection_id)

        success = collection_service.delete_collection(collection_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Collection '{collection_id}' not found"
            )

        return {"message": f"Collection '{collection_id}' deleted", "success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete collection '{collection_id}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete collection: {str(e)}"
        )

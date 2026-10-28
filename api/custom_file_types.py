"""Admin API for custom file types (JSON-like files with product-specific extensions).

Reading the list is open to anyone who can use the app, because the upload
dialog needs to know which extensions are accepted. Everything that changes
what the server will read, or sends a sample to a model, is admin-only.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from api import deps
from api.deps import require_admin
from middleware.user_context import get_current_user_id
from services import audit
from services import custom_file_types as cft
from services import custom_file_type_suggest as suggest_mod
from services import custom_file_type_usage as usage_mod
from services.ai_service import resolve_default_provider
from services.collection_service import collection_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/custom-file-types", tags=["custom-file-types"])

SAMPLE_LIMIT = 2 * 1024 * 1024


class ProfileBody(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    format: Optional[str] = None
    kind: Optional[str] = None
    record_path: Optional[str] = None
    title_field: Optional[str] = None
    include: Optional[list] = None
    exclude: Optional[list] = None
    labels: Optional[Dict[str, Any]] = None
    records_per_section: Optional[int] = None
    max_value_chars: Optional[int] = None
    enabled: Optional[bool] = None
    source: Optional[str] = None


class PreviewBody(BaseModel):
    extension: str
    sample: str
    profile: ProfileBody


def _bad_request(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


def _public(profile: Dict[str, Any]) -> Dict[str, Any]:
    return profile


def _ai_available() -> bool:
    """Whether a chat model is configured for the suggestion's optional AI pass.

    Asks the same resolver the suggestion uses (the deployment's default chat
    provider, air-gapped mode respected), so the answer matches what ``suggest``
    will do. Nothing is sent anywhere.
    """
    try:
        provider, _model = resolve_default_provider(purpose="Custom file type availability check")
    except Exception:
        return False
    return provider is not None


def _usage(extension: str, user_id: Optional[str]) -> Dict[str, Any]:
    """Per-collection usage over the collections this caller can see."""
    visible = collection_service.get_all_collections(user_id=user_id)
    return usage_mod.usage_by_collection(extension, visible, deps.get_indexer)


def _usage_or_none(extension: str, user_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Usage for a response whose main work already succeeded: never fails it."""
    try:
        return _usage(extension, user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Could not count files for {extension}: {e}")
        return None


@router.get("")
async def list_custom_file_types():
    """Registered types, so the UI can show and accept their extensions."""
    return {
        "types": [_public(p) for p in cft.registry.all()],
        # Whether the suggestion's optional AI pass has a model to use.
        "ai": {"available": _ai_available()},
    }


@router.get("/{extension}/usage")
def custom_file_type_usage(extension: str, user_id: str = Depends(get_current_user_id)):
    """How many indexed files use this extension, per collection (admin only).

    Counts only collections the caller can see, and only servable documents.
    ``can_reindex`` says whether the caller owns that collection (the scoped
    re-index is an owner action). Works for a type that has since been
    deleted, since its files stay indexed.
    """
    require_admin("manage custom file types")
    try:
        return _usage(extension, user_id)
    except cft.ProfileError as e:
        raise _bad_request(e)


@router.put("/{extension}")
def save_custom_file_type(extension: str, body: ProfileBody, user_id: str = Depends(get_current_user_id)):
    """Create or replace the profile for one extension (admin only).

    The profile is replaced as a whole: send every field you want to keep.
    ``reading_changed`` says whether this save changes how files are read; when
    it does, ``usage`` lists, per collection, the indexed files that should be
    re-indexed to pick it up.
    """
    admin = require_admin("manage custom file types")
    try:
        profile = cft.registry.put(
            extension, body.model_dump(exclude_none=True), user=admin or "")
    except cft.ProfileError as e:
        raise _bad_request(e)
    changed = profile["reading_updated_at"] == profile["updated_at"]
    audit.record("custom_file_type.save", target=profile["extension"],
                 detail={"label": profile["label"], "enabled": profile["enabled"],
                         "source": profile["source"], "reading_changed": changed})
    usage = _usage_or_none(profile["extension"], user_id)
    return {
        "profile": _public(profile),
        "reading_changed": changed,
        "affected_documents": usage["total_documents"] if usage else None,
        "usage": usage,
    }


@router.delete("/{extension}")
def delete_custom_file_type(extension: str, user_id: str = Depends(get_current_user_id)):
    """Stop accepting an extension (admin only). Already-indexed files are kept.

    The files stay searchable but are no longer a supported type, so a scoped
    re-index of them is refused until the type is registered again (it would
    drop them from the collection).
    """
    require_admin("manage custom file types")
    try:
        removed = cft.registry.delete(extension)
    except cft.ProfileError as e:
        raise _bad_request(e)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No custom file type {extension}")
    ext = cft.normalize_extension(extension)
    audit.record("custom_file_type.delete", target=ext)
    usage = _usage_or_none(ext, user_id)
    return {
        "deleted": True,
        "affected_documents": usage["total_documents"] if usage else None,
        "usage": usage,
    }


@router.post("/preview")
async def preview_custom_file_type(body: PreviewBody):
    """Show what a draft profile would index from a sample, without saving it (admin only)."""
    require_admin("manage custom file types")
    if len(body.sample.encode("utf-8", "ignore")) > SAMPLE_LIMIT:
        raise HTTPException(status_code=413, detail="Sample is larger than 2 MB; use a smaller excerpt")
    try:
        profile = cft.validate_profile(body.extension, body.profile.model_dump(exclude_none=True))
        data = cft.parse_lenient(body.sample, profile["format"]) if profile["format"] != "text" else body.sample
        pages, count = cft.render_with_count(data, profile)
    except cft.ProfileError as e:
        raise _bad_request(e)
    ordered = [pages[k] for k in sorted(pages)]
    text = "\n\n---\n\n".join(ordered[:2])
    return {
        "profile": profile,
        "preview": {
            "records": count, "sections": len(pages), "chars": sum(len(p) for p in ordered),
            "text": text[:suggest_mod.PREVIEW_CHARS] + ("…" if len(text) > suggest_mod.PREVIEW_CHARS else ""),
        },
    }


@router.post("/suggest")
async def suggest_custom_file_type(
    extension: str = Form(...),
    use_ai: bool = Form(True),
    file: UploadFile = File(...),
):
    """Propose a profile from a sample file (admin only).

    With `use_ai` the excerpt sent to the model is bounded and the model's answer
    is checked against the sample; any failure falls back to a rule-based proposal.
    Nothing is saved; the admin previews and saves it.
    """
    require_admin("manage custom file types")
    raw = await file.read(SAMPLE_LIMIT + 1)
    if len(raw) > SAMPLE_LIMIT:
        raise HTTPException(status_code=413, detail="Sample is larger than 2 MB; upload a smaller excerpt")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    try:
        result = suggest_mod.suggest(text, extension, use_ai=use_ai)
    except cft.ProfileError as e:
        raise _bad_request(e)
    audit.record("custom_file_type.suggest", target=result["extension"],
                 detail={"source": result["source"], "ai_requested": use_ai})
    return result

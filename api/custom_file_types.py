"""Admin API for custom file types (JSON-like files with product-specific extensions).

Reading the list is open to anyone who can use the app, because the upload
dialog needs to know which extensions are accepted. Everything that changes
what the server will read, or sends a sample to a model, is admin-only.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from api.deps import require_admin
from services import audit
from services import custom_file_types as cft
from services import custom_file_type_suggest as suggest_mod

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


@router.get("")
async def list_custom_file_types():
    """Registered types, so the UI can show and accept their extensions."""
    return {"types": [_public(p) for p in cft.registry.all()]}


@router.put("/{extension}")
async def save_custom_file_type(extension: str, body: ProfileBody):
    """Create or replace the profile for one extension (admin only)."""
    admin = require_admin("manage custom file types")
    try:
        # Get count of documents with this extension before saving
        from services.app_database import app_db
        affected_count = app_db.count_documents_by_extension(extension)
        
        profile = cft.registry.put(
            extension, body.model_dump(exclude_none=True), user=admin or "")
    except cft.ProfileError as e:
        raise _bad_request(e)
    audit.record("custom_file_type.save", target=profile["extension"],
                 detail={"label": profile["label"], "enabled": profile["enabled"]})
    return {"profile": _public(profile), "affected_documents": affected_count}


@router.delete("/{extension}")
async def delete_custom_file_type(extension: str):
    """Stop accepting an extension (admin only). Already-indexed files are kept."""
    require_admin("manage custom file types")
    try:
        # Get count of documents with this extension before deleting
        from services.app_database import app_db
        affected_count = app_db.count_documents_by_extension(extension)
        
        removed = cft.registry.delete(extension)
    except cft.ProfileError as e:
        raise _bad_request(e)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No custom file type {extension}")
    audit.record("custom_file_type.delete", target=cft.normalize_extension(extension))
    return {"deleted": True, "affected_documents": affected_count}


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

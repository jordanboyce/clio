"""Expertise pack endpoints.

Packs are global, admin-curated text that is placed in the prompt of every
collection they are attached to. Changing one changes how other people's
collections answer, so writing a pack needs an admin (``require_admin``: a
no-op on an open appliance, fail-closed once private collections are on), and
choosing which packs a collection uses is a configuration change reserved to
its owner. Both are audited, by id and name and never by body.
"""

import logging

from fastapi import HTTPException

from models.schemas import (
    ExpertisePack,
    ExpertisePackCreate,
    ExpertisePackUpdate,
    CollectionExpertiseResponse,
    SetCollectionExpertiseRequest,
)

from fastapi import APIRouter
from api.deps import (
    expertise_store,
    require_admin,
    require_collection_access,
)
from middleware.user_context import get_request_user
from services import audit, authored_text

logger = logging.getLogger(__name__)

router = APIRouter()


def _check_pack_lengths(name, description, body) -> None:
    try:
        authored_text.check_length("pack name", name, authored_text.MAX_NAME_CHARS)
        authored_text.check_length("pack description", description, authored_text.MAX_PACK_DESCRIPTION_CHARS)
        authored_text.check_length("pack body", body, authored_text.MAX_PACK_BODY_CHARS)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _with_warning(pack: ExpertisePack) -> ExpertisePack:
    """The save response: the pack, plus what the editor should tell the author."""
    pack.warning = authored_text.warning_for_author(pack.flag, "pack")
    return pack


def _flag_detail(pack: ExpertisePack) -> dict:
    flag = pack.flag or {}
    if not flag:
        return {"flagged": False}
    return {"flagged": True, "flag_level": flag.get("level"), "flag_categories": flag.get("categories")}


@router.get("/api/expertise/packs", response_model=list[ExpertisePack], tags=["expertise"])
async def list_expertise_packs():
    """Return all expertise packs ordered by name."""
    return expertise_store.list_packs()


@router.post("/api/expertise/packs", response_model=ExpertisePack, status_code=201, tags=["expertise"])
async def create_expertise_pack(data: ExpertisePackCreate):
    """Create a new expertise pack. Admin only; audited."""
    require_admin("manage saved instructions")
    _check_pack_lengths(data.name, data.description, data.body)
    pack = expertise_store.create_pack(data)
    audit.record(
        "expertise.create", target=pack.id,
        detail={"name": pack.name, "chars": len(pack.body), "digest": authored_text.digest(pack.body),
                **_flag_detail(pack)},
    )
    return _with_warning(pack)


@router.get("/api/expertise/packs/{pack_id}", response_model=ExpertisePack, tags=["expertise"])
async def get_expertise_pack(pack_id: str):
    """Return a single expertise pack by ID."""
    pack = expertise_store.get_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Expertise pack not found")
    return pack


@router.put("/api/expertise/packs/{pack_id}", response_model=ExpertisePack, tags=["expertise"])
async def update_expertise_pack(pack_id: str, data: ExpertisePackUpdate):
    """Partially update an expertise pack. Admin only; audited."""
    require_admin("manage saved instructions")
    _check_pack_lengths(data.name, data.description, data.body)
    before = expertise_store.get_pack(pack_id)
    pack = expertise_store.update_pack(pack_id, data)
    if pack is None or before is None:
        raise HTTPException(status_code=404, detail="Expertise pack not found")
    audit.record(
        "expertise.update", target=pack.id,
        detail={"name": pack.name, "chars": len(pack.body),
                "digest": authored_text.digest(pack.body), "previous_digest": authored_text.digest(before.body),
                "renamed_from": before.name if before.name != pack.name else None,
                "attached_to": len(expertise_store.collections_using(pack_id)),
                **_flag_detail(pack)},
    )
    return _with_warning(pack)


@router.delete("/api/expertise/packs/{pack_id}", status_code=204, tags=["expertise"])
async def delete_expertise_pack(pack_id: str):
    """Delete an expertise pack (also removes all collection attachments). Admin only; audited."""
    require_admin("manage saved instructions")
    pack = expertise_store.get_pack(pack_id)
    attached = expertise_store.collections_using(pack_id)
    deleted = expertise_store.delete_pack(pack_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Expertise pack not found")
    audit.record(
        "expertise.delete", target=pack_id,
        detail={"name": pack.name if pack else None, "was_attached_to": len(attached)},
    )


@router.get(
    "/api/collections/{collection_id}/expertise",
    response_model=CollectionExpertiseResponse,
    tags=["expertise"],
)
async def get_collection_expertise(collection_id: str):
    """List all expertise packs attached to a collection."""
    require_collection_access(collection_id, get_request_user())
    packs = expertise_store.get_packs_for_collection(collection_id)
    return CollectionExpertiseResponse(collection_id=collection_id, packs=packs)


@router.put(
    "/api/collections/{collection_id}/expertise",
    response_model=CollectionExpertiseResponse,
    tags=["expertise"],
)
async def set_collection_expertise(collection_id: str, data: SetCollectionExpertiseRequest):
    """Replace the full set of expertise packs attached to a collection.

    Owner only (like the guide): the attached packs decide how the collection
    answers, so a read-write collaborator, who may add sources, may not change
    them. Audited with the pack ids and names before and after.
    """
    require_collection_access(collection_id, get_request_user(), owner=True)
    pack_ids = list(dict.fromkeys(data.pack_ids))
    known = {p.id: p for p in expertise_store.list_packs()}
    unknown = [pid for pid in pack_ids if pid not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown expertise pack(s): {', '.join(unknown)}")
    before = expertise_store.get_attached_pack_ids(collection_id)
    expertise_store.set_packs_for_collection(collection_id, pack_ids)
    packs = expertise_store.get_packs_for_collection(collection_id)
    if set(before) != set(pack_ids):
        audit.record(
            "collection.expertise", collection_id=collection_id,
            detail={
                "before": [{"id": i, "name": known[i].name if i in known else None} for i in before],
                "after": [{"id": p.id, "name": p.name, "flagged": bool(p.flag)} for p in packs],
            },
        )
    return CollectionExpertiseResponse(collection_id=collection_id, packs=packs)

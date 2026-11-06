"""Admin endpoints for registered apps: who may call /api/v1, and with what."""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, StrictBool

from api.deps import require_admin
from services import api_apps, audit

router = APIRouter()

_MAX_BUDGET = 1_000_000


class _Budgets(BaseModel):
    # Requests per minute. Omitted = the deployment's limit; 0 = unlimited.
    search_per_minute: Optional[int] = Field(None, ge=0, le=_MAX_BUDGET)
    ask_per_minute: Optional[int] = Field(None, ge=0, le=_MAX_BUDGET)
    other_per_minute: Optional[int] = Field(None, ge=0, le=_MAX_BUDGET)


class AppCreate(_Budgets):
    name: str = Field(..., min_length=1, max_length=64)
    collections: List[str] = Field(..., min_length=1, max_length=500)
    can_write: StrictBool = False
    # The `sub` of a client-credentials token from the site's IdP.
    oidc_subject: Optional[str] = Field(None, max_length=255)
    # False for an app that only ever presents an IdP token.
    issue_key: StrictBool = True
    expires_in_days: Optional[int] = Field(None, ge=1, le=api_apps.MAX_EXPIRY_DAYS)


class AppUpdate(_Budgets):
    collections: Optional[List[str]] = Field(None, min_length=1, max_length=500)
    can_write: Optional[StrictBool] = None
    oidc_subject: Optional[str] = Field(None, max_length=255)
    expires_in_days: Optional[int] = Field(None, ge=1, le=api_apps.MAX_EXPIRY_DAYS)


class KeyRotation(BaseModel):
    # How long the old key keeps working. 0 ends it now, for a key that leaked.
    grace_hours: int = Field(api_apps.DEFAULT_ROTATION_GRACE_HOURS, ge=0, le=api_apps.MAX_ROTATION_GRACE_HOURS)


def _require_known_collections(collections: List[str]) -> None:
    from services.collection_service import collection_service

    for collection_id in collections:
        if not collection_service.get_collection(collection_id):
            raise HTTPException(status_code=404, detail=f"Collection '{collection_id}' not found")


def _not_found(app_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"App '{app_id}' not found")


@router.get("/api/admin/apps", summary="Registered apps", tags=["admin"])
def list_apps():
    require_admin("view registered apps")
    return {"apps": [api_apps.public_view(app) for app in api_apps.list_apps()]}


@router.post(
    "/api/admin/apps",
    summary="Register an app",
    tags=["admin"],
    status_code=status.HTTP_201_CREATED,
)
def create_app(body: AppCreate):
    """Returns the key once, when one was issued; it cannot be recovered."""
    admin = require_admin("register apps")
    _require_known_collections(body.collections)
    try:
        created = api_apps.create_app(
            body.name, body.collections, can_write=body.can_write, oidc_subject=body.oidc_subject,
            issue_key=body.issue_key, expires_in_days=body.expires_in_days,
            search_per_minute=body.search_per_minute, ask_per_minute=body.ask_per_minute,
            other_per_minute=body.other_per_minute, created_by=admin,
        )
    except api_apps.AppConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit.record("api_app.create", actor=admin, target=created["id"], detail={
        "name": created["name"], "collections": created["collections"], "can_write": created["can_write"],
        "oidc_subject": created["oidc_subject"], "expires_at": created["expires_at"],
    })
    return created


@router.patch("/api/admin/apps/{app_id}", summary="Change what an app may do", tags=["admin"])
def update_app(app_id: str, body: AppUpdate):
    """Takes effect on the app's next request. A field left out stays as it is."""
    admin = require_admin("change registered apps")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("collections"):
        _require_known_collections(changes["collections"])
    try:
        updated = api_apps.update_app(app_id, changes)
    except api_apps.AppConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if updated is None:
        raise _not_found(app_id)
    audit.record("api_app.update", actor=admin, target=app_id, detail={"name": updated["name"], "changes": changes})
    return updated


@router.post("/api/admin/apps/{app_id}/rotate-key", summary="Issue a new key", tags=["admin"])
def rotate_key(app_id: str, body: Optional[KeyRotation] = None):
    """Returns the new key once. The old one works until its grace ends."""
    admin = require_admin("rotate app keys")
    grace = (body or KeyRotation()).grace_hours
    try:
        rotated = api_apps.rotate_key(app_id, grace)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if rotated is None:
        raise _not_found(app_id)
    audit.record("api_app.rotate_key", actor=admin, target=app_id, detail={"name": rotated["name"], "grace_hours": grace})
    return rotated


@router.delete("/api/admin/apps/{app_id}", summary="Revoke an app", tags=["admin"])
def revoke_app(app_id: str):
    """Permanent, and takes effect at once. The record stays for the audit trail."""
    admin = require_admin("revoke apps")
    revoked = api_apps.revoke_app(app_id)
    if revoked is None:
        raise _not_found(app_id)
    audit.record("api_app.revoke", actor=admin, target=app_id, detail={"name": revoked["name"]})
    return revoked

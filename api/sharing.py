"""User and collection-sharing endpoints."""

import logging

from fastapi import Depends, HTTPException, Request, status

from config import settings
from services.sharing_service import sharing_service
from middleware.user_context import get_current_user_id

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/user/me", summary="Get current user info", tags=["users"])
async def get_current_user(
    request: Request, user_id: str = Depends(get_current_user_id)
):
    """Get the current user's identity and private-collections status.

    In private-collections mode, user_id is the verified identity (a built-in
    account's email, an OIDC subject or a proxy-vouched name); None means an
    anonymous (password-authenticated) caller who can reach team collections
    only.
    """
    from services.admins import admin_gate_active, is_admin
    from services.app_database import app_db
    from services import governance
    user = app_db.get_user(user_id) if user_id else None
    identity = getattr(request.state, "auth_identity", None)
    admin_identity = identity or user_id
    return {
        "user_id": user_id,
        "display_name": (user["display_name"] if user else user_id) or "anonymous",
        "private_collections": settings.private_collections,
        "db_backend": settings.db_backend,
        "is_admin": is_admin(admin_identity),
        # Whether to show the Admin tab (usage, content review, audit).
        # An open appliance shows it to everyone; once an operator list and
        # an identity provider exist it narrows to admins.
        "admin_console": is_admin(admin_identity) or (
            not settings.private_collections and not admin_gate_active()
        ),
        # Governance: whether the UI must show the acceptable-use modal
        # before this person adds sources, and what the scanner does.
        "aup": governance.aup_payload(user_id),
        "content_policy_action": settings.content_policy_action,
        "sensitivity_levels": list(governance.SENSITIVITY_LEVELS),
        # The per-collection storage cap, so the UI can show it without a
        # second round-trip (0 = unlimited).
        "collection_storage_limit_bytes": settings.collection_storage_limit_bytes,
    }


@router.post(
    "/api/collections/{collection_id}/share",
    summary="Create a share link for a collection",
    tags=["sharing"],
    status_code=status.HTTP_201_CREATED,
)
async def create_share(collection_id: str, body: dict, request: Request, user_id: str = Depends(get_current_user_id)):
    """
    Generate a shareable link for a collection.

    Body:
        permission: 'read' or 'readwrite' (default: 'read')
        expires_days: Optional number of days until expiry (null = never)
        notify_email: Optional recipient — email them the token and a join
            link via Resend (requires RESEND_API_KEY). The share is created
            either way; the response reports email_sent / email_error.

    """
    notify_email = (body.get("notify_email") or "").strip()
    try:
        share = sharing_service.create_share(
            collection_id=collection_id,
            owner_id=user_id,
            permission=body.get("permission", "read"),
            expires_days=body.get("expires_days"),
            invited_email=notify_email or None,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    from services import audit
    audit.record("share.create", actor=user_id, collection_id=collection_id,
                 target=notify_email or None,
                 detail={"share_id": share.get("share_id"), "permission": share.get("permission"),
                         "expires_at": share.get("expires_at")})

    if notify_email:
        from services.collection_service import collection_service
        from services.share_email import send_share_email

        collection = collection_service.get_collection(collection_id) or {}
        try:
            send_share_email(
                notify_email,
                share_token=share["share_id"],
                collection_name=collection.get("name", collection_id),
                permission=share["permission"],
                shared_by=user_id or "A teammate",
                app_url=str(request.base_url),
                expires_at=share.get("expires_at"),
            )
            share["email_sent"] = True
        except Exception as e:
            logger.warning(f"Share invitation email to {notify_email} failed: {e}")
            share["email_sent"] = False
            share["email_error"] = str(e)
    return share


@router.post(
    "/api/collections/{collection_id}/shares/bulk",
    summary="Invite several people to a collection at once",
    tags=["sharing"],
    status_code=status.HTTP_201_CREATED,
)
async def create_shares_bulk(collection_id: str, body: dict, request: Request, user_id: str = Depends(get_current_user_id)):
    """
    One share + emailed invitation per address, with per-address results.

    Body:
        emails: List of recipient addresses (deduplicated, max 100 per call)
        permission: 'read' or 'readwrite' (default 'read')
        expires_days: Optional expiry applied to every share

    """
    from services.app_database import app_db
    from services.share_email import send_share_email

    raw = body.get("emails") or []
    emails, seen = [], set()
    for e in raw:
        addr = (e or "").strip().lower()
        if addr and "@" in addr and addr not in seen:
            seen.add(addr)
            emails.append(addr)
    if not emails:
        raise HTTPException(status_code=400, detail="No valid email addresses given")
    if len(emails) > 100:
        raise HTTPException(status_code=400, detail="At most 100 invitations per call")

    permission = body.get("permission", "read")
    expires_days = body.get("expires_days")

    # Verify ownership once up front (create_share would also catch it, but
    # failing before any work keeps the batch all-or-nothing on auth).
    collection = app_db.get_collection(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail=f"Collection {collection_id} not found")
    if collection.get("owner_id") != user_id:
        raise HTTPException(status_code=403, detail="Only the collection owner can create shares")

    results = []
    collection_name = collection.get("name", collection_id)
    for addr in emails:
        entry = {"email": addr}
        try:
            share = sharing_service.create_share(
                collection_id=collection_id,
                owner_id=user_id,
                permission=permission,
                expires_days=expires_days,
                invited_email=addr,
            )
            entry["share_id"] = share["share_id"]
        except Exception as e:
            entry["error"] = str(e)
            results.append(entry)
            continue

        try:
            send_share_email(
                addr,
                share_token=share["share_id"],
                collection_name=collection_name,
                permission=permission,
                shared_by=user_id or "A teammate",
                app_url=str(request.base_url),
                expires_at=share.get("expires_at"),
            )
            entry["email_sent"] = True
        except Exception as e:
            entry["email_sent"] = False
            entry["email_error"] = str(e)
        results.append(entry)

    return {"results": results}


@router.get(
    "/api/collections/{collection_id}/shares",
    summary="List shares for a collection",
    tags=["sharing"],
)
async def list_collection_shares(collection_id: str, user_id: str = Depends(get_current_user_id)):
    """Get all active share links for a collection you own."""
    try:
        shares = sharing_service.get_shares_for_collection(collection_id, user_id)
        return {"shares": shares}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post(
    "/api/shares/{share_token}/accept",
    summary="Accept a share link",
    tags=["sharing"],
)
async def accept_share(share_token: str, user_id: str = Depends(get_current_user_id)):
    """Accept a share link to gain access to a collection."""
    try:
        result = sharing_service.accept_share(share_token, user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    from services import audit
    audit.record("share.accept", actor=user_id,
                 collection_id=(result or {}).get("collection_id"),
                 detail={"share_id": share_token, "permission": (result or {}).get("permission")})
    return result


@router.delete(
    "/api/shares/{share_id}",
    summary="Revoke a share link",
    tags=["sharing"],
)
async def revoke_share(share_id: str, user_id: str = Depends(get_current_user_id)):
    """Revoke a share link you created."""
    try:
        share = sharing_service.revoke_share(share_id, user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    from services import audit
    audit.record("share.revoke", actor=user_id,
                 collection_id=(share or {}).get("collection_id"),
                 target=(share or {}).get("invited_email"),
                 detail={"share_id": share_id})

    return {"message": "Share revoked", "success": True}

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

    In private-collections mode, user_id is the verified Cloudflare Access
    identity; None means an anonymous (password-authenticated) caller who can
    reach team collections only.
    """
    from services.access_provisioning import access_provisioning_enabled, admin_emails, is_admin
    from services.app_database import app_db
    from services import governance
    user = app_db.get_user(user_id) if user_id else None
    identity = getattr(request.state, "auth_identity", None)
    has_admin_allowlist = bool(admin_emails())
    admin_identity = identity if has_admin_allowlist else user_id
    return {
        "user_id": user_id,
        "display_name": (user["display_name"] if user else user_id) or "anonymous",
        "private_collections": settings.private_collections,
        "db_backend": settings.db_backend,
        # Whether inviting someone by email can also admit them at the
        # Cloudflare Access edge — the share dialog says so plainly rather
        # than letting an owner send an invitation that dead-ends at login.
        "edge_admission": access_provisioning_enabled(),
        "is_admin": is_admin(admin_identity),
        # Whether to show the Admin tab (usage, content review, audit).
        # Shared-collection deployments remain open when no operator list is
        # configured; an explicit ADMIN_EMAILS list narrows the console even
        # in shared mode, matching the backend gate. Edge admission always
        # requires an explicit admin.
        "admin_console": is_admin(admin_identity) or (
            not settings.private_collections and not has_admin_allowlist
        ),
        # Governance: whether the UI must show the acceptable-use modal
        # before this person adds sources, and what the scanner does.
        "aup": governance.aup_payload(user_id),
        "content_policy_action": settings.content_policy_action,
        "sensitivity_levels": list(governance.SENSITIVITY_LEVELS),
        # Online registration: the mode the /register page runs in (off
        # unless edge admission is configured) and, for admins, how many
        # requests are waiting so the Access panel can show a badge.
        "registration_mode": (
            settings.registration_mode if access_provisioning_enabled() else "off"
        ),
        "pending_registrations": (
            app_db.count_registration_requests("pending") if is_admin(admin_identity) else 0
        ),
        # The per-collection storage cap, so the UI can show it without a
        # second round-trip (0 = unlimited).
        "collection_storage_limit_bytes": settings.collection_storage_limit_bytes,
    }


# ── Edge admission administration ────────────────────────────────────────
# Who can reach the deployment at all is worth seeing in one place rather
# than inferring it from the Cloudflare dashboard: these endpoints show the
# Access policy's address list side by side with who actually holds a share,
# so an admin can spot both kinds of drift (admitted with nothing shared to
# them, or shared to but never admitted).

# Cloudflare Zero Trust is free to this many users; every person who signs in
# consumes a seat, so the admin view warns before the bill starts.
FREE_SEAT_LIMIT = 50


def _require_admin(user_id: str):
    from services.access_provisioning import access_provisioning_enabled, is_admin
    from middleware.user_context import get_request_identity

    if not access_provisioning_enabled():
        raise HTTPException(
            status_code=503,
            detail="Edge admission is not configured: set CF_API_TOKEN, "
                   "CF_ACCOUNT_ID and CF_ACCESS_POLICY_ID in .env.",
        )
    if not is_admin(get_request_identity() or user_id):
        raise HTTPException(
            status_code=403,
            detail="Only an admin (ADMIN_EMAILS) can manage edge admissions.",
        )


@router.get(
    "/api/access/admissions",
    summary="Who can reach this deployment, and why",
    tags=["access"],
)
async def list_admissions(user_id: str = Depends(get_current_user_id)):
    """The Access policy's address list, reconciled against live shares.

    `orphans` are admitted but hold no share — usually someone whose access
    was never withdrawn. `missing` hold a share but are not admitted, so
    their invitation would stop at the Cloudflare login.
    """
    _require_admin(user_id)
    from services.access_provisioning import MAX_EMAILS_PER_RULE, admitted_emails
    from services.app_database import app_db

    try:
        admitted = admitted_emails()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    contacts = app_db.list_share_contacts()
    admins = set(a.lower() for a in (settings.admin_emails or "").split(",") if a.strip())
    admitted_set = set(admitted)

    people = []
    for email in sorted(admitted):
        user = app_db.get_user(email)
        people.append({
            "email": email,
            "shares": contacts.get(email, 0),
            "is_admin": email in admins,
            "last_seen_at": user["last_seen_at"] if user else None,
            "has_signed_in": user is not None,
        })

    return {
        "configured": True,
        "policy_id": settings.cf_access_policy_id,
        "people": people,
        # Admitted but with nothing shared to them (admins excepted — they
        # are meant to be there whether or not anyone shared with them).
        "orphans": [p["email"] for p in people if p["shares"] == 0 and not p["is_admin"]],
        # Holds a share but cannot get past the edge to use it.
        "missing": sorted(e for e in contacts if e not in admitted_set),
        "counts": {
            "admitted": len(admitted),
            "free_seat_limit": FREE_SEAT_LIMIT,
            "rule_cap": MAX_EMAILS_PER_RULE,
        },
    }


@router.post(
    "/api/access/admissions",
    summary="Admit an address at the edge",
    tags=["access"],
    status_code=status.HTTP_201_CREATED,
)
async def create_admission(body: dict, user_id: str = Depends(get_current_user_id)):
    """Add an address to the Access policy directly, without sharing anything.

    Body: {"email": "someone@example.com"}
    """
    _require_admin(user_id)
    from services.access_provisioning import admit_email
    from middleware.user_context import get_request_identity

    email = (body.get("email") or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email address is required.")
    try:
        added = admit_email(email)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    logger.info(f"{get_request_identity() or user_id} admitted {email} at the edge")
    return {"email": email.lower(), "added": added}


@router.delete(
    "/api/access/admissions/{email}",
    summary="Withdraw an address at the edge",
    tags=["access"],
)
async def delete_admission(email: str, user_id: str = Depends(get_current_user_id)):
    """Remove an address from the Access policy.

    Their shares are left alone — revoking access at the door is deliberately
    separate from taking away what was shared, so this is reversible.
    """
    _require_admin(user_id)
    from services.access_provisioning import revoke_email
    from middleware.user_context import get_request_identity

    actor = get_request_identity() or user_id
    if email.strip().lower() == (actor or "").strip().lower():
        raise HTTPException(
            status_code=400,
            detail="You cannot withdraw your own access from inside the app.",
        )
    try:
        removed = revoke_email(email)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    logger.info(f"{actor} withdrew edge access for {email}")
    return {"email": email.strip().lower(), "removed": removed}


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

    When edge admission is configured (CF_API_TOKEN + CF_ACCESS_POLICY_ID)
    and the caller is an admin, notify_email is also added to the Cloudflare
    Access policy first, so the emailed link actually opens instead of
    stopping at the login. The response reports edge_admitted / edge_error.
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
        from services.access_provisioning import (
            access_provisioning_enabled,
            admit_email,
            is_admin,
        )
        from middleware.user_context import get_request_identity
        from services.collection_service import collection_service
        from services.share_email import send_share_email
        actor = get_request_identity() or user_id

        # Admit at the edge BEFORE sending, so the link works on arrival
        # rather than bouncing the recipient off the Access login.
        if access_provisioning_enabled():
            if is_admin(actor):
                try:
                    share["edge_admitted"] = admit_email(notify_email)
                except Exception as e:
                    logger.warning(f"Edge admission for {notify_email} failed: {e}")
                    share["edge_admitted"] = False
                    share["edge_error"] = str(e)
            else:
                share["edge_admitted"] = False
                share["edge_note"] = (
                    "Admitting a new person at the Cloudflare Access edge is an "
                    "admin action. This invitation only works if the recipient "
                    "can already reach the app."
                )

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

    Edge admission happens in ONE Cloudflare policy write for the whole
    batch (services.access_provisioning.admit_emails) — the per-address
    loop would be N round-trips with a lost-update race. As with single
    invites, only an admin's batch admits strangers; a non-admin's batch
    reports edge_admitted=false per address so nobody mails a hundred
    links that dead-end at the login.
    """
    from services.access_provisioning import (
        access_provisioning_enabled,
        admit_emails,
        is_admin,
    )
    from services.app_database import app_db
    from services.share_email import send_share_email
    from middleware.user_context import get_request_identity
    actor = get_request_identity() or user_id

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
    # failing before any policy write keeps the batch all-or-nothing on auth).
    collection = app_db.get_collection(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail=f"Collection {collection_id} not found")
    if collection.get("owner_id") != user_id:
        raise HTTPException(status_code=403, detail="Only the collection owner can create shares")

    # One policy write for the whole batch.
    admissions = {}
    if access_provisioning_enabled() and is_admin(actor):
        try:
            admissions = admit_emails(emails)
        except Exception as e:
            logger.warning(f"Bulk edge admission failed: {e}")
            admissions = {addr: f"rejected: {e}" for addr in emails}

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

        admission = admissions.get(addr, "")
        entry["edge_admitted"] = admission in ("added", "already")
        if admission.startswith("rejected:"):
            entry["edge_error"] = admission.partition(":")[2].strip()

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

    if access_provisioning_enabled() and not is_admin(actor):
        note = (
            "Admitting new people at the Cloudflare Access edge is an admin "
            "action; these invitations only work for recipients who can "
            "already reach the app."
        )
    else:
        note = None
    return {"results": results, "edge_note": note}


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
    """Revoke a share link you created.

    If the share was created by emailing an invitation and that address has
    no other live share, the recipient's Cloudflare Access admission is
    withdrawn too — otherwise the edge allowlist only ever grows, and someone
    with no collections keeps a way in.
    """
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

    result = {"message": "Share revoked", "success": True}
    invited = (share or {}).get("invited_email")
    if invited:
        from services.access_provisioning import (
            access_provisioning_enabled,
            is_admin,
            revoke_email,
        )
        from services.app_database import app_db

        if access_provisioning_enabled() and is_admin(user_id):
            if app_db.count_active_shares_for_email(invited, exclude_share_id=share_id) == 0:
                try:
                    result["edge_revoked"] = revoke_email(invited)
                except Exception as e:
                    logger.warning(f"Withdrawing edge admission for {invited} failed: {e}")
                    result["edge_error"] = str(e)
    return result

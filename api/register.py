"""Online registration endpoints.

Two halves with different trust:

- The public half (``GET /api/register/config``, ``POST /api/register``) is
  reachable without any credential: ``require_auth`` in main.py exempts it
  from the app's identity gate. If registration is intentionally enabled
  behind Cloudflare Access, its routes also need a narrowly scoped edge
  bypass. The hosted provisioner only bypasses the contact-only
  ``/request-access`` page. This endpoint validates hard, rate-limits per IP
  (its own class in ``middleware/rate_limit.py``), and returns nothing about
  the deployment beyond whether registration is open.
- The admin half (``/api/access/registrations``) is behind the normal
  identity gate and ``ADMIN_EMAILS``, because approving a request edits
  the Access policy for the whole deployment.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from config import settings
from middleware.user_context import get_current_user_id
from services import registration
from services.registration import RegistrationError

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Public ───────────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: str = Field(..., max_length=320)
    name: str = Field("", max_length=200)
    organization: str = Field("", max_length=200)
    note: str = Field("", max_length=1000)
    # Honeypot: real people never see it (the form hides it); bots that
    # fill every field give themselves away and get a quiet "pending".
    website: str = Field("", max_length=200)


@router.get(
    "/api/register/config",
    summary="Whether online registration is open, and its rules",
    tags=["registration"],
)
async def register_config():
    return registration.public_config()


@router.post(
    "/api/register",
    summary="Ask for access",
    tags=["registration"],
    status_code=status.HTTP_202_ACCEPTED,
)
def register(body: RegisterRequest, request: Request):  # sync: Cloudflare round-trips run in the threadpool
    if body.website.strip():
        # Silent success for the honeypot; nothing is recorded.
        return {"status": "pending", "email": body.email.strip().lower(),
                "message": "Thanks — your request has been sent for review."}

    client_ip = request.headers.get("cf-connecting-ip") or (
        request.client.host if request.client else None
    )
    try:
        return registration.submit(
            body.email, name=body.name, organization=body.organization,
            note=body.note, request_ip=client_ip,
        )
    except RegistrationError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


# ── Admin ────────────────────────────────────────────────────────────────


def _require_registration_admin(user_id: Optional[str]):
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
            detail="Only an admin (ADMIN_EMAILS) can review registration requests.",
        )


class DecisionBody(BaseModel):
    note: str = Field("", max_length=500)


@router.get(
    "/api/access/registrations",
    summary="Registration requests",
    tags=["access"],
)
async def list_registrations(
    status_filter: Optional[str] = None, user_id: str = Depends(get_current_user_id),
):
    _require_registration_admin(user_id)
    try:
        rows = registration.list_requests(status_filter or None)
    except RegistrationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "mode": settings.registration_mode,
        "allowed_domains": registration.allowed_domains(),
        "max_seats": settings.registration_max_seats,
        "requests": rows,
        "pending": sum(1 for r in rows if r.get("status") == "pending"),
    }


@router.post(
    "/api/access/registrations/{request_id}/approve",
    summary="Approve a registration request (admits the address at the edge)",
    tags=["access"],
)
async def approve_registration(
    request_id: str, body: Optional[DecisionBody] = None,
    user_id: str = Depends(get_current_user_id),
):
    _require_registration_admin(user_id)
    from middleware.user_context import get_request_identity
    actor = get_request_identity() or user_id
    try:
        return registration.approve(request_id, actor, note=(body.note if body else ""))
    except KeyError:
        raise HTTPException(status_code=404, detail="Registration request not found")
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post(
    "/api/access/registrations/{request_id}/deny",
    summary="Deny a registration request",
    tags=["access"],
)
async def deny_registration(
    request_id: str, body: Optional[DecisionBody] = None,
    user_id: str = Depends(get_current_user_id),
):
    _require_registration_admin(user_id)
    from middleware.user_context import get_request_identity
    actor = get_request_identity() or user_id
    try:
        return registration.deny(request_id, actor, note=(body.note if body else ""))
    except KeyError:
        raise HTTPException(status_code=404, detail="Registration request not found")

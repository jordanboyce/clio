"""Acceptable-use policy endpoints (the user-facing half of governance).

Document-level actions (report, sensitivity override) live with the
document routes in api/documents.py; the admin review surface lives in
api/admin.py. This module is only the policy text and the acknowledgement,
which every signed-in user touches once per policy version.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from middleware.user_context import get_current_user_id
from services import governance

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/aup", summary="Acceptable-use policy text and status", tags=["governance"])
async def get_aup(user_id: str = Depends(get_current_user_id)):
    return {**governance.aup_payload(user_id), "text": governance.aup_text()}


@router.post("/api/aup/accept", summary="Accept the current acceptable-use policy", tags=["governance"])
async def accept_aup(user_id: str = Depends(get_current_user_id)):
    """Records acceptance of the current version against the caller's
    identity. Anonymous (password-only) callers have nothing to record
    against, and are never gated on the policy in the first place."""
    if not user_id:
        raise HTTPException(status_code=403, detail="Sign in to acknowledge the policy.")
    ack = governance.accept_aup(user_id)
    return {**governance.aup_payload(user_id), "accepted_at": ack["accepted_at"]}

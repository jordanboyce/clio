"""Operator-only visibility endpoints.

The Phase-2 admin console reads these: who is spending the shared key on
what, what the process is doing right now, and the search audit trail that
search_history finally became once it gained identity columns.

Every endpoint calls require_admin first. Under private collections that
means ADMIN_EMAILS only (fails closed when the list is empty); with private
collections off it is a deliberate no-op — the shared-appliance model
treats everyone who can reach the app as a trusted teammate, same as
POST /api/config.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from api.deps import require_admin
from config import settings
from services import audit, governance

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Content governance ──────────────────────────────────────────────────
# The review surface: what the scanner held or flagged, what users
# reported, the audit trail, the hash blocklist, and who accepted the
# policy. Same gate as the rest of this module.


@router.get("/api/admin/review", summary="Content review queue", tags=["admin"])
async def get_review_queue():
    """Everything awaiting review: one entry per document with every reason
    attached (content scan, prompt-injection warnings, user reports), most
    urgent first. ``quarantined``, ``flagged`` and ``reports`` keep their
    original shapes for existing clients."""
    require_admin("review flagged content")
    from services.app_database import app_db

    queue = governance.review_queue()
    governance.invalidate_review_summary()
    return {
        "content_policy_action": settings.content_policy_action,
        "items": queue,
        "summary": governance.summarize_queue(queue),
        "quarantined": [d for d in queue if d.get("policy_status") == "quarantined"],
        "flagged": [d for d in queue if d.get("policy_status") == "flagged"],
        "reports": governance.recent_reports(limit=50),
        "blocked_hashes": len(app_db.list_blocked_hashes()),
    }


@router.get("/api/admin/review/summary", summary="Pending-review counts", tags=["admin"])
async def get_review_summary():
    """Cheap counts for the notification badge (cached for a few seconds)."""
    require_admin("review flagged content")
    return governance.review_summary()


class ReviewNote(BaseModel):
    note: str = ""


@router.post(
    "/api/admin/documents/{collection_id}/{document_id}/approve",
    summary="Mark a document as reviewed and fine",
    tags=["admin"],
)
async def approve_document(collection_id: str, document_id: str, body: Optional[ReviewNote] = None):
    """Closes everything open on the document: a hold or flag is lifted (it is
    visible again everywhere), injection warnings are dismissed and user
    reports are answered. The scan findings stay on the row so the decision
    is reviewable later; ``resolved`` says which of the three applied."""
    require_admin("approve a document")
    try:
        return governance.resolve_document(
            collection_id, document_id, note=(body.note if body else "")
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Document not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class RemoveRequest(BaseModel):
    block: bool = True
    reason: str = ""


@router.post(
    "/api/admin/documents/{collection_id}/{document_id}/remove",
    summary="Remove a document, optionally blocking its hash",
    tags=["admin"],
)
async def admin_remove_document(collection_id: str, document_id: str, body: Optional[RemoveRequest] = None):
    """Delete the document and, by default, add its sha256 to the blocklist so
    the same file cannot be re-added to any collection."""
    require_admin("remove a document")
    body = body or RemoveRequest()
    try:
        return governance.remove_document(
            collection_id, document_id, block=body.block, reason=body.reason,
            action="admin.remove_document",
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Document not found")


@router.get("/api/admin/audit", summary="Audit trail", tags=["admin"])
async def get_audit(
    limit: int = 200,
    action: Optional[str] = None,
    actor: Optional[str] = None,
    collection_id: Optional[str] = None,
    document_id: Optional[str] = None,
    since: Optional[str] = None,
    format: Optional[str] = None,
):
    """Newest-first events, filterable; ``format=csv`` returns a file."""
    require_admin("view the audit trail")
    limit = max(1, min(limit, 2000))
    rows = audit.list_events(
        limit=limit, action=action or None, actor=actor or None,
        collection_id=collection_id or None, document_id=document_id or None,
        since=since or None,
    )
    if (format or "").lower() == "csv":
        return PlainTextResponse(
            audit.to_csv(rows),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=clio-audit.csv"},
        )
    return {
        "retention_days": settings.audit_retention_days,
        "actions": audit.ACTIONS,
        "events": rows,
    }


@router.get("/api/admin/blocked-hashes", summary="Hash blocklist", tags=["admin"])
async def list_blocked_hashes():
    require_admin("view the blocklist")
    from services.app_database import app_db

    return {"hashes": app_db.list_blocked_hashes()}


class BlockHashRequest(BaseModel):
    content_hash: str
    reason: str = ""
    filename: Optional[str] = None


@router.post("/api/admin/blocked-hashes", summary="Block a file hash", tags=["admin"], status_code=201)
async def add_blocked_hash(body: BlockHashRequest):
    """Add a sha256 (64 hex chars) by hand — e.g. from an organisation's own
    list — without needing the file to be present."""
    admin = require_admin("block a file hash")
    from services.app_database import app_db

    h = body.content_hash.strip().lower()
    if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
        raise HTTPException(status_code=400, detail="content_hash must be a 64-character sha256 hex digest")
    row = app_db.add_blocked_hash(h, admin, body.reason, body.filename)
    audit.record("admin.block_hash", target=h, detail={"reason": body.reason, "filename": body.filename})
    return row


@router.delete("/api/admin/blocked-hashes/{content_hash}", summary="Unblock a file hash", tags=["admin"])
async def remove_blocked_hash(content_hash: str):
    require_admin("unblock a file hash")
    from services.app_database import app_db

    h = content_hash.strip().lower()
    if not app_db.remove_blocked_hash(h):
        raise HTTPException(status_code=404, detail="Hash not on the blocklist")
    audit.record("admin.unblock_hash", target=h)
    return {"removed": True}


@router.get("/api/admin/aup-acknowledgements", summary="Who accepted the policy", tags=["admin"])
async def list_aup_acknowledgements():
    require_admin("view policy acknowledgements")
    from services.app_database import app_db

    return {
        "required": settings.aup_required,
        "version": str(settings.aup_version),
        "acknowledgements": app_db.list_aup_acknowledgements(),
    }


class SuspendRequest(BaseModel):
    reason: str = ""


@router.post("/api/admin/users/{email}/suspend", summary="Suspend an identity", tags=["admin"])
async def suspend_user(email: str, body: Optional[SuspendRequest] = None):
    """Revoke every MCP token the identity holds and, when edge admission is
    configured, withdraw their Cloudflare Access admission. Their documents
    stay put — removal is a separate, reviewable action."""
    admin = require_admin("suspend a user")
    if admin and email.strip().lower() == admin.strip().lower():
        raise HTTPException(status_code=400, detail="You cannot suspend yourself from inside the app.")
    try:
        return governance.suspend_user(email, reason=(body.reason if body else ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/admin/usage", summary="Chat usage rollups", tags=["admin"])
async def get_usage(days: int = 30):
    """Per-user and per-day chat spend for the last `days` days."""
    require_admin("view usage reports")
    from services.app_database import app_db

    days = max(1, min(days, 365))
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    try:
        return {
            "days": days,
            "by_user": app_db.get_usage_summary(since, group_by="user"),
            "by_day": app_db.get_usage_summary(since, group_by="day"),
            "daily_token_budget": settings.chat_daily_token_budget,
        }
    except Exception as e:
        logger.error(f"Usage rollup failed: {e}")
        raise HTTPException(status_code=500, detail="Could not read usage data")


@router.get("/api/admin/search-history", summary="Recent searches", tags=["admin"])
async def get_search_history(limit: int = 100):
    """The search audit trail: who searched what, where, and when."""
    require_admin("view the search audit trail")
    from services.app_database import app_db

    limit = max(1, min(limit, 500))
    return {
        "retention_days": settings.search_history_retention_days,
        "searches": app_db.get_search_history(limit=limit),
    }


@router.get("/api/admin/stats", summary="Live process stats", tags=["admin"])
async def get_stats():
    """What the process is doing right now, plus today's spend."""
    require_admin("view system stats")
    from middleware.rate_limit import rate_limiter
    from middleware.request_meta import stats as request_stats
    from services.app_database import app_db
    from services.upload_service import upload_service

    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    try:
        usage_today_rows = app_db.get_usage_summary(today, group_by="day")
        usage_today = usage_today_rows[0] if usage_today_rows else {
            "turns": 0, "input_tokens": 0, "output_tokens": 0,
            "tool_calls": 0, "cache_hits": 0,
        }
    except Exception:
        usage_today = None

    try:
        index_jobs_active = upload_service.active_job_count()
    except Exception:
        index_jobs_active = None

    return {
        **request_stats(),
        "rate_limit": {
            "enabled": settings.rate_limit_enabled,
            "classes": rate_limiter.stats(),
        },
        "index_jobs_active": index_jobs_active,
        "max_concurrent_index_jobs": settings.max_concurrent_index_jobs,
        "usage_today": usage_today,
        "daily_token_budget": settings.chat_daily_token_budget,
    }

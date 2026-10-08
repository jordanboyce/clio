"""Append-only audit trail for the actions that carry accountability.

Search history already records who searched what; chat usage records who
spent what. Neither answers the question an operator actually asks when
something objectionable turns up: who put it here, who shared it, who
minted the token that read it, and what did the admin do about it. This
module is that trail.

One row per event: an actor (the verified identity for the request, or
None for an anonymous password caller), an action name from the
vocabulary below, the collection/document it concerned, an optional free
target (an email, a token id, a hash) and a small JSON detail blob. Rows
are never updated or deleted except by the retention sweep, which keeps
them much longer than the search log (AUDIT_RETENTION_DAYS, 0 = forever).

Recording never raises: an audit failure must not turn a successful upload
into a failed one. It logs a warning instead.
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# The vocabulary. Kept as plain strings so the admin filter can list them;
# add here when a new event type is introduced.
ACTIONS: List[str] = [
    "document.upload",          # synchronous upload request (files + resulting ids)
    "document.index_job",       # background index job started (repo / local paths)
    "document.delete",
    "document.flagged",         # content policy: indexed with a warning badge
    "document.quarantined",     # content policy: indexed but hidden pending review
    "document.rejected",        # content policy: refused at ingest
    "document.blocked",         # hash blocklist refused a re-upload
    "document.approved",        # admin cleared a flagged/quarantined document
    "document.reported",        # a user reported a document
    "document.injection_dismissed",  # admin reviewed prompt-injection warnings and kept the document
    "report.dismissed",         # admin closed the open user reports on a document
    "document.sensitivity",     # per-document sensitivity override changed
    "collection.sensitivity",   # collection label changed
    "collection.published",
    "collection.exported",      # bundle downloaded (with/without originals)
    "collection.imported",      # collection created from a bundle
    "share.create",
    "share.accept",
    "share.revoke",
    "mcp_token.create",
    "mcp_token.revoke",
    "mcp.tool_call",            # one agent tool call (MCP_AUDIT_TOOL_CALLS)
    "aup.accept",
    "admin.block_hash",
    "admin.unblock_hash",
    "admin.suspend_user",
    "admin.remove_document",    # admin removal (optionally with hash block)
    "access.register",          # someone asked for access from /register
    "access.approve_registration",
    "access.deny_registration",
]

_UNSET = object()


def record(
    action: str,
    *,
    actor: Any = _UNSET,
    collection_id: Optional[str] = None,
    document_id: Optional[str] = None,
    target: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    """Append one event. ``actor`` defaults to the current request's identity.

    Pass ``actor=None`` explicitly for a system-originated event (a
    background job, the retention sweep) so the request contextvar is not
    consulted.
    """
    if actor is _UNSET:
        try:
            from middleware.user_context import get_request_user

            actor = get_request_user()
        except Exception:
            actor = None

    try:
        from services.app_database import app_db

        app_db.add_audit_event(
            actor=actor,
            action=action,
            collection_id=collection_id,
            document_id=document_id,
            target=target,
            detail=json.dumps(detail, default=str) if detail else None,
        )
    except Exception as e:  # pragma: no cover - defensive by design
        logger.warning(f"Audit event {action} could not be recorded: {e}")


def list_events(
    limit: int = 200,
    *,
    action: Optional[str] = None,
    actor: Optional[str] = None,
    collection_id: Optional[str] = None,
    document_id: Optional[str] = None,
    since: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Newest-first page of events with the detail blob decoded."""
    from services.app_database import app_db

    rows = app_db.list_audit_events(
        limit=limit,
        action=action,
        actor=actor,
        collection_id=collection_id,
        document_id=document_id,
        since=since,
    )
    for row in rows:
        raw = row.get("detail")
        if raw:
            try:
                row["detail"] = json.loads(raw)
            except (TypeError, ValueError):
                pass
    return rows


def to_csv(rows: List[Dict[str, Any]]) -> str:
    """Flat CSV export for the admin console (detail as JSON text)."""
    import csv
    import io

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["id", "timestamp", "actor", "action", "collection_id",
                     "document_id", "target", "detail"])
    for r in rows:
        detail = r.get("detail")
        if isinstance(detail, (dict, list)):
            detail = json.dumps(detail, default=str)
        writer.writerow([
            r.get("id"), r.get("timestamp"), r.get("actor") or "",
            r.get("action"), r.get("collection_id") or "",
            r.get("document_id") or "", r.get("target") or "", detail or "",
        ])
    return out.getvalue()

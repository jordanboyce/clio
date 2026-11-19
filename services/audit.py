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

Each row also carries the request it came from (source address, request id,
sign-in method; middleware/request_context.py) and is written to the log
stream as one line on the ``clio.audit`` logger, so a SIEM that collects
the logs has the trail without polling the API: under LOG_FORMAT=json the
row is the ``audit`` object of that line.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from services.clock import utcnow

logger = logging.getLogger(__name__)
stream = logging.getLogger("clio.audit")

# The vocabulary. Kept as plain strings so the admin filter can list them;
# add here when a new event type is introduced.
ACTIONS: List[str] = [
    "document.upload",          # synchronous upload request (files + resulting ids)
    "document.index_job",       # background index job started (repo / local paths)
    "document.delete",
    "document.replace",         # a new version of the file took this version's place
    "document.bulk_delete",     # delete job started; each document also gets its own document.delete
    "document.flagged",        # content policy: indexed with a warning badge
    "document.quarantined",     # content policy: indexed but hidden pending review
    "document.rejected",        # content policy: refused at ingest
    "document.blocked",         # hash blocklist refused a re-upload
    "document.approved",        # admin cleared a flagged/quarantined document
    "document.reported",        # a user reported a document
    "document.injection_dismissed",  # admin reviewed prompt-injection warnings and kept the document
    "report.dismissed",         # admin closed the open user reports on a document
    "document.sensitivity",     # per-document sensitivity override changed
    "collection.sensitivity",   # collection label changed
    "document.classification",  # per-document classification marking changed
    "collection.classification",  # collection classification marking changed
    "classification.blocked",   # a local-only marking stopped data going to an outside service
    "collection.published",
    "collection.delete",        # collection and its documents removed (team flag, label)
    "collection.embedding",     # collection's embedding model changed (from/to model id)
    "collection.exported",      # bundle downloaded (with/without originals)
    "collection.static_export",  # exported for a public website (counts, model, who confirmed)
    "collection.static_addresses",  # where its sources are published on the web, for a static export
    "collection.imported",      # collection created from a bundle
    "collection.guide",         # guide text changed (length/digest/scan verdict, never the text)
    "collection.metadata",      # name/description changed (length/digest only)
    "collection.expertise",     # expertise packs attached/detached
    "collection.outputs_deleted",  # generated reviews and saved reports removed (counts only)
    "expertise.create",         # expertise pack created (id, name, digest)
    "expertise.update",
    "expertise.delete",
    "coverage.run",             # retrieval coverage report started
    "coverage.question_add",    # owner question added to the coverage report
    "coverage.question_delete",
    "share.create",
    "share.accept",
    "share.revoke",
    "mcp_token.create",
    "mcp_token.revoke",
    "mcp.tool_call",            # one agent tool call (MCP_AUDIT_TOOL_CALLS)
    "api_app.create",           # a registered app and what it may reach
    "api_app.update",
    "api_app.rotate_key",
    "api_app.revoke",
    "api.call",                 # one read by a registered app (MCP_AUDIT_TOOL_CALLS)
    "aup.accept",
    "config.update",            # deployment settings saved (changed field names, never values)
    "embedding.change",         # deployment embedding provider/model changed (ids only)
    "ai_key.store",             # team AI key stored or replaced (provider only)
    "ai_key.remove",            # team AI key removed (provider only)
    "admin.block_hash",
    "admin.unblock_hash",
    "admin.suspend_user",
    "admin.reinstate_user",     # a suspension lifted
    "admin.injection_tuning",   # injection-scan patterns muted/unmuted, allow phrases changed
    "auth.sign_in",             # a credential accepted (first time per caller, method and address in a while)
    "auth.sign_in_failed",      # a credential refused (password, token, app key, identity assertion)
    "auth.lockout",             # an address ran out of sign-in attempts (AUTH_FAILURE_LIMIT)
    "admin.remove_document",    # admin removal (optionally with hash block)
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
        actor = current_actor()
    context = _request_context()
    detail_text = json.dumps(detail, default=str) if detail else None
    try:
        from services.app_database import app_db

        app_db.add_audit_event(
            actor=actor,
            action=action,
            collection_id=collection_id,
            document_id=document_id,
            target=target,
            detail=detail_text,
            context=context,
        )
    except Exception as e:  # pragma: no cover - defensive by design
        logger.warning(f"Audit event {action} could not be recorded: {e}")
    _emit({"actor": actor, "action": action, "collection_id": collection_id,
           "document_id": document_id, "target": target, "detail": detail, **context})


def current_actor() -> Optional[str]:
    """Who the current request is: a registered app, else the verified
    identity, else the collection user (None or the single-user default).
    The verified identity comes before the collection user so a signed-in
    person on a deployment without private collections is still named."""
    try:
        from middleware.user_context import get_request_app, get_request_identity, get_request_user

        app = get_request_app()
        if app is not None:
            return app["actor"]
        return get_request_identity() or get_request_user()
    except Exception:  # noqa: BLE001 - attribution must not stop the event
        return None


def _request_context() -> Dict[str, Optional[str]]:
    try:
        from middleware.request_context import audit_context

        return audit_context()
    except Exception:  # noqa: BLE001
        return {"source_ip": None, "request_id": None, "auth_method": None}


def _emit(event: Dict[str, Any]) -> None:
    """One log line per row, for whoever collects the logs."""
    try:
        row = {"timestamp": utcnow().isoformat(), **event}
        stream.info("AUDIT %s", json.dumps(row, default=str), extra={"audit": row})
    except Exception:  # noqa: BLE001 - logging must not stop the event
        pass


def record_many(
    action: str,
    *,
    actor: Optional[str],
    collection_id: Optional[str],
    events: List[Dict[str, Any]],
) -> None:
    """Append one action over many documents as a single write.

    Still one row per document, so the trail reads the same as for single
    removals. Each event is ``{"document_id", "detail"}``; never raises.
    """
    if not events:
        return
    context = _request_context()
    try:
        from services.app_database import app_db

        app_db.add_audit_events(actor, action, collection_id, [
            {"document_id": e.get("document_id"),
             "detail": json.dumps(e["detail"], default=str) if e.get("detail") else None}
            for e in events
        ], context=context)
    except Exception as e:  # pragma: no cover - defensive by design
        logger.warning(f"Audit events {action} could not be recorded: {e}")
    for e in events:
        _emit({"actor": actor, "action": action, "collection_id": collection_id,
               "document_id": e.get("document_id"), "target": None, "detail": e.get("detail"), **context})


def list_events(
    limit: int = 200,
    *,
    action: Optional[str] = None,
    actor: Optional[str] = None,
    collection_id: Optional[str] = None,
    document_id: Optional[str] = None,
    since: Optional[str] = None,
    before_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Newest-first page of events with the detail blob decoded. Pass the
    last row's id as ``before_id`` for the next page."""
    from services.app_database import app_db

    rows = app_db.list_audit_events(
        limit=limit,
        action=action,
        actor=actor,
        collection_id=collection_id,
        document_id=document_id,
        since=since,
        before_id=before_id,
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

    from services.csv_safety import safe_row

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["id", "timestamp", "actor", "action", "collection_id",
                     "document_id", "target", "detail", "source_ip", "request_id", "auth_method"])
    for r in rows:
        detail = r.get("detail")
        if isinstance(detail, (dict, list)):
            detail = json.dumps(detail, default=str)
        # Actors, targets and details carry caller-chosen text (a filename,
        # a token name); an admin opens this file in a spreadsheet.
        writer.writerow(safe_row([
            r.get("id"), r.get("timestamp"), r.get("actor") or "",
            r.get("action"), r.get("collection_id") or "",
            r.get("document_id") or "", r.get("target") or "", detail or "",
            r.get("source_ip") or "", r.get("request_id") or "", r.get("auth_method") or "",
        ]))
    return out.getvalue()

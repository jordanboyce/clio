"""Content governance: sensitivity labels, quarantine, review, blocklist, AUP.

The three questions this module answers, and where each is enforced:

- **How sensitive is this?** A collection carries a label (public,
  internal, confidential, restricted); a document may override it. The
  label is informational everywhere except *restricted*, which is a real
  boundary: restricted collections cannot be shared, and MCP clients only
  see them through a personal token explicitly scoped to that collection.
  Enforced in ``sharing_service.create_share`` and
  ``mcp_server._resolve_collection_id`` / ``_visible_collections``.

- **May this document be served?** The content-policy scanner sets a
  ``policy_status`` at ingest. ``quarantined`` documents stay indexed but
  are filtered out of retrieval (``DocumentIndexer.search``), MCP document
  tools, and the pdf/chunks endpoints until an admin approves. Enforced by
  ``hidden_document_ids`` consumers and ``assert_document_servable``.

- **Who did this, and may they?** Attribution lives on the document row
  (``uploaded_by``); the audit trail (services/audit.py) records the rest;
  the acceptable-use acknowledgement gates ingest per identity.

Everything here is domain-neutral — it applies to any corpus.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from config import settings
from services import audit

logger = logging.getLogger(__name__)

SENSITIVITY_LEVELS = ("public", "internal", "confidential", "restricted")
DEFAULT_SENSITIVITY = "internal"

_UNSET = object()


# ── Sensitivity labels ────────────────────────────────────────────────────

def normalize_sensitivity(value: Any, allow_none: bool = False) -> Optional[str]:
    """Validate a label. ``allow_none`` accepts ""/None to mean "inherit"."""
    if value is None or value == "":
        if allow_none:
            return None
        raise ValueError(f"sensitivity must be one of {', '.join(SENSITIVITY_LEVELS)}")
    label = str(value).strip().lower()
    if label not in SENSITIVITY_LEVELS:
        raise ValueError(f"sensitivity must be one of {', '.join(SENSITIVITY_LEVELS)}")
    return label


def collection_sensitivity(collection: Optional[Dict[str, Any]]) -> str:
    label = (collection or {}).get("sensitivity") or DEFAULT_SENSITIVITY
    return label if label in SENSITIVITY_LEVELS else DEFAULT_SENSITIVITY


def effective_sensitivity(collection: Optional[Dict[str, Any]], doc_sensitivity: Optional[str]) -> str:
    """A document's override wins; otherwise the collection's label."""
    if doc_sensitivity in SENSITIVITY_LEVELS:
        return doc_sensitivity
    return collection_sensitivity(collection)


def is_restricted(collection: Optional[Dict[str, Any]]) -> bool:
    return collection_sensitivity(collection) == "restricted"


def mcp_can_expose(collection: Optional[Dict[str, Any]], token_scope: Optional[List[str]]) -> bool:
    """Whether an MCP request may see this collection at all.

    Restricted collections are hidden from every MCP client except one
    authenticating with a personal token whose scope names the collection.
    SSO sessions hitting /mcp, Access service tokens, and password callers
    never see them — the point of the label is that agent access is an
    explicit, revocable grant.
    """
    if not is_restricted(collection):
        return True
    cid = (collection or {}).get("id")
    return bool(cid and token_scope and cid in token_scope)


# ── Document servability ──────────────────────────────────────────────────

def is_hidden(doc_info: Optional[Dict[str, Any]]) -> bool:
    from services.content_policy import HIDDEN_STATUSES

    return bool(doc_info) and (doc_info.get("policy_status") or "clear") in HIDDEN_STATUSES


def assert_document_servable(doc_info: Optional[Dict[str, Any]], allow_admin: bool = True) -> None:
    """Raise 404 for a quarantined document unless the caller is an admin.

    404 rather than 403: a quarantined document should look absent to
    everyone but the reviewer, exactly like an inaccessible collection.
    Open (shared-appliance) deployments have no admin distinction, so
    require_admin's no-op there means everyone can still inspect it — the
    same trust model the rest of the app applies.
    """
    if not is_hidden(doc_info):
        return
    if allow_admin:
        from api.deps import require_admin

        try:
            require_admin("inspect a quarantined document")
            return
        except HTTPException:
            pass
    raise HTTPException(status_code=404, detail="Document not found")


def visible_documents(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop quarantined rows from a listing meant for retrieval surfaces."""
    return [d for d in docs if not is_hidden(d)]


# ── Removal / review actions ──────────────────────────────────────────────

def remove_document(
    collection_id: str,
    document_id: str,
    *,
    block: bool = False,
    reason: str = "",
    actor: Any = _UNSET,
    action: str = "document.delete",
) -> Dict[str, Any]:
    """Delete a document from its collection (index, tracking, file).

    Shared by the user-facing delete endpoint and the admin "remove and
    block" action. ``block`` adds the file's sha256 to the blocklist so the
    same bytes are refused on re-upload anywhere. Raises KeyError when the
    document is unknown.
    """
    from services.collection_service import collection_service
    from services.indexer_manager import indexer_manager

    indexer = indexer_manager.get_indexer(collection_id)
    store = indexer.vector_store.metadata_store
    doc_info = store.get_document_info(document_id)
    if not doc_info:
        raise KeyError(document_id)

    if actor is _UNSET:
        from middleware.user_context import get_request_user

        actor = get_request_user()

    blocked = False
    if block:
        content_hash = doc_info.get("content_hash")
        if content_hash:
            from services.app_database import app_db

            app_db.add_blocked_hash(content_hash, actor, reason, doc_info.get("filename"))
            blocked = True
            audit.record("admin.block_hash", actor=actor, collection_id=collection_id,
                         document_id=document_id, target=content_hash,
                         detail={"filename": doc_info.get("filename"), "reason": reason})
        else:
            logger.warning(
                f"Cannot block {document_id}: no content hash recorded (indexed before governance)"
            )

    num_deleted = indexer.delete_document(document_id)
    collection_service.remove_document(collection_id, document_id)
    invalidate_review_summary()

    file_deleted = False
    is_local_reference = doc_info.get("source_type") == "local_reference"
    if not is_local_reference:
        doc_path = indexer_manager.get_documents_path(collection_id) / doc_info["filename"]
        if doc_path.exists():
            doc_path.unlink()
            file_deleted = True
    indexer.save_index()
    _invalidate_answers(collection_id)

    audit.record(action, actor=actor, collection_id=collection_id, document_id=document_id,
                 detail={"filename": doc_info.get("filename"), "chunks_deleted": num_deleted,
                         "blocked": blocked, "reason": reason or None,
                         "uploaded_by": doc_info.get("uploaded_by")})
    return {
        "filename": doc_info["filename"],
        "chunks_deleted": num_deleted,
        "file_deleted": file_deleted,
        "was_local_reference": is_local_reference,
        "blocked": blocked,
    }


def set_policy_status(
    collection_id: str,
    document_id: str,
    status: str,
    *,
    actor: Any = _UNSET,
    note: str = "",
) -> Dict[str, Any]:
    """Admin review outcome. Currently only "approved" (clears a hold)."""
    from services.content_policy import POLICY_STATUSES
    from services.indexer_manager import indexer_manager

    if status not in POLICY_STATUSES:
        raise ValueError(f"status must be one of {', '.join(POLICY_STATUSES)}")
    store = indexer_manager.get_indexer(collection_id).vector_store.metadata_store
    doc_info = store.get_document_info(document_id)
    if not doc_info:
        raise KeyError(document_id)
    if actor is _UNSET:
        from middleware.user_context import get_request_user

        actor = get_request_user()
    store.set_document_governance(document_id, policy_status=status)
    _invalidate_answers(collection_id)
    invalidate_review_summary()
    audit.record(
        "document.approved" if status == "approved" else f"document.{status}",
        actor=actor, collection_id=collection_id, document_id=document_id,
        detail={"filename": doc_info.get("filename"), "previous": doc_info.get("policy_status"),
                "note": note or None},
    )
    return {"document_id": document_id, "policy_status": status}


def set_document_sensitivity(
    collection_id: str, document_id: str, sensitivity: Optional[str], *, actor: Any = _UNSET
) -> Dict[str, Any]:
    from services.indexer_manager import indexer_manager

    label = normalize_sensitivity(sensitivity, allow_none=True)
    store = indexer_manager.get_indexer(collection_id).vector_store.metadata_store
    doc_info = store.get_document_info(document_id)
    if not doc_info:
        raise KeyError(document_id)
    if actor is _UNSET:
        from middleware.user_context import get_request_user

        actor = get_request_user()
    store.set_document_governance(document_id, sensitivity=label)
    audit.record("document.sensitivity", actor=actor, collection_id=collection_id,
                 document_id=document_id,
                 detail={"filename": doc_info.get("filename"),
                         "from": doc_info.get("sensitivity"), "to": label})
    return {"document_id": document_id, "sensitivity": label}


def _invalidate_answers(collection_id: str) -> None:
    """A document's visibility changed: cached answers that cited it are stale."""
    try:
        from services.answer_cache import answer_cache

        answer_cache.clear(f"col:{collection_id}")
    except Exception as e:  # pragma: no cover
        logger.debug(f"Answer cache clear skipped for {collection_id}: {e}")


# ── Review queue ──────────────────────────────────────────────────────────
#
# One document can be in the queue for several reasons at once — the content
# scan held it, its text carries prompt-injection warnings, two people
# reported it. The reviewer decides about the *document*, so the queue is one
# row per document with every reason attached, sorted by how much it matters.

_INJECTION_LEVEL_RANK = {"high": 55, "medium": 40}
_CLOSED_REPORT_ACTIONS = ("report.dismissed", "document.approved")


def _policy_reason(doc: Dict[str, Any]) -> str:
    from services.content_policy import CRITICAL_CATEGORIES

    flags = doc.get("policy_flags") or {}
    cats = flags.get("categories") or {}
    parts = [
        f"{str(c).replace('_', ' ')}" + (f" ×{n}" if n and n > 1 else "")
        for c, n in sorted(cats.items(), key=lambda kv: -kv[1])
        if c not in ("secrets",)
    ]
    llm = (flags.get("llm") or {}).get("categories") or []
    for c in llm:
        label = str(c).replace("_", " ")
        if not any(p.startswith(label) for p in parts):
            parts.append(f"{label} (model opinion)")
    held = "Held" if doc.get("policy_status") == "quarantined" else "Flagged"
    what = ", ".join(parts[:3]) if parts else "content-policy signals"
    critical = " Critical category." if (flags.get("critical") or any(c in CRITICAL_CATEGORIES for c in cats)) else ""
    return f"{held} by the content scan: {what}.{critical}"


def _open_report_groups() -> Dict[Tuple[str, str], Dict[str, Any]]:
    """User reports nobody has answered yet, grouped per document.

    A report is open until an admin approves/dismisses the document after it
    was filed (deleting the document closes it implicitly: the group has
    nothing left to point at and is dropped when the document is not found).
    """
    reports = audit.list_events(limit=1000, action="document.reported")
    if not reports:
        return {}
    closed_at: Dict[Tuple[str, str], str] = {}
    for action in _CLOSED_REPORT_ACTIONS:
        for ev in audit.list_events(limit=2000, action=action):
            key = (ev.get("collection_id") or "", ev.get("document_id") or "")
            if (ev.get("timestamp") or "") > closed_at.get(key, ""):
                closed_at[key] = ev.get("timestamp") or ""

    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for ev in reversed(reports):  # oldest first, so first_at/last_at fall out naturally
        key = (ev.get("collection_id") or "", ev.get("document_id") or "")
        if not key[0] or not key[1]:
            continue
        ts = ev.get("timestamp") or ""
        if ts <= closed_at.get(key, ""):
            continue
        g = groups.setdefault(key, {
            "count": 0, "reporters": [], "reasons": [], "first_at": ts, "last_at": ts,
        })
        g["count"] += 1
        g["last_at"] = ts
        who = ev.get("actor") or "anonymous"
        if who not in g["reporters"]:
            g["reporters"].append(who)
        reason = ((ev.get("detail") or {}).get("reason") if isinstance(ev.get("detail"), dict) else "") or ""
        if reason and reason not in g["reasons"] and len(g["reasons"]) < 5:
            g["reasons"].append(reason)
    return groups


def _rank(item: Dict[str, Any]) -> int:
    """Higher = look at it sooner. Roughly: critical > held > reported > flagged > injection."""
    flags = item.get("policy_flags") or {}
    rank = 0
    if "policy" in item["kinds"]:
        if flags.get("critical"):
            rank = max(rank, 100)
        elif item.get("policy_status") == "quarantined":
            rank = max(rank, 90)
        else:
            rank = max(rank, 60 + int(min(float(flags.get("max_score") or 0), 1.0) * 9))
    if "report" in item["kinds"]:
        rank = max(rank, 70 + min(item["reports"]["count"], 5))
    if "injection" in item["kinds"]:
        rank = max(rank, _INJECTION_LEVEL_RANK.get(item["injection"]["level"], 0))
    return rank


def _priority_label(item: Dict[str, Any]) -> str:
    flags = item.get("policy_flags") or {}
    if "policy" in item["kinds"] and flags.get("critical"):
        return "critical"
    if item["rank"] >= 70 or (
        "injection" in item["kinds"] and item["injection"]["level"] == "high"
    ):
        return "high"
    if item["rank"] >= 50:
        return "medium"
    return "low"


def review_queue() -> List[Dict[str, Any]]:
    """Everything awaiting a reviewer, one entry per document, most urgent first.

    Each entry is the document row plus: ``kinds`` (any of "policy",
    "injection", "report"), ``priority`` (critical|high|medium|low), a numeric
    ``rank``, plain-language ``reasons`` (one per kind), and the raw
    ``injection`` / ``reports`` detail for the kinds that apply. Low-level
    injection warnings (discussion, quoted examples) are intentionally not
    queued — they stay visible on the document but are not an admin's job.
    """
    from services.app_database import app_db
    from services.indexer_manager import indexer_manager
    from services.prompt_injection_detector import summarize_document

    report_groups = _open_report_groups()
    items: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for collection in app_db.get_all_collections():
        cid = collection.get("id")
        if not cid:
            continue
        try:
            store = indexer_manager.get_indexer(cid).vector_store.metadata_store
            policy_docs = store.list_documents_by_policy_status(("flagged", "quarantined"))
            injected_docs = store.list_documents_with_injection_warnings()
            reported_ids = [did for (c, did) in report_groups if c == cid]
            reported_docs = [d for d in (store.get_document_info(i) for i in reported_ids) if d]
        except Exception as e:
            logger.warning(f"Review queue: could not read collection {cid}: {e}")
            continue

        for d in (*policy_docs, *injected_docs, *reported_docs):
            key = (cid, d["document_id"])
            item = items.get(key)
            if item is None:
                item = dict(d)
                item["collection_id"] = cid
                item["collection_name"] = collection.get("name", cid)
                item["sensitivity_effective"] = effective_sensitivity(collection, d.get("sensitivity"))
                item["kinds"] = []
                item["reasons"] = []
                item["injection"] = None
                item["reports"] = None
                items[key] = item
            if "policy" not in item["kinds"] and d.get("policy_status") in ("flagged", "quarantined"):
                item["kinds"].append("policy")
                item["reasons"].append({"kind": "policy", "text": _policy_reason(d)})
            if "injection" not in item["kinds"] and d.get("injection_warnings") and not d.get("injection_review"):
                summary = summarize_document(d["injection_warnings"])
                if summary and summary["needs_review"]:
                    item["kinds"].append("injection")
                    item["injection"] = summary
                    item["reasons"].append({"kind": "injection", "text": summary["reason"]})
            group = report_groups.get(key)
            if group and "report" not in item["kinds"]:
                item["kinds"].append("report")
                item["reports"] = group
                n = group["count"]
                why = "; ".join(group["reasons"][:2])
                item["reasons"].append({
                    "kind": "report",
                    "text": f"Reported by {len(group['reporters'])} "
                            f"{'person' if len(group['reporters']) == 1 else 'people'}"
                            + (f" ({n} reports)" if n > len(group["reporters"]) else "")
                            + (f": {why}" if why else "."),
                })

    queue = [i for i in items.values() if i["kinds"]]
    for i in queue:
        i["rank"] = _rank(i)
        i["priority"] = _priority_label(i)
    queue.sort(key=lambda i: (-i["rank"], i.get("upload_timestamp") or ""))
    return queue


def recent_reports(limit: int = 50) -> List[Dict[str, Any]]:
    return audit.list_events(limit=limit, action="document.reported")


def resolve_document(
    collection_id: str, document_id: str, *, actor: Any = _UNSET, note: str = ""
) -> Dict[str, Any]:
    """The reviewer looked and the document is fine: close everything open on it.

    Releases a flagged/quarantined document, dismisses its injection warnings
    (kept on the row, just no longer queued) and closes its open user reports.
    Returns what was resolved so the UI can say so. Raises KeyError when the
    document is unknown.
    """
    from services.indexer_manager import indexer_manager

    store = indexer_manager.get_indexer(collection_id).vector_store.metadata_store
    doc_info = store.get_document_info(document_id)
    if not doc_info:
        raise KeyError(document_id)
    if actor is _UNSET:
        from middleware.user_context import get_request_user

        actor = get_request_user()

    # Looked up first: approving writes a "document.approved" event, which
    # itself closes reports, so checking afterwards would miss them.
    had_reports = (collection_id, document_id) in _open_report_groups()

    resolved: List[str] = []
    status = doc_info.get("policy_status") or "clear"
    if status in ("flagged", "quarantined"):
        set_policy_status(collection_id, document_id, "approved", actor=actor, note=note)
        status = "approved"
        resolved.append("policy")

    if doc_info.get("injection_warnings") and not doc_info.get("injection_review"):
        store.set_document_governance(document_id, injection_review="dismissed")
        audit.record("document.injection_dismissed", actor=actor, collection_id=collection_id,
                     document_id=document_id,
                     detail={"filename": doc_info.get("filename"), "note": note or None})
        resolved.append("injection")

    if had_reports:
        audit.record("report.dismissed", actor=actor, collection_id=collection_id,
                     document_id=document_id,
                     detail={"filename": doc_info.get("filename"), "note": note or None})
        resolved.append("report")

    invalidate_review_summary()
    return {"document_id": document_id, "policy_status": status, "resolved": resolved}


# The footer/toolbar badge polls this; walking every collection's store on
# each poll would be wasteful, and a few seconds of lag on a badge is fine.
_SUMMARY_TTL = 30.0
_summary_lock = threading.Lock()
_summary_cache: Dict[str, Any] = {"at": 0.0, "value": None}


def invalidate_review_summary() -> None:
    with _summary_lock:
        _summary_cache["at"] = 0.0
        _summary_cache["value"] = None


def summarize_queue(queue: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "pending": len(queue),
        "critical": sum(1 for i in queue if i["priority"] == "critical"),
        "held": sum(1 for i in queue if i.get("policy_status") == "quarantined"),
        "flagged": sum(1 for i in queue if "policy" in i["kinds"] and i.get("policy_status") == "flagged"),
        "injection": sum(1 for i in queue if "injection" in i["kinds"]),
        "reports": sum(1 for i in queue if "report" in i["kinds"]),
    }


def review_summary(*, max_age: float = _SUMMARY_TTL) -> Dict[str, Any]:
    """Counts for the notification badge (cached for ``max_age`` seconds)."""
    with _summary_lock:
        cached = _summary_cache["value"]
        if cached is not None and time.monotonic() - _summary_cache["at"] < max_age:
            return cached
    value = summarize_queue(review_queue())
    with _summary_lock:
        _summary_cache["value"] = value
        _summary_cache["at"] = time.monotonic()
    return value


def suspend_user(email: str, *, actor: Any = _UNSET, reason: str = "") -> Dict[str, Any]:
    """Cut an identity off: withdraw edge admission (when configured) and
    revoke every MCP token they hold. Their documents are left in place —
    removal is a separate, reviewable decision."""
    from services.access_provisioning import access_provisioning_enabled, revoke_email
    from services.app_database import app_db

    target = (email or "").strip().lower()
    if not target:
        raise ValueError("An email address is required")
    if actor is _UNSET:
        from middleware.user_context import get_request_user

        actor = get_request_user()

    result: Dict[str, Any] = {"email": target, "tokens_revoked": 0, "edge_revoked": None}
    result["tokens_revoked"] = app_db.revoke_all_mcp_tokens_for_user(target)
    if access_provisioning_enabled():
        try:
            result["edge_revoked"] = revoke_email(target)
        except Exception as e:
            result["edge_error"] = str(e)
    audit.record("admin.suspend_user", actor=actor, target=target,
                 detail={"reason": reason or None, **{k: v for k, v in result.items() if k != "email"}})
    return result


# ── Reporting ─────────────────────────────────────────────────────────────

def report_document(
    collection_id: str, document_id: str, reason: str, *, actor: Any = _UNSET, app_url: str = ""
) -> Dict[str, Any]:
    """A user flagged a document for review: audit it and tell the admins."""
    from services.access_provisioning import admin_emails
    from services.indexer_manager import indexer_manager

    store = indexer_manager.get_indexer(collection_id).vector_store.metadata_store
    doc_info = store.get_document_info(document_id)
    if not doc_info:
        raise KeyError(document_id)
    if actor is _UNSET:
        from middleware.user_context import get_request_user

        actor = get_request_user()
    reason = (reason or "").strip()[:1000]
    invalidate_review_summary()
    audit.record("document.reported", actor=actor, collection_id=collection_id,
                 document_id=document_id,
                 detail={"filename": doc_info.get("filename"), "reason": reason,
                         "uploaded_by": doc_info.get("uploaded_by")})

    notified = 0
    recipients = admin_emails()
    if recipients:
        from services.share_email import send_plain_email, share_email_enabled

        if share_email_enabled():
            subject = f"[Clio] Document reported: {doc_info.get('filename')}"
            body = (
                f"{actor or 'An anonymous user'} reported a document for review.\n\n"
                f"File: {doc_info.get('filename')}\n"
                f"Collection: {collection_id}\n"
                f"Document id: {document_id}\n"
                f"Uploaded by: {doc_info.get('uploaded_by') or 'unknown'}\n"
                f"Reason: {reason or '(none given)'}\n\n"
                f"Review it in the Admin tab{': ' + app_url.rstrip('/') + '/?tab=admin' if app_url else ''}."
            )
            for to in recipients:
                try:
                    send_plain_email(to, subject=subject, text=body)
                    notified += 1
                except Exception as e:
                    logger.warning(f"Report notification to {to} failed: {e}")
    return {"reported": True, "admins_notified": notified}


# ── Acceptable-use policy ─────────────────────────────────────────────────

DEFAULT_AUP_TEXT = """## Acceptable use

This deployment indexes documents so that people on this team can search them and ask questions about them. Every upload, deletion, share and agent connection is recorded against your identity.

By continuing you agree that you will **not** add or use content that:

- depicts or solicits the sexual abuse or exploitation of children, in any form;
- incites violence, plans an attack, or provides instructions for weapons intended to harm people;
- advertises or arranges the sale of illegal weapons or controlled substances;
- consists of stolen credentials, payment card data, or other people's identity documents;
- targets a private individual for harassment, or publishes their private information to intimidate them;
- you are not authorised to hold or share.

Documents are scanned when they are added. Anything the scan flags is reviewed by an administrator, who may remove it, block it from being re-added, and withdraw your access. If you find something that should not be here, use **Report** on the document so an administrator can look at it.
"""


def aup_text() -> str:
    return (settings.aup_text or "").strip() or DEFAULT_AUP_TEXT


def aup_required_for(user_id: Optional[str]) -> bool:
    """The AUP gate needs an identity to record against, so it is only live
    under private collections and only for identified callers."""
    return bool(settings.aup_required and settings.private_collections and user_id)


def aup_accepted(user_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not user_id:
        return None
    from services.app_database import app_db

    try:
        return app_db.get_aup_acknowledgement(user_id, str(settings.aup_version))
    except Exception as e:  # pragma: no cover
        logger.warning(f"AUP lookup failed for {user_id}: {e}")
        return None


def aup_payload(user_id: Optional[str]) -> Dict[str, Any]:
    ack = aup_accepted(user_id)
    return {
        "enabled": bool(settings.aup_required),
        "required": aup_required_for(user_id),
        "version": str(settings.aup_version),
        "accepted": ack is not None,
        "accepted_at": (ack or {}).get("accepted_at"),
    }


def accept_aup(user_id: Optional[str]) -> Dict[str, Any]:
    if not user_id:
        raise ValueError("Acknowledgement needs a signed-in identity")
    from services.app_database import app_db

    ack = app_db.record_aup_acknowledgement(user_id, str(settings.aup_version))
    audit.record("aup.accept", actor=user_id, target=str(settings.aup_version))
    return ack


def require_aup(user_id: Optional[str]) -> None:
    """Gate for ingest endpoints: 403 with a machine-readable detail until
    the caller has accepted the current policy version."""
    if aup_required_for(user_id) and aup_accepted(user_id) is None:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "aup_required",
                "message": "Accept the acceptable-use policy before adding sources.",
                "version": str(settings.aup_version),
            },
        )


# ── Ingest helpers ────────────────────────────────────────────────────────

def content_hash_of(path: Path) -> str:
    """Full sha256 of a file (the document id is its first 16 hex chars)."""
    import hashlib

    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

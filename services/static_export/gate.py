"""The gate: what may be published, decided once for the preview and the export.

Every addition to an export reads its sources from ``_survey``'s
``documents``, which already leaves out held sources and sources with a
stricter label, and refuses a collection that holds anything local only.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from services.collection_bundle import describe_model
from services.static_export.common import (
    DEFAULT_PASSAGES_PER_FILE, DESTINATION, StaticExportError, _supported, browser_model,
)


def _open(db_path: Path) -> sqlite3.Connection:
    """Read only, and one transaction: every query sees the same collection."""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")
    return conn


def _index_info(conn: sqlite3.Connection) -> Dict[str, Any]:
    try:
        rows = conn.execute("SELECT key, value FROM index_info").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {k: json.loads(v) for k, v in rows}


def _local_only_message(collection: Dict[str, Any]) -> str:
    from services import data_controls

    why = data_controls.local_only_summary(collection["id"])
    name = data_controls.label_of(why.get("classification")) or "local only"
    if why.get("documents"):
        n = why["documents"]
        return (f"This collection holds {n} source{'' if n == 1 else 's'} marked {name}, which keeps "
                f"{'it' if n == 1 else 'them'} on this server, so the collection cannot be exported for a "
                f"website. Move what is cleared for release to a collection of its own and export that.")
    return (f"This collection is marked {name}, which keeps it on this server, so it cannot be exported "
            f"for a website.")


def _survey(collection_id: str) -> Dict[str, Any]:
    """Everything the preview and the export both decide from.

    Returns the open connection too; the caller closes it.
    """
    from services import data_controls, governance
    from services.collection_service import collection_service

    collection = collection_service.get_collection(collection_id)
    if not collection:
        raise StaticExportError(f"Collection '{collection_id}' not found", "not_found")
    indexes_dir = collection_service.get_indexes_path(collection_id)
    db_path = indexes_dir / "metadata.db"
    if not db_path.exists():
        raise StaticExportError("This collection has nothing indexed to export yet.", "empty")

    blockers: List[Dict[str, str]] = []
    if data_controls.holds_local_only(collection_id):
        blockers.append({"code": "local_only", "message": _local_only_message(collection)})

    sensitivity = governance.collection_sensitivity(collection)
    if sensitivity != "public":
        blockers.append({"code": "not_public", "message": (
            f"This collection is labelled {sensitivity.capitalize()}. Only a collection labelled Public can be "
            "exported for a website: once it is cleared for release, set its sensitivity to Public in the "
            "collection's settings.")})

    conn = _open(db_path)
    try:
        docs = [dict(r) for r in conn.execute("SELECT * FROM documents ORDER BY filename, document_id")]
        # As something outside would see it: a local-only source is hidden
        # here even if the whole-collection test above were ever to miss it.
        with data_controls.outside_consumer(DESTINATION):
            held = [d for d in docs if governance.is_hidden(d)]
        held_ids = {d["document_id"] for d in held}
        not_public = [d for d in docs if d["document_id"] not in held_ids
                      and d.get("sensitivity") in governance.SENSITIVITY_LEVELS
                      and d["sensitivity"] != "public"]
        left_out = held_ids | {d["document_id"] for d in not_public}
        kept = [d for d in docs if d["document_id"] not in left_out]

        info = _index_info(conn)
        model = describe_model(info, kept)
        browser = browser_model(model["model"]) if model["provider"] == "local" and model["recorded"] else None
        # No browser build: the site searches by keyword instead of refusing.
        keywords_reason = None
        if not model["recorded"]:
            keywords_reason = (
                "This collection was indexed before Clio recorded its embedding model, so a website cannot "
                "embed queries the same way: it will search by keyword. To search by meaning, re-index it, "
                "then export.")
        elif browser is None:
            where = "" if model["provider"] == "local" else f" ({model['provider']})"
            keywords_reason = (
                f"This collection was embedded with {model['model'] or 'an unknown model'}{where}, which has no "
                f"browser build Clio has checked, so the website will search by keyword. To search by meaning "
                f"there, clone or re-index the collection with {_supported()} and export that.")

        marks = ",".join("?" * len(kept))
        ids = [d["document_id"] for d in kept]
        passages, text_bytes = conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(LENGTH(CAST(text AS BLOB))), 0) FROM chunks "
            f"WHERE document_id IN ({marks})", ids).fetchone() if ids else (0, 0)
        if not passages and not any(b["code"] in ("local_only", "not_public") for b in blockers):
            blockers.append({"code": "empty", "message": "No passages are left to export."})
    except Exception:
        conn.close()
        raise

    return {
        "collection": collection, "indexes_dir": indexes_dir, "conn": conn,
        "blockers": blockers, "sensitivity": sensitivity,
        "documents": kept, "held": len(held), "not_public": len(not_public),
        "info": info, "model": model, "browser": browser, "keywords_reason": keywords_reason,
        "passages": int(passages), "text_bytes": int(text_bytes),
    }


def preview(collection_id: str, passages_per_file: int = DEFAULT_PASSAGES_PER_FILE) -> Dict[str, Any]:
    """What a static export would hold, and what stands in its way."""
    from services.static_export import additions

    survey = _survey(collection_id)
    try:
        # Each addition's size, worked out while the collection is open.
        extras = additions.offers(collection_id, survey)
    finally:
        survey["conn"].close()
    semantic = survey["browser"] is not None
    dimension = int(survey["info"].get("dimension") or 0) if semantic else 0
    passages = survey["passages"]
    settings_ = _pkg().address_settings(collection_id)
    repositories = _pkg()._repositories(collection_id)
    linked = sum(1 for d in survey["documents"] if _pkg().document_link(d, settings_["files_base_url"], repositories))
    return {
        "name": survey["collection"].get("name"),
        "ready": not survey["blockers"],
        "blockers": survey["blockers"],
        "sensitivity": survey["sensitivity"],
        "documents": len(survey["documents"]),
        "passages": passages,
        "held_excluded": survey["held"],
        "not_public_excluded": survey["not_public"],
        "embedding": survey["model"],
        "browser_model": (survey["browser"] or {}).get("model"),
        # "meaning", or "keywords" when the model has no browser build (why in keywords_reason).
        "search": "meaning" if semantic else "keywords",
        "keywords_reason": survey["keywords_reason"],
        # Where results link: how many sources carry a web address.
        "addresses": {"with_address": linked, "total": len(survey["documents"]),
                      "files_base_url": settings_["files_base_url"]},
        # One byte per dimension per passage; the text plus about 90 bytes of
        # JSON around each passage.
        "vectors_bytes": passages * dimension,
        "passages_bytes": survey["text_bytes"] + passages * 90,
        "passage_files": max(1, -(-passages // max(1, passages_per_file))),
        # A ready-to-serve site with the model and its runtime (site=True).
        "site": _pkg()._site_offer(survey["browser"]),
        # What else the export can carry (include=...): each with whether it
        # can be made here, why not, and its size in bytes.
        "additions": extras,
    }


def _pkg():
    """The package, for functions a caller (a test) may have replaced there."""
    from services import static_export

    return static_export

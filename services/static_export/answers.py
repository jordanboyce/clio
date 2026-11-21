"""C2: answers written in advance, published with the export.

The owner chooses questions to publish, each with an answer that cites
passages as [Source 1], [Source 2], ... in the order of its citations, and
approves each one. At export, an answer goes out only when it is approved,
every passage it cites is in this export (so no source the gate left out is
cited), and it passes the check Ask's answers get (services/answer_check.py:
the figures, identifiers and quotes in each cited sentence are in the
passages it cites). It travels with its question's vector, embedded by
Clio's own model, so a site searched by meaning can show it above the
results when a visitor's query is close enough. Nothing is generated in
the browser.

How close is "close enough" was measured, not guessed: scripts/answer_threshold.py
on tests/fixtures/static_answers.json, the lowest cosine with no false
match and no wrong answer on the fixture, plus 0.02 (docs/STATIC_SEARCH.md
has the figures). A model not measured gets no threshold, and its answers
are listed but never matched.

Kept with the collection in the app database; carried in a bundle, where an
imported answer comes back unapproved.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from services.static_export.additions import Addition, ExportContext, register
from services.static_export.common import StaticExportError

logger = logging.getLogger(__name__)

FILE = "answers.json"
_SETTINGS = "static_export_answers:{}"
MAX_QUESTION = 300
MAX_ANSWER = 4000
MAX_CITATIONS = 12
MAX_ANSWERS = 200
EXCERPT = 240

# From scripts/answer_threshold.py on tests/fixtures/static_answers.json
# (2026-11-20): 0 false matches in 50 unrelated queries at these values.
THRESHOLDS = {"all-MiniLM-L6-v2": 0.58, "BAAI/bge-small-en-v1.5": 0.77}

# Until the owner decides (docs/ROADMAP.md, decisions): shown with every published answer.
LABEL = "Written in advance, not for your question."


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load(collection_id: str) -> List[Dict[str, Any]]:
    from services.app_database import app_db

    saved = app_db.get_config(_SETTINGS.format(collection_id), {}) or {}
    rows = saved.get("answers") if isinstance(saved, dict) else None
    return [r for r in (rows or []) if isinstance(r, dict) and r.get("id")]


def _save(collection_id: str, rows: List[Dict[str, Any]]) -> None:
    from services.app_database import app_db

    app_db.set_config(_SETTINGS.format(collection_id), {"answers": rows})


def _clean(question: Any, answer: Any, citations: Any) -> Dict[str, Any]:
    q = " ".join(str(question or "").split())
    a = str(answer or "").strip()
    cites = [str(c).strip() for c in (citations or []) if str(c).strip()]
    if not q or len(q) > MAX_QUESTION:
        raise StaticExportError(f"A question needs 1 to {MAX_QUESTION} characters.", "answer")
    if not a or len(a) > MAX_ANSWER:
        raise StaticExportError(f"An answer needs 1 to {MAX_ANSWER} characters.", "answer")
    if not cites or len(cites) > MAX_CITATIONS or len(set(cites)) != len(cites):
        raise StaticExportError(f"An answer cites 1 to {MAX_CITATIONS} different passages.", "answer")
    return {"question": q, "answer": a, "citations": cites}


def add_answer(collection_id: str, question: str, answer: str, citations: List[str], *, actor: str) -> Dict[str, Any]:
    """Save an answer to publish. It waits for approval; editing it does too."""
    rows = _load(collection_id)
    if len(rows) >= MAX_ANSWERS:
        raise StaticExportError(f"A collection can publish at most {MAX_ANSWERS} answers.", "answer")
    row = {"id": uuid.uuid4().hex[:12], **_clean(question, answer, citations), "created_by": actor,
           "created_at": _now(), "approved_by": None, "approved_at": None}
    _save(collection_id, rows + [row])
    return row


def approve_answer(collection_id: str, answer_id: str, *, actor: str, approved: bool = True) -> Dict[str, Any]:
    rows = _load(collection_id)
    for row in rows:
        if row["id"] == answer_id:
            row["approved_by"], row["approved_at"] = (actor, _now()) if approved else (None, None)
            _save(collection_id, rows)
            return row
    raise StaticExportError("There is no such answer.", "not_found")


def remove_answer(collection_id: str, answer_id: str) -> None:
    rows = _load(collection_id)
    kept = [r for r in rows if r["id"] != answer_id]
    if len(kept) == len(rows):
        raise StaticExportError("There is no such answer.", "not_found")
    _save(collection_id, kept)


def carried(collection_id: str) -> Optional[List[Dict[str, Any]]]:
    """For a bundle: the answers, without who approved them."""
    rows = _load(collection_id)
    return [{k: r.get(k) for k in ("question", "answer", "citations")} for r in rows] or None


def restore(collection_id: str, rows: Any) -> int:
    """From a bundle: each answer checked again, and waiting for approval here."""
    kept = []
    for r in rows if isinstance(rows, list) else []:
        try:
            kept.append({"id": uuid.uuid4().hex[:12], **_clean(r.get("question"), r.get("answer"), r.get("citations")),
                         "created_by": "import", "created_at": _now(), "approved_by": None, "approved_at": None})
        except (StaticExportError, AttributeError):
            continue
    if kept:
        _save(collection_id, kept[:MAX_ANSWERS])
    return len(kept)


# ── Deciding what may be published ──────────────────────────────────────────


def _passages(conn: sqlite3.Connection, chunk_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not chunk_ids:
        return {}
    marks = ",".join("?" * len(chunk_ids))
    rows = conn.execute(f"SELECT chunk_id, document_id, page_number, text FROM chunks WHERE chunk_id IN ({marks})",
                        chunk_ids).fetchall()
    return {r["chunk_id"]: {"id": r["chunk_id"], "document_id": r["document_id"], "page": r["page_number"],
                            "text": r["text"]} for r in rows}


def judge(row: Dict[str, Any], passages: Dict[str, Dict[str, Any]], exported: set) -> Dict[str, Any]:
    """Whether one answer may be published: {"status", "failures"}.
    ``passages`` holds the cited passages found in the collection; ``exported``
    the ids of the sources this export carries."""
    from services.answer_check import CITATION_GROUP, check_answer

    if not row.get("approved_at"):
        return {"status": "waiting", "failures": []}
    found = [passages.get(c) for c in row["citations"]]
    if any(p is None for p in found):
        return {"status": "missing_passage", "failures": []}
    if any(p["document_id"] not in exported for p in found):
        return {"status": "left_out_source", "failures": []}
    if not CITATION_GROUP.search(row["answer"]):
        return {"status": "no_citation", "failures": []}
    failures = check_answer(row["answer"], [{"text_snippet": p["text"]} for p in found])
    return {"status": "check_failed" if failures else "ready", "failures": failures}


STATUS_WORDS = {
    "ready": "Ready to publish.",
    "waiting": "Waiting for approval.",
    "missing_passage": "Cites a passage the collection no longer has: write it again.",
    "left_out_source": "Cites a source this export leaves out, so it is not published.",
    "no_citation": "Cites no passage as [Source 1], so it is not published.",
    "check_failed": "Says something its passages do not, so it is not published.",
}


def review(collection_id: str) -> Dict[str, Any]:
    """For the owner: every answer with whether it would be published, and why not."""
    from services.static_export import _survey

    survey = _survey(collection_id)
    try:
        rows = _load(collection_id)
        passages = _passages(survey["conn"], sorted({c for r in rows for c in r["citations"]}))
    finally:
        survey["conn"].close()
    exported = {d["document_id"] for d in survey["documents"]}
    out = []
    for row in rows:
        verdict = judge(row, passages, exported)
        out.append({**row, **verdict, "status_text": STATUS_WORDS[verdict["status"]]})
    return {"answers": out, "ready": sum(1 for r in out if r["status"] == "ready"),
            "threshold": THRESHOLDS.get(_model(survey))}


def _model(survey: Dict[str, Any]) -> Optional[str]:
    return (survey.get("browser") or {}).get("clio_model")


# ── The addition ────────────────────────────────────────────────────────────


def _offer(o) -> Dict[str, Any]:
    rows = _load(o.collection_id)
    if not rows:
        return {"available": False, "reason": "No answers have been written to publish.", "bytes": 0}
    passages = _passages(o.survey["conn"], sorted({c for r in rows for c in r["citations"]}))
    exported = {d["document_id"] for d in o.survey["documents"]}
    verdicts = [judge(r, passages, exported)["status"] for r in rows]
    ready = verdicts.count("ready")
    if not ready:
        return {"available": False, "reason": f"None of the {len(rows)} answers is ready: "
                                              f"{STATUS_WORDS[verdicts[0]]}", "bytes": 0}
    others = len(rows) - ready
    note = f"{ready} answer{'' if ready == 1 else 's'}" + (f"; {others} not ready, left out" if others else "")
    if not o.semantic or _model(o.survey) not in THRESHOLDS:
        note += ". Listed only: matching a query needs an export searched by meaning with a measured model"
    return {"available": True, "reason": None, "note": note + ".", "bytes": ready * 5000}


def _embed(model: Optional[str], texts: List[str]) -> Optional[List[List[float]]]:
    from services.indexer_manager import indexer_manager

    if not model:
        return None
    try:
        service = indexer_manager._get_embedding_service(model)
        out = []
        for t in texts:
            v = np.asarray(service.embed_query(t), dtype=np.float32).reshape(-1)
            out.append([round(float(x), 5) for x in v / (np.linalg.norm(v) or 1)])
        return out
    except Exception as e:  # noqa: BLE001 - the answers are still listed
        logger.warning(f"Static export: could not embed the published questions: {e}")
        return None


def _write(ctx: ExportContext) -> Dict[str, Any]:
    rows = _load(ctx.collection_id)
    exported_passages = {p["id"]: p for p in ctx.passages}
    exported_docs = {d["id"] for d in ctx.documents}
    passages = _passages(ctx.survey["conn"], sorted({c for r in rows for c in r["citations"]}))
    ready = [r for r in rows if judge(r, passages, exported_docs)["status"] == "ready"
             and all(c in exported_passages for c in r["citations"])]
    ready.sort(key=lambda r: (r["approved_at"], r["id"]))
    titles = {d["id"]: d.get("title") or d.get("name") for d in ctx.documents}
    model = _model(ctx.survey) if ctx.semantic else None
    vectors = _embed(model, [r["question"] for r in ready]) if model in THRESHOLDS else None
    published = []
    for i, r in enumerate(ready):
        cites = []
        for n, c in enumerate(r["citations"], start=1):
            p = exported_passages[c]
            text = " ".join((p["text"] or "").split())
            cites.append({"n": n, "id": c, "document_id": p["document_id"], "page": p["page"],
                          "text": text[:EXCERPT] + ("…" if len(text) > EXCERPT else "")})
        published.append({"id": r["id"], "question": r["question"], "answer": r["answer"],
                          "written": (r["approved_at"] or "")[:10],
                          "sources": sorted({titles[c["document_id"]] for c in cites}),
                          "citations": cites, "vector": vectors[i] if vectors else None})
    data = {"label": LABEL, "model": model if vectors else None,
            "threshold": THRESHOLDS.get(model) if vectors else None, "answers": published}
    lines = json.dumps({k: v for k, v in data.items() if k != "answers"}, ensure_ascii=False)[:-1]
    body = ",\n".join(json.dumps(a, ensure_ascii=False, separators=(",", ":")) for a in published)
    ctx.put(FILE, f'{lines}, "answers": [\n{body}\n]}}\n')
    return {"file": FILE, "count": len(published), "threshold": data["threshold"]}


register(Addition(id="answers", label="Answers written in advance", offer=_offer, write=_write))

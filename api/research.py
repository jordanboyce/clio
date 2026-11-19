"""Library endpoints (the Library tab, called Research before R9): the paper
library over a collection. The URL prefix stays /api/research; see
docs/RESEARCH.md, "API".

Business logic lives in services/research; this module resolves access,
builds the model provider from the request (exactly as /api/chat does) and
shapes responses. Reading needs read access; anything that changes the
collection or its research records (analyze, rename, download, jobs) needs
contributor (write) access, and downloading references also needs the
acceptable-use acknowledgement every ingest path requires.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Annotated, Dict, List, Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from pydantic import AfterValidator, BaseModel, Field

from api.deps import get_indexer, require_collection_access
from middleware.user_context import get_request_user
from services import data_controls, governance
from services.research import briefs as briefs_service
from services.research import jobs as jobs_service
from services.research import citations, draft_check, library, lookup, naming, network, relevance
from services.research.graph import build_graph
from services.usage_service import check_daily_budget, record_chat_usage

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_REFERENCES_PER_DOWNLOAD = 50  # the link indexer's own per-request limit


# ── request bodies ──────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    document_ids: Optional[List[str]] = Field(None, description="Papers to analyze; omit for all")
    only_new: bool = Field(True, description="Skip papers that were analyzed before")
    use_registry: bool = Field(True, description="Consult OpenAlex/Crossref/arXiv by printed identifier")


class AnalyzeOneRequest(BaseModel):
    use_registry: bool = True


class RenameRequest(BaseModel):
    filename: Optional[str] = Field(None, max_length=200, description="Explicit new name; omit to use the suggestion")
    pattern: Optional[str] = Field(None, max_length=200, description="Naming pattern, e.g. '{year} - {authors} - {title}'")


class BatchRenameRequest(BaseModel):
    document_ids: List[str] = Field(..., min_length=1, max_length=500)
    pattern: Optional[str] = Field(None, max_length=200)
    dry_run: bool = Field(False, description="Only report the names the papers would get")


class ResolveRequest(BaseModel):
    document_id: Optional[str] = None
    reference_ids: Optional[List[int]] = Field(None, max_length=500)


class DownloadRequest(BaseModel):
    document_id: Optional[str] = Field(None, description="Take every reference of this paper")
    reference_ids: Optional[List[int]] = Field(None, max_length=500)
    include_links: bool = Field(False, description="Also fetch URLs written in the paper's body")
    include_pages: bool = Field(False, description="Fetch landing pages when no open PDF exists")


class AssessRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=1000)
    provider: Optional[str] = Field(None, description="AI provider; omit for the model-free evidence view")


# Job kinds as the API names them since R9, mapped to the kind stored on the
# job row (and returned in it). The stored names are the pre-R9 ones and are
# still accepted.
_JOB_KINDS = {"screen": "assess", "review": "brief", "assess": "assess", "brief": "brief"}


class JobRequest(BaseModel):
    kind: Annotated[Literal["screen", "review", "assess", "brief"], AfterValidator(_JOB_KINDS.get)] = Field(
        ..., description="screen | review (\"assess\" and \"brief\" are the old names, still accepted). "
                         "Job rows report the stored kind: assess or brief.")
    document_ids: Optional[List[str]] = Field(None, description="Papers to cover; omit for all")
    question: Optional[str] = Field(None, max_length=1000)
    depth: Literal["brief", "thorough"] = Field("brief", description="brief | thorough (brief jobs)")
    provider: str = Field(..., description="AI provider the job runs on")
    not_before: Optional[str] = Field(None, description="ISO time to start at (e.g. tonight)")
    skip_reviewed: bool = Field(True, description="Review jobs: skip papers that already have a review at this depth")


class BriefReadRequest(BaseModel):
    read: bool = True


StyleName = Literal["apa", "chicago", "mla", "ieee", "harvard"]


class RecordCorrection(BaseModel):
    changes: Dict[str, object] = Field(default_factory=dict, description=(
        "Fields to set: title, authors (list), year, venue, doi, isbn, kind, organization, publisher, "
        "place, edition, volume, issue, pages, report_number, url, editors, container_title, month, day. "
        "An empty value clears the field."))
    revert: List[str] = Field(default_factory=list, description="Fields to put back to what was read")


class CitePage(BaseModel):
    document_id: str
    page: Optional[int] = Field(None, ge=1, description="Page of the file the passage is on")


class CitationRequest(BaseModel):
    document_ids: Optional[List[str]] = Field(None, max_length=2000, description="Sources; omit for all")
    passages: Optional[List[CitePage]] = Field(None, max_length=500, description=(
        "Pages to cite, in order (e.g. a chat answer's sources): each gets an in-text citation with "
        "its printed page; numbering (IEEE) follows this order"))
    style: StyleName = "apa"
    export: Optional[Literal["bibtex", "ris", "csl-json"]] = Field(None, description="Also return a file")
    include_location: bool = Field(True, description="Say where internal sources are kept")


class DraftCheckRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=draft_check.MAX_DRAFT_CHARS)
    document_ids: Optional[List[str]] = Field(None, description="Sources to check against; omit for all")
    style: StyleName = "apa"
    provider: Optional[str] = Field(None, description="AI provider for claim verdicts; omit for none")
    suggest_uncited: bool = True
    include_location: bool = True


# ── helpers ─────────────────────────────────────────────────────────────

def _indexer(collection_id: str):
    try:
        return get_indexer(collection_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


def _require_write(collection_id: str) -> None:
    require_collection_access(collection_id, get_request_user(), write=True)


def _can_write(collection_id: str) -> bool:
    try:
        _require_write(collection_id)
        return True
    except HTTPException:
        return False


def _papers(indexer) -> List[dict]:
    """The collection's servable documents that can be papers."""
    return [d for d in indexer.list_documents()
            if library.is_paper(d) and not governance.is_hidden(d)]


def _hidden_ids(indexer) -> List[str]:
    """The quarantined documents, for dropping stale links that point at them."""
    return [d["document_id"] for d in indexer.list_documents() if governance.is_hidden(d)]


def _paper_doc(indexer, document_id: str) -> dict:
    for doc in _papers(indexer):
        if doc["document_id"] == document_id:
            return doc
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Source {document_id} not found")


def _select(indexer, document_ids: Optional[List[str]]) -> List[dict]:
    docs = _papers(indexer)
    if document_ids:
        wanted = set(document_ids)
        docs = [d for d in docs if d["document_id"] in wanted]
        if not docs:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="None of those sources were found")
    return docs


def _provider(provider_name: str, x_ai_key, x_ollama_model, x_anthropic_model,
              x_openai_model, x_ai_model, x_ai_base_url):
    """The provider and model a request runs on, resolved exactly as /api/chat does."""
    from api.chat import _build_provider
    from services.ai_service import AIService

    provider = _build_provider(provider_name, x_ai_key, x_ollama_model, x_anthropic_model,
                               x_openai_model, x_ai_model, x_ai_base_url)
    return provider, AIService(provider=provider).quality_model


def _budget_or_429(user) -> None:
    over = check_daily_budget(user)
    if over:
        raise HTTPException(status_code=429, detail=over.get("detail") or "Daily token budget reached",
                            headers={"Retry-After": str(over.get("retry_after_seconds", 3600))})


def _record_usage(user, collection_id: str, provider_name: str, usage: dict, started: float) -> None:
    if not usage:
        return
    record_chat_usage(user, collection_id, provider_name, usage.get("model"),
                      input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
                      duration_ms=int((time.time() - started) * 1000))


def _paper_summary(doc: dict, paper: Optional[dict], counts: dict) -> dict:
    paper = paper or {}
    did = doc["document_id"]
    doubtful = library.needs_checking(paper)
    unreachable = any(str(n).startswith("The catalogue could not be reached")
                      for n in (paper.get("analysis_notes") or []))
    suggested = naming.suggest_filename(paper, doc.get("filename") or "") if paper else None
    return {
        "document_id": did,
        "filename": doc.get("filename"),
        "source_format": doc.get("source_format"),
        "source_type": doc.get("source_type"),
        "source_path": doc.get("source_path") if doc.get("source_type") == "url" else None,
        "uploaded": doc.get("upload_timestamp"),
        "num_pages": doc.get("num_pages"),
        "kind": paper.get("kind"),
        "isbn": paper.get("isbn"),
        "details": paper.get("details") or {},
        "corrected_at": paper.get("corrected_at"),
        "missing": citations.build_item(doc, paper)["missing"],
        "analyzed": bool(paper.get("analyzed_at")),
        "analyzed_at": paper.get("analyzed_at"),
        "title": paper.get("title"),
        "authors": paper.get("authors") or [],
        "year": paper.get("year"),
        "venue": paper.get("venue"),
        "doi": paper.get("doi"),
        "arxiv_id": paper.get("arxiv_id"),
        "abstract": paper.get("abstract"),
        "provenance": paper.get("provenance") or {},
        # R3: what is not known to be right, and why. The Library shows the
        # file name in place of a doubtful title and "Authors need checking"
        # in place of doubtful names.
        "needs_checking": doubtful,
        "suggested_filename": suggested,
        # A3: a descriptive name is offered by default only when the title
        # and authors it is built from are known to be right.
        "rename_ready": bool(suggested) and not ({"title", "authors"} & set(doubtful)),
        # A6: its record was read while the catalogues could not be reached.
        "catalogue_unreachable": unreachable,
        "original_filename": paper.get("original_filename"),
        "renamed_at": paper.get("renamed_at"),
        "references": counts.get("references", {}).get(did, 0),
        "links": counts.get("links", {}).get(did, 0),
        "cited_by": counts.get("cited_by", {}).get(did, 0),
        "review_count": counts.get("briefs", {}).get(did, 0),
        "unread_reviews": counts.get("unread", {}).get(did, 0),
        # Pre-R9 names of the two counts above, kept for one release.
        "briefs": counts.get("briefs", {}).get(did, 0),
        "unread_briefs": counts.get("unread", {}).get(did, 0),
    }


def _counts(store) -> dict:
    counts: Dict[str, Dict[str, int]] = {"references": {}, "links": {}, "cited_by": {}, "briefs": {}, "unread": {}}
    for ref in store.list_references():
        key = "references" if ref["kind"] == "reference" else "links"
        counts[key][ref["document_id"]] = counts[key].get(ref["document_id"], 0) + 1
        target = ref.get("matched_document_id")
        if target:
            counts["cited_by"][target] = counts["cited_by"].get(target, 0) + 1
    for document_id, total, unread in store.brief_counts():
        counts["briefs"][document_id] = total
        counts["unread"][document_id] = unread
    return counts


def _capabilities() -> dict:
    from services.link_fetcher import link_indexing_available
    return {"registry_lookups": lookup.lookup_available(), "link_download": link_indexing_available(),
            # A6: catalogues resting after a failure (host names only).
            "catalogues_unreachable": lookup.unreachable_hosts() if lookup.lookup_available() else []}


# ── library ─────────────────────────────────────────────────────────────

@router.get("/api/research/papers", tags=["research"], summary="The collection's papers with their research records")
def list_papers(collection_id: str = "default") -> dict:
    indexer = _indexer(collection_id)
    store = library.research_store(indexer)
    docs = _papers(indexer)
    papers = store.get_papers([d["document_id"] for d in docs])
    counts = _counts(store)
    items = [_paper_summary(d, papers.get(d["document_id"]), counts) for d in docs]
    return {
        "papers": items,
        "stats": {
            "papers": len(items),
            "analyzed": sum(1 for p in items if p["analyzed"]),
            "needs_checking": sum(1 for p in items if p["needs_checking"]),
            "catalogue_unreachable": sum(1 for p in items if p["catalogue_unreachable"]),
            "renameable": sum(1 for p in items if p["suggested_filename"]
                              and p["suggested_filename"] != p["filename"]),
            "unread_reviews": sum(p["unread_reviews"] for p in items),
            "unread_briefs": sum(p["unread_reviews"] for p in items),  # pre-R9 name
        },
        "capabilities": _capabilities(),
        "can_write": _can_write(collection_id),
        "naming": {"default_pattern": naming.DEFAULT_PATTERN, "fields": list(naming.PATTERN_FIELDS)},
    }


@router.get("/api/research/papers/{document_id}", tags=["research"], summary="One paper: record, references, reviews")
def get_paper(document_id: str, collection_id: str = "default") -> dict:
    indexer = _indexer(collection_id)
    doc = _paper_doc(indexer, document_id)
    store = library.research_store(indexer)
    paper = store.get_paper(document_id)
    out = _paper_summary(doc, paper, _counts(store))
    out["analysis_notes"] = (paper or {}).get("analysis_notes") or []
    refs = store.list_references(document_id)
    names = {d["document_id"]: d.get("filename") for d in _papers(indexer)}
    for ref in refs:
        ref["matched_filename"] = names.get(ref.get("matched_document_id"))
    out["reference_list"] = [r for r in refs if r["kind"] == "reference"]
    out["link_list"] = [r for r in refs if r["kind"] == "link"]
    out["cited_by_list"] = [
        {"document_id": r["document_id"], "filename": names.get(r["document_id"]), "reason": r.get("match_reason")}
        for r in store.list_references(kind="reference")
        if r.get("matched_document_id") == document_id and r["document_id"] in names
    ]
    out["screenings"] = store.list_assessments(document_id=document_id, limit=10)
    out["reviews"] = store.list_briefs(document_id=document_id, limit=10)
    # Pre-R9 names of the two lists above, kept for one release.
    out["assessments"] = out["screenings"]
    out["brief_list"] = out["reviews"]
    out["capabilities"] = _capabilities()
    out["can_write"] = _can_write(collection_id)
    return out


@router.post("/api/research/papers/{document_id}/analyze", tags=["research"],
             summary="Read one paper's record and references now")
def analyze_paper(document_id: str, body: AnalyzeOneRequest = AnalyzeOneRequest(),
                  collection_id: str = "default") -> dict:
    indexer = _indexer(collection_id)
    _require_write(collection_id)
    _paper_doc(indexer, document_id)
    store = library.research_store(indexer)
    library.analyze_document(collection_id, indexer, document_id, use_registry=body.use_registry, store=store)
    library.match_library(store, _papers(indexer))
    return get_paper(document_id, collection_id)


@router.post("/api/research/analyze", tags=["research"], status_code=status.HTTP_202_ACCEPTED,
             summary="Analyze many papers in the background (no model involved)")
def analyze_many(body: AnalyzeRequest, response: Response, collection_id: str = "default") -> dict:
    """202 with a job when something was queued; 200 with ``job: null``
    when every selected paper is already analyzed.

    Analysis needs no model, so a selection larger than one job may cover
    is split into several jobs that run one after another: ``jobs`` lists
    them all and ``job`` is the first (what older clients follow)."""
    indexer = _indexer(collection_id)
    _require_write(collection_id)
    store = library.research_store(indexer)
    docs = _select(indexer, body.document_ids)
    if body.only_new:
        done = store.get_papers([d["document_id"] for d in docs])
        docs = [d for d in docs if not (done.get(d["document_id"]) or {}).get("analyzed_at")]
    if not docs:
        response.status_code = status.HTTP_200_OK
        return {"job": None, "message": "Every selected source is already analyzed."}
    size = jobs_service.max_documents()
    chunks = [docs[i:i + size] for i in range(0, len(docs), size)]
    started: List[dict] = []
    for n, chunk in enumerate(chunks, start=1):
        params = {"use_registry": body.use_registry}
        if len(chunks) > 1:
            params.update(part=n, parts=len(chunks))
        try:
            started.append(jobs_service.start_job(
                collection_id=collection_id, store=store, kind="analyze", documents=chunk,
                work=lambda d, _job_id: library.analyze_document(collection_id, indexer, d["document_id"],
                                                                 use_registry=body.use_registry, store=store),
                finish=lambda: library.match_library(store, _papers(indexer)),
                params=params, created_by=get_request_user(),
            ))
        except ValueError as e:
            for job in started:
                jobs_service.cancel_job(store, job["job_id"])
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return {"job": started[0], "jobs": started}


@router.post("/api/research/papers/{document_id}/rename", tags=["research"],
             summary="Rename a paper (to its suggested descriptive name by default)")
def rename_paper(document_id: str, body: RenameRequest, collection_id: str = "default") -> dict:
    indexer = _indexer(collection_id)
    _require_write(collection_id)
    doc = _paper_doc(indexer, document_id)
    store = library.research_store(indexer)
    new_name = body.filename
    if not new_name:
        try:
            new_name = naming.suggest_filename(store.get_paper(document_id), doc["filename"],
                                               body.pattern or naming.DEFAULT_PATTERN)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        if not new_name:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="No title is known for this source yet, so there is no name to suggest. "
                                       "Analyze it first, or type a name.")
    try:
        return naming.rename_document(collection_id, indexer, document_id, new_name, store=store)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Source {document_id} not found")


@router.post("/api/research/papers/{document_id}/restore-name", tags=["research"],
             summary="Give a renamed paper back the name it arrived with")
def restore_name(document_id: str, collection_id: str = "default") -> dict:
    indexer = _indexer(collection_id)
    _require_write(collection_id)
    _paper_doc(indexer, document_id)
    store = library.research_store(indexer)
    original = (store.get_paper(document_id) or {}).get("original_filename")
    if not original:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This source has not been renamed")
    return naming.rename_document(collection_id, indexer, document_id, original, store=store)


@router.post("/api/research/rename", tags=["research"], summary="Rename many papers to their suggested names")
def rename_many(body: BatchRenameRequest, collection_id: str = "default") -> dict:
    """With ``dry_run`` this is the preview: the names each paper would get
    (a name taken by another file would still gain a " (2)" on the real run)."""
    indexer = _indexer(collection_id)
    _require_write(collection_id)
    store = library.research_store(indexer)
    docs = _select(indexer, body.document_ids)
    papers = store.get_papers([d["document_id"] for d in docs])
    renamed, skipped = [], []
    for doc in docs:
        try:
            name = naming.suggest_filename(papers.get(doc["document_id"]), doc["filename"],
                                           body.pattern or naming.DEFAULT_PATTERN)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        if not name:
            skipped.append({"document_id": doc["document_id"], "filename": doc["filename"],
                            "reason": "no title known (analyze it first)"})
            continue
        if name == doc["filename"]:
            skipped.append({"document_id": doc["document_id"], "filename": doc["filename"],
                            "reason": "already has that name"})
            continue
        if body.dry_run:
            renamed.append({"document_id": doc["document_id"], "filename": name,
                            "previous_filename": doc["filename"], "file_moved": False})
            continue
        try:
            renamed.append(naming.rename_document(collection_id, indexer, doc["document_id"], name, store=store))
        except (ValueError, KeyError, OSError) as e:
            skipped.append({"document_id": doc["document_id"], "filename": doc["filename"], "reason": str(e)})
    return {"renamed": renamed, "skipped": skipped}


# ── references ──────────────────────────────────────────────────────────

def _chosen_references(store, body_document_id: Optional[str], reference_ids: Optional[List[int]],
                       include_links: bool = True) -> List[dict]:
    if reference_ids:
        refs = store.get_references(reference_ids)
    elif body_document_id:
        refs = store.list_references(body_document_id)
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name a source or some references")
    if not include_links:
        refs = [r for r in refs if r["kind"] == "reference"]
    return refs


def _waiting_for_catalogue(store, docs: List[dict]) -> List[dict]:
    papers = store.get_papers([d["document_id"] for d in docs])
    return [d for d in docs if any(str(n).startswith("The catalogue could not be reached")
                                   for n in ((papers.get(d["document_id"]) or {}).get("analysis_notes") or []))]


@router.post("/api/research/lookups/retry", tags=["research"], status_code=status.HTTP_202_ACCEPTED,
             summary="Ask the catalogues again for sources read while they could not be reached")
def retry_lookups(response: Response, collection_id: str = "default") -> dict:
    """A6: clears the rest every unanswered catalogue was given (R6) and
    analyses again the sources whose record says the catalogue could not be
    reached. 200 with ``job: null`` when there are none."""
    indexer = _indexer(collection_id)
    _require_write(collection_id)
    lookup.reset_hosts()
    store = library.research_store(indexer)
    stale = _waiting_for_catalogue(store, _papers(indexer))
    if not stale:
        response.status_code = status.HTTP_200_OK
        return {"job": None, "message": "No source is waiting for a catalogue."}
    started = jobs_service.start_job(
        collection_id=collection_id, store=store, kind="analyze", documents=stale[:jobs_service.max_documents()],
        work=lambda d, _job_id: library.analyze_document(collection_id, indexer, d["document_id"],
                                                         use_registry=True, store=store),
        finish=lambda: library.match_library(store, _papers(indexer)),
        params={"use_registry": True, "retry": True}, created_by=get_request_user(),
    )
    return {"job": started}


@router.post("/api/research/papers/{document_id}/find-copies", tags=["research"],
             status_code=status.HTTP_202_ACCEPTED,
             summary="Look for open copies of a paper's references, as a background job")
def find_open_copies(document_id: str, response: Response, collection_id: str = "default") -> dict:
    """R7: one job entry per reference, so the Library's job strip counts
    them and Stop takes effect between two lookups. A reference whose
    catalogue could not be asked counts as failed (nothing is recorded for
    it). 200 with ``job: null`` when nothing is left to look up."""
    indexer = _indexer(collection_id)
    _require_write(collection_id)
    doc = _paper_doc(indexer, document_id)
    if not lookup.lookup_available():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Catalogue lookups are off on this server.")
    if data_controls.holds_local_only(collection_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="This collection holds data marked local only, so its references are not "
                                   "sent to outside catalogues.")
    store = library.research_store(indexer)
    refs = [r for r in store.list_references(document_id) if r["kind"] == "reference"
            and r.get("status") not in ("in_library", "queued") and r.get("resolved_kind") != "pdf"]
    if not refs:
        response.status_code = status.HTTP_200_OK
        return {"job": None, "message": "Every reference already has an open copy, or is in the library."}
    refs = refs[:jobs_service.max_documents()]
    entries = [{"document_id": document_id, "filename": f"reference {r['ordinal']}", "reference": r}
               for r in refs]

    def work(entry: dict, _job_id: str) -> None:
        (result,) = library.resolve_references(store, [entry["reference"]])
        if result.get("lookup_failed"):
            raise lookup.Unreachable(result["lookup_failed"])

    started = jobs_service.start_job(
        collection_id=collection_id, store=store, kind="resolve", documents=entries, work=work,
        finish=lambda: library.match_library(store, _papers(indexer)),
        params={"document_id": document_id, "filename": doc.get("filename")}, created_by=get_request_user(),
    )
    return {"job": started}


@router.post("/api/research/references/resolve", tags=["research"],
             summary="Look for open copies of references (registry lookups)")
async def resolve_references(body: ResolveRequest, collection_id: str = "default") -> dict:
    indexer = _indexer(collection_id)
    _require_write(collection_id)
    store = library.research_store(indexer)
    refs = _chosen_references(store, body.document_id, body.reference_ids)
    if not lookup.lookup_available():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Registry lookups are off on this server (offline mode or RESEARCH_LOOKUP_ENABLED=false)")
    if data_controls.holds_local_only(collection_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="This collection holds data marked local only, so its references are not "
                                   "sent to outside catalogues.")
    resolved = await asyncio.to_thread(library.resolve_references, store, refs)
    failed = [r for r in resolved if r.get("lookup_failed")]
    out: Dict[str, object] = {"references": resolved, "lookup_failed": len(failed)}
    if failed:
        out["message"] = (f"{len(failed)} reference{'' if len(failed) == 1 else 's'} could not be looked up "
                          f"because the catalogue could not be reached ({failed[0]['lookup_failed']}). "
                          "Nothing was recorded for them; try again later.")
    return out


@router.post("/api/research/references/download", tags=["research"], status_code=status.HTTP_202_ACCEPTED,
             summary="Fetch and index a paper's references and links into the collection")
async def download_references(body: DownloadRequest, response: Response, collection_id: str = "default") -> dict:
    from services.link_fetcher import LinkError, LinkRefused, link_indexing_available, validate_link
    from services.upload_service import upload_service

    indexer = _indexer(collection_id)
    if not link_indexing_available():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Downloading is unavailable: this server does not fetch web links")
    _require_write(collection_id)
    governance.require_aup(get_request_user())
    store = library.research_store(indexer)
    refs = _chosen_references(store, body.document_id, body.reference_ids, include_links=body.include_links
                              or bool(body.reference_ids))
    if lookup.lookup_available() and not data_controls.holds_local_only(collection_id):
        refs = await asyncio.to_thread(library.resolve_references, store, refs)
    wanted = library.download_urls(refs, include_pages=body.include_pages)

    skipped = []
    by_id = {int(r["id"]): r for r in refs}
    for ref in refs:
        if int(ref["id"]) in wanted:
            continue
        reason = ("already in this collection" if ref.get("status") == "in_library"
                  else "already queued" if ref.get("status") == "queued"
                  else f"not looked up: the catalogue could not be reached ({ref['lookup_failed']}); try again later"
                  if ref.get("lookup_failed")
                  else "no open copy found" + ("" if body.include_pages else " (landing pages not requested)"))
        skipped.append({"reference_id": ref["id"], "text": (ref.get("raw_text") or "")[:200], "reason": reason})

    valid: Dict[int, str] = {}
    seen = set()
    for ref_id, url in wanted.items():
        try:
            clean = await asyncio.to_thread(validate_link, url)
        except (LinkError, LinkRefused) as e:
            skipped.append({"reference_id": ref_id, "text": (by_id[ref_id].get("raw_text") or "")[:200],
                            "reason": str(e)})
            continue
        if clean in seen:
            continue
        seen.add(clean)
        valid[ref_id] = clean
    if len(valid) > MAX_REFERENCES_PER_DOWNLOAD:
        for ref_id in list(valid)[MAX_REFERENCES_PER_DOWNLOAD:]:
            skipped.append({"reference_id": ref_id, "text": (by_id[ref_id].get("raw_text") or "")[:200],
                            "reason": f"over the {MAX_REFERENCES_PER_DOWNLOAD}-link limit for one download; run it again"})
            valid.pop(ref_id)
    if not valid:
        # Nothing to fetch, so nothing was accepted for later: plain 200.
        response.status_code = status.HTTP_200_OK
        return {"job_id": None, "queued": [], "skipped": skipped}

    try:
        job_id = upload_service.start_link_index(urls=list(valid.values()), collection_id=collection_id,
                                                 uploaded_by=get_request_user())
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    for ref_id in valid:
        store.update_reference(ref_id, status="queued")
    # Shaped like /documents/index-links so the app's job tracker follows it.
    job = {"job_id": job_id, "collection_id": collection_id, "status": "pending",
           "total_files": len(valid), "processed_files": 0, "progress_percent": 0.0,
           "job_type": "index", "queue_position": upload_service.queue_position(job_id)}
    return {"job_id": job_id, "job": job,
            "queued": [{"reference_id": rid, "url": url} for rid, url in valid.items()],
            "skipped": skipped}


# ── graph ───────────────────────────────────────────────────────────────

class GraphRequest(BaseModel):
    document_ids: Optional[List[str]] = Field(None, max_length=5000,
                                              description="Limit the map to these sources (the user's selection)")
    include_external: bool = True
    min_citations: int = Field(2, ge=1, le=20)


@router.get("/api/research/graph", tags=["research"], summary="Citation map of the collection's sources")
def citation_graph(collection_id: str = "default", include_external: bool = True, min_citations: int = 2) -> dict:
    return citation_graph_for(GraphRequest(include_external=include_external,
                                           min_citations=max(1, min(int(min_citations), 20))), collection_id)


@router.post("/api/research/graph", tags=["research"], summary="Citation map of selected sources")
def citation_graph_for(body: GraphRequest, collection_id: str = "default") -> dict:
    """With ``document_ids`` the map holds only those sources, the citations
    among them, and the works several of them cite."""
    indexer = _indexer(collection_id)
    store = library.research_store(indexer)
    docs = _select(indexer, body.document_ids) if body.document_ids else _papers(indexer)
    return build_graph(store, docs, include_external=body.include_external,
                       min_citations=body.min_citations, hidden_ids=_hidden_ids(indexer))


# ── network analysis ────────────────────────────────────────────────────

class NetworkRequest(GraphRequest):
    graph: Literal["citation", "authors", "entities"] = Field("citation", description="citation | authors | entities")
    seed: int = Field(network.SEED, ge=0, description="Louvain and sampling seed, for checking how stable a result is")


@router.get("/api/research/network", tags=["research"], summary="Network analysis of the collection's sources")
def network_analysis(collection_id: str = "default", graph: Literal["citation", "authors", "entities"] = "citation",
                     include_external: bool = True, min_citations: int = 2,
                     seed: int = Query(network.SEED, ge=0)) -> dict:
    return network_analysis_for(NetworkRequest(graph=graph, include_external=include_external, seed=seed,
                                               min_citations=max(1, min(int(min_citations), 20))), collection_id)


@router.post("/api/research/network", tags=["research"], summary="Network analysis of selected sources")
def network_analysis_for(body: NetworkRequest, collection_id: str = "default") -> dict:
    """Which papers, authors or concepts hold these sources together, with the
    links behind each claim and a note on how far the graph can be trusted. The
    documents are those of the citation graph, so a quarantined one is never in
    it. Only the citation graph depends on ``include_external``/``min_citations``."""
    indexer = _indexer(collection_id)
    store = library.research_store(indexer)
    docs = _select(indexer, body.document_ids) if body.document_ids else _papers(indexer)
    hidden = _hidden_ids(indexer)
    citation = body.graph == "citation"
    key = (str(indexer.vector_store.metadata_db_path), indexer.vector_store.metadata_store.corpus_version(),
           store.version_token(), body.graph, body.include_external if citation else None,
           body.min_citations if citation else None, body.seed,
           network.documents_digest((d["document_id"] for d in docs), hidden))

    def compute() -> dict:
        built = build_graph(store, docs, include_external=citation and body.include_external,
                            min_citations=body.min_citations, hidden_ids=hidden)
        if citation:
            return network.citation_network(built, seed=body.seed)
        if body.graph == "authors":
            return network.author_network(built, seed=body.seed)
        papers = [n for n in built["nodes"] if n["type"] == "paper"]
        titles = {n["id"]: n["title"] or n["filename"] for n in papers}
        return network.entity_network(
            network.load_document_entities(indexer.vector_store.metadata_store, papers), titles, seed=body.seed,
            exclude=network.author_name_forms(a for n in papers for a in n["authors"]))

    return network.cached(key, compute)


# ── screening ───────────────────────────────────────────────────────────
# R9 renamed "assess" to "screen" and "briefs" to "reviews" in the API too.
# The old paths and job kinds are served for one release; the database keeps
# its names (research_assessments, research_briefs, job kinds "assess" and
# "brief"), and so do the job rows the API returns.

@router.post("/api/research/papers/{document_id}/screen", tags=["research"],
             summary="Screening: is this paper worth reading for my question?")
@router.post("/api/research/papers/{document_id}/assess", tags=["research"], deprecated=True,
             summary="Old name of /screen, kept for one release")
def assess_paper(
    document_id: str,
    body: AssessRequest,
    collection_id: str = "default",
    x_ai_key: str = Header(None), x_ollama_model: str = Header(None), x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None), x_ai_model: str = Header(None), x_ai_base_url: str = Header(None),
) -> dict:
    started = time.time()
    indexer = _indexer(collection_id)
    _paper_doc(indexer, document_id)
    user = get_request_user()
    provider = model = None
    if body.provider:
        _budget_or_429(user)
        provider, model = _provider(body.provider, x_ai_key, x_ollama_model, x_anthropic_model,
                                    x_openai_model, x_ai_model, x_ai_base_url)
        data_controls.guard_model_call(collection_id, provider)
    try:
        result = relevance.assess(indexer, document_id, body.question, provider=provider, model=model)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:  # noqa: BLE001 - a provider failure is the caller's to see
        logger.warning("Assessment failed for %s: %s", document_id, e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"The model call failed: {e}")
    if body.provider:
        _record_usage(user, collection_id, body.provider, result.get("usage") or {}, started)
    saved = None
    if result.get("verdict") and _can_write(collection_id):
        saved = library.research_store(indexer).add_assessment(document_id, body.question, result)
    return {**result, "saved_id": saved["id"] if saved else None, "question": body.question}


@router.get("/api/research/screening", tags=["research"],
            summary="A reading list: saved screening verdicts for a question")
@router.get("/api/research/assessments", tags=["research"], deprecated=True,
            summary="Old name of /screening, kept for one release")
def reading_list(question: Optional[str] = None, collection_id: str = "default") -> dict:
    indexer = _indexer(collection_id)
    store = library.research_store(indexer)
    names = {d["document_id"]: d.get("filename") for d in _papers(indexer)}
    rows = [a for a in store.list_assessments(question=question, limit=2000) if a["document_id"] in names]
    latest: Dict[str, dict] = {}
    for row in rows:  # newest first
        latest.setdefault(row["document_id"], row)
    ranked = relevance.quick_rank(latest)
    questions = []
    for row in store.list_assessments(limit=2000):
        if row["question"] not in questions:
            questions.append(row["question"])
    return {
        "question": question,
        "questions": questions[:50],
        "items": [{**latest[d], "filename": names[d]} for d in ranked],
    }


# ── background jobs ─────────────────────────────────────────────────────

@router.post("/api/research/jobs", tags=["research"], status_code=status.HTTP_202_ACCEPTED,
             summary="Screen or review many papers in the background (e.g. overnight)")
def start_research_job(
    body: JobRequest,
    collection_id: str = "default",
    x_ai_key: str = Header(None), x_ollama_model: str = Header(None), x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None), x_ai_model: str = Header(None), x_ai_base_url: str = Header(None),
) -> dict:
    # kind and depth are Literals on JobRequest (422 on anything else).
    if body.kind == "assess" and not (body.question or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A screening job needs a question")
    indexer = _indexer(collection_id)
    _require_write(collection_id)
    user = get_request_user()
    _budget_or_429(user)
    provider, model = _provider(body.provider, x_ai_key, x_ollama_model, x_anthropic_model,
                                    x_openai_model, x_ai_model, x_ai_base_url)
    store = library.research_store(indexer)
    docs = _select(indexer, body.document_ids)
    if body.kind == "brief" and body.skip_reviewed:
        reviewed = store.reviewed_documents(body.depth, (body.question or "").strip() or None)
        docs = [d for d in docs if d["document_id"] not in reviewed]
        if not docs:
            return {"job": None, "message": "Every selected source already has a review like this."}
    question = (body.question or "").strip() or None

    data_controls.guard_model_call(collection_id, provider)

    def work(doc: dict, job_id: str) -> None:
        started = time.time()
        # Asked again for each paper: one may have been marked since the job began.
        data_controls.guard_model_call(collection_id, provider)
        if body.kind == "assess":
            try:
                result = relevance.assess(indexer, doc["document_id"], question, provider=provider, model=model)
            except relevance.ModelReplyError as exc:
                # The paper counts as failed (no verdict saved); the tokens were still spent.
                _record_usage(user, collection_id, body.provider, getattr(exc, "usage", None) or {}, started)
                raise
            _record_usage(user, collection_id, body.provider, result.get("usage") or {}, started)
            store.add_assessment(doc["document_id"], question, result, job_id=job_id)
        else:
            result = briefs_service.prepare(indexer, doc["document_id"], provider, model,
                                            depth=body.depth, question=question)
            _record_usage(user, collection_id, body.provider, result.get("usage") or {}, started)
            store.add_brief(doc["document_id"], body.depth, result, question=question, job_id=job_id)

    def budget_check() -> Optional[str]:
        over = check_daily_budget(user)
        return (over.get("detail") or "Daily token budget reached") if over else None

    try:
        job = jobs_service.start_job(
            collection_id=collection_id, store=store, kind=body.kind, documents=docs, work=work,
            params={"question": question, "depth": body.depth, "provider": body.provider, "model": model},
            created_by=user, not_before=body.not_before, budget_check=budget_check,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return {"job": job}


@router.get("/api/research/jobs", tags=["research"], summary="Recent Library jobs")
def list_research_jobs(collection_id: str = "default") -> dict:
    indexer = _indexer(collection_id)
    return {"jobs": jobs_service.list_jobs(library.research_store(indexer))}


@router.get("/api/research/jobs/{job_id}", tags=["research"], summary="One Library job")
def get_research_job(job_id: str, collection_id: str = "default") -> dict:
    indexer = _indexer(collection_id)
    store = library.research_store(indexer)
    jobs_service.reconcile(store)
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.post("/api/research/jobs/{job_id}/cancel", tags=["research"], summary="Stop a Library job")
def cancel_research_job(job_id: str, collection_id: str = "default") -> dict:
    indexer = _indexer(collection_id)
    _require_write(collection_id)
    store = library.research_store(indexer)
    if not store.get_job(job_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return {"cancelling": jobs_service.cancel_job(store, job_id)}


@router.delete("/api/research/jobs", tags=["research"],
               summary="Clear finished Library jobs from the list (their reviews and verdicts stay)")
def clear_research_jobs(collection_id: str = "default") -> dict:
    indexer = _indexer(collection_id)
    _require_write(collection_id)
    return {"removed": jobs_service.clear_finished(library.research_store(indexer))}


@router.delete("/api/research/jobs/{job_id}", tags=["research"],
               summary="Remove one finished Library job from the list (its reviews and verdicts stay)")
def remove_research_job(job_id: str, collection_id: str = "default") -> dict:
    indexer = _indexer(collection_id)
    _require_write(collection_id)
    store = library.research_store(indexer)
    if not store.get_job(job_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if not jobs_service.remove_job(store, job_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="This job is still waiting or running. Stop it first.")
    return {"removed": job_id}


# ── review queue ────────────────────────────────────────────────────────

def _review_items(collection_id: str, unread: bool, document_id: Optional[str]) -> List[dict]:
    indexer = _indexer(collection_id)
    store = library.research_store(indexer)
    docs = {d["document_id"]: d for d in _papers(indexer)}
    papers = store.get_papers(list(docs))
    items = []
    for brief in store.list_briefs(document_id=document_id, unread=unread, limit=500):
        doc = docs.get(brief["document_id"])
        if not doc:
            continue
        paper = papers.get(brief["document_id"]) or {}
        items.append({**brief, "filename": doc.get("filename"), "title": paper.get("title"),
                      "authors": paper.get("authors") or [], "year": paper.get("year")})
    return items


@router.get("/api/research/reviews", tags=["research"], summary="Prepared reviews, newest first")
def list_reviews(collection_id: str = "default", unread: bool = False, document_id: Optional[str] = None) -> dict:
    items = _review_items(collection_id, unread, document_id)
    return {"reviews": items, "unread": sum(1 for b in items if not b.get("read_at"))}


@router.get("/api/research/briefs", tags=["research"], deprecated=True,
            summary="Old name of /reviews (the list is under \"briefs\"), kept for one release")
def list_briefs(collection_id: str = "default", unread: bool = False, document_id: Optional[str] = None) -> dict:
    items = _review_items(collection_id, unread, document_id)
    return {"briefs": items, "unread": sum(1 for b in items if not b.get("read_at"))}


@router.post("/api/research/reviews/{brief_id}/read", tags=["research"], summary="Mark a review read or unread")
@router.post("/api/research/briefs/{brief_id}/read", tags=["research"], deprecated=True,
             summary="Old name of /reviews/{id}/read, kept for one release")
def mark_brief(brief_id: int, body: BriefReadRequest = BriefReadRequest(), collection_id: str = "default") -> dict:
    indexer = _indexer(collection_id)
    if not library.research_store(indexer).mark_brief_read(brief_id, body.read):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
    return {"id": brief_id, "read": body.read}


@router.delete("/api/research/reviews/{brief_id}", tags=["research"], summary="Delete a review")
@router.delete("/api/research/briefs/{brief_id}", tags=["research"], deprecated=True,
               summary="Old name of DELETE /reviews/{id}, kept for one release")
def delete_brief(brief_id: int, collection_id: str = "default") -> dict:
    indexer = _indexer(collection_id)
    _require_write(collection_id)
    if not library.research_store(indexer).delete_brief(brief_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
    return {"deleted": brief_id}


# ── citing ──────────────────────────────────────────────────────────────

def _collection_name(collection_id: str) -> Optional[str]:
    from services.collection_service import collection_service
    try:
        return (collection_service.get_collection(collection_id) or {}).get("name")
    except Exception:  # noqa: BLE001 - the name is a courtesy in a citation
        return None


def _citable(indexer) -> List[dict]:
    """Every servable source can be cited, papers or not (a chat answer can
    cite a memo or a spreadsheet)."""
    return [d for d in indexer.list_documents() if not governance.is_hidden(d)]


@router.patch("/api/research/papers/{document_id}/record", tags=["research"],
              summary="Correct a source's citation details")
def correct_record(document_id: str, body: RecordCorrection, collection_id: str = "default") -> dict:
    """What a person types is kept, labelled as theirs, through every later
    re-analysis; ``revert`` puts back what was read."""
    indexer = _indexer(collection_id)
    _require_write(collection_id)
    _paper_doc(indexer, document_id)
    store = library.research_store(indexer)
    try:
        library.correct_record(store, document_id, body.changes, body.revert, user=get_request_user())
    except library.CorrectionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    # A corrected title or DOI can connect references that did not match before.
    library.match_library(store, _papers(indexer))
    return get_paper(document_id, collection_id)


def _export_filename(name: Optional[str], ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", name or "").strip("-.") or "references"
    return f"{base[:80]}.{ext}"


@router.post("/api/research/citations", tags=["research"],
             summary="Citations and bibliography for sources, in a style or as BibTeX/RIS/CSL-JSON")
def cite_sources(body: CitationRequest, collection_id: str = "default") -> dict:
    """Built here from each source's record; nothing is sent anywhere.
    Each entry lists the fields it is ``missing`` rather than inventing them."""
    indexer = _indexer(collection_id)
    docs = {d["document_id"]: d for d in _citable(indexer)}
    wanted: List[str] = []
    for p in body.passages or []:
        if p.document_id not in wanted:
            wanted.append(p.document_id)
    default = [] if body.passages else [d for d in docs if library.is_paper(docs[d])]
    for did in body.document_ids or default:
        if did not in wanted:
            wanted.append(did)
    unknown = [d for d in wanted if d not in docs]
    wanted = [d for d in wanted if d in docs]
    if not wanted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="None of those sources were found")
    papers = library.research_store(indexer).get_papers(wanted)
    name = _collection_name(collection_id)
    items = {d: citations.build_item(docs[d], papers.get(d), name) for d in wanted}
    numbers = {d: i for i, d in enumerate(wanted, 1)}
    passages = [citations.cite(items[p.document_id], body.style, p.page, numbers[p.document_id],
                               body.include_location)
                for p in (body.passages or []) if p.document_id in items]
    ordered = list(items.values())
    entries = [citations.cite(items[d], body.style, None, numbers[d], body.include_location) for d in wanted]
    out = {
        "style": body.style,
        "styles": citations.STYLES,
        "entries": entries,
        "passages": passages,
        "bibliography": citations.bibliography(ordered, body.style, "text", body.include_location),
        "bibliography_html": citations.bibliography(ordered, body.style, "html", body.include_location),
        "incomplete": [e["document_id"] for e in entries if e["missing"]],
        "unknown": unknown,
    }
    if body.export:
        content, media_type, ext = citations.export(ordered, body.export, body.include_location)
        out["export"] = {"format": body.export, "content": content, "media_type": media_type,
                         "filename": _export_filename(name, ext)}
    return out


class WriteupRequest(BaseModel):
    document_ids: Optional[List[str]] = Field(None, max_length=500)
    style: Literal["apa", "chicago", "mla", "ieee", "harvard"] = "apa"
    question: Optional[str] = Field(None, max_length=500)


@router.post("/api/research/writeup", tags=["research"], status_code=status.HTTP_201_CREATED,
             summary="Write up the selected sources from their reviews' verified statements")
def write_up(body: WriteupRequest, collection_id: str = "default") -> dict:
    """A9 (roadmap R11): a cited summary built only from statements the
    reviews already verified, each with its quote and page, and the
    reference list. No model is called. It is saved in Reports."""
    import uuid

    from api.artifacts import _store as artifact_store
    from services.research import writeup
    from services.research.store import now_iso

    indexer = _indexer(collection_id)
    _require_write(collection_id)
    docs = _select(indexer, body.document_ids)
    store = library.research_store(indexer)
    latest: Dict[str, dict] = {}
    for doc in docs:
        found = store.list_briefs(document_id=doc["document_id"], limit=1)
        if found:
            latest[doc["document_id"]] = found[0]
    if not latest:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="None of these sources has a review yet. Prepare reviews first; the write-up "
                                   "is built from what they verified.")
    papers = store.get_papers([d["document_id"] for d in docs])
    title, content, sources = writeup.build(docs, papers, latest, body.style, _collection_name(collection_id),
                                            body.question)
    now = now_iso()
    artifact = artifact_store(indexer, collection_id).create(
        uuid.uuid4().hex, artifact_type="writeup", title=title[:200], status="done", focus=body.question,
        document_ids=[d["document_id"] for d in docs], content=content, sources=[],
        source_documents=[{"document_id": d["document_id"], "filename": d.get("filename"),
                           "content_hash": d.get("content_hash")} for d in docs],
        created_by=get_request_user(), created_at=now, started_at=now, finished_at=now,
    )
    return {"artifact": {k: artifact.get(k) for k in ("id", "title", "artifact_type", "status", "created_at")},
            "reviewed": len(latest), "selected": len(docs)}


@router.post("/api/research/check-draft", tags=["research"],
             summary="Check a draft's quotes and citations against the sources")
def check_draft_endpoint(
    body: DraftCheckRequest,
    collection_id: str = "default",
    x_ai_key: str = Header(None), x_ollama_model: str = Header(None), x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None), x_ai_model: str = Header(None), x_ai_base_url: str = Header(None),
) -> dict:
    """Quotes are checked word for word and citations resolved by fixed
    rules; a model, when chosen, only judges cited claims, and each verdict
    stands only on a quote found in the passage it names. Nothing is saved."""
    started = time.time()
    indexer = _indexer(collection_id)
    docs = _citable(indexer)
    if body.document_ids:
        wanted = set(body.document_ids)
        docs = [d for d in docs if d["document_id"] in wanted]
        if not docs:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="None of those sources were found")
    user = get_request_user()
    provider = model = None
    if body.provider:
        _budget_or_429(user)
        provider, model = _provider(body.provider, x_ai_key, x_ollama_model, x_anthropic_model,
                                    x_openai_model, x_ai_model, x_ai_base_url)
        data_controls.guard_model_call(collection_id, provider)
    papers = library.research_store(indexer).get_papers([d["document_id"] for d in docs])
    try:
        result = draft_check.check_draft(
            indexer, docs, papers, body.text, style=body.style, provider=provider, model=model,
            suggest_uncited=body.suggest_uncited, collection_name=_collection_name(collection_id),
            include_location=body.include_location)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:  # noqa: BLE001 - a provider failure is the caller's to see
        logger.warning("Draft check failed: %s", e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"The check failed: {e}")
    if body.provider:
        _record_usage(user, collection_id, body.provider, result.get("usage") or {}, started)
    names = {d["document_id"]: d.get("filename") for d in docs}
    used = {f.get("document_id") for f in result["findings"] if f.get("document_id")}
    used |= {b["document_id"] for b in result["bibliography"]}
    for f in result["findings"]:
        used |= set(f.get("document_ids") or [])
    result["sources"] = {did: {"filename": names.get(did)} for did in used if did}
    return result

"""The paper library: analysis, reference resolution and library matching.

Ties ``extract`` (the paper's own text), ``lookup`` (registries, by printed
identifier) and ``store`` together for one collection. Model-free from end
to end; ``relevance`` and ``briefs`` are the only modules that call a model.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from services.research import citations, lookup
from services.research.extract import (
    FRONT_PAGES, _isbn_valid, clean_text, extract_paper, normalize_doi, title_guess,
)
from services.research.grounding import similarity
from services.research.store import ResearchStore, now_iso

logger = logging.getLogger(__name__)

# Formats that can be papers. Spreadsheets are tables and code is code; the
# research views leave them out.
PAPER_FORMATS = {"pdf", "docx", "doc", "txt", "md", "markdown", "html", "htm", "pptx", "rtf", "tex", "epub"}
# Registry records must name a title that is printed on page one of the
# paper they were looked up for (OCR noise allowed).
PAGE_ONE_MATCH_THRESHOLD = 0.9
MIN_TITLE_KEY_CHARS = 20


def research_store(indexer) -> ResearchStore:
    return ResearchStore(indexer.vector_store.metadata_db_path)


def is_paper(doc: dict) -> bool:
    fmt = (doc.get("source_format") or Path(doc.get("filename") or "").suffix.lstrip(".")).lower()
    return fmt in PAPER_FORMATS


def document_path(collection_id: str, doc: dict) -> Optional[Path]:
    from services.collection_service import collection_service

    if doc.get("source_type") == "local_reference" and doc.get("source_path"):
        path = Path(doc["source_path"])
    else:
        path = collection_service.get_documents_path(collection_id) / doc["filename"]
    return path if path.exists() else None


def _pages_from_chunks(chunks: List[dict]) -> Dict[int, str]:
    """Rebuild page text from chunks when the file itself is gone. Chunks
    overlap; the overlap is trimmed where the next chunk repeats the tail
    of the previous one."""
    pages: Dict[int, str] = {}
    for chunk in chunks:
        page = int(chunk.get("page_number") or 1)
        text = chunk.get("text") or ""
        prev = pages.get(page, "")
        if prev:
            overlap = 0
            for size in range(min(len(prev), len(text), 400), 20, -1):
                if prev.endswith(text[:size]):
                    overlap = size
                    break
            text = text[overlap:]
            pages[page] = prev + ("\n" if not overlap else "") + text
        else:
            pages[page] = text
    return pages


def paper_pages(collection_id: str, indexer, doc: dict) -> Dict[int, str]:
    """``{page: text}`` for a paper, re-read from its file (exact line
    structure, which title and reference parsing need), else from chunks.

    Only a document whose text layer was read directly is re-read: one that
    needed OCR would be OCR'd again — minutes of CPU, or a paid vision-model
    call — so its indexed text is used instead."""
    path = document_path(collection_id, doc)
    if path is not None and (doc.get("extraction_method") or "text") == "text":
        try:
            result = indexer.document_extractor.extract_text(path)
            pages = {int(k): v or "" for k, v in result.page_texts.items()}
            if any(v.strip() for v in pages.values()):
                return pages
        except Exception as exc:  # noqa: BLE001 - fall back to the indexed text
            logger.info("Re-reading %s for research failed (%s); using its chunks", doc.get("filename"), exc)
    return _pages_from_chunks(indexer.vector_store.metadata_store.get_chunks_by_document(doc["document_id"]))


# ── analysis ────────────────────────────────────────────────────────────

def _confirm_registry(record: Optional[dict], printed_in: str, how: str, where: str,
                      notes: List[str]) -> Optional[dict]:
    """Accept a registry record only if its title is printed where the
    document's own title is (page one of an article, the front matter of a
    book)."""
    if not record or not record.get("title"):
        return None
    score = similarity(record["title"], printed_in)
    if score < PAGE_ONE_MATCH_THRESHOLD:
        notes.append(
            f"The {how} printed {where} points to a registry record titled “{record['title'][:120]}”, "
            "which is not this document's title, so that record was not used."
        )
        return None
    return {**record, "match": round(score, 3)}


def _deterministic_resolution(entry: dict) -> dict:
    """What can be downloaded without asking anyone: arXiv ids and printed URLs."""
    entry = dict(entry)
    if entry.get("arxiv_id"):
        entry.update(resolved_url=f"https://arxiv.org/pdf/{entry['arxiv_id']}", resolved_kind="pdf",
                     resolved_via="arXiv id printed in the reference", status="resolved")
    elif entry.get("url") and "doi.org/" not in entry["url"].lower():
        is_pdf = entry["url"].lower().split("?")[0].endswith(".pdf")
        entry.update(resolved_url=entry["url"], resolved_kind="pdf" if is_pdf else "page",
                     resolved_via="URL printed in the source", status="resolved")
    return entry


def analyze_document(collection_id: str, indexer, document_id: str,
                     use_registry: bool = True, store: Optional[ResearchStore] = None) -> dict:
    """Read a paper's record and references; store them; return the record."""
    store = store or research_store(indexer)
    metadata = indexer.vector_store.metadata_store
    doc = metadata.get_document_info(document_id)
    if not doc:
        raise KeyError(document_id)
    pages = paper_pages(collection_id, indexer, doc)
    if not any((t or "").strip() for t in pages.values()):
        store.upsert_paper(document_id, analyzed_at=now_iso(), provenance={},
                           analysis_notes=["The document has no readable text."])
        store.replace_references(document_id, [])
        return store.get_paper(document_id) or {}

    found = extract_paper(pages, document_path(collection_id, doc), fmt=doc.get("source_format"))
    notes = list(found["notes"])
    provenance = dict(found["provenance"])
    fields = {k: found[k] for k in ("title", "authors", "year", "doi", "arxiv_id", "isbn", "venue",
                                    "abstract", "kind")}
    ordered = sorted(pages)
    if found["kind"] == "book":
        printed_in = "\n".join(pages[p] for p in ordered[:FRONT_PAGES])
        where = "in the front matter"
    else:
        printed_in = pages[ordered[0]] if ordered else ""
        where = "on page 1"

    from services import data_controls

    if use_registry and lookup.lookup_available() and not data_controls.external_allowed(collection_id, doc):
        # A title or identifier sent to a catalogue says what this server holds.
        use_registry = False
        notes.append("Catalogue lookups were skipped: this source is marked local only.")
    if use_registry and lookup.lookup_available():
        record, how = None, ""
        for how, ident, fetch in (("ISBN", fields["isbn"], lookup.by_isbn),
                                  ("DOI", fields["doi"], lookup.by_doi),
                                  ("arXiv id", fields["arxiv_id"], lookup.by_arxiv)):
            if ident and not record:
                record = _confirm_registry(fetch(ident), printed_in, how, where, notes)
                if record:
                    break
        if not record and fields["title"]:
            record = lookup.by_title(fields["title"], printed_in, fields["year"])
            how = "title search"
        if record:
            detail = (f"{record['source']} record for the {how} printed {where}"
                      if how != "title search" else f"{record['source']} title search")
            detail += f"; its title is printed {where} (match {record['match']:.2f})"
            for field in ("title", "authors", "year", "venue", "doi", "arxiv_id"):
                if record.get(field):
                    fields[field] = record[field]
                    provenance[field] = {"source": record["source"], "detail": detail}
            # The document's own abstract is quoted verbatim; a registry
            # abstract only fills the gap when none could be found.
            if not fields["abstract"] and record.get("abstract"):
                fields["abstract"] = record["abstract"]
                provenance["abstract"] = {"source": record["source"], "kind": "abstract", "detail": detail}
        elif not notes or "registry" not in " ".join(notes):
            notes.append("No scholarly registry record could be confirmed for this document; "
                         "its details come from its own text.")
    elif use_registry:
        notes.append("Registry lookups are off on this server; details come from the document's own text.")

    details: Dict[str, object] = {}
    printed = citations.page_offset(pages)
    if printed:
        offset, agreeing = printed
        details["page_offset"] = offset
        if offset:
            provenance["page_offset"] = {
                "source": "paper",
                "detail": f"the page numbers printed on {agreeing} pages run {offset:+d} from the "
                          "file's pages; citations give the printed page"}
            if fields["kind"] != "book" and offset > 0:
                details["printed_pages"] = citations.page_range(pages, offset)

    # What a person corrected stays corrected: a re-analysis (after an
    # update, or "Read it again") rewrites only what was read.
    existing = store.get_paper(document_id) or {}
    corrected = {f for f, p in (existing.get("provenance") or {}).items()
                 if isinstance(p, dict) and p.get("source") == "user"}
    for field in corrected:
        if field in fields:
            fields[field] = existing.get(field)
        provenance[field] = existing["provenance"][field]
    old_details = existing.get("details") or {}
    details.update({k: v for k, v in old_details.items() if k in corrected})
    if corrected:
        notes.append(f"Kept {len(corrected)} detail{'s' if len(corrected) != 1 else ''} a person corrected: "
                     + ", ".join(sorted(f.replace('_', ' ') for f in corrected)) + ".")

    store.upsert_paper(document_id, **fields, provenance=provenance, details=details,
                       analysis_notes=notes, analyzed_at=now_iso())
    entries = [_deterministic_resolution(e) for e in found["references"] + found["links"]]
    store.replace_references(document_id, entries)
    return store.get_paper(document_id) or {}


# ── corrections by a person ─────────────────────────────────────────────

MAX_AUTHORS = 200
_FIELD_LIMITS = {"title": 1000, "venue": 500, "container_title": 500, "publisher": 300,
                 "organization": 300, "place": 200, "url": 2000}


class CorrectionError(ValueError):
    """A typed value that cannot go into a citation; the message says why."""


def _clean_value(field: str, value):
    """The stored form of a typed value, or CorrectionError."""
    if field in ("authors", "editors"):
        if isinstance(value, str):
            value = [line for line in re.split(r"[\n;]+", value)]
        if not isinstance(value, list):
            raise CorrectionError(f"{field} must be a list of names")
        names = [" ".join(str(n).split()) for n in value if str(n).strip()]
        if len(names) > MAX_AUTHORS:
            raise CorrectionError(f"At most {MAX_AUTHORS} {field}")
        if any(len(n) > 300 for n in names):
            raise CorrectionError("A name is longer than 300 characters")
        return names
    if value is None:
        return None
    value = " ".join(str(value).split())
    if not value:
        return None
    if field == "year":
        if not re.fullmatch(r"\d{3,4}", value) or not 1000 <= int(value) <= 2100:
            raise CorrectionError("The year must be a four-digit year")
        return int(value)
    if field in ("month", "day"):
        limit = 12 if field == "month" else 31
        if not value.isdigit() or not 1 <= int(value) <= limit:
            raise CorrectionError(f"The {field} must be a number from 1 to {limit}")
        return int(value)
    if field == "doi":
        doi = normalize_doi(value)
        if not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
            raise CorrectionError("That is not a DOI (they start with 10. and a slash, e.g. 10.1000/xyz)")
        return doi
    if field == "isbn":
        digits = re.sub(r"[^0-9Xx]", "", value).upper()
        if not _isbn_valid(digits):
            raise CorrectionError("That ISBN's check digit is wrong")
        return digits
    if field == "kind":
        if value not in citations.KINDS:
            raise CorrectionError(f"The type must be one of {', '.join(citations.KINDS)}")
        return value
    if field == "url" and not re.match(r"https?://", value, re.IGNORECASE):
        raise CorrectionError("A URL starts with http:// or https://")
    limit = _FIELD_LIMITS.get(field, 120)
    if len(value) > limit:
        raise CorrectionError(f"The {field.replace('_', ' ')} is longer than {limit} characters")
    return value


def correct_record(store: ResearchStore, document_id: str, changes: Dict[str, object],
                   revert: Iterable[str] = (), user: Optional[str] = None) -> dict:
    """Apply a person's corrections to a source's record.

    Each changed field's provenance says who typed it and keeps what was
    read before, so ``revert`` can put the read value back. A corrected
    field is kept through every later re-analysis."""
    unknown = [f for f in [*changes, *revert] if f not in citations.EDITABLE_FIELDS]
    if unknown:
        raise CorrectionError(f"These fields cannot be edited: {', '.join(unknown)}")
    paper = store.get_paper(document_id) or {}
    provenance = dict(paper.get("provenance") or {})
    details = dict(paper.get("details") or {})
    core: Dict[str, object] = {}
    stamp = now_iso()
    who = (user or "").strip() or "a user"

    def current(field):
        return paper.get(field) if field in citations.CORE_FIELDS else details.get(field)

    def put(field, value):
        if field in citations.CORE_FIELDS:
            core[field] = value
        elif value in (None, [], ""):
            details.pop(field, None)
        else:
            details[field] = value

    for field in revert:
        prov = provenance.get(field) or {}
        if prov.get("source") != "user":
            continue
        previous = prov.get("previous") or {}
        put(field, previous.get("value"))
        if previous.get("provenance"):
            provenance[field] = previous["provenance"]
        else:
            provenance.pop(field, None)

    for field, raw in changes.items():
        value = _clean_value(field, raw)
        if value == current(field) and (provenance.get(field) or {}).get("source") == "user":
            continue
        if value == current(field) and field not in provenance and value in (None, []):
            continue
        prov = provenance.get(field) or {}
        previous = (prov.get("previous") if prov.get("source") == "user"
                    else {"value": current(field), "provenance": prov or None})
        provenance[field] = {"source": "user", "detail": f"entered by {who}", "at": stamp,
                             "previous": previous}
        put(field, value)

    return store.upsert_paper(document_id, **core, provenance=provenance, details=details,
                              corrected_at=stamp)


# ── reference resolution (registry) ─────────────────────────────────────

def resolve_reference(entry: dict) -> dict:
    """Find an open copy for one reference. Returns the fields to update."""
    update: Dict[str, object] = {}
    record = None
    via = ""
    if entry.get("doi"):
        record, via = lookup.by_doi(entry["doi"]), "openalex/crossref record for the printed DOI"
    elif entry.get("arxiv_id"):
        record, via = lookup.by_arxiv(entry["arxiv_id"]), "arXiv record for the printed id"
    elif entry.get("kind") == "reference" and not entry.get("url"):
        guess = title_guess(entry.get("raw_text") or "")
        record = lookup.by_title(guess, entry.get("raw_text") or "", entry.get("year"))
        if record:
            via = f"title search; the found title is printed in the reference (match {record['match']:.2f})"
    if record:
        update["title"] = record.get("title")
        update["year"] = record.get("year") or entry.get("year")
        if record.get("doi") and not entry.get("doi"):
            update["doi"] = record["doi"]
        if record.get("pdf_url"):
            update.update(resolved_url=record["pdf_url"], resolved_kind="pdf",
                          resolved_via=via, status="resolved")
        elif record.get("landing_url") and record.get("is_oa"):
            update.update(resolved_url=record["landing_url"], resolved_kind="page",
                          resolved_via=via, status="resolved")
    if "resolved_url" not in update and not entry.get("resolved_url"):
        update["status"] = "unavailable"
        update.setdefault("resolved_via", via or "no open copy found")
    return update


def resolve_references(store: ResearchStore, references: Iterable[dict]) -> List[dict]:
    out = []
    for entry in references:
        if entry.get("status") in ("in_library", "queued"):
            out.append(entry)
            continue
        if entry.get("resolved_kind") == "pdf":
            out.append(entry)
            continue
        update = resolve_reference(entry) if lookup.lookup_available() else {}
        if update:
            store.update_reference(entry["id"], **update)
            entry = {**entry, **update}
        out.append(entry)
    return out


def download_urls(entries: Iterable[dict], include_pages: bool = False) -> Dict[int, str]:
    """``{reference_id: url}`` the link indexer should fetch.

    Open PDFs always; landing pages (publisher pages, mostly paywalled
    abstracts) only when asked. A link written in the paper's body (a
    dataset, a code repository) is fetched as whatever it is — the caller
    only passes links when they were asked for. References already in the
    library or already queued are skipped."""
    urls: Dict[int, str] = {}
    for entry in entries:
        if entry.get("status") in ("in_library", "queued"):
            continue
        url = None
        if entry.get("kind") == "link" and entry.get("resolved_url"):
            url = entry["resolved_url"]
        elif entry.get("resolved_url") and (entry.get("resolved_kind") == "pdf" or include_pages):
            url = entry["resolved_url"]
        elif include_pages and entry.get("doi"):
            url = f"https://doi.org/{entry['doi']}"
        elif include_pages and entry.get("url"):
            url = entry["url"]
        if url:
            urls[int(entry["id"])] = url
    return urls


# ── matching references to papers already in the library ────────────────

def _title_key(title: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean_text(title or "").lower())


def match_library(store: ResearchStore, documents: List[dict]) -> int:
    """Point every reference at the library paper it cites, if any.

    A match needs the same DOI or arXiv id, or the library paper's full
    title printed in the reference (titles shorter than
    MIN_TITLE_KEY_CHARS are too generic to match on). Returns the number of
    matched references."""
    papers = store.get_papers([d["document_id"] for d in documents])
    by_doi: Dict[str, str] = {}
    by_arxiv: Dict[str, str] = {}
    titles: List[tuple] = []
    for doc in documents:
        paper = papers.get(doc["document_id"]) or {}
        if paper.get("doi"):
            by_doi[normalize_doi(paper["doi"])] = doc["document_id"]
        if paper.get("arxiv_id"):
            by_arxiv[re.sub(r"v\d+$", "", paper["arxiv_id"])] = doc["document_id"]
        key = _title_key(paper.get("title"))
        if len(key) >= MIN_TITLE_KEY_CHARS:
            titles.append((key, doc["document_id"]))
    # Longest titles first, so "Deep learning for X" does not claim a
    # reference to "Deep learning for X in Y".
    titles.sort(key=lambda t: -len(t[0]))

    matched = 0
    for ref in store.list_references(kind="reference"):
        target, reason = None, None
        if ref.get("doi") and normalize_doi(ref["doi"]) in by_doi:
            target, reason = by_doi[normalize_doi(ref["doi"])], "same DOI"
        elif ref.get("arxiv_id") and re.sub(r"v\d+$", "", ref["arxiv_id"]) in by_arxiv:
            target, reason = by_arxiv[re.sub(r"v\d+$", "", ref["arxiv_id"])], "same arXiv id"
        else:
            raw_key = _title_key(ref.get("raw_text"))
            for key, document_id in titles:
                if key in raw_key:
                    target, reason = document_id, "title printed in the reference"
                    break
        if target == ref["document_id"]:
            target, reason = None, None
        if target:
            matched += 1
        if target != ref.get("matched_document_id"):
            status = "in_library" if target else (
                "resolved" if ref.get("resolved_url") else "parsed")
            if not target and ref.get("status") in ("queued", "unavailable"):
                status = ref["status"]
            store.update_reference(ref["id"], matched_document_id=target, match_reason=reason, status=status)
    return matched

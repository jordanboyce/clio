"""Prepared reviews: read the paper now, review it later.

A brief is a fixed set of sections — what the paper asks, how it answers,
what it finds, what it admits it cannot show, and (when the researcher gave
a question) what it offers for that question. Every statement in every
section quotes the paper and is verified (``grounding``); there is no
free-text section a model can fill from memory.

Depth decides how much of the paper the model reads:

* ``brief`` — the opening passages plus the passages the collection's own
  retrieval finds for methods, results, limitations and conclusions: one
  model call.
* ``thorough`` — the whole paper (up to ``THOROUGH_MAX_WINDOWS`` windows of
  ``WINDOW_CHARS``), one call per window. Statements from all windows are
  merged deterministically — de-duplicated by quote, kept in reading order —
  rather than condensed by another model call that could drift from them.

``coverage`` on the result records how much of the paper was read, so a
brief never implies it covered pages it did not.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from services.research.grounding import normalize, verify_statements
from services.research.passages import (
    GROUNDING_RULES, add_usage, chunk_passages, complete, parse_json_object, render,
    search_passage_ids, select,
)

DEPTHS = ("brief", "thorough")
SECTIONS = (
    ("tldr", "In one line"),
    ("question", "What it asks"),
    ("approach", "How it answers"),
    ("findings", "What it finds"),
    ("limitations", "Limitations the authors state"),
    ("relevance", "For your question"),
)
BRIEF_BUDGET_CHARS = 16000
WINDOW_CHARS = 15000
THOROUGH_MAX_WINDOWS = 8
MAX_ITEMS_PER_SECTION = {"tldr": 1, "question": 3, "approach": 6, "findings": 10,
                         "limitations": 6, "relevance": 6}
_RETRIEVAL_QUERIES = (
    "research question objective aim",
    "method approach experimental design data",
    "results findings we show",
    "limitations future work threats to validity",
    "conclusion",
)

_PROMPT = (
    "Prepare a review of a source (a research paper, a book, a report) for a researcher who will read it later. "
    "{scope}\n\n"
    "{rules}\n\n"
    "Return this JSON object (each list may be empty):\n"
    "{{\"tldr\": [one point: the main contribution or argument],\n"
    "  \"question\": [points: the problem or question it addresses],\n"
    "  \"approach\": [points: methods, data, experimental design, sample sizes],\n"
    "  \"findings\": [points: results, with their numbers exactly as written],\n"
    "  \"limitations\": [points: limitations or caveats the AUTHORS state; never your own critique]"
    "{relevance_key}}}\n"
    "where every point is {{\"point\": \"a plain-language statement\", \"quote\": \"exact words\", "
    "\"passage\": \"P7\"}}.\n"
    "{relevance_rule}"
    "Prefer specific statements (what, how many, compared with what) over general ones.\n\n"
    "PASSAGES:\n{passages}"
)


def _prompt(passages: Dict[str, dict], question: Optional[str], scope: str) -> str:
    if question:
        relevance_key = ",\n  \"relevance\": [points: what it offers for the researcher's question]"
        relevance_rule = (f"The researcher's question is: {question}\nIf these passages offer nothing for "
                          "it, leave \"relevance\" empty.\n")
    else:
        relevance_key, relevance_rule = "", ""
    return _PROMPT.format(scope=scope, rules=GROUNDING_RULES, relevance_key=relevance_key,
                          relevance_rule=relevance_rule, passages=render(passages))


def _windows(passages: Dict[str, dict], size: int) -> List[Dict[str, dict]]:
    windows: List[Dict[str, dict]] = []
    current: Dict[str, dict] = {}
    used = 0
    for pid, passage in passages.items():
        length = len(passage["text"])
        if current and used + length > size:
            windows.append(current)
            current, used = {}, 0
        current[pid] = passage
        used += length
    if current:
        windows.append(current)
    return windows


def _merge(sections: Dict[str, List[dict]], key: str, items: List[dict], order: Dict[str, int]) -> None:
    bucket = sections.setdefault(key, [])
    seen = {normalize(i["quote"]) for i in bucket}
    for item in items:
        quote_key = normalize(item["quote"])
        if quote_key in seen:
            continue
        seen.add(quote_key)
        bucket.append(item)
    bucket.sort(key=lambda i: order.get(i["passage"], 0))


def prepare(indexer, document_id: str, provider, model: str, depth: str = "brief",
            question: Optional[str] = None) -> dict:
    """Write one paper's brief. Returns ``{"sections", "rejected",
    "coverage", "model", "usage", "notes"}``."""
    if depth not in DEPTHS:
        raise ValueError(f"depth must be one of {', '.join(DEPTHS)}")
    question = " ".join((question or "").split())[:1000] or None
    chunks = indexer.vector_store.metadata_store.get_chunks_by_document(document_id)
    passages = chunk_passages(chunks)
    usage: dict = {}
    notes: List[str] = []
    if not passages:
        return {"sections": [], "rejected": 0, "coverage": {"passages": 0, "of": 0},
                "model": model or "", "usage": usage, "notes": ["The source has no indexed text."]}

    if depth == "brief":
        queries = list(_RETRIEVAL_QUERIES) + ([question] if question else [])
        hit_ids, _ = search_passage_ids(indexer, document_id, queries, passages, per_query=3)
        windows = [select(passages, list(passages)[:4] + hit_ids + list(passages)[-2:], BRIEF_BUDGET_CHARS)]
        scope = ("You are shown selected passages, not the whole document; review only what they show.")
    else:
        windows = _windows(passages, WINDOW_CHARS)
        if len(windows) > THOROUGH_MAX_WINDOWS:
            # A book does not fit: read parts spread evenly from the first to
            # the last, so the review covers its whole arc rather than only
            # its opening chapters, and say so.
            step = (len(windows) - 1) / (THOROUGH_MAX_WINDOWS - 1)
            picked = sorted({round(i * step) for i in range(THOROUGH_MAX_WINDOWS)})
            notes.append(f"The document is long: {len(picked)} of its {len(windows)} parts were read, "
                         "spread evenly from beginning to end.")
            windows = [windows[i] for i in picked]
        scope = "You are shown part {n} of {total} read from the document, in order; review only this part."

    order = {pid: i for i, pid in enumerate(passages)}
    sections: Dict[str, List[dict]] = {}
    rejected = 0
    read_ids: set = set()
    failed_windows = 0
    for n, window in enumerate(windows, start=1):
        prompt = _prompt(window, question, scope.format(n=n, total=len(windows)))
        text, call_usage = complete(provider, model, prompt, max_tokens=2200)
        add_usage(usage, call_usage)
        read_ids.update(window)
        parsed = parse_json_object(text)
        if not parsed:
            failed_windows += 1
            continue
        for key, _title in SECTIONS:
            if key == "relevance" and not question:
                continue
            items = parsed.get(key) or []
            if isinstance(items, dict):
                items = [items]
            kept, dropped = verify_statements(items if isinstance(items, list) else [], window)
            rejected += dropped
            _merge(sections, key, kept, order)

    if failed_windows:
        notes.append(f"The model's answer for {failed_windows} part(s) could not be read; "
                     "those parts are not reviewed.")
    if rejected:
        notes.append(f"{rejected} statement(s) were removed because their quote was not found in the source.")

    out_sections = []
    for key, title in SECTIONS:
        if key == "relevance" and not question:
            continue
        items = sections.get(key, [])[:MAX_ITEMS_PER_SECTION[key]]
        out_sections.append({"key": key, "title": title, "items": items})
    if not any(s["items"] for s in out_sections) and not failed_windows:
        notes.append("No statement could be verified against the source, so the review is empty.")

    shown_chars = sum(len(passages[p]["text"]) for p in read_ids)
    total_chars = sum(len(p["text"]) for p in passages.values())
    pages = sorted({passages[p]["page"] for p in read_ids if passages[p]["page"] is not None})
    return {
        "sections": out_sections,
        "rejected": rejected,
        "coverage": {"passages": len(read_ids), "of": len(passages),
                     "share": round(shown_chars / total_chars, 3) if total_chars else 0,
                     "pages": pages, "depth": depth, "notes": notes},
        "model": usage.get("model") or model or "",
        "usage": usage,
        "notes": notes,
    }

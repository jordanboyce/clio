"""Write up: a cited summary built only from what the reviews verified
(roadmap R11, work order A9).

No model is called. Every statement in the write-up is one a review already
checked against its source: the review's point, the quote that supports it
and the page, with an in-text citation in the chosen style. Statements are
gathered by what they say across sources (findings, methods, limitations,
and the researcher's question when the reviews had one), and the reference
list closes it. Sources without a review are named, not summarised.

The result is Markdown, saved in Reports like any other report.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from services.research import citations

# The review sections a write-up draws on, in the order it presents them.
PARTS = (
    ("relevance", "On your question"),
    ("findings", "What the sources find"),
    ("approach", "How they go about it"),
    ("limitations", "What they say they cannot show"),
)
MAX_STATEMENTS_PER_SOURCE = 6


def _quote(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= 300 else text[:299].rstrip() + "…"


def build(documents: List[dict], papers: Dict[str, dict], reviews: Dict[str, dict],
          style: str = "apa", collection_name: Optional[str] = None,
          question: Optional[str] = None) -> Tuple[str, str, List[dict]]:
    """``(title, markdown, sources)`` for the selected ``documents``.

    ``reviews`` maps document id → that source's latest review (its
    ``sections`` of verified statements)."""
    items = {d["document_id"]: citations.build_item(d, papers.get(d["document_id"]), collection_name)
             for d in documents}
    numbers = {did: i for i, did in enumerate(items, 1)}
    reviewed = [d for d in documents if (reviews.get(d["document_id"]) or {}).get("sections")]
    missing = [d for d in documents if d not in reviewed]

    focus = question or next((r.get("question") for r in reviews.values() if r and r.get("question")), None)
    title = f"Literature write-up: {focus}" if focus else "Literature write-up"
    lines = [f"# {title}", ""]
    lines.append(
        f"Built from the reviews of {len(reviewed)} source{'s' if len(reviewed) != 1 else ''}"
        + (f" in {collection_name}" if collection_name else "")
        + ". Every statement below is one a review verified against its source, with the quote and "
          "the page it rests on. Nothing here was written from a model's memory."
    )
    lines.append("")

    used_statements = 0
    for key, heading in PARTS:
        block: List[str] = []
        for doc in reviewed:
            did = doc["document_id"]
            item = items[did]
            sections = {s.get("key"): s for s in reviews[did].get("sections") or []}
            for statement in (sections.get(key) or {}).get("items", [])[:MAX_STATEMENTS_PER_SOURCE]:
                cite = citations.in_text(item, style, citations.printed_page(item, statement.get("page")),
                                         numbers[did])
                block.append(f"- {statement.get('point', '').strip()} “{_quote(statement.get('quote'))}” {cite}")
                used_statements += 1
        if block:
            lines += [f"## {heading}", "", *block, ""]

    if not used_statements:
        lines += ["No verified statements were found in the reviews of these sources.", ""]
    if missing:
        names = ", ".join(citations.in_text(items[d["document_id"]], style, None, numbers[d["document_id"]])
                          for d in missing)
        lines += ["## Not reviewed yet", "",
                  f"These selected sources have no review, so nothing from them appears above: {names}.", ""]

    lines += ["## References", "", "\n\n".join(citations.bibliography(list(items.values()), style, "markdown")), ""]
    sources = [{"document_id": d["document_id"], "filename": d.get("filename")} for d in documents]
    return title, "\n".join(lines).rstrip() + "\n", sources

"""G3: a reference list for the papers among the exported sources.

For a collection whose sources include papers the Library holds records
for (services/research/store.py), this adds to the reader a references page
(``read/references.html``), each exported paper once, formatted by
services/research/citations.py in the chosen style and linking to its page;
the standard scholarly ``citation_*`` meta tags on each paper's own page, which
Google Scholar and reference managers read; and the BibTeX file
(``read/references.bib``), offered from the references page.

The Library keeps its citation style in the browser, not on the server, so
the export is told which: the dialog sends the Library's, and
``citation_style`` on the command line or over HTTP sets it (APA when not
given).
"""

from __future__ import annotations

import html
from typing import Any, Dict, List, Optional

from services.static_export.additions import PAGE_HOOKS, Addition, ExportContext, register
from services.static_export import reader

PAGE = f"{reader.FOLDER}/references.html"
BIBTEX = f"{reader.FOLDER}/references.bib"
DEFAULT_STYLE = "apa"


def _papers(collection_id: str, document_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    from services.collection_service import collection_service
    from services.research.store import ResearchStore

    if not document_ids:
        return {}
    try:
        store = ResearchStore(collection_service.get_indexes_path(collection_id) / "metadata.db")
        found = store.get_papers(document_ids) or {}
    except Exception:  # noqa: BLE001 - no Library here
        return {}
    return {doc_id: paper for doc_id, paper in found.items() if paper}


def style_of(value: Optional[str]) -> str:
    from services.research.citations import STYLES

    from services.static_export.common import StaticExportError

    style = (value or DEFAULT_STYLE).strip().lower()
    if style not in STYLES:
        raise StaticExportError(f"citation_style must be one of {', '.join(STYLES)}.", "option")
    return style


def _offer(o) -> Dict[str, Any]:
    papers = _papers(o.collection_id, [d["document_id"] for d in o.survey["documents"]])
    if not papers:
        return {"available": False, "reason": "No exported source is a paper in the Library.", "bytes": 0}
    return {"available": True, "reason": None, "bytes": len(papers) * 1200,
            "note": f"{len(papers)} paper{'' if len(papers) == 1 else 's'}, in the Library's citation style. "
                    f"Needs the pages to read, which are included with it."}


def _items(ctx: ExportContext):
    from services.research import citations

    rows = {d["document_id"]: d for d in ctx.survey["documents"]}
    exported = [d["id"] for d in ctx.documents]
    papers = _papers(ctx.collection_id, exported)
    collection = (ctx.manifest.get("collection") or {}).get("name")
    return [(doc_id, citations.build_item(rows[doc_id], papers[doc_id], collection))
            for doc_id in exported if doc_id in papers]


def _write(ctx: ExportContext) -> Dict[str, Any]:
    from services.research import citations

    style = style_of(ctx.settings.get("citation_style"))
    items = _items(ctx)
    names = reader.page_names(ctx.documents)
    by_id = {doc_id: item for doc_id, item in items}
    ordered = [doc_id for doc_id, _ in items]
    if style != "ieee":
        ordered.sort(key=lambda i: citations.sort_key(by_id[i], style))
    entries = []
    for n, doc_id in enumerate(ordered, start=1):
        entry = citations.reference_entry(by_id[doc_id], style, "html", number=n if style == "ieee" else None)
        page = names[doc_id].split("/", 1)[1]
        entries.append(f'<li id="ref-{html.escape(doc_id)}">{entry} <a href="{html.escape(page)}">Read it here</a></li>\n')
    ctx.put(BIBTEX, citations.to_bibtex([by_id[i] for i in ordered]))
    collection = (ctx.manifest.get("collection") or {}).get("name") or "Sources"
    body = (f'<nav><a href="index.html">{html.escape(collection)}</a></nav>\n<header>\n<h1>References</h1>\n'
            f'<p class="meta">{len(entries)} paper{"" if len(entries) == 1 else "s"}, {citations.STYLES[style]}. '
            f'<a href="references.bib" download>Download as BibTeX</a></p>\n</header>\n<main>\n'
            f'<ol class="sources">\n{"".join(entries)}</ol>\n</main>\n')
    ctx.put(PAGE, reader.page(f"References · {collection}", body, depth=1, site=ctx.site))
    # For the meta tags on each paper's page (the reader writes them after this).
    ctx.results["_references_items"] = by_id
    return {"page": PAGE, "bibtex": BIBTEX, "style": style, "papers": len(entries)}


def meta_tags(item: Dict[str, Any], page_url: Optional[str]) -> str:
    """Highwire Press tags (citation_*), the ones Google Scholar documents."""
    tags = [("citation_title", item.get("title"))]
    for a in item.get("authors") or []:
        name = a.get("literal") or ", ".join(x for x in (a.get("family"), a.get("given")) if x)
        tags.append(("citation_author", name))
    if item.get("year"):
        tags.append(("citation_publication_date", str(item["year"])))
    if item.get("venue"):
        tags.append(("citation_book_title" if item.get("kind") == "chapter" else "citation_journal_title",
                     item["venue"]))
    for field, tag in (("volume", "citation_volume"), ("issue", "citation_issue"), ("doi", "citation_doi"),
                       ("isbn", "citation_isbn"), ("publisher", "citation_publisher")):
        if item.get(field):
            tags.append((tag, str(item[field])))
    if item.get("kind") == "report" and (item.get("organization") or item.get("publisher")):
        tags.append(("citation_technical_report_institution", item.get("organization") or item.get("publisher")))
    if page_url:
        tags.append(("citation_abstract_html_url", page_url))
    return "".join(f'<meta name="{t}" content="{html.escape(str(v))}">\n' for t, v in tags if v)


def _page_part(ctx: ExportContext, doc):
    if doc is None:
        return {"body": '<p><a href="references.html">References</a></p>\n'}
    item = (ctx.results.get("_references_items") or {}).get(doc["id"])
    if not item:
        return {}
    return {"head": meta_tags(item, reader.public_address(ctx, reader.page_names(ctx.documents)[doc["id"]],
                                                          ctx.site))}


PAGE_HOOKS["references"] = _page_part
register(Addition(id="references", label="A reference list for the papers, with BibTeX", offer=_offer,
                  write=_write, after=("reader",)))

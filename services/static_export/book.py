"""H2: the whole collection on one page to print, and as an EPUB.

``read/all.html`` holds every exported source, one after another, for
printing to PDF from a browser (the reader's stylesheet has print rules:
no search box, no navigation, each source on a new page).
``read/<collection>.epub`` is an EPUB 3 book made with the standard
library: a zip of XHTML with the package files the format requires, one
chapter per source. Both are built from the passages as the reader's pages
are, overlaps removed and the text escaped.
"""

from __future__ import annotations

import html
import io
import uuid
import zipfile
from typing import Any, Dict, List

from services.static_export.additions import PAGE_HOOKS, Addition, ExportContext, register
from services.static_export import reader

ALL = f"{reader.FOLDER}/all.html"
_STAMP = (2026, 1, 1, 0, 0, 0)  # zip entry dates: fixed, so an unchanged collection gives the same bytes


def _blocks(passages: List[Dict[str, Any]], limit: int, several: bool, xhtml: bool) -> str:
    br = "<br/>" if xhtml else "<br>"
    out, last, previous = [], None, ""
    for p in passages:
        if p["page"] != last:
            previous = ""
            if several and p["page"] is not None:
                out.append(f'<h3 class="page">Page {int(p["page"])}</h3>\n')
            last = p["page"]
        text = reader.without_overlap(previous, p["text"] or "", limit)
        previous = p["text"] or ""
        for para in [x.strip() for x in text.split("\n\n") if x.strip()]:
            out.append(f"<p>{html.escape(para).replace(chr(10), br)}</p>\n")
    return "".join(out)


def _offer(o) -> Dict[str, Any]:
    s = o.survey
    return {"available": bool(s["passages"]), "reason": None if s["passages"] else "No passages.",
            "bytes": int(s["text_bytes"] * 1.6) + 8000,
            "note": "One page to print and an EPUB. Needs the pages to read, which are included with it."}


def _write(ctx: ExportContext) -> Dict[str, Any]:
    collection = ctx.manifest.get("collection") or {}
    name = collection.get("name") or "Sources"
    grouped = reader.by_document(ctx.passages)
    limits = {d["document_id"]: reader.overlap_limit(d.get("chunk_overlap")) for d in ctx.survey["documents"]}

    def several(doc, passages):
        return (doc.get("pages") or 0) > 1 or len({p["page"] for p in passages}) > 1

    # One page holding everything.
    parts = [f'<nav><a href="index.html">{html.escape(name)}</a></nav>\n<header>\n<h1>{html.escape(name)}</h1>\n'
             f'<p class="meta">Every source, one after another, to read or print.</p>\n</header>\n<main>\n']
    for doc in ctx.documents:
        passages = grouped.get(doc["id"], [])
        parts.append(f'<article class="source" id="source-{html.escape(doc["id"])}">\n'
                     f"<h2>{html.escape(doc.get('title') or doc.get('name'))}</h2>\n"
                     + _blocks(passages, limits.get(doc["id"], 208), several(doc, passages), False) + "</article>\n")
    parts.append("</main>\n")
    ctx.put(ALL, reader.page(f"All of {name}", "".join(parts), depth=1, site=False))

    epub_name = f"{reader.FOLDER}/{reader._slug(name) or 'collection'}.epub"
    ctx.put(epub_name, epub(ctx, name, grouped, limits, several))
    return {"page": ALL, "epub": epub_name}


def epub(ctx: ExportContext, name: str, grouped, limits, several) -> bytes:
    language = "en"
    book_id = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'clio:' + ctx.collection_id)}"
    modified = (ctx.manifest.get("exported_at") or "2026-01-01T00:00:00")[:19] + "Z"
    chapters = []
    for i, doc in enumerate(ctx.documents, start=1):
        passages = grouped.get(doc["id"], [])
        title = html.escape(doc.get("title") or doc.get("name") or f"Source {i}")
        body = _blocks(passages, limits.get(doc["id"], 208), several(doc, passages), True)
        chapters.append((f"s{i:04d}.xhtml", title, (
            '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{language}" lang="{language}">\n'
            f"<head><title>{title}</title></head>\n<body>\n<h1>{title}</h1>\n{body}</body>\n</html>\n")))
    nav = ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
           f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
           f'xml:lang="{language}" lang="{language}">\n<head><title>Contents</title></head>\n<body>\n'
           '<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>\n'
           + "".join(f'<li><a href="{f}">{t}</a></li>\n' for f, t, _ in chapters) + "</ol></nav>\n</body>\n</html>\n")
    description = html.escape(collection_description(ctx))
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">\n'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
           f'<dc:identifier id="bookid">{book_id}</dc:identifier>\n<dc:title>{html.escape(name)}</dc:title>\n'
           f"<dc:language>{language}</dc:language>\n"
           + (f"<dc:description>{description}</dc:description>\n" if description else "")
           + f'<meta property="dcterms:modified">{modified}</meta>\n</metadata>\n<manifest>\n'
           '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>\n'
           + "".join(f'<item id="c{i}" href="{f}" media-type="application/xhtml+xml"/>\n'
                     for i, (f, _, _) in enumerate(chapters, start=1))
           + '</manifest>\n<spine>\n'
           + "".join(f'<itemref idref="c{i}"/>\n' for i in range(1, len(chapters) + 1)) + "</spine>\n</package>\n")
    container = ('<?xml version="1.0" encoding="utf-8"?>\n'
                 '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 "</rootfiles>\n</container>\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        def add(path: str, data: str, compress: bool = True) -> None:
            info = zipfile.ZipInfo(path, date_time=_STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
            zf.writestr(info, data)

        add("mimetype", "application/epub+zip", compress=False)  # first, and stored: the format requires it
        add("META-INF/container.xml", container)
        add("OEBPS/content.opf", opf)
        add("OEBPS/nav.xhtml", nav)
        for f, _, text in chapters:
            add(f"OEBPS/{f}", text)
    return buf.getvalue()


def collection_description(ctx: ExportContext) -> str:
    return " ".join(((ctx.manifest.get("collection") or {}).get("description") or "").split())


def _page_part(ctx: ExportContext, doc):
    if doc is not None:
        return {}
    epub_name = f"{reader._slug((ctx.manifest.get('collection') or {}).get('name') or '') or 'collection'}.epub"
    return {"body": f'<p><a href="all.html">Everything on one page, to print</a> · '
                    f'<a href="{html.escape(epub_name)}" download>As an EPUB book</a></p>\n'}


PAGE_HOOKS["book"] = _page_part
register(Addition(id="book", label="Everything on one page to print, and as an EPUB", offer=_offer, write=_write,
                  after=("reader",)))

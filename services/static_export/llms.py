"""F1: llms.txt and llms-full.txt, for AI agents reading the site.

``llms.txt`` (the convention at llmstxt.org) names the collection, says
what it is, and lists each exported source with one line and a link to its
Markdown page; ``llms-full.txt`` is the text of every source, one after the
other. Both come from the Markdown pages (B4), which are included with them,
so they hold exactly what the export holds and mention no source the gate
left out. In a whole site they sit at its root, where agents look for them;
otherwise beside the manifest. With the site's address (B3) the links are
absolute; without it, relative to the file.
"""

from __future__ import annotations

import re
from typing import Any, Dict

from services.static_export.additions import Addition, ExportContext, register
from services.static_export import markdown, reader

ROOT_FILE = "llms.txt"
FULL_FILE = "llms-full.txt"


def _first_sentence(text: str, limit: int = 140) -> str:
    flat = " ".join((text or "").split())
    m = re.match(r"(.{20,}?[.!?])(\s|$)", flat)
    line = m.group(1) if m else flat
    return line if len(line) <= limit else line[:limit - 1].rstrip() + "…"


def _offer(o) -> Dict[str, Any]:
    s = o.survey
    return {"available": bool(s["passages"]), "reason": None if s["passages"] else "No passages.",
            "bytes": int(s["text_bytes"] * 1.05) + len(s["documents"]) * 160,
            "note": "Needs the Markdown pages, which are included with it."}


def _link(ctx: ExportContext, name: str) -> str:
    """Where a file of the export is, from llms.txt."""
    absolute = reader.public_address(ctx, name, ctx.site)
    if absolute:
        return absolute
    from services.static_export.common import SITE_DATA_DIR

    return f"{SITE_DATA_DIR}/{name}" if ctx.site else name


def _write(ctx: ExportContext) -> Dict[str, Any]:
    collection = ctx.manifest.get("collection") or {}
    pages = (ctx.results.get("markdown") or {}).get("pages") or markdown.names(ctx.documents)
    first = {}
    for p in ctx.passages:
        first.setdefault(p["document_id"], p["text"])
    # The text is the sources', so it is written as Markdown text (escaped),
    # as on the Markdown pages.
    lines = [f"# {markdown.escape(collection.get('name') or 'Sources')}", ""]
    if collection.get("description"):
        lines += [f"> {markdown.escape(' '.join(collection['description'].split()))}", ""]
    lines += [f"{len(ctx.documents)} source{'' if len(ctx.documents) == 1 else 's'}, published with Clio. Each "
              f"link is the source's text as Markdown; {FULL_FILE} holds all of it.", "", "## Sources", ""]
    for doc in ctx.documents:
        title = markdown.escape(doc.get("title") or doc.get("name"))
        about = markdown.escape(_first_sentence(first.get(doc["id"], "")))
        lines.append(f"- [{title}]({_link(ctx, pages[doc['id']])})" + (f": {about}" if about else ""))
    ctx.put_root(ROOT_FILE, "\n".join(lines) + "\n")

    full = [f"# {markdown.escape(collection.get('name') or 'Sources')}", ""]
    for doc in ctx.documents:
        page = (ctx.data_dir / pages[doc["id"]]).read_text(encoding="utf-8")
        body = page.split("\n---\n", 1)[1].lstrip("\n") if page.startswith("---\n") else page
        link = (doc.get("link") or {}).get("url")
        full += [f"## {markdown.escape(doc.get('title') or doc.get('name'))}", "",
                 f"Source: {markdown.escape(doc.get('name') or '')}" + (f" ({link})" if link else ""), "",
                 body.replace("\n## Page ", "\n### Page ").rstrip(), ""]
    ctx.put_root(FULL_FILE, "\n".join(full) + "\n")
    return {"index": ROOT_FILE, "full": FULL_FILE}


register(Addition(id="llms", label="llms.txt, for AI agents reading the site", offer=_offer, write=_write,
                  after=("markdown",), stage=20))

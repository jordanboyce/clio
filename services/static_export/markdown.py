"""B4: the reader's pages as Markdown, for a site built with a generator.

One file per source under ``markdown/``, with YAML front matter (title,
source id, link, page count) and the source's text, overlaps removed as on
the reader's pages; ``_index.md`` lists them (Hugo reads it as the section's
page; MkDocs as one more page). Offered on the command line and over HTTP,
not in the dialog.

Source text is hostile, and Markdown renders HTML, links and, in Hugo,
shortcodes. So every character that could start any of those is escaped:
``< > &`` as entities, ``{ }`` as entities (no ``{{ }}`` survives for a
shortcode or a template), and the ASCII punctuation Markdown reads as
syntax with a backslash. The text reads the same; it can only ever be text.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from services.static_export.additions import Addition, ExportContext, register
from services.static_export import reader

FOLDER = "markdown"
CONTENTS = f"{FOLDER}/_index.md"

_ENTITIES = {"<": "&lt;", ">": "&gt;", "&": "&amp;", "{": "&#123;", "}": "&#125;"}
# Markdown syntax anywhere in a line (CommonMark lets any ASCII punctuation be escaped).
_INLINE = set("\\`*_[]()!|~^$")
# Syntax only at the start of a line: headings, quotes, lists, rules, tables.
_LINE_START = re.compile(r"^(\s*)([#>+\-=:]|\d+[.)])")


def escape(text: str) -> str:
    out = []
    for line in (text or "").split("\n"):
        chars = []
        for ch in line:
            if ch in _ENTITIES:
                chars.append(_ENTITIES[ch])
            elif ch in _INLINE:
                chars.append("\\" + ch)
            else:
                chars.append(ch)
        line = "".join(chars)
        m = _LINE_START.match(line)
        if m:
            lead, mark = m.group(1), m.group(2)
            line = lead + (mark[:-1] + "\\" + mark[-1]) + line[m.end():]
        # Four leading spaces would make a code block.
        out.append(line.lstrip(" ") if line.startswith("    ") else line)
    return "\n".join(out)


def _yaml(value: Any) -> str:
    """A front-matter value: JSON strings and numbers are valid YAML."""
    return json.dumps(value, ensure_ascii=False)


def _front(fields: Dict[str, Any]) -> str:
    rows = "".join(f"{k}: {_yaml(v)}\n" for k, v in fields.items() if v is not None)
    return f"---\n{rows}---\n\n"


def names(documents: List[Dict[str, Any]]) -> Dict[str, str]:
    return {doc_id: FOLDER + "/" + page.split("/", 1)[1][:-len(".html")] + ".md"
            for doc_id, page in reader.page_names(documents).items()}


def _source(doc: Dict[str, Any], passages: List[Dict[str, Any]], limit: int) -> str:
    link = (doc.get("link") or {}).get("url")
    out = [_front({"title": doc.get("title") or doc.get("name"), "source_id": doc["id"], "source": doc.get("name"),
                   "link": link, "pages": doc.get("pages")})]
    several = (doc.get("pages") or 0) > 1 or len({p["page"] for p in passages}) > 1
    last_page, previous = None, ""
    for p in passages:
        if p["page"] != last_page:
            previous = ""
            if several and p["page"] is not None:
                out.append(f"## Page {int(p['page'])}\n\n")
            last_page = p["page"]
        text = reader.without_overlap(previous, p["text"] or "", limit)
        previous = p["text"] or ""
        for para in re.split(r"\n\s*\n", text):
            if para.strip():
                # A line break inside a paragraph stays one (two spaces would be markup of its own).
                out.append(escape(para.strip()).replace("\n", "\\\n") + "\n\n")
    return "".join(out).rstrip("\n") + "\n"


def _contents(ctx: ExportContext, files: Dict[str, str]) -> str:
    collection = ctx.manifest.get("collection") or {}
    out = [_front({"title": collection.get("name") or "Sources",
                   "description": collection.get("description") or None})]
    for doc in ctx.documents:
        out.append(f"- [{escape(doc.get('title') or doc.get('name'))}]({files[doc['id']].split('/', 1)[1]})\n")
    return "".join(out)


def _offer(o) -> Dict[str, Any]:
    s = o.survey
    return {"available": bool(s["passages"]), "reason": None if s["passages"] else "No passages.",
            "bytes": int(s["text_bytes"] * 1.05) + len(s["documents"]) * 200}


def _write(ctx: ExportContext) -> Dict[str, Any]:
    files = names(ctx.documents)
    grouped = reader.by_document(ctx.passages)
    limits = {d["document_id"]: reader.overlap_limit(d.get("chunk_overlap")) for d in ctx.survey["documents"]}
    ctx.put(CONTENTS, _contents(ctx, files))
    for doc in ctx.documents:
        ctx.put(files[doc["id"]], _source(doc, grouped.get(doc["id"], []), limits.get(doc["id"], 208)))
    return {"contents": CONTENTS, "pages": {d["id"]: files[d["id"]] for d in ctx.documents}}


register(Addition(id="markdown", label="The pages as Markdown, for a site generator", offer=_offer, write=_write,
                  in_dialog=False))

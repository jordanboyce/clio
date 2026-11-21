"""C4: a saved report as one HTML file that needs nothing else.

The file holds the report's text, each citation as a link to the passage it
cites further down the same file, and the list of sources the report was
built from with their content hashes and whether each has changed since. It
has no script, loads nothing (its Content-Security-Policy is
``default-src 'none'``), and opens from disk with the network off.

The text is the model's Markdown, so it is rendered with a small subset
(headings, paragraphs, lists, bold, italics, code) after escaping
everything: nothing in a report or a passage can become markup.

Which rule applies (docs/CLASSIFICATION.md): a report leaving the server as
a file is not an outside service. Like a bundle, or a source someone
downloads, it goes only to a person who may read the collection, and it
carries its marking: a report from a collection marked local only, or built
from a source that is, says so at the top, with the marking's name.
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional

# [Source 3], [Source 1, Source 2], [Source 2: p. 4]: the citation groups Ask writes.
_SOURCE_GROUP = re.compile(r"\[Source\s+\d+(?:\s*[,;:](?:\s*Source)?[^\]]*)?\]", re.IGNORECASE)
_SOURCE_NUMBER = re.compile(r"Source\s+(\d+)", re.IGNORECASE)
# [leave-policy.pdf] or [leave-policy.pdf p.3]: the form the report prompt asks for.
_FILE_CITE = re.compile(r"\[([^\[\]\n]{1,200}?\.[A-Za-z0-9]{1,8})(?:\s+p\.?\s*(\d+))?\]")

CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"

STYLE = """
body { font: 16px/1.6 Georgia, "Times New Roman", serif; max-width: 46rem; margin: 2rem auto; padding: 0 1rem;
       color: #1f2933; background: #fff; }
header, nav, .meta, .sources, .marking, footer { font-family: system-ui, sans-serif; }
.marking { border: 2px solid #b91c1c; color: #7f1d1d; background: #fef2f2; padding: .5rem .75rem; font-weight: 600; }
.meta { color: #52606d; font-size: .9rem; }
a.cite { font-family: system-ui, sans-serif; font-size: .75em; vertical-align: super; text-decoration: none; }
.passage { border-left: 3px solid #cbd2d9; padding: .2rem 0 .2rem .8rem; margin: .4rem 0 1.2rem; }
.passage:target { border-color: #d97706; background: #fffbeb; }
.sources td, .sources th { text-align: left; padding: .2rem .5rem .2rem 0; vertical-align: top; font-size: .85rem; }
code { font-family: ui-monospace, Consolas, monospace; font-size: .9em; }
.changed { color: #b45309; }
@media (prefers-color-scheme: dark) {
  body { color: #e4e7eb; background: #12161c; }
  .meta { color: #9aa5b1; }
  .passage { border-color: #52606d; }
  .passage:target { background: #3f2d0c; }
  .marking { background: #450a0a; color: #fecaca; border-color: #f87171; }
}
"""


def _inline(text: str) -> str:
    """Escaped text with **bold**, *italic* and `code`. Never markup from the text itself."""
    out = html.escape(text, quote=True)
    out = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", out)
    return out


def _cite_links(escaped: str, sources: List[Dict[str, Any]]) -> str:
    """Citations in escaped text -> links to the passages below."""
    by_name: Dict[str, List[int]] = {}
    for i, s in enumerate(sources, start=1):
        by_name.setdefault(str(s.get("filename") or "").lower(), []).append(i)

    def group(m: re.Match) -> str:
        numbers = [int(n) for n in _SOURCE_NUMBER.findall(m.group(0))]
        links = [f'<a class="cite" href="#passage-{n}">[{n}]</a>' if 1 <= n <= len(sources)
                 else f'<span class="cite">[{n}?]</span>' for n in numbers]
        return "".join(links) or m.group(0)

    def by_file(m: re.Match) -> str:
        name = html.unescape(m.group(1)).strip().lower()
        page = m.group(2)
        found = by_name.get(name) or []
        if page:
            found = [i for i in found if str(sources[i - 1].get("page_number")) == page] or found
        if not found:
            return m.group(0)
        return f'<a class="cite" href="#passage-{found[0]}">[{found[0]}]</a>'

    escaped = _SOURCE_GROUP.sub(group, escaped)
    return _FILE_CITE.sub(by_file, escaped)


def markdown(text: str, sources: List[Dict[str, Any]]) -> str:
    """The report's Markdown as HTML: escaped first, then a small subset."""
    blocks: List[str] = []
    para: List[str] = []
    items: List[str] = []
    kind = ""

    def flush() -> None:
        nonlocal para, items, kind
        if para:
            blocks.append("<p>" + "<br>\n".join(_cite_links(_inline(p), sources) for p in para) + "</p>")
        if items:
            blocks.append(f"<{kind}>" + "".join(f"<li>{_cite_links(_inline(i), sources)}</li>" for i in items)
                          + f"</{kind}>")
        para, items, kind = [], [], ""

    for line in (text or "").replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
        number = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if not stripped:
            flush()
        elif heading:
            flush()
            level = min(6, len(heading.group(1)) + 1)  # the report's title is the page's h1
            blocks.append(f"<h{level}>{_cite_links(_inline(heading.group(2)), sources)}</h{level}>")
        elif bullet or number:
            want = "ul" if bullet else "ol"
            if para or (items and kind != want):
                flush()
            kind = want
            items.append((bullet or number).group(1))
        elif items and line.startswith(("  ", "\t")):
            items[-1] += " " + stripped
        else:
            if items:
                flush()
            para.append(stripped)
    flush()
    return "\n".join(blocks)


def marking_line(collection: Dict[str, Any], documents: Dict[str, Dict[str, Any]],
                 built_from: List[Dict[str, Any]]) -> Optional[str]:
    """The marking the file carries, in words, or None: the collection's, or
    the strictest of the sources it was built from."""
    from services import data_controls

    names = []
    marking = collection.get("classification")
    if marking:
        names.append(data_controls.label_of(marking) or marking)
    for s in built_from:
        doc = documents.get(s.get("document_id")) or {}
        if doc.get("classification"):
            names.append(data_controls.label_of(doc["classification"]) or doc["classification"])
    if not names:
        return None
    unique = list(dict.fromkeys(names))
    local = data_controls.holds_local_only(collection["id"])
    return (f"Marked {', '.join(unique)}." + (" Local only: keep this file on systems cleared to hold it, and "
                                               "give it to no outside service." if local else ""))


def render(artifact: Dict[str, Any], collection: Dict[str, Any], documents: Dict[str, Dict[str, Any]],
           changes: Dict[str, str], *, sensitivity: str) -> str:
    """The whole file. ``documents`` are the collection's sources as they are
    now; ``changes`` maps a source id to "changed" or "removed" since the
    report was made (artifact_store.source_changes)."""
    sources = list(artifact.get("sources") or [])
    built_from = list(artifact.get("source_documents") or [])
    title = artifact.get("title") or "Report"
    when = (artifact.get("finished_at") or artifact.get("created_at") or "")[:10]
    marking = marking_line(collection, documents, built_from)
    parts = ["<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             f'<meta http-equiv="Content-Security-Policy" content="{CSP}">',
             f"<title>{html.escape(title)}</title>", f"<style>{STYLE}</style>", "</head>", "<body>"]
    if marking:
        parts.append(f'<p class="marking" role="note">{html.escape(marking)}</p>')
    meta = [f"A report from {collection.get('name') or 'a collection'}", f"labelled {sensitivity.capitalize()}"]
    if when:
        meta.append(f"made {when}")
    if artifact.get("model"):
        meta.append(f"written by {artifact['model']}")
    parts += ["<header>", f"<h1>{html.escape(title)}</h1>", f'<p class="meta">{html.escape(", ".join(meta))}.</p>',
              "</header>", "<main>", markdown(artifact.get("content") or "", sources), "</main>"]
    if sources:
        parts += ['<section class="cited">', "<h2>The passages it cites</h2>"]
        for i, s in enumerate(sources, start=1):
            where = f", page {s['page_number']}" if s.get("page_number") else ""
            parts.append(f'<div class="passage" id="passage-{i}"><p class="meta"><strong>[{i}]</strong> '
                         f"{html.escape(str(s.get('filename') or ''))}{html.escape(where)}</p>"
                         f"<p>{html.escape(str(s.get('text_snippet') or '')).replace(chr(10), '<br>')}</p></div>")
        parts.append("</section>")
    if built_from:
        parts += ['<section class="sources">', "<h2>Built from</h2>", "<table>",
                  "<tr><th>Source</th><th>Content hash (SHA-256)</th><th>Since</th></tr>"]
        for s in built_from:
            state = changes.get(s.get("document_id"), "")
            since = {"changed": "changed since", "removed": "removed since"}.get(state, "unchanged")
            parts.append(f"<tr><td>{html.escape(str(s.get('filename') or s.get('document_id') or ''))}</td>"
                         f"<td><code>{html.escape(str(s.get('content_hash') or 'not recorded'))}</code></td>"
                         f'<td class="{"changed" if state else ""}">{since}</td></tr>')
        parts += ["</table>", "</section>"]
    parts += ["<footer><p class=\"meta\">Saved from Clio. Every citation links to the passage it cites, "
              "as it stood when the report was made.</p></footer>", "</body>", "</html>", ""]
    return "\n".join(parts)


def file_name(artifact: Dict[str, Any]) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", (artifact.get("title") or "report").lower()).strip("-")[:60]
    return f"{stem or 'report'}.html"

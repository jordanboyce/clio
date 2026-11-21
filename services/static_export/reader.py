"""B1: a page to read for each source, built from its passages.

Clio keeps no page's whole text, only its passages, and neighbouring
passages on a page share up to a few hundred characters (services/chunker.py
starts each with the end of the last). A reader page puts them back in
order and drops the text each one repeats from the one before, so nothing
is read twice. Every passage keeps an anchor (``#p-<its id>``) a search
result can land on, and a page of a source with several starts with a
marker (``#page-<n>``).

Pages are plain HTML with no script of their own and a Content-Security-
Policy that allows none inline; their look is ``read/reader.css``, which a
site can replace. Source text is always escaped: it was written by whoever
wrote the sources. In a whole site each page also carries the search box.
"""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any, Dict, List, Optional

from services.static_export.additions import Addition, ExportContext, register

FOLDER = "read"
STYLESHEET = f"{FOLDER}/reader.css"
CONTENTS = f"{FOLDER}/index.html"

# Pages carry no script but the components', loaded from this site.
# 'wasm-unsafe-eval' lets the search box run its model; nothing runs inline.
CSP = ("default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; media-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'")

# File names Windows reserves, with any extension.
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}
MAX_NAME = 60

STYLE = """/* Clio reader pages. Replace this file to give them your site's look. */
:root { color-scheme: light dark; }
body { font: 17px/1.6 Georgia, "Times New Roman", serif; max-width: 42rem; margin: 2rem auto; padding: 0 1rem;
       color: #1f2933; background: #fff; }
nav, header p, .meta, footer, clio-search, h2.page { font-family: system-ui, sans-serif; }
nav { font-size: .9rem; margin-bottom: 1.5rem; }
h1 { line-height: 1.25; }
h2.page { font-size: .8rem; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; color: #52606d;
          border-top: 1px solid #cbd2d9; padding-top: .5rem; margin: 2rem 0 1rem; }
.passage { margin: 0 0 1rem; }
.passage:target { background: #fef3c7; outline: .4rem solid #fef3c7; }
pre.passage { font: .85rem/1.5 ui-monospace, Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
.meta { color: #52606d; font-size: .9rem; }
ol.sources { padding-left: 1.2rem; }
ol.sources li { margin: .3rem 0; }
clio-search { display: block; margin: 1rem 0 2rem; --clio-accent: #1d4ed8; --clio-muted: #52606d;
              --clio-border: #9aa5b1; --clio-mark: #fde68a; }
a { color: #1d4ed8; }
footer { margin-top: 3rem; font-size: .85rem; color: #52606d; }
@media (prefers-color-scheme: dark) {
  body { color: #e4e7eb; background: #12161c; }
  a { color: #93c5fd; }
  h2.page, .meta, footer { color: #9aa5b1; }
  .passage:target { background: #3f2d0c; outline-color: #3f2d0c; }
  clio-search { --clio-accent: #93c5fd; --clio-muted: #9aa5b1; --clio-border: #52606d; --clio-mark: #854d0e; }
}
@media print {
  nav, clio-search, clio-related, footer { display: none; }
  body { max-width: none; margin: 0; }
  .passage:target { background: none; outline: none; }
}
"""


def anchor(passage_id: str) -> str:
    """The id of a passage's element; the same rule as anchorOf in clio-common.js."""
    return "p-" + re.sub(r"[^A-Za-z0-9_-]", "_", str(passage_id))


def _slug(name: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", name or "")
    flat = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", flat.lower()).strip("-")[:MAX_NAME].strip("-")
    if slug.split("-")[0] in _RESERVED or slug in _RESERVED:
        slug = f"{slug}-source"
    return slug


def page_names(documents: List[Dict[str, Any]]) -> Dict[str, str]:
    """Source id -> its page (read/<name>.html). From the file name, so the
    address stays when a source is edited; two sources with the same name
    are told apart by the start of their ids. Lower case ASCII, safe on
    every file system."""
    slugs = {d["id"]: _slug(d.get("name") or "") or f"source-{d['id'][:8]}" for d in documents}
    taken: Dict[str, int] = {}
    for s in slugs.values():
        taken[s] = taken.get(s, 0) + 1
    names = {}
    for doc_id, s in slugs.items():
        if taken[s] > 1 or s == "index":
            s = f"{s}-{re.sub(r'[^a-z0-9]', '', doc_id.lower())[:8]}"
        names[doc_id] = f"{FOLDER}/{s}.html"
    return names


def overlap_limit(chunk_overlap: Optional[int]) -> int:
    """The most a passage can repeat of the one before: the chunker carries
    over ``chunk_overlap`` characters, or a whole last paragraph of up to 120
    (services/chunker.py), give or take the whitespace it strips."""
    return max(int(chunk_overlap or 200), 120) + 8


def without_overlap(previous: str, text: str, limit: int = 208) -> str:
    """``text`` less what it repeats from the end of ``previous``: the
    longest end of ``previous``, no longer than ``limit``, that ``text``
    starts with, of 12 characters or more (shorter matches are as likely to
    be chance). The limit matters in repetitive text, where a longer match
    than the real overlap would drop text that is not repeated."""
    if not previous or not text:
        return text
    tail = previous[-limit:]
    probe = text[:12]
    if len(probe) < 12:
        return text
    at = tail.find(probe)
    while at != -1:
        if text.startswith(tail[at:]):
            return text[len(tail) - at:].lstrip()
        at = tail.find(probe, at + 1)
    return text


def _paragraphs(text: str) -> str:
    """A passage as HTML: blank lines part paragraphs, other line breaks stay."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in parts)


def page(title: str, body: str, *, depth: int, site: bool, head: str = "", components: str = "") -> str:
    """A whole page. ``depth`` is how many folders below the export folder it is."""
    up = "../" * depth
    search = ""
    if site:
        # The whole site keeps the scripts at its root, one level above the export.
        search = (f'<script type="module" src="{up}../clio-search.js"></script>\n'
                  f'<clio-search src="{up}" label="Search"></clio-search>\n')
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">\n'
        f"<title>{html.escape(title)}</title>\n"
        f'<link rel="stylesheet" href="{up}{STYLESHEET}">\n{head}'
        f"</head>\n<body>\n{search}{body}{components}</body>\n</html>\n")


def _source_page(ctx: ExportContext, doc: Dict[str, Any], passages: List[Dict[str, Any]], collection: str,
                 limit: int) -> str:
    title = doc.get("title") or doc.get("name")
    several = (doc.get("pages") or 0) > 1 or len({p["page"] for p in passages}) > 1
    code = any(p.get("lines") for p in passages)
    out: List[str] = []
    last_page: Optional[int] = None
    previous = ""
    for p in passages:
        if p["page"] != last_page:
            previous = ""  # passages never overlap across pages
            if several and p["page"] is not None:
                out.append(f'<h2 class="page" id="page-{int(p["page"])}">Page {int(p["page"])}</h2>\n')
            last_page = p["page"]
        text = without_overlap(previous, p["text"] or "", limit)
        previous = p["text"] or ""
        ident = anchor(p["id"])
        if not text.strip():
            out.append(f'<span class="passage" id="{ident}"></span>\n')
        elif code:
            out.append(f'<pre class="passage" id="{ident}">{html.escape(text)}</pre>\n')
        else:
            out.append(f'<div class="passage" id="{ident}">{_paragraphs(text)}</div>\n')
    link = (doc.get("link") or {}).get("url")
    meta = []
    if doc.get("pages"):
        meta.append(f"{int(doc['pages'])} page{'' if doc['pages'] == 1 else 's'}")
    if link:
        meta.append(f'<a href="{html.escape(link)}">The original</a>')
    body = (f'<nav><a href="index.html">{html.escape(collection)}</a></nav>\n'
            f"<header>\n<h1>{html.escape(title)}</h1>\n"
            + (f'<p class="meta">{" · ".join(meta)}</p>\n' if meta else "")
            + "</header>\n<main>\n" + "".join(out) + "</main>\n"
            + "<footer>Exported from Clio. The text is as Clio read it from the source.</footer>\n")
    return body


def _contents(ctx: ExportContext, names: Dict[str, str], collection: str) -> str:
    description = (ctx.manifest.get("collection") or {}).get("description") or ""
    items = []
    for doc in ctx.documents:
        pages = f' <span class="meta">· {int(doc["pages"])} pages</span>' if (doc.get("pages") or 0) > 1 else ""
        items.append(f'<li><a href="{names[doc["id"]].split("/", 1)[1]}">'
                     f'{html.escape(doc.get("title") or doc.get("name"))}</a>{pages}</li>\n')
    return (f"<header>\n<h1>{html.escape(collection)}</h1>\n"
            + (f'<p class="meta">{html.escape(description)}</p>\n' if description else "")
            + f'<p class="meta">{len(ctx.documents)} source{"" if len(ctx.documents) == 1 else "s"}</p>\n'
            + "</header>\n<main>\n<ol class=\"sources\">\n" + "".join(items) + "</ol>\n</main>\n")


SITEMAP = "sitemap.xml"


def public_address(ctx_or_settings: Any, name: str, site: bool) -> Optional[str]:
    """The absolute address of a file of the export, when the collection says
    where the site will be served (B3), else None. For a whole site that
    address is the site's root, with the export in search/; for the files
    alone, it is the folder holding manifest.json."""
    settings = getattr(ctx_or_settings, "settings", ctx_or_settings) or {}
    root = settings.get("site_url")
    if not root:
        return None
    from services.static_export.common import SITE_DATA_DIR

    return root + (f"{SITE_DATA_DIR}/" if site else "") + name


def _sitemap(urls: List[str]) -> str:
    rows = "".join(f"  <url><loc>{html.escape(u, quote=False)}</loc></url>\n" for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + rows + "</urlset>\n")


def _offer(o) -> Dict[str, Any]:
    from services.static_export.addresses import address_settings

    survey = o.survey
    # The text once (overlaps go), plus about 60 bytes of markup a passage
    # and 1.5 kB a page.
    size = survey["text_bytes"] + survey["passages"] * 60 + (len(survey["documents"]) + 1) * 1500 + len(STYLE)
    site_url = address_settings(o.collection_id).get("site_url")
    note = (f"With a sitemap and canonical links for {site_url}." if site_url else
            "No sitemap or canonical links: they need the address the site will be served at, under "
            "Where results link.")
    return {"available": bool(survey["passages"]), "reason": None if survey["passages"] else "No passages.",
            "bytes": size + (len(survey["documents"]) + 1) * 120 if site_url else size, "note": note,
            "sitemap": bool(site_url)}


def by_document(passages: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for p in passages:
        grouped.setdefault(p["document_id"], []).append(p)
    return grouped


def _write(ctx: ExportContext) -> Dict[str, Any]:
    collection = (ctx.manifest.get("collection") or {}).get("name") or "Sources"
    names = page_names(ctx.documents)
    grouped = by_document(ctx.passages)
    limits = {d["document_id"]: overlap_limit(d.get("chunk_overlap")) for d in ctx.survey["documents"]}
    for doc in ctx.documents:
        # The last address a source can have (B2): older clients ignore the field.
        doc["reader"] = names[doc["id"]]
    ctx.put(STYLESHEET, STYLE)

    def canonical(name: str) -> str:
        url = public_address(ctx, name, ctx.site)
        return f'<link rel="canonical" href="{html.escape(url)}">\n' if url else ""

    parts = ctx.page_parts(None)
    ctx.put(CONTENTS, page(collection, _contents(ctx, names, collection), depth=1, site=ctx.site,
                           head=canonical(CONTENTS) + parts["head"], components=parts["body"]))
    for doc in ctx.documents:
        body = _source_page(ctx, doc, grouped.get(doc["id"], []), collection, limits.get(doc["id"], 208))
        parts = ctx.page_parts(doc)
        ctx.put(names[doc["id"]], page(f"{doc.get('title') or doc.get('name')} · {collection}", body, depth=1,
                                       site=ctx.site, head=canonical(names[doc["id"]]) + parts["head"],
                                       components=parts["body"]))
    entry = {"contents": CONTENTS, "stylesheet": STYLESHEET, "anchor": "#p-{id}"}
    urls = [public_address(ctx, n, ctx.site) for n in [CONTENTS, *[names[d["id"]] for d in ctx.documents]]]
    if all(urls):
        ctx.put(SITEMAP, _sitemap(urls))
        entry["sitemap"] = SITEMAP
    return entry


register(Addition(id="reader", label="A page for each source", offer=_offer, write=_write, stage=10))

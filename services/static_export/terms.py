"""C6: an A to Z index of terms for the reader.

Clio stores the terms it finds in each passage only in a normalised form
(``normalize_entity`` lower-cases and strips plurals: "analysis" is kept as
"analysi"), which cannot be shown to a reader. So at export the exported
passages are read again with the same extractor, keeping the words as they
were written, and each term is shown in the form it is written most often.

Only the exported passages are read, so no term, count or link comes from a
source the gate left out. The terms kept are those the graph would keep
(services/entity_graph.corpus_filter: in at least two passages and no more
than 30% of them), and at most MAX_TERMS of those, the most widely used.
Each links to the passages that mention it on the reader's pages. Terms are
not turned into links inside the reader's pages.
"""

from __future__ import annotations

import html
import unicodedata
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple

from services.static_export.additions import PAGE_HOOKS, Addition, ExportContext, register
from services.static_export import reader

PAGE = f"{reader.FOLDER}/terms.html"
MAX_TERMS = 400
LINKS_PER_TERM = 12


def surface_terms(text: str, max_per_passage: int = 20) -> List[Tuple[str, str]]:
    """(normalised, as written) for each term in a passage, in the order and
    with the limits of entity_graph.extract_entities."""
    from services import entity_graph as eg

    out, seen = [], set()
    for cand in eg._find_acronyms(text) + eg._find_capitalized_phrases(text):
        norm = eg.normalize_entity(cand)
        if len(norm) < 2 or norm in eg._STOPWORDS or norm in seen:
            continue
        seen.add(norm)
        written = " ".join(cand.strip().strip(eg._PUNCT_STRIP).split())
        out.append((norm, written))
        if len(out) >= max_per_passage:
            break
    return out


def collect(passages: List[Dict[str, Any]]):
    """(term -> passages mentioning it, in export order; term -> its written forms)."""
    where: Dict[str, List[int]] = defaultdict(list)
    forms: Dict[str, Counter] = defaultdict(Counter)
    for i, p in enumerate(passages):
        for norm, written in surface_terms(p.get("text") or ""):
            where[norm].append(i)
            forms[norm][written] += 1
    return where, forms


def choose(where: Dict[str, List[int]], total: int) -> List[str]:
    from services import entity_graph as eg

    kept = eg.corpus_filter(Counter({t: len(v) for t, v in where.items()}), total)
    ranked = sorted(kept, key=lambda t: (-len(where[t]), t))[:MAX_TERMS]
    return ranked


def display(forms: Counter) -> str:
    """The form written most often; on a tie, the one with capitals, then the first in order."""
    return sorted(forms.items(), key=lambda kv: (-kv[1], kv[0] == kv[0].lower(), kv[0]))[0][0]


def _letter(term: str) -> str:
    first = unicodedata.normalize("NFKD", term[:1]).encode("ascii", "ignore").decode().upper()
    return first if first.isalpha() else "#"


def _offer(o) -> Dict[str, Any]:
    s = o.survey
    if s["passages"] < 2:
        return {"available": False, "reason": "It needs at least two passages.", "bytes": 0}
    return {"available": True, "reason": None, "bytes": min(MAX_TERMS, s["passages"]) * 400,
            "note": "Needs the pages to read, which are included with it."}


def _write(ctx: ExportContext) -> Dict[str, Any]:
    where, forms = collect(ctx.passages)
    terms = choose(where, len(ctx.passages))
    names = reader.page_names(ctx.documents)
    docs = {d["id"]: d for d in ctx.documents}
    rows = sorted(((display(forms[t]), t) for t in terms), key=lambda r: (_letter(r[0]), r[0].lower(), r[0]))
    letters: Dict[str, List[str]] = defaultdict(list)
    for shown, term in rows:
        links = []
        for i in where[term][:LINKS_PER_TERM]:
            p = ctx.passages[i]
            doc = docs[p["document_id"]]
            page = f", page {int(p['page'])}" if (doc.get("pages") or 0) > 1 and p.get("page") else ""
            href = names[doc["id"]].split("/", 1)[1] + "#" + reader.anchor(p["id"])
            links.append(f'<a href="{html.escape(href)}">{html.escape(doc.get("title") or doc.get("name"))}'
                         f"{html.escape(page)}</a>")
        more = len(where[term]) - LINKS_PER_TERM
        tail = f' <span class="meta">and {more} more</span>' if more > 0 else ""
        letters[_letter(shown)].append(f"<dt>{html.escape(shown)}</dt>\n<dd>{' · '.join(links)}{tail}</dd>\n")
    collection = (ctx.manifest.get("collection") or {}).get("name") or "Sources"
    jump = " ".join(f'<a href="#letter-{k}">{k}</a>' for k in sorted(letters))
    body = (f'<nav><a href="index.html">{html.escape(collection)}</a></nav>\n<header>\n<h1>Index</h1>\n'
            f'<p class="meta">{len(rows)} terms that appear in more than one passage.</p>\n'
            f'<p class="meta">{jump}</p>\n</header>\n<main>\n'
            + "".join(f'<h2 id="letter-{k}">{k}</h2>\n<dl>\n{"".join(letters[k])}</dl>\n' for k in sorted(letters))
            + "</main>\n")
    ctx.put(PAGE, reader.page(f"Index · {collection}", body, depth=1, site=ctx.site))
    return {"page": PAGE, "terms": len(rows)}


def _page_part(ctx: ExportContext, doc):
    # A link from the contents page only.
    return {} if doc is not None else {"body": '<p><a href="terms.html">Index of terms</a></p>\n'}


PAGE_HOOKS["terms"] = _page_part
register(Addition(id="terms", label="An index of terms", offer=_offer, write=_write, after=("reader",)))

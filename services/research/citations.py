"""Citations built from a source's own record, entirely on this server.

Reference managers and AI tools usually build a citation by sending the
work's title to a web service. Here nothing leaves: an entry is assembled
from the research record (fields read from the source's text, from a
catalogue looked up by a printed identifier, or typed by a user, each with
its provenance) and formatted by fixed rules.

What it never does is fill a gap. A source with no known year is cited
``n.d.`` and reported ``missing: ["year"]``; one with no author is cited by
its title, as the styles prescribe. ``missing`` tells the writer exactly
what to add before the citation is fit to print.

Styles: APA 7, Chicago author-date (17th), MLA 9, IEEE and Harvard (Cite
Them Right). Exports: BibTeX, RIS and CSL-JSON, which every reference
manager imports (Zotero, EndNote, Mendeley, JabRef, Word through them).

Page locators are the page *printed* on the page when the source prints
page numbers consistently (``page_offset``), else the page of the file.
Clio indexes pages by their position in the file, and a journal article on
pages 1184-1199 must not be cited at "p. 3".
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from collections import Counter
from typing import Dict, Iterable, List, Optional, Tuple

STYLES = {
    "apa": "APA 7th",
    "chicago": "Chicago author-date",
    "mla": "MLA 9th",
    "ieee": "IEEE",
    "harvard": "Harvard",
}
EXPORT_FORMATS = ("bibtex", "ris", "csl-json")
KINDS = ("article", "book", "chapter", "report", "thesis", "web", "dataset", "other")
# Fields a person may type into a source's record. The first group are
# columns of research_papers; the rest live in its ``details`` JSON.
CORE_FIELDS = ("title", "authors", "year", "venue", "doi", "isbn", "kind")
DETAIL_FIELDS = ("organization", "publisher", "place", "edition", "volume", "issue", "pages",
                 "report_number", "url", "editors", "container_title", "month", "day")
EDITABLE_FIELDS = CORE_FIELDS + DETAIL_FIELDS

_PARTICLES = {"van", "von", "de", "del", "della", "der", "den", "da", "di", "du", "la", "le",
              "dos", "das", "ter", "ten", "bin", "al", "ibn", "st.", "saint"}
_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}
_STOPWORDS = {"a", "an", "the", "of", "on", "in", "for", "and", "to", "with", "at", "by", "from"}

_I_OPEN, _I_CLOSE = "\x01", "\x02"   # italic markers, rendered per output format


# ── names ───────────────────────────────────────────────────────────────

def split_name(name: str) -> Dict[str, str]:
    """``{"family", "given", "suffix"}`` for a personal name, or
    ``{"literal"}`` for an organization.

    Accepts "Maria Okafor", "Okafor, Maria", "Ana de la Cruz", "M. K. Hale"
    and "Martin Luther King Jr.". An organization is typed in braces,
    ``{National Reactor Laboratory}``, the BibTeX convention, and is never
    split or abbreviated."""
    name = " ".join(str(name or "").split()).strip(" ,;")
    if not name:
        return {}
    if name.startswith("{") and name.endswith("}"):
        return {"literal": name[1:-1].strip()}
    if "," in name:
        family, _, rest = name.partition(",")
        rest = rest.strip()
        suffix = ""
        if "," in rest:
            rest, _, suffix = rest.partition(",")
        elif rest.lower() in _SUFFIXES:
            rest, suffix = "", rest
        return {"family": family.strip(), "given": rest.strip(), "suffix": suffix.strip()}
    words = name.split(" ")
    suffix = ""
    if len(words) > 1 and words[-1].lower().strip(",") in _SUFFIXES:
        suffix = words.pop().strip(",")
    if len(words) == 1:
        return {"family": words[0], "given": "", "suffix": suffix}
    # The family name starts at the first lower-case particle after the
    # given names ("Ana de la Cruz"), else it is the last word.
    start = len(words) - 1
    for i in range(1, len(words) - 1):
        if words[i].lower() in _PARTICLES:
            start = i
            break
    return {"family": " ".join(words[start:]), "given": " ".join(words[:start]), "suffix": suffix}


def _initials(given: str, spaced: bool = True) -> str:
    """"Maria Elena" -> "M. E."; "Jean-Paul" -> "J.-P."; "M.K." -> "M. K."."""
    out = []
    for word in re.split(r"[\s.]+", given or ""):
        if not word:
            continue
        parts = [p for p in word.split("-") if p]
        out.append("-".join(f"{p[0].upper()}." for p in parts))
    return (" " if spaced else "").join(out)


def _person(name: dict, order: str, initials: bool = False, spaced: bool = True) -> str:
    """One name as a style writes it. ``order``: "family-first" or "given-first"."""
    if not name:
        return ""
    if "literal" in name:
        return name["literal"]
    given = _initials(name.get("given", ""), spaced) if initials else name.get("given", "")
    family = name.get("family", "")
    suffix = name.get("suffix", "")
    if order == "family-first":
        out = f"{family}, {given}" if given else family
        return f"{out}, {suffix}" if suffix else out
    out = f"{given} {family}".strip()
    return f"{out} {suffix}" if suffix else out


def _join(names: List[str], last: str, serial: bool = True) -> str:
    if len(names) <= 1:
        return "".join(names)
    if len(names) == 2:
        return f"{names[0]} {last} {names[1]}"
    return ", ".join(names[:-1]) + ("," if serial else "") + f" {last} {names[-1]}"


# ── the item ────────────────────────────────────────────────────────────

def _strip_extension(filename: str) -> str:
    return re.sub(r"\.[A-Za-z0-9]{1,5}$", "", filename or "").strip()


def build_item(doc: dict, paper: Optional[dict], collection_name: Optional[str] = None) -> dict:
    """The citation item for one source: its fields, where each came from,
    and what the record lacks.

    ``doc`` is the collection's document row, ``paper`` its research record
    (``None`` when not analyzed). A source with no record is cited by its
    file name, said so in ``provenance.title``."""
    paper = paper or {}
    details = dict(paper.get("details") or {})
    provenance = dict(paper.get("provenance") or {})
    title = (paper.get("title") or "").strip()
    if not title:
        title = _strip_extension(doc.get("filename") or "") or "Untitled"
        provenance["title"] = {"source": "filename", "detail": "No title was read; the file name stands in"}
    kind = (paper.get("kind") or "").strip() or None
    venue = (details.get("container_title") or paper.get("venue") or "").strip() or None
    # Analysis calls everything that is not a book an "article"; with no
    # journal, DOI or arXiv id that is a guess, and an internal memo must not
    # be cited (or nagged about) as a journal article missing its journal.
    if (kind == "article" and (provenance.get("kind") or {}).get("source") != "user"
            and not (venue or paper.get("doi") or paper.get("arxiv_id"))):
        kind = None
    source_url = doc.get("source_path") if doc.get("source_type") == "url" else None
    url = (details.get("url") or source_url or "").strip() or None
    if not kind:
        if venue and paper.get("doi"):
            kind = "article"
        elif url and not paper.get("doi"):
            kind = "web"
        elif venue:
            kind = "article"
        else:
            kind = "report" if details.get("report_number") or details.get("organization") else "other"
    # A book's "venue" is its publisher (Open Library has no journal).
    publisher = (details.get("publisher") or "").strip() or None
    if kind == "book" and venue and not publisher:
        publisher, venue = venue, None

    authors = [split_name(a) for a in (paper.get("authors") or []) if str(a).strip()]
    authors = [a for a in authors if a]
    organization = (details.get("organization") or "").strip()
    if not authors and organization:
        authors = [{"literal": organization}]
    editors = [split_name(a) for a in (details.get("editors") or []) if str(a).strip()]
    year = paper.get("year")
    try:
        year = int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        year = None

    item = {
        "id": doc.get("document_id"),
        "document_id": doc.get("document_id"),
        "kind": kind,
        "title": title,
        "authors": authors,
        "editors": [e for e in editors if e],
        "year": year,
        "month": details.get("month"),
        "day": details.get("day"),
        "venue": venue,
        "volume": details.get("volume"),
        "issue": details.get("issue"),
        "pages": details.get("pages") or details.get("printed_pages"),
        "publisher": publisher,
        "place": details.get("place"),
        "edition": details.get("edition"),
        "report_number": details.get("report_number"),
        "organization": organization or None,
        "doi": paper.get("doi"),
        "isbn": paper.get("isbn"),
        "arxiv_id": paper.get("arxiv_id"),
        "url": url,
        "filename": doc.get("filename"),
        "collection": collection_name,
        "page_offset": details.get("page_offset"),
        "analyzed": bool(paper.get("analyzed_at")),
        "provenance": provenance,
    }
    item["internal"] = not (item["doi"] or item["isbn"] or item["arxiv_id"] or item["url"])
    item["missing"] = missing_fields(item)
    return item


def missing_fields(item: dict) -> List[str]:
    """What a printed citation of this item would need and does not have."""
    missing = []
    if item["provenance"].get("title", {}).get("source") == "filename":
        missing.append("title")
    if not item["authors"]:
        missing.append("authors")
    if not item["year"]:
        missing.append("year")
    if item["kind"] == "article" and not item["venue"]:
        missing.append("venue")
    if item["kind"] == "book" and not item["publisher"]:
        missing.append("publisher")
    if item["kind"] == "report" and not (item["publisher"] or item["organization"]):
        missing.append("publisher")
    if item["kind"] == "chapter" and not item["venue"]:
        missing.append("container_title")
    return missing


# ── page numbers ────────────────────────────────────────────────────────

_PAGE_LINE = re.compile(r"^\s*(?:page\s+|p\.\s*|-\s*)?(\d{1,5})(?:\s*-)?\s*$", re.IGNORECASE)
_EDGE_NUMBER = re.compile(r"(?:^|\s)(\d{1,5})(?:\s|$)")


def _edge_numbers(text: str) -> List[int]:
    """Numbers standing alone at the top or bottom of a page: running heads
    ("12  Journal of X") and folios ("— 12 —", "Page 12")."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return []
    found: List[int] = []
    for line in lines[:2] + lines[-2:]:
        m = _PAGE_LINE.match(line)
        if m:
            found.append(int(m.group(1)))
            continue
        # A short running head that starts or ends with the folio.
        if len(line) <= 90:
            for token in (line.split()[0], line.split()[-1]):
                if token.isdigit() and len(token) <= 5:
                    found.append(int(token))
    return found


def page_offset(pages: Dict[int, str]) -> Optional[Tuple[int, int]]:
    """``(offset, pages_agreeing)`` when the source prints page numbers that
    run in step with the file's pages (printed = file page + offset), else
    None.

    Requires at least three pages, and more than half of the pages that
    show any edge number, to agree on one offset: a year in a running head
    or a figure number must not move every citation."""
    votes: Counter = Counter()
    numbered = 0
    for page, text in pages.items():
        numbers = set(_edge_numbers(text))
        if not numbers:
            continue
        numbered += 1
        for number in numbers:
            if 1900 <= number <= 2100 and number - int(page) > 500:
                continue  # a year, not a folio
            votes[number - int(page)] += 1
    if not votes:
        return None
    offset, agreeing = votes.most_common(1)[0]
    if agreeing < 3 or agreeing * 2 <= numbered:
        return None
    if offset < -50:
        return None
    return offset, agreeing


def printed_page(item: dict, file_page: Optional[int]) -> Optional[str]:
    """The page to put in a citation for a passage on ``file_page``."""
    if not file_page:
        return None
    offset = item.get("page_offset")
    if isinstance(offset, int) and int(file_page) + offset >= 1:
        return str(int(file_page) + offset)
    return str(int(file_page))


def page_range(pages: Dict[int, str], offset: int) -> Optional[str]:
    if not pages:
        return None
    first, last = min(pages) + offset, max(pages) + offset
    if first < 1:
        return None
    return f"{first}–{last}" if last > first else str(first)


# ── rendering helpers ───────────────────────────────────────────────────

def _it(text: Optional[str]) -> str:
    return f"{_I_OPEN}{text}{_I_CLOSE}" if text else ""


def render(text: str, fmt: str = "text") -> str:
    """``text`` (plain), ``markdown`` (*italic*) or ``html`` (<i>, escaped)."""
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    text = re.sub(r"([.?!])\.", r"\1", text)          # "Title?." -> "Title?"
    text = re.sub(rf"([.?!]){_I_CLOSE}\.", rf"\1{_I_CLOSE}", text)
    text = re.sub(r" {2,}", " ", text).strip()
    if fmt == "html":
        text = html.escape(text, quote=False)
        return text.replace(_I_OPEN, "<i>").replace(_I_CLOSE, "</i>")
    if fmt == "markdown":
        return text.replace(_I_OPEN, "*").replace(_I_CLOSE, "*")
    return text.replace(_I_OPEN, "").replace(_I_CLOSE, "")


def _end(text: str) -> str:
    text = (text or "").strip()
    return text if not text or text[-1] in ".?!" or text.endswith(_I_CLOSE) and text[-2:-1] in ".?!" else text + "."


def _doi_url(item: dict) -> Optional[str]:
    if item.get("doi"):
        return f"https://doi.org/{item['doi']}"
    if item.get("arxiv_id"):
        return f"https://arxiv.org/abs/{item['arxiv_id']}"
    return item.get("url")


def _edition(item: dict, style: str) -> str:
    ed = str(item.get("edition") or "").strip()
    if not ed or ed in ("1", "1st"):
        return ""
    if ed.isdigit():
        n = int(ed)
        suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        ed = f"{n}{suffix}"
    return {"harvard": f"{ed} edn", "mla": f"{ed} ed."}.get(style, f"{ed} ed.")


def _location_note(item: dict, include_location: bool) -> str:
    """Where an internal source is kept, so a reviewer inside the
    organization can find it. Never for a source with a public identifier."""
    if not include_location or not item.get("internal"):
        return ""
    where = f"collection “{item['collection']}”, " if item.get("collection") else ""
    return f"Internal document, {where}file {item.get('filename')}."


def _date_label(item: dict, style: str) -> str:
    if item.get("year"):
        return str(item["year"])
    return {"mla": "", "ieee": ""}.get(style, "n.d.")


# ── styles: reference-list entries ──────────────────────────────────────

def _authors_apa(authors: List[dict]) -> str:
    names = [_person(a, "family-first", initials=True) for a in authors]
    if len(names) > 20:
        return ", ".join(names[:19]) + ", … " + names[-1]
    if len(names) == 2:
        return f"{names[0]}, & {names[1]}"
    return _join(names, "&") if len(names) > 2 else "".join(names)


def _apa(item: dict, include_location: bool) -> str:
    title = item["title"]
    date = f"({_date_label(item, 'apa')})."
    author = _authors_apa(item["authors"])
    kind = item["kind"]
    tail = []
    if kind in ("article", "chapter") and item.get("venue"):
        container = _it(item["venue"])
        if item.get("volume"):
            container += f", {_it(str(item['volume']))}"
            if item.get("issue"):
                container += f"({item['issue']})"
        elif item.get("issue"):
            container += f", ({item['issue']})"
        if item.get("pages"):
            container += f", {item['pages']}"
        head_title = _end(title)
        if kind == "chapter":
            eds = item.get("editors") or []
            ed_names = _join([_person(e, "given-first", initials=True) for e in eds], "&")
            prefix = f"In {ed_names} (Ed{'s' if len(eds) > 1 else ''}.), " if eds else "In "
            pages = f" (pp. {item['pages']})" if item.get("pages") else ""
            container = f"{prefix}{_it(item['venue'])}{pages}"
            tail.append(_end(container))
            if item.get("publisher"):
                tail.append(_end(item["publisher"]))
        else:
            tail.append(_end(container))
    else:
        extra = []
        ed = _edition(item, "apa")
        if ed:
            extra.append(ed)
        if item.get("report_number"):
            extra.append(f"Report No. {item['report_number']}")
        head_title = _it(title) + (f" ({', '.join(extra)})" if extra else "")
        head_title = _end(head_title)
        publisher = item.get("publisher")
        if publisher and publisher != (author if "literal" in (item["authors"] or [{}])[0] else None):
            tail.append(_end(publisher))
    link = _doi_url(item)
    if author:
        parts = [author if author.endswith(".") else author + ".", date, head_title, *tail]
    else:
        parts = [head_title, date, *tail]
    if link:
        parts.append(link)
    note = _location_note(item, include_location)
    if note:
        parts.append(note)
    return " ".join(p for p in parts if p)


def _authors_chicago(authors: List[dict]) -> str:
    if not authors:
        return ""
    names = [_person(authors[0], "family-first")] + [_person(a, "given-first") for a in authors[1:]]
    if len(names) > 10:
        return ", ".join(names[:7]) + ", et al."
    return _join(names, "and")


def _chicago(item: dict, include_location: bool) -> str:
    author = _authors_chicago(item["authors"])
    date = _date_label(item, "chicago")
    kind = item["kind"]
    parts = []
    if kind in ("article", "chapter") and item.get("venue"):
        title = f"“{_end(item['title'])}”"
        if kind == "chapter":
            eds = item.get("editors") or []
            ed = f", edited by {_join([_person(e, 'given-first') for e in eds], 'and')}" if eds else ""
            pages = f", {item['pages']}" if item.get("pages") else ""
            container = f"In {_it(item['venue'])}{ed}{pages}."
            pub = ": ".join(x for x in (item.get("place"), item.get("publisher")) if x)
            body = [title, container, _end(pub) if pub else ""]
        else:
            container = _it(item["venue"])
            if item.get("volume"):
                container += f" {item['volume']}"
            if item.get("issue"):
                container += f" ({item['issue']})"
            if item.get("pages"):
                container += f": {item['pages']}"
            body = [title, _end(container)]
    else:
        extra = []
        ed = _edition(item, "chicago")
        if ed:
            extra.append(ed)
        if item.get("report_number"):
            extra.append(f"Report {item['report_number']}")
        title = _end(_it(item["title"]))
        pub = ": ".join(x for x in (item.get("place"), item.get("publisher")) if x)
        body = [title, *(_end(x) for x in extra), _end(pub) if pub else ""]
    if author:
        parts = [_end(author), _end(date), *body]
    else:
        parts = [*body[:1], _end(date), *body[1:]]
    link = _doi_url(item)
    if link:
        parts.append(_end(link))
    note = _location_note(item, include_location)
    if note:
        parts.append(note)
    return " ".join(p for p in parts if p)


def _authors_mla(authors: List[dict]) -> str:
    if not authors:
        return ""
    first = _person(authors[0], "family-first")
    if len(authors) == 1:
        return first
    if len(authors) == 2:
        return f"{first}, and {_person(authors[1], 'given-first')}"
    return f"{first}, et al."


def _mla(item: dict, include_location: bool) -> str:
    author = _authors_mla(item["authors"])
    kind = item["kind"]
    containers = []
    if kind in ("article", "chapter") and item.get("venue"):
        title = f"“{_end(item['title'])}”"
        containers.append(_it(item["venue"]))
        if kind == "chapter" and item.get("editors"):
            containers.append("edited by " + _join([_person(e, "given-first") for e in item["editors"]], "and"))
        if item.get("volume"):
            containers.append(f"vol. {item['volume']}")
        if item.get("issue"):
            containers.append(f"no. {item['issue']}")
        if kind == "chapter" and item.get("publisher"):
            containers.append(item["publisher"])
        if item.get("year"):
            containers.append(str(item["year"]))
        if item.get("pages"):
            containers.append(f"pp. {item['pages']}" if re.search(r"[-–]", str(item["pages"])) else f"p. {item['pages']}")
    else:
        title = _end(_it(item["title"]))
        ed = _edition(item, "mla")
        if ed:
            containers.append(ed)
        if item.get("report_number"):
            containers.append(f"Report {item['report_number']}")
        if item.get("publisher"):
            containers.append(item["publisher"])
        if item.get("year"):
            containers.append(str(item["year"]))
    link = item.get("doi") and f"https://doi.org/{item['doi']}" or item.get("url")
    if link:
        containers.append(link)
    parts = [_end(author) if author else "", title]
    if containers:
        parts.append(_end(", ".join(containers)))
    note = _location_note(item, include_location)
    if note:
        parts.append(note)
    return " ".join(p for p in parts if p)


def _authors_ieee(authors: List[dict]) -> str:
    names = [_person(a, "given-first", initials=True) for a in authors]
    if len(names) > 6:
        return f"{names[0]} et al."
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return _join(names, "and")


def _ieee(item: dict, include_location: bool) -> str:
    author = _authors_ieee(item["authors"])
    kind = item["kind"]
    if kind in ("article", "chapter") and item.get("venue"):
        bits = [f"“{item['title']},”", _it(item["venue"]) + ","]
        if kind == "chapter":
            bits = [f"“{item['title']},”", "in " + _it(item["venue"]) + ","]
        if item.get("volume"):
            bits.append(f"vol. {item['volume']},")
        if item.get("issue"):
            bits.append(f"no. {item['issue']},")
        if item.get("pages"):
            bits.append(f"pp. {item['pages']},")
        if item.get("year"):
            bits.append(f"{item['year']},")
        body = " ".join(bits).rstrip(",") + "."
    elif kind == "report":
        org = item.get("organization") or item.get("publisher")
        bits = [f"“{item['title']},”"]
        if org and org != author:
            bits.append(f"{org},")
        if item.get("place"):
            bits.append(f"{item['place']},")
        if item.get("report_number"):
            bits.append(f"Rep. {item['report_number']},")
        if item.get("year"):
            bits.append(f"{item['year']},")
        body = " ".join(bits).rstrip(",") + "."
    else:
        bits = [_it(item["title"])]
        ed = _edition(item, "ieee")
        body = bits[0] + (f", {ed}" if ed else "") + "."
        pub = ": ".join(x for x in (item.get("place"), item.get("publisher")) if x)
        tail = ", ".join(x for x in (pub, str(item["year"]) if item.get("year") else "") if x)
        if tail:
            body += f" {tail}."
    parts = [f"{author}," if author else "", body]
    if item.get("doi"):
        parts.append(f"doi: {item['doi']}.")
    elif item.get("arxiv_id"):
        parts.append(f"arXiv: {item['arxiv_id']}.")
    elif item.get("url"):
        parts.append(f"[Online]. Available: {item['url']}")
    note = _location_note(item, include_location)
    if note:
        parts.append(note)
    return " ".join(p for p in parts if p)


def _authors_harvard(authors: List[dict]) -> str:
    names = [_person(a, "family-first", initials=True, spaced=False) for a in authors]
    return _join(names, "and", serial=False)


def _harvard(item: dict, include_location: bool) -> str:
    author = _authors_harvard(item["authors"])
    date = f"({_date_label(item, 'harvard')})"
    kind = item["kind"]
    if kind in ("article", "chapter") and item.get("venue"):
        title = f"‘{item['title']}’,"
        container = _it(item["venue"])
        if kind == "chapter":
            eds = item.get("editors") or []
            ed = f"{_authors_harvard(eds)} (ed{'s' if len(eds) > 1 else ''}.) " if eds else ""
            container = f"in {ed}{_it(item['venue'])}."
            pub = ": ".join(x for x in (item.get("place"), item.get("publisher")) if x)
            pages = f"pp. {item['pages']}." if item.get("pages") else ""
            body = [title, container, _end(pub) if pub else "", pages]
        else:
            if item.get("volume"):
                container += f", {item['volume']}"
                if item.get("issue"):
                    container += f"({item['issue']})"
            if item.get("pages"):
                container += f", pp. {item['pages']}"
            body = [title, _end(container)]
    else:
        title = _end(_it(item["title"]))
        extra = []
        ed = _edition(item, "harvard")
        if ed:
            extra.append(_end(ed))
        if item.get("report_number"):
            extra.append(f"Report {item['report_number']}.")
        pub = ": ".join(x for x in (item.get("place"), item.get("publisher")) if x)
        body = [title, *extra, _end(pub) if pub else ""]
    head = f"{author} {date}" if author else f"{body.pop(0).rstrip(',')} {date}"
    parts = [head, *body]
    link = _doi_url(item)
    if link:
        parts.append(f"Available at: {link}.")
    note = _location_note(item, include_location)
    if note:
        parts.append(note)
    return " ".join(p for p in parts if p)


_STYLE_FUNCS = {"apa": _apa, "chicago": _chicago, "mla": _mla, "ieee": _ieee, "harvard": _harvard}


def reference_entry(item: dict, style: str = "apa", fmt: str = "text",
                    include_location: bool = True, number: Optional[int] = None) -> str:
    """One reference-list entry."""
    if style not in _STYLE_FUNCS:
        raise ValueError(f"Unknown citation style {style!r}; choose one of {', '.join(STYLES)}")
    text = _STYLE_FUNCS[style](item, include_location)
    if style == "ieee" and number is not None:
        text = f"[{number}] {text}"
    return render(text, fmt)


# ── styles: in-text citations ───────────────────────────────────────────

def _surname(name: dict) -> str:
    return name.get("literal") or name.get("family") or ""


def _short_title(title: str) -> str:
    words = title.split()
    return " ".join(words[:4]) + ("…" if len(words) > 4 else "")


def in_text(item: dict, style: str = "apa", page: Optional[str] = None,
            number: Optional[int] = None, fmt: str = "text") -> str:
    """The parenthetical citation, with a page locator when given.

    APA "(Okafor et al., 2021, p. 4)", Chicago "(Okafor et al. 2021, 4)",
    MLA "(Okafor et al. 4)", IEEE "[3, p. 4]", Harvard "(Okafor et al., 2021, p. 4)"."""
    authors = item["authors"]
    names = [_surname(a) for a in authors]
    year = _date_label(item, style)
    page_s = str(page) if page else ""
    many = re.search(r"[-–,]", page_s)
    if style == "ieee":
        n = number if number is not None else "?"
        return render(f"[{n}, p{'p' if many else ''}. {page_s}]" if page_s else f"[{n}]", fmt)
    if style == "apa":
        who = (names[0] if len(names) == 1 else f"{names[0]} & {names[1]}" if len(names) == 2
               else f"{names[0]} et al." if names else _it(_short_title(item["title"])))
        loc = f", p{'p' if many else ''}. {page_s}" if page_s else ""
        return render(f"({who}, {year}{loc})", fmt)
    if style == "harvard":
        who = (names[0] if len(names) == 1 else f"{names[0]} and {names[1]}" if len(names) == 2
               else f"{names[0]}, {names[1]} and {names[2]}" if len(names) == 3
               else f"{names[0]} et al." if names else _it(_short_title(item["title"])))
        loc = f", p{'p' if many else ''}. {page_s}" if page_s else ""
        return render(f"({who}, {year}{loc})", fmt)
    if style == "chicago":
        who = (names[0] if len(names) == 1 else f"{names[0]} and {names[1]}" if len(names) == 2
               else f"{names[0]}, {names[1]}, and {names[2]}" if len(names) == 3
               else f"{names[0]} et al." if names else _it(_short_title(item["title"])))
        loc = f", {page_s}" if page_s else ""
        return render(f"({who} {year}{loc})", fmt)
    if style == "mla":
        who = (names[0] if len(names) == 1 else f"{names[0]} and {names[1]}" if len(names) == 2
               else f"{names[0]} et al." if names else f"“{_short_title(item['title'])}”")
        return render(f"({who} {page_s})" if page_s else f"({who})", fmt)
    raise ValueError(f"Unknown citation style {style!r}")


def sort_key(item: dict, style: str) -> tuple:
    """Alphabetical by first author (or title), then year: every style here
    but IEEE, which numbers in order of first citation."""
    first = _surname(item["authors"][0]) if item["authors"] else item["title"]
    first = re.sub(r"^(the|a|an)\s+", "", first.lower())
    return (unicodedata.normalize("NFKD", first), item.get("year") or 0, item["title"].lower())


def bibliography(items: Iterable[dict], style: str = "apa", fmt: str = "text",
                 include_location: bool = True) -> List[str]:
    items = list(items)
    if style == "ieee":
        return [reference_entry(it, style, fmt, include_location, number=i) for i, it in enumerate(items, 1)]
    return [reference_entry(it, style, fmt, include_location)
            for it in sorted(items, key=lambda it: sort_key(it, style))]


# ── exports ─────────────────────────────────────────────────────────────

def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()


def citation_key(item: dict, taken: set) -> str:
    """okafor2021root, okafor2021roota, …: stable and readable."""
    first = _surname(item["authors"][0]) if item["authors"] else ""
    if item["authors"] and "literal" in item["authors"][0]:
        first = (first.split() or [""])[0]   # an organization: its first word
    first = re.sub(r"[^a-z]", "", _ascii(first).lower()) or "anon"
    word = next((w for w in re.findall(r"[a-z]+", _ascii(item["title"]).lower()) if w not in _STOPWORDS), "")
    base = f"{first}{item.get('year') or 'nd'}{word}"
    key, n = base, 0
    while key in taken:
        key = f"{base}{chr(ord('a') + n)}" if n < 26 else f"{base}{n}"
        n += 1
    taken.add(key)
    return key


def _bib_escape(text: str) -> str:
    text = str(text)
    for ch in "&%$#_":
        text = text.replace(ch, "\\" + ch)
    return text.replace("{", "\\{").replace("}", "\\}") if not text.startswith("{") else text


def _bib_name(name: dict) -> str:
    if "literal" in name:
        return "{" + _bib_escape(name["literal"]) + "}"
    out = _bib_escape(name.get("family", ""))
    if " " in name.get("family", ""):
        out = "{" + out + "}"
    if name.get("suffix"):
        out += f", {name['suffix']}"
    if name.get("given"):
        out += f", {_bib_escape(name['given'])}"
    return out


_BIB_TYPES = {"article": "article", "book": "book", "chapter": "incollection", "report": "techreport",
              "thesis": "phdthesis", "web": "online", "dataset": "misc", "other": "misc"}


def to_bibtex(items: Iterable[dict], include_location: bool = True) -> str:
    taken: set = set()
    out = []
    for item in items:
        kind = item["kind"]
        if kind == "article" and not item.get("venue"):
            kind = "other"
        fields: List[Tuple[str, str]] = []
        if item["authors"]:
            fields.append(("author", " and ".join(_bib_name(a) for a in item["authors"])))
        if item.get("editors"):
            fields.append(("editor", " and ".join(_bib_name(a) for a in item["editors"])))
        fields.append(("title", "{" + _bib_escape(item["title"]) + "}"))
        if item.get("venue"):
            fields.append(("journal" if kind == "article" else "booktitle", _bib_escape(item["venue"])))
        for src, dst in (("year", "year"), ("month", "month"), ("volume", "volume"), ("issue", "number"),
                         ("edition", "edition"), ("place", "address"), ("doi", "doi"),
                         ("isbn", "isbn"), ("url", "url")):
            if item.get(src):
                fields.append((dst, _bib_escape(item[src]) if dst not in ("url", "doi") else str(item[src])))
        if item.get("pages"):
            fields.append(("pages", str(item["pages"]).replace("–", "--")))
        if item.get("publisher"):
            fields.append(("institution" if kind == "report" else "publisher", _bib_escape(item["publisher"])))
        elif kind == "report" and item.get("organization"):
            fields.append(("institution", _bib_escape(item["organization"])))
        if item.get("report_number"):
            fields.append(("number", _bib_escape(item["report_number"])))
        if item.get("arxiv_id"):
            fields += [("eprint", item["arxiv_id"]), ("archiveprefix", "arXiv")]
        note = _location_note(item, include_location)
        if note:
            fields.append(("note", _bib_escape(note)))
        key = citation_key(item, taken)
        body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields)
        out.append(f"@{_BIB_TYPES.get(kind, 'misc')}{{{key},\n{body}\n}}")
    return "\n\n".join(out) + ("\n" if out else "")


_RIS_TYPES = {"article": "JOUR", "book": "BOOK", "chapter": "CHAP", "report": "RPRT", "thesis": "THES",
              "web": "ELEC", "dataset": "DATA", "other": "GEN"}


def to_ris(items: Iterable[dict], include_location: bool = True) -> str:
    out = []
    for item in items:
        lines = [f"TY  - {_RIS_TYPES.get(item['kind'], 'GEN')}"]
        for a in item["authors"]:
            lines.append(f"AU  - {_person(a, 'family-first')}")
        for e in item.get("editors") or []:
            lines.append(f"A2  - {_person(e, 'family-first')}")
        lines.append(f"TI  - {item['title']}")
        if item.get("venue"):
            lines.append(f"{'JO' if item['kind'] == 'article' else 'T2'}  - {item['venue']}")
        if item.get("year"):
            lines.append(f"PY  - {item['year']}")
        for src, tag in (("volume", "VL"), ("issue", "IS"), ("publisher", "PB"), ("place", "CY"),
                         ("edition", "ET"), ("doi", "DO"), ("url", "UR"), ("report_number", "M1")):
            if item.get(src):
                lines.append(f"{tag}  - {item[src]}")
        if item.get("isbn"):
            lines.append(f"SN  - {item['isbn']}")
        if item.get("pages"):
            start, _, end = str(item["pages"]).replace("--", "–").partition("–")
            lines.append(f"SP  - {start.strip()}")
            if end:
                lines.append(f"EP  - {end.strip()}")
        note = _location_note(item, include_location)
        if note:
            lines.append(f"N1  - {note}")
        lines.append("ER  - ")
        out.append("\n".join(lines))
    return "\n\n".join(out) + ("\n" if out else "")


_CSL_TYPES = {"article": "article-journal", "book": "book", "chapter": "chapter", "report": "report",
              "thesis": "thesis", "web": "webpage", "dataset": "dataset", "other": "document"}


def _csl_name(name: dict) -> dict:
    if "literal" in name:
        return {"literal": name["literal"]}
    out = {"family": name.get("family", "")}
    if name.get("given"):
        out["given"] = name["given"]
    if name.get("suffix"):
        out["suffix"] = name["suffix"]
    return out


def to_csl_json(items: Iterable[dict], include_location: bool = True) -> str:
    taken: set = set()
    out = []
    for item in items:
        csl = {"id": citation_key(item, taken), "type": _CSL_TYPES.get(item["kind"], "document"),
               "title": item["title"]}
        if item["authors"]:
            csl["author"] = [_csl_name(a) for a in item["authors"]]
        if item.get("editors"):
            csl["editor"] = [_csl_name(a) for a in item["editors"]]
        if item.get("year"):
            parts = [item["year"]]
            for extra in (item.get("month"), item.get("day")):
                try:
                    parts.append(int(extra))
                except (TypeError, ValueError):
                    break
            csl["issued"] = {"date-parts": [parts]}
        for src, dst in (("venue", "container-title"), ("volume", "volume"), ("issue", "issue"),
                         ("pages", "page"), ("publisher", "publisher"), ("place", "publisher-place"),
                         ("edition", "edition"), ("report_number", "number"), ("doi", "DOI"),
                         ("isbn", "ISBN"), ("url", "URL")):
            if item.get(src):
                csl[dst] = str(item[src])
        if include_location and item.get("internal"):
            if item.get("collection"):
                csl["archive"] = item["collection"]
            csl["archive_location"] = item.get("filename")
        out.append(csl)
    return json.dumps(out, ensure_ascii=False, indent=2) + "\n"


def export(items: Iterable[dict], fmt: str, include_location: bool = True) -> Tuple[str, str, str]:
    """``(content, media_type, file_extension)`` for one export format."""
    items = list(items)
    if fmt == "bibtex":
        return to_bibtex(items, include_location), "application/x-bibtex", "bib"
    if fmt == "ris":
        return to_ris(items, include_location), "application/x-research-info-systems", "ris"
    if fmt == "csl-json":
        return to_csl_json(items, include_location), "application/vnd.citationstyles.csl+json", "json"
    raise ValueError(f"Unknown export format {fmt!r}; choose one of {', '.join(EXPORT_FORMATS)}")


def cite(item: dict, style: str, page: Optional[int] = None, number: Optional[int] = None,
         include_location: bool = True) -> dict:
    """Everything a caller shows for one source: the entry in three formats,
    the in-text citation (with the printed page for ``page``), and gaps."""
    locator = printed_page(item, page)
    return {
        "document_id": item["document_id"],
        "kind": item["kind"],
        "style": style,
        "reference": reference_entry(item, style, "text", include_location, number),
        "reference_html": reference_entry(item, style, "html", include_location, number),
        "reference_markdown": reference_entry(item, style, "markdown", include_location, number),
        "in_text": in_text(item, style, locator, number),
        "page": locator,
        "file_page": page,
        "page_is_printed": bool(page and isinstance(item.get("page_offset"), int)),
        "missing": item["missing"],
    }

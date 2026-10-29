"""Read a paper's bibliographic record and reference list from its own text.

No model is involved. Every value returned here is a substring of the paper
(whitespace and line-break hyphenation normalized) or is derived from one by
a rule a person can check — a year read from an arXiv identifier, say — and
each carries a provenance entry naming the page it came from and the rule
that found it. A field the rules cannot find is left empty: an empty title is
honest, a plausible invented one is the failure researchers worry about.

Inputs are plain ``{page_number: text}`` maps (what ``DocumentExtractor``
returns), so everything here is testable on strings.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

MAX_ABSTRACT_CHARS = 4000
MAX_REFERENCE_CHARS = 1200
MAX_REFERENCES = 500
MAX_LINKS = 200

# ── identifiers ─────────────────────────────────────────────────────────

# DOI syntax per the DOI handbook: "10." registrant "/" suffix. The suffix may
# contain almost anything; stop at whitespace and quote/angle characters and
# trim trailing sentence punctuation afterwards.
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.IGNORECASE)
_ARXIV_NEW_RE = re.compile(
    r"(?:arxiv(?:\.org/(?:abs|pdf)/|:\s?|\s+preprint\s+arxiv:\s?)|\barXiv\s+)(\d{4}\.\d{4,5})(v\d+)?",
    re.IGNORECASE,
)
_ARXIV_OLD_RE = re.compile(
    r"(?:arxiv(?:\.org/(?:abs|pdf)/|:\s?))([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.IGNORECASE
)
_URL_RE = re.compile(r"\bhttps?://[^\s<>\"'\]\)}]+", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?<!\d)(19[5-9]\d|20\d\d)(?!\d)")


def _trim_identifier(value: str) -> str:
    value = value.rstrip(".,;:")
    # A DOI or URL inside parentheses or brackets keeps its own balanced
    # pairs and loses the closing one that belonged to the sentence.
    for opener, closer in (("(", ")"), ("[", "]")):
        while value.endswith(closer) and value.count(closer) > value.count(opener):
            value = value[:-1]
    return value.rstrip(".,;:")


def normalize_doi(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^doi:\s*", "", value, flags=re.IGNORECASE)
    return _trim_identifier(value).lower()


def find_dois(text: str) -> List[str]:
    out = []
    # Line-wrapped DOIs ("10.1038/s41586-\n021-03819-2") are rejoined first.
    joined = re.sub(r"(10\.\d{4,9}/\S*?)-\s*\n\s*(\S)", r"\1-\2", text or "")
    for match in _DOI_RE.finditer(joined):
        doi = normalize_doi(match.group(1))
        if len(doi) > 7 and doi not in out:
            out.append(doi)
    return out


def find_arxiv_ids(text: str) -> List[str]:
    out = []
    for regex in (_ARXIV_NEW_RE, _ARXIV_OLD_RE):
        for match in regex.finditer(text or ""):
            ident = match.group(1)
            if ident not in out:
                out.append(ident)
    return out


def find_urls(text: str) -> List[str]:
    out = []
    joined = re.sub(r"(https?://\S+?)-\s*\n\s*(\S)", r"\1-\2", text or "")
    for match in _URL_RE.finditer(joined):
        url = _trim_identifier(match.group(0))
        if len(url) > 10 and url not in out:
            out.append(url)
    return out


def arxiv_year(arxiv_id: str) -> Optional[int]:
    """New-style arXiv ids begin with YYMM; old-style end in YYMMNNN."""
    match = re.match(r"(\d{2})(\d{2})\.\d{4,5}$", arxiv_id or "")
    if not match:
        match = re.search(r"/(\d{2})(\d{2})\d{3}$", arxiv_id or "")
        if not match:
            return None
        yy = int(match.group(1))
        return (1900 + yy) if yy >= 91 else (2000 + yy)
    return 2000 + int(match.group(1))


# ── text normalization ──────────────────────────────────────────────────

def clean_text(text: str) -> str:
    """Collapse PDF line wrapping: rejoin hyphenated breaks, flatten whitespace."""
    text = (text or "").replace("­", "")
    text = re.sub(r"(\w)-\s*\n\s*([a-z])", r"\1\2", text)
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("ﬀ", "ff")
    return re.sub(r"\s+", " ", text).strip()


def _lines(text: str) -> List[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


# ── title ───────────────────────────────────────────────────────────────

_BOILERPLATE_RE = re.compile(
    r"(arxiv|preprint|proceedings|journal|conference|workshop|vol\.|volume|issn|isbn|doi|"
    r"copyright|©|licen[cs]e|published|accepted|received|submitted|under review|"
    r"https?://|www\.|@|page \d|^\d+$|university|department|institute|school of|"
    r"correspondence|equal contribution|technical report|draft|manuscript)",
    re.IGNORECASE,
)


def _looks_like_title(line: str) -> bool:
    if not 12 <= len(line) <= 250:
        return False
    if _BOILERPLATE_RE.search(line):
        return False
    letters = sum(c.isalpha() for c in line)
    if letters < 0.6 * len(line.replace(" ", "")):
        return False
    words = line.split()
    if len(words) < 2:
        return False
    if line.lower().startswith(("abstract", "introduction", "keywords")):
        return False
    # Titles do not end in a full stop; sentences (body text, notes) do.
    if line.endswith(".") and not re.search(r"\b[A-Z][a-z]?\.$", line):
        return False
    return True


_NAME_PARTICLES = {"van", "von", "de", "der", "da", "di", "le", "la", "del", "den", "du", "dos", "ter"}


def _looks_like_authors(line: str) -> bool:
    """Names, separators and affiliation marks; few lowercase words."""
    stripped = re.sub(r"[\d∗*†‡§¶,&]|\band\b", " ", line)
    words = [w for w in stripped.split() if w and w.lower() not in _NAME_PARTICLES]
    if not words or len(words) > 40:
        return False
    capitalized = sum(1 for w in words if w[:1].isupper())
    # Affiliation marks glued to a name ("Smith1", "Brown*") are a strong tell.
    marked = bool(re.search(r"[A-Za-z][\d∗*†‡§¶]", line))
    return capitalized >= 0.8 * len(words) and ("," in line or " and " in line or marked or len(words) <= 4)


def title_from_text(first_page: str) -> Optional[Tuple[str, str]]:
    """The first title-like run of lines on page one: ``(title, rule)``."""
    lines = _lines(first_page)[:30]
    for i, line in enumerate(lines):
        if not _looks_like_title(line) or _looks_like_authors(line):
            continue
        parts = [line]
        # Long titles wrap onto a second (rarely third) line.
        # A continuation follows a line that was wrapped (long, no closing
        # period) or starts lowercase; an author line ends the title.
        for nxt in lines[i + 1:i + 3]:
            wrapped = len(parts[-1]) > 35 and not parts[-1].endswith(".")
            if (_looks_like_title(nxt) and not _looks_like_authors(nxt)
                    and not nxt.endswith(".")
                    and len(" ".join(parts + [nxt])) <= 250
                    and (wrapped or nxt[:1].islower())):
                parts.append(nxt)
            else:
                break
        return clean_text(" ".join(parts)), "first title-like lines of page 1"
    return None


def title_from_pdf(path: Path, first_page: str) -> Optional[Tuple[str, str]]:
    """The largest-font lines at the top of page one, or the PDF's Title
    field — either accepted only when page one actually contains it."""
    if path is None or path.suffix.lower() != ".pdf" or not path.exists():
        return None
    page_norm = _norm(first_page)
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            if not pdf.pages:
                return None
            page = pdf.pages[0]
            words = page.extract_words(extra_attrs=["size"], keep_blank_chars=False)
            top_half = [w for w in words if w["top"] < page.height * 0.5]
            if top_half:
                sizes = [round(w["size"], 1) for w in top_half if any(c.isalpha() for c in w["text"])]
                if sizes:
                    biggest = max(sizes)
                    body = Counter(round(w["size"], 1) for w in words).most_common(1)[0][0]
                    if biggest >= body + 1.5:
                        title_words = [w for w in top_half if abs(round(w["size"], 1) - biggest) <= 0.6]
                        title_words.sort(key=lambda w: (round(w["top"]), w["x0"]))
                        candidate = clean_text(" ".join(w["text"] for w in title_words))
                        if 12 <= len(candidate) <= 300 and _norm(candidate) in page_norm:
                            return candidate, "largest type on page 1"
    except Exception:  # noqa: BLE001 - a layout read is a bonus, never required
        pass
    try:
        from pypdf import PdfReader
        meta = PdfReader(str(path)).metadata
        raw = clean_text(str(meta.title)) if meta and meta.title else ""
        if 12 <= len(raw) <= 300 and _norm(raw) in page_norm:
            return raw, "PDF title field, confirmed on page 1"
    except Exception:  # noqa: BLE001
        pass
    return None


# ── authors ─────────────────────────────────────────────────────────────

_NAME_RE = re.compile(
    r"^(?:[A-Z][a-zA-Z'\-]*\.?\s+){1,3}(?:(?:van|von|de|der|da|di|le|la|del|den|du|dos|ter)\s+)*"
    r"[A-Z][a-zA-Z'\-]+$"
)


def authors_from_text(first_page: str, title: Optional[str]) -> List[str]:
    """Names on the author line(s) between the title and the abstract."""
    lines = _lines(first_page)[:40]
    start = 0
    if title:
        title_norm = _norm(title)
        for i, line in enumerate(lines):
            if _norm(line) and _norm(line) in title_norm:
                start = i + 1
    names: List[str] = []
    for line in lines[start:start + 8]:
        if line.lower().startswith(("abstract", "introduction")):
            break
        if not _looks_like_authors(line):
            if names:
                break
            continue
        for part in re.split(r",|\band\b|&|;", re.sub(r"[\d∗*†‡§¶]", "", line)):
            name = " ".join(part.split())
            if _NAME_RE.match(name) and 1 < len(name.split()) <= 5 and name not in names:
                names.append(name)
    return names[:30]


# ── year ────────────────────────────────────────────────────────────────

def year_from_text(first_page: str, arxiv_id: Optional[str]) -> Optional[Tuple[int, str]]:
    if arxiv_id:
        year = arxiv_year(arxiv_id)
        if year:
            return year, "arXiv identifier"
    this_year = datetime.now().year
    years = [int(y) for y in _YEAR_RE.findall(first_page or "") if int(y) <= this_year + 1]
    if not years:
        return None
    year, count = Counter(years).most_common(1)[0]
    return year, f"most frequent year on page 1 ({count}x)"


# ── abstract ────────────────────────────────────────────────────────────

_ABSTRACT_HEAD_RE = re.compile(r"(?:^|\n)\s*(?:a\s*b\s*s\s*t\s*r\s*a\s*c\s*t|summary)\s*[:.—–\-]?\s*", re.IGNORECASE)
_ABSTRACT_END_RE = re.compile(
    r"\n\s*(?:(?:1|I)\.?\s+)?(?:introduction|background|keywords|key\s*words|index terms|"
    r"ccs concepts|acm reference format|significance statement|main)\b",
    re.IGNORECASE,
)


def abstract_from_text(pages: Dict[int, str]) -> Optional[Tuple[str, int]]:
    """The text under an "Abstract" heading on page 1 or 2: ``(text, page)``."""
    for page_number in sorted(pages)[:2]:
        text = pages[page_number] or ""
        head = _ABSTRACT_HEAD_RE.search(text)
        if not head:
            continue
        rest = text[head.end():]
        end = _ABSTRACT_END_RE.search(rest)
        body = rest[:end.start()] if end else rest[:MAX_ABSTRACT_CHARS]
        body = clean_text(body)
        if len(body) >= 80:
            return body[:MAX_ABSTRACT_CHARS], page_number
    return None


# ── references ──────────────────────────────────────────────────────────

_REF_HEAD_RE = re.compile(
    r"(?:^|\n)\s*(?:\d+\.?\s*|[IVX]+\.\s*)?(references|bibliography|literature cited|works cited|"
    r"references and notes|cited literature)\s*\n",
    re.IGNORECASE,
)
_REF_END_RE = re.compile(
    r"\n\s*(?:[A-Z]\.?\s+|\d+\.?\s+)?(appendix|appendices|supplementary (?:material|information)|"
    r"acknowledg(?:e)?ments|author contributions|checklist)\b[^\n]{0,60}\n",
    re.IGNORECASE,
)
_BRACKET_ENTRY_RE = re.compile(r"(?:^|\n)\s*\[(\d{1,3})\]\s*")
_NUMBER_ENTRY_RE = re.compile(r"(?:^|\n)\s*(\d{1,3})\.\s+(?=[A-ZÀ-ɏ])")
_AUTHOR_YEAR_START_RE = re.compile(
    r"^(?:[A-ZÀ-ɏ][\w'\-À-ɏ]+(?:\s[A-ZÀ-ɏ][\w'\-]+)?,\s+(?:[A-Z]\.|[A-Z][a-z]+)"
    r"|[A-ZÀ-ɏ][\w'\-]+\s+[A-Z]{1,3}[,.]\s)"
)


def _reference_section(pages: Dict[int, str]) -> List[Tuple[int, str]]:
    """``[(page, text)]`` from the last References heading to the end
    (or to an appendix that follows it)."""
    ordered = sorted(pages)
    start: Optional[Tuple[int, int]] = None
    for page_number in ordered:
        for match in _REF_HEAD_RE.finditer("\n" + (pages[page_number] or "")):
            start = (page_number, max(0, match.end() - 1))
    if start is None:
        return []
    section = []
    for page_number in ordered:
        if page_number < start[0]:
            continue
        text = "\n" + (pages[page_number] or "")
        if page_number == start[0]:
            text = "\n" + text[start[1]:]
        end = _REF_END_RE.search(text)
        if end:
            section.append((page_number, text[:end.start()]))
            break
        section.append((page_number, text))
    return section


def _split_entries(section: List[Tuple[int, str]]) -> List[Tuple[int, str]]:
    joined = ""
    page_marks: List[Tuple[int, int]] = []
    for page_number, text in section:
        page_marks.append((len(joined), page_number))
        joined += text + "\n"

    def page_at(offset: int) -> int:
        page = section[0][0]
        for mark, number in page_marks:
            if mark <= offset:
                page = number
        return page

    # "[1]" is unambiguous from the first entry; "1." needs a few in a row
    # before it is trusted over a heading or a page number.
    for regex, minimum in ((_BRACKET_ENTRY_RE, 1), (_NUMBER_ENTRY_RE, 3)):
        starts = [m for m in regex.finditer(joined)]
        # A numbered style must count up from (about) 1 to be trusted —
        # otherwise "2. Methods" headings or page numbers would split entries.
        numbers = [int(m.group(1)) for m in starts]
        if len(starts) >= minimum and numbers[0] <= 2 and sum(
                1 for a, b in zip(numbers, numbers[1:]) if b == a + 1) >= 0.7 * (len(numbers) - 1):
            entries = []
            for i, m in enumerate(starts):
                end = starts[i + 1].start() if i + 1 < len(starts) else len(joined)
                entries.append((page_at(m.start()), joined[m.end():end]))
            return entries

    # Author-year style: a new entry starts on a line that opens with
    # "Surname, X." (or "Surname XY,") right after a line that ended one.
    entries: List[Tuple[int, str]] = []
    current: List[str] = []
    current_page = section[0][0]
    offset = 0
    previous_ended = True
    for line in joined.split("\n"):
        stripped = line.strip()
        line_offset = offset
        offset += len(line) + 1
        if not stripped:
            continue
        opens_entry = _AUTHOR_YEAR_START_RE.match(stripped) and (
            previous_ended or _PAREN_YEAR_RE.search(stripped[:160]))
        if opens_entry and current:
            entries.append((current_page, "\n".join(current)))
            current = []
        if not current:
            current_page = page_at(line_offset)
        current.append(stripped)
        previous_ended = (stripped.endswith((".", ")"))
                          or bool(re.search(r"(19|20)\d\d[a-z]?\.?$", stripped))
                          or bool(re.search(r"(?:https?://|doi:)\S+$", stripped, re.IGNORECASE)))
    if current:
        entries.append((current_page, "\n".join(current)))
    return entries


_PAREN_YEAR_RE = re.compile(r"\((19|20)\d\d[a-z]?\)")


def _reference_year(text: str) -> Optional[int]:
    this_year = datetime.now().year
    paren = _PAREN_YEAR_RE.search(text)
    if paren and int(paren.group(0)[1:5]) <= this_year + 1:
        return int(paren.group(0)[1:5])
    years = [int(y) for y in _YEAR_RE.findall(text) if int(y) <= this_year + 1]
    return years[-1] if years else None


def title_guess(raw: str) -> str:
    """A search phrase for a registry title lookup — never stored as the
    reference's title. A quoted title wins; otherwise the longest
    period-delimited segment after the author list."""
    quoted = re.search(r"[“\"]([^”\"]{15,300})[”\"]", raw)
    if quoted:
        return quoted.group(1).strip(" ,.")
    text = re.sub(r"\(?(19|20)\d\d[a-z]?\)?[.,]?", " ", raw)
    text = _URL_RE.sub(" ", text)
    segments = [s.strip() for s in re.split(r"\.\s+", text) if s.strip()]
    if len(segments) <= 1:
        return segments[0][:200] if segments else ""
    candidates = [s for s in segments[1:] if len(s.split()) >= 3 and not _looks_like_authors(s)]
    return max(candidates, key=len)[:300] if candidates else segments[1][:300]


def references_from_text(pages: Dict[int, str]) -> List[dict]:
    """Reference-list entries with any identifier each one prints."""
    section = _reference_section(pages)
    if not section:
        return []
    out: List[dict] = []
    seen = set()
    for page_number, raw in _split_entries(section):
        text = clean_text(raw)
        if len(text) < 20 or not re.search(r"[A-Za-z]{3}", text):
            continue
        text = text[:MAX_REFERENCE_CHARS]
        key = _norm(text)
        if key in seen:
            continue
        seen.add(key)
        dois = find_dois(raw)
        arxiv = find_arxiv_ids(raw)
        urls = [u for u in find_urls(raw) if "doi.org/" not in u.lower()]
        out.append({
            "kind": "reference",
            "ordinal": len(out) + 1,
            "raw_text": text,
            "page_number": page_number,
            "doi": dois[0] if dois else None,
            "arxiv_id": arxiv[0] if arxiv else None,
            "url": urls[0] if urls else None,
            "year": _reference_year(text),
        })
        if len(out) >= MAX_REFERENCES:
            break
    return out


def links_from_text(pages: Dict[int, str], exclude: Optional[set] = None) -> List[dict]:
    """URLs written in the body of the paper (the reference list's own
    URLs belong to their entries and are left out)."""
    section_pages = {p for p, _ in _reference_section(pages)}
    exclude = {u.lower() for u in (exclude or set())}
    out, seen = [], set()
    for page_number in sorted(pages):
        text = pages[page_number] or ""
        if page_number in section_pages:
            head = None
            for match in _REF_HEAD_RE.finditer("\n" + text):
                head = match
            text = text[:head.start()] if head else ""
        for url in find_urls(text):
            low = url.lower()
            if low in seen or low in exclude:
                continue
            seen.add(low)
            out.append({"kind": "link", "ordinal": len(out) + 1, "raw_text": url,
                        "page_number": page_number, "url": url,
                        "doi": normalize_doi(url) if "doi.org/" in low else None,
                        "arxiv_id": (find_arxiv_ids(url) or [None])[0]})
            if len(out) >= MAX_LINKS:
                return out
    return out


# ── the whole record ────────────────────────────────────────────────────

def _norm(text: str) -> str:
    """Comparison key: lowercase letters and digits only."""
    return re.sub(r"[^a-z0-9]+", "", clean_text(text).lower())


def extract_paper(pages: Dict[int, str], path: Optional[Path] = None) -> dict:
    """Bibliographic record + references + links, each field with provenance."""
    pages = {int(k): v or "" for k, v in (pages or {}).items()}
    ordered = sorted(pages)
    first = pages[ordered[0]] if ordered else ""
    first_number = ordered[0] if ordered else 1
    provenance: Dict[str, dict] = {}
    notes: List[str] = []

    found_title = title_from_pdf(path, first) if path else None
    found_title = found_title or title_from_text(first)
    title = found_title[0] if found_title else None
    if found_title:
        provenance["title"] = {"source": "paper", "page": first_number, "detail": found_title[1]}
    else:
        notes.append("No title could be read from page 1.")

    # The paper's own identifiers are the ones printed on page one (header,
    # footer, arXiv stamp). Later pages carry the identifiers of cited works.
    dois = find_dois(first)
    doi = dois[0] if dois else None
    if doi:
        provenance["doi"] = {"source": "paper", "page": first_number, "detail": "printed on page 1"}
    arxiv = find_arxiv_ids(first)
    arxiv_id = arxiv[0] if arxiv else None
    if arxiv_id:
        provenance["arxiv_id"] = {"source": "paper", "page": first_number, "detail": "printed on page 1"}

    authors = authors_from_text(first, title)
    if authors:
        provenance["authors"] = {"source": "paper", "page": first_number,
                                 "detail": "author line under the title (heuristic)"}

    found_year = year_from_text(first, arxiv_id)
    year = found_year[0] if found_year else None
    if found_year:
        provenance["year"] = {"source": "paper", "page": first_number, "detail": found_year[1]}

    found_abstract = abstract_from_text(pages)
    abstract = found_abstract[0] if found_abstract else None
    if found_abstract:
        provenance["abstract"] = {"source": "paper", "page": found_abstract[1],
                                  "detail": "text under the Abstract heading"}
    else:
        notes.append("No section headed 'Abstract' was found on the first two pages.")

    references = references_from_text(pages)
    if not references:
        notes.append("No reference list was found (no 'References' or 'Bibliography' heading).")
    ref_urls = {r["url"] for r in references if r.get("url")}
    links = links_from_text(pages, exclude=ref_urls)

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "venue": None,
        "abstract": abstract,
        "provenance": provenance,
        "notes": notes,
        "references": references,
        "links": links,
    }

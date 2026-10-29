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
MAX_OPENING_CHARS = 1500
MAX_REFERENCE_CHARS = 1200
MAX_REFERENCES = 1500
MAX_LINKS = 200
# Front matter: a book's title page follows a cover and a half-title, its
# copyright page and ISBN follow that; a paper has all of it on page one.
FRONT_PAGES = 8
# Where an opening passage (preface, introduction) may start.
OPENING_PAGES = 30
# A document this long, or one that prints an ISBN, is treated as a book.
BOOK_MIN_PAGES = 80
# A reference section in a book closes at the next chapter; this caps one
# that never does (a bibliography running to the end is fine, a misread
# heading swallowing half the book is not).
MAX_SECTION_PAGES = 60

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


_ISBN_RE = re.compile(r"ISBN(?:[-‐ ]?1[03])?[:\s]*((?:97[89][-‐\s]?)?(?:\d[-‐\s]?){9}[\dXx])", re.IGNORECASE)


def _isbn_valid(digits: str) -> bool:
    if len(digits) == 13 and digits.isdigit():
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits[:12]))
        return (10 - total % 10) % 10 == int(digits[12])
    if len(digits) == 10 and digits[:9].isdigit() and (digits[9].isdigit() or digits[9] in "Xx"):
        total = sum((10 - i) * int(d) for i, d in enumerate(digits[:9]))
        check = 10 if digits[9] in "Xx" else int(digits[9])
        return (total + check) % 11 == 0
    return False


def find_isbns(text: str) -> List[str]:
    """ISBNs printed after an "ISBN" label, checksum-verified, hyphens dropped."""
    out = []
    for match in _ISBN_RE.finditer(text or ""):
        digits = re.sub(r"[^0-9Xx]", "", match.group(1)).upper()
        if _isbn_valid(digits) and digits not in out:
            out.append(digits)
    return out


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


_TITLE_WORDS = {"The", "the", "A", "An", "an", "Of", "of", "On", "on", "In", "in", "For", "for", "To", "to",
                "With", "with", "From", "from", "At", "at", "Is", "is", "How", "Why", "What", "When", "Toward",
                "Towards", "Into", "into", "Beyond", "Under", "under", "Between", "between"}
_NAME_PARTICLES = {"van", "von", "de", "der", "da", "di", "le", "la", "del", "den", "du", "dos", "ter"}


def _looks_like_authors(line: str) -> bool:
    """Names, separators and affiliation marks; few lowercase words."""
    stripped = re.sub(r"[\d∗*†‡§¶,&]|\band\b", " ", line)
    words = [w for w in stripped.split() if w and w.lower() not in _NAME_PARTICLES]
    if not words or len(words) > 40:
        return False
    # "The Quiet Forest" is capitalized like a name list; articles and
    # prepositions never appear in names.
    if any(w in _TITLE_WORDS for w in words):
        return False
    capitalized = sum(1 for w in words if w[:1].isupper())
    # Affiliation marks glued to a name ("Smith1", "Brown*") are a strong tell.
    marked = bool(re.search(r"[A-Za-z][\d∗*†‡§¶]", line))
    return capitalized >= 0.8 * len(words) and ("," in line or " and " in line or marked or len(words) <= 4)


def title_from_text(first_page: str, page_number: int = 1) -> Optional[Tuple[str, str]]:
    """The first title-like run of lines on a page: ``(title, rule)``."""
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
                    and not nxt.endswith(".") and not nxt.lower().startswith("by ")
                    and len(" ".join(parts + [nxt])) <= 250
                    and (wrapped or nxt[:1].islower())):
                parts.append(nxt)
            else:
                break
        return clean_text(" ".join(parts)), f"first title-like lines of page {page_number}"
    return None


def title_from_pdf(path: Path, pages: Dict[int, str]) -> Optional[Tuple[str, str, int]]:
    """The largest type in the top half of the first front-matter page that
    has any, or the PDF's Title field — accepted only when that page's text
    contains it. Returns ``(title, rule, page)``."""
    if path is None or path.suffix.lower() != ".pdf" or not path.exists():
        return None
    front = [p for p in sorted(pages)[:FRONT_PAGES] if (pages[p] or "").strip()]
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            for number in front:
                if number - 1 >= len(pdf.pages):
                    break
                page = pdf.pages[number - 1]
                words = page.extract_words(extra_attrs=["size"], keep_blank_chars=False)
                top_half = [w for w in words if w["top"] < page.height * 0.5]
                sizes = [round(w["size"], 1) for w in top_half if any(c.isalpha() for c in w["text"])]
                if not sizes:
                    continue
                biggest = max(sizes)
                body = Counter(round(w["size"], 1) for w in words).most_common(1)[0][0]
                # A title page may hold nothing but the title, so "bigger
                # than the body text" is measured against the page only
                # when the page has body text to measure.
                if biggest < body + 1.5 and len(words) > 40:
                    continue
                title_words = [w for w in top_half if abs(round(w["size"], 1) - biggest) <= 0.6]
                title_words.sort(key=lambda w: (round(w["top"]), w["x0"]))
                candidate = clean_text(" ".join(w["text"] for w in title_words))
                if 12 <= len(candidate) <= 300 and _norm(candidate) in _norm(pages[number]) \
                        and _looks_like_title(candidate):
                    return candidate, f"largest type on page {number}", number
                break
    except Exception:  # noqa: BLE001 - a layout read is a bonus, never required
        pass
    try:
        from pypdf import PdfReader
        meta = PdfReader(str(path)).metadata
        raw = clean_text(str(meta.title)) if meta and meta.title else ""
        if 12 <= len(raw) <= 300:
            for number in front:
                if _norm(raw) in _norm(pages[number]):
                    return raw, f"PDF title field, confirmed on page {number}", number
    except Exception:  # noqa: BLE001
        pass
    return None


# ── authors ─────────────────────────────────────────────────────────────

_NAME_RE = re.compile(
    r"^(?:[A-Z][a-zA-Z'\-]*\.?\s+){1,3}(?:(?:van|von|de|der|da|di|le|la|del|den|du|dos|ter)\s+)*"
    r"[A-Z][a-zA-Z'\-]+$"
)


_PUBLISHER_RE = re.compile(
    r"\b(?:press|publish\w*|books|editions?|verlag|inc|ltd|llc|group|house|media|university|"
    r"college|institute|society|library|limited|company|co)\b\.?", re.IGNORECASE)


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
        # Books: "by Ada Lovelace", "Edited by …".
        line = re.sub(r"^(?:(?:edited|written|translated)\s+)?by\s+", "", line, flags=re.IGNORECASE)
        if not _looks_like_authors(line):
            if names:
                break
            continue
        for part in re.split(r",|\band\b|&|;", re.sub(r"[\d∗*†‡§¶]", "", line)):
            name = " ".join(part.split())
            if (_NAME_RE.match(name) and 1 < len(name.split()) <= 5 and name not in names
                    and not _PUBLISHER_RE.search(name)):
                names.append(name)
    return names[:30]


# ── year ────────────────────────────────────────────────────────────────

_COPYRIGHT_RE = re.compile(
    r"(?:©|\(c\)|copyright|first published|published)(?:\s*(?:©|\(c\)|in|by))*[^\n]{0,40}?"
    r"((?:19|20)\d\d(?:\s*[,–-]\s*(?:19|20)\d\d)*)",
    re.IGNORECASE,
)


def copyright_year(pages: Dict[int, str]) -> Optional[Tuple[int, int]]:
    """The latest year on a copyright / "first published" line in the front
    matter — the edition in hand: ``(year, page)``."""
    this_year = datetime.now().year
    for number in sorted(pages)[:FRONT_PAGES]:
        years = []
        for match in _COPYRIGHT_RE.finditer(pages[number] or ""):
            years.extend(int(y) for y in _YEAR_RE.findall(match.group(1)) if int(y) <= this_year + 1)
        if years:
            return max(years), number
    return None


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


_OPENING_HEAD_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:(?:\d+|[IVX]+|chapter\s+(?:1|one|i))\.?[ \t]+)?"
    r"(preface|foreword|introduction|prologue|overview)[ \t]*\n",
    re.IGNORECASE,
)


def _is_contents_page(text: str) -> bool:
    lines = _lines(text)
    if len(lines) < 5:
        return False
    numbered = sum(1 for line in lines if re.search(r"(?:\.{2,}|\s)\d{1,4}$", line))
    return numbered >= 0.4 * len(lines) or bool(re.match(r"\s*(?:table of\s+)?contents\b", text, re.IGNORECASE))


def opening_from_text(pages: Dict[int, str]) -> Optional[Tuple[str, int, str]]:
    """When there is no abstract: the first paragraphs under a Preface,
    Foreword or Introduction heading — quoted, never summarized, and
    labelled as an opening rather than an abstract: ``(text, page, heading)``."""
    for number in sorted(pages)[:OPENING_PAGES]:
        text = pages[number] or ""
        if _is_contents_page(text):
            continue
        head = _OPENING_HEAD_RE.search(text)
        if not head:
            continue
        body = text[head.end():]
        # Stop at the next page's own heading: an opening that runs on into
        # Chapter 1 is no longer the preface it is labelled as.
        end = re.search(r"\n[ \t]*(?:chapter|part)\b", body, re.IGNORECASE)
        if end:
            body = body[:end.start()]
        nxt = sorted(p for p in pages if p > number)[:2]
        for p in nxt:
            following = pages[p] or ""
            if (len(body) >= MAX_OPENING_CHARS or end
                    or _SECTION_END_RE.match("\n" + following.lstrip())
                    or _OPENING_HEAD_RE.match("\n" + following.lstrip())
                    or _REF_HEAD_RE.match("\n" + following.lstrip())):
                break
            body += "\n" + following
        body = clean_text(body)
        if len(body) < 200:
            continue
        cut = body[:MAX_OPENING_CHARS]
        sentence_end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        if sentence_end > 300:
            cut = cut[:sentence_end + 1]
        return cut, number, head.group(1).capitalize()
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


# A book's reference list for one chapter ends where the next chapter, part
# or the index begins.
_SECTION_END_RE = re.compile(
    r"\n[ \t]*(?:(?:chapter|part)[ \t]+(?:\d+|[ivxlc]+|one|two|three|four|five|six|seven|eight|nine|ten)\b"
    r"|index[ \t]*\n)",
    re.IGNORECASE,
)


def _reference_sections(pages: Dict[int, str]) -> List[List[Tuple[int, str]]]:
    """Every reference list: ``[[(page, text), …], …]``.

    A paper has one, at the end. A book may have one per chapter plus a
    bibliography, and prints "References" as a running head on every page
    of each — a heading met inside an open section continues it rather
    than starting another. A section closes at an appendix,
    acknowledgements, the next chapter or part, the index, or after
    MAX_SECTION_PAGES pages."""
    sections: List[List[Tuple[int, str]]] = []
    current: Optional[List[Tuple[int, str]]] = None
    for number in sorted(pages):
        text = "\n" + (pages[number] or "")
        pos = 0
        while True:
            if current is None:
                if _is_contents_page(text):
                    break
                head = _REF_HEAD_RE.search(text, pos)
                if not head:
                    break
                current = []
                pos = max(0, head.end() - 1)
            else:
                running = _REF_HEAD_RE.match(text, pos)
                if running and pos == 0:
                    pos = max(0, running.end() - 1)
            ends = [m for m in (_REF_END_RE.search(text, pos), _SECTION_END_RE.search(text, pos)) if m]
            end = min(ends, key=lambda m: m.start()) if ends else None
            if end:
                if text[pos:end.start()].strip():
                    current.append((number, text[pos:end.start()]))
                sections.append(current)
                current = None
                pos = end.start() + 1
                continue
            current.append((number, text[pos:]))
            if len(current) >= MAX_SECTION_PAGES:
                sections.append(current)
                current = None
            break
    if current:
        sections.append(current)
    return [s for s in sections if any(t.strip() for _, t in s)]


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
                entries.append((page_at(m.end() - 1), joined[m.end():end]))
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
    """Reference-list entries with any identifier each one prints, across
    every reference section (numbering restarts per chapter in many books,
    so each section is split on its own)."""
    entries = [entry for section in _reference_sections(pages) for entry in _split_entries(section)]
    out: List[dict] = []
    seen = set()
    for page_number, raw in entries:
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
    in_sections: Dict[int, List[str]] = {}
    for section in _reference_sections(pages):
        for number, chunk in section:
            in_sections.setdefault(number, []).append(chunk)
    exclude = {u.lower() for u in (exclude or set())}
    out, seen = [], set()
    for page_number in sorted(pages):
        text = pages[page_number] or ""
        for chunk in in_sections.get(page_number, []):
            if chunk.strip():
                text = text.replace(chunk.lstrip("\n"), " ")
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


_MD_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,2}[ \t]+(.+?)[ \t#]*$", re.MULTILINE)


def title_from_markdown(text: str) -> Optional[str]:
    """A Markdown document's first top-level heading, without emphasis marks."""
    match = _MD_HEADING_RE.search(text or "")
    if not match:
        return None
    title = re.sub(r"[*_`]+", "", match.group(1)).strip()
    return title if 2 <= len(title) <= 250 else None


def extract_paper(pages: Dict[int, str], path: Optional[Path] = None, fmt: Optional[str] = None) -> dict:
    """Bibliographic record + references + links, each field with provenance.

    Works for articles (everything on page one) and books (cover, title
    page, copyright page, per-chapter bibliographies); ``kind`` says which
    the document looked like."""
    pages = {int(k): v or "" for k, v in (pages or {}).items()}
    ordered = sorted(pages)
    front_numbers = [p for p in ordered[:FRONT_PAGES] if pages[p].strip()]
    first_number = front_numbers[0] if front_numbers else (ordered[0] if ordered else 1)
    first = pages.get(first_number, "")
    front_text = "\n".join(pages[p] for p in front_numbers)
    provenance: Dict[str, dict] = {}
    notes: List[str] = []

    isbns = find_isbns(front_text) or (find_isbns(pages[ordered[-1]]) if ordered else [])
    isbn = isbns[0] if isbns else None
    # A book's own identifiers sit on its copyright page; the front pages
    # read here may already reach a first chapter and its citations.
    imprint_page = next((p for p in front_numbers
                         if find_isbns(pages[p]) or _COPYRIGHT_RE.search(pages[p])), None)
    is_book = bool(isbn) or len(ordered) >= BOOK_MIN_PAGES
    if isbn:
        provenance["isbn"] = {"source": "paper", "detail": "printed in the front matter"}

    found = title_from_pdf(path, pages) if path else None
    title_page = found[2] if found else None
    if not found and (fmt or "").lower() in ("md", "markdown"):
        md_title = title_from_markdown(first)
        if md_title:
            found, title_page = (md_title, "first Markdown heading", first_number), first_number
    if not found and (fmt or "").lower() not in ("md", "markdown"):
        for number in front_numbers if is_book else front_numbers[:1]:
            if _is_contents_page(pages[number]):
                continue
            text_title = title_from_text(pages[number], number)
            if text_title:
                found, title_page = (*text_title, number), number
                break
    title = found[0] if found else None
    if found:
        provenance["title"] = {"source": "paper", "page": title_page, "detail": found[1]}
    else:
        notes.append("No title could be read from the first pages.")
    title_page = title_page or first_number

    # A paper's own identifiers are printed on page one (header, footer,
    # arXiv stamp); later pages carry the identifiers of cited works.
    if is_book:
        dois = find_dois(pages[imprint_page]) if imprint_page else []
        doi_page = imprint_page
    else:
        dois, doi_page = find_dois(first), first_number
    doi = dois[0] if dois else None
    if doi:
        provenance["doi"] = {"source": "paper", "page": doi_page,
                             "detail": "printed on the copyright page" if is_book else "printed on page 1"}
    arxiv = find_arxiv_ids(first)
    arxiv_id = arxiv[0] if arxiv else None
    if arxiv_id:
        provenance["arxiv_id"] = {"source": "paper", "page": first_number, "detail": "printed on page 1"}

    after_title = [p for p in front_numbers if p >= title_page][:2]
    authors = authors_from_text("\n".join(pages[p] for p in after_title), title)
    if authors:
        provenance["authors"] = {"source": "paper", "page": title_page,
                                 "detail": "author line under the title (heuristic)"}

    found_year, year_page = None, first_number
    if arxiv_id:
        found_year = year_from_text(first, arxiv_id)
    if not found_year:
        copy = copyright_year(pages)
        if copy:
            found_year, year_page = (copy[0], f"copyright line on page {copy[1]}"), copy[1]
    if not found_year and not is_book:
        found_year = year_from_text(first, None)
    year = found_year[0] if found_year else None
    if found_year:
        provenance["year"] = {"source": "paper", "page": year_page, "detail": found_year[1]}

    found_abstract = abstract_from_text(pages)
    abstract = found_abstract[0] if found_abstract else None
    if found_abstract:
        provenance["abstract"] = {"source": "paper", "page": found_abstract[1], "kind": "abstract",
                                  "detail": "text under the Abstract heading"}
    else:
        opening = opening_from_text(pages)
        if opening:
            abstract = opening[0]
            provenance["abstract"] = {"source": "paper", "page": opening[1], "kind": "opening",
                                      "detail": f"opening of the {opening[2]}, quoted (there is no abstract)"}
        else:
            notes.append("No abstract, preface or introduction was found in the first pages.")

    references = references_from_text(pages)
    if not references:
        notes.append("No reference list was found (no 'References' or 'Bibliography' heading).")
    ref_urls = {r["url"] for r in references if r.get("url")}
    links = links_from_text(pages, exclude=ref_urls)

    return {
        "kind": "book" if is_book else "article",
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "isbn": isbn,
        "venue": None,
        "abstract": abstract,
        "provenance": provenance,
        "notes": notes,
        "references": references,
        "links": links,
    }

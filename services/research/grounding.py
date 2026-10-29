"""Checking model output against the paper it claims to describe.

A model asked about a paper can be wrong in two ways that look identical on
the page: it can paraphrase the paper loosely, or it can state something the
paper never says. The defence here is mechanical. Every statement a model
returns must carry a verbatim quote and the id of the passage it quoted;
``verify_quote`` looks for that quote in that passage (and, failing that, in
the rest of the paper) after normalizing the differences PDF extraction
introduces — case, whitespace, line-break hyphenation, ligatures, curly
quotes. A statement whose quote is not found is dropped, and the drop is
counted so the reader sees how much was removed.

``similarity`` is the same idea for registry titles: a title a registry
returns for a search is accepted only if it is (nearly) printed in the text
it was searched from.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Tuple

# A quote must be long enough to mean something: "the model" appears in
# every paper. Shorter quotes are rejected rather than trivially verified.
MIN_QUOTE_CHARS = 20
# Fuzzy acceptance for OCR noise and the odd dropped character; a quote
# that differs more than this is a paraphrase, which is not a quote.
QUOTE_MATCH_THRESHOLD = 0.92

_QUOTE_CHARS = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
    "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
})


def normalize(text: str) -> str:
    """Comparison form: lowercase, unified punctuation, no hyphenated line
    breaks, single spaces, and no ellipses (a quote may elide)."""
    text = (text or "").translate(_QUOTE_CHARS).replace("­", "")
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    text = text.lower()
    text = re.sub(r"[^\w%.,;:()\-+=<>/'\" ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize(text))


def similarity(needle: str, haystack: str) -> float:
    """How completely ``needle`` appears, in order, in one region of
    ``haystack`` (0..1). 1.0 means it is there verbatim after normalization."""
    a, b = _compact(needle), _compact(haystack)
    if not a or not b:
        return 0.0
    if a in b:
        return 1.0
    matcher = SequenceMatcher(None, b, a, autojunk=False)
    block = matcher.find_longest_match(0, len(b), 0, len(a))
    if block.size == 0:
        return 0.0
    # Score only a window around the best anchor so matches scattered over a
    # long text cannot add up to a false "found".
    start = max(0, block.a - block.b - 10)
    window = b[start:start + len(a) + 20]
    window_matcher = SequenceMatcher(None, window, a, autojunk=False)
    blocks = [m for m in window_matcher.get_matching_blocks() if m.size]
    if not blocks:
        return 0.0
    matched = sum(m.size for m in blocks)
    # Characters the haystack has inside the matched region but the needle
    # lacks count against it too: "did recover" is not a quote of "did not
    # recover", though every character of it is present.
    span = (blocks[-1].a + blocks[-1].size) - blocks[0].a
    return matched / max(len(a), span)


_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_NEGATIONS = {"not", "no", "never", "without", "neither", "nor", "cannot", "unlike", "none", "fail", "failed"}


def _numbers(text: str) -> List[str]:
    return [n.replace(",", "") for n in _NUMBER_RE.findall(normalize(text))]


def _negations(text: str) -> set:
    words = set(re.findall(r"[a-z']+", normalize(text)))
    found = words & _NEGATIONS
    if any(w.endswith("n't") for w in words):
        found.add("n't")
    return found


def faithful(quote: str, text: str) -> bool:
    """The fuzzy score forgives OCR noise; it must not forgive a changed
    number ("23%" quoted as "32%" or "2.3%") or a dropped/added negation,
    which change what the paper says. Both are checked exactly."""
    numbers = _numbers(text)
    for number in _numbers(quote):
        if number not in numbers:
            return False
    passage_negations = _negations(text)
    return _negations(quote) <= passage_negations


def _quote_parts(quote: str) -> List[str]:
    """A quote with an ellipsis is checked as its separate pieces."""
    parts = re.split(r"\s*(?:\.\.\.|…|\[\.\.\.\])\s*", quote or "")
    return [p.strip(" \"'") for p in parts if len(_compact(p)) >= 8]


def verify_quote(quote: str, passage_id: Optional[str],
                 passages: Dict[str, dict]) -> Optional[dict]:
    """Find ``quote`` in the cited passage, else anywhere in the paper.

    ``passages`` maps passage id -> ``{"text", "page", "chunk_id"}``.
    Returns ``{"passage", "page", "chunk_id", "score", "relocated"}`` or None.
    ``relocated`` is true when the quote was real but the model cited the
    wrong passage — the statement is kept, pointing at where the words are."""
    quote = (quote or "").strip()
    if len(_compact(quote)) < MIN_QUOTE_CHARS - 5:
        return None
    parts = _quote_parts(quote) or [quote]

    def score_in(text: str) -> float:
        if not faithful(quote, text):
            return 0.0
        return min(similarity(part, text) for part in parts)

    candidates: List[Tuple[str, bool]] = []
    if passage_id and passage_id in passages:
        candidates.append((passage_id, False))
    candidates.extend((pid, True) for pid in passages if pid != passage_id)
    best: Optional[dict] = None
    for pid, relocated in candidates:
        score = score_in(passages[pid].get("text") or "")
        if score >= QUOTE_MATCH_THRESHOLD:
            found = {"passage": pid, "page": passages[pid].get("page"),
                     "chunk_id": passages[pid].get("chunk_id"),
                     "score": round(score, 3), "relocated": relocated}
            if not relocated or score == 1.0:
                return found
            if best is None or score > best["score"]:
                best = found
    return best


def verify_statements(statements: Iterable[dict], passages: Dict[str, dict]) -> Tuple[List[dict], int]:
    """Keep statements whose quote verifies; return ``(kept, rejected_count)``.

    Each statement is ``{"point", "quote", "passage"}`` as the model wrote it.
    Kept statements gain ``page``, ``chunk_id``, ``match`` and the quote as
    written; nothing the model said is kept without its evidence."""
    kept: List[dict] = []
    rejected = 0
    for item in statements or []:
        if not isinstance(item, dict):
            rejected += 1
            continue
        point = " ".join(str(item.get("point") or item.get("statement") or "").split())
        quote = " ".join(str(item.get("quote") or "").split())
        passage = str(item.get("passage") or item.get("passage_id") or "").strip().strip("[]")
        if not point or not quote:
            rejected += 1
            continue
        found = verify_quote(quote, passage, passages)
        if not found:
            rejected += 1
            continue
        kept.append({
            "point": point[:600],
            "quote": quote[:600],
            "passage": found["passage"],
            "page": found["page"],
            "chunk_id": found["chunk_id"],
            "match": found["score"],
            "relocated": found["relocated"],
        })
    return kept, rejected

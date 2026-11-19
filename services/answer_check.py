"""Check an Ask answer against the passages it cites (C4).

The Library checks quotes with deterministic code; Ask used to leave its
citations to the system prompt. This runs after every answer (a cached one
too) and needs no model: for each sentence that cites a source, the source
number must exist, and the figures, dates, identifiers and quoted strings in
the sentence must appear in the passages that sentence cites, after the same
normalising the Library uses (services/research/grounding.py). A sentence
that fails is marked "not found in the cited passage"; it is not removed or
rewritten.

What it does not check: wording, meaning or inference. A sentence with no
figures, identifiers or quotes passes as long as its source exists. That is
deliberate; the point is to catch the confident wrong number, the invented
form id, the misquote, with no false alarms on a fair paraphrase.

The citation groups are found with the same pattern the frontend uses
(frontend/src/utils/answerEvidence.js CITATION_GROUP), so the n-th group here
is the n-th citation control on the page.
"""

from __future__ import annotations

import re
from typing import Dict, List, Sequence, Set, Tuple

from services.research.draft_check import sentences
from services.research.grounding import normalize

# Mirrors CITATION_GROUP in frontend/src/utils/answerEvidence.js.
CITATION_GROUP = re.compile(r"\[Source\s+\d+(?:\s*[,;:](?:\s*Source)?[^\]]*)?\]", re.IGNORECASE)
_SOURCE_NUMBER = re.compile(r"Source\s+(\d+)", re.IGNORECASE)

_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*(?![\w])")
_IDENTIFIER = re.compile(r"\b(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*\b")
_QUOTED = re.compile(r"[\"“]([^\"“”\n]{3,200})[\"”]")
_LIST_MARKER = re.compile(r"^\s*(?:[#>*-]+\s*)?\d+[.)]\s+")
_LOCATORS = re.compile(
    r"\b(?:sources?|pages?|pp?\.|sections?|revision|rev\.?)\s+[A-Za-z]?\d+[A-Za-z]?\b"
    r"|\S+\.(?:pdf|docx?|xlsx?|csv|md|txt|pptx?|html?|json)\b",
    re.IGNORECASE,
)

_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
    "thousand": 1000, "once": 1, "twice": 2, "single": 1, "double": 2, "half": 0.5, "dozen": 12,
}
_MONTHS = {m: i for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"), start=1)}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})


def _canonical(number: str) -> str:
    """"1,000" and "1000", "07" and "7", "2.50" and "2.5" are one figure."""
    raw = number.replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return raw
    return str(int(value)) if value.is_integer() else repr(value)


def _figures_in_passage(text: str) -> Set[str]:
    norm = normalize(text)
    found = {_canonical(n) for n in _NUMBER.findall(norm)}
    words = re.findall(r"[a-z]+", norm)
    found.update(_canonical(str(_WORD_NUMBERS[w])) for w in words if w in _WORD_NUMBERS)
    found.update(str(_MONTHS[w]) for w in words if w in _MONTHS)
    # Hyphenated compounds ("twenty-five") and digits split by a hyphen
    # ("2026-03-24") each give their parts as well.
    for a, b in re.findall(r"\b([a-z]+)-([a-z]+)\b", norm):
        if a in _WORD_NUMBERS and b in _WORD_NUMBERS:
            found.add(_canonical(str(_WORD_NUMBERS[a] + _WORD_NUMBERS[b])))
    return found


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize(text))


def _claims(sentence: str) -> Tuple[List[str], List[str], List[str]]:
    """The figures, identifiers and quoted strings a sentence states, with
    its citations and any list numbering taken out first."""
    bare = _LIST_MARKER.sub("", CITATION_GROUP.sub(" ", sentence))
    # Where the sentence says where it read it ("as stated in Source 4",
    # "on page 3", "in policy-rev-b.pdf") is not a claim about the passage.
    bare = _LOCATORS.sub(" ", bare)
    quotes = [q.strip() for q in _QUOTED.findall(bare) if len(q.strip()) >= 3]
    unquoted = _QUOTED.sub(" ", bare)
    identifiers = list(dict.fromkeys(m for m in _IDENTIFIER.findall(unquoted) if not m.isdigit()))
    without_ids = _IDENTIFIER.sub(" ", unquoted)
    figures = list(dict.fromkeys(_canonical(n) for n in _NUMBER.findall(normalize(without_ids))))
    return figures, identifiers, quotes


def check_answer(answer: str, sources: Sequence[dict]) -> List[dict]:
    """The citation groups whose sentence is not supported, in order:
    ``{"group": n, "sentence": k, "sources": [numbers], "missing": [what was
    not found], "reason": "no_such_source" | "not_in_passage"}``. Empty when every cited
    sentence checks out. Never raises."""
    try:
        return _check(answer or "", list(sources or []))
    except Exception:  # noqa: BLE001 - a check must never cost the answer
        return []


def _check(answer: str, sources: List[dict]) -> List[dict]:
    groups = list(CITATION_GROUP.finditer(answer))
    if not groups:
        return []
    spans = sentences(answer)

    def sentence_of(pos: int) -> Tuple[int, int]:
        for start, end in spans:
            if start <= pos < end:
                return start, end
        return 0, len(answer)

    by_sentence: Dict[Tuple[int, int], List[int]] = {}
    for n, group in enumerate(groups):
        by_sentence.setdefault(sentence_of(group.start()), []).append(n)

    failures: List[dict] = []
    for k, ((start, end), members) in enumerate(by_sentence.items()):
        numbers = sorted({int(x) for n in members for x in _SOURCE_NUMBER.findall(groups[n].group(0))})
        absent = [x for x in numbers if not 1 <= x <= len(sources)]
        if absent:
            failures.extend({"group": n, "sentence": k, "sources": numbers,
                             "missing": [f"Source {x}" for x in absent], "reason": "no_such_source"}
                            for n in members)
            continue
        passages = " ".join(str(sources[x - 1].get("text_snippet") or "") for x in numbers)
        figures, identifiers, quotes = _claims(answer[start:end])
        have_figures = _figures_in_passage(passages)
        compact = _compact(passages)
        missing = [f for f in figures if f not in have_figures]
        missing += [i for i in identifiers if _compact(i) not in compact]
        missing += [f"“{q}”" for q in quotes if _compact(q) not in compact]
        if missing:
            failures.extend({"group": n, "sentence": k, "sources": numbers, "missing": missing,
                             "reason": "not_in_passage"} for n in members)
    failures.sort(key=lambda f: f["group"])
    return failures

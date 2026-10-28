"""Old versions and copies: demote the first, collapse the second.

Two retrieval problems with one shape. A policy set holds v2 and v3 of the
same document, and the same paragraph appears in several files. Without help
both rank equally, so the current text competes with its own history and one
passage can fill the whole result list.

* **Replaced by.** An owner marks a document as replaced by another. Search
  demotes the older document (it never disappears: it still answers when it
  is the only match, and a request can ask for it) and labels each of its
  results with the replacement.
* **Near-duplicate collapse.** After ranking, a result whose text is a
  near-duplicate of a higher-ranked one is folded into it. The survivor lists
  where else the text appears, so a citation can say "also in …".
* **Suggestions.** A cheap detector proposes likely replaced-by pairs from
  file names ("policy v2" / "policy v3", "(1)" copies) and identical text.
  It only suggests; an owner confirms.

Everything here is pure functions over result objects and plain dicts, so
the retrieval path can wrap each step and fall back to the unmodified list.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

# How many "also in" entries ride along on one result. The count of every
# collapsed copy is reported separately, so a boilerplate paragraph repeated
# in two hundred files costs a handful of tokens, not two hundred.
MAX_DUPLICATES_LISTED = 5

# Results fetched beyond top_k so collapsing has something to backfill with.
BACKFILL_MARGIN = 10


# ── text normalisation and similarity ──────────────────────────────────────

_NON_WORD = re.compile(r"[^\w\s.]|_", re.UNICODE)
_SPACES = re.compile(r"\s+")
_DIGIT_COMMA = re.compile(r"(?<=\d),(?=\d)")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

# Words that flip meaning without changing much of the surface text. Two
# passages that differ only in one of these are different statements, however
# similar they look: the guard requires both sides to carry the same set.
_MEANING_WORDS = frozenset({
    "no", "not", "never", "none", "neither", "nor", "without", "cannot",
    "may", "must", "shall", "should", "can", "will", "required", "optional",
    "prohibited", "forbidden", "permitted", "allowed", "unless", "except",
    "before", "after", "only", "all", "any",
})


def normalize_text(text: str) -> str:
    """Lowercase, drop punctuation (except digit separators), collapse spaces."""
    text = (text or "").lower()
    # Keep "." and "," inside numbers; everything else punctuation goes.
    text = _NON_WORD.sub(" ", _DIGIT_COMMA.sub("", text))
    return _SPACES.sub(" ", text).strip(" .")


def text_hash(normalized: str) -> str:
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def shingles(normalized: str, size: int = 3) -> Set[str]:
    """Word n-grams; a text shorter than ``size`` words is its own shingle."""
    words = normalized.replace(".", " ").split()
    if len(words) <= size:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + size]) for i in range(len(words) - size + 1)}


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def _signature(normalized: str) -> Tuple[Tuple[str, ...], frozenset]:
    """Numbers (in order) and meaning-flipping words present in a text."""
    numbers = tuple(_NUMBER.findall(normalized))
    flips = frozenset(w for w in normalized.replace(".", " ").split() if w in _MEANING_WORDS)
    return numbers, flips


class _Fingerprint:
    __slots__ = ("hash", "norm", "shingles", "signature", "length")

    def __init__(self, text: str):
        self.norm = normalize_text(text)
        self.hash = text_hash(self.norm)
        self.shingles = shingles(self.norm)
        self.signature = _signature(self.norm)
        self.length = len(self.norm)


def is_near_duplicate(a: _Fingerprint, b: _Fingerprint, threshold: float) -> bool:
    """Same text after normalisation, or word-3-gram Jaccard >= threshold
    with the same numbers and the same negation/modal words on both sides."""
    if not a.norm or not b.norm:
        return False
    if a.hash == b.hash:
        return True
    if min(a.length, b.length) < 0.8 * max(a.length, b.length):
        return False
    if a.signature != b.signature:
        return False
    return jaccard(a.shingles, b.shingles) >= threshold


# ── replaced-by: demotion ──────────────────────────────────────────────────

def annotate_superseded(results: Sequence[Any], info: Dict[str, dict]) -> int:
    """Attach ``superseded_by`` to each result whose document was replaced.
    Returns how many results were labelled."""
    labelled = 0
    for r in results:
        rel = info.get(getattr(r, "document_id", None) or "")
        if not rel:
            continue
        r.superseded_by = {"document_id": rel["document_id"], "filename": rel["filename"]}
        if rel.get("note"):
            r.superseded_by["note"] = rel["note"]
        labelled += 1
    return labelled


def demote_superseded(results: List[Any], penalty: float,
                      scores: Optional[Dict[int, float]] = None) -> List[Any]:
    """Re-rank ``results`` with a flat penalty on labelled-superseded ones.

    ``scores`` maps ``id(result)`` to the score that decided the current
    order (the reranker's probability when it ran). Without it the retrieval
    ``similarity_score`` is used and updated in place, so it stays a faithful
    description of the final order. The sort is stable: equal effective
    scores keep their previous order, so current text only moves ahead of
    older text it ties with or trails by less than the penalty.
    """
    if penalty <= 0 or not any(getattr(r, "superseded_by", None) for r in results):
        return results
    if scores is None:
        for r in results:
            if getattr(r, "superseded_by", None):
                r.similarity_score = max(0.0, float(r.similarity_score) - penalty)
        return sorted(results, key=lambda r: float(r.similarity_score), reverse=True)

    def effective(r: Any) -> float:
        base = scores.get(id(r))
        if base is None:
            base = float(r.similarity_score)
        return base - (penalty if getattr(r, "superseded_by", None) else 0.0)

    return sorted(results, key=effective, reverse=True)


def sigmoid(x: float) -> float:
    """Cross-encoder logit -> 0..1, so one penalty value means the same on
    the reranker's scale as on the retrieval score's."""
    try:
        return 1.0 / (1.0 + math.exp(-float(x)))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


# ── near-duplicate collapse ────────────────────────────────────────────────

def _dup_entry(result: Any) -> dict:
    entry = {
        "document_id": result.document_id,
        "filename": result.filename,
        "page": result.page_number,
    }
    cid = getattr(result, "collection_id", None)
    if cid:
        entry["collection_id"] = cid
    return entry


def collapse_near_duplicates(
    results: Sequence[Any],
    threshold: float,
    protected_documents: Optional[Iterable[str]] = None,
    groups_out: Optional[list] = None,
) -> Tuple[List[Any], int]:
    """Fold near-duplicate results into the best-ranked copy.

    ``results`` arrive best first. The first of a group survives and gains
    ``duplicates`` (at most MAX_DUPLICATES_LISTED entries) and
    ``duplicate_count``. When the survivor is a superseded document and a
    lower-ranked copy is current, the current copy takes the place instead:
    the reader should land on the live text, with the old one listed under it.

    A document the user explicitly selected (``protected_documents``) never
    loses its only result to collapse: selecting a file and getting nothing
    back from it would read as "it says nothing about this".

    Returns ``(kept results in rank order, number collapsed)``.
    """
    protected = set(protected_documents or ())
    prints: Dict[int, _Fingerprint] = {}
    groups: List[dict] = []          # {"lead": result, "copies": [result, ...]}
    by_hash: Dict[str, dict] = {}
    docs_with_result: Dict[str, int] = {}
    collapsed = 0

    def fingerprint(r: Any) -> _Fingerprint:
        fp = prints.get(id(r))
        if fp is None:
            fp = prints[id(r)] = _Fingerprint(getattr(r, "text_snippet", "") or "")
        return fp

    for r in results:
        fp = fingerprint(r)
        group = by_hash.get(fp.hash) if fp.norm else None
        if group is None and fp.norm:
            for g in groups:
                if is_near_duplicate(fingerprint(g["lead"]), fp, threshold):
                    group = g
                    break
        if group is not None and r.document_id in protected \
                and docs_with_result.get(r.document_id, 0) == 0:
            group = None               # the selection's only voice stays
        if group is None:
            group = {"lead": r, "copies": []}
            groups.append(group)
            if fp.norm:
                by_hash.setdefault(fp.hash, group)
            docs_with_result[r.document_id] = docs_with_result.get(r.document_id, 0) + 1
            continue
        lead = group["lead"]
        live_candidate = not getattr(r, "superseded_by", None)
        lead_is_old = bool(getattr(lead, "superseded_by", None))
        lead_doc_has_others = docs_with_result.get(lead.document_id, 0) > 1
        can_swap = (
            lead_is_old and live_candidate
            and (lead.document_id not in protected or lead_doc_has_others)
        )
        if can_swap:
            group["copies"].append(lead)
            group["lead"] = r
            docs_with_result[lead.document_id] -= 1
            docs_with_result[r.document_id] = docs_with_result.get(r.document_id, 0) + 1
        else:
            group["copies"].append(r)
        collapsed += 1

    kept: List[Any] = []
    for g in groups:
        lead = g["lead"]
        if g["copies"]:
            seen = {(lead.document_id, lead.page_number)}
            listed: List[dict] = []
            distinct = 0
            for c in g["copies"]:
                key = (c.document_id, c.page_number)
                if key in seen:
                    continue
                seen.add(key)
                distinct += 1
                if len(listed) < MAX_DUPLICATES_LISTED:
                    listed.append(_dup_entry(c))
            lead.duplicates = listed
            lead.duplicate_count = distinct
            if groups_out is not None:
                groups_out.append((lead, list(g["copies"])))
        kept.append(lead)
    return kept, collapsed


def source_note(result: Any) -> str:
    """One line the model reads next to a passage: whether it is from an older
    version and where else the same text appears. Empty for ordinary passages."""
    parts: List[str] = []
    rel = getattr(result, "superseded_by", None)
    if rel:
        parts.append(f"OLDER VERSION, replaced by {rel.get('filename')}")
    dups = getattr(result, "duplicates", None)
    if dups:
        names = ", ".join(dict.fromkeys(str(d.get("filename")) for d in dups[:3]))
        total = getattr(result, "duplicate_count", None) or len(dups)
        parts.append(f"same text also in {names}" + (f" and {total - 3} more" if total > 3 else ""))
    return f" ({'; '.join(parts)})" if parts else ""


# ── replacement suggestions ────────────────────────────────────────────────

_EXT = re.compile(r"\.[A-Za-z0-9]{1,8}$")
_VERSION = re.compile(r"(?<![a-z0-9])v(?:er(?:sion)?)?[ ._-]?(\d+(?:\.\d+)*)(?![a-z0-9])", re.I)
_REVISION = re.compile(r"(?<![a-z0-9])rev(?:ision)?[ ._-]?([a-z]|\d+)(?![a-z0-9])", re.I)
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_COPY_PAREN = re.compile(r"[ _-]*\((\d+)\)\s*$")
_COPY_WORD = re.compile(r"[ _-]*[-_ ]copy(?:[ _-]*(\d+))?\s*$", re.I)
_SEPARATORS = re.compile(r"[\s._\-]+")


def parse_filename_version(filename: str) -> Optional[Tuple[str, tuple, bool]]:
    """``(stem, version_key, is_copy)`` or None when the name carries no
    version cue. Two files with the same stem and different keys are versions
    of one document; ``is_copy`` marks "(1)" / "- Copy" download duplicates,
    which are ordered by upload time instead of by name."""
    name = _EXT.sub("", (filename or "").strip())
    copy_n = 0
    m = _COPY_PAREN.search(name) or _COPY_WORD.search(name)
    if m:
        copy_n = int(m.group(1) or 1)
        name = name[:m.start()]
    year = 0
    ym = _YEAR.search(name)
    if ym:
        year = int(ym.group(1))
        name = name[:ym.start()] + " " + name[ym.end():]
    version: tuple = ()
    vm = _VERSION.search(name)
    if vm:
        version = tuple(int(p) for p in vm.group(1).split("."))
        name = name[:vm.start()] + " " + name[vm.end():]
    rev = 0
    rm = _REVISION.search(name)
    if rm:
        tok = rm.group(1).lower()
        rev = int(tok) if tok.isdigit() else (ord(tok) - ord("a") + 1)
        name = name[:rm.start()] + " " + name[rm.end():]
    stem = _SEPARATORS.sub(" ", name).strip().lower()
    if len(stem) < 3 or not (copy_n or year or version or rev):
        return None
    return stem, (year, version, rev), bool(copy_n)


def suggest_supersessions(
    documents: Sequence[dict],
    leading_text: Optional[Dict[str, str]] = None,
    limit: int = 50,
) -> List[dict]:
    """Likely "replaced by" pairs, newest version winning. Never applied.

    ``documents`` are listing rows (document_id, filename, upload_timestamp,
    superseded_by). Documents already marked replaced are skipped as the old
    side, and any pair already related is not repeated. Rules, strongest
    first:

    * ``version_in_name`` — same stem, different version/year/revision token;
      each older file is suggested as replaced by the highest version.
    * ``copy_of`` — same stem, one is a "(1)" / "- Copy" duplicate; the later
      upload is the replacement.
    * ``same_text`` — different bytes, identical normalised opening text; the
      later upload is the replacement.
    """
    by_id = {d["document_id"]: d for d in documents}
    suggestions: List[dict] = []
    paired: Set[Tuple[str, str]] = set()

    def stamp(d: dict) -> str:
        return d.get("upload_timestamp") or ""

    def emit(old: dict, new: dict, reason: str, confidence: str):
        if old["document_id"] == new["document_id"] or old.get("superseded_by"):
            return
        key = (old["document_id"], new["document_id"])
        if key in paired:
            return
        paired.add(key)
        suggestions.append({
            "document_id": old["document_id"], "filename": old["filename"],
            "replaced_by": new["document_id"], "replaced_by_filename": new["filename"],
            "reason": reason, "confidence": confidence,
        })

    # 1 + 2: file names.
    stems: Dict[str, List[Tuple[tuple, bool, dict]]] = {}
    for d in documents:
        parsed = parse_filename_version(d.get("filename", ""))
        if parsed:
            stems.setdefault(parsed[0], []).append((parsed[1], parsed[2], d))
    # A stem is only a family when it also appears as a bare name or with
    # another cue; a lone "report 2025" has nothing to be compared to.
    bare: Dict[str, dict] = {}
    for d in documents:
        name = _SEPARATORS.sub(" ", _EXT.sub("", d.get("filename", ""))).strip().lower()
        if name in stems:
            bare.setdefault(name, d)
    for stem, members in stems.items():
        family = [(key, is_copy, d) for key, is_copy, d in members]
        if stem in bare and all(d["document_id"] != bare[stem]["document_id"] for _, _, d in family):
            family.append(((0, (), 0), False, bare[stem]))
        if len(family) < 2:
            continue
        versioned = [(key, d) for key, is_copy, d in family if not is_copy]
        copies = [d for key, is_copy, d in family if is_copy]
        if len(versioned) >= 2:
            ordered = sorted(versioned, key=lambda kd: (kd[0], stamp(kd[1])))
            top_key, top = ordered[-1]
            for key, d in ordered[:-1]:
                if key != top_key:
                    emit(d, top, "version_in_name", "high")
        for c in copies:
            originals = [d for _, d in versioned if d["document_id"] != c["document_id"]]
            original = max(originals, key=stamp) if originals else None
            if original is not None:
                older, newer = sorted([original, c], key=stamp)
                emit(older, newer, "copy_of", "medium")

    # 3: identical opening text under different bytes.
    if leading_text:
        buckets: Dict[str, List[dict]] = {}
        for doc_id, text in leading_text.items():
            d = by_id.get(doc_id)
            norm = normalize_text(text)
            if d is not None and len(norm) >= 80:
                buckets.setdefault(text_hash(norm), []).append(d)
        for group in buckets.values():
            if len(group) < 2:
                continue
            ordered = sorted(group, key=lambda d: (stamp(d), d["document_id"]))
            newest = ordered[-1]
            for d in ordered[:-1]:
                emit(d, newest, "same_text", "medium")

    return suggestions[:limit]

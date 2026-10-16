"""Query-adaptive retrieval planning for ``SearchMode.AUTO``.

Nobody should have to choose between semantic, keyword and hybrid search.
Hybrid retrieval is the right engine for nearly every query, so ``auto``
always runs it and only decides how much weight meaning gets against exact
keywords:

* an identifier, error code, path or quoted phrase must match literally, so
  keywords lead (``ERR_4021``, ``v2.3.1``, ``"retention period"``);
* a full question is about meaning, so embeddings lead;
* anything else gets the deployment's default balance.

Pure functions, no I/O, so the decision is cheap, deterministic and testable.
The reason string travels with the search response so a surprising ranking
can be explained.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from models.schemas import SearchMode

# Weight on the embedding side of the hybrid blend (1 - weight goes to BM25).
KEYWORD_LED = 0.2
KEYWORD_LEANING = 0.45
MEANING_LED = 0.8

_QUOTED = re.compile(r'"[^"]{2,}"|“[^”]{2,}”')
_INTERROGATIVE = {
    "who", "whom", "whose", "what", "which", "when", "where", "why", "how",
    "is", "are", "was", "were", "do", "does", "did", "can", "could", "should",
    "would", "will", "explain", "describe", "summarize", "summarise", "compare",
}
_EDGE_PUNCT = ".,;:!?()[]{}<>\"'`"


def _is_identifier(token: str) -> bool:
    """True for tokens that only match if spelled exactly.

    Digit/letter mixes (``ERR4021``, ``a1b2``), snake_case, camelCase, dotted
    names and versions (``config.yaml``, ``v2.3.1``), paths, emails and long
    digit runs. A bare year or small number is ordinary prose and does not
    count.
    """
    t = token.strip(_EDGE_PUNCT)
    if len(t) < 3:
        return False
    has_digit = any(c.isdigit() for c in t)
    has_alpha = any(c.isalpha() for c in t)
    if has_digit and has_alpha:
        return True
    if has_digit and not has_alpha:
        digits = sum(c.isdigit() for c in t)
        # 5+ digits (ids, part numbers) or dotted/dashed digit groups (1.2.3, 555-0100)
        return digits >= 5 or bool(re.fullmatch(r"\d+([.\-/]\d+)+", t))
    if "_" in t or "/" in t or "\\" in t or "@" in t:
        return True
    if re.search(r"[a-z][A-Z]", t):  # camelCase
        return True
    if re.search(r"[A-Za-z0-9]\.[A-Za-z0-9]", t) and not t.endswith("."):
        return True  # file.ext, pkg.module, v2.3
    return False


@dataclass(frozen=True)
class RetrievalPlan:
    """The concrete retrieval an ``auto`` query resolved to."""

    mode: SearchMode
    semantic_weight: float
    reason: str


def plan_retrieval(query: str, base_weight: float = 0.7) -> RetrievalPlan:
    """Pick the hybrid balance for ``query``. Always returns HYBRID."""
    base = max(0.0, min(float(base_weight), 1.0))
    text = (query or "").strip()
    tokens = text.split()

    if _QUOTED.search(text):
        return RetrievalPlan(SearchMode.HYBRID, KEYWORD_LED, "quoted phrase")

    identifiers = sum(1 for t in tokens if _is_identifier(t))
    if identifiers:
        if identifiers * 2 >= len(tokens):
            return RetrievalPlan(SearchMode.HYBRID, KEYWORD_LED, "exact identifier")
        return RetrievalPlan(SearchMode.HYBRID, KEYWORD_LEANING, "identifier in a longer query")

    first = tokens[0].lower().strip(_EDGE_PUNCT) if tokens else ""
    if text.endswith("?") or first in _INTERROGATIVE or len(tokens) >= 6:
        return RetrievalPlan(SearchMode.HYBRID, max(base, MEANING_LED), "natural-language question")

    if len(tokens) <= 2:
        return RetrievalPlan(SearchMode.HYBRID, KEYWORD_LEANING, "short term")

    return RetrievalPlan(SearchMode.HYBRID, base, "default balance")

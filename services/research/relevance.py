"""Is this paper worth my time for my question?

Two layers, the first of which needs no model:

1. **Where the paper talks about it** — the collection's own retrieval run
   inside the paper for the question. These passages are what the reader
   would skim to decide; they are shown whatever the model says.
2. **Verdict** (when a model is available) — the model reads the paper's
   opening passages plus those hits and answers ``relevant`` / ``partial`` /
   ``not_relevant`` / ``not_established``, with each supporting point
   quoting a passage. Points whose quote is not in the paper are dropped
   (``rejected`` counts them); a positive verdict left with no verified
   point becomes ``not_established``. The one-line summary is kept only if
   every number and negation in it appears in the verified quotes.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from services.research.grounding import faithful, verify_statements
from services.research.passages import (
    GROUNDING_RULES, add_usage, chunk_passages, complete, parse_json_object, render,
    search_passage_ids, select,
)

VERDICTS = ("relevant", "partial", "not_relevant", "not_established")
LEADING_PASSAGES = 3
BUDGET_CHARS = 14000
MAX_QUESTION_CHARS = 1000

_PROMPT = (
    "A researcher is deciding whether a paper is worth reading for their question. Decide "
    "whether THIS PAPER contains information that addresses the question.\n\n"
    "{rules}\n\n"
    "Return this JSON object:\n"
    "{{\"verdict\": \"relevant\" | \"partial\" | \"not_relevant\" | \"not_established\",\n"
    "  \"summary\": \"one or two sentences, stating only what your points show\",\n"
    "  \"points\": [{{\"point\": \"what the paper offers for the question\", \"quote\": \"...\", "
    "\"passage\": \"P3\"}}],\n"
    "  \"missing\": [\"parts of the question these passages do not address\"]}}\n"
    "Use \"relevant\" when the paper directly addresses the question, \"partial\" when it "
    "covers part of it or something adjacent, \"not_relevant\" when the passages show it is "
    "about something else, and \"not_established\" when these excerpts are not enough to "
    "tell. Give at most 6 points.\n\n"
    "QUESTION: {question}\n\n"
    "PASSAGES:\n{passages}"
)


def assess(indexer, document_id: str, question: str, provider=None, model: Optional[str] = None) -> dict:
    """Assess one paper. Without a provider, returns the retrieval layer only
    (``verdict`` None)."""
    question = " ".join((question or "").split())[:MAX_QUESTION_CHARS]
    if not question:
        raise ValueError("A question is required")
    chunks = indexer.vector_store.metadata_store.get_chunks_by_document(document_id)
    passages = chunk_passages(chunks)
    if not passages:
        return {"verdict": "not_established", "summary": "", "evidence": [], "rejected": 0,
                "retrieval": [], "missing": [], "model": "",
                "notes": ["The paper has no indexed text to assess."], "usage": {}}

    hit_ids, retrieval = search_passage_ids(indexer, document_id, [question], passages, per_query=8)
    result = {"verdict": None, "summary": "", "evidence": [], "rejected": 0, "retrieval": retrieval,
              "missing": [], "model": "", "notes": [], "usage": {}}
    if provider is None:
        result["notes"].append("No model configured: showing where the paper discusses the question.")
        return result

    leading = list(passages)[:LEADING_PASSAGES]
    shown = select(passages, leading + hit_ids, BUDGET_CHARS)
    prompt = _PROMPT.format(rules=GROUNDING_RULES, question=question, passages=render(shown))
    text, usage = complete(provider, model, prompt, max_tokens=1400)
    result["usage"] = add_usage({}, usage)
    result["model"] = usage.get("model") or model or ""
    parsed = parse_json_object(text)
    if not parsed:
        result["verdict"] = "not_established"
        result["notes"].append("The model's answer could not be read, so no verdict is given.")
        return result

    # Quotes are checked against the passages the model was shown — a quote
    # from a passage it never saw is a quote it made up.
    evidence, rejected = verify_statements(parsed.get("points") or [], shown)
    verdict = str(parsed.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        verdict = "not_established"
    if verdict in ("relevant", "partial") and not evidence:
        verdict = "not_established"
        result["notes"].append("The model judged the paper relevant but none of its quotes were "
                               "found in the paper, so the verdict was withdrawn.")
    summary = " ".join(str(parsed.get("summary") or "").split())[:500]
    if summary and evidence and not faithful(summary, " ".join(e["quote"] for e in evidence)):
        result["notes"].append("The model's summary stated numbers or negations not in its quotes "
                               "and was removed.")
        summary = ""
    if summary and not evidence and verdict != "not_relevant":
        summary = ""
    missing: List[str] = [" ".join(str(m).split())[:200] for m in (parsed.get("missing") or [])
                          if isinstance(m, str) and m.strip()][:6]
    result.update(verdict=verdict, summary=summary, evidence=evidence, rejected=rejected, missing=missing)
    if rejected:
        result["notes"].append(f"{rejected} statement(s) were removed because their quote was not "
                               "found in the paper.")
    result["shown"] = {"passages": len(shown), "of": len(passages)}
    return result


def quick_rank(results: Dict[str, dict]) -> List[str]:
    """Order document ids for a reading list: verdict first, then evidence."""
    rank = {"relevant": 0, "partial": 1, "not_established": 2, None: 3, "not_relevant": 4}
    return sorted(results, key=lambda d: (rank.get(results[d].get("verdict"), 3),
                                          -len(results[d].get("evidence") or []),
                                          -max([h["score"] for h in results[d].get("retrieval") or []] or [0])))

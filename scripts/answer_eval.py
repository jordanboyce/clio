"""Answer-level evaluation with no person in the loop (A2).

The retrieval benchmark says whether the right passage was found; this says
what the answer did with it. It builds a fixture's index in-process exactly
as scripts/retrieval_bench.py does, then asks each question through the
app's own answer turn (api/chat.py ``_run_turn``: retrieval, prompt, model,
citations, the C4 check) against a named model, and scores, where the right
answer is known:

  citation precision     cited passages that are among the labelled ones
  supported sentences    cited sentences whose figures, identifiers and
                         quotes are in the passage they cite (the C4 check)
  correct abstention     declined on the unanswerable questions, answered
                         on the rest (both halves are reported)
  time to answer         wall time per question, and to the first text

It runs against a local model with the network blocked:

    HF_HUB_OFFLINE=1 venv/Scripts/python scripts/answer_eval.py \\
        --provider ollama --model llama3.2:3b --limit 40 --out eval-llama

``--out`` writes results.json and flags.md: every sentence the C4 check
marked, with the passage it cites, for a person to judge false alarms.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import retrieval_bench as bench  # noqa: E402

DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "retrieval_hard"

# The pilot's abstention phrases (scripts/pilot_eval.py), plus the app's own
# "nothing found" wording.
from pilot_eval import _ABSTAIN  # noqa: E402

_CITED = re.compile(r"Source\s+(\d+)", re.IGNORECASE)


def _norm(text: str) -> str:
    return " ".join(str(text or "").split()).lower()


def source_is_relevant(corpus, query: dict, source: dict) -> bool:
    """Whether a cited source is one of the question's labelled units."""
    for ref in query["relevant"]:
        if "::" in ref:
            doc_id, _, sentence = ref.partition("::")
            if source.get("document_id") == doc_id and _norm(sentence) in _norm(source.get("text_snippet")):
                return True
        elif "#" in ref:
            doc_id, _, n = ref.partition("#")
            passage = corpus.document(doc_id)["passages"][int(n) - 1]
            if source.get("document_id") == doc_id and _norm(passage)[:200] in _norm(source.get("text_snippet")):
                return True
        elif source.get("document_id") == ref:
            return True
    return False


def _wire_turn(indexers, corpus):
    """Point the answer turn at the benchmark's index instead of the app's
    collections, and turn off what a benchmark must not touch (usage records,
    the answer cache, budgets). Everything else is the app's own code."""
    from api import chat

    def get_indexer(collection_id):
        return indexers[collection_id] if isinstance(indexers, dict) else indexers

    chat.get_indexer = get_indexer
    chat.collect_structured_tables = lambda *a: ([], {})
    chat._build_collection_overview = lambda *a: "Policies, procedures and standards of twenty divisions."
    chat.expertise_store.get_packs_for_collection = lambda *a: []
    chat.collection_service.get_collection = lambda cid: {"id": cid, "name": cid}
    chat.collection_service.get_all_collections = lambda: [{"id": c} for c in (
        corpus.collections if isinstance(indexers, dict) else ["main"])]
    chat._cache_context = lambda *a: None
    chat._cache_store = lambda *a, **k: None
    chat.check_daily_budget = lambda *a: None
    chat.record_chat_usage = lambda *a, **k: None
    return chat


def ask(chat, query: dict, provider: str, model: str, top_k: int) -> dict:
    where = query.get("collection") or "main"
    request = chat.ChatRequest(
        messages=[{"role": "user", "content": query["query"]}], provider=provider, use_cache=False,
        depth="quick", related=False, top_k=top_k, scope="all" if where == "*" else "current",
    )
    headers = {"ollama": "x_ollama_model", "anthropic": "x_anthropic_model", "openai": "x_openai_model"}
    args = {k: None for k in ("x_ai_key", "x_ollama_model", "x_anthropic_model", "x_openai_model",
                              "x_ai_model", "x_ai_base_url")}
    args[headers.get(provider, "x_ai_model")] = model
    from services import stage_timing

    started = time.perf_counter()
    first = None
    text, done, error = [], None, None
    with stage_timing.trace() as stages:
        for event in chat._timed_turn(chat._run_turn(request, where if where != "*" else "finance", "http://eval",
                                                     chat._provider_args(**args), None, None)):
            if event["type"] == "text":
                if first is None:
                    first = time.perf_counter() - started
                text.append(event["text"])
            elif event["type"] == "done":
                done = event
            elif event["type"] == "error":
                error = event.get("message")
    return {"answer": "".join(text).strip(), "done": done, "error": error,
            "seconds": time.perf_counter() - started, "first_text_seconds": first,
            "stages_ms": stage_timing.rounded(stages)}


def score(corpus, query: dict, outcome: dict) -> dict:
    from services.answer_check import CITATION_GROUP, check_answer
    from services.research.draft_check import sentences

    done = outcome["done"] or {}
    sources = done.get("sources") or []
    answer = outcome["answer"]
    cited = sorted({int(n) for g in CITATION_GROUP.findall(answer) for n in _CITED.findall(g)})
    valid = [n for n in cited if 1 <= n <= len(sources)]
    relevant = [n for n in valid if query["relevant"] and source_is_relevant(corpus, query, sources[n - 1])]
    checks = done.get("answer_checks")
    if checks is None:
        checks = check_answer(answer, sources)
    cited_sentences = sum(1 for s, e in sentences(answer) if CITATION_GROUP.search(answer[s:e]))
    flagged = len({f["sentence"] for f in checks})
    return {
        "id": query["id"], "category": query["category"], "query": query["query"],
        "answer": answer, "error": outcome["error"],
        "abstained": bool(_ABSTAIN.search(answer)) or not cited,
        "answerable": bool(query["relevant"]),
        "cited": cited, "cited_relevant": relevant,
        "cited_sentences": cited_sentences, "flagged_sentences": flagged,
        "checks": checks, "sources": [{"n": i + 1, "document_id": s.get("document_id"),
                                       "text": s.get("text_snippet")} for i, s in enumerate(sources)],
        "seconds": round(outcome["seconds"], 2),
        "stages_ms": outcome.get("stages_ms"),
        "first_text_seconds": round(outcome["first_text_seconds"], 2) if outcome["first_text_seconds"] else None,
    }


def summarise(rows: Sequence[dict]) -> Dict[str, Any]:
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    cited = sum(len(r["cited"]) for r in answerable)
    cited_sentences = sum(r["cited_sentences"] for r in rows)

    def share(part, whole):
        return round(part / whole, 3) if whole else None

    return {
        "questions": len(rows),
        "errors": sum(1 for r in rows if r["error"]),
        "citation_precision": share(sum(len(r["cited_relevant"]) for r in answerable), cited),
        "supported_sentence_rate": share(cited_sentences - sum(r["flagged_sentences"] for r in rows),
                                         cited_sentences),
        "declined_when_unanswerable": share(sum(1 for r in unanswerable if r["abstained"]), len(unanswerable)),
        "answered_when_answerable": share(sum(1 for r in answerable if not r["abstained"]), len(answerable)),
        "seconds_p50": round(statistics.median(r["seconds"] for r in rows), 1) if rows else None,
        "first_text_seconds_p50": round(statistics.median(
            [r["first_text_seconds"] for r in rows if r["first_text_seconds"] is not None] or [0]), 1),
        "model_first_text_seconds_p50": round(statistics.median(
            [r["stages_ms"]["model_first_text"] / 1000 for r in rows
             if (r.get("stages_ms") or {}).get("model_first_text")] or [0]), 1),
    }


def render(summary: dict, meta: dict) -> str:
    lines = [f"### Answer evaluation: {meta['fixture']} with {meta['provider']} {meta['model']}", "",
             f"{summary['questions']} questions ({meta['answerable']} answerable, {meta['unanswerable']} not), "
             f"top_k {meta['top_k']}, recipe {meta.get('recipe')}, git {meta.get('git')}.", "",
             "| measure | value |", "|---|---|"]
    for key in ("citation_precision", "supported_sentence_rate", "declined_when_unanswerable",
                "answered_when_answerable", "seconds_p50", "first_text_seconds_p50",
                "model_first_text_seconds_p50", "errors"):
        lines.append(f"| {key.replace('_', ' ')} | {summary[key]} |")
    return "\n".join(lines) + "\n"


def flags_report(rows: Sequence[dict]) -> str:
    """Every sentence the C4 check marked, with what it cites, to judge by hand."""
    from services.answer_check import CITATION_GROUP
    from services.research.draft_check import sentences

    out = ["# Sentences the citation check marked", ""]
    for r in rows:
        if not r["checks"]:
            continue
        spans = [(s, e) for s, e in sentences(r["answer"]) if CITATION_GROUP.search(r["answer"][s:e])]
        for k in sorted({f["sentence"] for f in r["checks"]}):
            f = next(x for x in r["checks"] if x["sentence"] == k)
            sentence = r["answer"][spans[k][0]:spans[k][1]] if k < len(spans) else "?"
            out += [f"## {r['id']} sentence {k}", "", f"> {sentence.strip()}", "",
                    f"Missing: {', '.join(f['missing'])} ({f['reason']})", ""]
            for n in f["sources"]:
                if 1 <= n <= len(r["sources"]):
                    out += [f"Source {n} ({r['sources'][n - 1]['document_id']}):", "",
                            "    " + " ".join(str(r["sources"][n - 1]["text"]).split())[:900], ""]
    return "\n".join(out) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--model", required=True)
    parser.add_argument("--limit", type=int, default=40, help="questions to ask, spread over the categories")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--recipe", type=int, default=None)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)

    cache = bench.prepare_environment()
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("services").setLevel(logging.WARNING)
    corpus = bench.load_corpus(args.fixture)
    problems = bench.validate_corpus(corpus)
    if problems:
        raise SystemExit("fixture is inconsistent:\n  " + "\n  ".join(problems))

    import tempfile
    workdir = Path(tempfile.mkdtemp(prefix="clio-answer-eval-"))
    print(f"indexing {len(corpus.documents)} documents ...", file=sys.stderr)
    indexers = bench.build_index(corpus, workdir / "index", cache, recipe=args.recipe)
    chat = _wire_turn(indexers, corpus)

    rng = random.Random(args.seed)
    by_cat: Dict[str, List[dict]] = {}
    for q in corpus.queries:
        by_cat.setdefault(q["category"], []).append(q)
    chosen: List[dict] = []
    while len(chosen) < min(args.limit, len(corpus.queries)):
        for cat in sorted(by_cat):
            pool = [q for q in by_cat[cat] if q not in chosen]
            if pool and len(chosen) < args.limit:
                chosen.append(rng.choice(pool))

    rows = []
    for i, q in enumerate(chosen, start=1):
        print(f"[{i}/{len(chosen)}] {q['id']}: {q['query']}", file=sys.stderr, flush=True)
        rows.append(score(corpus, q, ask(chat, q, args.provider, args.model, args.top_k)))

    summary = summarise(rows)
    meta = {"fixture": corpus.name, "provider": args.provider, "model": args.model, "top_k": args.top_k,
            "recipe": getattr(corpus, "recipe", None), "answerable": sum(r["answerable"] for r in rows),
            "unanswerable": sum(not r["answerable"] for r in rows),
            "git": bench._environment(corpus, "-", cache).get("git")}
    text = render(summary, meta)
    print(text)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "results.json").write_text(json.dumps({"meta": meta, "summary": summary, "rows": rows},
                                                          indent=1), encoding="utf-8")
        (args.out / "summary.md").write_text(text, encoding="utf-8")
        (args.out / "flags.md").write_text(flags_report(rows), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

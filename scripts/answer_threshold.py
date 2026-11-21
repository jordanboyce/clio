#!/usr/bin/env python
"""Measure how close a visitor's query must be to a published question
before a static site shows that question's answer (docs/STATIC_SEARCH.md,
"Answers written in advance").

    python scripts/answer_threshold.py                     # both browser models
    python scripts/answer_threshold.py --model all-MiniLM-L6-v2
    python scripts/answer_threshold.py --cache data/models/fastembed

Reads tests/fixtures/static_answers.json: published questions, paraphrases
of each, and queries that must match none (off-topic ones, and questions on
the same topics that were not published). For each model it embeds them as
Clio and the browser do (as queries), and prints, for each threshold, the
share of paraphrases that would show the right answer, the share showing a
wrong one, and the false-match rate on the unrelated queries. The threshold
it recommends is the lowest with no false match and no wrong answer on the
fixture, plus a margin; services/static_export/answers.py keeps the result.

Needs the model files on disk (a fastembed cache) and no network.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURE = ROOT / "tests" / "fixtures" / "static_answers.json"
MARGIN = 0.02


def embed(model: str, texts, cache: Path) -> np.ndarray:
    from services.embedder import FastEmbedService

    service = FastEmbedService(model, cache_dir=cache)
    vectors = np.asarray([service.embed_query(t) for t in texts], dtype=np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def measure(model: str, cache: Path, fixture: dict) -> dict:
    published = [p["q"] for p in fixture["published"]]
    paraphrases = [(i, t) for i, p in enumerate(fixture["published"]) for t in p["paraphrases"]]
    unrelated = fixture["unrelated"]
    q = embed(model, published, cache)
    para = embed(model, [t for _, t in paraphrases], cache)
    other = embed(model, unrelated, cache)

    para_scores = para @ q.T
    para_best = para_scores.argmax(axis=1)
    para_top = para_scores.max(axis=1)
    right = np.asarray([best == i for (i, _), best in zip(paraphrases, para_best)])
    other_top = (other @ q.T).max(axis=1)

    rows = []
    for t in np.round(np.arange(0.30, 0.96, 0.01), 2):
        shown = para_top >= t
        rows.append({"threshold": float(t), "paraphrase_right": float((shown & right).mean()),
                     "paraphrase_wrong": float((shown & ~right).mean()),
                     "false_match": float((other_top >= t).mean())})
    # Lowest threshold with no false match and no wrong answer on the fixture, plus a margin.
    worst_unrelated = float(other_top.max())
    worst_wrong = float(para_top[~right].max()) if (~right).any() else -1.0
    chosen = round(max(worst_unrelated, worst_wrong) + MARGIN, 2)
    at = {"threshold": chosen, "paraphrase_right": float(((para_top >= chosen) & right).mean()),
          "paraphrase_wrong": float(((para_top >= chosen) & ~right).mean()),
          "false_match": float((other_top >= chosen).mean())}
    hardest = sorted(zip(other_top.tolist(), unrelated), reverse=True)[:5]
    return {"model": model, "published": len(published), "paraphrases": len(paraphrases),
            "unrelated": len(unrelated), "recommended": at, "hardest_unrelated": hardest, "table": rows}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", action="append", help="a Clio model name (default: both browser models)")
    parser.add_argument("--cache", type=Path, default=ROOT / "data" / "models" / "fastembed")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args(argv)
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    models = args.model or ["all-MiniLM-L6-v2", "BAAI/bge-small-en-v1.5"]
    results = [measure(m, args.cache, fixture) for m in models]
    if args.json:
        print(json.dumps(results, indent=2))
        return 0
    for r in results:
        rec = r["recommended"]
        print(f"{r['model']}: {r['published']} questions, {r['paraphrases']} paraphrases, {r['unrelated']} unrelated")
        print(f"  recommended threshold {rec['threshold']:.2f}: paraphrases answered right "
              f"{rec['paraphrase_right']:.0%}, wrongly {rec['paraphrase_wrong']:.0%}; "
              f"false matches {rec['false_match']:.0%}")
        print("  closest unrelated: " + "; ".join(f"{s:.3f} {t}" for s, t in r["hardest_unrelated"]))
        for row in r["table"]:
            if abs(row["threshold"] * 100 % 5) < 1e-6:
                print(f"    {row['threshold']:.2f}  right {row['paraphrase_right']:.0%}  "
                      f"wrong {row['paraphrase_wrong']:.0%}  false {row['false_match']:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

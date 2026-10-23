"""The retrieval benchmark (scripts/retrieval_bench.py) under pytest.

The fixture check always runs: it is cheap and keeps the labels honest. The
benchmark itself loads two local models and takes about a minute, so it is
skipped unless ``CLIO_RETRIEVAL_BENCH=1`` is set:

    CLIO_RETRIEVAL_BENCH=1 venv/Scripts/python -m pytest tests/benchmark -s
"""

import importlib.util
import os
import pathlib
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "retrieval_bench.py"
_spec = importlib.util.spec_from_file_location("retrieval_bench", _SCRIPT)
bench = importlib.util.module_from_spec(_spec)
# Registered before executing: the module's dataclasses resolve their
# postponed annotations through sys.modules.
sys.modules[_spec.name] = bench
_spec.loader.exec_module(bench)

RUN_BENCHMARK = os.environ.get("CLIO_RETRIEVAL_BENCH", "").strip().lower() in ("1", "true", "yes")


def test_fixture_is_consistent():
    corpus = bench.load_corpus()
    assert bench.validate_corpus(corpus) == []
    assert 30 <= len(corpus.documents) <= 60
    assert 40 <= len(corpus.queries) <= 80
    # Every passage is one chunk, so a label like "doc#2" has a stable target.
    assert corpus.resolve("err-codes-importer#2") == ["err-codes-importer#2"]
    assert corpus.resolve("err-codes-importer") == [
        "err-codes-importer#1", "err-codes-importer#2", "err-codes-importer#3",
    ]


def test_scoring_ranks_each_labelled_unit_once():
    corpus = bench.load_corpus()
    query = {"relevant": ["err-codes-importer#2", "eng-config-example"]}
    ranks = bench.score_results(
        corpus, query, ["glossary#1", "eng-config-example#1", "err-codes-importer#1", "err-codes-importer#2"],
    )
    assert ranks == [4, 2]
    outcome = bench.QueryOutcome("q", "answerable", ranks, units=2, latency_ms=1.0,
                                 reranked=True, top1_score=0.5)
    assert outcome.recall_at(5) == 1.0
    assert outcome.recall_at(3) == 0.5
    assert outcome.reciprocal_rank == 0.5
    assert bench.percentile([10, 20, 30, 40], 0.5) == 25


@pytest.mark.skipif(not RUN_BENCHMARK, reason="set CLIO_RETRIEVAL_BENCH=1 to run the retrieval benchmark")
def test_retrieval_benchmark(tmp_path):
    report = bench.run_benchmark(index_dir=tmp_path, log=print)
    print(bench.render_markdown(report))
    names = [c.name for c in report.sequential]
    assert names == ["no reranker", "reranker x2", "reranker x3", "reranker x5"]
    assert len(report.concurrent) == 2
    # Regression floors, not targets: the values measured when the benchmark
    # landed were well above these (see docs/ROADMAP.md and the PR).
    baseline = report.sequential[0]
    assert baseline.recall(10) >= 0.80
    for c in report.sequential[1:]:
        assert c.recall(10) >= baseline.recall(10) - 0.05, c.name
    gated = report.concurrent[1]
    assert gated.settings["rerank_max_concurrent"] == 2
    assert report.total_seconds < 600

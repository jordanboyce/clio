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


# ── The quality-system fixture (aerospace supplier pilot) ───────────────────

QUALITY_FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "quality_bench"


def test_quality_fixture_is_consistent():
    corpus = bench.load_corpus(QUALITY_FIXTURE)
    assert bench.validate_corpus(corpus) == []
    assert corpus.name == "quality_bench"
    assert "revision" in corpus.categories
    # Every revision query competes with an older text that states another value.
    revision = [q for q in corpus.queries if q["category"] == "revision"]
    assert len(revision) >= 6 and all(q["stale"] for q in revision)
    assert corpus.supersede_marks == {"qp-87-rev-c": "qp-87-rev-d", "wi-2140-rev-f": "wi-2140-rev-g"}


def test_validation_catches_bad_revision_labels():
    corpus = bench.load_corpus(QUALITY_FIXTURE)
    corpus.documents[0] = {**corpus.documents[0], "superseded_by": "no-such-doc"}
    corpus.queries[0] = {**corpus.queries[0], "stale": list(corpus.queries[0]["relevant"])}
    problems = "\n".join(bench.validate_corpus(corpus))
    assert "superseded_by 'no-such-doc' is not a document" in problems
    assert "labelled both relevant and stale" in problems


def test_revision_safety_counts_stale_above_current():
    def outcome(hit_ranks, stale_rank):
        return bench.QueryOutcome("q", "revision", hit_ranks, units=1, latency_ms=1.0, reranked=False,
                                  top1_score=None, stale_rank=stale_rank, has_stale=True)

    assert outcome([1], 2).revision_safe is True
    assert outcome([3], None).revision_safe is True
    assert outcome([2], 1).revision_safe is False
    assert outcome([], None).revision_safe is False      # current text not retrieved at all
    config = bench.ConfigResult("c", {}, outcomes=[outcome([1], 2), outcome([2], 1)])
    assert config.revision_safety == 0.5 and config.stale_top1 == 0.5
    plain = bench.QueryOutcome("q", "answerable", [1], 1, 1.0, False, None)
    assert plain.revision_safe is None


class _HashEmbedder:
    """Deterministic bag-of-words vectors: enough to run the real search path
    offline, not a stand-in for a real model's quality."""

    model_name = "test-hash-embedder"
    embedding_dim = 256

    def _vec(self, text):
        import re

        import numpy as np
        v = np.zeros(self.embedding_dim, dtype="float32")
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            v[hash(word) % self.embedding_dim] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_texts(self, texts, **_):
        import numpy as np
        return np.stack([self._vec(t) for t in texts])

    def embed_query(self, text, **_):
        return self._vec(text)


def test_supersede_marks_reach_search_and_the_report(tmp_path):
    corpus = bench.load_corpus(QUALITY_FIXTURE)
    indexer = bench.build_index(corpus, tmp_path / "index", tmp_path, embedder=_HashEmbedder())
    info = indexer.vector_store.metadata_store.get_supersede_info(["qp-87-rev-c", "qp-87-rev-d"])
    assert info["qp-87-rev-c"]["document_id"] == "qp-87-rev-d"
    assert "qp-87-rev-d" not in info

    honoured = bench.run_config(indexer, corpus, "marks honoured", top_k=10, repeats=1, parallel=1,
                                enable_reranker=False)
    ignored = bench.run_config(indexer, corpus, "marks ignored", top_k=10, repeats=1, parallel=1,
                               enable_reranker=False, superseded_penalty=0.0)
    assert len(honoured.revision_outcomes) == len([q for q in corpus.queries if q.get("stale")])
    # Demoting replaced documents can only move stale passages down.
    assert honoured.revision_safety >= ignored.revision_safety
    report = bench.BenchmarkReport(environment={
        "fixture": corpus.name, "corpus_version": 1, "queries_version": 1, "documents": 16,
        "chunks": 51, "queries": len(corpus.queries), "embedding_model": "stub", "reranker_model": "-",
        "reranker_backend": "none", "cpus": 1, "platform": "-", "python": "-", "top_k": 10,
    }, sequential=[honoured, ignored], concurrent=[], total_seconds=1.0)
    markdown = bench.render_markdown(report)
    assert "Revision safety" in markdown and "| marks ignored |" in markdown
    # The fixture's own categories head the per-category tables.
    assert "| revision |" in markdown.split("Recall@5 by query category")[1]


@pytest.mark.skipif(not RUN_BENCHMARK, reason="set CLIO_RETRIEVAL_BENCH=1 to run the retrieval benchmark")
def test_quality_benchmark(tmp_path):
    report = bench.run_benchmark(index_dir=tmp_path, fixture_dir=QUALITY_FIXTURE, multipliers=(3,),
                                 skip_concurrency=True, log=print)
    print(bench.render_markdown(report))
    by_name = {c.name: c for c in report.sequential}
    assert set(by_name) == {"no reranker", "reranker x3", "no reranker, marks ignored",
                            "reranker x3, marks ignored"}
    # Recording "Replaced by" must never make revision safety worse.
    assert by_name["no reranker"].revision_safety >= by_name["no reranker, marks ignored"].revision_safety
    assert by_name["reranker x3"].revision_safety >= by_name["reranker x3, marks ignored"].revision_safety


# ── The hard fixture (A1): retrieval that misses ─────────────────────────────

HARD_FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "retrieval_hard"


def test_hard_fixture_is_consistent_and_regenerates_byte_for_byte(tmp_path, monkeypatch):
    corpus = bench.load_corpus(HARD_FIXTURE)
    assert bench.validate_corpus(corpus) == []
    assert corpus.collections == ["engineering", "facilities", "finance", "it"]
    assert len(corpus.documents) >= 300
    cats = {q["category"] for q in corpus.queries}
    assert {"near_duplicate", "late_page", "cross_page", "cross_collection", "revision"} <= cats

    spec = importlib.util.spec_from_file_location(
        "make_retrieval_corpus", pathlib.Path(bench.REPO_ROOT) / "scripts" / "make_retrieval_corpus.py")
    gen = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = gen
    spec.loader.exec_module(gen)
    monkeypatch.setattr(gen, "OUT", tmp_path)
    gen.main()
    # Line endings aside: a checkout may have either.
    for name in ("corpus.json", "queries.json"):
        made, kept = ((p / name).read_bytes().replace(b"\r\n", b"\n") for p in (tmp_path, HARD_FIXTURE))
        assert made == kept, name


def test_sentence_labels_follow_the_chunker(tmp_path):
    """A label names the sentence, so it resolves to whatever chunk holds it,
    and a sentence cut by a page break resolves to nothing until a chunk
    holds all of it."""
    corpus = bench.load_corpus(HARD_FIXTURE)
    indexers = bench.build_index(corpus, tmp_path / "index", tmp_path, embedder=_HashEmbedder())
    assert set(indexers) == set(corpus.collections)
    late = next(q for q in corpus.queries if q["category"] == "late_page")
    assert corpus.resolve(late["relevant"][0])
    split = [q for q in corpus.queries if q["category"] == "cross_page"]
    assert split and all(corpus.resolve(q["relevant"][0]) == [] for q in split)

    result = bench.run_config(indexers, corpus, "hash", top_k=10, repeats=1, parallel=1,
                              queries=[q for q in corpus.queries if q["category"] == "cross_collection"][:5],
                              enable_reranker=False)
    assert len(result.outcomes) == 5

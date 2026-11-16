"""The research metadata benchmark (scripts/research_bench.py) under pytest.

The label and matching checks always run: they are cheap, need no network
and keep the manifest honest. The benchmark itself indexes and analyzes real
files, so it is skipped unless asked for:

    CLIO_RESEARCH_BENCH=1     the committed smoke PDFs, offline only (~20 s)
    CLIO_RESEARCH_BENCH=full  the cached manifest files, offline and the
                              recorded online pass (--lookups replay); run
                              scripts/research_bench.py --download once first

    CLIO_RESEARCH_BENCH=1 venv/Scripts/python -m pytest tests/benchmark/test_research_bench.py -s
"""

import importlib.util
import os
import pathlib
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "research_bench.py"
_spec = importlib.util.spec_from_file_location("research_bench", _SCRIPT)
bench = importlib.util.module_from_spec(_spec)
# Registered before executing: the module's dataclasses resolve their
# postponed annotations through sys.modules.
sys.modules[_spec.name] = bench
_spec.loader.exec_module(bench)

MODE = os.environ.get("CLIO_RESEARCH_BENCH", "").strip().lower()


def test_manifest_is_consistent():
    manifest = bench.load_manifest()
    assert bench.validate(manifest) == []
    assert 40 <= len(manifest.entries) <= 60
    kinds = {e.kind for e in manifest.entries}
    assert set(bench.REQUIRED_KINDS) <= kinds
    assert any(e.columns == 2 for e in manifest.entries)
    assert any(e.derive for e in manifest.entries)          # an image-only scan
    assert any(not any(e.labels[i] for i in bench.IDENTIFIERS) for e in manifest.entries)
    # The live check's failure shapes are in the set.
    ids = {e.id for e in manifest.entries}
    assert {"arxiv-2212.10496", "arxiv-2312.10997", "acl-2023.acl-long.99"} <= ids


def test_smoke_set_is_consistent():
    smoke = bench.load_smoke()
    assert bench.validate(smoke) == []
    assert {e.kind for e in smoke.entries} >= {"preprint", "conference", "scanned", "book", "report"}


def test_matching_rules():
    # Titles: case, punctuation and accents do not matter; word boundaries do.
    assert bench.score_title("BERT: Pre-training of Deep Bidirectional Transformers",
                             "Bert — pre training of deep bidirectional transformers").correct
    assert bench.score_title("Rücklé", "Ruckle").correct
    lost = bench.score_title("Retrieval-AugmentedGeneration for Large Language Models",
                             "Retrieval-Augmented Generation for Large Language Models")
    assert not lost.correct and lost.near
    assert bench.title_pattern(lost) == "title: right letters, spaces lost or added"
    assert bench.score_title("Think Python", "Think Python: How to Think", ["Think Python"]).correct

    # Authors: F1 over family names.
    assert bench.family_name("Andreas Rücklé∗") == "ruckle"
    assert bench.family_name("Lindqvist, Tomas") == "lindqvist"
    assert bench.family_name("Martin Luther King Jr.") == "king"
    gold = ["Luyu Gao", "Xueguang Ma", "Jimmy Lin", "Jamie Callan"]
    merged = bench.score_authors(["LuyuGao XueguangMa JimmyLin JamieCallan"], gold)
    assert merged.f1 == 0 and bench.authors_pattern(merged) == "authors: several names run together as one"
    half = bench.score_authors(["L. Gao", "X. Ma"], gold)
    assert half.status == "partial" and abs(half.f1 - 2 / 3) < 1e-3
    assert bench.authors_pattern(half) == "authors: some authors missing"
    assert bench.score_authors([], []).correct
    assert not bench.score_authors(["Alan Turing"], []).correct
    assert bench.score_authors(["NIST"], [], [["NIST"]]).correct

    # Year: exact, or one of the accepted alternatives.
    assert bench.score_year(2015, 2014, [2015]).correct
    assert bench.score_year("2014", 2014).correct
    assert bench.score_year(2013, 2014).status == "wrong"

    # Identifiers: normalised; an invented one is counted apart from a miss.
    assert bench.score_identifier("arxiv_id", "arXiv:2212.10496v2", "2212.10496").correct
    assert bench.score_identifier("doi", "https://doi.org/10.1145/ABC", "10.1145/abc").correct
    assert bench.score_identifier("isbn", "0-306-40615-2", "978-0-306-40615-7").correct
    assert bench.score_identifier("doi", "10.1/x", None).status == "spurious"
    assert bench.score_identifier("doi", "10.48550/arXiv.1", None, ["10.48550/arxiv.1"]).status == "none_ok"
    assert bench.score_identifier("doi", None, "10.1/x").status == "missing"


@pytest.mark.skipif(MODE not in ("1", "true", "yes", "smoke", "full"),
                    reason="set CLIO_RESEARCH_BENCH=1 (smoke) or =full to run the research benchmark")
def test_research_benchmark():
    if MODE == "full":
        docset = bench.load_manifest()
        cache = pathlib.Path(os.environ.get("CLIO_RESEARCH_BENCH_CACHE") or bench.DEFAULT_CACHE)
        missing = bench.locate(docset, cache)
        if missing:
            pytest.skip(f"{len(missing)} manifest files are not cached; run scripts/research_bench.py --download")
        lookups = "replay" if bench.recording_path(cache, docset).exists() else "off"
        report = bench.run_benchmark(docset, cache=cache, lookups=lookups, log=print)
    else:
        report = bench.run_benchmark(bench.load_smoke(), lookups="off", log=print)
    print(bench.render_markdown(report))
    assert report.results and all("offline" in r.passes for r in report.results)
    # The offline pass may never reach a catalogue.
    assert all(s["ok"] == 0 for s in report.network["offline"].values())
    fields = bench.field_summary(report.results, "offline")
    assert fields["title"]["of"] == len(report.results)
    if MODE != "full":
        # The easy smoke document must come out right; if it does not, the
        # benchmark (or the analysis path) is broken, not merely inaccurate.
        clean = next(r for r in report.results if r.entry.id == "smoke-clean-article")
        assert all(s.correct for s in clean.passes["offline"]["scores"].values())

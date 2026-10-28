"""Per-collection retrieval coverage report, on real SQLite + the real indexer.

The collection is synthetic (tests/coverage_support.py) and the embedder is a
deterministic bag-of-words stand-in, so nothing downloads. Everything between
the sampled chunk and the stored report - probe derivation, DocumentIndexer.search,
quarantine, staleness, the HTTP gates - is the production code.
"""

import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import coverage_report as cr
from services import retrieval_metrics as rm
from tests.coverage_support import NOTICE, TOPICAL, build_collection


@pytest.fixture(autouse=True)
def _no_reranker(monkeypatch):
    """The production default for a box without the model; keeps ranks reproducible."""
    import config
    monkeypatch.setattr(config.settings, "enable_reranker", False, raising=False)


def _docs_with_unfindable():
    """Six topical documents that each end with the same boilerplate notice, plus a
    seventh document made of nothing but that notice."""
    docs = {"notice-copy.txt": list(NOTICE)}
    docs.update({name: list(texts) + NOTICE for name, texts in TOPICAL.items()})
    return docs


@pytest.fixture()
def indexer(tmp_path):
    """The notice-only document has no distinctive terms: its one chunk is word for
    word the notice every other document ends with, and a reviewer has pinned those
    copies (a real search-path intervention), so they outrank it for every query a
    chunk of its own would produce. Without a pin the identical chunks tie and the
    index's tie order decides, which is arbitrary but not something to build a
    test on."""
    ix = build_collection(tmp_path, _docs_with_unfindable())
    for n in range(2, 2 + len(TOPICAL)):
        ix.vector_store.metadata_store.set_chunk_boost(f"doc-{n}#4", 1.0, "reviewer pin")
    return ix


def _by_name(report):
    return {d["filename"]: d for d in report["documents"]}


# ── the metric helpers the benchmark shares ────────────────────────────

def test_rank_metrics():
    assert rm.first_rank({"b"}, ["a", "b", "c"]) == 2
    assert rm.first_rank({"z"}, ["a"]) is None
    assert rm.hit_at(3, 5) and not rm.hit_at(6, 5) and not rm.hit_at(None, 5)
    assert rm.reciprocal_rank(4) == 0.25 and rm.reciprocal_rank(None) == 0.0
    assert rm.mean([1, None, 3]) == 2 and rm.mean([]) is None
    assert rm.percentile([10, 20, 30, 40], 0.5) == 25


# ── sampling ───────────────────────────────────────────────────────────

def test_allocation_is_even_and_spills_to_big_documents():
    import random
    alloc = cr.allocate({"big": 500, "small": 2, "mid": 10}, 30, random.Random(0))
    assert alloc["small"] == 2                       # capped at its chunks
    assert alloc["mid"] == 10 and alloc["big"] == 18  # surplus goes to the one that can take it
    few = cr.allocate({f"d{i}": 5 for i in range(20)}, 7, random.Random(0))
    assert sum(few.values()) == 7 and max(few.values()) == 1   # more documents than probes


def test_sample_is_stratified_across_documents():
    refs = [(f"big#{i}", "big") for i in range(200)] + [(f"s{d}#{i}", f"s{d}") for d in range(5) for i in range(3)]
    picked = cr.sample_chunks(refs, 20, seed=3)
    by_doc = {}
    for _, d in picked:
        by_doc[d] = by_doc.get(d, 0) + 1
    assert len(picked) == 20
    assert all(by_doc[f"s{d}"] == 3 for d in range(5))   # every small document fully represented
    assert by_doc["big"] == 5                             # the big one does not take the budget


def test_sample_is_a_pure_function_of_seed():
    refs = [(f"d{d}#{i}", f"d{d}") for d in range(8) for i in range(10)]
    assert cr.sample_chunks(refs, 24, seed=7) == cr.sample_chunks(refs, 24, seed=7)
    assert cr.sample_chunks(refs, 24, seed=7) != cr.sample_chunks(refs, 24, seed=8)


# ── probe derivation ───────────────────────────────────────────────────

def test_heading_chunks_use_their_heading():
    text = ("Parental leave policy\n"
            "Employees may take up to twelve weeks of unpaid leave after the birth or adoption of a child.")
    assert cr.derive_query(text, cr.Counter(), cr.Counter(), 1) == ("Parental leave policy", "heading")


def test_keyword_probes_are_not_the_chunk_pasted_back():
    text = TOPICAL["hydroponics.txt"][0]
    df = cr.Counter(set(cr.content_words(text)))
    query, method = cr.derive_query(text, df, cr.Counter(), 1)
    assert method in ("keywords", "entities")
    assert query.lower() not in text.lower()          # no contiguous copy of the chunk
    assert 1 <= len(query.split()) <= 6
    assert not any(ch.isdigit() for ch in query)


def test_chunk_without_words_yields_no_probe():
    noise = "| --- | --- | --- |\n| 12 | 34 | 56 |\n| 78 | 90 | 12 |"
    assert cr.derive_query(noise, cr.Counter(), cr.Counter(), 1) is None


def test_probe_queries_are_deterministic_for_a_seed(indexer):
    real, seen = indexer.search, []

    def spy(query, *a, **k):
        if not k.get("_widen"):              # the duplicate-collapse backfill pass re-enters search
            seen.append(query)
        return real(query, *a, **k)

    indexer.search = spy

    def queries(seed):
        seen.clear()
        rep = cr.run_coverage(indexer, probes=12, seed=seed)
        return list(seen), rep

    a, rep_a = queries(5)
    b, rep_b = queries(5)
    c, _ = queries(6)
    assert a == b and len(a) == 12                       # same seed: the very same queries, in order
    assert a != c                                        # another seed samples other chunks
    assert rep_a["overall"] == {**rep_b["overall"], "latency_ms_p50": rep_a["overall"]["latency_ms_p50"],
                                "latency_ms_p95": rep_a["overall"]["latency_ms_p95"]}


# ── the report ─────────────────────────────────────────────────────────

def test_report_shape_and_honest_scope(indexer):
    rep = cr.run_coverage(indexer, probes=100, seed=1)
    assert "findability" in rep["measures"] and "not whether" in rep["measures"]
    assert rep["notes"][0] == rep["measures"]
    assert rep["corpus_version"] == indexer.vector_store.metadata_store.corpus_version()
    assert rep["embedding_model"] == "bag-of-words-test"
    assert rep["probe_method"]["requested"] == "heuristic" and rep["probe_method"]["llm"]["used"] is False
    assert rep["search"] == {"mode": "auto", "top_k": 10, "reranked_fraction": 0.0}
    o = rep["overall"]
    assert o["probes"] == 25                                      # fewer chunks than the budget: all of them
    assert o["document_hit_at_10"] >= o["document_hit_at_5"] and o["chunk_hit_at_10"] >= o["chunk_hit_at_5"]
    assert 0 < o["mrr_chunk"] <= 1 and o["latency_ms_p95"] >= o["latency_ms_p50"] >= 0
    assert rep["duration_seconds"] >= 0 and rep["truncated"] is False
    assert rep["documents_total"] == rep["documents_probed"] == 7


def test_unfindable_document_surfaces_as_the_weakest(indexer):
    rep = cr.run_coverage(indexer, probes=100, seed=1)
    docs = _by_name(rep)
    assert docs["notice-copy.txt"]["document_hit_at_5"] == 0.0
    assert all(docs[n]["document_hit_at_5"] >= 0.5 for n in TOPICAL)       # their own topics still retrieve
    weakest = rep["weakest_documents"]
    assert weakest[0]["filename"] == "notice-copy.txt"
    failing = weakest[0]["failing_probes"][0]
    assert failing["document_rank"] is None or failing["document_rank"] > 5
    assert "notice" in failing["query"]                                     # the probe an owner can read
    assert failing["preview"].startswith("This notice applies")


def test_chunks_never_retrieved_are_listed(indexer):
    rep = cr.run_coverage(indexer, probes=100, seed=1)
    never = rep["chunks_never_retrieved"]
    assert never["count"] == len(never["examples"])
    # every example really is outside the top 10 for its own probe
    from config import settings  # noqa: F401 - search path reads the live settings
    for ex in never["examples"]:
        got = indexer.search(ex["query"], top_k=10)["results"]
        assert ex["chunk_id"] not in [r.chunk_id for r in got]


def test_unprobeable_chunks_are_reported_not_counted_as_misses(tmp_path):
    docs = {"clean.txt": TOPICAL["sourdough.txt"],
            "grid.txt": ["| --- | --- |\n| 12 | 34 |\n| 56 | 78 |"]}
    rep = cr.run_coverage(build_collection(tmp_path, docs), probes=50, seed=1)
    assert rep["unprobeable_chunks"]["count"] == 1
    assert rep["unprobeable_chunks"]["examples"][0]["filename"] == "grid.txt"
    assert "grid.txt" not in _by_name(rep)                    # nothing to measure, so no hit rate
    assert rep["overall"]["probes"] == 3


def test_quarantined_documents_are_never_sampled_or_reported(tmp_path):
    docs = {"open.txt": TOPICAL["beekeeping.txt"], "held.txt": TOPICAL["canal-locks.txt"]}
    ix = build_collection(tmp_path, docs, quarantined={"held.txt"})
    refs = ix.vector_store.metadata_store.servable_chunk_refs()
    assert {d for _, d in refs} == {"doc-1"}
    rep = cr.run_coverage(ix, probes=100, seed=1)
    text = str(rep)
    assert "held.txt" not in text and "canal" not in text.lower() and "doc-2" not in text
    assert rep["documents_total"] == 1 and rep["overall"]["probes"] == 3


def test_empty_collection_degrades_to_an_empty_report(tmp_path):
    rep = cr.run_coverage(build_collection(tmp_path, {}), probes=100, seed=1)
    assert rep["overall"]["probes"] == 0 and rep["overall"]["document_hit_at_5"] is None
    assert rep["documents"] == [] and rep["weakest_documents"] == []


def test_probe_count_is_capped():
    assert cr.MAX_PROBES == 300 and cr.DEFAULT_PROBES == 100


def test_search_errors_are_counted_not_raised(tmp_path):
    ix = build_collection(tmp_path, {"a.txt": TOPICAL["sourdough.txt"]})
    ix.search = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    rep = cr.run_coverage(ix, probes=10, seed=1)
    assert rep["overall"]["probes"] == 0 and rep["overall"]["search_errors"] == 3
    assert any("failed" in n for n in rep["notes"])


def test_time_budget_truncates_and_says_so(tmp_path):
    ix = build_collection(tmp_path, {"a.txt": TOPICAL["sourdough.txt"] * 4})
    real = ix.search

    def slow(*a, **k):
        time.sleep(0.45)
        return real(*a, **k)

    ix.search = slow
    rep = cr.run_coverage(ix, probes=12, seed=1, time_budget_s=1.0)
    assert rep["truncated"] is True and 0 < rep["overall"]["probes"] < 12
    assert any("time budget" in n for n in rep["notes"])


# ── owner questions ────────────────────────────────────────────────────

def test_owner_questions_validate_their_documents(indexer, tmp_path):
    q = cr.add_question(indexer, "How do I keep a sourdough culture alive?", ["sourdough.txt"], "owner")
    assert q["expected_documents"] == [{"document_id": "doc-4", "filename": "sourdough.txt"}]
    by_id = cr.add_question(indexer, "When is a honey super added?", ["doc-6"], "owner")
    assert by_id["expected_documents"][0]["filename"] == "beekeeping.txt"
    for bad, expected in (("hi", ["doc-1"]), ("What is the weather like?", []),
                          ("What is the weather like?", ["nope.txt"])):
        with pytest.raises(cr.QuestionError):
            cr.add_question(indexer, bad, expected, "owner")


def test_owner_question_to_a_quarantined_document_is_refused(tmp_path):
    ix = build_collection(tmp_path, {"open.txt": TOPICAL["beekeeping.txt"],
                                     "held.txt": TOPICAL["canal-locks.txt"]}, quarantined={"held.txt"})
    with pytest.raises(cr.QuestionError):
        cr.add_question(ix, "How does a canal lock work?", ["held.txt"], "owner")
    with pytest.raises(cr.QuestionError):
        cr.add_question(ix, "How does a canal lock work?", ["doc-2"], "owner")


def test_owner_questions_are_reported_separately(tmp_path):
    indexer = build_collection(tmp_path, dict(TOPICAL))     # plain: no pinned notices to muddy a deliberate miss
    cr.add_question(indexer, "How do I test a telescope mirror for spherical aberration?",
                    ["telescope-optics.txt"], "owner")
    cr.add_question(indexer, "How often should the brood frames be inspected in spring?",
                    ["beekeeping.txt"], "owner")
    cr.add_question(indexer, "How long should a sourdough loaf bake in the Dutch oven?",
                    ["telescope-optics.txt"], "owner")                # wrong document on purpose: a gap
    rep = cr.run_coverage(indexer, probes=20, seed=1)
    owner = rep["owner_questions"]
    assert owner["total"] == owner["measured"] == 3
    status = {i["question"][:12]: i["status"] for i in owner["items"]}
    assert status["How do I tes"] == "hit" and status["How often sh"] == "hit"
    assert status["How long sho"] == "miss"
    assert owner["document_hit_at_5"] == pytest.approx(2 / 3, abs=1e-3)
    assert rep["overall"]["probes"] == 18            # generated probes (every chunk) are untouched by them


def test_owner_question_whose_document_was_deleted_is_not_counted(indexer):
    cr.add_question(indexer, "How do I feed a sourdough starter?", ["sourdough.txt"], "owner")
    store = indexer.vector_store.metadata_store
    indexer.delete_document("doc-4")
    rep = cr.run_coverage(indexer, probes=20, seed=1)
    item = rep["owner_questions"]["items"][0]
    assert item["status"] == "expected_document_unavailable"
    assert rep["owner_questions"]["measured"] == 0 and rep["owner_questions"]["document_hit_at_5"] is None
    assert store.list_coverage_questions()[0]["expected_documents"][0]["filename"] == "sourdough.txt"


# ── staleness ──────────────────────────────────────────────────────────

def test_stored_report_goes_stale_when_the_corpus_changes(indexer):
    store = indexer.vector_store.metadata_store
    assert cr.latest_report(indexer) is None
    store.save_coverage_report(cr.run_coverage(indexer, probes=10, seed=1))
    fresh = cr.latest_report(indexer)
    assert fresh["stale"] is False and fresh["stale_reasons"] == [] and fresh["age"].endswith("s")

    store.add_document("doc-99", "late.txt", 1, 1, "2026-06-01T00:00:00")
    stale = cr.latest_report(indexer)
    assert stale["stale"] is True and stale["stale_reasons"] == ["corpus_changed"]
    # the stored report itself is untouched - staleness is computed on read
    assert store.get_coverage_report()["corpus_version"] == fresh["corpus_version"]


def test_quarantining_a_document_also_marks_the_report_stale(indexer):
    store = indexer.vector_store.metadata_store
    store.save_coverage_report(cr.run_coverage(indexer, probes=10, seed=1))
    store.set_document_governance("doc-3", policy_status="quarantined")
    assert cr.latest_report(indexer)["stale"] is True


def test_changed_embedding_model_marks_the_report_stale(indexer):
    store = indexer.vector_store.metadata_store
    store.save_coverage_report(cr.run_coverage(indexer, probes=10, seed=1))
    indexer.embedding_service.model_name = "another-model"
    try:
        assert cr.latest_report(indexer)["stale_reasons"] == ["embedding_model_changed"]
    finally:
        indexer.embedding_service.model_name = "bag-of-words-test"


def test_age_label():
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert cr.age_label((now - timedelta(seconds=40)).isoformat(), now) == "40s"
    assert cr.age_label((now - timedelta(minutes=12)).isoformat(), now) == "12m"
    assert cr.age_label((now - timedelta(hours=5)).isoformat(), now) == "5h"
    assert cr.age_label((now - timedelta(days=3, hours=2)).isoformat(), now) == "3d"
    assert cr.age_label("garbage") is None and cr.age_label(None) is None


def test_additive_tables_heal_an_older_database(tmp_path):
    import sqlite3
    from services.metadata_store import MetadataStore
    ix = build_collection(tmp_path, {"a.txt": TOPICAL["sourdough.txt"]})
    db = ix.vector_store.metadata_db_path
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE coverage_report")
        conn.execute("DROP TABLE coverage_questions")
    # the cheap reader used by list_collections copes with the missing table...
    snap = MetadataStore.coverage_snapshot(db)
    assert snap["report"] is None and snap["newest_upload"]
    # ...and reopening the store recreates both with no version bump
    store = MetadataStore(db)
    assert store.get_coverage_report() is None and store.list_coverage_questions() == []
    assert store.get_schema_version() == ix.vector_store.metadata_store.get_schema_version()


# ── optional model-written questions ───────────────────────────────────

class _Provider:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, 0

    def complete(self, prompt, max_tokens=0, model=None):
        self.calls += 1
        if self.error:
            raise self.error
        return {"text": self.reply(prompt) if callable(self.reply) else self.reply}


def _echo_questions(prompt):
    import json, re
    nums = re.findall(r"^\[(\d+)\]$", prompt, re.M)
    return json.dumps([{"n": int(n), "question": f"What does the guide say about topic number {n}?"} for n in nums])


def test_model_questions_are_off_by_default_and_never_called(indexer):
    calls = []
    rep = cr.run_coverage(indexer, probes=10, seed=1, resolver=lambda **k: calls.append(1) or (None, None))
    assert calls == [] and rep["probe_method"]["requested"] == "heuristic"
    assert rep["probe_method"]["llm"] == {"requested": False, "used": False, "model": None,
                                          "error": None, "questions": 0}


def test_model_questions_replace_heuristic_ones_when_enabled(indexer):
    provider = _Provider(_echo_questions)
    rep = cr.run_coverage(indexer, probes=10, seed=1, use_llm=True, resolver=lambda **k: (provider, "m1"))
    pm = rep["probe_method"]
    assert pm["requested"] == "llm" and pm["llm"]["used"] is True and pm["llm"]["model"] == "m1"
    assert pm["by_method"].keys() == {"llm"} and provider.calls == 2      # 10 chunks, 8 per call


def test_model_failure_falls_back_to_heuristics_and_says_so(indexer):
    provider = _Provider(error=RuntimeError("endpoint unreachable"))
    rep = cr.run_coverage(indexer, probes=10, seed=1, use_llm=True, resolver=lambda **k: (provider, "m1"))
    assert rep["probe_method"]["llm"]["used"] is False
    assert rep["overall"]["probes"] == 10 and "llm" not in rep["probe_method"]["by_method"]
    assert any("heuristic probes ran instead" in n and "endpoint unreachable" in n for n in rep["notes"])


def test_unparseable_model_reply_falls_back(indexer):
    rep = cr.run_coverage(indexer, probes=6, seed=1, use_llm=True,
                          resolver=lambda **k: (_Provider("sorry, I cannot do that"), "m"))
    assert rep["probe_method"]["llm"]["used"] is False and rep["overall"]["probes"] == 6


def test_partial_model_answers_mix_with_heuristics(indexer):
    def only_first(prompt):
        return '[{"n": 1, "question": "What is the advice on this subject?"}]'

    rep = cr.run_coverage(indexer, probes=10, seed=1, use_llm=True,
                          resolver=lambda **k: (_Provider(only_first), "m"))
    assert set(rep["probe_method"]["by_method"]) >= {"llm"} and len(rep["probe_method"]["by_method"]) >= 2
    assert any("Some model questions were unavailable" in n for n in rep["notes"])


def test_offline_mode_refuses_a_cloud_provider_and_falls_back(indexer, monkeypatch):
    from services.app_database import app_db
    import config
    monkeypatch.setattr(app_db, "get_ai_preferences", lambda: {"default_provider": "anthropic"})
    monkeypatch.setattr(app_db, "get_agent_api_key", lambda p: "sk-test-not-real")
    monkeypatch.setattr(config.settings, "offline_mode", True)
    rep = cr.run_coverage(indexer, probes=8, seed=1, use_llm=True)       # real resolve_default_provider
    assert rep["probe_method"]["llm"]["used"] is False
    assert rep["overall"]["probes"] == 8 and any("heuristic probes ran instead" in n for n in rep["notes"])


def test_config_flag_defaults_off():
    import config
    assert config.Settings().coverage_llm_questions is False


# ── the runner ─────────────────────────────────────────────────────────

def test_background_run_persists_the_report(indexer):
    runner = cr.CoverageRunner()
    runner.start("c1", indexer, probes=10, seed=1)
    deadline = time.time() + 20
    while runner.is_running("c1") and time.time() < deadline:
        time.sleep(0.05)
    assert runner.status("c1")["state"] == "completed"
    assert indexer.vector_store.metadata_store.get_coverage_report()["overall"]["probes"] == 10


def test_second_run_for_the_same_collection_is_refused(indexer):
    gate, runner = threading.Event(), cr.CoverageRunner()
    real = indexer.search
    indexer.search = lambda *a, **k: (gate.wait(10), real(*a, **k))[1]
    runner.start("c1", indexer, probes=5, seed=1)
    try:
        with pytest.raises(cr.CoverageAlreadyRunning):
            runner.start("c1", indexer, probes=5, seed=1)
    finally:
        gate.set()
    deadline = time.time() + 20
    while runner.is_running("c1") and time.time() < deadline:
        time.sleep(0.05)
    runner.start("c1", indexer, probes=5, seed=1, blocking=True)       # free again once finished


def test_deployment_wide_cap_on_concurrent_runs(indexer):
    gate, runner = threading.Event(), cr.CoverageRunner()
    real = indexer.search
    indexer.search = lambda *a, **k: (gate.wait(10), real(*a, **k))[1]
    try:
        for i in range(cr.MAX_CONCURRENT_RUNS):
            runner.start(f"c{i}", indexer, probes=3, seed=1)
        with pytest.raises(cr.CoverageAlreadyRunning):
            runner.start("another", indexer, probes=3, seed=1)
    finally:
        gate.set()


def test_a_crashing_run_is_recorded_not_raised(indexer):
    runner = cr.CoverageRunner()
    indexer.vector_store.metadata_store.servable_chunk_refs = lambda: (_ for _ in ()).throw(RuntimeError("disk"))
    runner.start("c1", indexer, probes=5, blocking=True)
    st = runner.status("c1")
    assert st["state"] == "failed" and st["error"] == "disk"
    runner.start("c1", indexer, probes=5, blocking=True)               # a failed run does not wedge the collection


# ── HTTP: gates, 409, audit ────────────────────────────────────────────

@pytest.fixture()
def api(indexer, monkeypatch):
    import api.collections as col
    from api import deps
    from middleware.user_context import get_current_user_id
    from services import audit

    state = {"owner": True, "known": True}
    events = []

    def access(collection_id, user_id, write=False, owner=False):
        if collection_id != "docs" or not state["known"]:
            from fastapi import HTTPException
            raise HTTPException(404, "not found")
        if owner and not state["owner"]:
            from fastapi import HTTPException
            raise HTTPException(403, "owner only")
        return "owner" if state["owner"] else "read"

    monkeypatch.setattr(deps, "require_collection_access", access)
    monkeypatch.setattr(deps, "get_indexer", lambda cid: (access(cid, "u1"), indexer)[1])
    monkeypatch.setattr(cr, "coverage_runner", cr.CoverageRunner())
    monkeypatch.setattr(audit, "record", lambda action, **kw: events.append((action, kw)))
    app = FastAPI()
    app.include_router(col.router)
    app.dependency_overrides[get_current_user_id] = lambda: "u1"
    client = TestClient(app)
    client.state, client.events, client.indexer = state, events, indexer
    return client


def _wait_done(client):
    deadline = time.time() + 20
    while time.time() < deadline:
        body = client.get("/api/collections/docs/coverage").json()
        if body["run"]["state"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError("coverage run did not finish")


def test_get_before_any_run(api):
    body = api.get("/api/collections/docs/coverage").json()
    assert body["report"] is None and body["run"] == {"state": "idle"} and body["questions"] == []
    assert body["llm_questions"] is False


def test_run_then_read_the_report(api):
    r = api.post("/api/collections/docs/coverage", json={"probes": 15, "seed": 2})
    assert r.status_code == 202 and r.json()["run"]["state"] in ("running", "completed")
    body = _wait_done(api)
    assert body["run"]["state"] == "completed"
    assert body["report"]["overall"]["probes"] == 15 and body["report"]["stale"] is False
    assert body["report"]["probe_method"]["seed"] == 2
    assert ("coverage.run", {"actor": "u1", "collection_id": "docs", "detail": {"probes": 15, "seed": 2}}) in api.events


def test_run_without_a_body_uses_the_defaults(api):
    assert api.post("/api/collections/docs/coverage").status_code == 202
    assert _wait_done(api)["report"]["probe_method"]["requested_probes"] == cr.DEFAULT_PROBES


def test_run_rejects_out_of_range_probe_counts(api):
    assert api.post("/api/collections/docs/coverage", json={"probes": 301}).status_code == 422
    assert api.post("/api/collections/docs/coverage", json={"probes": 0}).status_code == 422


def test_concurrent_run_is_a_409(api):
    gate = threading.Event()
    real = api.indexer.search
    api.indexer.search = lambda *a, **k: (gate.wait(10), real(*a, **k))[1]
    try:
        assert api.post("/api/collections/docs/coverage", json={"probes": 5}).status_code == 202
        second = api.post("/api/collections/docs/coverage", json={"probes": 5})
        assert second.status_code == 409 and "already in progress" in second.json()["detail"]
        assert api.get("/api/collections/docs/coverage").json()["run"]["state"] == "running"
    finally:
        gate.set()
    assert _wait_done(api)["run"]["state"] == "completed"


def test_only_the_owner_may_run_or_edit_questions(api):
    api.state["owner"] = False
    assert api.post("/api/collections/docs/coverage").status_code == 403
    body = {"question": "How do I feed a starter?", "expected_documents": ["sourdough.txt"]}
    assert api.post("/api/collections/docs/coverage/questions", json=body).status_code == 403
    assert api.delete("/api/collections/docs/coverage/questions/1").status_code == 403
    assert api.get("/api/collections/docs/coverage").status_code == 200       # read access suffices
    assert api.events == []                                                   # nothing audited, nothing started


def test_unknown_collection_is_404_everywhere(api):
    for call in (lambda: api.get("/api/collections/other/coverage"),
                 lambda: api.post("/api/collections/other/coverage"),
                 lambda: api.post("/api/collections/other/coverage/questions",
                                  json={"question": "What?????", "expected_documents": ["x"]}),
                 lambda: api.delete("/api/collections/other/coverage/questions/1")):
        assert call().status_code == 404


def test_question_crud_is_audited(api):
    body = {"question": "How do I feed a sourdough starter?", "expected_documents": ["sourdough.txt"]}
    created = api.post("/api/collections/docs/coverage/questions", json=body)
    assert created.status_code == 201
    qid = created.json()["id"]
    listed = api.get("/api/collections/docs/coverage").json()["questions"]
    assert [q["id"] for q in listed] == [qid] and listed[0]["expected_documents"][0]["filename"] == "sourdough.txt"
    assert api.delete(f"/api/collections/docs/coverage/questions/{qid}").json() == {"deleted": qid}
    assert api.delete(f"/api/collections/docs/coverage/questions/{qid}").status_code == 404
    actions = [a for a, _ in api.events]
    assert actions == ["coverage.question_add", "coverage.question_delete"]
    assert api.events[0][1]["detail"]["documents"] == ["doc-4"]


def test_bad_question_is_a_422_with_a_reason(api):
    r = api.post("/api/collections/docs/coverage/questions",
                 json={"question": "Where is the roadmap?", "expected_documents": ["missing.txt"]})
    assert r.status_code == 422 and "missing.txt" in r.json()["detail"]
    assert api.post("/api/collections/docs/coverage/questions",
                    json={"question": "Where is the roadmap?", "expected_documents": []}).status_code == 422


def test_audit_vocabulary_lists_the_new_actions():
    from services import audit
    assert {"coverage.run", "coverage.question_add", "coverage.question_delete"} <= set(audit.ACTIONS)


# ── agent-facing summary ───────────────────────────────────────────────

def test_quality_summary_is_empty_without_a_report_but_reports_freshness(indexer):
    out = cr.quality_summary(indexer.vector_store.metadata_db_path)
    assert out == {"newest_upload": "2026-04-07T09:00:00"}


def test_quality_summary_after_a_run_and_after_a_change(indexer):
    store = indexer.vector_store.metadata_store
    store.save_coverage_report(cr.run_coverage(indexer, probes=100, seed=1))
    q = cr.quality_summary(indexer.vector_store.metadata_db_path)["quality"]
    assert set(q) == {"coverage_hit_at_5", "report_age", "stale"}
    assert q["stale"] is False and 0 < q["coverage_hit_at_5"] < 1
    store.add_document("doc-99", "late.txt", 1, 1, "2026-09-09T00:00:00")
    out = cr.quality_summary(indexer.vector_store.metadata_db_path)
    assert out["quality"]["stale"] is True and out["newest_upload"] == "2026-09-09T00:00:00"


def test_quality_summary_carries_the_owner_signal_when_present(indexer):
    cr.add_question(indexer, "How do I test a telescope mirror?", ["telescope-optics.txt"], "o")
    indexer.vector_store.metadata_store.save_coverage_report(cr.run_coverage(indexer, probes=10, seed=1))
    q = cr.quality_summary(indexer.vector_store.metadata_db_path)["quality"]
    assert q["owner_question_hit_at_5"] == 1.0


def test_quality_summary_never_raises(tmp_path):
    assert cr.quality_summary(tmp_path / "missing.db") == {}
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database")
    assert cr.quality_summary(junk) == {}


def test_newest_upload_ignores_quarantined_documents(tmp_path):
    ix = build_collection(tmp_path, {"old.txt": TOPICAL["beekeeping.txt"], "held.txt": TOPICAL["canal-locks.txt"]},
                          quarantined={"held.txt"})
    assert cr.quality_summary(ix.vector_store.metadata_db_path)["newest_upload"] == "2026-04-01T09:00:00"


def test_mcp_list_collections_carries_quality_and_keeps_its_keys(indexer, monkeypatch):
    from services import mcp_server

    db = indexer.vector_store.metadata_db_path
    monkeypatch.setattr(mcp_server, "_visible_collections",
                        lambda: [{"id": "docs", "name": "Docs", "description": "d", "document_count": 7}])
    monkeypatch.setattr(mcp_server.collection_service, "get_indexes_path", lambda cid: db.parent)

    before = mcp_server.list_collections()["collections"][0]
    assert {"collection_id", "name", "description", "document_count", "is_default"} <= set(before)
    assert before["newest_upload"] == "2026-04-07T09:00:00" and "quality" not in before

    indexer.vector_store.metadata_store.save_coverage_report(cr.run_coverage(indexer, probes=50, seed=1))
    after = mcp_server.list_collections()["collections"][0]
    assert after["quality"]["stale"] is False and 0 <= after["quality"]["coverage_hit_at_5"] <= 1
    assert {k: after[k] for k in before if k not in ("quality",)} == before   # nothing else moved

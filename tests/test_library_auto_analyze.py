"""New papers are analysed after indexing, without a click (work order A4,
roadmap R5), and a blocked catalogue does not hold that up (A6, R6)."""

import time

import httpx
import pytest

import config
from services.research import auto, jobs, library, lookup
from tests.coverage_support import build_collection
from tests.test_research import PAPER_A, PAPER_B


@pytest.fixture()
def collection(tmp_path, monkeypatch):
    from services.collection_service import collection_service
    from services.indexer_manager import indexer_manager

    docs_dir = tmp_path / "documents"
    docs_dir.mkdir()
    monkeypatch.setattr(collection_service, "get_documents_path", lambda cid: docs_dir)
    ix = build_collection(tmp_path, {"paper-a.pdf": PAPER_A, "2018-download.pdf": PAPER_B})
    ix.document_extractor.extract_text.side_effect = OSError("no file in tests")
    monkeypatch.setattr(indexer_manager, "get_indexer", lambda cid: ix)
    monkeypatch.setattr(config.settings, "library_auto_analyze", True, raising=False)
    monkeypatch.setattr(config.settings, "research_lookup_enabled", False, raising=False)
    return ix


def _ids(ix):
    return [d["document_id"] for d in ix.list_documents()]


def _wait(started):
    for job in started:
        jobs.wait_for(job["job_id"], timeout=30)


def test_indexed_papers_are_analysed_without_a_click(collection):
    started = auto.analyze_after_index("c1", _ids(collection))
    assert len(started) == 1 and started[0]["kind"] == "analyze"
    _wait(started)
    store = library.research_store(collection)
    papers = store.get_papers(_ids(collection))
    analysed = {d["filename"] for d in collection.list_documents() if (papers.get(d["document_id"]) or {}).get("analyzed_at")}
    assert {"paper-a.pdf", "2018-download.pdf"} <= analysed
    job = store.get_job(started[0]["job_id"])
    assert job["status"] == "done" and job["created_by"] == auto.AUTOMATIC


def test_already_analysed_papers_are_left_alone(collection):
    _wait(auto.analyze_after_index("c1", _ids(collection)))
    assert auto.analyze_after_index("c1", _ids(collection)) == []


def test_the_setting_turns_it_off(collection, monkeypatch):
    monkeypatch.setattr(config.settings, "library_auto_analyze", False, raising=False)
    assert auto.analyze_after_index("c1", _ids(collection)) == []


def test_a_failure_to_queue_is_never_raised(monkeypatch):
    from services.indexer_manager import indexer_manager

    monkeypatch.setattr(config.settings, "library_auto_analyze", True, raising=False)

    def broken(_cid):
        raise RuntimeError("store is gone")
    monkeypatch.setattr(indexer_manager, "get_indexer", broken)
    assert auto.analyze_after_index("c1", ["d1"]) == []


def test_blocked_catalogues_cost_one_timeout_per_host_not_one_per_source(collection, monkeypatch):
    """A6: with every catalogue dropping packets, analysis finishes from the
    sources' own text and each host is tried once."""
    monkeypatch.setattr(config.settings, "research_lookup_enabled", True, raising=False)
    monkeypatch.setattr(config.settings, "offline_mode", False, raising=False)
    asked = []

    def handler(request):
        asked.append(request.url.host)
        raise httpx.ConnectTimeout("dropped", request=request)
    monkeypatch.setattr(lookup, "_transport", httpx.MockTransport(handler))
    lookup.clear_cache()

    started = time.perf_counter()
    for _ in range(3):  # three rounds of the same sources: only the first asks
        for document_id in _ids(collection):
            doc = collection.vector_store.metadata_store.get_document_info(document_id)
            if library.is_paper(doc):
                library.analyze_document("c1", collection, document_id, use_registry=True)
    assert len(asked) == len(set(asked))          # each host at most once
    assert set(lookup.unreachable_hosts()) == set(asked)
    assert time.perf_counter() - started < 20
    note = library.research_store(collection).get_papers(_ids(collection))
    assert any("could not be reached" in " ".join(p.get("analysis_notes") or []) for p in note.values())


# ── the API: one notice, Try again, Find open copies as a job (A6, R7) ──

@pytest.fixture()
def client(collection, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api import research as api

    monkeypatch.setattr(api, "get_indexer", lambda cid: collection)
    monkeypatch.setattr(api, "require_collection_access", lambda *a, **k: "owner")
    monkeypatch.setattr(api.governance, "require_aup", lambda user: None)
    monkeypatch.setattr(api.data_controls, "holds_local_only", lambda cid: False)
    monkeypatch.setattr(config.settings, "research_lookup_enabled", True, raising=False)
    monkeypatch.setattr(config.settings, "offline_mode", False, raising=False)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def _blocked(monkeypatch):
    def handler(request):
        raise httpx.ConnectTimeout("dropped", request=request)
    monkeypatch.setattr(lookup, "_transport", httpx.MockTransport(handler))
    lookup.clear_cache()


def test_the_library_says_once_that_the_catalogues_were_unreachable_and_can_try_again(client, collection, monkeypatch):
    _blocked(monkeypatch)
    for document_id in _ids(collection):
        library.analyze_document("c1", collection, document_id, use_registry=True)
    listed = client.get("/api/research/papers?collection_id=c1").json()
    assert listed["stats"]["catalogue_unreachable"] >= 1
    assert listed["capabilities"]["catalogues_unreachable"]

    # Back online; Try again clears the rest and reads those sources again.
    monkeypatch.setattr(lookup, "_transport", httpx.MockTransport(lambda request: httpx.Response(404)))
    out = client.post("/api/research/lookups/retry?collection_id=c1")
    assert out.status_code == 202 and out.json()["job"]["kind"] == "analyze"
    jobs.wait_for(out.json()["job"]["job_id"], timeout=30)
    listed = client.get("/api/research/papers?collection_id=c1").json()
    assert listed["stats"]["catalogue_unreachable"] == 0 and not listed["capabilities"]["catalogues_unreachable"]


def test_find_open_copies_is_a_job_counted_per_reference(client, collection, monkeypatch):
    for document_id in _ids(collection):
        library.analyze_document("c1", collection, document_id, use_registry=False)
    paper = next(d for d in collection.list_documents() if d["filename"] == "2018-download.pdf")
    refs = [r for r in library.research_store(collection).list_references(paper["document_id"])
            if r["kind"] == "reference"]
    _blocked(monkeypatch)
    out = client.post(f"/api/research/papers/{paper['document_id']}/find-copies?collection_id=c1")
    assert out.status_code == 202
    job = out.json()["job"]
    assert job["kind"] == "resolve" and job["total"] == len(refs)
    jobs.wait_for(job["job_id"], timeout=30)
    done = library.research_store(collection).get_job(job["job_id"])
    # Every lookup failed fast (one timeout, then the host rests) and says so.
    assert done["failed"] == len(refs) and done["status"] == "failed"
    assert "not asked" in done["errors"][-1]["error"] or "could not be reached" in done["errors"][-1]["error"]

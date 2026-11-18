"""Research failures are never passed off as results.

Each test pins one way the Research tab used to be silently wrong: a big
"Analyze" that answered 500, a screening job whose model failed but read
"Done", a job with failures that read plain "Done", and a catalogue that
was down being remembered (and saved) as having no record. Catalogue
answers come from an ``httpx.MockTransport``; nothing touches the network.
"""

import httpx
import pytest

import config
from services.research import jobs, library, lookup
from services.research.store import ResearchStore
from tests.test_research import (  # noqa: F401 - fixtures used by name
    FakeProvider, _openalex_work, _quiet, client, docs_dir, indexer,
)


def _store(tmp_path):
    from services.metadata_store import MetadataStore
    MetadataStore(tmp_path / "m.db")
    return ResearchStore(tmp_path / "m.db")


# ── F4: analyzing more sources than one job may cover ───────────────────

def test_analyze_more_sources_than_one_job_covers_splits_the_work(client, monkeypatch):
    monkeypatch.setattr(config.settings, "library_max_job_documents", 2)
    response = client.post("/api/research/analyze?collection_id=c1", json={})
    assert response.status_code == 202, response.text
    out = response.json()
    assert len(out["jobs"]) == 2 and out["job"] == out["jobs"][0]       # older clients follow "job"
    assert [j["total"] for j in out["jobs"]] == [2, 1]
    assert [j["params"]["part"] for j in out["jobs"]] == [1, 2]
    for job in out["jobs"]:
        jobs.wait_for(job["job_id"])
    assert client.get("/api/research/papers?collection_id=c1").json()["stats"]["analyzed"] == 3
    listed = {j["job_id"]: j for j in client.get("/api/research/jobs?collection_id=c1").json()["jobs"]}
    assert all(listed[j["job_id"]]["status"] == "done" for j in out["jobs"])


def test_analyze_that_cannot_start_is_a_400_not_a_500(client, monkeypatch):
    def refuse(**kwargs):
        raise ValueError("A job can be scheduled at most 48 hours ahead.")
    monkeypatch.setattr(client.api.jobs_service, "start_job", refuse)
    response = client.post("/api/research/analyze?collection_id=c1", json={})
    assert response.status_code == 400 and "48 hours" in response.json()["detail"]


# ── F7: a job's end state says how it went ──────────────────────────────

def _run(store, docs, work, **kw):
    job = jobs.start_job(collection_id="c", store=store, kind="brief", documents=docs, work=work, params={}, **kw)
    jobs.wait_for(job["job_id"])
    return store.get_job(job["job_id"])


def _fail_on(*bad):
    def work(doc, job_id):
        if doc["document_id"] in bad:
            raise RuntimeError(f"could not read {doc['document_id']}")
    return work


def test_one_success_among_many_failures_is_done_with_errors_not_done(tmp_path):
    store = _store(tmp_path)
    docs = [{"document_id": f"d{i}"} for i in range(200)]
    final = _run(store, docs, _fail_on(*[f"d{i}" for i in range(1, 200)]))
    assert final["status"] == "done_with_errors"
    assert (final["done"], final["failed"]) == (1, 199) and len(final["errors"]) == 199
    assert final["finished"] is True


def test_job_end_states(tmp_path):
    store = _store(tmp_path)
    docs = [{"document_id": "a"}, {"document_id": "b"}]
    clean = _run(store, docs, _fail_on())
    assert (clean["status"], clean["failed"], clean["finished"]) == ("done", 0, True)
    none_worked = _run(store, docs, _fail_on("a", "b"))
    assert (none_worked["status"], none_worked["failed"]) == ("failed", 2)
    assert set(jobs.FINISHED) == {"done", "done_with_errors", "failed", "cancelled"}


def test_a_failed_finishing_step_is_reported(tmp_path):
    store = _store(tmp_path)

    def finish():
        raise RuntimeError("library matching broke")
    final = _run(store, [{"document_id": "a"}], _fail_on(), finish=finish)
    assert final["status"] == "done_with_errors" and final["failed"] == 0
    assert "library matching broke" in final["errors"][-1]["error"]


def test_a_budget_stop_carries_its_counts_and_reason(tmp_path):
    store = _store(tmp_path)
    calls = {"n": 0}

    def budget():
        calls["n"] += 1
        return "Daily token budget reached" if calls["n"] > 2 else None
    final = _run(store, [{"document_id": d} for d in "abcd"], _fail_on("b"), budget_check=budget)
    assert final["status"] == "failed" and final["finished"] is True
    assert (final["done"], final["failed"], final["total"]) == (1, 1, 4)
    assert final["current"] == "Daily token budget reached"
    assert final["errors"][-1] == {"document_id": None, "filename": None, "error": "Daily token budget reached"}


def test_old_clients_still_see_a_job_with_errors_as_finished(tmp_path):
    # Every client so far decides "still going" with status in (queued, running).
    store = _store(tmp_path)
    final = _run(store, [{"document_id": "a"}, {"document_id": "b"}], _fail_on("b"))
    assert final["status"] not in ("queued", "running")
    assert jobs.clear_finished(store) == 1                       # and the store agrees


# ── F6: a screening job whose model fails ───────────────────────────────

def test_screening_job_with_a_failing_model_is_not_saved_as_done(client, monkeypatch):
    fake = FakeProvider(lambda prompt: "I cannot answer in JSON, sorry.")
    monkeypatch.setattr(client.api, "_provider", lambda *args: (fake, "fake-model"))
    recorded = []
    monkeypatch.setattr(client.api, "record_chat_usage", lambda *a, **k: recorded.append(k))
    started = client.post("/api/research/jobs?collection_id=c1", json={
        "kind": "assess", "question": "Does conductance predict survival?",
        "document_ids": ["doc-1", "doc-2"], "provider": "anthropic"}).json()
    jobs.wait_for(started["job"]["job_id"])
    job = client.get(f"/api/research/jobs/{started['job']['job_id']}?collection_id=c1").json()
    assert job["status"] == "failed" and (job["done"], job["failed"]) == (0, 2)
    assert all("could not be read" in e["error"] for e in job["errors"])
    # No "can't tell from the text" verdicts were filed for a call that failed.
    reading = client.get("/api/research/assessments?collection_id=c1&question=Does conductance predict survival?").json()
    assert reading["items"] == []
    assert len(recorded) == 2                                    # the spent tokens still count


def test_single_screening_with_a_failing_model_is_an_error(client, monkeypatch):
    fake = FakeProvider("not json")
    monkeypatch.setattr(client.api, "_provider", lambda *args: (fake, "fake-model"))
    monkeypatch.setattr(client.api, "record_chat_usage", lambda *a, **k: None)
    response = client.post("/api/research/papers/doc-1/assess?collection_id=c1",
                           json={"question": "Does conductance predict survival?", "provider": "anthropic"})
    assert response.status_code == 502 and "could not be read" in response.json()["detail"]


# ── F8: a catalogue that could not be asked is not "no record" ──────────

DOI = "10.5555/jie.2021.0042"


@pytest.fixture()
def catalogue(monkeypatch):
    """Answers registry requests with ``state["answer"](request)``; counts them."""
    monkeypatch.setattr(config.settings, "research_lookup_enabled", True)
    monkeypatch.setattr(config.settings, "offline_mode", False)
    state = {"calls": 0, "answer": lambda request: httpx.Response(404)}

    def handler(request):
        state["calls"] += 1
        return state["answer"](request)
    monkeypatch.setattr(lookup, "_transport", httpx.MockTransport(handler))
    lookup.clear_cache()
    yield state
    lookup.clear_cache()


def _timeout(request):
    raise httpx.ReadTimeout("timed out", request=request)


FAILURES = {
    "timeout": (_timeout, "did not answer"),
    "429": (lambda request: httpx.Response(429), "429"),
    "5xx": (lambda request: httpx.Response(503), "503"),
}


@pytest.mark.parametrize("kind", sorted(FAILURES))
def test_a_catalogue_failure_is_raised_and_never_cached(catalogue, kind):
    answer, said = FAILURES[kind]
    catalogue["answer"] = answer
    with pytest.raises(lookup.Unreachable, match=said):
        lookup.by_doi(DOI)
    asked = catalogue["calls"]
    assert asked == 2                                    # OpenAlex, then Crossref
    # Once the catalogue is back, the record is found: the failure was not remembered.
    catalogue["answer"] = lambda request: httpx.Response(200, json=_openalex_work("Root hydraulics", DOI))
    assert lookup.by_doi(DOI)["title"] == "Root hydraulics"


def test_a_real_not_found_is_cached(catalogue):
    assert lookup.by_doi(DOI) is None
    asked = catalogue["calls"]
    assert lookup.by_doi(DOI) is None and catalogue["calls"] == asked


def test_one_catalogue_down_and_the_other_without_a_record_is_not_a_miss(catalogue):
    catalogue["answer"] = lambda request: (httpx.Response(429) if request.url.host == "api.openalex.org"
                                           else httpx.Response(404))
    with pytest.raises(lookup.Unreachable, match="api.openalex.org"):
        lookup.by_doi(DOI)


def test_a_reply_that_is_not_json_is_a_failure(catalogue):
    catalogue["answer"] = lambda request: httpx.Response(200, text="<html>Blocked by proxy</html>")
    with pytest.raises(lookup.Unreachable, match="could not be read"):
        lookup.by_title("Stomatal closure under soil drying in oak seedlings", "x")


@pytest.mark.parametrize("kind", sorted(FAILURES))
def test_a_catalogue_failure_is_never_saved_on_a_reference(client, catalogue, kind):
    client.post("/api/research/papers/doc-1/analyze?collection_id=c1", json={"use_registry": False})
    catalogue["answer"] = FAILURES[kind][0]
    out = client.post("/api/research/references/resolve?collection_id=c1", json={"document_id": "doc-1"}).json()
    failed = [r for r in out["references"] if r.get("lookup_failed")]
    assert failed and out["lookup_failed"] == len(failed) and "could not be reached" in out["message"]
    saved = client.get("/api/research/papers/doc-1?collection_id=c1").json()["reference_list"]
    for ref in saved:
        assert ref.get("resolved_via") != "no open copy found"
        assert ref["status"] != "unavailable"


def test_a_real_not_found_is_saved_on_a_reference(client, catalogue):
    client.post("/api/research/papers/doc-1/analyze?collection_id=c1", json={"use_registry": False})
    out = client.post("/api/research/references/resolve?collection_id=c1", json={"document_id": "doc-1"}).json()
    assert out["lookup_failed"] == 0
    saved = client.get("/api/research/papers/doc-1?collection_id=c1").json()["reference_list"]
    assert any(r["status"] == "unavailable" for r in saved)


def test_download_says_a_reference_was_not_looked_up(client, catalogue, monkeypatch):
    client.post("/api/research/papers/doc-1/analyze?collection_id=c1", json={"use_registry": False})
    catalogue["answer"] = FAILURES["5xx"][0]
    monkeypatch.setattr("services.link_fetcher.link_indexing_available", lambda: True)
    monkeypatch.setattr("services.link_fetcher.validate_link", lambda url: url)
    from services.upload_service import upload_service
    monkeypatch.setattr(upload_service, "start_link_index", lambda urls, collection_id, uploaded_by=None: 5)
    monkeypatch.setattr(upload_service, "queue_position", lambda job_id: None)
    out = client.post("/api/research/references/download?collection_id=c1", json={"document_id": "doc-1"}).json()
    reasons = [s["reason"] for s in out["skipped"]]
    assert any(r.startswith("not looked up: the catalogue could not be reached") for r in reasons)


def test_analysis_says_the_catalogue_was_unreachable_not_that_it_has_no_record(indexer, catalogue):
    catalogue["answer"] = FAILURES["timeout"][0]
    paper = library.analyze_document("c1", indexer, "doc-1")
    notes = " ".join(paper["analysis_notes"])
    assert "could not be reached" in notes and "No catalogue record could be confirmed" not in notes
    assert paper["title"].startswith("Root Hydraulics")          # from the paper's own text

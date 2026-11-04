"""Saved artifacts: generated reports outlast the page, and stay honest about
their sources.

The collection is built on the real VectorStore / MetadataStore (invented
filler, tests/coverage_support.py). The chat loop is scripted: what is under
test is what happens around it, and nothing touches the network.
"""

import json
import sqlite3
import threading
import zipfile

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import config
from models.schemas import ChatMessage, ChatResponse, ChatSource
from services import artifact_jobs, artifact_store, artifacts as artifacts_service
from services import collection_bundle as cb
from services.app_database import SQLiteBackend, app_db
from services.artifact_store import ArtifactStore
from services.collection_service import collection_service
from services.indexer_manager import indexer_manager
from services.research.store import ResearchStore
from tests.coverage_support import build_collection
from tests.test_governance import FakeEmbedder

FILES = {"sourdough.txt": ["Feed the starter twice a day."], "bridges.txt": ["Inspect the girders yearly."]}


def _source(document_id, filename="sourdough.txt", page=1):
    return ChatSource(filename=filename, page_number=page, text_snippet="Feed the starter", similarity_score=0.9,
                      document_id=document_id, pdf_url="http://old-host/x.pdf", page_url="http://old-host/x.pdf#page=1")


def _answer(*sources, content="# Report\nFeed it [sourdough.txt]."):
    return ChatResponse(message=ChatMessage(role="assistant", content=content), sources=list(sources))


@pytest.fixture(autouse=True)
def _no_reranker(monkeypatch):
    monkeypatch.setattr(config.settings, "enable_reranker", False, raising=False)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from api import artifacts as api

    indexer = build_collection(tmp_path, FILES)
    monkeypatch.setattr(collection_service, "base_dir", tmp_path / "collections")
    state = {"write": True, "reviewer": True, "chat": lambda *a, **k: _answer(_source("doc-1")), "seen": []}

    def access(collection_id, user_id, write=False, owner=False):
        if collection_id != "c1":
            raise HTTPException(404, "not found")
        if (write or owner) and not state["write"]:
            raise HTTPException(403, "read only")
        return "owner" if state["write"] else "read"

    def reviewer(action=""):
        if not state["reviewer"]:
            raise HTTPException(403, "not an admin")

    def chat(chat_request, request, **kwargs):
        state["seen"].append(chat_request)
        return state["chat"](chat_request, request, **kwargs)

    monkeypatch.setattr(api, "get_indexer", lambda cid: (access(cid, None), indexer)[1])
    monkeypatch.setattr(api, "require_collection_access", access)
    monkeypatch.setattr(api, "require_admin", reviewer)
    monkeypatch.setattr(api, "chat_with_documents", chat)
    monkeypatch.setattr(api, "_provider", lambda *args: (object(), "fake-model"))
    monkeypatch.setattr(api, "_budget_or_429", lambda user: None)
    monkeypatch.setattr(artifact_store, "load_blocked_hashes", lambda: state.get("blocked", set()))
    app = FastAPI()
    app.include_router(api.router)
    c = TestClient(app)
    c.state = state
    c.indexer = indexer
    return c


def _document_ids(indexer):
    return {d["filename"]: d["document_id"] for d in indexer.list_documents()}


def _generate(client, wait=True, **body):
    response = client.post("/api/artifacts?collection_id=c1", json={"artifact_type": "faq", "provider": "x", **body})
    assert response.status_code == 202, response.text
    artifact = response.json()
    if wait:
        artifact_jobs.wait_for(artifact["id"])
    return artifact


def _set_status(indexer, document_id, column, value):
    with sqlite3.connect(indexer.vector_store.metadata_db_path) as conn:
        conn.execute(f"UPDATE documents SET {column} = ? WHERE document_id = ?", (value, document_id))


def test_a_generated_report_is_saved_and_can_be_reopened(client):
    doc = _document_ids(client.indexer)["sourdough.txt"]
    client.state["chat"] = lambda *a, **k: _answer(_source(doc))
    artifact = _generate(client, focus="  starter\n feeding ")
    assert artifact["status"] in ("queued", "running", "done")
    assert artifact["title"] == "FAQ: starter feeding"

    # A new visit reads it from the server: nothing depends on the first response.
    listed = client.get("/api/artifacts?collection_id=c1").json()
    assert [a["id"] for a in listed["artifacts"]] == [artifact["id"]]
    assert listed["artifacts"][0]["status"] == "done"
    assert "content" not in listed["artifacts"][0]

    detail = client.get(f"/api/artifacts/{artifact['id']}?collection_id=c1").json()
    assert detail["content"].startswith("# Report")
    assert detail["model"] == "fake-model" and detail["provider"] == "x"
    assert detail["source_changes"] == [] and detail["held"] is False


def test_citation_links_are_built_for_the_host_reading_them(client):
    doc = _document_ids(client.indexer)["sourdough.txt"]
    client.state["chat"] = lambda *a, **k: _answer(_source(doc))
    artifact = _generate(client)
    with sqlite3.connect(client.indexer.vector_store.metadata_db_path) as conn:
        stored = conn.execute("SELECT sources FROM artifacts").fetchone()[0]
    assert "old-host" not in stored
    source = client.get(f"/api/artifacts/{artifact['id']}?collection_id=c1").json()["sources"][0]
    assert source["page_url"] == f"http://testserver/documents/{doc}/pdf?collection_id=c1#page=1"


def test_a_failure_is_saved_with_its_reason(client):
    def boom(*args, **kwargs):
        raise HTTPException(502, "The provider refused the key")

    client.state["chat"] = boom
    artifact = _generate(client)
    listed = client.get("/api/artifacts?collection_id=c1").json()["artifacts"][0]
    assert listed["id"] == artifact["id"]
    assert listed["status"] == "failed" and listed["error"] == "The provider refused the key"


def test_running_out_of_budget_mid_queue_fails_cleanly(client):
    client.state["chat"] = lambda *a, **k: JSONResponse(status_code=429, content={"detail": "over"})
    _generate(client)
    listed = client.get("/api/artifacts?collection_id=c1").json()["artifacts"][0]
    assert listed["status"] == "failed" and "budget" in listed["error"]


def test_an_empty_answer_is_not_saved_as_a_report(client):
    client.state["chat"] = lambda *a, **k: _answer(content="   ")
    _generate(client)
    listed = client.get("/api/artifacts?collection_id=c1").json()["artifacts"][0]
    assert listed["status"] == "failed" and listed["error"] == artifact_jobs.EMPTY_REASON


def test_leaving_does_not_stop_generation_and_listing_shows_progress(client):
    release = threading.Event()

    def slow(*args, **kwargs):
        release.wait(10)
        return _answer()

    client.state["chat"] = slow
    artifact = _generate(client, wait=False)
    listed = client.get("/api/artifacts?collection_id=c1").json()["artifacts"][0]
    assert listed["status"] in ("queued", "running")
    release.set()
    artifact_jobs.wait_for(artifact["id"])
    assert client.get("/api/artifacts?collection_id=c1").json()["artifacts"][0]["status"] == "done"


def test_a_row_an_earlier_process_left_behind_reads_as_failed_without_a_write(client):
    store = ArtifactStore(client.indexer.vector_store.metadata_db_path)
    store.create("abcabcabcabc", artifact_type="faq", title="FAQ", status="running", created_at="2026-01-01T00:00:00+00:00")
    listed = client.get("/api/artifacts?collection_id=c1").json()["artifacts"][0]
    assert listed["status"] == "failed" and listed["error"] == artifact_jobs.RESTART_REASON
    assert store.get("abcabcabcabc")["status"] == "running"


def test_the_request_context_reaches_the_worker(client):
    """The chat loop reads the caller from a contextvar for access and usage."""
    from middleware.user_context import reset_request_user, set_request_user, _request_user_id

    seen = {}

    def chat(*args, **kwargs):
        seen["user"] = _request_user_id.get()
        return _answer()

    store = ArtifactStore(client.indexer.vector_store.metadata_db_path)
    token = set_request_user("ana@example.test")
    try:
        artifact = artifact_jobs.start(store, lambda: {"content": chat().message.content},
                                       artifact_type="faq", title="FAQ")
    finally:
        reset_request_user(token)
    artifact_jobs.wait_for(artifact["id"])
    assert seen["user"] == "ana@example.test"


def test_the_key_is_never_saved(client):
    artifact = _generate(client)
    client.post("/api/artifacts?collection_id=c1", json={"artifact_type": "faq", "provider": "x"},
                headers={"x-ai-key": "sk-secret-value"})
    artifact_jobs.wait_for(artifact["id"])
    for row in ArtifactStore(client.indexer.vector_store.metadata_db_path).list():
        artifact_jobs.wait_for(row["id"])
    raw = client.indexer.vector_store.metadata_db_path.read_bytes()
    assert b"sk-secret-value" not in raw


def test_only_this_collection_can_be_drawn_on(client):
    response = client.post("/api/artifacts?collection_id=c1",
                           json={"artifact_type": "faq", "provider": "x", "scope": "all"})
    assert response.status_code == 422


def test_selection_keeps_known_servable_sources_only(client):
    ids = _document_ids(client.indexer)
    _set_status(client.indexer, ids["bridges.txt"], "policy_status", "quarantined")
    _generate(client, document_ids=[ids["sourdough.txt"], ids["bridges.txt"], "nope", ids["sourdough.txt"]])
    assert client.state["seen"][-1].document_ids == [ids["sourdough.txt"]]

    response = client.post("/api/artifacts?collection_id=c1", json={
        "artifact_type": "faq", "provider": "x", "document_ids": [ids["bridges.txt"]]})
    assert response.status_code == 404


def test_changed_and_removed_sources_are_reported(client):
    ids = _document_ids(client.indexer)
    _set_status(client.indexer, ids["sourdough.txt"], "content_hash", "before")
    _set_status(client.indexer, ids["bridges.txt"], "content_hash", "bridges-hash")
    client.state["chat"] = lambda *a, **k: _answer(_source(ids["sourdough.txt"]),
                                                   _source(ids["bridges.txt"], "bridges.txt"))
    artifact = _generate(client)
    assert client.get("/api/artifacts?collection_id=c1").json()["artifacts"][0]["source_changes"] == []

    _set_status(client.indexer, ids["sourdough.txt"], "content_hash", "after")
    with sqlite3.connect(client.indexer.vector_store.metadata_db_path) as conn:
        conn.execute("DELETE FROM documents WHERE document_id = ?", (ids["bridges.txt"],))

    detail = client.get(f"/api/artifacts/{artifact['id']}?collection_id=c1").json()
    assert sorted((c["filename"], c["change"]) for c in detail["source_changes"]) == [
        ("bridges.txt", "removed"), ("sourdough.txt", "changed")]
    # The citation of a removed source stays readable but no longer links anywhere.
    links = {s["filename"]: s["page_url"] for s in detail["sources"]}
    assert links["bridges.txt"] == "" and links["sourdough.txt"].startswith("http://testserver/")


@pytest.mark.parametrize("hold", ["quarantined", "blocklisted"])
def test_a_held_source_withholds_the_artifact_from_all_but_reviewers(client, hold):
    doc = _document_ids(client.indexer)["sourdough.txt"]
    _set_status(client.indexer, doc, "content_hash", "hash-1")
    client.state["chat"] = lambda *a, **k: _answer(_source(doc))
    artifact = _generate(client)
    url = f"/api/artifacts/{artifact['id']}?collection_id=c1"
    assert client.get(url).status_code == 200

    if hold == "quarantined":
        _set_status(client.indexer, doc, "policy_status", "quarantined")
    else:
        client.state["blocked"] = {"hash-1"}

    assert client.get(url).json()["held"] is True  # a reviewer still sees it, marked

    client.state["reviewer"] = False
    assert client.get(url).status_code == 404
    assert client.get(f"/api/artifacts/{artifact['id']}/file?collection_id=c1").status_code == 404
    assert client.delete(url).status_code == 404
    assert client.get("/api/artifacts?collection_id=c1").json()["artifacts"] == []


def _generate_from(client, doc):
    client.state["chat"] = lambda *a, **k: _answer(_source(doc))
    artifact = _generate(client)
    return artifact, f"/api/artifacts/{artifact['id']}?collection_id=c1"


def test_a_report_stays_held_after_its_quarantined_source_is_deleted(client):
    doc = _document_ids(client.indexer)["sourdough.txt"]
    artifact, url = _generate_from(client, doc)
    _set_status(client.indexer, doc, "policy_status", "quarantined")
    client.indexer.delete_document(doc)

    detail = client.get(url).json()  # a reviewer still sees it, and can delete it
    assert detail["held"] is True
    assert [c["change"] for c in detail["source_changes"]] == ["removed"]

    client.state["reviewer"] = False
    assert client.get(url).status_code == 404
    assert client.get("/api/artifacts?collection_id=c1").json()["artifacts"] == []


def test_a_full_clear_remembers_which_sources_were_held(client):
    doc = _document_ids(client.indexer)["sourdough.txt"]
    _artifact, url = _generate_from(client, doc)
    _set_status(client.indexer, doc, "policy_status", "quarantined")
    client.indexer.vector_store.metadata_store.clear_all()
    client.state["reviewer"] = False
    assert client.get(url).status_code == 404


def test_a_report_outlives_the_ordinary_deletion_of_its_source(client):
    doc = _document_ids(client.indexer)["sourdough.txt"]
    _artifact, url = _generate_from(client, doc)
    client.indexer.delete_document(doc)
    client.state["reviewer"] = False

    detail = client.get(url).json()
    assert detail["held"] is False
    assert [c["change"] for c in detail["source_changes"]] == ["removed"]


def test_the_lasting_hold_applies_only_while_the_document_is_absent():
    # Ids are content hashes, so the same id back means the same content, and
    # its live status decides again.
    row = {"source_documents": [{"document_id": "d1", "withheld": True}]}
    assert artifact_store.is_withheld(row, (), (), {}) is True
    assert artifact_store.is_withheld(row, (), (), {"d1"}) is False
    assert artifact_store.is_withheld(row, {"d1"}, (), {"d1"}) is True


def test_deleting_a_document_does_not_depend_on_the_artifacts_table(client):
    ids = _document_ids(client.indexer)
    store = client.indexer.vector_store.metadata_store
    _set_status(client.indexer, ids["sourdough.txt"], "policy_status", "quarantined")
    assert store.delete_document(ids["sourdough.txt"]) > 0  # no table yet

    _set_status(client.indexer, ids["bridges.txt"], "policy_status", "quarantined")
    with sqlite3.connect(client.indexer.vector_store.metadata_db_path) as conn:
        conn.execute("CREATE TABLE artifacts (nonsense TEXT)")
    assert store.delete_document(ids["bridges.txt"]) > 0  # one this version cannot read


def test_readers_can_read_but_not_generate_or_delete(client):
    artifact = _generate(client)
    client.state["write"] = False
    assert client.get("/api/artifacts?collection_id=c1").json()["artifacts"][0]["id"] == artifact["id"]
    assert client.get(f"/api/artifacts/{artifact['id']}?collection_id=c1").status_code == 200
    assert client.post("/api/artifacts?collection_id=c1",
                       json={"artifact_type": "faq", "provider": "x"}).status_code == 403
    assert client.delete(f"/api/artifacts/{artifact['id']}?collection_id=c1").status_code == 403


def test_a_collection_the_caller_cannot_see_is_absent(client):
    assert client.get("/api/artifacts?collection_id=other").status_code == 404
    assert client.post("/api/artifacts?collection_id=other",
                       json={"artifact_type": "faq", "provider": "x"}).status_code == 404


def test_delete_removes_the_row_and_its_file(client, tmp_path):
    artifact = _generate(client)
    store = ArtifactStore(client.indexer.vector_store.metadata_db_path,
                          collection_service.get_artifacts_path("c1"))
    produced = tmp_path / "overview.mp3"
    produced.write_bytes(b"ID3 audio")
    assert store.attach_file(artifact["id"], produced, "audio/mpeg")

    download = client.get(f"/api/artifacts/{artifact['id']}/file?collection_id=c1")
    assert download.status_code == 200 and download.content == b"ID3 audio"
    assert download.headers["content-type"] == "audio/mpeg"
    assert "overview.mp3" in download.headers["content-disposition"]
    assert client.get("/api/artifacts?collection_id=c1").json()["artifacts"][0]["has_file"] is True

    assert client.delete(f"/api/artifacts/{artifact['id']}?collection_id=c1").json() == {"deleted": True}
    assert client.get(f"/api/artifacts/{artifact['id']}?collection_id=c1").status_code == 404
    assert client.delete(f"/api/artifacts/{artifact['id']}?collection_id=c1").status_code == 404
    assert not (collection_service.get_artifacts_path("c1") / artifact["id"]).exists()


def test_a_report_without_a_file_has_nothing_to_download(client):
    artifact = _generate(client)
    assert client.get(f"/api/artifacts/{artifact['id']}/file?collection_id=c1").status_code == 404


def test_deleting_while_generating_drops_the_result(client):
    release = threading.Event()

    def slow(*args, **kwargs):
        release.wait(10)
        return _answer()

    client.state["chat"] = slow
    artifact = _generate(client, wait=False)
    assert client.delete(f"/api/artifacts/{artifact['id']}?collection_id=c1").status_code == 200
    release.set()
    artifact_jobs.wait_for(artifact["id"])
    assert client.get("/api/artifacts?collection_id=c1").json()["artifacts"] == []


def test_the_queue_has_a_limit(client, monkeypatch):
    monkeypatch.setattr(artifact_jobs, "MAX_ACTIVE_JOBS", 0)
    response = client.post("/api/artifacts?collection_id=c1", json={"artifact_type": "faq", "provider": "x"})
    assert response.status_code == 409
    assert client.get("/api/artifacts?collection_id=c1").json()["artifacts"] == []


@pytest.mark.parametrize("name", ["../evil", "a/b", "a\\b", "..", "", "c:evil", "x\ny"])
def test_a_stored_file_name_cannot_leave_the_artifact_folder(tmp_path, name):
    store = ArtifactStore(tmp_path / "m.db", tmp_path / "artifacts")
    assert store.file_target({"id": "abcabcabcabc", "file_name": name}) is None


def test_a_stored_id_cannot_leave_the_artifacts_folder(tmp_path):
    store = ArtifactStore(tmp_path / "m.db", tmp_path / "artifacts")
    assert store.file_target({"id": "../../etc", "file_name": "passwd"}) is None
    assert store.delete("../../etc") is False


def test_store_refuses_unknown_fields(tmp_path):
    store = ArtifactStore(tmp_path / "m.db")
    with pytest.raises(ValueError):
        store.update("abcabcabcabc", nonsense=1)


# ── podcast scripts ─────────────────────────────────────────────────────────

_SCRIPT = (
    "**Alex:** Today, sourdough starters. Feed yours daily [sourdough.txt].\n\n"
    "**Sam:** Why daily?\n\n"
    "**Alex:** The yeast runs out of food [sourdough.txt p.2].\n\n"
    "**Sam:** That is all we have time for.\n\n"
    "[END OF SCRIPT]"
)


def test_the_podcast_script_is_offered_and_asks_for_the_parseable_form(client):
    types = {t["type"]: t for t in client.get("/api/artifacts/types").json()["types"]}
    assert types["podcast_script"]["label"] == "Podcast script"
    message = artifacts_service.build_artifact_message("podcast_script", focus="yeast")
    for required in ("**Alex:**", "**Sam:**", "[END OF SCRIPT]", "Focus specifically on: yeast", "Grounding rules"):
        assert required in message


def test_a_finished_script_is_saved_without_its_end_marker(client):
    client.state["chat"] = lambda *a, **k: _answer(_source("doc-1"), content=_SCRIPT)
    artifact = _generate(client, artifact_type="podcast_script")
    detail = client.get(f"/api/artifacts/{artifact['id']}?collection_id=c1").json()
    assert detail["status"] == "done"
    assert detail["title"] == "Podcast script"
    assert detail["content"] == _SCRIPT.replace("\n\n[END OF SCRIPT]", "")


def test_a_cut_off_script_fails_rather_than_saving_half_an_episode(client):
    cut_off = _SCRIPT.replace("[END OF SCRIPT]", "**Alex:** And the next thing to know is")
    client.state["chat"] = lambda *a, **k: _answer(_source("doc-1"), content=cut_off)
    artifact = _generate(client, artifact_type="podcast_script")
    detail = client.get(f"/api/artifacts/{artifact['id']}?collection_id=c1").json()
    assert detail["status"] == "failed"
    assert "cut off" in detail["error"]
    assert detail["content"] == ""


@pytest.mark.parametrize("content", [
    "A plain summary of the sources, with no hosts at all.\n\n[END OF SCRIPT]",
    "**Alex:** I will do all the talking.\n\n**Alex:** Still me.\n\n[END OF SCRIPT]",
    "**Alex:** Hello.\n\n**Sam:** Bye.\n\n[END OF SCRIPT]",  # not an episode
])
def test_anything_but_a_two_host_conversation_is_refused(content):
    with pytest.raises(ValueError, match="two-host"):
        artifacts_service.finish_content("podcast_script", content)


def test_script_formatting_slips_are_tidied():
    tidy = artifacts_service.finish_content("podcast_script", """Here is your script:

# Episode one

Alex: Welcome back.
It is good to be here.

---

**Sam**: Thanks. [a.txt]

__Alex__: Next.

**Sam:** Goodbye. **[END OF SCRIPT]**
Anything after the end goes too.
""")
    assert tidy == ("**Alex:** Welcome back. It is good to be here.\n\n**Sam:** Thanks. [a.txt]\n\n"
                    "**Alex:** Next.\n\n**Sam:** Goodbye.")


@pytest.mark.parametrize("ending", [
    "[END OF SCRIPT]", "**[END OF SCRIPT]**", "**END OF SCRIPT**", "END OF SCRIPT", "[end of script]", "(End of script.)",
])
def test_the_ways_models_write_the_end_marker_all_count(ending):
    finished = artifacts_service.finish_content("podcast_script", _SCRIPT.replace("[END OF SCRIPT]", ending))
    assert finished.endswith("**Sam:** That is all we have time for.")


def test_a_host_mentioning_the_end_of_the_script_does_not_end_the_episode():
    spoken = "**Alex:** We have reached the end of script review.\n\n" + _SCRIPT
    assert artifacts_service.finish_content("podcast_script", spoken).startswith("**Alex:** We have reached")
    with pytest.raises(ValueError, match="cut off"):
        artifacts_service.finish_content("podcast_script", _SCRIPT.replace("[END OF SCRIPT]", "") +
                                         "**Alex:** And that is the end of script")


def test_an_empty_script_says_the_model_returned_nothing(client):
    # What a reasoning model gives when its thinking uses up the whole limit.
    client.state["chat"] = lambda *a, **k: _answer(_source("doc-1"), content="")
    artifact = _generate(client, artifact_type="podcast_script")
    detail = client.get(f"/api/artifacts/{artifact['id']}?collection_id=c1").json()
    assert detail["status"] == "failed"
    assert detail["error"] == artifact_jobs.EMPTY_REASON


def test_a_script_gets_more_room_than_a_report(client):
    client.state["chat"] = lambda *a, **k: _answer(_source("doc-1"), content=_SCRIPT)
    _generate(client, artifact_type="podcast_script")
    assert client.state["seen"][-1].max_output_tokens == 8000
    _generate(client)
    assert client.state["seen"][-1].max_output_tokens is None


def test_generating_again_does_not_repeat_a_cached_answer(client):
    _generate(client)
    assert client.state["seen"][-1].use_cache is False


def test_other_artifact_types_are_saved_as_written():
    assert artifacts_service.finish_content("faq", "  **Q?** A.\n") == "  **Q?** A.\n"


# ── bundles ─────────────────────────────────────────────────────────────────

class _SignedEmbedder(FakeEmbedder):
    signature = "local::fake-embed"


@pytest.fixture()
def bundle_env(tmp_path, monkeypatch):
    for s in {id(config.settings): config.settings, id(cb.settings): cb.settings}.values():
        monkeypatch.setattr(s, "data_dir", tmp_path)
        monkeypatch.setattr(s, "embedding_provider", "local")
        monkeypatch.setattr(s, "embedding_model", "fake-embed")
    monkeypatch.setattr(app_db, "db_path", tmp_path / "app.db")
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    monkeypatch.setattr(collection_service, "base_dir", tmp_path / "collections")
    monkeypatch.setattr(indexer_manager, "_indexers", {})
    monkeypatch.setattr(indexer_manager, "_get_embedding_service", lambda model: _SignedEmbedder())
    return tmp_path


def _bundle_collection(tmp_path):
    cid = collection_service.create_collection(name="Handbooks", embedding_model="fake-embed")["id"]
    indexer = indexer_manager.get_indexer(cid)
    docs_dir = collection_service.get_documents_path(cid)
    ids = []
    for name, text in [("holidays.txt", "Staff accrue 25 days of holiday a year. " * 20),
                       ("expenses.txt", "Expenses are reimbursed within thirty days. " * 20)]:
        (docs_dir / name).write_text(text, encoding="utf-8")
        ids.append(indexer.index_document(docs_dir / name, name, collection_id=cid).document_id)
    indexer.save_index()
    db = collection_service.get_indexes_path(cid) / "metadata.db"
    store = ArtifactStore(db, collection_service.get_artifacts_path(cid))
    for artifact_id, document_id, content, status in [
        ("aaaaaaaaaaaa", ids[0], "About holidays", "done"),
        ("bbbbbbbbbbbb", ids[1], "SECRET about expenses", "done"),
        ("cccccccccccc", ids[0], None, "running"),
    ]:
        store.create(artifact_id, artifact_type="faq", title="FAQ", status=status, content=content,
                     created_at="2026-01-01T00:00:00+00:00",
                     source_documents=[{"document_id": document_id, "filename": "x", "content_hash": f"h-{document_id}"}])
    for artifact_id in ("aaaaaaaaaaaa", "bbbbbbbbbbbb"):
        produced = tmp_path / f"{artifact_id}.mp3"
        produced.write_bytes(artifact_id.encode())
        store.attach_file(artifact_id, produced, "audio/mpeg", file_name="overview.mp3")
    return cid, ids, db


def test_export_carries_finished_artifacts_and_their_files(bundle_env):
    cid, _ids, _db = _bundle_collection(bundle_env)
    path, _ = cb.export_collection(cid)
    with zipfile.ZipFile(path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        names = zf.namelist()
    assert manifest["version"] == cb.BUNDLE_VERSION
    assert manifest["contents"]["artifacts"] == 2  # the unfinished one stays behind
    assert "artifacts/aaaaaaaaaaaa/overview.mp3" in names

    imported = cb.import_collection(path, owner_id="default", name="Copy")
    new_db = collection_service.get_indexes_path(imported["id"]) / "metadata.db"
    store = ArtifactStore(new_db, collection_service.get_artifacts_path(imported["id"]))
    assert {a["id"] for a in store.list()} == {"aaaaaaaaaaaa", "bbbbbbbbbbbb"}
    restored = store.file_path(store.get("aaaaaaaaaaaa"))
    assert restored is not None and restored.read_bytes() == b"aaaaaaaaaaaa"


def test_export_without_artifact_files_stays_readable_by_older_releases(bundle_env):
    cid, _ids, db = _bundle_collection(bundle_env)
    store = ArtifactStore(db, collection_service.get_artifacts_path(cid))
    for artifact in store.list():
        store.delete(artifact["id"])
    path, _ = cb.export_collection(cid)
    with zipfile.ZipFile(path) as zf:
        assert json.loads(zf.read("manifest.json"))["version"] == 1


def test_export_leaves_out_artifacts_built_from_a_quarantined_document(bundle_env):
    cid, ids, db = _bundle_collection(bundle_env)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE documents SET policy_status='quarantined' WHERE document_id=?", (ids[1],))
    path, _ = cb.export_collection(cid)
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        raw = zf.read("indexes/metadata.db")
        assert json.loads(zf.read("manifest.json"))["contents"]["artifacts"] == 1
    assert "artifacts/bbbbbbbbbbbb/overview.mp3" not in names
    assert b"SECRET about expenses" not in raw


def test_import_drops_artifacts_built_from_a_blocklisted_document(bundle_env, monkeypatch):
    cid, ids, _db = _bundle_collection(bundle_env)
    path, _ = cb.export_collection(cid)
    monkeypatch.setattr(artifact_store, "load_blocked_hashes", lambda: {f"h-{ids[1]}"})
    imported = cb.import_collection(path, owner_id="default", name="Copy")
    new_db = collection_service.get_indexes_path(imported["id"]) / "metadata.db"
    store = ArtifactStore(new_db, collection_service.get_artifacts_path(imported["id"]))
    assert {a["id"] for a in store.list()} == {"aaaaaaaaaaaa"}
    assert not (collection_service.get_artifacts_path(imported["id"]) / "bbbbbbbbbbbb").exists()


def test_export_leaves_out_artifacts_whose_source_was_deleted(bundle_env):
    cid, (_kept, gone), db = _bundle_collection(bundle_env)
    indexer = indexer_manager.get_indexer(cid)
    indexer.delete_document(gone)
    indexer.save_index()

    path, _ = cb.export_collection(cid)
    with zipfile.ZipFile(path) as zf:
        raw = zf.read("indexes/metadata.db")
        names = zf.namelist()
        assert json.loads(zf.read("manifest.json"))["contents"]["artifacts"] == 1
    assert b"SECRET about expenses" not in raw
    assert gone.encode() not in raw
    assert "artifacts/bbbbbbbbbbbb/overview.mp3" not in names

    # Only the bundle: the collection keeps the report, flagged.
    assert ArtifactStore(db).get("bbbbbbbbbbbb") is not None


def test_import_drops_artifacts_a_bundle_carried_for_an_absent_document(bundle_env):
    _cid, (_kept, gone), db = _bundle_collection(bundle_env)
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM documents WHERE document_id=?", (gone,))
    kept = artifact_store.prune_for_bundle(db, set())
    assert [row["id"] for row in kept] == ["aaaaaaaaaaaa"]


def _seed_brief(db, document_id, text):
    ResearchStore(db).add_brief(document_id, "brief", {"sections": [{"statements": [{"text": text}]}]})


def test_export_withholds_a_quarantined_documents_research_and_artifacts_alike(bundle_env):
    cid, (kept, hidden), db = _bundle_collection(bundle_env)
    _seed_brief(db, kept, "Brief about holidays")
    _seed_brief(db, hidden, "SECRET brief about expenses")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE documents SET policy_status='quarantined' WHERE document_id=?", (hidden,))

    path, _ = cb.export_collection(cid)
    with zipfile.ZipFile(path) as zf:
        raw = zf.read("indexes/metadata.db")
        names = zf.namelist()
    # The raw file, not a query: neither sweep may leave a row in a free page.
    assert b"SECRET" not in raw
    assert hidden.encode() not in raw
    assert "artifacts/bbbbbbbbbbbb/overview.mp3" not in names

    assert b"About holidays" in raw and b"Brief about holidays" in raw
    assert "artifacts/aaaaaaaaaaaa/overview.mp3" in names


def test_import_withholds_a_blocklisted_documents_research_and_artifacts_alike(bundle_env, monkeypatch):
    cid, (kept, blocked), db = _bundle_collection(bundle_env)
    _seed_brief(db, kept, "Brief about holidays")
    _seed_brief(db, blocked, "SECRET brief about expenses")
    with sqlite3.connect(db) as conn:
        blocked_hash = conn.execute("SELECT content_hash FROM documents WHERE document_id=?",
                                    (blocked,)).fetchone()[0]
    path, _ = cb.export_collection(cid)
    with zipfile.ZipFile(path) as zf:
        assert b"SECRET" in zf.read("indexes/metadata.db")  # it left before the block

    app_db.add_blocked_hash(blocked_hash, "admin", "test", "expenses.txt")
    imported = cb.import_collection(path, owner_id="default", name="Copy")
    new_db = collection_service.get_indexes_path(imported["id"]) / "metadata.db"
    raw = new_db.read_bytes()
    assert b"SECRET" not in raw
    assert blocked.encode() not in raw
    store = ArtifactStore(new_db, collection_service.get_artifacts_path(imported["id"]))
    assert {a["id"] for a in store.list()} == {"aaaaaaaaaaaa"}
    assert b"Brief about holidays" in raw


def test_import_tolerates_an_unusable_artifacts_table(tmp_path):
    db = tmp_path / "m.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE artifacts (nonsense TEXT)")
    assert artifact_store.prune_for_bundle(db, set()) == []
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='artifacts'").fetchone() is None


def test_deleting_the_collection_removes_artifact_files(bundle_env):
    # No index is opened: on Windows an open search index locks the folder.
    cid = collection_service.create_collection(name="Handbooks", embedding_model="fake-embed")["id"]
    folder = collection_service.get_artifacts_path(cid)
    store = ArtifactStore(collection_service.get_indexes_path(cid) / "metadata.db", folder)
    store.create("aaaaaaaaaaaa", artifact_type="faq", title="FAQ", status="done",
                 created_at="2026-01-01T00:00:00+00:00")
    produced = bundle_env / "overview.mp3"
    produced.write_bytes(b"audio")
    store.attach_file("aaaaaaaaaaaa", produced, "audio/mpeg")
    assert (folder / "aaaaaaaaaaaa" / "overview.mp3").is_file()

    collection_service.delete_collection(cid)
    assert not folder.exists()

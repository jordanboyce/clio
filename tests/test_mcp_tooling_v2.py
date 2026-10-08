"""MCP tooling v2: the ChatGPT connector aliases (`search` / `fetch`), index
job visibility and single-document re-index over MCP, governance labels
over MCP, schema constraints on numeric limits, resource metadata and the
/api/mcp/catalog endpoint.

Embeddings are faked (deterministic vectors) so no model loads; the
fixtures mirror tests/test_mcp_write.py.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError

import services.mcp_server as mcp
from services.app_database import SQLiteBackend, app_db
from services.chunker import TextChunker
from services.collection_service import collection_service
from services.document_extractor import DocumentExtractor
from services.indexer_manager import indexer_manager
from services.indexing import DocumentIndexer
from services.vector_store import VectorStore
from tests.test_mcp_write import EMBED_DIM, NOTES, FakeEmbedder


@pytest.fixture()
def fresh_db(tmp_path):
    old_db_path = app_db.db_path
    old_base_dir = collection_service.base_dir
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    collection_service.base_dir = tmp_path / "collections"
    yield app_db
    app_db.db_path = old_db_path
    collection_service.base_dir = old_base_dir


@pytest.fixture()
def indexer(tmp_path, fresh_db, monkeypatch):
    """A fake-embedding indexer registered as the 'default' collection's."""
    ix = DocumentIndexer(
        vector_store=VectorStore(index_dir=tmp_path / "indexes", embedding_dim=EMBED_DIM),
        embedding_service=FakeEmbedder(),
        document_extractor=DocumentExtractor(),
        text_chunker=TextChunker(chunk_size=200, chunk_overlap=40),
    )
    monkeypatch.setitem(indexer_manager._indexers, "default", ix)
    monkeypatch.setattr(mcp.settings, "mcp_default_collection", "default")
    from api import deps

    monkeypatch.setattr(deps, "_initialized", True)
    return ix


@pytest.fixture()
def open_client(indexer):
    import main

    return TestClient(main.app)

RUNBOOK = (
    "# Incident runbook\n\nWhen the tunnel drops, rotate the credentials and page "
    "the on-call engineer. Backups are verified every Monday morning.\n"
)


def _call(name, args):
    _, structured = asyncio.run(mcp._clio_mcp.call_tool(name, args))
    return structured


# ── search / fetch ───────────────────────────────────────────────────────────


def test_search_returns_ids_that_fetch_resolves(indexer):
    notes = mcp.write_document_sync("sprint-notes.md", NOTES, collection_id="default")
    mcp.write_document_sync("runbook.md", RUNBOOK, collection_id="default")

    result = mcp.search_sync("when do backups run")
    assert set(result) == {"results"}
    results = result["results"]
    assert 0 < len(results) <= 10
    for item in results:
        assert set(item) >= {"id", "title", "url", "text"}
        assert item["url"].startswith("clio://document/")
        assert "collection_id=default" in item["url"]
        assert item["metadata"]["collection_id"] == "default"
    assert any(r["metadata"]["document_id"] == notes["document_id"] for r in results)

    top = next(r for r in results if r["metadata"]["document_id"] == notes["document_id"])
    assert top["title"].startswith("sprint-notes.md")
    assert "#" in top["id"]  # document_id#chunk_id

    # The chunk id resolves to that passage (plus neighbours).
    fetched = mcp.fetch_sync(top["id"])
    assert set(fetched) == {"id", "title", "text", "url", "metadata"}
    assert fetched["id"] == top["id"]
    assert top["text"].rstrip(".").split("...")[0][:30] in fetched["text"]
    assert fetched["metadata"]["chunk_id"] == top["metadata"]["chunk_id"]
    assert fetched["metadata"]["collection_id"] == "default"
    assert fetched["metadata"]["truncated"] is False

    # The bare document id resolves to the whole document.
    whole = mcp.fetch_sync(notes["document_id"])
    assert whole["id"] == notes["document_id"]
    assert whole["title"] == "sprint-notes.md"
    assert "verified by the on-call engineer" in whole["text"]
    assert whole["metadata"]["chunks_returned"] == whole["metadata"]["total_chunks_in_document"]
    assert whole["metadata"]["sensitivity"] in ("public", "internal", "confidential", "restricted")

    # A host that hands the url back instead of the id still gets the document.
    via_url = mcp.fetch_sync(top["url"])
    assert via_url["id"] == top["id"]


def test_search_and_fetch_full_tool_path(indexer):
    mcp.write_document_sync("sprint-notes.md", NOTES, collection_id="default")
    structured = _call("search", {"query": "backups nightly"})
    assert structured["results"]
    fetched = _call("fetch", {"id": structured["results"][0]["id"]})
    assert fetched["text"]
    with pytest.raises(ToolError):
        _call("search", {})  # query is required


def test_search_falls_back_to_every_visible_collection(indexer, monkeypatch):
    mcp.write_document_sync("sprint-notes.md", NOTES, collection_id="default")
    monkeypatch.setattr(mcp.settings, "mcp_default_collection", "")
    assert any(c.get("id") == "default" for c in mcp._visible_collections())

    results = mcp.search_sync("backups")["results"]
    assert results and results[0]["metadata"]["collection_id"] == "default"
    # ...and fetch locates the document without a default collection either.
    assert mcp.fetch_sync(results[0]["id"])["text"]


def test_search_respects_token_scope(indexer):
    mcp.write_document_sync("sprint-notes.md", NOTES, collection_id="default")
    scope = mcp._request_mcp_token_scope.set(["elsewhere"])
    allow = mcp._request_mcp_token_allowlist.set(True)
    try:
        with pytest.raises(ValueError, match="No collections available"):
            mcp.search_sync("backups")
    finally:
        mcp._request_mcp_token_allowlist.reset(allow)
        mcp._request_mcp_token_scope.reset(scope)


def test_fetch_unknown_id_raises(indexer):
    with pytest.raises(ValueError, match="not found"):
        mcp.fetch_sync("0123456789abcdef")
    with pytest.raises(ValueError, match="not found"):
        mcp.fetch_sync("0123456789abcdef#chunk-9")
    with pytest.raises(ValueError, match="must not be empty"):
        mcp.fetch_sync("   ")
    with pytest.raises(ValueError, match="not a document id"):
        mcp.fetch_sync("https://example.org/nothing/here")


def test_fetch_unknown_chunk_raises(indexer):
    doc = mcp.write_document_sync("sprint-notes.md", NOTES, collection_id="default")
    with pytest.raises(ValueError, match="chunk_id"):
        mcp.fetch_sync(f"{doc['document_id']}#no-such-chunk")


def test_document_url_uses_public_url_when_set(monkeypatch):
    monkeypatch.setattr(mcp.settings, "mcp_public_url", "https://clio.example.org/mcp")
    url = mcp._document_url("abc", "default", chunk_id="abc_3", page_number=4)
    assert url == "https://clio.example.org/documents/abc/pdf?collection_id=default#page=4"
    assert mcp._parse_connector_id(url) == ("abc", None, "default")

    monkeypatch.setattr(mcp.settings, "mcp_public_url", "")
    url = mcp._document_url("abc", "default", chunk_id="abc_3")
    assert url == "clio://document/abc?collection_id=default#abc_3"
    assert mcp._parse_connector_id(url) == ("abc", "abc_3", "default")
    assert mcp._parse_connector_id("abc#abc_3") == ("abc", "abc_3", None)
    assert mcp._parse_connector_id("abc") == ("abc", None, None)


# ── Index jobs ───────────────────────────────────────────────────────────────


def _seed_jobs(db):
    done = db.create_upload_job("default", 3, job_type="index")
    db.update_upload_job(
        done, status="completed", processed_files=3, phase="completed",
        result_summary='{"documents_processed": 2, "total_chunks": 9, "document_ids": ["a", "b"], '
                       '"failed_files": [{"filename": "bad.pdf", "error": "encrypted"}]}',
    )
    running = db.create_upload_job("default", 4, job_type="upload")
    db.update_upload_job(running, status="running", processed_files=1, current_file="two.pdf",
                         phase="embedding", phase_detail="Embedding chunks")
    return done, running


def test_list_index_jobs_shape(indexer, fresh_db):
    done, running = _seed_jobs(fresh_db)
    fresh_db.create_upload_job("not-a-visible-collection", 1, job_type="index")

    listed = mcp.list_index_jobs(collection_id="default")
    assert listed["scope"] == "default"
    assert [j["job_id"] for j in listed["jobs"]] == [running, done]
    first = listed["jobs"][0]
    assert set(first) >= {
        "job_id", "collection_id", "status", "job_type", "phase", "processed_files",
        "total_files", "progress_percent", "error", "started_at", "completed_at", "failed_count",
    }
    assert first["status"] == "running" and first["job_type"] == "upload"
    assert first["progress_percent"] == 25.0 and first["current_file"] == "two.pdf"
    finished = listed["jobs"][1]
    assert finished["status"] == "completed" and finished["progress_percent"] == 100.0
    assert finished["failed_count"] == 1 and finished["completed_at"]

    # Omitting the collection lists visible collections only.
    everything = mcp.list_index_jobs()
    assert everything["scope"] == "all_collections"
    assert {j["collection_id"] for j in everything["jobs"]} == {"default"}

    assert mcp.list_index_jobs(limit=1)["total_returned"] == 1
    with pytest.raises(ValueError, match="not found"):
        mcp.list_index_jobs(collection_id="nope")


def test_get_index_job_shape(indexer, fresh_db):
    done, running = _seed_jobs(fresh_db)

    job = mcp.get_index_job(done)
    assert job["job_id"] == done and job["status"] == "completed"
    assert job["progress_percent"] == 100.0
    assert job["failed_files"] == [{"filename": "bad.pdf", "error": "encrypted"}]
    assert job["summary"]["documents_processed"] == 2
    assert job["summary"]["document_ids"] == ["a", "b"]
    assert "failed_files" not in job["summary"]

    live = mcp.get_index_job(running)
    assert live["status"] == "running" and live["failed_files"] == []
    assert live["cancel_requested"] is False and live["queue_position"] is None

    with pytest.raises(ValueError, match="not found"):
        mcp.get_index_job(9999)
    hidden = fresh_db.create_upload_job("not-a-visible-collection", 1, job_type="index")
    with pytest.raises(ValueError, match="not found"):
        mcp.get_index_job(hidden)


# ── reindex_document ─────────────────────────────────────────────────────────


def test_reindex_document_refuses_without_write(indexer, monkeypatch):
    doc = mcp.write_document_sync("sprint-notes.md", NOTES, collection_id="default")
    from services.upload_service import upload_service

    monkeypatch.setattr(upload_service, "submit_job", lambda *a, **k: pytest.fail("must not queue"))
    token = mcp._request_mcp_can_write.set(False)
    try:
        with pytest.raises(ValueError, match="read-only"):
            mcp.reindex_document_sync(doc["document_id"])
    finally:
        mcp._request_mcp_can_write.reset(token)


def test_reindex_document_queues_a_tracked_job(indexer, monkeypatch):
    doc = mcp.write_document_sync("sprint-notes.md", NOTES, collection_id="default")
    from services.upload_service import upload_service

    submitted = {}

    def fake_submit(collection_id, job_type, total_files, target, args=()):
        submitted.update(collection_id=collection_id, job_type=job_type,
                         total_files=total_files, target=target, args=args)
        return 77

    monkeypatch.setattr(upload_service, "submit_job", fake_submit)
    result = mcp.reindex_document(doc["document_id"], collection_id="default")

    assert result["status"] == "queued" and result["job_id"] == 77
    assert result["document_id"] == doc["document_id"]
    assert result["filename"] == "sprint-notes.md"
    assert submitted["collection_id"] == "default"
    assert submitted["job_type"] == "reindex" and submitted["total_files"] == 1
    assert submitted["target"] is mcp._run_reindex_document_job
    stored = indexer_manager.get_documents_path("default") / "sprint-notes.md"
    assert submitted["args"][2] == str(stored)


def test_reindex_document_refuses_missing_document_and_missing_file(indexer, monkeypatch):
    with pytest.raises(ValueError, match="not found"):
        mcp.reindex_document_sync("0123456789abcdef")

    doc = mcp.write_document_sync("sprint-notes.md", NOTES, collection_id="default")
    (indexer_manager.get_documents_path("default") / "sprint-notes.md").unlink()
    with pytest.raises(ValueError, match="no longer on disk"):
        mcp.reindex_document_sync(doc["document_id"])


def test_reindex_document_queue_full_is_a_tool_error(indexer, monkeypatch):
    doc = mcp.write_document_sync("sprint-notes.md", NOTES, collection_id="default")
    from services.upload_service import upload_service

    def refuse(*a, **k):
        raise RuntimeError("3 indexing jobs are already waiting to run.")

    monkeypatch.setattr(upload_service, "submit_job", refuse)
    with pytest.raises(ValueError, match="already waiting"):
        mcp.reindex_document_sync(doc["document_id"])


def test_reindex_job_body_rebuilds_chunks_and_keeps_label(indexer, fresh_db):
    """The job thread body, run inline: chunks are dropped and rebuilt from
    the stored file, the id survives (content hash), the label survives."""
    from services import governance

    doc = mcp.write_document_sync("sprint-notes.md", NOTES, collection_id="default")
    doc_id = doc["document_id"]
    governance.set_document_sensitivity("default", doc_id, "confidential", actor="tester")
    store = indexer.vector_store.metadata_store
    before = store.get_chunks_by_document(doc_id)
    assert before

    job_id = fresh_db.create_upload_job("default", 1, job_type="reindex")
    source = indexer_manager.get_documents_path("default") / "sprint-notes.md"
    mcp._run_reindex_document_job(
        job_id, "default", doc_id, str(source), "sprint-notes.md", "upload", "confidential", "tester",
    )

    after = store.get_document_info(doc_id)
    assert after and after["sensitivity"] == "confidential"
    assert len(store.get_chunks_by_document(doc_id)) == len(before)
    assert doc_id in collection_service.get_collection_document_ids("default")

    job = mcp.get_index_job(job_id)
    assert job["status"] == "completed" and job["progress_percent"] == 100.0
    assert job["summary"]["document_ids"] == [doc_id]
    assert job["summary"]["previous_document_id"] == doc_id
    assert job["failed_files"] == []

    from services import audit

    event = audit.list_events(action="document.reindex")[0]
    assert event["document_id"] == doc_id and event["detail"]["via"] == "mcp"


# ── update_document_metadata ─────────────────────────────────────────────────


def test_update_document_metadata_sets_and_clears_label(indexer):
    doc = mcp.write_document_sync("sprint-notes.md", NOTES, collection_id="default")
    doc_id = doc["document_id"]

    updated = mcp.update_document_metadata(doc_id, sensitivity="confidential")
    assert updated["status"] == "updated"
    assert updated["document_id"] == doc_id and updated["filename"] == "sprint-notes.md"
    assert updated["sensitivity"] == "confidential"
    assert updated["sensitivity_effective"] == "confidential"
    assert mcp.get_document_metadata(doc_id)["sensitivity"] == "confidential"

    cleared = mcp.update_document_metadata(doc_id, sensitivity="inherit")
    assert cleared["sensitivity"] is None
    assert cleared["sensitivity_effective"] == "internal"  # the collection default

    from services import audit

    events = audit.list_events(action="document.sensitivity")
    assert [e["detail"]["to"] for e in events[:2]] == [None, "confidential"]


def test_update_document_metadata_validates_labels(indexer):
    doc = mcp.write_document_sync("sprint-notes.md", NOTES, collection_id="default")
    doc_id = doc["document_id"]

    with pytest.raises(ToolError):  # Literal enum rejects an unknown label
        _call("update_document_metadata", {"document_id": doc_id, "sensitivity": "secret"})
    with pytest.raises(ValueError, match="sensitivity must be one of"):
        mcp.update_document_metadata_sync(doc_id, sensitivity="secret")
    with pytest.raises(ValueError, match="Nothing to update"):
        mcp.update_document_metadata_sync(doc_id)
    with pytest.raises(ValueError, match="not found"):
        mcp.update_document_metadata_sync("0123456789abcdef", sensitivity="public")
    assert mcp.get_document_metadata(doc_id)["sensitivity"] is None


def test_update_document_metadata_refuses_without_write(indexer, monkeypatch):
    doc = mcp.write_document_sync("sprint-notes.md", NOTES, collection_id="default")
    token = mcp._request_mcp_can_write.set(False)
    try:
        with pytest.raises(ValueError, match="read-only"):
            mcp.update_document_metadata_sync(doc["document_id"], sensitivity="public")
    finally:
        mcp._request_mcp_can_write.reset(token)

    from services.sharing_service import sharing_service

    monkeypatch.setattr(sharing_service, "check_collection_access", lambda cid, uid: "read")
    with pytest.raises(ValueError, match="read-only"):
        mcp.update_document_metadata_sync(doc["document_id"], sensitivity="public")
    assert mcp.get_document_metadata(doc["document_id"])["sensitivity"] is None


def test_relabel_does_not_need_the_aup(indexer, monkeypatch):
    """Mirrors api/documents: a relabel is _require_write, not _require_ingest."""
    from fastapi import HTTPException
    from services import governance

    doc = mcp.write_document_sync("sprint-notes.md", NOTES, collection_id="default")

    def _refuse(user_id):
        raise HTTPException(status_code=403, detail={"code": "aup_required", "message": "Accept it."})

    monkeypatch.setattr(governance, "require_aup", _refuse)
    assert mcp.update_document_metadata_sync(doc["document_id"], sensitivity="public")["sensitivity"] == "public"
    with pytest.raises(ValueError, match="Accept it"):
        mcp.reindex_document_sync(doc["document_id"])


# ── Schema quality, resources, catalog ──────────────────────────────────────


def _schema(tool, param):
    prop = tool.inputSchema["properties"][param]
    return prop["anyOf"][0] if "anyOf" in prop else prop


def test_numeric_limits_carry_schema_constraints():
    tools = {t.name: t for t in asyncio.run(mcp._clio_mcp.list_tools())}
    expected = {
        ("search_collection", "top_k"): (1, 20),
        ("search_all_collections", "top_k_per_collection"): (1, 10),
        ("research_documents", "top_k"): (1, 20),
        ("research_documents", "max_per_document"): (1, 10),
        ("research_documents", "max_context_chars"): (1000, 40000),
        ("list_recent_documents", "limit"): (1, 50),
        ("get_table_rows", "limit"): (1, 2000),
        ("find_in_documents", "max_results"): (1, 100),
        ("list_index_jobs", "limit"): (1, 50),
    }
    for (name, param), (lo, hi) in expected.items():
        schema = _schema(tools[name], param)
        assert schema.get("minimum") == lo, (name, param, schema)
        assert schema.get("maximum") == hi, (name, param, schema)
    assert _schema(tools["research_documents"], "subqueries")["maxItems"] == 3
    assert _schema(tools["get_index_job"], "job_id")["minimum"] == 1
    assert tools["update_document_metadata"].inputSchema["properties"]["sensitivity"]["anyOf"][0]["enum"] == [
        "public", "internal", "confidential", "restricted", "inherit",
    ]


def test_out_of_range_limits_are_rejected_at_the_boundary(indexer):
    with pytest.raises(ToolError, match="less than or equal to 50"):
        _call("list_recent_documents", {"limit": 500})
    with pytest.raises(ToolError, match="greater than or equal to 1"):
        _call("search_collection", {"query": "x", "top_k": 0})
    assert _call("list_recent_documents", {"limit": 50})["total_returned"] == 0


def test_every_tool_has_a_title_and_annotations():
    for tool in asyncio.run(mcp._clio_mcp.list_tools()):
        assert tool.title, tool.name
        assert tool.annotations is not None and tool.annotations.title == tool.title, tool.name
        assert tool.annotations.readOnlyHint is not None
        assert tool.description and tool.description.strip(), tool.name
    names = {t.name for t in asyncio.run(mcp._clio_mcp.list_tools())}
    assert {"search", "fetch", "list_index_jobs", "get_index_job", "reindex_document",
            "update_document_metadata"} <= names


def test_resources_carry_names_descriptions_and_mime_types():
    templates = asyncio.run(mcp._clio_mcp.list_resource_templates())
    statics = asyncio.run(mcp._clio_mcp.list_resources())
    by_uri = {r.uriTemplate: r for r in templates} | {str(r.uri): r for r in statics}
    assert set(by_uri) == {
        "collection://{id}", "collection://{id}/schema", "collection://{id}/guide",
        "collection://{id}/tables", "document://{id}", "table://{id}", "collections://all",
    }
    for uri, resource in by_uri.items():
        assert resource.name and resource.name != uri, uri
        assert resource.description, uri
        assert resource.mimeType == "application/json", uri
    assert by_uri["collections://all"].name == "all_collections"
    assert by_uri["document://{id}"].name == "document"


def test_catalog_endpoint_lists_tools_resources_and_prompts(open_client):
    response = open_client.get("/api/mcp/catalog")
    assert response.status_code == 200, response.text
    catalog = response.json()
    assert catalog["transport"] == "streamable-http" and catalog["endpoint"] == "/mcp/"

    tools = {t["name"]: t for t in catalog["tools"]}
    assert {"search", "fetch", "reindex_document", "search_collection", "write_document"} <= set(tools)
    assert tools["search"] == {
        "name": "search",
        "title": "Search (connector)",
        "description": "Search the indexed documents (OpenAI/ChatGPT connector shape).",
        "read_only": True,
        "destructive": False,
        "parameters": ["query"],
    }
    assert tools["reindex_document"]["read_only"] is False
    assert tools["reindex_document"]["destructive"] is True
    assert tools["reindex_document"]["parameters"] == ["document_id", "collection_id"]
    assert "\n" not in tools["search_collection"]["description"]
    assert tools["search_collection"]["description"].startswith("Search indexed documents")

    resources = {r["uri_template"]: r for r in catalog["resources"]}
    assert "collection://{id}" in resources and "collections://all" in resources
    assert resources["document://{id}"]["name"] == "document"
    assert all(r["description"] for r in resources.values())

    prompts = {p["name"]: p for p in catalog["prompts"]}
    assert set(prompts) == {"research_question", "find_exact_reference", "collection_overview"}
    assert prompts["research_question"]["arguments"] == ["question", "collection"]
    assert prompts["research_question"]["title"]

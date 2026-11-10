"""Classification markings: what counts as outside, and that data marked local
only is not handed to it by any route.

The embedder is faked and no provider is ever called: where a call would have
gone outside, the test asserts it was refused before a provider existed.
"""

import json

import pytest
from fastapi.testclient import TestClient

from config import settings
from models.schemas import SearchMode
from services import data_controls as dc
from services import governance
from services.app_database import app_db
from services.collection_service import collection_service
from services.config_manager import config_manager
from tests.test_governance import BENIGN, FakeEmbedder, _write, fresh_db, indexer, open_client  # noqa: F401

MARKING = "export_controlled"
CLOUD = {"provider": "anthropic", "messages": [{"role": "user", "content": "What is the holiday policy?"}]}
KEY = {"X-AI-Key": "sk-test-not-a-real-key"}


def _mark_collection(collection_id="default", marking=MARKING):
    app_db.update_collection(collection_id, classification=marking)


def _add(indexer, tmp_path, name="handbook.txt", text=BENIGN):
    return indexer.index_document(_write(tmp_path, name, text), name, collection_id="default")


@pytest.fixture()
def catalog(monkeypatch):
    """The built-in markings, whatever an earlier test saved."""
    monkeypatch.setattr(settings, "data_classifications", "")
    monkeypatch.setattr(settings, "controlled_data_hosts", "")


@pytest.fixture()
def hosted_embeddings(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "openai")


# ── What is outside ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("host, inside", [
    ("localhost", True), ("127.0.0.1", True), ("::1", True), ("10.2.3.4", True), ("192.168.1.20", True),
    ("ollama", True), ("host.docker.internal", False), ("api.openai.com", False), ("8.8.8.8", False),
    ("llm.corp.example", False), ("", False),
])
def test_a_host_is_inside_only_when_it_cannot_be_public(catalog, host, inside):
    assert dc.host_is_internal(host) is inside


def test_the_operator_can_name_an_internal_server(catalog, monkeypatch):
    monkeypatch.setattr(settings, "controlled_data_hosts", "llm.corp.example, *.lab.example")

    assert dc.url_is_internal("https://llm.corp.example/v1")
    assert dc.url_is_internal("http://gpu1.lab.example:8000")
    assert not dc.url_is_internal("https://llm.corp.example.attacker.net/v1")


@pytest.mark.parametrize("provider, base_url, outside", [
    ("anthropic", "", True), ("openai", "http://127.0.0.1:9000/v1", True), ("bedrock", "", True),
    ("ollama", "http://localhost:11434", False), ("ollama", "https://ollama.example.com", True),
    ("openai_compatible", "http://10.0.0.7:8000/v1", False), ("openai_compatible", "https://api.groq.com/openai/v1", True),
    ("openai_compatible", "", True),
])
def test_a_model_is_judged_by_where_it_runs_not_by_what_it_is_called(catalog, provider, base_url, outside):
    assert dc.model_is_external(provider, base_url) is outside


def test_a_built_provider_says_whether_it_is_outside(catalog):
    from services.ai_service import create_provider

    assert create_provider("ollama", base_url="http://localhost:11434").external is False
    assert create_provider("openai_compatible", base_url="https://api.together.xyz/v1").external is True
    assert dc.provider_is_external(object()) is True  # one that does not say


# ── The catalogue ───────────────────────────────────────────────────────────


def test_the_built_in_markings_are_all_local_only(catalog):
    assert {m["id"] for m in dc.catalog()} >= {"export_controlled", "cui", "proprietary"}
    assert all(m["local_only"] for m in dc.catalog())


def test_a_marking_removed_from_the_list_still_holds(catalog, monkeypatch):
    monkeypatch.setattr(settings, "data_classifications", json.dumps([{"label": "Internal note", "local_only": False}]))

    assert dc.is_local_only("internal_note") is False
    assert dc.is_local_only("export_controlled") is True   # no longer listed
    with pytest.raises(ValueError, match="Unknown classification"):
        dc.normalize("export_controlled")


def test_a_list_that_cannot_be_read_releases_nothing(catalog, monkeypatch):
    monkeypatch.setattr(settings, "data_classifications", "{not json")

    assert dc.catalog() == []
    assert dc.is_local_only("anything") is True


@pytest.mark.parametrize("bad", [
    "nope", [{"label": ""}], [{"label": "A"}, {"label": "a"}], [{"label": "A", "local_only": "yes"}], [{"label": "!!!"}],
])
def test_a_malformed_list_is_refused(catalog, bad):
    with pytest.raises(ValueError):
        dc.clean_catalog(bad)


def test_an_admin_can_save_their_own_markings(catalog, fresh_db):
    try:
        result = config_manager.update_config({
            "data_classifications": [{"label": "Attorney-client", "local_only": True},
                                     {"label": "Draft", "local_only": False}],
            "controlled_data_hosts": "LLM.corp.example",
        })

        assert result["success"], result
        shown = config_manager.get_current_config()
        assert [m["id"] for m in shown["data_classifications"]] == ["attorney_client", "draft"]
        assert settings.controlled_data_hosts == "llm.corp.example"
        assert dc.is_local_only("attorney_client") and not dc.is_local_only("draft")
        # JSON does not survive .env, and the database restores it on start.
        assert "DATA_CLASSIFICATIONS" not in config_manager.env_file.read_text()
        assert not config_manager.update_config({"data_classifications": [{"label": ""}]})["success"]
    finally:
        settings.data_classifications = ""
        settings.controlled_data_hosts = ""


# ── Serving: a marked collection ────────────────────────────────────────────


def test_a_marked_collection_opens_here_and_not_for_an_outside_model(catalog, indexer):
    from api.deps import get_indexer

    _mark_collection()

    assert get_indexer("default") is indexer
    with dc.outside_consumer("the AI model (anthropic)"):
        with pytest.raises(dc.ExternalServiceBlocked) as refused:
            get_indexer("default")
    assert refused.value.status_code == 403
    assert refused.value.detail["code"] == dc.BLOCKED_CODE
    assert "Export controlled" in refused.value.detail["detail"]
    assert app_db.list_audit_events(action="classification.blocked")


def test_a_label_only_marking_changes_nothing(catalog, indexer, monkeypatch):
    from api.deps import get_indexer

    monkeypatch.setattr(settings, "data_classifications", json.dumps([{"label": "Draft", "local_only": False}]))
    _mark_collection(marking="draft")

    with dc.outside_consumer("the AI model (anthropic)"):
        assert get_indexer("default") is indexer


def test_chat_with_a_hosted_model_is_refused_before_any_model_is_built(catalog, open_client, monkeypatch):
    import api.chat as chat

    def never(*a, **k):
        raise AssertionError("a provider was built for a local-only collection")

    monkeypatch.setattr(chat, "_build_provider", never)
    monkeypatch.setattr(chat, "create_provider", never)
    _mark_collection()

    for path in ("/api/chat", "/api/chat/stream"):
        response = open_client.post(path, json=CLOUD, headers=KEY)
        assert response.status_code == 403, (path, response.text)
        assert response.json()["detail"]["code"] == dc.BLOCKED_CODE

    starters = open_client.post("/api/chat/starters", json={"provider": "anthropic"}, headers=KEY)
    assert starters.status_code == 403


def test_chat_with_a_model_on_this_network_is_not_stopped(catalog, open_client, monkeypatch):
    import api.chat as chat

    seen = {}

    def turn(*a, **k):
        seen["outside"] = dc.current_outside_consumer()
        yield {"type": "done", "sources": [], "structured_results": [], "cached": False, "related_questions": [],
               "depth": "standard", "_answer": "25 days."}

    monkeypatch.setattr(chat, "_run_turn", turn)
    monkeypatch.setattr(chat, "_prepare_turn", lambda *a, **k: (None, None))
    _mark_collection()

    response = open_client.post("/api/chat", json={**CLOUD, "provider": "ollama"})

    assert response.status_code == 200, response.text
    assert seen["outside"] == ""


def test_a_custom_endpoint_on_the_internet_counts_as_outside(catalog, open_client):
    _mark_collection()

    response = open_client.post("/api/chat", json={**CLOUD, "provider": "openai_compatible"},
                                headers={"X-AI-Base-URL": "https://api.groq.com/openai/v1"})

    assert response.status_code == 403


def test_asking_across_all_collections_leaves_the_marked_ones_out(catalog, indexer, tmp_path):
    _mark_collection()
    rows = collection_service.get_all_collections()

    with dc.outside_consumer("the AI model (anthropic)"):
        assert all(dc.collection_withheld(c) for c in rows if c["id"] == "default")
    assert not any(dc.collection_withheld(c) for c in rows)


def test_agents_and_apps_never_see_a_marked_collection_whatever_their_token(catalog, fresh_db):
    from services import mcp_server as mcp

    cid = app_db.create_collection(name="controlled drawings", owner_id="default")
    collection_service._ensure_collection_dirs(cid)
    _mark_collection(cid)

    assert cid in {c["id"] for c in mcp._visible_collections()}   # the in-app chat, local model

    with dc.outside_consumer("an MCP client"):
        scope = mcp._request_mcp_token_scope.set([cid])
        try:
            assert cid not in {c["id"] for c in mcp._visible_collections()}
            with pytest.raises(ValueError, match="not found"):
                mcp._resolve_collection_id(cid)
        finally:
            mcp._request_mcp_token_scope.reset(scope)


def test_a_registered_app_runs_as_an_outside_consumer(catalog, fresh_db):
    from services import mcp_server as mcp

    app = {"id": "app1", "collections": ["default"], "can_write": False, "actor": "app:one"}
    with mcp.app_request_scope(app):
        assert dc.current_outside_consumer() == "a registered app"
    assert dc.current_outside_consumer() == ""


# ── Serving: one marked document ────────────────────────────────────────────


def test_marking_one_source_closes_its_collection_to_outside_services(catalog, indexer, tmp_path):
    from api.deps import get_indexer

    plain = _add(indexer, tmp_path, "handbook.txt")
    drawing = _add(indexer, tmp_path, "drawing.txt", "The turbine blade tolerance is 12 microns at the root fillet.")
    assert not dc.holds_local_only("default")

    governance.set_document_classification("default", drawing.document_id, MARKING)

    assert dc.holds_local_only("default")
    assert dc.local_only_summary("default") == {"local_only": True, "classification": MARKING, "documents": 1}
    with dc.outside_consumer("the AI model (openai)"):
        with pytest.raises(dc.ExternalServiceBlocked, match="holds 1 source marked Export controlled"):
            get_indexer("default")
        # Second line: were the collection opened anyway, the source stays out.
        found = indexer.search("turbine blade tolerance", top_k=5, mode=SearchMode.KEYWORD)["results"]
        assert drawing.document_id not in {r.document_id for r in found}
        assert governance.is_hidden(indexer.vector_store.metadata_store.get_document_info(drawing.document_id))
    here = indexer.search("turbine blade tolerance", top_k=5, mode=SearchMode.KEYWORD)["results"]
    assert drawing.document_id in {r.document_id for r in here}
    assert not governance.is_hidden(indexer.vector_store.metadata_store.get_document_info(plain.document_id))

    governance.set_document_classification("default", drawing.document_id, None)
    assert not dc.holds_local_only("default")


def test_the_marking_is_read_without_opening_the_collection(catalog, indexer, tmp_path, monkeypatch):
    """Listing collections for an agent must not load every index."""
    from services.indexer_manager import indexer_manager

    doc = _add(indexer, tmp_path)
    governance.set_document_classification("default", doc.document_id, MARKING)
    db_path = indexer.vector_store.metadata_store.db_path
    monkeypatch.delitem(indexer_manager._indexers, "default")
    monkeypatch.setattr(collection_service, "get_indexes_path", lambda cid: db_path.parent)
    monkeypatch.setattr(indexer_manager, "get_indexer",
                        lambda cid: (_ for _ in ()).throw(AssertionError("opened the index")))

    assert dc.holds_local_only("default")


def test_a_collection_that_cannot_be_read_counts_as_marked(catalog, fresh_db, monkeypatch):
    monkeypatch.setattr(dc, "_document_markings", lambda cid: (_ for _ in ()).throw(OSError("disk")))

    assert dc.holds_local_only("default") is True


def test_a_marking_survives_the_same_file_being_added_again(catalog, indexer, tmp_path):
    doc = _add(indexer, tmp_path)
    governance.set_document_classification("default", doc.document_id, MARKING)

    again = _add(indexer, tmp_path)

    info = indexer.vector_store.metadata_store.get_document_info(again.document_id)
    assert again.document_id == doc.document_id and info["classification"] == MARKING


def test_marking_and_unmarking_are_audited(catalog, indexer, tmp_path):
    doc = _add(indexer, tmp_path)

    governance.set_document_classification("default", doc.document_id, MARKING, actor="alice")
    governance.set_document_classification("default", doc.document_id, "", actor="bob")

    events = app_db.list_audit_events(action="document.classification")
    assert [(e["actor"], json.loads(e["detail"])["to"]) for e in reversed(events)] == [("alice", MARKING), ("bob", None)]
    with pytest.raises(ValueError, match="Unknown classification"):
        governance.set_document_classification("default", doc.document_id, "top_secret")


# ── HTTP ────────────────────────────────────────────────────────────────────


def test_marking_through_the_api(catalog, open_client, indexer, tmp_path):
    doc = _add(indexer, tmp_path)
    url = f"/documents/{doc.document_id}/governance"

    assert open_client.patch(url, json={"classification": "nonsense"}).status_code == 400
    marked = open_client.patch(url, json={"classification": MARKING})
    assert marked.status_code == 200 and marked.json()["local_only"] is True
    info = indexer.vector_store.metadata_store.get_document_info(doc.document_id)
    assert info["classification"] == MARKING

    # A label change that does not mention the marking leaves it alone.
    assert open_client.patch(url, json={"sensitivity": "confidential"}).status_code == 200
    info = indexer.vector_store.metadata_store.get_document_info(doc.document_id)
    assert (info["sensitivity"], info["classification"]) == ("confidential", MARKING)

    me = open_client.get("/api/user/me").json()
    assert MARKING in {m["id"] for m in me["classifications"]} and me["embedding_external"] is False


def test_marking_a_collection_through_the_api(catalog, open_client, fresh_db):
    cid = open_client.post("/api/collections", json={"name": "drawings"}).json()["id"]

    assert open_client.put(f"/api/collections/{cid}", json={"classification": "nonsense"}).status_code == 400
    marked = open_client.put(f"/api/collections/{cid}", json={"classification": MARKING})
    assert marked.status_code == 200 and marked.json()["classification"] == MARKING
    assert dc.collection_local_only(cid)
    assert json.loads(app_db.list_audit_events(action="collection.classification")[0]["detail"])["local_only"] is True

    # Saving the dialog again without touching the marking keeps it.
    assert open_client.put(f"/api/collections/{cid}", json={"description": "x"}).json()["classification"] == MARKING
    cleared = open_client.put(f"/api/collections/{cid}", json={"classification": ""})
    assert not cleared.json()["classification"] and not dc.collection_local_only(cid)


# ── Processing ──────────────────────────────────────────────────────────────


class CountingEmbedder(FakeEmbedder):
    def __init__(self):
        self.sent = []

    def embed_texts(self, texts, progress_callback=None):
        self.sent.extend(texts)
        return super().embed_texts(texts, progress_callback)

    def embed_query(self, query):
        self.sent.append(query)
        return super().embed_query(query)


def test_a_hosted_embedding_service_is_never_sent_a_marked_collection(catalog, indexer, tmp_path, hosted_embeddings):
    inner = CountingEmbedder()
    indexer.embedding_service = dc.guard_embedding(inner, "default")
    indexer.collection_id = "default"
    _mark_collection()

    doc = _add(indexer, tmp_path)
    found = indexer.search("holiday policy", top_k=3, mode=SearchMode.AUTO)

    assert inner.sent == []
    assert indexer.embedding_service.model_name == "fake-embed"   # everything else passes through
    assert doc.document_id in {r.document_id for r in found["results"]}   # by keyword
    assert indexer.vector_store.chunks_without_vectors()
    with pytest.raises(Exception, match="local only"):
        indexer.fill_missing_vectors()


def test_the_built_in_embedding_model_is_still_used_for_a_marked_collection(catalog, indexer, tmp_path):
    inner = CountingEmbedder()
    indexer.embedding_service = dc.guard_embedding(inner, "default")
    _mark_collection()

    _add(indexer, tmp_path)

    assert inner.sent


def test_an_embedding_server_on_this_network_is_inside(catalog, monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "openai_compatible")
    monkeypatch.setattr(settings, "embedding_base_url", "http://10.1.1.5:8080/v1")
    assert not dc.embedding_is_external()

    monkeypatch.setattr(settings, "embedding_base_url", "https://embeddings.example.com/v1")
    assert dc.embedding_is_external()


def test_indexing_a_marked_collection_uses_no_outside_model(catalog, indexer, tmp_path, monkeypatch):
    """Vision OCR, figure descriptions and the model's content review are all
    asked through one question while a source is being indexed."""
    from services import content_policy

    class Outside:
        external = True

        def complete(self, *a, **k):
            raise AssertionError("page text was sent to an outside model")

    class Engine:
        _provider = Outside()

    seen = {}
    monkeypatch.setattr(settings, "content_policy_llm_review", True)
    monkeypatch.setattr(content_policy, "_resolve_review_provider", lambda: (Outside(), "m"))
    monkeypatch.setattr(type(indexer.document_extractor), "_configured_vision_ocr_engine", lambda self: Engine())
    extract = indexer.document_extractor.extract_text

    def spy(path, *a, **k):
        seen["engine"] = indexer.document_extractor._get_vision_ocr_engine()
        return extract(path, *a, **k)

    monkeypatch.setattr(indexer.document_extractor, "extract_text", spy)
    _mark_collection()

    _add(indexer, tmp_path)

    assert seen["engine"] is None
    assert indexer.document_extractor._get_vision_ocr_engine() is not None   # outside an indexing step
    assert dc.allow_outside_processing(Outside())


def test_notices_about_a_marked_collection_stay_off_outside_services(catalog, indexer, tmp_path, monkeypatch):
    from services import share_email, source_changes

    monkeypatch.setattr(settings, "resend_api_key", "re_test")
    monkeypatch.setattr(settings, "alert_webhook_url", "https://hooks.slack.com/services/x")
    monkeypatch.setattr(share_email, "_deliver", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent")))
    _mark_collection()

    with pytest.raises(RuntimeError, match="not emailed"):
        share_email.send_share_email("a@b.example", share_token="t", collection_name="x", permission="read",
                                     shared_by="me", app_url="http://x", collection_id="default")
    assert source_changes._may_leave("default") is False
    assert dc.external_allowed("default") is False

    monkeypatch.setattr(settings, "alert_webhook_url", "http://10.0.0.9/hook")
    assert source_changes._may_leave("default") is True

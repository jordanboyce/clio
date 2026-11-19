"""Write up: a cited summary built only from verified review statements
(work order A9, roadmap R11)."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.research import library
from tests.coverage_support import build_collection
from tests.test_research import PAPER_A, PAPER_B


def _review(point, quote, page, key="findings"):
    return {"sections": [{"key": key, "title": "x", "items": [{"point": point, "quote": quote, "page": page}]}],
            "coverage": {}, "model": "fake"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from api import research as api
    from services.collection_service import collection_service

    docs_dir = tmp_path / "documents"
    docs_dir.mkdir()
    monkeypatch.setattr(collection_service, "get_documents_path", lambda cid: docs_dir)
    monkeypatch.setattr(collection_service, "get_artifacts_path", lambda cid: tmp_path / "artifacts")
    ix = build_collection(tmp_path, {"paper-a.pdf": PAPER_A, "2018-download.pdf": PAPER_B})
    ix.document_extractor.extract_text.side_effect = OSError("no file in tests")

    def access(collection_id, user_id, write=False, owner=False):
        if collection_id != "c1":
            raise HTTPException(404, "not found")
        return "owner"
    monkeypatch.setattr(api, "get_indexer", lambda cid: (access(cid, None), ix)[1])
    monkeypatch.setattr(api, "require_collection_access", access)
    monkeypatch.setattr(api.governance, "require_aup", lambda user: None)
    import api.artifacts as artifacts_api
    monkeypatch.setattr(artifacts_api.collection_service, "get_artifacts_path", lambda cid: tmp_path / "artifacts")
    app = FastAPI()
    app.include_router(api.router)
    c = TestClient(app)
    c.indexer = ix
    return c


def _ids(ix):
    return {d["filename"]: d["document_id"] for d in ix.list_documents()}


def test_the_write_up_holds_only_verified_statements_with_quote_and_page(client):
    ix = client.indexer
    ids = _ids(ix)
    store = library.research_store(ix)
    for did in ids.values():
        library.analyze_document("c1", ix, did, use_registry=False, store=store)
    store.add_brief(ids["paper-a.pdf"], "brief", _review("Deeper roots kept seedlings alive longer.",
                                                         "seedlings with deeper roots survived 40% longer", 3))
    out = client.post("/api/research/writeup?collection_id=c1", json={"style": "apa"})
    assert out.status_code == 201, out.text
    body = out.json()
    assert body["reviewed"] == 1 and body["selected"] == 2

    from api.artifacts import _store
    artifact = _store(ix, "c1").get(body["artifact"]["id"])
    content = artifact["content"]
    assert artifact["artifact_type"] == "writeup" and artifact["status"] == "done"
    assert "Deeper roots kept seedlings alive longer." in content
    assert "“seedlings with deeper roots survived 40% longer”" in content
    assert "## References" in content
    # The source without a review is named, not summarised.
    assert "## Not reviewed yet" in content


def test_with_no_review_there_is_nothing_to_write_up(client):
    out = client.post("/api/research/writeup?collection_id=c1", json={})
    assert out.status_code == 409 and "Prepare reviews first" in out.json()["detail"]


def test_a_write_up_is_never_generated_by_a_model():
    from services import artifacts

    assert "writeup" not in artifacts.ARTIFACT_TYPES
    with pytest.raises(ValueError):
        artifacts.build_artifact_message("writeup")
    assert artifacts.artifact_title("writeup") == "Literature write-up"

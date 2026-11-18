"""PRA models are off unless someone turns them on (ENABLE_PRA).

Off, nobody sees them: a SAPHIRE or Open-PSA file indexes as plain text with
no model, /api/pra answers 404, the pra_* MCP tools are neither listed nor
mentioned, and the features list says how to turn it on. Turning it on in
Settings brings the tools back without a restart.
"""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
import services.mcp_server as mcp
from services.document_extractor import DocumentExtractor, is_pra_document, is_supported_file
from tests.test_governance import fresh_db, indexer  # noqa: F401
from tests.test_pra_api import DOC, _add_doc, _pumps_model

MEF = Path(__file__).parent / "fixtures" / "pra" / "flight_hydraulics.xml"
MARD = Path(__file__).parent / "fixtures" / "pra" / "mard" / "MINI-PWR.FTL"


@pytest.fixture()
def pra(monkeypatch):
    def apply(on):
        monkeypatch.setattr(config.settings, "enable_pra", on)
        mcp.sync_pra_tools()
    yield apply
    monkeypatch.undo()
    mcp.sync_pra_tools()


def _tools():
    return {t.name for t in asyncio.run(mcp._clio_mcp.list_tools())}


def test_it_is_off_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_PRA", raising=False)
    assert config.Settings().enable_pra is False


def test_off_a_pra_file_is_plain_text(pra, tmp_path):
    pra(False)
    assert not is_pra_document(MEF) and not is_pra_document(MARD)

    mef = DocumentExtractor(enable_ocr=False).extract_text(MEF)
    mard = DocumentExtractor(enable_ocr=False).extract_text(MARD)

    assert "<opsa-mef" in "".join(mef.page_texts.values())
    assert mard.page_texts and "".join(mard.page_texts.values()).strip()


def test_off_a_zip_export_is_not_taken(pra, tmp_path):
    import zipfile

    export = tmp_path / "model.zip"
    with zipfile.ZipFile(export, "w") as zf:
        zf.write(MEF, "model.xml")
    pra(True)
    assert is_supported_file(export)
    pra(False)
    assert not is_supported_file(export)


def test_off_no_model_is_built_when_indexed(pra, indexer, tmp_path):  # noqa: F811
    from services.pra import store as pra_store

    pra(False)
    path = tmp_path / "flight_hydraulics.xml"
    path.write_bytes(MEF.read_bytes())

    meta = indexer.index_document(path, path.name, collection_id="default")

    doc = indexer.vector_store.metadata_store.get_document_info(meta.document_id)
    assert not str(doc.get("source_format") or "").startswith("pra")
    assert pra_store.load_model(indexer.vector_store.index_dir, meta.document_id) is None


def test_off_the_api_is_not_there(pra, indexer):  # noqa: F811
    import main
    from api import deps

    deps._initialized = True
    _add_doc(indexer, DOC, "pumps.xml", "pra-mef", _pumps_model())
    client = TestClient(main.app)
    pra(True)
    assert client.get("/api/pra/models").status_code == 200
    pra(False)
    assert client.get("/api/pra/models").status_code == 404
    assert client.get(f"/api/pra/models/{DOC}/fault-trees/PUMPS-FT").status_code == 404


def test_off_no_agent_sees_the_tools_or_a_word_about_them(pra):
    pra(False)
    assert not _tools() & set(mcp.PRA_TOOLS)
    assert "pra_" not in (mcp._clio_mcp.instructions or "")

    pra(True)
    assert set(mcp.PRA_TOOLS) <= _tools()
    assert "pra_models" in mcp._clio_mcp.instructions


def test_turning_it_on_in_settings_needs_no_restart(pra, indexer):  # noqa: F811
    import main

    pra(False)
    client = TestClient(main.app)
    features = {f["id"]: f for f in client.get("/api/features").json()["features"]}
    assert features["pra"]["state"] == "off" and "index those files again" in features["pra"]["fix"]

    saved = client.post("/api/config", json={"enable_pra": True})

    assert saved.status_code == 200, saved.text
    assert config.settings.enable_pra is True
    assert set(mcp.PRA_TOOLS) <= _tools()
    assert client.get("/api/config").json()["enable_pra"] is True

"""PRA models through the real upload endpoint: text, tables, the stored model.

Embeddings are faked (deterministic vectors) so no model loads. A PRA upload
becomes one document whose pages are searchable and citable, whose tables
answer SQL through query_table, and whose parsed model is saved beside the
index and removed with the document.
"""

import hashlib
import io
import zipfile
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import config
from models.schemas import SearchMode
from services.pra import store as pra_store
from services.pra.ingest import model_for_document

FIXTURES = Path(__file__).parent / "fixtures" / "pra"
MEF = FIXTURES / "flight_hydraulics.xml"
MARD = FIXTURES / "mard"


class _FakeEmbedder:
    model_name = "fake-embed"
    embedding_dim = 8

    def embed_texts(self, texts, progress_callback=None):
        if progress_callback:
            progress_callback(len(texts), len(texts))
        return np.array(
            [[b / 255.0 + 0.01 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts],
            dtype=np.float32,
        )

    def embed_query(self, query):
        return self.embed_texts([query])[0]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from api import deps
    from services.app_database import SQLiteBackend, app_db
    from services.chunker import TextChunker
    from services.collection_service import collection_service
    from services.document_extractor import DocumentExtractor
    from services.indexer_manager import indexer_manager
    from services.indexing import DocumentIndexer
    from services.vector_store import VectorStore

    monkeypatch.setattr(app_db, "db_path", tmp_path / "app.db")
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    monkeypatch.setattr(collection_service, "base_dir", tmp_path / "collections")
    indexer = DocumentIndexer(
        vector_store=VectorStore(index_dir=tmp_path / "indexes", embedding_dim=8),
        embedding_service=_FakeEmbedder(),
        document_extractor=DocumentExtractor(),
        text_chunker=TextChunker(chunk_size=1000, chunk_overlap=200),
    )
    indexer.collection_id = "default"
    monkeypatch.setitem(indexer_manager._indexers, "default", indexer)
    monkeypatch.setattr(deps, "_initialized", True)
    # Most tests parse in-process; test_zip_export_is_one_document goes through the helper.
    monkeypatch.setattr(config.settings, "extract_isolation", False)
    import main

    return TestClient(main.app), indexer


def _upload(client, name, data):
    return client.post("/documents/upload", files={"files": (name, data, "application/octet-stream")})


def _hits(indexer, query):
    return indexer.search(query, top_k=5, mode=SearchMode.KEYWORD)["results"]


def _zip_bytes(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_mef_upload_is_searchable_queryable_and_stored(env):
    client, indexer = env
    r = _upload(client, "flight_hydraulics.xml", MEF.read_bytes())
    assert r.status_code == 201, r.text
    doc_id = r.json()["document_ids"][0]
    info = indexer.vector_store.metadata_store.get_document_info(doc_id)
    assert info["source_format"] == "pra-mef"

    hits = _hits(indexer, "what feeds gate SYS-B-FAILS")
    assert hits and hits[0].document_id == doc_id
    assert "Gate SYS-B-FAILS (OR)" in hits[0].text_snippet and "EDP-B-FTR" in hits[0].text_snippet

    import services.mcp_server as mcp

    tables = {t["sheet_name"]: t["table_name"] for t in
              indexer.vector_store.structured_store.list_tables() if t["document_id"] == doc_id}
    assert set(tables) == {"basic_events", "gates", "sequences", "cut_sets"} - {"cut_sets"}
    result = mcp.query_table(
        f'SELECT "probability", "failure_rate" FROM "{tables["basic_events"]}" WHERE "name" = \'EDP-A-FTR\'',
        collection_id="default")
    assert result["rows"][0][0] == pytest.approx(2e-4, rel=1e-3) and result["rows"][0][1] == 2e-5

    index_dir = indexer.vector_store.index_dir
    assert pra_store.load_model(index_dir, doc_id).gates["LOSS-HYD"].type == "and"
    assert model_for_document(indexer, doc_id).name == "FlightHydraulics"

    # A lost model file is rebuilt from the stored source.
    pra_store.delete_model(index_dir, doc_id)
    assert model_for_document(indexer, doc_id).fault_trees["LossOfHydraulics"].top == "LOSS-HYD"
    assert pra_store.load_model(index_dir, doc_id) is not None

    assert client.delete(f"/documents/{doc_id}").status_code == 200
    assert pra_store.load_model(index_dir, doc_id) is None
    assert model_for_document(indexer, doc_id) is None
    assert not [t for t in indexer.vector_store.structured_store.list_tables() if t["document_id"] == doc_id]


def test_zip_export_is_one_document(env, monkeypatch):
    client, indexer = env
    monkeypatch.setattr(config.settings, "extract_isolation", True)  # parsed in the helper process
    data = _zip_bytes({f"MINI-PWR_Subs/{p.name}": p.read_bytes() for p in sorted(MARD.iterdir())})
    r = _upload(client, "mini-pwr.zip", data)
    assert r.status_code == 201, r.text
    assert r.json()["documents_processed"] == 1
    doc_id = r.json()["document_ids"][0]
    assert indexer.vector_store.metadata_store.get_document_info(doc_id)["source_format"] == "pra-mard"

    hits = _hits(indexer, "probability of the AFW motor-driven pump A failing to start")
    assert any("AFW-MDP-A-FS" in h.text_snippet and "7.94E-04" in h.text_snippet for h in hits)
    hits = _hits(indexer, "which sequences end in core damage")
    assert any("Sequences of TRANS ending in CD (CORE DAMAGE): 03" in h.text_snippet for h in hits)

    tables = {t["sheet_name"]: t for t in indexer.vector_store.structured_store.list_tables()
              if t["document_id"] == doc_id}
    assert tables["cut_sets"]["row_count"] == 5 and tables["sequences"]["row_count"] == 4
    model = model_for_document(indexer, doc_id)
    assert model.counts()["gates"] == 7 and len(model.source_files) == 11


def test_staged_upload_accepts_a_pra_zip(env):
    client, _ = env
    data = _zip_bytes({p.name: p.read_bytes() for p in MARD.iterdir()})
    r = client.post("/documents/upload-staged", files=[("files", ("mini.zip", data, "application/zip"))])
    assert r.status_code == 202 and [s["filename"] for s in r.json()["staged"]] == ["mini.zip"]


def test_other_zips_are_refused_as_before(env):
    client, _ = env
    data = _zip_bytes({"MINI-PWR.FTL": (MARD / "MINI-PWR.FTL").read_bytes(), "photo.txt": b"hello"})
    r = _upload(client, "bundle.zip", data)
    assert r.status_code == 400
    assert "Clio can't read .zip archives. Unpack it and upload the files inside." in r.json()["detail"]


def test_plain_sql_and_xml_stay_code(env):
    client, indexer = env
    sql = _upload(client, "schema.sql", b"CREATE TABLE pumps (id INTEGER PRIMARY KEY, name TEXT);\n")
    xml = _upload(client, "pom.xml", b"<?xml version='1.0'?><project><artifactId>pumps</artifactId></project>\n")
    store = indexer.vector_store.metadata_store
    assert store.get_document_info(sql.json()["document_ids"][0])["source_format"] == "sql"
    assert store.get_document_info(xml.json()["document_ids"][0])["source_format"] == "xml"
    assert not indexer.vector_store.structured_store.list_tables()
    assert not (indexer.vector_store.index_dir / "pra").exists()


def test_mard_sql_alone_is_a_model(env):
    client, indexer = env
    r = _upload(client, "MINI-PWR.SQL", (MARD / "MINI-PWR.SQL").read_bytes())
    doc_id = r.json()["document_ids"][0]
    assert indexer.vector_store.metadata_store.get_document_info(doc_id)["source_format"] == "pra-mard"
    assert model_for_document(indexer, doc_id).event_trees["TRANS"].sequences[0].logic == ["/RPS", "AFW", "HPI"]


def test_a_damaged_mard_file_fails_with_the_readers_reason(env):
    client, indexer = env
    r = _upload(client, "broken.ftl", b"<#macro page>not a fault tree</#macro>")
    assert r.status_code != 201
    assert "broken.ftl does not read as a SAPHIRE MAR-D .FTL file" in r.json()["detail"]
    assert not indexer.vector_store.structured_store.list_tables()

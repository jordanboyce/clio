"""Clio with no AI provider: index and search still work.

A fresh install has no API key and no local model server. Everything that
finds passages must work anyway; only the parts that write answers need a
model, and they must refuse plainly rather than fail.

The gap this pins: spreadsheets are typed SQL tables, never chunked or
embedded, so before table row search a CSV or XLSX row could be reached
only through a model's table tools. Search now returns the rows that hold
every keyword of the query.

Embeddings are faked (deterministic vectors) so no model loads.
"""

import hashlib

import numpy as np
import pytest
from fastapi.testclient import TestClient

from models.schemas import SearchResult
from services import table_row_search
from services.app_database import SQLiteBackend, app_db
from services.chunker import TextChunker
from services.collection_service import collection_service
from services.document_extractor import DocumentExtractor
from services.indexer_manager import indexer_manager
from services.indexing import DocumentIndexer
from services.structured_store import StructuredStore
from services.vector_store import VectorStore

EMBED_DIM = 8


class FakeEmbedder:
    model_name = "fake-embed"
    embedding_dim = EMBED_DIM

    @staticmethod
    def _vec(text: str) -> list:
        digest = hashlib.sha256(text.encode()).digest()
        return [b / 255.0 + 0.01 for b in digest[:EMBED_DIM]]

    def embed_texts(self, texts, progress_callback=None):
        return np.array([self._vec(t) for t in texts], dtype=np.float32)

    def embed_query(self, query):
        return np.array(self._vec(query), dtype=np.float32)


INVOICES = "invoice,customer,amount\nINV-1001,Acme Corp,1200\nINV-1002,Globex,560\nINV-1003,Acme Corp,3300\n"
INVENTORY = "sku,product,stock\nSKU-77,Copper kettle,14\nSKU-78,Cast iron pan,3\n"
POLICY = "The fire evacuation assembly point is the north car park beside the bicycle shed."


# ── StructuredStore.find_rows ───────────────────────────────────────────────


@pytest.fixture()
def store(tmp_path):
    s = StructuredStore(tmp_path / "structured.db")
    s.create_table("d_inv", "invoices.csv", ["invoice", "customer", "amount"], [
        {"invoice": "INV-1001", "customer": "Acme Corp", "amount": "1200"},
        {"invoice": "INV-1002", "customer": "Globex", "amount": "560"},
        {"invoice": "INV-1003", "customer": "Acme Corp", "amount": "3300"},
    ])
    s.create_table("d_stock", "inventory.csv", ["sku", "product", "stock"], [
        {"sku": "SKU-77", "product": "Copper kettle", "stock": "14"},
        {"sku": "SKU-78", "product": "Cast iron pan", "stock": "3"},
    ])
    return s


def _row_ids(rows):
    return [(r["document_id"], r["row_number"]) for r in rows]


def test_find_rows_needs_every_term_in_the_row(store):
    assert _row_ids(store.find_rows(["acme"])) == [("d_inv", 1), ("d_inv", 3)]
    assert _row_ids(store.find_rows(["acme", "3300"])) == [("d_inv", 3)]
    assert store.find_rows(["acme", "kettle"]) == []


def test_find_rows_returns_original_column_names_and_values(store):
    (row,) = store.find_rows(["globex"])
    assert row["filename"] == "invoices.csv"
    assert row["columns"] == ["invoice", "customer", "amount"]
    assert row["values"]["invoice"] == "INV-1002"
    assert str(row["values"]["amount"]) == "560"


def test_column_and_file_names_satisfy_their_terms(store):
    # "stock" is a header, "inventory" the filename: only "copper" must be in a cell.
    assert _row_ids(store.find_rows(["copper", "stock", "inventory"])) == [("d_stock", 1)]


def test_a_query_naming_only_the_table_returns_no_rows(store):
    assert store.find_rows(["stock"]) == []
    assert store.find_rows(["invoice", "customer"]) == []


def test_only_whole_header_words_satisfy_a_term(tmp_path):
    # "pan" is inside "company" but is not a header word: it must match a cell.
    s = StructuredStore(tmp_path / "structured.db")
    s.create_table("d", "suppliers.csv", ["company", "item"], [
        {"company": "Ironworks Ltd", "item": "Cast iron pan"},
        {"company": "Kettle Co", "item": "Copper kettle"},
    ])
    assert [r["row_number"] for r in s.find_rows(["pan"])] == [1]
    assert [r["row_number"] for r in s.find_rows(["kettle", "items"])] == [2]


def test_a_table_sqlite_cannot_search_is_skipped_not_fatal(store, monkeypatch):
    real_get_schema = store.get_schema

    def broken_for_invoices(identifier, identifier_type=None):
        schema = real_get_schema(identifier, identifier_type)
        if schema and "inv" in identifier:
            schema = dict(schema, columns=[dict(c, sql_name="no_such_column", raw_sql_name=None)
                                           for c in schema["columns"]])
        return schema

    monkeypatch.setattr(store, "get_schema", broken_for_invoices)
    assert _row_ids(store.find_rows(["kettle"])) == [("d_stock", 1)]


def test_plural_terms_match_the_singular(store):
    assert _row_ids(store.find_rows(["kettles"])) == [("d_stock", 1)]


def test_find_rows_respects_limit_and_document_restriction(store):
    assert len(store.find_rows(["acme"], limit=1)) == 1
    assert store.find_rows(["acme"], document_ids={"d_stock"}) == []
    assert store.find_rows(["acme"], limit=0) == []


# ── merge ───────────────────────────────────────────────────────────────────


def _result(name, score=0.5):
    return SearchResult(filename=name, page_number=1, text_snippet=name, similarity_score=score,
                        document_id=name, chunk_id=name, pdf_url="", page_url="")


def test_rows_lead_but_take_at_most_half_when_passages_fill_the_list():
    passages = [_result(f"p{i}") for i in range(10)]
    rows = [_result(f"r{i}", 1.0) for i in range(10)]
    merged = table_row_search.merge(passages, rows, top_k=10)
    assert [r.filename for r in merged] == ["r0", "r1", "r2", "r3", "r4", "p0", "p1", "p2", "p3", "p4"]


def test_rows_fill_the_room_passages_leave():
    merged = table_row_search.merge([_result("p0")], [_result(f"r{i}", 1.0) for i in range(10)], top_k=5)
    assert [r.filename for r in merged] == ["r0", "r1", "r2", "r3", "p0"]


def test_no_rows_leaves_passages_untouched():
    passages = [_result("p0"), _result("p1")]
    assert table_row_search.merge(passages, [], top_k=10) is passages


# ── HTTP, with no provider configured ───────────────────────────────────────


@pytest.fixture()
def indexer(tmp_path, monkeypatch):
    old_db_path, old_base_dir = app_db.db_path, collection_service.base_dir
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    collection_service.base_dir = tmp_path / "collections"

    ix = DocumentIndexer(
        vector_store=VectorStore(index_dir=tmp_path / "indexes", embedding_dim=EMBED_DIM),
        embedding_service=FakeEmbedder(),
        document_extractor=DocumentExtractor(),
        text_chunker=TextChunker(chunk_size=200, chunk_overlap=40),
    )
    monkeypatch.setitem(indexer_manager._indexers, "default", ix)
    from api import deps

    monkeypatch.setattr(deps, "_initialized", True)
    docs = {}
    for name, text in (("invoices.csv", INVOICES), ("inventory.csv", INVENTORY), ("evacuation.txt", POLICY)):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        docs[name] = ix.index_document(path, name, collection_id="default").document_id
    ix.docs = docs
    yield ix
    app_db.db_path, collection_service.base_dir = old_db_path, old_base_dir


@pytest.fixture()
def client(indexer):
    import main

    return TestClient(main.app)


def _search(client, **body):
    r = client.post("/search", json=body)
    assert r.status_code == 200, r.text
    return r.json()["results"]


def test_spreadsheets_index_without_ai(indexer):
    tables = indexer.vector_store.structured_store.list_tables()
    assert {t["filename"] for t in tables} == {"invoices.csv", "inventory.csv"}


def test_search_finds_prose_and_spreadsheet_rows_without_ai(client):
    hits = _search(client, query="evacuation assembly point", top_k=3)
    assert hits[0]["filename"] == "evacuation.txt"

    (row,) = [h for h in _search(client, query="INV-1002", mode="keyword") if h["csv_row_number"]]
    assert row["filename"] == "invoices.csv" and row["csv_row_number"] == 2
    assert row["csv_values"]["customer"] == "Globex"
    assert row["pdf_url"].endswith("/pdf?collection_id=default")

    # A question, not keywords: the filler words must not stop the match.
    top = _search(client, query="how many copper kettles are in stock")[0]
    assert top["filename"] == "inventory.csv" and top["csv_values"]["product"] == "Copper kettle"


def test_semantic_mode_and_filters_apply_to_rows(client, indexer):
    assert not [h for h in _search(client, query="Globex", mode="semantic") if h["csv_row_number"]]
    txt_only = _search(client, query="Globex", filters={"source_formats": ["txt"]})
    assert not [h for h in txt_only if h["csv_row_number"]]
    other_doc = _search(client, query="Globex", filters={"document_ids": [indexer.docs["inventory.csv"]]})
    assert not [h for h in other_doc if h["csv_row_number"]]


def test_quarantined_spreadsheet_rows_never_surface(client, indexer):
    indexer.vector_store.metadata_store.set_document_governance(
        indexer.docs["invoices.csv"], policy_status="quarantined")
    assert not [h for h in _search(client, query="Globex") if h["filename"] == "invoices.csv"]


def test_ai_requested_without_a_key_still_returns_results(client):
    hits = _search(client, query="evacuation assembly point",
                   ai={"provider": "anthropic", "synthesize": True, "rerank": True})
    assert hits and hits[0]["filename"] == "evacuation.txt"


def test_chat_without_a_key_refuses_plainly(client):
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "where do we meet?"}]})
    assert r.status_code == 400
    assert "API key required" in r.json()["detail"]

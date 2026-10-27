"""Collection statistics, document listing and file-list export.

Real SQLite behind every test: the numbers an agent quotes ("how many
documents", "the 20 largest PDFs") must come from the database exactly, not
from a fake that agrees with the implementation.
"""

import csv
import io
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import file_kinds
from services.metadata_store import MetadataStore


def _add(store, i, filename, **kw):
    store.add_document(
        document_id=kw.pop("document_id", f"doc{i:05d}"),
        filename=filename,
        num_pages=kw.pop("num_pages", 2),
        num_chunks=kw.pop("num_chunks", 4),
        upload_timestamp=kw.pop("upload_timestamp", f"2026-03-{(i % 28) + 1:02d}T10:00:00"),
        **kw,
    )


@pytest.fixture()
def store(tmp_path):
    s = MetadataStore(tmp_path / "metadata.db")
    _add(s, 1, "Policy.PDF", file_size=1000, source_format="pdf", sensitivity="internal")
    _add(s, 2, "handbook.pdf", file_size=5000, source_format="pdf")
    _add(s, 3, "notes.md", file_size=200, source_format="md", sensitivity="public")
    _add(s, 4, "budget.xlsx", file_size=9000, num_chunks=0, source_format="xlsx")
    _add(s, 5, "main.py", file_size=300, source_format="py", source_type="local_reference",
         source_path="/src/main.py")
    _add(s, 6, "Makefile", file_size=50)
    _add(s, 7, "legacy.weird", file_size=None)                      # no recorded size
    _add(s, 8, "secret.pdf", file_size=777, policy_status="quarantined")
    return s


# ── stats ──────────────────────────────────────────────────────────────

def test_totals_are_exact_and_exclude_quarantined(store):
    st = store.collection_stats()
    assert st["total_documents"] == 7                  # 8 rows, 1 quarantined
    assert st["total_pages"] == 14 and st["total_chunks"] == 24
    assert st["total_bytes"] == 1000 + 5000 + 200 + 9000 + 300 + 50
    assert st["documents_without_recorded_size"] == 1
    assert st["documents_without_chunks"] == 1          # the spreadsheet
    assert st["oldest_upload"] < st["newest_upload"]


def test_quarantined_file_leaks_into_no_breakdown(store):
    st = store.collection_stats()
    assert sum(r["documents"] for r in st["by_extension"]) == 7
    assert "secret.pdf" not in json.dumps(st)
    assert all(d["filename"] != "secret.pdf" for d in st["largest_documents"])
    assert sum(st["by_sensitivity"].values()) == 7
    assert sum(st["by_source_type"].values()) == 7


def test_extension_table_is_case_folded_and_sorted(store):
    st = store.collection_stats()
    rows = {r["extension"]: r for r in st["by_extension"]}
    assert rows[".pdf"]["documents"] == 2               # Policy.PDF + handbook.pdf
    assert rows[".pdf"]["bytes"] == 6000
    assert rows[".pdf"]["chunks"] == 8
    assert st["by_extension"][0]["extension"] == ".pdf"  # most common first
    assert st["distinct_extensions"] == len(rows)


def test_kinds_sensitivity_and_source_types(store):
    st = store.collection_stats()
    assert st["by_kind"]["docs"] == 3                    # 2 pdf + md
    assert st["by_kind"]["data"] == 1                    # xlsx
    assert st["by_kind"]["code"] >= 1                    # main.py
    assert st["by_sensitivity"] == {"unlabeled": 4, "internal": 1, "public": 1} or \
        st["by_sensitivity"]["internal"] == 1
    assert st["by_source_type"]["local_reference"] == 1
    assert st["largest_documents"][0] == {
        "document_id": "doc00004", "filename": "budget.xlsx", "bytes": 9000}
    assert len(st["largest_documents"]) <= 5


def test_stats_limited_to_a_selection(store):
    st = store.collection_stats(document_ids=["doc00001", "doc00003", "doc00008"])
    assert st["total_documents"] == 2                    # quarantined stays out even if selected
    assert st["total_bytes"] == 1200
    assert store.collection_stats(document_ids=[])["total_documents"] == 0


def test_stats_on_empty_store(tmp_path):
    st = MetadataStore(tmp_path / "e.db").collection_stats()
    assert st["total_documents"] == 0 and st["total_bytes"] == 0
    assert st["oldest_upload"] is None and st["by_extension"] == []
    assert st["by_kind"] == {} and st["largest_documents"] == []


# ── find_documents ─────────────────────────────────────────────────────

def test_default_listing_is_newest_first_without_quarantined(store):
    rows, total = store.find_documents()
    assert total == 7 and len(rows) == 7
    assert "secret.pdf" not in [r["filename"] for r in rows]
    stamps = [r["upload_timestamp"] for r in rows]
    assert stamps == sorted(stamps, reverse=True)
    assert "file_size" in rows[0]


@pytest.mark.parametrize("sort,first,last", [
    ("name", "budget.xlsx", "Policy.PDF"),
    ("largest", "budget.xlsx", "legacy.weird"),          # unknown size sorts last
    ("smallest", "Makefile", "legacy.weird"),            # ...and also last when ascending
])
def test_sorts(store, sort, first, last):
    rows, _ = store.find_documents(sort=sort)
    names = [r["filename"] for r in rows]
    assert names[0] == first and names[-1] == last


def test_name_sort_is_case_insensitive(store):
    names = [r["filename"] for r in store.find_documents(sort="name")[0]]
    assert names == sorted(names, key=str.lower)
    assert [r["filename"] for r in store.find_documents(sort="name_desc")[0]] == names[::-1]


def test_unknown_sort_is_rejected(store):
    with pytest.raises(ValueError, match="Unknown sort"):
        store.find_documents(sort="; DROP TABLE documents")


def test_pagination_walks_every_document_exactly_once(store):
    seen, offset = [], 0
    while True:
        rows, total = store.find_documents(limit=3, offset=offset, sort="name")
        if not rows:
            break
        seen += [r["document_id"] for r in rows]
        offset += len(rows)
    assert len(seen) == len(set(seen)) == total == 7
    assert store.find_documents(limit=3, offset=100)[0] == []


def test_filters_combine_and_total_reflects_them(store):
    rows, total = store.find_documents(keys=[".pdf"])
    assert total == 2 and {r["filename"] for r in rows} == {"Policy.PDF", "handbook.pdf"}
    rows, total = store.find_documents(q="POLICY", keys=[".pdf"])
    assert total == 1
    assert store.find_documents(q="%")[1] == 0           # LIKE wildcards are literal
    assert store.find_documents(q="_")[1] == 0
    assert store.find_documents(document_ids=["doc00002", "doc00005"], keys=[".pdf"])[1] == 1


def test_limit_zero_still_counts(store):
    rows, total = store.find_documents(limit=0)
    assert rows == [] and total == 7


def test_selection_with_thousands_of_ids(store):
    ids = [f"missing{i}" for i in range(5000)] + ["doc00001"]   # past SQLite's IN() batch size
    rows, total = store.find_documents(document_ids=ids)
    assert total == 1 and rows[0]["document_id"] == "doc00001"


def test_filename_with_quotes_and_unicode(tmp_path):
    s = MetadataStore(tmp_path / "u.db")
    _add(s, 1, "O'Brien \"Q3\" – résumé 日本語.pdf", file_size=1)
    rows, total = s.find_documents(q="résumé")
    assert total == 1 and rows[0]["filename"].startswith("O'Brien")


def test_ten_thousand_documents_stay_fast(tmp_path):
    s = MetadataStore(tmp_path / "big.db")
    import sqlite3
    with sqlite3.connect(tmp_path / "big.db") as conn:
        conn.executemany(
            "INSERT INTO documents (document_id, filename, num_pages, num_chunks, upload_timestamp, file_size) "
            "VALUES (?,?,?,?,?,?)",
            [(f"d{i}", f"file-{i:05d}.{['pdf', 'md', 'py', 'csv'][i % 4]}", 1, 2,
              f"2026-01-01T00:{i // 60 % 60:02d}:{i % 60:02d}", i) for i in range(10_000)])
    t0 = time.perf_counter()
    st = s.collection_stats()
    rows, total = s.find_documents(limit=500, offset=9_000, sort="largest")
    elapsed = time.perf_counter() - t0
    assert st["total_documents"] == total == 10_000
    assert st["total_bytes"] == sum(range(10_000))
    assert len(rows) == 500
    assert elapsed < 2.0, f"stats+page over 10k documents took {elapsed:.2f}s"


# ── file type translation ──────────────────────────────────────────────

def test_keys_for_file_type():
    assert file_kinds.keys_for_file_type("", ()) is None
    assert file_kinds.keys_for_file_type(None, ()) is None
    assert file_kinds.keys_for_file_type("PDF", ()) == [".pdf"]
    assert file_kinds.keys_for_file_type(" .Md ", ()) == [".md"]
    assert ".pdf" in file_kinds.keys_for_file_type("docs", ())
    assert ".csv" in file_kinds.keys_for_file_type("data", ())
    other = file_kinds.keys_for_file_type("other", [".pdf", ".weird", ".py"])
    assert other == [".weird"]


# ── MCP payloads ───────────────────────────────────────────────────────

class _FakeIndexer:
    def __init__(self, store):
        self.vector_store = SimpleNamespace(metadata_store=store)
        self._s = store

    def collection_stats(self, **kw):
        return self._s.collection_stats(**kw)

    def find_documents(self, **kw):
        return self._s.find_documents(**kw)


@pytest.fixture()
def mcp(store, monkeypatch):
    from services import mcp_server

    monkeypatch.setattr(mcp_server, "_resolve_collection_id", lambda c=None: c or "default")
    monkeypatch.setattr(mcp_server.indexer_manager, "get_indexer", lambda cid: _FakeIndexer(store))
    monkeypatch.setattr(mcp_server, "_corpus_version", lambda cid: "v1")
    monkeypatch.setattr(mcp_server.collection_service, "get_collection",
                        lambda cid: {"name": "Docs", "description": ""})
    return mcp_server


def test_mcp_stats_payload(mcp):
    p = mcp.collection_stats_payload("default")
    assert p["total_documents"] == 7 and p["corpus_version"] == "v1"
    assert "scope" not in p
    scoped = mcp.collection_stats_payload("default", ["doc00001"])
    assert scoped["total_documents"] == 1 and "selected" in scoped["scope"]


def test_mcp_list_walks_with_next_offset(mcp):
    seen, offset = [], 0
    for _ in range(10):
        p = mcp.list_documents_payload("default", limit=3, offset=offset, sort="name")
        seen += [d["filename"] for d in p["documents"]]
        assert p["total_matching"] == 7
        if p["next_offset"] is None:
            break
        offset = p["next_offset"]
    assert len(seen) == 7 and p["next_offset"] is None


def test_mcp_list_filters_and_shape(mcp):
    p = mcp.list_documents_payload("default", file_type="pdf", sort="largest")
    assert [d["filename"] for d in p["documents"]] == ["handbook.pdf", "Policy.PDF"]
    d = p["documents"][0]
    assert set(d) >= {"document_id", "filename", "bytes", "pages", "chunks", "uploaded", "source_type"}
    p = mcp.list_documents_payload("default", file_type="code")
    assert [d["source_path"] for d in p["documents"] if d.get("source_path")] == ["/src/main.py"]
    assert mcp.list_documents_payload("default", file_type="other")["total_matching"] == 1  # .weird
    assert mcp.list_documents_payload("default", name_contains="nothing")["documents"] == []


def test_mcp_list_clamps_limit_and_respects_selection(mcp):
    assert mcp.list_documents_payload("default", limit=10_000)["returned"] == 7
    p = mcp.list_documents_payload("default", document_ids=["doc00002", "doc00008"])
    assert [d["filename"] for d in p["documents"]] == ["handbook.pdf"]      # quarantined never shown


def test_mcp_bad_sort_is_a_clear_error(mcp):
    with pytest.raises(ValueError, match="Unknown sort"):
        mcp.list_documents_payload("default", sort="bogus")


def test_collection_info_is_bounded(mcp, tmp_path, monkeypatch):
    big = MetadataStore(tmp_path / "many.db")
    import sqlite3
    with sqlite3.connect(tmp_path / "many.db") as conn:
        conn.executemany(
            "INSERT INTO documents (document_id, filename, num_pages, num_chunks, upload_timestamp) VALUES (?,?,?,?,?)",
            [(f"d{i}", f"f{i}.txt", 1, 1, f"2026-01-01T00:00:{i % 60:02d}") for i in range(450)])
    monkeypatch.setattr(mcp.indexer_manager, "get_indexer", lambda cid: _FakeIndexer(big))
    monkeypatch.setattr(mcp.indexer_manager, "get_collection_stats",
                        lambda cid: {"total_documents": 450, "total_chunks": 450, "total_pages": 450})
    out = mcp.get_collection_info.__wrapped__("default") if hasattr(mcp.get_collection_info, "__wrapped__") \
        else mcp.get_collection_info("default")
    out = out if isinstance(out, dict) else json.loads(out)
    assert len(out["documents"]) == mcp._INFO_DOCUMENT_CAP
    assert out["documents_truncated"] is True and "list_documents" in out["documents_note"]
    assert out["file_types"] == {"txt": 450}


# ── REST: stats and export ─────────────────────────────────────────────

@pytest.fixture()
def client(store, monkeypatch):
    import api.documents as docs_api

    def get_indexer(cid="default"):
        if cid == "nope":
            raise ValueError("Collection 'nope' not found")
        return _FakeIndexer(store)

    monkeypatch.setattr(docs_api, "get_indexer", get_indexer)
    audited = []
    monkeypatch.setattr(docs_api.audit, "record", lambda *a, **k: audited.append((a, k)))
    app = FastAPI()
    app.include_router(docs_api.router)
    c = TestClient(app)
    c.audited = audited
    return c


def test_stats_endpoint(client):
    r = client.get("/documents/stats", params={"collection_id": "default"})
    assert r.status_code == 200 and r.json()["total_documents"] == 7
    assert client.get("/documents/stats", params={"collection_id": "nope"}).status_code == 404


def test_export_csv_has_every_file_and_a_bom(client):
    r = client.get("/documents/export", params={"collection_id": "default"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="documents-default.csv"' in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert len(rows) == 7
    assert [x["filename"] for x in rows] == sorted((x["filename"] for x in rows), key=str.lower)
    assert "secret.pdf" not in r.text
    assert client.audited and client.audited[0][0][0] == "documents.export"


def test_export_jsonl_filtered(client):
    r = client.get("/documents/export", params={"format": "jsonl", "file_type": "pdf", "sort": "largest"})
    lines = [json.loads(x) for x in r.text.splitlines()]
    assert [x["filename"] for x in lines] == ["handbook.pdf", "Policy.PDF"]
    assert lines[0]["bytes"] == 5000


def test_export_pages_through_more_than_one_page(client, monkeypatch):
    import api.documents as docs_api
    monkeypatch.setattr(docs_api, "_EXPORT_PAGE", 2)
    rows = list(csv.DictReader(io.StringIO(client.get("/documents/export").content.decode("utf-8-sig"))))
    assert len(rows) == 7 and len({r["document_id"] for r in rows}) == 7


def test_export_neutralises_spreadsheet_formulas(tmp_path, monkeypatch):
    import api.documents as docs_api

    s = MetadataStore(tmp_path / "f.db")
    _add(s, 1, "=HYPERLINK(\"http://evil\",\"x\").txt", file_size=1)
    _add(s, 2, "+cmd.txt", file_size=1)
    _add(s, 3, "ok.txt", file_size=1)
    monkeypatch.setattr(docs_api, "get_indexer", lambda cid="default": _FakeIndexer(s))
    monkeypatch.setattr(docs_api.audit, "record", lambda *a, **k: None)
    app = FastAPI()
    app.include_router(docs_api.router)
    rows = list(csv.DictReader(io.StringIO(
        TestClient(app).get("/documents/export").content.decode("utf-8-sig"))))
    names = sorted(r["filename"] for r in rows)
    assert all(not n.startswith(("=", "+", "-", "@")) for n in names)
    assert "ok.txt" in names
    # Neutralising must not touch empty cells: they stay empty, not "'".
    ok = next(r for r in rows if r["filename"] == "ok.txt")
    assert ok["sensitivity"] == "" and ok["source_path"] == ""


def test_export_validation(client):
    assert client.get("/documents/export", params={"format": "xml"}).status_code == 400
    assert client.get("/documents/export", params={"sort": "bogus"}).status_code == 400
    assert client.get("/documents/export", params={"collection_id": "nope"}).status_code == 404
    empty = client.get("/documents/export", params={"q": "zzz"})
    assert empty.status_code == 200
    assert len(list(csv.DictReader(io.StringIO(empty.content.decode("utf-8-sig"))))) == 0

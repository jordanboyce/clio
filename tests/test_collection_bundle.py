"""Collection export/import bundles, and the embedding record they rely on.

Embeddings are faked (deterministic vectors, same harness as
tests/test_governance.py) so no model loads; the fake carries a signature
the way services built by create_embedding_service do.
"""

import io
import json
import sqlite3
import time
import zipfile

import pytest

import config
from services import collection_bundle as cb
from services import coverage_report
from services.app_database import SQLiteBackend, app_db
from services.collection_service import collection_service
from services.indexer_manager import indexer_manager
from services.research.store import ResearchStore
from services.structured_store import StructuredStore
from models.schemas import SearchMode
from tests.test_governance import FakeEmbedder

SIG = "local::fake-embed"


class SignedEmbedder(FakeEmbedder):
    signature = SIG


@pytest.fixture()
def env(tmp_path, monkeypatch):
    # Other test modules reload config, so the bundle module may hold an
    # older Settings object than config.settings: patch every one in play.
    for s in {id(config.settings): config.settings, id(cb.settings): cb.settings}.values():
        monkeypatch.setattr(s, "data_dir", tmp_path)
        monkeypatch.setattr(s, "embedding_provider", "local")
        monkeypatch.setattr(s, "embedding_model", "fake-embed")
    monkeypatch.setattr(app_db, "db_path", tmp_path / "app.db")
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    monkeypatch.setattr(collection_service, "base_dir", tmp_path / "collections")
    monkeypatch.setattr(indexer_manager, "_indexers", {})
    monkeypatch.setattr(indexer_manager, "_get_embedding_service", lambda model: SignedEmbedder())
    return tmp_path


def _source_collection(tmp_path, quarantine=False):
    """A collection with two indexed text files; optionally one quarantined."""
    col = collection_service.create_collection(name="Handbooks", embedding_model="fake-embed")
    cid = col["id"]
    indexer = indexer_manager.get_indexer(cid)
    docs_dir = collection_service.get_documents_path(cid)
    ids = []
    for name, text in [("holidays.txt", "Staff accrue 25 days of holiday a year. " * 20),
                       ("expenses.txt", "Expenses are reimbursed within thirty days. " * 20)]:
        path = docs_dir / name
        path.write_text(text, encoding="utf-8")
        ids.append(indexer.index_document(path, name, collection_id=cid).document_id)
    indexer.save_index()
    if quarantine:
        db = collection_service.get_indexes_path(cid) / "metadata.db"
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE documents SET policy_status='quarantined' WHERE document_id=?", (ids[1],))
    return cid, ids


def _wait(job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = app_db.get_upload_job(job_id)
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def test_save_records_what_produced_the_vectors(env):
    cid, _ = _source_collection(env)
    info = indexer_manager.get_indexer(cid).vector_store.metadata_store.get_index_info()
    assert info["signature"] == SIG
    assert info["dimension"] == FakeEmbedder.embedding_dim
    assert info["normalization"] == "l2"


def test_export_strips_quarantined_documents_and_records_the_model(env):
    cid, (kept, hidden) = _source_collection(env, quarantine=True)
    path, filename = cb.export_collection(cid, include_sources=True)
    assert filename == "Handbooks.clio.zip"
    with zipfile.ZipFile(path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        names = zf.namelist()
        zf.extract("indexes/metadata.db", env / "out")
    assert manifest["embedding"]["signature"] == SIG
    assert manifest["contents"]["documents"] == 1
    assert manifest["contents"]["quarantined_excluded"] == 1
    assert manifest["contents"]["vectors_included"] is True
    assert [d["document_id"] for d in manifest["documents"]] == [kept]
    assert f"sources/{kept}/holidays.txt" in names
    assert not any(hidden in n for n in names)
    with sqlite3.connect(env / "out" / "indexes" / "metadata.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM chunks WHERE document_id=?", (hidden,)).fetchone()[0] == 0


# ── Research rows follow their document out of a bundle ──────────────────────

# Every string here is written to the rows of the document that gets withheld.
HIDDEN_STRINGS = {
    "paper title": "Withheld-paper-title-quokka",
    "paper abstract": "Withheld-abstract-quokka",
    "reference": "Withheld-reference-entry-quokka",
    "assessment summary": "Withheld-summary-quokka",
    "assessment evidence": "Withheld-evidence-quote-quokka",
    "assessment retrieval": "Withheld-retrieved-passage-quokka",
    "brief": "Withheld-brief-statement-quokka",
    "job question": "Withheld-job-question-quokka",
    "job error": "Withheld-job-error-quokka",
    "match reason": "Withheld-match-reason-quokka",
}


def _research_store(cid):
    return ResearchStore(collection_service.get_indexes_path(cid) / "metadata.db")


def _seed_research(cid, kept, hidden):
    """Research on both documents, with the kept paper citing the hidden one."""
    store = _research_store(cid)
    store.upsert_paper(hidden, title=HIDDEN_STRINGS["paper title"],
                       abstract=HIDDEN_STRINGS["paper abstract"], original_filename="expenses.txt")
    store.replace_references(hidden, [{"kind": "reference", "raw_text": HIDDEN_STRINGS["reference"]}])
    store.add_assessment(hidden, "Is it relevant?", {
        "verdict": "relevant", "summary": HIDDEN_STRINGS["assessment summary"],
        "evidence": [{"point": "p", "quote": HIDDEN_STRINGS["assessment evidence"]}],
        "retrieval": [{"text": HIDDEN_STRINGS["assessment retrieval"]}],
    })
    store.add_brief(hidden, "brief", {"sections": [{"statements": [{"text": HIDDEN_STRINGS["brief"]}]}]})

    # A job on the hidden paper alone, and a batch that covered both.
    store.create_job("job-hidden", "brief", {"question": HIDDEN_STRINGS["job question"],
                                             "document_ids": [hidden]}, total=1)
    store.create_job("job-mixed", "brief", {"document_ids": [kept, hidden]}, total=2)
    store.update_job("job-mixed", current="expenses.txt", errors=[
        {"document_id": hidden, "filename": "expenses.txt", "error": HIDDEN_STRINGS["job error"]}])

    store.upsert_paper(kept, title="Kept paper title")
    store.add_brief(kept, "brief", {"sections": [{"statements": [{"text": "Kept brief statement"}]}]})
    store.replace_references(kept, [
        {"kind": "reference", "ordinal": 1, "raw_text": "Kept entry citing the hidden paper",
         "matched_document_id": hidden, "match_reason": HIDDEN_STRINGS["match reason"],
         "status": "in_library"},
        {"kind": "reference", "ordinal": 2, "raw_text": "Kept entry with a download link",
         "resolved_url": "https://example.org/a.pdf", "matched_document_id": hidden,
         "match_reason": HIDDEN_STRINGS["match reason"], "status": "in_library"},
    ])
    store.create_job("job-kept", "analyze", {"document_ids": [kept]}, total=1)
    # A budget stop names no document, so it is no reason to drop the row.
    store.update_job("job-kept", errors=[{"document_id": None, "filename": None, "error": "budget"}])
    # What this row covers cannot be read, so nothing vouches for it.
    store.create_job("job-unreadable", "brief", {}, total=1)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE research_jobs SET params = 'not json' WHERE job_id = 'job-unreadable'")


def _exported_db(path, out_dir):
    """The exported metadata.db: its raw bytes, and a path to query it at."""
    with zipfile.ZipFile(path) as zf:
        raw = zf.read("indexes/metadata.db")
    out_dir.mkdir()
    db = out_dir / "metadata.db"
    db.write_bytes(raw)
    return raw, db


def _assert_no_hidden_research(db, hidden):
    with sqlite3.connect(db) as conn:
        for table in ("research_papers", "research_references", "research_assessments", "research_briefs"):
            rows = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE document_id=?", (hidden,)).fetchone()[0]
            assert rows == 0, table
        assert {r[0] for r in conn.execute("SELECT job_id FROM research_jobs")} == {"job-kept"}
        references = conn.execute(
            "SELECT raw_text, matched_document_id, match_reason, status FROM research_references "
            "ORDER BY ordinal").fetchall()
    # The kept paper's own reference list stays; only the pointer at the
    # withheld paper goes, and each entry falls back to what it can still do.
    assert references == [("Kept entry citing the hidden paper", None, None, "parsed"),
                          ("Kept entry with a download link", None, None, "resolved")]


def test_export_leaves_out_research_on_a_quarantined_document(env):
    cid, (kept, hidden) = _source_collection(env, quarantine=True)
    _seed_research(cid, kept, hidden)
    path, _ = cb.export_collection(cid)
    with zipfile.ZipFile(path) as zf:
        assert json.loads(zf.read("manifest.json"))["contents"]["quarantined_excluded"] == 1

    raw, db = _exported_db(path, env / "out")
    # The raw file, not a query: a deleted row must not survive in a free page.
    leaked = [what for what, text in HIDDEN_STRINGS.items() if text.encode() in raw]
    assert not leaked, f"left in the exported metadata.db: {leaked}"
    assert hidden.encode() not in raw
    assert b"expenses.txt" not in raw

    _assert_no_hidden_research(db, hidden)
    store = _research_store(cid)  # the collection itself is untouched
    assert store.get_paper(hidden) and store.list_briefs(document_id=hidden)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT title FROM research_papers").fetchall() == [("Kept paper title",)]
        assert "Kept brief statement" in conn.execute("SELECT content FROM research_briefs").fetchone()[0]


def test_export_leaves_out_research_on_a_deleted_document(env):
    # Nothing is hidden, so nothing is stripped: the rows a deleted document
    # left behind must still not travel.
    cid, (kept, gone) = _source_collection(env)
    _seed_research(cid, kept, gone)
    indexer_manager.get_indexer(cid).vector_store.metadata_store.delete_document(gone)
    path, _ = cb.export_collection(cid)
    raw, db = _exported_db(path, env / "out")
    assert not [what for what, text in HIDDEN_STRINGS.items() if text.encode() in raw]
    _assert_no_hidden_research(db, gone)


def test_import_leaves_out_research_on_a_blocklisted_document(env):
    cid, (kept, blocked) = _source_collection(env)
    _seed_research(cid, kept, blocked)
    path, _ = cb.export_collection(cid)
    with zipfile.ZipFile(path) as zf:
        entries = json.loads(zf.read("manifest.json"))["documents"]
    content_hash = next(d["content_hash"] for d in entries if d["document_id"] == blocked)
    app_db.add_blocked_hash(content_hash, "admin", "test", "expenses.txt")

    result = cb.import_collection(path, owner_id="default", name="Copy")
    assert result["documents_blocked"] == 1
    assert _wait(result["job_id"])["status"] == "completed"
    _assert_no_hidden_research(collection_service.get_indexes_path(result["id"]) / "metadata.db", blocked)


def test_import_drops_research_an_older_bundle_carried_for_an_absent_document(env):
    # Bundles exported before this fix hold rows for documents they stripped:
    # build one by taking the document out of a clean bundle's database.
    cid, (kept, hidden) = _source_collection(env)
    _seed_research(cid, kept, hidden)
    path, _ = cb.export_collection(cid)
    _, db = _exported_db(path, env / "out")
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM chunks WHERE document_id=?", (hidden,))
    conn.execute("DELETE FROM documents WHERE document_id=?", (hidden,))
    conn.commit()
    conn.close()  # a WAL database: only closing puts the deletes in the file zipped below
    older = env / "older.clio.zip"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(older, "w") as dst:
        dst.writestr("manifest.json", src.read("manifest.json"))
        dst.write(db, "indexes/metadata.db")

    result = cb.import_collection(older, owner_id="default", name="Copy")
    assert result["documents_blocked"] == 0
    assert _wait(result["job_id"])["status"] == "completed"
    _assert_no_hidden_research(collection_service.get_indexes_path(result["id"]) / "metadata.db", hidden)


def test_export_tolerates_a_database_that_predates_the_research_tables(env):
    cid, (kept, hidden) = _source_collection(env, quarantine=True)
    db = collection_service.get_indexes_path(cid) / "metadata.db"
    with sqlite3.connect(db) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'research_%'")]
    assert tables == []
    path, _ = cb.export_collection(cid)
    with zipfile.ZipFile(path) as zf:
        assert json.loads(zf.read("manifest.json"))["contents"]["quarantined_excluded"] == 1


def test_export_tolerates_a_database_holding_only_some_research_tables(env):
    cid, (kept, hidden) = _source_collection(env, quarantine=True)
    _seed_research(cid, kept, hidden)
    db = collection_service.get_indexes_path(cid) / "metadata.db"
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE research_references")
        conn.execute("DROP TABLE research_jobs")
    path, _ = cb.export_collection(cid)
    raw, _ = _exported_db(path, env / "out")
    assert HIDDEN_STRINGS["brief"].encode() not in raw


# ── So does everything else the collection worked out about it ───────────────

DERIVED_STRINGS = {
    "entity": "Withheld-entity-name-wombat",
    "boost note": "Withheld-boost-note-wombat",
    "owner question": "Withheld-owner-question-wombat",
    "shared question": "Withheld-shared-question-wombat",
    "superseded note": "Withheld-superseded-note-wombat",
    "table cell": "Withheld-table-cell-wombat",
    "view column": "withheld_view_column_wombat",
}
HIDDEN_TEXT = b"Expenses are reimbursed"


def _seed_derived(cid, kept, hidden):
    """What a collection holds about its documents besides chunks and research."""
    indexer = indexer_manager.get_indexer(cid)
    store = indexer.vector_store.metadata_store
    db = collection_service.get_indexes_path(cid) / "metadata.db"

    # A real run while the document is still servable, so the report really
    # does describe it (its previews and probe queries come from its text).
    report = coverage_report.run_coverage(indexer, probes=20, seed=1, use_llm=False)
    assert hidden in json.dumps(report)
    store.save_coverage_report(report)

    conn = sqlite3.connect(db)
    chunk_of = {doc: conn.execute("SELECT chunk_id FROM chunks WHERE document_id=?", (doc,)).fetchone()[0]
                for doc in (kept, hidden)}
    conn.close()
    store.add_chunk_entities({chunk_of[hidden]: [DERIVED_STRINGS["entity"]], chunk_of[kept]: ["Kept Entity"]})
    store.set_chunk_boost(chunk_of[hidden], 0.5, DERIVED_STRINGS["boost note"])
    store.set_chunk_boost(chunk_of[kept], 0.5, "Kept boost note")

    hidden_ref = {"document_id": hidden, "filename": "expenses.txt"}
    kept_ref = {"document_id": kept, "filename": "holidays.txt"}
    store.add_coverage_question(DERIVED_STRINGS["owner question"], [hidden_ref])
    store.add_coverage_question(DERIVED_STRINGS["shared question"], [kept_ref, hidden_ref])
    store.add_coverage_question("Kept question", [kept_ref])
    unreadable = store.add_coverage_question("Unreadable question", [kept_ref])

    table = StructuredStore(db).create_table(
        hidden, "expenses.txt", ["Cost"], [{"Cost": DERIVED_STRINGS["table cell"]}])["table_name"]
    conn = sqlite3.connect(db)
    conn.execute(f'CREATE VIEW "{table}__by_symbol" AS SELECT 1 AS {DERIVED_STRINGS["view column"]}')
    conn.execute("UPDATE coverage_questions SET expected_documents = 'not json' WHERE id = ?", (unreadable,))
    conn.execute("UPDATE documents SET superseded_by = ?, superseded_at = 'then', superseded_note = ? "
                 "WHERE document_id = ?", (hidden, DERIVED_STRINGS["superseded note"], kept))
    conn.commit()
    conn.close()
    return table


def _assert_nothing_derived_left(db):
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM coverage_report").fetchone()[0] == 0
        assert [r[0] for r in conn.execute("SELECT question FROM coverage_questions")] == ["Kept question"]
        assert [r[0] for r in conn.execute("SELECT entity FROM chunk_entities")] == ["Kept Entity"]
        assert [r[0] for r in conn.execute("SELECT note FROM chunk_boosts")] == ["Kept boost note"]
        assert conn.execute("SELECT superseded_by, superseded_at, superseded_note FROM documents").fetchall() \
            == [(None, None, None)]
    finally:
        conn.close()


def _withheld_in(raw):
    return [what for what, text in DERIVED_STRINGS.items() if text.encode() in raw]


def test_export_leaves_out_what_was_derived_from_a_quarantined_document(env):
    cid, (kept, hidden) = _source_collection(env)
    _seed_derived(cid, kept, hidden)
    db = collection_service.get_indexes_path(cid) / "metadata.db"
    conn = sqlite3.connect(db)
    conn.execute("UPDATE documents SET policy_status='quarantined' WHERE document_id=?", (hidden,))
    conn.commit()
    conn.close()

    path, _ = cb.export_collection(cid)
    raw, exported = _exported_db(path, env / "out")
    assert _withheld_in(raw) == []
    assert hidden.encode() not in raw
    assert hidden.replace("-", "_").encode() not in raw  # as it appears in a table or view name
    assert b"expenses.txt" not in raw
    assert HIDDEN_TEXT not in raw
    _assert_nothing_derived_left(exported)


def test_export_leaves_out_what_a_deleted_document_is_still_named_in(env):
    # The report and questions outlive a deletion on purpose, so nothing
    # stripped them: the bundle has to notice they name an absent document.
    cid, (kept, gone) = _source_collection(env)
    _seed_derived(cid, kept, gone)
    indexer_manager.get_indexer(cid).vector_store.delete_document(gone)
    path, _ = cb.export_collection(cid)
    raw, exported = _exported_db(path, env / "out")
    assert _withheld_in(raw) == []
    assert b"expenses.txt" not in raw
    _assert_nothing_derived_left(exported)


def test_export_with_nothing_withheld_keeps_the_coverage_work(env):
    cid, (first, second) = _source_collection(env)
    store = indexer_manager.get_indexer(cid).vector_store.metadata_store
    store.save_coverage_report(coverage_report.run_coverage(
        indexer_manager.get_indexer(cid), probes=20, seed=1, use_llm=False))
    store.add_coverage_question("Which expenses?", [{"document_id": second, "filename": "expenses.txt"}])
    path, _ = cb.export_collection(cid)
    _, exported = _exported_db(path, env / "out")
    conn = sqlite3.connect(exported)
    assert conn.execute("SELECT COUNT(*) FROM coverage_report").fetchone()[0] == 1
    assert [r[0] for r in conn.execute("SELECT question FROM coverage_questions")] == ["Which expenses?"]
    conn.close()


def test_import_leaves_no_trace_of_a_blocklisted_document_in_the_file(env):
    cid, (kept, blocked) = _source_collection(env)
    _seed_research(cid, kept, blocked)
    _seed_derived(cid, kept, blocked)
    path, _ = cb.export_collection(cid)
    with zipfile.ZipFile(path) as zf:
        entries = json.loads(zf.read("manifest.json"))["documents"]
    app_db.add_blocked_hash(next(d["content_hash"] for d in entries if d["document_id"] == blocked),
                            "admin", "test", "expenses.txt")

    result = cb.import_collection(path, owner_id="default", name="Copy")
    assert result["documents_blocked"] == 1
    assert _wait(result["job_id"])["status"] == "completed"
    db = collection_service.get_indexes_path(result["id"]) / "metadata.db"
    _assert_nothing_derived_left(db)
    # The raw file again: deleted rows sit in free pages until a VACUUM.
    raw = db.read_bytes()
    assert _withheld_in(raw) == []
    assert not [what for what, text in HIDDEN_STRINGS.items() if text.encode() in raw]
    assert HIDDEN_TEXT not in raw


def test_strip_tolerates_a_database_from_before_the_later_tables(tmp_path):
    db = tmp_path / "metadata.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE documents (document_id TEXT PRIMARY KEY, filename TEXT)")
    conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, chunk_id TEXT, document_id TEXT)")
    conn.execute("INSERT INTO documents VALUES ('a', 'a.txt'), ('b', 'b.txt')")
    conn.execute("INSERT INTO chunks VALUES (1, 'a-0', 'a'), (2, 'b-0', 'b')")
    conn.commit()
    conn.close()
    assert cb._strip_documents(db, ["b"]) == [2]
    assert cb._strip_documents(db, []) == []
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT document_id FROM documents").fetchall() == [("a",)]
    conn.close()


def test_import_with_matching_model_reuses_vectors(env):
    cid, _ = _source_collection(env)
    path, _ = cb.export_collection(cid, include_sources=True)
    result = cb.import_collection(path, owner_id="default", name="Copy")
    assert result["vectors"] == "reused" and result["sources_imported"] == 2
    job = _wait(result["job_id"])
    assert job["status"] == "completed", job
    assert json.loads(job["result_summary"])["vectors"] == "reused"

    new = indexer_manager.get_indexer(result["id"])
    assert new.vector_store.index.ntotal == new.vector_store.metadata_store.get_total_chunks() > 0
    # Fake vectors are hashes of the text, so a chunk's own text must find
    # that exact chunk first - proof each reused vector kept its chunk.
    chunk = new.vector_store.metadata_store.get_all_chunks_ordered()[-1]
    hits = new.search(chunk["text"], top_k=1, mode=SearchMode.SEMANTIC)["results"]
    assert hits[0].text_snippet.startswith(chunk["text"][:40])
    assert (collection_service.get_documents_path(result["id"]) / "holidays.txt").exists()


def test_import_with_another_model_re_embeds_from_chunk_text(env, monkeypatch):
    cid, _ = _source_collection(env)
    path, _ = cb.export_collection(cid)  # no originals
    import services.embedder as embedder
    monkeypatch.setattr(embedder, "service_signature", lambda **kw: "openrouter::x::other")
    result = cb.import_collection(path, owner_id="default")
    assert result["vectors"] == "re-embed" and result["sources_imported"] == 0
    job = _wait(result["job_id"])
    assert job["status"] == "completed", job
    assert json.loads(job["result_summary"])["vectors"] == "re-embedded"
    new = indexer_manager.get_indexer(result["id"])
    assert new.vector_store.index.ntotal == new.vector_store.metadata_store.get_total_chunks()
    chunk = new.vector_store.metadata_store.get_all_chunks_ordered()[0]
    hits = new.search(chunk["text"], top_k=1, mode=SearchMode.SEMANTIC)["results"]
    assert hits[0].text_snippet.startswith(chunk["text"][:40])


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.mark.parametrize("entries, message", [
    ({"manifest.json": "{}"}, "manifest.json or metadata.db is missing"),
    ({"manifest.json": json.dumps({"format": "clio-collection", "version": 99}),
      "indexes/metadata.db": b""}, "newer Clio"),
    ({"manifest.json": json.dumps({"format": "clio-collection", "version": 1}),
      "indexes/metadata.db": b"", "sources/../../evil.py": b""}, "unsafe path"),
    ({"manifest.json": json.dumps({"format": "clio-collection", "version": 1}),
      "indexes/metadata.db": b"", "main.py": b""}, "unexpected file"),
])
def test_bad_bundles_are_refused_before_anything_is_created(env, entries, message):
    path = env / "bad.zip"
    path.write_bytes(_zip(entries))
    before = len(app_db.get_all_collections())
    with pytest.raises(cb.BundleError, match=message):
        cb.import_collection(path, owner_id="default")
    assert len(app_db.get_all_collections()) == before


def test_not_a_zip_is_refused(env):
    path = env / "notes.txt"
    path.write_text("hello")
    with pytest.raises(cb.BundleError, match="not a Clio collection export"):
        cb.read_manifest(path)


def test_http_round_trip(env, monkeypatch):
    from fastapi.testclient import TestClient
    from api import deps
    import main

    monkeypatch.setattr(deps, "_initialized", True)
    client = TestClient(main.app)
    cid, _ = _source_collection(env)

    r = client.get(f"/api/collections/{cid}/export", params={"include_sources": "true"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert "Handbooks.clio.zip" in r.headers["content-disposition"]
    assert not list((env / "tmp").glob("*.zip"))  # removed after sending

    r = client.post("/api/collections/import", data={"name": "Shared handbooks"},
                    files={"file": ("Handbooks.clio.zip", r.content, "application/zip")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Shared handbooks" and body["vectors"] == "reused"
    assert _wait(body["job_id"])["status"] == "completed"

    r = client.post("/api/collections/import",
                    files={"file": ("x.zip", b"not a zip", "application/zip")})
    assert r.status_code == 400 and "not a Clio collection export" in r.json()["detail"]


# ── Saying which model indexed it, and previewing before importing ───────────


def test_manifest_says_which_model_in_words(env):
    cid, _ = _source_collection(env)
    path, _ = cb.export_collection(cid)
    with zipfile.ZipFile(path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    emb = manifest["embedding"]
    assert emb["signature"] == SIG and emb["recorded"] is True
    assert emb["label"] == f"fake-embed · on this machine · {FakeEmbedder.embedding_dim} dimensions"
    assert manifest["clio_version"]


def test_describe_model_falls_back_to_document_records_and_admits_it():
    info = cb.describe_model({}, [{"embedding_model": "old-model"}, {"embedding_model": "old-model"}])
    assert info["model"] == "old-model" and info["recorded"] is False
    assert cb.describe_model({}, [])["label"] == "unknown model"
    remote = cb.describe_model({"signature": "openrouter::https://x::text-embed", "dimension": 1536})
    assert remote["model"] == "text-embed" and remote["label"] == "text-embed · via openrouter · 1536 dimensions"


def test_export_preview_matches_what_export_builds(env):
    cid, _ = _source_collection(env, quarantine=True)
    preview = cb.export_preview(cid)
    assert preview["documents"] == 1 and preview["quarantined_excluded"] == 1
    assert preview["embedding"]["model"] == "fake-embed" and preview["vectors_included"] is True
    path, _ = cb.export_collection(cid)
    with zipfile.ZipFile(path) as zf:
        contents = json.loads(zf.read("manifest.json"))["contents"]
    assert (preview["documents"], preview["chunks"]) == (contents["documents"], contents["chunks"])


def test_inspect_says_reuse_for_the_same_model_and_re_embed_otherwise(env, monkeypatch):
    cid, _ = _source_collection(env)
    path, _ = cb.export_collection(cid)
    before = len(app_db.get_all_collections())

    same = cb.inspect_bundle(path)
    assert same["action"] == "reuse" and "reused" in same["reason"]
    assert same["name"] == "Handbooks"
    assert same["embedding"]["model"] == "fake-embed" and same["documents"] == 2

    import services.embedder as embedder
    monkeypatch.setattr(embedder, "service_signature", lambda **kw: "openrouter::x::other")
    other = cb.inspect_bundle(path)
    assert other["action"] == "re-embed" and "different model" in other["reason"]
    assert len(app_db.get_all_collections()) == before  # inspecting creates nothing


def test_inspect_reports_documents_this_server_has_blocklisted(env):
    cid, ids = _source_collection(env)
    path, _ = cb.export_collection(cid)
    with zipfile.ZipFile(path) as zf:
        content_hash = json.loads(zf.read("manifest.json"))["documents"][0]["content_hash"]
    app_db.add_blocked_hash(content_hash, "admin", "test", "x.txt")
    assert cb.inspect_bundle(path)["blocked_here"] == 1


def test_http_preview_then_confirm_without_uploading_twice(env, monkeypatch):
    from fastapi.testclient import TestClient
    from api import deps
    import main

    monkeypatch.setattr(deps, "_initialized", True)
    client = TestClient(main.app)
    cid, _ = _source_collection(env)

    pre = client.get(f"/api/collections/{cid}/export/preview").json()
    assert pre["embedding"]["label"].startswith("fake-embed") and pre["documents"] == 2

    bundle = client.get(f"/api/collections/{cid}/export").content
    r = client.post("/api/collections/import/preview", files={"file": ("h.clio.zip", bundle, "application/zip")})
    assert r.status_code == 200, r.text
    summary = r.json()
    assert summary["action"] == "reuse" and len(summary["upload_id"]) == 32
    staged = env / "tmp" / "staged" / f"{summary['upload_id']}.zip"
    assert staged.exists()

    r = client.post("/api/collections/import", data={"upload_id": summary["upload_id"], "name": "From preview"})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "From preview"
    assert _wait(r.json()["job_id"])["status"] == "completed"
    assert not staged.exists()  # consumed

    # An expired or invented id is refused plainly; so is "nothing at all".
    r = client.post("/api/collections/import", data={"upload_id": summary["upload_id"]})
    assert r.status_code == 400 and "expired" in r.json()["detail"]
    r = client.post("/api/collections/import", data={"upload_id": "../../etc/passwd"})
    assert r.status_code == 400
    assert client.post("/api/collections/import").status_code == 400


def test_preview_refuses_a_bad_bundle_and_keeps_nothing(env, monkeypatch):
    from fastapi.testclient import TestClient
    from api import deps
    import main

    monkeypatch.setattr(deps, "_initialized", True)
    r = TestClient(main.app).post("/api/collections/import/preview",
                                  files={"file": ("x.zip", b"nope", "application/zip")})
    assert r.status_code == 400
    assert not list((env / "tmp").glob("**/*.zip"))


def test_discard_removes_the_staged_bundle_and_sweep_removes_stale_ones(env, monkeypatch):
    from fastapi.testclient import TestClient
    from api import deps
    import main
    import os

    monkeypatch.setattr(deps, "_initialized", True)
    client = TestClient(main.app)
    cid, _ = _source_collection(env)
    bundle = client.get(f"/api/collections/{cid}/export").content
    uid = client.post("/api/collections/import/preview",
                      files={"file": ("h.zip", bundle, "application/zip")}).json()["upload_id"]
    assert client.delete(f"/api/collections/import/preview/{uid}").status_code == 200
    assert not (env / "tmp" / "staged" / f"{uid}.zip").exists()

    stale = cb._staged_dir() / ("a" * 32 + ".zip")
    stale.write_bytes(b"x")
    os.utime(stale, (0, 0))
    cb.sweep_staged()
    assert not stale.exists()

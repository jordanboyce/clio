"""'Replaced by' relationships and near-duplicate collapse.

Real SQLite, the real DocumentIndexer and the real HTTP routes; only the
embedding model is faked (deterministic vectors), so ranking assertions use
keyword (BM25) retrieval, which is exact. Corpus: a policy set where v3
replaces v2, a boilerplate paragraph copied into several files, and a few
unrelated documents to backfill with.
"""

import hashlib
import sqlite3

import numpy as np
import pytest
from fastapi.testclient import TestClient

import config
from models.schemas import SearchMode
from services import supersede
from services.app_database import SQLiteBackend, app_db
from services.chunker import TextChunker
from services.collection_service import collection_service
from services.document_extractor import DocumentExtractor
from services.indexer_manager import indexer_manager
from services.indexing import DocumentIndexer
from services.metadata_store import SCHEMA_VERSION, MetadataStore
from services.vector_store import VectorStore

EMBED_DIM = 8

V2 = ("Expense policy version two with legacy rates. Employees must submit expense claims within "
      "30 days of purchase to receive reimbursement from the finance department.")
V3 = ("Expense policy version three with current rates. Employees must submit expense claims within "
      "45 days of purchase to receive reimbursement from the finance department.")
BOILERPLATE = ("Confidentiality notice. All staff handling personal data must follow "
               "the data protection procedures and report any breach to the security office immediately.")
OTHERS = [
    "Data protection training is scheduled every quarter for new staff members.",
    "The data protection officer reviews each breach report within two working days.",
    "Security office hours are posted on the intranet next to the data protection page.",
    "Personal data requests are logged by the privacy team and answered in writing.",
]


class FakeEmbedder:
    model_name = "fake-embed"
    embedding_dim = EMBED_DIM

    @staticmethod
    def _vec(text):
        digest = hashlib.sha256(text.encode()).digest()
        return [b / 255.0 + 0.01 for b in digest[:EMBED_DIM]]

    def embed_texts(self, texts, progress_callback=None):
        if progress_callback:
            progress_callback(len(texts), len(texts))
        return np.array([self._vec(t) for t in texts], dtype=np.float32)

    def embed_query(self, query):
        return np.array(self._vec(query), dtype=np.float32)


@pytest.fixture()
def fresh_db(tmp_path):
    old_db_path, old_base = app_db.db_path, collection_service.base_dir
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    collection_service.base_dir = tmp_path / "collections"
    yield app_db
    app_db.db_path, collection_service.base_dir = old_db_path, old_base


@pytest.fixture()
def ix(tmp_path, fresh_db, monkeypatch):
    indexer = DocumentIndexer(
        vector_store=VectorStore(index_dir=tmp_path / "indexes", embedding_dim=EMBED_DIM),
        embedding_service=FakeEmbedder(),
        document_extractor=DocumentExtractor(),
        text_chunker=TextChunker(chunk_size=400, chunk_overlap=40),
    )
    monkeypatch.setitem(indexer_manager._indexers, "default", indexer)
    from api import deps

    monkeypatch.setattr(deps, "_initialized", True)
    return indexer


def _add(ix, tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return ix.index_document(p, name, collection_id="default").document_id


def _search(ix, query, top_k=5, **filters):
    out = ix.search(query, top_k=top_k, mode=SearchMode.KEYWORD, filters=filters or None)
    return out["results"]


@pytest.fixture()
def corpus(ix, tmp_path):
    """v2 indexed BEFORE v3 so it wins every tie; three copies of the notice."""
    ids = {
        "v2": _add(ix, tmp_path, "expense_policy_v2.txt", V2),
        "v3": _add(ix, tmp_path, "expense_policy_v3.txt", V3),
        "notice_a": _add(ix, tmp_path, "handbook_a.txt", BOILERPLATE),
        "notice_b": _add(ix, tmp_path, "handbook_b.txt", BOILERPLATE.upper().replace("  ", " ")),
        "notice_c": _add(ix, tmp_path, "handbook_c.txt", BOILERPLATE + "\n"),
    }
    for i, text in enumerate(OTHERS):
        ids[f"other{i}"] = _add(ix, tmp_path, f"other_{i}.txt", text)
    return ids


# ── storage ─────────────────────────────────────────────────────────────────


def test_migration_from_3_7_adds_columns_and_keeps_rows(tmp_path):
    path = tmp_path / "meta.db"
    MetadataStore(path).add_document("old", "old.pdf", 1, 1, "2025-01-01T00:00:00")
    with sqlite3.connect(path) as conn:
        for col in ("superseded_by", "superseded_at", "superseded_note"):
            conn.execute(f"ALTER TABLE documents DROP COLUMN {col}")
        conn.execute("UPDATE schema_info SET value = '3.7' WHERE key = 'version'")
    store = MetadataStore(path)
    assert store.get_schema_version() == SCHEMA_VERSION == "3.8"
    assert store.get_document_info("old")["superseded_by"] is None
    store.add_document("new", "new.pdf", 1, 1, "2026-01-01T00:00:00")
    store.set_document_superseded("old", "new", "rewritten")
    assert store.get_document_info("old")["superseded_by"] == "new"


def test_set_rejects_self_cycle_and_unknown(tmp_path):
    s = MetadataStore(tmp_path / "m.db")
    for d in "abc":
        s.add_document(d, f"{d}.txt", 1, 1, "2026-01-01T00:00:00")
    with pytest.raises(ValueError):
        s.set_document_superseded("a", "a")
    with pytest.raises(KeyError):
        s.set_document_superseded("a", "nope")
    with pytest.raises(KeyError):
        s.set_document_superseded("nope", "a")
    s.set_document_superseded("a", "b")
    s.set_document_superseded("b", "c")
    with pytest.raises(ValueError):          # c -> a would close a -> b -> c -> a
        s.set_document_superseded("c", "a")
    with pytest.raises(ValueError):
        s.set_document_superseded("b", "a")


def test_reindex_of_old_document_keeps_the_mark(ix, corpus, tmp_path):
    store = ix.vector_store.metadata_store
    store.set_document_superseded(corpus["v2"], corpus["v3"], "new limits")
    again = _add(ix, tmp_path, "expense_policy_v2.txt", V2)   # same bytes, same id
    assert again == corpus["v2"]
    info = store.get_document_info(corpus["v2"])
    assert info["superseded_by"] == corpus["v3"] and info["superseded_note"] == "new limits"


def test_deleting_the_replacement_clears_the_pointer(ix, corpus):
    store = ix.vector_store.metadata_store
    store.set_document_superseded(corpus["v2"], corpus["v3"])
    ix.delete_document(corpus["v3"])
    assert store.get_document_info(corpus["v2"])["superseded_by"] is None
    # ... and v2 is therefore an ordinary document again.
    assert all(r.superseded_by is None for r in _search(ix, "expense claims days"))


def test_listing_carries_replacement_name_and_hides_dangling(ix, corpus):
    store = ix.vector_store.metadata_store
    store.set_document_superseded(corpus["v2"], corpus["v3"])
    docs = {d["document_id"]: d for d in store.list_documents()}
    assert docs[corpus["v2"]]["superseded_by_filename"] == "expense_policy_v3.txt"
    store.set_document_governance(corpus["v3"], policy_status="quarantined")
    docs = {d["document_id"]: d for d in store.list_documents()}
    assert docs[corpus["v2"]]["superseded_by"] is None   # held replacement leaks nothing


def test_marks_survive_a_table_rebuild(ix, corpus):
    store = ix.vector_store.metadata_store
    store.set_document_superseded(corpus["v2"], corpus["v3"], "x")
    marks = store.list_supersede_marks()
    assert [m["document_id"] for m in marks] == [corpus["v2"]]
    store.clear_document_superseded(corpus["v2"])
    assert store.restore_supersede_marks(marks) == 1
    assert store.get_document_info(corpus["v2"])["superseded_by"] == corpus["v3"]


# ── retrieval: demotion ─────────────────────────────────────────────────────


def test_v3_outranks_v2_once_marked_and_not_before(ix, corpus):
    query = "expense claims reimbursement finance department"
    before = [r.document_id for r in _search(ix, query)]
    assert before.index(corpus["v2"]) < before.index(corpus["v3"])   # tie → insertion order

    ix.vector_store.metadata_store.set_document_superseded(corpus["v2"], corpus["v3"])
    after = _search(ix, query)
    ids = [r.document_id for r in after]
    assert ids.index(corpus["v3"]) < ids.index(corpus["v2"])
    old = next(r for r in after if r.document_id == corpus["v2"])
    assert old.superseded_by == {"document_id": corpus["v3"], "filename": "expense_policy_v3.txt"}
    assert next(r for r in after if r.document_id == corpus["v3"]).superseded_by is None


def test_include_superseded_disables_the_penalty_but_keeps_the_label(ix, corpus):
    ix.vector_store.metadata_store.set_document_superseded(corpus["v2"], corpus["v3"])
    res = _search(ix, "expense claims reimbursement finance department", include_superseded=True)
    ids = [r.document_id for r in res]
    assert ids.index(corpus["v2"]) < ids.index(corpus["v3"])           # tie order restored
    assert next(r for r in res if r.document_id == corpus["v2"]).superseded_by


def test_superseded_document_still_answers_when_it_is_the_only_match(ix, corpus):
    ix.vector_store.metadata_store.set_document_superseded(corpus["v2"], corpus["v3"])
    res = _search(ix, "legacy")      # only v2 mentions it
    assert [r.document_id for r in res] == [corpus["v2"]]
    assert res[0].superseded_by["filename"] == "expense_policy_v3.txt"


def test_penalty_zero_means_no_demotion(ix, corpus, monkeypatch):
    monkeypatch.setattr(config.settings, "superseded_penalty", 0.0)
    ix.vector_store.metadata_store.set_document_superseded(corpus["v2"], corpus["v3"])
    ids = [r.document_id for r in _search(ix, "expense claims reimbursement finance department")]
    assert ids.index(corpus["v2"]) < ids.index(corpus["v3"])


def test_supersede_failure_degrades_instead_of_raising(ix, corpus, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(ix.vector_store.metadata_store, "get_supersede_info", boom)
    assert _search(ix, "expense claims")          # still answers
    monkeypatch.setattr(supersede, "collapse_near_duplicates", boom)
    assert _search(ix, "data protection")


def test_quarantined_documents_never_appear_or_get_named(ix, corpus):
    store = ix.vector_store.metadata_store
    store.set_document_governance(corpus["notice_b"], policy_status="quarantined")
    store.set_document_governance(corpus["v3"], policy_status="quarantined")
    store.set_document_superseded(corpus["v2"], corpus["v3"])
    res = _search(ix, "expense claims data protection breach", top_k=10)
    shown = {r.document_id for r in res}
    assert corpus["notice_b"] not in shown and corpus["v3"] not in shown
    for r in res:
        assert r.superseded_by is None      # the held replacement's name is not leaked
        assert corpus["notice_b"] not in {d["document_id"] for d in (r.duplicates or [])}


# ── retrieval: duplicates ───────────────────────────────────────────────────


def test_duplicate_paragraphs_collapse_and_list_the_other_files(ix, corpus):
    res = _search(ix, "data protection procedures breach security office", top_k=3)
    notice_hits = [r for r in res if r.document_id.startswith(tuple([corpus["notice_a"][:2]]))
                   and r.duplicates]
    lead = next(r for r in res if r.duplicates)
    assert lead.duplicate_count == 2
    names = {d["filename"] for d in lead.duplicates}
    assert names <= {"handbook_a.txt", "handbook_b.txt", "handbook_c.txt"} and len(names) == 2
    assert all(d["page"] == 1 for d in lead.duplicates)
    # exactly one of the three copies is in the result list
    assert sum(1 for r in res if r.filename.startswith("handbook_")) == 1
    assert notice_hits is not None


def test_top_k_is_backfilled_after_collapsing(ix, corpus):
    """Three copies would fill top_k=4 with two distinct passages; the search
    widens so the caller still gets four different ones."""
    res = _search(ix, "data protection breach security office personal data", top_k=4)
    texts = {supersede.normalize_text(r.text_snippet) for r in res}
    assert len(res) == 4 and len(texts) == 4


def test_collapse_can_be_turned_off(ix, corpus, monkeypatch):
    monkeypatch.setattr(config.settings, "collapse_duplicates", False)
    res = _search(ix, "data protection procedures breach security office", top_k=5)
    assert sum(1 for r in res if r.filename.startswith("handbook_")) == 3
    assert not any(r.duplicates for r in res)


def test_selected_document_keeps_its_only_result(ix, corpus):
    """A user who ticked handbook_b in Sources must see handbook_b's passage
    even though it is a copy of a higher-ranked one."""
    res = _search(ix, "data protection procedures breach security office", top_k=5,
                  document_ids=[corpus["notice_a"], corpus["notice_b"]])
    assert {r.document_id for r in res} == {corpus["notice_a"], corpus["notice_b"]}


def test_different_numbers_do_not_merge():
    a = supersede._Fingerprint(V2)
    b = supersede._Fingerprint(V3)
    assert not supersede.is_near_duplicate(a, b, 0.5)
    # normalisation-only differences do merge
    assert supersede.is_near_duplicate(a, supersede._Fingerprint("  " + V2.upper() + "  "), 0.95)


@pytest.mark.parametrize("a,b", [
    ("Staff may carry over five days of leave into the next year.",
     "Staff must carry over five days of leave into the next year."),
    ("Employees are required to badge in at the main entrance every morning.",
     "Employees are not required to badge in at the main entrance every morning."),
])
def test_modal_and_negation_flips_do_not_merge(a, b):
    assert not supersede.is_near_duplicate(supersede._Fingerprint(a), supersede._Fingerprint(b), 0.5)


def test_threshold_evidence_for_near_copies():
    """Re-wrapped / lightly edited copies sit above 0.95 only when truly the
    same text; a reworded sentence stays below, so it is kept separate."""
    base = ("The committee shall review every application received before the closing date "
            "and publish its decisions on the public register within ten working days of the meeting.")
    rewrapped = base.replace(" and publish", "\nand publish").replace(". ", ".  ")
    reworded = base.replace("publish its decisions on", "post its findings to")
    fb = supersede._Fingerprint(base)
    assert supersede.jaccard(fb.shingles, supersede._Fingerprint(rewrapped).shingles) == 1.0
    assert supersede.jaccard(fb.shingles, supersede._Fingerprint(reworded).shingles) < 0.95


# ── suggestions ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name,expected_older", [
    ("Travel policy v2.pdf", True), ("travel_policy_v3.pdf", False),
])
def test_filename_version_parse(name, expected_older):
    stem, key, is_copy = supersede.parse_filename_version(name)
    assert stem == "travel policy" and not is_copy
    assert (key[1] == (2,)) is expected_older


def test_suggestions_from_names_copies_and_identical_text():
    docs = [
        {"document_id": "a", "filename": "Leave Policy 2025.pdf", "upload_timestamp": "2025-01-01"},
        {"document_id": "b", "filename": "Leave Policy 2026.pdf", "upload_timestamp": "2026-01-01"},
        {"document_id": "c", "filename": "Travel v2.docx", "upload_timestamp": "2025-02-01"},
        {"document_id": "d", "filename": "Travel v3.docx", "upload_timestamp": "2025-03-01"},
        {"document_id": "e", "filename": "Travel rev B.docx", "upload_timestamp": "2025-04-01"},
        {"document_id": "f", "filename": "Budget.xlsx", "upload_timestamp": "2025-05-01"},
        {"document_id": "g", "filename": "Budget (1).xlsx", "upload_timestamp": "2025-06-01"},
        {"document_id": "h", "filename": "minutes march.txt", "upload_timestamp": "2025-01-01"},
        {"document_id": "i", "filename": "board notes.txt", "upload_timestamp": "2025-07-01"},
        {"document_id": "j", "filename": "unrelated.txt", "upload_timestamp": "2025-07-01"},
        {"document_id": "k", "filename": "Travel v1.docx", "upload_timestamp": "2024-01-01",
         "superseded_by": "d"},
    ]
    text = "Minutes of the board meeting held in March covering budget approvals. " * 3
    out = supersede.suggest_supersessions(docs, {"h": text, "i": text, "j": "something else entirely"})
    pairs = {(s["document_id"], s["replaced_by"]): s["reason"] for s in out}
    assert pairs[("a", "b")] == "version_in_name"
    assert pairs[("c", "d")] == "version_in_name"
    assert pairs[("f", "g")] == "copy_of"
    assert pairs[("h", "i")] == "same_text"
    assert not any(old == "k" for old, _ in pairs)       # already marked
    assert not any("j" in p for p in pairs)


# ── HTTP ────────────────────────────────────────────────────────────────────


@pytest.fixture()
def client(ix):
    import main

    return TestClient(main.app)


def test_http_mark_list_search_and_clear(client, ix, corpus):
    v2, v3 = corpus["v2"], corpus["v3"]
    r = client.post(f"/documents/{v2}/supersede", json={"replaced_by": v3, "note": "limits changed"})
    assert r.status_code == 200 and r.json()["superseded_by"] == v3

    row = next(d for d in client.get("/documents").json()["documents"] if d["document_id"] == v2)
    assert row["superseded_by"] == v3 and row["superseded_by_filename"] == "expense_policy_v3.txt"
    assert row["superseded_note"] == "limits changed"

    hits = client.post("/search", json={"query": "expense claims finance department", "top_k": 5,
                                        "mode": "keyword"}).json()["results"]
    old = next(h for h in hits if h["document_id"] == v2)
    assert old["superseded_by"]["filename"] == "expense_policy_v3.txt"
    ids = [h["document_id"] for h in hits]
    assert ids.index(v3) < ids.index(v2)

    opted = client.post("/search", json={"query": "expense claims finance department", "top_k": 5,
                                         "mode": "keyword",
                                         "filters": {"include_superseded": True}}).json()["results"]
    ids = [h["document_id"] for h in opted]
    assert ids.index(v2) < ids.index(v3)

    cleared = client.delete(f"/documents/{v2}/supersede")
    assert cleared.status_code == 200 and cleared.json()["cleared"] is True
    assert client.delete(f"/documents/{v2}/supersede").json()["cleared"] is False
    assert ix.vector_store.metadata_store.get_document_info(v2)["superseded_by"] is None

    actions = [e["action"] for e in app_db.list_audit_events(document_id=v2)]
    assert "document.supersede" in actions and "document.supersede_cleared" in actions


def test_http_rejections(client, ix, corpus):
    v2, v3 = corpus["v2"], corpus["v3"]
    assert client.post(f"/documents/{v2}/supersede", json={"replaced_by": v2}).status_code == 400
    assert client.post(f"/documents/{v2}/supersede", json={"replaced_by": "missing"}).status_code == 404
    assert client.post("/documents/missing/supersede", json={"replaced_by": v3}).status_code == 404
    assert client.post(f"/documents/{v2}/supersede", json={"replaced_by": v3}).status_code == 200
    assert client.post(f"/documents/{v3}/supersede", json={"replaced_by": v2}).status_code == 400   # loop
    ix.vector_store.metadata_store.set_document_governance(corpus["notice_a"], policy_status="quarantined")
    assert client.post(f"/documents/{v2}/supersede",
                       json={"replaced_by": corpus["notice_a"]}).status_code in (403, 404, 423)


def test_http_other_collection_is_not_reachable(client, ix, corpus):
    r = client.post(f"/documents/{corpus['v2']}/supersede?collection_id=nonexistent",
                    json={"replaced_by": corpus["v3"]})
    assert r.status_code in (403, 404)


def test_http_suggestions_are_read_only_and_not_applied(client, ix, corpus):
    out = client.get("/documents/supersede-suggestions").json()
    pairs = {(s["document_id"], s["replaced_by"]) for s in out["suggestions"]}
    assert (corpus["v2"], corpus["v3"]) in pairs
    assert ix.vector_store.metadata_store.get_document_info(corpus["v2"])["superseded_by"] is None


def test_chunk_inspector_reports_the_replacement(client, ix, corpus):
    client.post(f"/documents/{corpus['v2']}/supersede", json={"replaced_by": corpus["v3"]})
    body = client.get(f"/documents/{corpus['v2']}/chunks").json()
    assert body["superseded_by"]["filename"] == "expense_policy_v3.txt"


# ── MCP / chat shaping ──────────────────────────────────────────────────────


def test_mcp_result_carries_labels_and_filter_is_normalised(ix, corpus):
    from services import mcp_server

    store = ix.vector_store.metadata_store
    store.set_document_superseded(corpus["v2"], corpus["v3"])
    res = _search(ix, "expense claims finance department")
    old = next(r for r in res if r.document_id == corpus["v2"])
    payload = mcp_server._serialize_result(old, 1, 500)
    assert payload["superseded_by"]["filename"] == "expense_policy_v3.txt"
    assert "Older version" in payload["superseded_notice"]

    dup = next(r for r in _search(ix, "data protection procedures breach security office") if r.duplicates)
    assert mcp_server._serialize_result(dup, 1, 500)["duplicate_count"] == 2

    assert mcp_server._normalize_search_filters({"include_superseded": True}) == {"include_superseded": True}
    assert mcp_server._normalize_search_filters({"include_superseded": "true"}) == {"include_superseded": True}
    assert mcp_server._normalize_search_filters({"include_superseded": False}) is None


def test_chat_context_note_and_instructions():
    from services.chat_evidence import CITATION_INSTRUCTIONS
    from types import SimpleNamespace

    r = SimpleNamespace(superseded_by={"document_id": "x", "filename": "v3.pdf"},
                        duplicates=[{"filename": "a.pdf"}, {"filename": "b.pdf"}], duplicate_count=2)
    note = supersede.source_note(r)
    assert "OLDER VERSION, replaced by v3.pdf" in note and "a.pdf, b.pdf" in note
    assert supersede.source_note(SimpleNamespace()) == ""
    assert "OLDER VERSION" in CITATION_INSTRUCTIONS


def test_research_fusion_folds_duplicates_and_keeps_coverage():
    from services.research_search import research

    def hit(doc, name, text):
        return {"document_id": doc, "filename": name, "excerpt": text, "page_number": 1,
                "chunk_id": f"{doc}-0", "collection_id": "c"}

    a = hit("a", "a.txt", BOILERPLATE)
    b = hit("b", "b.txt", BOILERPLATE)
    other = hit("o", "o.txt", OTHERS[0])

    def search(q, mode):
        return [a, other] if mode == "hybrid" else [b]

    out = research("data protection", None, search, lambda p: [], collapse_threshold=0.95)
    docs = [r["document_id"] for r in out["results"]]
    assert docs.count("a") + docs.count("b") == 1
    lead = next(r for r in out["results"] if r["document_id"] in ("a", "b"))
    assert lead["duplicate_count"] == 1
    assert lead["branch_hits"] == 2 and lead["evidence_strength"] == "strong"
    assert out["coverage"][0]["status"] == "retrieved"

    off = research("data protection", None, search, lambda p: [])
    assert [r["document_id"] for r in off["results"]].count("b") == 1

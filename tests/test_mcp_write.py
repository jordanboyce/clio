"""Agents adding and updating sources over MCP: the write_document tool,
the per-token write grant, and the gates a write must pass.

Embeddings are faked (deterministic vectors) so no model loads.
"""

import hashlib
import importlib

import numpy as np
import pytest
from fastapi.testclient import TestClient

import services.mcp_server as mcp
from api import documents
from services import mcp_tokens
from services.app_database import SQLiteBackend, app_db
from services.chunker import TextChunker
from services.collection_locks import collection_write_lock
from services.collection_service import collection_service
from services.document_extractor import DocumentExtractor
from services.indexer_manager import indexer_manager
from services.indexing import DocumentIndexer
from services.vector_store import VectorStore
from models.schemas import SearchMode

EMBED_DIM = 8

NOTES = (
    "# Sprint notes\n\nThe deployment runbook moved to the wiki. Backups run "
    "nightly at 02:00 and are verified by the on-call engineer every Monday.\n"
)
MORE = "\n## Follow-ups\n\n- Rotate the tunnel credentials before the audit.\n"


class FakeEmbedder:
    model_name = "fake-embed"
    embedding_dim = EMBED_DIM

    @staticmethod
    def _vec(text: str) -> list:
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
    # The developer .env may pin another default collection; tests that omit
    # collection_id must land in the fixture's 'default'.
    monkeypatch.setattr(mcp.settings, "mcp_default_collection", "default")
    from api import deps

    monkeypatch.setattr(deps, "_initialized", True)
    return ix


def _filenames(indexer):
    return {d["filename"] for d in indexer.list_documents()}


# ── write_document: create / replace / append ───────────────────────────────


def test_create_writes_file_indexes_and_audits(indexer):
    result = mcp.write_document_sync("sprint-notes", NOTES, collection_id="default")

    assert result["status"] == "created"
    assert result["filename"] == "sprint-notes.md"          # bare name → .md
    assert result["total_chunks"] >= 1
    doc_id = result["document_id"]

    stored = indexer_manager.get_documents_path("default") / "sprint-notes.md"
    assert stored.read_text(encoding="utf-8") == NOTES

    assert "sprint-notes.md" in _filenames(indexer)
    assert doc_id in collection_service.get_collection_document_ids("default")

    hits = indexer.search("when do backups run", top_k=3, mode=SearchMode.KEYWORD)["results"]
    assert any(r.document_id == doc_id for r in hits)

    from services import audit

    event = audit.list_events(action="document.upload")[0]
    assert event["detail"]["via"] == "mcp"
    assert event["detail"]["mode"] == "create"
    assert event["document_id"] == doc_id


def test_create_refuses_existing_filename(indexer):
    mcp.write_document_sync("notes.md", NOTES)
    with pytest.raises(ValueError, match="mode='replace'"):
        mcp.write_document_sync("notes.md", "something else entirely")


def test_replace_retires_old_document(indexer):
    first = mcp.write_document_sync("notes.md", NOTES)
    second = mcp.write_document_sync("notes.md", "Completely new text about lunch menus.", mode="replace")

    assert second["status"] == "replaced"
    assert second["replaced_document_id"] == first["document_id"]
    assert second["document_id"] != first["document_id"]

    store = indexer.vector_store.metadata_store
    assert store.get_document_info(first["document_id"]) is None
    assert store.get_document_info(second["document_id"])["filename"] == "notes.md"
    assert first["document_id"] not in collection_service.get_collection_document_ids("default")

    stored = indexer_manager.get_documents_path("default") / "notes.md"
    assert "lunch menus" in stored.read_text(encoding="utf-8")


def test_append_extends_existing_content(indexer):
    first = mcp.write_document_sync("notes.md", NOTES)
    result = mcp.write_document_sync("notes.md", MORE, mode="append")

    assert result["status"] == "appended"
    assert result["replaced_document_id"] == first["document_id"]
    stored = (indexer_manager.get_documents_path("default") / "notes.md").read_text(encoding="utf-8")
    assert stored.startswith(NOTES)
    assert stored.rstrip().endswith("before the audit.")

    hits = indexer.search("tunnel credentials", top_k=3, mode=SearchMode.KEYWORD)["results"]
    assert any(r.document_id == result["document_id"] for r in hits)


def test_append_without_existing_creates(indexer):
    result = mcp.write_document_sync("fresh.md", NOTES, mode="append")
    assert result["status"] == "created"
    assert "replaced_document_id" not in result


def test_duplicate_content_under_another_name_is_reported(indexer):
    first = mcp.write_document_sync("a.md", NOTES)
    dup = mcp.write_document_sync("b.md", NOTES)

    assert dup["status"] == "duplicate"
    assert dup["existing_filename"] == "a.md"
    assert dup["document_id"] == first["document_id"]
    assert not (indexer_manager.get_documents_path("default") / "b.md").exists()
    assert _filenames(indexer) == {"a.md"}


# ── Input validation ────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["", "   ", "..", "../escape.md", "notes.pdf", "photo.png", "a\nb.md"])
def test_bad_filenames_are_refused(indexer, bad):
    with pytest.raises(ValueError):
        mcp.write_document_sync(bad, NOTES)
    assert _filenames(indexer) == set()


def test_folder_separators_flatten_like_uploads(indexer):
    result = mcp.write_document_sync("research/2026/summary.md", NOTES)
    assert result["filename"] == "research_2026_summary.md"


def test_empty_and_oversized_content_refused(indexer, monkeypatch):
    with pytest.raises(ValueError, match="empty"):
        mcp.write_document_sync("x.md", "   \n")
    monkeypatch.setattr(mcp, "_WRITE_MAX_CHARS", 20)
    with pytest.raises(ValueError, match="limit"):
        mcp.write_document_sync("x.md", "x" * 21)


def test_bad_mode_refused(indexer):
    with pytest.raises(ValueError, match="mode must be"):
        mcp.write_document_sync("x.md", NOTES, mode="upsert")


def test_unknown_collection_refused(indexer):
    with pytest.raises(ValueError, match="not found"):
        mcp.write_document_sync("x.md", NOTES, collection_id="nope")


# ── Gates: token write flag, read-only share, AUP ───────────────────────────


def test_read_only_token_cannot_write(indexer):
    token = mcp._request_mcp_can_write.set(False)
    try:
        with pytest.raises(ValueError, match="read-only"):
            mcp.write_document_sync("x.md", NOTES)
    finally:
        mcp._request_mcp_can_write.reset(token)
    assert _filenames(indexer) == set()


def test_read_only_share_cannot_write(indexer, monkeypatch):
    from services.sharing_service import sharing_service

    monkeypatch.setattr(sharing_service, "check_collection_access", lambda cid, uid: "read")
    with pytest.raises(ValueError, match="read-only"):
        mcp.write_document_sync("x.md", NOTES)


def test_aup_gate_applies_to_agent_writes(indexer, monkeypatch):
    from fastapi import HTTPException
    from services import governance

    def _refuse(user_id):
        raise HTTPException(status_code=403, detail={"code": "aup_required",
                                                     "message": "Accept the policy first."})

    monkeypatch.setattr(governance, "require_aup", _refuse)
    with pytest.raises(ValueError, match="Accept the policy first"):
        mcp.write_document_sync("x.md", NOTES)


def test_blocked_hash_is_refused_and_leaves_no_file(indexer):
    digest = hashlib.sha256(NOTES.encode("utf-8")).hexdigest()
    app_db.add_blocked_hash(digest, "admin", "test", "old.md")

    with pytest.raises(ValueError, match="cannot be re-added"):
        mcp.write_document_sync("again.md", NOTES)
    assert not (indexer_manager.get_documents_path("default") / "again.md").exists()
    assert _filenames(indexer) == set()


# ── A write that does not finish leaves the note as it was ──────────────────


OTHER = "Keeper note: the rotation schedule is weekly, and nobody disputes that fact."


def _stored(name):
    return (indexer_manager.get_documents_path("default") / name).read_bytes().decode("utf-8")


def _rows(indexer, name):
    return [d for d in indexer.list_documents() if d["filename"] == name]


def _holds(indexer, name, text):
    """The note is intact everywhere an agent could read it from."""
    rows = _rows(indexer, name)
    assert len(rows) == 1, f"{name} has {len(rows)} rows"
    assert _stored(name) == text
    assert rows[0]["document_id"] == hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], "row is another version"
    store = indexer.vector_store.metadata_store
    chunks = store.get_chunks_by_document(rows[0]["document_id"])
    assert chunks
    assert rows[0]["document_id"] in collection_service.get_collection_document_ids("default")


@pytest.mark.parametrize("mode", ["replace", "append"])
def test_text_matching_another_note_leaves_the_target_alone(indexer, mode):
    mcp.write_document_sync("keeper.md", OTHER)
    target = "Victim note: irreplaceable content SALAMANDER9.\n"
    mcp.write_document_sync("victim.md", target)
    # An append only collides when the combined text matches, so give it one that does.
    colliding = OTHER if mode == "replace" else "Tail of the note."
    if mode == "append":
        mcp.write_document_sync("twin.md", target + "\n" + colliding)

    with pytest.raises(ValueError, match="Nothing changed"):
        mcp.write_document_sync("victim.md", colliding, mode=mode)

    _holds(indexer, "victim.md", target)
    hits = indexer.search("SALAMANDER9", top_k=3, mode=SearchMode.KEYWORD)["results"]
    assert any(r.filename == "victim.md" for r in hits)


def test_a_log_keeps_its_entries_when_one_step_matches_another_note(indexer):
    """Ten sequential appends once lost the heading and the first entry this way."""
    mcp.write_document_sync("other-log.md", "# log\n\nENTRY-00")
    mcp.write_document_sync("seq-log.md", "# log")

    with pytest.raises(ValueError, match="Nothing changed"):
        mcp.write_document_sync("seq-log.md", "ENTRY-00", mode="append")
    mcp.write_document_sync("seq-log.md", "ENTRY-01", mode="append")

    text = _stored("seq-log.md")
    assert text.startswith("# log")
    assert "ENTRY-01" in text
    assert len(_rows(indexer, "seq-log.md")) == 1


def test_quota_refusal_leaves_the_note_alone(indexer, monkeypatch):
    import config

    target = "Important paragraph QUOTAVICTIM.\n"
    mcp.write_document_sync("big.md", target)
    monkeypatch.setattr(config.settings, "collection_storage_limit_bytes", 100)

    with pytest.raises(ValueError, match="storage limit"):
        mcp.write_document_sync("big.md", "bigger " * 40, mode="replace")

    _holds(indexer, "big.md", target)


def test_quota_counts_the_space_a_replace_gives_back(indexer, monkeypatch):
    import config

    mcp.write_document_sync("note.md", "a" * 60)
    monkeypatch.setattr(config.settings, "collection_storage_limit_bytes", 100)

    # 60 now + 70 new would not fit; 70 alone does, and that is what remains.
    assert mcp.write_document_sync("note.md", "b" * 70, mode="replace")["status"] == "replaced"
    _holds(indexer, "note.md", "b" * 70)

    # Over the cap already (it was lowered): shrinking must still be possible.
    monkeypatch.setattr(config.settings, "collection_storage_limit_bytes", 40)
    assert mcp.write_document_sync("note.md", "c" * 50, mode="replace")["status"] == "replaced"
    _holds(indexer, "note.md", "c" * 50)


def test_indexing_failure_leaves_the_note_alone(indexer, monkeypatch):
    mcp.write_document_sync("note.md", NOTES)

    def _boom(*args, **kwargs):
        raise RuntimeError("embedding service fell over")

    monkeypatch.setattr(indexer, "index_document", _boom)
    with pytest.raises(ValueError, match="not changed"):
        mcp.write_document_sync("note.md", "Replacement text.", mode="replace")
    monkeypatch.undo()

    _holds(indexer, "note.md", NOTES)


def test_failure_after_indexing_rolls_the_new_version_back(indexer, monkeypatch):
    first = mcp.write_document_sync("note.md", NOTES)

    def _boom(src, dst):
        raise PermissionError(13, "in use", str(dst))

    monkeypatch.setattr(mcp, "_install_stored_file", _boom)
    with pytest.raises(ValueError, match="not changed") as refused:
        mcp.write_document_sync("note.md", "Replacement text about lunch.", mode="replace")
    monkeypatch.undo()

    assert str(indexer_manager.get_documents_path("default")) not in str(refused.value)
    _holds(indexer, "note.md", NOTES)
    assert [d["document_id"] for d in _rows(indexer, "note.md")] == [first["document_id"]]
    assert not indexer.search("lunch", top_k=3, mode=SearchMode.KEYWORD)["results"]


def test_blocked_replacement_leaves_the_note_alone(indexer):
    mcp.write_document_sync("note.md", NOTES)
    blocked = "Text an administrator has blocked."
    app_db.add_blocked_hash(hashlib.sha256(blocked.encode("utf-8")).hexdigest(), "admin", "test", "old.md")

    with pytest.raises(ValueError, match="cannot be re-added"):
        mcp.write_document_sync("note.md", blocked, mode="replace")

    _holds(indexer, "note.md", NOTES)


def test_writes_leave_no_staging_behind(indexer, monkeypatch):
    mcp.write_document_sync("note.md", NOTES)
    mcp.write_document_sync("note.md", MORE, mode="append")
    mcp.write_document_sync("other.md", OTHER)
    with pytest.raises(ValueError, match="Nothing changed"):
        mcp.write_document_sync("note.md", OTHER, mode="replace")

    documents = indexer_manager.get_documents_path("default")
    assert not list(documents.parent.glob(".write-*"))
    assert {p.name for p in documents.iterdir()} == {"note.md", "other.md"}


# ── Replace and append as an agent would rely on them ───────────────────────


def test_writing_identical_text_back_is_a_no_op(indexer):
    first = mcp.write_document_sync("note.md", NOTES)
    again = mcp.write_document_sync("note.md", NOTES, mode="replace")

    assert again["status"] == "unchanged"
    assert again["document_id"] == first["document_id"]
    assert "replaced_document_id" not in again
    _holds(indexer, "note.md", NOTES)


def test_replacing_a_table_moves_the_table_too(indexer):
    first = mcp.write_document_sync("cities.csv", "city,pop\nBoise,235000\n")
    second = mcp.write_document_sync("cities.csv", "city,pop\nNampa,100000\n", mode="replace")

    tables = {t["document_id"] for t in mcp.list_tables("default")["tables"]}
    assert tables == {second["document_id"]} != {first["document_id"]}
    assert _stored("cities.csv") == "city,pop\nNampa,100000\n"


def test_replace_keeps_a_stricter_label(indexer):
    from services import governance

    first = mcp.write_document_sync("note.md", NOTES)
    governance.set_document_sensitivity("default", first["document_id"], "restricted", actor="tester")

    second = mcp.write_document_sync("note.md", "New text about lunch menus.", mode="replace")

    store = indexer.vector_store.metadata_store
    assert store.get_document_info(second["document_id"])["sensitivity"] == "restricted"


def test_append_keeps_existing_bytes(indexer):
    path = indexer_manager.get_documents_path("default") / "crlf.md"
    mcp.write_document_sync("crlf.md", "Line one of the note.\r\nLine two of the note.\r\n")

    mcp.write_document_sync("crlf.md", "Line three.", mode="append")

    assert path.read_bytes().startswith(b"Line one of the note.\r\nLine two of the note.\r\n")


def test_append_refuses_a_file_it_cannot_read_faithfully(indexer):
    path = indexer_manager.get_documents_path("default") / "latin.md"
    first = mcp.write_document_sync("latin.md", NOTES)
    raw = "caf\xe9 notes".encode("latin-1")
    path.write_bytes(raw)

    with pytest.raises(ValueError, match="UTF-8"):
        mcp.write_document_sync("latin.md", "More.", mode="append")

    assert path.read_bytes() == raw
    assert _rows(indexer, "latin.md")[0]["document_id"] == first["document_id"]


def test_replace_clears_rows_a_race_left_behind(indexer):
    """Duplicate rows for one name kept stale text searchable after a replace."""
    documents = indexer_manager.get_documents_path("default")
    for body in ("Creator one wrote STALEONE.", "Creator two wrote STALETWO."):
        path = documents / "race.md"
        path.write_text(body, encoding="utf-8")
        meta = indexer.index_document(path, "race.md", collection_id="default")
        collection_service.add_document("default", meta.document_id)
    assert len(_rows(indexer, "race.md")) == 2

    mcp.write_document_sync("race.md", "Cleanly replaced text.", mode="replace")

    _holds(indexer, "race.md", "Cleanly replaced text.")
    for stale in ("STALEONE", "STALETWO"):
        assert not indexer.search(stale, top_k=3, mode=SearchMode.KEYWORD)["results"]


def test_replace_back_to_a_duplicate_rows_text_keeps_one_row(indexer):
    documents = indexer_manager.get_documents_path("default")
    ids = []
    for body in ("First version of the note.", "Second version of the note."):
        path = documents / "race.md"
        path.write_text(body, encoding="utf-8")
        meta = indexer.index_document(path, "race.md", collection_id="default")
        collection_service.add_document("default", meta.document_id)
        ids.append(meta.document_id)

    result = mcp.write_document_sync("race.md", "First version of the note.", mode="replace")

    assert result["document_id"] == ids[0]
    assert [d["document_id"] for d in _rows(indexer, "race.md")] == [ids[0]]
    assert _stored("race.md") == "First version of the note."


def test_create_sees_a_name_past_the_first_page_of_lookalikes(indexer):
    """The lookup was a substring search capped at 50 rows, newest first."""
    mcp.write_document_sync("a.md", "The original note named a.")
    for i in range(55):
        mcp.write_document_sync(f"data-{i:02d}a.md", f"Lookalike number {i} of many.")

    with pytest.raises(ValueError, match="already exists"):
        mcp.write_document_sync("a.md", "A second note with the same name.")
    assert len(_rows(indexer, "a.md")) == 1


@pytest.mark.parametrize("source_type", ["folder_sync", "local_reference"])
@pytest.mark.parametrize("mode", ["replace", "append"])
def test_notes_owned_by_a_source_refuse_agent_edits(indexer, mode, source_type):
    """The edit would not reach the source, and the next sync would bring the old text back."""
    first = mcp.write_document_sync("plan.md", NOTES)
    store = indexer.vector_store.metadata_store
    store.update_document_source(first["document_id"], source_path="/vault/plan.md", source_type=source_type)

    with pytest.raises(ValueError, match="re-sync|re-index"):
        mcp.write_document_sync("plan.md", "Agent text.", mode=mode)

    _holds(indexer, "plan.md", NOTES)


def test_names_differing_only_by_case_are_one_note(indexer):
    """They are one file on Windows and macOS, so the second overwrote the first."""
    mcp.write_document_sync("alpha.md", NOTES)

    for mode in ("create", "replace", "append"):
        with pytest.raises(ValueError, match="case"):
            mcp.write_document_sync("Alpha.md", "Other text.", mode=mode)

    _holds(indexer, "alpha.md", NOTES)
    assert _filenames(indexer) == {"alpha.md"}


@pytest.mark.parametrize("bad", ["notes:colon.md", "what?.md", "a*b.md", 'q"uote.md', "a<b.md", "a|b.md", "x" * 300 + ".md"])
def test_names_that_are_not_portable_are_refused(indexer, bad):
    with pytest.raises(ValueError, match="filename") as refused:
        mcp.write_document_sync(bad, NOTES)
    assert str(indexer_manager.get_documents_path("default")) not in str(refused.value)
    assert _filenames(indexer) == set()


def test_text_that_cannot_be_encoded_is_refused_before_anything_changes(indexer):
    mcp.write_document_sync("note.md", NOTES)

    with pytest.raises(ValueError, match="UTF-8"):
        mcp.write_document_sync("note.md", "broken \ud800 text", mode="replace")

    _holds(indexer, "note.md", NOTES)


# ── Several agents at once ──────────────────────────────────────────────────


def _at_once(count, fn):
    """Run fn(i) on count threads released together; return results and errors."""
    import threading

    barrier = threading.Barrier(count)
    results, errors = [None] * count, [None] * count

    def run(i):
        barrier.wait()
        try:
            results[i] = fn(i)
        except Exception as e:  # noqa: BLE001 - collected and asserted on
            errors[i] = e

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads)
    return results, errors


def test_concurrent_appends_all_survive(indexer):
    mcp.write_document_sync("log.md", "# Shared log")

    results, errors = _at_once(10, lambda i: mcp.write_document_sync(
        "log.md", f"ENTRY-{i:02d}", mode="append"))

    assert errors == [None] * 10
    assert {r["status"] for r in results} == {"appended"}
    text = _stored("log.md")
    assert text.startswith("# Shared log")
    assert all(f"ENTRY-{i:02d}" in text for i in range(10))
    _holds(indexer, "log.md", text)
    assert len(indexer.list_documents()) == 1


def test_concurrent_creates_of_one_name_make_one_note(indexer):
    results, errors = _at_once(6, lambda i: mcp.write_document_sync(
        "race.md", f"Creator {i} wrote this body."))

    assert sum(1 for r in results if r and r["status"] == "created") == 1
    assert sum(1 for e in errors if e and "already exists" in str(e)) == 5
    assert len(_rows(indexer, "race.md")) == 1


def test_concurrent_replaces_leave_one_whole_version(indexer):
    mcp.write_document_sync("note.md", NOTES)
    bodies = [f"Version {i} of the note, complete in itself." for i in range(6)]

    _, errors = _at_once(6, lambda i: mcp.write_document_sync("note.md", bodies[i], mode="replace"))

    assert errors == [None] * 6
    final = _stored("note.md")
    assert final in bodies
    _holds(indexer, "note.md", final)
    chunks = indexer.vector_store.metadata_store.get_chunks_by_document(_rows(indexer, "note.md")[0]["document_id"])
    assert all(c["text"] in final for c in chunks)


def test_concurrent_writes_of_identical_text_under_two_names(indexer):
    results, errors = _at_once(2, lambda i: mcp.write_document_sync(f"twin-{i}.md", NOTES))

    assert errors == [None, None]
    assert sorted(r["status"] for r in results) == ["created", "duplicate"]
    assert len(indexer.list_documents()) == 1


def test_a_writer_that_cannot_get_its_turn_says_so(indexer, monkeypatch):
    monkeypatch.setattr(mcp, "_WRITE_LOCK_TIMEOUT_SECONDS", 0.05)
    mcp.write_document_sync("note.md", NOTES)

    with collection_write_lock("default"):
        _, errors = _at_once(1, lambda i: mcp.write_document_sync("note.md", "Other.", mode="replace"))

    assert "Nothing was changed" in str(errors[0])
    _holds(indexer, "note.md", NOTES)


def test_a_reindex_of_a_retired_document_does_not_bring_it_back(indexer, fresh_db):
    """Queued, then the note was replaced before the job ran."""
    first = mcp.write_document_sync("note.md", NOTES)
    second = mcp.write_document_sync("note.md", "Newer text about lunch menus.", mode="replace")
    job_id = fresh_db.create_upload_job("default", 1, job_type="reindex")

    with pytest.raises(ValueError, match="no longer"):
        mcp._run_reindex_document_job(job_id, "default", first["document_id"], "tester")

    assert [d["document_id"] for d in _rows(indexer, "note.md")] == [second["document_id"]]
    assert len(indexer.vector_store.metadata_store.get_chunks_by_document(second["document_id"])) >= 1


# ── read_document: the exact text, and the version to write back against ────


def test_a_read_edit_replace_round_trip_adds_nothing(indexer):
    """Chunks overlap, so rebuilding a note from them used to duplicate text."""
    text = "Opening line.\r\n\r\n" + ("A sentence about rotation schedules. " * 40) + "\r\nLast line.\r\n"
    mcp.write_document_sync("note.md", text)

    read = mcp.read_document_sync("note.md")
    assert read["text"] == text and read["total_chars"] == len(text) and read["next_offset"] is None

    edited = read["text"] + "Added line.\r\n"
    mcp.write_document_sync("note.md", edited, mode="replace", expected_document_id=read["document_id"])
    assert _stored("note.md") == edited


def test_read_accepts_a_filename_or_a_document_id_and_the_filename_outlives_edits(indexer):
    first = mcp.write_document_sync("note.md", NOTES)
    assert mcp.read_document_sync(first["document_id"])["text"] == NOTES

    second = mcp.write_document_sync("note.md", NOTES + MORE, mode="replace")
    read = mcp.read_document_sync("note.md")
    assert read["text"] == NOTES + MORE and read["document_id"] == second["document_id"]
    with pytest.raises(ValueError, match="No source"):
        mcp.read_document_sync(first["document_id"])


def test_a_long_note_reads_back_whole_in_pages(indexer):
    text = "".join(f"Line {i} of a long note.\n" for i in range(4000))
    mcp.write_document_sync("long.md", text)

    pieces, offset = [], 0
    while offset is not None:
        page = mcp.read_document_sync("long.md", offset=offset, max_chars=10_000)
        pieces.append(page["text"])
        offset = page["next_offset"]

    assert len(pieces) > 1 and "".join(pieces) == text


def test_read_refuses_what_it_cannot_return_exactly(indexer):
    mcp.write_document_sync("binary.md", NOTES)
    (indexer_manager.get_documents_path("default") / "binary.md").write_bytes(b"caf\xe9\n")
    with pytest.raises(ValueError, match="not UTF-8"):
        mcp.read_document_sync("binary.md")

    mcp.write_document_sync("gone.md", OTHER)
    (indexer_manager.get_documents_path("default") / "gone.md").unlink()
    with pytest.raises(ValueError, match="no longer on disk"):
        mcp.read_document_sync("gone.md")

    with pytest.raises(ValueError, match="No source"):
        mcp.read_document_sync("never-written.md")


def test_read_does_not_show_a_quarantined_note(indexer):
    doc = mcp.write_document_sync("held.md", NOTES)
    indexer.vector_store.metadata_store.set_document_governance(doc["document_id"], policy_status="quarantined")

    with pytest.raises(ValueError, match="No source"):
        mcp.read_document_sync("held.md")


def test_read_flags_masked_text_and_a_file_that_differs_from_the_index(indexer, monkeypatch):
    mcp.write_document_sync("note.md", NOTES)
    assert "redacted" not in mcp.read_document_sync("note.md") and "note" not in mcp.read_document_sync("note.md")

    with monkeypatch.context() as patch:
        patch.setattr(mcp.pii_detector, "redact_if_enabled", lambda text: text.replace("Backups", "[MASKED]"))
        masked = mcp.read_document_sync("note.md")
    assert masked["redacted"] is True and "[MASKED]" in masked["text"]

    (indexer_manager.get_documents_path("default") / "note.md").write_text("Edited outside Clio.", encoding="utf-8")
    assert "differs from what is indexed" in mcp.read_document_sync("note.md")["note"]


# ── expected_document_id: only write over what you read ─────────────────────


@pytest.mark.parametrize("mode", ["replace", "append"])
def test_a_write_based_on_an_old_version_is_refused(indexer, mode):
    stale = mcp.write_document_sync("note.md", NOTES)["document_id"]
    newer = NOTES + MORE
    mcp.write_document_sync("note.md", newer, mode="replace")

    with pytest.raises(ValueError, match="changed since you read it"):
        mcp.write_document_sync("note.md", "My edit.", mode=mode, expected_document_id=stale)

    _holds(indexer, "note.md", newer)


def test_a_write_expecting_a_removed_note_does_not_recreate_it(indexer):
    from services import governance

    doc = mcp.write_document_sync("note.md", NOTES)
    governance.remove_document("default", doc["document_id"], actor="tester")

    with pytest.raises(ValueError, match="no longer exists"):
        mcp.write_document_sync("note.md", "My edit.", mode="replace", expected_document_id=doc["document_id"])

    assert _rows(indexer, "note.md") == []


def test_retrying_a_write_that_landed_is_not_a_conflict(indexer):
    first = mcp.write_document_sync("note.md", NOTES)
    edited = NOTES + MORE
    landed = mcp.write_document_sync("note.md", edited, mode="replace", expected_document_id=first["document_id"])

    retried = mcp.write_document_sync("note.md", edited, mode="replace", expected_document_id=first["document_id"])

    assert landed["status"] == "replaced" and retried["status"] == "unchanged"
    _holds(indexer, "note.md", edited)


def test_going_back_to_the_expected_text_after_another_edit_is_still_a_conflict(indexer):
    first = mcp.write_document_sync("note.md", NOTES)
    mcp.write_document_sync("note.md", NOTES + MORE, mode="replace")

    with pytest.raises(ValueError, match="changed since you read it"):
        mcp.write_document_sync("note.md", NOTES, mode="replace", expected_document_id=first["document_id"])


def test_two_agents_editing_one_note_keep_both_edits(indexer):
    """The checkup's lost update: both read, both replace, the first edit vanished."""
    import threading

    mcp.write_document_sync("shared.md", "Base line of the note.\n")
    both_have_read = threading.Barrier(2)

    def edit(i):
        for attempt in range(5):
            read = mcp.read_document_sync("shared.md")
            if attempt == 0:
                both_have_read.wait()
            try:
                return mcp.write_document_sync(
                    "shared.md", read["text"] + f"Added by agent {i}.\n",
                    mode="replace", expected_document_id=read["document_id"],
                )
            except ValueError as e:
                assert "changed since" in str(e)
        raise AssertionError("never landed")

    _, errors = _at_once(2, edit)

    assert errors == [None, None]
    text = _stored("shared.md")
    assert "Added by agent 0." in text and "Added by agent 1." in text
    _holds(indexer, "shared.md", text)


# ── reindex_document: a failure keeps the document ──────────────────────────


def _reindex(fresh_db, document_id):
    job_id = fresh_db.create_upload_job("default", 1, job_type="reindex")
    mcp._run_reindex_document_job(job_id, "default", document_id, "tester")


def test_a_reindex_the_model_cannot_embed_keeps_the_document(indexer, fresh_db, monkeypatch):
    doc = mcp.write_document_sync("note.md", NOTES)

    def down(*a, **k):
        raise RuntimeError("the model is unavailable")

    monkeypatch.setattr(indexer.embedding_service, "embed_texts", down)
    with pytest.raises(RuntimeError, match="unavailable"):
        _reindex(fresh_db, doc["document_id"])

    _holds(indexer, "note.md", NOTES)
    hits = indexer.search("when do backups run", top_k=3, mode=SearchMode.KEYWORD)["results"]
    assert any(r.document_id == doc["document_id"] for r in hits)


def test_a_reindex_of_a_file_that_went_missing_keeps_the_document(indexer, fresh_db):
    doc = mcp.write_document_sync("note.md", NOTES)
    (indexer_manager.get_documents_path("default") / "note.md").unlink()

    with pytest.raises(Exception):
        _reindex(fresh_db, doc["document_id"])

    assert [d["document_id"] for d in _rows(indexer, "note.md")] == [doc["document_id"]]
    assert indexer.vector_store.metadata_store.get_chunks_by_document(doc["document_id"])


def test_a_reindex_of_an_edited_file_retires_the_old_version(indexer, fresh_db):
    doc = mcp.write_document_sync("note.md", NOTES)
    edited = NOTES + MORE
    (indexer_manager.get_documents_path("default") / "note.md").write_text(edited, encoding="utf-8", newline="")

    _reindex(fresh_db, doc["document_id"])

    _holds(indexer, "note.md", edited)
    assert doc["document_id"] not in collection_service.get_collection_document_ids("default")


def test_a_reindex_keeps_a_quarantine_decision(indexer, fresh_db):
    doc = mcp.write_document_sync("note.md", NOTES)
    store = indexer.vector_store.metadata_store
    store.set_document_governance(doc["document_id"], policy_status="quarantined")

    _reindex(fresh_db, doc["document_id"])

    assert store.get_document_info(doc["document_id"])["policy_status"] == "quarantined"


def test_a_reindex_of_a_table_keeps_it_queryable(indexer, fresh_db):
    tables = indexer.vector_store.structured_store
    doc = mcp.write_document_sync("t.csv", "a,b\n1,2\n")

    _reindex(fresh_db, doc["document_id"])
    assert [t["document_id"] for t in tables.list_tables()] == [doc["document_id"]]

    (indexer_manager.get_documents_path("default") / "t.csv").write_bytes(b"a,b\n3,4\n5,6\n")
    _reindex(fresh_db, doc["document_id"])
    rows = _rows(indexer, "t.csv")
    assert len(rows) == 1 and rows[0]["document_id"] != doc["document_id"]
    assert [t["document_id"] for t in tables.list_tables()] == [rows[0]["document_id"]]


def test_a_reindex_keeps_a_table_when_it_cannot_be_read(indexer, fresh_db):
    tables = indexer.vector_store.structured_store
    doc = mcp.write_document_sync("t.csv", "a,b\n1,2\n")
    (indexer_manager.get_documents_path("default") / "t.csv").unlink()

    with pytest.raises(Exception):
        _reindex(fresh_db, doc["document_id"])

    assert [t["document_id"] for t in tables.list_tables()] == [doc["document_id"]]
    assert [d["document_id"] for d in _rows(indexer, "t.csv")] == [doc["document_id"]]


# ── The app's writers and agent writes ──────────────────────────────────────


def _waits_for_the_lock(fn):
    """fn does not finish while the collection's write lock is held, then does."""
    import threading

    done = threading.Event()

    def run():
        fn()
        done.set()

    thread = threading.Thread(target=run)
    with collection_write_lock("default"):
        thread.start()
        assert not done.wait(1.0), "ran without waiting for the lock"
    thread.join(10)
    assert done.is_set()


def _upload(name, body):
    from io import BytesIO

    from fastapi import UploadFile

    return documents.upload_documents(files=[UploadFile(BytesIO(body), filename=name)], collection_id="default")


def test_deleting_a_document_waits_for_a_running_write(indexer):
    from services import governance

    doc = mcp.write_document_sync("note.md", NOTES)

    _waits_for_the_lock(lambda: governance.remove_document("default", doc["document_id"], actor="tester"))

    assert _rows(indexer, "note.md") == []


def test_deleting_a_batch_waits_for_a_running_write(indexer):
    from services import governance

    doc = mcp.write_document_sync("note.md", NOTES)
    row = indexer.vector_store.metadata_store.get_document_info(doc["document_id"])

    _waits_for_the_lock(lambda: governance.remove_documents("default", [row], actor="tester"))

    assert _rows(indexer, "note.md") == []


def test_an_upload_waits_for_a_running_write(indexer):
    _waits_for_the_lock(lambda: _upload("uploaded.md", NOTES.encode()))

    assert _stored("uploaded.md") == NOTES


def test_a_failed_reupload_keeps_the_version_already_there(indexer, monkeypatch):
    mcp.write_document_sync("note.md", NOTES)

    def boom(*a, **k):
        raise RuntimeError("cannot read this")

    monkeypatch.setattr(indexer, "index_document", boom)
    with pytest.raises(Exception):
        _upload("note.md", b"A new version that will not index.")

    _holds(indexer, "note.md", NOTES)


def test_a_note_cannot_take_a_name_a_running_job_is_adding(indexer, monkeypatch):
    from services.upload_service import upload_service

    (indexer_manager.get_documents_path("default") / "incoming.md").write_text("Being indexed.", encoding="utf-8")
    monkeypatch.setattr(upload_service, "has_running_job", lambda collection_id: True)

    with pytest.raises(ValueError, match="retry when the job has finished"):
        mcp.write_document_sync("incoming.md", NOTES)

    assert _stored("incoming.md") == "Being indexed."


def test_a_leftover_file_with_no_job_running_does_not_block_a_name(indexer):
    (indexer_manager.get_documents_path("default") / "leftover.md").write_text("Stale.", encoding="utf-8")

    assert mcp.write_document_sync("leftover.md", NOTES)["status"] == "created"
    _holds(indexer, "leftover.md", NOTES)


def test_a_write_waits_out_a_full_reindex(indexer, monkeypatch):
    from services import reindex_service

    mcp.write_document_sync("note.md", NOTES)
    monkeypatch.setattr(reindex_service, "is_reindexing", lambda collection_id: True)

    with pytest.raises(ValueError, match="is being re-indexed"):
        mcp.write_document_sync("note.md", "Edit.", mode="replace")

    _holds(indexer, "note.md", NOTES)


def test_a_folder_index_does_not_write_over_a_note_of_the_same_name(indexer, fresh_db, tmp_path):
    from services.upload_service import upload_service

    mcp.write_document_sync("README.md", NOTES)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# The repository's own readme\n\nHow to build it.\n", encoding="utf-8")

    job_id = fresh_db.create_upload_job("default", 1, job_type="index")
    upload_service._run_repo_index(job_id, [str(repo / "README.md")], str(repo), "default", "tester")

    _holds(indexer, "README.md", NOTES)
    assert _stored("README_1.md").startswith("# The repository's own readme")
    assert len(_rows(indexer, "README_1.md")) == 1


def test_a_failed_folder_file_removes_only_its_own_copy(indexer, fresh_db, tmp_path):
    from services.upload_service import upload_service

    mcp.write_document_sync("empty.md", NOTES)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "empty.md").write_bytes(b"")  # nothing to extract, so the file fails

    job_id = fresh_db.create_upload_job("default", 1, job_type="index")
    upload_service._run_repo_index(job_id, [str(repo / "empty.md")], str(repo), "default", "tester")

    _holds(indexer, "empty.md", NOTES)
    assert not (indexer_manager.get_documents_path("default") / "empty_1.md").exists()


def test_claiming_a_stored_name_never_hands_it_out_twice(tmp_path):
    from services.upload_service import _claim_stored_path

    paths, errors = _at_once(8, lambda i: _claim_stored_path(tmp_path, "same.md"))

    assert errors == [None] * 8
    assert len(set(paths)) == 8 and all(p.exists() for p in paths)


# ── Adding a file again replaces the version already there ──────────────────

CHANGED = "# Sprint notes\n\nThe runbook moved back to the repository last week.\n"


def _index_in_place(fresh_db, *paths):
    from services.upload_service import upload_service

    job_id = fresh_db.create_upload_job("default", len(paths), job_type="index")
    upload_service._run_local_index(job_id, [str(p) for p in paths], "default", False, "tester")


def _staged_upload(fresh_db, name, body):
    """The browser's and the CLI's route: stage the bytes, then one job over the stored paths."""
    from io import BytesIO

    from fastapi import UploadFile

    staged = documents.stage_uploads(files=[UploadFile(BytesIO(body), filename=name)], collection_id="default")
    _index_in_place(fresh_db, *[s["path"] for s in staged["staged"]])


@pytest.fixture(params=["direct", "staged"])
def upload(request, fresh_db):
    if request.param == "direct":
        return _upload
    return lambda name, body: _staged_upload(fresh_db, name, body)


def test_uploading_a_changed_file_replaces_the_old_version(indexer, fresh_db, upload):
    upload("note.md", NOTES.encode())
    old_id = _rows(indexer, "note.md")[0]["document_id"]

    upload("note.md", CHANGED.encode())

    _holds(indexer, "note.md", CHANGED)
    assert indexer.vector_store.metadata_store.get_chunks_by_document(old_id) == []
    assert old_id not in collection_service.get_collection_document_ids("default")
    assert [e["document_id"] for e in fresh_db.list_audit_events(action="document.replace")] == [old_id]


def test_uploading_the_same_file_again_changes_nothing(indexer, upload):
    upload("note.md", NOTES.encode())
    upload("note.md", NOTES.encode())

    _holds(indexer, "note.md", NOTES)


def test_a_new_upload_clears_versions_left_by_earlier_uploads(indexer, tmp_path, upload):
    upload("note.md", NOTES.encode())
    older = tmp_path / "note.md"
    older.write_text("An older version from before re-uploads replaced anything.", encoding="utf-8")
    indexer.index_document(older, "note.md", collection_id="default")
    assert len(_rows(indexer, "note.md")) == 2

    upload("note.md", CHANGED.encode())

    _holds(indexer, "note.md", CHANGED)


def test_a_sensitivity_label_survives_a_new_version(indexer, upload):
    from services import governance

    upload("note.md", NOTES.encode())
    store = indexer.vector_store.metadata_store
    label = governance.SENSITIVITY_LEVELS[-1]
    store.set_document_governance(_rows(indexer, "note.md")[0]["document_id"], sensitivity=label)

    upload("note.md", CHANGED.encode())

    assert store.get_document_info(_rows(indexer, "note.md")[0]["document_id"])["sensitivity"] == label


def test_an_upload_leaves_a_reference_of_the_same_name_alone(indexer, fresh_db, tmp_path, upload):
    original = tmp_path / "note.md"
    original.write_text(CHANGED, encoding="utf-8")
    _index_in_place(fresh_db, original)

    upload("note.md", NOTES.encode())

    rows = _rows(indexer, "note.md")
    assert sorted(r["source_type"] for r in rows) == ["local_reference", "upload"]
    assert original.read_text(encoding="utf-8") == CHANGED


def test_an_upload_over_a_synced_files_name_takes_it(indexer, upload):
    """The upload overwrites that stored copy, so its row would describe bytes that are gone."""
    upload("README.md", NOTES.encode())
    store = indexer.vector_store.metadata_store
    store.update_document_source(_rows(indexer, "README.md")[0]["document_id"],
                                 source_path="/repo/README.md", source_type="folder_sync")

    upload("README.md", CHANGED.encode())

    _holds(indexer, "README.md", CHANGED)


def test_a_staged_upload_is_a_stored_file_not_a_reference(indexer, fresh_db):
    """Marked as a reference, it was shown as a folder, kept its file when deleted and refused agent edits."""
    from services import governance

    _staged_upload(fresh_db, "note.md", NOTES.encode())
    assert _rows(indexer, "note.md")[0]["source_type"] == "upload"

    result = mcp.write_document_sync("note.md", CHANGED, mode="replace")
    assert result["status"] == "replaced"
    _holds(indexer, "note.md", CHANGED)

    governance.remove_document("default", result["document_id"], actor="tester")
    assert not (indexer_manager.get_documents_path("default") / "note.md").exists()


def test_uploads_labelled_as_references_earlier_are_put_right(indexer, fresh_db, tmp_path):
    documents_dir = indexer_manager.get_documents_path("default")
    store = indexer.vector_store.metadata_store
    uploaded = mcp.write_document_sync("uploaded.md", NOTES)["document_id"]
    store.update_document_source(uploaded, source_path=str((documents_dir / "uploaded.md").absolute()))
    original = tmp_path / "elsewhere.md"
    original.write_text(CHANGED, encoding="utf-8")
    _index_in_place(fresh_db, original)

    store.adopt_staged_uploads(documents_dir)

    info = store.get_document_info(uploaded)
    assert (info["source_type"], info["source_path"]) == ("upload", None)
    assert _rows(indexer, "elsewhere.md")[0]["source_type"] == "local_reference"


def test_an_upload_finishing_after_an_agent_write_is_the_last_write(indexer, fresh_db, monkeypatch):
    mcp.write_document_sync("note.md", NOTES)
    index_prepared = indexer.index_prepared_documents

    def agent_writes_while_embedding(*args, **kwargs):
        monkeypatch.setattr(indexer, "index_prepared_documents", index_prepared)
        mcp.write_document_sync("note.md", "The agent's edit.", mode="replace")
        return index_prepared(*args, **kwargs)

    monkeypatch.setattr(indexer, "index_prepared_documents", agent_writes_while_embedding)
    _staged_upload(fresh_db, "note.md", CHANGED.encode())

    _holds(indexer, "note.md", CHANGED)


def test_an_upload_gives_way_to_a_write_that_already_replaced_it(indexer, fresh_db, monkeypatch):
    """The row was in the index for a moment, so an agent's replace retired it before the file moved."""
    mcp.write_document_sync("note.md", NOTES)
    index_prepared = indexer.index_prepared_documents

    def agent_writes_after_indexing(*args, **kwargs):
        monkeypatch.setattr(indexer, "index_prepared_documents", index_prepared)
        metas = index_prepared(*args, **kwargs)
        mcp.write_document_sync("note.md", "The agent's edit.", mode="replace")
        return metas

    monkeypatch.setattr(indexer, "index_prepared_documents", agent_writes_after_indexing)
    _staged_upload(fresh_db, "note.md", CHANGED.encode())

    _holds(indexer, "note.md", "The agent's edit.")
    assert len(collection_service.get_collection_document_ids("default")) == 1


# ── The same bytes again keep the decisions made about them ─────────────────


def _decide(indexer, document_id):
    from services import governance

    label = governance.SENSITIVITY_LEVELS[-1]
    indexer.vector_store.metadata_store.set_document_governance(
        document_id, sensitivity=label, policy_status="quarantined", policy_flags={"categories": {"secrets": 1}},
    )
    return label


def _assert_decided(indexer, document_id, label):
    from services import governance

    info = indexer.vector_store.metadata_store.get_document_info(document_id)
    assert info["sensitivity"] == label
    assert info["policy_status"] == "quarantined"
    assert info["policy_flags"] == {"categories": {"secrets": 1}}
    assert governance.is_hidden(info)


def test_uploading_identical_bytes_keeps_a_quarantine_and_label(indexer, upload):
    upload("note.md", NOTES.encode())
    doc_id = _rows(indexer, "note.md")[0]["document_id"]
    label = _decide(indexer, doc_id)

    upload("note.md", NOTES.encode())

    assert len(_rows(indexer, "note.md")) == 1
    _assert_decided(indexer, doc_id, label)


def test_identical_bytes_under_another_name_keep_a_quarantine_and_label(indexer, upload):
    upload("note.md", NOTES.encode())
    doc_id = _rows(indexer, "note.md")[0]["document_id"]
    label = _decide(indexer, doc_id)

    upload("copy.md", NOTES.encode())

    _assert_decided(indexer, doc_id, label)


def test_a_changed_copy_of_a_quarantined_file_stays_held(indexer, fresh_db, upload):
    """One edited line must not get a held document past its review, or out of the queue."""
    from services import governance

    upload("note.md", NOTES.encode())
    _decide(indexer, _rows(indexer, "note.md")[0]["document_id"])

    upload("note.md", CHANGED.encode())

    rows = _rows(indexer, "note.md")
    assert len(rows) == 1 and _stored("note.md") == CHANGED
    info = indexer.vector_store.metadata_store.get_document_info(rows[0]["document_id"])
    assert info["policy_status"] == "quarantined" and governance.is_hidden(info)
    assert info["policy_flags"] == {"categories": {"secrets": 1}}
    held = fresh_db.list_audit_events(action="document.quarantined")
    assert [e["document_id"] for e in held] == [rows[0]["document_id"]]


def test_an_agent_is_told_its_replacement_of_a_quarantined_note_is_held(indexer):
    first = mcp.write_document_sync("note.md", NOTES)
    _decide(indexer, first["document_id"])

    result = mcp.write_document_sync("note.md", CHANGED, mode="replace")

    assert result["status"] == "replaced" and result["policy_status"] == "quarantined"


def test_a_changed_copy_of_an_approved_file_is_reviewed_afresh(indexer, upload):
    """An approval was of those bytes; it says nothing about new ones."""
    upload("note.md", NOTES.encode())
    indexer.vector_store.metadata_store.set_document_governance(
        _rows(indexer, "note.md")[0]["document_id"], policy_status="approved")

    upload("note.md", CHANGED.encode())

    info = indexer.vector_store.metadata_store.get_document_info(_rows(indexer, "note.md")[0]["document_id"])
    assert (info["policy_status"] or "clear") == "clear"


def test_a_later_scan_verdict_stands_where_nobody_decided(indexer, monkeypatch):
    """Keeping decisions must not freeze the row: a clear row takes what the new scan says."""
    from services import content_policy

    _upload("note.md", NOTES.encode())
    doc_id = _rows(indexer, "note.md")[0]["document_id"]

    monkeypatch.setattr(content_policy, "scanning_enabled", lambda: True)
    monkeypatch.setattr(content_policy, "decide", lambda *a, **k: ("flagged", {"categories": {"x": 1}}, "internal"))
    _upload("note.md", NOTES.encode())

    info = indexer.vector_store.metadata_store.get_document_info(doc_id)
    assert info["policy_status"] == "flagged"
    assert info["sensitivity"] == "internal"


# ── A background upload that does not finish changes nothing ───────────────


def _nothing_staged():
    staging = collection_service.get_staging_path("default")
    return not staging.exists() or not any(staging.iterdir())


def test_a_failed_job_keeps_the_file_already_there(indexer, fresh_db):
    mcp.write_document_sync("note.md", NOTES)

    _staged_upload(fresh_db, "note.md", b"")  # nothing to extract, so the file fails

    _holds(indexer, "note.md", NOTES)
    assert _nothing_staged()


def test_a_refused_upload_keeps_the_file_already_there(indexer, fresh_db):
    mcp.write_document_sync("note.md", NOTES)
    blocked = CHANGED.encode()
    fresh_db.add_blocked_hash(hashlib.sha256(blocked).hexdigest(), "admin", "not allowed", "note.md")

    _staged_upload(fresh_db, "note.md", blocked)

    _holds(indexer, "note.md", NOTES)
    assert _nothing_staged()


def test_a_refused_new_upload_leaves_no_file(indexer, fresh_db):
    blocked = CHANGED.encode()
    fresh_db.add_blocked_hash(hashlib.sha256(blocked).hexdigest(), "admin", "not allowed", "new.md")

    _staged_upload(fresh_db, "new.md", blocked)

    assert _rows(indexer, "new.md") == []
    assert not (indexer_manager.get_documents_path("default") / "new.md").exists()
    assert _nothing_staged()


def test_a_cancelled_job_keeps_the_file_already_there(indexer, fresh_db, monkeypatch):
    from services.upload_service import upload_service

    mcp.write_document_sync("note.md", NOTES)
    monkeypatch.setattr(upload_service, "_is_cancelled", lambda job_id: True)

    _staged_upload(fresh_db, "note.md", CHANGED.encode())

    _holds(indexer, "note.md", NOTES)
    assert _nothing_staged()


def test_a_failed_move_into_the_library_takes_the_new_row_back(indexer, fresh_db, monkeypatch):
    import os

    mcp.write_document_sync("note.md", NOTES)
    real_replace = os.replace

    def refuse_staged(src, dst, **kwargs):
        if ".staged" in str(src):
            raise PermissionError("the file is open elsewhere")
        return real_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", refuse_staged)
    _staged_upload(fresh_db, "note.md", CHANGED.encode())

    _holds(indexer, "note.md", NOTES)
    assert _nothing_staged()


def test_a_job_waits_for_a_running_write_before_it_moves_its_file(indexer, fresh_db):
    import threading

    mcp.write_document_sync("note.md", NOTES)
    job = threading.Thread(target=_staged_upload, args=(fresh_db, "note.md", CHANGED.encode()))

    with collection_write_lock("default"):
        job.start()
        job.join(1.0)
        assert job.is_alive(), "finished without waiting for the lock"
        assert _stored("note.md") == NOTES
    job.join(10)

    _holds(indexer, "note.md", CHANGED)


def test_during_a_background_upload_the_old_file_is_still_there(indexer):
    from io import BytesIO
    from pathlib import Path

    from fastapi import UploadFile

    mcp.write_document_sync("note.md", NOTES)

    staged = documents.stage_uploads(files=[UploadFile(BytesIO(CHANGED.encode()), filename="note.md")], collection_id="default")

    _holds(indexer, "note.md", NOTES)
    assert collection_service.get_staging_path("default") in Path(staged["staged"][0]["path"]).parents


def test_a_restart_clears_uploads_no_job_will_index(indexer, fresh_db):
    from io import BytesIO

    from fastapi import UploadFile
    from services.upload_service import upload_service

    documents.stage_uploads(files=[UploadFile(BytesIO(NOTES.encode()), filename="note.md")], collection_id="default")
    assert not _nothing_staged()

    upload_service.recover_orphaned_jobs()

    assert _nothing_staged()


def _stage(name="note.md", body=NOTES):
    from io import BytesIO
    from pathlib import Path

    from fastapi import UploadFile

    staged = documents.stage_uploads(files=[UploadFile(BytesIO(body.encode()), filename=name)], collection_id="default")
    return Path(staged["staged"][0]["path"])


def _age(path, seconds):
    import os
    import time

    then = time.time() - seconds
    os.utime(path.parent, (then, then))


def test_cancelling_a_queued_job_removes_its_uploads(indexer, monkeypatch):
    from services.upload_service import upload_service

    mcp.write_document_sync("note.md", NOTES)
    monkeypatch.setattr(upload_service, "_dispatch", lambda: None)  # it stays in the queue
    job_id = upload_service.start_local_index([str(_stage(body=CHANGED))], "default", uploaded_by="tester")
    assert not _nothing_staged()

    assert upload_service.cancel_job(job_id) == "cancelled"

    assert _nothing_staged()
    _holds(indexer, "note.md", NOTES)


def test_uploads_no_job_came_for_are_cleared_after_a_day(indexer):
    from services.upload_service import STAGED_UPLOAD_MAX_AGE_SECONDS, upload_service

    fresh, stale = _stage("fresh.md"), _stage("stale.md")
    _age(stale, STAGED_UPLOAD_MAX_AGE_SECONDS + 60)

    upload_service.sweep_staged_uploads()

    assert fresh.exists() and not stale.parent.exists()


def test_old_uploads_stay_while_a_job_is_queued_on_the_collection(indexer, monkeypatch):
    """A long queue can keep a job's files waiting for longer than a day."""
    from services.upload_service import STAGED_UPLOAD_MAX_AGE_SECONDS, upload_service

    monkeypatch.setattr(upload_service, "_dispatch", lambda: None)
    waiting = _stage()
    job_id = upload_service.start_local_index([str(waiting)], "default", uploaded_by="tester")
    _age(waiting, STAGED_UPLOAD_MAX_AGE_SECONDS + 60)

    upload_service.sweep_staged_uploads()
    assert waiting.exists()

    upload_service.cancel_job(job_id)


def test_the_daily_sweep_clears_old_uploads(indexer):
    from services import retention
    from services.upload_service import STAGED_UPLOAD_MAX_AGE_SECONDS

    stale = _stage("stale.md")
    _age(stale, STAGED_UPLOAD_MAX_AGE_SECONDS + 60)

    retention._sweep()

    assert not stale.parent.exists()


# ── Adding the same link again replaces what it brought in ─────────────────

URL = "https://example.com/page"
PAGE = "<html><body><h1>Status</h1><p>The {} release went out and the on-call engineer checked it.</p></body></html>"


def _add_link(fresh_db, monkeypatch, body, name="Status (example.com).html", url=URL):
    from pathlib import Path

    import services.upload_service as us
    from services.link_fetcher import FetchedLink

    monkeypatch.setattr(us, "fetch_link", lambda u: FetchedLink(
        url=u, final_url=u, content_type="text/html", extension=Path(name).suffix,
        body=body.encode(), filename=name, title="Status"))
    job_id = fresh_db.create_upload_job("default", 1, job_type="index")
    us.upload_service._run_link_index(job_id, [url], "default", "tester")


def _library():
    return sorted(p.name for p in indexer_manager.get_documents_path("default").iterdir())


def test_a_changed_page_replaces_the_old_version(indexer, fresh_db, monkeypatch):
    first, second = PAGE.format("spring"), PAGE.format("autumn")
    _add_link(fresh_db, monkeypatch, first)
    old_id = _rows(indexer, "Status (example.com).html")[0]["document_id"]

    _add_link(fresh_db, monkeypatch, second)

    _holds(indexer, "Status (example.com).html", second)
    assert indexer.vector_store.metadata_store.get_chunks_by_document(old_id) == []
    assert _library() == ["Status (example.com).html"]
    assert _rows(indexer, "Status (example.com).html")[0]["source_path"] == URL
    assert _nothing_staged()


def test_a_page_keeps_its_name_when_its_title_changes(indexer, fresh_db, monkeypatch):
    _add_link(fresh_db, monkeypatch, PAGE.format("spring"))

    _add_link(fresh_db, monkeypatch, PAGE.format("autumn"), name="Renamed page (example.com).html")

    _holds(indexer, "Status (example.com).html", PAGE.format("autumn"))
    assert _library() == ["Status (example.com).html"]


def test_an_unchanged_page_stays_one_document_and_one_file(indexer, fresh_db, monkeypatch):
    _add_link(fresh_db, monkeypatch, PAGE.format("spring"))

    _add_link(fresh_db, monkeypatch, PAGE.format("spring"))

    _holds(indexer, "Status (example.com).html", PAGE.format("spring"))
    assert _library() == ["Status (example.com).html"]


def test_a_page_that_became_another_type_moves_to_the_new_name(indexer, fresh_db, monkeypatch):
    _add_link(fresh_db, monkeypatch, PAGE.format("spring"))

    _add_link(fresh_db, monkeypatch, "# Status\n\nThe autumn release went out and was checked.\n", name="Status.md")

    _holds(indexer, "Status.md", "# Status\n\nThe autumn release went out and was checked.\n")
    assert _rows(indexer, "Status (example.com).html") == []
    assert _library() == ["Status.md"]


def test_adding_a_link_clears_copies_left_by_earlier_adds(indexer, fresh_db, monkeypatch):
    _add_link(fresh_db, monkeypatch, PAGE.format("spring"))
    extra = indexer_manager.get_documents_path("default") / "Status (example.com)_1.html"
    extra.write_text(PAGE.format("summer"), encoding="utf-8")
    meta = indexer.index_document(extra, extra.name, collection_id="default")
    indexer.vector_store.metadata_store.update_document_source(meta.document_id, source_path=URL, source_type="url")

    _add_link(fresh_db, monkeypatch, PAGE.format("autumn"))

    docs = indexer.list_documents()
    assert len(docs) == 1 and _library() == [docs[0]["filename"]]
    _holds(indexer, docs[0]["filename"], PAGE.format("autumn"))


def test_a_page_that_cannot_be_read_keeps_the_version_already_there(indexer, fresh_db, monkeypatch):
    _add_link(fresh_db, monkeypatch, PAGE.format("spring"))

    _add_link(fresh_db, monkeypatch, "<html></html>")  # nothing to extract, so the page fails

    _holds(indexer, "Status (example.com).html", PAGE.format("spring"))
    assert _library() == ["Status (example.com).html"]
    assert _nothing_staged()


def test_a_cancelled_refetch_keeps_the_version_already_there(indexer, fresh_db, monkeypatch):
    from services.upload_service import upload_service

    _add_link(fresh_db, monkeypatch, PAGE.format("spring"))
    monkeypatch.setattr(upload_service, "_is_cancelled", lambda job_id: True)

    _add_link(fresh_db, monkeypatch, PAGE.format("autumn"))

    _holds(indexer, "Status (example.com).html", PAGE.format("spring"))
    assert _nothing_staged()


def test_an_unchanged_page_keeps_a_quarantine_and_label(indexer, fresh_db, monkeypatch):
    _add_link(fresh_db, monkeypatch, PAGE.format("spring"))
    doc_id = _rows(indexer, "Status (example.com).html")[0]["document_id"]
    label = _decide(indexer, doc_id)

    _add_link(fresh_db, monkeypatch, PAGE.format("spring"))

    _assert_decided(indexer, doc_id, label)


def test_a_changed_page_of_a_quarantined_link_stays_held(indexer, fresh_db, monkeypatch):
    from services import governance

    _add_link(fresh_db, monkeypatch, PAGE.format("spring"))
    _decide(indexer, _rows(indexer, "Status (example.com).html")[0]["document_id"])

    _add_link(fresh_db, monkeypatch, PAGE.format("autumn"))

    rows = _rows(indexer, "Status (example.com).html")
    assert len(rows) == 1
    assert governance.is_hidden(indexer.vector_store.metadata_store.get_document_info(rows[0]["document_id"]))


def test_two_links_with_the_same_title_both_stay(indexer, fresh_db, monkeypatch):
    _add_link(fresh_db, monkeypatch, PAGE.format("spring"))

    _add_link(fresh_db, monkeypatch, PAGE.format("autumn"), url="https://example.com/other")

    _holds(indexer, "Status (example.com).html", PAGE.format("spring"))
    _holds(indexer, "Status (example.com)_1.html", PAGE.format("autumn"))


def test_a_changed_reference_replaces_itself(indexer, fresh_db, tmp_path):
    original = tmp_path / "paper.md"
    original.write_text(NOTES, encoding="utf-8")
    _index_in_place(fresh_db, original)
    old_id = _rows(indexer, "paper.md")[0]["document_id"]

    original.write_text(CHANGED, encoding="utf-8")
    _index_in_place(fresh_db, original)

    rows = _rows(indexer, "paper.md")
    assert len(rows) == 1 and rows[0]["document_id"] != old_id
    assert original.read_text(encoding="utf-8") == CHANGED


def test_references_of_one_name_from_different_folders_both_stay(indexer, fresh_db, tmp_path):
    first, second = tmp_path / "a" / "same.md", tmp_path / "b" / "same.md"
    for path, text in ((first, NOTES), (second, CHANGED)):
        path.parent.mkdir()
        path.write_text(text, encoding="utf-8")

    _index_in_place(fresh_db, first, second)

    assert len(_rows(indexer, "same.md")) == 2


# ── The async MCP wrapper and tool registration ─────────────────────────────


def test_async_tool_wraps_sync_and_is_registered(indexer):
    import asyncio

    result = asyncio.run(mcp.write_document("via-tool.md", NOTES, collection_id="default"))
    assert result["status"] == "created"

    names = {t.name for t in mcp._clio_mcp._tool_manager.list_tools()}
    assert "write_document" in names
    assert not any(n.startswith("delete") for n in names)


# ── Token write flag: DB, service, API, middleware ───────────────────────────


def test_token_can_write_roundtrip(fresh_db):
    ro = app_db.create_mcp_token("alice", "reader", "h-ro", "asy_mcp_aaaaaa")
    rw = app_db.create_mcp_token("alice", "writer", "h-rw", "asy_mcp_bbbbbb", can_write=True)
    assert ro["can_write"] is False
    assert rw["can_write"] is True
    assert app_db.get_mcp_token_by_hash("h-ro")["can_write"] is False
    assert app_db.get_mcp_token_by_hash("h-rw")["can_write"] is True
    by_id = {t["id"]: t for t in app_db.list_mcp_tokens("alice")}
    assert by_id[ro["id"]]["can_write"] is False
    assert by_id[rw["id"]]["can_write"] is True


def test_generate_token_defaults_read_only(fresh_db):
    record = mcp_tokens.generate_token("alice", "laptop")
    assert record["can_write"] is False
    verified = mcp_tokens.verify_token(record["token"])
    assert verified["can_write"] is False

    writer = mcp_tokens.generate_token("alice", "agent", can_write=True)
    assert mcp_tokens.verify_token(writer["token"])["can_write"] is True


@pytest.fixture()
def open_client(indexer):
    import main

    return TestClient(main.app)


def test_token_api_accepts_can_write(open_client, fresh_db):
    r = open_client.post("/api/mcp/tokens", json={"name": "agent", "can_write": True})
    assert r.status_code == 201, r.text
    assert r.json()["can_write"] is True
    listed = open_client.get("/api/mcp/tokens").json()["tokens"]
    assert listed[0]["can_write"] is True

    r = open_client.post("/api/mcp/tokens", json={"name": "reader"})
    assert r.json()["can_write"] is False

    from services import audit

    assert audit.list_events(action="mcp_token.create")[0]["detail"]["can_write"] is False


def test_middleware_records_write_flag_on_request_state(tmp_path, monkeypatch):
    import asyncio

    import config
    import main
    from services import app_database
    from starlette.requests import Request

    monkeypatch.setenv("AUTH_PASSWORD", "deploy-secret")
    importlib.reload(config)
    main = importlib.reload(main)
    try:
        monkeypatch.setattr(app_database.app_db, "db_path", tmp_path / "app.db")
        app_database.app_db._init_db()

        async def downstream(request):
            return "ok"

        def _request(token):
            return Request({
                "type": "http", "method": "POST", "path": "/mcp/",
                "headers": [(b"authorization", f"Bearer {token}".encode())],
                "query_string": b"",
            })

        reader = mcp_tokens.generate_token("alice", "reader")["token"]
        writer = mcp_tokens.generate_token("alice", "writer", can_write=True)["token"]

        req = _request(reader)
        assert asyncio.run(main.require_auth(req, downstream)) == "ok"
        assert req.state.mcp_token_can_write is False

        req = _request(writer)
        assert asyncio.run(main.require_auth(req, downstream)) == "ok"
        assert req.state.mcp_token_can_write is True
    finally:
        monkeypatch.delenv("AUTH_PASSWORD", raising=False)
        importlib.reload(config)
        importlib.reload(main)


def test_toggleable_app_binds_write_flag_from_state():
    """The ASGI wrapper turns request.state.mcp_token_can_write into the
    contextvar the tool consults; a missing flag (SSO, password) allows."""
    import asyncio

    seen = []

    async def inner(scope, receive, send):
        seen.append(mcp.get_request_mcp_can_write())

    app = mcp.ToggleableMCPApp(inner)

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        pass

    base = {"type": "http", "path": "/", "method": "POST", "query_string": b""}
    asyncio.run(app({**base, "state": {"mcp_token_can_write": False}}, receive, send))
    asyncio.run(app({**base, "state": {"mcp_token_can_write": True}}, receive, send))
    asyncio.run(app({**base, "state": {}}, receive, send))
    assert seen == [False, True, True]
    assert mcp.get_request_mcp_can_write() is True  # reset after the request

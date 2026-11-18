"""OCR, figure extraction and transcription run in helper processes with limits.

services/extract_sandbox.py ran only the document parsers in helpers. Local
OCR (Docling, Tesseract), figure extraction and Whisper transcription ran in
the server, so one hostile scan or recording could hold a thread for hours or
take the server's memory. Each now runs in a helper of its own kind:
``ocr`` and ``audio`` with longer limits than ``parse``.

The real engines are not needed here: tests/sandbox_test_tasks.py stands in
for the work inside a real helper process, so what is tested is the helper,
its limits, and what crosses back.
"""

import os
import sys

import pytest

import config
from services import extract_sandbox
from services.document_extractor import DocumentExtractor
from services.extract_sandbox import ExtractionLimitExceeded
from tests.test_figures import chart, docx, logo, photo  # noqa: F401 - fixtures

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_AS is Linux-only")


@pytest.fixture()
def helpers(monkeypatch):
    """Helpers that know the test tasks; ``helpers("hang")`` also replaces the real ones."""
    def start(mode=""):
        extract_sandbox._pool.shutdown()  # idle helpers started without the test tasks
        monkeypatch.setenv(extract_sandbox.TEST_TASKS_ENV, "tests.sandbox_test_tasks")
        monkeypatch.setenv("CLIO_EXTRACT_TEST_MODE", mode)
        monkeypatch.setattr(config.settings, "extract_isolation", True)

    yield start
    extract_sandbox._pool.shutdown()


@pytest.fixture()
def limits(monkeypatch):
    def apply(**values):
        for name, value in values.items():
            monkeypatch.setattr(config.settings, name, value)
    return apply


@pytest.fixture()
def started(monkeypatch):
    """Every helper process started during the test."""
    made = []
    real_init = extract_sandbox._Helper.__init__

    def record(self, *a, **k):
        real_init(self, *a, **k)
        made.append(self)

    monkeypatch.setattr(extract_sandbox._Helper, "__init__", record)
    return made


def _never():
    pytest.fail("ran in the server process")


# ── limits per kind ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind, setting, words", [
    ("ocr", "ocr_timeout_seconds", "Running OCR on scan.pdf took longer than 1 seconds"),
    ("audio", "transcribe_timeout_seconds", "Transcribing scan.pdf took longer than 1 seconds"),
    ("parse", "extract_timeout_seconds", "Reading scan.pdf took longer than 1 seconds"),
])
def test_work_that_hangs_is_killed_and_reported(tmp_path, helpers, limits, started, kind, setting, words):
    helpers()
    limits(**{setting: 1})
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"%PDF-1.4")

    with pytest.raises(ExtractionLimitExceeded, match=words) as caught:
        extract_sandbox.run("hang", path, _never, kind=kind)

    assert setting.upper() in str(caught.value)
    assert started and started[-1].kind == kind
    assert started[-1].proc.poll() is not None  # the helper was killed, not left running
    assert extract_sandbox.run("pid", path, _never, kind=kind) != os.getpid()
    assert len(started) == 2  # the next file got a fresh helper


def test_each_kind_has_its_own_limits(tmp_path, helpers, limits, started):
    """A long OCR limit does not stretch a parse, and the reverse."""
    helpers()
    limits(ocr_timeout_seconds=600, extract_timeout_seconds=1)
    path = tmp_path / "a.pdf"
    path.write_bytes(b"x")

    with pytest.raises(ExtractionLimitExceeded, match="EXTRACT_TIMEOUT_SECONDS"):
        extract_sandbox.run("hang", path, _never, kind="parse")
    assert isinstance(extract_sandbox.run("pid", path, _never, kind="ocr"), int)
    assert {h.kind for h in started} == {"parse", "ocr"}


@linux_only
@pytest.mark.parametrize("kind, setting, name", [
    ("ocr", "ocr_memory_limit_mb", "OCR_MEMORY_LIMIT_MB"),
    ("audio", "transcribe_memory_limit_mb", "TRANSCRIBE_MEMORY_LIMIT_MB"),
])
def test_work_that_needs_too_much_memory_is_stopped(tmp_path, helpers, limits, kind, setting, name):
    helpers()
    limits(**{setting: 64, "ocr_timeout_seconds": 60, "transcribe_timeout_seconds": 60})
    path = tmp_path / "big.wav"
    path.write_bytes(b"x")

    with pytest.raises(ExtractionLimitExceeded, match=f"more than 64 MB of memory.*{name}"):
        extract_sandbox.run("hog", path, _never, kind=kind)
    assert isinstance(extract_sandbox.run("pid", path, _never, kind=kind), int)


# ── errors keep their types ─────────────────────────────────────────────────


def test_a_file_at_fault_stays_a_file_at_fault(tmp_path, helpers):
    from services.pra.readers.base import PraFormatError

    helpers()
    with pytest.raises(PraFormatError, match="header line is missing"):
        extract_sandbox.run("raise_pra", tmp_path / "m.zip", _never, kind="ocr")


def test_a_missing_model_stays_a_missing_model(tmp_path, helpers):
    from services.audio_transcriber import TranscriptionModelUnavailable

    helpers()
    with pytest.raises(TranscriptionModelUnavailable, match="not on this server"):
        extract_sandbox.run("raise_model", tmp_path / "m.wav", _never, kind="audio")


# ── the real call sites ─────────────────────────────────────────────────────


def test_local_ocr_runs_in_an_ocr_helper(tmp_path, helpers, started):
    helpers("answer")
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"%PDF-1.4")

    pages = DocumentExtractor(enable_ocr=False)._local_ocr(path, pages=[2, 3])

    assert set(pages) == {1, 2}  # page numbers come back as ints
    assert pages[1].startswith("read in pid ") and pages[1] != f"read in pid {os.getpid()}"
    assert pages[2] == "pages asked: [2, 3]"
    assert started[-1].kind == "ocr"


def test_a_recording_is_transcribed_in_an_audio_helper(tmp_path, helpers, limits, started):
    helpers("answer")
    limits(enable_transcription=True, whisper_model="small", whisper_language="")
    path = tmp_path / "standup.mp3"
    path.write_bytes(b"not decoded here")

    result = DocumentExtractor(enable_ocr=False).extract_text(path)

    assert result.page_texts[1] == "[00:05] model small"
    assert result.page_texts[2].startswith("[04:10] pid ") and result.page_texts[2] != f"[04:10] pid {os.getpid()}"
    assert started[-1].kind == "audio"


def test_a_recording_that_hangs_fails_its_upload_with_a_422(tmp_path, helpers, limits, monkeypatch):
    from fastapi.testclient import TestClient

    import main

    helpers("hang")
    limits(enable_transcription=True, transcribe_timeout_seconds=1)
    from api import deps
    from services.app_database import SQLiteBackend, app_db
    from services.collection_service import collection_service
    from services.indexer_manager import indexer_manager
    from tests.test_governance import _make_indexer

    monkeypatch.setattr(app_db, "db_path", tmp_path / "app.db")
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    monkeypatch.setattr(collection_service, "base_dir", tmp_path / "collections")
    monkeypatch.setitem(indexer_manager._indexers, "default", _make_indexer(tmp_path))
    monkeypatch.setattr(indexer_manager, "get_documents_path", lambda cid: tmp_path / "library")
    (tmp_path / "library").mkdir()
    monkeypatch.setattr(deps, "_initialized", True)

    r = TestClient(main.app).post("/documents/upload",
                                  files={"files": ("standup.mp3", b"ID3 not really audio", "audio/mpeg")})

    assert r.status_code == 422, r.text
    assert "TRANSCRIBE_TIMEOUT_SECONDS" in r.text


def test_figures_come_back_from_the_helper_byte_for_byte(docx, limits):  # noqa: F811
    from services.indexing.indexer import DocumentIndexer

    path = docx

    limits(extract_isolation=False)
    here = DocumentIndexer._figures_in(path, "docx", {1: "text"}, "figs.docx")
    extract_sandbox._pool.shutdown()
    limits(extract_isolation=True)
    isolated = DocumentIndexer._figures_in(path, "docx", {1: "text"}, "figs.docx")

    assert here and [(f.data, f.ext, f.page_number, f.caption) for f in isolated] == \
        [(f.data, f.ext, f.page_number, f.caption) for f in here]


def test_a_document_whose_figures_hang_keeps_its_text(tmp_path, helpers, limits, caplog):
    from services.indexing.indexer import DocumentIndexer

    helpers("hang")
    limits(extract_timeout_seconds=1)
    path = tmp_path / "deck.pdf"
    path.write_bytes(b"%PDF-1.4")

    with caplog.at_level("WARNING"):
        assert DocumentIndexer._figures_in(path, "pdf", {1: "text"}, "deck.pdf") == []
    assert "Figures in deck.pdf were not kept" in caplog.text
    assert "EXTRACT_TIMEOUT_SECONDS" in caplog.text

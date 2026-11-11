"""PDFium is never entered from two threads at once.

PDFium is not thread-safe. Folder indexing extracts several files in parallel
(bulk_extract_workers), and before every PDFium call took PDFIUM_LOCK that
corrupted PDFium's global state: every later PDF failed with "Data format
error", or the process died with a segmentation fault.

These tests count how many PdfDocuments are open at the same moment while text
extraction, figure extraction and page previews run on many threads together.
"""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from services import page_preview
from services.document_extractor import DocumentExtractor
from services.figure_extractor import extract_figures


@pytest.fixture()
def pdfs(tmp_path):
    import pypdfium2 as pdfium

    paths = []
    for n in range(4):
        doc = pdfium.PdfDocument.new()
        for _ in range(3 + n):
            doc.new_page(300, 400)
        p = tmp_path / f"doc{n}.pdf"
        doc.save(str(p))
        doc.close()
        paths.append(p)
    return paths


@pytest.fixture()
def open_documents(monkeypatch):
    """Patch PdfDocument to record the most documents open at once."""
    import pypdfium2 as pdfium

    real = pdfium.PdfDocument
    state = {"open": 0, "peak": 0, "opened": 0}
    guard = threading.Lock()

    class Counting(real):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            with guard:
                state["open"] += 1
                state["opened"] += 1
                state["peak"] = max(state["peak"], state["open"])
            # Widen the window a racing thread would need.
            threading.Event().wait(0.002)

        def close(self):
            if getattr(self, "_counted_closed", False):
                return super().close()
            self._counted_closed = True
            with guard:
                state["open"] -= 1
            return super().close()

    monkeypatch.setattr(pdfium, "PdfDocument", Counting)
    return state


def test_parallel_extraction_figures_and_previews_take_turns(pdfs, open_documents):
    extractor = DocumentExtractor(enable_ocr=False)
    page_preview_cache = getattr(page_preview, "_cache", None)
    if hasattr(page_preview_cache, "clear"):
        page_preview_cache.clear()

    def work(i):
        path = pdfs[i % len(pdfs)]
        kind = i % 3
        if kind == 0:
            return len(extractor._extract_pdf_with_pdfium(path))
        if kind == 1:
            extract_figures(path, "pdf", {})
            return -1
        return len(page_preview._render_pdf_page(path, 1, 120))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(work, range(48)))

    assert open_documents["opened"] >= 48
    assert open_documents["peak"] == 1
    assert open_documents["open"] == 0
    # Text extraction still reads every page of every file.
    assert sorted({r for i, r in enumerate(results) if i % 3 == 0}) == [3, 4, 5, 6]


def test_the_lock_is_reentrant():
    from services.pdfium_lock import PDFIUM_LOCK

    with PDFIUM_LOCK:
        with PDFIUM_LOCK:
            pass

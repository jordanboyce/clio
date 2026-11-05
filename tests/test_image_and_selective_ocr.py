"""Standalone-image indexing and selective per-page OCR.

The vision provider is faked throughout — the network path is exercised by a
manual smoke test, not CI. These tests pin the routing logic: which pages get
OCR'd, how images fall back, and how the vision API key is resolved.
"""

import pytest

import services.document_extractor as de
from services.document_extractor import DocumentExtractor, ExtractionResult


class FakeVisionEngine:
    def __init__(self, description="a red circle. Text: HELLO", page_text="ocr text"):
        self.description = description
        self.page_text = page_text
        self.describe_calls = []
        self.extract_calls = []

    def describe_image(self, image_path):
        self.describe_calls.append(image_path)
        return self.description

    def extract_text(self, pdf_path, pages=None):
        self.extract_calls.append((pdf_path, pages))
        from services.ocr_engine import OCRPage, OCRResult
        nums = pages or [1]
        return OCRResult(
            pages=[OCRPage(page_number=p, text=f"{self.page_text} {p}", metadata={}) for p in nums],
            engine="fake",
            metadata={},
        )


@pytest.fixture()
def png(tmp_path):
    from PIL import Image
    p = tmp_path / "photo.png"
    Image.new("RGB", (32, 32), "red").save(p)
    return p


def _extractor(**kwargs):
    return DocumentExtractor(**kwargs)


def test_image_extensions_supported():
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".tiff"):
        assert ext in DocumentExtractor.SUPPORTED_EXTENSIONS


def test_image_routes_through_vision_engine(png, monkeypatch):
    ex = _extractor()
    fake = FakeVisionEngine()
    monkeypatch.setattr(ex, "_get_vision_ocr_engine", lambda: fake)

    result = ex.extract_text(png)
    assert result.method == "vision"
    assert "HELLO" in result.page_texts[1]
    assert fake.describe_calls  # the vision path was used
    # injection scan ran over the produced text (a result exists for the page)
    assert result.injection_warnings and 1 in result.injection_warnings


def test_image_without_any_engine_fails_with_guidance(png, monkeypatch):
    ex = _extractor()
    monkeypatch.setattr(ex, "_get_vision_ocr_engine", lambda: None)
    monkeypatch.setattr(de, "get_available_engines", lambda: [])

    with pytest.raises(Exception, match="vision"):
        ex.extract_text(png)


def test_ollama_cloud_vision_key_falls_back_to_shared_key(monkeypatch):
    import config
    ex = _extractor(vision_ocr_provider="ollama_cloud", vision_ocr_api_key="")
    monkeypatch.setattr(config.settings, "ollama_cloud_api_key", "shared-key")
    assert ex._resolve_vision_api_key("ollama_cloud") == "shared-key"
    # An explicit key still wins
    ex2 = _extractor(vision_ocr_provider="ollama_cloud", vision_ocr_api_key="explicit")
    assert ex2._resolve_vision_api_key("ollama_cloud") == "explicit"


# ── Selective per-page OCR ──────────────────────────────────────────────────


@pytest.fixture()
def fake_pdf(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-fake")
    return p


def _with_native_pages(monkeypatch, ex, pages):
    monkeypatch.setattr(ex, "_extract_pdf_with_pdfplumber", lambda path: dict(pages))


def test_weak_pages_get_selective_ocr(fake_pdf, monkeypatch):
    """A texty document with two empty pages OCRs exactly those two pages."""
    ex = _extractor(enable_ocr=True)
    good = "x" * 300
    _with_native_pages(monkeypatch, ex, {1: good, 2: "", 3: good, 4: "  ", 5: good})

    calls = []

    def fake_ocr(path, pages=None):
        calls.append(pages)
        return {p: f"ocr {p}" for p in pages}, list(pages), []

    monkeypatch.setattr(ex, "_extract_pdf_with_ocr", fake_ocr)

    result = ex._extract_pdf(fake_pdf)
    assert calls == [[2, 4]]
    assert result.page_texts[2] == "ocr 2"
    assert result.page_texts[4] == "ocr 4"
    assert result.page_texts[1] == good  # untouched
    assert result.method == "hybrid"
    assert sorted(result.ocr_pages) == [2, 4]


def test_selective_ocr_respects_page_budget(fake_pdf, monkeypatch):
    ex = _extractor(enable_ocr=True, ocr_max_pages=1)
    good = "x" * 300
    _with_native_pages(monkeypatch, ex, {1: good, 2: "", 3: ""})

    calls = []

    def fake_ocr(path, pages=None):
        calls.append(pages)
        return {p: f"ocr {p}" for p in pages}, list(pages), []

    monkeypatch.setattr(ex, "_extract_pdf_with_ocr", fake_ocr)
    ex._extract_pdf(fake_pdf)
    assert calls == [[2]]  # capped at ocr_max_pages


def test_long_document_still_gets_selective_ocr(fake_pdf, monkeypatch):
    """The full-document page cap must not disable the selective path."""
    ex = _extractor(enable_ocr=True, ocr_max_pages=5)
    good = "x" * 300
    pages = {p: good for p in range(1, 21)}  # 20 pages > cap of 5
    pages[7] = ""
    _with_native_pages(monkeypatch, ex, pages)

    calls = []

    def fake_ocr(path, pages=None):
        calls.append(pages)
        return {p: f"ocr {p}" for p in pages}, list(pages), []

    monkeypatch.setattr(ex, "_extract_pdf_with_ocr", fake_ocr)
    result = ex._extract_pdf(fake_pdf)
    assert calls == [[7]]
    assert result.page_texts[7] == "ocr 7"


def test_mostly_empty_document_runs_full_ocr(fake_pdf, monkeypatch):
    """Weak average still triggers whole-document OCR (pages=None)."""
    ex = _extractor(enable_ocr=True)
    _with_native_pages(monkeypatch, ex, {1: "", 2: "tiny", 3: ""})

    calls = []

    def fake_ocr(path, pages=None):
        calls.append(pages)
        return {1: "a", 2: "b", 3: "c"}, [1, 2, 3], []

    monkeypatch.setattr(ex, "_extract_pdf_with_ocr", fake_ocr)
    result = ex._extract_pdf(fake_pdf)
    assert calls == [None]
    assert result.method == "ocr"


def test_scanned_doc_over_page_cap_still_raises(fake_pdf, monkeypatch):
    """A fully scanned document beyond the page budget fails loudly as before."""
    ex = _extractor(enable_ocr=True, ocr_max_pages=2)
    _with_native_pages(monkeypatch, ex, {})
    monkeypatch.setattr(ex, "_get_pdf_page_count", lambda path: 10)

    with pytest.raises(Exception, match="too many pages"):
        ex._extract_pdf(fake_pdf)

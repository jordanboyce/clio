"""Scanned pages are read by whichever local engine can run.

The full image ships Tesseract and not Docling, and the extractor used to ask
only for Docling, so turning OCR on did nothing there while every status report
said it worked. The real-engine tests need Tesseract and Poppler, which the
image has.
"""

import shutil

import pytest

from services import document_extractor as de, ocr_engine
from services.document_extractor import DocumentExtractor
from services.ocr_engine import OCRPage, OCRResult

needs_tesseract = pytest.mark.skipif(
    not ("pytesseract" in ocr_engine.get_available_engines() and shutil.which("pdftoppm")),
    reason="Tesseract and Poppler are not installed",
)


def _scan(tmp_path, name="scan.pdf"):
    from PIL import Image, ImageDraw, ImageFont

    page = Image.new("RGB", (1700, 800), "white")
    ImageDraw.Draw(page).text((80, 200), "Seismic inspection of the turbine", fill="black",
                              font=ImageFont.load_default(size=80))
    path = tmp_path / name
    page.save(path, "PDF" if name.endswith(".pdf") else None, resolution=200)
    return path


class _Engine:
    def __init__(self, text=None, error=None):
        self.text, self.error = text, error
        self.pages = None

    def extract_text(self, path, pages=None):
        self.pages = pages
        if self.error:
            raise self.error
        return OCRResult(pages=[OCRPage(page_number=1, text=self.text)], engine="fake")


def _engines(monkeypatch, **engines):
    monkeypatch.setattr(de, "get_available_engines", lambda: list(engines))
    monkeypatch.setattr(de, "create_ocr_engine", lambda engine_name, fallback: engines[engine_name])


def test_tesseract_reads_when_docling_is_not_installed(monkeypatch, tmp_path):
    _engines(monkeypatch, pytesseract=_Engine("read by tesseract"))

    assert DocumentExtractor(enable_ocr=True)._local_ocr(tmp_path / "a.pdf") == {1: "read by tesseract"}


def test_docling_without_its_models_falls_back_to_tesseract(monkeypatch, tmp_path):
    _engines(monkeypatch, docling=_Engine(error=OSError("model files are not here")),
             pytesseract=_Engine("read by tesseract"))

    assert DocumentExtractor(enable_ocr=True)._local_ocr(tmp_path / "a.pdf") == {1: "read by tesseract"}


def test_an_engine_that_finds_nothing_gives_way_to_the_next(monkeypatch, tmp_path):
    _engines(monkeypatch, docling=_Engine("   "), pytesseract=_Engine("read by tesseract"))

    assert DocumentExtractor(enable_ocr=True)._local_ocr(tmp_path / "a.pdf") == {1: "read by tesseract"}


def test_selective_ocr_asks_the_local_engine_for_only_the_weak_pages(monkeypatch, tmp_path):
    engine = _Engine("read by tesseract")
    _engines(monkeypatch, pytesseract=engine)

    DocumentExtractor(enable_ocr=True)._extract_pdf_with_ocr(tmp_path / "a.pdf", pages=[7])

    assert engine.pages == [7]


def test_availability_follows_the_engines_that_can_run(monkeypatch):
    extractor = DocumentExtractor(enable_ocr=True)

    monkeypatch.setattr(de, "get_available_engines", lambda: ["pytesseract"])
    assert extractor.is_ocr_available() and extractor.get_ocr_engine_name() == "pytesseract"

    monkeypatch.setattr(de, "get_available_engines", lambda: [])
    assert not extractor.is_ocr_available() and extractor.get_ocr_engine_name() is None


def test_a_vision_model_with_no_key_still_leaves_the_local_engine(monkeypatch):
    extractor = DocumentExtractor(enable_ocr=True, vision_ocr_provider="anthropic")
    monkeypatch.setattr(extractor, "_resolve_vision_api_key", lambda provider: "")
    monkeypatch.setattr(de, "get_available_engines", lambda: ["pytesseract"])

    assert not extractor.vision_ocr_usable()
    assert extractor.is_ocr_available() and extractor.get_ocr_engine_name() == "pytesseract"


def test_a_scan_with_ocr_off_says_how_to_read_it(tmp_path):
    pytest.importorskip("PIL")

    with pytest.raises(Exception, match="Turn OCR on in Settings"):
        DocumentExtractor(enable_ocr=False).extract_text(_scan(tmp_path))


def test_a_file_that_is_not_a_pdf_at_all_still_says_it_could_not_be_read(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"this is not a pdf")

    with pytest.raises(Exception, match="all methods failed"):
        DocumentExtractor(enable_ocr=False).extract_text(path)


@needs_tesseract
def test_a_real_scanned_pdf_is_read(tmp_path):
    result = DocumentExtractor(enable_ocr=True).extract_text(_scan(tmp_path))

    assert "turbine" in result.page_texts[1].lower()
    assert result.method == "ocr"


@needs_tesseract
def test_tesseract_renders_only_the_pages_asked_for(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw, ImageFont
    import pdf2image

    font = ImageFont.load_default(size=80)
    sheets = []
    for word in ("alpha", "bravo", "charlie"):
        sheet = Image.new("RGB", (1700, 800), "white")
        ImageDraw.Draw(sheet).text((80, 200), f"Page about {word}", fill="black", font=font)
        sheets.append(sheet)
    path = tmp_path / "three.pdf"
    sheets[0].save(path, "PDF", resolution=200, save_all=True, append_images=sheets[1:])

    rendered = []
    real = pdf2image.convert_from_path

    def spy(*args, **kwargs):
        rendered.append((kwargs.get("first_page"), kwargs.get("last_page")))
        return real(*args, **kwargs)

    monkeypatch.setattr(pdf2image, "convert_from_path", spy)
    engine = ocr_engine.LegacyOCREngine()

    only_second = engine.extract_text(str(path), pages=[2]).to_page_dict()
    assert list(only_second) == [2] and "bravo" in only_second[2].lower()
    assert rendered == [(2, 2)]

    rendered.clear()
    assert list(engine.extract_text(str(path)).to_page_dict()) == [1, 2, 3]
    assert rendered == [(1, 1), (2, 2), (3, 3)]


@needs_tesseract
def test_a_real_image_is_read(tmp_path):
    result = DocumentExtractor(enable_ocr=True).extract_text(_scan(tmp_path, "scan.png"))

    assert "turbine" in result.page_texts[1].lower()

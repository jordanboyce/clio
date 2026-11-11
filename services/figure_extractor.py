"""Figures embedded in documents: the charts, diagrams and photographs a
reader recognises on sight.

The text index drops them: the DOCX reader walks paragraphs, the PPTX
reader takes text frames, HTML goes through its text and PDFs through
their text layer. This module pulls the pictures back out at index time,
tagged with the page (or slide, or section) the text extractor would give
the words around them, so a result that cites page 12 can show the chart
on page 12.

What is kept is deliberately narrow. Pictures smaller than
``MIN_FIGURE_PX`` on a side are bullets, rules and icons; a picture whose
pixels were already kept for this document is a repeated logo or header
graphic; vector drawings (EMF, WMF, SVG) have no raster to show. Everything
else is stored as it was embedded (PNG, JPEG, GIF, WebP) or, for other
encodings and PDF image objects, re-encoded as PNG. Counts and bytes are
capped per document so a photo album cannot swallow a collection's storage.

Nothing here raises past :func:`extract_figures`: a document whose figures
cannot be read still indexes its text.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

FIGURE_FORMATS = frozenset({"pdf", "docx", "pptx", "html", "htm"})

MIN_FIGURE_PX = 48
MAX_FIGURES_PER_DOCUMENT = 200
MAX_FIGURE_BYTES = 12 * 1024 * 1024
MAX_FIGURE_TOTAL_BYTES = 96 * 1024 * 1024
# <img src="https://..."> in saved pages: fetched through the link fetcher,
# so its address checks apply, and only this many per document.
MAX_REMOTE_IMAGES = 12
REMOTE_IMAGE_MAX_BYTES = 8 * 1024 * 1024
MAX_CAPTION_CHARS = 300

# Encodings stored as embedded; anything else is re-encoded as PNG.
_KEPT_FORMATS = {"PNG": "png", "JPEG": "jpg", "GIF": "gif", "WEBP": "webp"}
_MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}

# "Figure 3: ...", "Fig. 2 - ...", "Chart 1", "Exhibit 4a".
CAPTION_RE = re.compile(
    r"^\s*(?:fig(?:ure)?\.?|chart|exhibit|diagram|graph|illustration|image|photo(?:graph)?|plate)"
    r"\s*\d+[a-z]?\b",
    re.IGNORECASE,
)
# Alt text that is only the file's name says nothing about the picture.
_FILENAME_RE = re.compile(r"^[\w\- .()]+\.(?:png|jpe?g|gif|bmp|tiff?|webp|emf|wmf|svg)$", re.IGNORECASE)
# Office's default shape names.
_GENERIC_NAME_RE = re.compile(
    r"^(?:picture|image|graphic|content placeholder|picture placeholder|grafik|bild|imagen)\s*\d*$",
    re.IGNORECASE,
)


@dataclass
class ExtractedFigure:
    """One picture pulled from a document, ready to store."""

    page_number: int
    position: int            # order on the page, 1-based
    data: bytes
    ext: str                 # png | jpg | gif | webp
    width: int
    height: int
    caption: str = ""
    content_hash: str = ""
    description: Optional[str] = None   # filled by the vision pass, when configured

    @property
    def media_type(self) -> str:
        return media_type_for(self.ext)


def media_type_for(ext: Optional[str]) -> str:
    return _MEDIA_TYPES.get((ext or "").lower().lstrip("."), "application/octet-stream")


def supports_figures(source_format: Optional[str]) -> bool:
    return bool(source_format) and source_format.lower() in FIGURE_FORMATS


def clean_caption(text: Optional[str]) -> str:
    """Alt text or caption trimmed to one line; '' for nothing worth keeping."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", str(text)).strip()
    if not text or _FILENAME_RE.match(text) or _GENERIC_NAME_RE.match(text):
        return ""
    if len(text) > MAX_CAPTION_CHARS:
        text = text[: MAX_CAPTION_CHARS - 1].rstrip() + "…"
    return text


def captions_in_text(text: Optional[str]) -> List[str]:
    """Lines of a page that read like figure captions, in order."""
    if not text:
        return []
    return [clean_caption(line) for line in text.splitlines() if CAPTION_RE.match(line) and clean_caption(line)]


class _Collector:
    """Applies the size filter, the per-document dedupe and the caps."""

    def __init__(self):
        self.figures: List[ExtractedFigure] = []
        self._hashes: set = set()
        self._positions: Dict[int, int] = {}
        self._total_bytes = 0
        self.skipped_small = 0
        self.skipped_duplicate = 0

    @property
    def full(self) -> bool:
        return len(self.figures) >= MAX_FIGURES_PER_DOCUMENT or self._total_bytes >= MAX_FIGURE_TOTAL_BYTES

    def add(self, page_number: int, data: Optional[bytes] = None, caption: str = "",
            image=None, prefer_jpeg: bool = False) -> Optional[ExtractedFigure]:
        """Keep one picture given as encoded bytes or as a PIL image."""
        if self.full:
            return None
        from PIL import Image

        try:
            if image is None:
                if not data or len(data) > MAX_FIGURE_BYTES:
                    return None
                image = Image.open(io.BytesIO(data))
                image.load()
            width, height = image.size
        except Exception as e:  # not a raster image (SVG, EMF, truncated bytes)
            logger.debug(f"figure skipped: unreadable image ({e})")
            return None

        if width < MIN_FIGURE_PX or height < MIN_FIGURE_PX:
            self.skipped_small += 1
            return None

        content_hash = _pixel_hash(image)
        if content_hash in self._hashes:
            self.skipped_duplicate += 1
            return None

        ext = _KEPT_FORMATS.get(image.format or "") if data is not None else None
        if ext is None:
            data, ext = _encode(image, prefer_jpeg)
        if len(data) > MAX_FIGURE_BYTES or self._total_bytes + len(data) > MAX_FIGURE_TOTAL_BYTES:
            return None

        self._hashes.add(content_hash)
        self._total_bytes += len(data)
        position = self._positions.get(page_number, 0) + 1
        self._positions[page_number] = position
        figure = ExtractedFigure(
            page_number=page_number, position=position, data=data, ext=ext,
            width=width, height=height, caption=clean_caption(caption), content_hash=content_hash,
        )
        self.figures.append(figure)
        return figure


def _pixel_hash(image) -> str:
    """Identity of what the picture shows, not how it was encoded: the same
    logo saved as PNG in one place and JPEG in another is one figure."""
    rgb = image.convert("RGB")
    h = hashlib.sha1()
    h.update(f"{rgb.width}x{rgb.height}:".encode())
    h.update(rgb.tobytes())
    return h.hexdigest()


def _encode(image, prefer_jpeg: bool) -> Tuple[bytes, str]:
    from PIL import Image

    buf = io.BytesIO()
    has_alpha = image.mode in ("RGBA", "LA", "P") and (
        image.mode != "P" or "transparency" in image.info
    )
    if prefer_jpeg and not has_alpha:
        image.convert("RGB").save(buf, format="JPEG", quality=88, optimize=True)
        return buf.getvalue(), "jpg"
    if image.mode not in ("RGB", "RGBA", "L", "LA", "P", "1"):
        image = image.convert("RGBA" if has_alpha else "RGB")
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), "png"


# ── PDF ─────────────────────────────────────────────────────────────────


def _pdf_figures(path: Path, page_texts: Optional[Dict[int, str]], collector: _Collector) -> None:
    from services.pdfium_lock import PDFIUM_LOCK

    with PDFIUM_LOCK:
        _pdf_figures_locked(path, page_texts, collector)


def _pdf_figures_locked(path: Path, page_texts: Optional[Dict[int, str]], collector: _Collector) -> None:
    """Call with PDFIUM_LOCK held. Every image handed to the collector is a
    copy, so nothing it keeps still points into memory PDFium owns."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        for index in range(len(doc)):
            if collector.full:
                break
            page_number = index + 1
            page = doc[index]
            try:
                objects = [
                    obj for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE], max_depth=4)
                    if isinstance(obj, pdfium.PdfImage)
                ]
                placed = []
                for obj in objects:
                    try:
                        left, bottom, right, top = obj.get_bounds()
                    except Exception:
                        left, top = 0.0, 0.0
                    placed.append((-top, left, obj))
                placed.sort(key=lambda t: (t[0], t[1]))

                kept = []
                for _, _, obj in placed:
                    try:
                        w, h = obj.get_px_size()
                        if w < MIN_FIGURE_PX or h < MIN_FIGURE_PX:
                            collector.skipped_small += 1
                            continue
                        is_jpeg = "DCTDecode" in (obj.get_filters() or [])
                        # The bitmap belongs to the page (pypdfium2 warns on
                        # closing it); copy the pixels and let it go here,
                        # under the lock, rather than in some later GC.
                        bitmap = obj.get_bitmap(render=False)
                        image = bitmap.to_pil().copy()
                        del bitmap
                    except Exception as e:
                        logger.debug(f"pdf figure on page {page_number} of {path.name} skipped: {e}")
                        continue
                    figure = collector.add(page_number, image=image, prefer_jpeg=is_jpeg)
                    if figure is not None:
                        kept.append(figure)
                _assign_page_captions(kept, (page_texts or {}).get(page_number))
            finally:
                page.close()
    finally:
        doc.close()


def _assign_page_captions(figures: List[ExtractedFigure], page_text: Optional[str]) -> None:
    """Match 'Figure N' lines of the page to its pictures when the counts
    agree; otherwise say nothing rather than label a chart with the wrong
    caption."""
    if not figures:
        return
    captions = captions_in_text(page_text)
    if len(captions) == len(figures):
        for figure, caption in zip(figures, captions):
            figure.caption = figure.caption or caption


# ── DOCX ────────────────────────────────────────────────────────────────

# Mirrors DocumentExtractor._extract_docx, which groups every ten
# non-empty paragraphs into a "page".
_DOCX_PARAGRAPHS_PER_PAGE = 10


def _docx_figures(path: Path, collector: _Collector) -> None:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    parts = doc.part.related_parts
    body = doc.element.body

    text_paragraphs = 0
    pending: List[Tuple[int, bytes, str]] = []   # (text index, blob, alt): await a caption
    records: List[Tuple[int, bytes, str]] = []

    def flush(caption: str = "") -> None:
        for idx, blob, alt in pending:
            records.append((idx, blob, alt or caption))
        pending.clear()

    for element in body.iterchildren():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag not in ("p", "tbl"):
            continue
        text = Paragraph(element, doc).text.strip() if tag == "p" else ""
        blips = element.xpath(".//a:blip")
        alts = [clean_caption(d.get("descr") or d.get("title")) for d in element.xpath(".//wp:docPr")]
        if len(alts) != len(blips):
            alts = [""] * len(blips)

        if text and pending:
            flush(text if CAPTION_RE.match(text) else "")

        for blip, alt in zip(blips, alts):
            rid = blip.get(qn("r:embed"))
            part = parts.get(rid) if rid else None
            blob = getattr(part, "blob", None)
            if not blob:
                continue
            caption = alt or (clean_caption(text) if text and len(text) <= MAX_CAPTION_CHARS else "")
            if caption:
                records.append((text_paragraphs, blob, caption))
            else:
                pending.append((text_paragraphs, blob, ""))

        if text:
            text_paragraphs += 1
    flush()

    last_page = max(1, (max(text_paragraphs, 1) - 1) // _DOCX_PARAGRAPHS_PER_PAGE + 1)
    for idx, blob, caption in records:
        page = min(idx // _DOCX_PARAGRAPHS_PER_PAGE + 1, last_page)
        collector.add(page, data=blob, caption=caption)


# ── PPTX ────────────────────────────────────────────────────────────────


def _pptx_figures(path: Path, collector: _Collector) -> None:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(str(path))

    def pictures(shapes) -> list:
        found = []
        for shape in shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                found.extend(pictures(shape.shapes))
                continue
            image = None
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE or shape.is_placeholder:
                try:
                    image = shape.image
                except (AttributeError, ValueError):
                    image = None
            if image is not None:
                found.append((shape.top or 0, shape.left or 0, shape, image))
        return found

    for slide_number, slide in enumerate(prs.slides, start=1):
        if collector.full:
            break
        for _, _, shape, image in sorted(pictures(slide.shapes), key=lambda t: (t[0], t[1])):
            caption = ""
            try:
                props = shape._element.xpath(".//p:cNvPr")
                if props:
                    caption = clean_caption(props[0].get("descr") or props[0].get("title"))
            except Exception:
                caption = ""
            caption = caption or clean_caption(shape.name)
            collector.add(slide_number, data=image.blob, caption=caption)


# ── HTML ────────────────────────────────────────────────────────────────

_DATA_URI_RE = re.compile(r"^data:(image/[\w.+-]+)?;?(?:[^,]*?)(base64)?,(.*)$", re.DOTALL)


def _html_figures(data: bytes, collector: _Collector, *, fetch_remote: bool, base_url: str = "") -> None:
    from bs4 import BeautifulSoup

    from services.document_extractor import DocumentExtractor

    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "iframe", "svg", "canvas"]):
        tag.decompose()
    DocumentExtractor._strip_html_boilerplate(soup)

    base = soup.find("base", href=True)
    if base is not None:
        base_url = base["href"]
    body = soup.body or soup

    # Section numbers follow _html_to_sections: a new section at each h1/h2,
    # and the text before the first heading is a section only when there is some.
    first_heading = body.find(["h1", "h2"])
    section = 1
    if first_heading is not None and not any(
        s.strip() for s in first_heading.find_all_previous(string=True)
    ):
        section = 0

    remote: List[Tuple[int, str, str]] = []
    seen_urls: set = set()
    for node in body.find_all(["h1", "h2", "img"]):
        if node.name in ("h1", "h2"):
            section += 1
            continue
        src = (node.get("src") or node.get("data-src") or "").strip()
        if not src:
            continue
        caption = clean_caption(node.get("alt"))
        if not caption:
            figure = node.find_parent("figure")
            if figure is not None and figure.figcaption is not None:
                caption = clean_caption(figure.figcaption.get_text(" ", strip=True))
        caption = caption or clean_caption(node.get("title"))
        page = max(1, section)

        match = _DATA_URI_RE.match(src)
        if match:
            if match.group(2):
                try:
                    collector.add(page, data=base64.b64decode(match.group(3), validate=False), caption=caption)
                except Exception as e:
                    logger.debug(f"html figure: unreadable data URI ({e})")
            continue
        url = urljoin(base_url, src) if base_url else src
        if url.lower().startswith(("http://", "https://")) and url not in seen_urls:
            seen_urls.add(url)
            remote.append((page, url, caption))

    if not remote or not fetch_remote:
        if remote:
            logger.debug(f"html figures: {len(remote)} remote image(s) not fetched (link indexing off)")
        return

    from services.link_fetcher import LinkError, fetch_link

    for page, url, caption in remote[:MAX_REMOTE_IMAGES]:
        if collector.full:
            break
        try:
            fetched = fetch_link(url, max_bytes=REMOTE_IMAGE_MAX_BYTES)
        except LinkError as e:
            logger.debug(f"html figure not fetched from {url}: {e}")
            continue
        except Exception as e:
            logger.warning(f"html figure fetch failed for {url}: {e}")
            continue
        if fetched.content_type and not fetched.content_type.startswith("image/"):
            continue
        collector.add(page, data=fetched.body, caption=caption)


# ── Entry point ─────────────────────────────────────────────────────────


def extract_figures(path: Path, source_format: Optional[str] = None,
                    page_texts: Optional[Dict[int, str]] = None,
                    *, fetch_remote: Optional[bool] = None) -> List[ExtractedFigure]:
    """The figures embedded in one document, in reading order.

    ``page_texts`` is the text extractor's output for the same file: PDF
    captions are read from it, and every figure's page is clamped to the
    pages it has. ``fetch_remote`` decides whether ``<img src="https://…">``
    in HTML is downloaded; by default it follows the link-indexing setting.
    Never raises: a document whose pictures cannot be read yields [].
    """
    fmt = (source_format or path.suffix.lstrip(".")).lower()
    if fmt not in FIGURE_FORMATS:
        return []
    collector = _Collector()
    try:
        if fmt == "pdf":
            _pdf_figures(path, page_texts, collector)
        elif fmt == "docx":
            _docx_figures(path, collector)
        elif fmt == "pptx":
            _pptx_figures(path, collector)
        else:
            if fetch_remote is None:
                from services.link_fetcher import link_indexing_available

                fetch_remote = link_indexing_available()
            _html_figures(path.read_bytes(), collector, fetch_remote=fetch_remote)
    except Exception as e:
        logger.warning(f"Figure extraction failed for {path.name}: {e}")
        return []

    figures = collector.figures
    if page_texts:
        last = max(page_texts)
        for figure in figures:
            figure.page_number = min(max(1, figure.page_number), last)
    if figures or collector.skipped_small or collector.skipped_duplicate:
        logger.info(
            f"Figures in {path.name}: {len(figures)} kept, {collector.skipped_small} too small, "
            f"{collector.skipped_duplicate} repeated"
        )
    return figures

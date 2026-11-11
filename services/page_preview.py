"""Page previews: a small picture of the page a result cites.

The index is text, but the source is often a page a reader recognises on
sight: a chart, a form, a slide, a photograph. A result or a chat source
whose format can be drawn carries a ``preview_url``; the endpoint behind it
renders the cited page (PDFs, through pdfium) or scales the picture itself
(image documents) to a thumbnail. Nothing is stored at index time: previews
are drawn on request and kept in a small in-memory cache keyed on the file's
identity, so a re-indexed or replaced file never shows a stale picture.

Only formats with a faithful page to show get a preview. A DOCX "page" is
ten paragraphs and a Markdown "page" is a section: neither has a rendering
that matches the citation, so they are left out rather than drawn wrongly.
"""

from __future__ import annotations

import hashlib
import io
import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

PDF_FORMATS = frozenset({"pdf"})
IMAGE_FORMATS = frozenset({"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "gif"})
PREVIEW_FORMATS = PDF_FORMATS | IMAGE_FORMATS

DEFAULT_WIDTH = 320
MIN_WIDTH = 64
MAX_WIDTH = 640

# Thumbnails are a few tens of kilobytes; this keeps the cache well under
# 32 MB on a busy instance while a page of results reuses its pictures.
CACHE_ENTRIES = 256


class PreviewUnavailable(Exception):
    """The document has no page to draw (format, page out of range, or the
    file could not be read as what its extension says it is)."""


def supports_preview(source_format: Optional[str]) -> bool:
    return bool(source_format) and source_format.lower() in PREVIEW_FORMATS


def preview_url(base_url: str, document_id: str, collection_id: str,
                page_number: Optional[int], source_format: Optional[str]) -> Optional[str]:
    """The URL a result carries, or None when the format has no preview."""
    if not supports_preview(source_format):
        return None
    page = page_number if page_number and page_number > 0 else 1
    return f"{base_url}/documents/{document_id}/preview?collection_id={collection_id}&page={page}"


def clamp_width(width: Optional[int]) -> int:
    if not width:
        return DEFAULT_WIDTH
    return max(MIN_WIDTH, min(MAX_WIDTH, int(width)))


def _render_pdf_page(path: Path, page_number: int, width: int) -> bytes:
    from services.pdfium_lock import PDFIUM_LOCK

    with PDFIUM_LOCK:
        image = _render_pdf_page_locked(path, page_number, width)
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _render_pdf_page_locked(path: Path, page_number: int, width: int):
    """The page as a PIL image (a copy, not a view of PDFium's bitmap).
    Call with PDFIUM_LOCK held."""
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(str(path))
    except Exception as e:  # pdfium raises its own error type per failure
        raise PreviewUnavailable(f"Could not open {path.name} as a PDF: {e}") from e
    try:
        if page_number < 1 or page_number > len(doc):
            raise PreviewUnavailable(
                f"{path.name} has {len(doc)} page(s); page {page_number} does not exist"
            )
        page = doc[page_number - 1]
        try:
            page_width = page.get_width() or 1
            bitmap = page.render(scale=width / page_width)
            try:
                image = bitmap.to_pil().copy()
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        doc.close()
    return image


def _scale_image(path: Path, width: int) -> Tuple[bytes, str]:
    from PIL import Image

    try:
        with Image.open(path) as img:
            img.load()
            # Multi-frame formats (GIF, TIFF) show their first frame.
            if img.mode in ("RGBA", "LA", "P"):
                background = Image.new("RGB", img.size, "white")
                background.paste(img.convert("RGBA"), mask=img.convert("RGBA").getchannel("A"))
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")
            if img.width > width:
                img.thumbnail((width, width * 4), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=82, optimize=True)
            return buf.getvalue(), "image/jpeg"
    except PreviewUnavailable:
        raise
    except Exception as e:
        raise PreviewUnavailable(f"Could not read {path.name} as an image: {e}") from e


class PreviewCache:
    """A small LRU of rendered previews, keyed on the file's identity."""

    def __init__(self, max_entries: int = CACHE_ENTRIES):
        self.max_entries = max_entries
        self._entries: "OrderedDict[tuple, Tuple[bytes, str]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple) -> Optional[Tuple[bytes, str]]:
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None:
                self._entries.move_to_end(key)
            return hit

    def put(self, key: tuple, value: Tuple[bytes, str]) -> None:
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_cache = PreviewCache()


def _file_identity(path: Path) -> tuple:
    st = path.stat()
    return (str(path.resolve()), st.st_mtime_ns, st.st_size)


def etag_for(path: Path, page_number: int, width: int) -> str:
    """A strong validator: changes when the file, page or size does."""
    ident = _file_identity(path) + (page_number, width)
    return '"' + hashlib.sha1(repr(ident).encode("utf-8")).hexdigest()[:32] + '"'


def render_preview(path: Path, source_format: Optional[str], page_number: int = 1,
                   width: int = DEFAULT_WIDTH) -> Tuple[bytes, str]:
    """Draw the preview for one page; returns (bytes, media_type).

    ``source_format`` is the indexed format (the file's extension without
    the dot). Raises PreviewUnavailable for formats without a preview, a page
    that does not exist, or a file that cannot be read.
    """
    fmt = (source_format or path.suffix.lstrip(".")).lower()
    if fmt not in PREVIEW_FORMATS:
        raise PreviewUnavailable(f"No page preview for .{fmt} files")
    width = clamp_width(width)
    page_number = page_number if fmt in PDF_FORMATS else 1
    key = _file_identity(path) + (page_number, width)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    if fmt in PDF_FORMATS:
        rendered = (_render_pdf_page(path, page_number, width), "image/png")
    else:
        rendered = _scale_image(path, width)
    _cache.put(key, rendered)
    return rendered

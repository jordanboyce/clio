"""Write the committed smoke PDFs for the research metadata benchmark.

Every document here is invented (titles, people, places, numbers; DOIs under
the 10.5555 test prefix; arXiv ids numbered past anything arXiv has issued),
so the files are free to redistribute (CC0). Each one reproduces a shape of
PDF that real papers come in and that Clio's paper records have got wrong:

  smoke-clean-article     one column, arXiv stamp, DOI: the easy case
  smoke-two-column        conference layout, author marks, venue header
  smoke-lost-spaces       words separated by kerning, not space characters
                          ("LuyuGao XueguangMa" in the live check)
  smoke-epigraph          a quotation and its author above the title
                          (a paper credited to the person it quotes)
  smoke-title-image       the title is a picture; page text starts mid-abstract
  smoke-scan              image only, no text layer (a scanned page)
  smoke-book              title page, copyright page with an ISBN, preface
  smoke-report            corporate author, no identifiers at all

Run it from the repository root; it rewrites the PDFs next to smoke.json:

    venv/Scripts/python tests/fixtures/research_bench/make_smoke_pdfs.py

The output is deterministic for a given Pillow version (Pillow draws the two
raster pages). Regenerating a file changes its bytes only if this script or
Pillow changed; bump smoke.json's version when a document's content changes.
"""

from __future__ import annotations

import zlib
from pathlib import Path
from typing import List, Optional, Tuple

OUT = Path(__file__).resolve().parent / "smoke"
PAGE_W, PAGE_H = 612, 792  # US Letter, points

FONTS = {"F1": "Helvetica", "F2": "Helvetica-Bold", "F3": "Times-Roman", "F4": "Times-Bold"}


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _latin1(text: str) -> bytes:
    return text.encode("cp1252", errors="replace")


class Page:
    """One page's content stream, built from positioned text runs."""

    def __init__(self) -> None:
        self.ops: List[bytes] = []
        self.image: Optional[Tuple[int, int, bytes]] = None  # (w, h, gray pixels)
        self.image_box: Tuple[float, float, float, float] = (0, 0, PAGE_W, PAGE_H)

    def text(self, x: float, y: float, s: str, font: str = "F1", size: float = 10) -> "Page":
        self.ops.append(b"BT /%s %.1f Tf %.1f %.1f Td (" % (font.encode(), size, x, y)
                        + _latin1(_esc(s)) + b") Tj ET")
        return self

    def kerned(self, x: float, y: float, words: List[str], font: str = "F1", size: float = 10,
               gap: int = -160) -> "Page":
        """Words placed apart by TJ kerning with no space glyph between them."""
        parts = b" ".join(b"(" + _latin1(_esc(w)) + b")" for w in words)
        parts = parts.replace(b") (", b") %d (" % gap)
        self.ops.append(b"BT /%s %.1f Tf %.1f %.1f Td [" % (font.encode(), size, x, y) + parts + b"] TJ ET")
        return self

    def paragraph(self, x: float, y: float, s: str, width: float, font: str = "F3", size: float = 10,
                  leading: float = 12.5) -> float:
        """Wrap ``s`` into lines no wider than ``width``; return the y below it."""
        max_chars = max(10, int(width / (size * 0.48)))
        line = ""
        for word in s.split():
            if line and len(line) + 1 + len(word) > max_chars:
                self.text(x, y, line, font, size)
                y -= leading
                line = word
            else:
                line = f"{line} {word}" if line else word
        if line:
            self.text(x, y, line, font, size)
            y -= leading
        return y

    def full_page_image(self, width: int, height: int, gray: bytes,
                        box: Tuple[float, float, float, float] = (0, 0, PAGE_W, PAGE_H)) -> "Page":
        self.image = (width, height, gray)
        self.image_box = box
        return self


def write_pdf(path: Path, pages: List[Page], title: Optional[str] = None) -> None:
    """A minimal, valid PDF 1.4: the standard 14 fonts, no timestamps."""
    objects: List[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_ids = {name: add(b"<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>"
                          % base.encode()) for name, base in FONTS.items()}
    font_dict = b" ".join(b"/%s %d 0 R" % (n.encode(), i) for n, i in font_ids.items())
    pages_id = len(objects) + 1
    objects.append(b"")  # placeholder for /Pages
    page_ids = []
    for page in pages:
        ops = list(page.ops)
        xobjects = b""
        if page.image:
            w, h, gray = page.image
            data = zlib.compress(gray, 9)
            img_id = add(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray "
                         b"/BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n" % (w, h, len(data))
                         + data + b"\nendstream")
            x0, y0, bw, bh = page.image_box
            ops.insert(0, b"q %.1f 0 0 %.1f %.1f %.1f cm /Im1 Do Q" % (bw, bh, x0, y0))
            xobjects = b" /XObject << /Im1 %d 0 R >>" % img_id
        stream = b"\n".join(ops)
        content_id = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
                            b"/Resources << /Font << %s >>%s >> >>"
                            % (pages_id, PAGE_W, PAGE_H, content_id, font_dict, xobjects)))
    objects[pages_id - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % i for i in page_ids), len(page_ids))
    info_id = add(b"<< /Producer (clio research_bench smoke fixture)%s >>"
                  % ((b" /Title (" + _latin1(_esc(title)) + b")") if title else b""))
    catalog_id = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, catalog_id, info_id, xref)
    path.write_bytes(bytes(out))


def raster(lines: List[Tuple[str, int, int]], width: int, height: int) -> bytes:
    """Gray pixels of text lines ``(text, size_px, y_px)`` centred on white."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)
    for text, size, y in lines:
        font = ImageFont.load_default(size=size)
        w = draw.textlength(text, font=font)
        draw.text(((width - w) / 2, y), text, fill=0, font=font)
    return img.tobytes()


FILLER = ("The survey covered forty-two stations along the northern shore over three winters. Each station "
          "logged water level, salinity and temperature every ten minutes, and the archive now holds "
          "about nineteen million readings. We describe how the readings were cleaned, how gaps were "
          "marked rather than filled, and how the archive answers range queries in under a second.")


def references(page: Page, y: float, entries: List[str], x: float = 72, width: float = 468) -> None:
    page.text(x, y, "References", "F2", 12)
    y -= 18
    for entry in entries:
        y = page.paragraph(x, y, entry, width, "F3", 9, 11) - 4


REFS = [
    "[1] H. Lindqvist and P. Oduya. Tide gauge drift over decades. Imaginary Ocean Letters, 2019.",
    "[2] M. Ferreira. Compressing sensor archives without losing gaps. Journal of Invented Systems, 2021.",
    "[3] K. Abara, L. Brandt. Range queries over irregular time series. arXiv:2611.99950, 2026.",
]


def clean_article() -> List[Page]:
    p1 = Page()
    p1.text(20, 600, "arXiv:2611.99901v2 [cs.DB] 3 Nov 2026", "F1", 9)
    p1.text(72, 700, "Lighthouse Indexing for Coastal Sensor Archives", "F2", 18)
    p1.text(72, 676, "Ines Halvorsen, Tomasz Wierzbicki and Amara Okonjo", "F1", 11)
    p1.text(72, 662, "Department of Marine Informatics, Northshore Institute", "F1", 9)
    p1.text(72, 630, "Abstract", "F2", 11)
    y = p1.paragraph(72, 614, FILLER, 468)
    p1.text(72, y - 16, "1 Introduction", "F2", 11)
    y = p1.paragraph(72, y - 32, FILLER, 468)
    p1.text(72, 60, "doi: 10.5555/smoke.2026.0001", "F1", 8)
    p2 = Page()
    references(p2, 720, REFS)
    return [p1, p2]


def two_column() -> List[Page]:
    p1 = Page()
    p1.text(150, 760, "Proceedings of the Workshop on Imaginary Retrieval, pages 12-20", "F3", 9)
    p1.text(190, 748, "(c) 2024 Association for Imaginary Linguistics", "F3", 9)
    p1.text(118, 712, "Sparse Lexicons Outperform Dense Maps on", "F4", 15)
    p1.text(176, 694, "Short Harbour Logbook Queries", "F4", 15)
    p1.text(130, 668, "Rhea Castellanos1* Bram Vos2 Keiko Tanabe1", "F3", 11)
    p1.text(150, 654, "1Southport University 2Delta Maritime Archive", "F3", 9)
    p1.text(150, 620, "Abstract", "F4", 11)
    y = p1.paragraph(72, 604, FILLER, 220, "F3", 9.5, 11.5)
    p1.text(72, y - 12, "1 Introduction", "F4", 11)
    p1.paragraph(72, y - 26, FILLER + " " + FILLER, 220, "F3", 9.5, 11.5)
    p1.paragraph(320, 604, FILLER + " " + FILLER + " " + FILLER, 220, "F3", 9.5, 11.5)
    p1.text(72, 40, "doi: 10.5555/wir.2024.012", "F3", 8)
    p2 = Page()
    references(p2, 720, REFS, x=72, width=220)
    return [p1, p2]


def lost_spaces() -> List[Page]:
    p1 = Page()
    p1.kerned(72, 700, ["Tidal", "Memory", "in", "Long", "Sensor", "Records"], "F2", 18, gap=-90)
    p1.kerned(72, 676, ["Oskar", "Lindgren1", "Wen", "Zhao2", "Priya", "Raman1", "Tomas", "Ruiz2"], "F1", 11,
              gap=-90)
    p1.text(72, 662, "1Northshore Institute 2Southport University", "F1", 9)
    p1.text(72, 630, "Abstract", "F2", 11)
    p1.paragraph(72, 614, FILLER, 468)
    p1.text(72, 60, "Preprint, 2025.", "F1", 8)
    p2 = Page()
    references(p2, 720, REFS)
    return [p1, p2]


def epigraph() -> List[Page]:
    p1 = Page()
    p1.text(300, 740, "\"A harbour remembers every storm it has sheltered.\"", "F3", 10)
    p1.text(380, 726, "Marta Ellison", "F3", 10)
    p1.text(72, 690, "Storm Surge Memory in Enclosed Harbours", "F2", 18)
    p1.text(72, 666, "Dmitri Volkov and Ayesha Karim", "F1", 11)
    p1.text(72, 652, "Coastal Dynamics Group, Westmere College", "F1", 9)
    p1.text(72, 620, "Abstract", "F2", 11)
    p1.paragraph(72, 604, FILLER, 468)
    p1.text(72, 60, "Published 2023 in the Journal of Invented Coasts.", "F1", 8)
    p2 = Page()
    references(p2, 720, REFS)
    return [p1, p2]


def title_image() -> List[Page]:
    p1 = Page()
    w, h = 1200, 90
    p1.full_page_image(w, h, raster([("Counting Gulls with Rooftop Cameras", 46, 18)], w, h),
                       box=(56, 690, 500, 37.5))
    p1.text(72, 668, "Lena Okafor, Sami Haddad", "F1", 11)
    # Body text set in the same size as the author line: nothing on the page
    # says which line is the title once the real one is a picture.
    p1.text(72, 640, "We count nesting gulls on city rooftops from fixed cameras across two", "F1", 10)
    p1.text(72, 627, "breeding seasons and compare the counts with volunteer surveys of the", "F1", 10)
    p1.text(72, 614, "same roofs. Automated counts track the surveys within six percent.", "F1", 10)
    p1.paragraph(72, 590, FILLER, 468)
    p1.text(72, 60, "Imaginary Urban Ecology, 2022", "F1", 8)
    p2 = Page()
    references(p2, 720, REFS)
    return [p1, p2]


def scan() -> List[Page]:
    w, h = 1020, 1320  # 120 dpi
    gray = raster([
        ("Notes on Breakwater Settlement", 44, 170),
        ("at the Old Quay", 44, 225),
        ("Harriet Calloway", 30, 320),
        ("Harbour Survey Office", 24, 365),
        ("1987", 26, 430),
        ("Abstract", 26, 520),
        ("Settlement of the old quay breakwater was surveyed", 22, 570),
        ("at twelve marks over nine years.", 22, 602),
    ], w, h)
    return [Page().full_page_image(w, h, gray)]


def book() -> List[Page]:
    cover = Page()
    cover.text(150, 560, "A Field Guide to", "F2", 26)
    cover.text(150, 525, "Salt Marsh Grasses", "F2", 26)
    cover.text(150, 470, "by Pilar Montenegro", "F1", 14)
    imprint = Page()
    imprint.text(72, 300, "A Field Guide to Salt Marsh Grasses", "F1", 9)
    imprint.text(72, 286, "Copyright (c) 2019 Pilar Montenegro. All rights reserved.", "F1", 9)
    imprint.text(72, 272, "First published 2019 by Ostrander Press, Port Elwin.", "F1", 9)
    imprint.text(72, 258, "ISBN 978-0-306-40615-7", "F1", 9)
    preface = Page()
    preface.text(72, 700, "Preface", "F2", 16)
    preface.paragraph(72, 670, "This book began as a set of notes for volunteers who count grasses on the "
                      "marsh each spring. " + FILLER, 468)
    chapter = Page()
    chapter.text(72, 700, "Chapter 1", "F2", 16)
    chapter.paragraph(72, 670, FILLER + " " + FILLER, 468)
    return [cover, imprint, preface, chapter]


def report() -> List[Page]:
    p1 = Page()
    p1.text(72, 640, "Annual Review of Breakwater Condition", "F2", 22)
    p1.text(72, 600, "Harbour Authority Engineering Office", "F1", 13)
    p1.text(72, 582, "March 2021", "F1", 12)
    p2 = Page()
    p2.text(72, 700, "Summary", "F2", 14)
    p2.paragraph(72, 676, "All twelve breakwater sections were inspected between October and January. "
                 + FILLER, 468)
    return [p1, p2]


DOCUMENTS = {
    "smoke-clean-article.pdf": (clean_article, None),
    "smoke-two-column.pdf": (two_column, None),
    "smoke-lost-spaces.pdf": (lost_spaces, None),
    "smoke-epigraph.pdf": (epigraph, None),
    "smoke-title-image.pdf": (title_image, None),
    "smoke-scan.pdf": (scan, None),
    "smoke-book.pdf": (book, None),
    "smoke-report.pdf": (report, None),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (build, title) in DOCUMENTS.items():
        write_pdf(OUT / name, build(), title)
        print(f"wrote {OUT / name} ({(OUT / name).stat().st_size} bytes)")


if __name__ == "__main__":
    main()

"""Broader format support: encodings, delimited text, text markup, subtitles,
OpenDocument, EPUB, e-mail, notebooks, SVG, RTF, and the refusal messages."""

import base64
import codecs
import io
import json
import mailbox
import shutil
import sys
import zipfile
from email.message import EmailMessage
from pathlib import Path

import pytest

from services import format_readers
from services.document_extractor import (
    DocumentExtractor,
    is_supported_filename,
    unsupported_reason,
)

FIXTURES = Path(__file__).parent / "fixtures"
SENTENCE = "Groundwater recharge in semi-arid basins depends on episodic flooding."


@pytest.fixture()
def extractor():
    return DocumentExtractor(enable_ocr=False)


def _text(result):
    return "\n".join(result.page_texts.values())


def _write(tmp_path, name, data):
    path = tmp_path / name
    if isinstance(data, str):
        data = data.encode("utf-8")
    path.write_bytes(data)
    return path


def _zip(tmp_path, name, members):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        for member, content in members.items():
            zf.writestr(member, content)
    return path


# ---------------------------------------------------------------------------
# Text decoding
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("encoding, bom", [
    ("utf-16-le", codecs.BOM_UTF16_LE),
    ("utf-16-be", codecs.BOM_UTF16_BE),
    ("utf-32-le", codecs.BOM_UTF32_LE),
    ("utf-8", codecs.BOM_UTF8),
])
def test_bom_encodings_decode_cleanly(extractor, tmp_path, encoding, bom):
    path = _write(tmp_path, "note.txt", bom + f"{SENTENCE} Café owners.".encode(encoding))
    text = _text(extractor.extract_text(path))
    assert text == f"{SENTENCE} Café owners."
    assert "\x00" not in text and "\ufeff" not in text


def test_utf16_without_bom_is_still_detected(extractor, tmp_path):
    for enc in ("utf-16-le", "utf-16-be"):
        path = _write(tmp_path, f"{enc}.txt", f"{SENTENCE} Café".encode(enc))
        text = _text(extractor.extract_text(path))
        assert text == f"{SENTENCE} Café"


def test_cp1252_text_keeps_accents_and_smart_quotes(extractor, tmp_path):
    path = _write(tmp_path, "menu.txt", "Café \u201cnoir\u201d \u2013 5\u20ac".encode("cp1252"))
    assert _text(extractor.extract_text(path)) == "Café \u201cnoir\u201d \u2013 5\u20ac"


def test_undecodable_bytes_never_raise_or_leave_nuls(extractor, tmp_path):
    path = _write(tmp_path, "odd.txt", b"abc \x81\x8d\x8f\x90\x9d def")
    text = _text(extractor.extract_text(path))
    assert "abc" in text and "def" in text and "\x00" not in text


def test_markdown_and_json_use_the_same_decoder(extractor, tmp_path):
    md = _write(tmp_path, "n.md", codecs.BOM_UTF16_LE + "# Titel\nCafé".encode("utf-16-le"))
    assert "Café" in _text(extractor.extract_text(md))
    js = _write(tmp_path, "d.json", codecs.BOM_UTF8 + json.dumps({"city": "Malmö"}).encode("utf-8"))
    assert "Malmö" in _text(extractor.extract_text(js))
    jl = _write(tmp_path, "d.jsonl", json.dumps({"city": "Malmö"}).encode("cp1252") + b"\n")
    text = _text(extractor.extract_text(jl))
    assert "\ufffd" not in text and ("Malmö" in text or "Malm\\u00f6" in text)


# ---------------------------------------------------------------------------
# CSV / TSV
# ---------------------------------------------------------------------------

def test_semicolon_cp1252_csv_parses_into_columns(extractor, tmp_path):
    path = _write(tmp_path, "semi.csv", "name;city;amount\nAna;Malmö;1,5\nBo;Köln;2,25\n".encode("cp1252"))
    sheet = extractor.extract_tabular_sheets(path)[0]
    assert sheet["columns"] == ["name", "city", "amount"]
    assert sheet["rows"][0]["city"] == "Malmö"
    assert sheet["rows"][1]["name"] == "Bo"
    page = extractor.extract_text(path).page_texts[1]
    assert page.splitlines()[0] == "name | city | amount"
    assert "Ana | Malmö | 1,5" in page


@pytest.mark.parametrize("delimiter", ["\t", "|", ";", ","])
def test_delimiter_is_detected(extractor, tmp_path, delimiter):
    rows = [["id", "site", "note"], ["1", "Boise", "alpha"], ["2", "Nampa", "beta"]]
    path = _write(tmp_path, "t.csv", "\n".join(delimiter.join(r) for r in rows) + "\n")
    assert extractor.extract_tabular_sheets(path)[0]["columns"] == ["id", "site", "note"]


def test_commas_inside_semicolon_cells_do_not_confuse_detection(extractor, tmp_path):
    path = _write(tmp_path, "d.csv", "a;b;c\n1,5;2,5;3,5\n4,5;5,5;6,5\n7,5;8,5;9,5\n")
    assert extractor.extract_tabular_sheets(path)[0]["columns"] == ["a", "b", "c"]


def test_tsv_is_supported_and_stays_queryable(extractor, tmp_path):
    path = _write(tmp_path, "d.tsv", "name\tcity\nAna\tBoise\nBo\tNampa\n")
    assert ".tsv" in extractor.SUPPORTED_EXTENSIONS and ".tsv" in extractor.TABULAR_EXTENSIONS
    sheet = extractor.extract_tabular_sheets(path)[0]
    assert sheet["columns"] == ["name", "city"]
    assert sheet["rows"][1] == {"name": "Bo", "city": "Nampa"}
    assert "Ana | Boise" in extractor.extract_text(path).page_texts[1]


def test_utf16_csv_reads_as_columns(extractor, tmp_path):
    path = _write(tmp_path, "u.csv", codecs.BOM_UTF16_LE + "name,city\nAna,Malmö\nBo,Köln\n".encode("utf-16-le"))
    sheet = extractor.extract_tabular_sheets(path)[0]
    assert sheet["columns"] == ["name", "city"]
    assert sheet["rows"][0]["city"] == "Malmö"


def test_preamble_rows_are_still_skipped(extractor, tmp_path):
    body = "Account Report\nAs Of,01/15/2026\n\nSymbol,Description,Quantity,Price\nAAPL,Apple,10,190.5\nMSFT,Microsoft,5,410.25\n"
    sheet = extractor.extract_tabular_sheets(_write(tmp_path, "p.csv", body))[0]
    assert sheet["columns"] == ["Symbol", "Description", "Quantity", "Price"]
    assert len(sheet["rows"]) == 2


# ---------------------------------------------------------------------------
# Plain-text-like formats
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "d.rst", "d.adoc", "d.asciidoc", "d.org", "d.tex", "d.bib", "d.log", "d.text",
])
def test_text_markup_formats_are_indexed_as_text(extractor, tmp_path, name):
    path = _write(tmp_path, name, f"Title\n=====\n\n{SENTENCE} Café.\n".encode("cp1252"))
    assert is_supported_filename(name)
    text = _text(extractor.extract_text(path))
    assert SENTENCE in text and "Café" in text


def test_long_logs_are_split_into_pages(extractor, tmp_path):
    path = _write(tmp_path, "big.log", "\n".join(f"line {i} ok" for i in range(1200)))
    pages = extractor.extract_text(path).page_texts
    assert len(pages) == 3
    assert "line 0 ok" in pages[1] and "line 1199 ok" in pages[3]


SRT = """1
00:00:01,000 --> 00:00:03,500
<i>Groundwater</i> recharge is slow.

2
00:00:04,000 --> 00:00:06,000
{\\an8}It depends on flooding.
Café owners agree.

3
00:00:07,000 --> 00:00:08,000
It depends on flooding.
"""

VTT = """WEBVTT - basin talk

NOTE this comment is not speech

intro
00:00:01.000 --> 00:00:03.500 align:start position:0%
<v Ana>Groundwater <c.yellow>recharge</c> is slow.

00:00:04.000 --> 00:00:06.000
It depends on flooding.

00:00:06.000 --> 00:00:07.000
It depends on flooding.
"""


@pytest.mark.parametrize("name, body", [("talk.srt", SRT), ("talk.vtt", VTT)])
def test_subtitles_lose_timestamps_and_cue_numbers(extractor, tmp_path, name, body):
    text = _text(extractor.extract_text(_write(tmp_path, name, body)))
    assert "Groundwater recharge is slow." in text
    assert "It depends on flooding." in text
    assert "-->" not in text and "00:00" not in text
    assert "WEBVTT" not in text and "NOTE" not in text and "<" not in text and "{" not in text
    if name == "talk.srt":
        assert "Café owners agree." in text
        assert not any(line.strip() in {"1", "2", "3"} for line in text.splitlines())
    else:
        assert text.count("It depends on flooding.") == 1  # the repeated roll-up cue is collapsed
        assert "intro" not in text  # the cue identifier is not speech


# ---------------------------------------------------------------------------
# HTML family, SVG, RTF
# ---------------------------------------------------------------------------

def test_xhtml_goes_through_the_html_extractor(extractor, tmp_path):
    xhtml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Basin Notes</title></head>'
        f"<body><h1>Recharge</h1><p>{SENTENCE}</p></body></html>"
    )
    path = _write(tmp_path, "page.xhtml", xhtml)
    text = _text(extractor.extract_text(path))
    assert SENTENCE in text and "Basin Notes" in text and "<?xml" not in text


SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">
  <title>Basin diagram</title><desc>Shows recharge zones</desc>
  <script>alert('x')</script>
  <g><text x="1" y="1">Aquifer <tspan>North</tspan></text><text>Well 7</text></g>
</svg>"""


def test_svg_text_title_and_desc_are_extracted(extractor, tmp_path):
    text = _text(extractor.extract_text(_write(tmp_path, "d.svg", SVG)))
    for expected in ("Basin diagram", "Shows recharge zones", "Aquifer North", "Well 7"):
        assert expected in text
    assert "alert" not in text


def test_svg_with_entity_declarations_is_refused(extractor, tmp_path):
    bomb = '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa">]><svg xmlns="http://www.w3.org/2000/svg"><text>&a;</text></svg>'
    with pytest.raises(ValueError, match="entities"):
        extractor.extract_text(_write(tmp_path, "bomb.svg", bomb))


def test_oversized_svg_is_refused(extractor, tmp_path, monkeypatch):
    monkeypatch.setattr(format_readers, "MAX_SVG_BYTES", 100)
    with pytest.raises(ValueError, match="not indexed"):
        extractor.extract_text(_write(tmp_path, "big.svg", SVG))


def test_rtf_text_survives_escapes_and_drops_control_groups(extractor, tmp_path):
    rtf = (
        r"{\rtf1\ansi\ansicpg1252{\fonttbl{\f0 Arial;}}{\colortbl;\red0\green0\blue0;}"
        r"{\*\generator Word}\pard Caf\'e9 owners {\b reported} 12\'25 fewer visits.\par "
        r"Unicode \u8364?uro and smart \ldblquote quotes\rdblquote .\par}"
    )
    text = _text(extractor.extract_text(_write(tmp_path, "l.rtf", rtf)))
    assert "Café owners reported 12% fewer visits." in text
    assert "\u20acuro" in text and "\u201cquotes\u201d" in text
    assert "Arial" not in text and "Word" not in text and "\\" not in text


# ---------------------------------------------------------------------------
# Spreadsheets
# ---------------------------------------------------------------------------

def _workbook(path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Pumps"
    ws.append(["tag", "site", "flow"])
    ws.append(["P-101", "Boise", 12.5])
    ws.append(["P-102", "Nampa", 8.25])
    wb.save(path)
    return path


def test_xlsm_reads_like_xlsx(extractor, tmp_path):
    src = _workbook(tmp_path / "w.xlsx")
    path = tmp_path / "macro.xlsm"
    shutil.copy(src, path)
    assert ".xlsm" in extractor.SUPPORTED_EXTENSIONS and ".xlsm" in extractor.TABULAR_EXTENSIONS
    text = _text(extractor.extract_text(path))
    assert "[Sheet: Pumps]" in text and "P-101 | Boise | 12.5" in text
    sheet = extractor.extract_tabular_sheets(path)[0]
    assert sheet["columns"] == ["tag", "site", "flow"]


def test_legacy_xls_is_read_when_xlrd_is_installed(extractor, tmp_path):
    pytest.importorskip("xlrd")
    path = tmp_path / "pumps.xls"
    shutil.copy(FIXTURES / "pumps.xls", path)
    text = _text(extractor.extract_text(path))
    assert "[Sheet: Pumps]" in text and "P-102 | Nampa | 8.25" in text
    assert extractor.extract_tabular_sheets(path)[0]["columns"] == ["tag", "site", "flow"]


def test_legacy_xls_without_xlrd_gives_an_actionable_error(extractor, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "xlrd", None)  # makes `import xlrd` fail
    path = tmp_path / "pumps.xls"
    shutil.copy(FIXTURES / "pumps.xls", path)
    with pytest.raises(ImportError, match="pip install xlrd"):
        extractor.extract_text(path)


# ---------------------------------------------------------------------------
# OpenDocument
# ---------------------------------------------------------------------------

ODF_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"'
)


def _odf(tmp_path, name, body_xml, mimetype):
    content = f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {ODF_NS}><office:body>{body_xml}</office:body></office:document-content>'
    return _zip(tmp_path, name, {"mimetype": mimetype, "content.xml": content})


def test_odt_paragraphs_headings_and_tables(extractor, tmp_path):
    body = (
        "<office:text>"
        '<text:h text:outline-level="1">Basin Report</text:h>'
        f'<text:p>{SENTENCE}<text:s text:c="2"/>Café<text:line-break/>owners <text:span>agree</text:span>.</text:p>'
        "<table:table><table:table-row>"
        "<table:table-cell><text:p>Site</text:p></table:table-cell><table:table-cell><text:p>Flow</text:p></table:table-cell>"
        "</table:table-row><table:table-row>"
        "<table:table-cell><text:p>Boise</text:p></table:table-cell><table:table-cell><text:p>12</text:p></table:table-cell>"
        "</table:table-row></table:table>"
        "</office:text>"
    )
    path = _odf(tmp_path, "r.odt", body, "application/vnd.oasis.opendocument.text")
    text = _text(extractor.extract_text(path))
    assert "Basin Report" in text and SENTENCE in text and "owners agree." in text
    assert "Site | Flow" in text and "Boise | 12" in text


def test_odp_has_one_page_per_slide(extractor, tmp_path):
    body = (
        "<office:presentation>"
        '<draw:page draw:name="one"><draw:frame><draw:text-box><text:p>Opening</text:p></draw:text-box></draw:frame></draw:page>'
        '<draw:page draw:name="two"></draw:page>'
        '<draw:page draw:name="three"><draw:frame><draw:text-box><text:p>Closing</text:p><text:p>Thanks</text:p></draw:text-box></draw:frame></draw:page>'
        "</office:presentation>"
    )
    pages = extractor.extract_text(_odf(tmp_path, "d.odp", body, "application/vnd.oasis.opendocument.presentation")).page_texts
    assert list(pages) == [1, 3]  # the blank slide is skipped, numbers stay true
    assert pages[1] == "Opening" and "Closing" in pages[3] and "Thanks" in pages[3]


def test_ods_has_one_page_per_sheet_and_ignores_repeated_blank_rows(extractor, tmp_path):
    def row(*cells):
        return "<table:table-row>" + "".join(
            f"<table:table-cell><text:p>{c}</text:p></table:table-cell>" if c else "<table:table-cell/>" for c in cells
        ) + "</table:table-row>"

    body = (
        "<office:spreadsheet>"
        f'<table:table table:name="Pumps">{row("tag", "flow")}{row("P-1", "12")}'
        '<table:table-row table:number-rows-repeated="1048000"><table:table-cell table:number-columns-repeated="1024"/></table:table-row>'
        "</table:table>"
        f'<table:table table:name="Sites">{row("site")}{row("Boise")}</table:table>'
        "</office:spreadsheet>"
    )
    pages = extractor.extract_text(_odf(tmp_path, "s.ods", body, "application/vnd.oasis.opendocument.spreadsheet")).page_texts
    assert list(pages) == [1, 2]
    assert pages[1] == "[Sheet: Pumps]\ntag | flow\nP-1 | 12"
    assert "[Sheet: Sites]" in pages[2] and "Boise" in pages[2]


def test_odf_that_is_not_a_zip_gives_a_clear_error(extractor, tmp_path):
    with pytest.raises(ValueError, match="not a valid OpenDocument"):
        extractor.extract_text(_write(tmp_path, "bad.odt", b"not a zip"))


# ---------------------------------------------------------------------------
# EPUB
# ---------------------------------------------------------------------------

def _epub(tmp_path, name="book.epub"):
    container = (
        '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>The Basin Book</dc:title></metadata>'
        '<manifest><item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c2" href="text/ch%202.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c3" href="blank.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="css" href="style.css" media-type="text/css"/>'
        '<item id="extra" href="unlisted.xhtml" media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="c2"/><itemref idref="c3"/><itemref idref="c1"/></spine></package>'
    )

    def chapter(title, body):
        return (f'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title></head>'
                f"<body>{body}</body></html>")

    return _zip(tmp_path, name, {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": container,
        "OEBPS/content.opf": opf,
        "OEBPS/ch1.xhtml": chapter("One", "<h1>First</h1><p>Alpha recharge.</p>"),
        "OEBPS/text/ch 2.xhtml": chapter("Two", "<h1>Second</h1><p>Beta flooding.</p>"),
        "OEBPS/blank.xhtml": chapter("", ""),
        "OEBPS/unlisted.xhtml": chapter("Hidden", "<p>Not in the spine.</p>"),
        "OEBPS/style.css": "p { color: red }",
    })


def test_epub_chapters_follow_spine_order(extractor, tmp_path):
    pages = extractor.extract_text(_epub(tmp_path)).page_texts
    assert list(pages) == [1, 2]  # the blank chapter is skipped
    assert "Beta flooding." in pages[1] and "The Basin Book" in pages[1]  # title on the first page
    assert "Alpha recharge." in pages[2]
    assert "Not in the spine" not in "".join(pages.values())
    assert "color: red" not in "".join(pages.values())


def test_epub_without_a_package_falls_back_to_its_html_files(extractor, tmp_path):
    path = _zip(tmp_path, "odd.epub", {"a.xhtml": "<html><body><p>Only chapter.</p></body></html>"})
    assert "Only chapter." in _text(extractor.extract_text(path))


# ---------------------------------------------------------------------------
# E-mail
# ---------------------------------------------------------------------------

def _message(subject, body, html=None, attachments=()):
    msg = EmailMessage()
    msg["From"] = "Ana <ana@example.com>"
    msg["To"] = "bo@example.com"
    msg["Date"] = "Tue, 03 Nov 2026 09:00:00 -0700"
    msg["Subject"] = subject
    if body is not None:
        msg.set_content(body)
    if html is not None:
        if body is None:
            msg.set_content(html, subtype="html")
        else:
            msg.add_alternative(html, subtype="html")
    for name in attachments:
        msg.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename=name)
    return msg


def test_eml_has_headers_body_and_attachment_names(extractor, tmp_path):
    msg = _message("Recharge numbers", f"{SENTENCE} Café.", attachments=("flows.pdf", "map.png"))
    path = _write(tmp_path, "m.eml", msg.as_bytes())
    text = _text(extractor.extract_text(path))
    assert "From: Ana <ana@example.com>" in text and "To: bo@example.com" in text
    assert "Date: Tue, 03 Nov 2026" in text and "Subject: Recharge numbers" in text
    assert f"{SENTENCE} Café." in text
    assert "Attachments: flows.pdf, map.png" in text
    assert "%PDF" not in text


def test_eml_html_only_body_becomes_text(extractor, tmp_path):
    msg = _message("HTML only", None, html="<html><body><h1>Update</h1><p>Wells are <b>flowing</b>.</p></body></html>")
    text = _text(extractor.extract_text(_write(tmp_path, "h.eml", msg.as_bytes())))
    assert "Update" in text and "Wells are flowing." in text and "<p>" not in text


def test_mbox_is_one_page_per_message(extractor, tmp_path):
    path = tmp_path / "box.mbox"
    box = mailbox.mbox(str(path))
    for subject, body in [("One", "first body"), ("Two", "second body"), ("Three", "third body")]:
        box.add(_message(subject, body))
    box.flush()
    box.close()
    pages = extractor.extract_text(path).page_texts
    assert list(pages) == [1, 2, 3]
    assert "Subject: Two" in pages[2] and "second body" in pages[2]


def test_mbox_message_count_is_capped(extractor, tmp_path, monkeypatch):
    monkeypatch.setattr(format_readers, "MAX_MBOX_MESSAGES", 3)
    path = tmp_path / "many.mbox"
    box = mailbox.mbox(str(path))
    for i in range(6):
        box.add(_message(f"m{i}", f"body {i}"))
    box.flush()
    box.close()
    assert list(extractor.extract_text(path).page_texts) == [1, 2, 3]


def test_undeclared_8bit_mail_keeps_its_accents(extractor, tmp_path):
    raw = "From: a@example.com\nSubject: Café\n\nCafé owners.\n".encode("utf-8")
    assert "Café owners." in _text(extractor.extract_text(_write(tmp_path, "raw.eml", raw)))


# ---------------------------------------------------------------------------
# Notebooks
# ---------------------------------------------------------------------------

def _notebook(cells):
    return json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": cells})


def test_ipynb_markdown_code_and_text_outputs(extractor, tmp_path):
    cells = [
        {"cell_type": "markdown", "metadata": {}, "source": ["# Recharge model\n", "Café notes"]},
        {"cell_type": "code", "metadata": {}, "execution_count": 1, "source": ["print('hello basin')"],
         "outputs": [
             {"output_type": "stream", "name": "stdout", "text": ["hello basin\n"]},
             {"output_type": "execute_result", "execution_count": 1, "metadata": {}, "data": {"text/plain": "42", "image/png": "iVBORw0KGgo="}},
             {"output_type": "display_data", "metadata": {}, "data": {"image/png": "iVBORw0KGgo="}},
             {"output_type": "error", "ename": "ValueError", "evalue": "bad flow", "traceback": ["\x1b[31mTraceback"]},
         ]},
        {"cell_type": "code", "metadata": {}, "source": "", "outputs": []},
    ]
    text = _text(extractor.extract_text(_write(tmp_path, "nb.ipynb", _notebook(cells))))
    assert "# Recharge model" in text and "Café notes" in text
    assert "[Code]\nprint('hello basin')" in text
    assert "hello basin\n42" in text.replace("[Output]\n", "")
    assert "ValueError: bad flow" in text
    assert "iVBOR" not in text and "Traceback" not in text


def test_ipynb_is_grouped_into_pages_of_twenty_cells(extractor, tmp_path):
    cells = [{"cell_type": "markdown", "metadata": {}, "source": f"cell {i}"} for i in range(45)]
    pages = extractor.extract_text(_write(tmp_path, "big.ipynb", _notebook(cells))).page_texts
    assert list(pages) == [1, 2, 3]
    assert "cell 0" in pages[1] and "cell 20" in pages[2] and "cell 44" in pages[3]


def test_invalid_notebook_has_a_clear_error(extractor, tmp_path):
    with pytest.raises(ValueError, match="not valid JSON"):
        extractor.extract_text(_write(tmp_path, "bad.ipynb", "{nope"))


# ---------------------------------------------------------------------------
# The gate, the refusal messages, and the allowlists
# ---------------------------------------------------------------------------

NEW_EXTENSIONS = [
    ".tsv", ".xlsm", ".rtf", ".odt", ".ods", ".odp", ".epub", ".eml", ".mbox", ".ipynb",
    ".xhtml", ".xht", ".svg", ".rst", ".adoc", ".asciidoc", ".org", ".tex", ".bib", ".log",
    ".text", ".srt", ".vtt",
]


@pytest.mark.parametrize("ext", NEW_EXTENSIONS)
def test_new_extensions_pass_the_gate_and_every_allowlist(ext):
    from services import file_picker, file_kinds, link_fetcher
    assert is_supported_filename(f"file{ext}")
    assert ext in DocumentExtractor.SUPPORTED_EXTENSIONS
    assert file_kinds.kind_for_filename(f"file{ext}") is not None
    if ext not in {".xht", ".mbox"}:  # picker globs list the common spellings
        assert f"*{ext}" in " ".join(glob for _, glob in file_picker.SUPPORTED_FILETYPES)


def test_zip_stays_rejected_with_a_human_message(extractor, tmp_path):
    assert not is_supported_filename("bundle.zip")
    assert "Clio can't read .zip archives" in unsupported_reason("bundle.zip")
    path = _write(tmp_path, "bundle.zip", b"PK\x05\x06" + b"\x00" * 18)
    with pytest.raises(ValueError, match=r"Unsupported file type: \.zip\. Clio can't read \.zip archives"):
        extractor.extract_text(path)


def test_unsupported_message_lists_supported_types_for_unknown_extensions():
    message = unsupported_reason("movie.mkv")
    assert message.startswith("Clio can't read .mkv files. Supported:")
    for needle in ("PDF", ".docx", ".odt", "EPUB", ".eml", "TSV", "source code"):
        assert needle in message


@pytest.mark.parametrize("name, hint", [
    ("old.doc", ".docx"), ("old.ppt", ".pptx"), ("mail.msg", ".eml"), ("book.xlsb", ".xlsx"),
])
def test_legacy_office_formats_say_how_to_convert(name, hint):
    assert not is_supported_filename(name)
    reason = unsupported_reason(name)
    assert hint in reason and "upload" in reason


def test_api_upload_message_is_the_human_one():
    from api.documents import _unsupported_message
    assert "Clio can't read .zip archives" in _unsupported_message("bundle.zip")
    assert ".docx" in _unsupported_message("old.doc")

"""Readers for text-like and zip/XML-based formats that need no third-party parser.

Everything here is standard library: text decoding with encoding detection,
subtitles, RTF, SVG, OpenDocument (.odt/.ods/.odp), EPUB, e-mail (.eml/.mbox)
and Jupyter notebooks. Each ``read_*`` / ``*_pages`` function returns the shape
the extractor wants, ``{page_or_section_number: text}`` with empty pages left
out, and raises ``ValueError`` with a plain-language message when the file is
not what its extension says.

XML is parsed with ``xml.etree`` (expat), which never fetches external
entities; documents that declare entities at all are refused, and zip members
and SVG files are size-capped, so a crafted file cannot exhaust memory.
"""

from __future__ import annotations

import codecs
import html
import json
import logging
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote

logger = logging.getLogger(__name__)

Pages = Dict[int, str]
# html bytes/str -> {section: text}; supplied by DocumentExtractor so EPUB and
# e-mail share the one HTML-to-text implementation.
HtmlToSections = Callable[[object, str], Pages]

MAX_XML_BYTES = 100 * 1024 * 1024      # one XML part inside a zip
MAX_SVG_BYTES = 10 * 1024 * 1024       # a standalone SVG file
MAX_MBOX_MESSAGES = 5000
NOTEBOOK_CELLS_PER_PAGE = 20
SUBTITLE_CUES_PER_PAGE = 100
LOG_LINES_PER_PAGE = 500


# ---------------------------------------------------------------------------
# Text decoding
# ---------------------------------------------------------------------------

def _guess_bomless_utf16(head: bytes) -> Optional[str]:
    """UTF-16 without a BOM: mostly-Latin text has a NUL in every other byte."""
    if len(head) < 4:
        return None
    even, odd = head[0::2], head[1::2]
    zeros_even = even.count(0) / len(even)
    zeros_odd = odd.count(0) / len(odd)
    if zeros_odd > 0.3 and zeros_even < 0.05:
        return "utf-16-le"
    if zeros_even > 0.3 and zeros_odd < 0.05:
        return "utf-16-be"
    return None


def _decodes_strictly(path: Path, encoding: str, chunk: int = 1 << 20) -> bool:
    decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
    try:
        with open(path, "rb") as f:
            while True:
                block = f.read(chunk)
                if not block:
                    decoder.decode(b"", final=True)
                    return True
                decoder.decode(block)
    except UnicodeDecodeError:
        return False


def detect_encoding(path: Path) -> str:
    """The codec a text file should be read with.

    A BOM wins (UTF-8, UTF-16 LE/BE, UTF-32); then BOM-less UTF-16 is spotted
    by its NUL pattern; then strict UTF-8; then Windows-1252 (what "ANSI" text
    from Excel and Notepad really is); and Latin-1, which accepts any byte.
    """
    with open(path, "rb") as f:
        head = f.read(65536)
    if head.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return "utf-32"
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    guess = _guess_bomless_utf16(head)
    if guess:
        return guess
    for encoding in ("utf-8", "cp1252"):
        if _decodes_strictly(path, encoding):
            return encoding
    return "latin-1"


def decode_text_bytes(data: bytes) -> str:
    """Decode in-memory bytes by the same rules as :func:`detect_encoding`."""
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = "utf-32"
    elif data.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    else:
        encoding = _guess_bomless_utf16(data[:65536])
        if not encoding:
            for candidate in ("utf-8", "cp1252"):
                try:
                    return _clean(data.decode(candidate))
                except UnicodeDecodeError:
                    continue
            encoding = "latin-1"
    return _clean(data.decode(encoding, errors="replace"))


def _clean(text: str) -> str:
    # A NUL is never text; a stray BOM character only breaks matching.
    if "\x00" in text:
        text = text.replace("\x00", "")
    return text.lstrip("\ufeff")


def read_text_file(path: Path) -> str:
    """Read a text file as a string, whatever its encoding. Never raises on
    bad bytes and never returns NULs or a leading BOM."""
    return decode_text_bytes(Path(path).read_bytes())


def paginate_lines(text: str, lines_per_page: int) -> Pages:
    lines = text.splitlines()
    pages: Pages = {}
    for i in range(0, len(lines), lines_per_page):
        chunk = "\n".join(lines[i:i + lines_per_page]).strip()
        if chunk:
            pages[len(pages) + 1] = chunk
    return pages or {1: ""}


def text_pages(path: Path, lines_per_page: Optional[int] = None) -> Pages:
    """Plain-text-like files (.rst, .tex, .log, ...): one page, or one per N lines."""
    text = read_text_file(path)
    if lines_per_page:
        return paginate_lines(text, lines_per_page)
    return {1: text.strip()}


# ---------------------------------------------------------------------------
# Subtitles (.srt / .vtt)
# ---------------------------------------------------------------------------

_CUE_TIME = re.compile(
    r"^\s*(?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}\s*-->\s*(?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}.*$"
)
_CUE_MARKUP = re.compile(r"<[^>]{1,80}>|\{\\[^}]*\}")


def subtitle_pages(path: Path) -> Pages:
    """Spoken text of an SRT/WebVTT file; cue numbers and timestamps are dropped."""
    raw = read_text_file(path).replace("\r\n", "\n").replace("\r", "\n")
    cues: List[str] = []
    for block in re.split(r"\n\s*\n", raw):
        lines = block.strip("\n").split("\n")
        if not lines or not lines[0].strip():
            continue
        head = lines[0].strip()
        if head.startswith("WEBVTT"):
            # The header block may run straight into the first cue.
            lines = lines[1:]
            while lines and not _CUE_TIME.match(lines[0]) and lines[0].strip():
                lines = lines[1:]
            if not lines:
                continue
            head = lines[0].strip()
        if head.split(None, 1)[0] in ("NOTE", "STYLE", "REGION"):
            continue
        stamp = next((i for i, ln in enumerate(lines) if _CUE_TIME.match(ln)), None)
        if stamp is None:
            continue  # a block with no timing is not a cue
        spoken = []
        for ln in lines[stamp + 1:]:
            ln = html.unescape(_CUE_MARKUP.sub("", ln))
            ln = re.sub(r"\s+", " ", ln).strip()
            if ln:
                spoken.append(ln)
        cue = "\n".join(spoken)
        # Roll-up captions repeat the previous line; keep one copy.
        if cue and (not cues or cue != cues[-1]):
            cues.append(cue)
    pages: Pages = {}
    for i in range(0, len(cues), SUBTITLE_CUES_PER_PAGE):
        pages[len(pages) + 1] = "\n".join(cues[i:i + SUBTITLE_CUES_PER_PAGE])
    return pages or {1: ""}


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def parse_xml(data: bytes, what: str = "XML") -> ET.Element:
    if re.search(rb"<!ENTITY", data[:65536]):
        raise ValueError(f"This {what} declares XML entities, which Clio does not expand.")
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:
        raise ValueError(f"This {what} is not well-formed XML: {e}") from e


def _read_member(zf: zipfile.ZipFile, name: str, limit: int = MAX_XML_BYTES) -> bytes:
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise ValueError(f"The archive has no {name}; it is not a valid file of this type.") from None
    if info.file_size > limit:
        raise ValueError(f"{name} is larger than {limit // (1024 * 1024)} MB uncompressed; refusing to read it.")
    return zf.read(info)


def _open_zip(path: Path, what: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ValueError(f"This is not a valid {what} file (it is not a zip container).") from None


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------

def svg_pages(path: Path) -> Pages:
    """Text an SVG shows or declares: <title>, <desc> and <text>/<tspan>."""
    path = Path(path)
    if path.stat().st_size > MAX_SVG_BYTES:
        raise ValueError(f"SVG files over {MAX_SVG_BYTES // (1024 * 1024)} MB are not indexed.")
    root = parse_xml(path.read_bytes(), "SVG")
    seen: List[str] = []

    def add(parts) -> None:
        line = re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()
        if line and line not in seen:
            seen.append(line)

    for el in root.iter():
        name = _local(el.tag)
        if name in ("title", "desc"):
            add(el.itertext())
        elif name == "text":
            add(el.itertext())
    return {1: "\n".join(seen)}


# ---------------------------------------------------------------------------
# RTF
# ---------------------------------------------------------------------------

_RTF_TOKEN = re.compile(
    r"\\([a-zA-Z]+)(-?\d+)? ?"      # control word with optional parameter
    r"|\\'([0-9a-fA-F]{2})"          # hex-escaped byte
    r"|\\([\\{}~\-_*])"              # control symbol
    r"|\\[\r\n]"                     # backslash-newline = paragraph
    r"|([{}])"                       # group
    r"|([^\\{}]+)"                   # plain text
)
_RTF_SKIP_DESTINATIONS = frozenset({
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "themedata", "datastore",
    "latentstyles", "listtable", "listoverridetable", "revtbl", "rsidtbl", "generator",
    "xmlnstbl", "filetbl", "private", "fldinst", "header", "footer", "headerl", "headerr",
    "headerf", "footerl", "footerr", "footerf", "bkmkstart", "bkmkend", "operator",
})
_RTF_SYMBOLS = {
    "par": "\n", "line": "\n", "sect": "\n", "page": "\n", "row": "\n", "tab": "\t", "cell": " | ",
    "lquote": "\u2018", "rquote": "\u2019", "ldblquote": "\u201c", "rdblquote": "\u201d",
    "endash": "\u2013", "emdash": "\u2014", "bullet": "\u2022", "emspace": " ", "enspace": " ",
}


def rtf_to_text(source: str) -> str:
    stack: List[List] = []          # [skipping, unicode_skip]
    skipping, uc_skip = False, 1
    pending_skip = 0
    codepage = "cp1252"
    out: List[str] = []
    for m in _RTF_TOKEN.finditer(source):
        word, param, hexbyte, symbol, group, text = m.groups()
        if group == "{":
            stack.append([skipping, uc_skip])
        elif group == "}":
            if stack:
                skipping, uc_skip = stack.pop()
        elif word is not None:
            if word in _RTF_SKIP_DESTINATIONS:
                skipping = True
            elif word == "ansicpg" and param:
                codepage = f"cp{param}"
            elif word == "uc" and param:
                uc_skip = int(param)
            elif word == "u" and param is not None:
                if not skipping:
                    code = int(param)
                    out.append(chr(code + 65536 if code < 0 else code))
                pending_skip = uc_skip
            elif word in _RTF_SYMBOLS and not skipping:
                out.append(_RTF_SYMBOLS[word])
        elif hexbyte is not None:
            if pending_skip:
                pending_skip -= 1
            elif not skipping:
                try:
                    out.append(bytes([int(hexbyte, 16)]).decode(codepage))
                except (UnicodeDecodeError, LookupError):
                    out.append(bytes([int(hexbyte, 16)]).decode("latin-1"))
        elif symbol is not None:
            if symbol == "*":
                skipping = True        # an optional destination we do not know
            elif not skipping:
                out.append({"~": "\u00a0", "-": "", "_": "-"}.get(symbol, symbol))
        elif text is not None:
            text = text.replace("\r", "").replace("\n", "")
            if pending_skip:
                drop = min(pending_skip, len(text))
                text, pending_skip = text[drop:], pending_skip - drop
            if text and not skipping:
                out.append(text)
        else:                           # backslash-newline
            if not skipping:
                out.append("\n")
    result = "".join(out)
    result = re.sub(r"[ \t]+\n", "\n", result)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


def rtf_pages(path: Path) -> Pages:
    data = Path(path).read_bytes()
    if not data.lstrip().startswith(b"{\\rtf"):
        # Misnamed plain text: index it as text rather than dropping it.
        return {1: decode_text_bytes(data).strip()}
    text = rtf_to_text(data.decode("latin-1"))
    return paginate_lines(text, 200) if text else {1: ""}


# ---------------------------------------------------------------------------
# OpenDocument (.odt / .ods / .odp)
# ---------------------------------------------------------------------------

_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"


def _odf_inline(el: ET.Element) -> str:
    parts = [el.text or ""]
    for child in el:
        name = _local(child.tag)
        if name == "s":
            parts.append(" " * int(child.get(f"{{{_TEXT}}}c", "1") or 1))
        elif name == "tab":
            parts.append("\t")
        elif name == "line-break":
            parts.append("\n")
        elif name == "note-citation":
            pass
        else:
            parts.append(_odf_inline(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _odf_cell_text(cell: ET.Element) -> str:
    paras = [_odf_inline(p).strip() for p in cell.iter() if _local(p.tag) in ("p", "h")]
    text = " ".join(p for p in paras if p)
    if not text:
        text = cell.get(f"{{{_OFFICE}}}string-value") or cell.get(f"{{{_OFFICE}}}value") or ""
    return text


def _odf_table_rows(table: ET.Element, max_repeat_rows: int = 100) -> List[str]:
    rows: List[str] = []
    for row in table.iter(f"{{{_TABLE}}}table-row"):
        cells: List[str] = []
        for cell in row:
            if _local(cell.tag) not in ("table-cell", "covered-table-cell"):
                continue
            text = _odf_cell_text(cell)
            repeat = min(int(cell.get(f"{{{_TABLE}}}number-columns-repeated", "1") or 1), 256)
            cells.extend([text] * repeat)
        while cells and not cells[-1]:
            cells.pop()
        if not cells:
            continue
        line = " | ".join(cells)
        repeat = min(int(row.get(f"{{{_TABLE}}}number-rows-repeated", "1") or 1), max_repeat_rows)
        rows.extend([line] * repeat)
    return rows


def _odf_blocks(node: ET.Element) -> List[str]:
    """Paragraphs, headings and tables below `node`, in document order."""
    blocks: List[str] = []
    for child in node:
        name = _local(child.tag)
        if name in ("p", "h"):
            text = _odf_inline(child).strip()
            if text:
                blocks.append(text)
        elif name == "table" and child.tag == f"{{{_TABLE}}}table":
            rows = _odf_table_rows(child)
            if rows:
                blocks.append("\n".join(rows))
        elif name in ("tracked-changes", "sequence-decls", "variable-decls", "user-field-decls"):
            continue
        else:
            blocks.extend(_odf_blocks(child))
    return blocks


def _odf_body(path: Path, kind: str) -> ET.Element:
    with _open_zip(path, f"OpenDocument .{kind}") as zf:
        root = parse_xml(_read_member(zf, "content.xml"), "OpenDocument file")
    body = root.find(f"{{{_OFFICE}}}body")
    if body is None:
        raise ValueError("This OpenDocument file has no document body.")
    return body


def odt_pages(path: Path, blocks_per_page: int = 10) -> Pages:
    body = _odf_body(path, "odt")
    blocks = _odf_blocks(body)
    pages: Pages = {}
    for i in range(0, len(blocks), blocks_per_page):
        pages[len(pages) + 1] = "\n\n".join(blocks[i:i + blocks_per_page]).strip()
    return pages or {1: ""}


def odp_pages(path: Path) -> Pages:
    """One page per slide (slide numbers stay true even when a slide is blank)."""
    body = _odf_body(path, "odp")
    pages: Pages = {}
    slide = 0
    for page in body.iter(f"{{{_DRAW}}}page"):
        slide += 1
        text = "\n\n".join(_odf_blocks(page)).strip()
        if text:
            pages[slide] = text
    return pages or {1: ""}


def ods_pages(path: Path) -> Pages:
    """One page per sheet, rows as pipe-separated lines (same shape as XLSX)."""
    body = _odf_body(path, "ods")
    pages: Pages = {}
    for number, table in enumerate(body.iter(f"{{{_TABLE}}}table"), start=1):
        rows = _odf_table_rows(table)
        if rows:
            name = table.get(f"{{{_TABLE}}}name") or f"Sheet{number}"
            pages[number] = "\n".join([f"[Sheet: {name}]"] + rows)
    return pages or {1: ""}


# ---------------------------------------------------------------------------
# EPUB
# ---------------------------------------------------------------------------

_HTML_MEDIA = ("application/xhtml+xml", "text/html")


def epub_pages(path: Path, html_to_sections: HtmlToSections) -> Pages:
    """One page per spine chapter, in reading order; empty chapters are skipped."""
    with _open_zip(path, "EPUB") as zf:
        title = ""
        chapters: List[str] = []
        opf_dir = ""
        try:
            container = parse_xml(_read_member(zf, "META-INF/container.xml"), "EPUB")
            rootfile = next(
                (el.get("full-path") for el in container.iter() if _local(el.tag) == "rootfile"), None)
            if not rootfile:
                raise ValueError("EPUB container.xml names no package file.")
            opf = parse_xml(_read_member(zf, rootfile), "EPUB package")
            opf_dir = posixpath.dirname(rootfile)
            manifest: Dict[str, Tuple[str, str]] = {}
            for el in opf.iter():
                name = _local(el.tag)
                if name == "item" and el.get("id") and el.get("href"):
                    manifest[el.get("id")] = (el.get("href"), el.get("media-type", ""))
                elif name == "title" and not title and (el.text or "").strip():
                    title = el.text.strip()
            for el in opf.iter():
                if _local(el.tag) == "itemref" and el.get("idref") in manifest:
                    href, media = manifest[el.get("idref")]
                    if media in _HTML_MEDIA or href.lower().endswith((".xhtml", ".html", ".htm")):
                        chapters.append(posixpath.normpath(posixpath.join(opf_dir, unquote(href.split("#")[0]))))
        except ValueError:
            if not zf.namelist():
                raise
            logger.info("EPUB %s has no usable package; reading its HTML files in name order", Path(path).name)
        if not chapters:
            chapters = sorted(n for n in zf.namelist() if n.lower().endswith((".xhtml", ".html", ".htm")))
        pages: Pages = {}
        names = set(zf.namelist())
        for chapter in chapters:
            if chapter not in names:
                continue
            try:
                sections = html_to_sections(_read_member(zf, chapter, 20 * 1024 * 1024), chapter)
            except ValueError as e:
                logger.warning("EPUB %s: skipping %s (%s)", Path(path).name, chapter, e)
                continue
            text = "\n\n".join(t for t in sections.values() if t and t.strip()).strip()
            if text:
                pages[len(pages) + 1] = text
    if pages and title and title.lower() not in pages[1][:300].lower():
        pages[1] = f"{title}\n\n{pages[1]}"
    return pages or {1: ""}


# ---------------------------------------------------------------------------
# E-mail (.eml / .mbox)
# ---------------------------------------------------------------------------

def _part_text(part) -> str:
    """Decoded text of a message part. A declared charset is honoured; mail that
    declares none (or only us-ascii) but carries 8-bit text goes through the
    same detection as text files instead of turning into replacement characters."""
    payload = part.get_payload(decode=True) or b""
    charset = (part.get_content_charset() or "").lower()
    if charset and charset not in ("us-ascii", "ascii"):
        try:
            return _clean(payload.decode(charset))
        except (LookupError, UnicodeDecodeError):
            pass
    return decode_text_bytes(payload)


def _message_text(msg, html_to_sections: HtmlToSections) -> str:
    header_lines = []
    for label in ("From", "To", "Cc", "Date", "Subject"):
        value = msg.get(label)
        if value:
            header_lines.append(f"{label}: {' '.join(str(value).split())}")

    body = ""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is not None:
        content = _part_text(part)
        if part.get_content_type() == "text/html":
            sections = html_to_sections(content, "email body")
            content = "\n\n".join(t for t in sections.values() if t)
        body = _clean(content).strip()

    names = []
    for att in msg.iter_attachments():
        name = att.get_filename()
        if name:
            names.append(str(name))
    sections = ["\n".join(header_lines)]
    if body:
        sections.append(body)
    if names:
        sections.append("Attachments: " + ", ".join(names))
    return "\n\n".join(s for s in sections if s).strip()


def eml_pages(path: Path, html_to_sections: HtmlToSections) -> Pages:
    from email import policy
    from email.parser import BytesParser

    msg = BytesParser(policy=policy.default).parsebytes(Path(path).read_bytes())
    text = _message_text(msg, html_to_sections)
    return {1: text}


def mbox_pages(path: Path, html_to_sections: HtmlToSections) -> Pages:
    """One page per message, capped at MAX_MBOX_MESSAGES."""
    import email
    import mailbox
    from email import policy

    box = mailbox.mbox(str(path), factory=lambda f: email.message_from_binary_file(f, policy=policy.default),
                       create=False)
    pages: Pages = {}
    truncated = False
    try:
        for number, key in enumerate(box.iterkeys(), start=1):
            if number > MAX_MBOX_MESSAGES:
                truncated = True
                break
            try:
                text = _message_text(box[key], html_to_sections)
            except Exception as e:  # noqa: BLE001 - one broken message must not lose the mailbox
                logger.warning("mbox %s: message %d unreadable (%s)", Path(path).name, number, e)
                continue
            if text:
                pages[number] = text
    finally:
        box.close()
    if truncated:
        logger.warning("mbox %s has more than %d messages; indexed the first %d",
                       Path(path).name, MAX_MBOX_MESSAGES, MAX_MBOX_MESSAGES)
    return pages or {1: ""}


# ---------------------------------------------------------------------------
# Jupyter notebooks
# ---------------------------------------------------------------------------

def _cell_source(cell: dict) -> str:
    src = cell.get("source", "")
    return ("".join(src) if isinstance(src, list) else str(src or "")).strip()


def _output_text(output: dict, limit: int = 2000) -> str:
    kind = output.get("output_type")
    if kind == "stream":
        text = output.get("text", "")
    elif kind in ("execute_result", "display_data"):
        text = (output.get("data") or {}).get("text/plain", "")
    elif kind == "error":
        text = f"{output.get('ename', 'Error')}: {output.get('evalue', '')}"
    else:
        text = output.get("text", "")
    text = ("".join(text) if isinstance(text, list) else str(text or "")).strip()
    return text[:limit] + (" ..." if len(text) > limit else "")


def ipynb_pages(path: Path) -> Pages:
    try:
        nb = json.loads(read_text_file(path))
    except json.JSONDecodeError as e:
        raise ValueError(f"This notebook is not valid JSON: {e}") from e
    if not isinstance(nb, dict):
        raise ValueError("This notebook is not a Jupyter notebook (expected a JSON object).")
    cells = nb.get("cells")
    if cells is None:  # nbformat 3
        sheets = nb.get("worksheets") or [{}]
        cells = sheets[0].get("cells", [])
    blocks: List[str] = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        kind = cell.get("cell_type")
        if kind == "code":
            source = _cell_source({"source": cell.get("source", cell.get("input", ""))})
            parts = [f"[Code]\n{source}"] if source else []
            outputs = [t for t in (_output_text(o) for o in cell.get("outputs", []) if isinstance(o, dict)) if t]
            if outputs:
                parts.append("[Output]\n" + "\n".join(outputs))
            text = "\n".join(parts)
        else:
            text = _cell_source(cell)
        if text:
            blocks.append(text)
    pages: Pages = {}
    for i in range(0, len(blocks), NOTEBOOK_CELLS_PER_PAGE):
        pages[len(pages) + 1] = "\n\n".join(blocks[i:i + NOTEBOOK_CELLS_PER_PAGE])
    return pages or {1: ""}

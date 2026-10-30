"""Text someone typed or pasted straight into a collection.

Not every source is a file. A paragraph from an email, a passage copied out
of a web page that will not fetch, notes from a call: the Sources panel has
an editor for these, and what it sends arrives here as HTML (the editor's
own markup, or formatting carried along by a rich paste) or as plain text.

This module turns that into a document the indexing pipeline already
understands: a small, clean HTML file with the title as its <title>, saved
into the collection's documents directory and indexed like any uploaded
.html. The HTML extractor sections it by heading, so a long note cites by
section the way a Markdown file does.

Cleaning is an allowlist. Formatting that means something to a reader
(headings, paragraphs, lists, emphasis, quotes, code, tables, links) is
kept; everything else is unwrapped so its text survives, and every
attribute but a safe link href is dropped. Pasted markup from a word
processor or a web page can carry scripts, event handlers, tracking images
and megabytes of inline style, none of which belongs in a source.
"""

import html as html_lib
import re
from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup, Comment, NavigableString

# Write-time caps. A note is written by a person; a million characters is a
# book, and anything bigger is a file that should be uploaded as one.
MAX_TITLE_CHARS = 200
MAX_TEXT_CHARS = 1_000_000
DEFAULT_TITLE = "Pasted text"
SOURCE_TYPE = "text"

ALLOWED_TAGS = frozenset({
    "p", "br", "hr", "div",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "strong", "b", "em", "i", "u", "s", "sub", "sup",
    "a", "blockquote", "pre", "code",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
})
# Tags whose contents are not text anyone meant to keep.
DROPPED_TAGS = frozenset({
    "script", "style", "noscript", "template", "iframe", "object", "embed",
    "svg", "canvas", "video", "audio", "img", "picture", "source", "head",
    "meta", "link", "title", "form", "input", "button", "select", "textarea",
})
_SAFE_HREF = re.compile(r"^(https?://|mailto:)", re.IGNORECASE)
_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class PastedTextError(ValueError):
    """The note cannot become a document: empty, or over a cap."""


@dataclass
class PastedDocument:
    """What the upload service saves and indexes."""

    title: str
    filename: str
    body: bytes
    chars: int  # characters of readable text, for the response and the audit record


def clean_html(markup: str) -> str:
    """Reduce arbitrary markup to the allowlisted tags, with no attributes
    but a safe link href. Unknown tags are unwrapped, so their text stays."""
    soup = BeautifulSoup(markup or "", "html.parser")
    for tag in soup.find_all(DROPPED_TAGS):
        tag.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    # Walk a snapshot: unwrap() changes the tree under the iterator otherwise.
    for tag in list(soup.find_all(True)):
        if tag.name not in ALLOWED_TAGS:
            tag.unwrap()
            continue
        href = tag.get("href") if tag.name == "a" else None
        tag.attrs = {}
        if href and _SAFE_HREF.match(href.strip()):
            tag["href"] = href.strip()
    body = soup.body or soup
    return "".join(str(child) for child in body.children).strip()


def text_to_html(text: str) -> str:
    """Plain text as paragraphs: blank lines separate them, single newlines
    become line breaks, so what was pasted reads the way it was written."""
    blocks = re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").replace("\r", "\n").strip())
    paragraphs = []
    for block in blocks:
        lines = [html_lib.escape(line.rstrip()) for line in block.split("\n") if line.strip()]
        if lines:
            paragraphs.append("<p>" + "<br>".join(lines) + "</p>")
    return "\n".join(paragraphs)


def readable_text(markup: str) -> str:
    """The text a reader would see in the cleaned markup, whitespace folded."""
    soup = BeautifulSoup(markup or "", "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


def _first_block(markup: str) -> str:
    """The text of the first heading or paragraph in the cleaned markup."""
    soup = BeautifulSoup(markup or "", "html.parser")
    for child in soup.children:
        text = readable_text(str(child))
        if text:
            return text
    return ""


def safe_filename(title: str, extension: str = ".html") -> str:
    """A filesystem-safe name from the title; the caller de-duplicates."""
    stem = _UNSAFE_NAME.sub(" ", title or "")
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    if len(stem) > 80:
        cut = stem[:80]
        if " " in cut[40:]:
            cut = cut[: cut.rfind(" ")]
        stem = cut.rstrip(" .,;:-")
    return f"{stem or DEFAULT_TITLE}{extension}"


def build_document(title: Optional[str], html: Optional[str] = None,
                   text: Optional[str] = None) -> PastedDocument:
    """Validate and assemble the file to index.

    ``html`` is the editor's markup and wins when both are given; ``text``
    is the plain fallback (a textarea, the CLI, an MCP client). Raises
    PastedTextError when nothing readable is left or a cap is exceeded.
    """
    title = re.sub(r"\s+", " ", title or "").strip()
    if len(title) > MAX_TITLE_CHARS:
        raise PastedTextError(f"The title is longer than {MAX_TITLE_CHARS} characters")

    raw = html if (html or "").strip() else text or ""
    if len(raw) > MAX_TEXT_CHARS:
        raise PastedTextError(
            f"The text is longer than {MAX_TEXT_CHARS:,} characters; upload it as a file instead"
        )

    content = clean_html(html) if (html or "").strip() else text_to_html(text or "")
    readable = readable_text(content)
    if not readable:
        raise PastedTextError("There is no text to add")

    if not title:
        # The first line of what was written names it, as a note app would.
        first = _first_block(content) or readable
        title = first[:80].rsplit(" ", 1)[0] if len(first) > 80 else first
        title = title.strip(" .,;:-") or DEFAULT_TITLE

    document = (
        "<!DOCTYPE html>\n<html>\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>{html_lib.escape(title)}</title>\n</head>\n<body>\n{content}\n</body>\n</html>\n"
    )
    return PastedDocument(
        title=title,
        filename=safe_filename(title),
        body=document.encode("utf-8"),
        chars=len(readable),
    )

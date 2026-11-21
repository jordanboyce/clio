"""H3: a report as slides, in the Markdown Marp and Reveal.js read.

Made for the briefing (sections of short points), it works for any report:
a title slide, then a slide for each section (``##``), a long section split
over several, and a last slide listing the sources its citations name. A
citation [Source N] stays as [N]. ``marp`` starts with Marp's front matter;
``reveal`` leaves it out (Reveal.js's Markdown plugin would show it) and
uses the same ``---`` between slides, Reveal.js's default separator.

The model's own Markdown (bold, lists) is kept; ``<``, ``>`` and ``&`` are
written as entities, since both tools can render HTML in a slide.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

MAX_LINES = 7
_SOURCE = re.compile(r"\[Source\s+(\d+)(?:[^\]]*)\]", re.IGNORECASE)


def _safe(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cites(text: str) -> str:
    return _SOURCE.sub(lambda m: f"[{m.group(1)}]", text)


def slides(artifact: Dict[str, Any], collection_name: str, fmt: str = "marp") -> str:
    title = artifact.get("title") or "Report"
    when = (artifact.get("finished_at") or artifact.get("created_at") or "")[:10]
    sections: List[List[str]] = [[]]
    headings: List[str] = [""]
    for line in (artifact.get("content") or "").replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        m = re.match(r"^#{1,3}\s+(.*)$", stripped)
        if m:
            headings.append(m.group(1).strip())
            sections.append([])
        elif stripped:
            sections[-1].append(_safe(_cites(stripped)))
    out: List[str] = []
    if fmt == "marp":
        out.append("---\nmarp: true\npaginate: true\n---\n")
    sub = " · ".join(x for x in (collection_name, when) if x)
    out.append(f"# {_safe(title)}\n\n{_safe(sub)}\n" if sub else f"# {_safe(title)}\n")
    for heading, lines in zip(headings, sections):
        if not lines:
            continue
        if not heading and lines and lines[0].lstrip("# ").strip() == title:
            lines = lines[1:]
        chunks = [lines[i:i + MAX_LINES] for i in range(0, len(lines), MAX_LINES)] or [[]]
        for n, chunk in enumerate(chunks):
            name = _safe(heading) + (" (continued)" if n else "")
            out.append("\n---\n\n" + (f"## {name}\n\n" if heading else "") + "\n".join(
                chunk if all(c.startswith(("-", "*", "1", "2", "3", "4", "5", "6", "7", "8", "9")) for c in chunk)
                else [c + "\n" for c in chunk]).rstrip() + "\n")
    sources = list(artifact.get("sources") or [])
    if sources:
        rows = [f"{i}. {_safe(str(s.get('filename') or ''))}" + (f", page {s['page_number']}" if s.get("page_number")
                                                              else "") for i, s in enumerate(sources, start=1)]
        out.append("\n---\n\n## Sources\n\n" + "\n".join(rows) + "\n")
    return "".join(out)

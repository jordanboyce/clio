"""H4: transcripts that play, for audio and video sources.

Clio transcribes a recording into lines that start with their time,
``[03:07] what was said``, grouped into pages of four minutes
(services/document_extractor.py). The segments' own start and end times
are not kept after indexing, so the times in the text are what there is:
each line becomes a cue that starts at its time and ends where the next
begins (a last line gets LAST_CUE seconds). For each recording the export
writes ``transcripts/<name>.vtt`` (WebVTT, for a <track> or a player) and
``transcripts/<name>.json`` (the cues, each with the passage it is in) for
<clio-transcript>, which shows the transcript beside a player and seeks to a
line when it is chosen, or to a passage when the page is opened at one.

The player plays the recording from the source's own address (Where
results link); a recording with none gets its transcript without a player.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Dict, List, Optional

from services.static_export.additions import PAGE_HOOKS, Addition, ExportContext, register
from services.static_export import reader

FOLDER = "transcripts"
LAST_CUE = 5
MEDIA_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".wma", ".opus", ".ogg", ".flac", ".mpga",
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".mpeg",
}
VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".mpeg"}
_LINE = re.compile(r"^\[(\d{1,4}):([0-5]\d)\]\s*(.*)$")


def is_media(name: Optional[str]) -> bool:
    return bool(name) and ("." + name.rsplit(".", 1)[-1].lower() if "." in name else "") in MEDIA_EXTENSIONS


def cues(passages: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """The timed lines of one recording, in order, each with the passage it is first in."""
    found: List[Dict[str, Any]] = []
    previous = ""
    last_page = None
    for p in passages:
        if p["page"] != last_page:
            previous, last_page = "", p["page"]
        text = reader.without_overlap(previous, p["text"] or "", limit)
        previous = p["text"] or ""
        for line in text.split("\n"):
            m = _LINE.match(line.strip())
            if m:
                found.append({"start": int(m.group(1)) * 60 + int(m.group(2)), "text": m.group(3).strip(),
                              "passage": p["id"]})
    found.sort(key=lambda c: c["start"])
    for i, c in enumerate(found):
        nxt = next((n["start"] for n in found[i + 1:] if n["start"] > c["start"]), None)
        c["end"] = nxt if nxt is not None else c["start"] + LAST_CUE
    return found


def _stamp(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}.000"


def webvtt(items: List[Dict[str, Any]]) -> str:
    out = ["WEBVTT", ""]
    for i, c in enumerate(items, start=1):
        # A cue's text may not hold "-->" or a blank line; < and & are markup in WebVTT.
        text = c["text"].replace("-->", "->").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        out += [str(i), f"{_stamp(c['start'])} --> {_stamp(c['end'])}", text or "…", ""]
    return "\n".join(out)


def _media(ctx: ExportContext) -> List[Dict[str, Any]]:
    return [d for d in ctx.documents if is_media(d.get("name"))]


def _offer(o) -> Dict[str, Any]:
    found = [d for d in o.survey["documents"] if is_media(d.get("filename"))]
    if not found:
        return {"available": False, "reason": "No source in this export is a recording.", "bytes": 0}
    return {"available": True, "reason": None, "bytes": len(found) * 20000,
            "note": f"{len(found)} recording{'' if len(found) == 1 else 's'}. Each plays from its own address, "
                    f"under Where results link."}


def _write(ctx: ExportContext) -> Dict[str, Any]:
    grouped = reader.by_document(ctx.passages)
    limits = {d["document_id"]: reader.overlap_limit(d.get("chunk_overlap")) for d in ctx.survey["documents"]}
    names = reader.page_names(ctx.documents)
    entries = {}
    for doc in _media(ctx):
        items = cues(grouped.get(doc["id"], []), limits.get(doc["id"], 208))
        if not items:
            continue
        stem = f"{FOLDER}/" + names[doc["id"]].split("/", 1)[1][:-len(".html")]
        ctx.put(f"{stem}.vtt", webvtt(items))
        ctx.put(f"{stem}.json", json.dumps({"source": doc["id"], "cues": items}, ensure_ascii=False,
                                           separators=(",", ":")) + "\n")
        kind = "video" if ("." + doc["name"].rsplit(".", 1)[-1].lower()) in VIDEO_EXTENSIONS else "audio"
        entries[doc["id"]] = {"vtt": f"{stem}.vtt", "cues": f"{stem}.json", "kind": kind, "count": len(items)}
    return {"sources": entries}


def _page_part(ctx: ExportContext, doc):
    if doc is None or not ctx.site:
        return {}
    if doc["id"] not in ((ctx.results.get("transcripts") or {}).get("sources") or {}):
        return {}
    return {"head": '<script type="module" src="../../clio-transcript.js"></script>\n',
            "body": f'<section aria-label="Transcript">\n<clio-transcript src="../" source="{html.escape(doc["id"])}">'
                    "</clio-transcript>\n</section>\n"}


PAGE_HOOKS["transcripts"] = _page_part
register(Addition(id="transcripts", label="Transcripts that play beside the recording", offer=_offer,
                  write=_write))

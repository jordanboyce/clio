"""G1: what changed since the last export, as a page and an Atom feed.

A passage's id comes from its source's content, page and position, so a
reworded source gets new ids throughout: changes are told per source, not
per sentence. Against the previous export, a source is

- added: its id and its file name are both new;
- removed: its id and its file name are both gone;
- replaced: its file name is the same and its id is not (the content changed);
- superseded: it is marked as replaced by another exported source, which it
  was not last time.

The previous export comes from the folder being replaced (the command line
reads its manifest first), or, for a download from the app, from what the
server remembers of the last export: each source's id, name and title, and
the change history, never any text (``static_export_last:<collection>`` in
the app database). The history, newest first and at most HISTORY entries,
travels in the manifest so the next export carries it on.

The page is ``read/changes.html``; the feed, ``changes.xml``, needs the
site's address (B3), since Atom needs absolute addresses.
"""

from __future__ import annotations

import html
from typing import Any, Dict, List, Optional

from services.static_export.additions import PAGE_HOOKS, Addition, ExportContext, register
from services.static_export import reader

PAGE = f"{reader.FOLDER}/changes.html"
FEED = "changes.xml"
HISTORY = 50
_MEMORY = "static_export_last:{}"


def summary(manifest: Dict[str, Any], superseded: Dict[str, Optional[str]]) -> Dict[str, Any]:
    """What the next export compares with: ids, names, titles, never text."""
    history = ((manifest.get("additions") or {}).get("changes") or {}).get("history") or []
    return {"exported_at": manifest.get("exported_at"),
            "documents": [{"id": d["id"], "name": d.get("name"), "title": d.get("title"),
                           "superseded_by": superseded.get(d["id"])} for d in manifest.get("documents", [])],
            "history": history[:HISTORY]}


def remember(collection_id: str, manifest: Dict[str, Any], superseded: Dict[str, Optional[str]]) -> None:
    from services.app_database import app_db

    app_db.set_config(_MEMORY.format(collection_id), summary(manifest, superseded))


def remembered(collection_id: str) -> Optional[Dict[str, Any]]:
    from services.app_database import app_db

    saved = app_db.get_config(_MEMORY.format(collection_id), None)
    return saved if isinstance(saved, dict) and isinstance(saved.get("documents"), list) else None


def from_manifest(manifest: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A previous export's manifest, read from the folder it is replacing."""
    if not isinstance(manifest, dict) or not isinstance(manifest.get("documents"), list):
        return None
    return summary(manifest, {d.get("id"): d.get("superseded_by") for d in manifest["documents"]
                              if isinstance(d, dict)})


def compare(previous: Dict[str, Any], current: List[Dict[str, Any]], superseded: Dict[str, Optional[str]],
            when: str) -> List[Dict[str, Any]]:
    before = {d["id"]: d for d in previous.get("documents", []) if isinstance(d, dict) and d.get("id")}
    before_names = {d.get("name"): d for d in before.values()}
    now = {d["id"]: d for d in current}
    now_names = {d.get("name"): d for d in current}
    titles = {d["id"]: d.get("title") or d.get("name") for d in current}
    entries = []
    for d in current:
        if d["id"] in before:
            was = before[d["id"]].get("superseded_by")
            by = superseded.get(d["id"])
            if by and by in now and by != was:
                entries.append({"kind": "superseded", "id": d["id"], "title": titles[d["id"]],
                                "by": by, "by_title": titles[by]})
            continue
        old = before_names.get(d.get("name"))
        if old and old["id"] not in now:
            entries.append({"kind": "replaced", "id": d["id"], "was": old["id"], "title": titles[d["id"]]})
        else:
            entries.append({"kind": "added", "id": d["id"], "title": titles[d["id"]]})
    for d in before.values():
        if d["id"] not in now and d.get("name") not in now_names:
            entries.append({"kind": "removed", "id": d["id"], "title": d.get("title") or d.get("name")})
    order = {"added": 0, "replaced": 1, "superseded": 2, "removed": 3}
    entries.sort(key=lambda e: (order[e["kind"]], (e.get("title") or "").lower(), e["id"]))
    return [{**e, "date": when} for e in entries]


_WORDS = {"added": "Added", "removed": "Removed", "replaced": "Changed", "superseded": "Superseded"}


def _sentence(e: Dict[str, Any]) -> str:
    if e["kind"] == "superseded":
        return f"{e['title']} is replaced by {e['by_title']}."
    return {"added": f"{e['title']} was added.", "removed": f"{e['title']} was removed.",
            "replaced": f"{e['title']} was changed."}[e["kind"]]


def _offer(o) -> Dict[str, Any]:
    from services.static_export.addresses import address_settings

    site_url = address_settings(o.collection_id).get("site_url")
    last = remembered(o.collection_id)
    note = ("Compared with the last export" + (f" ({last['exported_at'][:10]})" if last and last.get("exported_at")
                                                else "") if last else "No earlier export to compare with yet")
    note += "; with a feed." if site_url else "; no feed: it needs the address the site will be served at."
    return {"available": bool(o.survey["passages"]), "reason": None, "bytes": 8000, "note": note}


def _write(ctx: ExportContext) -> Dict[str, Any]:
    superseded = {d["document_id"]: d.get("superseded_by") for d in ctx.survey["documents"]}
    exported = {d["id"] for d in ctx.documents}
    superseded = {k: v for k, v in superseded.items() if k in exported and v in exported}
    # A source still in the collection but left out of this export (held, or
    # labelled more strictly) is not named, not even in last time's history.
    left_out = {r[0] for r in ctx.survey["conn"].execute("SELECT document_id FROM documents")} - exported
    previous = ctx.previous
    if previous:
        previous = {**previous, "documents": [d for d in previous.get("documents", [])
                                              if isinstance(d, dict) and d.get("id") not in left_out]}
        previous["history"] = [e for e in previous.get("history") or [] if isinstance(e, dict)
                               and not ({e.get("id"), e.get("was"), e.get("by")} & left_out)]
    when = (ctx.manifest.get("exported_at") or "")[:19] + "Z" if ctx.manifest.get("exported_at") else ""
    fresh = compare(previous, ctx.documents, superseded, when) if previous else []
    history = (fresh + list((previous or {}).get("history") or []))[:HISTORY]
    names = reader.page_names(ctx.documents)
    collection = (ctx.manifest.get("collection") or {}).get("name") or "Sources"

    def link(e):
        page = names.get(e["id"])
        return f'<a href="{html.escape(page.split("/", 1)[1])}">{html.escape(e["title"] or "")}</a>' if page \
            else html.escape(e["title"] or "")

    rows = []
    for e in history:
        text = (f"{link(e)} is replaced by {link({'id': e['by'], 'title': e['by_title']})}." if e["kind"] == "superseded"
                else f"{link(e)} was {'changed' if e['kind'] == 'replaced' else e['kind']}.")
        rows.append(f'<li><span class="meta">{html.escape(e["date"][:10])} · {_WORDS[e["kind"]]}</span> {text}</li>\n')
    intro = ("Sources added, changed, replaced or removed between one export and the next, newest first. A change "
             "inside a source is shown as the whole source changing.")
    body = (f'<nav><a href="index.html">{html.escape(collection)}</a></nav>\n<header>\n<h1>What changed</h1>\n'
            f'<p class="meta">{intro}</p>\n</header>\n<main>\n'
            + (f'<ol class="sources">\n{"".join(rows)}</ol>\n' if rows else "<p>Nothing yet: this is the first "
               "export these pages were compared with.</p>\n") + "</main>\n")
    feed_url = reader.public_address(ctx, FEED, ctx.site)
    head = (f'<link rel="alternate" type="application/atom+xml" title="What changed" href="{html.escape(feed_url)}">\n'
            if feed_url else "")
    ctx.put(PAGE, reader.page(f"What changed · {collection}", body, depth=1, site=ctx.site, head=head))
    entry: Dict[str, Any] = {"page": PAGE, "new": len(fresh), "history": history,
                             "compared_with": (previous or {}).get("exported_at")}
    if feed_url:
        ctx.put(FEED, _feed(ctx, history, feed_url, names, collection))
        entry["feed"] = FEED
    return entry


def _feed(ctx: ExportContext, history, feed_url, names, collection) -> str:
    page_url = reader.public_address(ctx, PAGE, ctx.site)
    updated = history[0]["date"] if history else "2000-01-01T00:00:00Z"
    x = lambda s: html.escape(s or "", quote=True)  # noqa: E731
    out = ['<?xml version="1.0" encoding="utf-8"?>', '<feed xmlns="http://www.w3.org/2005/Atom">',
           f"<title>{x(collection)}: what changed</title>", f"<id>{x(feed_url)}</id>",
           f'<link rel="self" href="{x(feed_url)}"/>', f'<link rel="alternate" href="{x(page_url)}"/>',
           f"<updated>{x(updated)}</updated>", f"<author><name>{x(collection)}</name></author>"]
    for e in history:
        target = reader.public_address(ctx, names[e["id"]], ctx.site) if e["id"] in names else page_url
        out += ["<entry>", f"<title>{x(_WORDS[e['kind']] + ': ' + (e['title'] or ''))}</title>",
                f"<id>{x(feed_url)}#{x(e['kind'])}-{x(e['id'])}-{x(e['date'])}</id>",
                f'<link rel="alternate" href="{x(target)}"/>', f"<updated>{x(e['date'])}</updated>",
                f"<summary>{x(_sentence(e))}</summary>", "</entry>"]
    out.append("</feed>")
    return "\n".join(out) + "\n"


def _page_part(ctx: ExportContext, doc):
    return {} if doc is not None else {"body": '<p><a href="changes.html">What changed</a></p>\n'}


PAGE_HOOKS["changes"] = _page_part
register(Addition(id="changes", label="What changed since the last export, with a feed", offer=_offer,
                  write=_write, after=("reader",)))

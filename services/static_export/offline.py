"""G2: a whole site that works offline and installs on a phone.

Adds, at the site's root: ``sw.js`` (docs/static-search/clio-sw.js), the
service worker that keeps the site's files for when there is no connection;
``site.webmanifest``, so a browser offers to install the site; and two
plain icons. Every page of the export (the search page and the reader's)
loads ``clio-offline.js``, which registers the worker.

The worker keeps the rules the components keep (see clio-sw.js): the
manifest is read from the network first whenever there is one, every other
file is kept under its checksum, so one export's manifest is never paired
with another's files, and a new export shows on the next visit with a
connection.
"""

from __future__ import annotations

import json
import struct
import zlib
from typing import Any, Dict

from services.static_export.additions import PAGE_HOOKS, Addition, ExportContext, register
from services.static_export.common import SITE_PAGE_DIR

WORKER = "sw.js"
APP_MANIFEST = "site.webmanifest"
THEME = (29, 78, 216)  # the reader's link colour


def png(size: int, rgb=THEME) -> bytes:
    """A plain square PNG, made without an imaging library."""
    row = b"\x00" + bytes(rgb) * size
    raw = zlib.compress(row * size, 9)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def _offer(o) -> Dict[str, Any]:
    return {"available": True, "reason": None, "bytes": 6000,
            "note": "Visitors' browsers keep the whole site, the model included, after the first visit."}


def _write(ctx: ExportContext) -> Dict[str, Any]:
    collection = ctx.manifest.get("collection") or {}
    name = collection.get("name") or "Search"
    ctx.put_root(WORKER, (SITE_PAGE_DIR / "clio-sw.js").read_text(encoding="utf-8"))
    ctx.put_root("icon-192.png", png(192))
    ctx.put_root("icon-512.png", png(512))
    app = {"name": name, "short_name": name[:12], "description": collection.get("description") or "",
           "start_url": "./index.html", "scope": "./", "display": "standalone",
           "background_color": "#ffffff", "theme_color": "#%02x%02x%02x" % THEME,
           "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                     {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]}
    ctx.put_root(APP_MANIFEST, json.dumps(app, indent=2, ensure_ascii=False) + "\n")
    return {"worker": f"../{WORKER}", "app_manifest": f"../{APP_MANIFEST}"}


# What the site's pages carry to register the worker: the search page gets
# it from _write_site (site.py); the reader's pages from here.
HEAD_AT_ROOT = f'<link rel="manifest" href="{APP_MANIFEST}">\n<script type="module" src="clio-offline.js"></script>\n'


def _page_part(ctx: ExportContext, doc):
    return {"head": f'<link rel="manifest" href="../../{APP_MANIFEST}">\n'
                    '<script type="module" src="../../clio-offline.js"></script>\n'}


PAGE_HOOKS["offline"] = _page_part
register(Addition(id="offline", label="Works offline, and installs on a phone", offer=_offer, write=_write,
                  site_only=True))

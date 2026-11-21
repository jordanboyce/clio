"""What an export can carry besides the search: the additions.

An export with nothing chosen is the export as it always was. Each addition
named in ``include`` writes its own files beside the manifest, through the
same gate: it reads only what ``_survey`` kept (no held source, no source
with a stricter label, nothing at all from a collection holding anything
local only), and it is told the passages, sources and links the export
itself writes, so it can mention nothing the export left out.

The manifest lists what an export carries under ``additions``, each with its
files and their checksums. A client that predates an addition ignores the
key, so a site that copied ``clio-search.js`` last month still searches a new
export.

An addition is registered with ``register`` by the module that builds it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from services.static_export.common import StaticExportError


@dataclass
class Addition:
    id: str
    label: str
    # Decides from the survey alone: {"available": bool, "reason": str|None, "bytes": int}.
    offer: Callable[["Offer"], Dict[str, Any]]
    # Writes the files through ctx.put; may return fields for its manifest entry.
    write: Callable[["ExportContext"], Optional[Dict[str, Any]]]
    # Only in a whole site (it needs the page's scripts).
    site_only: bool = False
    # Ids of additions this one needs (chosen with it).
    after: tuple = ()
    # Written in this order; the reader goes last, so the pages can show
    # what the others wrote.
    stage: int = 0


ADDITIONS: Dict[str, Addition] = {}

# What other additions put on the reader's pages, by addition id:
# hook(ctx, doc or None for the contents page) -> {"head": html, "body": html}.
# A hook runs only when its addition is in the export; one registered under
# "" runs always (the site's own address, say).
PAGE_HOOKS: Dict[str, Callable[["ExportContext", Optional[Dict[str, Any]]], Dict[str, str]]] = {}


def register(addition: Addition) -> Addition:
    ADDITIONS[addition.id] = addition
    return addition


# The modules that register additions, in the order their additions are offered.
_MODULES: tuple = ("reader",)


def _load() -> None:
    """Import the modules that register additions (they import this one)."""
    import importlib

    for name in _MODULES:
        importlib.import_module(f"services.static_export.{name}")


def known() -> List[str]:
    _load()
    return list(ADDITIONS)


def parse_include(value: Any) -> List[str]:
    """``include`` as given (None, "reader,related", ["reader"], "all") -> ids in
    the order they are written. An unknown name is refused."""
    _load()
    if value is None or value == "" or value == ():
        return []
    names = value.replace(",", " ").split() if isinstance(value, str) else [str(v).strip() for v in value]
    names = [n for n in names if n]
    if "all" in names:
        # Everything this export can carry: resolve() drops what it cannot.
        return ["all"]
    unknown = [n for n in names if n not in ADDITIONS]
    if unknown:
        raise StaticExportError(
            f"There is no addition called {unknown[0]}. Choose from: {', '.join(ADDITIONS)}.", "option")
    chosen = set(names)
    for n in list(chosen):
        chosen.update(ADDITIONS[n].after)
    return [n for n in ADDITIONS if n in chosen]


@dataclass
class Offer:
    """What the preview knows when it asks an addition whether it can be made."""
    collection_id: str
    survey: Dict[str, Any]
    site: Optional[bool]  # None: the preview, which offers both kinds

    @property
    def semantic(self) -> bool:
        return self.survey["browser"] is not None


def offers(collection_id: str, survey: Dict[str, Any]) -> List[Dict[str, Any]]:
    """For the preview: each addition, whether it can be made, why not, its size."""
    _load()
    rows = []
    offer = Offer(collection_id, survey, None)
    for a in ADDITIONS.values():
        try:
            o = a.offer(offer)
        except Exception as e:  # noqa: BLE001 - one addition's trouble never hides the export
            o = {"available": False, "reason": f"It could not be worked out: {e}", "bytes": 0}
        rows.append({"id": a.id, "label": a.label, "site_only": a.site_only, "available": bool(o.get("available")),
                     "reason": o.get("reason"), "bytes": int(o.get("bytes") or 0),
                     **{k: v for k, v in o.items() if k not in ("available", "reason", "bytes")}})
    return rows


@dataclass
class ExportContext:
    """What an addition is given while an export is being written."""
    collection_id: str
    survey: Dict[str, Any]
    data_dir: Path
    site: bool
    manifest: Dict[str, Any]  # as it will be published; additions may add fields to its documents
    passages: List[Dict[str, Any]]  # every exported passage, in the export's order
    vectors: Any = None  # the passages' float32 vectors, same order, or None (keyword export)
    settings: Dict[str, Any] = field(default_factory=dict)  # the collection's saved export settings
    written: List[Path] = field(default_factory=list)
    chosen: List[str] = field(default_factory=list)
    results: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # earlier additions' manifest entries
    files: List[Dict[str, Any]] = field(default_factory=list)  # this addition's files so far

    @property
    def semantic(self) -> bool:
        return self.vectors is not None

    @property
    def documents(self) -> List[Dict[str, Any]]:
        """The manifest's source entries (id, name, title, pages, passages, link)."""
        return self.manifest["documents"]

    def page_parts(self, doc: Optional[Dict[str, Any]]) -> Dict[str, str]:
        """What the other additions in this export put on one reader page."""
        head, body = [], []
        for name, hook in PAGE_HOOKS.items():
            if name and name not in self.chosen:
                continue
            part = hook(self, doc) or {}
            head.append(part.get("head", ""))
            body.append(part.get("body", ""))
        return {"head": "".join(head), "body": "".join(body)}

    def put(self, name: str, data: Any) -> Dict[str, Any]:
        """Write one file under the export folder (``name`` uses "/"), and list it."""
        raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        path = self.data_dir / Path(*name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        self.written.append(path)
        path.write_bytes(raw)
        entry = {"file": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        self.files.append(entry)
        return entry


def write_all(chosen: Iterable[str], ctx: ExportContext) -> Dict[str, Dict[str, Any]]:
    """Run each chosen addition; returns the manifest's ``additions``."""
    out: Dict[str, Dict[str, Any]] = {}
    ctx.chosen = list(chosen)
    for name in sorted(chosen, key=lambda n: ADDITIONS[n].stage):
        addition = ADDITIONS[name]
        ctx.files = []
        extra = addition.write(ctx) or {}
        entry = {**extra, "files": sorted(ctx.files, key=lambda f: f["file"])}
        out[name] = entry
        ctx.results[name] = entry
    return {name: out[name] for name in chosen}


def resolve(chosen: List[str], collection_id: str, survey: Dict[str, Any], site: bool) -> List[str]:
    """The additions to write. "all" is every one this export can carry; a
    name given outright that it cannot carry is refused, saying why."""
    offer = Offer(collection_id, survey, site)
    if chosen == ["all"]:
        fits = [a.id for a in ADDITIONS.values() if (site or not a.site_only) and a.offer(offer).get("available")]
        # An addition that reads from one left out cannot be made either.
        return [n for n in fits if all(dep in fits for dep in ADDITIONS[n].after)]
    for name in chosen:
        addition = ADDITIONS[name]
        if addition.site_only and not site:
            raise StaticExportError(f"{addition.label} needs a whole site: choose site as well.", "option")
        o = addition.offer(offer)
        if not o.get("available"):
            raise StaticExportError(f"{addition.label} cannot be included: {o.get('reason')}", "option")
    return chosen


def listed_files(manifest: Dict[str, Any]) -> List[str]:
    """Every file an export's manifest names, besides itself."""
    names = [f["file"] for f in (manifest.get("passages") or {}).get("files", [])]
    if manifest.get("vectors"):
        names.append(manifest["vectors"]["file"])
    for entry in (manifest.get("additions") or {}).values():
        names += [f["file"] for f in entry.get("files", [])]
    return names

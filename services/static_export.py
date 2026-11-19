"""Export a collection so a static website can search it with no server.

The export is a folder (or a zip of one):

    manifest.json    which model made the vectors, how to read the files
    passages.json    one passage per line: id, document_id, page, text
                     (passages-0001.json, ... when there are many)
    vectors.bin      one vector per passage, in the same order, 8-bit

A page loads the three, embeds the visitor's query in the browser with the
same model, and ranks by the dot product. docs/static-search/ holds a
reference client; docs/STATIC_SEARCH.md is the guide.

With ``site=True`` the export is a whole site instead, ready to serve: the
reference page, the files above under search/, and everything the page needs
to embed a query with no outside service: transformers.js and its
WebAssembly runtime under vendor/ (baked into the image at build time, see
scripts/vendor_static_search.mjs), and under models/ the very ONNX file Clio
embeds with. That is what a network that blocks Hugging Face and the CDNs
needs, and since it is the same file, the probe matches exactly.

Three things make this different from a collection bundle
(services/collection_bundle.py):

Same model on both sides. A query vector only means something next to
passage vectors from the same model, so the export is refused unless the
collection was embedded on this machine with a model that also has a browser
build whose vectors were checked against Clio's (``BROWSER_MODELS``). The
manifest records the model, its pooling and its query prefix, and carries a
probe: a fixed sentence with the vector Clio's own model gives it. A client
embeds the sentence itself and refuses to search by meaning when the two
disagree, which catches a wrong model, wrong pooling, a missing prefix and a
quantised model build alike.

Everything in it is published. The text goes to every visitor and the vectors
give away its gist, so this is the most outside of all outside services
(services/data_controls.py). A collection that holds anything local only is
refused whole, as everywhere else. Beyond that the collection must be
labelled Public, sources with a stricter label of their own and held sources
are left out, and the caller has to say the collection is cleared for
release. Nothing here calls a network: it reads the index on disk.

Reviewable. Passages are ordered by source name, page and position, their ids
are Clio's chunk ids, and each sits on its own line, so a year's export
diffs cleanly against the last one in the site's repository.

An asset for a website. Each source in the manifest says where it lives on
the web (``link``): the address someone gave it, the page a link source came
from, the file at its commit for a source synced from a public repository, or
the collection's base address for files. The client turns that into a link to
the passage (a PDF's page, a web page's text, a file's lines). A collection
whose model has no browser build is exported for keyword search (format v2,
no vectors) rather than refused, so any collection that may be published can
be. A whole site also carries <clio-search>, the element a host site drops
into its own pages, and settings for the common web servers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import sqlite3
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlsplit

import numpy as np

from services.collection_bundle import _clio_version, _work_dir, describe_model

logger = logging.getLogger(__name__)

FORMAT = "clio-static-search"
VERSION = 1
# An export with no vectors (the collection's model has no browser build) is
# searched by keyword only. A v1 client would fail on it, so it says v2, which
# such a client refuses with a message instead.
KEYWORD_VERSION = 2

MANIFEST_FILE = "manifest.json"
VECTORS_FILE = "vectors.bin"
PASSAGES_FILE = "passages.json"

DEFAULT_PASSAGES_PER_FILE = 2000
QUANTIZATIONS = ("int8", "float32")

# What the export is, to the rule that keeps local-only data on this server.
DESTINATION = "a public website"

# The sentence whose vector travels in the manifest. Changing it changes
# nothing for old exports: each manifest carries its own.
PROBE_TEXT = "Which passages answer this question?"

# The ready-to-serve site (``site=True``). The runtime comes from the image,
# or from DATA_DIR/static-search/vendor on a source install
# (scripts/vendor_static_search.mjs writes either).
SITE_DATA_DIR = "search"
SITE_RUNTIME_IMAGE_DIR = Path("/opt/clio/static-search/vendor")
SITE_RUNTIME_FILES = ("transformers.min.js", "ort-wasm-simd-threaded.asyncify.js",
                      "ort-wasm-simd-threaded.asyncify.wasm")
SITE_PAGE_DIR = Path(__file__).resolve().parent.parent / "docs" / "static-search"
SITE_PAGE_FILES = ("index.html", "clio-static-search.js", "clio-search.js")
# Settings for the common web servers (content types, caching, an optional
# CORS line), copied to the root of a whole site. Each server reads only its own.
SITE_HOSTING_DIR = SITE_PAGE_DIR / "hosting"
SITE_HOSTING_FILES = ("_headers", ".htaccess", "web.config", "README.txt")
# Besides the ONNX file, what transformers.js reads from a model folder.
SITE_MODEL_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json")

# Clio model -> the browser build that reproduces its query vectors, and how
# that model pools. Add a model here only after comparing vectors: for both
# of these, transformers.js at fp32 or fp16 gives a query vector within
# 0.00001 cosine of fastembed's, and the q8 builds drift to 0.991-0.995,
# which reorders close results (docs/STATIC_SEARCH.md has the numbers).
BROWSER_MODELS: Dict[str, Dict[str, str]] = {
    "all-MiniLM-L6-v2": {"model": "Xenova/all-MiniLM-L6-v2", "pooling": "mean"},
    "BAAI/bge-small-en-v1.5": {"model": "Xenova/bge-small-en-v1.5", "pooling": "cls"},
}


class StaticExportError(ValueError):
    """The export cannot be made; the message says why in plain words.

    ``code`` is stable: ``not_found``, ``empty``, ``local_only``,
    ``not_public``, ``model``, ``vectors``, ``confirm``, ``option``, ``site``,
    ``address``.
    """

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


def browser_model(model_name: Optional[str]) -> Optional[Dict[str, str]]:
    """The browser build for a Clio model name, or None when there is none."""
    name = (model_name or "").strip()
    if name.lower().startswith("sentence-transformers/"):
        name = name.split("/", 1)[1]
    for known, entry in BROWSER_MODELS.items():
        if known.lower() == name.lower():
            return {"clio_model": known, **entry}
    return None


def _supported() -> str:
    return " or ".join(BROWSER_MODELS)


# ── Where each source lives on the web ───────────────────────────────────────

MAX_ADDRESS_CHARS = 2048
_ADDRESS_SETTINGS = "static_export_addresses:{}"
# Sources whose file a site can publish; a pasted note or an agent's note has
# no file at an address of its own.
_FILE_SOURCE_TYPES = (None, "", "upload", "local_reference", "folder_sync")
_PAGE_EXTENSIONS = (".pdf",)
_TEXT_FRAGMENT_EXTENSIONS = (".html", ".htm", ".xhtml", ".md", ".txt")


def clean_public_url(value: Optional[str]) -> Optional[str]:
    """An address a visitor's browser can open, or None for "none".

    Accepted: an absolute http(s) URL with no user name or password in it, or
    a path on the site itself starting with "/". Anything else (javascript:,
    a file path, an address with spaces) is refused with StaticExportError.
    """
    text = (value or "").strip()
    if not text:
        return None
    if len(text) > MAX_ADDRESS_CHARS or re.search(r"[\s\x00-\x1f\x7f<>\"\\]", text):
        raise StaticExportError(f"{text[:80]} is not a web address a page can link to.", "address")
    if text.startswith("/") and not text.startswith("//"):
        return text
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise StaticExportError(
            f"{text[:80]} is not a web address: use one that starts with https:// (or a path on the site, "
            f"starting with /).", "address")
    if parts.username or parts.password:
        raise StaticExportError("A web address for a public site must not carry a user name or password.",
                                "address")
    return text


def address_settings(collection_id: str) -> Dict[str, Any]:
    """The collection's saved export addresses: ``{"files_base_url": ...}``."""
    from services.app_database import app_db

    saved = app_db.get_config(_ADDRESS_SETTINGS.format(collection_id), {}) or {}
    return {"files_base_url": saved.get("files_base_url") if isinstance(saved, dict) else None}


def _web_page(path: str) -> str:
    return quote(path, safe="/-._~")


# A repository host whose web pages show a file at a commit: (does the host
# match, the file's page, the anchor for lines). Other hosts get no link of
# their own; give their sources an address instead.
_FORGES = (
    (lambda h: h == "github.com", "{base}/blob/{commit}/{path}", "#L{start}-L{end}"),
    (lambda h: h == "gitlab.com" or h.startswith("gitlab."), "{base}/-/blob/{commit}/{path}", "#L{start}-{end}"),
    (lambda h: h == "bitbucket.org", "{base}/src/{commit}/{path}", "#lines-{start}:{end}"),
    (lambda h: h == "codeberg.org" or h.startswith(("gitea.", "forgejo.")),
     "{base}/src/commit/{commit}/{path}", "#L{start}-L{end}"),
)


def repository_file_link(remote: Optional[str], commit: Optional[str], path: str) -> Optional[Dict[str, str]]:
    """The web page of one file at one commit, for the hosts in ``_FORGES``.

    Takes the clone's remote as git has it (https://, ssh:// or git@host:...);
    credentials in it are dropped, never published.
    """
    remote = (remote or "").strip()
    if not remote or not commit or not re.fullmatch(r"[0-9a-fA-F]{7,64}", commit):
        return None
    scp = re.fullmatch(r"[\w.-]+@([\w.-]+):(.+)", remote)
    if scp:
        host, repo = scp.group(1), scp.group(2)
    else:
        parts = urlsplit(remote)
        if parts.scheme not in ("http", "https", "ssh", "git") or not parts.hostname:
            return None
        host, repo = parts.hostname, parts.path
    host = host.lower()
    repo = re.sub(r"\.git$", "", repo.strip("/"))
    if repo.count("/") < 1 or ".." in repo.split("/"):
        return None
    for matches, page, lines in _FORGES:
        if matches(host):
            url = page.format(base=f"https://{host}/{repo}", commit=commit, path=_web_page(path))
            return {"url": url, "lines": lines}
    return None


def _repositories(collection_id: str) -> List[Dict[str, Any]]:
    """Synced folders that are git repositories: [{"root", "remote", "commit"}]."""
    from services.upload_service import upload_service

    found = []
    for entry in upload_service.list_sync_folders(collection_id):
        if not entry.get("git") or not entry.get("git_head"):
            continue
        remote = entry.get("git_url")
        root = Path(entry["path"])
        if not remote:
            # A checkout indexed in place: its own origin, read from disk.
            try:
                from services import git_source

                remote = git_source._run(["config", "--get", "remote.origin.url"], cwd=root,
                                         safe_dir=root).strip()
            except Exception:  # noqa: BLE001 - no origin, no link
                remote = None
        if remote:
            found.append({"root": root, "remote": remote, "commit": entry["git_head"]})
    return found


def _repository_link(doc: Dict[str, Any], repositories: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    if doc.get("source_type") != "folder_sync" or not doc.get("source_path"):
        return None
    try:
        path = Path(doc["source_path"]).resolve()
    except (OSError, ValueError):
        return None
    for repo in repositories:
        try:
            relative = path.relative_to(repo["root"].resolve())
        except (OSError, ValueError):
            continue
        return repository_file_link(repo["remote"], repo["commit"], relative.as_posix())
    return None


def document_link(doc: Dict[str, Any], files_base_url: Optional[str],
                  repositories: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Where a visitor goes for this source, and how to point inside it.

    In order: the address someone gave the source; the page a link source was
    fetched from; the file at its commit, for a file synced from a public
    repository; the collection's base address for files plus the file name.
    ``page`` and ``lines`` are anchors with {page}, {start} and {end} to fill
    in; ``text`` says the page can take a text fragment (#:~:text=).
    """
    name = doc.get("filename") or ""
    extension = Path(name).suffix.lower()
    url, came_from, lines = None, None, None
    own = doc.get("public_url")
    try:
        own = clean_public_url(own)
    except StaticExportError:
        own = None  # an address that was never checked (an old bundle) is not published
    if own:
        url, came_from = own, "own"
    elif doc.get("source_type") == "url" and str(doc.get("source_path") or "").lower().startswith(("http://", "https://")):
        url, came_from = doc["source_path"], "link"
    else:
        repo = _repository_link(doc, repositories)
        if repo:
            url, came_from, lines = repo["url"], "repository", repo["lines"]
        elif files_base_url and doc.get("source_type") in _FILE_SOURCE_TYPES:
            url, came_from = files_base_url.rstrip("/") + "/" + _web_page(name), "files"
    if not url:
        return None
    link: Dict[str, Any] = {"url": url, "from": came_from}
    if lines:
        link["lines"] = lines
    elif extension in _PAGE_EXTENSIONS:
        link["page"] = "#page={page}"
    elif extension in _TEXT_FRAGMENT_EXTENSIONS or came_from == "link":
        link["text"] = True
    return link


def addresses(collection_id: str) -> Dict[str, Any]:
    """Every source that would be exported, with the address it would carry."""
    survey = _survey(collection_id)
    survey["conn"].close()
    settings_ = address_settings(collection_id)
    repositories = _repositories(collection_id)
    rows = []
    for d in survey["documents"]:
        link = document_link(d, settings_["files_base_url"], repositories)
        rows.append({"id": d["document_id"], "name": d["filename"], "source_type": d.get("source_type") or "upload",
                     "own_url": d.get("public_url"), "url": (link or {}).get("url"),
                     "from": (link or {}).get("from")})
    return {"files_base_url": settings_["files_base_url"], "documents": rows,
            "with_address": sum(1 for r in rows if r["url"]), "total": len(rows)}


def save_addresses(collection_id: str, *, files_base_url: Any = ...,
                   documents: Optional[Dict[str, Optional[str]]] = None) -> Dict[str, Any]:
    """Save the base address for files and any source's own address.

    ``files_base_url`` left out keeps the saved one; None or "" clears it.
    ``documents`` maps a source's id to its address (None or "" clears it).
    Every address is checked first, so a refusal saves nothing.
    """
    from services.app_database import app_db
    from services.indexer_manager import indexer_manager

    base = clean_public_url(files_base_url) if files_base_url is not ... else ...
    cleaned = {doc_id: clean_public_url(url) for doc_id, url in (documents or {}).items()}
    if cleaned:
        store = indexer_manager.get_indexer(collection_id).vector_store.metadata_store
        unknown = [doc_id for doc_id in cleaned if not store.get_document_info(doc_id)]
        if unknown:
            raise StaticExportError(f"This collection has no source {unknown[0]}.", "address")
        for doc_id, url in cleaned.items():
            store.set_document_public_url(doc_id, url)
    if base is not ...:
        app_db.set_config(_ADDRESS_SETTINGS.format(collection_id), {"files_base_url": base})
    return {"files_base_url": base if base is not ... else address_settings(collection_id)["files_base_url"],
            "documents": len(cleaned)}


# ── What a ready-to-serve site needs ─────────────────────────────────────────


def site_runtime_dirs() -> List[Path]:
    from config import settings

    return [SITE_RUNTIME_IMAGE_DIR, Path(settings.data_dir) / "static-search" / "vendor"]


def site_runtime() -> Optional[Dict[str, Any]]:
    """The browser runtime a site export can carry, or None when this server
    has no copy: {"dir", "files": [names], "info": runtime.json, "bytes"}."""
    for d in site_runtime_dirs():
        try:
            if not all((d / f).is_file() for f in SITE_RUNTIME_FILES + ("runtime.json",)):
                continue
            info = json.loads((d / "runtime.json").read_text(encoding="utf-8"))
            files = sorted(f.name for f in d.iterdir() if f.is_file())
            return {"dir": d, "files": files, "info": info, "bytes": sum((d / f).stat().st_size for f in files)}
        except (OSError, ValueError):
            continue
    return None


def site_model(clio_model: str) -> Optional[Dict[str, Any]]:
    """The model files a site export can carry: the copy fastembed loads on
    this server, laid out the way transformers.js reads a model folder.

    Returns {"files": {path in the folder: source path}, "source", "license",
    "bytes"}, or None when the model is not on disk here.
    """
    from services import embedder

    try:
        fe_id = embedder.fastembed_model_id(clio_model)
        cache = embedder.fastembed_cached_in(clio_model)
        if fe_id is None or cache is None:
            return None
        desc = embedder._fastembed_catalog().get(fe_id) or {}
        model_file = desc.get("model_file") or "model.onnx"
        model_dir = embedder.fastembed_model_dir(clio_model, cache)
        candidates = sorted((model_dir / "snapshots").glob("*")) if model_dir is not None else []
        candidates.append(Path(cache) / f"fast-{fe_id.split('/')[-1]}")
        for folder in candidates:
            onnx = folder / model_file
            if not (onnx.is_file() and (folder / "tokenizer.json").is_file() and (folder / "config.json").is_file()):
                continue
            files = {name: folder / name for name in SITE_MODEL_FILES if (folder / name).is_file()}
            files["onnx/model.onnx"] = onnx
            return {"files": files, "source": (desc.get("sources") or {}).get("hf") or fe_id,
                    "license": desc.get("license"), "bytes": sum(p.stat().st_size for p in files.values())}
    except Exception as e:  # noqa: BLE001 - only decides whether the option is offered
        logger.warning(f"Static export: could not find {clio_model}'s files for a site export: {e}")
    return None


def _page_bytes() -> int:
    files = [SITE_PAGE_DIR / n for n in SITE_PAGE_FILES] + [SITE_HOSTING_DIR / n for n in SITE_HOSTING_FILES]
    return sum(p.stat().st_size for p in files if p.is_file())


def _site_offer(browser: Optional[Dict[str, str]]) -> Dict[str, Any]:
    """Whether a ready-to-serve site can be exported here, and its extra size."""
    if browser is None:
        # Searched by keyword: the site is the page and the client, no model.
        return {"available": True, "reason": None, "model": None, "model_bytes": 0, "runtime_bytes": 0,
                "bytes": _page_bytes()}
    runtime, model = site_runtime(), site_model(browser["clio_model"])
    if runtime is None:
        return {"available": False, "reason": (
            "This server has no copy of the browser runtime (transformers.js), so the site has to load it and "
            "the model from the internet. The published Clio images carry it; on a source install run "
            "scripts/vendor_static_search.mjs once.")}
    if model is None:
        return {"available": False, "reason": (
            f"{browser['clio_model']} is not on this server's disk, so it cannot be included.")}
    return {"available": True, "reason": None, "model": browser["clio_model"],
            "model_bytes": model["bytes"], "runtime_bytes": runtime["bytes"],
            "bytes": model["bytes"] + runtime["bytes"]}


# ── Reading the collection ───────────────────────────────────────────────────


def _open(db_path: Path) -> sqlite3.Connection:
    """Read only, and one transaction: every query sees the same collection."""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")
    return conn


def _index_info(conn: sqlite3.Connection) -> Dict[str, Any]:
    try:
        rows = conn.execute("SELECT key, value FROM index_info").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {k: json.loads(v) for k, v in rows}


def _local_only_message(collection: Dict[str, Any]) -> str:
    from services import data_controls

    why = data_controls.local_only_summary(collection["id"])
    name = data_controls.label_of(why.get("classification")) or "local only"
    if why.get("documents"):
        n = why["documents"]
        return (f"This collection holds {n} source{'' if n == 1 else 's'} marked {name}, which keeps "
                f"{'it' if n == 1 else 'them'} on this server, so the collection cannot be exported for a "
                f"website. Move what is cleared for release to a collection of its own and export that.")
    return (f"This collection is marked {name}, which keeps it on this server, so it cannot be exported "
            f"for a website.")


def _survey(collection_id: str) -> Dict[str, Any]:
    """Everything the preview and the export both decide from.

    Returns the open connection too; the caller closes it.
    """
    from services import data_controls, governance
    from services.collection_service import collection_service

    collection = collection_service.get_collection(collection_id)
    if not collection:
        raise StaticExportError(f"Collection '{collection_id}' not found", "not_found")
    indexes_dir = collection_service.get_indexes_path(collection_id)
    db_path = indexes_dir / "metadata.db"
    if not db_path.exists():
        raise StaticExportError("This collection has nothing indexed to export yet.", "empty")

    blockers: List[Dict[str, str]] = []
    if data_controls.holds_local_only(collection_id):
        blockers.append({"code": "local_only", "message": _local_only_message(collection)})

    sensitivity = governance.collection_sensitivity(collection)
    if sensitivity != "public":
        blockers.append({"code": "not_public", "message": (
            f"This collection is labelled {sensitivity.capitalize()}. Only a collection labelled Public can be "
            "exported for a website: once it is cleared for release, set its sensitivity to Public in the "
            "collection's settings.")})

    conn = _open(db_path)
    try:
        docs = [dict(r) for r in conn.execute("SELECT * FROM documents ORDER BY filename, document_id")]
        # As something outside would see it: a local-only source is hidden
        # here even if the whole-collection test above were ever to miss it.
        with data_controls.outside_consumer(DESTINATION):
            held = [d for d in docs if governance.is_hidden(d)]
        held_ids = {d["document_id"] for d in held}
        not_public = [d for d in docs if d["document_id"] not in held_ids
                      and d.get("sensitivity") in governance.SENSITIVITY_LEVELS
                      and d["sensitivity"] != "public"]
        left_out = held_ids | {d["document_id"] for d in not_public}
        kept = [d for d in docs if d["document_id"] not in left_out]

        info = _index_info(conn)
        model = describe_model(info, kept)
        browser = browser_model(model["model"]) if model["provider"] == "local" and model["recorded"] else None
        # No browser build: the site searches by keyword instead of refusing.
        keywords_reason = None
        if not model["recorded"]:
            keywords_reason = (
                "This collection was indexed before Clio recorded its embedding model, so a website cannot "
                "embed queries the same way: it will search by keyword. To search by meaning, re-index it, "
                "then export.")
        elif browser is None:
            where = "" if model["provider"] == "local" else f" ({model['provider']})"
            keywords_reason = (
                f"This collection was embedded with {model['model'] or 'an unknown model'}{where}, which has no "
                f"browser build Clio has checked, so the website will search by keyword. To search by meaning "
                f"there, clone or re-index the collection with {_supported()} and export that.")

        marks = ",".join("?" * len(kept))
        ids = [d["document_id"] for d in kept]
        passages, text_bytes = conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(LENGTH(CAST(text AS BLOB))), 0) FROM chunks "
            f"WHERE document_id IN ({marks})", ids).fetchone() if ids else (0, 0)
        if not passages and not any(b["code"] in ("local_only", "not_public") for b in blockers):
            blockers.append({"code": "empty", "message": "No passages are left to export."})
    except Exception:
        conn.close()
        raise

    return {
        "collection": collection, "indexes_dir": indexes_dir, "conn": conn,
        "blockers": blockers, "sensitivity": sensitivity,
        "documents": kept, "held": len(held), "not_public": len(not_public),
        "info": info, "model": model, "browser": browser, "keywords_reason": keywords_reason,
        "passages": int(passages), "text_bytes": int(text_bytes),
    }


def preview(collection_id: str, passages_per_file: int = DEFAULT_PASSAGES_PER_FILE) -> Dict[str, Any]:
    """What a static export would hold, and what stands in its way."""
    survey = _survey(collection_id)
    survey["conn"].close()
    semantic = survey["browser"] is not None
    dimension = int(survey["info"].get("dimension") or 0) if semantic else 0
    passages = survey["passages"]
    settings_ = address_settings(collection_id)
    repositories = _repositories(collection_id)
    linked = sum(1 for d in survey["documents"] if document_link(d, settings_["files_base_url"], repositories))
    return {
        "name": survey["collection"].get("name"),
        "ready": not survey["blockers"],
        "blockers": survey["blockers"],
        "sensitivity": survey["sensitivity"],
        "documents": len(survey["documents"]),
        "passages": passages,
        "held_excluded": survey["held"],
        "not_public_excluded": survey["not_public"],
        "embedding": survey["model"],
        "browser_model": (survey["browser"] or {}).get("model"),
        # "meaning", or "keywords" when the model has no browser build (why in keywords_reason).
        "search": "meaning" if semantic else "keywords",
        "keywords_reason": survey["keywords_reason"],
        # Where results link: how many sources carry a web address.
        "addresses": {"with_address": linked, "total": len(survey["documents"]),
                      "files_base_url": settings_["files_base_url"]},
        # One byte per dimension per passage; the text plus about 90 bytes of
        # JSON around each passage.
        "vectors_bytes": passages * dimension,
        "passages_bytes": survey["text_bytes"] + passages * 90,
        "passage_files": max(1, -(-passages // max(1, passages_per_file))),
        # A ready-to-serve site with the model and its runtime (site=True).
        "site": _site_offer(survey["browser"]),
    }


# ── Vectors ──────────────────────────────────────────────────────────────────


def _read_vectors(index_path: Path) -> Tuple[Dict[int, int], np.ndarray]:
    """(chunk row id -> row of the matrix, the stored vectors) from the index on disk."""
    import faiss

    if not index_path.exists():
        raise StaticExportError("This collection has no vectors yet, so there is nothing to search by meaning. "
                                "Open it in Clio so it can finish embedding, then export.", "vectors")
    # `index` owns the memory the downcast views point into: keep it in scope.
    index = faiss.read_index(str(index_path))
    idmap = faiss.downcast_index(index)
    if not isinstance(idmap, faiss.IndexIDMap2):
        raise StaticExportError("This collection's index is in an older layout. Open the collection in Clio "
                                "once so it is brought up to date, then export.", "vectors")
    ids = faiss.vector_to_array(idmap.id_map)
    flat = faiss.downcast_index(idmap.index)
    matrix = flat.reconstruct_n(0, flat.ntotal) if flat.ntotal else np.zeros((0, flat.d), dtype=np.float32)
    return {int(row_id): row for row, row_id in enumerate(ids)}, np.asarray(matrix, dtype=np.float32)


def quantize(matrix: np.ndarray, quantization: str) -> Tuple[bytes, float]:
    """(file bytes, scale). A reader recovers a score as ``dot(row, query) / scale``.

    int8 uses one scale for the whole file, chosen so the largest component
    in the corpus lands on 127: unit vectors of a few hundred dimensions have
    components far below 1, and scaling by 127 alone would throw most of the
    eight bits away.
    """
    if quantization == "float32":
        return np.ascontiguousarray(matrix, dtype="<f4").tobytes(), 1.0
    peak = float(np.abs(matrix).max()) if matrix.size else 0.0
    scale = 127.0 / peak if peak > 0 else 1.0
    return np.clip(np.rint(matrix * scale), -127, 127).astype(np.int8).tobytes(), scale


def _probe(model_name: Optional[str], dimension: int) -> Optional[Dict[str, Any]]:
    """Clio's own query vector for the probe sentence, or None when the model
    is not loaded here. The export still works without it; a client then has
    only the model's name to go on."""
    from services.indexer_manager import indexer_manager

    try:
        service = indexer_manager._get_embedding_service(model_name)
        vector = np.asarray(service.embed_query(PROBE_TEXT), dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if vector.shape[0] != dimension or norm == 0:
            return None
        return {"text": PROBE_TEXT, "vector": [round(float(x), 6) for x in vector / norm]}
    except Exception as e:  # noqa: BLE001 - the probe is a check, not the export
        logger.warning(f"Static export: no probe vector, the embedding model is not available here: {e}")
        return None


# ── Writing ──────────────────────────────────────────────────────────────────


def _passage_file_name(number: int, total: int) -> str:
    return PASSAGES_FILE if total == 1 else f"passages-{number:04d}.json"


def _manifest_text(manifest: Dict[str, Any]) -> str:
    """Indented for reading, with the probe's few hundred numbers on one line."""
    probe = manifest["embedding"].get("probe")
    if not probe:
        return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    marker = f"@{uuid.uuid4().hex}@"
    shown = {**manifest, "embedding": {**manifest["embedding"], "probe": {**probe, "vector": marker}}}
    text = json.dumps(shown, indent=2, ensure_ascii=False)
    return text.replace(f'"{marker}"', json.dumps(probe["vector"], separators=(",", ":"))) + "\n"


def clear_previous(out_dir: Path) -> None:
    """Remove a previous export's files from a folder, and nothing else."""
    for path in list(out_dir.glob("passages-[0-9][0-9][0-9][0-9].json")) + [
            out_dir / PASSAGES_FILE, out_dir / VECTORS_FILE, out_dir / MANIFEST_FILE]:
        if path.is_file():
            path.unlink()


def write_export(collection_id: str, out_dir: Path, *, confirm_public: bool = False,
                 quantization: str = "int8",
                 passages_per_file: int = DEFAULT_PASSAGES_PER_FILE,
                 site: bool = False,
                 files_base_url: Any = ...) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Write manifest.json, the passage file(s) and vectors.bin into ``out_dir``.

    With ``site`` they go into ``out_dir/search`` instead, beside the
    reference page, the <clio-search> element, web server settings, the
    browser runtime under vendor/ and the model under models/: a folder any
    static web server can serve as it is, which loads nothing from anywhere
    else.

    A collection whose model has no browser build is written for keyword
    search: no vectors.bin, and a manifest of format v2 saying why.
    ``files_base_url`` overrides the saved base address for files for this
    export only (see address_settings).

    Returns (the manifest, what the audit trail should keep). Raises
    StaticExportError when anything stands in the way; nothing is written in
    that case. How many sources were left out is in the second value and not
    in the manifest, which is published.
    """
    from services.indexer_manager import indexer_manager

    if quantization not in QUANTIZATIONS:
        raise StaticExportError(f"quantization must be one of {', '.join(QUANTIZATIONS)}", "option")
    if passages_per_file < 1:
        raise StaticExportError("passages_per_file must be at least 1", "option")

    # An open indexer may hold vectors not yet written to disk.
    cached = indexer_manager._indexers.get(collection_id)
    if cached is not None:
        try:
            cached.vector_store.save()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Static export: could not flush the open index for {collection_id}: {e}")

    survey = _survey(collection_id)
    conn: sqlite3.Connection = survey["conn"]
    try:
        if survey["blockers"]:
            first = survey["blockers"][0]
            raise StaticExportError(first["message"], first["code"])
        if not confirm_public:
            raise StaticExportError(
                "Confirm that everything in this collection is cleared for public release: the text of every "
                "passage is published to every visitor of the site.", "confirm")

        collection, model, browser = survey["collection"], survey["model"], survey["browser"]
        semantic = browser is not None
        documents = survey["documents"]
        base = (clean_public_url(files_base_url) if files_base_url is not ...
                else address_settings(collection_id)["files_base_url"])
        repositories = _repositories(collection_id)
        links = {d["document_id"]: document_link(d, base, repositories) for d in documents}
        titles = _library_titles(collection_id, [d["document_id"] for d in documents])
        runtime = model_files = None
        if site and semantic:
            runtime, model_files = site_runtime(), site_model(browser["clio_model"])
            if runtime is None or model_files is None:
                raise StaticExportError(_site_offer(browser)["reason"], "site")
        data_dir = out_dir / SITE_DATA_DIR if site else out_dir
        rows_by_id: Dict[int, int] = {}
        matrix = np.zeros((0, 0), dtype=np.float32)
        dimension = 0
        if semantic:
            rows_by_id, matrix = _read_vectors(survey["indexes_dir"] / "faiss.index")
            dimension = int(matrix.shape[1])
            if survey["info"].get("dimension") and int(survey["info"]["dimension"]) != dimension:
                raise StaticExportError("The recorded model does not match the index dimension. Re-index the "
                                        "collection, then export.", "vectors")

        # A file read from a repository keeps its line range, which a link to
        # the file's page can point at.
        chunk_columns = {r[1] for r in conn.execute("PRAGMA table_info(chunks)")}
        lines_sql = ", c.line_start, c.line_end" if {"line_start", "line_end"} <= chunk_columns else ""
        marks = ",".join("?" * len(documents))
        cursor = conn.execute(
            f"SELECT c.id, c.chunk_id, c.document_id, c.page_number, c.text{lines_sql} FROM chunks c "
            f"JOIN documents d ON d.document_id = c.document_id WHERE c.document_id IN ({marks}) "
            f"ORDER BY d.filename, c.document_id, c.page_number, c.chunk_index, c.chunk_id",
            [d["document_id"] for d in documents])

        total = survey["passages"]
        file_count = max(1, -(-total // passages_per_file))
        data_dir.mkdir(parents=True, exist_ok=True)
        order: List[int] = []
        exported = 0
        with_lines = False
        missing = 0
        per_document: Dict[str, int] = {}
        passage_files: List[Dict[str, Any]] = []
        written: List[Path] = []
        try:
            for number in range(1, file_count + 1):
                name = _passage_file_name(number, file_count)
                digest = hashlib.sha256()
                count = 0
                with open(data_dir / name, "wb") as f:
                    written.append(data_dir / name)

                    def put(data: bytes) -> None:
                        digest.update(data)
                        f.write(data)

                    put(b"[\n")
                    for row in cursor.fetchmany(passages_per_file):
                        if semantic:
                            if row["id"] not in rows_by_id:
                                missing += 1
                                continue
                            order.append(rows_by_id[row["id"]])
                        exported += 1
                        per_document[row["document_id"]] = per_document.get(row["document_id"], 0) + 1
                        entry = {"id": row["chunk_id"], "document_id": row["document_id"],
                                 "page": row["page_number"], "text": row["text"]}
                        if lines_sql and row["line_start"] is not None:
                            entry["lines"] = [row["line_start"], row["line_end"] or row["line_start"]]
                            with_lines = True
                        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
                        put((",\n" if count else "") .encode() + line.encode("utf-8"))
                        count += 1
                    put(b"\n]\n")
                passage_files.append({"file": name, "count": count, "sha256": digest.hexdigest()})
            if missing:
                raise StaticExportError(
                    f"{missing} passage{' has' if missing == 1 else 's have'} no vector yet. Open the collection "
                    "in Clio so it can finish embedding, then export.", "vectors")

            if semantic:
                data, scale = quantize(matrix[np.asarray(order, dtype=np.int64)] if order
                                       else np.zeros((0, dimension), dtype=np.float32), quantization)
                (data_dir / VECTORS_FILE).write_bytes(data)
                written.append(data_dir / VECTORS_FILE)
                embedding, vectors = _semantic_entries(browser, model, dimension, site, runtime, model_files), {
                    "file": VECTORS_FILE, "count": len(order), "dimension": dimension,
                    "quantization": quantization, "dtype": "int8" if quantization == "int8" else "float32le",
                    "scale": scale, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                }
            else:
                # A previous export's vectors would no longer match these passages.
                (data_dir / VECTORS_FILE).unlink(missing_ok=True)
                embedding = {"model": model["model"], "signature": model.get("signature"), "browser": None,
                             "probe": None, "search": "keywords", "reason": survey["keywords_reason"]}
                vectors = None
            manifest = {
                "format": FORMAT,
                "version": VERSION if semantic else KEYWORD_VERSION,
                "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "clio_version": _clio_version(),
                "collection": {"name": collection.get("name"), "description": collection.get("description") or ""},
                "embedding": embedding,
                "vectors": vectors,
                "passages": {"count": exported,
                             "fields": ["id", "document_id", "page", "text"] + (["lines"] if with_lines else []),
                             "files": passage_files},
                "documents": [_document_entry(d, per_document.get(d["document_id"], 0), links[d["document_id"]],
                                              titles.get(d["document_id"]))
                              for d in documents],
            }
            (data_dir / MANIFEST_FILE).write_text(_manifest_text(manifest), encoding="utf-8", newline="\n")
            written.append(data_dir / MANIFEST_FILE)
            if site:
                written += _write_site(out_dir, runtime, browser["clio_model"] if semantic else None, model_files)
        except Exception:
            for path in written:
                path.unlink(missing_ok=True)
            raise
        return manifest, {
            "documents": len(documents), "passages": exported, "model": model["model"],
            "search": "meaning" if semantic else "keywords",
            "quantization": quantization if semantic else None,
            "vectors_sha256": vectors["sha256"] if vectors else None,
            "linked_documents": sum(1 for link in links.values() if link),
            "held_excluded": survey["held"], "not_public_excluded": survey["not_public"],
            "confirmed_public": True, "site": site,
        }
    finally:
        conn.close()


def _semantic_entries(browser, model, dimension, site, runtime, model_files) -> Dict[str, Any]:
    """The manifest's ``embedding`` for an export searched by meaning."""
    from services.embedder import retrieval_prompts

    query_prefix, _ = retrieval_prompts(browser["clio_model"])
    browser_entry: Dict[str, Any] = {"library": "@huggingface/transformers", "model": browser["model"],
                                     "dtype": "fp32", "also_matches": ["fp16"]}
    if site:
        # The page loads this folder (models/<model>/) and the runtime
        # beside it, not the hub build named above.
        browser_entry["bundled"] = {
            "model": browser["clio_model"], "source": model_files["source"],
            "license": model_files["license"],
            "sha256": _sha256_file(model_files["files"]["onnx/model.onnx"]),
            "runtime": runtime["info"].get("packages", {}),
        }
    return {
        "model": browser["clio_model"],
        "signature": model["signature"],
        "dimension": dimension,
        "pooling": browser["pooling"],
        "normalization": "l2",
        "metric": "inner_product",
        "query_prefix": query_prefix or "",
        "search": "meaning",
        # What a page loads to embed a query the same way.
        "browser": browser_entry,
        "probe": _probe(browser["clio_model"], dimension),
    }


def _title(filename: str) -> str:
    """A readable name from a file name: no extension, and in a name with no
    spaces (leave-policy_2024), hyphens and underscores read as spaces."""
    stem = Path(filename or "").stem
    if " " not in stem:
        stem = re.sub(r"[-_]+", " ", stem)
    return re.sub(r"\s+", " ", stem).strip() or filename


def _library_titles(collection_id: str, document_ids: List[str]) -> Dict[str, str]:
    """Titles the Library holds for these sources, where it is sure of them:
    one marked "needs checking" is left out, and the file name is shown."""
    from services.collection_service import collection_service
    from services.research import library
    from services.research.store import ResearchStore

    try:
        # The store itself, not an indexer: the export never opens one.
        store = ResearchStore(collection_service.get_indexes_path(collection_id) / "metadata.db")
        papers = store.get_papers(document_ids)
    except Exception as e:  # noqa: BLE001 - a nicer title, never a reason to fail
        logger.info(f"Static export: no Library titles for {collection_id}: {e}")
        return {}
    titles = {}
    for doc_id, paper in (papers or {}).items():
        title = str((paper or {}).get("title") or "").strip()
        if title and "title" not in library.needs_checking(paper):
            titles[doc_id] = title
    return titles


def _document_entry(doc: Dict[str, Any], passages: int, link: Optional[Dict[str, Any]],
                    title: Optional[str] = None) -> Dict[str, Any]:
    entry = {"id": doc["document_id"], "name": doc["filename"], "title": title or _title(doc["filename"]),
             "pages": doc.get("num_pages"), "passages": passages}
    if link:
        entry["link"] = link
    return entry


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_site(out_dir: Path, runtime: Optional[Dict[str, Any]], clio_model: Optional[str],
                model_files: Optional[Dict[str, Any]]) -> List[Path]:
    """Put the page, the element, the web server settings, the runtime and the
    model beside search/. A keyword-only site has no runtime or model.
    Returns what it wrote."""
    written: List[Path] = []

    def put(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        written.append(target)
        shutil.copyfile(source, target)

    for name in SITE_PAGE_FILES:
        put(SITE_PAGE_DIR / name, out_dir / name)
    for name in SITE_HOSTING_FILES:
        put(SITE_HOSTING_DIR / name, out_dir / name)
    if runtime is None or clio_model is None or model_files is None:
        return written
    for name in runtime["files"]:
        put(runtime["dir"] / name, out_dir / "vendor" / name)
    model_dir = out_dir / "models" / Path(*clio_model.split("/"))
    for name, source in model_files["files"].items():
        put(source, model_dir / name)
    note = model_dir / "SOURCE.txt"
    written.append(note)
    note.write_text(
        f"{clio_model}: the ONNX export Clio embeds with, copied from {model_files['source']} on the Hugging "
        f"Face hub. Licence: {model_files['license'] or 'see the model card'}. The site embeds queries with "
        f"this file, so its vectors match the export's.\n", encoding="utf-8", newline="\n")
    return written


def export_zip(collection_id: str, **options: Any) -> Tuple[Path, str, Dict[str, Any]]:
    """The export as one zip. Returns (zip path, download name, audit detail).

    The caller deletes the zip once it has been sent.
    """
    work = _work_dir() / f"static-{uuid.uuid4().hex}"
    work.mkdir()
    try:
        manifest, detail = write_export(collection_id, work, **options)
        zip_path = _work_dir() / f"static-{uuid.uuid4().hex}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if options.get("site"):
                for path in sorted(p for p in work.rglob("*") if p.is_file()):
                    zf.write(path, path.relative_to(work).as_posix())
            else:
                names = [MANIFEST_FILE, *[f["file"] for f in manifest["passages"]["files"]]]
                for name in names + ([VECTORS_FILE] if manifest["vectors"] else []):
                    zf.write(work / name, name)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    name = manifest["collection"]["name"] or collection_id
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in name).strip("-")
    suffix = "static-site" if options.get("site") else "static-search"
    return zip_path, f"{slug or collection_id}.{suffix}.zip", detail


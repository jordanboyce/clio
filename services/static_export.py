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
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from services.collection_bundle import _clio_version, _work_dir, describe_model

logger = logging.getLogger(__name__)

FORMAT = "clio-static-search"
VERSION = 1

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
SITE_PAGE_FILES = ("index.html", "clio-static-search.js")
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
    ``not_public``, ``model``, ``vectors``, ``confirm``, ``option``, ``site``.
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


def _site_offer(browser: Optional[Dict[str, str]]) -> Dict[str, Any]:
    """Whether a ready-to-serve site can be exported here, and its extra size."""
    if browser is None:
        return {"available": False, "reason": None}
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
        browser = browser_model(model["model"]) if model["provider"] == "local" else None
        if not model["recorded"]:
            blockers.append({"code": "model", "message": (
                "This collection was indexed before Clio recorded its embedding model, so nothing can say "
                "which model a website must use. Re-index it, then export.")})
        elif browser is None:
            where = "" if model["provider"] == "local" else f" ({model['provider']})"
            blockers.append({"code": "model", "message": (
                f"This collection was embedded with {model['model'] or 'an unknown model'}{where}, which has no "
                f"browser build Clio has checked. A website has to embed each query with the same model, so "
                f"clone or re-index the collection with {_supported()} and export that.")})

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
        "info": info, "model": model, "browser": browser,
        "passages": int(passages), "text_bytes": int(text_bytes),
    }


def preview(collection_id: str, passages_per_file: int = DEFAULT_PASSAGES_PER_FILE) -> Dict[str, Any]:
    """What a static export would hold, and what stands in its way."""
    survey = _survey(collection_id)
    survey["conn"].close()
    dimension = int(survey["info"].get("dimension") or 0)
    passages = survey["passages"]
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
                 site: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Write manifest.json, the passage file(s) and vectors.bin into ``out_dir``.

    With ``site`` they go into ``out_dir/search`` instead, beside the
    reference page, the browser runtime under vendor/ and the model under
    models/: a folder any static web server can serve as it is, which loads
    nothing from anywhere else.

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
        documents = survey["documents"]
        runtime = model_files = None
        if site:
            runtime, model_files = site_runtime(), site_model(browser["clio_model"])
            if runtime is None or model_files is None:
                raise StaticExportError(_site_offer(browser)["reason"], "site")
        data_dir = out_dir / SITE_DATA_DIR if site else out_dir
        rows_by_id, matrix = _read_vectors(survey["indexes_dir"] / "faiss.index")
        dimension = int(matrix.shape[1])
        if survey["info"].get("dimension") and int(survey["info"]["dimension"]) != dimension:
            raise StaticExportError("The recorded model does not match the index dimension. Re-index the "
                                    "collection, then export.", "vectors")

        marks = ",".join("?" * len(documents))
        cursor = conn.execute(
            f"SELECT c.id, c.chunk_id, c.document_id, c.page_number, c.text FROM chunks c "
            f"JOIN documents d ON d.document_id = c.document_id WHERE c.document_id IN ({marks}) "
            f"ORDER BY d.filename, c.document_id, c.page_number, c.chunk_index, c.chunk_id",
            [d["document_id"] for d in documents])

        total = survey["passages"]
        file_count = max(1, -(-total // passages_per_file))
        data_dir.mkdir(parents=True, exist_ok=True)
        order: List[int] = []
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
                        if row["id"] not in rows_by_id:
                            missing += 1
                            continue
                        order.append(rows_by_id[row["id"]])
                        per_document[row["document_id"]] = per_document.get(row["document_id"], 0) + 1
                        line = json.dumps({"id": row["chunk_id"], "document_id": row["document_id"],
                                           "page": row["page_number"], "text": row["text"]},
                                          ensure_ascii=False, separators=(",", ":"))
                        put((",\n" if count else "") .encode() + line.encode("utf-8"))
                        count += 1
                    put(b"\n]\n")
                passage_files.append({"file": name, "count": count, "sha256": digest.hexdigest()})
            if missing:
                raise StaticExportError(
                    f"{missing} passage{' has' if missing == 1 else 's have'} no vector yet. Open the collection "
                    "in Clio so it can finish embedding, then export.", "vectors")

            data, scale = quantize(matrix[np.asarray(order, dtype=np.int64)] if order
                                   else np.zeros((0, dimension), dtype=np.float32), quantization)
            (data_dir / VECTORS_FILE).write_bytes(data)
            written.append(data_dir / VECTORS_FILE)

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
            manifest = {
                "format": FORMAT,
                "version": VERSION,
                "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "clio_version": _clio_version(),
                "collection": {"name": collection.get("name"), "description": collection.get("description") or ""},
                "embedding": {
                    "model": browser["clio_model"],
                    "signature": model["signature"],
                    "dimension": dimension,
                    "pooling": browser["pooling"],
                    "normalization": "l2",
                    "metric": "inner_product",
                    "query_prefix": query_prefix or "",
                    # What a page loads to embed a query the same way.
                    "browser": browser_entry,
                    "probe": _probe(browser["clio_model"], dimension),
                },
                "vectors": {
                    "file": VECTORS_FILE, "count": len(order), "dimension": dimension,
                    "quantization": quantization, "dtype": "int8" if quantization == "int8" else "float32le",
                    "scale": scale, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                },
                "passages": {"count": len(order), "fields": ["id", "document_id", "page", "text"],
                             "files": passage_files},
                "documents": [{"id": d["document_id"], "name": d["filename"], "pages": d.get("num_pages"),
                               "passages": per_document.get(d["document_id"], 0)} for d in documents],
            }
            (data_dir / MANIFEST_FILE).write_text(_manifest_text(manifest), encoding="utf-8", newline="\n")
            written.append(data_dir / MANIFEST_FILE)
            if site:
                written += _write_site(out_dir, runtime, browser["clio_model"], model_files)
        except Exception:
            for path in written:
                path.unlink(missing_ok=True)
            raise
        return manifest, {
            "documents": len(documents), "passages": len(order), "model": browser["clio_model"],
            "quantization": quantization, "vectors_sha256": manifest["vectors"]["sha256"],
            "held_excluded": survey["held"], "not_public_excluded": survey["not_public"],
            "confirmed_public": True, "site": site,
        }
    finally:
        conn.close()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_site(out_dir: Path, runtime: Dict[str, Any], clio_model: str,
                model_files: Dict[str, Any]) -> List[Path]:
    """Put the page, the runtime and the model beside search/. Returns what it wrote."""
    written: List[Path] = []

    def put(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        written.append(target)
        shutil.copyfile(source, target)

    for name in SITE_PAGE_FILES:
        put(SITE_PAGE_DIR / name, out_dir / name)
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
                for name in [MANIFEST_FILE, *[f["file"] for f in manifest["passages"]["files"]], VECTORS_FILE]:
                    zf.write(work / name, name)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    name = manifest["collection"]["name"] or collection_id
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in name).strip("-")
    suffix = "static-site" if options.get("site") else "static-search"
    return zip_path, f"{slug or collection_id}.{suffix}.zip", detail


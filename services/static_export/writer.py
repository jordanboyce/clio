"""Writing an export: the passage files, vectors.bin, the manifest, the zip."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from services.collection_bundle import _clio_version
from services.static_export import additions, changes
from services.static_export.common import (
    DEFAULT_PASSAGES_PER_FILE, FORMAT, KEYWORD_VERSION, MANIFEST_FILE, PASSAGES_FILE, QUANTIZATIONS,
    SITE_DATA_DIR, VECTORS_FILE, VERSION, StaticExportError,
)
from services.static_export.addresses import address_settings, clean_public_url, document_link
from services.static_export.vectors import _read_vectors, _semantic_entries, quantize
from services.static_export.site import _write_site
from services.static_export.sources import _document_entry

logger = logging.getLogger(__name__)


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


def write_export(collection_id: str, out_dir: Path, *, confirm_public: bool = False,
                 quantization: str = "int8",
                 passages_per_file: int = DEFAULT_PASSAGES_PER_FILE,
                 site: bool = False,
                 files_base_url: Any = ...,
                 include: Any = (),
                 previous: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Write manifest.json, the passage file(s) and vectors.bin into ``out_dir``.

    With ``site`` they go into ``out_dir/search`` instead, beside the
    reference page, the <clio-search> element, web server settings, the
    browser runtime under vendor/ and the model under models/: a folder any
    static web server can serve as it is, which loads nothing from anywhere
    else.

    A collection whose model has no browser build is written for keyword
    search: no vectors.bin, and a manifest of format v2 saying why.
    ``files_base_url`` overrides the saved base address for files for this
    export only (see address_settings). ``include`` names the additions to
    write beside the files (services/static_export/additions.py). ``previous``
    is the manifest of the export being replaced, when there is one; without
    it the changes are told against what the server remembers of the last.

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
    chosen = additions.parse_include(include)

    # An open indexer may hold vectors not yet written to disk.
    cached = indexer_manager._indexers.get(collection_id)
    if cached is not None:
        try:
            cached.vector_store.save()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Static export: could not flush the open index for {collection_id}: {e}")

    survey = _pkg()._survey(collection_id)
    conn: sqlite3.Connection = survey["conn"]
    try:
        if survey["blockers"]:
            first = survey["blockers"][0]
            raise StaticExportError(first["message"], first["code"])
        if not confirm_public:
            raise StaticExportError(
                "Confirm that everything in this collection is cleared for public release: the text of every "
                "passage is published to every visitor of the site.", "confirm")
        chosen = additions.resolve(chosen, collection_id, survey, site)

        collection, model, browser = survey["collection"], survey["model"], survey["browser"]
        semantic = browser is not None
        documents = survey["documents"]
        base = (clean_public_url(files_base_url) if files_base_url is not ...
                else address_settings(collection_id)["files_base_url"])
        repositories = _pkg()._repositories(collection_id)
        links = {d["document_id"]: document_link(d, base, repositories) for d in documents}
        titles = _pkg()._library_titles(collection_id, [d["document_id"] for d in documents])
        runtime = model_files = None
        if site and semantic:
            runtime, model_files = _pkg().site_runtime(), _pkg().site_model(browser["clio_model"])
            if runtime is None or model_files is None:
                raise StaticExportError(_pkg()._site_offer(browser)["reason"], "site")
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
        kept: Optional[List[Dict[str, Any]]] = [] if chosen else None
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
                        if kept is not None:
                            kept.append(entry)
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
                ordered = (matrix[np.asarray(order, dtype=np.int64)] if order
                           else np.zeros((0, dimension), dtype=np.float32))
                data, scale = quantize(ordered, quantization)
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
                ordered = None
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
            if chosen:
                ctx = additions.ExportContext(
                    collection_id=collection_id, survey=survey, data_dir=data_dir, site=site, manifest=manifest,
                    passages=kept or [], vectors=ordered, settings=address_settings(collection_id),
                    written=written, root_dir=out_dir,
                    previous=changes.from_manifest(previous) or changes.remembered(collection_id))
                manifest["additions"] = additions.write_all(chosen, ctx)
            (data_dir / MANIFEST_FILE).write_text(_manifest_text(manifest), encoding="utf-8", newline="\n")
            written.append(data_dir / MANIFEST_FILE)
            if site:
                written += _write_site(out_dir, runtime, browser["clio_model"] if semantic else None, model_files)
            # What the next export is compared with (ids, names, titles; never text).
            changes.remember(collection_id, manifest, {d["document_id"]: d.get("superseded_by") for d in documents})
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
            "confirmed_public": True, "site": site, "include": chosen,
        }
    finally:
        conn.close()


def _pkg():
    """The package, for functions a caller (a test) may have replaced there."""
    from services import static_export

    return static_export

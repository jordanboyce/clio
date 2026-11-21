"""Export a collection as a portable bundle, and import one.

A bundle is a zip:

    manifest.json           what this is, how it was built, what it holds
    indexes/metadata.db     chunk text, pages, document records, CSV tables
    indexes/faiss.index     the vectors (omitted when they cannot be vouched for)
    sources/<doc id>/<name> the original files (optional)
    figures/<doc id>/<n>.<ext> pictures extracted from the documents
    artifacts/<id>/<name>   files of saved artifacts, such as audio

The chunk text is the valuable part. Extraction - vision OCR above all - is
what takes hours; embedding chunk text takes minutes. So an importer embeds
the chunk text again without redoing extraction. The shipped vectors are for
older importers only: this one never loads them, since a FAISS file is parsed
by native code and vectors need not match the text they claim to stand for.
The keyword (BM25) index is always rebuilt on import from the same text
rather than shipped.

On import, what the bundle says about its documents is decided again here:
the content-policy, personal-data and injection scans run over the imported
text, reviews and holds from elsewhere are dropped, the importer is recorded
as who added each document, hashes and sizes come from the files actually
unpacked, and the storage limit counts those bytes. The shipped metadata.db
keeps its data and loses its code (triggers, foreign views).

Quarantined documents never leave: they are stripped from the exported
database and vectors, and so is what was derived from them: research whose
briefs and evidence quote the paper, extracted entities, reviewer notes,
coverage entries and saved artifacts (their rows travel in metadata.db) built
from it. On import, documents whose hash this deployment has blocklisted are
stripped the same way. A document deleted earlier counts too: what was derived
from it, reports included, stays behind.
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
import zlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Tuple

import faiss
import numpy as np

from config import settings
from services import artifact_store, authored_text, figures
from services.research.store import prune_orphan_jobs, prune_orphan_rows

logger = logging.getLogger(__name__)

BUNDLE_FORMAT = "clio-collection"
BUNDLE_VERSION = 3
# A bundle says the lowest version that can read it: 1 without artifact or
# figure files (every release reads those), 2 with artifact files, 3 with
# figure files.
_BASE_BUNDLE_VERSION = 1
_ARTIFACT_BUNDLE_VERSION = 2

_MANIFEST = "manifest.json"
_METADATA = "indexes/metadata.db"
_VECTORS = "indexes/faiss.index"
_SOURCES = "sources/"
_FIGURES = "figures/"
_ARTIFACT_FILES = "artifacts/"


class BundleError(ValueError):
    """The bundle is unusable; the message says why in plain words."""


def _clio_version() -> str:
    try:
        from main import app

        return str(app.version)
    except Exception:  # pragma: no cover - only absent outside the server
        return ""


def _work_dir() -> Path:
    # On the data volume, not the container's /tmp: bundles with sources can
    # be gigabytes.
    path = Path(settings.data_dir) / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ── Shared: stripping documents out of a metadata.db copy ────────────────────


def _names_absent_document(value: Any, present: set) -> bool:
    """Whether any ``document_id`` anywhere in parsed JSON is not in ``present``."""
    if isinstance(value, dict):
        if value.get("document_id") and value["document_id"] not in present:
            return True
        return any(_names_absent_document(v, present) for v in value.values())
    if isinstance(value, list):
        return any(_names_absent_document(v, present) for v in value)
    return False


def _sweep_derived(conn: sqlite3.Connection, stripped: bool) -> None:
    """Clear what a metadata.db still holds about documents no longer in it.

    Takes each table only if the database has it: these all arrived after the
    first bundles. The coverage report goes whole, and so does an owner's
    question that names an absent document. Neither can be filtered: a
    report's probe queries are built from the document's text and its totals
    count it, and the question was written about it. An owner can run the
    report and write the question again; a withheld paper cannot be recalled.
    """
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    for table in ("chunk_entities", "chunk_boosts"):
        if table in have:
            conn.execute(f"DELETE FROM {table} WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)")
    if "document_figures" in have:
        conn.execute("DELETE FROM document_figures "
                     "WHERE document_id NOT IN (SELECT document_id FROM documents)")
    if "superseded_by" in {r[1] for r in conn.execute("PRAGMA table_info(documents)")}:
        # As on deletion: what this document replaced is current again.
        conn.execute("UPDATE documents SET superseded_by = NULL, superseded_at = NULL, "
                     "superseded_note = NULL "
                     "WHERE superseded_by NOT IN (SELECT document_id FROM documents)")

    present = {r[0] for r in conn.execute("SELECT document_id FROM documents")}

    def unvouched(text: Any) -> bool:
        try:
            return _names_absent_document(json.loads(text), present)
        except (TypeError, ValueError):
            return True  # unreadable, so nothing can say what it names

    if "coverage_questions" in have:
        rows = conn.execute("SELECT id, expected_documents FROM coverage_questions").fetchall()
        conn.executemany("DELETE FROM coverage_questions WHERE id = ?",
                         [(qid,) for qid, expected in rows if unvouched(expected)])
    if "coverage_report" in have:
        # After a strip the report goes even if it never names the document:
        # a future field that quotes a chunk should not need this code to know.
        reports = conn.execute("SELECT report FROM coverage_report").fetchall()
        if stripped or any(unvouched(r[0]) for r in reports):
            conn.execute("DELETE FROM coverage_report")


def _strip_documents(db_path: Path, document_ids: Iterable[str]) -> List[int]:
    """Delete documents (chunks, records, CSV tables) from a metadata.db,
    along with what was derived from any document no longer in it.

    The sweep runs even when ``document_ids`` is empty: a document deleted
    earlier leaves research rows and coverage entries behind, and a bundle
    from before this sweep holds them for documents it stripped.

    Returns the chunk row ids removed - their vectors must go too.
    """
    ids = sorted(set(document_ids))
    marks = ",".join("?" * len(ids))
    conn = _connect_untrusted(db_path, write=True)
    try:
        rowids: List[int] = []
        if ids:
            rowids = [r[0] for r in conn.execute(
                f"SELECT id FROM chunks WHERE document_id IN ({marks})", ids)]
            conn.execute(f"DELETE FROM chunks WHERE document_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM documents WHERE document_id IN ({marks})", ids)
            try:
                conn.execute(f"DELETE FROM document_figures WHERE document_id IN ({marks})", ids)
            except sqlite3.OperationalError:
                pass  # pre-3.9 database without figures
            try:
                tables = [r[0] for r in conn.execute(
                    f"SELECT table_name FROM csv_schemas WHERE document_id IN ({marks})", ids)]
                for table in tables:
                    conn.execute(f"DROP VIEW IF EXISTS {_quote_identifier(f'{table}__by_symbol')}")
                    conn.execute(f"DROP TABLE IF EXISTS {_quote_identifier(table)}")
                conn.execute(f"DELETE FROM csv_schemas WHERE document_id IN ({marks})", ids)
            except sqlite3.OperationalError:
                pass  # no structured tables in this collection
        prune_orphan_rows(conn)
        prune_orphan_jobs(conn)
        _sweep_derived(conn, stripped=bool(ids))
        conn.commit()
        return rowids
    finally:
        conn.close()


def _documents(db_path: Path) -> List[Dict[str, Any]]:
    conn = _connect_untrusted(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM documents ORDER BY filename")]
    finally:
        conn.close()


def _figure_rows(db_path: Path) -> List[Dict[str, Any]]:
    """The document_figures rows, or [] for a database from before them."""
    conn = _connect_untrusted(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT document_id, figure_number, path, byte_size FROM document_figures "
            "ORDER BY document_id, figure_number")]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _chunk_count(db_path: Path) -> int:
    conn = _connect_untrusted(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        conn.close()


def _index_info(db_path: Path) -> Dict[str, Any]:
    conn = _connect_untrusted(db_path)
    try:
        rows = conn.execute("SELECT key, value FROM index_info").fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    return {k: json.loads(v) for k, v in rows}


def _static_addresses(collection_id: str) -> Optional[Dict[str, Any]]:
    """The static export's saved addresses, or None when none are set."""
    try:
        from services.static_export import address_settings

        saved = address_settings(collection_id)
    except Exception:  # noqa: BLE001 - a nicety of the bundle, never a reason to fail
        return None
    return saved if any(saved.values()) else None


def _static_answers(collection_id: str) -> Optional[List[Dict[str, Any]]]:
    try:
        from services.static_export import answers

        return answers.carried(collection_id)
    except Exception:  # noqa: BLE001
        return None


def describe_model(info: Dict[str, Any], documents: Iterable[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Say, in words a person can check, which embedding model made the vectors.

    ``info`` is the collection's recorded index_info (or the manifest's
    ``embedding`` block). Older collections never recorded it, so fall back to
    the per-document ``embedding_model`` and say how sure we are.
    """
    signature = info.get("signature") or None
    model = info.get("model")
    if not model and signature:
        model = signature.split("::")[-1]
    if not model:
        names = sorted({d.get("embedding_model") for d in documents if d.get("embedding_model")})
        model = names[0] if len(names) == 1 else None
    provider = signature.split("::")[0] if signature else None
    dimension = info.get("dimension")
    where = None if not provider else ("on this machine" if provider == "local" else f"via {provider}")
    bits = [model or "unknown model"]
    if where:
        bits.append(where)
    if dimension:
        bits.append(f"{dimension} dimensions")
    return {
        "model": model,
        "provider": provider,
        "dimension": dimension,
        "signature": signature,
        "recorded": bool(signature),
        "label": " · ".join(bits),
    }


# ── Export ───────────────────────────────────────────────────────────────────


def export_collection(collection_id: str, include_sources: bool = False,
                      exported_by: Optional[str] = None) -> Tuple[Path, str]:
    """Write a bundle for a collection. Returns (zip path, download name).

    The caller deletes the zip once it has been sent.
    """
    from services import governance
    from services.collection_service import collection_service
    from services.indexer_manager import indexer_manager

    collection = collection_service.get_collection(collection_id)
    if not collection:
        raise BundleError(f"Collection '{collection_id}' not found")

    indexes_dir = collection_service.get_indexes_path(collection_id)
    documents_dir = collection_service.get_documents_path(collection_id)
    src_db = indexes_dir / "metadata.db"
    if not src_db.exists():
        raise BundleError("This collection has nothing indexed to export yet.")

    # An open indexer may hold vectors not yet written to disk.
    cached = indexer_manager._indexers.get(collection_id)
    if cached is not None:
        try:
            cached.vector_store.save()
        except Exception as e:
            logger.warning(f"Export: could not flush the open index for {collection_id}: {e}")

    work = _work_dir() / f"export-{uuid.uuid4().hex}"
    work.mkdir()
    try:
        # A consistent snapshot even while the collection is in use (WAL).
        db_copy = work / "metadata.db"
        src = sqlite3.connect(src_db)
        dst = sqlite3.connect(db_copy)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        hidden = [d["document_id"] for d in _documents(db_copy) if governance.is_hidden(d)]
        removed_rowids = _strip_documents(db_copy, hidden)
        # Before VACUUM, so what this removes is gone from the file too.
        artifacts = artifact_store.prune_for_bundle(db_copy, artifact_store.load_blocked_hashes())
        conn = sqlite3.connect(db_copy)
        conn.execute("VACUUM")
        conn.close()

        documents = _documents(db_copy)
        chunks = _chunk_count(db_copy)
        info = _index_info(db_copy)

        # Vectors ship only when they provably line up with the chunks.
        vectors_path: Optional[Path] = None
        vectors_note = None
        faiss_src = indexes_dir / "faiss.index"
        if faiss_src.exists():
            index = faiss.read_index(str(faiss_src))
            if removed_rowids:
                index.remove_ids(np.asarray(removed_rowids, dtype=np.int64))
            if index.ntotal != chunks:
                vectors_note = (f"left out: the index holds {index.ntotal} vectors for "
                                f"{chunks} chunks, so it is out of step")
            elif info.get("dimension") and int(info["dimension"]) != index.d:
                vectors_note = "left out: the recorded model does not match the index dimension"
            else:
                vectors_path = work / "faiss.index"
                faiss.write_index(index, str(vectors_path))
                info.setdefault("dimension", index.d)
        if not info.get("signature"):
            # Indexed before models were recorded: say what we can, but an
            # importer must re-embed because nothing proves the match.
            models = sorted({d.get("embedding_model") for d in documents if d.get("embedding_model")})
            info = {
                "signature": None,
                "model": models[0] if len(models) == 1 else None,
                "dimension": info.get("dimension"),
                "note": "Recorded before this version of Clio tracked the embedding "
                        "model; importers will re-embed from the chunk text.",
            }

        sources: Dict[str, str] = {}
        if include_sources:
            for doc in documents:
                if doc.get("source_type") == "local_reference" and doc.get("source_path"):
                    path = Path(doc["source_path"])
                else:
                    path = documents_dir / doc["filename"]
                if path.is_file():
                    sources[doc["document_id"]] = str(path)

        files_store = artifact_store.ArtifactStore(db_copy, collection_service.get_artifacts_path(collection_id))
        artifact_files = {row["id"]: path for row in artifacts if (path := files_store.file_path(row))}

        # Figures of the documents still in the copy (the strip above took
        # the quarantined ones' rows with them).
        figures_root = collection_service.get_figures_path(collection_id)
        figure_files = [
            (row, path) for row in _figure_rows(db_copy)
            if (path := figures.resolve_figure_file(figures_root, row)) is not None
        ]

        if figure_files:
            version = BUNDLE_VERSION
        elif artifact_files:
            version = _ARTIFACT_BUNDLE_VERSION
        else:
            version = _BASE_BUNDLE_VERSION
        manifest = {
            "format": BUNDLE_FORMAT,
            "version": version,
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "exported_by": exported_by,
            "collection": {
                "id": collection_id,
                "name": collection.get("name"),
                "description": collection.get("description") or "",
                "color": collection.get("color"),
                "guide": collection.get("guide"),
                "sensitivity": governance.collection_sensitivity(collection),
                # Travels with the bundle. A server that does not have this
                # marking treats it as local only (services/data_controls.py).
                "classification": collection.get("classification") or None,
                "chunk_size": collection.get("chunk_size"),
                "chunk_overlap": collection.get("chunk_overlap"),
                # Where its static export links and is served (services/static_export).
                "static_export": _static_addresses(collection_id),
                # Answers to publish with it; imported ones wait for approval there.
                "static_answers": _static_answers(collection_id),
            },
            "embedding": {**info, **{k: v for k, v in describe_model(info, documents).items()
                                     if k in ("provider", "label", "recorded")}},
            "clio_version": _clio_version(),
            "contents": {
                "documents": len(documents),
                "chunks": chunks,
                "vectors_included": vectors_path is not None,
                "vectors_note": vectors_note,
                "sources_included": len(sources),
                "quarantined_excluded": len(hidden),
                "artifacts": len(artifacts),
                "figures": len(figure_files),
            },
            "documents": [
                {
                    "document_id": d["document_id"],
                    "filename": d["filename"],
                    "pages": d.get("num_pages"),
                    "chunks": d.get("num_chunks"),
                    "content_hash": d.get("content_hash"),
                    "sensitivity": d.get("sensitivity"),
                    "source_included": d["document_id"] in sources,
                }
                for d in documents
            ],
        }

        zip_path = _work_dir() / f"export-{uuid.uuid4().hex}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(_MANIFEST, json.dumps(manifest, indent=2))
            zf.write(db_copy, _METADATA)
            if vectors_path is not None:
                zf.write(vectors_path, _VECTORS)
            for document_id, path in sources.items():
                # PDFs and images are already compressed; don't spend CPU.
                zf.write(path, f"{_SOURCES}{document_id}/{Path(path).name}",
                         compress_type=zipfile.ZIP_STORED)
            for artifact_id, path in artifact_files.items():
                zf.write(path, f"{_ARTIFACT_FILES}{artifact_id}/{path.name}",
                         compress_type=zipfile.ZIP_STORED)
            for row, path in figure_files:
                zf.write(path, f"{_FIGURES}{row['document_id']}/{path.name}",
                         compress_type=zipfile.ZIP_STORED)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in (collection.get("name") or collection_id))
    return zip_path, f"{slug.strip('-') or collection_id}.clio.zip"


# ── Import ───────────────────────────────────────────────────────────────────


_MB = 1024 * 1024
# An entry smaller than this cannot hurt however well it compresses (the
# same floor as services/format_readers.check_zip_container).
_RATIO_FLOOR_BYTES = _MB
# Room in the zip's central directory per allowed entry: the fixed 46-byte
# header plus a long file name and a zip64 extra field, with margin.
_DIRECTORY_BYTES_PER_ENTRY = 512


def _bundle_limits() -> Dict[str, int]:
    return {
        "members": max(0, int(settings.bundle_max_members or 0)),
        "total": max(0, int(settings.bundle_max_uncompressed_mb or 0)) * _MB,
        "ratio": max(0, int(settings.archive_max_compression_ratio or 0)),
        "manifest": max(0, int(settings.bundle_max_manifest_mb or 0)) * _MB,
    }


def _check_directory_size(zip_path: Path, limits: Dict[str, int]) -> None:
    """Refuse a bundle with too many entries before zipfile parses its directory.

    ``zipfile.ZipFile`` reads the whole central directory and builds an
    object per entry, so a forged end record can cost memory before any
    entry is looked at. The end record says how many entries there are and
    how large the directory is; both are checked first. ``_EndRecData`` is
    the standard library's own reader of that record (zip64 included).
    """
    if not limits["members"]:
        return
    with open(zip_path, "rb") as fp:
        try:
            end = zipfile._EndRecData(fp)
        except OSError:
            end = None
    if not end:
        raise BundleError("That file is not a Clio collection export (.clio.zip).")
    entries, directory_bytes = end[zipfile._ECD_ENTRIES_TOTAL], end[zipfile._ECD_SIZE]
    if entries > limits["members"] or directory_bytes > limits["members"] * _DIRECTORY_BYTES_PER_ENTRY:
        raise BundleError(
            f"The bundle holds more than {limits['members']:,} entries, more than this server "
            f"imports (BUNDLE_MAX_MEMBERS)."
        )


def _check_entry_sizes(infos: List[zipfile.ZipInfo], limits: Dict[str, int]) -> None:
    """Declared sizes only (``zipfile`` and ``_copy_member`` hold every entry to its own)."""
    total = 0
    for info in infos:
        total += info.file_size
        if limits["total"] and total > limits["total"]:
            raise BundleError(
                f"The bundle unpacks to more than {limits['total'] // _MB:,} MB, more than this "
                f"server imports (BUNDLE_MAX_UNCOMPRESSED_MB)."
            )
        ratio = info.file_size / max(info.compress_size, 1)
        if limits["ratio"] and info.file_size >= _RATIO_FLOOR_BYTES and ratio > limits["ratio"]:
            raise BundleError(
                f"{info.filename} in the bundle is compressed {ratio:.0f} to 1, which only a file "
                f"built to exhaust memory does; this server refuses above {limits['ratio']} to 1 "
                f"(ARCHIVE_MAX_COMPRESSION_RATIO)."
            )


def _read_manifest_bytes(zf: zipfile.ZipFile, limit: int) -> bytes:
    """manifest.json, never more of it than the limit, whatever its entry declares."""
    info = zf.getinfo(_MANIFEST)
    too_big = BundleError(
        f"The bundle's manifest.json is larger than {limit // _MB} MB, more than this server "
        f"reads (BUNDLE_MAX_MANIFEST_MB)."
    )
    if limit and info.file_size > limit:
        raise too_big
    try:
        with zf.open(info) as member:
            data = member.read(limit + 1) if limit else member.read()
    except (zipfile.BadZipFile, zlib.error, EOFError) as e:
        raise BundleError(f"The bundle is damaged: manifest.json cannot be read ({e}).") from e
    if limit and len(data) > limit:
        raise too_big
    return data


def read_manifest(zip_path: Path) -> Dict[str, Any]:
    """Validate a bundle's shape and return its manifest. Raises BundleError.

    The preview and the import both start here, so the bundle limits
    (BUNDLE_MAX_*, ARCHIVE_MAX_COMPRESSION_RATIO) are checked before
    anything is read from the bundle or unpacked.
    """
    if not zipfile.is_zipfile(zip_path):
        raise BundleError("That file is not a Clio collection export (.clio.zip).")
    limits = _bundle_limits()
    _check_directory_size(zip_path, limits)
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        names = [info.filename for info in infos]
        if _MANIFEST not in names or _METADATA not in names:
            raise BundleError("That zip is not a Clio collection export: manifest.json or metadata.db is missing.")
        for name in names:
            _check_member(name)
        _check_entry_sizes(infos, limits)
        raw = _read_manifest_bytes(zf, limits["manifest"])
    try:
        manifest = json.loads(raw)
    except ValueError:
        raise BundleError("The bundle's manifest.json is not valid JSON.")
    if not isinstance(manifest, dict):
        raise BundleError("That zip is not a Clio collection export.")
    if manifest.get("format") != BUNDLE_FORMAT:
        raise BundleError("That zip is not a Clio collection export.")
    if int(manifest.get("version") or 0) > BUNDLE_VERSION:
        raise BundleError(
            f"This bundle was made by a newer Clio (format v{manifest.get('version')}); "
            f"this server reads up to v{BUNDLE_VERSION}. Update Clio to import it."
        )
    return manifest


def _check_member(name: str) -> None:
    """Only the paths a bundle is allowed to hold; nothing that escapes."""
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise BundleError(f"The bundle contains an unsafe path: {name}")
    if name in (_MANIFEST, _METADATA, _VECTORS) or name.endswith("/"):
        return
    if name.startswith((_SOURCES, _FIGURES, _ARTIFACT_FILES)) and len(path.parts) == 3:
        return
    raise BundleError(f"The bundle contains an unexpected file: {name}")


def _figure_members(zf: zipfile.ZipFile, rows: List[dict], kept: Dict[str, Any]) -> List[zipfile.ZipInfo]:
    """The figure files recorded for kept documents; a member the database
    does not name is left in the zip."""
    wanted = {(row["document_id"], PurePosixPath(str(row["path"])).name)
              for row in rows if row["document_id"] in kept}
    members = []
    for info in zf.infolist():
        if not info.filename.startswith(_FIGURES) or info.filename.endswith("/"):
            continue
        _, document_id, filename = PurePosixPath(info.filename).parts
        if (document_id, filename) in wanted:
            members.append(info)
    return members


def _restore_figure_files(zf: zipfile.ZipFile, members: List[zipfile.ZipInfo], figures_root: Path) -> int:
    """Unpack the planned figures, each under its document's folder."""
    for info in members:
        _, document_id, filename = PurePosixPath(info.filename).parts
        target = figures_root / document_id / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        _copy_member(zf, info, target)
    return len(members)


def _artifact_members(zf: zipfile.ZipFile, artifacts: List[dict]) -> List[Tuple[zipfile.ZipInfo, dict]]:
    """The files of the artifacts that were kept, with their rows."""
    by_id = {row["id"]: row for row in artifacts}
    members = []
    for info in zf.infolist():
        if not info.filename.startswith(_ARTIFACT_FILES) or info.filename.endswith("/"):
            continue
        _, artifact_id, filename = PurePosixPath(info.filename).parts
        row = by_id.get(artifact_id)
        if row and row.get("file_name") == filename:
            members.append((info, row))
    return members


def _restore_artifact_files(zf: zipfile.ZipFile, members: List[Tuple[zipfile.ZipInfo, dict]],
                            db_path: Path, files_dir: Path) -> None:
    """Unpack the planned artifact files, each to its own folder."""
    store = artifact_store.ArtifactStore(db_path, files_dir)
    for info, row in members:
        target = store.file_target(row)
        if target is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        _copy_member(zf, info, target)


# ── Import: the shipped metadata.db is someone else's file ──────────────────
#
# Everything in a bundle was written by whoever made it. What this server
# keeps from it is the data (chunk text, pages, tables, figures); what the
# file says about that data is decided again here: its code (triggers, views),
# its verdicts (content policy, injection warnings, reviews), who added each
# document, each document's hash and size, and its vectors.

_STRUCTURED_TABLE = re.compile(r"csv_data_[A-Za-z0-9_]{1,200}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
# Rows of each structured table the policy scan reads, as at ingest.
_SCAN_ROWS = 500
# A declared size past this is nonsense (and past SQLite's integers).
_MAX_DECLARED_SIZE = 1 << 53


def _quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _connect_untrusted(db_path: Path, *, write: bool = False) -> sqlite3.Connection:
    """A connection to a metadata.db that may have come from elsewhere.

    ``trusted_schema=OFF`` stops the file's own schema (views, triggers,
    defaults, generated columns) from calling anything but innocuous
    functions, and the connection is read-only unless the caller changes it.
    """
    uri = f"{Path(db_path).resolve().as_uri()}?mode={'rw' if write else 'ro'}"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA trusted_schema = OFF")
    return conn


def _copy_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path) -> Tuple[int, str]:
    """Unpack one member, never past the size its entry declares (the size the
    storage check counted). Returns (bytes written, sha256 of them)."""
    hasher = hashlib.sha256()
    written = 0
    try:
        with zf.open(info) as src, open(target, "wb") as dst:
            while True:
                block = src.read(1 << 16)
                if not block:
                    break
                written += len(block)
                if written > info.file_size:
                    raise BundleError(f"The bundle is damaged: {info.filename} is larger than it says.")
                hasher.update(block)
                dst.write(block)
    except (zipfile.BadZipFile, zlib.error, EOFError) as e:
        target.unlink(missing_ok=True)
        raise BundleError(f"The bundle is damaged: {info.filename} cannot be read ({e}).") from e
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return written, hasher.hexdigest()


def _sanitise_imported_db(db_path: Path) -> None:
    """Keep a shipped metadata.db's data and none of its code.

    A trigger would run inside this server on every later write (an admin's
    hold could be undone as it is made), so every trigger goes, and every view
    but the by-symbol rollups the structured store makes for its own tables.
    The SQL tool lets a model read exactly the registered ``csv_data_*``
    tables, so a table nobody registered is dropped and a registration that
    names anything else is removed. Chunks that belong to no document go too:
    nothing would govern them. Virtual tables are refused: Clio makes none here.
    """
    try:
        conn = _connect_untrusted(db_path, write=True)
    except sqlite3.Error as e:
        raise BundleError("The bundle's metadata.db is not a readable Clio index.") from e
    try:
        if (conn.execute("PRAGMA quick_check").fetchone() or ("",))[0] != "ok":
            raise BundleError("The bundle's metadata.db is damaged.")
        objects = conn.execute("SELECT type, name, sql FROM sqlite_master").fetchall()
        if any(kind == "table" and str(sql or "").lstrip().upper().startswith("CREATE VIRTUAL")
               for kind, _, sql in objects):
            raise BundleError("The bundle's metadata.db holds tables Clio does not make.")
        tables = {name for kind, name, _ in objects if kind == "table"}
        registered = set()
        if "csv_schemas" in tables:
            for row_id, table in conn.execute("SELECT id, table_name FROM csv_schemas").fetchall():
                if isinstance(table, str) and _STRUCTURED_TABLE.fullmatch(table) and table in tables:
                    registered.add(table)
                else:
                    conn.execute("DELETE FROM csv_schemas WHERE id = ?", (row_id,))
        rollups = {f"{table}__by_symbol" for table in registered}
        for kind, name, _ in objects:
            if kind == "trigger" or (kind == "view" and name not in rollups):
                conn.execute(f"DROP {kind.upper()} IF EXISTS {_quote_identifier(name)}")
            elif kind == "table" and name.lower().startswith("csv_data_") and name not in registered:
                conn.execute(f"DROP TABLE IF EXISTS {_quote_identifier(name)}")
        conn.execute("DELETE FROM chunks WHERE document_id NOT IN (SELECT document_id FROM documents)")
        conn.commit()
    except sqlite3.DatabaseError as e:
        raise BundleError("The bundle's metadata.db is not a readable Clio index.") from e
    finally:
        conn.close()


def _structured_tables(conn: sqlite3.Connection, document_id: str) -> List[str]:
    try:
        rows = conn.execute("SELECT table_name FROM csv_schemas WHERE document_id = ? ORDER BY id",
                            (document_id,)).fetchall()
    except sqlite3.OperationalError:
        return []  # no structured tables in this collection
    return [r[0] for r in rows if isinstance(r[0], str) and _STRUCTURED_TABLE.fullmatch(r[0])]


def _scan_pages_of(conn: sqlite3.Connection, document_id: str) -> Dict[Any, str]:
    """The document's text as the policy scans read it: chunk text by page,
    and for a spreadsheet the first rows of each sheet (as at ingest)."""
    pages: Dict[Any, List[str]] = {}
    for page, text in conn.execute(
            "SELECT page_number, text FROM chunks WHERE document_id = ? ORDER BY page_number, chunk_index",
            (document_id,)):
        pages.setdefault(page, []).append(str(text or ""))
    texts = {page: "\n".join(parts) for page, parts in pages.items()}
    for n, table in enumerate(_structured_tables(conn, document_id), start=len(texts) + 1):
        rows = conn.execute(f"SELECT * FROM {_quote_identifier(table)} LIMIT {_SCAN_ROWS}").fetchall()
        texts[n] = "\n".join(", ".join(str(v) for v in row) for row in rows)
    return texts


def _stored_bytes(conn: sqlite3.Connection, document_id: str) -> int:
    """Bytes of what a document brings with it in metadata.db: chunk text and
    structured rows. The floor for a document that arrives without its file."""
    total = conn.execute("SELECT COALESCE(SUM(LENGTH(CAST(text AS BLOB))), 0) FROM chunks "
                         "WHERE document_id = ?", (document_id,)).fetchone()[0]
    for table in _structured_tables(conn, document_id):
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({_quote_identifier(table)})")]
        if columns:
            sums = ", ".join(f"COALESCE(SUM(LENGTH(CAST({_quote_identifier(c)} AS BLOB))), 0)"
                             for c in columns)
            total += sum(conn.execute(f"SELECT {sums} FROM {_quote_identifier(table)}").fetchone())
    return int(total)


def _declared_size(value: Any) -> int:
    try:
        return max(0, min(int(value or 0), _MAX_DECLARED_SIZE))
    except (TypeError, ValueError):
        return 0


def _plan_sources(zf: zipfile.ZipFile, kept: Dict[str, Any]) -> Dict[str, zipfile.ZipInfo]:
    """The original each kept document gets: its own member, the first one per
    stored filename (two documents sharing a name keep the first)."""
    plan: Dict[str, zipfile.ZipInfo] = {}
    names = set()
    for info in zf.infolist():
        if not info.filename.startswith(_SOURCES) or info.filename.endswith("/"):
            continue
        _, document_id, filename = PurePosixPath(info.filename).parts
        doc = kept.get(document_id)
        stored = Path(doc["filename"]).name if doc else ""
        if not stored or Path(filename).name != stored or document_id in plan or stored in names:
            continue
        plan[document_id] = info
        names.add(stored)
    return plan


def _stricter_label(*labels: Any) -> Optional[str]:
    from services.governance import SENSITIVITY_LEVELS

    known = [label for label in labels if label in SENSITIVITY_LEVELS]
    return max(known, key=SENSITIVITY_LEVELS.index) if known else None


def _rescan(conn: sqlite3.Connection, doc: Dict[str, Any]) -> Dict[str, Any]:
    """The content-policy decision and injection warnings for an imported
    document, made here from its text exactly as ingest makes them."""
    from services import content_policy
    from services.code_extractor import is_code_file
    from services.indexing.indexer import policy_decision
    from services.prompt_injection_detector import PromptInjectionDetector
    from services.prompt_injection_detector import scanning_enabled as injection_scanning_enabled

    filename = str(doc.get("filename") or "")
    pages = _scan_pages_of(conn, doc["document_id"])
    code = is_code_file(filename)
    results = (content_policy.scan_pages(pages, filename=filename, code=code)
               if pages and content_policy.scanning_enabled() else {})
    status, flags, suggested = policy_decision(results, pages, filename=filename)
    injection = None
    if pages and not code and injection_scanning_enabled():
        scans = PromptInjectionDetector().scan_pages(pages, filename=filename)
        injection = {str(page): scan.to_dict() for page, scan in scans.items() if scan.is_flagged} or None
    return {"status": status, "flags": flags, "suggested": suggested, "injection": injection}


def _govern_imported(db_path: Path, kept: Dict[str, Any], sources: Dict[str, Tuple[int, str]],
                     actor: Optional[str]) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    """Decide again everything the bundle said about its documents.

    Each document is scanned (content policy, personal data, injection) from
    its own text; an earlier review, approval or hold from elsewhere does not
    carry over. The importer is who added it. A document that came with its
    file gets that file's hash and size; one that did not keeps a declared
    hash only when it is the hash its id was cut from, and counts at least
    the bytes its text and tables take here. Returns the documents the policy
    rejects (to be stripped) and every kept document's decision.
    """
    rejected: List[str] = []
    decisions: Dict[str, Dict[str, Any]] = {}
    conn = _connect_untrusted(db_path, write=True)
    try:
        for document_id, doc in kept.items():
            decision = _rescan(conn, doc)
            if decision["status"] == "rejected":
                rejected.append(document_id)
                decisions[document_id] = decision
                continue
            if document_id in sources:
                file_size, content_hash = sources[document_id]
            else:
                declared = str(doc.get("content_hash") or "")
                content_hash = declared if (_SHA256.fullmatch(declared)
                                            and declared[:16] == document_id) else None
                file_size = max(_declared_size(doc.get("file_size")), _stored_bytes(conn, document_id))
            decision["file_size"] = file_size
            decisions[document_id] = decision
            flags, injection = decision["flags"], decision["injection"]
            conn.execute(
                "UPDATE documents SET content_hash = ?, file_size = ?, policy_status = ?, "
                "policy_flags = ?, injection_warnings = ?, injection_review = NULL, "
                "uploaded_by = ?, sensitivity = ? WHERE document_id = ?",
                (content_hash, file_size, decision["status"],
                 json.dumps(flags) if flags else None, json.dumps(injection) if injection else None,
                 actor, _stricter_label(doc.get("sensitivity"), decision["suggested"]), document_id),
            )
        conn.commit()
    finally:
        conn.close()
    return rejected, decisions


# Source types whose stored copy is the whole source, so they mean the same
# thing in the importing collection: a pasted note, an agent's note, a link.
_PORTABLE_SOURCE_TYPES = ("text", "agent_note")


def _settle_source_types(conn: sqlite3.Connection) -> None:
    """Keep the source types that travel; make every other source an upload.

    A local reference or a synced file names a path on the exporter's
    machine. Here it means nothing, and a re-index or a folder sync must
    never be pointed at a path a bundle chose, so those arrive as the
    importer's own uploads. A link keeps its URL, but only a web URL.
    """
    placeholders = ", ".join("?" for _ in _PORTABLE_SOURCE_TYPES)
    conn.execute(
        "UPDATE documents SET source_path = NULL "
        f"WHERE source_type IN ({placeholders})", _PORTABLE_SOURCE_TYPES,
    )
    conn.execute(
        "UPDATE documents SET source_type = 'upload', source_path = NULL "
        f"WHERE NOT (COALESCE(source_type, 'upload') IN ({placeholders}) "
        "OR (source_type = 'url' AND (lower(COALESCE(source_path, '')) LIKE 'http://%' "
        "OR lower(COALESCE(source_path, '')) LIKE 'https://%')))",
        _PORTABLE_SOURCE_TYPES,
    )
    # Where a source is published travels too, but only as an address a
    # browser may open: a bundle can come from anywhere.
    columns = {r[1] for r in conn.execute("PRAGMA table_info(documents)")}
    if "public_url" in columns:
        conn.execute(
            "UPDATE documents SET public_url = NULL WHERE public_url IS NOT NULL AND NOT ("
            "lower(public_url) LIKE 'http://%' OR lower(public_url) LIKE 'https://%' "
            "OR (public_url LIKE '/%' AND public_url NOT LIKE '//%'))")


def import_collection(zip_path: Path, owner_id: str, name: Optional[str] = None,
                      actor: Optional[str] = None) -> Dict[str, Any]:
    """Create a collection from a bundle and queue the job that finishes it.

    Returns the new collection plus `job_id` and `vectors` (always
    "re-embed": a bundle's vectors are never loaded). Raises BundleError for a
    bad bundle, StorageLimitExceeded when it does not fit, RuntimeError when
    the job queue is full.
    """
    from services import audit, content_policy, data_controls, storage_quota
    from services.collection_service import collection_service
    from services.governance import normalize_sensitivity
    from services.indexer_manager import indexer_manager
    from services.indexing.indexer import record_policy_decision
    from services.metadata_store import MetadataStore
    from services.upload_service import upload_service

    manifest = read_manifest(zip_path)
    meta = manifest.get("collection") or {}
    embedding = manifest.get("embedding") or {}
    signature = embedding.get("signature") or ""

    # A local model travels as the collection's own model setting, so a
    # local-provider importer embeds the text again with that same model.
    collection_model = (signature[len("local::"):] if signature.startswith("local::")
                        else settings.embedding_model)

    # Prepare everything in a scratch folder first: the storage check, the
    # blocklist and the policy scan all need the bundle's contents, and the new
    # collection must not be opened (which creates an empty index with its own
    # SQLite side files) before the real database is in place.
    work = _work_dir() / f"import-{uuid.uuid4().hex}"
    work.mkdir()
    cid = None
    limit = storage_quota.limit_bytes()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            db_tmp = work / "metadata.db"
            db_info = zf.getinfo(_METADATA)
            if limit and db_info.file_size > limit:
                raise storage_quota.StorageLimitExceeded("new", db_info.file_size, 0, limit,
                                                         "the bundle's index (metadata.db)")
            _copy_member(zf, db_info, db_tmp)
            _sanitise_imported_db(db_tmp)
            try:
                MetadataStore(db_tmp)  # the columns this version governs with, on an older file
                docs = _documents(db_tmp)
            except sqlite3.DatabaseError:
                raise BundleError("The bundle's metadata.db is not a readable Clio index.")

            # This deployment's blocklist applies to imported documents too,
            # first by the hash the bundle declares and then by the real one.
            blocked = {d["document_id"] for d in docs
                       if d.get("content_hash") and content_policy.is_hash_blocked(d["content_hash"])}
            _strip_documents(db_tmp, blocked)
            kept = {d["document_id"]: d for d in docs if d["document_id"] not in blocked}
            artifacts = artifact_store.prune_for_bundle(db_tmp, artifact_store.load_blocked_hashes())

            # Storage counts the bytes this import will really write: each
            # member as its zip entry measures it (unpacking never passes
            # that), plus, for a document without its file, the larger of its
            # declared size and its text. A new collection starts empty, so
            # the whole bundle must fit.
            plan = _plan_sources(zf, kept)
            figure_rows = _figure_rows(db_tmp)
            figure_members = _figure_members(zf, figure_rows, kept)
            artifact_members = _artifact_members(zf, artifacts)
            conn = _connect_untrusted(db_tmp)
            try:
                without_file = sum(max(_declared_size(d.get("file_size")), _stored_bytes(conn, i))
                                   for i, d in kept.items() if i not in plan)
            finally:
                conn.close()
            incoming = without_file + sum(info.file_size for info in plan.values()) + sum(
                info.file_size for info in figure_members) + sum(info.file_size for info, _ in artifact_members)
            if limit and incoming > limit:
                raise storage_quota.StorageLimitExceeded("new", incoming, 0, limit,
                                                         meta.get("name") or "this bundle")

            sources_dir = work / "sources"
            sources_dir.mkdir()
            sources: Dict[str, Tuple[int, str]] = {}
            for document_id, info in plan.items():
                sources[document_id] = _copy_member(zf, info, sources_dir / document_id)
            really_blocked = {i for i, (_, digest) in sources.items() if content_policy.is_hash_blocked(digest)}

            # A model review of the text must not go outside for local-only
            # data, as at ingest: the collection's marking or any document's.
            local_only = data_controls.is_local_only(str(meta.get("classification") or "").strip().lower()
                                                     or None) or any(
                data_controls.is_local_only(d.get("classification")) for d in kept.values())
            with data_controls.processing_locally(local_only):
                rejected, decisions = _govern_imported(db_tmp, {i: d for i, d in kept.items()
                                                                if i not in really_blocked}, sources, actor)
            withdrawn = really_blocked | set(rejected)
            if withdrawn:
                blocked |= really_blocked
                _strip_documents(db_tmp, withdrawn)
                kept = {i: d for i, d in kept.items() if i not in withdrawn}
                artifacts = artifact_store.prune_for_bundle(db_tmp, artifact_store.load_blocked_hashes())
                artifact_members = _artifact_members(zf, artifacts)
                figure_members = _figure_members(zf, figure_rows, kept)

            conn = sqlite3.connect(db_tmp)
            try:
                try:
                    _settle_source_types(conn)
                    conn.commit()
                except sqlite3.OperationalError:
                    pass  # pre-3.1 database without the source columns
                # Deleted rows stay readable in free pages until this rewrites the file.
                conn.execute("VACUUM")
            finally:
                conn.close()

            collection = collection_service.create_collection(
                name=(name or meta.get("name") or "Imported collection")[: authored_text.MAX_NAME_CHARS],
                description=(meta.get("description") or "")[: authored_text.MAX_DESCRIPTION_CHARS],
                color=meta.get("color") or "#3b82f6",
                chunk_size=int(meta.get("chunk_size") or 500),
                chunk_overlap=int(meta.get("chunk_overlap") or 50),
                embedding_model=collection_model,
                owner_id=owner_id,
            )
            cid = collection["id"]
            updates = {"guide": (meta.get("guide") or "")[: authored_text.MAX_GUIDE_CHARS]}
            try:
                updates["sensitivity"] = normalize_sensitivity(meta.get("sensitivity"))
            except Exception:
                pass
            marking = str(meta.get("classification") or "").strip().lower()[:40]
            if marking:
                # Kept as it came, known here or not, and set before anything is embedded.
                updates["classification"] = marking
            collection_service.update_collection(cid, **{k: v for k, v in updates.items() if v})
            if meta.get("static_export"):
                from services.static_export import restore_address_settings

                restore_address_settings(cid, meta.get("static_export"))
            if meta.get("static_answers"):
                from services.static_export import answers as static_answers

                static_answers.restore(cid, meta.get("static_answers"))

            indexes_dir = collection_service.get_indexes_path(cid)
            documents_dir = collection_service.get_documents_path(cid)
            # Copied through SQLite rather than moved: the scratch file has a
            # write-ahead log beside it that a plain move would leave behind.
            src, dst = sqlite3.connect(db_tmp), sqlite3.connect(indexes_dir / "metadata.db")
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()

            # Originals land in this collection's library; paths from the
            # exporting machine mean nothing here. The shipped vectors are
            # never read: a FAISS file is parsed by native code, and vectors
            # need not match the text they claim to stand for.
            extracted = set()
            for document_id in kept:
                if document_id in sources:
                    shutil.move(str(sources_dir / document_id),
                                str(documents_dir / Path(kept[document_id]["filename"]).name))
                    extracted.add(document_id)
            _restore_artifact_files(zf, artifact_members, indexes_dir / "metadata.db",
                                    collection_service.get_artifacts_path(cid))
            _restore_figure_files(zf, figure_members, collection_service.get_figures_path(cid))
    except Exception as e:
        if cid:
            indexer_manager.close_indexer(cid)
            collection_service.delete_collection(cid)
        if isinstance(e, sqlite3.DatabaseError):
            # A file shaped to look like an index but not one: the client's mistake, not a 500.
            raise BundleError("The bundle's metadata.db is not a readable Clio index.") from e
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)

    for document_id, decision in decisions.items():
        doc = next((d for d in docs if d["document_id"] == document_id), {})
        record_policy_decision(decision["status"], decision["flags"], filename=doc.get("filename") or "",
                               document_id=document_id, collection_id=cid, actor=actor)
    held = sum(1 for i in kept if decisions.get(i, {}).get("status") == "quarantined")
    job_id = upload_service.submit_job(
        cid, "import", 1, _run_import, args=(cid, embedding.get("model")),
    )
    audit.record(
        "collection.imported", actor=actor, collection_id=cid, target=str(job_id),
        detail={
            "from": meta.get("name"), "documents": len(kept), "blocked": len(blocked),
            "rejected": len(rejected), "held": held,
            "sources": len(extracted), "vectors": "re-embed",
            "signature": signature or None,
            # The bundle's guide is text from another deployment: record that
            # it arrived (size + fingerprint, never the text) and what the
            # prompt-injection scan said about it.
            "guide_chars": len(meta.get("guide") or ""),
            "guide_digest": authored_text.digest(meta.get("guide")) if meta.get("guide") else None,
            "guide_flag": ((collection_service.get_collection(cid) or {}).get("guide_flag") or {}).get("level"),
        },
    )
    return {
        **collection_service.get_collection(cid),
        "job_id": job_id,
        "vectors": "re-embed",
        "documents_imported": len(kept),
        "documents_blocked": len(blocked),
        "documents_rejected": len(rejected),
        "documents_held": held,
        "sources_imported": len(extracted),
        "permission": "owner",
    }


def _run_import(job_id: int, collection_id: str, source_model: Optional[str]) -> None:
    """Job body: rebuild keyword search, then embed the chunk text here."""
    from services.bm25_service import BM25Index
    from services.collection_service import collection_service
    from services.embedder import EmbeddingDimensionMismatch, EmbeddingUnavailable
    from services.indexer_manager import indexer_manager
    from services.upload_service import upload_service

    indexes_dir = collection_service.get_indexes_path(collection_id)
    db_path = indexes_dir / "metadata.db"
    total = _chunk_count(db_path)

    upload_service.report_progress(job_id, "saving", 0, "Rebuilding keyword search", 0, total)
    bm25 = BM25Index(indexes_dir / "bm25.db")
    bm25.clear()
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT chunk_id, text FROM chunks ORDER BY id")
        while True:
            rows = cursor.fetchmany(1000)
            if not rows:
                break
            bm25.add_documents_batch(rows)
    finally:
        conn.close()

    indexer = indexer_manager.get_indexer(collection_id)
    store = indexer.vector_store

    # Embed from the stored chunk text: no extraction, no OCR.
    service = indexer.embedding_service
    with store._index_lock:
        store.index = store._new_index()
    done = 0
    try:
        for rows in store.metadata_store.iter_rowid_texts(batch_size=256):
            if upload_service.is_cancelled(job_id):
                store.save()
                upload_service.finish_job(job_id, {"chunks": total, "embedded": done}, cancelled=True)
                return
            ids = np.asarray([r[0] for r in rows], dtype=np.int64)
            vectors = np.asarray(service.embed_texts(indexer._texts_to_embed(
                [{"text": r[1], "filename": r[2]} for r in rows])), dtype=np.float32)
            vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
            with store._index_lock:
                store.index.add_with_ids(vectors, ids)
            done += len(rows)
            upload_service.report_progress(
                job_id, "embedding", done / max(total, 1) * 100,
                f"Embedding {done}/{total} chunks with {service.model_name}", done, total,
            )
    except EmbeddingDimensionMismatch:
        raise
    except EmbeddingUnavailable as e:
        # The collection is complete and keyword search was rebuilt above. What
        # is left is what DocumentIndexer.fill_missing_vectors does once a model
        # is there, so this job is finished, not failed.
        logger.warning(f"Imported '{collection_id}' for keyword search only, {done}/{total} vectors: {e}")
        store.vectors_pending = True
        store.save()
        upload_service.finish_job(job_id, {"chunks": total, "embedded": done, "vectors": "pending",
                                           "source_model": source_model, "reason": str(e)})
        return
    store.save()
    store.vectors_pending = False
    upload_service.finish_job(job_id, {"chunks": total, "vectors": "re-embedded",
                                       "model": service.model_name, "source_model": source_model})


# ── Export preview ───────────────────────────────────────────────────────────


def export_preview(collection_id: str) -> Dict[str, Any]:
    """What an export would contain, without building the zip.

    Lets the Export dialog state the embedding model and the size up front.
    """
    from services import governance
    from services.collection_service import collection_service

    collection = collection_service.get_collection(collection_id)
    if not collection:
        raise BundleError(f"Collection '{collection_id}' not found")
    indexes_dir = collection_service.get_indexes_path(collection_id)
    db = indexes_dir / "metadata.db"
    if not db.exists():
        raise BundleError("This collection has nothing indexed to export yet.")

    docs = _documents(db)
    visible = [d for d in docs if not governance.is_hidden(d)]
    info = _index_info(db)
    model = describe_model(info, visible)
    chunks = sum(int(d.get("num_chunks") or 0) for d in visible)
    return {
        "name": collection.get("name"),
        "documents": len(visible),
        "chunks": chunks,
        "quarantined_excluded": len(docs) - len(visible),
        "embedding": model,
        # Reusable on import only when the vectors can be vouched for.
        "vectors_included": bool(model["recorded"]) and (indexes_dir / "faiss.index").exists(),
        "sources_bytes": sum(int(d.get("file_size") or 0) for d in visible),
        "figures_bytes": sum(int(r.get("byte_size") or 0) for r in _figure_rows(db)
                             if r["document_id"] in {d["document_id"] for d in visible}),
    }


# ── Import preview: look inside a bundle before creating anything ────────────


def inspect_bundle(zip_path: Path) -> Dict[str, Any]:
    """Summarise a bundle for the Import dialog and say how it will land here.

    ``action`` is always "re-embed": the chunk text is embedded again here
    (extraction and OCR are never redone), because a bundle's vectors are
    never loaded.
    """
    from services import content_policy

    manifest = read_manifest(zip_path)
    meta = manifest.get("collection") or {}
    embedding = manifest.get("embedding") or {}
    contents = manifest.get("contents") or {}
    docs = manifest.get("documents") or []

    reason = ("This server embeds the bundle's text again with its own model; vectors made "
              "elsewhere are not loaded. Extraction and OCR are not redone, and the text is "
              "checked against this server's content policy as it lands. If no embedding model "
              "can be reached, it is searchable by keyword until one is.")

    blocked = sum(1 for d in docs if d.get("content_hash") and content_policy.is_hash_blocked(d["content_hash"]))
    name = meta.get("name") or "Imported collection"
    return {
        "name": name,
        "description": meta.get("description") or "",
        "documents": len(docs),
        "chunks": int(contents.get("chunks") or 0),
        "sources_included": int(contents.get("sources_included") or 0),
        "quarantined_excluded": int(contents.get("quarantined_excluded") or 0),
        "blocked_here": blocked,
        "exported_at": manifest.get("exported_at"),
        "exported_by": manifest.get("exported_by"),
        "clio_version": manifest.get("clio_version") or None,
        "embedding": describe_model(embedding),
        "action": "re-embed",
        "reason": reason,
        "this_server": {"model": settings.embedding_model, "provider": settings.embedding_provider},
        "bytes": zip_path.stat().st_size,
    }


# A previewed bundle waits here so the confirm step does not upload it twice
# (bundles with originals run to gigabytes). Swept when stale.
_STAGED_TTL = 3600


def _staged_dir() -> Path:
    path = _work_dir() / "staged"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sweep_staged(max_age: float = _STAGED_TTL) -> None:
    import time

    cutoff = time.time() - max_age
    for p in _staged_dir().glob("*.zip"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def stage_bundle(uploaded: Path) -> Tuple[str, Dict[str, Any]]:
    """Validate an uploaded bundle, keep it for the confirm step, summarise it."""
    sweep_staged()
    summary = inspect_bundle(uploaded)  # raises BundleError for a bad bundle
    upload_id = uuid.uuid4().hex
    shutil.move(str(uploaded), str(_staged_dir() / f"{upload_id}.zip"))
    return upload_id, summary


def staged_path(upload_id: str) -> Path:
    if not upload_id or len(upload_id) != 32 or any(c not in "0123456789abcdef" for c in upload_id):
        raise BundleError("That upload is not valid. Choose the file again.")
    path = _staged_dir() / f"{upload_id}.zip"
    if not path.exists():
        raise BundleError("That upload expired. Choose the file again.")
    return path

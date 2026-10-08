"""Export a collection as a portable bundle, and import one.

A bundle is a zip:

    manifest.json           what this is, how it was built, what it holds
    indexes/metadata.db     chunk text, pages, document records, CSV tables
    indexes/faiss.index     the vectors (omitted when they cannot be vouched for)
    sources/<doc id>/<name> the original files (optional)

The chunk text is the valuable part. Extraction - vision OCR above all - is
what takes hours; embedding chunk text takes minutes. So an importer whose
embedding model matches the manifest's signature exactly reuses the vectors
as they are, and any other importer re-embeds from the chunk text without
redoing extraction. The keyword (BM25) index is always rebuilt on import
from the same text rather than shipped.

Quarantined documents never leave: they are stripped from the exported
database and vectors. On import, documents whose hash this deployment has
blocklisted are stripped the same way.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Tuple

import faiss
import numpy as np

from config import settings

logger = logging.getLogger(__name__)

BUNDLE_FORMAT = "clio-collection"
BUNDLE_VERSION = 1

_MANIFEST = "manifest.json"
_METADATA = "indexes/metadata.db"
_VECTORS = "indexes/faiss.index"
_SOURCES = "sources/"


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


def _strip_documents(db_path: Path, document_ids: Iterable[str]) -> List[int]:
    """Delete documents (chunks, records, CSV tables) from a metadata.db.

    Returns the chunk row ids removed - their vectors must go too.
    """
    ids = sorted(set(document_ids))
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    conn = sqlite3.connect(db_path)
    try:
        rowids = [r[0] for r in conn.execute(
            f"SELECT id FROM chunks WHERE document_id IN ({marks})", ids)]
        conn.execute(f"DELETE FROM chunks WHERE document_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM documents WHERE document_id IN ({marks})", ids)
        try:
            tables = [r[0] for r in conn.execute(
                f"SELECT table_name FROM csv_schemas WHERE document_id IN ({marks})", ids)]
            for table in tables:
                conn.execute(f'DROP TABLE IF EXISTS "{table}"')
            conn.execute(f"DELETE FROM csv_schemas WHERE document_id IN ({marks})", ids)
        except sqlite3.OperationalError:
            pass  # no structured tables in this collection
        conn.commit()
        return rowids
    finally:
        conn.close()


def _documents(db_path: Path) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM documents ORDER BY filename")]
    finally:
        conn.close()


def _chunk_count(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        conn.close()


def _index_info(db_path: Path) -> Dict[str, Any]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT key, value FROM index_info").fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    return {k: json.loads(v) for k, v in rows}


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

        manifest = {
            "format": BUNDLE_FORMAT,
            "version": BUNDLE_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "exported_by": exported_by,
            "collection": {
                "id": collection_id,
                "name": collection.get("name"),
                "description": collection.get("description") or "",
                "color": collection.get("color"),
                "guide": collection.get("guide"),
                "sensitivity": governance.collection_sensitivity(collection),
                "chunk_size": collection.get("chunk_size"),
                "chunk_overlap": collection.get("chunk_overlap"),
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
    finally:
        shutil.rmtree(work, ignore_errors=True)

    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in (collection.get("name") or collection_id))
    return zip_path, f"{slug.strip('-') or collection_id}.clio.zip"


# ── Import ───────────────────────────────────────────────────────────────────


def read_manifest(zip_path: Path) -> Dict[str, Any]:
    """Validate a bundle's shape and return its manifest. Raises BundleError."""
    if not zipfile.is_zipfile(zip_path):
        raise BundleError("That file is not a Clio collection export (.clio.zip).")
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if _MANIFEST not in names or _METADATA not in names:
            raise BundleError("That zip is not a Clio collection export: manifest.json or metadata.db is missing.")
        for name in names:
            _check_member(name)
        try:
            manifest = json.loads(zf.read(_MANIFEST))
        except ValueError:
            raise BundleError("The bundle's manifest.json is not valid JSON.")
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
    if name.startswith(_SOURCES) and len(path.parts) == 3:
        return
    raise BundleError(f"The bundle contains an unexpected file: {name}")


def import_collection(zip_path: Path, owner_id: str, name: Optional[str] = None,
                      actor: Optional[str] = None) -> Dict[str, Any]:
    """Create a collection from a bundle and queue the job that finishes it.

    Returns the new collection plus `job_id` and `vectors` ("reused" or
    "re-embed"). Raises BundleError for a bad bundle, StorageLimitExceeded
    when it does not fit, RuntimeError when the job queue is full.
    """
    from services import audit, content_policy, storage_quota
    from services.collection_service import collection_service
    from services.embedder import service_signature
    from services.governance import normalize_sensitivity
    from services.indexer_manager import indexer_manager
    from services.upload_service import upload_service

    manifest = read_manifest(zip_path)
    meta = manifest.get("collection") or {}
    embedding = manifest.get("embedding") or {}
    signature = embedding.get("signature") or ""

    # A local model travels as the collection's own model setting, so a
    # local-provider importer loads exactly that model and can reuse vectors.
    collection_model = (signature[len("local::"):] if signature.startswith("local::")
                        else settings.embedding_model)

    # Prepare the database in a scratch folder first: the storage check and
    # the blocklist both need its contents, and the new collection must not
    # be opened (which creates an empty index with its own SQLite side files)
    # before the real database is in place.
    work = _work_dir() / f"import-{uuid.uuid4().hex}"
    work.mkdir()
    cid = None
    try:
        db_tmp = work / "metadata.db"
        with zipfile.ZipFile(zip_path) as zf, zf.open(_METADATA) as src, open(db_tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
        try:
            docs = _documents(db_tmp)
        except sqlite3.DatabaseError:
            raise BundleError("The bundle's metadata.db is not a readable Clio index.")

        # This deployment's blocklist applies to imported documents too.
        blocked = [d["document_id"] for d in docs
                   if d.get("content_hash") and content_policy.is_hash_blocked(d["content_hash"])]
        removed_rowids = _strip_documents(db_tmp, blocked)
        kept = {d["document_id"]: d for d in docs if d["document_id"] not in blocked}
        conn = sqlite3.connect(db_tmp)
        try:
            conn.execute("UPDATE documents SET source_type = 'upload', source_path = NULL")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # pre-3.1 database without the source columns
        finally:
            conn.close()

        # Storage counts documents' recorded sizes, as it does for uploads;
        # a new collection starts empty, so the whole bundle must fit.
        limit = storage_quota.limit_bytes()
        incoming = sum(int(d.get("file_size") or 0) for d in kept.values())
        if limit and incoming > limit:
            raise storage_quota.StorageLimitExceeded("new", incoming, 0, limit,
                                                     meta.get("name") or "this bundle")

        collection = collection_service.create_collection(
            name=name or meta.get("name") or "Imported collection",
            description=meta.get("description") or "",
            color=meta.get("color") or "#3b82f6",
            chunk_size=int(meta.get("chunk_size") or 500),
            chunk_overlap=int(meta.get("chunk_overlap") or 50),
            embedding_model=collection_model,
            owner_id=owner_id,
        )
        cid = collection["id"]
        updates = {"guide": meta.get("guide")}
        try:
            updates["sensitivity"] = normalize_sensitivity(meta.get("sensitivity"))
        except Exception:
            pass
        collection_service.update_collection(cid, **{k: v for k, v in updates.items() if v})

        indexes_dir = collection_service.get_indexes_path(cid)
        documents_dir = collection_service.get_documents_path(cid)
        shutil.move(str(db_tmp), str(indexes_dir / "metadata.db"))

        with zipfile.ZipFile(zip_path) as zf:
            target_signature = service_signature(collection_model=collection_model)
            reuse = bool(signature) and signature == target_signature and _VECTORS in zf.namelist()
            if reuse:
                vectors = indexes_dir / "faiss.index"
                with zf.open(_VECTORS) as src, open(vectors, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                if removed_rowids:
                    index = faiss.read_index(str(vectors))
                    index.remove_ids(np.asarray(removed_rowids, dtype=np.int64))
                    faiss.write_index(index, str(vectors))

            # Originals land in this collection's library; paths from the
            # exporting machine mean nothing here.
            extracted = set()
            for info in zf.infolist():
                if not info.filename.startswith(_SOURCES) or info.filename.endswith("/"):
                    continue
                _, document_id, filename = PurePosixPath(info.filename).parts
                doc = kept.get(document_id)
                if doc is None or Path(filename).name != Path(doc["filename"]).name:
                    continue
                target = documents_dir / Path(doc["filename"]).name
                if target.exists():
                    continue  # two documents sharing a filename: keep the first
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                extracted.add(document_id)
    except Exception:
        if cid:
            indexer_manager.close_indexer(cid)
            collection_service.delete_collection(cid)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)

    job_id = upload_service.submit_job(
        cid, "import", 1, _run_import, args=(cid, reuse, embedding.get("model")),
    )
    audit.record(
        "collection.imported", actor=actor, collection_id=cid, target=str(job_id),
        detail={
            "from": meta.get("name"), "documents": len(kept), "blocked": len(blocked),
            "sources": len(extracted), "vectors": "reused" if reuse else "re-embed",
            "signature": signature or None,
        },
    )
    return {
        **collection_service.get_collection(cid),
        "job_id": job_id,
        "vectors": "reused" if reuse else "re-embed",
        "documents_imported": len(kept),
        "documents_blocked": len(blocked),
        "sources_imported": len(extracted),
        "permission": "owner",
    }


def _run_import(job_id: int, collection_id: str, reuse: bool, source_model: Optional[str]) -> None:
    """Job body: rebuild keyword search, then reuse or re-embed the vectors."""
    from services.bm25_service import BM25Index
    from services.collection_service import collection_service
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
    if reuse and store.index.ntotal == total:
        upload_service.finish_job(job_id, {"chunks": total, "vectors": "reused",
                                           "model": source_model})
        return

    # Re-embed from the stored chunk text: no extraction, no OCR.
    service = indexer.embedding_service
    with store._index_lock:
        store.index = store._new_index()
    done = 0
    for rows in store.metadata_store.iter_rowid_texts(batch_size=256):
        if upload_service.is_cancelled(job_id):
            store.save()
            upload_service.finish_job(job_id, {"chunks": total, "embedded": done}, cancelled=True)
            return
        ids = np.asarray([r[0] for r in rows], dtype=np.int64)
        vectors = np.asarray(service.embed_texts([r[1] for r in rows]), dtype=np.float32)
        vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
        with store._index_lock:
            store.index.add_with_ids(vectors, ids)
        done += len(rows)
        upload_service.report_progress(
            job_id, "embedding", done / max(total, 1) * 100,
            f"Embedding {done}/{total} chunks with {service.model_name}", done, total,
        )
    store.save()
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
    }


# ── Import preview: look inside a bundle before creating anything ────────────


def inspect_bundle(zip_path: Path) -> Dict[str, Any]:
    """Summarise a bundle for the Import dialog and say how it will land here.

    ``action`` is "reuse" when this server embeds with exactly the model the
    bundle records (instant), otherwise "re-embed" (chunk text is embedded
    again here; extraction and OCR are never redone).
    """
    from services import content_policy
    from services.embedder import service_signature

    manifest = read_manifest(zip_path)
    meta = manifest.get("collection") or {}
    embedding = manifest.get("embedding") or {}
    contents = manifest.get("contents") or {}
    signature = embedding.get("signature") or ""
    docs = manifest.get("documents") or []

    collection_model = (signature[len("local::"):] if signature.startswith("local::")
                        else settings.embedding_model)
    target = service_signature(collection_model=collection_model)
    with zipfile.ZipFile(zip_path) as zf:
        has_vectors = _VECTORS in zf.namelist()
    reuse = bool(signature) and signature == target and has_vectors

    if reuse:
        reason = "This server embeds with the same model, so the vectors are reused as they are."
    elif not signature:
        reason = "The bundle does not record its embedding model, so its text is embedded again here."
    elif not has_vectors:
        reason = "The bundle carries no vectors, so its text is embedded again here."
    else:
        reason = (f"This server uses a different model ({settings.embedding_model}), so the "
                  "bundle's text is embedded again here. Extraction and OCR are not redone.")

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
        "action": "reuse" if reuse else "re-embed",
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

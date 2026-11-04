"""Saved artifacts: the generated reports (and later audio) of one collection.

The rows live in the collection's metadata.db next to the research tables, so
collection export/import and backups cover them. Binary output lives in the
collection's ``artifacts/<id>/`` folder, which deleting the collection removes.

A row keeps what is needed to judge the artifact later:

* ``source_documents`` is what it was built from: the sources the answer drew
  on plus any the person selected, each with the content hash it had then. A
  reader compares those with the live documents to say what has since changed,
  and to withhold an artifact whose source is now quarantined or blocklisted.
  Deleting a quarantined source would end that comparison, so the deletion
  marks the entry ``withheld`` and the hold lasts while the document is absent.
* ``sources`` holds the citations without URLs. A URL names a host and a
  collection, and neither survives a move, so readers rebuild them.

No path is ever stored: a file's location is derived from the artifact id and
a bare file name, both checked again on the way out because an imported
database is not trusted.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Collection, Iterable, List, Mapping, Optional, Set

from services.sqlite_utils import sqlite_connect

ACTIVE_STATUSES = ("queued", "running")
LIST_LIMIT = 500

_ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    artifact_type TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,       -- queued | running | done | failed
    focus TEXT,
    custom_instructions TEXT,
    document_ids TEXT,          -- JSON: the selection, NULL for the whole collection
    provider TEXT,
    model TEXT,
    content TEXT,
    sources TEXT,               -- JSON: citations, without URLs
    source_documents TEXT,      -- JSON: [{document_id, filename, content_hash}]
    error TEXT,
    file_name TEXT,
    media_type TEXT,
    file_size INTEGER,
    created_by TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_artifacts_created ON artifacts(created_at);
"""

_FIELDS = ("artifact_type", "title", "status", "focus", "custom_instructions", "document_ids",
           "provider", "model", "content", "sources", "source_documents", "error",
           "file_name", "media_type", "file_size", "created_by", "created_at",
           "started_at", "finished_at")
_JSON_FIELDS = {"document_ids": None, "sources": [], "source_documents": []}
# A list row carries no body or citations: those are read one artifact at a time.
_SUMMARY_FIELDS = tuple(f for f in _FIELDS if f not in ("content", "sources", "custom_instructions"))


def _encode(field: str, value: Any) -> Any:
    return json.dumps(value) if field in _JSON_FIELDS and value is not None else value


def _decode(row: sqlite3.Row) -> dict:
    out = dict(row)
    for field, default in _JSON_FIELDS.items():
        if field not in out:
            continue
        try:
            decoded = json.loads(out[field]) if out[field] else default
        except (TypeError, ValueError):
            decoded = default
        out[field] = decoded if isinstance(decoded, list) else default
    return out


class ArtifactStore:
    """The saved artifacts of one collection (one metadata.db)."""

    def __init__(self, db_path: Path, files_dir: Optional[Path] = None):
        self.db_path = Path(db_path)
        self.files_dir = Path(files_dir) if files_dir else None
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite_connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def create(self, artifact_id: str, **fields: Any) -> dict:
        names = ["id", *fields]
        values = [artifact_id, *(_encode(k, v) for k, v in fields.items())]
        self._check(fields)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                f"INSERT INTO artifacts ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
                values,
            )
        return self.get(artifact_id)

    def get(self, artifact_id: str) -> Optional[dict]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        return _decode(row) if row else None

    def list(self, limit: int = LIST_LIMIT) -> List[dict]:
        """Newest first, without bodies."""
        columns = ", ".join(("id", *_SUMMARY_FIELDS))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {columns} FROM artifacts ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [_decode(row) for row in rows]

    def count(self) -> int:
        with closing(self._connect()) as conn:
            return conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]

    def update(self, artifact_id: str, **fields: Any) -> bool:
        """Write the given fields. False when the row is gone: it was deleted
        while a job was still working on it, and the result is dropped."""
        self._check(fields)
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = [_encode(k, v) for k, v in fields.items()]
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(f"UPDATE artifacts SET {sets} WHERE id = ?", [*values, artifact_id])
        return cursor.rowcount > 0

    def delete(self, artifact_id: str) -> bool:
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
        folder = self._file_folder(artifact_id)
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)
        return cursor.rowcount > 0

    # ── files ───────────────────────────────────────────────────────────

    def _file_folder(self, artifact_id: str) -> Optional[Path]:
        if self.files_dir is None or not _ID_PATTERN.match(artifact_id or ""):
            return None
        return self.files_dir / artifact_id

    def attach_file(self, artifact_id: str, source: Path, media_type: str, file_name: Optional[str] = None) -> bool:
        """Move a produced file into the artifact's folder and record it."""
        folder = self._file_folder(artifact_id)
        name = _safe_file_name(file_name or Path(source).name)
        if folder is None or name is None:
            raise ValueError("That artifact id or file name cannot be stored.")
        if self.get(artifact_id) is None:
            return False
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        shutil.move(str(source), str(target))
        return self.update(artifact_id, file_name=name, media_type=media_type,
                           file_size=target.stat().st_size)

    def file_target(self, artifact: Mapping[str, Any]) -> Optional[Path]:
        """Where an artifact's file belongs, or None for an unusable id or name."""
        folder = self._file_folder(artifact.get("id"))
        name = _safe_file_name(artifact.get("file_name"))
        return folder / name if folder is not None and name is not None else None

    def file_path(self, artifact: Mapping[str, Any]) -> Optional[Path]:
        """Where an artifact's file is, or None when it has none on disk."""
        path = self.file_target(artifact)
        return path if path is not None and path.is_file() else None

    @staticmethod
    def _check(fields: Mapping[str, Any]) -> None:
        unknown = set(fields) - set(_FIELDS)
        if unknown:
            raise ValueError(f"unknown artifact fields: {sorted(unknown)}")


def _safe_file_name(name: Any) -> Optional[str]:
    if not isinstance(name, str) or not name or name in (".", ".."):
        return None
    return name if re.fullmatch(r"[^/\\:\x00-\x1f]{1,200}", name) else None


# ── judging an artifact against the live documents ──────────────────────────

def is_withheld(artifact: Mapping[str, Any], hidden_ids: Collection[str], blocked_hashes: Collection[str],
                present_ids: Collection[str]) -> bool:
    """Did it draw on a source that may no longer be shown?

    Document ids are content hashes, so a marked document that is back is the
    same content, and its live status decides again.
    """
    for source in artifact.get("source_documents") or []:
        document_id = source.get("document_id")
        if document_id in hidden_ids or source.get("content_hash") in blocked_hashes:
            return True
        if source.get("withheld") and document_id not in present_ids:
            return True
    return False


def mark_sources_withheld(conn: sqlite3.Connection, document_ids: Iterable[str]) -> None:
    """Record on the artifacts built from these documents that they were held.

    Runs on the connection deleting the documents, so the two land together.
    An artifacts table this version cannot read has nothing to mark, and must
    not stop a deletion.
    """
    ids = set(document_ids)
    if not ids:
        return
    try:
        rows = conn.execute("SELECT id, source_documents FROM artifacts").fetchall()
    except sqlite3.OperationalError:
        return
    for artifact_id, raw in rows:
        try:
            sources = json.loads(raw) if raw else []
        except ValueError:
            continue
        if not isinstance(sources, list):
            continue
        named = [s for s in sources if isinstance(s, dict) and s.get("document_id") in ids]
        for source in named:
            source["withheld"] = True
        if named:
            conn.execute("UPDATE artifacts SET source_documents = ? WHERE id = ?",
                         (json.dumps(sources), artifact_id))


def source_changes(artifact: Mapping[str, Any], documents: Mapping[str, Mapping[str, Any]]) -> List[dict]:
    """The sources that are no longer as they were when this was generated."""
    changes = []
    for source in artifact.get("source_documents") or []:
        current = documents.get(source.get("document_id"))
        if current is None:
            change = "removed"
        elif source.get("content_hash") and current.get("content_hash") != source["content_hash"]:
            change = "changed"
        else:
            continue
        changes.append({"document_id": source.get("document_id"),
                        "filename": source.get("filename") or "", "change": change})
    return changes


# ── bundles ─────────────────────────────────────────────────────────────────

def prune_for_bundle(db_path: Path, blocked_hashes: Collection[str]) -> List[dict]:
    """Leave only the finished artifacts a bundle may carry; return those rows.

    Run on the scratch copy of a metadata.db, in both directions, once withheld
    documents are stripped from it. An artifact built from any document no
    longer there stays behind: stripped or deleted, its content must not travel
    on inside the report. An artifacts table this version cannot read is
    dropped rather than trusted.
    """
    try:
        store = ArtifactStore(db_path)
        rows = store.list(limit=store.count() or 1)
        with closing(store._connect()) as conn:
            present = {r[0] for r in conn.execute("SELECT document_id FROM documents")}
    except sqlite3.OperationalError:
        with closing(sqlite_connect(db_path)) as conn, conn:
            conn.execute("DROP TABLE IF EXISTS artifacts")
        return []
    kept = []
    for row in rows:
        absent = any(s.get("document_id") not in present for s in row.get("source_documents") or [])
        if row.get("status") != "done" or absent or is_withheld(row, (), blocked_hashes, present):
            store.delete(row["id"])
        else:
            kept.append(row)
    return kept


def load_blocked_hashes() -> Set[str]:
    """The deployment's blocklist, read once per request. An error propagates:
    this decides what may be shown, so it must not fail open."""
    from services.app_database import app_db

    return {row["content_hash"] for row in app_db.list_blocked_hashes()}

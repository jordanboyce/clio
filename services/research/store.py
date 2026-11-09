"""Persistence for the research layer, inside the collection's metadata.db.

Tables (all keyed by the collection's own ``document_id``):

* ``research_papers``      — bibliographic record per paper, with the source
  of every field (``provenance``) and the filename it had before a rename.
* ``research_references``  — one row per reference-list entry or in-text
  link, with whatever identifier the entry carried and how it was resolved.
* ``research_assessments`` — "is this paper worth my time for question Q":
  the verdict and only the verified evidence behind it.
* ``research_briefs``      — prepared reviews waiting to be read.
* ``research_jobs``        — background batches (analyze, assess, brief).

Living in metadata.db means collection export/import, backups and the
storage accounting already cover it. Rows for a deleted document are
ignored by every reader (they join against ``documents``) and kept, because
document ids are content hashes: add the same file back and its reviews, which
cost a model to produce, return with it. They go when someone asks
(``delete_outputs``, behind Settings > Data). Those rows hold statements drawn
from the paper, so a collection bundle sweeps them regardless, or a withheld
paper's words leave with the export: see ``prune_orphan_rows`` and
``prune_orphan_jobs``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from services.sqlite_utils import sqlite_connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_papers (
    document_id TEXT PRIMARY KEY,
    title TEXT,
    authors TEXT,               -- JSON list of display names
    year INTEGER,
    doi TEXT,
    arxiv_id TEXT,
    venue TEXT,
    abstract TEXT,
    provenance TEXT,            -- JSON {field: {"source": ..., "page": ..., "detail": ...}}
    original_filename TEXT,     -- the name before the first research rename
    renamed_at TEXT,
    analyzed_at TEXT,
    analysis_notes TEXT,        -- JSON list of plain-language notes
    kind TEXT,                  -- 'article' | 'book' (how the document looked), or what a user set
    isbn TEXT,
    details TEXT,               -- JSON: citation fields beyond the columns (publisher, volume, pages,
                                -- report number, organization, page_offset ...)
    corrected_at TEXT           -- the last time a person corrected the record
);
CREATE TABLE IF NOT EXISTS research_references (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    kind TEXT NOT NULL,         -- 'reference' | 'link'
    ordinal INTEGER,
    raw_text TEXT NOT NULL,
    page_number INTEGER,
    doi TEXT,
    arxiv_id TEXT,
    url TEXT,
    title TEXT,                 -- from a registry, never guessed by a model
    year INTEGER,
    resolved_url TEXT,          -- something the link fetcher can download
    resolved_kind TEXT,         -- 'pdf' | 'page'
    resolved_via TEXT,          -- e.g. 'doi', 'arxiv', 'openalex:doi', 'openalex:title 0.97'
    matched_document_id TEXT,   -- the library paper this entry refers to
    match_reason TEXT,
    status TEXT NOT NULL DEFAULT 'parsed',   -- parsed | resolved | unavailable | queued | in_library
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_refs_doc ON research_references(document_id);
CREATE TABLE IF NOT EXISTS research_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    question TEXT NOT NULL,
    verdict TEXT NOT NULL,      -- relevant | partial | not_relevant | not_established
    summary TEXT,
    evidence TEXT,              -- JSON list of verified {point, quote, passage, page}
    rejected INTEGER DEFAULT 0, -- statements dropped because their quote was not in the paper
    retrieval TEXT,             -- JSON: the passages search found, model-free
    model TEXT,
    job_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_assess_doc ON research_assessments(document_id);
CREATE TABLE IF NOT EXISTS research_briefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    depth TEXT NOT NULL,        -- brief | thorough
    question TEXT,
    content TEXT NOT NULL,      -- JSON: sections of verified statements
    rejected INTEGER DEFAULT 0,
    coverage TEXT,              -- JSON: how much of the paper the model was shown
    model TEXT,
    job_id TEXT,
    created_at TEXT NOT NULL,
    read_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_briefs_doc ON research_briefs(document_id);
CREATE TABLE IF NOT EXISTS research_jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,         -- analyze | assess | brief
    status TEXT NOT NULL,       -- queued | running | done | failed | cancelled
    params TEXT,
    total INTEGER DEFAULT 0,
    done INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0,
    current TEXT,
    errors TEXT,                -- JSON list of {document_id, filename, error}
    created_by TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
"""

_PAPER_FIELDS = ("title", "authors", "year", "doi", "arxiv_id", "venue", "abstract",
                 "provenance", "original_filename", "renamed_at", "analyzed_at",
                 "analysis_notes", "kind", "isbn", "details", "corrected_at")
# Columns added after the table first shipped; created on older databases.
_ADDED_PAPER_COLUMNS = (("kind", "TEXT"), ("isbn", "TEXT"), ("details", "TEXT"), ("corrected_at", "TEXT"))
_JSON_PAPER_FIELDS = {"authors", "provenance", "analysis_notes", "details"}
_JSON_DICT_FIELDS = {"provenance", "details"}

_REFERENCE_FIELDS = ("kind", "ordinal", "raw_text", "page_number", "doi", "arxiv_id", "url",
                     "title", "year", "resolved_url", "resolved_kind", "resolved_via",
                     "matched_document_id", "match_reason", "status")

_initialized: set = set()
_init_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _existing_tables(conn: sqlite3.Connection) -> set:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def prune_orphan_rows(conn: sqlite3.Connection) -> int:
    """Drop rows whose document is gone, on a connection the caller commits.

    A paper that merely cites a missing document keeps its reference entry;
    only the pointer goes. Takes an open connection, and skips tables the
    database does not have, so a bundle can be swept without creating the
    research schema in a database from before it existed.
    """
    have = _existing_tables(conn)
    removed = 0
    for table in ("research_papers", "research_references",
                  "research_assessments", "research_briefs"):
        if table in have:
            cur = conn.execute(
                f"DELETE FROM {table} WHERE document_id NOT IN (SELECT document_id FROM documents)"
            )
            removed += cur.rowcount
    if "research_references" in have:
        conn.execute(
            "UPDATE research_references SET matched_document_id = NULL, match_reason = NULL, "
            "status = CASE WHEN resolved_url IS NOT NULL THEN 'resolved' ELSE 'parsed' END "
            "WHERE matched_document_id IS NOT NULL "
            "AND matched_document_id NOT IN (SELECT document_id FROM documents)"
        )
    return removed


def prune_orphan_jobs(conn: sqlite3.Connection) -> int:
    """Drop job rows naming a document that is gone; the caller commits.

    The whole row goes rather than the one id: its question, filename, error
    text and counts all describe work on that paper, and nothing reads a job
    row for the sake of the other papers in the batch. A row that cannot be
    read goes too, since nothing can say what it names. Kept out of
    ``prune_orphans`` because this is for what leaves a deployment: a live
    collection may keep the history of work on a paper it has since deleted.
    """
    if "research_jobs" not in _existing_tables(conn):
        return 0
    present = {row[0] for row in conn.execute("SELECT document_id FROM documents")}
    doomed = []
    for job_id, params, errors in conn.execute(
            "SELECT job_id, params, errors FROM research_jobs").fetchall():
        try:
            named = list(json.loads(params or "{}").get("document_ids") or [])
            # A budget stop records an error with no document_id.
            named += [e["document_id"] for e in json.loads(errors or "[]") if e.get("document_id")]
            if not present.issuperset(named):
                doomed.append(job_id)
        except (TypeError, ValueError, AttributeError, KeyError):
            doomed.append(job_id)
    conn.executemany("DELETE FROM research_jobs WHERE job_id = ?", [(j,) for j in doomed])
    return len(doomed)


class ResearchStore:
    """Research tables for one collection (one metadata.db)."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        key = str(self.db_path.resolve())
        if key not in _initialized:
            with _init_lock:
                if key not in _initialized:
                    with sqlite_connect(self.db_path) as conn:
                        conn.executescript(_SCHEMA)
                        have = {row[1] for row in conn.execute("PRAGMA table_info(research_papers)")}
                        for column, kind in _ADDED_PAPER_COLUMNS:
                            if column not in have:
                                conn.execute(f"ALTER TABLE research_papers ADD COLUMN {column} {kind}")
                        conn.commit()
                    _initialized.add(key)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite_connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── papers ──────────────────────────────────────────────────────────

    @staticmethod
    def _paper_row(row: sqlite3.Row) -> dict:
        out = dict(row)
        for field in _JSON_PAPER_FIELDS:
            out[field] = _loads(out.get(field), {} if field in _JSON_DICT_FIELDS else [])
        return out

    def version_token(self) -> str:
        """A cheap token that changes when analysis does. The corpus version
        cannot stand in for it: analyzing, renaming and matching rewrite these
        tables and leave the documents table untouched."""
        with self._connect() as conn:
            papers = conn.execute("SELECT COUNT(*), COALESCE(MAX(analyzed_at), ''), COALESCE(MAX(renamed_at), ''), "
                                  "COALESCE(MAX(corrected_at), '') FROM research_papers").fetchone()
            references = conn.execute("SELECT COUNT(*), COUNT(matched_document_id), COALESCE(MAX(updated_at), '') "
                                      "FROM research_references").fetchone()
        return ":".join(str(value) for value in (*papers, *references))

    def get_paper(self, document_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM research_papers WHERE document_id = ?",
                               (document_id,)).fetchone()
        return self._paper_row(row) if row else None

    def get_papers(self, document_ids: Optional[Iterable[str]] = None) -> Dict[str, dict]:
        with self._connect() as conn:
            if document_ids is None:
                rows = conn.execute("SELECT * FROM research_papers").fetchall()
            else:
                ids = list(dict.fromkeys(document_ids))
                rows = []
                for start in range(0, len(ids), 500):
                    batch = ids[start:start + 500]
                    marks = ",".join("?" for _ in batch)
                    rows.extend(conn.execute(
                        f"SELECT * FROM research_papers WHERE document_id IN ({marks})", batch
                    ).fetchall())
        return {row["document_id"]: self._paper_row(row) for row in rows}

    def upsert_paper(self, document_id: str, **fields: Any) -> dict:
        """Write the given fields; fields not passed keep their stored value.

        ``original_filename`` is write-once: the first rename records it and
        later renames leave it alone, so "restore original name" always means
        the name the paper arrived with."""
        values = {}
        for field, value in fields.items():
            if field not in _PAPER_FIELDS:
                raise ValueError(f"unknown research paper field {field!r}")
            values[field] = json.dumps(value) if field in _JSON_PAPER_FIELDS else value
        with self._connect() as conn:
            existing = conn.execute("SELECT original_filename FROM research_papers "
                                    "WHERE document_id = ?", (document_id,)).fetchone()
            if existing is None:
                cols = ["document_id", *values]
                conn.execute(
                    f"INSERT INTO research_papers ({', '.join(cols)}) "
                    f"VALUES ({', '.join('?' for _ in cols)})",
                    [document_id, *values.values()],
                )
            else:
                if existing["original_filename"]:
                    values.pop("original_filename", None)
                if values:
                    sets = ", ".join(f"{k} = ?" for k in values)
                    conn.execute(f"UPDATE research_papers SET {sets} WHERE document_id = ?",
                                 [*values.values(), document_id])
            conn.commit()
        return self.get_paper(document_id) or {}

    # ── references ──────────────────────────────────────────────────────

    def replace_references(self, document_id: str, entries: List[dict]) -> None:
        """Replace a paper's parsed references and links in one transaction.

        A re-analysis keeps what the user already did with an entry (queued
        for download, resolved) when the same raw text comes back, and the
        entry's row id: a download that read the ids before this ran still has
        to mark the right rows."""
        stamp = now_iso()
        with self._connect() as conn:
            # The write lock comes before the read, or a status written between
            # the read and the delete would be lost.
            conn.execute("BEGIN IMMEDIATE")
            previous = {
                (row["kind"], row["raw_text"]): dict(row)
                for row in conn.execute(
                    "SELECT * FROM research_references WHERE document_id = ?", (document_id,)
                ).fetchall()
            }
            conn.execute("DELETE FROM research_references WHERE document_id = ?", (document_id,))
            kept_ids = set()
            for entry in entries:
                row = {field: entry.get(field) for field in _REFERENCE_FIELDS}
                row["status"] = row["status"] or "parsed"
                before = previous.get((row["kind"], row["raw_text"]))
                if before and before.get("status") == "queued" and row["status"] in ("parsed", "resolved"):
                    row["status"] = "queued"
                values = {"document_id": document_id, **row, "updated_at": stamp}
                # The same text can be printed twice; only the first keeps the id.
                if before and before["id"] not in kept_ids:
                    kept_ids.add(before["id"])
                    values["id"] = before["id"]
                conn.execute(
                    f"INSERT INTO research_references ({', '.join(values)}) "
                    f"VALUES ({', '.join('?' for _ in values)})",
                    list(values.values()),
                )
            conn.commit()

    def list_references(self, document_id: Optional[str] = None,
                        kind: Optional[str] = None) -> List[dict]:
        clauses, params = [], []
        if document_id is not None:
            clauses.append("document_id = ?")
            params.append(document_id)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM research_references {where} ORDER BY document_id, kind DESC, ordinal, id",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def get_references(self, ids: Iterable[int]) -> List[dict]:
        ids = [int(i) for i in ids]
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM research_references WHERE id IN ({marks})",
                                ids).fetchall()
        return [dict(r) for r in rows]

    def update_reference(self, ref_id: int, **fields: Any) -> None:
        values = {k: v for k, v in fields.items() if k in _REFERENCE_FIELDS}
        if not values:
            return
        sets = ", ".join(f"{k} = ?" for k in values)
        with self._connect() as conn:
            conn.execute(f"UPDATE research_references SET {sets}, updated_at = ? WHERE id = ?",
                         [*values.values(), now_iso(), int(ref_id)])
            conn.commit()

    # ── assessments ─────────────────────────────────────────────────────

    def add_assessment(self, document_id: str, question: str, result: dict,
                       job_id: Optional[str] = None) -> dict:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO research_assessments (document_id, question, verdict, summary, "
                "evidence, rejected, retrieval, model, job_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (document_id, question, result["verdict"], result.get("summary") or "",
                 json.dumps(result.get("evidence") or []), int(result.get("rejected") or 0),
                 json.dumps(result.get("retrieval") or []), result.get("model") or "",
                 job_id, now_iso()),
            )
            conn.commit()
            new_id = cur.lastrowid
        return self.list_assessments(assessment_id=new_id)[0]

    def list_assessments(self, document_id: Optional[str] = None,
                         question: Optional[str] = None,
                         assessment_id: Optional[int] = None,
                         limit: int = 200) -> List[dict]:
        clauses, params = [], []
        if document_id is not None:
            clauses.append("document_id = ?")
            params.append(document_id)
        if question is not None:
            clauses.append("question = ?")
            params.append(question)
        if assessment_id is not None:
            clauses.append("id = ?")
            params.append(int(assessment_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM research_assessments {where} ORDER BY id DESC LIMIT ?",
                [*params, int(limit)],
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["evidence"] = _loads(item.get("evidence"), [])
            item["retrieval"] = _loads(item.get("retrieval"), [])
            out.append(item)
        return out

    # ── briefs ──────────────────────────────────────────────────────────

    def add_brief(self, document_id: str, depth: str, result: dict,
                  question: Optional[str] = None, job_id: Optional[str] = None) -> dict:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO research_briefs (document_id, depth, question, content, rejected, "
                "coverage, model, job_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (document_id, depth, question or None, json.dumps(result.get("sections") or []),
                 int(result.get("rejected") or 0), json.dumps(result.get("coverage") or {}),
                 result.get("model") or "", job_id, now_iso()),
            )
            conn.commit()
            new_id = cur.lastrowid
        return self.list_briefs(brief_id=new_id)[0]

    def list_briefs(self, document_id: Optional[str] = None, unread: bool = False,
                    brief_id: Optional[int] = None, limit: int = 200) -> List[dict]:
        clauses, params = [], []
        if document_id is not None:
            clauses.append("document_id = ?")
            params.append(document_id)
        if unread:
            clauses.append("read_at IS NULL")
        if brief_id is not None:
            clauses.append("id = ?")
            params.append(int(brief_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM research_briefs {where} ORDER BY id DESC LIMIT ?",
                [*params, int(limit)],
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["sections"] = _loads(item.pop("content", None), [])
            item["coverage"] = _loads(item.get("coverage"), {})
            out.append(item)
        return out

    def brief_counts(self) -> List[tuple]:
        """``[(document_id, briefs, unread)]`` without loading brief bodies."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT document_id, COUNT(*), SUM(CASE WHEN read_at IS NULL THEN 1 ELSE 0 END) "
                "FROM research_briefs GROUP BY document_id"
            ).fetchall()
        return [(r[0], int(r[1]), int(r[2] or 0)) for r in rows]

    def reviewed_documents(self, depth: str, question: Optional[str]) -> set:
        """Documents that already have a review at this depth for this question."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT document_id FROM research_briefs WHERE depth = ? AND "
                "COALESCE(question, '') = ?", (depth, question or "")
            ).fetchall()
        return {r[0] for r in rows}

    def mark_brief_read(self, brief_id: int, read: bool = True) -> bool:
        with self._connect() as conn:
            cur = conn.execute("UPDATE research_briefs SET read_at = ? WHERE id = ?",
                               (now_iso() if read else None, int(brief_id)))
            conn.commit()
            return cur.rowcount > 0

    def delete_brief(self, brief_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM research_briefs WHERE id = ?", (int(brief_id),))
            conn.commit()
            return cur.rowcount > 0

    # ── jobs ────────────────────────────────────────────────────────────

    @staticmethod
    def _job_row(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["params"] = _loads(item.get("params"), {})
        item["errors"] = _loads(item.get("errors"), [])
        return item

    def create_job(self, job_id: str, kind: str, params: dict, total: int,
                   created_by: Optional[str] = None) -> dict:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO research_jobs (job_id, kind, status, params, total, created_by, created_at) "
                "VALUES (?, ?, 'queued', ?, ?, ?, ?)",
                (job_id, kind, json.dumps(params), int(total), created_by, now_iso()),
            )
            conn.commit()
        return self.get_job(job_id) or {}

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {"status", "total", "done", "failed", "current", "errors", "started_at", "finished_at"}
        values = {}
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"unknown job field {key!r}")
            values[key] = json.dumps(value) if key == "errors" else value
        if not values:
            return
        sets = ", ".join(f"{k} = ?" for k in values)
        with self._connect() as conn:
            conn.execute(f"UPDATE research_jobs SET {sets} WHERE job_id = ?",
                         [*values.values(), job_id])
            conn.commit()

    def get_job(self, job_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM research_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job_row(row) if row else None

    def list_jobs(self, limit: int = 50) -> List[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM research_jobs ORDER BY created_at DESC, rowid DESC LIMIT ?",
                                (int(limit),)).fetchall()
        return [self._job_row(r) for r in rows]

    # ── housekeeping ────────────────────────────────────────────────────

    def output_counts(self) -> Dict[str, int]:
        """Every generated review and verdict, those of deleted sources included.

        Not what any list shows: readers leave those rows out, so this is the
        only place a person can learn they are still there.
        """
        with self._connect() as conn:
            return {
                "reviews": conn.execute("SELECT COUNT(*) FROM research_briefs").fetchone()[0],
                "screenings": conn.execute("SELECT COUNT(*) FROM research_assessments").fetchone()[0],
            }

    def delete_outputs(self) -> Dict[str, int]:
        """Delete every review and verdict, and what is left of deleted sources.

        The bibliographic records of sources still in the collection stay:
        they are read from the papers themselves and cost no model to rebuild.
        """
        with self._connect() as conn:
            reviews = conn.execute("DELETE FROM research_briefs").rowcount
            screenings = conn.execute("DELETE FROM research_assessments").rowcount
            prune_orphan_rows(conn)
            conn.commit()
        return {"reviews": reviews, "screenings": screenings}

    def prune_orphans(self) -> int:
        """Drop research rows whose document is gone from the collection."""
        with self._connect() as conn:
            removed = prune_orphan_rows(conn)
            conn.commit()
        return removed

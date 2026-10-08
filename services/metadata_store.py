"""SQLite-based metadata storage for document chunks."""

import sqlite3
from pathlib import Path

from services.sqlite_utils import sqlite_connect
from typing import List, Optional, Dict, Any, Set
import json
import logging

logger = logging.getLogger(__name__)

# Current schema version - increment when making breaking changes
SCHEMA_VERSION = "3.6"

# v3.3 governance columns on the documents table, in migration order.
_GOVERNANCE_DOC_COLUMNS = [
    ("uploaded_by", "TEXT DEFAULT NULL"),      # verified identity that added it
    ("content_hash", "TEXT DEFAULT NULL"),     # full sha256 (blocklist key)
    ("sensitivity", "TEXT DEFAULT NULL"),      # per-document label override
    ("policy_status", "TEXT DEFAULT 'clear'"), # clear|flagged|quarantined|approved
    ("policy_flags", "TEXT DEFAULT NULL"),     # JSON scan summary
]


class MetadataStore:
    """SQLite-based metadata storage for scalability."""

    def __init__(self, db_path: Path):
        """
        Initialize the metadata store.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()
        self._migrate_schema()

    def _init_db(self):
        """Initialize database schema."""
        with sqlite_connect(self.db_path) as conn:
            # Schema version tracking table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_info (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chunk_id TEXT UNIQUE NOT NULL,
                    document_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    -- v3.0: Format-aware metadata
                    source_format TEXT DEFAULT NULL,
                    extraction_method TEXT DEFAULT 'text',
                    -- v3.0: CSV-specific metadata (JSON-encoded)
                    csv_row_number INTEGER DEFAULT NULL,
                    csv_columns TEXT DEFAULT NULL,
                    csv_values TEXT DEFAULT NULL,
                    -- v3.6: code symbol metadata (which function/class a chunk is)
                    language TEXT DEFAULT NULL,
                    symbol_name TEXT DEFAULT NULL,
                    symbol_type TEXT DEFAULT NULL,
                    line_start INTEGER DEFAULT NULL,
                    line_end INTEGER DEFAULT NULL
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_document_id
                ON chunks(document_id)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_chunk_id
                ON chunks(chunk_id)
            """)

            # Note: idx_source_format is created in _migrate_schema after columns are added

            # v3.5: entity-graph retrieval boost — per-chunk entity mentions.
            # The migration path also creates this so existing dbs upgrade.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chunk_entities (
                    chunk_id TEXT NOT NULL,
                    entity TEXT NOT NULL,
                    PRIMARY KEY (chunk_id, entity)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_chunk_entities_entity
                ON chunk_entities(entity)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    num_pages INTEGER NOT NULL,
                    num_chunks INTEGER NOT NULL,
                    upload_timestamp TEXT NOT NULL,
                    -- v3.0: Document-level versioning
                    source_format TEXT DEFAULT NULL,
                    extraction_method TEXT DEFAULT 'text',
                    embedding_model TEXT DEFAULT NULL,
                    chunk_size INTEGER DEFAULT NULL,
                    chunk_overlap INTEGER DEFAULT NULL,
                    schema_version TEXT DEFAULT '3.0',
                    injection_warnings TEXT DEFAULT NULL,
                    -- v3.3: governance (attribution, blocklist key, labels, review)
                    uploaded_by TEXT DEFAULT NULL,
                    content_hash TEXT DEFAULT NULL,
                    sensitivity TEXT DEFAULT NULL,
                    policy_status TEXT DEFAULT 'clear',
                    policy_flags TEXT DEFAULT NULL,
                    -- v3.4: bytes of the source file (per-collection storage cap)
                    file_size INTEGER DEFAULT NULL
                )
            """)

            # Check if this is a new database by looking for v3.0 columns
            # Only set schema version to 3.0 if the table was just created with new columns
            chunk_columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
            is_new_database = "source_format" in chunk_columns

            if is_new_database:
                # Set initial schema version for new databases
                conn.execute("""
                    INSERT OR IGNORE INTO schema_info (key, value)
                    VALUES ('version', ?)
                """, (SCHEMA_VERSION,))

                # Create index on source_format
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_source_format
                    ON chunks(source_format)
                """)

            conn.commit()
            logger.info(f"Initialized metadata database at {self.db_path}")

    def _migrate_schema(self):
        """Run schema migrations for existing databases."""
        with sqlite_connect(self.db_path) as conn:
            # Check current schema version
            try:
                cursor = conn.execute(
                    "SELECT value FROM schema_info WHERE key = 'version'"
                )
                row = cursor.fetchone()
                current_version = row[0] if row else "1.0"
            except sqlite3.OperationalError:
                # schema_info table doesn't exist, very old schema
                current_version = "1.0"

            if current_version == SCHEMA_VERSION:
                return  # Already up to date

            logger.info(f"Migrating schema from {current_version} to {SCHEMA_VERSION}")

            # Migration from 1.0/2.0 to 3.0
            if current_version in ("1.0", "2.0"):
                self._migrate_to_v3(conn)
                current_version = "3.0"

            # Migration from 3.0 to 3.1
            if current_version == "3.0":
                self._migrate_to_v3_1(conn)
                current_version = "3.1"

            # Migration from 3.1 to 3.2
            if current_version == "3.1":
                self._migrate_to_v3_2(conn)
                current_version = "3.2"

            # Migration from 3.2 to 3.3
            if current_version == "3.2":
                self._migrate_to_v3_3(conn)
                current_version = "3.3"

            # Migration from 3.3 to 3.4
            if current_version == "3.3":
                self._migrate_to_v3_4(conn)
                current_version = "3.4"

            # Migration from 3.4 to 3.5
            if current_version == "3.4":
                self._migrate_to_v3_5(conn)
                current_version = "3.5"

            # Migration from 3.5 to 3.6 (code symbol columns on chunks)
            if current_version == "3.5":
                self._migrate_to_v3_6(conn)
                current_version = "3.6"

            # Update schema version
            conn.execute("""
                INSERT OR REPLACE INTO schema_info (key, value)
                VALUES ('version', ?)
            """, (SCHEMA_VERSION,))
            conn.commit()

            logger.info(f"Schema migration to {SCHEMA_VERSION} completed")

    def _migrate_to_v3(self, conn: sqlite3.Connection):
        """Migrate from v1.0/v2.0 to v3.0 schema."""
        # Add new columns to chunks table if they don't exist
        chunk_columns = self._get_table_columns(conn, "chunks")

        new_chunk_columns = [
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ("source_format", "TEXT DEFAULT NULL"),
            ("extraction_method", "TEXT DEFAULT 'text'"),
            ("csv_row_number", "INTEGER DEFAULT NULL"),
            ("csv_columns", "TEXT DEFAULT NULL"),
            ("csv_values", "TEXT DEFAULT NULL"),
        ]

        for col_name, col_def in new_chunk_columns:
            if col_name not in chunk_columns:
                conn.execute(f"ALTER TABLE chunks ADD COLUMN {col_name} {col_def}")
                logger.info(f"Added column {col_name} to chunks table")

        # Add new columns to documents table if they don't exist
        doc_columns = self._get_table_columns(conn, "documents")

        new_doc_columns = [
            ("source_format", "TEXT DEFAULT NULL"),
            ("extraction_method", "TEXT DEFAULT 'text'"),
            ("embedding_model", "TEXT DEFAULT NULL"),
            ("chunk_size", "INTEGER DEFAULT NULL"),
            ("chunk_overlap", "INTEGER DEFAULT NULL"),
            ("schema_version", "TEXT DEFAULT '3.0'"),
        ]

        for col_name, col_def in new_doc_columns:
            if col_name not in doc_columns:
                conn.execute(f"ALTER TABLE documents ADD COLUMN {col_name} {col_def}")
                logger.info(f"Added column {col_name} to documents table")

        # Create new indexes
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_source_format
            ON chunks(source_format)
        """)

        # Infer source_format from filename for existing documents
        conn.execute("""
            UPDATE documents SET source_format =
                CASE
                    WHEN LOWER(filename) LIKE '%.pdf' THEN 'pdf'
                    WHEN LOWER(filename) LIKE '%.txt' THEN 'txt'
                    WHEN LOWER(filename) LIKE '%.docx' THEN 'docx'
                    WHEN LOWER(filename) LIKE '%.csv' THEN 'csv'
                    WHEN LOWER(filename) LIKE '%.md' THEN 'md'
                    WHEN LOWER(filename) LIKE '%.json' THEN 'json'
                    ELSE 'unknown'
                END
            WHERE source_format IS NULL
        """)

        # Update chunks with source_format from their documents
        conn.execute("""
            UPDATE chunks SET source_format = (
                SELECT d.source_format FROM documents d
                WHERE d.document_id = chunks.document_id
            )
            WHERE source_format IS NULL
        """)

    def _migrate_to_v3_1(self, conn: sqlite3.Connection):
        """Migrate from v3.0 to v3.1 schema (local file reference support)."""
        doc_columns = self._get_table_columns(conn, "documents")

        new_doc_columns = [
            ("source_path", "TEXT DEFAULT NULL"),      # Original filesystem path for local references
            ("source_type", "TEXT DEFAULT 'upload'"),  # 'upload' or 'local_reference'
        ]

        for col_name, col_def in new_doc_columns:
            if col_name not in doc_columns:
                conn.execute(f"ALTER TABLE documents ADD COLUMN {col_name} {col_def}")
                logger.info(f"Added column {col_name} to documents table")

    def _migrate_to_v3_2(self, conn: sqlite3.Connection):
        """Migrate from v3.1 to v3.2 schema (injection warnings storage)."""
        doc_columns = self._get_table_columns(conn, "documents")
        if "injection_warnings" not in doc_columns:
            conn.execute("ALTER TABLE documents ADD COLUMN injection_warnings TEXT DEFAULT NULL")
            logger.info("Added column injection_warnings to documents table")

    def _migrate_to_v3_3(self, conn: sqlite3.Connection):
        """Migrate from v3.2 to v3.3 schema (governance columns).

        Existing rows get policy_status 'clear' — they were indexed before the
        scanner existed and are not retroactively screened — and no
        uploaded_by, which the sidebar shows as "unattributed".
        """
        doc_columns = self._get_table_columns(conn, "documents")
        for col_name, col_def in _GOVERNANCE_DOC_COLUMNS:
            if col_name not in doc_columns:
                conn.execute(f"ALTER TABLE documents ADD COLUMN {col_name} {col_def}")
                logger.info(f"Added column {col_name} to documents table")
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_documents_policy_status
            ON documents(policy_status)
        """)

    def _migrate_to_v3_4(self, conn: sqlite3.Connection):
        """Migrate from v3.3 to v3.4 schema (per-document byte size).

        The per-collection storage cap is enforced against SUM(file_size), so
        existing rows are backfilled from the files still on disk: the stored
        copy in the collection's documents directory, or the in-place
        reference's source_path. A file that has since disappeared stays
        NULL and simply does not count.
        """
        doc_columns = self._get_table_columns(conn, "documents")
        if "file_size" not in doc_columns:
            conn.execute("ALTER TABLE documents ADD COLUMN file_size INTEGER DEFAULT NULL")
            logger.info("Added column file_size to documents table")
        self._backfill_file_sizes(conn)

    def _backfill_file_sizes(self, conn: sqlite3.Connection) -> int:
        """Fill NULL file_size rows from disk. Returns the number updated."""
        documents_dir = self.db_path.parent.parent / "documents"
        rows = conn.execute(
            "SELECT document_id, filename, source_path FROM documents WHERE file_size IS NULL"
        ).fetchall()
        updates = []
        for document_id, filename, source_path in rows:
            candidates = []
            if source_path:
                candidates.append(Path(source_path))
            if filename:
                candidates.append(documents_dir / filename)
            for candidate in candidates:
                try:
                    if candidate.is_file():
                        updates.append((candidate.stat().st_size, document_id))
                        break
                except OSError:
                    continue
        if updates:
            conn.executemany(
                "UPDATE documents SET file_size = ? WHERE document_id = ?", updates
            )
            logger.info(f"Backfilled file_size for {len(updates)} document(s)")
        return len(updates)

    def _migrate_to_v3_5(self, conn: sqlite3.Connection):
        """Migrate from v3.4 to v3.5 schema (entity graph).

        Adds the chunk_entities table powering entity-graph retrieval
        boosting (services/entity_graph.py), then backfills entities for all
        existing chunks with the heuristic extractor. The backfill is
        idempotent: rows are INSERT OR IGNORE'd, so a re-open after an
        interrupted migration simply continues.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chunk_entities (
                chunk_id TEXT NOT NULL,
                entity TEXT NOT NULL,
                PRIMARY KEY (chunk_id, entity)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chunk_entities_entity
            ON chunk_entities(entity)
        """)
        logger.info("Created chunk_entities table")

    _CHUNK_SYMBOL_COLUMNS = ("language", "symbol_name", "symbol_type", "line_start", "line_end")

    def _migrate_to_v3_6(self, conn: sqlite3.Connection):
        """Migrate to v3.6: persist code symbol metadata on chunks.

        The code extractor has always produced symbol names, types and line
        ranges, but the chunks table dropped them on insert, so nothing
        downstream (the chunk viewer, MCP context, search results) could
        say which function a passage came from. Additive, nullable columns.
        """
        existing = self._get_table_columns(conn, "chunks")
        for column, ddl in (
            ("language", "TEXT DEFAULT NULL"),
            ("symbol_name", "TEXT DEFAULT NULL"),
            ("symbol_type", "TEXT DEFAULT NULL"),
            ("line_start", "INTEGER DEFAULT NULL"),
            ("line_end", "INTEGER DEFAULT NULL"),
        ):
            if column not in existing:
                conn.execute(f"ALTER TABLE chunks ADD COLUMN {column} {ddl}")
        logger.info("Added code symbol columns to chunks")

    def _backfill_entities(self, max_per_chunk: int = 20) -> int:
        """Extract entities for every chunk that has none yet.

        Runs outside the migration transaction (it can be slow on a large
        corpus) but is safe to interrupt and re-run: only chunks with zero
        entity rows are processed. Returns the number of chunks processed.
        """
        from services.entity_graph import extract_entities

        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT c.chunk_id, c.text FROM chunks c
                LEFT JOIN chunk_entities e ON e.chunk_id = c.chunk_id
                WHERE e.chunk_id IS NULL
            """).fetchall()

        processed = 0
        batch: List[tuple] = []
        with sqlite_connect(self.db_path) as conn:
            for chunk_id, text in rows:
                for ent in extract_entities(text or "", max_per_chunk=max_per_chunk):
                    batch.append((chunk_id, ent))
                processed += 1
                if len(batch) >= 5000:
                    conn.executemany(
                        "INSERT OR IGNORE INTO chunk_entities (chunk_id, entity) VALUES (?, ?)",
                        batch,
                    )
                    conn.commit()
                    batch.clear()
            if batch:
                conn.executemany(
                    "INSERT OR IGNORE INTO chunk_entities (chunk_id, entity) VALUES (?, ?)",
                    batch,
                )
                conn.commit()
        if processed:
            logger.info(f"Backfilled entities for {processed} chunk(s)")
        return processed

    def backfill_entities_if_empty(self, max_per_chunk: int = 20) -> int:
        """Public hook: called after opening a store whose schema is current.
        No-op for fresh or fully-extracted databases."""
        with sqlite_connect(self.db_path) as conn:
            has_chunks = conn.execute("SELECT 1 FROM chunks LIMIT 1").fetchone()
            if not has_chunks:
                return 0
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunk_entities'"
            ).fetchone()
            if not table_exists:
                return 0
        return self._backfill_entities(max_per_chunk=max_per_chunk)

    def add_chunk_entities(self, chunk_entities: Dict[str, List[str]]):
        """Record extracted entities for chunks. ``chunk_entities`` maps
        chunk_id -> list of normalized entity strings."""
        rows = [
            (chunk_id, ent)
            for chunk_id, entities in chunk_entities.items()
            for ent in entities
        ]
        if not rows:
            return
        with sqlite_connect(self.db_path) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO chunk_entities (chunk_id, entity) VALUES (?, ?)",
                rows,
            )
            conn.commit()

    def entity_document_frequency(self) -> Dict[str, int]:
        """Entity -> number of distinct chunks containing it (the 'document
        frequency' in IR terms, where a chunk is the document)."""
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT entity, COUNT(*) FROM chunk_entities GROUP BY entity"
            ).fetchall()
        return {entity: count for entity, count in rows}

    def find_chunks_by_entities(
        self,
        entities: List[str],
        allowed_chunk_ids: Optional[set] = None,
        min_shared: int = 1,
        limit: int = 100,
    ) -> Dict[str, Set[str]]:
        """Chunks mentioning one or more of ``entities``.

        Returns chunk_id -> set of the queried entities it mentions. Only
        chunks sharing at least ``min_shared`` distinct entities with the
        query are returned — callers pass min_shared=2 to require a real
        cross-entity link. ``allowed_chunk_ids`` applies the same metadata
        pre-filter the vector search uses.
        """
        if not entities:
            return {}
        result: Dict[str, Set[str]] = {}
        with sqlite_connect(self.db_path) as conn:
            for start in range(0, len(entities), self._IN_CLAUSE_BATCH):
                batch = entities[start:start + self._IN_CLAUSE_BATCH]
                placeholders = ",".join("?" for _ in batch)
                rows = conn.execute(f"""
                    SELECT chunk_id, entity FROM chunk_entities
                    WHERE entity IN ({placeholders})
                """, batch).fetchall()
                for chunk_id, entity in rows:
                    if allowed_chunk_ids is not None and chunk_id not in allowed_chunk_ids:
                        continue
                    result.setdefault(chunk_id, set()).add(entity)
        if min_shared > 1:
            result = {
                cid: shared for cid, shared in result.items()
                if len(shared) >= min_shared
            }
        # Deterministic cap: most-connected chunks first.
        if len(result) > limit:
            result = dict(
                sorted(result.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:limit]
            )
        return result

    def get_total_entity_chunks(self) -> int:
        """Chunks carrying at least one entity row."""
        with sqlite_connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT chunk_id) FROM chunk_entities"
            ).fetchone()
        return row[0] if row else 0

    def _ensure_v3_1_columns(self, conn: sqlite3.Connection):
        """Ensure v3.1+ columns exist (defensive migration for runtime checks)."""
        doc_columns = self._get_table_columns(conn, "documents")
        migrated = False
        if "source_path" not in doc_columns:
            logger.warning("Running defensive v3.1 migration - columns were missing")
            self._migrate_to_v3_1(conn)
            migrated = True
        if "injection_warnings" not in doc_columns:
            logger.warning("Running defensive v3.2 migration - injection_warnings column missing")
            self._migrate_to_v3_2(conn)
            migrated = True
        if "policy_status" not in doc_columns:
            logger.warning("Running defensive v3.3 migration - governance columns missing")
            self._migrate_to_v3_3(conn)
            migrated = True
        if "file_size" not in doc_columns:
            logger.warning("Running defensive v3.4 migration - file_size column missing")
            self._migrate_to_v3_4(conn)
            migrated = True
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunk_entities'"
        ).fetchone():
            logger.warning("Running defensive v3.5 migration - chunk_entities table missing")
            self._migrate_to_v3_5(conn)
            migrated = True
        if "symbol_name" not in self._get_table_columns(conn, "chunks"):
            logger.warning("Running defensive v3.6 migration - chunk symbol columns missing")
            self._migrate_to_v3_6(conn)
            migrated = True
        if migrated:
            conn.execute("""
                INSERT OR REPLACE INTO schema_info (key, value)
                VALUES ('version', ?)
            """, (SCHEMA_VERSION,))
            conn.commit()

    def _get_table_columns(self, conn: sqlite3.Connection, table_name: str) -> set:
        """Get set of column names for a table."""
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        return {row[1] for row in cursor.fetchall()}

    def add_chunks(self, chunks: List[dict]):
        """
        Add chunk metadata to the database.

        Args:
            chunks: List of chunk metadata dictionaries
        """
        with sqlite_connect(self.db_path) as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO chunks
                (chunk_id, document_id, filename, page_number, chunk_index, text,
                 source_format, extraction_method, csv_row_number, csv_columns, csv_values,
                 language, symbol_name, symbol_type, line_start, line_end)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                (
                    chunk["chunk_id"],
                    chunk["document_id"],
                    chunk["filename"],
                    chunk["page_number"],
                    chunk["chunk_index"],
                    chunk["text"],
                    chunk.get("source_format"),
                    chunk.get("extraction_method", "text"),
                    chunk.get("csv_row_number"),
                    json.dumps(chunk["csv_columns"]) if chunk.get("csv_columns") else None,
                    json.dumps(chunk["csv_values"]) if chunk.get("csv_values") else None,

                    chunk.get("language"),

                    chunk.get("symbol_name"),

                    chunk.get("symbol_type"),

                    chunk.get("line_start"),

                    chunk.get("line_end"),
                )
                for chunk in chunks
            ])
            conn.commit()

        logger.debug(f"Added {len(chunks)} chunks to metadata store")

    @staticmethod
    def _row_to_chunk(row: sqlite3.Row) -> dict:
        """Convert a chunks row to a dict, decoding JSON-encoded CSV fields."""
        result = dict(row)
        if result.get("csv_columns"):
            result["csv_columns"] = json.loads(result["csv_columns"])
        if result.get("csv_values"):
            result["csv_values"] = json.loads(result["csv_values"])
        return result

    # SQLite's default host-parameter limit is 999; stay under it when
    # expanding IN (...) clauses.
    _IN_CLAUSE_BATCH = 900

    def get_chunks_by_chunk_ids(self, chunk_ids: List[str]) -> Dict[str, dict]:
        """
        Batch-fetch chunk metadata by chunk_id (uses the idx_chunk_id index).

        Args:
            chunk_ids: Chunk identifiers to fetch

        Returns:
            Dict mapping chunk_id -> chunk metadata dict (missing ids omitted)
        """
        wanted = list(dict.fromkeys(chunk_ids))
        if not wanted:
            return {}

        results: Dict[str, dict] = {}
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            for start in range(0, len(wanted), self._IN_CLAUSE_BATCH):
                batch = wanted[start:start + self._IN_CLAUSE_BATCH]
                placeholders = ",".join("?" for _ in batch)
                cursor = conn.execute(f"""
                    SELECT chunk_id, document_id, filename, page_number, chunk_index, text,
                           source_format, extraction_method, csv_row_number, csv_columns, csv_values,
                           language, symbol_name, symbol_type, line_start, line_end
                    FROM chunks
                    WHERE chunk_id IN ({placeholders})
                """, batch)
                for row in cursor.fetchall():
                    chunk = self._row_to_chunk(row)
                    results[chunk["chunk_id"]] = chunk
        return results

    def get_ids_for_chunk_ids(self, chunk_ids) -> Dict[str, int]:
        """
        Map chunk_ids to their stable SQLite row ids (used as FAISS ids).

        Args:
            chunk_ids: Iterable of chunk identifiers

        Returns:
            Dict mapping chunk_id -> chunks.id (missing ids omitted)
        """
        wanted = list(dict.fromkeys(chunk_ids))
        if not wanted:
            return {}

        results: Dict[str, int] = {}
        with sqlite_connect(self.db_path) as conn:
            for start in range(0, len(wanted), self._IN_CLAUSE_BATCH):
                batch = wanted[start:start + self._IN_CLAUSE_BATCH]
                placeholders = ",".join("?" for _ in batch)
                cursor = conn.execute(
                    f"SELECT chunk_id, id FROM chunks WHERE chunk_id IN ({placeholders})",
                    batch,
                )
                for chunk_id, row_id in cursor.fetchall():
                    results[chunk_id] = row_id
        return results

    def get_chunks_by_rowids(self, row_ids) -> Dict[int, dict]:
        """
        Batch-fetch chunk metadata by SQLite row id (primary-key lookup).

        Args:
            row_ids: Iterable of chunks.id values (FAISS ids)

        Returns:
            Dict mapping row id -> chunk metadata dict (missing ids omitted)
        """
        wanted = sorted({int(i) for i in row_ids})
        if not wanted:
            return {}

        results: Dict[int, dict] = {}
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            for start in range(0, len(wanted), self._IN_CLAUSE_BATCH):
                batch = wanted[start:start + self._IN_CLAUSE_BATCH]
                placeholders = ",".join("?" for _ in batch)
                cursor = conn.execute(f"""
                    SELECT id, chunk_id, document_id, filename, page_number, chunk_index, text,
                           source_format, extraction_method, csv_row_number, csv_columns, csv_values
                    FROM chunks
                    WHERE id IN ({placeholders})
                """, batch)
                for row in cursor.fetchall():
                    chunk = self._row_to_chunk(row)
                    results[chunk.pop("id")] = chunk
        return results

    def get_document_chunk_rowids(self, document_id: str) -> List[int]:
        """
        Get the SQLite row ids (FAISS ids) for all chunks of a document.

        Args:
            document_id: Document identifier

        Returns:
            List of chunks.id values, ordered by insertion
        """
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id FROM chunks WHERE document_id = ? ORDER BY id",
                (document_id,),
            )
            return [row[0] for row in cursor.fetchall()]

    def get_all_rowids_ordered(self) -> List[int]:
        """
        All chunk row ids in insertion order.

        Position i in this list corresponds to FAISS position i in a legacy
        (pre-IDMap) index — used once to migrate old indexes to id-mapped ones.
        """
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute("SELECT id FROM chunks ORDER BY id")
            return [row[0] for row in cursor.fetchall()]

    def iter_rowid_texts(self, batch_size: int = 256):
        """Stream lists of (rowid, text) in id order - the FAISS id and the
        text it embeds, which is all a re-embed needs."""
        conn = sqlite_connect(self.db_path)
        try:
            cursor = conn.execute("SELECT id, text FROM chunks ORDER BY id")
            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                yield rows
        finally:
            conn.close()

    # ── What produced the vectors ────────────────────────────────────────
    # A small key/value table in the collection's own database, so the index
    # describes itself wherever it is copied. Created on first use rather
    # than by a schema migration: older databases simply have no answer.

    def set_index_info(self, info: Dict[str, Any]) -> None:
        with sqlite_connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS index_info (key TEXT PRIMARY KEY, value TEXT)"
            )
            conn.executemany(
                "INSERT OR REPLACE INTO index_info (key, value) VALUES (?, ?)",
                [(k, json.dumps(v)) for k, v in info.items()],
            )
            conn.commit()

    def get_index_info(self) -> Dict[str, Any]:
        with sqlite_connect(self.db_path) as conn:
            try:
                rows = conn.execute("SELECT key, value FROM index_info").fetchall()
            except sqlite3.OperationalError:
                return {}
        return {k: json.loads(v) for k, v in rows}

    def get_documents_info(self, document_ids) -> Dict[str, dict]:
        """
        Batch version of get_document_info for a set of documents.

        Args:
            document_ids: Iterable of document identifiers

        Returns:
            Dict mapping document_id -> document metadata dict (missing ids omitted)
        """
        wanted = list(dict.fromkeys(document_ids))
        if not wanted:
            return {}

        results: Dict[str, dict] = {}
        with sqlite_connect(self.db_path) as conn:
            self._ensure_v3_1_columns(conn)
            conn.row_factory = sqlite3.Row
            for start in range(0, len(wanted), self._IN_CLAUSE_BATCH):
                batch = wanted[start:start + self._IN_CLAUSE_BATCH]
                placeholders = ",".join("?" for _ in batch)
                cursor = conn.execute(f"""
                    SELECT document_id, filename, num_pages, num_chunks, upload_timestamp,
                           source_format, extraction_method, embedding_model, chunk_size,
                           chunk_overlap, schema_version, source_path, source_type,
                           uploaded_by, sensitivity, policy_status
                    FROM documents
                    WHERE document_id IN ({placeholders})
                """, batch)
                for row in cursor.fetchall():
                    info = dict(row)
                    results[info["document_id"]] = info
        return results

    def get_chunks_by_document(self, document_id: str) -> List[dict]:
        """
        Get all chunks for a specific document.

        Args:
            document_id: Document identifier

        Returns:
            List of chunk metadata dictionaries
        """
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT chunk_id, document_id, filename, page_number, chunk_index, text,
                       source_format, extraction_method, csv_row_number, csv_columns, csv_values,
                       language, symbol_name, symbol_type, line_start, line_end
                FROM chunks
                WHERE document_id = ?
                ORDER BY page_number, chunk_index
            """, (document_id,))

            results = []
            for row in cursor.fetchall():
                result = dict(row)
                # Decode JSON fields
                if result.get("csv_columns"):
                    result["csv_columns"] = json.loads(result["csv_columns"])
                if result.get("csv_values"):
                    result["csv_values"] = json.loads(result["csv_values"])
                results.append(result)
            return results

    def delete_document(self, document_id: str) -> int:
        """
        Delete all chunks for a specific document.

        Args:
            document_id: Document identifier

        Returns:
            Number of chunks deleted
        """
        with sqlite_connect(self.db_path) as conn:
            # Collect chunk ids first — chunk_entities has no FK cascade, so
            # its rows must go before the chunks rows they reference vanish.
            chunk_ids = [
                row[0] for row in conn.execute(
                    "SELECT chunk_id FROM chunks WHERE document_id = ?",
                    (document_id,),
                ).fetchall()
            ]

            cursor = conn.execute("""
                DELETE FROM chunks
                WHERE document_id = ?
            """, (document_id,))

            deleted = cursor.rowcount

            if chunk_ids:
                for start in range(0, len(chunk_ids), self._IN_CLAUSE_BATCH):
                    batch = chunk_ids[start:start + self._IN_CLAUSE_BATCH]
                    placeholders = ",".join("?" for _ in batch)
                    conn.execute(
                        f"DELETE FROM chunk_entities WHERE chunk_id IN ({placeholders})",
                        batch,
                    )

            conn.execute("""
                DELETE FROM documents
                WHERE document_id = ?
            """, (document_id,))

            conn.commit()

        logger.info(f"Deleted {deleted} chunks for document {document_id}")
        return deleted

    def get_total_chunks(self) -> int:
        """Get total number of chunks in the store."""
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM chunks")
            return cursor.fetchone()[0]

    def get_document_stats(self) -> dict:
        """Collection totals straight from SQL: document count and page sum.

        Backs the stats endpoint the frontend polls — materializing the full
        document list just to sum two columns is pathological at 63k+
        documents, so the aggregation stays in the database.
        """
        with sqlite_connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(num_pages), 0), COALESCE(SUM(file_size), 0) FROM documents"
            ).fetchone()
            return {"total_documents": row[0], "total_pages": row[1], "storage_bytes": row[2]}

    def corpus_version(self) -> str:
        """A cheap token that changes whenever the corpus does.

        Document count, newest upload timestamp, chunk total and the number
        of hidden (quarantined) rows — one aggregate over the small documents
        table. Adding, replacing, deleting or quarantining a source moves at
        least one of them, which is what the answer cache keys on.
        """
        with sqlite_connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT COUNT(*), COALESCE(MAX(upload_timestamp), ''), COALESCE(SUM(num_chunks), 0),
                          COALESCE(SUM(CASE WHEN policy_status = 'quarantined' THEN 1 ELSE 0 END), 0)
                     FROM documents"""
            ).fetchone()
            return f"{row[0]}:{row[1]}:{row[2]}:{row[3]}"

    def get_storage_bytes(self) -> int:
        """Bytes of source files this collection holds (SUM of file_size).

        Rows indexed before v3.4 whose file has since vanished carry NULL and
        are not counted; everything else was backfilled from disk.
        """
        with sqlite_connect(self.db_path) as conn:
            row = conn.execute("SELECT COALESCE(SUM(file_size), 0) FROM documents").fetchone()
            return int(row[0] or 0)

    # The grouping key of a filename: its last suffix, or the whole name when
    # it has none (Makefile). Must agree with services.file_kinds.kind_key_for_filename.
    _DOC_KEY_SQL = "lower(substr({col}, length(rtrim({col}, replace({col}, '.', '')))))"

    def _doc_filter(self, q: str = "", keys: Optional[List[str]] = None, alias: str = "") -> tuple:
        """WHERE clause + params for the filename substring and kind-key filters."""
        col = f"{alias}filename"
        clauses, params = [], []
        if q:
            clauses.append(f"{col} LIKE ? ESCAPE '\\'")
            params.append(f"%{self._escape_like(q)}%")
        if keys is not None:
            keys = [k.lower() for k in keys][: self._IN_CLAUSE_BATCH]
            if not keys:
                clauses.append("0")
            else:
                placeholders = ", ".join("?" for _ in keys)
                clauses.append(f"{self._DOC_KEY_SQL.format(col=col)} IN ({placeholders})")
                params.extend(keys)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def count_documents(self, q: str = "", keys: Optional[List[str]] = None) -> int:
        """Count documents, optionally filtered by a filename substring and
        by kind keys (suffixes / bare names, see services.file_kinds)."""
        with sqlite_connect(self.db_path) as conn:
            where, params = self._doc_filter(q, keys)
            row = conn.execute(f"SELECT COUNT(*) FROM documents {where}", params).fetchone()
            return row[0]

    def count_documents_by_key(self, q: str = "") -> Dict[str, int]:
        """``{key: count}`` over every document, keyed like count_documents'
        `keys` filter, so the UI can show per-kind totals for a paged list."""
        with sqlite_connect(self.db_path) as conn:
            where, params = self._doc_filter(q)
            key = self._DOC_KEY_SQL.format(col="filename")
            rows = conn.execute(
                f"SELECT {key} AS k, COUNT(*) FROM documents {where} GROUP BY k", params
            ).fetchall()
            return {str(k or ""): int(n) for k, n in rows}

    @staticmethod
    def _escape_like(q: str) -> str:
        return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _document_row_to_dict(row) -> dict:
        r = dict(row)
        for key in ("injection_warnings", "policy_flags"):
            if r.get(key):
                try:
                    r[key] = json.loads(r[key])
                except (TypeError, ValueError):
                    r[key] = None
        return r

    # Columns every document listing returns. One definition so the sidebar,
    # the MCP tools and the review queue never disagree about the row shape.
    _DOC_LIST_COLUMNS = """
                    d.document_id,
                    d.filename,
                    d.num_chunks,
                    d.num_pages,
                    d.source_type,
                    d.source_path,
                    d.source_format,
                    d.upload_timestamp,
                    d.injection_warnings,
                    d.uploaded_by,
                    d.content_hash,
                    d.sensitivity,
                    d.policy_status,
                    d.policy_flags"""

    def list_documents_page(self, limit: int, offset: int = 0, q: str = "",
                            keys: Optional[List[str]] = None) -> List[dict]:
        """One page of documents, newest first, optionally filename-filtered.

        The unpaged list_documents() walks the whole table — fine for hundreds
        of documents, pathological for a 75k-document collection. This is the
        SQL-side pagination the sidebar uses; the shape of each row matches
        list_documents().
        """
        with sqlite_connect(self.db_path) as conn:
            self._ensure_v3_1_columns(conn)
            conn.row_factory = sqlite3.Row
            where, params = self._doc_filter(q, keys, alias="d.")
            params = list(params) + [limit, offset]
            cursor = conn.execute(f"""
                SELECT {self._DOC_LIST_COLUMNS}
                FROM documents d
                {where}
                ORDER BY COALESCE(d.upload_timestamp, '1970-01-01') DESC, d.document_id
                LIMIT ? OFFSET ?
            """, params)
            return [self._document_row_to_dict(row) for row in cursor.fetchall()]

    def list_documents(self) -> List[dict]:
        """
        List all documents with their statistics.

        The query starts FROM the ``documents`` table so files with zero
        chunks (e.g. CSV/XLSX uploads, which now live entirely in the
        structured SQL store and never get embedded) still show up in the
        sources sidebar. ``num_chunks`` and ``num_pages`` are taken from the
        authoritative document row; the chunks table is no longer consulted.

        Returns:
            List of document metadata dictionaries including source_type and source_path
        """
        with sqlite_connect(self.db_path) as conn:
            # Ensure v3.1 columns exist
            self._ensure_v3_1_columns(conn)

            conn.row_factory = sqlite3.Row
            cursor = conn.execute(f"""
                SELECT {self._DOC_LIST_COLUMNS}
                FROM documents d
                ORDER BY COALESCE(d.upload_timestamp, '1970-01-01') DESC
            """)
            return [self._document_row_to_dict(row) for row in cursor.fetchall()]

    def list_documents_by_policy_status(self, statuses) -> List[dict]:
        """Documents whose policy_status is in ``statuses`` (the review queue)."""
        wanted = [s for s in statuses if s]
        if not wanted:
            return []
        with sqlite_connect(self.db_path) as conn:
            self._ensure_v3_1_columns(conn)
            conn.row_factory = sqlite3.Row
            placeholders = ",".join("?" for _ in wanted)
            cursor = conn.execute(f"""
                SELECT {self._DOC_LIST_COLUMNS}
                FROM documents d
                WHERE d.policy_status IN ({placeholders})
                ORDER BY COALESCE(d.upload_timestamp, '1970-01-01') DESC
            """, wanted)
            return [self._document_row_to_dict(row) for row in cursor.fetchall()]

    def get_hidden_document_ids(self) -> set:
        """Ids of documents retrieval must not return (quarantined)."""
        with sqlite_connect(self.db_path) as conn:
            self._ensure_v3_1_columns(conn)
            rows = conn.execute(
                "SELECT document_id FROM documents WHERE policy_status = 'quarantined'"
            ).fetchall()
            return {r[0] for r in rows}

    def set_document_governance(self, document_id: str, *, sensitivity=..., policy_status=None,
                                policy_flags=...):
        """Update the governance columns of one document.

        ``sensitivity`` and ``policy_flags`` use Ellipsis as "leave alone" so
        None can mean "clear the override" / "no flags".
        """
        updates, params = [], []
        if sensitivity is not ...:
            updates.append("sensitivity = ?")
            params.append(sensitivity)
        if policy_status is not None:
            updates.append("policy_status = ?")
            params.append(policy_status)
        if policy_flags is not ...:
            updates.append("policy_flags = ?")
            params.append(json.dumps(policy_flags) if policy_flags else None)
        if not updates:
            return
        params.append(document_id)
        with sqlite_connect(self.db_path) as conn:
            self._ensure_v3_1_columns(conn)
            conn.execute(f"UPDATE documents SET {', '.join(updates)} WHERE document_id = ?", params)
            conn.commit()

    def add_document(self, document_id: str, filename: str, num_pages: int,
                     num_chunks: int, upload_timestamp: str,
                     source_format: str = None, extraction_method: str = "text",
                     embedding_model: str = None, chunk_size: int = None,
                     chunk_overlap: int = None, source_path: str = None,
                     source_type: str = "upload", injection_warnings: dict = None,
                     uploaded_by: str = None, content_hash: str = None,
                     sensitivity: str = None, policy_status: str = "clear",
                     policy_flags: dict = None, file_size: int = None):
        """
        Add document metadata.

        Args:
            document_id: Document identifier
            filename: Original filename
            num_pages: Number of pages
            num_chunks: Number of chunks
            upload_timestamp: ISO timestamp of upload
            source_format: Source file format (pdf, txt, etc.)
            extraction_method: How text was extracted (text, ocr, hybrid)
            embedding_model: Embedding model used
            chunk_size: Chunk size used during indexing
            chunk_overlap: Chunk overlap used during indexing
            source_path: Original filesystem path (for local references)
            source_type: Source type: 'upload' or 'local_reference'
        """
        self.add_documents([{
            "document_id": document_id,
            "filename": filename,
            "num_pages": num_pages,
            "num_chunks": num_chunks,
            "upload_timestamp": upload_timestamp,
            "source_format": source_format,
            "extraction_method": extraction_method,
            "embedding_model": embedding_model,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "source_path": source_path,
            "source_type": source_type,
            "injection_warnings": injection_warnings,
            "uploaded_by": uploaded_by,
            "content_hash": content_hash,
            "sensitivity": sensitivity,
            "policy_status": policy_status,
            "policy_flags": policy_flags,
            "file_size": file_size,
        }])

    def add_documents(self, documents: List[dict]):
        """
        Add many document metadata rows in a single transaction.

        Bulk ingest records one row per file; committing them together instead
        of once per file is a large part of what makes many-small-file corpora
        index at full speed.

        Args:
            documents: List of dicts with the same keys as add_document's
                arguments (missing optional keys use the same defaults).
        """
        if not documents:
            return

        with sqlite_connect(self.db_path) as conn:
            # Ensure v3.1 columns exist (defensive migration for cached instances)
            self._ensure_v3_1_columns(conn)

            conn.executemany("""
                INSERT OR REPLACE INTO documents
                (document_id, filename, num_pages, num_chunks, upload_timestamp,
                 source_format, extraction_method, embedding_model, chunk_size,
                 chunk_overlap, schema_version, source_path, source_type, injection_warnings,
                 uploaded_by, content_hash, sensitivity, policy_status, policy_flags, file_size)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                (
                    doc["document_id"],
                    doc["filename"],
                    doc["num_pages"],
                    doc["num_chunks"],
                    doc["upload_timestamp"],
                    doc.get("source_format"),
                    doc.get("extraction_method", "text"),
                    doc.get("embedding_model"),
                    doc.get("chunk_size"),
                    doc.get("chunk_overlap"),
                    SCHEMA_VERSION,
                    doc.get("source_path"),
                    doc.get("source_type", "upload"),
                    json.dumps(doc["injection_warnings"]) if doc.get("injection_warnings") else None,
                    doc.get("uploaded_by"),
                    doc.get("content_hash"),
                    doc.get("sensitivity"),
                    doc.get("policy_status") or "clear",
                    json.dumps(doc["policy_flags"]) if doc.get("policy_flags") else None,
                    doc.get("file_size"),
                )
                for doc in documents
            ])
            conn.commit()

    def get_all_chunks_ordered(self) -> List[dict]:
        """
        Get all chunks in order (for migration/initialization).

        Returns:
            List of all chunk metadata dictionaries
        """
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT chunk_id, document_id, filename, page_number, chunk_index, text,
                       source_format, extraction_method, csv_row_number, csv_columns, csv_values,
                       language, symbol_name, symbol_type, line_start, line_end
                FROM chunks
                ORDER BY id
            """)

            results = []
            for row in cursor.fetchall():
                result = dict(row)
                # Decode JSON fields
                if result.get("csv_columns"):
                    result["csv_columns"] = json.loads(result["csv_columns"])
                if result.get("csv_values"):
                    result["csv_values"] = json.loads(result["csv_values"])
                results.append(result)
            return results

    def iter_chunk_texts(self, substring: str = None, case_sensitive: bool = False):
        """Stream (chunk_id, document_id, filename, page_number, text) tuples.

        With `substring` (must be plain text, not a pattern), rows are
        prefiltered in SQL via INSTR so only candidate chunks ever leave the
        database — callers still re-verify the match, since SQLite's lower()
        is ASCII-only. Without it, all chunks stream through a server-side
        cursor in id order, never materializing the corpus in memory.
        """
        conn = sqlite_connect(self.db_path)
        try:
            base = """
                SELECT chunk_id, document_id, filename, page_number, text
                FROM chunks
            """
            if substring:
                if case_sensitive:
                    cursor = conn.execute(
                        base + " WHERE instr(text, ?) > 0 ORDER BY id", (substring,)
                    )
                else:
                    cursor = conn.execute(
                        base + " WHERE instr(lower(text), ?) > 0 ORDER BY id",
                        (substring.lower(),),
                    )
            else:
                cursor = conn.execute(base + " ORDER BY id")
            while True:
                rows = cursor.fetchmany(500)
                if not rows:
                    break
                yield from rows
        finally:
            conn.close()

    def get_schema_version(self) -> str:
        """Get the current schema version."""
        with sqlite_connect(self.db_path) as conn:
            try:
                cursor = conn.execute(
                    "SELECT value FROM schema_info WHERE key = 'version'"
                )
                row = cursor.fetchone()
                return row[0] if row else "1.0"
            except sqlite3.OperationalError:
                return "1.0"

    def get_document_info(self, document_id: str) -> Optional[dict]:
        """
        Get document metadata by ID.

        Args:
            document_id: Document identifier

        Returns:
            Document metadata dictionary or None
        """
        with sqlite_connect(self.db_path) as conn:
            # Ensure v3.1 columns exist (defensive migration for cached instances)
            self._ensure_v3_1_columns(conn)

            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT document_id, filename, num_pages, num_chunks, upload_timestamp,
                       source_format, extraction_method, embedding_model, chunk_size,
                       chunk_overlap, schema_version, source_path, source_type,
                       uploaded_by, content_hash, sensitivity, policy_status, policy_flags
                FROM documents
                WHERE document_id = ?
            """, (document_id,))

            row = cursor.fetchone()
            if row:
                return self._document_row_to_dict(row)
            return None

    def document_exists(self, document_id: str) -> bool:
        """
        Check if a document exists in the store.

        Args:
            document_id: Document identifier

        Returns:
            True if document exists
        """
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT 1 FROM chunks WHERE document_id = ? LIMIT 1
            """, (document_id,))
            return cursor.fetchone() is not None

    def update_document_source(self, document_id: str, source_path: str,
                                source_type: str = "local_reference"):
        """
        Update the source path and type for a document.

        Args:
            document_id: Document identifier
            source_path: Original filesystem path
            source_type: Source type: 'upload' or 'local_reference'
        """
        with sqlite_connect(self.db_path) as conn:
            # Ensure v3.1 columns exist (defensive migration for cached instances)
            self._ensure_v3_1_columns(conn)

            conn.execute("""
                UPDATE documents
                SET source_path = ?, source_type = ?
                WHERE document_id = ?
            """, (source_path, source_type, document_id))
            conn.commit()
            logger.info(f"Updated source for document {document_id}: {source_type} -> {source_path}")

    def get_document_ids_by_content_hashes(self, hashes) -> Dict[str, str]:
        """``{content_hash: document_id}`` for every hash that already has a
        document row here.

        This is the incremental-sync lookup: a folder re-indexed against the
        same collection hashes its files up front and drops the ones whose
        bytes are already in the index, instead of re-extracting and
        re-embedding the whole tree. Hashes with no row are simply absent.
        """
        wanted = list({h for h in hashes if h})
        if not wanted:
            return {}
        found: Dict[str, str] = {}
        with sqlite_connect(self.db_path) as conn:
            self._ensure_v3_1_columns(conn)
            for start in range(0, len(wanted), self._IN_CLAUSE_BATCH):
                batch = wanted[start:start + self._IN_CLAUSE_BATCH]
                placeholders = ",".join("?" for _ in batch)
                rows = conn.execute(
                    f"SELECT content_hash, document_id FROM documents "
                    f"WHERE content_hash IN ({placeholders})",
                    batch,
                ).fetchall()
                for content_hash, document_id in rows:
                    found[content_hash] = document_id
        return found

    def list_documents_under_source_root(self, source_root: str,
                                         source_type: Optional[str] = None) -> List[dict]:
        """Documents whose recorded ``source_path`` lies inside ``source_root``.

        Used by folder sync to find what an earlier sync of that folder put
        in the index, so files that have changed or disappeared on disk can
        be replaced or pruned. ``source_type`` narrows the match (folder
        sync passes its own type so it never touches in-place local
        references that merely live under the same directory).
        """
        root = Path(source_root)
        prefix = str(root)
        if not prefix.endswith(("/", "\\")):
            prefix += "/"
        like = self._escape_like(prefix) + "%"
        # Windows-style paths are recorded with backslashes; match both.
        like_win = self._escape_like(prefix[:-1] + "\\") + "%"
        params: list = [like, like_win]
        type_clause = ""
        if source_type:
            type_clause = " AND source_type = ?"
            params.append(source_type)
        with sqlite_connect(self.db_path) as conn:
            self._ensure_v3_1_columns(conn)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(f"""
                SELECT document_id, filename, source_path, source_type, content_hash, file_size
                FROM documents
                WHERE source_path IS NOT NULL
                  AND (source_path LIKE ? ESCAPE '\\' OR source_path LIKE ? ESCAPE '\\')
                  {type_clause}
            """, params).fetchall()
        result = []
        for row in rows:
            r = dict(row)
            try:
                if not Path(r["source_path"]).is_relative_to(root):
                    continue
            except (TypeError, ValueError):
                continue
            result.append(r)
        return result

    def get_filtered_chunk_ids(
        self,
        document_ids: Optional[List[str]] = None,
        source_formats: Optional[List[str]] = None,
        filenames: Optional[List[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Optional[set]:
        """Resolve a set of chunk_ids matching the given metadata filters.

        Returns ``None`` when no filter is supplied (meaning "no restriction —
        search the whole index"). Returns an explicit (possibly empty) set when
        at least one filter is active; an empty set legitimately means "no chunk
        matches", and callers should short-circuit to zero results.

        Filters compose with AND. ``source_formats`` matches on the document's
        ``source_format`` (e.g. 'pdf', 'csv'); ``date_from``/``date_to`` compare
        against ``documents.upload_timestamp`` as ISO strings (lexicographic
        comparison is correct for ISO-8601). Values are matched case-insensitively
        for formats and filenames.
        """
        has_filter = any([document_ids, source_formats, filenames, date_from, date_to])
        if not has_filter:
            return None

        clauses: List[str] = []
        params: List[Any] = []

        if document_ids:
            placeholders = ",".join("?" for _ in document_ids)
            clauses.append(f"c.document_id IN ({placeholders})")
            params.extend(document_ids)

        if source_formats:
            fmts = [f.lower().lstrip(".") for f in source_formats]
            placeholders = ",".join("?" for _ in fmts)
            # Prefer the chunk's own source_format, fall back to the document's.
            clauses.append(
                f"LOWER(COALESCE(c.source_format, d.source_format)) IN ({placeholders})"
            )
            params.extend(fmts)

        if filenames:
            names = [n.lower() for n in filenames]
            placeholders = ",".join("?" for _ in names)
            clauses.append(f"LOWER(c.filename) IN ({placeholders})")
            params.extend(names)

        if date_from:
            clauses.append("COALESCE(d.upload_timestamp, '') >= ?")
            params.append(date_from)

        if date_to:
            clauses.append("COALESCE(d.upload_timestamp, '') <= ?")
            params.append(date_to)

        where = " AND ".join(clauses)
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                f"""
                SELECT c.chunk_id
                FROM chunks c
                LEFT JOIN documents d ON d.document_id = c.document_id
                WHERE {where}
                """,
                params,
            )
            return {row[0] for row in cursor.fetchall()}

    def get_filter_facets(self) -> Dict[str, Any]:
        """Return the available filter values for this collection.

        Powers filter UIs and lets the agent discover what it can filter on:
        the distinct source formats present, and the list of documents (id,
        filename, format, timestamp). Cheap — reads only the documents table.
        """
        with sqlite_connect(self.db_path) as conn:
            self._ensure_v3_1_columns(conn)
            conn.row_factory = sqlite3.Row

            formats = [
                row[0]
                for row in conn.execute(
                    """
                    SELECT DISTINCT LOWER(source_format)
                    FROM documents
                    WHERE source_format IS NOT NULL AND source_format != ''
                    ORDER BY 1
                    """
                ).fetchall()
            ]

            documents = [
                {
                    "document_id": row["document_id"],
                    "filename": row["filename"],
                    "source_format": row["source_format"],
                    "upload_timestamp": row["upload_timestamp"],
                }
                for row in conn.execute(
                    """
                    SELECT document_id, filename, source_format, upload_timestamp
                    FROM documents
                    ORDER BY COALESCE(upload_timestamp, '1970-01-01') DESC
                    """
                ).fetchall()
            ]

        return {"source_formats": formats, "documents": documents}

    def clear_all(self):
        """Clear all chunks and documents from the metadata store."""
        with sqlite_connect(self.db_path) as conn:
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM documents")
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunk_entities'"
            ).fetchone():
                conn.execute("DELETE FROM chunk_entities")
            conn.commit()
            logger.info("Cleared all metadata from database")

"""Per-collection metadata.db readers shared by the SQLite and PostgreSQL app backends.

Collection document counts and storage bytes live in each collection's own
metadata.db (always SQLite), whichever backend holds app.db, so both backends
import these from one place instead of one importing from the other.
"""

import logging
import sqlite3
from pathlib import Path

from config import settings
from services.sqlite_utils import sqlite_connect

logger = logging.getLogger(__name__)


def _get_document_count_from_metadata(collection_id: str, data_dir: Path = None) -> int:
    """Get the actual document count from the collection's metadata.db.

    This is the source of truth for document counts, avoiding sync issues
    between app.db and the actual indexed data.

    Args:
        collection_id: Collection ID
        data_dir: Base data directory

    Returns:
        Number of unique documents in the collection's index
    """
    if data_dir is None:
        data_dir = settings.data_dir
    metadata_db_path = data_dir / "collections" / collection_id / "indexes" / "metadata.db"

    if not metadata_db_path.exists():
        return 0

    try:
        with sqlite_connect(metadata_db_path) as conn:
            try:
                # documents table is authoritative: counts zero-chunk docs
                # (CSV/XLSX live in the structured store) and avoids a
                # COUNT(DISTINCT) scan over millions of chunk rows.
                cursor = conn.execute("SELECT COUNT(*) FROM documents")
            except sqlite3.OperationalError:
                # Legacy metadata.db without a documents table
                cursor = conn.execute("SELECT COUNT(DISTINCT document_id) FROM chunks")
            return cursor.fetchone()[0]
    except Exception as e:
        logger.warning(f"Could not read metadata.db for collection {collection_id}: {e}")
        return 0


def _get_storage_bytes_from_metadata(collection_id: str, data_dir: Path = None) -> int:
    """Bytes of source files in a collection, read straight from metadata.db.

    Same shape as the document count above: a single aggregate over the
    documents table, without loading the collection's vector index, so the
    collections list can show usage against the storage cap for free.
    """
    if data_dir is None:
        data_dir = settings.data_dir
    metadata_db_path = data_dir / "collections" / collection_id / "indexes" / "metadata.db"
    if not metadata_db_path.exists():
        return 0
    try:
        with sqlite_connect(metadata_db_path) as conn:
            try:
                row = conn.execute("SELECT COALESCE(SUM(file_size), 0) FROM documents").fetchone()
            except sqlite3.OperationalError:
                return 0  # pre-v3.4 metadata.db; migrates on first open
            return int(row[0] or 0)
    except Exception as e:
        logger.warning(f"Could not read storage for collection {collection_id}: {e}")
        return 0

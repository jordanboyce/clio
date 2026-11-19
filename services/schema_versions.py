"""The database schema versions this build reads, and the rule for a newer one.

Standard library only, with no app imports, so scripts/backup.py can load it
by path to stamp and check a backup without starting the app.

Two kinds of SQLite database live in a data directory:

- each collection's ``metadata.db``, versioned in its ``schema_info`` table
  as "3.9" and so on (services/metadata_store.py);
- the application database ``app.db``, versioned in its own ``schema_info``
  table from APP_DB_SCHEMA_VERSION 1 on. An app.db with no version predates
  that and counts as 0 (services/app_database.py).

A database stamped newer than this build reads was written by a later Clio.
Migrating it backwards, or re-stamping it, would damage it silently, so the
server refuses to open it (NewerSchemaError) and says which versions met.
Before an older database is migrated, a copy goes to DATA_DIR/backups.
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path
from typing import Optional, Tuple

METADATA_SCHEMA_VERSION = "3.9"
APP_DB_SCHEMA_VERSION = 1

SNAPSHOT_DIR = "pre-upgrade"


class NewerSchemaError(RuntimeError):
    """A database was written by a newer version of Clio than this one."""


def version_key(version) -> Tuple[int, ...]:
    """"3.10" > "3.9"; anything unreadable sorts first."""
    try:
        return tuple(int(part) for part in str(version).strip().split("."))
    except (TypeError, ValueError):
        return (-1,)


def is_newer(found, supported) -> bool:
    return version_key(found) > version_key(supported)


def newer_message(what: str, path: Path, found, supported) -> str:
    return (
        f"{what} at {path} was written by a newer version of Clio (schema {found}); "
        f"this version reads up to schema {supported}. Start the newer version again, "
        f"or restore a backup taken with this one. Nothing was changed."
    )


def read_version(db_path: Path) -> Optional[str]:
    """The version in a database's schema_info table, read-only; None when it has none."""
    if not Path(db_path).is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, timeout=30)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT value FROM schema_info WHERE key = 'version'").fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return str(row[0]) if row else None


def snapshot_before_migrating(db_path: Path, backups_dir: Path, label: str,
                              found, target) -> Optional[Path]:
    """Copy ``db_path`` into ``backups_dir/pre-upgrade`` with SQLite's online
    backup, before a migration from ``found`` to ``target`` touches it.

    Returns the copy, or None when it could not be made (the caller logs it;
    a full disk must not stop an upgrade that worked before this existed).
    """
    folder = Path(backups_dir) / SNAPSHOT_DIR
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
    dest = folder / f"{safe_label}-schema-{found}-to-{target}-{stamp}.db"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(str(db_path), timeout=30)
        try:
            copy = sqlite3.connect(str(dest))
            try:
                source.backup(copy)
            finally:
                copy.close()
        finally:
            source.close()
    except (OSError, sqlite3.Error, shutil.Error):
        try:
            dest.unlink()
        except OSError:
            pass
        return None
    return dest

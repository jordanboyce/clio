"""Upgrades an operator can trust (E6): a database a newer Clio wrote is
refused, not re-stamped; app.db carries a version; an older database is
copied aside before it is migrated."""

import sqlite3
from pathlib import Path

import pytest

from config import settings
from services.app_database import SQLiteBackend
from services.metadata_store import SCHEMA_VERSION, MetadataStore
from services.schema_versions import APP_DB_SCHEMA_VERSION, NewerSchemaError, is_newer, read_version


def _stamp(db: Path, version) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT OR REPLACE INTO schema_info (key, value) VALUES ('version', ?)", (str(version),))


def _snapshots(name_part: str) -> list:
    folder = Path(settings.data_dir) / "backups" / "pre-upgrade"
    return sorted(p.name for p in folder.glob("*.db") if name_part in p.name) if folder.is_dir() else []


def test_versions_compare_as_numbers():
    assert is_newer("3.10", "3.9")
    assert not is_newer("3.9", "3.9")
    assert not is_newer("3.8", "3.9")
    assert is_newer(2, 1)


def test_a_collection_database_from_a_newer_clio_is_refused_and_left_alone(tmp_path):
    db = tmp_path / "newer" / "indexes" / "metadata.db"
    db.parent.mkdir(parents=True)
    MetadataStore(db)
    _stamp(db, "4.0")

    with pytest.raises(NewerSchemaError) as refused:
        MetadataStore(db)

    assert "schema 4.0" in str(refused.value) and f"up to schema {SCHEMA_VERSION}" in str(refused.value)
    assert read_version(db) == "4.0"


def test_an_older_collection_database_is_copied_aside_then_migrated(tmp_path):
    db = tmp_path / "olderone" / "indexes" / "metadata.db"
    db.parent.mkdir(parents=True)
    MetadataStore(db)
    _stamp(db, "3.8")

    MetadataStore(db)

    assert read_version(db) == SCHEMA_VERSION
    copies = _snapshots("olderone-metadata-schema-3.8-to-")
    assert len(copies) == 1
    copy = Path(settings.data_dir) / "backups" / "pre-upgrade" / copies[0]
    assert read_version(copy) == "3.8"


def _app_version(db: Path) -> str:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT value FROM schema_info WHERE key = 'version'").fetchone()[0]


def test_app_db_is_stamped_with_its_version(tmp_path):
    db = tmp_path / "app.db"
    SQLiteBackend(db)

    assert _app_version(db) == str(APP_DB_SCHEMA_VERSION)


def test_an_app_db_from_a_newer_clio_is_refused_and_left_alone(tmp_path):
    db = tmp_path / "app.db"
    SQLiteBackend(db)
    _stamp(db, APP_DB_SCHEMA_VERSION + 1)

    with pytest.raises(NewerSchemaError) as refused:
        SQLiteBackend(db)

    message = str(refused.value)
    assert f"schema {APP_DB_SCHEMA_VERSION + 1}" in message and f"up to schema {APP_DB_SCHEMA_VERSION}" in message
    assert _app_version(db) == str(APP_DB_SCHEMA_VERSION + 1)


def test_an_unversioned_app_db_keeps_its_rows_and_is_copied_first(tmp_path):
    """An app.db from before versions were stamped is the upgrade every
    existing install takes."""
    db = tmp_path / "app.db"
    backend = SQLiteBackend(db)
    backend.set_config("chunk_size", 900)
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE schema_info")

    reopened = SQLiteBackend(db)

    assert reopened.get_config("chunk_size") == 900
    assert _app_version(db) == str(APP_DB_SCHEMA_VERSION)
    assert _snapshots(f"app-schema-0-to-{APP_DB_SCHEMA_VERSION}")


def test_the_server_refuses_to_start_on_a_newer_collection(tmp_path, monkeypatch):
    import main
    from services.collection_service import collection_service

    db = tmp_path / "c1" / "indexes" / "metadata.db"
    db.parent.mkdir(parents=True)
    MetadataStore(db)
    monkeypatch.setattr(collection_service, "base_dir", tmp_path)
    main._refuse_newer_databases()  # same version: fine

    _stamp(db, "9.0")
    with pytest.raises(NewerSchemaError, match="schema 9.0"):
        main._refuse_newer_databases()

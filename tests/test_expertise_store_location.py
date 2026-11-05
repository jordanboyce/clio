"""Expertise packs live in DATA_DIR, however the server was started.

The store used to open data/app.db relative to the working directory. That
split a custom DATA_DIR's data across two places and stopped the server from
starting on a read-only image (Apptainer), so these tests also pin what an
install that already has packs in the old place gets after upgrading.
"""

import sqlite3

import pytest

from models.schemas import ExpertisePackCreate
from services import expertise_store as module
from services.expertise_store import ExpertiseStore


@pytest.fixture
def places(tmp_path, monkeypatch):
    """A working directory and, apart from it, a custom DATA_DIR."""
    cwd = tmp_path / "cwd"
    data_dir = tmp_path / "elsewhere" / "clio-data"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(module.settings, "data_dir", data_dir)
    return cwd, data_dir


def _old_install(cwd, name="Old pack", collection="col-1"):
    """Packs written by the previous version: <cwd>/data/app.db."""
    (cwd / "data").mkdir(exist_ok=True)
    old = ExpertiseStore(cwd / "data" / "app.db")
    pack = old.create_pack(ExpertisePackCreate(name=name, body="Check the units."))
    old.set_packs_for_collection(collection, [pack.id])
    return pack


def test_store_lives_in_data_dir_and_leaves_the_working_directory_alone(places):
    cwd, data_dir = places

    store = ExpertiseStore()
    store.create_pack(ExpertisePackCreate(name="New", body="Body"))

    assert store.db_path == data_dir / "app.db"
    assert (data_dir / "app.db").is_file()
    assert not (cwd / "data").exists()


def test_existing_packs_and_attachments_come_across(places):
    cwd, _ = places
    pack = _old_install(cwd)

    store = ExpertiseStore()

    moved = store.get_pack(pack.id)
    assert moved.name == "Old pack"
    assert moved.body == "Check the units."
    assert moved.created_at == pack.created_at
    assert store.get_attached_pack_ids("col-1") == [pack.id]


def test_the_old_database_is_left_untouched(places):
    cwd, _ = places
    pack = _old_install(cwd)

    ExpertiseStore()

    assert ExpertiseStore(cwd / "data" / "app.db").get_pack(pack.id) is not None


def test_a_deleted_pack_does_not_come_back_on_the_next_start(places):
    cwd, _ = places
    pack = _old_install(cwd)

    assert ExpertiseStore().delete_pack(pack.id)

    assert ExpertiseStore().list_packs() == []


def test_packs_already_in_data_dir_are_not_merged_with_the_old_place(places):
    """Only the first start of the store in a database looks for old packs."""
    cwd, _ = places
    ExpertiseStore().create_pack(ExpertisePackCreate(name="Current", body="Body"))
    _old_install(cwd, name="Stale")

    assert [p.name for p in ExpertiseStore().list_packs()] == ["Current"]


def test_default_data_dir_keeps_its_packs_in_place(tmp_path, monkeypatch):
    """The Docker image and a default install: the two paths are one file."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module.settings, "data_dir", module.Path("./data"))

    first = ExpertiseStore()
    pack = first.create_pack(ExpertisePackCreate(name="Kept", body="Body"))

    assert [p.id for p in ExpertiseStore().list_packs()] == [pack.id]
    assert (tmp_path / "data" / "app.db").is_file()


def test_a_fresh_install_does_not_create_the_old_location(places):
    cwd, _ = places

    assert ExpertiseStore().list_packs() == []
    assert not (cwd / "data").exists()


def test_an_unreadable_old_database_does_not_stop_startup(places, caplog):
    cwd, _ = places
    (cwd / "data").mkdir()
    (cwd / "data" / "app.db").write_bytes(b"this is not a database")

    assert ExpertiseStore().list_packs() == []
    assert "Could not copy expertise packs" in caplog.text


def test_an_old_database_with_no_packs_table_is_ignored(places, caplog):
    cwd, _ = places
    (cwd / "data").mkdir()
    conn = sqlite3.connect(cwd / "data" / "app.db")
    conn.execute("CREATE TABLE config (key TEXT)")
    conn.commit()
    conn.close()

    assert ExpertiseStore().list_packs() == []
    assert "Could not copy expertise packs" not in caplog.text

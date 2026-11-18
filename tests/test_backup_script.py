"""scripts/backup.py: backup, verify and restore round trip."""

import importlib.util
import io
import pathlib
import sqlite3
import tarfile

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "backup.py"
_spec = importlib.util.spec_from_file_location("clio_backup", _SCRIPT)
backup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup)


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    (d / "collections" / "c1" / "documents").mkdir(parents=True)
    (d / "collections" / "c1" / "documents" / "a.md").write_text("hello", encoding="utf-8")
    (d / "models").mkdir()
    (d / "models" / "big.bin").write_bytes(b"0" * 100)
    (d / "backups").mkdir()
    (d / "backups" / "old.tar.gz").write_bytes(b"x")
    conn = sqlite3.connect(d / "app.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES ('kept')")
    conn.commit()           # still open: committed rows live in the WAL
    yield d
    conn.close()


def test_round_trip_includes_wal_content_and_skips_models(data, tmp_path):
    out = tmp_path / "b.tar.gz"
    manifest = backup.backup(data, out)
    assert "app.db" in manifest["files"]
    assert not any(k.startswith(("models/", "backups/")) for k in manifest["files"])
    assert not any(k.endswith(("-wal", "-shm")) for k in manifest["files"])

    _, problems = backup.verify(out)
    assert problems == []

    target = tmp_path / "restored"
    backup.restore(out, target, force=False)
    assert (target / "collections" / "c1" / "documents" / "a.md").read_text(encoding="utf-8") == "hello"
    rows = sqlite3.connect(target / "app.db").execute("SELECT v FROM t").fetchall()
    assert rows == [("kept",)]


def test_restore_refuses_non_empty_target_without_force(data, tmp_path):
    out = tmp_path / "b.tar.gz"
    backup.backup(data, out)
    target = tmp_path / "restored"
    target.mkdir()
    (target / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit):
        backup.restore(out, target, force=False)
    assert (target / "keep.txt").exists()
    backup.restore(out, target, force=True)


def _rewrite(src, dst, mutate):
    with tarfile.open(src) as a, tarfile.open(dst, "w:gz") as b:
        for m in a.getmembers():
            f = a.extractfile(m)
            data = f.read() if f else None
            m, data = mutate(m, data)
            b.addfile(m, io.BytesIO(data) if data is not None else None)


def test_verify_detects_corruption(data, tmp_path):
    out, bad = tmp_path / "b.tar.gz", tmp_path / "bad.tar.gz"
    backup.backup(data, out)

    def corrupt(m, d):
        return (m, b"X" * m.size) if m.name.endswith("a.md") else (m, d)

    _rewrite(out, bad, corrupt)
    _, problems = backup.verify(bad)
    assert any("a.md" in p for p in problems)
    with pytest.raises(SystemExit):
        backup.restore(bad, tmp_path / "r", force=False)
    assert not (tmp_path / "r").exists()


def test_path_traversal_member_is_rejected(data, tmp_path):
    out, bad = tmp_path / "b.tar.gz", tmp_path / "bad.tar.gz"
    backup.backup(data, out)
    with tarfile.open(out) as a, tarfile.open(bad, "w:gz") as b:
        for m in a.getmembers():
            f = a.extractfile(m)
            b.addfile(m, io.BytesIO(f.read()) if f else None)
        evil = tarfile.TarInfo("../escape.txt")
        evil.size = 1
        b.addfile(evil, io.BytesIO(b"x"))
    with pytest.raises(SystemExit):
        backup.verify(bad)
    assert not (tmp_path / "escape.txt").exists()


def test_not_a_clio_backup_is_refused(tmp_path):
    p = tmp_path / "other.tar.gz"
    with tarfile.open(p, "w:gz") as t:
        ti = tarfile.TarInfo("readme")
        ti.size = 0
        t.addfile(ti, io.BytesIO(b""))
    with pytest.raises(SystemExit):
        backup.verify(p)



# ── Versions, the key, and a live vector index (E6) ─────────────────────────

def _collection(data, schema="3.9"):
    idx = data / "collections" / "c1" / "indexes"
    idx.mkdir(parents=True)
    (idx / "faiss.index").write_bytes(b"FAISS" + b"1" * 64)
    with sqlite3.connect(idx / "metadata.db") as conn:
        conn.execute("CREATE TABLE schema_info (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_info VALUES ('version', ?)", (schema,))
    (data / "secret.key").write_bytes(b"k" * 44)
    return idx


def test_the_manifest_records_versions_and_the_key(data, tmp_path):
    _collection(data)
    manifest = backup.backup(data, tmp_path / "b.tar.gz")

    assert manifest["app_version"]
    assert manifest["schemas"] == {"app.db": "0", "collections/c1/indexes/metadata.db": "3.9"}
    assert manifest["secret_key"] == "included" and "secret.key" in manifest["files"]


def test_the_key_can_be_left_out(data, tmp_path):
    _collection(data)
    manifest = backup.backup(data, tmp_path / "b.tar.gz", include_key=False)

    assert manifest["secret_key"] == "left out"
    assert "secret.key" not in manifest["files"] and "secret.key" in manifest["excluded"]
    assert backup.verify(tmp_path / "b.tar.gz")[1] == []


def test_a_backup_from_a_newer_clio_is_not_restored(data, tmp_path):
    _collection(data, schema="9.0")
    out = tmp_path / "b.tar.gz"
    backup.backup(data, out)

    with pytest.raises(SystemExit) as refused:
        backup.restore(out, tmp_path / "restored", force=False)

    assert "schema 9.0" in str(refused.value) and "reads up to 3.9" in str(refused.value)
    assert not (tmp_path / "restored").exists()


def test_an_index_replaced_during_the_backup_still_verifies(data, tmp_path, monkeypatch):
    """The index is read once into staging; the server replacing the file
    afterwards (write, then rename) cannot make the archive disagree with its
    manifest. Before, the live file was hashed and then read again."""
    idx = _collection(data)
    real_sha256 = backup._sha256
    saves = []

    def hash_then_the_server_saves(path):
        digest = real_sha256(path)
        saves.append(1)  # every save writes a different index
        (idx / "faiss.index").write_bytes(b"FAISS" + b"2" * (80 + len(saves)))
        return digest

    monkeypatch.setattr(backup, "_sha256", hash_then_the_server_saves)
    out = tmp_path / "b.tar.gz"
    backup.backup(data, out)

    assert backup.verify(out)[1] == []

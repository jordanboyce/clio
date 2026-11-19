#!/usr/bin/env python
"""Back up, verify and restore a Clio data directory.

    python scripts/backup.py backup  --data ./data --out clio-2026-10-27.tar.gz [--no-key]
    python scripts/backup.py verify  clio-2026-10-27.tar.gz
    python scripts/backup.py restore clio-2026-10-27.tar.gz --data ./data-restored

In Docker, run it in a throwaway container against the same volume so the
paths match the server's:

    docker run --rm -v clio_data:/app/data -v "$PWD:/out" clio:local \\
        python scripts/backup.py backup --data /app/data --out /out/clio.tar.gz

What it does, and why it is safe to run against a live server:

* SQLite databases (app, per-collection metadata and keyword indexes, caches)
  are copied with SQLite's online backup API, which yields a consistent
  snapshot even while the server is writing, including pending WAL content.
* Vector indexes are read once into a staging copy, before the databases
  beside them are snapshotted, and that copy is what is hashed and archived.
  The server replaces an index file whole (write, then rename), so the copy
  is always a complete index; at worst it lags its database by the last few
  writes, and a restored collection embeds the passages it lacks again on
  first open (that needs the embedding model) and drops vectors whose
  passages were deleted. A backup taken while the server runs is therefore
  restorable.
* Everything else (uploaded documents) is copied as files.
* The manifest records the Clio version that made the backup and each
  database's schema version. `restore` refuses a backup whose databases are
  newer than this copy of Clio reads, naming both versions.
* DATA_DIR/secret.key, which decrypts the credentials stored in app.db, is
  included unless --no-key is given. Without it, restored credentials cannot
  be read unless SECRET_ENCRYPTION_KEY was set; keep the key apart from the
  backup when the backup leaves the machine.
* Model caches (`models/`) and previous backups are left out: they are
  re-downloadable and large.
* A MANIFEST.json with a SHA-256 per file travels inside the archive.
  `verify` recomputes every hash and runs `PRAGMA integrity_check` on every
  database; `restore` verifies first and refuses a non-empty target without
  --force. Archive members with absolute paths or `..` are rejected.

With DB_BACKEND=postgres the application database lives in Postgres: dump it
separately (pg_dump). Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Optional

MANIFEST = "MANIFEST.json"
FORMAT = 1
EXCLUDE_TOP = {"models", "backups"}      # rebuildable / self-referential
SQLITE_MAGIC = b"SQLite format 3\x00"
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
SECRET_KEY = "secret.key"
_SERVICES = Path(__file__).resolve().parent.parent / "services"


def _load(name: str):
    """A dependency-free app module by path (importing services would start the app)."""
    spec = importlib.util.spec_from_file_location(f"_clio_backup_{name}", _SERVICES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema_kind(rel: str) -> Optional[str]:
    if rel == "app.db":
        return "app"
    if rel.endswith("/indexes/metadata.db"):
        return "metadata"
    return None


def _app_version() -> str:
    try:
        return _load("provenance").version()
    except Exception:  # noqa: BLE001 - a backup is still a backup without it
        return "unknown"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == SQLITE_MAGIC
    except OSError:
        return False


def _snapshot_sqlite(src: Path, dst: Path) -> None:
    """Consistent copy of a live database (WAL content included)."""
    source = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        dest = sqlite3.connect(dst)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()


def _integrity(path: Path) -> Optional[str]:
    """None when healthy, else the first problem reported."""
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as e:
        return str(e)
    return None if row and row[0] == "ok" else (row[0] if row else "no result")


def _walk(data: Path):
    for dirpath, dirnames, filenames in os.walk(data, followlinks=False):
        rel_dir = Path(dirpath).relative_to(data)
        if rel_dir == Path("."):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_TOP]
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.is_symlink() or name.endswith(SIDECAR_SUFFIXES):
                continue
            yield p, (rel_dir / name).as_posix()


def _is_index_file(rel: str) -> bool:
    """A vector index file the server may replace while the backup runs."""
    return "/indexes/" in f"/{rel}" and not rel.endswith(".tmp")


def backup(data: Path, out: Path, include_key: bool = True) -> dict:
    if not data.is_dir():
        raise SystemExit(f"error: {data} is not a directory")
    out = out.resolve()
    work = Path(tempfile.mkdtemp(prefix="clio-backup-"))
    files: dict[str, dict] = {}
    try:
        walked = [(src, rel) for src, rel in _walk(data) if src.resolve() != out]
        if not include_key:
            walked = [(src, rel) for src, rel in walked if rel != SECRET_KEY]
        # Index files first, each read once into staging, then the databases:
        # an index can then only lag its database, which the server repairs.
        for src, rel in walked:
            if _is_index_file(rel) and not _is_sqlite(src):
                staged = work / hashlib.sha1(rel.encode()).hexdigest()
                try:
                    shutil.copyfile(src, staged)
                except FileNotFoundError:
                    continue  # replaced or removed between the walk and the copy
                files[rel] = {"path": staged, "kind": "file"}
        schemas: dict[str, str] = {}
        for src, rel in walked:
            if rel in files:
                continue
            if _is_sqlite(src):
                staged = work / hashlib.sha1(rel.encode()).hexdigest()
                _snapshot_sqlite(src, staged)
                files[rel] = {"path": staged, "kind": "sqlite"}
                if _schema_kind(rel):
                    found = _load("schema_versions").read_version(staged)
                    schemas[rel] = found if found is not None else "0"
            elif not _is_index_file(rel):
                files[rel] = {"path": src, "kind": "file"}
        manifest = {
            "format": FORMAT,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "app_version": _app_version(),
            "schemas": schemas,
            "secret_key": "included" if include_key and SECRET_KEY in files else "left out",
            "excluded": sorted(EXCLUDE_TOP | (set() if include_key else {SECRET_KEY})),
            "files": {},
        }
        for rel, info in files.items():
            manifest["files"][rel] = {
                "sha256": _sha256(info["path"]),
                "size": info["path"].stat().st_size,
                "kind": info["kind"],
            }
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = out.with_name(out.name + ".partial")
        with tarfile.open(tmp_out, "w:gz") as tar:
            blob = json.dumps(manifest, indent=1).encode()
            ti = tarfile.TarInfo(MANIFEST)
            ti.size = len(blob)
            ti.mtime = int(time.time())
            tar.addfile(ti, io.BytesIO(blob))
            for rel, info in files.items():
                tar.add(info["path"], arcname=f"data/{rel}", recursive=False)
        os.replace(tmp_out, out)
        os.chmod(out, 0o600)     # documents may be sensitive
        return manifest
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = tar.getmembers()
    for m in members:
        p = Path(m.name)
        if p.is_absolute() or ".." in p.parts or m.issym() or m.islnk() or m.isdev():
            raise SystemExit(f"error: unsafe archive member {m.name!r}")
    return members


def _read_manifest(tar: tarfile.TarFile) -> dict:
    try:
        f = tar.extractfile(MANIFEST)
    except KeyError:
        f = None
    if f is None:
        raise SystemExit(f"error: {MANIFEST} missing; not a Clio backup")
    manifest = json.load(f)
    if manifest.get("format") != FORMAT:
        raise SystemExit(f"error: unsupported backup format {manifest.get('format')!r}")
    return manifest


def verify_extracted(root: Path, manifest: dict) -> list[str]:
    problems: list[str] = []
    for rel, meta in manifest["files"].items():
        p = root / "data" / rel
        if not p.is_file():
            problems.append(f"missing: {rel}")
        elif p.stat().st_size != meta["size"] or _sha256(p) != meta["sha256"]:
            problems.append(f"corrupt: {rel}")
        elif meta["kind"] == "sqlite":
            issue = _integrity(p)
            if issue:
                problems.append(f"database {rel}: {issue}")
    return problems


def verify(archive: Path) -> tuple[dict, list[str]]:
    if not archive.is_file():
        raise SystemExit(f"error: {archive} not found")
    with tarfile.open(archive, "r:gz") as tar:
        members = _safe_members(tar)
        manifest = _read_manifest(tar)
        with tempfile.TemporaryDirectory(prefix="clio-verify-") as tmp:
            tar.extractall(tmp, members=members, filter="data")
            problems = verify_extracted(Path(tmp), manifest)
            extra = {m.name for m in members if m.isfile()} - {MANIFEST} - {
                f"data/{r}" for r in manifest["files"]}
            problems += [f"unlisted: {n}" for n in sorted(extra)]
    return manifest, problems


def newer_schemas(manifest: dict) -> list[str]:
    """Databases in the backup that this copy of Clio cannot read."""
    versions = _load("schema_versions")
    supported = {"app": versions.APP_DB_SCHEMA_VERSION, "metadata": versions.METADATA_SCHEMA_VERSION}
    problems = []
    for rel, found in sorted((manifest.get("schemas") or {}).items()):
        kind = _schema_kind(rel)
        if kind and versions.is_newer(found, supported[kind]):
            problems.append(f"{rel} is schema {found}; this version reads up to {supported[kind]}")
    return problems


def restore(archive: Path, target: Path, force: bool) -> dict:
    manifest, problems = verify(archive)
    if problems:
        raise SystemExit("error: backup failed verification, nothing restored:\n  "
                         + "\n  ".join(problems[:20]))
    newer = newer_schemas(manifest)
    if newer:
        raise SystemExit(
            f"error: this backup was made by Clio {manifest.get('app_version', 'unknown')}, "
            "which is newer than this one. Restore it with that version. Nothing restored:\n  "
            + "\n  ".join(newer))
    if target.exists() and any(target.iterdir()) and not force:
        raise SystemExit(f"error: {target} is not empty; stop the server and pass --force "
                         "to overwrite")
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar, tempfile.TemporaryDirectory(
            prefix="clio-restore-", dir=target.parent) as tmp:
        tar.extractall(tmp, members=_safe_members(tar), filter="data")
        staged = Path(tmp) / "data"
        for item in staged.iterdir():
            dest = target / item.name
            if dest.exists():
                shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
            shutil.move(str(item), dest)
    # SQLite sidecars from a previous life of this directory would be replayed
    # against the restored database; remove them.
    for p in target.rglob("*"):
        if p.name.endswith(SIDECAR_SUFFIXES):
            p.unlink()
    return manifest


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup"); b.add_argument("--data", default=os.environ.get("DATA_DIR", "./data"))
    b.add_argument("--out", required=True)
    b.add_argument("--no-key", action="store_true",
                   help=f"leave {SECRET_KEY} out (keep it apart from the backup)")
    v = sub.add_parser("verify"); v.add_argument("archive")
    r = sub.add_parser("restore"); r.add_argument("archive")
    r.add_argument("--data", default=os.environ.get("DATA_DIR", "./data"))
    r.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "backup":
        m = backup(Path(a.data), Path(a.out), include_key=not a.no_key)
        total = sum(f["size"] for f in m["files"].values())
        print(f"backed up {len(m['files'])} files ({total / 1e6:.1f} MB) to {a.out}")
        print(f"made by Clio {m['app_version']}; {SECRET_KEY} {m['secret_key']}")
        if m["secret_key"] == "left out":
            print("note: stored credentials need that key (or SECRET_ENCRYPTION_KEY) after a restore.")
        return 0
    if a.cmd == "verify":
        m, problems = verify(Path(a.archive))
        if problems:
            print("FAILED:\n  " + "\n  ".join(problems[:50]))
            return 1
        print(f"ok: {len(m['files'])} files verified (created {m['created']}, "
              f"Clio {m.get('app_version', 'unknown')}, {SECRET_KEY} {m.get('secret_key', 'unknown')})")
        for issue in newer_schemas(m):
            print(f"note: {issue}; restore it with a newer Clio")
        return 0
    m = restore(Path(a.archive), Path(a.data), a.force)
    print(f"restored {len(m['files'])} files to {a.data}; start the server and run "
          "scripts/preflight.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())

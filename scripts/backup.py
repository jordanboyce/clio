#!/usr/bin/env python
"""Back up, verify and restore a Clio data directory.

    python scripts/backup.py backup  --data ./data --out clio-2026-10-27.tar.gz
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
* Everything else (uploaded documents, vector indexes) is copied as files.
  A vector index being written at that instant can be captured mid-save, so
  for a backup you must be able to restore, stop the server first
  (`docker compose stop`) or take a volume snapshot. The tool says so and
  never claims more than it checked.
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


def backup(data: Path, out: Path) -> dict:
    if not data.is_dir():
        raise SystemExit(f"error: {data} is not a directory")
    out = out.resolve()
    work = Path(tempfile.mkdtemp(prefix="clio-backup-"))
    files: dict[str, dict] = {}
    try:
        for src, rel in _walk(data):
            if src.resolve() == out:
                continue
            if _is_sqlite(src):
                staged = work / hashlib.sha1(rel.encode()).hexdigest()
                _snapshot_sqlite(src, staged)
                files[rel] = {"path": staged, "kind": "sqlite"}
            else:
                files[rel] = {"path": src, "kind": "file"}
        manifest = {
            "format": FORMAT,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "excluded": sorted(EXCLUDE_TOP),
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


def restore(archive: Path, target: Path, force: bool) -> dict:
    manifest, problems = verify(archive)
    if problems:
        raise SystemExit("error: backup failed verification, nothing restored:\n  "
                         + "\n  ".join(problems[:20]))
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
    v = sub.add_parser("verify"); v.add_argument("archive")
    r = sub.add_parser("restore"); r.add_argument("archive")
    r.add_argument("--data", default=os.environ.get("DATA_DIR", "./data"))
    r.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "backup":
        m = backup(Path(a.data), Path(a.out))
        total = sum(f["size"] for f in m["files"].values())
        print(f"backed up {len(m['files'])} files ({total / 1e6:.1f} MB) to {a.out}")
        print("note: stop the server first if this backup must be restorable "
              "(vector indexes are copied as files).")
        return 0
    if a.cmd == "verify":
        m, problems = verify(Path(a.archive))
        if problems:
            print("FAILED:\n  " + "\n  ".join(problems[:50]))
            return 1
        print(f"ok: {len(m['files'])} files verified (created {m['created']})")
        return 0
    m = restore(Path(a.archive), Path(a.data), a.force)
    print(f"restored {len(m['files'])} files to {a.data}; start the server and run "
          "scripts/preflight.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())

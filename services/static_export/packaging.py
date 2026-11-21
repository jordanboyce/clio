"""An export's files on disk: replacing last time's, and the zip."""

from __future__ import annotations

import json
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, Tuple

from services.collection_bundle import _work_dir
from services.static_export.additions import listed_files
from services.static_export.common import MANIFEST_FILE, PASSAGES_FILE, VECTORS_FILE


def clear_previous(out_dir: Path) -> None:
    """Remove a previous export's files from a folder, and nothing else: the
    three core files, and whatever the previous manifest listed as additions."""
    listed = []
    try:
        old = json.loads((out_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
        listed = [n for n in listed_files(old) if ".." not in n.split("/") and not n.startswith("/")]
    except (OSError, ValueError, AttributeError, TypeError, KeyError):
        pass
    folders = set()
    for name in listed:
        path = out_dir / Path(*name.split("/"))
        if path.is_file():
            path.unlink()
            folders.update(p for p in path.parents if out_dir in p.parents)
    for path in list(out_dir.glob("passages-[0-9][0-9][0-9][0-9].json")) + [
            out_dir / PASSAGES_FILE, out_dir / VECTORS_FILE, out_dir / MANIFEST_FILE]:
        if path.is_file():
            path.unlink()
    for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
        try:
            folder.rmdir()  # only when it is empty: a site's own files stay
        except OSError:
            pass


def export_zip(collection_id: str, **options: Any) -> Tuple[Path, str, Dict[str, Any]]:
    """The export as one zip. Returns (zip path, download name, audit detail).

    The caller deletes the zip once it has been sent.
    """
    work = _work_dir() / f"static-{uuid.uuid4().hex}"
    work.mkdir()
    try:
        manifest, detail = _pkg().write_export(collection_id, work, **options)
        zip_path = _work_dir() / f"static-{uuid.uuid4().hex}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if options.get("site"):
                for path in sorted(p for p in work.rglob("*") if p.is_file()):
                    zf.write(path, path.relative_to(work).as_posix())
            else:
                for name in [MANIFEST_FILE, *listed_files(manifest)]:
                    if name.startswith("../"):
                        continue  # only a whole site has files above the export folder
                    zf.write(work / Path(*name.split("/")), name)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    name = manifest["collection"]["name"] or collection_id
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in name).strip("-")
    suffix = "static-site" if options.get("site") else "static-search"
    return zip_path, f"{slug or collection_id}.{suffix}.zip", detail


def _pkg():
    from services import static_export

    return static_export

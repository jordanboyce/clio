#!/usr/bin/env python
"""Export a collection as files a static website can search.

    python scripts/export_static.py "Course handbook" --check
    python scripts/export_static.py "Course handbook" --out ../site/public/search --confirm-public
    python scripts/export_static.py "Course handbook" --out ../site/public --site --confirm-public

Writes manifest.json, passages.json (passages-0001.json, ... when there are
many) and vectors.bin into --out, replacing a previous export there, so the
yearly round is: export, review `git diff` on the passage files, commit.
Passages keep their ids and their order between exports, one per line.

The collection is named by its id or its name. Run it with the server's
environment (DATA_DIR and the rest of .env) so it reads the same data; it
only reads, and needs no network. In Docker:

    docker compose exec clio python scripts/export_static.py NAME --out /app/data/static/NAME --confirm-public

The same rules as the Export dialog apply (docs/STATIC_SEARCH.md): the
collection must be labelled Public and hold nothing marked local only, and
--confirm-public says it is cleared for release. --check prints what would
be exported, and what stands in the way, without writing anything.

--site writes a whole site into --out instead: the export under search/, the
reference page, and the embedding model and its browser runtime, so the site
needs neither Hugging Face nor a CDN (the image carries the runtime; a source
install fetches it once with scripts/vendor_static_search.mjs).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _resolve(name: str) -> str:
    from services.app_database import app_db

    rows = app_db.get_all_collections()
    by_id = [c for c in rows if c["id"] == name]
    matches = by_id or [c for c in rows if (c.get("name") or "").strip().lower() == name.strip().lower()]
    if not matches:
        raise SystemExit(f"No collection has the id or name '{name}'.")
    if len(matches) > 1:
        ids = ", ".join(c["id"] for c in matches)
        raise SystemExit(f"{len(matches)} collections are named '{name}'. Use an id: {ids}")
    return matches[0]["id"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("collection", help="collection id or name")
    parser.add_argument("--out", type=Path, help="folder to write the export into")
    parser.add_argument("--confirm-public", action="store_true",
                        help="everything in the collection is cleared for public release")
    parser.add_argument("--check", action="store_true", help="say what would be exported and stop")
    parser.add_argument("--float32", action="store_true", help="write full-precision vectors (four times the size)")
    parser.add_argument("--passages-per-file", type=int, default=None)
    parser.add_argument("--site", action="store_true",
                        help="write a ready-to-serve site, with the model and its runtime, instead of the data alone")
    args = parser.parse_args(argv)

    from services import audit, static_export

    collection_id = _resolve(args.collection)
    per_file = args.passages_per_file or static_export.DEFAULT_PASSAGES_PER_FILE

    if args.check or not args.out:
        try:
            summary = static_export.preview(collection_id, per_file)
        except static_export.StaticExportError as e:
            print(f"Cannot export: {e}", file=sys.stderr)
            return 1
        print(json.dumps(summary, indent=2))
        if not args.check:
            print("\nNothing written: pass --out FOLDER and --confirm-public to export.", file=sys.stderr)
        return 0 if summary["ready"] else 1

    # Into a folder beside the files first: a refused export leaves last
    # year's files as they were.
    staging = args.out / ".clio-static-export"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        manifest, detail = static_export.write_export(
            collection_id, staging, confirm_public=args.confirm_public,
            quantization="float32" if args.float32 else "int8", passages_per_file=per_file, site=args.site)
    except static_export.StaticExportError as e:
        shutil.rmtree(staging, ignore_errors=True)
        print(f"Cannot export: {e}", file=sys.stderr)
        return 1
    if args.site:
        # A site replaces the files it writes and leaves the folder's others alone.
        static_export.clear_previous(args.out / static_export.SITE_DATA_DIR)
        for path in sorted((p for p in staging.rglob("*") if p.is_file()), reverse=True):
            target = args.out / path.relative_to(staging)
            target.parent.mkdir(parents=True, exist_ok=True)
            path.replace(target)
        shutil.rmtree(staging, ignore_errors=True)
    else:
        static_export.clear_previous(args.out)
        for path in staging.iterdir():
            path.replace(args.out / path.name)
        staging.rmdir()

    audit.record("collection.static_export", actor="cli", collection_id=collection_id,
                 detail={**detail, "out": str(args.out)})
    files = [f["file"] for f in manifest["passages"]["files"]]
    print(f"Wrote {manifest['passages']['count']} passages from {len(manifest['documents'])} sources to {args.out}: "
          f"manifest.json, {', '.join(files)}, vectors.bin ({manifest['vectors']['bytes']:,} bytes).")
    bundled = manifest["embedding"]["browser"].get("bundled")
    if bundled:
        print(f"The site carries {bundled['model']} and transformers.js: serve {args.out} as it is.")
    else:
        print(f"A page must embed queries with {manifest['embedding']['browser']['model']} "
              f"({manifest['embedding']['pooling']} pooling).")
    if not manifest["embedding"]["probe"]:
        print("No probe vector was written: the embedding model could not be loaded here.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

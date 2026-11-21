"""What a ready-to-serve site needs besides the data: the page, the element,
web server settings, the browser runtime and the model files."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.static_export.common import (
    SITE_HOSTING_DIR, SITE_HOSTING_FILES, SITE_MODEL_FILES, SITE_PAGE_DIR, SITE_PAGE_FILES,
    SITE_RUNTIME_FILES, SITE_RUNTIME_IMAGE_DIR,
)

logger = logging.getLogger(__name__)


def site_runtime_dirs() -> List[Path]:
    from config import settings

    return [SITE_RUNTIME_IMAGE_DIR, Path(settings.data_dir) / "static-search" / "vendor"]


def site_runtime() -> Optional[Dict[str, Any]]:
    """The browser runtime a site export can carry, or None when this server
    has no copy: {"dir", "files": [names], "info": runtime.json, "bytes"}."""
    for d in _pkg().site_runtime_dirs():
        try:
            if not all((d / f).is_file() for f in SITE_RUNTIME_FILES + ("runtime.json",)):
                continue
            info = json.loads((d / "runtime.json").read_text(encoding="utf-8"))
            files = sorted(f.name for f in d.iterdir() if f.is_file())
            return {"dir": d, "files": files, "info": info, "bytes": sum((d / f).stat().st_size for f in files)}
        except (OSError, ValueError):
            continue
    return None


def site_model(clio_model: str) -> Optional[Dict[str, Any]]:
    """The model files a site export can carry: the copy fastembed loads on
    this server, laid out the way transformers.js reads a model folder.

    Returns {"files": {path in the folder: source path}, "source", "license",
    "bytes"}, or None when the model is not on disk here.
    """
    from services import embedder

    try:
        fe_id = embedder.fastembed_model_id(clio_model)
        cache = embedder.fastembed_cached_in(clio_model)
        if fe_id is None or cache is None:
            return None
        desc = embedder._fastembed_catalog().get(fe_id) or {}
        model_file = desc.get("model_file") or "model.onnx"
        model_dir = embedder.fastembed_model_dir(clio_model, cache)
        candidates = sorted((model_dir / "snapshots").glob("*")) if model_dir is not None else []
        candidates.append(Path(cache) / f"fast-{fe_id.split('/')[-1]}")
        for folder in candidates:
            onnx = folder / model_file
            if not (onnx.is_file() and (folder / "tokenizer.json").is_file() and (folder / "config.json").is_file()):
                continue
            files = {name: folder / name for name in SITE_MODEL_FILES if (folder / name).is_file()}
            files["onnx/model.onnx"] = onnx
            return {"files": files, "source": (desc.get("sources") or {}).get("hf") or fe_id,
                    "license": desc.get("license"), "bytes": sum(p.stat().st_size for p in files.values())}
    except Exception as e:  # noqa: BLE001 - only decides whether the option is offered
        logger.warning(f"Static export: could not find {clio_model}'s files for a site export: {e}")
    return None


def _page_bytes() -> int:
    files = [SITE_PAGE_DIR / n for n in SITE_PAGE_FILES] + [SITE_HOSTING_DIR / n for n in SITE_HOSTING_FILES]
    return sum(p.stat().st_size for p in files if p.is_file())


def _site_offer(browser: Optional[Dict[str, str]]) -> Dict[str, Any]:
    """Whether a ready-to-serve site can be exported here, and its extra size."""
    if browser is None:
        # Searched by keyword: the site is the page and the client, no model.
        return {"available": True, "reason": None, "model": None, "model_bytes": 0, "runtime_bytes": 0,
                "bytes": _page_bytes()}
    runtime, model = _pkg().site_runtime(), _pkg().site_model(browser["clio_model"])
    if runtime is None:
        return {"available": False, "reason": (
            "This server has no copy of the browser runtime (transformers.js), so the site has to load it and "
            "the model from the internet. The published Clio images carry it; on a source install run "
            "scripts/vendor_static_search.mjs once.")}
    if model is None:
        return {"available": False, "reason": (
            f"{browser['clio_model']} is not on this server's disk, so it cannot be included.")}
    return {"available": True, "reason": None, "model": browser["clio_model"],
            "model_bytes": model["bytes"], "runtime_bytes": runtime["bytes"],
            "bytes": model["bytes"] + runtime["bytes"]}


def _write_site(out_dir: Path, runtime: Optional[Dict[str, Any]], clio_model: Optional[str],
                model_files: Optional[Dict[str, Any]], additions: Optional[Dict[str, Any]] = None) -> List[Path]:
    """Put the page, the element, the web server settings, the runtime and the
    model beside search/. A keyword-only site has no runtime or model.
    ``additions`` (the manifest's) can add to the search page: a link to the
    reader's pages, the offline worker. Returns what it wrote."""
    written: List[Path] = []

    def put(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        written.append(target)
        shutil.copyfile(source, target)

    for name in SITE_PAGE_FILES:
        if name == "index.html" and additions:
            page = (SITE_PAGE_DIR / name).read_text(encoding="utf-8")
            if "offline" in additions:
                from services.static_export.offline import HEAD_AT_ROOT

                page = page.replace("<head>\n", "<head>\n" + HEAD_AT_ROOT, 1)
            if "reader" in additions:
                page = page.replace('<h1 id="title">Search</h1>\n', '<h1 id="title">Search</h1>\n'
                                    '  <p><a href="search/read/index.html">Read the sources</a></p>\n', 1)
            target = out_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            written.append(target)
            target.write_text(page, encoding="utf-8", newline="")
            continue
        put(SITE_PAGE_DIR / name, out_dir / name)
    for name in SITE_HOSTING_FILES:
        put(SITE_HOSTING_DIR / name, out_dir / name)
    if runtime is None or clio_model is None or model_files is None:
        return written
    for name in runtime["files"]:
        put(runtime["dir"] / name, out_dir / "vendor" / name)
    model_dir = out_dir / "models" / Path(*clio_model.split("/"))
    for name, source in model_files["files"].items():
        put(source, model_dir / name)
    note = model_dir / "SOURCE.txt"
    written.append(note)
    note.write_text(
        f"{clio_model}: the ONNX export Clio embeds with, copied from {model_files['source']} on the Hugging "
        f"Face hub. Licence: {model_files['license'] or 'see the model card'}. The site embeds queries with "
        f"this file, so its vectors match the export's.\n", encoding="utf-8", newline="\n")
    return written


def _pkg():
    """The package, for functions a caller (a test) may have replaced there."""
    from services import static_export

    return static_export

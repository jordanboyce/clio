"""What every part of the static export shares: the format's constants, the
error, the browser builds Clio has checked, and small helpers."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict, Optional

FORMAT = "clio-static-search"
VERSION = 1
# An export with no vectors (the collection's model has no browser build) is
# searched by keyword only. A v1 client would fail on it, so it says v2, which
# such a client refuses with a message instead.
KEYWORD_VERSION = 2

MANIFEST_FILE = "manifest.json"
VECTORS_FILE = "vectors.bin"
PASSAGES_FILE = "passages.json"

DEFAULT_PASSAGES_PER_FILE = 2000
QUANTIZATIONS = ("int8", "float32")

# What the export is, to the rule that keeps local-only data on this server.
DESTINATION = "a public website"

# The sentence whose vector travels in the manifest. Changing it changes
# nothing for old exports: each manifest carries its own.
PROBE_TEXT = "Which passages answer this question?"

# The ready-to-serve site (``site=True``). The runtime comes from the image,
# or from DATA_DIR/static-search/vendor on a source install
# (scripts/vendor_static_search.mjs writes either).
SITE_DATA_DIR = "search"
SITE_RUNTIME_IMAGE_DIR = Path("/opt/clio/static-search/vendor")
SITE_RUNTIME_FILES = ("transformers.min.js", "ort-wasm-simd-threaded.asyncify.js",
                      "ort-wasm-simd-threaded.asyncify.wasm")
SITE_PAGE_DIR = Path(__file__).resolve().parents[2] / "docs" / "static-search"
SITE_PAGE_FILES = ("index.html", "clio-common.js", "clio-static-search.js", "clio-search.js", "clio-related.js",
                   "clio-answers.js", "clio-cite.js", "clio-table.js")
# Settings for the common web servers (content types, caching, an optional
# CORS line), copied to the root of a whole site. Each server reads only its own.
SITE_HOSTING_DIR = SITE_PAGE_DIR / "hosting"
SITE_HOSTING_FILES = ("_headers", ".htaccess", "web.config", "README.txt")
# Besides the ONNX file, what transformers.js reads from a model folder.
SITE_MODEL_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json")

# Clio model -> the browser build that reproduces its query vectors, and how
# that model pools. Add a model here only after comparing vectors: for both
# of these, transformers.js at fp32 or fp16 gives a query vector within
# 0.00001 cosine of fastembed's, and the q8 builds drift to 0.991-0.995,
# which reorders close results (docs/STATIC_SEARCH.md has the numbers).
BROWSER_MODELS: Dict[str, Dict[str, str]] = {
    "all-MiniLM-L6-v2": {"model": "Xenova/all-MiniLM-L6-v2", "pooling": "mean"},
    "BAAI/bge-small-en-v1.5": {"model": "Xenova/bge-small-en-v1.5", "pooling": "cls"},
}


class StaticExportError(ValueError):
    """The export cannot be made; the message says why in plain words.

    ``code`` is stable: ``not_found``, ``empty``, ``local_only``,
    ``not_public``, ``model``, ``vectors``, ``confirm``, ``option``, ``site``,
    ``address``.
    """

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


def browser_model(model_name: Optional[str]) -> Optional[Dict[str, str]]:
    """The browser build for a Clio model name, or None when there is none."""
    name = (model_name or "").strip()
    if name.lower().startswith("sentence-transformers/"):
        name = name.split("/", 1)[1]
    for known, entry in BROWSER_MODELS.items():
        if known.lower() == name.lower():
            return {"clio_model": known, **entry}
    return None


def _supported() -> str:
    return " or ".join(BROWSER_MODELS)


def _title(filename: str) -> str:
    """A readable name from a file name: no extension, and in a name with no
    spaces (leave-policy_2024), hyphens and underscores read as spaces."""
    stem = Path(filename or "").stem
    if " " not in stem:
        stem = re.sub(r"[-_]+", " ", stem)
    return re.sub(r"\s+", " ", stem).strip() or filename


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()

"""Where this code came from, stated by the code itself.

Clio is MIT licensed, so derivatives are allowed and expected. What the
licence also requires is that the copyright notice travels with them. The
constants here are the markers that let a Clio-derived product be recognised
later even when the README, the name and the branding have changed: a
lineage id that nothing else on the internet uses, the source repository,
and the build that produced the running copy.

They surface in several places that survive a fork independently of one
another: the ``X-Clio-Lineage`` response header, the ``clio_lineage`` table
in app.db, the ``origin`` block of ``/api/about`` and the MCP health check,
the OCI labels on the Docker image, and the banner at the top of the built
frontend bundle. To check whether a product descends from Clio, search its
bundle, its database or its HTTP responses for ``LINEAGE_ID``.

This module is a leaf: it imports nothing from the rest of the app so that
``config`` and the database layer can import it at start-up.
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

# A token with no meaning and no other occurrence anywhere. Do not "tidy" it
# or make it configurable: its only value is that it stays exactly this.
LINEAGE_ID = "clio.helicon.58a48ab52f51"

PROJECT_NAME = "Clio"
SOURCE_URL = "https://github.com/jordanboyce/clio"
COPYRIGHT = "Copyright (c) 2026 Jordan Boyce"
LICENSE = "MIT"

# Header carried on every HTTP response (see middleware/request_meta.py).
LINEAGE_HEADER = "X-Clio-Lineage"

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args],
            capture_output=True, text=True, timeout=2, cwd=str(_REPO_ROOT),
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - provenance never breaks start-up
        return ""


@lru_cache(maxsize=1)
def version() -> str:
    """The running version: the image's CLIO_VERSION, else ``git describe``
    of a source checkout, else ``dev``."""
    v = os.environ.get("CLIO_VERSION", "").strip()
    if v and v != "dev":
        return v
    return _git("describe", "--tags", "--always", "--dirty") or v or "dev"


@lru_cache(maxsize=1)
def commit() -> str:
    """The commit this copy was built from: the image's CLIO_VCS_REF, else
    the checkout's HEAD, else ``unknown``."""
    c = os.environ.get("CLIO_VCS_REF", "").strip()
    if c and c != "unknown":
        return c
    return _git("rev-parse", "HEAD") or c or "unknown"


def origin() -> dict:
    """The lineage block, identical wherever it is reported."""
    return {
        "lineage_id": LINEAGE_ID,
        "project": PROJECT_NAME,
        "source": SOURCE_URL,
        "license": LICENSE,
        "copyright": COPYRIGHT,
    }


def build_info() -> dict:
    """Version, commit and origin together, for /api/about and friends."""
    return {"version": version(), "commit": commit(), "origin": origin()}

"""Where a parsed PRA model lives: a JSON file beside the collection's index.

``<index_dir>/pra/<document_id>.json``, written when the document is indexed
and removed with it. The source file stays the record: a missing or
unreadable model file is rebuilt by parsing the source again, so a re-index,
a backup restore or a bundle import never needs to carry these files.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from services.pra.model import PraModel

logger = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _path(index_dir: Path, document_id: str) -> Path:
    if not _SAFE_ID.match(document_id or ""):
        raise ValueError(f"Not a document id: {document_id!r}")
    return Path(index_dir) / "pra" / f"{document_id}.json"


def save_model(index_dir: Path, document_id: str, model: PraModel) -> Path:
    """Write atomically, so a reader never sees half a model."""
    path = _path(index_dir, document_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".pra-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(model.to_dict(), fh, separators=(",", ":"))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def load_model(index_dir: Path, document_id: str) -> Optional[PraModel]:
    """The stored model, or None when there is none (or it cannot be read)."""
    path = _path(index_dir, document_id)
    if not path.exists():
        return None
    try:
        return PraModel.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:  # noqa: BLE001 - a bad file means "parse the source again"
        logger.warning("Stored PRA model %s is unreadable (%s); it will be rebuilt", path.name, e)
        return None


def delete_model(index_dir: Path, document_id: str) -> bool:
    path = _path(index_dir, document_id)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False

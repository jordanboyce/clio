"""Which folders on the server may be read as sources (LOCAL_SOURCE_ROOTS).

Unset, a writer may name any path the server can read, which suits an install
on someone's own machine, where the Folder button opens a native picker. Set,
only paths inside these folders can be added or synced, and the Sources panel
offers a field for typing one: that is how a repository mounted into a container
becomes a source. A root that cannot be understood allows nothing rather than
everything, so a typo cannot quietly turn the limit off.

The paths compared are resolved, so ``..`` and symlinks cannot step outside.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from config import settings

logger = logging.getLogger(__name__)

REFUSAL = "That path is outside the folders this server allows as sources (LOCAL_SOURCE_ROOTS)."


def configured() -> List[str]:
    """The roots as written, for showing to someone who can add sources."""
    return [root.strip() for root in (settings.local_source_roots or "").split(",") if root.strip()]


def restricted() -> bool:
    return bool(configured())


def _resolved_roots() -> List[Path]:
    roots = []
    for root in configured():
        path = Path(root)
        if not path.is_absolute():
            logger.warning(f"LOCAL_SOURCE_ROOTS entry '{root}' is not an absolute path, so it allows nothing")
            continue
        roots.append(path.resolve())
    return roots


def inside(path: str | Path, folder: str | Path) -> bool:
    """``path`` is ``folder`` or lies under it, with symlinks followed."""
    try:
        return Path(path).resolve().is_relative_to(Path(folder).resolve())
    except (OSError, ValueError):  # a NUL byte, a path too long to resolve
        return False


def allowed(path: str | Path) -> bool:
    """May this path be read as a source? Always true while no roots are set."""
    if not restricted():
        return True
    return any(inside(path, root) for root in _resolved_roots())

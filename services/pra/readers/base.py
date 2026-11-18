"""What every PRA reader shares: the error people see, the limits, text decoding.

A reader is handed ``(name, bytes)`` pairs, never paths, so the same code
reads a file on disk and a member of a zip export without unpacking it.
"""

from __future__ import annotations

import codecs
from collections import OrderedDict
from typing import Dict, Iterable, List, Tuple

Source = Tuple[str, bytes]  # (file name as the person sees it, its bytes)

# How many names a warning lists before it says "and N more".
WARNING_NAMES = 8


class PraFormatError(ValueError):
    """A file that is not a readable PRA model, with a sentence saying why."""

    code = "pra_format"


def limits() -> Dict[str, int]:
    """PRA_MAX_FILE_MB (bytes) and PRA_MAX_ELEMENTS; 0 turns a limit off."""
    from config import settings

    return {
        "bytes": max(0, int(settings.pra_max_file_mb)) * 1024 * 1024,
        "elements": max(0, int(settings.pra_max_elements)),
    }


def check_size(sources: Iterable[Source]) -> None:
    """Refuse a model whose files add up to more than PRA_MAX_FILE_MB."""
    cap = limits()["bytes"]
    total = sum(len(data) for _, data in sources)
    if cap and total > cap:
        raise PraFormatError(
            f"This PRA model is {total / (1024 * 1024):.1f} MB, more than the "
            f"{cap // (1024 * 1024)} MB Clio reads (PRA_MAX_FILE_MB)."
        )


def decode(data: bytes) -> str:
    """MAR-D text: a BOM wins, then UTF-8, then Latin-1 (what SAPHIRE writes).

    BOMs inside the text (SAPHIRE concatenates per-tree sections that each
    start with one) and NULs are removed.
    """
    if data.startswith(codecs.BOM_UTF8):
        text = data[len(codecs.BOM_UTF8):].decode("utf-8", errors="replace")
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = data.decode("utf-16", errors="replace")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
    return text.replace("﻿", "").replace("\x00", "")


def name_list(names: Iterable[str], limit: int = WARNING_NAMES) -> str:
    """'A, B, C' or 'A, B, C and 12 more'."""
    items = list(dict.fromkeys(names))
    shown = ", ".join(items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


class WarningLog:
    """Warnings grouped by kind, so a model says "40 events ..." once, not 40 times."""

    def __init__(self) -> None:
        self._groups: "OrderedDict[str, List[str]]" = OrderedDict()
        self._plain: List[str] = []

    def add(self, message: str) -> None:
        if message not in self._plain:
            self._plain.append(message)

    def group(self, template: str, name: str) -> None:
        """``template`` holds ``{count}`` and ``{names}``; ``name`` joins the group."""
        self._groups.setdefault(template, []).append(name)

    def render(self) -> List[str]:
        out = list(self._plain)
        for template, names in self._groups.items():
            unique = list(dict.fromkeys(names))
            out.append(template.format(count=len(unique), names=name_list(unique)))
        return out

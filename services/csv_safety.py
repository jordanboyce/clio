"""CSV cells that a spreadsheet will not run as formulas.

A CSV export carries values someone else wrote (file names, audit targets,
actors, error text). Excel, LibreOffice and Google Sheets treat a cell that
starts with ``=``, ``+``, ``-`` or ``@`` as a formula, and a leading tab or
carriage return can hide one, so an export opened by an admin could run
``=HYPERLINK(...)`` or worse. Following the OWASP guidance, such a text cell
gets a leading single quote, which the spreadsheet shows as plain text.

Only text is touched: an int or float (a negative count, say) stays a
number. Every CSV the server produces goes through ``safe_row``.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> Any:
    """``value`` with a leading quote when it is text a spreadsheet would
    read as a formula; anything else unchanged."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def safe_row(values: Iterable[Any]) -> List[Any]:
    """``safe_cell`` over a ``csv.writer`` row."""
    return [safe_cell(v) for v in values]


def safe_dict_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """``safe_cell`` over a ``csv.DictWriter`` row."""
    return {k: safe_cell(v) for k, v in row.items()}

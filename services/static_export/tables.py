"""D1: the spreadsheets of the exported sources, for <clio-table>.

Clio keeps a spreadsheet as typed SQLite tables, not only as passages
(services/structured_store.py): a registry, ``csv_schemas``, names each
table with its source, sheet, columns and their types. Each table of an
exported source goes out as ``tables/<name>.json`` (its columns and types,
then one row per line) and ``tables/<name>.csv`` for a spreadsheet, every
cell through services/csv_safety.py so none runs as a formula. A table over
MAX_ROWS is left out, and the dialog counts it.

Read from the export's own read-only connection; table and column names
come from the registry and are checked against the table's own schema
before they are used.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import sqlite3
from typing import Any, Dict, List

from services.static_export.additions import PAGE_HOOKS, Addition, ExportContext, register
from services.static_export import reader

FOLDER = "tables"
MAX_ROWS = 5000
_IDENT = re.compile(r"^[A-Za-z0-9_]+$")


def _registry(conn: sqlite3.Connection, document_ids: List[str]) -> List[Dict[str, Any]]:
    if not document_ids:
        return []
    marks = ",".join("?" * len(document_ids))
    try:
        rows = conn.execute(
            f"SELECT document_id, sheet_name, filename, table_name, row_count, schema_json FROM csv_schemas "
            f"WHERE document_id IN ({marks}) ORDER BY filename, document_id, sheet_name", document_ids).fetchall()
    except sqlite3.OperationalError:
        return []  # no spreadsheet was ever indexed here
    out = []
    for r in rows:
        try:
            schema = json.loads(r["schema_json"])
        except ValueError:
            continue
        out.append({"document_id": r["document_id"], "sheet": r["sheet_name"] or "", "filename": r["filename"],
                    "table": r["table_name"], "rows": int(r["row_count"] or 0),
                    "columns": [c for c in schema.get("columns", []) if c.get("sql_name")]})
    return out


def _read(conn: sqlite3.Connection, entry: Dict[str, Any]):
    """(the columns kept, the rows as shown, typed values to sort by for the
    columns whose text was kept beside them). A cell is shown as the source
    wrote it where the store kept that text (dates, amounts), else as its
    value, with a true/false column as yes/no."""
    table = entry["table"]
    if not _IDENT.match(table):
        return [], [], {}
    present = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
    columns = [c for c in entry["columns"] if _IDENT.match(c["sql_name"]) and c["sql_name"] in present]
    if not columns:
        return [], [], {}
    picked, raw_at = [], {}
    for c in columns:
        picked.append(f'"{c["sql_name"]}"')
        raw = c.get("raw_sql_name")
        if raw and _IDENT.match(raw) and raw in present:
            raw_at[len(picked) - 1] = len(columns) + len(raw_at)
    raws = [f'"{c["raw_sql_name"]}"' for i, c in enumerate(columns) if i in raw_at]
    order = '"__row_number"' if "__row_number" in present else "rowid"
    found = conn.execute(f'SELECT {", ".join(picked + raws)} FROM "{table}" ORDER BY {order} LIMIT ?',
                         (MAX_ROWS + 1,)).fetchall()
    shown, sort = [], {i: [] for i in raw_at}
    for r in found:
        row = []
        for i, c in enumerate(columns):
            value = _cell(r[i])
            if i in raw_at:
                sort[i].append(value)
                text = r[raw_at[i]]
                value = text if text is not None else value
            elif str(c.get("type")) == "boolean" and value in (0, 1):
                value = "yes" if value else "no"
            row.append(value)
        shown.append(row)
    return columns, shown, sort


def _names(documents: List[Dict[str, Any]], entries: List[Dict[str, Any]]) -> List[str]:
    pages = reader.page_names(documents)
    out, used = [], set()
    for e in entries:
        stem = pages[e["document_id"]].split("/", 1)[1][:-len(".html")]
        sheet = reader._slug(e["sheet"]) if e["sheet"] else ""
        name = f"{stem}-{sheet}" if sheet else stem
        while name in used:
            name += "-x"
        used.add(name)
        out.append(f"{FOLDER}/{name}")
    return out


def _cell(v: Any) -> Any:
    if isinstance(v, bytes):
        return None
    return v


def _offer(o) -> Dict[str, Any]:
    entries = _registry(o.survey["conn"], [d["document_id"] for d in o.survey["documents"]])
    if not entries:
        return {"available": False, "reason": "No source in this export is a spreadsheet.", "bytes": 0}
    kept = [e for e in entries if e["rows"] <= MAX_ROWS]
    over = len(entries) - len(kept)
    if not kept:
        return {"available": False, "reason": f"Every table has more than {MAX_ROWS:,} rows, the most a page "
                                              f"can carry.", "bytes": 0}
    size = sum(e["rows"] * max(1, len(e["columns"])) * 24 for e in kept)
    note = f"{len(kept)} table{'' if len(kept) == 1 else 's'}"
    if over:
        note += f"; {over} over {MAX_ROWS:,} rows left out"
    return {"available": True, "reason": None, "bytes": size, "note": note + ".", "left_out": over}


def _write(ctx: ExportContext) -> Dict[str, Any]:
    conn = ctx.survey["conn"]
    entries = [e for e in _registry(conn, [d["id"] for d in ctx.documents]) if e["rows"] <= MAX_ROWS]
    names = _names(ctx.documents, entries)
    tables = []
    for entry, name in zip(entries, names):
        kept, rows, sort = _read(conn, entry)
        if len(rows) > MAX_ROWS or (not rows and entry["rows"]):
            continue
        columns = [{"name": c.get("name") or c["sql_name"], "type": c.get("type") or "text"} for c in kept]
        # Typed values to sort by, for the columns shown as written ("12 Jan 2026" sorts by its date).
        head = json.dumps({"source": entry["document_id"], "sheet": entry["sheet"], "columns": columns,
                           "sort": {str(i): v for i, v in sort.items()}}, ensure_ascii=False)[:-1]
        body = ",\n".join(json.dumps([_cell(v) for v in r], ensure_ascii=False, separators=(",", ":")) for r in rows)
        ctx.put(f"{name}.json", f'{head}, "rows": [\n{body}\n]}}\n')
        from services.csv_safety import safe_row

        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\r\n")
        writer.writerow(safe_row([c["name"] for c in columns]))
        for r in rows:
            writer.writerow(safe_row([_cell(v) for v in r]))
        ctx.put(f"{name}.csv", "﻿" + out.getvalue())
        tables.append({"source": entry["document_id"], "sheet": entry["sheet"], "file": f"{name}.json",
                       "csv": f"{name}.csv", "rows": len(rows), "columns": len(columns)})
    return {"tables": tables, "max_rows": MAX_ROWS}


def _page_part(ctx: ExportContext, doc):
    if doc is None or not ctx.site:
        return {}
    mine = [t for t in (ctx.results.get("tables") or {}).get("tables", []) if t["source"] == doc["id"]]
    if not mine:
        return {}
    boxes = "".join(f'<clio-table src="../" table="{html.escape(t["file"])}"'
                    + (f' label="{html.escape(t["sheet"])}"' if t["sheet"] else "") + "></clio-table>\n"
                    for t in mine)
    return {"head": '<script type="module" src="../../clio-table.js"></script>\n',
            "body": f'<section aria-label="Tables">\n{boxes}</section>\n'}


PAGE_HOOKS["tables"] = _page_part
register(Addition(id="tables", label="Spreadsheets, as tables to sort and filter", offer=_offer, write=_write))

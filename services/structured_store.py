"""Structured data store for CSV/XLSX tabular query support.

Stores each ingested tabular document as a typed SQLite table alongside the
semantic chunk index so the chat LLM can answer aggregation/numeric questions
by running real SQL instead of scanning row-text chunks.

The table registry lives in the same SQLite database used by MetadataStore
(the collection's metadata.db) so deletion and migration stay coherent with
the rest of the indexing stack.

Extension points
----------------
External plugin modules can register additional type detectors and
column-role detectors at import time:

  * ``register_type_extension(TypeExtension(...))``  — adds a custom type that
    is checked during column inference with the same 80% threshold used for
    built-in types.
  * ``register_role_detector(fn)``  — adds a function ``(col_name, col_type)
    → Optional[str]`` that maps column names to semantic roles.

Both registries are module-level lists; registration is idempotent in the
sense that Python only executes each ``import`` once per interpreter session.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from services.sqlite_utils import sqlite_connect

logger = logging.getLogger(__name__)


_IDENTIFIER_NORMALIZE_RE = re.compile(r'[-_\s.]+')


def _normalize_for_match(s: str) -> str:
    """Lowercase + strip quotes/extensions + collapse separators for fuzzy id matching."""
    if not s:
        return ''
    out = s.strip().strip('"').strip("'").lower()
    if out.endswith('.csv') or out.endswith('.xlsx') or out.endswith('.xls'):
        out = out.rsplit('.', 1)[0]
    return _IDENTIFIER_NORMALIZE_RE.sub('', out)


# ---------------------------------------------------------------------------
# Plugin registry
# ---------------------------------------------------------------------------

@dataclass
class TypeExtension:
    """A custom column type registered by an external plugin.

    Attributes
    ----------
    name:
        The type string stored in schema JSON, e.g. ``'currency'``.
    sqlite_type:
        The SQLite affinity to use (``'REAL'``, ``'TEXT'``, etc.).
    detector:
        Called with a single stripped string value; returns ``True`` when
        that value matches the type.  ``infer_column_type`` promotes a column
        to this type when ≥80% of non-null sample values match.
    coerce:
        Called with a single raw value; returns the coerced storage value
        (or ``None`` if the value cannot be parsed).
    """
    name: str
    sqlite_type: str
    detector: Callable[[str], bool]
    coerce: Callable[[Any], Optional[Any]]


_type_extensions: List[TypeExtension] = []
_role_detectors: List[Callable[[str, str], Optional[str]]] = []


def register_type_extension(ext: TypeExtension) -> None:
    """Register a custom type detector/coercer (called by plugins at import time)."""
    _type_extensions.append(ext)


def register_role_detector(fn: Callable[[str, str], Optional[str]]) -> None:
    """Register a column role detector (called by plugins at import time)."""
    _role_detectors.append(fn)


def detect_column_role(col_name: str, col_type: str) -> Optional[str]:
    """Dispatch to all registered role detectors; return the first match."""
    for fn in _role_detectors:
        role = fn(col_name, col_type)
        if role:
            return role
    return None


# ---------------------------------------------------------------------------
# Generic type inference helpers
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(
    r'^\d{4}-\d{1,2}-\d{1,2}(\s+\d{1,2}:\d{2}(:\d{2})?)?$'
    r'|^\d{1,2}/\d{1,2}/\d{2,4}$'
    r'|^\d{1,2}-[A-Za-z]{3}-\d{2,4}$'
    r'|^[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}$'
)

_IDENT_SAFE_RE = re.compile(r'[^A-Za-z0-9_]')

# Strings that indicate a missing/unavailable value in brokerage/bank exports.
# Treated as NULL during both type inference and value coercion.
_NULL_MARKERS = frozenset({
    '', '--', '-', 'n/a', 'na', 'nan', 'null', 'none', 'nil', '—', '–',
    'n.a.', 'n.a', '#n/a', '#na', '#null', '#value!', '#ref!', '#div/0!',
    'provide', 'missing', 'unknown', 'tbd', 'tba', 'undefined',
})

# K/M/B multiplier suffix regex (e.g. "1.5K" → 1500, "2.3M" → 2_300_000)
_KMB_RE = re.compile(r'^(-?[\d,]*\.?\d+)\s*([kmb])$', re.IGNORECASE)
_RAW_PRESERVING_TYPES = frozenset({'integer', 'real', 'currency', 'percent', 'date'})


def _parse_generic_number(s: str) -> Optional[float]:
    """Parse a numeric string including common financial formats.

    Handles:
    - Thousands commas: ``"1,234.56"`` → ``1234.56``
    - Accounting-negative parens: ``"(1,234)"`` → ``-1234``
    - K/M/B suffix: ``"1.5K"`` → ``1500``, ``"2.3M"`` → ``2_300_000``
    - Currency symbols and ISO codes are NOT stripped here — those are
      handled by the financial type extension's coerce function.
    """
    if not s:
        return None
    s_lower = s.lower()
    if s_lower in _NULL_MARKERS:
        return None

    negative = False
    if s.startswith('(') and s.endswith(')'):
        negative = True
        s = s[1:-1]

    cleaned = s.replace(',', '').strip()

    # K/M/B multiplier
    kmb = _KMB_RE.match(cleaned)
    if kmb:
        base_str, suffix = kmb.group(1).replace(',', ''), kmb.group(2).upper()
        try:
            base = float(base_str)
        except ValueError:
            return None
        multiplier = {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}[suffix]
        result = base * multiplier
        return -result if negative else result

    try:
        result = float(cleaned)
        return -result if negative else result
    except ValueError:
        return None


def _is_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return True
    if val is None:
        return False
    return str(val).strip().lower() in {'true', 'false', 'yes', 'no', 'y', 'n'}


def _to_bool(val: Any) -> Optional[int]:
    if val is None:
        return None
    if isinstance(val, bool):
        return 1 if val else 0
    s = str(val).strip().lower()
    if s in {'true', 'yes', 'y', '1'}:
        return 1
    if s in {'false', 'no', 'n', '0'}:
        return 0
    return None


def _is_date_like(val: Any) -> bool:
    return _parse_date_like(val) is not None


def _parse_date_like(val: Any) -> Optional[str]:
    """Parse a date-like value and normalize it to an ISO string."""
    if val is None:
        return None
    if isinstance(val, float) and val != val:
        return None
    if isinstance(val, datetime):
        parsed = val
        raw = val.isoformat()
    elif isinstance(val, date):
        return val.isoformat()
    else:
        raw = str(val).strip()
        if not raw or raw.lower() in _NULL_MARKERS:
            return None
        has_date_signals = (
            bool(_DATE_RE.match(raw))
            or bool(re.search(r'\d{1,4}[/-]\d{1,2}[/-]\d{1,4}', raw))
            or bool(re.search(r'[A-Za-z]{3,}', raw))
        )
        if not has_date_signals:
            return None
        parsed = pd.to_datetime(raw, errors='coerce')
        if pd.isna(parsed):
            return None
        if hasattr(parsed, "to_pydatetime"):
            parsed = parsed.to_pydatetime()

    has_time = bool(re.search(r'\d:\d|am\b|pm\b', raw, flags=re.IGNORECASE))
    if parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0 and parsed.microsecond == 0 and not has_time:
        return parsed.date().isoformat()
    return parsed.replace(microsecond=0).isoformat()


def _numeric_stats(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {}
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    total = sum(sorted_vals)
    mean = total / n

    def percentile(p: float) -> float:
        k = max(0, min(n - 1, int(round((p / 100.0) * (n - 1)))))
        return sorted_vals[k]

    return {
        'min': sorted_vals[0],
        'max': sorted_vals[-1],
        'mean': mean,
        'sum': total,
        'p25': percentile(25),
        'p50': percentile(50),
        'p75': percentile(75),
        'count': n,
    }


def infer_column_type(values: List[Any]) -> Dict[str, Any]:
    """Inspect a column's values and return its inferred type + metadata.

    Uses an 80% threshold: if ≥80% of non-null values satisfy a type's
    criteria, the whole column is promoted to that type.  Non-conforming
    minority values become NULL at storage time.  This correctly handles
    brokerage columns that mix numeric cells with sentinel strings like
    ``'--'``, ``'N/A'``, or ``'Provide'``.

    Priority order:
      1. Boolean  (100% match, ≤3 distinct values)
      2. Date     (≥80%)
      3. Registered type extensions  (≥80% — e.g. currency, percent)
      4. Integer  (≥80%, all parsed values are whole numbers)
      5. Real     (≥80%)
      6. Text     (fallback)
    """
    # Common null markers count as missing, not as type-breaking values
    non_null = [
        v for v in values
        if v is not None and str(v).strip().lower() not in _NULL_MARKERS
    ]
    null_count = len(values) - len(non_null)

    if not non_null:
        return {'type': 'text', 'null_count': null_count, 'distinct_count': 0, 'stats': None}

    sample = non_null[:2000]
    sample_strs = [str(v).strip() for v in sample]
    n = len(sample)
    threshold = 0.80
    distinct = len(set(sample_strs))

    # 1. Boolean (100% match, ≤3 distinct)
    if all(_is_bool(v) for v in sample) and distinct <= 3:
        return {'type': 'boolean', 'null_count': null_count, 'distinct_count': distinct, 'stats': None}

    # 2. Date (≥80%)
    date_count = sum(1 for s in sample_strs if _is_date_like(s))
    if date_count / n >= threshold:
        parsed_dates = [d for d in (_parse_date_like(s) for s in sample_strs) if d is not None]
        return {
            'type': 'date',
            'null_count': null_count,
            'distinct_count': distinct,
            'stats': {'min': min(parsed_dates), 'max': max(parsed_dates)} if parsed_dates else None,
        }

    # 3. Registered type extensions (≥80%)
    for ext in _type_extensions:
        match_count = sum(1 for s in sample_strs if ext.detector(s))
        if match_count / n >= threshold:
            coerced = [ext.coerce(v) for v in sample]
            numeric_vals = [c for c in coerced if c is not None]
            if numeric_vals:
                return {
                    'type': ext.name,
                    'null_count': null_count,
                    'distinct_count': distinct,
                    'stats': _numeric_stats(numeric_vals),
                }

    # 4 & 5. Generic numeric OR extension-coercible (≥80%).
    # Fallback when no single extension hit 80% on its own (e.g. a column
    # mixing "$1,591.20" and "(408.80)" — currency matches once, generic
    # matches once, neither clears threshold individually). Any value that
    # parses as a number via either path counts toward promotion.
    numeric_vals: List[float] = []
    int_count = 0
    had_extension_match = False
    for s in sample_strs:
        num: Optional[float] = _parse_generic_number(s)
        if num is None:
            for ext in _type_extensions:
                val = ext.coerce(s)
                if isinstance(val, (int, float)):
                    num = float(val)
                    had_extension_match = True
                    break
        if num is not None:
            numeric_vals.append(num)
            if num == int(num):
                int_count += 1

    numeric_frac = len(numeric_vals) / n
    if numeric_frac >= threshold:
        # If any value needed extension coercion (currency / percent formatting),
        # classify as real — an "integer" column shouldn't quietly absorb
        # currency-formatted cells even if they happen to be whole dollars.
        if int_count == len(numeric_vals) and not had_extension_match:
            return {'type': 'integer', 'null_count': null_count, 'distinct_count': distinct,
                    'stats': _numeric_stats(numeric_vals)}
        return {'type': 'real', 'null_count': null_count, 'distinct_count': distinct,
                'stats': _numeric_stats(numeric_vals)}

    # 6. Text fallback
    return {'type': 'text', 'null_count': null_count, 'distinct_count': distinct, 'stats': None}


def sanitize_identifier(name: str, fallback: str = 'col') -> str:
    """Convert an arbitrary string to a safe SQL identifier (ASCII, <=60 chars)."""
    cleaned = _IDENT_SAFE_RE.sub('_', str(name)).strip('_')
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f'_{cleaned}'
    return cleaned[:60]


# ---------------------------------------------------------------------------
# SQL validation
# ---------------------------------------------------------------------------

_FORBIDDEN_KEYWORDS = {
    'INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE', 'ATTACH',
    'DETACH', 'PRAGMA', 'VACUUM', 'REINDEX', 'TRUNCATE',
    'GRANT', 'REVOKE', 'BEGIN', 'COMMIT', 'ROLLBACK', 'SAVEPOINT',
}


class SQLValidationError(ValueError):
    """Raised when a user/LLM-supplied SQL string fails validation."""


def validate_select(sql: str) -> str:
    """Validate that *sql* is a single read-only SELECT / WITH statement.

    Returns the cleaned SQL (no trailing semicolon). Raises
    :class:`SQLValidationError` on any violation.
    """
    if not sql or not sql.strip():
        raise SQLValidationError('SQL query is empty')

    cleaned = sql.strip().rstrip(';').strip()
    if ';' in cleaned:
        raise SQLValidationError('Multiple statements are not allowed')

    # Blank out quoted string literals so keywords inside them don't trip the check
    stripped = re.sub(r"'([^'\\]|\\.)*'", "''", cleaned)
    stripped = re.sub(r'"([^"\\]|\\.)*"', '""', stripped)
    upper = stripped.upper()

    for kw in _FORBIDDEN_KEYWORDS:
        if re.search(rf'\b{kw}\b', upper):
            raise SQLValidationError(f'Forbidden keyword: {kw}')

    first_word = re.match(r'^\s*(\w+)', upper)
    if not first_word or first_word.group(1) not in {'SELECT', 'WITH'}:
        raise SQLValidationError('Only SELECT / WITH queries are allowed')

    return cleaned


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------

# Maps inferred type names to SQLite column affinities.
# Plugin types are added dynamically from _type_extensions at create_table time.
_TYPE_TO_SQLITE: Dict[str, str] = {
    'integer': 'INTEGER',
    'real':    'REAL',
    'boolean': 'INTEGER',
    'date':    'TEXT',
    'text':    'TEXT',
}


def _coerce_value(val: Any, inferred_type: str) -> Any:
    """Coerce a raw value to its storage form for the given inferred type."""
    if val is None:
        return None
    if isinstance(val, float) and val != val:  # NaN
        return None
    s = str(val).strip()
    if not s or s.lower() in _NULL_MARKERS:
        return None

    if inferred_type in {'integer', 'real'}:
        num = _parse_generic_number(s)
        if num is None:
            # Fall back to any registered extension coercer. Covers the case
            # where a 'real' column holds currency-formatted cells (e.g.
            # "$1,591.20") that _parse_generic_number intentionally ignores.
            for ext in _type_extensions:
                val = ext.coerce(s)
                if isinstance(val, (int, float)):
                    num = float(val)
                    break
        if num is None:
            return None
        return int(num) if inferred_type == 'integer' else num

    if inferred_type == 'boolean':
        return _to_bool(val)

    if inferred_type == 'date':
        return _parse_date_like(val)

    # Delegate to the registered extension coercer for plugin types
    for ext in _type_extensions:
        if ext.name == inferred_type:
            return ext.coerce(val)

    # date and text: store as string
    return s


def _raw_storage_value(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, float) and val != val:
        return None
    s = str(val).strip()
    if not s or s.lower() in _NULL_MARKERS:
        return None
    return s


def _should_preserve_raw(values: List[Any], inferred_type: str) -> bool:
    if inferred_type not in _RAW_PRESERVING_TYPES:
        return False
    return any(_raw_storage_value(v) is not None for v in values)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

_DEFAULT_MAX_QUERY_ROWS = 1000


class StructuredStore:
    """Manages per-document typed SQL tables and their schema registry.

    Tables are stored inside the collection's metadata SQLite database using
    ``csv_data_{document_id}[_sheet]`` names. The ``csv_schemas`` registry
    table maps documents to their physical tables and caches inferred
    schema/roles.

    This class is intentionally domain-agnostic. Domain-specific behaviour
    (custom types, column-role detection) can be layered on by a plugin module
    that registers itself with :func:`register_type_extension` and
    :func:`register_role_detector`.
    """

    META_TABLE_PREFIX = 'csv_data_'
    SCHEMA_REGISTRY = 'csv_schemas'

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite_connect(self.db_path) as conn:
            conn.execute(f'''
                CREATE TABLE IF NOT EXISTS {self.SCHEMA_REGISTRY} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL,
                    sheet_name TEXT NOT NULL DEFAULT '',
                    filename TEXT NOT NULL,
                    table_name TEXT NOT NULL UNIQUE,
                    row_count INTEGER NOT NULL,
                    column_count INTEGER NOT NULL,
                    schema_json TEXT NOT NULL,
                    financial_roles TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(document_id, sheet_name)
                )
            ''')
            conn.execute(
                f'CREATE INDEX IF NOT EXISTS idx_csv_schemas_doc '
                f'ON {self.SCHEMA_REGISTRY}(document_id)'
            )
            conn.commit()

    def _table_name(self, document_id: str, sheet_name: str = '') -> str:
        safe_doc = sanitize_identifier(document_id, fallback='doc')
        if sheet_name:
            safe_sheet = sanitize_identifier(sheet_name, fallback='sheet')
            return f'{self.META_TABLE_PREFIX}{safe_doc}_{safe_sheet}'
        return f'{self.META_TABLE_PREFIX}{safe_doc}'

    def create_table(
        self,
        document_id: str,
        filename: str,
        columns: List[str],
        rows: List[Dict[str, Any]],
        sheet_name: str = '',
        role_overrides: Optional[Dict[str, str]] = None,
        type_overrides: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Create a typed table + schema entry for an ingested tabular file.

        Parameters
        ----------
        role_overrides:
            Optional mapping of original column name → semantic role string.
            Overrides the heuristic role detector result.  Supplied by the
            vendor profile system (P0.4).
        type_overrides:
            Optional mapping of original column name → type string
            (``'real'``, ``'currency'``, ``'percent'``, ``'date'``, etc.).
            Overrides the heuristic ``infer_column_type`` result.  Supplied
            by the vendor profile system (P0.4).
        """
        if not columns:
            raise ValueError('Cannot create structured table with no columns')

        role_overrides = role_overrides or {}
        type_overrides = type_overrides or {}

        col_values: Dict[str, List[Any]] = {col: [row.get(col) for row in rows] for col in columns}

        col_infos: List[Dict[str, Any]] = []
        used_idents = set()
        for col in columns:
            info = infer_column_type(col_values[col])
            # P0.4: apply profile type override if present
            if col in type_overrides:
                info = dict(info, type=type_overrides[col])
            base_ident = sanitize_identifier(col, fallback='col')
            ident = base_ident
            counter = 1
            while ident.lower() in used_idents:
                counter += 1
                ident = f'{base_ident}_{counter}'
            used_idents.add(ident.lower())

            raw_sql_name: Optional[str] = None
            if _should_preserve_raw(col_values[col], info['type']):
                raw_ident = f'{ident}__raw'
                raw_counter = 1
                while raw_ident.lower() in used_idents:
                    raw_counter += 1
                    raw_ident = f'{ident}__raw_{raw_counter}'
                used_idents.add(raw_ident.lower())
                raw_sql_name = raw_ident

            # P0.4: profile role override takes precedence over heuristic
            role = role_overrides.get(col) or detect_column_role(col, info['type'])
            sample_values = [
                str(v)[:100]
                for v in col_values[col][:5]
                if v is not None and str(v).strip() != ''
            ]
            col_infos.append({
                'name': col,
                'sql_name': ident,
                'type': info['type'],
                'role': role,
                'raw_sql_name': raw_sql_name,
                'null_count': info['null_count'],
                'distinct_count': info['distinct_count'],
                'stats': info['stats'],
                'samples': sample_values,
            })

        table_name = self._table_name(document_id, sheet_name)

        # Build SQLite affinity map from core types + registered extensions
        type_to_sqlite = dict(_TYPE_TO_SQLITE)
        for ext in _type_extensions:
            type_to_sqlite[ext.name] = ext.sqlite_type

        with sqlite_connect(self.db_path) as conn:
            conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            col_defs = ['__row_number INTEGER PRIMARY KEY']
            for info in col_infos:
                sqlite_type = type_to_sqlite.get(info['type'], 'TEXT')
                col_defs.append(f'"{info["sql_name"]}" {sqlite_type}')
                if info.get('raw_sql_name'):
                    col_defs.append(f'"{info["raw_sql_name"]}" TEXT')
            conn.execute(f'CREATE TABLE "{table_name}" ({", ".join(col_defs)})')

            insert_columns = ['__row_number']
            for c in col_infos:
                insert_columns.append(c['sql_name'])
                if c.get('raw_sql_name'):
                    insert_columns.append(c['raw_sql_name'])
            placeholders = ', '.join(['?'] * len(insert_columns))
            insert_cols_sql = '"' + '", "'.join(insert_columns) + '"'
            insert_sql = f'INSERT INTO "{table_name}" ({insert_cols_sql}) VALUES ({placeholders})'

            batch: List[List[Any]] = []
            for idx, row in enumerate(rows, start=1):
                values: List[Any] = [idx]
                for info in col_infos:
                    raw_value = row.get(info['name'])
                    values.append(_coerce_value(raw_value, info['type']))
                    if info.get('raw_sql_name'):
                        values.append(_raw_storage_value(raw_value))
                batch.append(values)
                if len(batch) >= 500:
                    conn.executemany(insert_sql, batch)
                    batch = []
            if batch:
                conn.executemany(insert_sql, batch)

            financial_roles = {c['sql_name']: c['role'] for c in col_infos if c['role']}
            schema_payload = {
                'document_id': document_id,
                'sheet_name': sheet_name,
                'filename': filename,
                'table_name': table_name,
                'row_count': len(rows),
                'column_count': len(col_infos),
                'columns': col_infos,
            }

            conn.execute(
                f'''INSERT INTO {self.SCHEMA_REGISTRY}
                    (document_id, sheet_name, filename, table_name, row_count,
                     column_count, schema_json, financial_roles)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(document_id, sheet_name) DO UPDATE SET
                        filename = excluded.filename,
                        table_name = excluded.table_name,
                        row_count = excluded.row_count,
                        column_count = excluded.column_count,
                        schema_json = excluded.schema_json,
                        financial_roles = excluded.financial_roles,
                        created_at = CURRENT_TIMESTAMP''',
                (
                    document_id, sheet_name, filename, table_name,
                    len(rows), len(col_infos),
                    json.dumps(schema_payload, default=str),
                    json.dumps(financial_roles),
                ),
            )
            conn.commit()

        logger.info(
            f"Created structured table {table_name}: {len(rows)} rows × {len(col_infos)} cols "
            f"(roles: {sorted(set(financial_roles.values())) or 'none'})"
        )

        # P0.3: auto-create a by-symbol rollup view when a symbol/ticker role is detected.
        # Brokerage files typically have one row per tax lot; the view aggregates to positions.
        symbol_col_info = next(
            (c for c in col_infos if c.get('role') in ('ticker', 'cusip', 'isin')),
            None,
        )
        if symbol_col_info:
            self._create_symbol_rollup_view(table_name, col_infos, symbol_col_info['sql_name'])

        return schema_payload

    def _create_symbol_rollup_view(
        self,
        table_name: str,
        col_infos: List[Dict[str, Any]],
        symbol_col: str,
    ) -> None:
        """Create a ``<table>__by_symbol`` VIEW aggregating lot-level rows per symbol.

        SUMs market_value, cost_basis, pnl, and quantity across lots; COUNTs
        lot rows; and computes a weighted-average unit cost when both cost_basis
        and quantity are present. Non-numeric / non-role columns are omitted to
        avoid ambiguous GROUP BY semantics.

        Excludes parent-rollup rows (Pershing-style files include both a
        position-level summary row AND its child lot rows for the same
        security; summing both double-counts) and footer/total rows.
        """
        SUMMABLE_ROLES = {'market_value', 'cost_basis', 'pnl', 'quantity'}
        FIRST_ROLES = {'name', 'asset_class', 'sector', 'region', 'currency', 'account', 'rating'}

        # Lookup helpers
        sql_name_by_norm: Dict[str, str] = {}
        for c in col_infos:
            orig = (c.get('name') or '').strip().lower()
            norm = re.sub(r'[\s_\-]+', '_', orig).strip('_')
            if norm:
                sql_name_by_norm[norm] = c['sql_name']
            sql_name_by_norm[c['sql_name'].lower()] = c['sql_name']

        # A column that's populated only on lot rows (and blank on parent
        # rollups) is the cleanest signal that lot/rollup duplication exists.
        # Pershing uses "Original Quantity"; other custodians may use
        # "Lot Quantity", "Acquired Quantity", "Open Date", etc.
        LOT_ONLY_HINTS = (
            'original_quantity', 'original_total_cost', 'original_adjusted_cost',
            'lot_quantity', 'acquired_quantity', 'acquisition_date', 'open_date',
        )
        lot_marker_col: Optional[str] = next(
            (sql_name_by_norm[h] for h in LOT_ONLY_HINTS if h in sql_name_by_norm),
            None,
        )

        # Identify identifier columns we'll fall back to when symbol is blank
        # so CUSIP-only securities (UITs, munis, CDs) each get their own bucket
        # instead of all collapsing into a single empty-symbol row.
        cusip_col = next(
            (c['sql_name'] for c in col_infos if c.get('role') == 'cusip' and c['sql_name'] != symbol_col),
            None,
        )
        secid_col = sql_name_by_norm.get('security_identifier') or sql_name_by_norm.get('security_id')
        name_col = next(
            (c['sql_name'] for c in col_infos if c.get('role') == 'name'),
            None,
        )

        group_key_parts = [f'NULLIF(TRIM("{symbol_col}"), \'\')']
        for fallback in (cusip_col, secid_col, name_col):
            if fallback and fallback != symbol_col:
                group_key_parts.append(f'NULLIF(TRIM(CAST("{fallback}" AS TEXT)), \'\')')
        group_key = f'COALESCE({", ".join(group_key_parts)})' if len(group_key_parts) > 1 else group_key_parts[0]

        select_parts: List[str] = [f'{group_key} AS "{symbol_col}"']
        cost_basis_col: Optional[str] = None
        qty_col: Optional[str] = None

        for c in col_infos:
            col = c['sql_name']
            role = c.get('role')
            if col == symbol_col:
                continue
            if role in SUMMABLE_ROLES:
                select_parts.append(f'SUM("{col}") AS "{col}"')
                if role == 'cost_basis':
                    cost_basis_col = col
                if role == 'quantity':
                    qty_col = col
            elif role in FIRST_ROLES:
                select_parts.append(f'MAX("{col}") AS "{col}"')

        select_parts.append('COUNT(*) AS lot_count')

        if cost_basis_col and qty_col:
            select_parts.append(
                f'CASE WHEN SUM("{qty_col}") != 0 '
                f'THEN SUM("{cost_basis_col}") / SUM("{qty_col}") '
                f'ELSE NULL END AS unit_cost'
            )

        # Build the WHERE clause:
        #   - drop parent rollup rows (lot marker column is NULL/blank)
        #   - drop footer "TOTAL" rows (have no quantity)
        #   - drop rows where the grouping key is entirely NULL
        where_clauses: List[str] = [f'{group_key} IS NOT NULL']
        if lot_marker_col:
            where_clauses.append(
                f'("{lot_marker_col}" IS NOT NULL '
                f'AND TRIM(CAST("{lot_marker_col}" AS TEXT)) != \'\')'
            )
        if qty_col:
            where_clauses.append(f'"{qty_col}" IS NOT NULL')
        # Filter common footer markers (case-insensitive) appearing in
        # name/security identifier columns
        footer_marker_cols = [c for c in (name_col, secid_col, symbol_col) if c]
        for fc in footer_marker_cols:
            where_clauses.append(
                f'(UPPER(COALESCE(CAST("{fc}" AS TEXT), \'\')) NOT IN '
                f"('TOTAL', 'GRAND TOTAL', 'SUBTOTAL', 'TOTALS'))"
            )

        view_name = f'{table_name}__by_symbol'
        sql = (
            f'CREATE VIEW IF NOT EXISTS "{view_name}" AS\n'
            f'SELECT {", ".join(select_parts)}\n'
            f'FROM "{table_name}"\n'
            f'WHERE {" AND ".join(where_clauses)}\n'
            f'GROUP BY {group_key}'
        )
        with sqlite_connect(self.db_path) as conn:
            conn.execute(f'DROP VIEW IF EXISTS "{view_name}"')
            conn.execute(sql)
            conn.commit()
        logger.info(
            f"Created rollup view {view_name} "
            f"(group_key={group_key!r}, lot_marker={lot_marker_col!r})"
        )

    def delete_document(self, document_id: str) -> int:
        """Drop all structured tables + schema rows belonging to a document."""
        dropped = 0
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                f'SELECT table_name FROM {self.SCHEMA_REGISTRY} WHERE document_id = ?',
                (document_id,),
            )
            for (table_name,) in cursor.fetchall():
                conn.execute(f'DROP VIEW IF EXISTS "{table_name}__by_symbol"')
                conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
                dropped += 1
            conn.execute(
                f'DELETE FROM {self.SCHEMA_REGISTRY} WHERE document_id = ?',
                (document_id,),
            )
            conn.commit()
        if dropped:
            logger.info(f'Dropped {dropped} structured table(s) for document {document_id}')
        return dropped

    def clear_all(self) -> None:
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(f'SELECT table_name FROM {self.SCHEMA_REGISTRY}')
            for (table_name,) in cursor.fetchall():
                conn.execute(f'DROP VIEW IF EXISTS "{table_name}__by_symbol"')
                conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            conn.execute(f'DELETE FROM {self.SCHEMA_REGISTRY}')
            conn.commit()

    def list_tables(self) -> List[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                f'''SELECT document_id, sheet_name, filename, table_name,
                           row_count, column_count, schema_json, financial_roles
                    FROM {self.SCHEMA_REGISTRY}
                    ORDER BY filename, sheet_name'''
            )
            result = []
            for row in cursor.fetchall():
                schema = json.loads(row['schema_json'])
                roles = json.loads(row['financial_roles']) if row['financial_roles'] else {}
                result.append({
                    'document_id': row['document_id'],
                    'sheet_name': row['sheet_name'],
                    'filename': row['filename'],
                    'table_name': row['table_name'],
                    'row_count': row['row_count'],
                    'column_count': row['column_count'],
                    'columns': [
                        {
                            'name': c['name'],
                            'sql_name': c['sql_name'],
                            'type': c['type'],
                            'role': c.get('role'),
                        }
                        for c in schema['columns']
                    ],
                    'financial_roles': roles,
                })
            return result

    def get_schema(
        self,
        identifier: str,
        identifier_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Look up a table schema by table_name, filename, or document_id.

        When identifier_type is provided ("table_name", "filename", or
        "document_id"), the lookup is restricted to that column — useful when
        multiple tables might match the same literal but you need a specific
        interpretation. When None (default), all three fields are searched.
        """
        if identifier_type is not None and identifier_type not in {
            "table_name", "filename", "document_id",
        }:
            raise ValueError(
                f"identifier_type must be one of 'table_name', 'filename', "
                f"'document_id', or None; got {identifier_type!r}"
            )

        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if identifier_type is not None:
                cursor = conn.execute(
                    f'''SELECT schema_json FROM {self.SCHEMA_REGISTRY}
                        WHERE {identifier_type} = ?
                        ORDER BY sheet_name
                        LIMIT 1''',
                    (identifier,),
                )
            else:
                cursor = conn.execute(
                    f'''SELECT schema_json FROM {self.SCHEMA_REGISTRY}
                        WHERE table_name = ? OR filename = ? OR document_id = ?
                        ORDER BY sheet_name
                        LIMIT 1''',
                    (identifier, identifier, identifier),
                )
            row = cursor.fetchone()
            if not row:
                return None
            return json.loads(row['schema_json'])

    def suggest_identifiers(self, identifier: str, limit: int = 3) -> List[Dict[str, str]]:
        """Suggest likely matches for an identifier the model guessed wrong.

        The model sometimes concatenates or lightly mangles a table identifier
        (e.g. "doc_stem - doc_stem.csv" instead of "csv_data_doc_stem"). Rather
        than forcing a `list_tables` round-trip, we surface a did_you_mean hint
        on the lookup-failure error so the model can self-correct on the next
        turn.

        Matching strategy (cheap, local, deterministic):
          - normalize both sides: lowercase, strip quotes, drop extensions,
            collapse [-_\\s]+ to empty
          - any registered identifier (table_name / filename / document_id)
            whose normalized form is contained in — or contains — the
            normalized guess is a candidate
          - rank by longest common substring length, return top `limit`

        Returns a list of ``{"table_name", "filename", "document_id"}`` dicts.
        """
        norm_guess = _normalize_for_match(identifier)
        if not norm_guess:
            return []

        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                f'''SELECT document_id, sheet_name, filename, table_name
                    FROM {self.SCHEMA_REGISTRY}
                    ORDER BY filename, sheet_name'''
            )
            rows = cursor.fetchall()

        scored: List[tuple[int, Dict[str, str]]] = []
        for row in rows:
            candidates = (row['table_name'], row['filename'], row['document_id'])
            best = 0
            for cand in candidates:
                if not cand:
                    continue
                norm_cand = _normalize_for_match(cand)
                if not norm_cand:
                    continue
                if norm_guess == norm_cand:
                    best = max(best, 10_000)
                elif norm_guess in norm_cand or norm_cand in norm_guess:
                    best = max(best, min(len(norm_guess), len(norm_cand)))
            if best > 0:
                scored.append((best, {
                    'table_name': row['table_name'],
                    'filename': row['filename'],
                    'document_id': row['document_id'],
                }))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:limit]]

    def find_rows(
        self,
        terms: List[str],
        limit: int = 10,
        document_ids: Optional[set] = None,
    ) -> List[Dict[str, Any]]:
        """Rows whose cells contain every search term, for keyword search.

        Spreadsheets are never chunked or embedded (questions about them are
        SQL), so without this a row is findable only through a model's table
        tools. A term that names the table — a column header, the filename or
        the sheet — is satisfied by the table itself ("copper kettle stock"
        finds the kettle row of a sheet with a Stock column); every other
        term must appear in some cell of the row, case-insensitively. A table
        whose name alone covers every term returns nothing: that query is
        about the table, not about a row of it.

        Returns up to `limit` ``{document_id, filename, sheet_name,
        row_number, columns, values}`` dicts in file then row order, with
        ``values`` keyed by the original column names.
        """
        terms = [t.lower() for t in terms if t]
        if not terms or limit <= 0:
            return []

        found: List[Dict[str, Any]] = []
        for table in self.list_tables():
            if document_ids is not None and table['document_id'] not in document_ids:
                continue
            schema = self.get_schema(table['table_name'], 'table_name') or {}
            columns = schema.get('columns') or []
            if not columns:
                continue
            names = set(re.findall(r'[^\W_]+', ' '.join(
                [table['filename'] or '', table['sheet_name'] or '']
                + [c['name'] for c in columns]
            ).lower()))
            cell_terms = [
                t for t in terms
                if t not in names and not (t.endswith('s') and t[:-1] in names)
            ]
            if not cell_terms:
                continue

            # The original text when the store kept it beside a typed column
            # (dates, amounts), else the typed value itself.
            cells = [c.get('raw_sql_name') or c['sql_name'] for c in columns]
            clauses, params = [], []
            for term in cell_terms:
                # "kettles" should find "kettle": also try the singular.
                variants = [term] + ([term[:-1]] if len(term) > 3 and term.endswith('s') else [])
                ors = []
                for variant in variants:
                    pattern = '%' + variant.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
                    for cell in cells:
                        ors.append(f'CAST("{cell}" AS TEXT) LIKE ? ESCAPE \'\\\'')
                        params.append(pattern)
                clauses.append('(' + ' OR '.join(ors) + ')')

            select = ', '.join(['__row_number'] + [f'"{cell}"' for cell in cells])
            try:
                with sqlite_connect(self.db_path) as conn:
                    matches = conn.execute(
                        f'SELECT {select} FROM "{table["table_name"]}" '
                        f'WHERE {" AND ".join(clauses)} '
                        f'ORDER BY __row_number LIMIT ?',
                        params + [limit - len(found)],
                    ).fetchall()
            except sqlite3.Error as e:
                # A sheet too wide for one SQLite expression (hundreds of
                # columns) is skipped, not allowed to hide every other table.
                logger.warning(f"Row search skipped {table['filename']}: {e}")
                continue
            for row in matches:
                found.append({
                    'document_id': table['document_id'],
                    'filename': table['filename'],
                    'sheet_name': table['sheet_name'],
                    'row_number': row[0],
                    'columns': [c['name'] for c in columns],
                    'values': {
                        c['name']: ('' if v is None else v)
                        for c, v in zip(columns, row[1:])
                    },
                })
            if len(found) >= limit:
                break
        return found

    def get_schemas_for_document(self, document_id: str) -> List[Dict[str, Any]]:
        with sqlite_connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                f'''SELECT schema_json FROM {self.SCHEMA_REGISTRY}
                    WHERE document_id = ? ORDER BY sheet_name''',
                (document_id,),
            )
            return [json.loads(r['schema_json']) for r in cursor.fetchall()]

    def execute_query(
        self,
        sql: str,
        max_rows: int = _DEFAULT_MAX_QUERY_ROWS,
    ) -> Dict[str, Any]:
        """Run a validated SELECT and return {columns, rows, row_count, truncated}."""
        cleaned = validate_select(sql)

        uri = f'file:{self.db_path}?mode=ro'
        with sqlite_connect(uri, uri=True) as conn:
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.execute(cleaned)
            except sqlite3.Error as e:
                raise SQLValidationError(f'SQL error: {e}')

            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows: List[List[Any]] = []
            truncated = False
            for i, row in enumerate(cursor):
                if i >= max_rows:
                    truncated = True
                    break
                rows.append([row[col] for col in columns])

        return {
            'columns': columns,
            'rows': rows,
            'row_count': len(rows),
            'truncated': truncated,
        }

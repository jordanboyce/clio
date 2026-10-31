"""Both concrete database backends must cover the full abstract surface.

A parity break shipped once already: the SQLite backend gained
invited_email and two share-contact lookups while the Postgres backend
didn't, so every share creation under DB_BACKEND=postgresql would have
thrown. This test makes that class of drift a test failure instead of a
production incident: every abstract method must exist on both backends
with the same positional signature.

AST-based on purpose — importing PostgresBackend requires psycopg2, which
isn't installed in every dev environment.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _is_abstract(func: ast.FunctionDef) -> bool:
    return any(
        (isinstance(d, ast.Name) and d.id == "abstractmethod")
        or (isinstance(d, ast.Attribute) and d.attr == "abstractmethod")
        for d in func.decorator_list
    )


def _methods(path: Path, class_name: str, abstract_only: bool = False) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                f.name: [a.arg for a in f.args.args]
                for f in node.body
                if isinstance(f, ast.FunctionDef) and not f.name.startswith("__")
                and (not abstract_only or _is_abstract(f))
            }
    raise AssertionError(f"class {class_name} not found in {path}")


def test_backends_implement_full_abstract_surface():
    # Concrete methods on the base (shared by both backends) are inherited,
    # so only the abstract ones must be re-implemented.
    base = _methods(ROOT / "services" / "db_backend.py", "DatabaseBackend", abstract_only=True)
    sqlite = _methods(ROOT / "services" / "app_database.py", "SQLiteBackend")
    postgres = _methods(ROOT / "services" / "db_postgres.py", "PostgresBackend")

    missing_sqlite = sorted(m for m in base if m not in sqlite)
    missing_postgres = sorted(m for m in base if m not in postgres)
    assert not missing_sqlite, f"SQLiteBackend missing: {missing_sqlite}"
    assert not missing_postgres, f"PostgresBackend missing: {missing_postgres}"

    # Positional signatures must agree, or a keyword call that works on one
    # backend silently breaks on the other.
    mismatched = [
        (m, base[m], sqlite.get(m), postgres.get(m))
        for m in base
        if sqlite.get(m) != base[m] or postgres.get(m) != base[m]
    ]
    assert not mismatched, f"signature drift: {mismatched}"

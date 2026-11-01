"""The lineage markers: the same id in the header, the About endpoint, the
MCP health check, the database and the frontend sources. They exist so that
a product built from Clio can be recognised later; a change to the id would
quietly defeat that, so the value is pinned here."""

import pathlib
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from services import provenance
from services.app_database import SQLiteBackend

ROOT = pathlib.Path(__file__).resolve().parent.parent
LINEAGE_ID = "clio.helicon.58a48ab52f51"


def test_lineage_id_is_pinned():
    assert provenance.LINEAGE_ID == LINEAGE_ID
    assert provenance.SOURCE_URL == "https://github.com/jordanboyce/clio"


def test_frontend_and_image_carry_the_same_id():
    # The id is defined separately for each artifact; they must not drift.
    for rel in ("frontend/vite.config.js", "frontend/index.html",
                "frontend/src/style.css", "frontend/vitest.config.js", "Dockerfile",
                "docs/PROVENANCE.md"):
        assert LINEAGE_ID in (ROOT / rel).read_text(encoding="utf-8"), rel


def test_build_info_shape():
    info = provenance.build_info()
    assert info["version"]
    assert info["commit"]
    assert info["origin"] == {
        "lineage_id": LINEAGE_ID,
        "project": "Clio",
        "source": "https://github.com/jordanboyce/clio",
        "license": "MIT",
        "copyright": "Copyright (c) 2026 Jordan Boyce",
    }


def test_version_prefers_image_env(monkeypatch):
    monkeypatch.setenv("CLIO_VERSION", "9.9.9")
    monkeypatch.setenv("CLIO_VCS_REF", "abc123")
    provenance.version.cache_clear()
    provenance.commit.cache_clear()
    try:
        assert provenance.version() == "9.9.9"
        assert provenance.commit() == "abc123"
    finally:
        provenance.version.cache_clear()
        provenance.commit.cache_clear()


@pytest.fixture(scope="module")
def client():
    from main import app

    return TestClient(app)


def test_every_response_carries_the_lineage_header(client):
    for path in ("/health", "/api/about", "/does-not-exist"):
        r = client.get(path)
        assert r.headers.get("X-Clio-Lineage") == LINEAGE_ID, path


def test_about_reports_origin(client):
    data = client.get("/api/about").json()
    assert data["origin"]["lineage_id"] == LINEAGE_ID
    assert data["origin"]["source"] == "https://github.com/jordanboyce/clio"
    assert data["version"] and data["commit"]


def test_openapi_carries_version_and_license():
    from main import app

    info = app.openapi()["info"]
    assert info["version"] == provenance.version()
    assert info["license"]["name"] == "MIT"


def test_database_records_lineage_once(tmp_path):
    db_path = tmp_path / "app.db"
    SQLiteBackend(db_path)
    SQLiteBackend(db_path)  # a second start must not add a second row
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT lineage_id, source, created_by_version FROM clio_lineage"
        ).fetchall()
    assert rows == [(LINEAGE_ID, "https://github.com/jordanboyce/clio", provenance.version())]


def test_mcp_health_check_reports_origin():
    from services import mcp_server

    assert re.search(r'"origin": _provenance\.origin\(\)', pathlib.Path(mcp_server.__file__).read_text())
    assert mcp_server._provenance.origin()["lineage_id"] == LINEAGE_ID

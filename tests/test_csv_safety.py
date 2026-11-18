"""CSV exports cannot carry spreadsheet formulas (I9).

The document list export already quoted formula-shaped cells inline; the
audit CSV an admin downloads did not, although its actor, target and detail
columns carry text a caller chose. Both now go through services/csv_safety.
"""

import csv
import io

import pytest

from services import audit
from services.csv_safety import safe_cell, safe_dict_row, safe_row


@pytest.mark.parametrize("text", [
    "=HYPERLINK(\"http://evil.example\",\"x\")", "+1+1", "-2+3", "@SUM(A1)", "\t=1", "\r=1",
])
def test_formula_shaped_text_is_quoted(text):
    assert safe_cell(text) == "'" + text


@pytest.mark.parametrize("value", ["report.pdf", "", " =not first", "a=b", "'already"])
def test_other_text_is_unchanged(value):
    assert safe_cell(value) == value


@pytest.mark.parametrize("value", [-5, -0.5, 0, 12, None, True])
def test_numbers_and_non_text_stay_as_they_are(value):
    assert safe_cell(value) is value


def test_row_helpers():
    assert safe_row([1, "=x", "ok", -3]) == [1, "'=x", "ok", -3]
    assert safe_dict_row({"a": "@x", "b": -1}) == {"a": "'@x", "b": -1}


def test_audit_csv_neutralises_caller_chosen_text():
    rows = [{
        "id": 7, "timestamp": "2026-11-18T00:00:00", "actor": "=cmd|' /C calc'!A0",
        "action": "document.upload", "collection_id": "c1", "document_id": "d1",
        "target": "+evil.pdf", "detail": "@SUM(1)",
    }, {
        "id": 8, "timestamp": "2026-11-18T00:00:01", "actor": "ada@example.com",
        "action": "document.delete", "collection_id": "c1", "document_id": "d2",
        "target": "notes.md", "detail": {"bytes": -1},
    }]
    parsed = list(csv.DictReader(io.StringIO(audit.to_csv(rows))))
    first, second = parsed
    assert first["actor"] == "'=cmd|' /C calc'!A0"
    assert first["target"] == "'+evil.pdf"
    assert first["detail"] == "'@SUM(1)"
    assert first["id"] == "7"
    assert second["actor"] == "ada@example.com" and second["target"] == "notes.md"
    assert second["detail"] == '{"bytes": -1}'
    for row in parsed:
        assert not any(v.startswith(("=", "+", "-", "@", "\t", "\r")) for v in row.values())

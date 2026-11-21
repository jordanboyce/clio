"""D1: spreadsheets in the export, and <clio-table>."""

import csv
import io
import json

import pytest

from services import static_export as se
from services.static_export import tables
from tests.browser_harness import static_server
from tests.static_real import SAMPLES
from tests.test_collection_bundle import env  # noqa: F401
from tests.test_static_export import _public, kit, site  # noqa: F401
from tests.test_static_reader import _add

REGISTER = (SAMPLES / "equipment-register.csv").read_text(encoding="utf-8").rstrip("\n") + \
    '\nC-1999,"=HYPERLINK(""http://x.example"",""click"")",Records,standard,,2026-03-30,5,2026-04-06,,no,no\n'


@pytest.fixture()
def register(site):
    cid, ids = _public(site)
    ids.append(_add(cid, "equipment-register.csv", REGISTER))
    return cid, ids


def test_each_table_goes_out_as_data_and_as_a_safe_csv(register, tmp_path):
    cid, ids = register
    offer = next(a for a in se.preview(cid)["additions"] if a["id"] == "tables")
    assert offer["available"] and offer["note"] == "1 table."
    out = tmp_path / "out"
    manifest, _ = se.write_export(cid, out, confirm_public=True, include="tables")
    [entry] = manifest["additions"]["tables"]["tables"]
    assert entry["source"] == ids[2] and entry["file"] == "tables/equipment-register.json"
    data = json.loads((out / entry["file"]).read_text(encoding="utf-8"))
    names = [c["name"] for c in data["columns"]]
    assert names[:3] == ["checkout_id", "item", "team"] and entry["rows"] == len(data["rows"]) == REGISTER.count("\n") - 1
    assert (out / entry["file"]).read_text(encoding="utf-8").count("\n") == entry["rows"] + 2  # one row a line
    first = data["rows"][0]
    assert first[0] == "C-1001" and first[5] == "2026-01-12" and first[-1] == "no"
    rows = list(csv.reader(io.StringIO((out / entry["csv"]).read_text(encoding="utf-8-sig"))))
    assert rows[0] == names and len(rows) == entry["rows"] + 1
    hostile = next(r for r in rows if r[0] == "C-1999")
    assert hostile[1].startswith("'=HYPERLINK")
    # The JSON keeps the value as it is: the component shows it as text.
    assert any(str(r[1]).startswith("=HYPERLINK") for r in data["rows"])


def test_a_table_over_the_limit_is_left_out_and_counted(register, monkeypatch, tmp_path):
    cid, _ = register
    monkeypatch.setattr(tables, "MAX_ROWS", 3)
    offer = next(a for a in se.preview(cid)["additions"] if a["id"] == "tables")
    assert offer["available"] is False and "more than 3 rows" in offer["reason"]
    _add(cid, "small.csv", "a,b\n1,2\n3,4\n")
    offer = next(a for a in se.preview(cid)["additions"] if a["id"] == "tables")
    assert offer["note"] == "1 table; 1 over 3 rows left out." and offer["left_out"] == 1
    manifest, _ = se.write_export(cid, tmp_path / "out", confirm_public=True, include="tables")
    assert [t["file"] for t in manifest["additions"]["tables"]["tables"]] == ["tables/small.json"]
    assert "left_out" not in json.dumps(manifest)


def test_no_spreadsheet_no_tables(site):
    cid, _ = _public(site)
    offer = next(a for a in se.preview(cid)["additions"] if a["id"] == "tables")
    assert offer["available"] is False and "spreadsheet" in offer["reason"]


def test_the_register_sorts_filters_and_downloads_on_its_reader_page(kit, register, browser, tmp_path):
    cid, ids = register
    out = tmp_path / "site"
    se.write_export(cid, out, confirm_public=True, site=True, include="reader,tables")
    page_html = (out / "search/read/equipment-register.html").read_text(encoding="utf-8")
    assert '<clio-table src="../" table="tables/equipment-register.json">' in page_html
    with static_server(out) as server:
        page = browser.page()
        page.goto(server.url("search/read/equipment-register.html"))
        t = "document.querySelector('clio-table').shadowRoot"
        page.wait_for(f"{t}.querySelectorAll('tbody tr').length > 0")
        col = page.eval(f"[...{t}.querySelectorAll('th button')].findIndex(b => b.textContent.startsWith('checkout_date'))")
        cells = lambda: page.eval(  # noqa: E731
            f"[...{t}.querySelectorAll('tbody tr')].map(r => r.children[{col}].textContent)")
        page.eval(f"{t}.querySelectorAll('th button')[{col}].click()")
        dates = cells()
        assert dates == sorted(dates) and len(dates) > 3
        page.eval(f"{t}.querySelectorAll('th button')[{col}].click()")
        assert cells() == sorted(dates, reverse=True)
        assert page.eval(f"{t}.querySelectorAll('th')[{col}].getAttribute('aria-sort')") == "descending"
        page.type_into(f"{t}.querySelector('input')", "Inspections")
        teams = page.eval(f"[...{t}.querySelectorAll('tbody tr')].map(r => r.children[2].textContent)")
        assert teams and set(teams) == {"Inspections"}
        assert "of" in page.eval(f"{t}.querySelector('.status').textContent")
        # The formula is shown as text, never run, and the CSV link is the safe file.
        page.type_into(f"{t}.querySelector('input')", "HYPERLINK")
        assert page.eval(f"{t}.querySelector('tbody td:nth-child(2)').textContent").startswith("=HYPERLINK")
        assert page.eval(f"{t}.querySelector('tbody a')") is None
        href = page.eval(f"{t}.querySelector('a[download]').href")
        assert "/search/tables/equipment-register.csv?v=" in href
        page.close()

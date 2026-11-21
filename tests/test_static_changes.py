"""G1: what changed between exports, as a page and an Atom feed."""

import xml.etree.ElementTree as ET

from services import static_export as se
from services.indexer_manager import indexer_manager
from tests.test_collection_bundle import env  # noqa: F401
from tests.test_static_export import _public, site  # noqa: F401
from tests.test_static_reader import _add, check_page

ATOM = "{http://www.w3.org/2005/Atom}"


def _reword(cid, doc_id, name, text):
    """Change a source's text in place: same file name, new content, new id."""
    indexer = indexer_manager.get_indexer(cid)
    indexer.delete_document(doc_id)
    indexer.save_index()
    return _add(cid, name, text)


def _entries(path):
    root = ET.parse(path).getroot()
    return [(e.find(f"{ATOM}title").text, e.find(f"{ATOM}link").get("href")) for e in root.findall(f"{ATOM}entry")]


def test_changing_one_source_and_exporting_again_gives_one_feed_entry(site, tmp_path):
    from scripts import export_static

    cid, ids = _public(site)
    se.save_addresses(cid, site_url="https://example.org/handbook/")
    out = tmp_path / "site"
    assert export_static.main([cid, "--out", str(out), "--confirm-public", "--include", "changes"]) == 0
    first = (out / "read/changes.html").read_text(encoding="utf-8")
    assert "Nothing yet" in first and _entries(out / "changes.xml") == []

    new_id = _reword(cid, ids[0], "holidays.txt", "Staff accrue 27 days of holiday a year from 2027. " * 20)
    assert new_id != ids[0]
    assert export_static.main([cid, "--out", str(out), "--confirm-public", "--include", "changes"]) == 0
    entries = _entries(out / "changes.xml")
    assert entries == [("Changed: holidays", "https://example.org/handbook/read/holidays.html")]
    page = (out / "read/changes.html").read_text(encoding="utf-8")
    check_page(page)
    assert '<a href="holidays.html">holidays</a> was changed.' in page
    assert 'rel="alternate" type="application/atom+xml"' in page

    # Exporting again with nothing changed adds nothing, and the files stay the same.
    before = (out / "changes.xml").read_bytes()
    assert export_static.main([cid, "--out", str(out), "--confirm-public", "--include", "changes"]) == 0
    assert (out / "changes.xml").read_bytes() == before


def test_the_app_download_compares_with_what_the_server_remembers(site, tmp_path):
    cid, ids = _public(site)
    se.write_export(cid, tmp_path / "a", confirm_public=True)
    added = _add(cid, "travel.txt", "Travel is booked through the desk. " * 20)
    manifest, _ = se.write_export(cid, tmp_path / "b", confirm_public=True, include="changes")
    history = manifest["additions"]["changes"]["history"]
    assert [(e["kind"], e["id"]) for e in history] == [("added", added)]
    # The memory holds ids, names and titles, never text.
    from services.app_database import app_db

    memory = app_db.get_config(f"static_export_last:{cid}")
    assert {d["name"] for d in memory["documents"]} == {"holidays.txt", "expenses.txt", "travel.txt"}
    assert "Travel is booked" not in str(memory)


def test_a_source_now_held_is_not_named_even_in_old_history(site, tmp_path):
    cid, ids = _public(site)
    se.write_export(cid, tmp_path / "a", confirm_public=True)
    secret = _add(cid, "salaries.txt", "Salary bands. " * 30)
    se.write_export(cid, tmp_path / "b", confirm_public=True, include="changes")  # salaries added
    indexer_manager.get_indexer(cid).vector_store.metadata_store.set_document_governance(secret, sensitivity="internal")
    manifest, _ = se.write_export(cid, tmp_path / "c", confirm_public=True, include="changes")
    raw = (tmp_path / "c" / "read/changes.html").read_text(encoding="utf-8") + str(manifest)
    assert "salaries" not in raw and secret not in raw


def test_a_source_marked_as_replaced_is_said_to_be(site, tmp_path):
    cid, ids = _public(site)
    se.write_export(cid, tmp_path / "a", confirm_public=True)
    store = indexer_manager.get_indexer(cid).vector_store.metadata_store
    store.set_document_superseded(ids[1], ids[0])
    manifest, _ = se.write_export(cid, tmp_path / "b", confirm_public=True, include="changes")
    [entry] = manifest["additions"]["changes"]["history"]
    assert entry["kind"] == "superseded" and entry["id"] == ids[1] and entry["by"] == ids[0]
    assert "expenses</a> is replaced by <a" in (tmp_path / "b" / "read/changes.html").read_text(encoding="utf-8")

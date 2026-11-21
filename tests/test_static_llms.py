"""F1: llms.txt and llms-full.txt."""

import re

from services import static_export as se
from tests.test_collection_bundle import env  # noqa: F401
from tests.test_static_export import _public, kit, site  # noqa: F401
from tests.test_static_reader import LONG, _flat, handbook  # noqa: F401


def test_a_whole_site_has_llms_txt_at_its_root_listing_only_exported_sources(kit, handbook, tmp_path):
    from services.indexer_manager import indexer_manager

    cid, ids = handbook
    indexer_manager.get_indexer(cid).vector_store.metadata_store.set_document_governance(ids[1], sensitivity="internal")
    out = tmp_path / "site"
    manifest, detail = se.write_export(cid, out, confirm_public=True, site=True, include="llms")
    assert detail["include"] == ["markdown", "llms"]
    index = (out / "llms.txt").read_text(encoding="utf-8")
    assert index.startswith("# Handbooks\n")
    links = re.findall(r"^- \[(.+?)\]\((.+?)\)", index, re.M)
    assert [t for t, _ in links] == ["holidays", "travel policy", "visitor notes"]
    assert all(u.startswith("search/markdown/") and (out / u).is_file() for _, u in links)
    assert "expenses" not in index.lower() and "reimbursed" not in index
    full = (out / "llms-full.txt").read_text(encoding="utf-8")
    assert "## travel policy" in full and _flat(LONG) in _flat(full.replace("\\", ""))
    assert "reimbursed" not in full
    listed = {f["file"] for f in manifest["additions"]["llms"]["files"]}
    assert listed == {"../llms.txt", "../llms-full.txt"}


def test_with_the_sites_address_the_links_are_absolute(handbook, tmp_path):
    cid, _ = handbook
    se.save_addresses(cid, site_url="https://docs.example.org/handbook/")
    se.write_export(cid, tmp_path / "out", confirm_public=True, include="llms")
    index = (tmp_path / "out" / "llms.txt").read_text(encoding="utf-8")
    assert "(https://docs.example.org/handbook/markdown/travel-policy.md)" in index
    path, _, _ = se.export_zip(cid, confirm_public=True, include="llms")
    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            assert "llms.txt" in zf.namelist() and "llms-full.txt" in zf.namelist()
    finally:
        path.unlink()

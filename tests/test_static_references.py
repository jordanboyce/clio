"""G3: a reference list for the exported papers, with citation_* tags and BibTeX."""

import re

import pytest

from services import static_export as se
from services.collection_service import collection_service
from tests.test_collection_bundle import env  # noqa: F401
from tests.test_static_export import _public, site  # noqa: F401
from tests.test_static_reader import _add, check_page


@pytest.fixture()
def papers(site):
    from services.research.store import ResearchStore

    cid, ids = _public(site)
    ids.append(_add(cid, "okafor.txt", "Root growth in dry soils was measured over three seasons. " * 10))
    store = ResearchStore(collection_service.get_indexes_path(cid) / "metadata.db")
    store.upsert_paper(ids[2], title="Root growth under drought", authors=["Ada Okafor", "Lin Wei"], year=2021,
                       venue="Journal of Soil Science", doi="10.1234/jss.2021.7", kind="article",
                       analyzed_at="2026-11-18T00:00:00")
    store.upsert_paper(ids[0], title="Holiday Entitlement <i>for</i> Staff", authors=["Bea Adams"], year=2019,
                       kind="report", analyzed_at="2026-11-18T00:00:00")
    return cid, ids


def test_the_references_page_lists_each_paper_once_in_the_chosen_style(papers, tmp_path):
    cid, ids = papers
    offer = next(a for a in se.preview(cid)["additions"] if a["id"] == "references")
    assert offer["available"] and offer["note"].startswith("2 papers")
    out = tmp_path / "out"
    manifest, detail = se.write_export(cid, out, confirm_public=True, include="references", citation_style="apa")
    assert detail["include"] == ["reader", "references"]
    assert manifest["additions"]["references"]["style"] == "apa"
    page = (out / "read/references.html").read_text(encoding="utf-8")
    check_page(page)
    items = re.findall(r'<li id="ref-([^"]+)">(.*?)</li>', page)
    assert [i for i, _ in items] == [ids[0], ids[2]]  # alphabetical by author in APA: Adams, then Okafor
    assert "Okafor, A., &amp; Wei, L. (2021)" in items[1][1] and "<i>Journal of Soil Science</i>" in items[1][1]
    # A title is text: the <i> someone typed into it is shown, not obeyed.
    assert "&lt;i&gt;for&lt;/i&gt;" in items[0][1]
    assert 'href="okafor.html">Read it here' in items[1][1]
    assert 'href="references.bib" download' in page
    bib = (out / "read/references.bib").read_text(encoding="utf-8")
    assert bib.count("@") == 2 and "10.1234/jss.2021.7" in bib
    assert 'href="references.html">References' in (out / "read/index.html").read_text(encoding="utf-8")

    ieee, _ = se.write_export(cid, tmp_path / "ieee", confirm_public=True, include="references",
                              citation_style="ieee")
    assert "[1]" in (tmp_path / "ieee" / "read/references.html").read_text(encoding="utf-8")
    with pytest.raises(se.StaticExportError, match="citation_style"):
        se.write_export(cid, tmp_path / "bad", confirm_public=True, include="references", citation_style="vancouver")


def test_each_papers_page_carries_scholarly_meta_tags(papers, tmp_path):
    cid, ids = papers
    se.save_addresses(cid, site_url="https://example.org/papers/")
    out = tmp_path / "out"
    se.write_export(cid, out, confirm_public=True, include="references")
    page = (out / "read/okafor.html").read_text(encoding="utf-8")
    check_page(page)
    assert '<meta name="citation_title" content="Root growth under drought">' in page
    assert page.count('name="citation_author"') == 2 and 'content="Okafor, Ada"' in page
    assert '<meta name="citation_publication_date" content="2021">' in page
    assert '<meta name="citation_journal_title" content="Journal of Soil Science">' in page
    assert '<meta name="citation_doi" content="10.1234/jss.2021.7">' in page
    assert 'content="https://example.org/papers/read/okafor.html"' in page
    # A source that is not a paper gets none.
    assert "citation_title" not in (out / "read/expenses.html").read_text(encoding="utf-8")


def test_no_papers_no_references(site):
    cid, _ = _public(site)
    offer = next(a for a in se.preview(cid)["additions"] if a["id"] == "references")
    assert offer["available"] is False and "Library" in offer["reason"]

"""H2: the collection on one page to print, and as an EPUB."""

import io
import xml.etree.ElementTree as ET
import zipfile

from services import static_export as se
from tests.test_collection_bundle import env  # noqa: F401
from tests.test_static_export import _public, site  # noqa: F401
from tests.test_static_reader import HOSTILE, LONG, _flat, check_page, handbook  # noqa: F401

XHTML = "{http://www.w3.org/1999/xhtml}"


def test_one_page_holds_every_source_and_the_epub_is_well_formed(handbook, tmp_path):
    cid, ids = handbook
    out = tmp_path / "out"
    manifest, detail = se.write_export(cid, out, confirm_public=True, include="book")
    assert detail["include"] == ["reader", "book"]
    entry = manifest["additions"]["book"]
    page = (out / entry["page"]).read_text(encoding="utf-8")
    c = check_page(page)
    assert page.count('<article class="source"') == len(ids)
    assert _flat(LONG) in _flat("".join(c.text))
    assert "&lt;script&gt;" in page and "<script>alert" not in page
    assert "break-before: page" in (out / "read/reader.css").read_text(encoding="utf-8")
    assert 'href="all.html">Everything on one page' in (out / "read/index.html").read_text(encoding="utf-8")

    raw = (out / entry["epub"]).read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        infos = zf.infolist()
        assert infos[0].filename == "mimetype" and infos[0].compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype") == b"application/epub+zip"
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        assert container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile").get(
            "full-path") == "OEBPS/content.opf"
        opf = ET.fromstring(zf.read("OEBPS/content.opf"))
        ns = {"o": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
        assert opf.find(".//dc:title", ns).text == "Handbooks"
        spine = [i.get("idref") for i in opf.findall(".//o:itemref", ns)]
        hrefs = {i.get("id"): i.get("href") for i in opf.findall(".//o:item", ns)}
        assert len(spine) == len(ids)
        bodies = []
        for ref in spine:
            doc = ET.fromstring(zf.read("OEBPS/" + hrefs[ref]))  # each chapter parses as XML
            bodies.append("".join(doc.find(f"{XHTML}body").itertext()))
        ET.fromstring(zf.read("OEBPS/nav.xhtml"))
        assert any("<script>alert('x')</script>" in b for b in bodies)  # as text
        assert any(_flat(LONG) in _flat(b) for b in bodies)
    again, _ = se.write_export(cid, tmp_path / "again", confirm_public=True, include="book")
    assert (tmp_path / "again" / entry["epub"]).read_bytes()[:200] == raw[:200]

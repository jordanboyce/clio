"""C6: an A to Z index of terms on the reader's pages."""

import re

from services import static_export as se
from services.static_export import terms
from tests.test_collection_bundle import env  # noqa: F401
from tests.test_static_export import _public, site  # noqa: F401
from tests.test_static_reader import _add, check_page

TEXTS = {
    "alpha.txt": "The Regional Analysis Unit reviewed the Budget Analysis. Analyses by the Regional Analysis Unit "
                 "were sent to NASA and to the Finance Office.",
    "beta.txt": "Finance Office staff met the Regional Analysis Unit. NASA asked about Budget Analysis twice.",
    "gamma.txt": "Nothing much here about the Finance Office, except a note from NASA.",
}


def _collection(site_env):
    cid, ids = _public(site_env)
    for name, text in TEXTS.items():
        ids.append(_add(cid, name, text))
    # Enough other passages that a term in three of them is not too common to index (at most 30%).
    for i in range(6):
        ids.append(_add(cid, f"filler-{i}.txt", f"plain words about item {i} with nothing named in them."))
    return cid, ids


def test_terms_are_shown_as_written_and_link_to_their_passages(site, tmp_path):
    cid, ids = _collection(site)
    out = tmp_path / "out"
    manifest, detail = se.write_export(cid, out, confirm_public=True, include="terms")
    # The index needs the reader, which comes with it.
    assert detail["include"] == ["reader", "terms"] and manifest["additions"]["terms"]["terms"] > 0
    raw = (out / "read/terms.html").read_text(encoding="utf-8")
    c = check_page(raw)
    shown = re.findall(r"<dt>(.*?)</dt>", raw)
    assert "NASA" in shown and "Finance Office" in shown and "Regional Analysis Unit" in shown
    assert not any(t == t.lower() for t in shown) and "analysi" not in raw.lower().replace("analysis", "")
    assert shown == sorted(shown, key=lambda s: (s[0].upper(), s.lower()))
    for href in re.findall(r'<dd>.*?</dd>', raw):
        for page, anchor in re.findall(r'href="([^"#]+)#(p-[^"]+)"', href):
            assert f'id="{anchor}"' in (out / "read" / page).read_text(encoding="utf-8")
    assert 'href="terms.html">Index of terms' in (out / "read/index.html").read_text(encoding="utf-8")
    assert "letter-N" in c.ids


def test_a_term_only_a_left_out_source_uses_is_not_in_the_index(site, tmp_path):
    from services.indexer_manager import indexer_manager

    cid, ids = _collection(site)
    secret = _add(cid, "zeta.txt", "Project Kestrel Overrun. The Project Kestrel Overrun was hidden. "
                                   "Project Kestrel Overrun again.")
    _add(cid, "eta.txt", "Nothing to see. Project Kestrel Overrun is mentioned once here too.")
    se.write_export(cid, tmp_path / "a", confirm_public=True, include="terms")
    assert "Project Kestrel Overrun" in (tmp_path / "a/read/terms.html").read_text(encoding="utf-8")
    indexer_manager.get_indexer(cid).vector_store.metadata_store.set_document_governance(secret, sensitivity="internal")
    se.write_export(cid, tmp_path / "b", confirm_public=True, include="terms")
    assert "Kestrel" not in (tmp_path / "b/read/terms.html").read_text(encoding="utf-8")


def test_the_most_often_written_form_wins():
    from collections import Counter

    assert terms.display(Counter({"Analysis": 3, "analysis": 1})) == "Analysis"
    assert terms.display(Counter({"budget": 1, "Budget": 1})) == "Budget"
    assert terms.surface_terms("Talks with the Regional Analysis Unit about it.") == [
        ("regional analysis unit", "Regional Analysis Unit")]

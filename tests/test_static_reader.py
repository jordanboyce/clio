"""B1/B2: a page to read for each exported source, and results that land on it."""

import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from services import static_export as se
from services.collection_service import collection_service
from services.indexer_manager import indexer_manager
from services.static_export import reader
from tests.browser_harness import static_server
from tests.test_collection_bundle import env  # noqa: F401
from tests.test_static_export import _public, kit, site  # noqa: F401

LONG = " ".join(f"Clause {i} of the travel policy covers journeys of {i * 7} miles or more." for i in range(1, 120))
HOSTILE = ("The visitor's notes said <script>alert('x')</script> and <img src=x onerror=alert(1)> "
           "and &amp; and \"quotes\". ") * 3

VOID = {"meta", "link", "br", "img", "input", "hr", "source", "track", "wbr", "area", "base", "col", "embed"}


class _Checker(HTMLParser):
    """Enough of a validator for generated pages: every element closed in
    order, ids unique, the head carrying what every page must carry."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.ids, self.problems, self.tags, self.text = [], set(), [], [], []
        self.csp = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a))
        if "id" in a:
            if a["id"] in self.ids:
                self.problems.append(f"duplicate id {a['id']}")
            self.ids.add(a["id"])
        if tag == "meta" and a.get("http-equiv") == "Content-Security-Policy":
            self.csp = a.get("content")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.problems.append(f"</{tag}> closes {self.stack[-1:] or 'nothing'}")
        else:
            self.stack.pop()

    def handle_data(self, data):
        if self.stack and self.stack[-1] not in ("style", "script", "title"):
            self.text.append(data)


def check_page(raw: str) -> _Checker:
    assert raw.startswith("<!doctype html>\n")
    c = _Checker()
    c.feed(raw)
    c.close()
    assert not c.problems and not c.stack, (c.problems, c.stack)
    names = [t for t, _ in c.tags]
    assert names[:3] == ["html", "head", "meta"] and dict(c.tags[0][1]).get("lang")
    assert "title" in names and c.csp and "script-src 'self'" in c.csp and "unsafe-inline" not in c.csp
    # No script of its own, inline or not, other than the site's components.
    for tag, a in c.tags:
        if tag == "script":
            assert a.get("src", "").endswith(".js") and a.get("type") == "module"
        assert not any(k.startswith("on") for k in a), (tag, a)
    return c


def _add(cid, name, text):
    docs_dir = collection_service.get_documents_path(cid)
    (docs_dir / name).write_text(text, encoding="utf-8")
    indexer = indexer_manager.get_indexer(cid)
    doc_id = indexer.index_document(docs_dir / name, name, collection_id=cid).document_id
    indexer.save_index()
    return doc_id


@pytest.fixture()
def handbook(site):
    cid, ids = _public(site)
    ids.append(_add(cid, "travel-policy.txt", LONG))
    ids.append(_add(cid, "visitor notes.txt", HOSTILE))
    return cid, ids


def _flat(text):
    return re.sub(r"\s+", " ", text).strip()


def test_every_source_has_a_page_that_reads_like_the_source(handbook, tmp_path):
    cid, ids = handbook
    out = tmp_path / "out"
    manifest, _ = se.write_export(cid, out, confirm_public=True, include="reader")
    entry = manifest["additions"]["reader"]
    assert entry["contents"] == "read/index.html" and entry["anchor"] == "#p-{id}"
    pages = {d["id"]: d["reader"] for d in manifest["documents"]}
    assert set(pages) == set(ids) and len(set(pages.values())) == len(ids)
    assert pages[ids[2]] == "read/travel-policy.html" and pages[ids[3]] == "read/visitor-notes.html"
    listed = {f["file"] for f in entry["files"]}
    assert listed == {"read/index.html", "read/reader.css", *pages.values()}

    passages = json.loads((out / "passages.json").read_text(encoding="utf-8"))
    for doc in manifest["documents"]:
        raw = (out / doc["reader"]).read_text(encoding="utf-8")
        c = check_page(raw)
        # An anchor on every passage of the source.
        mine = [p for p in passages if p["document_id"] == doc["id"]]
        assert {reader.anchor(p["id"]) for p in mine} <= c.ids
    # Overlapping passages are put back without repeating the overlap.
    travel = [p for p in passages if p["document_id"] == ids[2]]
    assert len(travel) > 3, "the fixture should need several passages"
    joined = " ".join(p["text"] for p in travel)
    assert len(_flat(joined)) > len(_flat(LONG)) + 50, "the fixture's passages should overlap"
    c = check_page((out / "read/travel-policy.html").read_text(encoding="utf-8"))
    main = _flat("".join(c.text).split("Exported from Clio")[0])
    assert main.endswith(_flat(LONG)) and main.count("Clause 60 of the travel policy") == 1


def test_source_text_is_shown_as_text_never_run(handbook, tmp_path):
    cid, ids = handbook
    out = tmp_path / "out"
    se.write_export(cid, out, confirm_public=True, include="reader")
    raw = (out / "read/visitor-notes.html").read_text(encoding="utf-8")
    assert "<script>alert" not in raw and "<img src=x" not in raw
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in raw and "&amp;amp;" in raw
    c = check_page(raw)
    assert "<img src=x onerror=alert(1)>" in "".join(c.text)
    assert not any(t == "img" for t, _ in c.tags)


def test_two_exports_of_an_unchanged_collection_are_identical(handbook, tmp_path):
    cid, _ = handbook
    a, _ = se.write_export(cid, tmp_path / "a", confirm_public=True, include="reader")
    b, _ = se.write_export(cid, tmp_path / "b", confirm_public=True, include="reader")
    assert a["additions"] == b["additions"]
    for f in a["additions"]["reader"]["files"]:
        assert (tmp_path / "a" / f["file"]).read_bytes() == (tmp_path / "b" / f["file"]).read_bytes()


def test_page_names_are_stable_safe_and_distinct():
    docs = [{"id": "aaaa1111", "name": "Leave Policy (2024).pdf"}, {"id": "bbbb2222", "name": "CON.txt"},
            {"id": "cccc3333", "name": "Résumé guide.docx"}, {"id": "dddd4444", "name": "notes.md"},
            {"id": "eeee5555", "name": "notes.txt"}, {"id": "ffff6666", "name": "???.txt"},
            {"id": "abcd7777", "name": "index.html"}]
    names = reader.page_names(docs)
    assert names["aaaa1111"] == "read/leave-policy-2024.html"
    assert names["bbbb2222"] == "read/con-source.html"
    assert names["cccc3333"] == "read/resume-guide.html"
    assert names["dddd4444"] == "read/notes-dddd4444.html" and names["eeee5555"] == "read/notes-eeee5555.html"
    assert names["ffff6666"] == "read/source-ffff6666.html" and names["abcd7777"] == "read/index-abcd7777.html"
    assert all(re.fullmatch(r"read/[a-z0-9-]+\.html", n) and len(n) < 90 for n in names.values())


@pytest.mark.parametrize("previous, text, expected", [
    ("Alpha beta gamma delta epsilon zeta.", "gamma delta epsilon zeta. Eta theta.", "Eta theta."),
    ("No overlap at all in this one.", "Something else entirely here.", "Something else entirely here."),
    ("short", "short and more", "short and more"),
    # Repetitive text: only as much as the chunker can carry over is dropped.
    ("ab " * 200, "ab " * 100 + "end", "ab " * 30 + "end"),
])
def test_overlap_is_dropped_once(previous, text, expected):
    got = reader.without_overlap(previous.strip(), text, limit=210)
    if expected.startswith("ab "):
        assert got.endswith("end") and len(text) - len(got) <= 210 and got.count("ab") >= 30
    else:
        assert got == expected


def test_from_a_reader_page_every_result_is_a_link_that_lands_on_its_passage(kit, handbook, browser, tmp_path):
    cid, ids = handbook
    out = tmp_path / "site"
    # One source keeps an address on the web, which still comes first.
    se.save_addresses(cid, documents={ids[0]: "https://example.org/holidays.html"})
    manifest, _ = se.write_export(cid, out, confirm_public=True, site=True, include="reader")
    with static_server(out) as server:
        page = browser.page()
        page.goto(server.url("search/read/travel-policy.html"))
        box = "document.querySelector('clio-search')"
        page.wait_for(f"!{box}.shadowRoot.querySelector('input').disabled")
        page.eval(f"{box}.setAttribute('results', '20')")
        page.type_into(f"{box}.shadowRoot.querySelector('input')", "policy holiday visitor clause")
        hits = page.wait_for(f"(() => {{ const r = [...{box}.shadowRoot.querySelectorAll('li')];"
                             " return r.length > 3 ? r.map(li => { const a = li.querySelector('a.title');"
                             " return a ? a.href : null }) : null })()")
        assert all(hits), hits
        on_web = [h for h in hits if h.startswith("https://example.org/holidays.html")]
        in_reader = [h for h in hits if h.startswith(server.url("search/read/"))]
        assert on_web and in_reader and len(on_web) + len(in_reader) == len(hits)
        assert all(re.search(r"\.html#p-[A-Za-z0-9_-]+$", h) for h in in_reader)
        page.goto(in_reader[0])
        target = in_reader[0].split("#", 1)[1]
        assert page.eval(f"document.getElementById({target!r}) !== null && "
                         f"document.querySelector(':target')?.id === {target!r}")
        assert page.eval("window.__errors === undefined || window.__errors.length === 0")
        assert not [u for u in page.requested() if not u.startswith(server.url())]
        page.close()

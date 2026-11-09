"""Citing: styles and exports, printed page numbers, corrections, and the
draft checker (quotes, citations, reference lists), over HTTP and MCP.

Papers are the invented ones from test_research (10.5555 test DOIs); the
model is a scripted fake and nothing touches the network.
"""

import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import config
from services.research import citations, draft_check, library, lookup
from tests.coverage_support import build_collection
from tests.test_research import PAPER_A, PAPER_B, FakeProvider


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(config.settings, "research_lookup_enabled", False, raising=False)
    monkeypatch.setattr(config.settings, "enable_reranker", False, raising=False)
    lookup.clear_cache()


@pytest.fixture()
def docs_dir(tmp_path, monkeypatch):
    from services.collection_service import collection_service
    directory = tmp_path / "documents"
    directory.mkdir()
    monkeypatch.setattr(collection_service, "get_documents_path", lambda cid: directory)
    return directory


MEMO = ["Pump seal inspection, unit 2. The seal leaked 4 litres per hour before replacement and none after."]


@pytest.fixture()
def indexer(tmp_path, docs_dir):
    ix = build_collection(tmp_path, {"paper-a.pdf": PAPER_A, "2018-download.pdf": PAPER_B, "seal-memo.txt": MEMO})
    ix.document_extractor.extract_text.side_effect = OSError("no file in tests")
    store = library.research_store(ix)
    for did in ("doc-1", "doc-2"):
        library.analyze_document("c1", ix, did, store=store)
    return ix


def _items(indexer):
    store = library.research_store(indexer)
    docs = {d["document_id"]: d for d in indexer.list_documents()}
    papers = store.get_papers(list(docs))
    return {did: citations.build_item(docs[did], papers.get(did), "Lab") for did in docs}


ARTICLE = {
    "title": "Root Hydraulics Predict Drought Survival", "authors": ["Mira Okafor", "Tomas Lindqvist", "Ana de la Cruz"],
    "year": 2021, "venue": "Journal of Imaginary Ecology", "doi": "10.5555/jie.2021.0042",
    "details": {"volume": "12", "issue": "3", "pages": "1184–1199", "page_offset": 1183},
}


def _article(**over):
    return citations.build_item({"document_id": "a", "filename": "a.pdf"}, {**ARTICLE, **over})


# ── names and styles ────────────────────────────────────────────────────

@pytest.mark.parametrize("name, family, given", [
    ("Mira Okafor", "Okafor", "Mira"),
    ("Okafor, Mira", "Okafor", "Mira"),
    ("Ana de la Cruz", "de la Cruz", "Ana"),
    ("Martin Luther King Jr.", "King", "Martin Luther"),
])
def test_names_split_into_family_and_given(name, family, given):
    parts = citations.split_name(name)
    assert (parts["family"], parts["given"]) == (family, given)


def test_an_organization_in_braces_is_never_split():
    assert citations.split_name("{Idaho Test Laboratory}") == {"literal": "Idaho Test Laboratory"}


@pytest.mark.parametrize("style, reference, in_text", [
    ("apa", "Okafor, M., Lindqvist, T., & de la Cruz, A. (2021). Root Hydraulics Predict Drought Survival. "
            "Journal of Imaginary Ecology, 12(3), 1184–1199. https://doi.org/10.5555/jie.2021.0042",
     "(Okafor et al., 2021, p. 1187)"),
    ("chicago", "Okafor, Mira, Tomas Lindqvist, and Ana de la Cruz. 2021. “Root Hydraulics Predict Drought "
                "Survival.” Journal of Imaginary Ecology 12 (3): 1184–1199. https://doi.org/10.5555/jie.2021.0042.",
     "(Okafor, Lindqvist, and de la Cruz 2021, 1187)"),
    ("mla", "Okafor, Mira, et al. “Root Hydraulics Predict Drought Survival.” Journal of Imaginary Ecology, "
            "vol. 12, no. 3, 2021, pp. 1184–1199, https://doi.org/10.5555/jie.2021.0042.",
     "(Okafor et al. 1187)"),
    ("ieee", "[1] M. Okafor, T. Lindqvist, and A. de la Cruz, “Root Hydraulics Predict Drought Survival,” "
             "Journal of Imaginary Ecology, vol. 12, no. 3, pp. 1184–1199, 2021. doi: 10.5555/jie.2021.0042.",
     "[1, p. 1187]"),
    ("harvard", "Okafor, M., Lindqvist, T. and de la Cruz, A. (2021) ‘Root Hydraulics Predict Drought Survival’, "
                "Journal of Imaginary Ecology, 12(3), pp. 1184–1199. Available at: https://doi.org/10.5555/jie.2021.0042.",
     "(Okafor, Lindqvist and de la Cruz, 2021, p. 1187)"),
])
def test_an_article_in_each_style_cites_the_printed_page(style, reference, in_text):
    item = _article()
    cited = citations.cite(item, style, page=4, number=1)
    assert cited["reference"] == reference
    assert cited["in_text"] == in_text
    assert cited["page"] == "1187" and cited["page_is_printed"]


def test_titles_are_italic_in_markdown_and_html_and_escaped():
    item = _article(venue="Plants & Water")
    assert "*Plants & Water*" in citations.reference_entry(item, "apa", "markdown")
    assert "<i>Plants &amp; Water</i>" in citations.reference_entry(item, "apa", "html")


def test_nothing_is_invented_for_a_sparse_internal_report():
    item = citations.build_item(
        {"document_id": "r", "filename": "itl-0042.pdf"},
        {"title": "Reactor Coolant Pump Seal Inspection", "authors": [],
         "details": {"organization": "Idaho Test Laboratory", "report_number": "ITL-EXT-21-0042"}},
        "Plant reports")
    assert item["kind"] == "report" and item["internal"]
    assert item["missing"] == ["year"]
    apa = citations.reference_entry(item, "apa")
    assert apa.startswith("Idaho Test Laboratory. (n.d.). Reactor Coolant Pump Seal Inspection (Report No. ITL-EXT-21-0042).")
    assert "collection “Plant reports”, file itl-0042.pdf" in apa
    assert "file itl-0042.pdf" not in citations.reference_entry(item, "apa", include_location=False)
    assert citations.in_text(item, "apa", "4") == "(Idaho Test Laboratory, n.d., p. 4)"


def test_a_source_never_analyzed_is_cited_by_its_file_name_and_says_so():
    item = citations.build_item({"document_id": "m", "filename": "seal-memo.txt"}, None)
    assert item["title"] == "seal-memo"
    assert item["missing"] == ["title", "authors", "year"]


def test_bibliography_is_alphabetical_except_ieee():
    a = _article()
    b = citations.build_item({"document_id": "b"}, {"title": "Embolism", "authors": ["Bianca Hale"], "year": 2018})
    assert [e[:4] for e in citations.bibliography([a, b], "apa")] == ["Hale", "Okaf"]
    assert [e[:3] for e in citations.bibliography([a, b], "ieee")] == ["[1]", "[2]"]


def test_exports_carry_every_field_for_reference_managers():
    a, b = _article(), _article()
    bib = citations.to_bibtex([a, b])
    assert "@article{okafor2021root," in bib and "@article{okafor2021roota," in bib
    assert "author = {Okafor, Mira and Lindqvist, Tomas and {de la Cruz}, Ana}" in bib
    assert "pages = {1184--1199}" in bib and "doi = {10.5555/jie.2021.0042}" in bib
    ris = citations.to_ris([a])
    assert "TY  - JOUR" in ris and "AU  - de la Cruz, Ana" in ris and "SP  - 1184" in ris and "EP  - 1199" in ris
    csl = json.loads(citations.to_csl_json([a]))[0]
    assert csl["type"] == "article-journal" and csl["issued"] == {"date-parts": [[2021]]}
    assert csl["author"][2] == {"family": "de la Cruz", "given": "Ana"}


# ── printed page numbers ────────────────────────────────────────────────

def test_printed_page_numbers_are_read_from_running_heads():
    pages = {i: f"Journal of Imaginary Ecology   {1183 + i}\nBody text of the page.\n" for i in range(1, 9)}
    assert citations.page_offset(pages) == (1183, 8)
    assert citations.page_range(pages, 1183) == "1184–1191"


def test_a_year_or_a_stray_number_does_not_move_page_numbers():
    pages = {1: "Published 2021\nTitle", 2: "Text\n12", 3: "Text", 4: "Text\n7"}
    assert citations.page_offset(pages) is None


def test_analysis_records_the_offset_and_pages(tmp_path, docs_dir):
    pages = [f"Imaginary Ecology {40 + i}\nPassage {i} about roots and drought." for i in range(1, 6)]
    ix = build_collection(tmp_path, {"article.pdf": pages})
    ix.document_extractor.extract_text.side_effect = OSError("no file")
    paper = library.analyze_document("c1", ix, "doc-1")
    assert paper["details"]["page_offset"] == 40
    assert paper["details"]["printed_pages"] == "41–45"
    assert paper["provenance"]["page_offset"]["source"] == "paper"


# ── corrections ─────────────────────────────────────────────────────────

def test_a_correction_is_labelled_kept_through_reanalysis_and_revertible(indexer):
    store = library.research_store(indexer)
    read_title = store.get_paper("doc-2")["title"]
    library.correct_record(store, "doc-2", {"year": "2017", "venue": "Imaginary Forest Science",
                                            "volume": "8", "authors": ["Hale, Bianca", "Carlos Ruiz"]}, user="ana")
    paper = store.get_paper("doc-2")
    assert paper["year"] == 2017 and paper["details"]["volume"] == "8"
    assert paper["provenance"]["year"]["source"] == "user"
    assert paper["provenance"]["year"]["detail"] == "entered by ana"
    assert paper["provenance"]["year"]["previous"]["value"] == 2018

    library.analyze_document("c1", indexer, "doc-2", store=store)
    again = store.get_paper("doc-2")
    assert again["year"] == 2017 and again["venue"] == "Imaginary Forest Science"
    assert again["details"]["volume"] == "8" and again["title"] == read_title
    assert any("a person corrected" in n for n in again["analysis_notes"])

    library.correct_record(store, "doc-2", {"year": "2016"})
    library.correct_record(store, "doc-2", {}, revert=["year", "volume"])
    reverted = store.get_paper("doc-2")
    assert reverted["year"] == 2018 and "volume" not in reverted["details"]
    assert reverted["provenance"]["year"]["source"] != "user"


@pytest.mark.parametrize("field, value, message", [
    ("year", "21", "four-digit"),
    ("doi", "not a doi", "not a DOI"),
    ("isbn", "978-0-00-000000-1", "check digit"),
    ("kind", "poem", "type must be"),
    ("url", "ftp://x", "http"),
    ("shoe_size", "9", "cannot be edited"),
])
def test_corrections_are_validated(indexer, field, value, message):
    with pytest.raises(library.CorrectionError, match=message):
        library.correct_record(library.research_store(indexer), "doc-1", {field: value})


# ── the draft checker ───────────────────────────────────────────────────

def _check(indexer, text, **kw):
    store = library.research_store(indexer)
    docs = indexer.list_documents()
    return draft_check.check_draft(indexer, docs, store.get_papers([d["document_id"] for d in docs]), text, **kw)


def _kinds(result):
    return [f["kind"] for f in result["findings"]]


def test_sentences_are_not_cut_at_abbreviations_or_initials():
    text = "Okafor et al. (2021) measured it, p. 4 shows it. J. K. Hale agreed. A new one starts here!"
    spans = draft_check.sentences(text)
    assert [text[s:e] for s, e in spans] == [
        "Okafor et al. (2021) measured it, p. 4 shows it.", "J. K. Hale agreed.", "A new one starts here!"]


def test_citation_forms_are_recognized():
    found = draft_check.find_citations(
        "As Okafor and Hale (2021, p. 4) say. Seen before (see Okafor et al., 2021, pp. 4–5; Hale 2018). "
        "MLA too (Okafor 12). Numbered [2, 4–5]. Not a citation (Figure 3).")
    forms = [(c["form"], c.get("surnames"), c.get("year"), c.get("page"), c.get("numbers")) for c in found]
    assert forms == [
        ("author-date", ["Okafor", "Hale"], "2021", "4", None),
        ("author-date", ["Okafor"], "2021", "4–5", None),
        ("author-date", ["Hale"], "2018", None, None),
        ("author-page", ["Okafor"], None, "12", None),
        ("numeric", None, None, None, [2, 4, 5]),
    ]


def test_a_verified_quote_with_the_right_page_passes(indexer):
    out = _check(indexer, 'Okafor et al. (2021) report that "Conductance did not predict growth rate" (p. 2).')
    verified = [f for f in out["findings"] if f["kind"] == "quote_verified"]
    assert len(verified) == 1 and verified[0]["document_id"] == "doc-1" and verified[0]["page"] == 2
    assert out["summary"]["errors"] == 0


def test_an_altered_quote_shows_what_the_source_says(indexer):
    out = _check(indexer, 'Okafor et al. (2021, p. 2) found "conductance did predict growth rate".')
    altered = next(f for f in out["findings"] if f["kind"] == "quote_altered")
    assert altered["severity"] == "error"
    assert altered["source_text"] == "Conductance did not predict growth rate."
    assert "drops it" in altered["message"]


def test_a_changed_number_in_a_quote_is_caught(indexer):
    out = _check(indexer, 'They found "Survival was 13% higher in the top conductance quartile" (Okafor et al., 2021, p. 2).')
    altered = next(f for f in out["findings"] if f["kind"] == "quote_altered")
    assert "13" in altered["message"] and "31" in altered["message"]


def test_a_quote_on_another_page_or_in_another_source_is_flagged(indexer):
    out = _check(indexer, 'Okafor et al. say "Conductance did not predict growth rate" (Okafor et al., 2021, p. 3). '
                          'And "Embolism thresholds vary threefold across biomes" (Okafor et al., 2021, p. 1).')
    kinds = _kinds(out)
    assert "quote_page_mismatch" in kinds and "quote_other_source" in kinds
    other = next(f for f in out["findings"] if f["kind"] == "quote_other_source")
    assert other["document_id"] == "doc-2" and other["in_text"] == "(Hale & Ruiz, 2018, p. 1)"


def test_an_uncited_quote_gets_its_citation(indexer):
    out = _check(indexer, 'As noted, "our sites were all in one valley, so the result may not transfer".')
    f = next(f for f in out["findings"] if f["kind"] == "quote_uncited")
    assert f["in_text"] == "(Okafor et al., 2021, p. 2)"


def test_citations_are_resolved_or_flagged(indexer):
    out = _check(indexer, "Roots matter (Okafor et al., 2020). Spring pressure (Ito, 2010). "
                          "Thresholds track rainfall (Hale & Ruiz, 2018).")
    kinds = _kinds(out)
    assert "citation_year_mismatch" in kinds and "citation_not_in_library" in kinds
    assert out["summary"]["citations_resolved"] == 1
    claim = next(f for f in out["findings"] if f["kind"] == "claim_evidence")
    assert claim["document_id"] == "doc-2" and claim["evidence"]


def test_the_reference_list_is_checked_against_the_text(indexer):
    out = _check(indexer, "Thresholds vary [1]. Roots predict survival [3].\n\nReferences\n"
                          "[1] B. Hale, C. Ruiz. Xylem Embolism Thresholds Across Forest Biomes. 2017.\n"
                          "[2] D. Moreau. Stomatal closure under soil drying in oak seedlings. 2019.\n")
    kinds = _kinds(out)
    assert "reference_year_mismatch" in kinds and "reference_unused" in kinds and "citation_no_entry" in kinds
    assert [r["status"] for r in out["references"]] == ["matched", "not_in_library"]


def test_uncited_sentences_get_a_suggested_source(indexer):
    out = _check(indexer, "Embolism thresholds vary threefold across biomes and track mean annual precipitation.")
    f = next(f for f in out["findings"] if f["kind"] == "uncited_suggestion")
    assert f["document_id"] == "doc-2" and f["in_text"].startswith("(Hale & Ruiz, 2018")
    quiet = _check(indexer, "Embolism thresholds vary threefold across biomes and track mean annual precipitation.",
                   suggest_uncited=False)
    assert "uncited_suggestion" not in _kinds(quiet)


def test_a_model_verdict_stands_only_on_a_verified_quote(indexer):
    # P2 is page 2 of paper A, where "Conductance did not predict growth rate" is printed.
    provider = FakeProvider({"claims": [
        {"claim": 1, "verdict": "supported", "quote": "Conductance did not predict growth rate", "passage": "P2"},
        {"claim": 2, "verdict": "supported", "quote": "Conductance strongly predicted height", "passage": "P2"},
    ]})
    out = _check(indexer, "Conductance did not predict growth rate (Okafor et al., 2021). "
                          "Conductance also predicted sapling height (Okafor et al., 2021).",
                 provider=provider, model="fake-model")
    kinds = _kinds(out)
    assert "claim_supported" in kinds and "claim_unverified" in kinds
    assert out["usage"]["calls"] == 1 and out["summary"]["claims_judged"] == 2
    assert "Conductance did not predict growth rate" in provider.prompts[0]


def test_numbered_citations_without_a_list_say_what_is_needed(indexer):
    out = _check(indexer, "Thresholds vary [1].")
    assert any("reference list" in n for n in out["notes"])


def test_overlong_or_empty_drafts_are_refused(indexer):
    with pytest.raises(ValueError):
        _check(indexer, "   ")
    with pytest.raises(ValueError):
        _check(indexer, "x" * (draft_check.MAX_DRAFT_CHARS + 1))


# ── HTTP ────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(indexer, monkeypatch):
    from api import research as api

    state = {"write": True}

    def access(collection_id, user_id, write=False, owner=False):
        if collection_id != "c1":
            raise HTTPException(404, "not found")
        if (write or owner) and not state["write"]:
            raise HTTPException(403, "read only")
        return "owner"

    monkeypatch.setattr(api, "get_indexer", lambda cid: (access(cid, None), indexer)[1])
    monkeypatch.setattr(api, "require_collection_access", access)
    monkeypatch.setattr(api, "_collection_name", lambda cid: "Lab library")
    app = FastAPI()
    app.include_router(api.router)
    c = TestClient(app)
    c.state = state
    return c


def test_api_citations_and_exports(client):
    out = client.post("/api/research/citations?collection_id=c1",
                      json={"passages": [{"document_id": "doc-1", "page": 2}, {"document_id": "doc-3", "page": 1}],
                            "style": "apa", "export": "bibtex"}).json()
    assert out["passages"][0]["in_text"] == "(Okafor et al., 2021, p. 2)"
    assert out["passages"][1]["in_text"] == "(seal-memo, n.d., p. 1)"
    assert "doc-3" in out["incomplete"]
    assert "Internal document, collection “Lab library”, file seal-memo.txt." in out["bibliography"][1]
    assert out["export"]["filename"] == "Lab-library.bib" and "@misc{" in out["export"]["content"]
    every = client.post("/api/research/citations?collection_id=c1", json={"style": "ieee"}).json()
    assert len(every["entries"]) == 3 and every["bibliography"][0].startswith("[1]")
    assert client.post("/api/research/citations?collection_id=c1",
                       json={"document_ids": ["nope"]}).status_code == 404


def test_api_correction_needs_write_access_and_validates(client):
    out = client.patch("/api/research/papers/doc-2/record?collection_id=c1",
                       json={"changes": {"venue": "Imaginary Forest Science", "volume": "8"}}).json()
    assert out["venue"] == "Imaginary Forest Science" and out["details"]["volume"] == "8"
    assert out["provenance"]["venue"]["source"] == "user" and out["corrected_at"]
    assert "venue" not in out["missing"]
    bad = client.patch("/api/research/papers/doc-2/record?collection_id=c1", json={"changes": {"year": "x"}})
    assert bad.status_code == 422 and "four-digit" in bad.json()["detail"]
    client.state["write"] = False
    assert client.patch("/api/research/papers/doc-2/record?collection_id=c1",
                        json={"changes": {"year": "2019"}}).status_code == 403


def test_api_check_draft_is_open_to_readers_and_scoped(client):
    client.state["write"] = False
    text = 'They found "Conductance did not predict growth rate" (Okafor et al., 2021, p. 2).'
    out = client.post("/api/research/check-draft?collection_id=c1", json={"text": text}).json()
    assert out["summary"]["quotes_verified"] == 1
    assert out["sources"]["doc-1"]["filename"] == "paper-a.pdf"
    scoped = client.post("/api/research/check-draft?collection_id=c1",
                         json={"text": text, "document_ids": ["doc-2"]}).json()
    assert "quote_verified" not in [f["kind"] for f in scoped["findings"]]
    assert client.post("/api/research/check-draft?collection_id=c1", json={"text": ""}).status_code == 422


# ── MCP ─────────────────────────────────────────────────────────────────

def test_mcp_tools_cite_and_check(indexer, monkeypatch):
    import services.mcp_server as mcp
    monkeypatch.setattr(mcp, "_resolve_collection_id", lambda cid=None: "c1")
    monkeypatch.setattr(mcp.indexer_manager, "get_indexer", lambda cid: indexer)
    monkeypatch.setattr(mcp.collection_service, "get_collection", lambda cid: {"id": "c1", "name": "Lab"})
    cited = mcp.cite_sources_sync([{"document_id": "doc-1", "page_number": 2}], style="mla")
    assert cited["citations"][0]["in_text"] == "(Okafor et al. 2)"
    assert cited["bibliography"][0].startswith("Okafor, Mira, et al.")
    with pytest.raises(ValueError):
        mcp.cite_sources_sync([{"document_id": "missing"}])

    checked = mcp.check_citations_sync('Okafor et al. (2021, p. 2) found "conductance did predict growth rate".')
    altered = next(f for f in checked["findings"] if f["kind"] == "quote_altered")
    assert altered["filename"] == "paper-a.pdf" and altered["source_text"]
    monkeypatch.setattr(mcp, "get_request_mcp_profile", lambda: {"include_sources": False})
    hidden = mcp.check_citations_sync('Okafor et al. (2021, p. 2) found "conductance did predict growth rate".')
    assert "source_text" not in next(f for f in hidden["findings"] if f["kind"] == "quote_altered")


def test_quotes_in_a_long_source_are_found_without_matching_every_passage(tmp_path, docs_dir, monkeypatch):
    pages = [f"Chapter text {i}. Filler about the general history of forestry and timber markets." for i in range(1, 200)]
    pages[150] = "Seedlings watered weekly retained turgor through the second drought, unlike the controls."
    ix = build_collection(tmp_path, {"long-book.pdf": pages})
    calls = []
    real = draft_check.verify_quote
    monkeypatch.setattr(draft_check, "verify_quote", lambda q, p, ps: calls.append(len(ps)) or real(q, p, ps))
    out = draft_check.check_draft(ix, ix.list_documents(), {},
                                  '"Seedlings watered weekly retained turgor through the second drought" (Book, n.d.)')
    quote = next(f for f in out["findings"] if f["kind"].startswith("quote_"))
    assert quote["page"] == 151
    assert calls and max(calls) < 10

"""Paper records that say when they are not sure (roadmap R2, R3; work
order A1, A2).

The shapes come from the committed smoke PDFs and from what the 2026-11-18
live walk showed: a title that is the abstract, a title that is two body
sentences, a venue read as the author, an organisation cited as a person.
"""

from pathlib import Path

import pytest

from services.research import library
from services.research.extract import (
    arxiv_id_from_filename, author_problem, authors_from_text_checked, extract_paper, respace_lines,
    title_from_text, title_problem,
)

SMOKE = Path(__file__).parent / "fixtures" / "research_bench" / "smoke"


# ── plausibility rules ──────────────────────────────────────────────────

@pytest.mark.parametrize("title, reason", [
    ("We count nesting gulls on city rooftops from fixed cameras across two breeding seasons and "
     "compare the counts with volunteer surveys of the", "middle of a sentence"),
    ("The survey covered forty-two stations along the northern shore over three winters. Each station",
     "sentences"),
    ("breeding seasons and compare the counts", "starts in the middle"),
    ("TidalMemoryinLongSensorRecords and more", "spaces"),
    ("Proceedings of the Workshop on Imaginary Retrieval", "venue"),
    ("Imaginary Urban Ecology, 2022", "venue"),
    ("A" + " very long title" * 12, "too long"),
])
def test_implausible_titles_are_refused(title, reason):
    assert reason in (title_problem(title) or "")


@pytest.mark.parametrize("title", [
    "Attention Is All You Need",
    "Lighthouse Indexing for Coastal Sensor Archives",
    "What Is a Gull? Counting Birds from Rooftops",
    "Sparse vs. Dense Retrieval: A Comparison",
    "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
])
def test_ordinary_titles_pass(title):
    assert title_problem(title) is None


def test_a_layout_title_may_be_longer_than_a_text_one():
    title = "Sparse Lexicons Outperform Dense Maps " * 4 + "Queries"
    assert title_problem(title) and not title_problem(title, from_layout=True)


@pytest.mark.parametrize("name", ["Imaginary Urban Ecology", "Harbour Authority Engineering Office",
                                  "Journal of Invented Coasts", "Smith2"])
def test_names_that_are_not_people(name):
    assert author_problem(name)


@pytest.mark.parametrize("name", ["Ines Halvorsen", "Jan van der Berg", "Ayesha Karim"])
def test_names_that_are_people(name):
    assert author_problem(name) is None


# ── title from text ─────────────────────────────────────────────────────

def test_abstract_text_is_not_taken_for_a_title_when_the_title_is_a_picture():
    page = ("Lena Okafor, Sami Haddad\n"
            "We count nesting gulls on city rooftops from fixed cameras across two\n"
            "breeding seasons and compare the counts with volunteer surveys of the\n"
            "same roofs. Automated counts track the surveys within six percent.\n"
            "Imaginary Urban Ecology, 2022\n")
    assert title_from_text(page) is None


def test_body_sentences_are_not_taken_for_a_title():
    page = ("TidalMemoryinLongSensorRecords\n"
            "Abstract\n"
            "The survey covered forty-two stations along the northern shore over three winters. Each station\n"
            "logged water level, salinity and temperature every ten minutes.\n")
    assert title_from_text(page) is None


def test_a_title_followed_by_authors_is_still_found():
    assert title_from_text("Quiet Harbours at Night\nAda Byron, Charles Babbage\nAbstract\n")[0] \
        == "Quiet Harbours at Night"


# ── authors ─────────────────────────────────────────────────────────────

def test_affiliation_marks_separate_names_without_commas():
    read = authors_from_text_checked("Sparse Lexicons\nRhea Castellanos1* Bram Vos2 Keiko Tanabe1\n"
                                     "1Southport University 2Delta Maritime Archive\n", "Sparse Lexicons")
    assert read["names"] == ["Rhea Castellanos", "Bram Vos", "Keiko Tanabe"]


def test_run_together_names_get_their_spaces_back_and_say_so():
    assert respace_lines("OskarLindgren1WenZhao2PriyaRaman1TomasRuiz2") == \
        "Oskar Lindgren, Wen Zhao, Priya Raman, Tomas Ruiz"
    read = authors_from_text_checked("Tidal Memory\nOskarLindgren1WenZhao2PriyaRaman1TomasRuiz2\n", "Tidal Memory")
    assert read["names"] == ["Oskar Lindgren", "Wen Zhao", "Priya Raman", "Tomas Ruiz"] and read["repaired"]


def test_a_venue_line_is_never_an_author():
    read = authors_from_text_checked("Imaginary Urban Ecology, 2022\n", None)
    assert read["names"] == [] and read["organization"] is None


def test_an_issuing_body_is_an_organisation_not_a_person():
    read = authors_from_text_checked("Annual Review of Breakwater Condition\nHarbour Authority Engineering Office\n"
                                     "March 2021\n", "Annual Review of Breakwater Condition")
    assert read["names"] == [] and read["organization"] == "Harbour Authority Engineering Office"


def test_a_title_repeated_on_the_copyright_page_does_not_move_the_author_search():
    pages = "A Field Guide to\nSalt Marsh Grasses\nby Pilar Montenegro\nA Field Guide to Salt Marsh Grasses\n© 2019"
    assert authors_from_text_checked(pages, "A Field Guide to Salt Marsh Grasses")["names"] == ["Pilar Montenegro"]


# ── identifiers ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("filename, expected", [
    ("2401.18059.pdf", "2401.18059"), ("2312.10997v5.pdf", "2312.10997"), ("arXiv-1706.03762.pdf", "1706.03762"),
    ("2024.wir-1.12.pdf", None), ("1234.5678.pdf", None), ("report.pdf", None),
])
def test_arxiv_id_from_the_file_name(filename, expected):
    assert arxiv_id_from_filename(filename) == expected


# ── whole records from the smoke PDFs ───────────────────────────────────

def _pages(name):
    from services.document_extractor import DocumentExtractor
    result = DocumentExtractor().extract_text(SMOKE / name)
    return {int(k): v for k, v in result.page_texts.items()}


def _confidence(record, field):
    return (record["provenance"].get(field) or {}).get("confidence")


@pytest.mark.parametrize("name, title, authors", [
    ("smoke-clean-article.pdf", "Lighthouse Indexing for Coastal Sensor Archives",
     ["Ines Halvorsen", "Tomasz Wierzbicki", "Amara Okonjo"]),
    ("smoke-two-column.pdf", "Sparse Lexicons Outperform Dense Maps on Short Harbour Logbook Queries",
     ["Rhea Castellanos", "Bram Vos", "Keiko Tanabe"]),
    ("smoke-epigraph.pdf", "Storm Surge Memory in Enclosed Harbours", ["Dmitri Volkov", "Ayesha Karim"]),
    ("smoke-book.pdf", "A Field Guide to Salt Marsh Grasses", ["Pilar Montenegro"]),
])
def test_right_records_are_stated_with_confidence(name, title, authors):
    record = extract_paper(_pages(name), SMOKE / name, "pdf")
    assert record["title"] == title and record["authors"] == authors
    assert _confidence(record, "title") == "high" and _confidence(record, "authors") == "high"


def test_lost_spaces_are_repaired_and_the_names_marked_for_checking():
    record = extract_paper(_pages("smoke-lost-spaces.pdf"), SMOKE / "smoke-lost-spaces.pdf", "pdf")
    assert record["title"] == "Tidal Memory in Long Sensor Records"
    assert record["authors"] == ["Oskar Lindgren", "Wen Zhao", "Priya Raman", "Tomas Ruiz"]
    assert _confidence(record, "authors") == "low"


def test_a_picture_title_leaves_the_title_empty_and_the_authors_doubtful():
    record = extract_paper(_pages("smoke-title-image.pdf"), SMOKE / "smoke-title-image.pdf", "pdf")
    assert record["title"] is None
    assert record["authors"] == ["Lena Okafor", "Sami Haddad"] and _confidence(record, "authors") == "low"


def test_a_report_credits_its_organisation_not_a_person():
    record = extract_paper(_pages("smoke-report.pdf"), SMOKE / "smoke-report.pdf", "pdf")
    assert record["authors"] == [] and record["organization"] == "Harbour Authority Engineering Office"


# ── needs_checking ──────────────────────────────────────────────────────

def _paper(**fields):
    return {"analyzed_at": "2026-11-18T00:00:00Z", **fields}


def test_low_confidence_fields_need_checking_with_a_reason():
    paper = _paper(title="Gulls", authors=["Lena Okafor"], year=2022, provenance={
        "title": {"source": "paper", "confidence": "low", "check": "why title"},
        "authors": {"source": "paper", "confidence": "high"},
        "year": {"source": "paper", "confidence": "high"}})
    assert library.needs_checking(paper) == {"title": "why title"}


def test_a_missing_title_needs_checking_but_missing_authors_alone_do_not():
    assert set(library.needs_checking(_paper(title=None, authors=[], provenance={}))) == {"title"}


def test_corrected_and_catalogue_values_never_need_checking():
    paper = _paper(title="We count nesting gulls on city rooftops. Each roof", authors=["Imaginary Urban Ecology"],
                   provenance={"title": {"source": "user"}, "authors": {"source": "openalex", "confidence": "high"}})
    assert library.needs_checking(paper) == {}


def test_records_analysed_before_confidence_existed_are_judged_by_the_rules():
    paper = _paper(title="We count nesting gulls on city rooftops from fixed cameras across two breeding "
                         "seasons and compare the counts with volunteer surveys of the",
                   authors=["Imaginary Urban Ecology"], year=2022,
                   provenance={"title": {"source": "paper", "detail": "first title-like lines of page 1"},
                               "authors": {"source": "paper"}, "year": {"source": "paper"}})
    assert set(library.needs_checking(paper)) == {"title", "authors"}


def test_an_unanalysed_paper_has_nothing_to_check():
    assert library.needs_checking({"title": None}) == {}


# ── rules from the manifest misses (2026-11-18) ─────────────────────────

from services.research.extract import (  # noqa: E402
    dated_line_year, strip_article_label, title_doubt, venue_from_text,
)


def test_accented_names_are_names():
    read = authors_from_text_checked("BEIR\nNandan Thakur, Nils Reimers, Andreas Rücklé, Iryna Gurevych\n", "BEIR")
    assert "Andreas Rücklé" in read["names"]


@pytest.mark.parametrize("name", ["Google AI Language", "Microsoft Research", "Mountain View", "Facebook AI Research",
                                  "KyungHyun Cho Yoshua Bengio"])
def test_affiliations_places_and_fused_names_are_not_people(name):
    assert author_problem(name)


def test_a_name_that_is_part_of_the_title_is_not_an_author():
    assert author_problem("Think Python", "Think Python: How to Think Like a Computer Scientist")


def test_names_without_spaces_between_their_parts_are_split():
    assert respace_lines("KaimingHe XiangyuZhang ShaoqingRen JianSun") == \
        "Kaiming He, Xiangyu Zhang, Shaoqing Ren, Jian Sun"


def test_a_list_that_goes_on_below_is_marked_incomplete():
    page = ("Language Models are Few-Shot Learners\n"
            "Tom B. Brown, Benjamin Mann, Nick Ryder\n"
            "OpenAI\n"
            "Prafulla Dhariwal Arvind Neelakantan Pranav Shyam Girish Sastry\n"
            "Abstract\n")
    read = authors_from_text_checked(page, "Language Models are Few-Shot Learners")
    assert read["names"] == ["Tom B. Brown", "Benjamin Mann", "Nick Ryder"] and read["incomplete"]


def test_affiliation_letters_glued_to_names_are_noticed():
    page = ("A Survey\nYunfan Gaoa, Yun Xiongb, Xinyu Gaob\naShanghai Research Institute\nbFudan University\n")
    assert authors_from_text_checked(page, "A Survey")["letters_glued"]


def test_the_year_on_the_proceedings_line_beats_the_most_frequent_one():
    page = ("BLEU: a Method for Automatic Evaluation of Machine Translation\n"
            "as shown by Smith (1999) and Jones (1999)\n"
            "Proceedings of the 40th Annual Meeting of the ACL, Philadelphia, July 2002, pp. 311-318.\n")
    assert dated_line_year(page) == 2002


def test_a_year_read_by_frequency_alone_needs_checking():
    pages = {1: "Quiet Harbours at Night\nAda Byron, Charles Babbage\nAbstract\nWe count boats seen in 1999 and 1999.\n"}
    record = extract_paper(pages)
    assert record["year"] == 1999 and _confidence(record, "year") == "low"


@pytest.mark.parametrize("title, reason", [
    ("Withdrawn NIST Technical Series Publication", "notice"),
    ("SIMULA SPRINGER BRIEFS ON COMPUTING 6", "series"),
    ("Programming for Computations –", "middle of a sentence"),
])
def test_covers_series_lines_and_cut_titles_are_refused(title, reason):
    assert reason in (title_problem(title) or "")


def test_an_article_type_label_is_not_part_of_the_title():
    assert strip_article_label("Comment: The FAIR Guiding Principles for scientific data management") == \
        "The FAIR Guiding Principles for scientific data management"


def test_a_word_broken_across_lines_keeps_the_title_but_doubts_it():
    assert title_doubt("LORA: LOW-RANK ADAPTATION OF LARGE LAN- GUAGE MODELS")
    assert title_doubt("Attention Is All You Need") is None


@pytest.mark.parametrize("line, venue", [
    ("Imaginary Urban Ecology, 2022", "Imaginary Urban Ecology"),
    ("Proceedings of the Workshop on Imaginary Retrieval, pages 12-20", "Proceedings of the Workshop on Imaginary Retrieval"),
    ("Published 2023 in the Journal of Invented Coasts.", "Journal of Invented Coasts"),
    ("Google Research, 2019", None),
    ("Lena Okafor, Sami Haddad", None),
])
def test_venue_lines(line, venue):
    assert venue_from_text(line) == venue


def test_a_journal_article_without_a_doi_is_not_cited_as_an_internal_document():
    from services.research import citations
    doc = {"document_id": "d", "filename": "gulls.pdf"}
    article = citations.build_item(doc, {"title": "Gulls", "kind": "article", "venue": "Urban Ecology", "year": 2022,
                                         "analyzed_at": "x"}, "Default")
    memo = citations.build_item(doc, {"title": "Quarterly memo", "kind": "report", "analyzed_at": "x"}, "Default")
    assert not article["internal"] and memo["internal"]

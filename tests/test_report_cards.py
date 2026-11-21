"""H1: study cards from an FAQ report."""

from services import report_cards
from tests.test_collection_bundle import _source_collection, env  # noqa: F401
from tests.test_report_file import _report, client  # noqa: F401

FAQ = """# Staff FAQ

**How much holiday do staff get?**
Staff accrue 25 days a year [Source 1].

**When are expenses paid?** Within thirty days of a claim [Source 2]. Claims need *receipts*.

1. **Is <b>this</b> a question?** Yes, with a	tab [Source 9].

## Notes

Some closing remarks that are not a card.
"""


def test_an_faqs_pairs_are_read_in_order_with_citations_as_names():
    sources = [{"filename": "holidays.txt", "page_number": 1}, {"filename": "expenses.txt", "page_number": 2}]
    cards = report_cards.pairs(FAQ, sources)
    assert cards == [
        {"question": "How much holiday do staff get?", "answer": "Staff accrue 25 days a year (holidays.txt p. 1)."},
        {"question": "When are expenses paid?",
         "answer": "Within thirty days of a claim (expenses.txt p. 2). Claims need receipts."},
        {"question": "Is <b>this</b> a question?", "answer": "Yes, with a tab."},
    ]
    tsv = report_cards.anki_tsv(cards, "Staff FAQ")
    lines = tsv.splitlines()
    assert lines[:2] == ["#separator:tab", "#html:true"] and lines[3] == "#columns:Front\tBack"
    assert all(line.count("\t") == 1 for line in lines[4:]) and len(lines) == 7
    assert "Is &lt;b&gt;this&lt;/b&gt; a question?\t" in tsv


def test_a_study_guide_has_no_answers_so_no_cards(env, client):
    cid, ids = _source_collection(env)
    _report(cid, ids, "## Review Questions\n\n1. What is accrued?\n2. When are claims paid?\n")
    r = client.get(f"/api/artifacts/rep000000001/cards?collection_id={cid}")
    assert r.status_code == 422 and "without answers" in r.json()["detail"]


def test_http_gives_anki_and_json(env, client):
    cid, ids = _source_collection(env)
    _report(cid, ids, FAQ)
    r = client.get(f"/api/artifacts/rep000000001/cards?collection_id={cid}")
    assert r.status_code == 200 and "staff-faq.anki.txt" in r.headers["content-disposition"]
    assert r.text.startswith("#separator:tab\n")
    j = client.get(f"/api/artifacts/rep000000001/cards?collection_id={cid}&format=json").json()
    assert j["title"] == "Staff FAQ" and len(j["cards"]) == 3

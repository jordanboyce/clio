"""C4: an Ask answer is checked against the passages it cites, with no model."""

from services.answer_check import check_answer

POLICY = {"text_snippet": "Claims above 400 dollars need a second signature. They must reach accounts "
                          "payable within 60 days, on form TE-1824, or they are refused."}
LEAVE = {"text_snippet": "A leaver's file is held for six years from the last day of employment. "
                         "The schedule took effect on 24 March 2026."}


def test_an_answer_that_matches_its_sources_passes():
    answer = ("Claims above 400 dollars need a second signature [Source 1]. They are due within "
              "60 days on form TE-1824 [Source 1]. A leaver's file is kept for 6 years [Source 2].")
    assert check_answer(answer, [POLICY, LEAVE]) == []


def test_one_altered_figure_marks_that_sentence_only():
    answer = ("Claims above 400 dollars need a second signature [Source 1]. They are due within "
              "90 days [Source 1]. A leaver's file is kept for six years [Source 2].")

    failures = check_answer(answer, [POLICY, LEAVE])

    assert failures == [{"group": 1, "sentence": 1, "sources": [1], "missing": ["90"], "reason": "not_in_passage"}]


def test_an_invented_identifier_or_misquote_is_marked():
    answer = ('Use form TE-1842 [Source 1]. The policy says claims "must reach payroll" [Source 1].')
    failures = check_answer(answer, [POLICY])
    assert [f["missing"] for f in failures] == [["TE-1842"], ["“must reach payroll”"]]


def test_a_source_number_that_does_not_exist_is_marked():
    failures = check_answer("Claims need two signatures [Source 3].", [POLICY, LEAVE])
    assert failures[0]["reason"] == "no_such_source" and failures[0]["missing"] == ["Source 3"]


def test_fair_restatements_raise_no_false_alarm():
    answer = (
        "1. The schedule started on 2026-03-24 [Source 2].\n"
        "2. Files are held for 6 years [Source 2].\n"
        "Claims over $400 need two signatures within sixty days [Source 1, Source 2]."
    )
    assert check_answer(answer, [POLICY, LEAVE]) == []


def test_sentences_without_citations_and_garbage_input_are_left_alone():
    assert check_answer("Seven years, probably.", [POLICY]) == []
    assert check_answer(None, None) == []


def test_where_a_sentence_says_it_read_something_is_not_a_figure():
    """Measured on A2 (llama3.2:3b): "as stated in Source 4" was read as the
    figure 4 and the sentence marked. Locators are not claims."""
    passage = {"text_snippet": "Managers confirm the access of each team member every year."}
    answer = ("Access is reviewed every year, as stated in Source 1 on page 3 of "
              "lakeshore-access-standard-rev-b.pdf [Source 1].")
    assert check_answer(answer, [passage]) == []

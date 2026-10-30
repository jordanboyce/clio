"""Content-policy scanner: rule packs, thresholds, and the document decision.

The scanner is a tripwire, not a judge: these tests pin down that ordinary
prose and *discussion* of sensitive topics stay clear, that the categories
which matter trip, and that the decision knob and the critical-category
escalation behave as documented.
"""

import pytest

from services import content_policy as cp


BENIGN = (
    "Quarterly review. Revenue grew 4% on the back of the new logistics "
    "contract; headcount is flat. The kitchen renovation finishes in March. "
    "Please send expense reports by Friday."
)

SAFEGUARDING_POLICY = (
    "Our safeguarding policy: any concern about child sexual abuse or the "
    "exploitation of a minor must be reported to the designated officer "
    "within 24 hours. Staff receive annual grooming-awareness training."
)

CSAM_SOLICITATION = (
    "looking to trade underage nude vids, have a big collection, hit me on telegram"
)

DRUG_SALE = (
    "Selling top-grade cocaine and fentanyl, stealth shipping worldwide, "
    "price list on request, escrow in monero accepted."
)

EXPLICIT = " ".join(["hardcore porn cock pussy blowjob cumshot deepthroat"] * 2)

CRED_DUMP = "\n".join(f"user{i}@example.com:Passw0rd{i}!xyz" for i in range(12))

PRIVATE_KEY = (
    "config notes\n-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n-----END RSA PRIVATE KEY-----\n"
)

EXPLOSIVE_HOWTO = "step by step instructions for how to build a pipe bomb at home"


def _luhn_card():
    return "4539 1488 0343 6467"  # a Luhn-valid test number


def test_benign_text_is_clear():
    r = cp.scan_pages({1: BENIGN})[1]
    assert not r.is_flagged
    assert r.risk_score == 0.0
    assert r.categories == {}


def test_discussion_of_abuse_is_not_material():
    """A safeguarding policy mentions the topic; it must not be treated as
    the thing itself — only the low-weight mention rule fires."""
    r = cp.scan_pages({1: SAFEGUARDING_POLICY})[1]
    assert not r.is_flagged
    assert not r.has_critical
    assert set(r.categories) <= {"csae"}


def test_solicitation_is_critical():
    r = cp.scan_pages({1: CSAM_SOLICITATION})[1]
    assert r.is_flagged and r.has_critical
    assert "csae" in r.categories


def test_explosive_instructions_are_critical():
    r = cp.scan_pages({1: EXPLOSIVE_HOWTO})[1]
    assert r.has_critical
    assert "violent_extremism" in r.categories


def test_drug_sale_flags_high():
    r = cp.scan_pages({1: DRUG_SALE})[1]
    assert r.is_flagged and not r.has_critical
    assert "drug_trade" in r.categories


def test_explicit_needs_several_hits():
    single = cp.scan_pages({1: "He wrote the word porn once in a media-studies essay."})[1]
    assert not single.is_flagged
    many = cp.scan_pages({1: EXPLICIT})[1]
    assert many.is_flagged
    assert "sexual_explicit" in many.categories


def test_credential_dump_flags_and_suggests_confidential():
    r = cp.scan_pages({1: CRED_DUMP})[1]
    assert r.is_flagged
    assert "credential_dump" in r.categories
    status, flags, suggested = cp.decide({1: r}, action="flag")
    assert status == "flagged"
    assert suggested == "confidential"


def test_private_key_is_sensitivity_only():
    r = cp.scan_pages({1: PRIVATE_KEY})[1]
    assert not r.is_flagged            # secrets never flag on their own
    assert "secrets" in r.categories
    status, flags, suggested = cp.decide({1: r}, action="quarantine")
    assert status == "clear"
    assert suggested == "confidential"
    assert flags and flags["categories"].get("secrets")  # hint survives


def test_card_number_dump_uses_luhn():
    invoices = "\n".join(f"INV-{n} total 1234 5678 9012 3450" for n in range(8))  # not Luhn-valid
    assert not cp.scan_pages({1: invoices})[1].is_flagged
    cards = "\n".join(f"card {_luhn_card()} exp 12/29 cvv 123" for _ in range(6))
    r = cp.scan_pages({1: cards})[1]
    assert r.is_flagged
    assert any(f.pattern_name == "card_number_bulk" for f in r.findings)


def test_code_mode_only_runs_sensitivity_packs():
    text = DRUG_SALE + "\n" + PRIVATE_KEY
    r = cp.scan_pages({1: text}, code=True)[1]
    assert "drug_trade" not in r.categories
    assert "secrets" in r.categories


def test_decide_action_off_is_always_clear():
    r = cp.scan_pages({1: CSAM_SOLICITATION})[1]
    assert cp.decide({1: r}, action="off")[0] == "clear"


def test_decide_critical_escalates_flag_to_quarantine():
    r = cp.scan_pages({1: CSAM_SOLICITATION})[1]
    assert cp.decide({1: r}, action="flag")[0] == "quarantined"
    assert cp.decide({1: r}, action="quarantine")[0] == "quarantined"
    assert cp.decide({1: r}, action="reject")[0] == "rejected"


def test_decide_non_critical_follows_knob():
    r = cp.scan_pages({1: DRUG_SALE})[1]
    assert cp.decide({1: r}, action="flag")[0] == "flagged"
    assert cp.decide({1: r}, action="quarantine")[0] == "quarantined"
    assert cp.decide({1: r}, action="reject")[0] == "rejected"


def test_decide_flags_payload_shape():
    r = cp.scan_pages({1: DRUG_SALE, 2: BENIGN})[1]
    status, flags, _ = cp.decide({1: r, 2: cp.scan_pages({2: BENIGN})[2]}, action="flag")
    assert status == "flagged"
    assert flags["flagged_page_count"] == 1
    assert "1" in flags["flagged_pages"]
    assert flags["categories"]["drug_trade"] >= 1
    assert flags["critical"] is False
    assert flags["action"] == "flag"


def test_llm_verdict_can_escalate_but_not_clear():
    clean = cp.scan_pages({1: BENIGN})[1]
    # The LLM says drug trade with confidence: the document is flagged.
    status, flags, _ = cp.decide(
        {1: clean}, action="flag",
        llm={"categories": ["drug_trade"], "confidence": 0.9, "rationale": "x"},
    )
    assert status == "flagged"
    assert flags["llm"]["categories"] == ["drug_trade"]
    # The LLM says clean on a rule-flagged page: the rule wins.
    hot = cp.scan_pages({1: DRUG_SALE})[1]
    status, _, _ = cp.decide(
        {1: hot}, action="flag",
        llm={"categories": [], "confidence": 0.9, "rationale": "fine"},
    )
    assert status == "flagged"
    # A critical LLM verdict quarantines even in flag mode.
    status, _, _ = cp.decide(
        {1: clean}, action="flag",
        llm={"categories": ["csae"], "confidence": 0.95, "rationale": "x"},
    )
    assert status == "quarantined"


def test_llm_review_is_off_by_default(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "content_policy_llm_review", False)
    assert cp.llm_review({1: DRUG_SALE}) is None


def test_scan_result_to_dict_caps_findings():
    r = cp.scan_pages({1: CRED_DUMP * 5})[1]
    d = r.to_dict()
    assert d["is_flagged"] is True
    assert len(d["findings"]) <= 40
    assert d["categories"]["credential_dump"] >= 8


def test_digits_inside_a_card_number_are_not_a_slogan():
    """"1488" in "4539 1488 0343 6467" is a number, not the slogan."""
    page = cp.scan_pages({1: "Card 4539 1488 0343 6467 expires 03/27. Ref 1488-22."})[1]
    assert "violent_extremism" not in page.categories
    slogans = cp.scan_pages({1: "They chant 1488 at every rally; 1488 is painted on the wall."})[1]
    assert slogans.categories.get("violent_extremism") == 2


def test_a_person_called_dick_is_not_explicit_content():
    biography = (
        "Dick joined the firm in 1998. Dick led the audit practice, and Dick's team "
        "grew to forty. Colleagues remember Dick for his patience. Author: Dick Francis."
    )
    page = cp.scan_pages({1: biography})[1]
    assert "sexual_explicit" not in page.categories and not page.is_flagged


def test_one_innocent_word_repeated_is_not_explicit_content():
    for text in (
        "The cat: our pussy sleeps all day. The pussy eats at six. A pussy needs a vet. Pussy cats purr.",
        "Blue tits, great tits and coal tits visit the feeder; tits nest in the box; tits are common garden birds.",
        "She graduated summa cum laude; he graduated magna cum laude; both cum laude honours; cum laude again.",
        "Phone: xxx-xxx-xxxx. Account: xxx. Ref xxx. Code xxx. Fill in xxx where shown.",
    ):
        page = cp.scan_pages({1: text})[1]
        assert "sexual_explicit" not in page.categories, text


def test_explicit_text_still_flags():
    page = cp.scan_pages({1: EXPLICIT})[1]
    assert page.is_flagged and "sexual_explicit" in page.categories

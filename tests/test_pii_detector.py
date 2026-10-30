"""Personal data in sources: found locally, summarised without storing it,
masked on the way out when asked.

Presidio is optional; the suite runs on the built-in regex engine and pins the
contract both engines share: a dense page is a ``pii`` content-policy finding
whose fate PII_SCAN_ACTION decides, a sparse page is nothing, and the stored
summary never holds the data itself.
"""

import pytest

import config
from services import content_policy as cp
from services import pii_detector as pii

# Emails and phones only: a column of card numbers is a credential dump in its
# own right (content_policy) and would mask what this suite is about.
CUSTOMER_EXPORT = "\n".join(
    f"Customer {i}: user{i}@example.com, +1 (415) 555-01{i:02d}, server 10.0.0.{i + 1}"
    for i in range(6)
)
LETTERHEAD = "Acme Ltd, 1 Main St. Call +1 (415) 555-0100 or write to hello@acme.example."


@pytest.fixture()
def pii_action(monkeypatch):
    def _set(action, threshold=None):
        monkeypatch.setattr(config.settings, "pii_scan_action", action, raising=False)
        if threshold is not None:
            monkeypatch.setattr(config.settings, "pii_flag_threshold", threshold, raising=False)
    return _set


def test_regex_engine_finds_cards_and_ssns():
    r = pii.scan("Card 4539 1488 0343 6467, SSN 123-45-6789, IBAN GB82 WEST 1234 5698 7654 32.", dense_threshold=1)
    assert r.entities == {"CREDIT_CARD": 1, "US_SSN": 1, "IBAN_CODE": 1}


def test_regex_engine_finds_the_common_types():
    r = pii.scan(CUSTOMER_EXPORT, dense_threshold=10)
    assert r.entities["EMAIL_ADDRESS"] == 6
    assert r.entities["PHONE_NUMBER"] == 6
    assert r.entities["IP_ADDRESS"] == 6
    assert r.total == 18 and r.dense
    assert "email addresses" in r.describe()


def test_previews_are_masked_and_bounded():
    r = pii.scan(CUSTOMER_EXPORT, dense_threshold=1)
    assert r.previews and len(r.previews) <= pii.MAX_PREVIEWS
    for p in r.previews:
        assert "*" in p["preview"]
        assert "example.com" not in p["preview"] and "555-01" not in p["preview"]
    assert pii.mask("jane.doe@acme.com").startswith("j") and pii.mask("jane.doe@acme.com").endswith("com")
    assert pii.mask("ab") == "****"


def test_sparse_pages_are_not_dense():
    assert not pii.scan(LETTERHEAD, dense_threshold=10).dense
    assert pii.scan("Nothing personal here, just the agenda.").total == 0


def test_redact_replaces_spans_with_types():
    out = pii.redact("Mail jane@acme.example or ring +1 (415) 555-0100, host 10.0.0.7.")
    assert out == "Mail <EMAIL_ADDRESS> or ring <PHONE_NUMBER>, host <IP_ADDRESS>."


def test_redact_if_enabled_follows_the_setting(monkeypatch):
    monkeypatch.setattr(config.settings, "pii_redact_outputs", False, raising=False)
    assert pii.redact_if_enabled("jane@acme.example") == "jane@acme.example"
    monkeypatch.setattr(config.settings, "pii_redact_outputs", True, raising=False)
    assert pii.redact_if_enabled("jane@acme.example") == "<EMAIL_ADDRESS>"


def test_engine_status_names_the_engine():
    status = pii.engine_status()
    assert status["engine"] in ("regex", "presidio")
    assert "EMAIL_ADDRESS" in status["entities"]


def test_entities_setting_narrows_the_search(monkeypatch):
    monkeypatch.setattr(config.settings, "pii_entities", "EMAIL_ADDRESS", raising=False)
    r = pii.scan(CUSTOMER_EXPORT, dense_threshold=1)
    assert set(r.entities) == {"EMAIL_ADDRESS"}


# ── Through the content-policy pipeline ─────────────────────────────────────


def test_label_mode_suggests_confidential_without_flagging(pii_action):
    pii_action("label", threshold=10)
    page = cp.scan_pages({1: CUSTOMER_EXPORT})[1]
    assert "pii" in page.categories and not page.is_flagged
    status, flags, suggested = cp.decide({1: page})
    assert status == "clear" and suggested == "confidential"
    assert flags and flags["categories"]["pii"] == 18


def test_flag_mode_queues_a_dense_page_even_when_policy_is_off(pii_action, monkeypatch):
    monkeypatch.setattr(config.settings, "content_policy_action", "off", raising=False)
    pii_action("flag", threshold=10)
    assert cp.scanning_enabled()
    page = cp.scan_pages({1: CUSTOMER_EXPORT})[1]
    assert page.is_flagged
    status, flags, suggested = cp.decide({1: page})
    assert status == "flagged" and suggested == "confidential"
    finding = next(f for f in page.findings if f.category == "pii")
    assert "email addresses" in finding.matched_text
    assert "example.com" not in finding.matched_text  # masked, never the data


def test_quarantine_mode_holds_a_customer_list(pii_action):
    pii_action("quarantine", threshold=10)
    page = cp.scan_pages({1: CUSTOMER_EXPORT})[1]
    status, flags, _ = cp.decide({1: page})
    assert status == "quarantined" and flags["action"] == "quarantine"


def test_off_skips_the_pii_scan(pii_action, monkeypatch):
    pii_action("off")
    page = cp.scan_pages({1: CUSTOMER_EXPORT})[1]
    assert "pii" not in page.categories
    monkeypatch.setattr(config.settings, "content_policy_action", "off", raising=False)
    assert not cp.scanning_enabled()


def test_sparse_page_is_never_a_finding(pii_action):
    pii_action("quarantine", threshold=10)
    page = cp.scan_pages({1: LETTERHEAD})[1]
    assert "pii" not in page.categories and not page.is_flagged


def test_code_is_skipped(pii_action):
    pii_action("flag", threshold=1)
    page = cp.scan_pages({1: CUSTOMER_EXPORT}, code=True)[1]
    assert "pii" not in page.categories


def test_policy_reason_reads_as_personal_data():
    from services.governance import _policy_reason

    text = _policy_reason({"policy_status": "flagged", "policy_flags": {"categories": {"pii": 18}}})
    assert text == "Flagged by the content scan: personal data (18 items)."

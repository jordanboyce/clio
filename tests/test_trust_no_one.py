"""Trust no one: one switch for the prompt-injection and PII scans.

Off (the default), the people adding sources are trusted: no source is
scanned for instructions aimed at a model or for personal data, and nothing
is masked, whatever INJECTION_SCAN_ACTION and PII_SCAN_ACTION say. On, those
two decide what the scans do. The content policy and the blocklist are not
part of it. CLIO_PROFILE=secure turns it on.
"""

import pytest
from fastapi.testclient import TestClient

import config
from services import pii_detector, prompt_injection_detector
from tests.test_governance import ATTACK, _index, fresh_db, indexer, policy  # noqa: F401

PEOPLE = "\n".join(f"Contact {n}: person{n}@example.com, +1 555 010 {n:04d}" for n in range(30))


@pytest.fixture()
def trust(monkeypatch):
    def apply(on, **more):
        for s in {id(config.settings): config.settings, id(pii_detector): None}.values():
            if s is not None:
                monkeypatch.setattr(s, "trust_no_one", on)
                for name, value in more.items():
                    monkeypatch.setattr(s, name, value)
    return apply


# ── the setting ───────────────────────────────────────────────────────────


def test_it_is_off_by_default(monkeypatch):
    monkeypatch.delenv("TRUST_NO_ONE", raising=False)
    monkeypatch.delenv("CLIO_PROFILE", raising=False)
    assert config.Settings().trust_no_one is False


def test_the_secure_profile_turns_it_on(monkeypatch):
    monkeypatch.delenv("TRUST_NO_ONE", raising=False)
    monkeypatch.setenv("CLIO_PROFILE", "secure")
    assert config.Settings().trust_no_one is True


def test_an_operator_may_still_turn_it_off_under_the_secure_profile(monkeypatch):
    monkeypatch.setenv("CLIO_PROFILE", "secure")
    monkeypatch.setenv("TRUST_NO_ONE", "false")
    assert config.Settings().trust_no_one is False


# ── what it switches ──────────────────────────────────────────────────────


def test_off_means_no_scan_whatever_the_actions_say(trust):
    trust(False, injection_scan_action="review", pii_scan_action="quarantine", pii_redact_outputs=True)

    assert prompt_injection_detector.scan_action() == "off"
    assert not prompt_injection_detector.scanning_enabled()
    assert pii_detector.action() == "off"
    assert not pii_detector.scanning_enabled()
    assert not pii_detector.redaction_enabled()


def test_on_means_the_actions_decide(trust):
    trust(True, injection_scan_action="warn", pii_scan_action="flag", pii_redact_outputs=True)

    assert prompt_injection_detector.scan_action() == "warn"
    assert pii_detector.action() == "flag"
    assert pii_detector.redaction_enabled()


def test_a_trusted_source_is_indexed_without_injection_or_pii_findings(tmp_path, indexer, policy, trust):  # noqa: F811
    trust(False, pii_scan_action="flag")
    policy("flag")
    meta = _index(indexer, tmp_path, "notes.txt", ATTACK + "\n" + PEOPLE)

    doc = indexer.vector_store.metadata_store.get_document_info(meta.document_id)
    assert not doc.get("injection_warnings")
    assert "pii" not in ((doc.get("policy_flags") or {}).get("categories") or {})


def test_with_trust_no_one_on_the_same_source_is_flagged(tmp_path, indexer, policy, trust):  # noqa: F811
    trust(True, pii_scan_action="flag", injection_scan_action="review")
    policy("flag")
    meta = _index(indexer, tmp_path, "notes.txt", ATTACK + "\n" + PEOPLE)

    doc = indexer.vector_store.metadata_store.get_document_info(meta.document_id)
    assert doc.get("injection_warnings")
    assert "pii" in ((doc.get("policy_flags") or {}).get("categories") or {})


def test_the_content_policy_is_not_part_of_it(trust):
    from services import content_policy

    trust(False, content_policy_action="flag")
    assert content_policy.rule_packs_enabled()


# ── where it shows ────────────────────────────────────────────────────────


@pytest.fixture()
def client(indexer):  # noqa: F811
    import main

    return TestClient(main.app)


def test_settings_read_and_save_it(client, trust, monkeypatch):
    from services.config_manager import config_manager

    trust(False)
    assert client.get("/api/config").json()["trust_no_one"] is False

    saved = client.post("/api/config", json={"trust_no_one": True})
    assert saved.status_code == 200, saved.text
    assert config.settings.trust_no_one is True
    assert client.post("/api/config", json={"trust_no_one": "yes"}).status_code in (400, 422)
    assert config_manager is not None


def test_the_review_screen_and_the_features_list_say_what_runs(client, trust):
    trust(False, injection_scan_action="review", pii_scan_action="label")

    review = client.get("/api/admin/review").json()
    assert review["trust_no_one"] is False
    assert review["injection_scan_action"] == "off" and review["pii_scan_action"] == "off"
    pii = next(f for f in client.get("/api/features").json()["features"] if f["id"] == "pii")
    assert pii["state"] == "off" and pii["label"] == "Trust no one" and "trusted" in pii["detail"]

"""One guesser behind an untrusted proxy locks everyone out (E11); the
readiness report says so and names the setting to change."""

import pytest
from fastapi.testclient import TestClient

import config

PASSWORD = "a-long-enough-password"
PROXY = "10.0.0.5"


@pytest.fixture()
def behind_a_proxy(monkeypatch):
    import main
    from services import readiness

    for s in {id(config.settings): config.settings, id(main.settings): main.settings,
              id(readiness.settings): readiness.settings}.values():
        monkeypatch.setattr(s, "auth_password", PASSWORD)
        monkeypatch.setattr(s, "auth_failure_limit", 3)
        monkeypatch.setattr(s, "host", "0.0.0.0")
        monkeypatch.setattr(s, "bind_address", "")
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    return TestClient(main.app, client=(PROXY, 50000))


def _auth(secret=PASSWORD):
    return {"Authorization": f"Bearer {secret}"}


def test_one_guesser_locks_out_everyone_behind_the_proxy(behind_a_proxy):
    """The reproduction: every caller has the proxy's address."""
    for _ in range(3):
        assert behind_a_proxy.get("/api/collections", headers=_auth("guess")).status_code == 401

    assert behind_a_proxy.get("/api/collections", headers=_auth()).status_code == 429


def _report_check(client):
    report = client.get("/api/system/readiness", headers=_auth()).json()
    return next((c for c in report["checks"] if c["id"] == "proxy_address"), None)


def test_the_readiness_report_names_the_address_and_the_setting(behind_a_proxy):
    for _ in range(50):
        behind_a_proxy.get("/livez")

    check = _report_check(behind_a_proxy)

    assert check["status"] == "warn"
    assert PROXY in check["detail"] and "locks everyone out" in check["detail"]
    assert f"FORWARDED_ALLOW_IPS={PROXY}" in check["fix"]


def test_no_warning_while_requests_come_from_many_addresses(behind_a_proxy):
    import main

    other = TestClient(main.app, client=("10.0.0.9", 50000))
    for _ in range(30):
        behind_a_proxy.get("/livez")
        other.get("/livez")

    assert _report_check(behind_a_proxy) is None


def test_no_warning_on_a_loopback_install(behind_a_proxy, monkeypatch):
    from services import readiness

    monkeypatch.setattr(readiness.settings, "host", "127.0.0.1")
    for _ in range(50):
        behind_a_proxy.get("/livez")

    assert _report_check(behind_a_proxy) is None

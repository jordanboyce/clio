"""GET /api/me says where "Sign out and clear this browser" goes (E10)."""

import asyncio
from types import SimpleNamespace

import config
from api import system


def _me(identity, via):
    request = SimpleNamespace(state=SimpleNamespace(auth_identity=identity, auth_via=via))
    return asyncio.run(system.whoami(request))


def test_a_signed_in_person_is_told_where_to_sign_out(monkeypatch):
    monkeypatch.setattr(system.settings, "sign_out_url", "/oauth2/sign_out")

    me = _me("alice@example.com", "trusted_header")

    assert me == {"authenticated_via": "trusted_header", "identity": "alice@example.com",
                  "sign_out_url": "/oauth2/sign_out"}


def test_nobody_to_sign_out_gets_no_address(monkeypatch):
    monkeypatch.setattr(system.settings, "sign_out_url", "/oauth2/sign_out")

    assert _me(None, "password")["sign_out_url"] == ""


def test_the_default_is_no_sign_out_page():
    assert config.Settings.model_fields["sign_out_url"].default == ""

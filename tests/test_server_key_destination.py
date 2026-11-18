"""Keys the server holds only go to the endpoint they were configured for.

A team key (stored through /api/agent/config) and the deployment key
(AI_API_KEY) used to be attached to whatever X-AI-Base-URL a request named,
so anyone who could reach the API could point a request at a listener of
their own and read the key off the wire. These tests run a local listener in
that role and assert it never sees a server-held key, while a caller's own
key, and the deployment's own endpoint, keep working.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import HTTPException

import api.chat as chat
import api.deps as deps
from services.ai_service import _endpoint_identity, create_provider, server_key_allowed

DEPLOYMENT_KEY = "sk-DEPLOYMENT-SECRET"
TEAM_KEY = "sk-TEAM-SECRET"


def _listener():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # never print Authorization headers

        def _answer(self):
            seen.append((self.command, self.path, self.headers.get("Authorization")))
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            body = json.dumps({"object": "list", "data": [{"id": "m", "object": "model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _answer
        do_POST = _answer

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}", seen


@pytest.fixture()
def attacker():
    server, url, seen = _listener()
    yield url, seen
    server.shutdown()
    server.server_close()


@pytest.fixture()
def deployment_endpoint():
    server, url, seen = _listener()
    yield url, seen
    server.shutdown()
    server.server_close()


@pytest.fixture()
def deployment(monkeypatch, deployment_endpoint):
    """The secure-profile shape: a private OpenAI-compatible endpoint and its key."""
    from config import settings

    url, seen = deployment_endpoint
    monkeypatch.setattr(settings, "ai_provider", "openai_compatible")
    monkeypatch.setattr(settings, "ai_base_url", url)
    monkeypatch.setattr(settings, "ai_api_key", DEPLOYMENT_KEY)
    monkeypatch.setattr(settings, "ai_model", "")
    monkeypatch.setattr(settings, "offline_mode", False)
    return url, seen


@pytest.fixture()
def team_key(monkeypatch):
    from services.app_database import app_db

    monkeypatch.setattr(app_db, "get_agent_api_key", lambda provider: TEAM_KEY)


def _keys(seen):
    return {auth for _, _, auth in seen}


# ── the rule itself ─────────────────────────────────────────────────────────

def test_endpoint_identity_ignores_spelling_not_destination():
    assert _endpoint_identity("HTTPS://Gateway.Corp:443/v1/") == "https://gateway.corp"
    assert _endpoint_identity("http://gw:8000") == _endpoint_identity("http://gw:8000/v1")
    assert _endpoint_identity("http://gw:8000") != _endpoint_identity("http://gw:8001")
    assert _endpoint_identity("http://gw.corp") != _endpoint_identity("http://gw.corp.evil.example")
    assert _endpoint_identity("http://gw/openai") != _endpoint_identity("http://gw/other")


def test_server_key_allowed(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "ai_provider", "openai_compatible")
    monkeypatch.setattr(settings, "ai_base_url", "http://gateway.corp:8000")
    assert server_key_allowed("openai", None)                      # provider's own endpoint
    assert server_key_allowed("openai_compatible", "")
    assert server_key_allowed("openai_compatible", "http://gateway.corp:8000/v1/")
    assert not server_key_allowed("openai_compatible", "http://evil.example/v1")
    assert not server_key_allowed("openai", "http://gateway.corp:8000")  # not the default provider
    monkeypatch.setattr(settings, "ai_base_url", "")
    assert not server_key_allowed("openai_compatible", "http://gateway.corp:8000")


# ── AI_API_KEY ──────────────────────────────────────────────────────────────

def test_validate_key_never_sends_the_deployment_key_elsewhere(deployment, attacker):
    url, seen = attacker
    chat.validate_api_key(x_ai_provider="openai_compatible", x_ai_key=None, x_ai_base_url=f"{url}/v1",
                          x_ai_model="m", x_ollama_model="")
    assert seen, "the request should still go to the endpoint the caller named"
    assert f"Bearer {DEPLOYMENT_KEY}" not in _keys(seen)


def test_model_listing_never_sends_the_deployment_key_elsewhere(deployment, attacker):
    url, seen = attacker
    chat.list_provider_models(x_ai_provider="openai_compatible", x_ai_key="", x_ai_base_url=url)
    assert f"Bearer {DEPLOYMENT_KEY}" not in _keys(seen)


def test_create_provider_withholds_the_deployment_key_from_a_caller_url(deployment, attacker):
    url, seen = attacker
    create_provider("openai_compatible", None, base_url=url, model="m").list_models()
    assert f"Bearer {DEPLOYMENT_KEY}" not in _keys(seen)


def test_the_deployment_endpoint_still_gets_its_key(deployment):
    url, seen = deployment
    # No base URL from the caller, and the configured one spelled differently.
    chat.list_provider_models(x_ai_provider="openai_compatible", x_ai_key="", x_ai_base_url="", refresh=True)
    chat.list_provider_models(x_ai_provider="openai_compatible", x_ai_key="", x_ai_base_url=f"{url}/v1/",
                              refresh=True)
    assert [auth for _, _, auth in seen] == [f"Bearer {DEPLOYMENT_KEY}", f"Bearer {DEPLOYMENT_KEY}"]


# ── team keys ───────────────────────────────────────────────────────────────

def test_model_listing_never_sends_a_team_key_elsewhere(deployment, team_key, attacker):
    url, seen = attacker
    chat.list_provider_models(x_ai_provider="openai_compatible", x_ai_key="", x_ai_base_url=f"{url}/v1",
                              refresh=True)
    assert f"Bearer {TEAM_KEY}" not in _keys(seen)


def test_chat_refuses_a_caller_url_without_a_caller_key(deployment, team_key, attacker):
    url, seen = attacker
    with pytest.raises(HTTPException) as refused:
        chat._build_provider("openai", None, None, None, None, None, f"{url}/v1")
    assert refused.value.status_code == 400
    assert "only sent to the endpoint they were configured for" in refused.value.detail
    assert seen == []


def test_resolve_ai_key(deployment, team_key):
    url, _ = deployment
    assert deps.resolve_ai_key("openai", None) == TEAM_KEY
    assert deps.resolve_ai_key("openai", None, "http://evil.example/v1") == ""
    assert deps.resolve_ai_key("openai", "sk-mine", "http://evil.example/v1") == "sk-mine"
    assert deps.resolve_ai_key("openai_compatible", None, url) == TEAM_KEY


def test_a_callers_own_key_still_reaches_a_callers_own_endpoint(deployment, team_key, attacker):
    url, seen = attacker
    provider = chat._build_provider("openai", "sk-mine", None, None, None, None, f"{url}/v1")
    provider.list_models()
    assert _keys(seen) == {"Bearer sk-mine"}


def test_create_provider_refuses_a_server_key_however_it_got_there(deployment, team_key, attacker):
    """A future caller that resolves a key without the base URL still cannot leak it."""
    url, seen = attacker
    for key in (DEPLOYMENT_KEY, TEAM_KEY):
        with pytest.raises(ValueError, match="only sent to the endpoint it was configured for"):
            create_provider("openai_compatible", key, base_url=f"{url}/v1")
    assert seen == []
    # The configured endpoint itself is fine with the same key.
    endpoint, _ = deployment
    create_provider("openai_compatible", DEPLOYMENT_KEY, base_url=f"{endpoint}/v1/", model="m")


# ── AI_ALLOWED_BASE_URLS (S10) ──────────────────────────────────────────────
# A caller's own key to a caller's own endpoint leaks nothing of the server's,
# but the server still makes the request: an open choice is a way to make it
# call into its own network. The operator can close it.

def test_base_url_allowlist_rule(monkeypatch):
    from config import settings
    from services.ai_service import base_url_allowed

    monkeypatch.setattr(settings, "ai_base_url", "http://gateway.corp:8000/v1")
    monkeypatch.setattr(settings, "ollama_base_url", "http://localhost:11434")
    monkeypatch.setattr(settings, "ai_allowed_base_urls", "")
    assert base_url_allowed("openai_compatible", "http://169.254.169.254/latest")   # empty: any

    monkeypatch.setattr(settings, "ai_allowed_base_urls", "none")
    assert base_url_allowed("openai_compatible", "")                               # nothing chosen
    assert base_url_allowed("openai_compatible", "http://gateway.corp:8000")       # AI_BASE_URL
    assert base_url_allowed("ollama", "http://localhost:11434/")                   # OLLAMA_BASE_URL
    assert not base_url_allowed("openai_compatible", "http://100.100.100.200/v1")
    assert base_url_allowed("anthropic", "http://ignored.example")                 # fixed endpoint

    monkeypatch.setattr(settings, "ai_allowed_base_urls", "https://llm.corp.example, http://vllm:8000/v1")
    assert base_url_allowed("openai", "https://llm.corp.example/team-a/v1")
    assert base_url_allowed("openai_compatible", "http://vllm:8000")
    assert not base_url_allowed("openai_compatible", "https://llm.corp.example.evil.example")
    assert not base_url_allowed("openai_compatible", "http://vllm:8001")
    assert base_url_allowed("openai_compatible", "http://gateway.corp:8000")       # own endpoint stays


def test_a_caller_endpoint_outside_the_allowlist_is_never_called(deployment, attacker, monkeypatch):
    from config import settings

    url, seen = attacker
    monkeypatch.setattr(settings, "ai_allowed_base_urls", "none")
    with pytest.raises(ValueError, match="endpoints its administrator allowed"):
        create_provider("openai_compatible", "sk-mine", base_url=f"{url}/v1", model="m")
    with pytest.raises(HTTPException) as refused:
        chat._build_provider("openai", "sk-mine", None, None, None, None, f"{url}/v1")
    assert refused.value.status_code == 400
    with pytest.raises(ValueError, match="endpoints its administrator allowed"):
        create_provider("ollama", base_url=url)
    assert seen == []
    # The operator's own endpoint still works, and so does a listed one.
    endpoint, _ = deployment
    create_provider("openai_compatible", None, base_url=f"{endpoint}/v1/", model="m").list_models()
    monkeypatch.setattr(settings, "ai_allowed_base_urls", url)
    create_provider("openai_compatible", "sk-mine", base_url=f"{url}/v1", model="m").list_models()
    assert _keys(seen) == {"Bearer sk-mine"}


def test_secure_profile_allows_only_the_operators_endpoint(monkeypatch):
    import config

    monkeypatch.setenv("CLIO_PROFILE", "secure")
    assert config.Settings().ai_allowed_base_urls == "none"
    monkeypatch.setenv("AI_ALLOWED_BASE_URLS", "https://llm.corp.example")
    assert config.Settings().ai_allowed_base_urls == "https://llm.corp.example"
    monkeypatch.delenv("CLIO_PROFILE")
    monkeypatch.delenv("AI_ALLOWED_BASE_URLS")
    assert config.Settings().ai_allowed_base_urls == ""


# Unset, the rule follows the bind (E3): open while only this machine can reach
# the port, the operator's own endpoints once the network can.

@pytest.mark.parametrize("host, bind, expected", [
    ("127.0.0.1", "", "any"),            # bare metal, the default
    ("0.0.0.0", "127.0.0.1", "any"),     # container published on loopback only
    ("0.0.0.0", "", "none"),             # container or server on the network
    ("10.0.0.5", "", "none"),
])
def test_unset_allowlist_follows_the_bind(monkeypatch, host, bind, expected):
    from config import settings
    from services.ai_service import allowed_base_urls

    monkeypatch.setattr(settings, "ai_allowed_base_urls", "")
    monkeypatch.setattr(settings, "host", host)
    monkeypatch.setattr(settings, "bind_address", bind)

    assert allowed_base_urls(settings) == expected


def test_a_network_server_refuses_a_chosen_endpoint_unless_any_is_set(monkeypatch):
    from config import settings
    from services.ai_service import base_url_allowed

    monkeypatch.setattr(settings, "ai_base_url", "http://gateway.corp:8000/v1")
    monkeypatch.setattr(settings, "host", "0.0.0.0")
    monkeypatch.setattr(settings, "bind_address", "")
    monkeypatch.setattr(settings, "ai_allowed_base_urls", "")
    assert not base_url_allowed("openai_compatible", "http://169.254.169.254/latest")
    assert base_url_allowed("openai_compatible", "http://gateway.corp:8000")      # its own endpoint

    for value in ("any", "*", "ANY"):
        monkeypatch.setattr(settings, "ai_allowed_base_urls", value)
        assert base_url_allowed("openai_compatible", "http://169.254.169.254/latest")


def test_the_startup_line_says_what_was_decided_and_why(monkeypatch):
    from config import settings
    from services.ai_service import allowed_base_urls_log_line

    monkeypatch.setattr(settings, "ai_allowed_base_urls", "")
    monkeypatch.setattr(settings, "host", "0.0.0.0")
    monkeypatch.setattr(settings, "bind_address", "")
    line = allowed_base_urls_log_line(settings)
    assert "only" in line and "reachable from the network" in line and "AI_ALLOWED_BASE_URLS" in line

    monkeypatch.setattr(settings, "host", "127.0.0.1")
    assert "any address" in allowed_base_urls_log_line(settings)

    monkeypatch.setattr(settings, "ai_allowed_base_urls", "https://llm.corp.example")
    assert "https://llm.corp.example" in allowed_base_urls_log_line(settings)


def test_the_refusal_names_no_setting(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "ai_allowed_base_urls", "none")
    with pytest.raises(ValueError) as refused:
        create_provider("openai_compatible", "sk-mine", base_url="http://10.1.2.3/v1", model="m")
    assert "AI_ALLOWED" not in str(refused.value) and "=" not in str(refused.value)

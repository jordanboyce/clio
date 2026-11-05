"""The deployment's own AI provider (AI_PROVIDER / AI_BASE_URL / AI_MODEL).

This is what makes the container usable on-prem against a private model: the
operator configures the endpoint once in the environment, and every AI surface
reaches it without anyone entering a URL or key in a browser. The properties
worth pinning down are that the default fills only what a caller omitted, that
a caller bringing its own credentials still wins, that offline mode is not
weakened by it, and that the key never leaves the server.
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import config
from config import settings as _import_time_settings
from models.schemas import AIOptions, ArtifactRequest, ChatRequest, StarterQuestionsRequest
from services.ai_service import apply_deployment_defaults, create_provider
from services.config_manager import MASKED_SECRET, VALID_CONFIG_FIELDS, config_manager


def _settings_objects():
    """Every live Settings instance.

    Tests that reload `config` leave modules split across two instances —
    one held by whoever imported before the reload, one in `config.settings`
    now. Patching both is what makes these tests independent of suite order.
    """
    objs = {id(_import_time_settings): _import_time_settings}
    objs.setdefault(id(config.settings), config.settings)
    return list(objs.values())


@pytest.fixture()
def ai_settings(monkeypatch):
    """Set deployment AI settings on every live Settings instance."""

    def apply(**values):
        for s in _settings_objects():
            for key, value in values.items():
                monkeypatch.setattr(s, key, value)

    return apply


@pytest.fixture()
def private_endpoint(ai_settings):
    """A deployment pointed at a self-hosted OpenAI-compatible server."""
    ai_settings(
        ai_provider="openai_compatible",
        ai_base_url="http://vllm.internal:8000/v1",
        ai_model="llama-3.3-70b",
        ai_api_key="internal-key",
        ai_provider_label="Acme Internal LLM",
    )


# ── the factory ───────────────────────────────────────────────────────────


def test_provider_builds_from_the_environment_alone(private_endpoint):
    provider = create_provider("openai_compatible")

    assert str(provider.client.base_url).rstrip("/") == "http://vllm.internal:8000/v1"
    assert provider.FAST_MODEL == "llama-3.3-70b"
    assert provider.client.api_key == "internal-key"


def test_caller_credentials_win_over_the_deployment(private_endpoint):
    provider = create_provider(
        "openai_compatible", "byo-key", base_url="http://elsewhere/v1", model="mixtral"
    )

    assert str(provider.client.base_url).rstrip("/") == "http://elsewhere/v1"
    assert provider.FAST_MODEL == "mixtral"
    assert provider.client.api_key == "byo-key"


def test_other_providers_are_untouched(private_endpoint):
    api_key, kwargs = apply_deployment_defaults("anthropic", None, {})

    assert api_key is None
    assert kwargs == {}
    # …and an Anthropic request without a key still fails the same way.
    with pytest.raises(ValueError, match="API key required"):
        create_provider("anthropic")


def test_offline_mode_still_refuses_cloud_providers(ai_settings):
    ai_settings(ai_provider="anthropic", ai_api_key="sk-ant-deployment", offline_mode=True)

    with pytest.raises(ValueError, match="offline"):
        create_provider("anthropic")


def test_ollama_falls_back_to_the_configured_daemon(ai_settings):
    """Inside a container "localhost" is the container, so the fallback has to
    be OLLAMA_BASE_URL — the browser only sends a base URL when it differs
    from its own default."""
    ai_settings(ai_provider="", ollama_base_url="http://host.docker.internal:11434")

    provider = create_provider("ollama")

    assert provider.base_url == "http://host.docker.internal:11434"


# ── key resolution ────────────────────────────────────────────────────────


def test_key_resolution_order(private_endpoint):
    from api.deps import resolve_ai_key

    assert resolve_ai_key("openai_compatible", "header-key") == "header-key"
    assert resolve_ai_key("openai_compatible", None) == "internal-key"
    # A provider this deployment did not configure gets nothing.
    assert resolve_ai_key("openai", None) == ""


# ── the HTTP surface the frontend reads ───────────────────────────────────


@pytest.fixture()
def client(monkeypatch, tmp_path):
    import main

    # Config writes normally mirror into the repo's .env — keep them here.
    monkeypatch.setattr(config_manager, "env_file", tmp_path / ".env")
    return TestClient(main.app)


# ── a request that names no provider ──────────────────────────────────────


REQUEST_MODELS = [
    lambda **kw: ChatRequest(messages=[], **kw),
    lambda **kw: AIOptions(**kw),
    lambda **kw: StarterQuestionsRequest(**kw),
    lambda **kw: ArtifactRequest(artifact_type="summary", **kw),
]


@pytest.mark.parametrize("request_model", REQUEST_MODELS)
def test_a_request_that_names_no_provider_uses_the_deployments(private_endpoint, request_model):
    assert request_model().provider == "openai_compatible"
    assert request_model(provider=None).provider == "openai_compatible"
    assert request_model(provider="").provider == "openai_compatible"


@pytest.mark.parametrize("request_model", REQUEST_MODELS)
def test_a_request_that_names_a_provider_keeps_it(private_endpoint, request_model):
    assert request_model(provider="ollama").provider == "ollama"


@pytest.mark.parametrize("request_model", REQUEST_MODELS)
def test_a_deployment_with_no_provider_keeps_the_old_default(ai_settings, request_model):
    ai_settings(ai_provider="")
    assert request_model().provider == "anthropic"


def test_the_default_follows_a_settings_change_without_a_restart(ai_settings):
    ai_settings(ai_provider="ollama")
    assert ChatRequest(messages=[]).provider == "ollama"
    ai_settings(ai_provider="openai_compatible")
    assert ChatRequest(messages=[]).provider == "openai_compatible"


@pytest.fixture()
def built_provider(monkeypatch):
    """Records which provider /api/chat builds, and stops there."""
    import api.chat as chat

    names = []

    def build(name, *a, **k):
        names.append(name)
        raise HTTPException(status_code=418, detail="stop here")

    monkeypatch.setattr(chat, "_build_provider", build)
    return names


CHAT = {"messages": [{"role": "user", "content": "What does recharge depend on?"}], "use_cache": False}


def test_chat_with_no_provider_reaches_the_deployments_endpoint(client, private_endpoint, built_provider):
    client.post("/api/chat", json=CHAT)
    assert built_provider == ["openai_compatible"]


def test_chat_naming_a_provider_is_not_redirected(client, private_endpoint, built_provider):
    client.post("/api/chat", json={**CHAT, "provider": "ollama"})
    assert built_provider == ["ollama"]


@pytest.mark.parametrize("route", ["/api/ai/validate-key", "/api/ai/models"])
def test_provider_header_routes_default_to_the_deployments(client, private_endpoint, monkeypatch, route):
    import api.chat as chat

    seen = []

    def from_headers(provider, *a, **k):
        seen.append(provider)
        raise ValueError("stop")

    monkeypatch.setattr(chat, "_provider_from_headers", from_headers)

    client.post(route)
    assert seen == ["openai_compatible"]

    seen.clear()
    client.post(route, headers={"X-AI-Provider": "openai"})
    assert seen == ["openai"]


def test_deployment_endpoint_reports_the_endpoint_but_never_the_key(client, private_endpoint):
    body = client.get("/api/ai/deployment").json()

    assert body["configured"] is True
    assert body["provider"] == "openai_compatible"
    assert body["label"] == "Acme Internal LLM"
    assert body["model"] == "llama-3.3-70b"
    assert body["base_url"] == "http://vllm.internal:8000/v1"
    assert body["key_configured"] is True
    assert "internal-key" not in str(body)


def test_deployment_endpoint_is_quiet_when_unconfigured(client, ai_settings):
    ai_settings(ai_provider="")

    body = client.get("/api/ai/deployment").json()

    assert body["configured"] is False
    assert "base_url" not in body


def test_unlabelled_private_endpoint_gets_a_neutral_name(client, ai_settings):
    ai_settings(
        ai_provider="openai_compatible",
        ai_base_url="http://llm.internal/v1",
        ai_provider_label="",
    )

    assert client.get("/api/ai/deployment").json()["label"] == "Private endpoint"


# ── runtime configuration (survives a container recreated without .env) ────


def test_config_round_trip_masks_the_key(client, private_endpoint):
    current = config_manager.get_current_config()

    assert current["ai_base_url"] == "http://vllm.internal:8000/v1"
    assert current["ai_api_key"] == MASKED_SECRET
    assert current["ai_api_key_set"] is True
    assert {"ai_provider", "ai_base_url", "ai_model", "ai_api_key"} <= VALID_CONFIG_FIELDS


def test_config_rejects_an_endpointless_private_provider(client, ai_settings):
    ai_settings(ai_provider="", ai_base_url="")

    result = config_manager.update_config({"ai_provider": "openai_compatible"})

    assert result["success"] is False
    assert "ai_base_url" in result["errors"][0]


def test_config_rejects_an_unknown_provider(client):
    result = config_manager.update_config({"ai_provider": "not-a-provider"})

    assert result["success"] is False
    assert "Unknown AI provider" in result["errors"][0]

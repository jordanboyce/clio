"""The deployment's AI provider for server-side calls nobody asked for by name.

Requests name a provider or get one through ``default_ai_provider`` (see
test_deployment_ai_default.py). The content-policy reviewer, the custom file
type suggestion and the coverage questions have no request to take it from:
they ask ``resolve_default_provider``. An on-prem install that sets only
AI_PROVIDER has to reach its endpoint from there too, without a stored
preference changing meaning and without a provider nobody configured appearing.
"""

import pytest

import config
from config import settings as _import_time_settings
from services import content_policy, readiness
from services.ai_service import OllamaProvider, OpenAICompatibleProvider, resolve_default_provider
from services.app_database import app_db

PURPOSE = {"purpose": "test"}


def _settings_objects():
    """Every live Settings instance, since tests that reload `config` split them."""
    objs = {id(s): s for s in (_import_time_settings, config.settings, readiness.settings)}
    return list(objs.values())


@pytest.fixture()
def ai_settings(monkeypatch):
    def apply(**values):
        for s in _settings_objects():
            for key, value in values.items():
                monkeypatch.setattr(s, key, value)

    return apply


@pytest.fixture(autouse=True)
def nothing_configured(ai_settings, monkeypatch):
    """No provider anywhere, whatever the machine's .env says."""
    ai_settings(
        ai_provider="",
        ai_base_url="",
        ai_model="",
        ai_api_key="",
        offline_mode=False,
        ollama_base_url="http://ollama.internal:11434",
        mcp_ollama_model="",
        content_policy_llm_review=False,
        content_policy_llm_provider="",
        content_policy_llm_model="",
    )
    monkeypatch.setattr(app_db, "get_ai_preferences", lambda: None)
    monkeypatch.setattr(app_db, "get_agent_api_key", lambda provider: "")


@pytest.fixture()
def preference(monkeypatch):
    def apply(name):
        monkeypatch.setattr(app_db, "get_ai_preferences", lambda: {"default_provider": name})

    return apply


@pytest.fixture()
def private_endpoint(ai_settings):
    """AI_PROVIDER, AI_BASE_URL and AI_MODEL, and nothing else: the on-prem setup."""
    ai_settings(
        ai_provider="openai_compatible",
        ai_base_url="http://vllm.internal:8000/v1",
        ai_model="llama-3.3-70b",
    )


# ── the fallback ──────────────────────────────────────────────────────────


def test_no_preference_uses_the_deployments_endpoint(private_endpoint):
    provider, model = resolve_default_provider(**PURPOSE)

    assert isinstance(provider, OpenAICompatibleProvider)
    assert str(provider.client.base_url).rstrip("/") == "http://vllm.internal:8000/v1"
    assert provider.FAST_MODEL == "llama-3.3-70b"
    assert model == "llama-3.3-70b"


@pytest.mark.parametrize("prefs", [{}, {"default_provider": None}, {"default_provider": ""}, {"default_provider": "  "}])
def test_a_blank_preference_counts_as_no_preference(private_endpoint, monkeypatch, prefs):
    monkeypatch.setattr(app_db, "get_ai_preferences", lambda: prefs)

    assert isinstance(resolve_default_provider(**PURPOSE)[0], OpenAICompatibleProvider)


def test_an_unreadable_preference_store_still_reaches_the_deployment(private_endpoint, monkeypatch):
    def broken():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(app_db, "get_ai_preferences", broken)

    assert isinstance(resolve_default_provider(**PURPOSE)[0], OpenAICompatibleProvider)


def test_the_deployment_key_is_used(private_endpoint, ai_settings):
    ai_settings(ai_api_key="internal-key")

    assert resolve_default_provider(**PURPOSE)[0].client.api_key == "internal-key"


def test_the_team_key_store_still_comes_first(private_endpoint, ai_settings, monkeypatch):
    ai_settings(ai_api_key="internal-key")
    monkeypatch.setattr(app_db, "get_agent_api_key", lambda provider: "team-key")

    assert resolve_default_provider(**PURPOSE)[0].client.api_key == "team-key"


def test_a_callers_model_beats_the_deployments(private_endpoint):
    provider, model = resolve_default_provider(model="mixtral", **PURPOSE)

    assert model == "mixtral"
    assert str(provider.client.base_url).rstrip("/") == "http://vllm.internal:8000/v1"


def test_a_deployment_ollama_uses_ai_base_url_and_ai_model(ai_settings):
    ai_settings(ai_provider="ollama", ai_base_url="http://gpu.internal:11434", ai_model="llama3.3")

    provider, model = resolve_default_provider(**PURPOSE)

    assert isinstance(provider, OllamaProvider)
    assert provider.base_url == "http://gpu.internal:11434"
    assert provider.model == "llama3.3"
    assert model == "llama3.3"


def test_a_deployment_ollama_without_ai_settings_uses_the_ollama_ones(ai_settings):
    ai_settings(ai_provider="ollama", mcp_ollama_model="phi3")

    provider, model = resolve_default_provider(**PURPOSE)

    assert provider.base_url == "http://ollama.internal:11434"
    assert model == "phi3"


# ── what must not change ──────────────────────────────────────────────────


def test_nothing_configured_is_still_nothing():
    """Not anthropic, which is what a request naming no provider falls back to."""
    assert resolve_default_provider(**PURPOSE) == (None, None)


def test_preference_none_keeps_the_model_off(private_endpoint, preference):
    preference("none")

    assert resolve_default_provider(**PURPOSE) == (None, None)


def test_a_deployment_provider_of_none_means_none(ai_settings):
    ai_settings(ai_provider="none")

    assert resolve_default_provider(**PURPOSE) == (None, None)


def test_a_stored_preference_beats_the_deployment(private_endpoint, preference):
    preference("ollama")

    provider, model = resolve_default_provider(**PURPOSE)

    assert isinstance(provider, OllamaProvider)
    assert provider.base_url == "http://ollama.internal:11434"
    assert model == provider.model != "llama-3.3-70b"


def test_a_stored_preference_for_the_same_ollama_resolves_as_it_did(ai_settings, preference):
    """The deployment's URL and model only apply when it was the one that chose."""
    ai_settings(ai_provider="ollama", ai_base_url="http://gpu.internal:11434", ai_model="llama3.3",
                mcp_ollama_model="phi3")
    preference("ollama")

    provider, model = resolve_default_provider(**PURPOSE)

    assert provider.base_url == "http://ollama.internal:11434"
    assert model == "phi3"


def test_an_explicit_provider_beats_both(private_endpoint, preference):
    preference("anthropic")

    assert isinstance(resolve_default_provider(provider="ollama", **PURPOSE)[0], OllamaProvider)


def test_offline_mode_refuses_a_hosted_deployment_provider(ai_settings):
    ai_settings(ai_provider="anthropic", ai_api_key="sk-ant-deployment", offline_mode=True)

    assert resolve_default_provider(**PURPOSE) == (None, None)


def test_a_hosted_deployment_provider_with_no_key_is_unavailable(ai_settings):
    ai_settings(ai_provider="anthropic")

    assert resolve_default_provider(**PURPOSE) == (None, None)


# ── the callers ───────────────────────────────────────────────────────────


def test_custom_file_type_check_reports_the_deployments_model(private_endpoint):
    from api.custom_file_types import _ai_available

    assert _ai_available() is True


def test_custom_file_type_check_with_nothing_configured():
    from api.custom_file_types import _ai_available

    assert _ai_available() is False


@pytest.fixture()
def reviewed(monkeypatch):
    """Answers the reviewer's prompt instead of calling the endpoint, and records the call."""
    calls = []

    def complete(self, prompt, max_tokens, model):
        calls.append({"base_url": str(self.client.base_url).rstrip("/"), "model": model})
        return {"text": '{"categories": ["drug_trade"], "confidence": 0.9, "rationale": "x"}'}

    monkeypatch.setattr(OpenAICompatibleProvider, "complete", complete)
    return calls


def test_content_policy_review_reaches_the_deployments_endpoint(private_endpoint, ai_settings, reviewed):
    ai_settings(content_policy_llm_review=True)

    verdict = content_policy.llm_review({1: "A page to review."})

    assert reviewed == [{"base_url": "http://vllm.internal:8000/v1", "model": "llama-3.3-70b"}]
    assert verdict["categories"] == ["drug_trade"]
    assert verdict["model"] == "llama-3.3-70b"


def test_content_policy_settings_still_win(private_endpoint, ai_settings, reviewed):
    ai_settings(content_policy_llm_review=True, content_policy_llm_model="reviewer-model")

    assert content_policy.llm_review({1: "A page to review."})["model"] == "reviewer-model"


def test_content_policy_review_with_nothing_configured_does_nothing(ai_settings, reviewed):
    ai_settings(content_policy_llm_review=True)

    assert content_policy.llm_review({1: "A page to review."}) is None
    assert reviewed == []


# ── the egress report ─────────────────────────────────────────────────────


def _content_review():
    return next((e for e in readiness.egress_paths() if e["kind"] == "Content review"), None)


def test_egress_lists_review_going_to_the_deployments_endpoint(private_endpoint, ai_settings):
    ai_settings(content_policy_llm_review=True)

    assert _content_review()["destination"] == "openai_compatible"
    assert _content_review()["state"] == "internal"


def test_egress_lists_nothing_when_review_is_off(private_endpoint):
    assert _content_review() is None


def test_egress_lists_nothing_when_review_has_no_provider(ai_settings):
    ai_settings(content_policy_llm_review=True)

    assert _content_review() is None


def test_egress_says_review_to_a_hosted_provider_leaves_the_network(ai_settings):
    ai_settings(content_policy_llm_review=True, ai_provider="anthropic")
    assert _content_review()["state"] == "open"

    ai_settings(offline_mode=True)
    assert _content_review()["state"] == "blocked"


def test_egress_names_the_reviewers_own_provider_first(private_endpoint, ai_settings):
    ai_settings(content_policy_llm_review=True, content_policy_llm_provider="ollama")

    assert _content_review()["destination"] == "ollama"


def test_egress_follows_a_stored_preference_over_the_deployment(private_endpoint, ai_settings, preference):
    """Pages go to the preference, so "internal" would hide them leaving the network."""
    ai_settings(content_policy_llm_review=True)
    preference("anthropic")

    assert _content_review()["destination"] == "anthropic"
    assert _content_review()["state"] == "open"

    ai_settings(offline_mode=True)
    assert _content_review()["state"] == "blocked"


def test_egress_lists_a_stored_preference_with_no_deployment_provider(ai_settings, preference):
    ai_settings(content_policy_llm_review=True)
    preference("anthropic")

    assert _content_review()["destination"] == "anthropic"
    assert _content_review()["state"] == "open"


def test_egress_lists_nothing_when_the_preference_turns_models_off(private_endpoint, ai_settings, preference):
    ai_settings(content_policy_llm_review=True)
    preference("none")

    assert _content_review() is None


def test_egress_lists_nothing_when_the_reviewer_is_set_to_none(private_endpoint, ai_settings):
    ai_settings(content_policy_llm_review=True, content_policy_llm_provider="none")

    assert _content_review() is None


@pytest.mark.parametrize("reviewer, stored, deployment, expected", [
    ("", None, "openai_compatible", OpenAICompatibleProvider),
    ("", "ollama", "openai_compatible", OllamaProvider),
    ("ollama", "anthropic", "openai_compatible", OllamaProvider),
    ("openai_compatible", "ollama", "openai_compatible", OpenAICompatibleProvider),
])
def test_egress_names_the_provider_the_reviewer_really_gets(
        ai_settings, preference, reviewer, stored, deployment, expected):
    ai_settings(content_policy_llm_review=True, content_policy_llm_provider=reviewer, ai_provider=deployment,
                ai_base_url="http://vllm.internal:8000/v1", ai_model="llama-3.3-70b")
    preference(stored)

    built, _ = resolve_default_provider(provider=reviewer, **PURPOSE)

    assert isinstance(built, expected)
    assert _content_review()["destination"] == {OllamaProvider: "ollama",
                                                OpenAICompatibleProvider: "openai_compatible"}[expected]

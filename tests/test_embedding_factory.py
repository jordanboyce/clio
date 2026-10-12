"""create_embedding_service routes to the provider settings call for.

The factory is the single provider-selection point shared by the indexer
manager and the reindex service — these tests pin that routing, the key
resolution chain (explicit → EMBEDDING_API_KEY → legacy Ollama Cloud key →
AI Providers team key), the request shape of the OpenAI-style service, and
the catalog every entry is built from.
"""

import json
from types import SimpleNamespace

import pytest

import services.embedder as embedder
from services.embedding_providers import EMBEDDING_PROVIDERS, PROVIDER_IDS, get_provider


def _settings():
    # Resolved at call time: test_auth.py reloads the config module mid-suite,
    # so a module-level `from config import settings` here would go stale and
    # monkeypatches would land on an object the factory no longer reads.
    import config
    return config.settings


@pytest.fixture()
def stub_services(monkeypatch):
    """Replace all three service classes with recorders — no network, no models."""
    calls = {}

    class FakeLocal:
        def __init__(self, model_name):
            calls["local"] = {"model_name": model_name}

    class FakeOllama:
        def __init__(self, model_name, base_url, api_key=""):
            calls["ollama"] = {"model_name": model_name, "base_url": base_url, "api_key": api_key}

    class FakeCompat:
        def __init__(self, model_name, base_url, api_key="", label="", batch_size=64):
            calls["compat"] = {
                "model_name": model_name, "base_url": base_url,
                "api_key": api_key, "label": label,
            }

    # Both local backends resolve to the same recorder: the routing under
    # test is provider selection, not which ONNX/PyTorch runtime loads it.
    monkeypatch.setattr(embedder, "EmbeddingService", FakeLocal)
    monkeypatch.setattr(embedder, "FastEmbedService", FakeLocal)
    monkeypatch.setattr(embedder, "OllamaEmbeddingService", FakeOllama)
    monkeypatch.setattr(embedder, "OpenAICompatibleEmbeddingService", FakeCompat)
    return calls


@pytest.fixture()
def no_team_keys(monkeypatch):
    from services.app_database import app_db
    monkeypatch.setattr(app_db, "get_agent_api_key", lambda provider: None)


@pytest.fixture()
def clean_embedding_settings(monkeypatch):
    s = _settings()
    monkeypatch.setattr(s, "embedding_api_key", "")
    monkeypatch.setattr(s, "ollama_cloud_api_key", "")
    monkeypatch.setattr(s, "remote_embedding_model", "")
    monkeypatch.setattr(s, "embedding_base_url", "")
    monkeypatch.setattr(s, "offline_mode", False)
    return s


# ── Routing ──────────────────────────────────────────────────────────────────


def test_local_uses_global_model(monkeypatch, stub_services, clean_embedding_settings):
    monkeypatch.setattr(_settings(), "embedding_provider", "local")
    monkeypatch.setattr(_settings(), "embedding_model", "all-MiniLM-L6-v2")
    embedder.create_embedding_service()
    assert stub_services["local"] == {"model_name": "all-MiniLM-L6-v2"}


def test_local_collection_override_wins(monkeypatch, stub_services, clean_embedding_settings):
    monkeypatch.setattr(_settings(), "embedding_provider", "local")
    monkeypatch.setattr(_settings(), "embedding_model", "all-MiniLM-L6-v2")
    embedder.create_embedding_service(collection_model="BAAI/bge-base-en-v1.5")
    assert stub_services["local"] == {"model_name": "BAAI/bge-base-en-v1.5"}


def test_ollama_provider_uses_base_url_no_key(monkeypatch, stub_services, clean_embedding_settings):
    monkeypatch.setattr(_settings(), "embedding_provider", "ollama")
    monkeypatch.setattr(_settings(), "ollama_base_url", "http://box:11434")
    monkeypatch.setattr(_settings(), "remote_embedding_model", "mxbai-embed-large")
    embedder.create_embedding_service()
    assert stub_services["ollama"] == {
        "model_name": "mxbai-embed-large",
        "base_url": "http://box:11434",
        "api_key": "",
    }


def test_ollama_blank_model_falls_back_to_catalog_default(monkeypatch, stub_services, clean_embedding_settings):
    monkeypatch.setattr(_settings(), "embedding_provider", "ollama")
    embedder.create_embedding_service()
    assert stub_services["ollama"]["model_name"] == get_provider("ollama")["default_model"]


def test_ollama_cloud_uses_legacy_env_key(monkeypatch, stub_services, clean_embedding_settings, no_team_keys):
    monkeypatch.setattr(_settings(), "embedding_provider", "ollama_cloud")
    monkeypatch.setattr(_settings(), "ollama_cloud_api_key", "env-key")
    monkeypatch.setattr(_settings(), "remote_embedding_model", "nomic-embed-text")
    embedder.create_embedding_service()
    assert stub_services["ollama"] == {
        "model_name": "nomic-embed-text",
        "base_url": "https://ollama.com",
        "api_key": "env-key",
    }


def test_ollama_cloud_falls_back_to_team_key(monkeypatch, stub_services, clean_embedding_settings):
    from services.app_database import app_db

    monkeypatch.setattr(_settings(), "embedding_provider", "ollama_cloud")
    monkeypatch.setattr(
        app_db, "get_agent_api_key", lambda provider: "team-key" if provider == "ollama_cloud" else None
    )
    embedder.create_embedding_service()
    assert stub_services["ollama"]["api_key"] == "team-key"


def test_cloud_provider_without_any_key_fails_closed(monkeypatch, stub_services, clean_embedding_settings, no_team_keys):
    monkeypatch.setattr(_settings(), "embedding_provider", "google")
    with pytest.raises(RuntimeError, match="Google Gemini embeddings need an API key"):
        embedder.create_embedding_service()


def test_google_reuses_provider_card_key(monkeypatch, stub_services, clean_embedding_settings):
    from services.app_database import app_db

    monkeypatch.setattr(_settings(), "embedding_provider", "google")
    monkeypatch.setattr(
        app_db, "get_agent_api_key", lambda provider: "AIza-team" if provider == "google" else None
    )
    embedder.create_embedding_service()
    assert stub_services["compat"] == {
        "model_name": "gemini-embedding-001",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key": "AIza-team",
        "label": "Google Gemini",
    }


def test_openrouter_reuses_provider_card_key_and_free_default(monkeypatch, stub_services, clean_embedding_settings):
    from services.app_database import app_db

    monkeypatch.setattr(_settings(), "embedding_provider", "openrouter")
    monkeypatch.setattr(
        app_db, "get_agent_api_key", lambda provider: "sk-or-team" if provider == "openrouter" else None
    )
    embedder.create_embedding_service()
    assert stub_services["compat"] == {
        "model_name": "nvidia/nemotron-3-embed-1b:free",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "sk-or-team",
        "label": "OpenRouter",
    }


def test_openrouter_without_key_points_at_the_provider_card(monkeypatch, stub_services, clean_embedding_settings, no_team_keys):
    monkeypatch.setattr(_settings(), "embedding_provider", "openrouter")
    with pytest.raises(RuntimeError, match="add the OpenRouter key under Settings"):
        embedder.create_embedding_service()


def test_xai_is_hidden_and_fails_closed_without_a_model(monkeypatch, clean_embedding_settings):
    """xAI documents an embeddings route but serves no model on it (2026-09).
    The entry stays hidden; choosing it by env must fail with a readable
    reason before any network call, not with a vendor stack trace."""
    from services.app_database import app_db

    assert get_provider("xai")["hidden"] is True
    monkeypatch.setattr(_settings(), "embedding_provider", "xai")
    monkeypatch.setattr(
        app_db, "get_agent_api_key", lambda provider: "xai-team" if provider == "grok" else None
    )
    with pytest.raises(RuntimeError, match=r"xAI \(Grok\) needs a model name"):
        embedder.create_embedding_service()


def test_xai_with_explicit_model_probes_the_real_endpoint(monkeypatch, stub_services, clean_embedding_settings):
    from services.app_database import app_db

    monkeypatch.setattr(_settings(), "embedding_provider", "xai")
    monkeypatch.setattr(_settings(), "remote_embedding_model", "v1")
    monkeypatch.setattr(
        app_db, "get_agent_api_key", lambda provider: "xai-team" if provider == "grok" else None
    )
    embedder.create_embedding_service()
    assert stub_services["compat"] == {
        "model_name": "v1",
        "base_url": "https://api.x.ai/v1",
        "api_key": "xai-team",
        "label": "xAI (Grok)",
    }


def test_embedding_api_key_beats_provider_card(monkeypatch, stub_services, clean_embedding_settings):
    from services.app_database import app_db

    monkeypatch.setattr(_settings(), "embedding_provider", "openai")
    monkeypatch.setattr(_settings(), "embedding_api_key", "sk-embed")
    monkeypatch.setattr(_settings(), "remote_embedding_model", "text-embedding-3-large")
    monkeypatch.setattr(app_db, "get_agent_api_key", lambda provider: "sk-chat")
    embedder.create_embedding_service()
    assert stub_services["compat"]["api_key"] == "sk-embed"
    assert stub_services["compat"]["model_name"] == "text-embedding-3-large"


def test_mistral_needs_its_own_key(monkeypatch, stub_services, clean_embedding_settings, no_team_keys):
    monkeypatch.setattr(_settings(), "embedding_provider", "mistral")
    with pytest.raises(RuntimeError, match="enter one in the Embedding section"):
        embedder.create_embedding_service()
    monkeypatch.setattr(_settings(), "embedding_api_key", "mst-1")
    embedder.create_embedding_service()
    assert stub_services["compat"]["base_url"] == "https://api.mistral.ai/v1"
    assert stub_services["compat"]["model_name"] == "mistral-embed"


def test_custom_endpoint_uses_configured_base_url_and_optional_key(monkeypatch, stub_services, clean_embedding_settings, no_team_keys):
    monkeypatch.setattr(_settings(), "embedding_provider", "openai_compatible")
    monkeypatch.setattr(_settings(), "embedding_base_url", "http://lmstudio:1234/v1")
    monkeypatch.setattr(_settings(), "remote_embedding_model", "nomic-embed-text-v1.5")
    embedder.create_embedding_service()
    assert stub_services["compat"] == {
        "model_name": "nomic-embed-text-v1.5",
        "base_url": "http://lmstudio:1234/v1",
        "api_key": "",
        "label": "Custom endpoint",
    }


def test_overrides_build_unsaved_configuration(monkeypatch, stub_services, clean_embedding_settings, no_team_keys):
    """The Settings 'Test connection' probe passes values before saving."""
    monkeypatch.setattr(_settings(), "embedding_provider", "local")
    embedder.create_embedding_service(overrides={
        "embedding_provider": "voyage",
        "remote_embedding_model": "voyage-3.5",
        "embedding_api_key": "pa-1",
    })
    assert "local" not in stub_services
    assert stub_services["compat"]["model_name"] == "voyage-3.5"
    assert stub_services["compat"]["api_key"] == "pa-1"
    assert stub_services["compat"]["base_url"] == "https://api.voyageai.com/v1"


def test_offline_mode_refuses_cloud_embeddings(monkeypatch, stub_services, clean_embedding_settings):
    monkeypatch.setattr(_settings(), "offline_mode", True)
    monkeypatch.setattr(_settings(), "embedding_provider", "google")
    monkeypatch.setattr(_settings(), "embedding_api_key", "AIza")
    with pytest.raises(RuntimeError, match="OFFLINE_MODE"):
        embedder.create_embedding_service()
    # Operator-controlled hosts stay allowed.
    monkeypatch.setattr(_settings(), "embedding_provider", "ollama")
    embedder.create_embedding_service()
    assert "ollama" in stub_services


def test_unknown_provider_is_rejected(monkeypatch, stub_services, clean_embedding_settings):
    monkeypatch.setattr(_settings(), "embedding_provider", "carrier-pigeon")
    with pytest.raises(RuntimeError, match="Unknown embedding provider"):
        embedder.create_embedding_service()


def test_signature_changes_with_anything_that_changes_vectors(monkeypatch, clean_embedding_settings):
    monkeypatch.setattr(_settings(), "embedding_provider", "google")
    a = embedder.embedding_signature()
    # Overrides describe the same triple the saved settings would.
    monkeypatch.setattr(_settings(), "embedding_provider", "local")
    assert embedder.embedding_signature(overrides={"embedding_provider": "google"}) == a
    c = embedder.embedding_signature()
    monkeypatch.setattr(_settings(), "embedding_provider", "google")
    monkeypatch.setattr(_settings(), "remote_embedding_model", "other-model")
    b = embedder.embedding_signature()
    assert len({a, b, c}) == 3


# ── Catalog ──────────────────────────────────────────────────────────────────


def test_catalog_entries_are_complete():
    required = {"id", "label", "kind", "cost", "cost_label", "privacy", "blurb",
                "needs_key", "key_provider", "base_url", "default_model", "models"}
    for p in EMBEDDING_PROVIDERS:
        missing = required - set(p)
        assert not missing, f"{p['id']} missing {missing}"
        assert p["kind"] in ("local", "ollama", "openai_compatible")
        if p["kind"] == "openai_compatible" and p["id"] != "openai_compatible":
            assert p["base_url"], f"{p['id']} needs a fixed base_url"
        if p["models"]:
            assert p["default_model"] in {m["id"] for m in p["models"]}
    assert len(set(PROVIDER_IDS)) == len(PROVIDER_IDS)


def test_settings_rejects_unknown_provider_at_startup(monkeypatch):
    import config
    monkeypatch.setenv("EMBEDDING_PROVIDER", "nope")
    with pytest.raises(ValueError, match="EMBEDDING_PROVIDER"):
        config.Settings()


def test_legacy_ollama_embedding_model_env_still_read(monkeypatch):
    import config
    monkeypatch.delenv("REMOTE_EMBEDDING_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "mxbai-embed-large")
    assert config.Settings().remote_embedding_model == "mxbai-embed-large"


# ── Transport ────────────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_ollama_cloud_call_sends_bearer_header(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        return _Resp({"embeddings": [[0.1, 0.2]]})

    monkeypatch.setattr(embedder.urllib.request, "urlopen", fake_urlopen)
    svc = embedder.OllamaEmbeddingService(
        model_name="nomic-embed-text", base_url="https://ollama.com", api_key="sk-123"
    )
    assert captured["url"] == "https://ollama.com/api/embed"
    assert captured["auth"] == "Bearer sk-123"
    assert svc.embedding_dim == 2


def test_local_daemon_call_sends_no_auth_header(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["auth"] = req.get_header("Authorization")
        return _Resp({"embeddings": [[0.1, 0.2, 0.3]]})

    monkeypatch.setattr(embedder.urllib.request, "urlopen", fake_urlopen)
    embedder.OllamaEmbeddingService(model_name="nomic-embed-text")
    assert captured["auth"] is None


def test_openai_compatible_request_and_response_shape(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = json.loads(req.data.decode())
        n = len(captured["body"]["input"])
        # Return rows out of order to prove `index` is honoured.
        data = [{"index": i, "embedding": [float(i), 1.0]} for i in reversed(range(n))]
        return _Resp({"object": "list", "data": data})

    monkeypatch.setattr(embedder.urllib.request, "urlopen", fake_urlopen)
    svc = embedder.OpenAICompatibleEmbeddingService(
        model_name="text-embedding-3-small",
        base_url="https://api.openai.com/v1/",
        api_key="sk-abc",
        label="OpenAI",
    )
    assert captured["url"] == "https://api.openai.com/v1/embeddings"
    assert captured["auth"] == "Bearer sk-abc"
    assert captured["body"] == {"model": "text-embedding-3-small", "input": ["dimension probe"]}
    assert svc.embedding_dim == 2

    vecs = svc.embed_texts(["a", "b", "c"])
    assert vecs.shape == (3, 2)
    assert [row[0] for row in vecs.tolist()] == [0.0, 1.0, 2.0]

    q = svc.embed_query("hello")
    assert q.shape == (2,)


def test_openai_compatible_batches_large_inputs(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode())
        calls.append(len(body["input"]))
        data = [{"index": i, "embedding": [0.5]} for i in range(len(body["input"]))]
        return _Resp({"data": data})

    monkeypatch.setattr(embedder.urllib.request, "urlopen", fake_urlopen)
    svc = embedder.OpenAICompatibleEmbeddingService(
        model_name="m", base_url="http://x/v1", batch_size=10
    )
    progress = []
    svc.embed_texts([str(i) for i in range(25)], progress_callback=lambda d, t: progress.append((d, t)))
    assert calls == [1, 10, 10, 5]
    assert progress == [(10, 25), (20, 25), (25, 25)]


def test_openai_compatible_explains_auth_failure(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(embedder.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="Google Gemini rejected the API key"):
        embedder.OpenAICompatibleEmbeddingService(
            model_name="gemini-embedding-001",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            api_key="bad",
            label="Google Gemini",
        )


def test_openai_compatible_explains_missing_credit(monkeypatch):
    import io
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 402, "Payment Required", {},
            io.BytesIO(b'{"error":{"message":"Insufficient credits"}}'),
        )

    monkeypatch.setattr(embedder.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="OpenRouter says this account has no credit.*Insufficient credits"):
        embedder.OpenAICompatibleEmbeddingService(
            model_name="qwen/qwen3-embedding-8b",
            base_url="https://openrouter.ai/api/v1",
            api_key="sk-or-1",
            label="OpenRouter",
        )


def test_openai_compatible_requires_base_url():
    with pytest.raises(RuntimeError, match="needs a base URL"):
        embedder.OpenAICompatibleEmbeddingService(model_name="m", base_url="", label="Custom endpoint")

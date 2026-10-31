"""The embedding picker's HTTP surface.

GET /api/embedding/providers feeds the Settings radio list straight from the
catalog and reports whether a key is already usable; POST /api/embedding/test
probes an unsaved configuration; POST /api/config only claims a re-index is
needed when an embedding value actually changed. Nothing here loads a model
or touches the network — the factory is stubbed.
"""

import pytest
from fastapi.testclient import TestClient

import services.embedder as embedder
from services.app_database import SQLiteBackend, app_db
from services.config_manager import MASKED_SECRET, config_manager


@pytest.fixture()
def fresh_db(tmp_path):
    old_db_path = app_db.db_path
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    yield app_db
    app_db.db_path = old_db_path


@pytest.fixture()
def client(fresh_db, monkeypatch):
    import main
    from api import system

    monkeypatch.setattr(system.settings, "embedding_provider", "local")
    monkeypatch.setattr(system.settings, "embedding_api_key", "")
    monkeypatch.setattr(system.settings, "ollama_cloud_api_key", "")
    monkeypatch.setattr(system.settings, "offline_mode", False)
    # Settings writes normally mirror into the repo's .env — keep the test out of it.
    monkeypatch.setattr(config_manager, "env_file", fresh_db.db_path.parent / ".env")
    return TestClient(main.app)


def test_catalog_hides_unavailable_provider_and_reports_keys(client, fresh_db):
    fresh_db.set_agent_api_key("google", "AIza-team")
    fresh_db.set_agent_api_key("openrouter", "sk-or-team")

    body = client.get("/api/embedding/providers").json()
    ids = [p["id"] for p in body["providers"]]

    assert ids[0] == "local"
    assert "ollama_cloud" not in ids  # hidden: ollama.com serves no embedding models
    assert "xai" not in ids           # hidden: xAI lists no embedding model
    assert {"google", "mistral", "voyage", "jina", "openrouter", "openai", "ollama", "openai_compatible"} <= set(ids)

    by_id = {p["id"]: p for p in body["providers"]}
    assert by_id["google"]["key_configured"] is True
    assert by_id["google"]["key_source"] == "provider_card"
    assert by_id["openrouter"]["key_source"] == "provider_card"
    assert by_id["openrouter"]["default_model"].endswith(":free")
    assert by_id["mistral"]["key_configured"] is False
    assert by_id["local"]["needs_key"] is False
    assert body["current"]["provider"] == "local"
    # No secret leaks through the catalog.
    assert "AIza-team" not in str(body)
    assert "sk-or-team" not in str(body)


def test_catalog_keeps_hidden_provider_when_it_is_the_current_choice(client, monkeypatch):
    from api import system
    monkeypatch.setattr(system.settings, "embedding_provider", "ollama_cloud")
    ids = [p["id"] for p in client.get("/api/embedding/providers").json()["providers"]]
    assert "ollama_cloud" in ids


def test_test_endpoint_probes_unsaved_values(client, monkeypatch):
    seen = {}

    class FakeService:
        model_name = "mistral-embed"
        embedding_dim = 1024

    def fake_factory(collection_model=None, overrides=None):
        seen.update(overrides or {})
        return FakeService()

    monkeypatch.setattr(embedder, "create_embedding_service", fake_factory)

    resp = client.post("/api/embedding/test", json={
        "embedding_provider": "mistral",
        "remote_embedding_model": "mistral-embed",
        "embedding_api_key": "mst-1",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["dimensions"] == 1024
    assert body["model"] == "mistral-embed"
    assert seen["embedding_provider"] == "mistral"
    assert seen["embedding_api_key"] == "mst-1"


def test_test_endpoint_treats_mask_as_use_saved_key(client, monkeypatch):
    seen = {}

    def fake_factory(collection_model=None, overrides=None):
        seen.update(overrides or {})
        raise RuntimeError("Google Gemini rejected the API key (HTTP 401). Check the key and try again.")

    monkeypatch.setattr(embedder, "create_embedding_service", fake_factory)

    body = client.post("/api/embedding/test", json={
        "embedding_provider": "google",
        "embedding_api_key": MASKED_SECRET,
    }).json()
    assert "embedding_api_key" not in seen
    assert body["ok"] is False
    assert "rejected the API key" in body["error"]


def test_saving_unchanged_embedding_settings_needs_no_reindex(client):
    current = client.get("/api/config").json()
    payload = {
        "embedding_provider": current["embedding_provider"],
        "embedding_model": current["embedding_model"],
        "remote_embedding_model": current["remote_embedding_model"],
        "embedding_base_url": current["embedding_base_url"],
    }
    first = client.post("/api/config", json=payload).json()
    assert first["success"] is True
    assert first["requires_reindex"] is False
    assert first["requires_restart"] is False

    changed = client.post("/api/config", json={**payload, "embedding_provider": "google"}).json()
    assert changed["requires_reindex"] is True
    assert changed["requires_restart"] is False
    assert client.get("/api/config").json()["embedding_provider"] == "google"


def test_saving_unknown_provider_is_refused(client):
    resp = client.post("/api/config", json={"embedding_provider": "carrier-pigeon"})
    assert resp.status_code == 422
    assert "Unknown embedding provider" in resp.json()["detail"]


def test_legacy_ollama_model_key_maps_to_remote_model(client, fresh_db):
    fresh_db.set_config("ollama_embedding_model", "mxbai-embed-large")
    assert client.get("/api/config").json()["remote_embedding_model"] == "mxbai-embed-large"

    body = client.post("/api/config", json={"ollama_embedding_model": "bge-m3"}).json()
    assert body["success"] is True
    assert "remote_embedding_model" in body["updated_fields"]
    assert client.get("/api/config").json()["remote_embedding_model"] == "bge-m3"


def test_embedding_key_is_masked_and_round_trips(client):
    client.post("/api/config", json={"embedding_api_key": "pa-secret"})
    cfg = client.get("/api/config").json()
    assert cfg["embedding_api_key"] == MASKED_SECRET
    assert cfg["embedding_api_key_set"] is True
    assert "pa-secret" not in str(cfg)

    # Echoing the mask back leaves the stored key alone; blank clears it.
    client.post("/api/config", json={"embedding_api_key": MASKED_SECRET})
    assert client.get("/api/config").json()["embedding_api_key_set"] is True
    client.post("/api/config", json={"embedding_api_key": ""})
    assert client.get("/api/config").json()["embedding_api_key_set"] is False

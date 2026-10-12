"""The embedding runtime: backend selection, cache probing, the warm-up
status machine, and the first-run endpoints.

Nothing here touches the network or loads a real model: importability is
monkeypatched, the cache is a temp directory, the service factory is a
stub, and the HuggingFace / Ollama probes are replaced.
"""

import json
import threading
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

import services.embedder as embedder
import services.embedding_status as status_mod
from services.app_database import SQLiteBackend, app_db
from services.config_manager import config_manager
from services.embedding_status import embedding_status


# ── helpers ────────────────────────────────────────────────────────────────


class FakeService:
    backend = "fastembed"

    def __init__(self, model_name="all-MiniLM-L6-v2", dim=4):
        self.model_name = model_name
        self.embedding_dim = dim

    def embed_texts(self, texts, progress_callback=None):
        return np.zeros((len(texts), self.embedding_dim), dtype=np.float32)

    def embed_query(self, query):
        return np.zeros(self.embedding_dim, dtype=np.float32)


def set_setting(monkeypatch, name, value):
    """Patch a setting on every Settings object in play.

    test_auth.py reloads the config module mid-suite, after which
    ``config.settings`` and the ``settings`` api.system imported at startup
    are different objects; code reads one or the other, so patch both.
    """
    import config
    from api import system

    for obj in {id(config.settings): config.settings, id(system.settings): system.settings}.values():
        monkeypatch.setattr(obj, name, value)


def wait_for(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def wait_settled(timeout=5.0):
    """Until the warm-up thread has finished."""
    return wait_for(lambda: not embedding_status.is_running(), timeout)


def fake_fastembed_cache(root, model_file="model.onnx", complete=True):
    """Lay out what fastembed leaves behind for Qdrant/all-MiniLM-L6-v2-onnx."""
    repo = root / "models--Qdrant--all-MiniLM-L6-v2-onnx"
    snap = repo / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    (snap / model_file).write_bytes(b"onnx" * 10)
    (snap / "config.json").write_text("{}")
    if complete:
        (repo / "files_metadata.json").write_text(json.dumps({"snapshots/abc123/model.onnx": {"size": 40}}))
    return repo


FAKE_CATALOG = {
    "sentence-transformers/all-MiniLM-L6-v2": {
        "model": "sentence-transformers/all-MiniLM-L6-v2",
        "dim": 384,
        "size_in_GB": 0.09,
        "model_file": "model.onnx",
        "sources": {"hf": "Qdrant/all-MiniLM-L6-v2-onnx", "url": None},
    },
    "BAAI/bge-small-en-v1.5": {
        "model": "BAAI/bge-small-en-v1.5",
        "dim": 384,
        "size_in_GB": 0.067,
        "model_file": "model_optimized.onnx",
        "sources": {"hf": "Qdrant/bge-small-en-v1.5-onnx-Q", "url": None},
    },
}


@pytest.fixture(autouse=True)
def quiet_status():
    embedding_status.reset()
    yield
    wait_settled()
    embedding_status.reset()


@pytest.fixture()
def fake_catalog(monkeypatch):
    monkeypatch.setattr(embedder, "_fastembed_catalog", lambda: FAKE_CATALOG)


@pytest.fixture()
def both_backends(monkeypatch, fake_catalog):
    monkeypatch.setattr(embedder, "fastembed_importable", lambda: True)
    monkeypatch.setattr(embedder, "sentence_transformers_importable", lambda: True)


# ── backend selection and name mapping ─────────────────────────────────────


def test_catalog_names_map_to_fastembed_ids(fake_catalog, monkeypatch):
    monkeypatch.setattr(embedder, "fastembed_importable", lambda: True)
    assert embedder.fastembed_model_id("all-MiniLM-L6-v2") == "sentence-transformers/all-MiniLM-L6-v2"
    assert embedder.fastembed_model_id("BAAI/bge-small-en-v1.5") == "BAAI/bge-small-en-v1.5"
    # Listed as a candidate, but this fastembed has no export of it: dropped.
    assert embedder.fastembed_model_id("all-MiniLM-L12-v2") is None
    assert embedder.fastembed_model_id("all-mpnet-base-v2") is None
    assert embedder.fastembed_model_id("") is None


def test_real_fastembed_catalog_serves_the_default_model():
    """The installed fastembed must know the default model, or the whole
    'no torch needed' story is false."""
    pytest.importorskip("fastembed")
    embedder._fastembed_catalog.cache_clear()
    try:
        assert embedder.fastembed_model_id("all-MiniLM-L6-v2") == "sentence-transformers/all-MiniLM-L6-v2"
        assert embedder.fastembed_supports("BAAI/bge-base-en-v1.5")
    finally:
        embedder._fastembed_catalog.cache_clear()


def test_auto_prefers_fastembed_for_supported_models(both_backends, monkeypatch):
    from config import settings
    set_setting(monkeypatch, "local_embedding_backend", "auto")
    assert embedder.resolve_local_backend("all-MiniLM-L6-v2") == "fastembed"
    # Not exported by fastembed → the torch backend when installed.
    assert embedder.resolve_local_backend("all-mpnet-base-v2") == "sentence-transformers"


def test_auto_falls_back_to_torch_when_fastembed_is_absent(fake_catalog, monkeypatch):
    monkeypatch.setattr(embedder, "fastembed_importable", lambda: False)
    monkeypatch.setattr(embedder, "sentence_transformers_importable", lambda: True)
    assert embedder.resolve_local_backend("all-MiniLM-L6-v2") == "sentence-transformers"


def test_no_backend_installed_names_both_install_commands(fake_catalog, monkeypatch):
    monkeypatch.setattr(embedder, "fastembed_importable", lambda: False)
    monkeypatch.setattr(embedder, "sentence_transformers_importable", lambda: False)
    with pytest.raises(RuntimeError) as exc:
        embedder.resolve_local_backend("all-MiniLM-L6-v2")
    assert "pip install fastembed" in str(exc.value)
    assert "pip install -r requirements-torch.txt" in str(exc.value)


def test_fastembed_only_install_with_unsupported_model_points_at_torch(fake_catalog, monkeypatch):
    monkeypatch.setattr(embedder, "fastembed_importable", lambda: True)
    monkeypatch.setattr(embedder, "sentence_transformers_importable", lambda: False)
    with pytest.raises(RuntimeError) as exc:
        embedder.resolve_local_backend("all-mpnet-base-v2")
    assert "requirements-torch.txt" in str(exc.value)


def test_explicit_backend_setting_is_honoured_or_refused(both_backends):
    assert embedder.resolve_local_backend("all-MiniLM-L6-v2", "sentence-transformers") == "sentence-transformers"
    assert embedder.resolve_local_backend("all-MiniLM-L6-v2", "fastembed") == "fastembed"
    with pytest.raises(RuntimeError):
        embedder.resolve_local_backend("all-mpnet-base-v2", "fastembed")
    with pytest.raises(RuntimeError):
        embedder.resolve_local_backend("all-MiniLM-L6-v2", "carrier-pigeon")


def test_signature_does_not_depend_on_backend(both_backends, monkeypatch):
    from config import settings
    set_setting(monkeypatch, "embedding_provider", "local")
    set_setting(monkeypatch, "embedding_model", "all-MiniLM-L6-v2")
    set_setting(monkeypatch, "local_embedding_backend", "fastembed")
    a = embedder.embedding_signature()
    set_setting(monkeypatch, "local_embedding_backend", "sentence-transformers")
    b = embedder.embedding_signature()
    assert a == b == "local::all-MiniLM-L6-v2"


def test_factory_builds_the_backend_it_resolved(both_backends, monkeypatch):
    built = {}
    monkeypatch.setattr(embedder, "FastEmbedService", lambda model_name: built.setdefault("fe", FakeService(model_name)))
    monkeypatch.setattr(embedder, "EmbeddingService", lambda model_name: built.setdefault("st", FakeService(model_name)))
    svc = embedder.create_embedding_service(overrides={"embedding_provider": "local", "embedding_model": "all-MiniLM-L6-v2"})
    assert svc is built["fe"]
    svc = embedder.create_embedding_service(overrides={"embedding_provider": "local", "embedding_model": "all-mpnet-base-v2"})
    assert svc is built["st"]
    assert svc.signature == "local::all-mpnet-base-v2"


def test_retrieval_prompts_are_shared_by_model_family():
    assert embedder.retrieval_prompts("all-MiniLM-L6-v2") == (None, None)
    assert embedder.retrieval_prompts("BAAI/bge-small-en-v1.5")[0].startswith("Represent this sentence")
    assert embedder.retrieval_prompts("nomic-ai/nomic-embed-text-v1.5") == ("search_query: ", "search_document: ")
    assert embedder.retrieval_prompts("intfloat/multilingual-e5-small") == ("query: ", "passage: ")


def test_describe_embedding_reports_backend():
    info = embedder.describe_embedding(FakeService())
    assert info["backend"] == "fastembed"
    assert info["model"] == "all-MiniLM-L6-v2"


def test_local_catalog_lists_backends_and_new_models():
    from services.embedding_providers import local_model_catalog, local_model_entry
    by_name = {m["name"]: m for m in local_model_catalog()}
    assert by_name["all-MiniLM-L6-v2"]["backends"] == ["fastembed", "sentence-transformers"]
    assert by_name["all-mpnet-base-v2"]["backends"] == ["sentence-transformers"]
    for new in ("BAAI/bge-small-en-v1.5", "nomic-ai/nomic-embed-text-v1.5",
                "jinaai/jina-embeddings-v2-base-code", "intfloat/multilingual-e5-small"):
        assert new in by_name
    assert local_model_entry("BAAI/bge-small-en-v1.5")["size_mb"] == 130
    assert local_model_entry("nomic-ai/nomic-embed-text-v1.5")["dimensions"] == 768


# ── cache probing ──────────────────────────────────────────────────────────


def test_fastembed_cache_probe_needs_the_completion_marker(tmp_path, both_backends, monkeypatch):
    monkeypatch.setattr(embedder, "fastembed_cache_dirs", lambda: [tmp_path / "image", tmp_path / "data"])
    assert embedder.local_model_cached("all-MiniLM-L6-v2", "fastembed") is False

    # A download in flight: files present, no files_metadata.json yet.
    fake_fastembed_cache(tmp_path / "data", complete=False)
    assert embedder.local_model_cached("all-MiniLM-L6-v2", "fastembed") is False

    (tmp_path / "data" / "models--Qdrant--all-MiniLM-L6-v2-onnx" / "files_metadata.json").write_text("{}")
    assert embedder.local_model_cached("all-MiniLM-L6-v2", "fastembed") is True
    assert embedder.fastembed_cached_in("all-MiniLM-L6-v2") == tmp_path / "data"
    # The backend the settings resolve to is what the bare call answers for.
    assert embedder.local_model_cached("all-MiniLM-L6-v2") is True


def test_fastembed_image_cache_is_searched_first(tmp_path, both_backends, monkeypatch):
    monkeypatch.setattr(embedder, "fastembed_cache_dirs", lambda: [tmp_path / "image", tmp_path / "data"])
    fake_fastembed_cache(tmp_path / "image")
    fake_fastembed_cache(tmp_path / "data")
    assert embedder.fastembed_cached_in("all-MiniLM-L6-v2") == tmp_path / "image"


def test_legacy_fastembed_folder_counts_as_cached(tmp_path, both_backends, monkeypatch):
    monkeypatch.setattr(embedder, "fastembed_cache_dirs", lambda: [tmp_path])
    legacy = tmp_path / "fast-all-MiniLM-L6-v2"
    legacy.mkdir()
    (legacy / "model.onnx").write_bytes(b"x")
    assert embedder.local_model_cached("all-MiniLM-L6-v2", "fastembed") is True


def test_torch_backend_probe_uses_hf_cache(both_backends, monkeypatch):
    monkeypatch.setattr(embedder, "hf_model_cached", lambda name: name == "all-mpnet-base-v2")
    assert embedder.local_model_cached("all-mpnet-base-v2", "sentence-transformers") is True
    assert embedder.local_model_cached("all-MiniLM-L6-v2", "sentence-transformers") is False


def test_nothing_is_cached_when_no_backend_is_installed(fake_catalog, monkeypatch):
    monkeypatch.setattr(embedder, "fastembed_importable", lambda: False)
    monkeypatch.setattr(embedder, "sentence_transformers_importable", lambda: False)
    assert embedder.local_model_cached("all-MiniLM-L6-v2") is False


def test_download_dir_and_size_follow_the_backend(tmp_path, both_backends, monkeypatch):
    monkeypatch.setattr(embedder, "fastembed_data_cache_dir", lambda: tmp_path)
    assert embedder.local_model_download_dir("all-MiniLM-L6-v2", "fastembed") == tmp_path / "models--Qdrant--all-MiniLM-L6-v2-onnx"
    assert embedder.local_model_download_bytes("all-MiniLM-L6-v2", "fastembed") == 90_000_000
    # The torch backend's estimate is the curated catalog size.
    assert embedder.local_model_download_bytes("all-mpnet-base-v2", "sentence-transformers") == 420_000_000
    assert embedder.local_model_download_bytes("some/unknown-model", "sentence-transformers") is None


# ── the status machine ─────────────────────────────────────────────────────


@pytest.fixture()
def local_settings(monkeypatch, both_backends):
    from config import settings
    set_setting(monkeypatch, "embedding_provider", "local")
    set_setting(monkeypatch, "embedding_model", "all-MiniLM-L6-v2")
    set_setting(monkeypatch, "offline_mode", False)
    set_setting(monkeypatch, "local_embedding_backend", "auto")
    monkeypatch.setattr(embedder, "hub_offline", lambda: False)
    return settings


def test_offline_and_uncached_is_missing(local_settings, monkeypatch):
    monkeypatch.setattr(embedder, "hub_offline", lambda: True)
    monkeypatch.setattr(embedder, "local_model_cached", lambda name, backend=None: False)

    def never(*a):
        raise AssertionError("must not try to build the service")

    embedding_status.warm(force=True, service_factory=never)
    assert wait_settled()
    st = embedding_status.status()
    assert st["status"] == "missing"
    assert st["can_download"] is False
    assert st["backend"] == "fastembed"
    assert st["provider"] == "local"
    assert st["model"] == "all-MiniLM-L6-v2"
    assert st["error"] is None
    assert st["finished_at"] is not None


def test_cached_model_goes_loading_then_ready(local_settings, monkeypatch):
    monkeypatch.setattr(embedder, "local_model_cached", lambda name, backend=None: True)
    gate = threading.Event()
    seen = {}

    def factory(provider, model):
        seen["args"] = (provider, model)
        gate.wait(2)
        return FakeService(model)

    embedding_status.warm(force=True, service_factory=factory)
    assert embedding_status.status()["status"] == "loading"
    assert embedding_status.status()["progress"] is None
    gate.set()
    assert wait_settled()
    st = embedding_status.status()
    assert st["status"] == "ready"
    assert st["backend"] == "fastembed"
    assert st["finished_at"] is not None
    assert seen["args"] == ("local", "all-MiniLM-L6-v2")


def test_uncached_model_reports_download_progress_then_ready(local_settings, monkeypatch, tmp_path):
    target = tmp_path / "models--Qdrant--all-MiniLM-L6-v2-onnx"
    monkeypatch.setattr(embedder, "local_model_cached", lambda name, backend=None: False)
    monkeypatch.setattr(embedder, "local_model_download_dir", lambda name, backend=None: target)
    monkeypatch.setattr(embedder, "local_model_download_bytes", lambda name, backend=None: 1000)
    monkeypatch.setattr(status_mod, "WATCH_INTERVAL_SECONDS", 0.02)
    release = threading.Event()

    def factory(provider, model):
        # Pretend to be the hub client: the cache directory grows past the
        # estimate while the "download" runs.
        (target / "blobs").mkdir(parents=True)
        (target / "blobs" / "abc.incomplete").write_bytes(b"x" * 600)
        wait_for(lambda: (embedding_status.status()["progress"] or {}).get("downloaded_bytes", 0) >= 600)
        (target / "blobs" / "abc.incomplete").write_bytes(b"x" * 1500)
        wait_for(lambda: (embedding_status.status()["progress"] or {}).get("downloaded_bytes", 0) >= 1500)
        release.wait(2)
        return FakeService(model)

    embedding_status.warm(force=True, service_factory=factory)
    assert wait_for(lambda: (embedding_status.status()["progress"] or {}).get("downloaded_bytes", 0) >= 1500)
    st = embedding_status.status()
    assert st["status"] == "downloading"
    assert st["progress"]["total_bytes"] == 1000
    assert st["progress"]["downloaded_bytes"] >= 1500
    assert st["progress"]["percent"] == 99  # capped until the load completes
    assert st["progress"]["file"] == str(target)
    assert st["can_download"] is True

    release.set()
    assert wait_settled()
    st = embedding_status.status()
    assert st["status"] == "ready"
    assert st["progress"] is None
    assert st["error"] is None


def test_factory_failure_is_an_error_status(local_settings, monkeypatch):
    monkeypatch.setattr(embedder, "local_model_cached", lambda name, backend=None: True)

    def boom(provider, model):
        raise RuntimeError("onnxruntime exploded")

    embedding_status.warm(force=True, service_factory=boom)
    assert wait_settled()
    st = embedding_status.status()
    assert st["status"] == "error"
    assert "onnxruntime exploded" in st["error"]
    assert st["finished_at"] is not None


def test_backend_resolution_failure_is_an_error_status(local_settings, monkeypatch):
    def unresolved(model, backend=None):
        raise RuntimeError("fastembed has no ONNX export of 'x'")

    monkeypatch.setattr(embedder, "resolve_local_backend", unresolved)
    embedding_status.warm(force=True, service_factory=lambda p, m: FakeService())
    assert wait_settled()
    assert embedding_status.status()["status"] == "error"
    assert "no ONNX export" in embedding_status.status()["error"]


def test_warm_is_idempotent_and_skips_without_a_backend(local_settings, monkeypatch):
    monkeypatch.setattr(embedder, "local_model_cached", lambda name, backend=None: True)
    calls = []

    def factory(provider, model):
        calls.append(model)
        return FakeService(model)

    embedding_status.warm(force=True, service_factory=factory)
    assert wait_settled()
    embedding_status.warm(service_factory=factory)          # already ready → no-op
    assert wait_settled()
    assert calls == ["all-MiniLM-L6-v2"]
    embedding_status.warm(force=True, service_factory=factory)
    assert wait_settled()
    assert calls == ["all-MiniLM-L6-v2", "all-MiniLM-L6-v2"]

    # With no backend importable warm() spawns nothing and says why.
    monkeypatch.setattr(embedder, "fastembed_importable", lambda: False)
    monkeypatch.setattr(embedder, "sentence_transformers_importable", lambda: False)
    st = embedding_status.warm(force=True, service_factory=factory)
    assert st["status"] == "error"
    assert "pip install fastembed" in st["error"]
    assert not embedding_status.is_running()
    assert len(calls) == 2


def test_remote_provider_is_ready_after_a_probe(monkeypatch):
    from config import settings
    set_setting(monkeypatch, "embedding_provider", "ollama")
    set_setting(monkeypatch, "remote_embedding_model", "")
    probed = []

    class Remote(FakeService):
        backend = None

        def embed_query(self, query):
            probed.append(query)
            return super().embed_query(query)

    embedding_status.warm(force=True, service_factory=lambda p, m: Remote(m))
    assert wait_settled()
    st = embedding_status.status()
    assert st["status"] == "ready"
    assert st["provider"] == "ollama"
    assert st["model"] == "nomic-embed-text"  # the catalog default
    assert st["backend"] is None
    assert st["can_download"] is False
    assert probed


def test_remote_probe_times_out(monkeypatch):
    from config import settings
    set_setting(monkeypatch, "embedding_provider", "ollama")
    monkeypatch.setattr(status_mod, "REMOTE_PROBE_TIMEOUT_SECONDS", 0.05)
    release = threading.Event()

    def slow(provider, model):
        release.wait(5)
        return FakeService(model)

    embedding_status.warm(force=True, service_factory=slow)
    assert wait_settled()
    st = embedding_status.status()
    assert st["status"] == "error"
    assert "did not answer" in st["error"]
    release.set()


def test_startup_hook_is_skipped_under_pytest_and_by_env(monkeypatch):
    # Under pytest the startup hook never starts a job (PYTEST_CURRENT_TEST is set).
    assert embedding_status.warm_on_startup() is False
    assert embedding_status.status()["status"] == "unconfigured"
    monkeypatch.setenv("CLIO_SKIP_EMBEDDING_WARMUP", "1")
    assert embedding_status.warm_on_startup() is False


# ── HTTP surface ───────────────────────────────────────────────────────────


@pytest.fixture()
def fresh_db(tmp_path):
    old_db_path = app_db.db_path
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    yield app_db
    app_db.db_path = old_db_path


@pytest.fixture()
def client(fresh_db, monkeypatch, local_settings):
    import main
    from api import system

    set_setting(monkeypatch, "embedding_api_key", "")
    set_setting(monkeypatch, "ollama_cloud_api_key", "")
    set_setting(monkeypatch, "remote_embedding_model", "")
    monkeypatch.setattr(config_manager, "env_file", fresh_db.db_path.parent / ".env")
    # No real model load from any endpoint that warms, nor from /health's
    # collection stats (which open the default indexer).
    monkeypatch.setattr(status_mod, "_default_service_factory", lambda p, m: FakeService(m))
    import services.indexer_manager as im
    monkeypatch.setattr(im, "create_embedding_service", lambda collection_model=None, overrides=None: FakeService(collection_model or "all-MiniLM-L6-v2"))
    monkeypatch.setattr(im.indexer_manager, "_embedding_services", {})
    monkeypatch.setattr(embedder, "local_model_cached", lambda name, backend=None: True)
    monkeypatch.setattr(system, "_huggingface_reachable", lambda timeout=3.0: True)
    monkeypatch.setattr(system, "_ollama_tags", lambda base_url, timeout=2.0: None)
    return TestClient(main.app)


def test_status_endpoint_carries_offline_flag(client):
    body = client.get("/api/embedding/status").json()
    assert body["status"] == "unconfigured"
    assert body["offline_mode"] is False
    for key in ("provider", "backend", "model", "progress", "error", "started_at", "finished_at", "can_download"):
        assert key in body


def test_warm_endpoint_starts_the_job(client):
    body = client.post("/api/embedding/warm").json()
    assert body["status"] in ("loading", "ready")
    assert body["provider"] == "local"
    assert wait_settled()
    assert client.get("/api/embedding/status").json()["status"] == "ready"


def test_health_carries_embedding_status(client):
    client.post("/api/embedding/warm")
    assert wait_settled()
    emb = client.get("/health").json()["embedding"]
    assert emb["status"] == "ready"
    assert emb["backend"] == "fastembed"
    assert emb["progress"] is None
    assert emb["error"] is None
    assert emb["local_model_missing"] is False
    assert emb["provider"] == "local"


def test_options_recommends_local_when_available(client, monkeypatch):
    from api import system
    monkeypatch.setattr(embedder, "local_model_cached", lambda name, backend=None: False)
    monkeypatch.setattr(system, "_ollama_tags", lambda base_url, timeout=2.0: ["nomic-embed-text:latest", "llama3:8b", "bge-m3"])
    body = client.get("/api/embedding/options").json()
    assert body["recommended"] == "local"
    assert body["offline_mode"] is False
    assert body["current"] == {"provider": "local", "model": "all-MiniLM-L6-v2", "backend": None}
    local = body["local"]
    assert local["available"] is True
    assert local["backend"] == "fastembed"
    assert local["model"] == "all-MiniLM-L6-v2"
    assert local["model_label"].startswith("MiniLM L6")
    assert local["size_mb"] == 90
    assert local["cached"] is False
    assert local["huggingface_reachable"] is True
    assert local["install_hint"] is None
    from config import settings
    assert body["ollama"] == {
        "reachable": True,
        "base_url": settings.ollama_base_url,
        "embedding_models": ["nomic-embed-text:latest", "bge-m3"],
        "suggested": "nomic-embed-text",
    }
    hosted = {h["id"]: h for h in body["hosted"]}
    assert set(hosted) == {"google", "mistral", "voyage", "jina", "openrouter", "openai"}
    assert hosted["google"] == {
        "id": "google", "label": "Google Gemini", "key_configured": False,
        "default_model": "gemini-embedding-001", "blocked_offline": False,
    }


def test_options_falls_back_to_ollama_then_hosted(client, monkeypatch, fresh_db):
    from api import system
    monkeypatch.setattr(embedder, "fastembed_importable", lambda: False)
    monkeypatch.setattr(embedder, "sentence_transformers_importable", lambda: False)
    monkeypatch.setattr(system, "_ollama_tags", lambda base_url, timeout=2.0: ["nomic-embed-text"])
    body = client.get("/api/embedding/options").json()
    assert body["local"]["available"] is False
    assert body["local"]["backend"] is None
    assert "pip install fastembed" in body["local"]["install_hint"]
    assert body["recommended"] == "ollama"

    monkeypatch.setattr(system, "_ollama_tags", lambda base_url, timeout=2.0: None)
    fresh_db.set_agent_api_key("google", "AIza-team")
    body = client.get("/api/embedding/options").json()
    assert body["ollama"]["reachable"] is False
    assert body["ollama"]["embedding_models"] == []
    assert body["recommended"] == "hosted"
    assert {h["id"]: h["key_configured"] for h in body["hosted"]}["google"] is True
    assert "AIza-team" not in str(body)

    fresh_db.set_agent_api_key("google", "")
    body = client.get("/api/embedding/options").json()
    assert body["recommended"] == "local"  # nothing better: say local and let the hint explain


def test_options_does_not_probe_huggingface_when_offline(client, monkeypatch):
    from api import system
    set_setting(monkeypatch, "offline_mode", True)

    def must_not_probe(timeout=3.0):
        raise AssertionError("HF probed under OFFLINE_MODE")

    monkeypatch.setattr(system, "_huggingface_reachable", must_not_probe)
    monkeypatch.setattr(embedder, "local_model_cached", lambda name, backend=None: False)
    body = client.get("/api/embedding/options").json()
    assert body["offline_mode"] is True
    assert body["local"]["huggingface_reachable"] is None
    assert all(h["blocked_offline"] for h in body["hosted"])
    assert body["recommended"] == "local"


def test_select_saves_and_reports_reindex(client):
    first = client.post("/api/embedding/select", json={"provider": "local", "model": "all-MiniLM-L6-v2"}).json()
    assert first["saved"] is True
    assert first["needs_reindex"] is False
    assert first["status"]["provider"] == "local"
    assert first["status"]["status"] in ("loading", "ready")
    assert wait_settled()

    changed = client.post("/api/embedding/select", json={
        "provider": "local", "model": "BAAI/bge-small-en-v1.5", "warm": False,
    }).json()
    assert changed["needs_reindex"] is True
    assert changed["status"]["status"] == "unconfigured"
    cfg = client.get("/api/config").json()
    assert cfg["embedding_model"] == "BAAI/bge-small-en-v1.5"
    assert cfg["embedding_provider"] == "local"


def test_select_maps_remote_fields(client, monkeypatch):
    body = client.post("/api/embedding/select", json={
        "provider": "mistral", "model": "mistral-embed", "api_key": "mst-1", "warm": False,
    }).json()
    assert body["saved"] is True
    assert body["needs_reindex"] is True
    cfg = client.get("/api/config").json()
    assert cfg["embedding_provider"] == "mistral"
    assert cfg["remote_embedding_model"] == "mistral-embed"
    assert cfg["embedding_api_key_set"] is True
    assert "mst-1" not in str(cfg)

    body = client.post("/api/embedding/select", json={
        "provider": "ollama", "base_url": "http://gpu-box:11434", "warm": False,
    }).json()
    assert body["saved"] is True
    assert client.get("/api/config").json()["ollama_base_url"] == "http://gpu-box:11434"


def test_select_rejects_unknown_and_cloud_when_offline(client, monkeypatch):
    from api import system
    resp = client.post("/api/embedding/select", json={"provider": "carrier-pigeon"})
    assert resp.status_code == 400
    assert "Unknown embedding provider" in resp.json()["detail"]

    set_setting(monkeypatch, "offline_mode", True)
    resp = client.post("/api/embedding/select", json={"provider": "google"})
    assert resp.status_code == 400
    assert "OFFLINE_MODE" in resp.json()["detail"]
    # Self-hosted stays allowed offline.
    resp = client.post("/api/embedding/select", json={"provider": "ollama", "warm": False})
    assert resp.status_code == 200
    assert client.get("/api/config").json()["embedding_provider"] == "ollama"


class _FakeStream:
    def __init__(self, lines, status_code=200):
        self._lines = lines
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_lines(self):
        yield from self._lines

    def read(self):
        return b"model not found"


class _FakeHttpxClient:
    lines = []
    status_code = 200
    calls = []

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def stream(self, method, url, json=None, **kw):
        type(self).calls.append((method, url, json))
        return _FakeStream(type(self).lines, type(self).status_code)


@pytest.fixture()
def fake_httpx(monkeypatch):
    import httpx
    _FakeHttpxClient.lines = []
    _FakeHttpxClient.status_code = 200
    _FakeHttpxClient.calls = []
    monkeypatch.setattr(httpx, "Client", _FakeHttpxClient)
    return _FakeHttpxClient


def test_ollama_pull_streams_progress_into_status(client, monkeypatch, fake_httpx):
    from api import system
    set_setting(monkeypatch, "embedding_provider", "ollama")
    gate = threading.Event()

    def lines():
        yield json.dumps({"status": "pulling manifest"})
        yield json.dumps({"status": "pulling abc", "digest": "sha256:abc", "total": 1000, "completed": 250})
        gate.wait(2)
        yield json.dumps({"status": "pulling abc", "digest": "sha256:abc", "total": 1000, "completed": 1000})
        yield json.dumps({"status": "success"})

    fake_httpx.lines = lines()
    body = client.post("/api/embedding/ollama/pull", json={"model": "nomic-embed-text"}).json()
    assert body["status"] == "downloading"
    assert body["provider"] == "ollama"
    assert body["model"] == "nomic-embed-text"

    assert wait_for(lambda: (embedding_status.status()["progress"] or {}).get("downloaded_bytes") == 250)
    st = embedding_status.status()
    assert st["status"] == "downloading"
    assert st["progress"]["total_bytes"] == 1000
    assert st["progress"]["percent"] == 25
    gate.set()
    # The finished pull re-runs the warm-up (stubbed factory) → ready.
    assert wait_for(lambda: embedding_status.status()["status"] == "ready")
    assert wait_settled()
    method, url, payload = fake_httpx.calls[0]
    assert (method, payload["model"]) == ("POST", "nomic-embed-text")
    assert url.endswith("/api/pull")


def test_ollama_pull_failure_is_an_error(client, monkeypatch, fake_httpx):
    from api import system
    set_setting(monkeypatch, "embedding_provider", "ollama")
    fake_httpx.lines = [json.dumps({"error": "pull model manifest: file does not exist"})]
    client.post("/api/embedding/ollama/pull", json={"model": "nope"})
    assert wait_for(lambda: embedding_status.status()["status"] == "error")
    assert "does not exist" in embedding_status.status()["error"]

    fake_httpx.status_code = 404
    fake_httpx.lines = []
    assert wait_for(lambda: not (embedding_status._pull_thread and embedding_status._pull_thread.is_alive()))
    client.post("/api/embedding/ollama/pull", json={"model": "nope"})
    assert wait_for(lambda: "HTTP 404" in (embedding_status.status()["error"] or ""))

    resp = client.post("/api/embedding/ollama/pull", json={"model": "  "})
    assert resp.status_code == 400


def test_ollama_pull_leaves_status_alone_for_other_providers(client, fake_httpx):
    fake_httpx.lines = [json.dumps({"status": "success"})]
    body = client.post("/api/embedding/ollama/pull", json={"model": "nomic-embed-text"}).json()
    assert body["status"] == "unconfigured"
    assert wait_for(lambda: not (embedding_status._pull_thread and embedding_status._pull_thread.is_alive()))
    assert embedding_status.status()["status"] == "unconfigured"


# ── FastEmbedService over a stubbed fastembed ──────────────────────────────


def test_fastembed_service_batches_prefixes_and_reports_progress(tmp_path, both_backends, monkeypatch):
    fastembed = pytest.importorskip("fastembed")
    calls = []

    class StubTextEmbedding:
        def __init__(self, model_name, cache_dir=None, **kw):
            calls.append(("init", model_name, cache_dir))
            self.model = object()  # no native prefix methods

        def embed(self, documents, batch_size=256, **kw):
            docs = [documents] if isinstance(documents, str) else list(documents)
            calls.append(("embed", docs, batch_size))
            for d in docs:
                yield np.full(384, float(len(d)), dtype=np.float32)

        def passage_embed(self, texts, **kw):
            calls.append(("passage", list(texts)))
            yield from self.embed(list(texts), **kw)

        def query_embed(self, query, **kw):
            calls.append(("query", query))
            yield from self.embed(query, **kw)

    monkeypatch.setattr(fastembed, "TextEmbedding", StubTextEmbedding)
    monkeypatch.setattr(embedder, "fastembed_cache_dirs", lambda: [tmp_path / "image", tmp_path / "data"])
    monkeypatch.setattr(embedder, "fastembed_data_cache_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(embedder, "hub_offline", lambda: False)
    monkeypatch.setattr(embedder, "EMBED_BATCH_SIZE", 2)

    svc = embedder.FastEmbedService("BAAI/bge-small-en-v1.5")
    assert svc.backend == "fastembed"
    assert svc.model_name == "BAAI/bge-small-en-v1.5"           # the Clio name, not fastembed's
    assert svc.fastembed_model == "BAAI/bge-small-en-v1.5"
    assert svc.embedding_dim == 384
    assert svc.cache_dir == tmp_path / "data"                   # nothing cached → the data dir
    assert calls[0] == ("init", "BAAI/bge-small-en-v1.5", str(tmp_path / "data"))

    progress = []
    out = svc.embed_texts(["a", "bb", "ccc"], progress_callback=lambda d, t: progress.append((d, t)))
    assert out.shape == (3, 384) and out.dtype == np.float32
    assert progress == [(2, 3), (3, 3)]
    passages = [c for c in calls if c[0] == "passage"]
    assert [len(c[1]) for c in passages] == [2, 1]              # EMBED_BATCH_SIZE batches
    assert passages[0][1] == ["a", "bb"]                        # bge: no document prefix

    q = svc.embed_query("hello")
    assert q.shape == (384,)
    query_call = [c for c in calls if c[0] == "query"][0]
    assert query_call[1] == ["Represent this sentence for searching relevant passages: hello"]
    assert svc.embed_texts([]).shape == (0, 384)
    assert embedder.describe_embedding(svc)["query_prompt"].startswith("Represent this sentence")

    # A cached copy in the image path is preferred over the data dir.
    fake_fastembed_cache(tmp_path / "image")
    svc2 = embedder.FastEmbedService("all-MiniLM-L6-v2")
    assert svc2.cache_dir == tmp_path / "image"
    assert svc2._query_prompt is None and svc2._document_prompt is None


def test_fastembed_service_refuses_to_download_offline(tmp_path, both_backends, monkeypatch):
    pytest.importorskip("fastembed")
    monkeypatch.setattr(embedder, "fastembed_cache_dirs", lambda: [tmp_path])
    monkeypatch.setattr(embedder, "fastembed_data_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(embedder, "hub_offline", lambda: True)
    with pytest.raises(RuntimeError) as exc:
        embedder.FastEmbedService("all-MiniLM-L6-v2")
    assert "offline" in str(exc.value)
    assert "docs/AIRGAP.md" in str(exc.value)
    with pytest.raises(RuntimeError):
        embedder.FastEmbedService("all-mpnet-base-v2")  # no ONNX export

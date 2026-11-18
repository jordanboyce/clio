"""One capability record per model (services/model_catalog.py, /api/ai/models).

Providers are local HTTP fakes or monkeypatched SDK calls: no live keys.
"""

import json
import re
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from api.chat import list_provider_models
from services import ai_service, model_catalog
from services.ai_service import create_provider


@pytest.fixture()
def fake_api():
    """A JSON server: ``routes`` maps "METHOD /path" to (status, payload) or
    a callable taking the parsed body. Requests are recorded without bodies."""
    routes: dict = {}
    requests: list = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # never print Authorization headers

        def handle_request(self):
            path = self.path.split("?", 1)[0]
            requests.append((self.command, path, self.headers.get("Authorization"),
                             self.headers.get("x-goog-api-key")))
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            route = routes.get(f"{self.command} {path}")
            if route is None:
                status, payload = 404, {"error": "no route"}
            elif callable(route):
                status, payload = route(body)
            else:
                status, payload = route
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = handle_request
        do_POST = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", routes, requests
    finally:
        server.shutdown()
        server.server_close()


def by_id(records):
    return {r["id"]: r for r in records}


# ── The table ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("provider", sorted(model_catalog.CATALOG))
def test_every_default_model_in_the_table_is_well_formed(provider):
    entry = model_catalog.CATALOG[provider]
    pattern = entry["id_pattern"]
    rec_ids = [m for m, _, _ in entry["recommended"]]
    for field in ("default_model", "fast_model", "vision_model"):
        model = entry.get(field)
        if provider == "openai_compatible":
            assert model is None  # any server at all: nothing to name
            continue
        assert isinstance(model, str) and model == model.strip() and model, (provider, field)
        assert not re.search(r"\s", model), (provider, field, model)
        assert re.fullmatch(pattern, model), (provider, field, model)
        if rec_ids:
            assert model in rec_ids, f"{provider}.{field} {model} is not among its recommendations"
            facts = model_catalog.pattern_facts(provider, model)
            assert facts.get("kinds") == ["chat"], (provider, model, facts)
            if field == "vision_model":
                assert facts.get("vision") is True, (provider, model)
    for model, label, role in entry["recommended"]:
        assert re.fullmatch(pattern, model), (provider, model)
        assert label.strip() and role in ("quality", "fast", "best", "vision")
    for regex, attrs in entry["patterns"]:
        assert set(attrs) <= set(model_catalog.CAPABILITY_FIELDS) | {"namespaced"}
        assert all(k in model_catalog.KINDS for k in attrs.get("kinds", []))


def test_provider_classes_take_their_defaults_from_the_table():
    pairs = {
        "anthropic": ai_service.AnthropicProvider, "openai": ai_service.OpenAIProvider,
        "google": ai_service.GoogleProvider, "grok": ai_service.GrokProvider,
        "openrouter": ai_service.OpenRouterProvider,
        "ollama_cloud": ai_service.OllamaCloudProvider, "bedrock": ai_service.BedrockProvider,
    }
    for name, cls in pairs.items():
        assert cls.QUALITY_MODEL == model_catalog.default_model(name)
        assert cls.FAST_MODEL == model_catalog.fast_model(name)
    assert ai_service.DEFAULT_OLLAMA_MODEL == model_catalog.default_model("ollama")


def test_unknown_stays_unknown():
    record = model_catalog.complete_record("openai_compatible", {"id": "house-model-7"})
    assert record["kinds"] == [] and record["source"] == "unknown"
    assert record["vision"] is None and record["tools"] is None and record["context_tokens"] is None
    assert record["pattern_fields"] == []


def test_provider_facts_win_and_table_fills_are_marked():
    record = model_catalog.complete_record(
        "anthropic", {"id": "claude-sonnet-5-5", "kinds": ["chat"], "vision": False})
    assert record["vision"] is False  # the provider said so; the table's True does not override
    assert record["tools"] is True and record["pattern_fields"] == ["tools"]
    assert record["source"] == "provider"


# ── Ollama, checked against its real response shapes ───────────────────────


OLLAMA_TAGS_NEW = {"models": [
    {"name": "gemma3:4b", "size": 3, "modified_at": "t1",
     "details": {"families": ["gemma3", "clip"], "context_length": 131072, "embedding_length": 2560},
     "capabilities": ["completion", "vision"]},
    {"name": "llama3.2:3b", "size": 2, "modified_at": "t2",
     "details": {"families": ["llama"], "context_length": 131072},
     "capabilities": ["completion", "tools"]},
    {"name": "nomic-embed-text:latest", "size": 1, "modified_at": "t3",
     "details": {"families": ["nomic-bert"], "context_length": 2048, "embedding_length": 768},
     "capabilities": ["embedding"]},
]}


def test_ollama_capabilities_from_tags(fake_api):
    root, routes, requests = fake_api
    routes["GET /api/tags"] = (200, OLLAMA_TAGS_NEW)
    result = model_catalog.catalog("ollama", create_provider("ollama", base_url=root).list_model_records)

    records = by_id(result["records"])
    assert (records["gemma3:4b"]["kinds"], records["gemma3:4b"]["vision"], records["gemma3:4b"]["tools"]) == (["chat"], True, False)
    assert records["llama3.2:3b"]["tools"] is True and records["llama3.2:3b"]["vision"] is False
    emb = records["nomic-embed-text:latest"]
    assert emb["kinds"] == ["embedding"] and emb["embedding_dimensions"] == 768 and emb["max_input_tokens"] == 2048
    assert all(r["source"] == "provider" and r["pattern_fields"] == [] for r in result["records"])
    # Nothing installed is called "llama3.2", so the default is an installed chat model.
    assert result["defaults"] == {"model": "gemma3:4b", "fast": "gemma3:4b", "vision": "gemma3:4b"}
    assert [r[1] for r in requests] == ["/api/tags"]  # no /api/show needed


def test_older_ollama_reports_capabilities_through_show(fake_api):
    root, routes, requests = fake_api
    routes["GET /api/tags"] = (200, {"models": [{"name": "qwen3:8b", "details": {"families": ["qwen3"]}}]})
    routes["POST /api/show"] = (200, {
        "capabilities": ["completion", "tools", "thinking"],
        "model_info": {"general.architecture": "qwen3", "qwen3.context_length": 40960},
        "details": {"families": ["qwen3"]},
    })
    records = create_provider("ollama", base_url=root).list_model_records()
    record = model_catalog.complete_record("ollama", records[0])
    assert (record["kinds"], record["tools"], record["vision"], record["context_tokens"]) == (["chat"], True, False, 40960)
    assert record["source"] == "provider"


def test_oldest_ollama_falls_back_to_families_then_names_marked_as_guesses(fake_api):
    root, routes, _ = fake_api
    routes["GET /api/tags"] = (200, {"models": [
        {"name": "llava:7b", "details": {"families": ["llama", "clip"]}},
        {"name": "mxbai-embed-large:latest", "details": {"families": ["bert"]}},
        {"name": "mistral:7b", "details": {"families": ["llama"]}},
    ]})
    routes["POST /api/show"] = (200, {"details": {"families": ["llama"]}})  # no capabilities key
    raw = create_provider("ollama", base_url=root).list_model_records()
    records = by_id(model_catalog.complete_record("ollama", r) for r in raw)
    assert records["llava:7b"]["vision"] is True and records["llava:7b"]["source"] == "pattern"
    assert records["mxbai-embed-large:latest"]["kinds"] == ["embedding"]
    assert records["mxbai-embed-large:latest"]["pattern_fields"] == ["kinds"]
    assert records["mistral:7b"]["source"] == "unknown"


def test_ollama_vision_models_endpoint_uses_capabilities(fake_api, monkeypatch):
    import asyncio
    from api import system
    from config import settings

    root, routes, _ = fake_api
    routes["GET /api/tags"] = (200, {"models": OLLAMA_TAGS_NEW["models"] + [
        # Named like a vision model, but Ollama says it is not one.
        {"name": "my-vision-notes:latest", "details": {}, "capabilities": ["completion"]},
    ]})
    monkeypatch.setattr(settings, "ollama_base_url", root)
    body = asyncio.run(system.get_ollama_vision_models())
    assert body["available"] is True and body["total_models"] == 4
    assert [m["name"] for m in body["models"]] == ["gemma3:4b"]
    assert body["models"][0]["vision_detected_by"] == "capabilities"
    assert body["suggested"] == model_catalog.suggested("ollama", "vision")


# ── OpenAI and OpenAI-compatible servers ───────────────────────────────────


def test_openai_ids_alone_are_classified_by_the_table_and_marked(fake_api):
    root, routes, _ = fake_api
    routes["GET /v1/models"] = (200, {"object": "list", "data": [
        {"id": i, "object": "model", "created": 0, "owned_by": "openai"}
        for i in ("gpt-6.1-sol", "text-embedding-3-small", "tts-1", "whisper-1", "gpt-image-2", "omni-moderation-latest")
    ]})
    provider = create_provider("openai", "sk-test", base_url=f"{root}/v1")
    result = model_catalog.catalog("openai", provider.list_model_records)
    records = by_id(result["records"])
    assert records["gpt-6.1-sol"]["kinds"] == ["chat"] and records["gpt-6.1-sol"]["recommended"] is True
    assert records["text-embedding-3-small"]["kinds"] == ["embedding"]
    assert records["text-embedding-3-small"]["embedding_dimensions"] == 1536
    assert records["tts-1"]["kinds"] == ["speech"]
    assert records["whisper-1"]["kinds"] == ["transcription"]
    assert records["gpt-image-2"]["kinds"] == ["image"]
    assert records["omni-moderation-latest"]["kinds"] == ["moderation"]
    assert all(r["source"] == "pattern" for r in result["records"])
    # Only what the key offers is recommended: gpt-6-astra and gpt-6-luna are not listed.
    assert [r["id"] for r in result["records"] if r["recommended"]] == ["gpt-6.1-sol"]
    assert result["defaults"]["model"] == "gpt-6.1-sol"
    assert result["defaults"]["fast"] == "gpt-6.1-sol"  # the table's fast model is not offered


def test_compatible_server_extras_are_read_as_provider_facts(fake_api):
    root, routes, _ = fake_api
    routes["GET /v1/models"] = (200, {"object": "list", "data": [
        {"id": "vllm-chat", "object": "model", "max_model_len": 32768},
        {"id": "together-embed", "object": "model", "type": "embedding", "context_length": 8192},
        {"id": "mistral-large", "object": "model", "max_context_length": 131072,
         "capabilities": {"completion_chat": True, "function_calling": True, "vision": False}},
        {"id": "groq-llama", "object": "model", "context_window": 131072},
        {"id": "nomic-embed-v2", "object": "model"},
    ]})
    provider = create_provider("openai_compatible", "k", base_url=f"{root}/v1")
    records = by_id(model_catalog.catalog("openai_compatible", provider.list_model_records)["records"])
    assert records["vllm-chat"]["context_tokens"] == 32768 and records["vllm-chat"]["source"] == "unknown"
    assert records["together-embed"]["kinds"] == ["embedding"] and records["together-embed"]["source"] == "provider"
    mistral = records["mistral-large"]
    assert (mistral["kinds"], mistral["tools"], mistral["vision"], mistral["context_tokens"]) == (["chat"], True, False, 131072)
    assert records["groq-llama"]["context_tokens"] == 131072
    assert records["nomic-embed-v2"]["kinds"] == ["embedding"] and records["nomic-embed-v2"]["source"] == "pattern"


def test_openrouter_reports_modalities_tools_and_context(fake_api, monkeypatch):
    root, routes, requests = fake_api
    routes["GET /api/v1/models"] = (200, {"data": [
        {"id": "anthropic/claude-sonnet-5.5", "name": "Anthropic: Claude Sonnet 5.5", "context_length": 1000000,
         "architecture": {"input_modalities": ["text", "image", "file"], "output_modalities": ["text"]},
         "supported_parameters": ["max_tokens", "tools", "reasoning"]},
        {"id": "deepseek/deepseek-v4", "name": "DeepSeek V4", "context_length": 163840,
         "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
         "supported_parameters": ["max_tokens", "temperature"]},
    ]})
    provider = create_provider("openrouter", "sk-or-test")
    provider.client = provider.client.with_options(base_url=f"{root}/api/v1")
    result = model_catalog.catalog("openrouter", provider.list_model_records)
    records = by_id(result["records"])
    sonnet = records["anthropic/claude-sonnet-5.5"]
    assert (sonnet["vision"], sonnet["tools"], sonnet["context_tokens"], sonnet["source"]) == (True, True, 1000000, "provider")
    assert sonnet["label"] == "Anthropic: Claude Sonnet 5.5" and sonnet["roles"] == ["quality", "vision"]
    deepseek = records["deepseek/deepseek-v4"]
    assert (deepseek["vision"], deepseek["tools"]) == (False, False)
    assert requests[0][2] == "Bearer sk-or-test"


def test_xai_language_models_add_modalities(fake_api):
    root, routes, requests = fake_api
    routes["GET /v1/models"] = (200, {"object": "list", "data": [
        {"id": "grok-4.7", "object": "model", "context_length": 500000},
        {"id": "grok-imagine-image", "object": "model"},
    ]})
    routes["GET /v1/language-models"] = (200, {"models": [
        {"id": "grok-4.7", "input_modalities": ["text", "image"], "output_modalities": ["text"]},
    ]})
    routes["GET /v1/image-generation-models"] = (200, {"models": [
        {"id": "grok-imagine-image", "input_modalities": ["text"], "output_modalities": ["image"]},
    ]})
    provider = create_provider("grok", "xai-test")
    provider.client = provider.client.with_options(base_url=f"{root}/v1")
    records = by_id(model_catalog.catalog("grok", provider.list_model_records)["records"])
    grok = records["grok-4.7"]
    assert (grok["kinds"], grok["vision"], grok["context_tokens"], grok["source"]) == (["chat"], True, 500000, "provider")
    assert grok["pattern_fields"] == ["tools"]
    assert records["grok-imagine-image"]["kinds"] == ["image"]
    assert {r[1] for r in requests} == {"/v1/models", "/v1/language-models", "/v1/image-generation-models"}


def test_gemini_native_list_gives_generation_methods(fake_api, monkeypatch):
    root, routes, requests = fake_api
    routes["GET /v1beta/models"] = (200, {"models": [
        {"name": "models/gemini-3.8-flash", "displayName": "Gemini 3.8 Flash", "inputTokenLimit": 1048576,
         "supportedGenerationMethods": ["generateContent", "countTokens"]},
        {"name": "models/gemini-embedding-001", "displayName": "Gemini Embedding 001", "inputTokenLimit": 2048,
         "supportedGenerationMethods": ["embedContent"]},
    ]})
    monkeypatch.setattr(ai_service.GoogleProvider, "NATIVE_MODELS_URL", f"{root}/v1beta/models")
    provider = create_provider("google", "AIza-test")
    result = model_catalog.catalog("google", provider.list_model_records)
    records = by_id(result["records"])
    flash = records["gemini-3.8-flash"]
    assert (flash["kinds"], flash["context_tokens"], flash["label"], flash["source"]) == (["chat"], 1048576, "Gemini 3.8 Flash", "provider")
    assert flash["vision"] is True and set(flash["pattern_fields"]) == {"vision", "tools"}
    emb = records["gemini-embedding-001"]
    assert emb["kinds"] == ["embedding"] and emb["max_input_tokens"] == 2048
    assert requests[0][3] == "AIza-test" and requests[0][2] is None
    assert result["defaults"]["vision"] == "gemini-3.8-flash"


def test_anthropic_models_api_capabilities(monkeypatch):
    provider = create_provider("anthropic", "sk-ant-test")
    data = [types.SimpleNamespace(model_dump=lambda d=d: d) for d in (
        {"id": "claude-sonnet-5-5", "display_name": "Claude Sonnet 5.5", "max_input_tokens": 1000000,
         "capabilities": {"image_input": {"supported": True}}},
        {"id": "claude-haiku-5-5", "display_name": "Claude Haiku 5.5", "max_input_tokens": 1000000,
         "capabilities": {"image_input": {"supported": True}}},
    )]
    seen = {}

    class Models:
        def list(self, limit):
            seen["limit"] = limit
            return types.SimpleNamespace(data=data)

    class Client:
        models = Models()

        def with_options(self, **kw):
            seen["options"] = kw
            return self

    provider.client = Client()
    result = model_catalog.catalog("anthropic", provider.list_model_records)
    assert seen["options"] == {"timeout": ai_service.LIST_TIMEOUT, "max_retries": 0}
    ids = [r["id"] for r in result["records"]]
    assert ids == ["claude-sonnet-5-5", "claude-haiku-5-5"]  # recommendations first, table order
    sonnet = result["records"][0]
    assert (sonnet["vision"], sonnet["context_tokens"], sonnet["max_input_tokens"]) == (True, 1000000, 1000000)
    assert sonnet["source"] == "provider" and sonnet["pattern_fields"] == ["tools"]
    assert result["defaults"] == {"model": "claude-sonnet-5-5", "fast": "claude-haiku-5-5", "vision": "claude-sonnet-5-5"}


def test_bedrock_foundation_models(monkeypatch):
    calls = {}

    class FakeBedrock:
        def list_foundation_models(self, **kw):
            calls["filter"] = kw
            return {"modelSummaries": [
                {"modelId": "anthropic.claude-sonnet-5-5", "modelName": "Claude Sonnet 5.5",
                 "inputModalities": ["TEXT", "IMAGE"], "outputModalities": ["TEXT"],
                 "modelLifecycle": {"status": "ACTIVE"}},
                {"modelId": "anthropic.claude-v2", "modelName": "Claude", "inputModalities": ["TEXT"],
                 "outputModalities": ["TEXT"], "modelLifecycle": {"status": "LEGACY"}},
            ]}

    fake_boto3 = types.SimpleNamespace(client=lambda service, **kw: calls.setdefault("client", (service, kw)) and FakeBedrock())
    fake_config = types.ModuleType("botocore.config")
    fake_config.Config = lambda **kw: kw
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setitem(sys.modules, "botocore", types.ModuleType("botocore"))
    monkeypatch.setitem(sys.modules, "botocore.config", fake_config)

    provider = ai_service.BedrockProvider.__new__(ai_service.BedrockProvider)
    provider.client = types.SimpleNamespace(aws_region="us-east-1")
    provider._region, provider._aws_keys = None, None
    records = model_catalog.catalog("bedrock", provider.list_model_records)["records"]
    assert [r["id"] for r in records] == ["anthropic.claude-sonnet-5-5"]
    assert records[0]["vision"] is True and records[0]["source"] == "provider"
    assert calls["filter"] == {"byProvider": "Anthropic"}
    assert calls["client"][0] == "bedrock" and calls["client"][1]["region_name"] == "us-east-1"


# ── The endpoint: compatibility, offline, cache ────────────────────────────


def test_endpoint_keeps_the_old_models_field(fake_api):
    root, routes, _ = fake_api
    routes["GET /v1/models"] = (200, {"object": "list", "data": [{"id": "b-model"}, {"id": "a-model"}]})
    body = list_provider_models(x_ai_provider="openai_compatible", x_ai_key="k", x_ai_base_url=f"{root}/v1")
    assert body["models"] == ["a-model", "b-model"]  # what old callers read: ids, sorted as before
    assert [r["id"] for r in body["records"]] == ["a-model", "b-model"]
    assert body["listed"] is True and body["error"] is None


def test_unreachable_provider_answers_from_the_table_quickly():
    started = time.monotonic()
    # A closed local port: refused, no long timeout.
    provider =create_provider("openai", "sk-test", base_url="http://127.0.0.1:9/v1")
    result = model_catalog.catalog("openai", provider.list_model_records)
    assert time.monotonic() - started < ai_service.LIST_TIMEOUT + 2
    assert result["listed"] is False and result["models"] == [] and result["error"]
    assert [r["id"] for r in result["records"]] == ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna"]
    assert all(r["source"] == "pattern" and r["recommended"] for r in result["records"])
    assert result["defaults"]["model"] == "gpt-6.1-sol"


def test_offline_mode_answers_without_calling_anyone(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "offline_mode", True)
    started = time.monotonic()
    body = list_provider_models(x_ai_provider="anthropic", x_ai_key="sk-ant-test", x_ai_base_url=None)
    assert time.monotonic() - started < 1.0
    assert body["listed"] is False and body["models"] == []
    assert "offline" in body["error"].lower()
    assert body["records"] and body["defaults"]["model"] == "claude-sonnet-5-5"


def test_missing_key_still_returns_the_records_shape():
    body = list_provider_models(x_ai_provider="openai", x_ai_key=None, x_ai_base_url=None)
    assert body["models"] == [] and body["error"]
    assert set(body) >= {"models", "records", "defaults", "listed", "error"}


def test_listing_is_cached_per_key_and_refresh_asks_again(fake_api):
    root, routes, requests = fake_api
    routes["GET /v1/models"] = (200, {"object": "list", "data": [{"id": "m"}]})
    args = dict(x_ai_provider="openai_compatible", x_ai_base_url=f"{root}/v1")
    list_provider_models(x_ai_key="key-one", **args)
    list_provider_models(x_ai_key="key-one", **args)
    assert len(requests) == 1
    list_provider_models(x_ai_key="key-two", **args)
    assert len(requests) == 2
    list_provider_models(x_ai_key="key-one", refresh=True, **args)
    assert len(requests) == 3
    assert "key-one" not in repr(model_catalog._cache) and "key-two" not in repr(model_catalog._cache)


def test_a_failed_listing_is_not_cached(fake_api):
    root, routes, requests = fake_api
    routes["GET /v1/models"] = (500, {"error": "down"})
    args = dict(x_ai_provider="openai_compatible", x_ai_key="k", x_ai_base_url=f"{root}/v1")
    assert list_provider_models(**args)["listed"] is False
    routes["GET /v1/models"] = (200, {"object": "list", "data": [{"id": "m"}]})
    assert list_provider_models(**args)["models"] == ["m"]


def test_route_over_http(fake_api):
    from fastapi.testclient import TestClient
    import main

    root, routes, _ = fake_api
    routes["GET /v1/models"] = (200, {"object": "list", "data": [{"id": "llava-next-vl"}]})
    client = TestClient(main.app)
    body = client.post("/api/ai/models?refresh=true", headers={
        "X-AI-Provider": "openai_compatible", "X-AI-Base-URL": f"{root}/v1", "X-AI-Key": "k",
    }).json()
    assert body["models"] == ["llava-next-vl"]
    record = body["records"][0]
    assert record["vision"] is True and record["source"] == "pattern"
    assert record["pattern_fields"] == ["kinds", "vision"]

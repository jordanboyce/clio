"""Catalog of places embeddings can run.

This is the single description of every embedding provider the app offers:
what it is in plain language, what it costs, where the text goes, which API
key it needs, and which models to suggest. The Settings UI renders this
catalog directly (GET /api/embedding/providers) and the factory in
services/embedder.py uses it to build the right service — so adding a
provider is one entry here plus, if it speaks a new protocol, one service
class.

The catalog must stay importable without touching config or the database:
config.py validates EMBEDDING_PROVIDER against PROVIDER_IDS at startup.

Provider `kind` decides the transport:
  - "local"              in-process: fastembed (ONNX, FastEmbedService) or
                         sentence-transformers (PyTorch, EmbeddingService);
                         services/embedder.py picks per LOCAL_EMBEDDING_BACKEND
  - "ollama"             Ollama's /api/embed (OllamaEmbeddingService)
  - "openai_compatible"  POST {base_url}/embeddings, the shape OpenAI made
                         standard and most hosted embedding APIs copy
                         (OpenAICompatibleEmbeddingService)

`key_provider` names the AI Providers card whose saved team key is reused
when no embedding-specific key is set, so someone who already added a Google,
OpenAI or OpenRouter key for chat doesn't have to paste it twice.

`cost` is a coarse, honest label for non-engineers, not a price list:
  - "free"        nothing to pay, ever (runs on your own hardware)
  - "free_tier"   the vendor offers a no-cost allowance; heavy use may bill
  - "paid"        metered from the first request
  - "self_hosted" free to call, but you run the server
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Ollama Cloud's direct API (https://ollama.com) serves chat models only; it
# answers /api/embed with 401 and lists no embedding models in /api/tags
# (verified 2026-09-09). The provider stays in the catalog so an existing
# EMBEDDING_PROVIDER=ollama_cloud deployment still resolves and gets a clear
# error, but it is hidden from the picker (`hidden`) until Ollama ships it.
OLLAMA_CLOUD_BASE_URL = "https://ollama.com"

EMBEDDING_PROVIDERS: List[Dict[str, Any]] = [
    {
        "id": "local",
        "label": "Built in",
        "kind": "local",
        "cost": "free",
        "cost_label": "Free",
        "privacy": "Nothing leaves this server.",
        "blurb": (
            "Runs on this server's CPU. No account or key. Fine for small "
            "collections; slow on small hosting plans, and each model must be "
            "downloaded once."
        ),
        "needs_key": False,
        "key_provider": None,
        "key_link": None,
        "base_url": None,
        "default_model": "all-MiniLM-L6-v2",
        "models": [
            # `backends` names the in-process runtime(s) that can serve the
            # model: "fastembed" (ONNX, no PyTorch) and/or
            # "sentence-transformers" (PyTorch). Both produce the same vectors
            # for a given model, so the choice never forces a re-index.
            {"id": "all-MiniLM-L6-v2", "label": "MiniLM L6 — fast, small (default)", "dimensions": 384, "size_mb": 90, "language": "English", "backends": ["fastembed", "sentence-transformers"]},
            {"id": "BAAI/bge-small-en-v1.5", "label": "BGE small — stronger than MiniLM, same size class", "dimensions": 384, "size_mb": 130, "language": "English", "backends": ["fastembed", "sentence-transformers"]},
            {"id": "all-MiniLM-L12-v2", "label": "MiniLM L12 — balanced", "dimensions": 384, "size_mb": 120, "language": "English", "backends": ["sentence-transformers"]},
            {"id": "BAAI/bge-base-en-v1.5", "label": "BGE base — stronger English search", "dimensions": 768, "size_mb": 420, "language": "English", "backends": ["fastembed", "sentence-transformers"]},
            {"id": "nomic-ai/nomic-embed-text-v1.5", "label": "Nomic Embed v1.5 — 8k context, good for code and long passages", "dimensions": 768, "size_mb": 270, "language": "English", "backends": ["fastembed", "sentence-transformers"]},
            {"id": "jinaai/jina-embeddings-v2-base-code", "label": "Jina Code v2 — source code", "dimensions": 768, "size_mb": 320, "language": "Code", "backends": ["fastembed", "sentence-transformers"]},
            {"id": "all-mpnet-base-v2", "label": "MPNet base — high quality, slower", "dimensions": 768, "size_mb": 420, "language": "English", "backends": ["sentence-transformers"]},
            {"id": "paraphrase-multilingual-MiniLM-L12-v2", "label": "Multilingual MiniLM — 50+ languages", "dimensions": 384, "size_mb": 470, "language": "Multilingual", "backends": ["fastembed", "sentence-transformers"]},
            {"id": "intfloat/multilingual-e5-small", "label": "Multilingual E5 small — 100 languages", "dimensions": 384, "size_mb": 470, "language": "Multilingual", "backends": ["sentence-transformers"]},
            {"id": "Qwen/Qwen3-Embedding-0.6B", "label": "Qwen3 Embedding 0.6B — best quality, heavy", "dimensions": 1024, "size_mb": 1300, "language": "Multilingual", "backends": ["fastembed", "sentence-transformers"]},
            {"id": "paraphrase-MiniLM-L3-v2", "label": "MiniLM L3 — fastest, lower quality", "dimensions": 384, "size_mb": 60, "language": "English", "backends": ["sentence-transformers"]},
        ],
    },
    {
        "id": "google",
        "label": "Google Gemini",
        "kind": "openai_compatible",
        "cost": "free_tier",
        "cost_label": "Free tier",
        "privacy": "Document text is sent to Google when indexing and searching.",
        "blurb": (
            "Google AI Studio keys include a free allowance that covers most "
            "personal and small-team use. Good multilingual quality."
        ),
        "needs_key": True,
        "key_provider": "google",
        "key_link": "https://aistudio.google.com/app/apikey",
        "pricing_link": "https://ai.google.dev/gemini-api/docs/pricing",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-embedding-001",
        "models": [
            {"id": "gemini-embedding-001", "label": "Gemini Embedding 001 (recommended)", "dimensions": 3072, "language": "Multilingual"},
        ],
    },
    {
        "id": "mistral",
        "label": "Mistral",
        "kind": "openai_compatible",
        "cost": "free_tier",
        "cost_label": "Free tier",
        "privacy": "Document text is sent to Mistral when indexing and searching.",
        "blurb": (
            "Mistral's free experiment plan includes embeddings at reduced "
            "rate limits. EU-hosted."
        ),
        "needs_key": True,
        "key_provider": None,
        "key_link": "https://console.mistral.ai/api-keys",
        "pricing_link": "https://mistral.ai/pricing",
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-embed",
        "models": [
            {"id": "mistral-embed", "label": "Mistral Embed (recommended)", "dimensions": 1024, "language": "Multilingual"},
            {"id": "codestral-embed", "label": "Codestral Embed — for source code", "dimensions": 1536, "language": "Code"},
        ],
    },
    {
        "id": "voyage",
        "label": "Voyage AI",
        "kind": "openai_compatible",
        "cost": "free_tier",
        "cost_label": "Free tier",
        "privacy": "Document text is sent to Voyage AI when indexing and searching.",
        "blurb": (
            "Retrieval-focused models with a generous free monthly allowance. "
            "Pay-as-you-go beyond that."
        ),
        "needs_key": True,
        "key_provider": None,
        "key_link": "https://dashboard.voyageai.com/api-keys",
        "pricing_link": "https://docs.voyageai.com/docs/pricing",
        "base_url": "https://api.voyageai.com/v1",
        "default_model": "voyage-3.5-lite",
        "models": [
            {"id": "voyage-3.5-lite", "label": "Voyage 3.5 Lite — fast (recommended)", "dimensions": 1024, "language": "Multilingual"},
            {"id": "voyage-3.5", "label": "Voyage 3.5 — higher quality", "dimensions": 1024, "language": "Multilingual"},
            {"id": "voyage-code-3", "label": "Voyage Code 3 — for source code", "dimensions": 1024, "language": "Code"},
        ],
    },
    {
        "id": "jina",
        "label": "Jina AI",
        "kind": "openai_compatible",
        "cost": "free_tier",
        "cost_label": "Free tier",
        "privacy": "Document text is sent to Jina AI when indexing and searching.",
        "blurb": "Free starter credits without a card; long-document friendly models.",
        "needs_key": True,
        "key_provider": None,
        "key_link": "https://jina.ai/api-dashboard/",
        "pricing_link": "https://jina.ai/embeddings/",
        "base_url": "https://api.jina.ai/v1",
        "default_model": "jina-embeddings-v3",
        "models": [
            {"id": "jina-embeddings-v3", "label": "Jina Embeddings v3 (recommended)", "dimensions": 1024, "language": "Multilingual"},
        ],
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "kind": "openai_compatible",
        "cost": "free_tier",
        "cost_label": "Free tier",
        "privacy": (
            "Document text is sent to OpenRouter and on to whichever company "
            "hosts the chosen model; some free models let that host keep it "
            "for training."
        ),
        "blurb": (
            "One key, dozens of embedding models. The ':free' ones cost nothing "
            "but allow only 50 requests a day (1,000 once you have bought $10 of "
            "credit), and every search is a request, so busy teams should pick "
            "a paid model at a few cents per million tokens. Reuses the "
            "OpenRouter key from AI Providers."
        ),
        "needs_key": True,
        "key_provider": "openrouter",
        "key_link": "https://openrouter.ai/keys",
        "pricing_link": "https://openrouter.ai/collections/embedding-models",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "nvidia/nemotron-3-embed-1b:free",
        "models": [
            {"id": "nvidia/nemotron-3-embed-1b:free", "label": "Nemotron 3 Embed 1B — free (recommended)", "dimensions": 2048, "language": "Multilingual"},
            {"id": "nvidia/llama-nemotron-embed-vl-1b-v2:free", "label": "Llama Nemotron Embed VL 1B — free, very long inputs", "dimensions": 2048, "language": "Multilingual"},
            {"id": "liquid/lfm-2.5-embedding-350m:free", "label": "LFM2.5 Embedding 350M — free, small; host may train on your text", "dimensions": 1024, "language": "Multilingual"},
            {"id": "qwen/qwen3-embedding-8b", "label": "Qwen3 Embedding 8B — paid, about $0.01 per million tokens", "dimensions": 4096, "language": "Multilingual"},
            {"id": "baai/bge-m3", "label": "BGE-M3 — paid, about $0.01 per million tokens", "dimensions": 1024, "language": "Multilingual"},
            {"id": "openai/text-embedding-3-small", "label": "OpenAI text-embedding-3-small — paid, about $0.02 per million tokens", "dimensions": 1536, "language": "Multilingual"},
        ],
    },
    {
        "id": "openai",
        "label": "OpenAI",
        "kind": "openai_compatible",
        "cost": "paid",
        "cost_label": "Paid (low cost)",
        "privacy": "Document text is sent to OpenAI when indexing and searching.",
        "blurb": (
            "Metered per token but very cheap for embeddings — a few cents "
            "per thousand pages. Reuses the OpenAI key from AI Providers."
        ),
        "needs_key": True,
        "key_provider": "openai",
        "key_link": "https://platform.openai.com/api-keys",
        "pricing_link": "https://openai.com/api/pricing/",
        "base_url": "https://api.openai.com/v1",
        "default_model": "text-embedding-3-small",
        "models": [
            {"id": "text-embedding-3-small", "label": "text-embedding-3-small (recommended)", "dimensions": 1536, "language": "Multilingual"},
            {"id": "text-embedding-3-large", "label": "text-embedding-3-large — higher quality", "dimensions": 3072, "language": "Multilingual"},
        ],
    },
    {
        "id": "ollama",
        "label": "Ollama on your own server",
        "kind": "ollama",
        "cost": "self_hosted",
        "cost_label": "Self-hosted",
        "privacy": "Text goes only to the Ollama server you point at.",
        "blurb": (
            "An Ollama instance you run (this machine, a GPU box on your "
            "network). Pull an embedding model there first."
        ),
        "needs_key": False,
        "key_provider": None,
        "key_link": None,
        "base_url": None,  # settings.ollama_base_url
        "default_model": "nomic-embed-text",
        "models": [
            {"id": "nomic-embed-text", "label": "nomic-embed-text (recommended)", "dimensions": 768, "language": "English"},
            {"id": "embeddinggemma", "label": "embeddinggemma — Google, multilingual", "dimensions": 768, "language": "Multilingual"},
            {"id": "mxbai-embed-large", "label": "mxbai-embed-large — higher quality", "dimensions": 1024, "language": "English"},
            {"id": "bge-m3", "label": "bge-m3 — multilingual", "dimensions": 1024, "language": "Multilingual"},
            {"id": "qwen3-embedding:0.6b", "label": "qwen3-embedding 0.6B — multilingual", "dimensions": 1024, "language": "Multilingual"},
            {"id": "all-minilm", "label": "all-minilm — small and fast", "dimensions": 384, "language": "English"},
        ],
    },
    {
        "id": "openai_compatible",
        "label": "Custom endpoint",
        "kind": "openai_compatible",
        "cost": "self_hosted",
        "cost_label": "Depends on the service",
        "privacy": "Text goes to whatever endpoint you enter.",
        "blurb": (
            "Any service that speaks the OpenAI embeddings API: LM Studio, "
            "vLLM, LiteLLM, Together, Cohere's compatibility endpoint, and "
            "so on. Enter its base URL, key, and model name."
        ),
        "needs_key": False,
        "key_provider": "openai_compatible",
        "key_link": None,
        "base_url": None,  # settings.embedding_base_url
        "default_model": "",
        "models": [],
    },
    {
        "id": "ollama_cloud",
        "label": "Ollama Cloud",
        "kind": "ollama",
        "cost": "free_tier",
        "cost_label": "Free tier",
        "privacy": "Document text is sent to ollama.com when indexing and searching.",
        "blurb": (
            "Not available: Ollama Cloud serves chat models only and rejects "
            "embedding requests (checked September 2026). Pick another option."
        ),
        "hidden": True,
        "needs_key": True,
        "key_provider": "ollama_cloud",
        "key_link": "https://ollama.com/settings/keys",
        "base_url": OLLAMA_CLOUD_BASE_URL,
        "default_model": "nomic-embed-text",
        "models": [
            {"id": "nomic-embed-text", "label": "nomic-embed-text", "dimensions": 768, "language": "English"},
        ],
    },
    # xAI's REST reference documents POST /v1/embeddings and GET
    # /v1/embedding-models, but the only model id on that page is a
    # placeholder ("v1", version 0.1.0) and the models/pricing page lists no
    # embedding model at all (checked 2026-09-09; api.x.ai/v1/embedding-models
    # answers 401 without a key, so the route exists). Kept hidden with the
    # real endpoint so EMBEDDING_PROVIDER=xai plus REMOTE_EMBEDDING_MODEL gets
    # a genuine probe the day xAI ships one; until then it fails closed.
    {
        "id": "xai",
        "label": "xAI (Grok)",
        "kind": "openai_compatible",
        "cost": "paid",
        "cost_label": "Paid",
        "privacy": "Document text is sent to xAI when indexing and searching.",
        "blurb": (
            "Not available: xAI documents an embeddings endpoint but offers no "
            "embedding model on it (checked September 2026). Pick another option."
        ),
        "hidden": True,
        "needs_key": True,
        "key_provider": "grok",
        "key_link": "https://console.x.ai/",
        "pricing_link": "https://docs.x.ai/developers/models",
        "base_url": "https://api.x.ai/v1",
        "default_model": "",
        "models": [],
    },
]

PROVIDER_IDS = tuple(p["id"] for p in EMBEDDING_PROVIDERS)

# Providers whose vectors are computed somewhere other than this deployment.
# OFFLINE_MODE refuses these; "ollama" and "openai_compatible" point at
# operator-controlled hosts and stay allowed, matching ai_service's rule.
CLOUD_EMBEDDING_PROVIDERS = tuple(
    p["id"] for p in EMBEDDING_PROVIDERS
    if p["id"] not in ("local", "ollama", "openai_compatible")
)


def get_provider(provider_id: str) -> Optional[Dict[str, Any]]:
    """Catalog entry for a provider id, or None when unknown."""
    for p in EMBEDDING_PROVIDERS:
        if p["id"] == provider_id:
            return p
    return None


def default_model_for(provider_id: str) -> str:
    p = get_provider(provider_id)
    return p["default_model"] if p else ""


def local_model_entry(model_name: str) -> Optional[Dict[str, Any]]:
    """Catalog entry for a local model name, or None when it is not curated."""
    local = get_provider("local") or {"models": []}
    for m in local["models"]:
        if m["id"] == model_name:
            return m
    return None


def local_model_catalog() -> List[Dict[str, Any]]:
    """The curated sentence-transformers list, in the shape older callers of
    config_manager.get_embedding_models() expect (`name` + `description`)."""
    local = get_provider("local") or {"models": []}
    out = []
    for m in local["models"]:
        out.append({
            "name": m["id"],
            "description": m["label"],
            "dimensions": m.get("dimensions"),
            "size_mb": m.get("size_mb"),
            "language": m.get("language"),
            "backends": list(m.get("backends", [])),
        })
    return out

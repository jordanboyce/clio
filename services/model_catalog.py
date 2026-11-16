"""One capability record per model, built on the server.

``/api/ai/models`` answers with a record per model instead of a bare id, so a
picker can tell a chat model from an embedding, speech or image model and
show what it can do. Every record is filled from the best source available,
in this order:

1. **What the provider reports.** Each provider class in
   services/ai_service.py turns its own model API into partial records
   (``AIProvider.list_model_records``): Ollama's capabilities list,
   Anthropic's ``capabilities``/``max_input_tokens``, Gemini's
   ``supportedGenerationMethods``, xAI's modalities, OpenRouter's
   architecture and supported parameters, Bedrock's modalities, and the
   extra fields some OpenAI-compatible servers add to ``/models``.
2. **This table** of name patterns, for what the API does not say (OpenAI's
   ``/models`` gives only an id, so its embedding and speech models are known
   here by name).
3. **Unknown.** Fields nobody knows stay ``None``; nothing is guessed silently.

A record::

    {
      "id": "claude-sonnet-5-5",
      "label": "Claude Sonnet 5.5",
      "kinds": ["chat"],            # chat, embedding, image, speech, transcription,
                                    # moderation, rerank, video, realtime, other; [] = not known
      "vision": True,               # accepts image input; None = not known
      "tools": True,                # native tool calling; None = not known
      "context_tokens": 1000000,    # context window; None = not known
      "embedding_dimensions": None, # embedding models: vector size
      "max_input_tokens": 1000000,  # most tokens one request (or one embedding input) may hold
      "source": "provider",         # where ``kinds`` came from: provider | pattern | unknown
      "pattern_fields": ["tools"],  # every field filled from this table rather than the provider
      "recommended": True,          # in this provider's recommendations (and offered by the key)
      "roles": ["quality", "vision"],
    }

This file is also the one place model names live: the recommendations and
defaults (``default_model``, ``fast_model``, ``vision_model``) the server uses
when the caller names no model, and the frontend shows. Keep it small, and
when a provider ships a new generation, change the names here only.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from typing import Callable, Iterable, List, Optional

KINDS = (
    "chat", "embedding", "image", "speech", "transcription",
    "moderation", "rerank", "video", "realtime", "other",
)

# Fields a provider or the table can fill, in the order a record lists them.
CAPABILITY_FIELDS = (
    "kinds", "vision", "tools", "context_tokens",
    "embedding_dimensions", "max_input_tokens",
)


def _p(pattern: str, **attrs) -> tuple:
    return (re.compile(pattern, re.IGNORECASE), attrs)


# Patterns shared by OpenAI and the hosts that serve OpenAI's models under an
# ``openai/`` namespace. First match wins, so the specific ones come first.
_OPENAI_PATTERNS = [
    _p(r"^text-embedding-3-small", kinds=["embedding"], embedding_dimensions=1536, max_input_tokens=8191),
    _p(r"^text-embedding-3-large", kinds=["embedding"], embedding_dimensions=3072, max_input_tokens=8191),
    _p(r"^text-embedding-ada-002", kinds=["embedding"], embedding_dimensions=1536, max_input_tokens=8191),
    _p(r"embedding", kinds=["embedding"]),
    _p(r"(^tts-|-tts\b|-tts-)", kinds=["speech"]),
    _p(r"(^whisper|transcribe)", kinds=["transcription"]),
    _p(r"(^dall-e|^gpt-image|^chatgpt-image)", kinds=["image"]),
    _p(r"^sora", kinds=["video"]),
    _p(r"moderation", kinds=["moderation"]),
    _p(r"(realtime|-audio)", kinds=["realtime"]),
    _p(r"^(babbage|davinci|ada|curie)", kinds=["other"]),
    _p(r"^gpt-3\.5", kinds=["chat"], vision=False, tools=True),
    _p(r"^(gpt-4o|gpt-4\.1|gpt-4\.5|gpt-5|gpt-6|o1|o3|o4)", kinds=["chat"], vision=True, tools=True),
    _p(r"^(gpt-|chatgpt-|o\d)", kinds=["chat"]),
]

_OLLAMA_EMBED_HINT = r"(embed|minilm|bge|(^|[-_/:])e5([-_.:]|$))"
_OLLAMA_VISION_HINT = r"(llava|vl\b|-vl|vl:|vision|minicpm-v|moondream|bakllava|cogvlm|internvl|clip)"

CATALOG: dict = {
    "anthropic": {
        "id_pattern": r"^claude-[a-z0-9-]+$",
        "default_model": "claude-sonnet-5-5",
        "fast_model": "claude-haiku-5-5",
        "vision_model": "claude-sonnet-5-5",
        "recommended": [
            ("claude-sonnet-5-5", "Claude Sonnet 5.5", "quality"),
            ("claude-opus-5-5", "Claude Opus 5.5", "best"),
            ("claude-haiku-5-5", "Claude Haiku 5.5", "fast"),
        ],
        # The Models API lists only Messages-API models. It reports image
        # input but not tool use, which every Claude 3+ model has.
        "patterns": [
            _p(r"^claude-(2|instant)", kinds=["chat"], vision=False, tools=False),
            _p(r"^claude-", kinds=["chat"], vision=True, tools=True),
        ],
    },
    "openai": {
        "id_pattern": r"^[a-z0-9][a-z0-9.-]*$",
        "default_model": "gpt-6.1-sol",
        "fast_model": "gpt-6-luna",
        "vision_model": "gpt-6.1-sol",
        "recommended": [
            ("gpt-6.1-sol", "GPT-6.1 Sol", "quality"),
            ("gpt-6-astra", "GPT-6 Astra", "best"),
            ("gpt-6-luna", "GPT-6 Luna", "fast"),
        ],
        "patterns": _OPENAI_PATTERNS,
    },
    "google": {
        "id_pattern": r"^gemini-[a-z0-9.-]+$",
        "default_model": "gemini-3.8-flash",
        "fast_model": "gemini-3.5-flash-lite",
        "vision_model": "gemini-3.8-flash",
        "recommended": [
            ("gemini-3.8-flash", "Gemini 3.8 Flash", "quality"),
            ("gemini-3.1-pro-preview", "Gemini 3.1 Pro (preview)", "best"),
            ("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite", "fast"),
        ],
        "patterns": [
            _p(r"^gemini-embedding-001", kinds=["embedding"], embedding_dimensions=3072),
            _p(r"embedding", kinds=["embedding"]),
            _p(r"^imagen", kinds=["image"]),
            _p(r"^veo", kinds=["video"]),
            _p(r"-tts", kinds=["speech"]),
            _p(r"(-live|native-audio)", kinds=["realtime"]),
            _p(r"^aqa", kinds=["other"]),
            _p(r"^gemini-", kinds=["chat"], vision=True, tools=True),
            _p(r"^gemma-[3-9]", kinds=["chat"], vision=True),
            _p(r"^gemma-", kinds=["chat"]),
        ],
    },
    "grok": {
        "id_pattern": r"^grok-[a-z0-9.-]+$",
        "default_model": "grok-4.7",
        "fast_model": "grok-4.20-0309-non-reasoning",
        "vision_model": "grok-4.7",
        "recommended": [
            ("grok-4.7", "Grok 4.7", "quality"),
            ("grok-4.20-0309-non-reasoning", "Grok 4.20 (non-reasoning)", "fast"),
        ],
        # xAI reports modalities on /v1/language-models; the table only
        # knows that Grok chat models call tools.
        "patterns": [
            _p(r"^grok-imagine", kinds=["image"]),
            _p(r"embed", kinds=["embedding"]),
            _p(r"^grok-4", kinds=["chat"], vision=True, tools=True),
            _p(r"^grok-", kinds=["chat"], tools=True),
        ],
    },
    "openrouter": {
        "id_pattern": r"^[a-z0-9-]+/[A-Za-z0-9.:-]+$",
        "default_model": "anthropic/claude-sonnet-5.5",
        "fast_model": "anthropic/claude-haiku-5.5",
        "vision_model": "anthropic/claude-sonnet-5.5",
        "recommended": [
            ("anthropic/claude-sonnet-5.5", "Claude Sonnet 5.5", "quality"),
            ("anthropic/claude-opus-5.5", "Claude Opus 5.5", "best"),
            ("anthropic/claude-haiku-5.5", "Claude Haiku 5.5", "fast"),
        ],
        # OpenRouter reports modalities, context and tool support for every
        # model, so the table is only the fallback when it cannot be reached.
        "patterns": [
            _p(r"^anthropic/claude-", kinds=["chat"], vision=True, tools=True),
            _p(r"^openai/", namespaced=True),
            _p(r"embed", kinds=["embedding"]),
        ],
    },
    "github": {
        # GitHub Models was retired on 2026-07-30; the provider stays until it
        # is removed, so these are the names it last served.
        "id_pattern": r"^[a-z0-9-]+/[A-Za-z0-9.:-]+$",
        "default_model": "openai/gpt-4o",
        "fast_model": "openai/gpt-4o-mini",
        "vision_model": "openai/gpt-4o",
        "recommended": [
            ("openai/gpt-4o", "GPT-4o", "quality"),
            ("openai/gpt-4o-mini", "GPT-4o Mini", "fast"),
        ],
        "patterns": [
            _p(r"^openai/", namespaced=True),
            _p(r"embed", kinds=["embedding"]),
        ],
    },
    "ollama_cloud": {
        "id_pattern": r"^[a-z0-9][a-z0-9._/-]*(:[a-z0-9._-]+)?$",
        "default_model": "gpt-oss:120b",
        "fast_model": "gpt-oss:20b",
        "vision_model": "gemma4:31b",
        "recommended": [
            ("gpt-oss:120b", "GPT-OSS 120B", "quality"),
            ("gpt-oss:20b", "GPT-OSS 20B", "fast"),
            ("gemma4:31b", "Gemma 4 31B (free tier)", "vision"),
        ],
        "patterns": [
            _p(r"^gpt-oss", kinds=["chat"], vision=False, tools=True),
            _p(r"^gemma4", kinds=["chat"], vision=True, tools=True),
            _p(_OLLAMA_EMBED_HINT, kinds=["embedding"]),
            _p(_OLLAMA_VISION_HINT, kinds=["chat"], vision=True),
        ],
    },
    "ollama": {
        # Local models are whatever was pulled, so there is nothing to
        # recommend by name: the default is picked from what is installed
        # (see pick_defaults). ``default_model`` is the name used only when a
        # request names no model and nothing better is known.
        "id_pattern": r"^[a-z0-9][a-z0-9._/-]*(:[a-z0-9._-]+)?$",
        "default_model": "llama3.2",
        "fast_model": "llama3.2",
        "vision_model": "gemma3",
        "suggested_embedding": "nomic-embed-text",
        "suggested_vision": "gemma3",
        "recommended": [],
        # Fallback for Ollama older than 0.6, whose /api/show has no
        # capabilities list: the names the old code guessed from.
        "patterns": [
            _p(_OLLAMA_EMBED_HINT, kinds=["embedding"]),
            _p(_OLLAMA_VISION_HINT, kinds=["chat"], vision=True),
        ],
    },
    "bedrock": {
        "id_pattern": r"^((global|us|eu|apac|jp|au)\.)?[a-z0-9-]+\.[a-z0-9.:-]+$",
        "default_model": "anthropic.claude-sonnet-5-5",
        "fast_model": "anthropic.claude-haiku-5-5",
        "vision_model": "anthropic.claude-sonnet-5-5",
        "recommended": [
            ("anthropic.claude-sonnet-5-5", "Claude Sonnet 5.5", "quality"),
            ("anthropic.claude-opus-5-5", "Claude Opus 5.5", "best"),
            ("anthropic.claude-haiku-5-5", "Claude Haiku 5.5", "fast"),
        ],
        "patterns": [
            _p(r"^((global|us|eu|apac|jp|au)\.)?anthropic\.claude", kinds=["chat"], vision=True, tools=True),
            _p(r"embed", kinds=["embedding"]),
            _p(r"(image|canvas|stable-diffusion|sd3)", kinds=["image"]),
        ],
    },
    "openai_compatible": {
        # Any server at all: nothing to recommend, and only the broadest name
        # patterns. Everything here is reported as "pattern".
        "id_pattern": None,
        "default_model": None,
        "fast_model": None,
        "vision_model": None,
        "recommended": [],
        "patterns": [
            _p(r"rerank", kinds=["rerank"]),
            _p(r"(embed|(^|[-_/])bge-|(^|[-_/])e5-|minilm)", kinds=["embedding"]),
            _p(r"whisper|transcribe", kinds=["transcription"]),
            _p(r"(^|[-_/])tts", kinds=["speech"]),
            _p(r"(llava|-vl\b|-vl-|vision)", kinds=["chat"], vision=True),
        ],
    },
}


def provider_entry(provider: str) -> dict:
    """The table entry for a provider; unknown providers get the generic one."""
    return CATALOG.get(provider) or CATALOG["openai_compatible"]


def default_model(provider: str) -> Optional[str]:
    return provider_entry(provider).get("default_model")


def fast_model(provider: str) -> Optional[str]:
    return provider_entry(provider).get("fast_model") or default_model(provider)


def vision_model(provider: str) -> Optional[str]:
    return provider_entry(provider).get("vision_model")


def suggested(provider: str, what: str) -> Optional[str]:
    """A model worth pulling for a job (``embedding`` or ``vision``), e.g. for
    an "ollama pull …" hint."""
    return provider_entry(provider).get(f"suggested_{what}")


def empty_record(model_id: str, label: Optional[str] = None) -> dict:
    return {
        "id": model_id,
        "label": label or model_id,
        "kinds": [],
        "vision": None,
        "tools": None,
        "context_tokens": None,
        "embedding_dimensions": None,
        "max_input_tokens": None,
        "source": "unknown",
        "pattern_fields": [],
        "recommended": False,
        "roles": [],
    }


def pattern_facts(provider: str, model_id: str) -> dict:
    """What the table says about a model id: ``{}`` when nothing matches."""
    for regex, attrs in provider_entry(provider)["patterns"]:
        if regex.search(model_id):
            if attrs.get("namespaced"):
                # "openai/gpt-…" on a host that re-serves OpenAI's models.
                return pattern_facts("openai", model_id.split("/", 1)[1])
            return dict(attrs)
    return {}


def _int_or_none(value) -> Optional[int]:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def complete_record(provider: str, raw: dict) -> dict:
    """One full record from a provider's partial one plus the table.

    ``raw`` holds only what the provider said (any of CAPABILITY_FIELDS, plus
    ``id`` and ``label``). It may carry ``pattern_fields`` of its own for
    facts the provider class itself guessed (old Ollama's model families).
    """
    record = empty_record(str(raw["id"]), raw.get("label"))
    pattern_fields = list(raw.get("pattern_fields") or [])
    for field in CAPABILITY_FIELDS:
        value = raw.get(field)
        if field == "kinds":
            value = [k for k in (value or []) if k in KINDS]
            value = sorted(set(value), key=KINDS.index) or None
        elif field in ("context_tokens", "embedding_dimensions", "max_input_tokens"):
            value = _int_or_none(value)
        elif value is not None:
            value = bool(value)
        if value is not None:
            record[field] = value

    facts = pattern_facts(provider, record["id"])
    for field in CAPABILITY_FIELDS:
        if field in facts and (record[field] is None or record[field] == []):
            record[field] = list(facts[field]) if field == "kinds" else facts[field]
            if field not in pattern_fields:
                pattern_fields.append(field)

    if not record["kinds"]:
        record["source"] = "unknown"
    elif "kinds" in pattern_fields:
        record["source"] = "pattern"
    else:
        record["source"] = "provider"
    record["pattern_fields"] = [f for f in CAPABILITY_FIELDS if f in pattern_fields]
    return record


def _matches(recommended_id: str, live_id: str) -> bool:
    """A live id is the recommended one, or a dated/tagged snapshot of it
    (``claude-x-20251001``, ``llama3.2:latest``)."""
    if live_id == recommended_id:
        return True
    if live_id == f"{recommended_id}:latest":
        return True
    rest = live_id[len(recommended_id):] if live_id.startswith(recommended_id) else ""
    return bool(re.fullmatch(r"-\d{4}-?\d{2}-?\d{2}|-v\d+(:\d+)?|-\d{3,4}", rest))


def build_records(provider: str, raw_records: Optional[Iterable[dict]]) -> List[dict]:
    """Records for everything a provider listed, recommendations first.

    ``raw_records`` is None when the provider could not be asked (offline, no
    key, unreachable): the answer is then the table's recommendations, every
    one marked as coming from the table, rather than nothing.
    """
    entry = provider_entry(provider)
    recs = entry.get("recommended") or []
    if raw_records is None:
        out = []
        for model_id, label, role in recs:
            record = complete_record(provider, {"id": model_id, "label": label})
            out.append(record)
        listed_ids = None
    else:
        out = []
        seen = set()
        for raw in raw_records:
            if not raw or not raw.get("id") or raw["id"] in seen:
                continue
            seen.add(raw["id"])
            out.append(complete_record(provider, raw))
        listed_ids = seen

    order = []
    for model_id, label, role in recs:
        match = next((r for r in out if _matches(model_id, r["id"])), None)
        if match is None:
            continue  # the key does not offer it: never recommend what cannot be used
        match["recommended"] = True
        if role not in match["roles"]:
            match["roles"].append(role)
        if match["label"] == match["id"] or listed_ids is None:
            match["label"] = label
        if match not in order:
            order.append(match)
    for record in out:
        if record["id"] == vision_model(provider) and record["recommended"] and "vision" not in record["roles"]:
            record["roles"].append("vision")
    return order + [r for r in out if r not in order]


def _is_chat(record: dict) -> bool:
    return "chat" in record["kinds"]


def pick_defaults(provider: str, records: List[dict], listed: bool) -> dict:
    """The models a caller gets when it names none: ``model`` (answers),
    ``fast`` (quick one-shot calls) and ``vision`` (OCR). Only ids the
    provider listed, when it listed any; ``None`` when nothing fits."""
    ids = [r["id"] for r in records]

    def find(want: Optional[str]) -> Optional[str]:
        if not want:
            return None
        if not listed:
            return want
        return next((i for i in ids if _matches(want, i)), None)

    chat = [r for r in records if _is_chat(r)]
    model = find(default_model(provider))
    if model is None and listed:
        model = next((r["id"] for r in chat if r["recommended"]), None) or (chat[0]["id"] if chat else None)
    fast = find(fast_model(provider)) or model

    vision = find(vision_model(provider))
    if vision is not None and listed:
        rec = next(r for r in records if r["id"] == vision)
        if rec["vision"] is False:
            vision = None
    if vision is None and listed:
        vision = next((r["id"] for r in chat if r["vision"] is True and r["source"] == "provider"), None) \
            or next((r["id"] for r in chat if r["vision"] is True), None)
    return {"model": model, "fast": fast, "vision": vision}


# ── The answer /api/ai/models gives, cached per provider and key ──────────

CACHE_SECONDS = 300.0
_CACHE_MAX = 64
_cache: dict = {}
_cache_lock = threading.Lock()


def cache_key(provider: str, api_key: Optional[str], base_url: Optional[str]) -> tuple:
    """Who a cached listing belongs to. The key is kept only as a digest, so
    the cache never holds it."""
    digest = hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()[:24]
    return (provider, (base_url or "").rstrip("/"), digest)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def catalog(provider: str, list_records: Optional[Callable[[], Optional[list]]],
            key: Optional[tuple] = None, refresh: bool = False) -> dict:
    """Records, defaults and the old id list for one provider.

    ``list_records`` asks the provider (``AIProvider.list_model_records``);
    None means it cannot be asked at all. A listing that fails answers from
    the table instead, with the error, and is not cached; a listing that
    works is cached for CACHE_SECONDS under ``key`` unless ``refresh``.

    ``models`` is the bare id list the endpoint returned before records, and
    keeps the old meaning: what the provider listed, [] when it listed nothing.
    """
    now = time.monotonic()
    if key is not None and not refresh:
        with _cache_lock:
            hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return {**hit[1], "cached": True}

    error = None
    raw = None
    if list_records is not None:
        try:
            raw = list_records()
        except Exception as e:  # unreachable, blocked, bad key: answer from the table
            error = str(e) or e.__class__.__name__
            raw = None
    listed = raw is not None
    records = build_records(provider, raw)
    result = {
        "models": [r["id"] for r in records] if listed else [],
        "records": records,
        "defaults": pick_defaults(provider, records, listed),
        # Models worth installing for a job, for "ollama pull …" hints.
        "suggested": {"embedding": suggested(provider, "embedding"), "vision": suggested(provider, "vision")},
        "listed": listed,
        "error": error,
        "cached": False,
    }
    if listed and key is not None:
        with _cache_lock:
            if len(_cache) >= _CACHE_MAX:
                _cache.pop(min(_cache, key=lambda k: _cache[k][0]), None)
            _cache[key] = (now, result)
    return result

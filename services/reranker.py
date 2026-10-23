"""Local cross-encoder reranker for retrieval results.

A bi-encoder (the embedding model) measures semantic nearness by comparing two
independently-computed vectors. A cross-encoder instead feeds the (query, chunk)
pair through the model *together*, so it can attend across both texts and judge
true relevance far more accurately. We run it as a second-stage reranker: pull a
wider candidate pool from BM25 / vector / hybrid search, then reorder by
cross-encoder score and keep the top_k.

Why this lives next to the embedder rather than behind the LLM reranker in
`ai_service`:

  - It is **local**, so it costs no API tokens and adds no network round-trip.
  - Nothing leaves the machine — no data is sent to an external model.
  - It runs for **every** caller, including the MCP tools, which never pass an
    `ai_service` and therefore never triggered the LLM-judgment reranker.

Two backends serve the same models, mirroring the embedder:

  - **fastembed** (the default): the ONNX export on onnxruntime, which the core
    install already has for embeddings. No PyTorch, ~80 MB for the default
    model, and it is baked into the Docker image next to the embedding model,
    so a stock deployment reranks out of the box.
  - **sentence-transformers**: the PyTorch CrossEncoder, for a model fastembed
    has no export of, or when torch is what is installed.

The model is loaded lazily on first use and the whole thing degrades silently:
if no backend is installed, the model is not on disk and cannot be fetched, or
scoring throws, callers get their input order back (capped to top_k) instead
of an error. ``ENABLE_RERANKER`` unset means automatic: on exactly when the
model is already on disk for a usable backend. The embedding warm-up at
startup fetches it once when downloads are allowed (`prefetch`), so a
connected install becomes "on" after its first start without anyone choosing.
"""

from __future__ import annotations

import functools
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

RERANKER_BACKENDS = ("auto", "fastembed", "sentence-transformers")

# Clio / HuggingFace model name -> the fastembed id of its ONNX export. The
# weights are the same; only the packaging differs. A name fastembed knows
# verbatim is accepted as-is (see fastembed_reranker_id).
_FASTEMBED_RERANKERS: Dict[str, str] = {
    "cross-encoder/ms-marco-MiniLM-L-6-v2": "Xenova/ms-marco-MiniLM-L-6-v2",
    "cross-encoder/ms-marco-MiniLM-L-12-v2": "Xenova/ms-marco-MiniLM-L-12-v2",
}


def _default_text_getter(item: Any) -> str:
    """Pull the rerankable text off a SearchResult-like object or dict."""
    if isinstance(item, dict):
        return item.get("text_snippet") or item.get("text") or item.get("excerpt") or ""
    return (
        getattr(item, "text_snippet", None)
        or getattr(item, "text", None)
        or getattr(item, "excerpt", None)
        or ""
    )


# ── Backend resolution ──────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def _fastembed_catalog() -> Dict[str, dict]:
    """fastembed's cross-encoder catalog keyed by id ({} when absent).

    Computed once per process: listing it imports fastembed and onnxruntime,
    which the module-level imports deliberately avoid.
    """
    from services.embedder import fastembed_importable

    if not fastembed_importable():
        return {}
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        return {m["model"]: m for m in TextCrossEncoder.list_supported_models()}
    except Exception as e:  # noqa: BLE001 - an old fastembed without rerankers, a broken wheel
        logger.warning(f"fastembed is installed but has no usable reranker support: {e}")
        return {}


def fastembed_reranker_id(model_name: str) -> Optional[str]:
    """The fastembed id serving ``model_name``, or None when fastembed is
    absent or has no export of that model."""
    if not model_name:
        return None
    catalog = _fastembed_catalog()
    if not catalog:
        return None
    candidate = _FASTEMBED_RERANKERS.get(model_name, model_name)
    if candidate in catalog:
        return candidate
    lowered = {k.lower(): k for k in catalog}
    return lowered.get(candidate.lower())


def resolve_backend(model_name: str, backend: Optional[str] = None) -> Optional[str]:
    """Which in-process backend would load ``model_name``, or None when
    nothing installed can.

    ``backend`` is RERANKER_BACKEND (or an override):
      auto                  fastembed when it is importable and knows the
                            model, else sentence-transformers when importable
      fastembed             only fastembed (None when it has no export)
      sentence-transformers only the PyTorch CrossEncoder
    """
    from config import settings
    from services.embedder import fastembed_importable, sentence_transformers_importable

    choice = (backend or getattr(settings, "reranker_backend", "auto") or "auto").strip().lower()
    if choice not in RERANKER_BACKENDS:
        logger.warning(f"RERANKER_BACKEND={choice!r} is not one of {RERANKER_BACKENDS}; using auto")
        choice = "auto"

    fe_ok = fastembed_importable() and fastembed_reranker_id(model_name) is not None
    st_ok = sentence_transformers_importable()
    if choice == "fastembed":
        return "fastembed" if fe_ok else None
    if choice == "sentence-transformers":
        return "sentence-transformers" if st_ok else None
    if fe_ok:
        return "fastembed"
    if st_ok:
        return "sentence-transformers"
    return None


# ── On-disk presence (never downloads) ──────────────────────────────────────

def _fastembed_model_dir(model_name: str, cache_dir: Path) -> Optional[Path]:
    from services.embedder import _hf_repo_folder

    fe_id = fastembed_reranker_id(model_name)
    if fe_id is None:
        return None
    desc = _fastembed_catalog().get(fe_id) or {}
    hf = (desc.get("sources") or {}).get("hf")
    if not hf:
        return Path(cache_dir) / f"fast-{fe_id.split('/')[-1]}"
    return Path(cache_dir) / _hf_repo_folder(hf)


def fastembed_reranker_cached(model_name: str, cache_dir: Path) -> bool:
    """Whether fastembed could load the reranker from ``cache_dir`` with no
    network: the hub-style folder is complete when ``files_metadata.json``
    sits next to a snapshot holding the ONNX file."""
    fe_id = fastembed_reranker_id(model_name)
    if fe_id is None:
        return False
    desc = _fastembed_catalog().get(fe_id) or {}
    model_file = desc.get("model_file") or "onnx/model.onnx"
    model_dir = _fastembed_model_dir(model_name, cache_dir)
    try:
        if model_dir is not None and (model_dir / "files_metadata.json").is_file():
            if any((model_dir / "snapshots").glob(f"*/{model_file}")):
                return True
        legacy = Path(cache_dir) / f"fast-{fe_id.split('/')[-1]}"
        if legacy.is_dir() and any(legacy.iterdir()):
            return True
    except OSError:
        return False
    return False


def fastembed_reranker_cached_in(model_name: str) -> Optional[Path]:
    """The first cache directory (image bake, FASTEMBED_CACHE_PATH, data
    dir) holding a complete copy of the reranker."""
    from services.embedder import fastembed_cache_dirs

    for d in fastembed_cache_dirs():
        if fastembed_reranker_cached(model_name, d):
            return d
    return None


def _sentence_transformers_cached(model_name: str) -> bool:
    """True when the PyTorch CrossEncoder can load with no network at all."""
    import importlib.util

    try:
        if importlib.util.find_spec("sentence_transformers") is None:
            return False
        from huggingface_hub import snapshot_download

        snapshot_download(model_name, local_files_only=True)
        return True
    except Exception:  # noqa: BLE001 - not cached: simply off
        return False


def model_cached(model_name: str, backend: Optional[str] = None) -> bool:
    """Whether the reranker is on disk for the backend that would load it.
    A pure filesystem lookup: never downloads, never loads a model."""
    resolved = resolve_backend(model_name, backend)
    if resolved == "fastembed":
        return fastembed_reranker_cached_in(model_name) is not None
    if resolved == "sentence-transformers":
        return _sentence_transformers_cached(model_name)
    return False


# model name -> (checked_at, usable). A missing model can be seeded while the
# server runs (an offline bundle is copied in, the warm-up fetches it), so a
# "no" is rechecked.
_auto_cache: dict = {}
_AUTO_RECHECK_SECONDS = 60.0


def _locally_available(model_name: str) -> bool:
    """True when the reranker can load with no network access at all."""
    import time

    now = time.monotonic()
    cached = _auto_cache.get(model_name)
    if cached and (cached[1] or now - cached[0] < _AUTO_RECHECK_SECONDS):
        return cached[1]
    usable = model_cached(model_name)
    _auto_cache[model_name] = (now, usable)
    return usable


def reranker_enabled() -> bool:
    """Resolve ``ENABLE_RERANKER``: explicit true/false wins, unset is automatic."""
    from config import settings

    configured = getattr(settings, "enable_reranker", None)
    if configured is None:
        return _locally_available(settings.reranker_model)
    return bool(configured)


# ── Fetching (the one place that may download) ──────────────────────────────

def prefetch(model_name: Optional[str] = None, backend: Optional[str] = None) -> str:
    """Download the reranker for the backend that would load it, into the
    cache the runtime reads (fastembed: the data directory, or the image
    bake path when CLIO_MODEL_BAKE_DIR names it; sentence-transformers: the
    HF cache). Returns a one-line description; raises when it cannot.

    Called by the embedding warm-up after the embedding model is ready, by
    scripts/prefetch_offline_models.py, and by the Docker build.
    """
    import os

    from config import settings

    name = model_name or settings.reranker_model
    resolved = resolve_backend(name, backend)
    if resolved is None:
        raise RuntimeError(
            f"no reranker backend can serve {name!r}: install fastembed (recommended) "
            "or sentence-transformers, or pick a model fastembed has an export of."
        )
    if resolved == "fastembed":
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        from services.embedder import fastembed_data_cache_dir

        target = Path(os.environ.get("CLIO_MODEL_BAKE_DIR") or fastembed_data_cache_dir())
        target.mkdir(parents=True, exist_ok=True)
        fe_id = fastembed_reranker_id(name)
        TextCrossEncoder(fe_id, cache_dir=str(target))
        _auto_cache.pop(name, None)
        return f"reranker '{fe_id}' (fastembed) -> {target}"

    from sentence_transformers import CrossEncoder

    CrossEncoder(name)
    _auto_cache.pop(name, None)
    return f"reranker '{name}' (sentence-transformers)"


def warm_in_background() -> Optional[threading.Thread]:
    """Fetch the reranker once, off the request path, when that is allowed.

    Only under the automatic setting (``ENABLE_RERANKER`` unset), only when
    the model is not already on disk, and never when downloads are forbidden
    (OFFLINE_MODE / HF_HUB_OFFLINE) — an air-gapped install stays exactly as
    it was. Best effort: a failed fetch is a log line, and search carries on
    in retrieval order until the model is seeded another way.
    """
    from config import settings
    from services.embedder import hub_offline

    if getattr(settings, "enable_reranker", None) is not None:
        return None
    if hub_offline() or resolve_backend(settings.reranker_model) is None:
        return None
    if model_cached(settings.reranker_model):
        return None

    def _run():
        try:
            logger.info(f"Fetching the reranker {settings.reranker_model} (one-time)...")
            logger.info(f"Reranker ready: {prefetch()}")
        except Exception as e:  # noqa: BLE001 - search works without it
            logger.warning(
                f"Could not fetch the reranker {settings.reranker_model}; results keep "
                f"retrieval order until it is seeded (see docs/AIRGAP.md). {e}"
            )

    thread = threading.Thread(target=_run, name="reranker-warmup", daemon=True)
    thread.start()
    return thread


# ── The reranker ────────────────────────────────────────────────────────────

class CrossEncoderReranker:
    """Lazily-loaded cross-encoder reranker with graceful degradation."""

    def __init__(self, model_name: str, backend: Optional[str] = None):
        self.model_name = model_name
        self.backend: Optional[str] = resolve_backend(model_name, backend)
        self._model = None
        self._load_failed = False
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        """True if the model is loaded or can plausibly still be loaded."""
        return not self._load_failed and self.backend is not None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self):
        if self.backend == "fastembed":
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            from services.embedder import fastembed_data_cache_dir

            fe_id = fastembed_reranker_id(self.model_name)
            cache_dir = fastembed_reranker_cached_in(self.model_name) or fastembed_data_cache_dir()
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            return TextCrossEncoder(fe_id, cache_dir=str(cache_dir))
        if self.backend == "sentence-transformers":
            from sentence_transformers import CrossEncoder

            return CrossEncoder(self.model_name)
        raise RuntimeError("no reranker backend is installed (fastembed or sentence-transformers)")

    def _ensure_model(self):
        """Load the cross-encoder on first use. Thread-safe; loads at most once."""
        if self._model is not None or self._load_failed:
            return self._model
        with self._lock:
            if self._model is not None or self._load_failed:
                return self._model
            try:
                logger.info(f"Loading cross-encoder reranker: {self.model_name} ({self.backend})")
                self._model = self._load()
                logger.info(f"Cross-encoder reranker ready: {self.model_name} ({self.backend})")
            except Exception as e:  # noqa: BLE001 - any failure must degrade, not crash
                self._load_failed = True
                logger.warning(
                    "Cross-encoder reranker unavailable (%s, %s); falling back to "
                    "retrieval order. %s",
                    self.model_name,
                    self.backend,
                    e,
                )
        return self._model

    # Pairs are scored in small batches sorted by length: every batch is
    # padded to its longest member, so mixing a 400-token table chunk with
    # 80-token prose makes the prose pay for the table. Sorting first cut a
    # 40-candidate rerank from 2.7 s to 1.6 s on a number-heavy corpus.
    _BATCH = 8

    def _scores(self, model, query: str, texts: List[str]) -> List[float]:
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        ordered = [texts[i] for i in order]
        if self.backend == "fastembed":
            raw = [float(s) for s in model.rerank(query, ordered, batch_size=self._BATCH)]
        else:
            raw = [float(s) for s in model.predict([[query, t] for t in ordered], batch_size=self._BATCH)]
        scores = [0.0] * len(texts)
        for pos, i in enumerate(order):
            scores[i] = raw[pos]
        return scores

    def rerank(
        self,
        query: str,
        items: Sequence[Any],
        top_k: int,
        text_getter: Optional[Callable[[Any], str]] = None,
    ) -> List[Any]:
        """Reorder `items` by cross-encoder relevance to `query`, keep top_k.

        Returns the input list (capped to top_k, original order) unchanged if
        the model is unavailable or anything goes wrong — never raises.
        """
        return self.rerank_with_status(query, items, top_k, text_getter)[0]

    def rerank_with_status(
        self,
        query: str,
        items: Sequence[Any],
        top_k: int,
        text_getter: Optional[Callable[[Any], str]] = None,
    ) -> tuple[List[Any], bool]:
        """`rerank`, plus whether the cross-encoder actually ran.

        False means the caller got retrieval order: the model is unavailable,
        scoring failed, or every inference slot was busy for longer than
        RERANK_QUEUE_TIMEOUT_SECONDS. The last case is the load-shedding
        contract: a cross-encoder pass is pure CPU, so under a burst of
        agent calls the honest choice is a slightly less precise ordering
        now rather than every search queueing behind the reranker.
        """
        items = list(items)
        if not items or top_k <= 0:
            return items[: max(0, top_k)], False
        if len(items) == 1:
            return items[:top_k], False

        model = self._ensure_model()
        if model is None:
            return items[:top_k], False

        gate = _inference_gate()
        if gate is not None and not gate.acquire(timeout=_queue_timeout()):
            logger.info("Reranker busy (%d slots); returning retrieval order", _gate_size)
            return items[:top_k], False
        getter = text_getter or _default_text_getter
        try:
            texts = [getter(it) or "" for it in items]
            scores = self._scores(model, query, texts)
            order = sorted(range(len(items)), key=lambda i: scores[i], reverse=True)
            ranked = [items[i] for i in order[:top_k]]
            logger.info(
                "Cross-encoder reranked %d candidates -> top %d",
                len(items),
                len(ranked),
            )
            return ranked, True
        except Exception as e:  # noqa: BLE001
            logger.warning("Cross-encoder rerank failed, using retrieval order: %s", e)
            return items[:top_k], False
        finally:
            if gate is not None:
                gate.release()


# Concurrent cross-encoder passes. onnxruntime and torch each spread one
# pass over every core, so N passes at once mostly fight each other: two in
# flight keep the CPU busy without the thrash. Rebuilt when the setting
# changes (tests, config reload).
_gate: Optional[threading.BoundedSemaphore] = None
_gate_size = 0
_gate_lock = threading.Lock()


def _inference_gate() -> Optional[threading.BoundedSemaphore]:
    global _gate, _gate_size
    from config import settings

    size = int(getattr(settings, "rerank_max_concurrent", 2) or 0)
    if size <= 0:
        return None
    with _gate_lock:
        if _gate is None or _gate_size != size:
            _gate, _gate_size = threading.BoundedSemaphore(size), size
        return _gate


def _queue_timeout() -> float:
    from config import settings

    return max(0.0, float(getattr(settings, "rerank_queue_timeout_seconds", 1.0)))


_reranker: Optional[CrossEncoderReranker] = None
_reranker_lock = threading.Lock()


def get_reranker() -> Optional[CrossEncoderReranker]:
    """Return the shared reranker if enabled (or automatically available).

    The instance is created once and reused. Returns None when reranking is
    off so callers can cheaply skip the wider candidate fetch.
    """
    from config import settings

    if not reranker_enabled():
        return None

    global _reranker
    if _reranker is None or _reranker.model_name != settings.reranker_model:
        with _reranker_lock:
            if _reranker is None or _reranker.model_name != settings.reranker_model:
                _reranker = CrossEncoderReranker(settings.reranker_model)
    return _reranker


def status() -> Dict[str, Any]:
    """What the reranker is doing right now, for readiness and health checks:
    the configured model, the backend that serves it, whether it is on disk,
    whether it is active, and why not when it is not."""
    from config import settings

    model = settings.reranker_model
    configured = getattr(settings, "enable_reranker", None)
    backend = resolve_backend(model)
    cached = model_cached(model) if backend else False
    # Decided from the fresh disk check, not the 60-second availability
    # cache, so a status read right after a seed tells the truth.
    active = backend is not None and (configured is True or (configured is None and cached))
    if configured is False:
        reason = "disabled by ENABLE_RERANKER=false"
    elif backend is None:
        reason = "no backend installed (fastembed or sentence-transformers)"
    elif not cached and configured is None:
        reason = "model not on disk yet"
    else:
        reason = None
    return {
        "model": model,
        "backend": backend,
        "setting": "auto" if configured is None else ("on" if configured else "off"),
        "cached": cached,
        "active": active,
        "loaded": bool(_reranker and _reranker.loaded),
        "reason": reason,
    }

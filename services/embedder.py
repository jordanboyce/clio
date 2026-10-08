"""Embedding services: local (fastembed or sentence-transformers) or remote.

Every service shares the same duck-typed surface: `model_name`,
`embedding_dim`, `embed_texts(texts, progress_callback=None)`, and
`embed_query(query)`. Batching lives HERE, not in callers — the indexer
passes a progress_callback instead of re-batching on its own.

The "local" provider has two in-process backends:

- fastembed (`FastEmbedService`): the model exported to ONNX, run by
  onnxruntime. No PyTorch, ~100 MB of dependencies instead of ~2 GB, and
  the default for every model it knows.
- sentence-transformers (`EmbeddingService`): the PyTorch runtime, needed
  for models fastembed has no ONNX export of (requirements-torch.txt).

Both load the same weights, so a backend switch never changes the vectors
an index holds — see `embedding_signature`.
"""

import functools
import json
import logging
import time
import os
import threading
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)

# One shared batch size for document embedding. Callers must not re-batch.
EMBED_BATCH_SIZE = 32


# ── Local backends ──────────────────────────────────────────────────────────

FASTEMBED_BACKEND = "fastembed"
ST_BACKEND = "sentence-transformers"
LOCAL_BACKENDS = (FASTEMBED_BACKEND, ST_BACKEND)

INSTALL_FASTEMBED = "pip install fastembed"
INSTALL_TORCH = "pip install -r requirements-torch.txt"

# Clio catalog name → fastembed model id. fastembed names most models by
# their original HF repo; the sentence-transformers ones carry the org that
# a bare name implies. Entries fastembed does not actually ship (checked
# against TextEmbedding.list_supported_models() the first time a backend is
# resolved) are dropped from the effective mapping, so a model listed here
# but absent from the installed fastembed falls through to
# sentence-transformers instead of failing to load.
_FASTEMBED_CANDIDATES: Dict[str, str] = {
    "all-MiniLM-L6-v2": "sentence-transformers/all-MiniLM-L6-v2",
    "all-MiniLM-L12-v2": "sentence-transformers/all-MiniLM-L12-v2",
    "BAAI/bge-small-en-v1.5": "BAAI/bge-small-en-v1.5",
    "BAAI/bge-base-en-v1.5": "BAAI/bge-base-en-v1.5",
    "nomic-ai/nomic-embed-text-v1.5": "nomic-ai/nomic-embed-text-v1.5",
    "jinaai/jina-embeddings-v2-base-code": "jinaai/jina-embeddings-v2-base-code",
    "intfloat/multilingual-e5-small": "intfloat/multilingual-e5-small",
    "paraphrase-multilingual-MiniLM-L12-v2": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "mixedbread-ai/mxbai-embed-large-v1": "mixedbread-ai/mxbai-embed-large-v1",
    "Qwen/Qwen3-Embedding-0.6B": "Qwen/Qwen3-Embedding-0.6B",
}

# Where the Docker image bakes the fastembed model (a fixed image path:
# settings.data_dir is a volume, so a bake into it would be hidden by the
# mount). FastEmbedService looks here first, then in the data directory.
FASTEMBED_IMAGE_CACHE = Path("/opt/clio/models/fastembed")


def fastembed_importable() -> bool:
    """Whether the fastembed package is installed (no import side effects)."""
    from importlib.util import find_spec
    try:
        return find_spec("fastembed") is not None
    except Exception:
        return False


def sentence_transformers_importable() -> bool:
    """Whether sentence-transformers AND torch are installed."""
    from importlib.util import find_spec
    try:
        return find_spec("sentence_transformers") is not None and find_spec("torch") is not None
    except Exception:
        return False


@functools.lru_cache(maxsize=1)
def _fastembed_catalog() -> Dict[str, dict]:
    """fastembed's supported dense text models keyed by id ({} when absent).

    Computed once per process: listing the catalog imports fastembed (and
    onnxruntime), which the module-level import deliberately avoids so this
    file stays importable in a torch-only or dependency-free environment.
    """
    if not fastembed_importable():
        return {}
    try:
        from fastembed import TextEmbedding
        return {m["model"]: m for m in TextEmbedding.list_supported_models()}
    except Exception as e:  # a broken onnxruntime wheel, for instance
        logger.warning(f"fastembed is installed but unusable: {e}")
        return {}


def fastembed_model_id(model_name: str) -> Optional[str]:
    """The fastembed id serving a Clio catalog name, or None when fastembed
    is absent or has no export of that model. A name fastembed knows
    verbatim (e.g. a full HF repo id) is accepted as-is."""
    if not model_name:
        return None
    catalog = _fastembed_catalog()
    if not catalog:
        return None
    candidate = _FASTEMBED_CANDIDATES.get(model_name, model_name)
    if candidate in catalog:
        return candidate
    # fastembed matches ids case-insensitively.
    lowered = {k.lower(): k for k in catalog}
    return lowered.get(candidate.lower())


def fastembed_supports(model_name: str) -> bool:
    return fastembed_model_id(model_name) is not None


def resolve_local_backend(model_name: str, backend: Optional[str] = None) -> str:
    """Pick the in-process backend for a local model.

    ``backend`` is LOCAL_EMBEDDING_BACKEND (or an unsaved override):
      auto                  fastembed when importable and it knows the model,
                            else sentence-transformers when importable
      fastembed             fastembed or an error
      sentence-transformers the PyTorch backend or an error
    The error names the install command for each so the message can be
    shown to an operator as-is.
    """
    from config import settings

    choice = (backend or settings.local_embedding_backend or "auto").strip().lower()
    fe_ok = fastembed_importable()
    st_ok = sentence_transformers_importable()

    if choice == FASTEMBED_BACKEND:
        if not fe_ok:
            raise RuntimeError(
                f"LOCAL_EMBEDDING_BACKEND=fastembed but fastembed is not installed: {INSTALL_FASTEMBED}"
            )
        if not fastembed_supports(model_name):
            raise RuntimeError(
                f"fastembed has no ONNX export of '{model_name}'. Pick a model marked "
                f"fastembed in the catalog, or set LOCAL_EMBEDDING_BACKEND=sentence-transformers "
                f"({INSTALL_TORCH})."
            )
        return FASTEMBED_BACKEND
    if choice == ST_BACKEND:
        if not st_ok:
            raise RuntimeError(
                f"LOCAL_EMBEDDING_BACKEND=sentence-transformers but the PyTorch backend "
                f"is not installed: {INSTALL_TORCH}"
            )
        return ST_BACKEND
    if choice != "auto":
        raise RuntimeError(
            f"LOCAL_EMBEDDING_BACKEND={choice!r} is not one of auto, {FASTEMBED_BACKEND}, {ST_BACKEND}"
        )

    if fe_ok and fastembed_supports(model_name):
        return FASTEMBED_BACKEND
    if st_ok:
        return ST_BACKEND
    if fe_ok:
        raise RuntimeError(
            f"fastembed is installed but has no ONNX export of '{model_name}', and the "
            f"PyTorch backend is not installed. Pick a model marked fastembed in the "
            f"catalog, or install it: {INSTALL_TORCH}"
        )
    raise RuntimeError(
        "No local embedding backend is installed. Install one: "
        f"`{INSTALL_FASTEMBED}` (small, ONNX; recommended) or "
        f"`{INSTALL_TORCH}` (PyTorch; wider model catalog)."
    )


def fastembed_data_cache_dir() -> Path:
    """Where fastembed downloads land: under the data volume, so a model
    fetched once survives container recreation with the data directory."""
    from config import settings
    return Path(settings.data_dir) / "models" / "fastembed"


def fastembed_cache_dirs() -> List[Path]:
    """Every directory a fastembed model may already live in, in lookup
    order: the image bake, an explicit FASTEMBED_CACHE_PATH, the data dir."""
    dirs: List[Path] = [FASTEMBED_IMAGE_CACHE]
    env = os.environ.get("FASTEMBED_CACHE_PATH", "").strip()
    if env:
        dirs.append(Path(env))
    dirs.append(fastembed_data_cache_dir())
    seen, out = set(), []
    for d in dirs:
        key = str(d)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _hf_repo_folder(repo_id: str) -> str:
    try:
        from huggingface_hub.file_download import repo_folder_name
        return repo_folder_name(repo_id=repo_id, repo_type="model")
    except Exception:
        return "models--" + repo_id.replace("/", "--")


def fastembed_model_dir(model_name: str, cache_dir: Path) -> Optional[Path]:
    """The directory fastembed writes a model's files into under cache_dir
    (its hub-style ``models--<org>--<repo>`` folder), or None when fastembed
    does not know the model."""
    fe_id = fastembed_model_id(model_name)
    if fe_id is None:
        return None
    desc = _fastembed_catalog().get(fe_id) or {}
    hf = (desc.get("sources") or {}).get("hf")
    if not hf:
        return Path(cache_dir) / f"fast-{fe_id.split('/')[-1]}"
    return Path(cache_dir) / _hf_repo_folder(hf)


def fastembed_model_cached(model_name: str, cache_dir: Path) -> bool:
    """Whether fastembed could load the model from cache_dir without the hub.

    fastembed downloads into huggingface_hub's cache layout and, once every
    file verified, writes ``files_metadata.json`` next to ``snapshots/`` —
    that file is the completion marker; a directory without it is a
    download in flight or an interrupted one. Older fastembed versions kept
    an extracted ``fast-<model>`` folder instead; a non-empty one still
    loads, so it counts too.
    """
    fe_id = fastembed_model_id(model_name)
    if fe_id is None:
        return False
    desc = _fastembed_catalog().get(fe_id) or {}
    model_file = desc.get("model_file") or "model.onnx"
    model_dir = fastembed_model_dir(model_name, cache_dir)
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


def fastembed_cached_in(model_name: str) -> Optional[Path]:
    """The first cache directory holding a complete copy of the model."""
    for d in fastembed_cache_dirs():
        if fastembed_model_cached(model_name, d):
            return d
    return None


def hf_model_dir(model_name: str) -> Optional[Path]:
    """The huggingface_hub cache folder sentence-transformers fills for a
    model (where a download in flight grows), or None without the hub lib."""
    if os.path.isdir(model_name):
        return Path(model_name)
    try:
        from huggingface_hub import constants
    except Exception:
        return None
    repo = model_name if "/" in model_name else f"sentence-transformers/{model_name}"
    return Path(constants.HF_HUB_CACHE) / _hf_repo_folder(repo)


def hf_model_cached(model_name: str) -> bool:
    """Whether a sentence-transformers model is already in the local HF cache.

    A pure filesystem lookup (no network, no torch load). A bare name like
    ``all-MiniLM-L6-v2`` resolves to the ``sentence-transformers/<name>``
    repo sentence-transformers actually caches under; a name that already
    carries an org is used as-is. A local filesystem path means the model
    ships with the app and is "cached" by construction.
    """
    if not model_name:
        return False
    if os.path.isdir(model_name):
        return True
    try:
        from huggingface_hub import try_to_load_from_cache
    except Exception:  # huggingface_hub absent — cannot probe; don't nag
        return True
    repo = model_name if "/" in model_name else f"sentence-transformers/{model_name}"
    try:
        return try_to_load_from_cache(repo, "config.json") is not None
    except Exception:  # unexpected cache state — default to "available"
        return True


def local_model_cached(model_name: str, backend: Optional[str] = None) -> bool:
    """Whether the local model is on disk for the backend that would load it.

    A pure filesystem lookup so a deployment that skipped baking the model —
    or sits behind a network that blocks huggingface.co — can be told up
    front that the built-in embedding model is missing, instead of
    discovering it mid-index. ``backend`` defaults to the one
    resolve_local_backend() picks; when no backend is installed at all the
    answer is False (nothing could load it).
    """
    if not model_name:
        return False
    if backend is None:
        try:
            backend = resolve_local_backend(model_name)
        except RuntimeError:
            return False
    if backend == FASTEMBED_BACKEND:
        return fastembed_cached_in(model_name) is not None
    return hf_model_cached(model_name)


def local_model_download_dir(model_name: str, backend: Optional[str] = None) -> Optional[Path]:
    """The directory a download of the model grows in, for progress sampling."""
    if backend is None:
        try:
            backend = resolve_local_backend(model_name)
        except RuntimeError:
            return None
    if backend == FASTEMBED_BACKEND:
        return fastembed_model_dir(model_name, fastembed_data_cache_dir())
    return hf_model_dir(model_name)


def local_model_download_bytes(model_name: str, backend: Optional[str] = None) -> Optional[int]:
    """Expected size of the model's download, for a progress percentage.

    fastembed's catalog knows the exact ONNX bundle size; otherwise the
    curated catalog's size_mb (the PyTorch weights) is the estimate.
    """
    if backend == FASTEMBED_BACKEND:
        fe_id = fastembed_model_id(model_name)
        desc = _fastembed_catalog().get(fe_id) if fe_id else None
        if desc and desc.get("size_in_GB"):
            return int(float(desc["size_in_GB"]) * 1_000_000_000)
    from services.embedding_providers import local_model_entry
    entry = local_model_entry(model_name)
    if entry and entry.get("size_mb"):
        return int(entry["size_mb"]) * 1_000_000
    return None


def hub_offline() -> bool:
    """True when downloads from huggingface.co are forbidden (OFFLINE_MODE or
    HF_HUB_OFFLINE set by the operator)."""
    from config import settings
    if settings.offline_mode:
        return True
    return os.environ.get("HF_HUB_OFFLINE", "").strip().lower() in ("1", "true", "yes", "on")


def retrieval_prompts(model_name: str) -> Tuple[Optional[str], Optional[str]]:
    """(query_prefix, document_prefix) a model expects, by model family.

    Shared by both local backends so a backend switch cannot change how
    queries or passages are framed — the prefixes are part of what makes
    two vectors comparable, as much as the weights are.
    """
    name = model_name.lower()
    if "qwen3-embedding" in name:
        return (
            "Instruct: Given a search query, retrieve relevant passages that answer the query.\nQuery: ",
            None,
        )
    if "nomic-embed" in name:
        return "search_query: ", "search_document: "
    if "bge" in name or "mxbai" in name:
        return "Represent this sentence for searching relevant passages: ", None
    if "e5" in name:
        return "query: ", "passage: "
    return None, None


def embedding_availability() -> dict:
    """Cheap readiness signal for the configured embedding provider.

    ``local_model_missing`` is True only when the provider is ``local`` and the
    chosen model is not in the local cache — i.e. it will have to be downloaded
    (which a blocked network cannot do). Remote providers (ollama, a hosted or
    custom endpoint) are never "missing" here: their own probe reports
    connectivity.
    """
    from config import settings

    if settings.embedding_provider != "local":
        entry = get_provider(settings.embedding_provider) or {}
        model = settings.remote_embedding_model or entry.get("default_model", "")
        return {
            "provider": settings.embedding_provider,
            "model": model,
            "local_model_missing": False,
        }
    model = settings.embedding_model
    return {
        "provider": "local",
        "model": model,
        "local_model_missing": not local_model_cached(model),
    }


class FastEmbedService:
    """Generates embeddings with fastembed (ONNX runtime, no PyTorch).

    Same surface as EmbeddingService. ``model_name`` stays the Clio catalog
    name (``all-MiniLM-L6-v2``), not fastembed's id, so the signature an
    index carries is identical whichever backend produced it.
    """

    backend = FASTEMBED_BACKEND

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", cache_dir: Optional[Union[str, Path]] = None):
        from fastembed import TextEmbedding  # lazy: only needed for this backend

        fe_id = fastembed_model_id(model_name)
        if fe_id is None:
            raise RuntimeError(
                f"fastembed has no ONNX export of '{model_name}'. Pick a model marked "
                f"fastembed in the catalog, or use the sentence-transformers backend "
                f"({INSTALL_TORCH})."
            )
        self.model_name = model_name
        self.fastembed_model = fe_id

        if cache_dir is not None:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = fastembed_cached_in(model_name) or fastembed_data_cache_dir()
        cached = fastembed_model_cached(model_name, self.cache_dir)
        if not cached and hub_offline():
            raise RuntimeError(
                f"Embedding model '{model_name}' is not in the local fastembed cache "
                f"({self.cache_dir}) and this deployment runs in offline (air-gapped) "
                f"mode, so it cannot be downloaded. Pre-seed the cache from a connected "
                f"machine (see docs/AIRGAP.md), switch EMBEDDING_PROVIDER to 'ollama', "
                f"or use the model baked into the Docker image."
            )
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if cached:
            logger.info(f"Loading '{model_name}' via fastembed from {self.cache_dir}")
        else:
            logger.info(f"Model '{model_name}' not cached; fastembed downloading into {self.cache_dir}…")
        try:
            if not cached:
                # One download attempt, not fastembed's three with 3/9/27 s
                # back-off: a blocked hub should fail in a second (the
                # operator sees why and retries from the UI), not block a
                # health check for forty.
                _fastembed_prefetch(fe_id, self.cache_dir)
            self.model = TextEmbedding(fe_id, cache_dir=str(self.cache_dir))
        except Exception as e:
            if cached:
                raise
            # fastembed's own message ("could not load from any source") hides
            # the one fact an operator can act on: the hub was unreachable.
            raise RuntimeError(
                f"Could not download embedding model '{model_name}' from huggingface.co "
                f"into {self.cache_dir}: {e}. Check the network or proxy, pre-seed the "
                f"cache from a connected machine (docs/AIRGAP.md), or choose Ollama or a "
                f"hosted provider instead."
            ) from e

        desc = _fastembed_catalog().get(fe_id) or {}
        dim = desc.get("dim")
        if not dim:
            dim = len(next(iter(self.model.embed(["dimension probe"]))))
        self.embedding_dim = int(dim)

        # onnxruntime sessions are re-entrant, but indexing threads and live
        # searches would otherwise compete for every core at once; one batch
        # at a time keeps query latency bounded, like the torch backend.
        self._encode_lock = threading.Lock()
        self._query_prompt_name: Optional[str] = None
        self._document_prompt_name: Optional[str] = None
        self._query_prompt, self._document_prompt = retrieval_prompts(model_name)
        # A model class that implements its own query/passage framing (jina
        # v3 style) must not get Clio's prefix on top of it.
        inner = getattr(self.model, "model", None)
        self._native_prefixes = _overrides_prefix_methods(inner)
        if self._native_prefixes:
            self._query_prompt = self._document_prompt = None
        logger.info(
            f"fastembed model loaded: {fe_id} dim={self.embedding_dim} "
            f"(query_prefix={self._query_prompt!r}, document_prefix={self._document_prompt!r})"
        )

    def _vectors(self, iterable) -> np.ndarray:
        rows = [np.asarray(v, dtype=np.float32) for v in iterable]
        if not rows:
            return np.array([], dtype=np.float32).reshape(0, self.embedding_dim)
        return np.vstack(rows)

    def embed_texts(
        self,
        texts: List[str],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> np.ndarray:
        if not texts:
            return np.array([], dtype=np.float32).reshape(0, self.embedding_dim)
        logger.debug(f"fastembed embed_texts: {len(texts)} texts")
        batches = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[start:start + EMBED_BATCH_SIZE]
            if self._document_prompt:
                batch = [f"{self._document_prompt}{t}" for t in batch]
            with self._encode_lock:
                batches.append(self._vectors(self.model.passage_embed(batch, batch_size=EMBED_BATCH_SIZE)))
            if progress_callback:
                progress_callback(min(start + len(batch), len(texts)), len(texts))
        return np.vstack(batches)

    def embed_query(self, query: str) -> np.ndarray:
        logger.debug(f"fastembed embed_query: {query[:50]}...")
        text = f"{self._query_prompt}{query}" if self._query_prompt else query
        with self._encode_lock:
            return self._vectors(self.model.query_embed([text]))[0]


def _fastembed_prefetch(fe_id: str, cache_dir: Path, retries: int = 1) -> None:
    """Download a fastembed model with a bounded number of attempts.

    TextEmbedding's constructor hard-codes three attempts with growing
    sleeps; the registry class that serves the model exposes the same
    download with a ``retries`` argument. Once the files are in cache_dir,
    the constructor's own cache probe finds them and downloads nothing.
    Any API drift falls through to the constructor's default behaviour.
    """
    try:
        from fastembed import TextEmbedding
        for cls in TextEmbedding.EMBEDDINGS_REGISTRY:
            for desc in cls._list_supported_models():
                if desc.model.lower() == fe_id.lower():
                    cls.download_model(desc, str(cache_dir), retries=retries)
                    return
    except (AttributeError, TypeError):
        return


def _overrides_prefix_methods(inner) -> bool:
    """Whether a fastembed model class frames queries/passages itself."""
    if inner is None:
        return False
    try:
        from fastembed.text.text_embedding_base import TextEmbeddingBase
    except Exception:
        return False
    cls = type(inner)
    query = getattr(cls, "query_embed", None)
    passage = getattr(cls, "passage_embed", None)
    return (
        (query is not None and query is not TextEmbeddingBase.query_embed)
        or (passage is not None and passage is not TextEmbeddingBase.passage_embed)
    )


class OllamaEmbeddingService:
    """Generates embeddings via Ollama's /api/embed endpoint.

    Works against a local/self-hosted Ollama daemon (no API key) or against
    Ollama Cloud (base_url https://ollama.com + api_key). Requires the chosen
    embedding model to be available on that endpoint (`ollama pull <model>`
    locally; hosted models are served on demand). No HuggingFace download,
    no sentence-transformers dependency for this path.
    """

    def __init__(
        self,
        model_name: str = "nomic-embed-text",
        base_url: str = "http://localhost:11434",
        api_key: str = "",
    ):
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        logger.info(f"Initialising Ollama embedding: model={model_name} url={self.base_url}")
        # Probe once to verify connectivity and discover dimensionality.
        try:
            sample = self._call_api(["dimension probe"])
            self.embedding_dim = len(sample[0])
            logger.info(f"Ollama embedding ready: dim={self.embedding_dim}")
        except Exception as e:
            if api_key:
                hint = (
                    f"Check that your Ollama Cloud API key is valid and that "
                    f"'{model_name}' is an embedding model available at {self.base_url}."
                )
            else:
                hint = (
                    f"Make sure Ollama is running and the model is pulled "
                    f"(`ollama pull {model_name}`)."
                )
            raise RuntimeError(
                f"Cannot reach Ollama at {self.base_url} with model '{model_name}'. {hint} Error: {e}"
            ) from e

    def _call_api(self, texts: List[str]) -> List[List[float]]:
        payload = json.dumps({"model": self.model_name, "input": texts}).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            f"{self.base_url}/api/embed",
            data=payload,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
        embeddings = data.get("embeddings")
        if not embeddings:
            raise ValueError(f"Ollama /api/embed returned no embeddings: {data}")
        return embeddings

    def embed_texts(
        self,
        texts: List[str],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> np.ndarray:
        if not texts:
            return np.array([]).reshape(0, self.embedding_dim)
        logger.debug(f"Ollama embed_texts: {len(texts)} texts")
        # Batch in chunks of 64 to avoid oversized requests.
        results: List[List[float]] = []
        for i in range(0, len(texts), 64):
            results.extend(self._call_api(texts[i : i + 64]))
            if progress_callback:
                progress_callback(min(i + 64, len(texts)), len(texts))
        return np.array(results, dtype=np.float32)

    def embed_query(self, query: str) -> np.ndarray:
        logger.debug(f"Ollama embed_query: {query[:60]}…")
        return np.array(self._call_api([query])[0], dtype=np.float32)


class EmbeddingService:
    """Generates embeddings using sentence-transformers models (PyTorch)."""

    backend = ST_BACKEND

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """
        Initialize the embedding service.

        Args:
            model_name: Name of the sentence-transformers model to use
        """
        from sentence_transformers import SentenceTransformer  # lazy: only needed for local provider

        self.model_name = model_name
        logger.info(f"Loading embedding model: {model_name}")

        # Try to load the model, with helpful error messages for SSL issues.
        # trust_remote_code=True is needed for models like nomic-ai/nomic-embed-text-v1.5
        # and newer instruction-aware embedding models.
        #
        # Prefer the local cache first: SentenceTransformer otherwise issues a HEAD
        # request to huggingface.co on every startup to check for updates, which
        # adds 10+ seconds of retry noise on flaky or offline networks.
        try:
            self.model = SentenceTransformer(
                model_name, trust_remote_code=True, local_files_only=True
            )
            logger.info(f"Loaded '{model_name}' from local cache (offline).")
        except Exception:
            # In offline/air-gapped mode there is no network to fall back to:
            # fail immediately with instructions instead of letting the hub
            # client churn through retries against an unreachable host.
            if os.environ.get("HF_HUB_OFFLINE") == "1":
                raise RuntimeError(
                    f"Embedding model '{model_name}' is not in the local "
                    f"HuggingFace cache and this deployment runs in offline "
                    f"(air-gapped) mode, so it cannot be downloaded. Either "
                    f"pre-seed the cache from a connected machine (see "
                    f"docs/AIRGAP.md), switch EMBEDDING_PROVIDER to 'ollama', "
                    f"or use the model baked into the Docker image."
                )
            logger.info(f"Model '{model_name}' not in cache; downloading from HuggingFace…")
            try:
                self.model = SentenceTransformer(model_name, trust_remote_code=True)
            except Exception as e:
                error_msg = str(e).lower()
                if 'ssl' in error_msg or 'certificate' in error_msg:
                    logger.error(
                        f"SSL certificate error when downloading model '{model_name}'. "
                        f"This often happens with packaged executables. "
                        f"Try: 1) Run from source instead of exe, or "
                        f"2) Pre-download the model by running: "
                        f"python -c \"from sentence_transformers import SentenceTransformer; SentenceTransformer('{model_name}')\""
                    )
                raise

        self.embedding_dim = self.model.get_sentence_embedding_dimension()
        # encode() is reached from background indexing threads and live search
        # requests at once; PyTorch inference isn't guaranteed re-entrant on a
        # shared model, so serialize per batch (bounded wait for queries).
        self._encode_lock = threading.Lock()
        self._query_prompt_name: Optional[str] = None
        self._document_prompt_name: Optional[str] = None
        self._query_prompt: Optional[str] = None
        self._document_prompt: Optional[str] = None
        self._configure_prompting()

        logger.info(f"Model loaded. Embedding dimension: {self.embedding_dim}")

    def _configure_prompting(self):
        """Configure model-specific query/document prompting for retrieval.

        The prefix strings come from retrieval_prompts() — shared with the
        fastembed backend — so both backends frame text identically. When
        the model ships its own named prompts (bge, qwen3 on recent
        sentence-transformers) those are used by name instead.
        """
        model_name = self.model_name.lower()
        model_prompts = getattr(self.model, "prompts", {}) or {}
        query_prompt, document_prompt = retrieval_prompts(self.model_name)

        if ("qwen3-embedding" in model_name or "bge" in model_name) and "query" in model_prompts:
            self._query_prompt_name = "query"
        else:
            self._query_prompt = query_prompt
        self._document_prompt = document_prompt

        if self._query_prompt_name or self._query_prompt or self._document_prompt_name or self._document_prompt:
            logger.info(
                "Configured retrieval prompts for embeddings "
                f"(query_prompt_name={self._query_prompt_name}, document_prompt_name={self._document_prompt_name})"
            )

    @staticmethod
    def _apply_prompt(texts: Union[str, List[str]], prompt: str) -> Union[str, List[str]]:
        """Fallback prompt application when encode() does not accept prompt args."""
        if isinstance(texts, str):
            return f"{prompt}{texts}"
        return [f"{prompt}{text}" for text in texts]

    def _encode(
        self,
        texts: Union[str, List[str]],
        prompt_name: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> np.ndarray:
        """Encode text with optional prompt support and compatibility fallback."""
        import torch  # dependency of sentence-transformers; lazy like the model import

        encode_kwargs = {
            "show_progress_bar": False,
            "convert_to_numpy": True,
        }

        with self._encode_lock, torch.inference_mode():
            if prompt_name:
                try:
                    return self.model.encode(texts, prompt_name=prompt_name, **encode_kwargs)
                except TypeError:
                    logger.debug("Model encode() does not support prompt_name; falling back")
                except Exception as e:
                    logger.debug(f"Prompt-name encoding failed ({prompt_name}): {e}")

            if prompt:
                try:
                    return self.model.encode(texts, prompt=prompt, **encode_kwargs)
                except TypeError:
                    logger.debug("Model encode() does not support prompt; prefixing manually")
                except Exception as e:
                    logger.debug(f"Prompt encoding failed; prefixing manually: {e}")
                texts = self._apply_prompt(texts, prompt)

            return self.model.encode(texts, **encode_kwargs)

    def embed_texts(
        self,
        texts: List[str],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> np.ndarray:
        """
        Generate embeddings for a list of texts.

        Batches internally (EMBED_BATCH_SIZE) so a large document never holds
        the encode lock for its full duration, and progress can be reported
        without callers re-implementing batching.

        Args:
            texts: List of text strings to embed
            progress_callback: Optional (done, total) callback per batch

        Returns:
            NumPy array of shape (len(texts), embedding_dim)
        """
        if not texts:
            return np.array([]).reshape(0, self.embedding_dim)

        logger.debug(f"Generating embeddings for {len(texts)} texts")
        batches = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[start:start + EMBED_BATCH_SIZE]
            batches.append(self._encode(
                batch,
                prompt_name=self._document_prompt_name,
                prompt=self._document_prompt,
            ))
            if progress_callback:
                progress_callback(min(start + len(batch), len(texts)), len(texts))
        return np.vstack(batches)

    def embed_query(self, query: str) -> np.ndarray:
        """
        Generate embedding for a single query.

        Args:
            query: Query text

        Returns:
            NumPy array of shape (embedding_dim,)
        """
        logger.debug(f"Generating embedding for query: {query[:50]}...")
        return self._encode(
            query,
            prompt_name=self._query_prompt_name,
            prompt=self._query_prompt,
        )


class OpenAICompatibleEmbeddingService:
    """Generates embeddings via the OpenAI-style `POST {base_url}/embeddings`.

    This one request shape — `{"model": ..., "input": [...]}` in, a `data`
    list of `{"index", "embedding"}` out — is what OpenAI, Google Gemini's
    compatibility layer, Mistral, Voyage, Jina, OpenRouter, Together, LM
    Studio, vLLM and most other embedding APIs speak, so one class covers
    all of them.
    `label` is only used in error messages so a non-engineer reads
    "Google Gemini rejected the key", not a URL.
    """

    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str = "",
        label: str = "",
        batch_size: int = 64,
    ):
        if not base_url:
            raise RuntimeError(
                f"{label or 'The custom embedding endpoint'} needs a base URL "
                f"(for example https://api.example.com/v1)."
            )
        if not model_name:
            raise RuntimeError(f"{label or 'The embedding provider'} needs a model name.")
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.label = label or self.base_url
        self.batch_size = batch_size
        logger.info(f"Initialising OpenAI-compatible embedding: model={model_name} url={self.base_url}")
        try:
            sample = self._call_api(["dimension probe"])
        except Exception as e:
            raise RuntimeError(self._explain(e)) from e
        self.embedding_dim = len(sample[0])
        logger.info(f"{self.label} embedding ready: dim={self.embedding_dim}")

    def _explain(self, e: Exception) -> str:
        """Turn transport errors into one sentence a person can act on."""
        import urllib.error

        if isinstance(e, urllib.error.HTTPError):
            body = ""
            try:
                body = e.read().decode(errors="replace")[:300]
            except Exception:
                pass
            # Google answers a bad key with 400 + "Please pass a valid API key".
            if e.code in (401, 403) or (e.code == 400 and "api key" in body.lower()):
                return f"{self.label} rejected the API key (HTTP {e.code}). Check the key and try again."
            if e.code == 404:
                return (
                    f"{self.label} has no model called '{self.model_name}' or the base URL "
                    f"{self.base_url} is wrong (HTTP 404). {body}"
                )
            if e.code == 402:
                # OpenRouter answers 402 when the account has no credit left.
                return (
                    f"{self.label} says this account has no credit for model "
                    f"'{self.model_name}' (HTTP 402). Add credit or choose a free model. {body}"
                )
            if e.code == 429:
                # The body usually names the limit that was hit (OpenRouter's
                # free models cap requests per minute and per day).
                return (
                    f"{self.label} is rate-limiting this key (HTTP 429). Wait a moment "
                    f"or check your plan's limits. {body}"
                )
            return f"{self.label} returned HTTP {e.code} for model '{self.model_name}'. {body}"
        if isinstance(e, urllib.error.URLError):
            return f"Could not reach {self.label} at {self.base_url}: {e.reason}"
        return f"{self.label} embedding call failed: {e}"

    def _call_api(self, texts: List[str]) -> List[List[float]]:
        payload = json.dumps({"model": self.model_name, "input": texts}).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=payload,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
        items = data.get("data")
        if not items:
            raise ValueError(f"{self.label} returned no embeddings: {str(data)[:300]}")
        # Providers may return rows out of order; `index` is authoritative.
        ordered = sorted(items, key=lambda item: item.get("index", 0))
        vectors = [item["embedding"] for item in ordered]
        if len(vectors) != len(texts):
            raise ValueError(
                f"{self.label} returned {len(vectors)} embeddings for {len(texts)} inputs"
            )
        return vectors

    def embed_texts(
        self,
        texts: List[str],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> np.ndarray:
        if not texts:
            return np.array([]).reshape(0, self.embedding_dim)
        logger.debug(f"{self.label} embed_texts: {len(texts)} texts")
        results: List[List[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            try:
                results.extend(self._call_api(batch))
            except Exception as e:
                raise RuntimeError(self._explain(e)) from e
            if progress_callback:
                progress_callback(min(i + len(batch), len(texts)), len(texts))
        return np.array(results, dtype=np.float32)

    def embed_query(self, query: str) -> np.ndarray:
        logger.debug(f"{self.label} embed_query: {query[:60]}…")
        try:
            return np.array(self._call_api([query])[0], dtype=np.float32)
        except Exception as e:
            raise RuntimeError(self._explain(e)) from e


# Re-exported for older imports; the catalog is the source of truth now.
from services.embedding_providers import (  # noqa: E402
    CLOUD_EMBEDDING_PROVIDERS,
    OLLAMA_CLOUD_BASE_URL,
    get_provider,
    local_model_entry,
)


def resolve_embedding_api_key(provider_id: str, explicit: Optional[str] = None) -> str:
    """The key an embedding provider should use, following one chain:

    1. an explicit value (the Settings form's own key field, or a test probe)
    2. the embedding-specific key saved in config (EMBEDDING_API_KEY)
    3. for Ollama Cloud, the legacy OLLAMA_CLOUD_API_KEY / _TOKEN
    4. the team key saved on the matching AI Providers card, so a key added
       for chat is reused without pasting it twice
    """
    from config import settings

    if explicit:
        return explicit
    if settings.embedding_api_key:
        return settings.embedding_api_key
    if provider_id == "ollama_cloud" and settings.ollama_cloud_api_key:
        return settings.ollama_cloud_api_key
    entry = get_provider(provider_id) or {}
    card = entry.get("key_provider")
    if card:
        try:
            from services.app_database import app_db
            return app_db.get_agent_api_key(card) or ""
        except Exception:
            return ""
    return ""


def embedding_signature(overrides: Optional[dict] = None) -> str:
    """Stable string naming the (provider, endpoint, model) triple in effect.

    The indexer manager keys its service cache on this, so any settings
    change that would produce different vectors yields a different key.

    The local backend (fastembed vs sentence-transformers) is deliberately
    NOT part of the signature: both run the same model weights — fastembed's
    copy is the same checkpoint exported to ONNX — and the retrieval
    prefixes are shared (retrieval_prompts), so vectors from either backend
    are interchangeable within float noise. Installing or removing torch, or
    flipping LOCAL_EMBEDDING_BACKEND, must therefore never force a re-index.
    (Caveat: a few fastembed exports are int8-quantized — the ``-Q`` HF
    sources — which perturbs vectors slightly; search quality is unaffected
    but exact scores can differ across backends.)
    """
    from config import settings

    o = overrides or {}
    provider = o.get("embedding_provider", settings.embedding_provider)
    if provider == "local":
        return f"local::{o.get('embedding_model', settings.embedding_model)}"
    entry = get_provider(provider) or {}
    if provider == "ollama":
        base = o.get("ollama_base_url", settings.ollama_base_url)
    elif provider == "openai_compatible":
        base = o.get("embedding_base_url", settings.embedding_base_url)
    else:
        base = entry.get("base_url") or ""
    model = o.get("remote_embedding_model", settings.remote_embedding_model) or entry.get("default_model", "")
    return f"{provider}::{base}::{model}"


def service_signature(collection_model: Optional[str] = None, overrides: Optional[dict] = None) -> str:
    """The signature of the service create_embedding_service would build.

    Like embedding_signature(), but honours the per-collection local model
    override the factory applies — the signature must name the model that
    actually produced a collection's vectors.
    """
    from config import settings

    o = overrides or {}
    if o.get("embedding_provider", settings.embedding_provider) == "local":
        return f"local::{collection_model or o.get('embedding_model', settings.embedding_model)}"
    return embedding_signature(o)


def describe_embedding(service) -> dict:
    """Everything another system needs to embed queries compatibly.

    Vectors are only reusable with the exact model, and some models (bge,
    e5, qwen3) expect an instruction prefix on queries or passages; a reader
    that skips it gets quietly worse results rather than an error.
    """
    info = {
        "signature": getattr(service, "signature", None),
        "model": getattr(service, "model_name", None),
        "backend": getattr(service, "backend", None),
        "dimension": int(getattr(service, "embedding_dim", 0) or 0),
        # VectorStore L2-normalises before adding and searching (inner
        # product over unit vectors = cosine similarity).
        "normalization": "l2",
        "metric": "inner_product",
    }
    for attr in ("_query_prompt_name", "_query_prompt", "_document_prompt_name", "_document_prompt"):
        value = getattr(service, attr, None)
        if isinstance(value, str) and value:
            info[attr.lstrip("_")] = value
    return info


class EmbeddingUnavailable(RuntimeError):
    """The configured embedding model cannot be built right now.

    Raised by DeferredEmbeddingService when a vector is actually needed and
    the model still is not there (hub blocked, backend missing, bad key).
    The API maps it to 503 with the message, so the person sees what to fix
    rather than a stack trace, and every read path that needs no vectors
    (listing sources, settings, the onboarding) keeps working meanwhile.
    """


def known_embedding_dim(model_name: Optional[str] = None, index_dir=None,
                        provider: Optional[str] = None) -> Optional[int]:
    """The vector dimension the configured model produces, without loading it.

    Tries the catalog first (every curated local and hosted model records
    its dimension), then an existing FAISS index in `index_dir` (vectors
    already on disk fix the dimension whatever the catalog says). None when
    neither knows, in which case the model has to be loaded to find out.
    """
    from config import settings

    provider = provider or settings.embedding_provider
    if provider == "local":
        entry = local_model_entry(model_name or settings.embedding_model)
        if entry and entry.get("dimensions"):
            return int(entry["dimensions"])
    else:
        prov = get_provider(provider) or {}
        wanted = model_name or settings.remote_embedding_model or prov.get("default_model")
        for m in prov.get("models", []):
            if m.get("id") == wanted and m.get("dimensions"):
                return int(m["dimensions"])
    if index_dir is not None:
        try:
            path = Path(index_dir) / "faiss.index"
            if path.exists() and path.stat().st_size > 0:
                import faiss
                return int(faiss.read_index(str(path)).d)
        except Exception as e:  # unreadable index: let the loader deal with it
            logger.debug(f"Could not read index dimension from {index_dir}: {e}")
    return None


class DeferredEmbeddingService:
    """A stand-in for an embedding service that could not be built yet.

    Behind a corporate firewall the model download may fail, or a backend
    may be missing; that must not stop the person from opening the app,
    browsing what is already indexed, or choosing another provider. This
    object carries the model name and dimension (known from the catalog or
    the existing index) so the indexer and vector store can be constructed,
    and builds the real service on the first call that needs vectors. If
    that still fails it raises EmbeddingUnavailable with the reason, and
    waits a little before trying the hub again so a search storm does not
    turn into a download storm.
    """

    backend = None
    RETRY_AFTER_SECONDS = 20.0

    def __init__(self, model_name: str, embedding_dim: int, factory, reason: str = ""):
        self.model_name = model_name
        self.embedding_dim = int(embedding_dim)
        self._factory = factory
        self._real = None
        self._lock = threading.Lock()
        self.last_error: Optional[str] = reason or None
        self._last_attempt = 0.0
        self.signature = None

    @property
    def available(self) -> bool:
        return self._real is not None

    @property
    def real(self):
        return self._real

    def _ensure(self):
        if self._real is not None:
            return self._real
        with self._lock:
            if self._real is not None:
                return self._real
            now = time.monotonic()
            if self.last_error and now - self._last_attempt < self.RETRY_AFTER_SECONDS:
                raise EmbeddingUnavailable(self.last_error)
            self._last_attempt = now
            try:
                service = self._factory()
            except Exception as e:
                self.last_error = str(e) or e.__class__.__name__
                raise EmbeddingUnavailable(self.last_error) from e
            real_dim = int(getattr(service, "embedding_dim", self.embedding_dim) or self.embedding_dim)
            if real_dim != self.embedding_dim:
                self.last_error = (
                    f"The loaded model '{getattr(service, 'model_name', self.model_name)}' produces "
                    f"{real_dim}-dimensional vectors but this collection's index expects "
                    f"{self.embedding_dim}; re-index the collection to switch models."
                )
                raise EmbeddingUnavailable(self.last_error)
            self._real = service
            self.backend = getattr(service, "backend", None)
            self.signature = getattr(service, "signature", self.signature)
            self.last_error = None
            logger.info(f"Embedding model '{self.model_name}' is now available")
            return service

    def embed_texts(self, texts, progress_callback=None):
        return self._ensure().embed_texts(texts, progress_callback=progress_callback)

    def embed_query(self, query):
        return self._ensure().embed_query(query)

    def __getattr__(self, name):
        # Prompt names and other advisory attributes read by describe_embedding.
        real = self.__dict__.get("_real")
        if real is not None and name.startswith("_") and not name.startswith("__"):
            return getattr(real, name)
        raise AttributeError(name)


def create_embedding_service(
    collection_model: Optional[str] = None,
    overrides: Optional[dict] = None,
):
    """Build the embedding service and tag it with its signature.

    The signature travels with the vectors the service writes (see
    VectorStore.embedding_info), so an index can say what produced it.
    """
    service = _build_embedding_service(collection_model, overrides)
    try:
        service.signature = service_signature(collection_model, overrides)
    except Exception:  # a stub or slotted object; the signature is advisory
        pass
    return service


def _build_embedding_service(
    collection_model: Optional[str] = None,
    overrides: Optional[dict] = None,
):
    """Build the embedding service the current settings call for.

    This is the ONE place provider selection happens — the indexer manager and
    the reindex service both route through it, so a reindex can never silently
    re-embed with a different provider than query time uses.

    - "local": fastembed or sentence-transformers, per LOCAL_EMBEDDING_BACKEND
      (resolve_local_backend); `collection_model` (a per-collection
      override) wins over the global EMBEDDING_MODEL.
    - "ollama": self-hosted daemon at OLLAMA_BASE_URL, no key.
    - "ollama_cloud": ollama.com with a bearer key.
    - any other catalog entry: the OpenAI-style embeddings endpoint the
      catalog names (or EMBEDDING_BASE_URL for "openai_compatible"), with
      the key from resolve_embedding_api_key.

    `overrides` lets the Settings "Test connection" probe build a service
    from values that have not been saved yet; keys are the config field
    names.
    """
    from config import settings

    o = overrides or {}
    provider = o.get("embedding_provider", settings.embedding_provider)
    entry = get_provider(provider)
    if entry is None:
        raise RuntimeError(
            f"Unknown embedding provider '{provider}'. Choose one of: "
            + ", ".join(p["id"] for p in _visible_providers())
        )
    if settings.offline_mode and provider in CLOUD_EMBEDDING_PROVIDERS:
        raise RuntimeError(
            f"Embedding provider '{provider}' sends text to an external service, "
            f"which this deployment forbids (OFFLINE_MODE). Use 'local', "
            f"'ollama', or a self-hosted 'openai_compatible' endpoint."
        )

    if provider == "local":
        model = collection_model or o.get("embedding_model", settings.embedding_model)
        backend = resolve_local_backend(model, o.get("local_embedding_backend"))
        if backend == FASTEMBED_BACKEND:
            return FastEmbedService(model_name=model)
        return EmbeddingService(model_name=model)

    model = o.get("remote_embedding_model", settings.remote_embedding_model) or entry["default_model"]
    explicit_key = o.get("embedding_api_key")
    api_key = resolve_embedding_api_key(provider, explicit_key)
    if entry["needs_key"] and not api_key:
        where = (
            f"add the {entry['label']} key under Settings → AI Providers, or enter one "
            f"in the Embedding section"
            if entry.get("key_provider")
            else "enter one in the Embedding section of Settings"
        )
        raise RuntimeError(f"{entry['label']} embeddings need an API key: {where}.")

    if provider == "ollama":
        return OllamaEmbeddingService(
            model_name=model,
            base_url=o.get("ollama_base_url", settings.ollama_base_url),
        )
    if provider == "ollama_cloud":
        return OllamaEmbeddingService(
            model_name=model,
            base_url=OLLAMA_CLOUD_BASE_URL,
            api_key=api_key,
        )

    base_url = entry["base_url"] or o.get("embedding_base_url", settings.embedding_base_url)
    return OpenAICompatibleEmbeddingService(
        model_name=model,
        base_url=base_url,
        api_key=api_key,
        label=entry["label"],
    )


def _visible_providers():
    from services.embedding_providers import EMBEDDING_PROVIDERS
    return [p for p in EMBEDDING_PROVIDERS if not p.get("hidden")]

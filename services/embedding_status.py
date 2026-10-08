"""Embedding readiness: one process-wide status the UI can poll.

The embedding model is the one thing a fresh Clio cannot do without, and on
a fresh install it is also the one thing that takes a while (a download,
then a load). This module owns that story:

- ``warm()`` starts a background job that gets the configured provider
  ready — for the local provider it loads the model, downloading it first
  when it is missing and allowed to; for remote providers it runs a cheap
  probe — and keeps ``status()`` honest the whole way through:
  ``unconfigured → loading | downloading → ready``, or ``missing`` (offline
  and not cached) or ``error``.
- A download has no progress API in either backend, so a watcher thread
  samples the size of the model's cache directory every 0.5 s against the
  expected size and reports a percentage (capped at 99 until the model has
  actually loaded).
- ``pull_ollama_model()`` streams Ollama's ``/api/pull`` into the same
  status so an Ollama-based first run shows the same progress bar.

The service the job builds goes through ``indexer_manager`` — the same
cache indexing and search use — so the warm-up pays the load once and the
first request finds it already there.
"""

from __future__ import annotations

import copy
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional

logger = logging.getLogger(__name__)

STATUSES = ("ready", "loading", "downloading", "missing", "error", "unconfigured")

# How long a remote provider gets to answer the warm-up probe.
REMOTE_PROBE_TIMEOUT_SECONDS = 10.0
# How often the download watcher samples the cache directory.
WATCH_INTERVAL_SECONDS = 0.5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dir_size(path: Path) -> int:
    """Bytes on disk under ``path`` (symlinks counted once, via their target).

    huggingface_hub writes blobs as ``blobs/<sha>.incomplete`` while a
    download runs and symlinks ``snapshots/<rev>/<file>`` to them when done,
    so the blob directory is where the bytes actually accumulate.
    """
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.stat(os.path.join(root, name)).st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _default_service_factory(provider: str, model: str):
    """Build (and cache) the service through the indexer manager's own path,
    so the instance warmed here is the instance indexing and search use."""
    from services.indexer_manager import indexer_manager

    return indexer_manager._get_embedding_service(model)


def _ollama_pull_stream(base_url: str, model: str) -> Iterator[dict]:
    """Yield the JSON lines Ollama streams from POST /api/pull."""
    import json

    import httpx

    with httpx.Client(timeout=httpx.Timeout(30.0, read=600.0)) as client:
        with client.stream(
            "POST", f"{base_url.rstrip('/')}/api/pull", json={"model": model, "stream": True}
        ) as resp:
            if resp.status_code >= 400:
                body = resp.read().decode(errors="replace")[:300]
                raise RuntimeError(f"Ollama /api/pull returned HTTP {resp.status_code}: {body}")
            for line in resp.iter_lines():
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


class EmbeddingStatus:
    """Thread-safe holder of the embedding readiness state (see module doc)."""

    def __init__(self):
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._pull_thread: Optional[threading.Thread] = None
        self._state: Dict[str, Any] = self._blank()

    # ── state ──────────────────────────────────────────────────────────

    @staticmethod
    def _blank() -> Dict[str, Any]:
        return {
            "provider": None,
            "backend": None,
            "model": None,
            "status": "unconfigured",
            "progress": None,
            "error": None,
            "started_at": None,
            "finished_at": None,
            "can_download": True,
        }

    def reset(self) -> None:
        """Forget everything (tests, and a provider change that should not
        keep showing the previous provider's error). A running job keeps
        running; its final write lands on the fresh state."""
        with self._lock:
            self._state = self._blank()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

    def _set(self, **fields: Any) -> None:
        with self._lock:
            self._state.update(fields)

    def is_running(self) -> bool:
        t = self._thread
        return bool(t is not None and t.is_alive())

    # ── warm-up ────────────────────────────────────────────────────────

    def warm_on_startup(self) -> bool:
        """The lifespan hook: start the warm-up unless this process should
        not (CLIO_SKIP_EMBEDDING_WARMUP=1, or running under pytest, where a
        background download or model load would leak out of the test).
        Returns whether a job was started."""
        if os.environ.get("CLIO_SKIP_EMBEDDING_WARMUP", "").strip().lower() in ("1", "true", "yes", "on"):
            logger.info("Embedding warm-up skipped (CLIO_SKIP_EMBEDDING_WARMUP)")
            return False
        if "PYTEST_CURRENT_TEST" in os.environ or sys.modules.get("pytest") is not None:
            return False
        before = self.status()
        self.warm()
        return self.is_running() or self.status() != before

    def warm(
        self,
        force: bool = False,
        service_factory: Optional[Callable[[str, str], Any]] = None,
    ) -> Dict[str, Any]:
        """Start the warm-up in a background thread and return the status.

        Idempotent: while a job runs, or once the provider is ready, another
        call is a no-op unless ``force`` asks for a fresh run (after the
        settings changed, or to retry a download). ``service_factory``
        (provider, model) → service lets tests substitute the model load.
        """
        from config import settings
        from services.embedder import (
            fastembed_importable,
            sentence_transformers_importable,
            INSTALL_FASTEMBED,
            INSTALL_TORCH,
        )
        from services.embedding_providers import get_provider

        provider = settings.embedding_provider
        if provider == "local":
            model = settings.embedding_model
        else:
            entry = get_provider(provider) or {}
            model = settings.remote_embedding_model or entry.get("default_model", "")

        with self._lock:
            if self.is_running():
                return self.status()
            current = self._state
            same_target = current["provider"] == provider and current["model"] == model
            if not force and same_target and current["status"] == "ready":
                return self.status()

            if provider == "local" and not fastembed_importable() and not sentence_transformers_importable():
                # Nothing could load a model: say so without spawning a job.
                self._state = {
                    **self._blank(),
                    "provider": provider,
                    "model": model,
                    "status": "error",
                    "error": (
                        "No local embedding backend is installed. Install one: "
                        f"`{INSTALL_FASTEMBED}` (recommended) or `{INSTALL_TORCH}`."
                    ),
                    "can_download": False,
                    "started_at": _now(),
                    "finished_at": _now(),
                }
                return self.status()

            self._state = {
                **self._blank(),
                "provider": provider,
                "model": model,
                "status": "loading",
                "started_at": _now(),
            }
            factory = service_factory or _default_service_factory
            self._thread = threading.Thread(
                target=self._run,
                args=(provider, model, factory),
                name="embedding-warmup",
                daemon=True,
            )
            self._thread.start()
        return self.status()

    def _run(self, provider: str, model: str, factory: Callable[[str, str], Any]) -> None:
        try:
            if provider == "local":
                self._warm_local(model, factory)
            else:
                self._warm_remote(provider, model, factory)
        except Exception as e:  # noqa: BLE001 - the status IS the error report
            logger.warning(f"Embedding warm-up failed ({provider}/{model}): {e}")
            self._set(status="error", error=str(e), progress=None, finished_at=_now())

    def _warm_local(self, model: str, factory: Callable[[str, str], Any]) -> None:
        from services.embedder import (
            hub_offline,
            local_model_cached,
            local_model_download_bytes,
            local_model_download_dir,
            resolve_local_backend,
        )

        backend = resolve_local_backend(model)  # RuntimeError → error status
        self._set(backend=backend)
        offline = hub_offline()
        cached = local_model_cached(model, backend)

        if not cached and offline:
            self._set(
                status="missing",
                can_download=False,
                error=None,
                progress=None,
                finished_at=_now(),
            )
            return

        stop = threading.Event()
        watcher: Optional[threading.Thread] = None
        if cached:
            self._set(status="loading", can_download=True)
        else:
            total = local_model_download_bytes(model, backend)
            target = local_model_download_dir(model, backend)
            self._set(
                status="downloading",
                can_download=True,
                progress={
                    "downloaded_bytes": 0,
                    "total_bytes": total,
                    "percent": 0 if total else None,
                    "file": str(target) if target else None,
                },
            )
            if target is not None:
                watcher = threading.Thread(
                    target=self._watch_download,
                    args=(target, total, stop),
                    name="embedding-download-watch",
                    daemon=True,
                )
                watcher.start()

        try:
            service = factory("local", model)
        finally:
            stop.set()
            if watcher is not None:
                watcher.join(timeout=2.0)

        self._set(
            status="ready",
            backend=getattr(service, "backend", backend),
            progress=None,
            error=None,
            finished_at=_now(),
        )
        self._after_ready()

    def _watch_download(self, target: Path, total: Optional[int], stop: threading.Event) -> None:
        """Sample the cache directory's size until the load finishes."""
        while not stop.is_set():
            done = _dir_size(target)
            percent = None
            if total:
                percent = min(99, int(done * 100 / total))
            with self._lock:
                if self._state["status"] != "downloading":
                    return
                self._state["progress"] = {
                    "downloaded_bytes": done,
                    "total_bytes": total,
                    "percent": percent,
                    "file": str(target),
                }
            stop.wait(WATCH_INTERVAL_SECONDS)

    def _warm_remote(self, provider: str, model: str, factory: Callable[[str, str], Any]) -> None:
        """A remote provider is 'ready' when it answers one tiny embed call
        within REMOTE_PROBE_TIMEOUT_SECONDS — the same thing the Settings
        'Test connection' button does, on the saved values."""
        import concurrent.futures

        self._set(status="loading", backend=None, can_download=False)

        def probe():
            service = factory(provider, model)
            # Service constructors already embed a probe string; one more
            # confirms the full path (and is what a timeout wraps).
            service.embed_query("embedding warm-up")
            return service

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="embedding-probe")
        try:
            future = pool.submit(probe)
            try:
                service = future.result(timeout=REMOTE_PROBE_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                raise RuntimeError(
                    f"{provider} did not answer the embedding probe within "
                    f"{int(REMOTE_PROBE_TIMEOUT_SECONDS)} s."
                ) from None
        finally:
            pool.shutdown(wait=False)

        self._set(
            status="ready",
            model=getattr(service, "model_name", model) or model,
            progress=None,
            error=None,
            finished_at=_now(),
        )
        self._after_ready()

    def _after_ready(self) -> None:
        """Pre-open the default collection so the first request finds its
        indexer built. Best-effort: a missing default collection or a
        store that fails to open is logged, never surfaced as an embedding
        error (the model IS ready)."""
        if "PYTEST_CURRENT_TEST" in os.environ or sys.modules.get("pytest") is not None:
            return
        try:
            from services.indexer_manager import indexer_manager

            indexer = indexer_manager.get_indexer("default")
            logger.info(
                f"Default collection indexed chunks: {indexer.vector_store.get_total_chunks()}"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not load default indexer after embedding warm-up: {e}")

    # ── Ollama pull ────────────────────────────────────────────────────

    def pull_ollama_model(self, model: str, base_url: Optional[str] = None) -> Dict[str, Any]:
        """Pull an embedding model on the Ollama server in the background.

        While the configured provider is ``ollama`` the pull's
        ``completed/total`` bytes drive the ``downloading`` status, and a
        finished pull re-runs the warm-up so the status lands on ``ready``.
        For any other provider the pull still runs (the person is about to
        switch), but the status is left alone.
        """
        from config import settings

        base = (base_url or settings.ollama_base_url).rstrip("/")
        with self._lock:
            if self._pull_thread is not None and self._pull_thread.is_alive():
                return self.status()
            tracks = settings.embedding_provider == "ollama"
            if tracks:
                self._state = {
                    **self._blank(),
                    "provider": "ollama",
                    "model": model,
                    "status": "downloading",
                    "started_at": _now(),
                    "progress": {"downloaded_bytes": 0, "total_bytes": None, "percent": 0, "file": model},
                }
            self._pull_thread = threading.Thread(
                target=self._run_pull,
                args=(base, model, tracks),
                name="ollama-pull",
                daemon=True,
            )
            self._pull_thread.start()
        return self.status()

    def _run_pull(self, base_url: str, model: str, tracks: bool) -> None:
        try:
            for event in _ollama_pull_stream(base_url, model):
                if event.get("error"):
                    raise RuntimeError(str(event["error"]))
                if not tracks:
                    continue
                total = event.get("total")
                completed = event.get("completed")
                if total:
                    percent = min(99, int((completed or 0) * 100 / total))
                    self._set(
                        progress={
                            "downloaded_bytes": int(completed or 0),
                            "total_bytes": int(total),
                            "percent": percent,
                            "file": event.get("digest") or event.get("status") or model,
                        }
                    )
                elif event.get("status"):
                    with self._lock:
                        prog = self._state.get("progress") or {}
                        self._state["progress"] = {**prog, "file": event["status"]}
            logger.info(f"Ollama pull finished: {model}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Ollama pull of '{model}' failed: {e}")
            if tracks:
                self._set(status="error", error=str(e), progress=None, finished_at=_now())
            return
        if tracks:
            self._set(progress={"downloaded_bytes": 0, "total_bytes": None, "percent": 100, "file": model})
            self.warm(force=True)


embedding_status = EmbeddingStatus()

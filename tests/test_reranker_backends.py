"""The reranker's two backends: resolution, on-disk detection, status, and the
automatic-on contract (never downloads on the search path).

The real fastembed model is only exercised when it is already on disk; the
rest runs against fake caches so the suite stays offline and fast.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config import settings
from services import reranker
from services.embedder import _hf_repo_folder


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(reranker, "_reranker", None)
    monkeypatch.setattr(reranker, "_auto_cache", {})
    monkeypatch.setattr(settings, "enable_reranker", None, raising=False)
    monkeypatch.setattr(settings, "reranker_backend", "auto", raising=False)
    monkeypatch.setattr(settings, "reranker_model", "cross-encoder/ms-marco-MiniLM-L-6-v2", raising=False)


def _fake_fastembed_cache(root: Path, hf_repo: str = "Xenova/ms-marco-MiniLM-L-6-v2") -> Path:
    """Lay out a complete fastembed download the way huggingface_hub does."""
    model_dir = root / _hf_repo_folder(hf_repo)
    snap = model_dir / "snapshots" / "abc123" / "onnx"
    snap.mkdir(parents=True)
    (snap / "model.onnx").write_bytes(b"\0")
    (model_dir / "files_metadata.json").write_text("{}")
    return model_dir


def test_default_model_maps_to_fastembed_export():
    pytest.importorskip("fastembed")
    assert reranker.fastembed_reranker_id("cross-encoder/ms-marco-MiniLM-L-6-v2") == "Xenova/ms-marco-MiniLM-L-6-v2"
    # A fastembed id is accepted as-is, case-insensitively.
    assert reranker.fastembed_reranker_id("xenova/ms-marco-minilm-l-6-v2") == "Xenova/ms-marco-MiniLM-L-6-v2"
    assert reranker.fastembed_reranker_id("no-such/model") is None


def test_auto_prefers_fastembed_then_sentence_transformers(monkeypatch):
    import services.embedder as embedder

    monkeypatch.setattr(embedder, "fastembed_importable", lambda: True)
    monkeypatch.setattr(embedder, "sentence_transformers_importable", lambda: True)
    monkeypatch.setattr(reranker, "fastembed_reranker_id", lambda name: "Xenova/x")
    assert reranker.resolve_backend("m") == "fastembed"
    assert reranker.resolve_backend("m", "sentence-transformers") == "sentence-transformers"

    # fastembed has no export: auto falls through to torch; forcing fastembed yields nothing.
    monkeypatch.setattr(reranker, "fastembed_reranker_id", lambda name: None)
    assert reranker.resolve_backend("m") == "sentence-transformers"
    assert reranker.resolve_backend("m", "fastembed") is None

    monkeypatch.setattr(embedder, "sentence_transformers_importable", lambda: False)
    assert reranker.resolve_backend("m") is None


def test_fastembed_cache_detection(tmp_path, monkeypatch):
    pytest.importorskip("fastembed")
    import services.embedder as embedder

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(embedder, "fastembed_cache_dirs", lambda: [empty])
    assert reranker.fastembed_reranker_cached_in("cross-encoder/ms-marco-MiniLM-L-6-v2") is None

    seeded = tmp_path / "seeded"
    model_dir = _fake_fastembed_cache(seeded)
    monkeypatch.setattr(embedder, "fastembed_cache_dirs", lambda: [empty, seeded])
    assert reranker.fastembed_reranker_cached_in("cross-encoder/ms-marco-MiniLM-L-6-v2") == seeded

    # A download in flight (no completion marker) does not count.
    (model_dir / "files_metadata.json").unlink()
    assert reranker.fastembed_reranker_cached_in("cross-encoder/ms-marco-MiniLM-L-6-v2") is None


def test_auto_setting_follows_disk_and_never_downloads(monkeypatch):
    calls = []
    monkeypatch.setattr(reranker, "model_cached", lambda name, backend=None: calls.append(name) or False)
    monkeypatch.setattr(reranker, "prefetch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("downloaded")))
    assert reranker.get_reranker() is None
    assert calls  # it looked at the disk

    monkeypatch.setattr(reranker, "_auto_cache", {})
    monkeypatch.setattr(reranker, "model_cached", lambda name, backend=None: True)
    monkeypatch.setattr(reranker, "resolve_backend", lambda name, backend=None: "fastembed")
    assert reranker.get_reranker() is not None


def test_status_explains_why_not(monkeypatch):
    monkeypatch.setattr(reranker, "resolve_backend", lambda name, backend=None: None)
    state = reranker.status()
    assert state["active"] is False and "no backend" in state["reason"]

    monkeypatch.setattr(reranker, "resolve_backend", lambda name, backend=None: "fastembed")
    monkeypatch.setattr(reranker, "model_cached", lambda name, backend=None: False)
    state = reranker.status()
    assert state["active"] is False and state["reason"] == "model not on disk yet"

    monkeypatch.setattr(reranker, "model_cached", lambda name, backend=None: True)
    state = reranker.status()
    assert state == {**state, "active": True, "cached": True, "backend": "fastembed", "reason": None}

    monkeypatch.setattr(settings, "enable_reranker", False, raising=False)
    assert reranker.status()["reason"] == "disabled by ENABLE_RERANKER=false"


def test_warm_in_background_respects_offline_and_presence(monkeypatch):
    import services.embedder as embedder

    started = []
    monkeypatch.setattr(reranker.threading, "Thread", lambda **kw: started.append(kw) or type("T", (), {"start": lambda s: None})())
    monkeypatch.setattr(reranker, "resolve_backend", lambda name, backend=None: "fastembed")

    monkeypatch.setattr(embedder, "hub_offline", lambda: True)
    monkeypatch.setattr(reranker, "model_cached", lambda name, backend=None: False)
    assert reranker.warm_in_background() is None  # offline: never fetch

    monkeypatch.setattr(embedder, "hub_offline", lambda: False)
    monkeypatch.setattr(reranker, "model_cached", lambda name, backend=None: True)
    assert reranker.warm_in_background() is None  # already on disk

    monkeypatch.setattr(settings, "enable_reranker", True, raising=False)
    monkeypatch.setattr(reranker, "model_cached", lambda name, backend=None: False)
    assert reranker.warm_in_background() is None  # explicit setting: first use decides

    monkeypatch.setattr(settings, "enable_reranker", None, raising=False)
    assert reranker.warm_in_background() is not None
    assert started and started[0]["name"] == "reranker-warmup"


def test_rerank_degrades_to_input_order_without_a_backend(monkeypatch):
    monkeypatch.setattr(reranker, "resolve_backend", lambda name, backend=None: None)
    r = reranker.CrossEncoderReranker("whatever")
    items = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
    assert r.rerank("q", items, 2) == items[:2]
    assert r.available is False


def test_fastembed_rerank_orders_by_relevance():
    """End to end on the real ONNX model, only when it is already on disk."""
    pytest.importorskip("fastembed")
    if not reranker.model_cached("cross-encoder/ms-marco-MiniLM-L-6-v2", "fastembed"):
        pytest.skip("reranker model not on disk")
    r = reranker.CrossEncoderReranker("cross-encoder/ms-marco-MiniLM-L-6-v2", "fastembed")
    items = [
        {"text": "The cafeteria menu changes weekly."},
        {"text": "Restore procedure: call the on-call engineer."},
        {"text": "Backups run nightly at 02:00 UTC."},
    ]
    top = r.rerank("when do backups run", items, 1)
    assert top == [items[2]]
    assert r.backend == "fastembed" and r.loaded


def test_scores_keep_input_order_across_length_sorted_batches(monkeypatch):
    """Pairs are scored shortest-first in batches; scores must land back on
    the right candidate."""
    monkeypatch.setattr(reranker, "resolve_backend", lambda name, backend=None: "fastembed")
    r = reranker.CrossEncoderReranker("m", "fastembed")

    class Model:
        def rerank(self, query, docs, batch_size=8):
            assert docs == sorted(docs, key=len)
            return [float(len(d)) for d in docs]  # longer text = higher score

    r._model = Model()
    items = [{"text": "ccc"}, {"text": "a"}, {"text": "bbbbb"}, {"text": "dd"}]
    assert r.rerank("q", items, 4) == [items[2], items[0], items[3], items[1]]


def test_rerank_sheds_load_when_every_slot_is_busy(monkeypatch):
    monkeypatch.setattr(reranker, "resolve_backend", lambda name, backend=None: "fastembed")
    monkeypatch.setattr(settings, "rerank_max_concurrent", 1, raising=False)
    monkeypatch.setattr(settings, "rerank_queue_timeout_seconds", 0.05, raising=False)
    monkeypatch.setattr(reranker, "_gate", None)
    monkeypatch.setattr(reranker, "_gate_size", 0)
    r = reranker.CrossEncoderReranker("m", "fastembed")

    class Model:
        def rerank(self, query, docs, batch_size=8):
            return [float(i) for i in range(len(docs))]

    r._model = Model()
    items = [{"text": "a"}, {"text": "bb"}, {"text": "ccc"}]

    ranked, did = r.rerank_with_status("q", items, 3)
    assert did is True and ranked == [items[2], items[1], items[0]]

    gate = reranker._inference_gate()
    assert gate.acquire(blocking=False)  # hold the only slot
    try:
        ranked, did = r.rerank_with_status("q", items, 2)
        assert did is False and ranked == items[:2]  # retrieval order, promptly
    finally:
        gate.release()

    monkeypatch.setattr(settings, "rerank_max_concurrent", 0, raising=False)
    assert reranker._inference_gate() is None  # 0 = unbounded

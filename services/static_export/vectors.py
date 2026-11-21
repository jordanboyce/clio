"""The vectors: read from the index on disk, quantised, and the probe."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

from services.static_export.common import PROBE_TEXT, StaticExportError, _sha256_file

logger = logging.getLogger(__name__)


def _read_vectors(index_path: Path) -> Tuple[Dict[int, int], np.ndarray]:
    """(chunk row id -> row of the matrix, the stored vectors) from the index on disk."""
    import faiss

    if not index_path.exists():
        raise StaticExportError("This collection has no vectors yet, so there is nothing to search by meaning. "
                                "Open it in Clio so it can finish embedding, then export.", "vectors")
    # `index` owns the memory the downcast views point into: keep it in scope.
    index = faiss.read_index(str(index_path))
    idmap = faiss.downcast_index(index)
    if not isinstance(idmap, faiss.IndexIDMap2):
        raise StaticExportError("This collection's index is in an older layout. Open the collection in Clio "
                                "once so it is brought up to date, then export.", "vectors")
    ids = faiss.vector_to_array(idmap.id_map)
    flat = faiss.downcast_index(idmap.index)
    matrix = flat.reconstruct_n(0, flat.ntotal) if flat.ntotal else np.zeros((0, flat.d), dtype=np.float32)
    return {int(row_id): row for row, row_id in enumerate(ids)}, np.asarray(matrix, dtype=np.float32)


def quantize(matrix: np.ndarray, quantization: str) -> Tuple[bytes, float]:
    """(file bytes, scale). A reader recovers a score as ``dot(row, query) / scale``.

    int8 uses one scale for the whole file, chosen so the largest component
    in the corpus lands on 127: unit vectors of a few hundred dimensions have
    components far below 1, and scaling by 127 alone would throw most of the
    eight bits away.
    """
    if quantization == "float32":
        return np.ascontiguousarray(matrix, dtype="<f4").tobytes(), 1.0
    peak = float(np.abs(matrix).max()) if matrix.size else 0.0
    scale = 127.0 / peak if peak > 0 else 1.0
    return np.clip(np.rint(matrix * scale), -127, 127).astype(np.int8).tobytes(), scale


def _probe(model_name: Optional[str], dimension: int) -> Optional[Dict[str, Any]]:
    """Clio's own query vector for the probe sentence, or None when the model
    is not loaded here. The export still works without it; a client then has
    only the model's name to go on."""
    from services.indexer_manager import indexer_manager

    try:
        service = indexer_manager._get_embedding_service(model_name)
        vector = np.asarray(service.embed_query(PROBE_TEXT), dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if vector.shape[0] != dimension or norm == 0:
            return None
        return {"text": PROBE_TEXT, "vector": [round(float(x), 6) for x in vector / norm]}
    except Exception as e:  # noqa: BLE001 - the probe is a check, not the export
        logger.warning(f"Static export: no probe vector, the embedding model is not available here: {e}")
        return None


def _semantic_entries(browser, model, dimension, site, runtime, model_files) -> Dict[str, Any]:
    """The manifest's ``embedding`` for an export searched by meaning."""
    from services.embedder import retrieval_prompts

    query_prefix, _ = retrieval_prompts(browser["clio_model"])
    browser_entry: Dict[str, Any] = {"library": "@huggingface/transformers", "model": browser["model"],
                                     "dtype": "fp32", "also_matches": ["fp16"]}
    if site:
        # The page loads this folder (models/<model>/) and the runtime
        # beside it, not the hub build named above.
        browser_entry["bundled"] = {
            "model": browser["clio_model"], "source": model_files["source"],
            "license": model_files["license"],
            "sha256": _sha256_file(model_files["files"]["onnx/model.onnx"]),
            "runtime": runtime["info"].get("packages", {}),
        }
    return {
        "model": browser["clio_model"],
        "signature": model["signature"],
        "dimension": dimension,
        "pooling": browser["pooling"],
        "normalization": "l2",
        "metric": "inner_product",
        "query_prefix": query_prefix or "",
        "search": "meaning",
        # What a page loads to embed a query the same way.
        "browser": browser_entry,
        "probe": _probe(browser["clio_model"], dimension),
    }

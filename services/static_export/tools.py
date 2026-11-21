"""F3: the passages and vectors in formats other tools load directly.

``data/passages.jsonl`` holds one passage per line (with its source's
title and address), ``data/vectors.npy`` the passages' unit vectors as
float32 in the same order, and ``data/embedding.json`` the model, pooling,
normalisation and query prefix a tool must use to embed a query the same
way: what DuckDB, LanceDB or pgvector need, with no Clio code. The vectors
are full precision (not the site's 8 bits), so a score from them is Clio's
own cosine. Parquet is left out: it needs a package Clio does not have.
"""

from __future__ import annotations

import io
import json
from typing import Any, Dict

import numpy as np

from services.static_export.additions import Addition, ExportContext, register

FOLDER = "data"


def _offer(o) -> Dict[str, Any]:
    s = o.survey
    if not s["passages"]:
        return {"available": False, "reason": "No passages.", "bytes": 0}
    dim = int(s["info"].get("dimension") or 0) if o.semantic else 0
    note = None if o.semantic else "Passages only: this export has no vectors, so there is no vectors.npy."
    return {"available": True, "reason": None, "bytes": s["text_bytes"] + s["passages"] * (140 + dim * 4),
            "note": note}


def _write(ctx: ExportContext) -> Dict[str, Any]:
    docs = {d["id"]: d for d in ctx.documents}
    lines = []
    for row, p in enumerate(ctx.passages):
        doc = docs[p["document_id"]]
        entry = {"row": row, **p, "title": doc.get("title"), "source": doc.get("name"),
                 "url": (doc.get("link") or {}).get("url")}
        lines.append(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
    ctx.put(f"{FOLDER}/passages.jsonl", "\n".join(lines) + "\n")
    embedding = ctx.manifest["embedding"]
    described = {k: embedding.get(k) for k in ("model", "signature", "dimension", "pooling", "normalization",
                                                "metric", "query_prefix", "search")}
    entry: Dict[str, Any] = {"passages": f"{FOLDER}/passages.jsonl", "count": len(lines)}
    if ctx.semantic:
        vectors = np.asarray(ctx.vectors, dtype="<f4")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = (vectors / np.where(norms == 0, 1, norms)).astype("<f4")
        buf = io.BytesIO()
        np.save(buf, vectors, allow_pickle=False)
        ctx.put(f"{FOLDER}/vectors.npy", buf.getvalue())
        entry["vectors"] = f"{FOLDER}/vectors.npy"
        described["browser_model"] = (embedding.get("browser") or {}).get("model")
        from services.embedder import fastembed_model_id

        # The same model under the name the fastembed library knows it by.
        described["fastembed_model"] = fastembed_model_id(embedding.get("model") or "")
    described["rows"] = "passages.jsonl line n is vectors.npy row n"
    ctx.put(f"{FOLDER}/embedding.json", json.dumps(described, indent=2, ensure_ascii=False) + "\n")
    entry["embedding"] = f"{FOLDER}/embedding.json"
    return entry


register(Addition(id="tools", label="Passages and vectors for other tools (JSON lines, NumPy)", offer=_offer,
                  write=_write))

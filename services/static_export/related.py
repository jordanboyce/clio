"""C1: related passages, for <clio-related>: "see also" from other sources.

At export, each passage's nearest passages from other sources are found
from the vectors already in hand, in blocks of rows, so the full similarity
matrix is never held: memory is the vectors plus one block's scores. They
are gathered per source, the best few from other sources and no more than
two from any one, into ``related/<name>.json``. Each entry carries the
passage's id, source, page, score and the start of its text, so a reader
page shows its "see also" from its own small file, without the model, the
vectors or the passage files.

Only for an export searched by meaning, and only in a whole site (the
component ships with the site's scripts).
"""

from __future__ import annotations

import html
import json
from typing import Any, Dict, List

import numpy as np

from services.static_export.additions import PAGE_HOOKS, Addition, ExportContext, register
from services.static_export import reader

FOLDER = "related"
PER_SOURCE = 6          # related passages shown for a source
FROM_ONE_SOURCE = 2     # at most this many from any one other source
PER_PASSAGE = 4         # neighbours looked at for each passage
EXCERPT = 240           # characters of a related passage's text
BLOCK = 64              # rows scored at a time


def neighbours(vectors: np.ndarray, groups: np.ndarray, k: int = PER_PASSAGE, block: int = BLOCK):
    """For each row, the ``k`` most similar rows in another group: (indices,
    scores), each rows x k, best first; -1 where there are fewer. ``vectors``
    are unit length, so the dot product is the cosine. Scored ``block`` rows
    at a time: at most block x rows scores are held at once."""
    n = vectors.shape[0]
    k = max(1, min(k, n))
    out_i = np.full((n, k), -1, dtype=np.int64)
    out_s = np.full((n, k), -np.inf, dtype=np.float32)
    if n == 0:
        return out_i, out_s
    matrix = np.ascontiguousarray(vectors, dtype=np.float32)
    for start in range(0, n, block):
        stop = min(n, start + block)
        scores = matrix[start:stop] @ matrix.T            # block x n
        np.putmask(scores, groups[start:stop, None] == groups[None, :], -np.inf)
        if k < n:
            top = np.argpartition(scores, n - k, axis=1)[:, n - k:]
        else:
            top = np.tile(np.arange(n), (stop - start, 1))
        picked = np.take_along_axis(scores, top, axis=1)
        order = np.argsort(-picked, axis=1, kind="stable")
        out_i[start:stop] = np.take_along_axis(top, order, axis=1)
        out_s[start:stop] = np.take_along_axis(picked, order, axis=1)
        del scores
    out_i[~np.isfinite(out_s)] = -1
    return out_i, out_s


def per_source(passages: List[Dict[str, Any]], idx: np.ndarray, scores: np.ndarray) -> Dict[str, List[Dict[str, Any]]]:
    """Source id -> its related passages: the best score each other passage
    reaches from any of the source's passages, best first, ties by order."""
    best: Dict[str, Dict[int, float]] = {}
    for row, p in enumerate(passages):
        mine = best.setdefault(p["document_id"], {})
        for j, s in zip(idx[row], scores[row]):
            if j < 0:
                continue
            j = int(j)
            if float(s) > mine.get(j, -np.inf):
                mine[j] = float(s)
    result: Dict[str, List[Dict[str, Any]]] = {}
    for doc_id, found in best.items():
        chosen, per_doc = [], {}
        for j, s in sorted(found.items(), key=lambda kv: (-kv[1], kv[0])):
            other = passages[j]["document_id"]
            if per_doc.get(other, 0) >= FROM_ONE_SOURCE:
                continue
            per_doc[other] = per_doc.get(other, 0) + 1
            text = " ".join((passages[j]["text"] or "").split())
            chosen.append({"id": passages[j]["id"], "document_id": other, "page": passages[j]["page"],
                           "score": round(s, 4), "text": text[:EXCERPT] + ("…" if len(text) > EXCERPT else "")})
            if len(chosen) >= PER_SOURCE:
                break
        result[doc_id] = chosen
    return result


def file_names(documents: List[Dict[str, Any]]) -> Dict[str, str]:
    return {doc_id: FOLDER + "/" + page.split("/", 1)[1][:-len(".html")] + ".json"
            for doc_id, page in reader.page_names(documents).items()}


def _offer(o) -> Dict[str, Any]:
    s = o.survey
    if not o.semantic:
        return {"available": False, "reason": "It needs an export searched by meaning: this one is searched by "
                                              "keyword.", "bytes": 0}
    if len(s["documents"]) < 2:
        return {"available": False, "reason": "It needs at least two sources.", "bytes": 0}
    return {"available": True, "reason": None, "bytes": len(s["documents"]) * PER_SOURCE * (EXCERPT + 120)}


def _write(ctx: ExportContext) -> Dict[str, Any]:
    ids = {d["id"]: i for i, d in enumerate(ctx.documents)}
    groups = np.asarray([ids[p["document_id"]] for p in ctx.passages], dtype=np.int64)
    vectors = np.asarray(ctx.vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors = vectors / np.where(norms == 0, 1, norms)
    idx, scores = neighbours(vectors, groups)
    related = per_source(ctx.passages, idx, scores)
    names = file_names(ctx.documents)
    for doc in ctx.documents:
        ctx.put(names[doc["id"]], json.dumps({"source": doc["id"], "related": related.get(doc["id"], [])},
                                             ensure_ascii=False, separators=(",", ":")) + "\n")
    return {"sources": {d["id"]: names[d["id"]] for d in ctx.documents}, "per_source": PER_SOURCE}


def _page_part(ctx: ExportContext, doc):
    if doc is None:
        return {}
    return {"head": '<script type="module" src="../../clio-related.js"></script>\n',
            "body": f'<aside>\n<clio-related src="../" source="{html.escape(doc["id"])}" label="Related passages">'
                    f"</clio-related>\n</aside>\n"}


PAGE_HOOKS["related"] = _page_part
register(Addition(id="related", label="Related passages from other sources", offer=_offer, write=_write,
                  site_only=True))

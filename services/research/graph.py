"""The citation graph of a collection's papers.

Nodes are the papers in the library; an edge A → B means A's reference list
contains an entry matched to B (same DOI, same arXiv id, or B's title
printed in the entry — ``library.match_library``), and every edge says which.
Optionally, works the library does not hold but that several of its papers
cite appear as "missing" nodes: the usual answer to "what should I read
next". Those are grouped only by a printed DOI/arXiv id or a registry title,
so two different works never merge on a guess.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from services.research.extract import title_guess
from services.research.library import is_paper
from services.research.store import ResearchStore

MAX_EXTERNAL_NODES = 60


def _short(text: Optional[str], limit: int = 90) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _external_key(ref: dict) -> Optional[str]:
    if ref.get("doi"):
        return "doi:" + ref["doi"].lower()
    if ref.get("arxiv_id"):
        return "arxiv:" + re.sub(r"v\d+$", "", ref["arxiv_id"])
    if ref.get("title"):  # a registry title, set by a verified lookup
        return "title:" + re.sub(r"[^a-z0-9]+", "", ref["title"].lower())
    # No identifier and no registry record: group on the cited title as
    # printed. Two papers citing the same work in different styles may not
    # merge (an undercount); different works only merge on an identical,
    # long cited title.
    guess = re.sub(r"[^a-z0-9]+", "", title_guess(ref.get("raw_text") or "").lower())
    return "cited:" + guess if len(guess) >= 25 else None


def build_graph(store: ResearchStore, documents: List[dict], include_external: bool = True,
                min_citations: int = 2) -> dict:
    docs = [d for d in documents if is_paper(d)]
    ids = {d["document_id"] for d in docs}
    papers = store.get_papers(ids)
    references = [r for r in store.list_references(kind="reference") if r["document_id"] in ids]

    cited_by: Dict[str, int] = {}
    cites: Dict[str, int] = {}
    edges = []
    seen_edges = set()
    for ref in references:
        target = ref.get("matched_document_id")
        if not target or target not in ids:
            continue
        key = (ref["document_id"], target)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        edges.append({"source": ref["document_id"], "target": target,
                      "reason": ref.get("match_reason") or "", "reference_id": ref["id"],
                      "external": False})
        cited_by[target] = cited_by.get(target, 0) + 1
        cites[ref["document_id"]] = cites.get(ref["document_id"], 0) + 1

    nodes = []
    for doc in docs:
        paper = papers.get(doc["document_id"]) or {}
        nodes.append({
            "id": doc["document_id"],
            "type": "paper",
            "label": _short(paper.get("title") or doc.get("filename")),
            "title": paper.get("title"),
            "filename": doc.get("filename"),
            "authors": paper.get("authors") or [],
            "year": paper.get("year"),
            "venue": paper.get("venue"),
            "abstract": paper.get("abstract"),
            "abstract_source": (paper.get("provenance") or {}).get("abstract"),
            "analyzed": bool(paper.get("analyzed_at")),
            "cited_by": cited_by.get(doc["document_id"], 0),
            "cites": cites.get(doc["document_id"], 0),
        })

    external_nodes: List[dict] = []
    if include_external:
        groups: Dict[str, dict] = {}
        for ref in references:
            if ref.get("matched_document_id"):
                continue
            key = _external_key(ref)
            if not key:
                continue
            group = groups.setdefault(key, {"citers": set(), "refs": []})
            group["citers"].add(ref["document_id"])
            group["refs"].append(ref)
        ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]["citers"]), kv[0]))
        for key, group in ranked:
            if len(group["citers"]) < max(1, min_citations):
                continue
            if len(external_nodes) >= MAX_EXTERNAL_NODES:
                break
            first = group["refs"][0]
            # A registry title when one was verified; otherwise the reference
            # as printed, flagged so the view does not present it as a title.
            label = first.get("title") or title_guess(first.get("raw_text") or "")
            node_id = "ext:" + key
            external_nodes.append({
                "id": node_id,
                "type": "external",
                "label": _short(label),
                "title": first.get("title"),
                "as_cited": first.get("raw_text"),
                "year": first.get("year"),
                "doi": first.get("doi"),
                "arxiv_id": first.get("arxiv_id"),
                "cited_by": len(group["citers"]),
                "reference_ids": [r["id"] for r in group["refs"]],
                "downloadable": any(r.get("resolved_kind") == "pdf" or r.get("doi") or r.get("arxiv_id")
                                    for r in group["refs"]),
            })
            reason = {"doi": "same DOI", "arxiv": "same arXiv id", "title": "same registry title",
                      "cited": "same cited title"}[key.split(":", 1)[0]]
            for citer in sorted(group["citers"]):
                edges.append({"source": citer, "target": node_id, "reason": reason,
                              "reference_id": next(r["id"] for r in group["refs"] if r["document_id"] == citer),
                              "external": True})

    return {
        "nodes": nodes + external_nodes,
        "edges": edges,
        "stats": {
            "papers": len(nodes),
            "analyzed": sum(1 for n in nodes if n["analyzed"]),
            "internal_edges": sum(1 for e in edges if not e["external"]),
            "external_nodes": len(external_nodes),
        },
    }

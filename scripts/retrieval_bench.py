"""Retrieval benchmark: does the reranker (and its pool size) earn its latency?

Indexes the versioned fixture corpus under tests/fixtures/retrieval_bench/
with the real local embedding model (all-MiniLM-L6-v2 via fastembed) and runs
every labelled query through ``DocumentIndexer.search`` — the one retrieval
path the UI, the API and the MCP tools share — under several reranker
configurations. It reports, per configuration:

  recall@5, recall@10, MRR      over the labelled relevant units
  p50 / p95 latency             wall time of ``search`` including the query
                                embedding, sequential, one call at a time
  per-category recall@5         identifier, quoted phrase, negation, date,
                                table lookup, plain question
  top-1 score separation        median retrieval score of the best hit for
                                answerable vs unanswerable queries

Configurations: no reranker; the cross-encoder with candidate multipliers
2, 3 and 5 (pool = top_k x multiplier, capped at 50); and, under eight
parallel callers, the default pool with RERANK_MAX_CONCURRENT=0 (unbounded)
against 2 (the default, with the one-second queue timeout that sheds to
retrieval order), reporting latency, throughput and the shed fraction.

Run it directly (downloads both models on first use if they are not in
FASTEMBED_CACHE_PATH or <repo>/data/models/fastembed):

    venv/Scripts/python scripts/retrieval_bench.py
    venv/Scripts/python scripts/retrieval_bench.py --repeats 3 --json bench.json --out bench.md
    venv/Scripts/python scripts/retrieval_bench.py --fixture tests/fixtures/quality_bench --skip-concurrency

or through pytest, where it is skipped unless asked for:

    CLIO_RETRIEVAL_BENCH=1 venv/Scripts/python -m pytest tests/benchmark -s

The corpus and the labels carry a ``version``; numbers are only comparable
within one version. Add a passage or a query, bump the version, and say so
in the PR that quotes the new numbers.

Other fixtures. ``--fixture tests/fixtures/quality_bench`` runs the same
harness over a synthetic aerospace-supplier quality system (procedures in a
superseded and a current revision, an ECN, a supplier deviation, an NCR log).
A fixture can mark documents ``superseded_by`` another, exactly as an owner
does with "Replaced by", and label ``stale`` passages on ``revision``
queries. The report then adds **revision safety**: the share of revision
queries where the current passage is retrieved and no stale passage ranks
above it, with the marks honoured and with them ignored
(``SUPERSEDED_PENALTY=0``), so the value of recording revisions is measured
rather than asserted.

Caveat for reading the numbers: the corpus is ~90 chunks, so a pool of 50
candidates is more than half the index and recall ceilings sit high. What
transfers to a real collection is the *shape* of the result — whether the
cross-encoder's reordering helps or hurts each query category, and how
latency grows with the pool, which is linear in the candidate count on any
corpus size. Absolute recall does not transfer.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "retrieval_bench"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
CATEGORIES = ("answerable", "identifier", "quoted", "negation", "date", "table", "unanswerable")


# ── Environment ──────────────────────────────────────────────────────────────

def prepare_environment() -> Path:
    """Point the process at a throwaway data dir and a persistent model cache.

    Idempotent, and safe after ``config`` is imported (the pytest conftest
    does the data-dir part itself): only variables that are still unset are
    touched. Returns the model cache directory fastembed will read and, when
    a model is missing, download into.
    """
    os.environ.setdefault("CLIO_ENV_FILE", str(FIXTURE_DIR / ".env.does-not-exist"))
    os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="clio-bench-"))
    os.environ.setdefault("PRIVATE_COLLECTIONS", "false")
    os.environ.setdefault("AUTH_PASSWORD", "")
    os.environ.setdefault("AI_PROVIDER", "")
    cache = os.environ.get("FASTEMBED_CACHE_PATH", "").strip()
    if not cache:
        cache = str(REPO_ROOT / "data" / "models" / "fastembed")
        os.environ["FASTEMBED_CACHE_PATH"] = cache
    Path(cache).mkdir(parents=True, exist_ok=True)
    return Path(cache)


# ── Fixture ──────────────────────────────────────────────────────────────────

@dataclass
class Corpus:
    version: int
    documents: List[dict]
    queries: List[dict]
    queries_version: int
    categories: Sequence[str] = CATEGORIES
    name: str = "retrieval_bench"

    @property
    def supersede_marks(self) -> Dict[str, str]:
        """``{document id: id of the document that replaced it}``."""
        return {d["id"]: d["superseded_by"] for d in self.documents if d.get("superseded_by")}

    @property
    def has_revision_queries(self) -> bool:
        return any(q.get("stale") for q in self.queries)

    # Filled by build_index for documents given as pages: what the chunker
    # made of each, as ``{document id: [(chunk id, normalised text)]}``.
    chunked: Dict[str, List[tuple]] = field(default_factory=dict)

    @property
    def collections(self) -> List[str]:
        return sorted({d.get("collection") or "main" for d in self.documents})

    def document(self, doc_id: str) -> dict:
        for d in self.documents:
            if d["id"] == doc_id:
                return d
        raise KeyError(doc_id)

    def chunk_ids_of(self, doc_id: str) -> List[str]:
        d = self.document(doc_id)
        if "pages" in d:
            return [cid for cid, _ in self.chunked.get(doc_id, [])]
        return [f"{doc_id}#{i}" for i in range(1, len(d["passages"]) + 1)]

    def resolve(self, ref: str) -> List[str]:
        """A labelled unit -> the chunk ids that satisfy it.

        ``doc``: any chunk of the document. ``doc#n``: its n-th passage.
        ``doc::sentence``: a chunk of the document that holds the whole
        sentence (spaces normalised), so the label survives any chunking."""
        if "::" in ref:
            doc_id, _, sentence = ref.partition("::")
            self.document(doc_id)
            wanted = _norm_text(sentence)
            return [cid for cid, text in self.chunked.get(doc_id, []) if wanted in text]
        if "#" in ref:
            doc_id, _, n = ref.partition("#")
            ids = self.chunk_ids_of(doc_id)
            idx = int(n)
            if not 1 <= idx <= len(ids):
                raise KeyError(ref)
            return [ids[idx - 1]]
        return self.chunk_ids_of(ref)


def _norm_text(text: str) -> str:
    return " ".join(text.split())


def load_corpus(fixture_dir: Path = FIXTURE_DIR) -> Corpus:
    corpus = json.loads((fixture_dir / "corpus.json").read_text(encoding="utf-8"))
    queries = json.loads((fixture_dir / "queries.json").read_text(encoding="utf-8"))
    return Corpus(
        version=int(corpus["version"]),
        documents=list(corpus["documents"]),
        queries=list(queries["queries"]),
        queries_version=int(queries["version"]),
        categories=tuple(queries.get("categories") or CATEGORIES),
        name=Path(fixture_dir).name,
    )


def validate_corpus(corpus: Corpus) -> List[str]:
    """Every problem with the fixture, as one line each; empty when sound."""
    problems: List[str] = []
    doc_ids = [d["id"] for d in corpus.documents]
    if len(set(doc_ids)) != len(doc_ids):
        problems.append("duplicate document ids")
    for d in corpus.documents:
        units = d.get("passages") or d.get("pages")
        if not units or any(not p.strip() for p in units):
            problems.append(f"{d['id']}: empty passage list or blank passage")
    collections = set(corpus.collections)
    qids = [q["id"] for q in corpus.queries]
    if len(set(qids)) != len(qids):
        problems.append("duplicate query ids")
    seen_categories = set()
    for q in corpus.queries:
        seen_categories.add(q["category"])
        if q["category"] not in corpus.categories:
            problems.append(f"{q['id']}: unknown category {q['category']!r}")
        if (q["category"] == "unanswerable") != (len(q["relevant"]) == 0):
            problems.append(f"{q['id']}: unanswerable queries and only they have no relevant units")
        where = q.get("collection")
        if where and where != "*" and where not in collections:
            problems.append(f"{q['id']}: collection {where!r} holds no documents")
        for ref in q["relevant"]:
            problem = _check_ref(corpus, ref)
            if problem:
                problems.append(f"{q['id']}: relevant unit {ref!r} {problem}")
        stale = q.get("stale") or []
        if stale and q["category"] != "revision":
            problems.append(f"{q['id']}: only revision queries carry stale units")
        if q["category"] == "revision" and (not stale or not q["relevant"]):
            problems.append(f"{q['id']}: a revision query needs relevant and stale units")
        for ref in stale:
            problem = _check_ref(corpus, ref)
            if problem:
                problems.append(f"{q['id']}: stale unit {ref!r} {problem}")
                continue
            if ref in q["relevant"] or ("::" not in ref and set(corpus.resolve(ref)) & {
                    c for r in q["relevant"] if "::" not in r for c in corpus.resolve(r)}):
                problems.append(f"{q['id']}: {ref!r} is labelled both relevant and stale")
    known = set(doc_ids)
    for old, new in corpus.supersede_marks.items():
        if new not in known:
            problems.append(f"{old}: superseded_by {new!r} is not a document")
        elif new == old:
            problems.append(f"{old}: a document cannot replace itself")
    missing = set(corpus.categories) - seen_categories
    if missing:
        problems.append(f"no queries in categories: {sorted(missing)}")
    return problems


def _check_ref(corpus: Corpus, ref: str) -> Optional[str]:
    """Why a label points at nothing, or None. A sentence label is checked
    against the document's own text, before any chunking."""
    try:
        if "::" in ref:
            doc_id, _, sentence = ref.partition("::")
            d = corpus.document(doc_id)
            if _norm_text(sentence) not in _norm_text(" ".join(d.get("pages") or d.get("passages"))):
                return "is not in that document"
            return None
        corpus.resolve(ref)
    except (KeyError, ValueError):
        return "does not exist"
    return None


# ── Index ────────────────────────────────────────────────────────────────────

def build_index(corpus: Corpus, index_dir: Path, cache_dir: Path, embedder=None,
                model_name: str = EMBEDDING_MODEL, recipe: Optional[int] = None):
    """Index every passage as one chunk with the real local embedder.
    Returns the DocumentIndexer whose ``search`` the benchmark calls.

    ``embedder`` replaces the real model (tests pass a stub so the wiring can
    be checked offline); it needs ``embed_texts``, ``embed_query``,
    ``model_name`` and ``embedding_dim``."""
    from unittest.mock import MagicMock

    from models.schemas import ChunkMetadata
    from services.indexing.indexer import DocumentIndexer
    from services.vector_store import VectorStore

    if embedder is None:
        from services.embedder import FastEmbedService
        embedder = FastEmbedService(model_name, cache_dir=cache_dir)
    indexers = {}
    for collection in corpus.collections:
        documents = [d for d in corpus.documents if (d.get("collection") or "main") == collection]
        target = index_dir if len(corpus.collections) == 1 else Path(index_dir) / collection
        store = VectorStore(target, embedding_dim=getattr(embedder, "embedding_dim", 384))
        chunks, texts = _chunks_for(corpus, documents, ChunkMetadata, recipe)
        vectors = embedder.embed_texts(texts)
        store.add_chunks(chunks, vectors)
        # Recorded as the app records it, so search (the reranker's input)
        # follows the same recipe.
        from services.indexing import embedding_text
        embedding_text.write_recipe(store.metadata_store, corpus.recipe)
        apply_supersede_marks(corpus, store.metadata_store, documents)
        indexers[collection] = DocumentIndexer(
            vector_store=store,
            embedding_service=embedder,
            document_extractor=MagicMock(),
            text_chunker=MagicMock(),
        )
    return indexers[corpus.collections[0]] if len(indexers) == 1 else indexers


def _chunks_for(corpus: Corpus, documents: List[dict], ChunkMetadata, recipe: Optional[int] = None) -> tuple:
    """The chunks to index and the text to embed for each.

    A document given as passages is one chunk per passage. A document given
    as pages goes through the app's own chunker at the configured size. Both
    are embedded with the text the indexer would embed under ``recipe``
    (services/indexing/embedding_text.py; default: what a new collection
    gets)."""
    from config import settings
    from services.chunker import TextChunker
    from services.indexing import embedding_text

    recipe = embedding_text.CURRENT_RECIPE if recipe is None else recipe
    corpus.recipe = recipe
    chunker = TextChunker(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap,
                          continuous=recipe >= embedding_text.HEADED)
    chunks: List[Any] = []
    texts: List[str] = []
    for d in documents:
        if "pages" in d:
            made = chunker.chunk_document(
                {i: p for i, p in enumerate(d["pages"], start=1)}, d["id"], d["filename"],
                source_format=d.get("format") or "pdf", extraction_method="text",
            )
            corpus.chunked[d["id"]] = [(c.chunk_id, _norm_text(c.text)) for c in made]
            chunks.extend(made)
            texts.extend(embedding_text.texts_for_embedding(made, recipe=recipe))
            continue
        for i, text in enumerate(d["passages"], start=1):
            chunks.append(ChunkMetadata(
                chunk_id=f"{d['id']}#{i}",
                document_id=d["id"],
                filename=d["filename"],
                page_number=i,
                chunk_index=0,
                text=text,
                source_format=d.get("format"),
                extraction_method="text",
            ))
            texts.append(embedding_text.texts_for_embedding([chunks[-1]], recipe=recipe)[0])
    return chunks, texts


def apply_supersede_marks(corpus: Corpus, metadata_store, documents: Optional[List[dict]] = None) -> int:
    """Record the fixture's ``superseded_by`` marks the way an owner would.

    The replaced-by relation lives on the documents table, which chunk
    ingestion alone does not fill, so each document is registered first.
    Returns the number of marks applied.
    """
    documents = corpus.documents if documents is None else documents
    here = {d["id"] for d in documents}
    marks = {old: new for old, new in corpus.supersede_marks.items() if old in here and new in here}
    if not marks:
        return 0
    stamp = "2026-01-01T00:00:00+00:00"
    for d in documents:
        units = d.get("passages") or d.get("pages")
        metadata_store.add_document(
            d["id"], d["filename"], num_pages=len(units),
            num_chunks=len(corpus.chunked.get(d["id"], units)), upload_timestamp=stamp,
            source_format=d.get("format"),
        )
    for old, new in marks.items():
        metadata_store.set_document_superseded(old, new, "fixture mark", at=stamp)
    return len(marks)


def ensure_reranker_model(cache_dir: Path) -> str:
    """Make sure the cross-encoder is on disk (downloading once if allowed)
    and return the backend that serves it."""
    from config import settings
    from services import reranker

    if not reranker.model_cached(settings.reranker_model):
        previous = os.environ.get("CLIO_MODEL_BAKE_DIR")
        os.environ["CLIO_MODEL_BAKE_DIR"] = str(cache_dir)
        try:
            reranker.prefetch(settings.reranker_model)
        finally:
            if previous is None:
                os.environ.pop("CLIO_MODEL_BAKE_DIR", None)
            else:
                os.environ["CLIO_MODEL_BAKE_DIR"] = previous
    reranker._auto_cache.clear()
    return reranker.resolve_backend(settings.reranker_model) or "none"


# ── Metrics ──────────────────────────────────────────────────────────────────

@dataclass
class QueryOutcome:
    query_id: str
    category: str
    hit_ranks: List[int]          # 1-based rank of the first hit for each relevant unit (missing = not retrieved)
    units: int                    # number of labelled units
    latency_ms: float
    reranked: bool
    top1_score: Optional[float]
    # Revision queries only: rank of the first stale passage (None = not
    # retrieved) and whether the query had stale labels at all.
    stale_rank: Optional[int] = None
    has_stale: bool = False

    @property
    def revision_safe(self) -> Optional[bool]:
        """The current passage was retrieved and no stale one ranks above it."""
        if not self.has_stale:
            return None
        if not self.hit_ranks:
            return False
        first = min(self.hit_ranks)
        return self.stale_rank is None or self.stale_rank > first

    def recall_at(self, k: int) -> Optional[float]:
        if self.units == 0:
            return None
        return sum(1 for r in self.hit_ranks if r <= k) / self.units

    @property
    def reciprocal_rank(self) -> Optional[float]:
        if self.units == 0:
            return None
        return _metrics.reciprocal_rank(min(self.hit_ranks) if self.hit_ranks else None)


def first_stale_rank(corpus: Corpus, query: dict, result_ids: Sequence[str]) -> Optional[int]:
    """Rank (1-based) of the first result that is a labelled stale unit."""
    stale = {c for ref in query.get("stale") or [] for c in corpus.resolve(ref)}
    for rank, cid in enumerate(result_ids, start=1):
        if cid in stale:
            return rank
    return None


def score_results(corpus: Corpus, query: dict, result_ids: Sequence[str]) -> List[int]:
    """For each labelled unit, the rank (1-based) at which it first appears."""
    ranks: List[int] = []
    for ref in query["relevant"]:
        wanted = set(corpus.resolve(ref))
        for rank, cid in enumerate(result_ids, start=1):
            if cid in wanted:
                ranks.append(rank)
                break
    return ranks


# The rank arithmetic is shared with the per-collection coverage report
# (services/coverage_report.py), so both quote numbers computed one way.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from services import retrieval_metrics as _metrics  # noqa: E402

percentile = _metrics.percentile
_mean = _metrics.mean


@dataclass
class ConfigResult:
    name: str
    settings: Dict[str, Any]
    outcomes: List[QueryOutcome] = field(default_factory=list)
    wall_seconds: float = 0.0
    parallel: int = 1
    categories: Sequence[str] = CATEGORIES

    @property
    def revision_outcomes(self) -> List[QueryOutcome]:
        return [o for o in self.outcomes if o.has_stale]

    @property
    def revision_safety(self) -> Optional[float]:
        """Share of revision queries answered from the current revision first."""
        pool = self.revision_outcomes
        return sum(1 for o in pool if o.revision_safe) / len(pool) if pool else None

    @property
    def stale_top1(self) -> Optional[float]:
        """Share of revision queries whose top result is a stale passage."""
        pool = self.revision_outcomes
        return sum(1 for o in pool if o.stale_rank == 1) / len(pool) if pool else None

    @property
    def answerable(self) -> List[QueryOutcome]:
        return [o for o in self.outcomes if o.units > 0]

    def recall(self, k: int, category: Optional[str] = None) -> Optional[float]:
        pool = self.answerable if category is None else [o for o in self.answerable if o.category == category]
        return _mean(o.recall_at(k) for o in pool)

    def mrr(self, category: Optional[str] = None) -> Optional[float]:
        pool = self.answerable if category is None else [o for o in self.answerable if o.category == category]
        return _mean(o.reciprocal_rank for o in pool)

    def latency(self, pct: float) -> float:
        return percentile([o.latency_ms for o in self.outcomes], pct)

    @property
    def shed_fraction(self) -> Optional[float]:
        if not self.settings.get("enable_reranker"):
            return None
        return sum(1 for o in self.outcomes if not o.reranked) / len(self.outcomes)

    @property
    def throughput(self) -> float:
        return len(self.outcomes) / self.wall_seconds if self.wall_seconds else float("nan")

    def top1_median(self, answerable: bool) -> Optional[float]:
        scores = [o.top1_score for o in self.outcomes
                  if (o.units > 0) == answerable and o.top1_score is not None]
        return statistics.median(scores) if scores else None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "settings": self.settings,
            "parallel": self.parallel,
            "queries": len(self.outcomes),
            "recall@5": self.recall(5),
            "recall@10": self.recall(10),
            "mrr": self.mrr(),
            "p50_ms": self.latency(0.50),
            "p95_ms": self.latency(0.95),
            "shed_fraction": self.shed_fraction,
            "wall_seconds": self.wall_seconds,
            "throughput_qps": self.throughput,
            "top1_median_answerable": self.top1_median(True),
            "top1_median_unanswerable": self.top1_median(False),
            "revision_safety": self.revision_safety,
            "stale_top1": self.stale_top1,
            "by_category": {
                c: {"recall@5": self.recall(5, c), "recall@10": self.recall(10, c), "mrr": self.mrr(c)}
                for c in self.categories if c != "unanswerable"
            },
            "per_query": [
                {"id": o.query_id, "category": o.category, "hit_ranks": o.hit_ranks,
                 "latency_ms": round(o.latency_ms, 1), "reranked": o.reranked,
                 **({"stale_rank": o.stale_rank, "revision_safe": o.revision_safe} if o.has_stale else {})}
                for o in self.outcomes
            ],
        }


# ── Running ──────────────────────────────────────────────────────────────────

class _SettingsScope:
    """Apply reranker settings on the live ``config.settings`` and restore them."""

    KEYS = ("enable_reranker", "reranker_candidate_multiplier",
            "rerank_max_concurrent", "rerank_queue_timeout_seconds", "superseded_penalty")

    def __init__(self, **overrides):
        self.overrides = overrides
        self._saved: Dict[str, Any] = {}

    def __enter__(self):
        from config import settings
        for key in self.KEYS:
            self._saved[key] = getattr(settings, key, None)
        for key, value in self.overrides.items():
            setattr(settings, key, value)
        return self

    def __exit__(self, *exc):
        from config import settings
        for key, value in self._saved.items():
            setattr(settings, key, value)
        return False


def _search(indexer, query: dict, top_k: int) -> tuple:
    """``(results, reranked)`` for one query. ``indexer`` is one indexer or
    ``{collection: indexer}``; a query names its collection, and ``*``
    searches them all through the app's own merge."""
    if not isinstance(indexer, dict):
        out = indexer.search(query["query"], top_k=top_k)
        return out["results"], bool(out.get("retrieval", {}).get("reranked"))
    where = query.get("collection") or next(iter(indexer))
    if where != "*":
        out = indexer[where].search(query["query"], top_k=top_k)
        return out["results"], bool(out.get("retrieval", {}).get("reranked"))
    from services.collection_search import search_collections
    hits = search_collections(sorted(indexer.items()), query["query"], top_k)
    return [r for r, _ in hits], None


def _one_query(indexer, corpus: Corpus, query: dict, top_k: int) -> QueryOutcome:
    started = time.perf_counter()
    results, reranked = _search(indexer, query, top_k)
    elapsed = (time.perf_counter() - started) * 1000.0
    ids = [r.chunk_id for r in results]
    return QueryOutcome(
        query_id=query["id"],
        category=query["category"],
        hit_ranks=score_results(corpus, query, ids),
        units=len(query["relevant"]),
        latency_ms=elapsed,
        reranked=bool(reranked) if reranked is not None else bool(overrides_reranker()),
        top1_score=float(results[0].similarity_score) if results else None,
        stale_rank=first_stale_rank(corpus, query, ids),
        has_stale=bool(query.get("stale")),
    )


def overrides_reranker() -> bool:
    from config import settings
    return bool(settings.enable_reranker)


def run_config(indexer, corpus: Corpus, name: str, *, top_k: int, repeats: int,
               parallel: int, queries: Optional[Sequence[dict]] = None, **overrides) -> ConfigResult:
    result = ConfigResult(name=name, settings=dict(overrides), parallel=parallel,
                          categories=corpus.categories)
    chosen = list(queries) if queries is not None else corpus.queries
    with _SettingsScope(**overrides):
        # Warm-up: lazy model load and first-call imports must not count.
        _search(indexer, corpus.queries[0], top_k)
        jobs = [q for _ in range(repeats) for q in chosen]
        started = time.perf_counter()
        if parallel <= 1:
            for q in jobs:
                result.outcomes.append(_one_query(indexer, corpus, q, top_k))
        else:
            lock = threading.Lock()

            def _run(q):
                outcome = _one_query(indexer, corpus, q, top_k)
                with lock:
                    result.outcomes.append(outcome)

            with ThreadPoolExecutor(max_workers=parallel) as pool:
                list(pool.map(_run, jobs))
        result.wall_seconds = time.perf_counter() - started
    return result


@dataclass
class BenchmarkReport:
    environment: Dict[str, Any]
    sequential: List[ConfigResult]
    concurrent: List[ConfigResult]
    total_seconds: float

    def to_dict(self) -> dict:
        return {
            "environment": self.environment,
            "total_seconds": self.total_seconds,
            "sequential": [c.to_dict() for c in self.sequential],
            "concurrent": [c.to_dict() for c in self.concurrent],
        }


def _fusion() -> str:
    from services import vector_store
    return vector_store.HYBRID_FUSION


def _chunking() -> tuple:
    from config import settings
    return settings.chunk_size, settings.chunk_overlap


def _environment(corpus: Corpus, backend: str, cache_dir: Path,
                 model_name: str = EMBEDDING_MODEL) -> Dict[str, Any]:
    import subprocess

    env: Dict[str, Any] = {
        "fixture": corpus.name,
        "corpus_version": corpus.version,
        "queries_version": corpus.queries_version,
        "documents": len(corpus.documents),
        "collections": len(corpus.collections),
        "chunks": sum(len(corpus.chunked[d["id"]]) if "pages" in d else len(d["passages"])
                      for d in corpus.documents),
        "recipe": getattr(corpus, "recipe", None),
        "fusion": _fusion(),
        "chunk_size": _chunking()[0],
        "chunk_overlap": _chunking()[1],
        "queries": len(corpus.queries),
        "supersede_marks": len(corpus.supersede_marks),
        "embedding_model": model_name,
        "reranker_backend": backend,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpus": os.cpu_count(),
        "model_cache": str(cache_dir),
    }
    from config import settings
    env["reranker_model"] = settings.reranker_model
    for mod in ("fastembed", "onnxruntime"):
        try:
            env[mod] = __import__(mod).__version__
        except Exception:  # noqa: BLE001 - version is informational
            env[mod] = None
    try:
        env["git"] = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip() or None
    except Exception:  # noqa: BLE001
        env["git"] = None
    return env


def run_benchmark(*, top_k: int = 10, repeats: int = 1, multipliers: Sequence[int] = (2, 3, 5),
                  parallel: int = 8, concurrency_multiplier: int = 3, concurrency_queries: int = 40,
                  skip_concurrency: bool = False, index_dir: Optional[Path] = None,
                  fixture_dir: Path = FIXTURE_DIR, embedding_model: str = EMBEDDING_MODEL,
                  recipe: Optional[int] = None,
                  log: Callable[[str], None] = lambda s: None) -> BenchmarkReport:
    """Build the index once and run every configuration against it.

    The concurrency runs measure latency, throughput and shedding, not
    recall, so they take an evenly spaced subset of ``concurrency_queries``
    queries (0 = all). The whole run is dominated by the cross-encoder
    passes (about 10 ms per candidate pair on a 12-core desktop): roughly
    three minutes with the default multipliers, under a minute with
    ``--multipliers 3``.
    """
    total_started = time.perf_counter()
    cache_dir = prepare_environment()
    logging.getLogger("services").setLevel(logging.WARNING)
    logging.getLogger("fastembed").setLevel(logging.WARNING)

    corpus = load_corpus(fixture_dir)
    problems = validate_corpus(corpus)
    if problems:
        raise ValueError("fixture is inconsistent:\n  " + "\n  ".join(problems))

    workdir = index_dir or Path(tempfile.mkdtemp(prefix="clio-bench-index-"))
    log(f"indexing {len(corpus.documents)} documents with {embedding_model} ...")
    indexer = build_index(corpus, workdir / "index", cache_dir, model_name=embedding_model, recipe=recipe)
    log("checking the reranker model ...")
    backend = ensure_reranker_model(cache_dir)
    env = _environment(corpus, backend, cache_dir, embedding_model)
    env["top_k"] = top_k

    sequential: List[ConfigResult] = []
    log("no reranker ...")
    sequential.append(run_config(indexer, corpus, "no reranker", top_k=top_k, repeats=repeats,
                                 parallel=1, enable_reranker=False))
    for mult in multipliers:
        log(f"reranker x{mult} ...")
        sequential.append(run_config(
            indexer, corpus, f"reranker x{mult}", top_k=top_k, repeats=repeats, parallel=1,
            enable_reranker=True, reranker_candidate_multiplier=mult, rerank_max_concurrent=0,
        ))
    if corpus.supersede_marks and corpus.has_revision_queries:
        # Same index, marks still recorded, demotion switched off: what search
        # does for a collection where nobody has recorded "Replaced by".
        log("revision marks ignored ...")
        sequential.append(run_config(indexer, corpus, "no reranker, marks ignored", top_k=top_k,
                                     repeats=repeats, parallel=1, enable_reranker=False,
                                     superseded_penalty=0.0))
        mult = 3 if 3 in multipliers else (list(multipliers) or [3])[0]
        sequential.append(run_config(
            indexer, corpus, f"reranker x{mult}, marks ignored", top_k=top_k, repeats=repeats,
            parallel=1, enable_reranker=True, reranker_candidate_multiplier=mult,
            rerank_max_concurrent=0, superseded_penalty=0.0,
        ))

    concurrent: List[ConfigResult] = []
    if not skip_concurrency:
        subset = corpus.queries
        if 0 < concurrency_queries < len(corpus.queries):
            step = len(corpus.queries) / concurrency_queries
            subset = [corpus.queries[int(i * step)] for i in range(concurrency_queries)]
        for max_conc in (0, 2):
            log(f"{parallel} parallel callers, RERANK_MAX_CONCURRENT={max_conc} ...")
            concurrent.append(run_config(
                indexer, corpus, f"x{concurrency_multiplier}, {parallel} callers, max_concurrent={max_conc}",
                top_k=top_k, repeats=repeats, parallel=parallel, queries=subset,
                enable_reranker=True, reranker_candidate_multiplier=concurrency_multiplier,
                rerank_max_concurrent=max_conc, rerank_queue_timeout_seconds=1.0,
            ))

    return BenchmarkReport(environment=env, sequential=sequential, concurrent=concurrent,
                           total_seconds=time.perf_counter() - total_started)


# ── Reporting ────────────────────────────────────────────────────────────────

def _fmt(v: Optional[float], digits: int = 3) -> str:
    if v is None:
        return "-"
    if v != v:  # NaN
        return "-"
    return f"{v:.{digits}f}"


def _pct(v: Optional[float]) -> str:
    return "-" if v is None else f"{100 * v:.0f}%"


def render_markdown(report: BenchmarkReport) -> str:
    env = report.environment
    lines = [
        f"### Retrieval benchmark: {env.get('fixture', 'retrieval_bench')} "
        f"(corpus v{env['corpus_version']}, queries v{env['queries_version']})",
        "",
        f"{env['documents']} documents / {env['chunks']} chunks / {env['queries']} queries; "
        f"indexing recipe {env.get('recipe')}, fusion {env.get('fusion')}; "
        f"embedding {env['embedding_model']}, reranker {env['reranker_model']} ({env['reranker_backend']}); "
        f"fastembed {env.get('fastembed')}, onnxruntime {env.get('onnxruntime')}; "
        f"{env['cpus']} CPUs, {env['platform']}, Python {env['python']}, git {env.get('git')}; "
        f"total {report.total_seconds:.0f} s.",
        "",
        f"**Sequential (one query at a time, top_k={env.get('top_k', 10)})**",
        "",
        "| configuration | recall@5 | recall@10 | MRR | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|",
    ]
    for c in report.sequential:
        lines.append(f"| {c.name} | {_fmt(c.recall(5))} | {_fmt(c.recall(10))} | {_fmt(c.mrr())} "
                     f"| {c.latency(0.5):.0f} | {c.latency(0.95):.0f} |")
    categories = report.sequential[0].categories if report.sequential else CATEGORIES
    cats = [c for c in categories if c != "unanswerable"]
    lines += ["", "**Recall@5 by query category**", "",
              "| configuration | " + " | ".join(cats) + " |",
              "|---|" + "---|" * len(cats)]
    for c in report.sequential:
        lines.append(f"| {c.name} | " + " | ".join(_fmt(c.recall(5, cat), 2) for cat in cats) + " |")
    lines += ["", "**MRR by query category**", "",
              "| configuration | " + " | ".join(cats) + " |",
              "|---|" + "---|" * len(cats)]
    for c in report.sequential:
        lines.append(f"| {c.name} | " + " | ".join(_fmt(c.mrr(cat), 2) for cat in cats) + " |")
    lines += ["", "**Top-1 retrieval score, median (answerable vs unanswerable)**", "",
              "| configuration | answerable | unanswerable |", "|---|---|---|"]
    for c in report.sequential:
        lines.append(f"| {c.name} | {_fmt(c.top1_median(True))} | {_fmt(c.top1_median(False))} |")
    if any(c.revision_safety is not None for c in report.sequential):
        n_rev = len(report.sequential[0].revision_outcomes)
        lines += ["", f"**Revision safety ({n_rev} revision queries, top_k={report.environment.get('top_k', 10)})**", "",
                  "Safe = the current passage is retrieved and no superseded or informal passage "
                  "stating the old value ranks above it.", "",
                  "| configuration | revision-safe | stale passage ranked first |", "|---|---|---|"]
        for c in report.sequential:
            lines.append(f"| {c.name} | {_pct(c.revision_safety)} | {_pct(c.stale_top1)} |")
    if report.concurrent:
        lines += ["", f"**Concurrent ({report.concurrent[0].parallel} parallel callers, reranker on)**", "",
                  "| configuration | recall@5 | recall@10 | p50 ms | p95 ms | queries/s | shed |",
                  "|---|---|---|---|---|---|---|"]
        for c in report.concurrent:
            lines.append(f"| {c.name} | {_fmt(c.recall(5))} | {_fmt(c.recall(10))} | {c.latency(0.5):.0f} "
                         f"| {c.latency(0.95):.0f} | {c.throughput:.1f} | {_pct(c.shed_fraction)} |")
    misses = _misses(report)
    if misses:
        lines += ["", "**Queries missed at @5 (first hit rank or –)**", "",
                  "| query | category | " + " | ".join(c.name for c in report.sequential) + " |",
                  "|---|---|" + "---|" * len(report.sequential)]
        lines += misses
    return "\n".join(lines) + "\n"


def _misses(report: BenchmarkReport) -> List[str]:
    """Queries that at least one sequential configuration misses at @5."""
    rows = []
    by_config = [{o.query_id: o for o in c.outcomes} for c in report.sequential]
    for q in report.sequential[0].outcomes:
        if q.units == 0:
            continue
        cells = []
        missed = False
        for table in by_config:
            o = table.get(q.query_id)
            ranks = sorted(o.hit_ranks) if o else []
            if len(ranks) < q.units or any(r > 5 for r in ranks):
                missed = True
            cells.append(",".join(str(r) for r in ranks) if ranks else "-")
        if missed:
            rows.append(f"| {q.query_id} | {q.category} | " + " | ".join(cells) + " |")
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--fixture", type=Path, default=FIXTURE_DIR,
                        help="fixture directory holding corpus.json and queries.json "
                             "(e.g. tests/fixtures/quality_bench)")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=1, help="run each query this many times per configuration")
    parser.add_argument("--multipliers", default="2,3,5", help="candidate multipliers to benchmark sequentially")
    parser.add_argument("--parallel", type=int, default=8, help="parallel callers for the concurrency runs")
    parser.add_argument("--concurrency-multiplier", type=int, default=3)
    parser.add_argument("--concurrency-queries", type=int, default=40,
                        help="evenly spaced subset of queries for the concurrency runs (0 = all)")
    parser.add_argument("--skip-concurrency", action="store_true")
    parser.add_argument("--json", type=Path, help="write the raw numbers here")
    parser.add_argument("--out", type=Path, help="write the markdown report here (also printed)")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--fusion", choices=("sum", "rrf"), default=None,
                        help="hybrid fusion (services/vector_store.py HYBRID_FUSION); default: the current one")
    parser.add_argument("--recipe", type=int, default=None,
                        help="indexing recipe (services/indexing/embedding_text.py); default: the current one")
    parser.add_argument("--embedding-model", default=EMBEDDING_MODEL,
                        help="fastembed model to index with (the images bake all-MiniLM-L6-v2 or BAAI/bge-small-en-v1.5)")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(REPO_ROOT))
    prepare_environment()
    logging.basicConfig(level=logging.WARNING)
    if args.fusion:
        from services import vector_store
        vector_store.HYBRID_FUSION = args.fusion
    log = (lambda s: None) if args.quiet else (lambda s: print(s, file=sys.stderr, flush=True))

    report = run_benchmark(
        top_k=args.top_k, repeats=args.repeats,
        multipliers=[int(m) for m in args.multipliers.split(",") if m.strip()],
        parallel=args.parallel, concurrency_multiplier=args.concurrency_multiplier,
        concurrency_queries=args.concurrency_queries,
        skip_concurrency=args.skip_concurrency, fixture_dir=args.fixture,
        embedding_model=args.embedding_model, recipe=args.recipe, log=log,
    )
    markdown = render_markdown(report)
    print(markdown)
    if args.out:
        args.out.write_text(markdown, encoding="utf-8")
    if args.json:
        args.json.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

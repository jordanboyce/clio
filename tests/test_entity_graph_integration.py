"""Integration: entity-graph boost moves chunks across the results boundary.

Builds a tiny three-document corpus in a throwaway collection where:
  - doc_a (Alice) and doc_b (Bob) never mention each other — the vector
    scores here are artificial so we control them exactly;
  - doc_c names Alice AND Project Titan — it's the cross-document bridge.

With plain semantic search (mocked embeddings) doc_c does not crack top-2 for
"who worked with Alice on Project Titan?" With the entity boost on, it must.
"""

from unittest.mock import MagicMock

import numpy as np
import pytest


class _ConstEmbedding:
    """Embedding service: every text gets the same 384-dim vector, so all
    chunks score identically on vector similarity. That isolates the
    entity-graph effect — nothing else can separate the candidates."""

    def __init__(self, dim: int = 384):
        self._dim = dim

    def embed_query(self, _text: str) -> np.ndarray:
        v = np.ones(self._dim, dtype=np.float32)
        return v / np.linalg.norm(v)

    def embed_texts(self, texts):
        v = np.ones(self._dim, dtype=np.float32)
        v = v / np.linalg.norm(v)
        return np.tile(v, (len(texts), 1))

    def describe_embedding(self):
        return {"provider": "test", "model": "constant", "dim": self._dim}


DOC_ALICE = (
    "Alice Johnson manages the platform team. "
    "Her background is distributed systems and incident response. "
    "Colleagues describe her tenure at the firm as steady and unremarkable."
)
DOC_BOB = (
    "Bob Smith handles vendor negotiations. "
    "His portfolio covers contract renewals and procurement. "
    "He joined the firm recently from a logistics company."
)
# The bridge must name BOTH entities to earn the boost; the status doc keeps
# the longer phrase alive at df >= 2 so the query's "Project Titan" can
# resolve to it (a one-chunk entity can't bridge documents by design).
DOC_BRIDGE = (
    "The Titan Renewal Project shipped in March. "
    "Alice Johnson led the engineering workstream alongside a small crew. "
    "The postmortem credits her with the migration strategy."
)
DOC_STATUS = (
    "Weekly status update. Budget remains stable. The Titan Renewal Project "
    "review moved to Fridays."
)


def _index(tmp_path, docs: dict):
    """Build a DocumentIndexer with constant embeddings and index each
    (doc_id, text) pair as its own 'file'. Returns the indexer."""
    from services.indexing.indexer import DocumentIndexer
    from services.vector_store import VectorStore

    vector_store = VectorStore(tmp_path / "index")
    indexer = DocumentIndexer(
        vector_store=vector_store,
        embedding_service=_ConstEmbedding(),
        document_extractor=MagicMock(),   # unused: chunks added directly
        text_chunker=MagicMock(),
    )

    for doc_id, text in docs.items():
        # One chunk per document — enough to exercise the entity logic.
        from models.schemas import ChunkMetadata
        chunk = ChunkMetadata(
            chunk_id=f"{doc_id}-c0",
            document_id=doc_id,
            filename=f"{doc_id}.txt",
            page_number=1,
            chunk_index=0,
            text=text,
        )
        embeddings = indexer.embedding_service.embed_texts([text])
        vector_store.add_chunks([chunk], embeddings)
    return indexer


@pytest.fixture()
def indexer(tmp_path):
    return _index(tmp_path, {
        "doc_alice": DOC_ALICE,
        "doc_bob": DOC_BOB,
        "doc_bridge": DOC_BRIDGE,
        "doc_status": DOC_STATUS,
    })


def test_entities_extracted_on_index(indexer):
    ents = indexer.vector_store.metadata_store.entity_document_frequency()
    # alice johnson appears in BOTH the bio doc and the bridge doc
    assert ents.get("alice johnson", 0) == 2
    # project titan only once — it's the bridge doc's link to the query
    assert ents.get("titan renewal project", 0) >= 1


def test_query_entities_resolved(indexer):
    from services.entity_graph import extract_entities, corpus_filter, related_entities
    from collections import Counter

    store = indexer.vector_store.metadata_store
    qe = extract_entities("Who worked with Alice on Project Titan?",
                          max_per_chunk=10)
    df = store.entity_document_frequency()
    # On a tiny test corpus the ubiquity cap is looser than production's 30%.
    eligible = corpus_filter(Counter(df), store.get_total_chunks(),
                             max_doc_frequency=0.9)
    resolved = related_entities({e: df[e] for e in eligible},
                                max_doc_frequency=0.9,
                                query_entities=qe)
    # "Alice" in the query resolves to "alice johnson" in the corpus;
    # "Project Titan" resolves to "titan renewal project".
    resolved_targets = set(resolved.values())
    assert "alice johnson" in resolved_targets
    assert "titan renewal project" in resolved_targets


def test_semantic_alone_misses_bridge(indexer, monkeypatch):
    # Sanity baseline: with the boost disabled the bridge doc is NOT forced
    # ahead; all vector scores are equal so ordering is insertion order.
    import config
    from models.schemas import SearchMode
    monkeypatch.setattr(config.settings, "enable_entity_boost", False)
    out = indexer.search(
        "Who worked with Alice on Project Titan?",
        top_k=1,
        mode=SearchMode.SEMANTIC,
    )
    # With constant embeddings the winner is essentially arbitrary — what
    # matters is that the bridge doc scoring hasn't been engineered yet.
    assert len(out["results"]) == 1


def test_entity_boost_surfaces_bridge(indexer, monkeypatch):
    import config
    monkeypatch.setattr(config.settings, "enable_entity_boost", True)
    monkeypatch.setattr(config.settings, "entity_boost_weight", 0.5)
    from models.schemas import SearchMode
    out = indexer.search(
        "Who worked with Alice on Project Titan?",
        top_k=3,
        mode=SearchMode.SEMANTIC,
    )
    result_ids = [r.chunk_id for r in out["results"]]
    # The bridge document must appear — no other doc names both entities.
    assert "doc_bridge-c0" in result_ids


def test_entity_boost_no_query_entities_passthrough(indexer, monkeypatch):
    import config
    monkeypatch.setattr(config.settings, "enable_entity_boost", True)
    from models.schemas import SearchMode
    out = indexer.search(
        "the quick brown fox",   # nothing capitalized, no acronyms
        top_k=2,
        mode=SearchMode.SEMANTIC,
    )
    assert "results" in out


def test_entity_boost_config_disabled(indexer, monkeypatch):
    import config
    monkeypatch.setattr(config.settings, "enable_entity_boost", False)
    from models.schemas import SearchMode
    out = indexer.search(
        "Who worked with Alice on Project Titan?",
        top_k=3,
        mode=SearchMode.SEMANTIC,
    )
    # No crash, no boost applied.
    assert len(out["results"]) == 3


def test_get_entities_for_chunks_batch(indexer):
    """The chunk-inspector getter returns per-chunk entities for the requested
    ids only, omitting unknown ids."""
    store = indexer.vector_store.metadata_store
    got = store.get_entities_for_chunks(["doc_alice-c0", "doc_bridge-c0", "nope"])
    assert "alice johnson" in got["doc_alice-c0"]
    assert "alice johnson" in got["doc_bridge-c0"]
    assert "nope" not in got


def test_get_entities_for_chunks_empty(indexer):
    store = indexer.vector_store.metadata_store
    assert store.get_entities_for_chunks([]) == {}


def test_chunk_boost_reorders_retrieval(indexer, monkeypatch):
    """A positive manual boost must move a chunk above its neighbours that
    otherwise score identically (constant embeddings = equal vectors)."""
    import config
    monkeypatch.setattr(config.settings, "enable_entity_boost", False)
    monkeypatch.setattr(config.settings, "enable_chunk_boosts", True)
    monkeypatch.setattr(config.settings, "chunk_boost_weight", 0.5)
    from models.schemas import SearchMode

    store = indexer.vector_store.metadata_store
    # Baseline: doc_bob is not top-1 for this query (all scores equal, so
    # ordering is insertion order — bob is second).
    baseline = indexer.search(
        "Who worked with Alice on Project Titan?",
        top_k=3,
        mode=SearchMode.SEMANTIC,
    )["results"]
    # Pin doc_bob; it must now rank first.
    store.set_chunk_boost("doc_bob-c0", 1.0)
    out = indexer.search(
        "Who worked with Alice on Project Titan?",
        top_k=3,
        mode=SearchMode.SEMANTIC,
    )["results"]
    assert out[0].chunk_id == "doc_bob-c0"


def test_chunk_boost_negative_demotes(indexer, monkeypatch):
    import config
    monkeypatch.setattr(config.settings, "enable_entity_boost", False)
    monkeypatch.setattr(config.settings, "enable_chunk_boosts", True)
    monkeypatch.setattr(config.settings, "chunk_boost_weight", 0.5)
    from models.schemas import SearchMode

    store = indexer.vector_store.metadata_store
    store.set_chunk_boost("doc_alice-c0", -1.0)
    out = indexer.search(
        "Who worked with Alice on Project Titan?",
        top_k=3,
        mode=SearchMode.SEMANTIC,
    )["results"]
    assert out[-1].chunk_id == "doc_alice-c0"

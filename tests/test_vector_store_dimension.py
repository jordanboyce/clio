"""A dimension change is refused everywhere except by a re-index."""

import numpy as np
import pytest

from services.vector_store import VectorStore


def _with_a_vector(tmp_path, dim):
    store = VectorStore(tmp_path, embedding_dim=dim)
    store.index.add_with_ids(np.ones((1, dim), dtype=np.float32), np.asarray([1], dtype=np.int64))
    store._write_index_atomic(store.index)


def test_mismatched_index_is_refused_by_default(tmp_path):
    _with_a_vector(tmp_path, 384)
    with pytest.raises(RuntimeError, match="has dimension 384"):
        VectorStore(tmp_path, embedding_dim=2048)


def test_an_index_with_no_vectors_has_nothing_to_protect(tmp_path):
    VectorStore(tmp_path, embedding_dim=384).save()
    assert VectorStore(tmp_path, embedding_dim=2048).index.d == 2048


def test_an_unknown_dimension_is_not_written_down(tmp_path):
    store = VectorStore(tmp_path, embedding_dim=None)
    store.save()
    assert not store.index_path.exists()

    store.settle_embedding({"signature": "local::m", "dimension": 16})
    assert store.index.d == 16
    store.save()
    assert VectorStore(tmp_path, embedding_dim=16).index.d == 16


def test_an_unknown_dimension_takes_the_size_of_vectors_on_disk(tmp_path):
    _with_a_vector(tmp_path, 16)
    store = VectorStore(tmp_path, embedding_dim=None)
    assert store.embedding_dim == 16 and store.index_path.exists()


def test_reindex_may_discard_a_mismatched_index(tmp_path):
    VectorStore(tmp_path, embedding_dim=384).save()
    store = VectorStore(tmp_path, embedding_dim=2048, discard_mismatched_index=True)
    assert store.index.d == 2048
    store.clear_index()
    assert VectorStore(tmp_path, embedding_dim=2048).index.d == 2048

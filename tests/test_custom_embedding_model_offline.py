"""A custom embedding model that cannot be fetched, on a locked-down network.

No catalog records its dimension and no index on disk has vectors to read it
from, so nothing knows how big its vectors will be. The collection must open and
work by keyword regardless, and nothing may guess: once a wrong size is in a
saved index, the real model is refused until someone re-indexes.
"""

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from models.schemas import SearchMode
from services.embedder import (
    DeferredEmbeddingService,
    EmbeddingDimensionMismatch,
    EmbeddingUnavailable,
    index_dimension,
)
from services.embedding_status import embedding_status

CUSTOM = "acme/custom-embed-v9"


class Model:
    """A real model's surface, in whatever size this site's model happens to be."""

    model_name = CUSTOM
    signature = f"local::{CUSTOM}"
    backend = "sentence-transformers"

    def __init__(self, dim):
        self.embedding_dim = dim

    def _vector(self, text):
        vector = np.ones(self.embedding_dim, dtype=np.float32)
        vector[hash(text) % self.embedding_dim] += 1
        return vector

    def embed_texts(self, texts, progress_callback=None):
        return np.asarray([self._vector(t) for t in texts], dtype=np.float32)

    def embed_query(self, query):
        return self._vector(query)


@pytest.fixture()
def site(monkeypatch, tmp_path):
    """IndexerManagers over one data dir, like restarts of one deployment."""
    from services import indexer_manager as im
    from services.collection_service import collection_service

    class Site:
        model = None  # None: the hub is blocked and the model cannot be built
        collection = None

        def open(self):
            """The collection as a freshly started server opens it."""
            return im.IndexerManager().get_indexer(self.collection)

        @property
        def indexes(self):
            return collection_service.get_indexes_path(self.collection)

    this = Site()

    def build(*a, **k):
        if this.model is None:
            raise RuntimeError(f"Could not download embedding model '{CUSTOM}' from huggingface.co")
        return this.model

    monkeypatch.setattr(im, "create_embedding_service", build)
    monkeypatch.setattr(im.settings, "embedding_provider", "local")
    monkeypatch.setattr(im.settings, "embedding_model", CUSTOM)
    monkeypatch.setattr(collection_service, "base_dir", tmp_path)
    monkeypatch.setattr(DeferredEmbeddingService, "RETRY_AFTER_SECONDS", 0.0)
    this.collection = collection_service.create_collection(name="Site", embedding_model=CUSTOM)["id"]
    yield this
    embedding_status.reset()


def _upload(indexer, tmp_path, name="notes.txt", text="Groundwater recharge depends on episodic flooding."):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return indexer.index_document(path, name)


def test_the_collection_opens_and_works_by_keyword(site, tmp_path):
    indexer = site.open()

    assert isinstance(indexer.embedding_service, DeferredEmbeddingService)
    assert indexer.embedding_service.embedding_dim is None
    assert indexer.list_documents() == []

    document = _upload(indexer, tmp_path)

    assert document.total_chunks == 1
    found = indexer.search("groundwater recharge")
    assert [r.filename for r in found["results"]] == ["notes.txt"]
    assert found["retrieval"]["mode"] == "keyword"
    assert indexer.search("groundwater", mode=SearchMode.KEYWORD)["results"]
    with pytest.raises(EmbeddingUnavailable, match="huggingface.co"):
        indexer.search("groundwater", mode=SearchMode.SEMANTIC)


def test_nothing_guessed_reaches_the_disk(site, tmp_path):
    indexer = site.open()
    _upload(indexer, tmp_path)

    indexer.save_index()

    assert not (site.indexes / "faiss.index").exists()
    assert indexer.vector_store.metadata_store.get_index_info() in (None, {})


@pytest.mark.parametrize("dim", [8, 512, 1536])
def test_the_model_decides_the_dimension_when_it_arrives(site, tmp_path, dim):
    indexer = site.open()
    store = indexer.vector_store
    _upload(indexer, tmp_path)
    _upload(indexer, tmp_path, "other.txt", "Basalt aquifers store groundwater for decades.")

    site.model = Model(dim)

    assert indexer.fill_missing_vectors() == 2
    assert store.embedding_dim == dim and store.index.d == dim and store.index.ntotal == 2
    assert index_dimension(site.indexes) == dim
    hits = indexer.search("Basalt aquifers store groundwater", mode=SearchMode.SEMANTIC)["results"]
    assert {r.filename for r in hits} == {"notes.txt", "other.txt"}

    # A new source now gets its vector straight away.
    _upload(indexer, tmp_path, "third.txt", "Springs discharge where the water table meets the surface.")
    assert store.index.ntotal == 3


def test_a_restart_opens_on_the_size_the_model_wrote(site, tmp_path):
    indexer = site.open()
    _upload(indexer, tmp_path)
    site.model = Model(512)
    indexer.fill_missing_vectors()

    # Blocked again after a restart: the vectors on disk now fix the size.
    site.model = None
    reopened = site.open()
    assert reopened.embedding_service.embedding_dim == 512
    assert reopened.vector_store.index.ntotal == 1

    # And it is the normal path once the model is back.
    site.model = Model(512)
    healthy = site.open()
    assert not isinstance(healthy.embedding_service, DeferredEmbeddingService)
    assert healthy.vector_store.index.ntotal == 1


def test_a_restart_before_the_model_arrives_keeps_waiting(site, tmp_path):
    indexer = site.open()
    _upload(indexer, tmp_path)
    indexer.save_index()

    reopened = site.open()
    assert reopened.embedding_service.embedding_dim is None
    assert reopened.vector_store.vectors_pending
    assert reopened.search("groundwater")["results"]

    site.model = Model(256)
    assert reopened.fill_missing_vectors() == 1
    assert index_dimension(site.indexes) == 256


def test_an_empty_index_saved_under_the_last_model_does_not_pin_the_size(site, tmp_path):
    # A first run on the default model saves an empty 384 index at shutdown;
    # the operator then configures the site's model. That file used to count as
    # vectors on disk, so the real model was refused until a re-index.
    site.model = Model(384)
    site.open().vector_store.save()
    assert (site.indexes / "faiss.index").exists()
    assert index_dimension(site.indexes) is None

    site.model = None
    indexer = site.open()
    assert indexer.embedding_service.embedding_dim is None
    _upload(indexer, tmp_path)
    indexer.save_index()

    site.model = Model(768)
    assert indexer.fill_missing_vectors() == 1
    assert index_dimension(site.indexes) == 768


def test_a_model_that_opens_directly_replaces_an_empty_index_of_another_size(site):
    site.model = Model(384)
    site.open().vector_store.save()

    site.model = Model(768)
    indexer = site.open()

    assert not isinstance(indexer.embedding_service, DeferredEmbeddingService)
    assert indexer.vector_store.index.d == 768


def test_vectors_already_on_disk_are_never_replaced_by_a_model_of_another_size(site, tmp_path):
    site.model = Model(384)
    first = site.open()
    _upload(first, tmp_path)
    first.save_index()
    on_disk = (site.indexes / "faiss.index").read_bytes()

    site.model = None
    indexer = site.open()
    site.model = Model(768)

    with pytest.raises(EmbeddingDimensionMismatch):
        _upload(indexer, tmp_path, "new.txt", "A second source about aquifers.")
    assert indexer.vector_store.index.d == 384 and indexer.vector_store.index.ntotal == 1

    indexer.save_index()
    assert index_dimension(site.indexes) == 384
    assert len((site.indexes / "faiss.index").read_bytes()) == len(on_disk)


def test_vectors_are_recorded_under_the_model_that_made_them(site, tmp_path):
    # Without this an export of the collection could not vouch for its vectors.
    indexer = site.open()
    _upload(indexer, tmp_path)

    site.model = Model(512)
    indexer.fill_missing_vectors()

    info = indexer.vector_store.metadata_store.get_index_info()
    assert info["signature"] == f"local::{CUSTOM}" and info["dimension"] == 512


def test_the_status_bar_says_so_when_the_model_arrives(site, tmp_path):
    from services import feature_status

    embedding_status.reset()
    indexer = site.open()
    _upload(indexer, tmp_path)
    assert feature_status._semantic()["state"] == "limited"

    site.model = Model(512)
    indexer.fill_missing_vectors()

    assert feature_status._semantic()["state"] == "on"
    assert embedding_status.status()["model"] == CUSTOM


def test_another_collections_model_says_nothing_about_the_configured_one(site, monkeypatch):
    from services import indexer_manager as im

    embedding_status.reset()
    monkeypatch.setattr(im.settings, "embedding_model", "some-other-configured-model")
    indexer = site.open()

    site.model = Model(512)
    indexer.embedding_service.embed_query("anything")

    assert indexer.vector_store.index.d == 512
    assert embedding_status.status()["status"] != "ready"


@pytest.fixture()
def black_hole(site, monkeypatch):
    """A network that drops packets: building the model hangs until released."""
    from services import indexer_manager as im

    released = threading.Event()
    builds = []

    def hang(*a, **k):
        builds.append(threading.current_thread().name)
        released.wait(30)
        return site.model

    monkeypatch.setattr(im, "create_embedding_service", hang)
    monkeypatch.setattr(im, "EMBEDDING_BUILD_WAIT_SECONDS", 0.05)
    yield SimpleNamespace(released=released, builds=builds)
    released.set()


def test_a_dropping_firewall_does_not_hold_up_opening_a_collection(site, black_hole, tmp_path):
    started = time.monotonic()
    indexer = site.open()

    assert time.monotonic() - started < 5
    assert isinstance(indexer.embedding_service, DeferredEmbeddingService)

    _upload(indexer, tmp_path)
    assert indexer.search("groundwater recharge")["retrieval"]["mode"] == "keyword"
    assert time.monotonic() - started < 5


def test_one_attempt_at_a_time_however_many_callers_ask(site, black_hole):
    from services import indexer_manager as im

    manager = im.IndexerManager()
    for _ in range(5):
        with pytest.raises(RuntimeError, match="Still trying"):
            manager._reach_embedding_service(CUSTOM)

    assert len(black_hole.builds) == 1


def test_the_wait_names_the_model_this_deployment_is_configured_to_reach(site, black_hole, monkeypatch):
    from services import indexer_manager as im

    monkeypatch.setattr(im.settings, "embedding_provider", "openai_compatible")
    monkeypatch.setattr(im.settings, "remote_embedding_model", "site-embedder")

    with pytest.raises(RuntimeError, match="'site-embedder'"):
        im.IndexerManager()._reach_embedding_service("all-MiniLM-L6-v2")


def test_the_model_is_there_after_a_slow_attempt_finally_succeeds(site, black_hole, tmp_path):
    indexer = site.open()
    _upload(indexer, tmp_path)

    site.model = Model(512)
    black_hole.released.set()

    deadline = time.monotonic() + 10
    filled = 0
    while not filled and time.monotonic() < deadline:
        try:
            filled = indexer.fill_missing_vectors()
        except EmbeddingUnavailable:
            time.sleep(0.01)

    assert filled == 1
    assert index_dimension(site.indexes) == 512
    assert len(black_hole.builds) == 1


def test_a_model_on_disk_is_loaded_while_the_caller_waits(site, monkeypatch):
    # Startup must not change for the ordinary case: no keyword-only first search.
    from services import indexer_manager as im

    monkeypatch.setattr(im, "local_model_cached", lambda model: True)
    monkeypatch.setattr(im, "EMBEDDING_BUILD_WAIT_SECONDS", 0.0)
    site.model = Model(384)

    indexer = site.open()

    assert not isinstance(indexer.embedding_service, DeferredEmbeddingService)


def test_a_self_hosted_endpoint_that_is_down_is_the_same_case(site, tmp_path, monkeypatch):
    from services import indexer_manager as im

    monkeypatch.setattr(im.settings, "embedding_provider", "openai_compatible")
    monkeypatch.setattr(im.settings, "embedding_base_url", "http://vllm.internal:8000/v1")
    monkeypatch.setattr(im.settings, "remote_embedding_model", "site-embedder")

    indexer = site.open()

    assert indexer.embedding_service.model_name == "site-embedder"
    _upload(indexer, tmp_path)
    assert indexer.search("groundwater recharge")["results"]

    site.model = Model(1024)
    assert indexer.fill_missing_vectors() == 1
    assert index_dimension(site.indexes) == 1024


def test_an_idle_release_of_the_placeholder_comes_back_empty(site, tmp_path):
    store = site.open().vector_store

    assert store.unload_if_idle(0)
    assert store.index.ntotal == 0
    assert store.embedding_dim is None

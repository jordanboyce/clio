"""Filtered search inside FAISS, and releasing idle vectors from memory.

The release is only safe because the store object never goes away and an index
with unsaved vectors is never released, so most of these tests are about the
ways that could go wrong.
"""

import threading
import time
from contextlib import contextmanager

import faiss
import numpy as np
import pytest

import config
from config import settings
from models.schemas import ChunkMetadata
from services import vector_store as vector_store_module
from services.rw_lock import FifoSemaphore, ReadWriteLock
from services.vector_store import VectorStore

DIM = 8


def _chunks(count, document_id="doc", start=0):
    return [
        ChunkMetadata(
            document_id=document_id,
            filename=f"{document_id}.txt",
            page_number=1,
            chunk_index=i,
            text=f"passage number {i} of {document_id}",
            chunk_id=f"{document_id}_{i}",
        )
        for i in range(start, start + count)
    ]


def _vectors(count, seed=1):
    return np.random.default_rng(seed).standard_normal((count, DIM), dtype=np.float32)


@pytest.fixture()
def store(tmp_path):
    return VectorStore(tmp_path, embedding_dim=DIM)


def _saved_store(tmp_path, count=20):
    store = VectorStore(tmp_path, embedding_dim=DIM)
    store.add_chunks(_chunks(count), _vectors(count))
    store.save()
    return store


# ── Filtered search ─────────────────────────────────────────────────────────


def _brute_force_top(vectors, query, allowed_positions, k):
    normal = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    scores = normal @ (query / np.linalg.norm(query))
    ranked = sorted(allowed_positions, key=lambda p: scores[p], reverse=True)
    return ranked[:k]


def test_filtered_search_returns_the_true_top_results(store):
    """The whole point of the old scan-everything approach was exactness."""
    vectors = _vectors(400)
    chunks = _chunks(400)
    store.add_chunks(chunks, vectors)
    query = _vectors(1, seed=99)[0]
    allowed_positions = list(range(0, 400, 7))
    allowed = {chunks[p].chunk_id for p in allowed_positions}

    results = store.search(query, top_k=10, allowed_chunk_ids=allowed)

    expected = [chunks[p].chunk_id for p in _brute_force_top(vectors, query, allowed_positions, 10)]
    assert [r.chunk_id for r in results] == expected


def test_filtered_search_with_fewer_matches_than_top_k(store):
    chunks = _chunks(50)
    store.add_chunks(chunks, _vectors(50))
    allowed = {chunks[3].chunk_id, chunks[30].chunk_id}

    results = store.search(_vectors(1, seed=5)[0], top_k=10, allowed_chunk_ids=allowed)

    assert {r.chunk_id for r in results} == allowed


def test_filter_matching_nothing_returns_nothing(store):
    store.add_chunks(_chunks(5), _vectors(5))
    assert store.search(_vectors(1, seed=5)[0], top_k=3, allowed_chunk_ids=set()) == []
    assert store.search(_vectors(1, seed=5)[0], top_k=3, allowed_chunk_ids={"not-a-chunk"}) == []


def test_filtered_hybrid_search_stays_inside_the_filter(store):
    chunks = _chunks(100)
    store.add_chunks(chunks, _vectors(100))
    allowed = {c.chunk_id for c in chunks[:10]}

    results = store.search_hybrid("passage number", _vectors(1, seed=5)[0], top_k=8, allowed_chunk_ids=allowed)

    assert results
    assert {r.chunk_id for r in results} <= allowed


def test_filtered_search_after_a_delete_never_returns_the_deleted(store):
    store.add_chunks(_chunks(10, "keep") + _chunks(10, "gone"), _vectors(20))
    store.delete_document("gone")
    allowed = {f"keep_{i}" for i in range(10)} | {f"gone_{i}" for i in range(10)}

    results = store.search(_vectors(1, seed=5)[0], top_k=20, allowed_chunk_ids=allowed)

    assert len(results) == 10
    assert all(r.document_id == "keep" for r in results)


# ── Releasing idle vectors ──────────────────────────────────────────────────


def test_idle_saved_index_is_released_and_comes_back_identical(tmp_path):
    store = _saved_store(tmp_path)
    query = _vectors(1, seed=5)[0]
    before = [(r.chunk_id, r.similarity_score) for r in store.search(query, top_k=5)]

    assert store.unload_if_idle(0) is True
    assert store.memory_stats() == {"loaded": 0, "vectors": 0, "bytes": 0}

    assert [(r.chunk_id, r.similarity_score) for r in store.search(query, top_k=5)] == before
    assert store.memory_stats()["vectors"] == 20
    assert store.reload_count == 1


def test_recently_used_index_is_kept(tmp_path):
    store = _saved_store(tmp_path)
    store.search(_vectors(1, seed=5)[0], top_k=5)
    assert store.unload_if_idle(3600) is False
    assert store.memory_stats()["loaded"] == 1


def test_unsaved_vectors_are_never_released(tmp_path):
    """Releasing here would silently lose the batch a running job just added."""
    store = _saved_store(tmp_path)
    store.add_chunks(_chunks(5, "new"), _vectors(5, seed=2))

    assert store.unload_if_idle(0) is False
    assert store.memory_stats()["vectors"] == 25

    store.save()
    assert store.unload_if_idle(0) is True


def test_unsaved_delete_is_never_released(tmp_path):
    store = _saved_store(tmp_path)
    store.delete_document("doc")
    assert store.unload_if_idle(0) is False


def test_replacing_the_index_counts_as_unsaved(tmp_path):
    """The collection-import path swaps in a new index and fills it directly."""
    store = _saved_store(tmp_path)
    store.index = store._new_index()
    assert store.unload_if_idle(0) is False


def test_index_that_was_never_saved_is_kept(tmp_path):
    store = VectorStore(tmp_path, embedding_dim=DIM)
    store.add_chunks(_chunks(3), _vectors(3))
    store._dirty = False  # however it got here, there is nothing on disk to return to
    assert store.unload_if_idle(0) is False
    assert store.memory_stats()["vectors"] == 3


def test_empty_never_saved_index_can_go(tmp_path):
    store = VectorStore(tmp_path, embedding_dim=DIM)
    assert store.unload_if_idle(0) is True
    assert store.index.ntotal == 0


def test_a_writer_holding_the_store_loses_nothing(tmp_path):
    """A job keeps its reference across a release and writes on the next batch."""
    store = _saved_store(tmp_path)
    assert store.unload_if_idle(0) is True

    store.add_chunks(_chunks(5, "later"), _vectors(5, seed=2))
    store.save()

    assert VectorStore(tmp_path, embedding_dim=DIM).index.ntotal == 25


def test_save_while_released_writes_nothing(tmp_path):
    """Otherwise every shutdown reads every collection back into memory."""
    store = _saved_store(tmp_path)
    store.unload_if_idle(0)
    stamp = store.index_path.stat().st_mtime_ns

    store.save()

    assert store.memory_stats()["loaded"] == 0
    assert store.index_path.stat().st_mtime_ns == stamp


def test_index_in_use_is_not_released(tmp_path):
    store = _saved_store(tmp_path)
    holding, done = threading.Event(), threading.Event()

    def hold_lock():
        with store._index_lock:
            holding.set()
            done.wait(5)

    thread = threading.Thread(target=hold_lock)
    thread.start()
    holding.wait(5)
    try:
        assert store.unload_if_idle(0) is False
    finally:
        done.set()
        thread.join()
    assert store.memory_stats()["loaded"] == 1


def test_closed_store_stays_closed(tmp_path):
    store = _saved_store(tmp_path)
    store.close()
    assert store.index is None
    assert store.unload_if_idle(0) is False


def test_searches_during_releases_always_get_right_answers(tmp_path):
    store = _saved_store(tmp_path, count=200)
    query = _vectors(1, seed=5)[0]
    expected = [r.chunk_id for r in store.search(query, top_k=5)]
    stop, failures = threading.Event(), []

    def churn():
        # stop.wait yields: a bare spin would hold the GIL against the searchers.
        while not stop.wait(0.001):
            store.unload_if_idle(0)

    def search():
        for _ in range(25):
            found = [r.chunk_id for r in store.search(query, top_k=5)]
            if found != expected:
                failures.append(found)

    churner = threading.Thread(target=churn)
    searchers = [threading.Thread(target=search) for _ in range(4)]
    churner.start()
    for t in searchers:
        t.start()
    for t in searchers:
        t.join()
    stop.set()
    churner.join()

    assert not failures


# ── The readers-writer lock ─────────────────────────────────────────────────


TIMEOUT = 20  # generous: only a deadlock or a broken test should ever reach it


def _background(work):
    """Run work on a daemon thread, so a deadlock fails the test instead of hanging it."""
    errors = []

    def run():
        try:
            work()
        except BaseException as exc:  # noqa: BLE001 - reported by _finish
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.errors = errors
    thread.start()
    return thread


def _finish(*threads):
    for thread in threads:
        thread.join(TIMEOUT)
        assert not thread.is_alive(), "still blocked: this is a deadlock or a starved thread"
        assert not thread.errors, thread.errors


def _until(condition):
    deadline = time.monotonic() + TIMEOUT
    while not condition():
        assert time.monotonic() < deadline, "the expected state never arrived"
        time.sleep(0.001)


def _holding_shared(lock):
    """A thread holding shared access until the returned event is set."""
    held, let_go = threading.Event(), threading.Event()

    def hold():
        with lock.shared():
            held.set()
            let_go.wait(TIMEOUT)

    thread = _background(hold)
    assert held.wait(TIMEOUT)
    return thread, let_go


def test_readers_share_the_lock():
    lock = ReadWriteLock()
    together = threading.Barrier(4, timeout=TIMEOUT)

    def read():
        with lock.shared():
            together.wait()  # only passes while all four are inside

    _finish(*[_background(read) for _ in range(4)])


def test_a_writer_excludes_readers_and_other_writers():
    lock = ReadWriteLock()
    entered = []
    with lock:
        reader = _background(lambda: _enter_shared(lock, entered))
        writer = _background(lambda: _enter(lock, entered))
        time.sleep(0.05)
        assert entered == []
    _finish(reader, writer)
    assert sorted(entered) == ["reader", "writer"]


def _enter_shared(lock, entered):
    with lock.shared():
        entered.append("reader")


def _enter(lock, entered):
    with lock:
        entered.append("writer")


def test_a_waiting_writer_goes_ahead_of_readers_who_arrive_after_it():
    lock = ReadWriteLock()
    first_reader, let_go = _holding_shared(lock)
    entered = []

    writer = _background(lambda: _enter(lock, entered))
    _until(lambda: lock._writers_waiting == 1)
    late_reader = _background(lambda: _enter_shared(lock, entered))
    time.sleep(0.05)
    assert entered == []

    let_go.set()
    _finish(first_reader, writer, late_reader)
    assert entered == ["writer", "reader"]


def test_a_thread_already_reading_is_not_held_behind_a_waiting_writer():
    """The writer waits on this thread, so making this thread wait would be a deadlock."""
    lock = ReadWriteLock()
    writer_waiting = threading.Event()
    writer = None

    def read_twice():
        nonlocal writer
        with lock.shared():
            writer = _background(lambda: _enter(lock, []))
            _until(lambda: lock._writers_waiting == 1)
            writer_waiting.set()
            with lock.shared():
                pass

    reader = _background(read_twice)
    assert writer_waiting.wait(TIMEOUT)
    _finish(reader)
    _finish(writer)


def test_a_writer_can_take_the_lock_again_in_either_mode():
    lock = ReadWriteLock()

    def nested():
        with lock:
            with lock:
                with lock.shared():
                    with lock:
                        pass
            assert lock._writer == threading.get_ident()
        assert lock._writer is None

    _finish(_background(nested))


def test_a_reader_asking_for_exclusive_access_is_refused_not_deadlocked():
    lock = ReadWriteLock()

    def upgrade():
        with lock.shared():
            assert lock.acquire(blocking=False) is False
            with pytest.raises(RuntimeError):
                lock.acquire()
        # Refused, it still holds nothing extra and the lock is usable.
        with lock:
            pass

    _finish(_background(upgrade))


def test_trying_for_exclusive_access_gives_up_while_anyone_holds_or_awaits_it():
    lock = ReadWriteLock()
    reader, let_go = _holding_shared(lock)
    assert lock.acquire(blocking=False) is False

    writer = _background(lambda: _enter(lock, []))
    _until(lambda: lock._writers_waiting == 1)
    assert lock.acquire(blocking=False) is False

    let_go.set()
    _finish(reader, writer)
    assert lock.acquire(blocking=False) is True
    lock.release()


def test_releasing_what_is_not_held_is_an_error():
    lock = ReadWriteLock()
    with pytest.raises(RuntimeError):
        lock.release()
    with pytest.raises(RuntimeError):
        lock.release_shared()


def test_an_exception_gives_the_lock_back():
    lock = ReadWriteLock()
    with pytest.raises(ValueError):
        with lock.shared():
            raise ValueError
    with pytest.raises(ValueError):
        with lock:
            raise ValueError
    assert lock.acquire(blocking=False) is True
    lock.release()


# ── Searching a collection in parallel ──────────────────────────────────────


class _IndexCalls:
    """Watches the store's real FAISS calls and records any that overlap unsafely."""

    def __init__(self):
        self._guard = threading.Lock()
        self.searching = 0
        self.mutating = 0
        self.searches = 0
        self.mutations = 0
        self.most_searching = 0
        self.overlaps = []

    @contextmanager
    def search(self, name):
        with self._guard:
            if self.mutating:
                self.overlaps.append(f"{name} during a mutation")
            self.searching += 1
            self.searches += 1
            self.most_searching = max(self.most_searching, self.searching)
        try:
            yield
        finally:
            with self._guard:
                self.searching -= 1

    @contextmanager
    def mutation(self, name):
        with self._guard:
            if self.searching or self.mutating:
                self.overlaps.append(f"{name} with {self.searching} searches and {self.mutating} mutations")
            self.mutating += 1
            self.mutations += 1
        try:
            yield
        finally:
            with self._guard:
                self.mutating -= 1


@pytest.fixture()
def calls(monkeypatch):
    """Every search and every change of an index, each held open a moment to widen races."""
    watcher = _IndexCalls()

    def watched(real, track, name):
        def call(*args, **kwargs):
            with track(name):
                time.sleep(0.001)
                return real(*args, **kwargs)

        return call

    for method, track in (
        ("search", watcher.search),
        ("reconstruct", watcher.search),
        ("add_with_ids", watcher.mutation),
        ("remove_ids", watcher.mutation),
    ):
        monkeypatch.setattr(
            faiss.IndexIDMap2, method, watched(getattr(faiss.IndexIDMap2, method), track, method)
        )
    # Saving only reads the index, but it stays exclusive, so it is held to that.
    monkeypatch.setattr(faiss, "write_index", watched(faiss.write_index, watcher.mutation, "write_index"))
    return watcher


def _block_searches(monkeypatch):
    """Make index searches wait inside FAISS until the returned event is set."""
    inside, let_go = threading.Event(), threading.Event()
    real = faiss.IndexIDMap2.search

    def search(*args, **kwargs):
        inside.set()
        let_go.wait(TIMEOUT)
        return real(*args, **kwargs)

    monkeypatch.setattr(faiss.IndexIDMap2, "search", search)
    return inside, let_go


def test_searches_of_one_collection_run_at_the_same_time(tmp_path, monkeypatch):
    store = _saved_store(tmp_path)
    together = threading.Barrier(4, timeout=TIMEOUT)
    real = faiss.IndexIDMap2.search

    def search(*args, **kwargs):
        together.wait()  # only passes with all four inside FAISS at once
        return real(*args, **kwargs)

    monkeypatch.setattr(faiss.IndexIDMap2, "search", search)
    query = _vectors(1, seed=5)[0]

    _finish(*[_background(lambda: store.search(query, top_k=5)) for _ in range(4)])


def test_a_new_collections_first_searches_can_arrive_together(tmp_path):
    """Their shared first look at the metadata adds its columns, which must happen once."""
    store = VectorStore(tmp_path, embedding_dim=DIM)
    start = threading.Barrier(16, timeout=TIMEOUT)

    def first_look():
        start.wait()
        store.metadata_store.get_documents_info({"doc"})

    _finish(*[_background(first_look) for _ in range(16)])


def test_a_search_leaves_openmp_capped_on_its_own_thread(tmp_path):
    """Uncapped, every searching thread starts a team per core and parallel search gains little."""
    store = _saved_store(tmp_path)
    allowed = []

    def search():
        store.search(_vectors(1, seed=5)[0], top_k=5)
        allowed.append(faiss.omp_get_max_threads())

    _finish(_background(search))
    assert allowed == [1]


def test_a_write_waits_for_the_search_in_flight(tmp_path, monkeypatch):
    store = _saved_store(tmp_path)
    inside, let_go = _block_searches(monkeypatch)
    search = _background(lambda: store.search(_vectors(1, seed=5)[0], top_k=5))
    assert inside.wait(TIMEOUT)

    writer = _background(lambda: store.add_chunks(_chunks(5, "new"), _vectors(5, seed=2)))
    _until(lambda: store._index_lock._writers_waiting == 1)
    assert store.memory_stats()["vectors"] == 20

    let_go.set()
    _finish(search, writer)
    assert store.memory_stats()["vectors"] == 25


def test_a_search_in_flight_keeps_its_index_loaded(tmp_path, monkeypatch):
    store = _saved_store(tmp_path)
    inside, let_go = _block_searches(monkeypatch)
    query = _vectors(1, seed=5)[0]
    found = []
    search = _background(lambda: found.extend(store.search(query, top_k=5)))
    assert inside.wait(TIMEOUT)

    assert store.unload_if_idle(0) is False
    assert store.memory_stats()["loaded"] == 1

    let_go.set()
    _finish(search)
    assert len(found) == 5
    assert store.reload_count == 0


def test_searches_arriving_together_read_a_released_index_back_once(tmp_path, monkeypatch):
    store = _saved_store(tmp_path)
    query = _vectors(1, seed=5)[0]
    expected = [r.chunk_id for r in store.search(query, top_k=5)]
    assert store.unload_if_idle(0) is True

    reads = []
    real_read = faiss.read_index

    def read_index(*args, **kwargs):
        reads.append(1)
        time.sleep(0.05)  # long enough for every search to arrive
        return real_read(*args, **kwargs)

    monkeypatch.setattr(faiss, "read_index", read_index)
    start = threading.Barrier(8, timeout=TIMEOUT)
    found = []

    def search():
        start.wait()
        found.append([r.chunk_id for r in store.search(query, top_k=5)])

    _finish(*[_background(search) for _ in range(8)])

    assert found == [expected] * 8
    assert len(reads) == 1
    assert store.reload_count == 1


def test_work_nested_inside_the_lock_does_not_deadlock(tmp_path):
    """A document swap holds the lock across deletes, adds, saves and searches."""
    store = _saved_store(tmp_path)
    query = _vectors(1, seed=5)[0]

    def nested():
        with store._index_lock:
            store.add_chunks(_chunks(5, "new"), _vectors(5, seed=2))
            assert store.search(query, top_k=5)
            store.reconstruct_vector(1)
            store.save()
            assert store.unload_if_idle(0) is True
            assert store.search(query, top_k=5)  # reads it back, already exclusive
            store.load()
            store.delete_documents(["new"])
            store.index = store._new_index()
            store.save()

    _finish(_background(nested))
    assert store.memory_stats()["vectors"] == 0


def test_a_search_never_sees_a_document_swap_half_done(tmp_path):
    """The re-index path relies on `with store._index_lock:` shutting searches out."""
    store = _saved_store(tmp_path)
    store.add_chunks(_chunks(5, "other"), _vectors(5, seed=2))
    query = _vectors(1, seed=5)[0]
    found = []

    with store._index_lock:
        store.delete_documents(["doc"])
        search = _background(lambda: found.extend(store.search(query, top_k=25)))
        time.sleep(0.05)
        assert found == []
        store.add_chunks(_chunks(20, "doc"), _vectors(20))
    _finish(search)
    assert len(found) == 25  # half-way through, only the other document's 5 could match


def test_a_writer_gets_in_under_constant_searching(tmp_path, calls):
    store = _saved_store(tmp_path)
    stop = threading.Event()
    query = _vectors(1, seed=5)[0]

    def search():
        while not stop.is_set():
            store.search(query, top_k=5)

    searchers = [_background(search) for _ in range(6)]
    try:
        _until(lambda: calls.most_searching >= 2)
        writer = _background(lambda: store.add_chunks(_chunks(5, "new"), _vectors(5, seed=2)))
        saver = _background(store.save)
        _finish(writer, saver)
        assert store.memory_stats()["vectors"] == 25
    finally:
        stop.set()
    _finish(*searchers)
    assert not calls.overlaps


@pytest.mark.parametrize("scan_slots", [0, 2])
def test_mixed_load_keeps_searches_right_and_writes_alone(tmp_path, calls, monkeypatch, scan_slots):
    """Searches, adds, deletes, saves and releases all at once, on one collection.

    A write overlapping a search is a native crash when it goes badly, so the
    pass condition is not just surviving: `calls` sees every FAISS call and fails
    this on any overlap, even one that happened not to crash. It runs both with
    and without the limit on concurrent scans, which has its own path to the lock.
    """
    _limit_scans(monkeypatch, scan_slots)
    stable = 200
    vectors = _vectors(stable)
    store = _saved_store(tmp_path, count=stable)
    writers_done = threading.Event()

    def search(seed):
        # Each stable vector is its own best match however much churn is going on.
        rng = np.random.default_rng(seed)
        count = 0
        while count < 40 or not writers_done.is_set():
            position = int(rng.integers(stable))
            results = store.search(vectors[position], top_k=3)
            assert results and results[0].chunk_id == f"doc_{position}"
            hybrid = store.search_hybrid("passage number", vectors[position], top_k=3, semantic_weight=1.0)
            assert hybrid and hybrid[0].chunk_id == f"doc_{position}"
            count += 1
            time.sleep(0.001)

    def read_back(seed):
        rng = np.random.default_rng(seed)
        ids = [int(i) for i in store.metadata_store.get_ids_for_chunk_ids([f"doc_{i}" for i in range(stable)]).values()]
        while not writers_done.is_set():
            store.reconstruct_vector(ids[int(rng.integers(stable))])
            time.sleep(0.001)

    def write(worker):
        kept = []
        for batch in range(30):
            document = f"w{worker}b{batch}"
            store.add_chunks(_chunks(10, document), _vectors(10, seed=1000 * worker + batch))
            kept.append(document)
            if batch % 3 == 2:
                store.delete_documents(kept[:2])
                del kept[:2]

    def save():
        while not writers_done.wait(0.003):
            store.save()

    def release():
        while not writers_done.wait(0.001):
            store.unload_if_idle(0)

    searchers = [_background(lambda s=s: search(s)) for s in range(6)]
    readers = [_background(lambda s=s: read_back(100 + s)) for s in range(2)]
    writers = [_background(lambda w=w: write(w)) for w in range(2)]
    saver, releaser = _background(save), _background(release)

    _finish(*writers)
    writers_done.set()
    _finish(*searchers, *readers, saver, releaser)

    assert not calls.overlaps
    assert calls.most_searching >= 2, "the searches never actually ran in parallel"
    assert calls.mutations > 0

    # What is on disk agrees with the metadata: nothing lost, nothing left behind.
    store.save()
    reopened = VectorStore(tmp_path, embedding_dim=DIM)
    vector_ids = faiss.vector_to_array(faiss.downcast_index(reopened.index).id_map)
    assert sorted(vector_ids.tolist()) == sorted(reopened.metadata_store.get_all_rowids_ordered())
    documents_kept = 2 * (30 - 2 * 10)  # two writers, each deleting two of its 30 documents ten times
    assert reopened.get_total_chunks() == stable + documents_kept * 10


# ── Taking turns on large scans ─────────────────────────────────────────────


def _limit_scans(monkeypatch, slots, min_vectors=0):
    """Set the scan limit, by default treating every collection as a large one."""
    monkeypatch.setattr(settings, "scan_max_concurrent", slots)
    monkeypatch.setattr(vector_store_module, "SCAN_QUEUE_MIN_VECTORS", min_vectors)
    monkeypatch.setattr(vector_store_module, "_scan_slots", None)  # a failed test keeps its own


def _search_on(store, seed=5):
    return _background(lambda: store.search(_vectors(1, seed=seed)[0], top_k=5))


def _waiting_for_a_slot(count=1):
    """Until count threads are queued for the current scan slots."""
    _until(lambda: len(vector_store_module._current_scan_slots()._waiting) == count)


def test_the_semaphore_serves_waiters_in_arrival_order():
    semaphore = FifoSemaphore(1)
    served = []
    semaphore.acquire()

    def wait_in_line(number):
        semaphore.acquire()
        served.append(number)
        semaphore.release()

    threads = []
    for number in range(5):
        threads.append(_background(lambda n=number: wait_in_line(n)))
        _until(lambda n=number: len(semaphore._waiting) == n + 1)
    semaphore.release()

    _finish(*threads)
    assert served == [0, 1, 2, 3, 4]


def test_a_caller_that_stepped_out_of_line_comes_back_to_its_old_place():
    """Everyone a writer sends out of line rejoins at once, in no particular order."""
    semaphore = FifoSemaphore(1)
    stepped_out = semaphore.acquire()
    semaphore.release()
    semaphore.acquire()  # someone else has the slot now
    served = []

    def wait_in_line(name, place=None):
        semaphore.acquire(place)
        served.append(name)
        semaphore.release()

    later = _background(lambda: wait_in_line("arrived later"))
    _until(lambda: len(semaphore._waiting) == 1)
    returning = _background(lambda: wait_in_line("stepped out", stepped_out))
    _until(lambda: len(semaphore._waiting) == 2)
    semaphore.release()

    _finish(later, returning)
    assert served == ["stepped out", "arrived later"]


def test_a_freed_slot_cannot_be_taken_ahead_of_the_queue():
    """The fault in threading.Semaphore this class exists to avoid."""
    semaphore = FifoSemaphore(1)
    semaphore.acquire()
    waiter_inside, waiter_leaves = threading.Event(), threading.Event()

    def wait_in_line():
        semaphore.acquire()
        waiter_inside.set()
        waiter_leaves.wait(TIMEOUT)
        semaphore.release()

    waiter = _background(wait_in_line)
    _until(lambda: len(semaphore._waiting) == 1)
    semaphore.release()

    late = _background(semaphore.acquire)
    _until(lambda: len(semaphore._waiting) == 1)  # queued, though a slot was just released
    assert waiter_inside.wait(TIMEOUT)
    waiter_leaves.set()
    _finish(waiter, late)


def test_trying_for_shared_access_never_waits():
    lock = ReadWriteLock()
    assert lock.try_acquire_shared() is True
    assert lock.try_acquire_shared() is True  # again, while already reading
    lock.release_shared()
    lock.release_shared()

    with lock:
        assert lock.try_acquire_shared() is True  # a writer that searches stays a writer
        lock.release_shared()
        outcome = []
        _finish(_background(lambda: outcome.append(lock.try_acquire_shared())))
        assert outcome == [False]

    reader, let_reader_go = _holding_shared(lock)
    writer = _background(lambda: _enter(lock, []))
    _until(lambda: lock._writers_waiting == 1)
    outcome = []
    _finish(_background(lambda: outcome.append(lock.try_acquire_shared())))
    assert outcome == [False]  # a waiting writer holds back new readers here too
    let_reader_go.set()
    _finish(reader, writer)


def test_waiting_for_writers_takes_nothing_and_returns_once_they_are_gone():
    lock = ReadWriteLock()
    lock.wait_until_no_writers()  # none: returns at once
    returned = threading.Event()
    with lock:
        waiter = _background(lambda: (lock.wait_until_no_writers(), returned.set()))
        assert not returned.wait(0.05)
    _finish(waiter)
    assert returned.is_set()
    assert not lock._readers and lock._writer is None


def test_large_scans_across_collections_share_the_limit(tmp_path, monkeypatch):
    """Memory bandwidth is shared by every collection, so the limit has to be too."""
    _limit_scans(monkeypatch, 2)
    stores = [_saved_store(tmp_path / str(i)) for i in range(2)]
    guard = threading.Lock()
    scanning = []
    most_scanning = []
    let_go = threading.Event()
    real = faiss.IndexIDMap2.search

    def search(*args, **kwargs):
        with guard:
            scanning.append(1)
            most_scanning.append(len(scanning))
        let_go.wait(TIMEOUT)
        with guard:
            scanning.pop()
        return real(*args, **kwargs)

    monkeypatch.setattr(faiss.IndexIDMap2, "search", search)

    searches = [_search_on(stores[i % 2]) for i in range(6)]
    _waiting_for_a_slot(4)
    _until(lambda: len(scanning) == 2)
    let_go.set()
    _finish(*searches)
    assert max(most_scanning) == 2
    assert len(most_scanning) == 6


def test_a_small_collection_does_not_queue_behind_large_scans(tmp_path, monkeypatch):
    large_store = _saved_store(tmp_path / "large", count=20)
    small_store = _saved_store(tmp_path / "small", count=19)
    _limit_scans(monkeypatch, 1, min_vectors=20)
    inside, let_go = threading.Event(), threading.Event()
    real = faiss.IndexIDMap2.search

    def search(index, *args, **kwargs):
        if index.ntotal >= 20:
            inside.set()
            let_go.wait(TIMEOUT)
        return real(index, *args, **kwargs)

    monkeypatch.setattr(faiss.IndexIDMap2, "search", search)

    large = _search_on(large_store)
    assert inside.wait(TIMEOUT)
    queued = _search_on(large_store)
    _waiting_for_a_slot()

    _finish(_search_on(small_store))  # with the only slot taken and a search queued for it

    let_go.set()
    _finish(large, queued)


def test_zero_means_no_limit(tmp_path, monkeypatch):
    _limit_scans(monkeypatch, 0)
    store = _saved_store(tmp_path)
    together = threading.Barrier(4, timeout=TIMEOUT)
    real = faiss.IndexIDMap2.search

    def search(*args, **kwargs):
        together.wait()  # only passes with all four inside FAISS at once
        return real(*args, **kwargs)

    monkeypatch.setattr(faiss.IndexIDMap2, "search", search)
    _finish(*[_search_on(store) for _ in range(4)])


def test_a_queued_search_does_not_delay_a_writer(tmp_path, monkeypatch):
    _limit_scans(monkeypatch, 1)
    store = _saved_store(tmp_path)
    inside, let_go = _block_searches(monkeypatch)

    running = _search_on(store)
    assert inside.wait(TIMEOUT)
    queued = _search_on(store)
    _waiting_for_a_slot()
    assert len(store._index_lock._readers) == 1  # only the one actually scanning

    writer = _background(lambda: store.add_chunks(_chunks(5, "new"), _vectors(5, seed=2)))
    _until(lambda: store._index_lock._writers_waiting == 1)
    let_go.set()
    _finish(running, writer, queued)
    assert store.memory_stats()["vectors"] == 25


def test_a_writer_on_one_collection_does_not_stall_scans_of_another(tmp_path, monkeypatch):
    """Holding a slot while waiting for its own collection's lock would stall all of them."""
    _limit_scans(monkeypatch, 1)
    written, other = _saved_store(tmp_path / "written"), _saved_store(tmp_path / "other")
    lock = written._index_lock
    stepped_aside = threading.Event()
    real_wait = lock.wait_until_no_writers

    def wait_until_no_writers():
        stepped_aside.set()
        real_wait()

    monkeypatch.setattr(lock, "wait_until_no_writers", wait_until_no_writers)

    with lock:
        behind_the_writer = _search_on(written)
        assert stepped_aside.wait(TIMEOUT)
        _finish(_search_on(other))  # with the one slot this search just gave back

    _finish(behind_the_writer)


def test_a_search_sent_away_by_a_writer_keeps_its_place_in_line(tmp_path, monkeypatch):
    _limit_scans(monkeypatch, 1)
    store = _saved_store(tmp_path)
    slots = vector_store_module._current_scan_slots()
    asked_for, given = [], []
    real_acquire = slots.acquire

    def acquire(place=None):
        asked_for.append(place)
        given.append(real_acquire(place))
        return given[-1]

    monkeypatch.setattr(slots, "acquire", acquire)

    with store._index_lock:
        search = _search_on(store)
        _until(lambda: len(given) == 1)
        _until(lambda: slots._free == 1)  # stepped aside, slot back
    _finish(search)

    assert asked_for == [None, given[0]]


def test_a_writer_that_searches_gets_a_turn_without_deadlock(tmp_path, monkeypatch):
    """Other searches queued on the same collection step aside for it, and so must not block it."""
    _limit_scans(monkeypatch, 1)
    store = _saved_store(tmp_path)
    query = _vectors(1, seed=5)[0]
    stop = threading.Event()

    def keep_searching():
        while not stop.is_set():
            store.search(query, top_k=5)

    others = [_background(keep_searching) for _ in range(4)]

    def nested():
        for _ in range(10):
            with store._index_lock:
                store.add_chunks(_chunks(1, "new"), _vectors(1, seed=2))
                assert store.search(query, top_k=5)
                store.delete_documents(["new"])

    try:
        _finish(_background(nested))
    finally:
        stop.set()
    _finish(*others)


def test_a_released_index_is_read_back_without_holding_a_slot(tmp_path, monkeypatch):
    _limit_scans(monkeypatch, 1)
    store = _saved_store(tmp_path)
    assert store.unload_if_idle(0) is True
    slots = vector_store_module._current_scan_slots()
    free_during_load = []
    real_load = store._load_or_create_index

    def load():
        free_during_load.append(slots._free)
        real_load()

    monkeypatch.setattr(store, "_load_or_create_index", load)
    # Not store.search(), whose empty-index check would read it back first.
    similarities, _ = store._top_matches(_vectors(1, seed=5)[:1], 5, None)
    assert similarities.shape == (1, 5)
    assert free_during_load == [1]
    assert slots._free == 1


def test_a_slot_is_given_back_when_a_scan_fails(tmp_path, monkeypatch):
    _limit_scans(monkeypatch, 1)
    store = _saved_store(tmp_path)

    def broken(*args, **kwargs):
        raise RuntimeError("scan failed")

    monkeypatch.setattr(faiss.IndexIDMap2, "search", broken)
    with pytest.raises(RuntimeError):
        store.search(_vectors(1, seed=5)[0], top_k=5)
    assert vector_store_module._current_scan_slots()._free == 1
    assert not store._index_lock._readers


def test_default_limit_is_half_the_usable_cpus(monkeypatch):
    def usable(count):
        if hasattr(config.os, "sched_getaffinity"):
            monkeypatch.setattr(config.os, "sched_getaffinity", lambda pid: set(range(count)))
        monkeypatch.setattr(config.os, "cpu_count", lambda: count)

    usable(12)
    assert config._half_the_cpus() == 6
    usable(1)
    assert config._half_the_cpus() == 1


# ── The sweep across collections ────────────────────────────────────────────


class _Indexer:
    def __init__(self, store):
        self.vector_store = store


@pytest.fixture()
def manager(tmp_path, monkeypatch):
    from config import settings
    from services.indexer_manager import IndexerManager

    instance = IndexerManager.__new__(IndexerManager)
    instance._indexers = {"a": _Indexer(_saved_store(tmp_path / "a")), "b": _Indexer(_saved_store(tmp_path / "b"))}
    instance.index_unload_count = 0
    monkeypatch.setattr(settings, "index_idle_unload_seconds", 1)
    for indexer in instance._indexers.values():
        indexer.vector_store._last_used -= 10
    return instance


def test_sweep_releases_idle_collections_and_counts_them(manager):
    assert manager.index_memory_stats()["loaded"] == 2
    manager._indexers["b"].vector_store.search(_vectors(1, seed=5)[0], top_k=2)

    assert manager.unload_idle_indexes() == 1

    stats = manager.index_memory_stats()
    assert stats["open"] == 2
    assert stats["loaded"] == 1
    assert stats["vectors"] == 20
    assert stats["unloads"] == 1


def test_sweep_is_off_at_zero(manager, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "index_idle_unload_seconds", 0)
    assert manager.unload_idle_indexes() == 0
    assert manager.index_memory_stats()["loaded"] == 2

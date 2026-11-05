"""A reentrant readers-writer lock for guarding a FAISS index, and a fair semaphore.

The standard library has no readers-writer lock. FAISS allows any number of concurrent searches
but nothing alongside a mutation (a crash, not an exception), so searches share
this lock and everything else takes it alone.

``with lock:`` and ``acquire()`` are exclusive, so code written against the
RLock this replaced keeps its meaning. Only ``with lock.shared():`` shares.

Rules that keep it from deadlocking:

- A thread holding the lock may take it again, in either mode. A writer that
  searches stays a writer.
- A thread holding shared access cannot go on to take it exclusively. Two
  readers doing that would wait on each other forever, so it raises instead.
  Release first and check again afterwards.
- A waiting writer holds back new readers, or a steady stream of searches would
  keep it out for good. A thread that already reads is let through regardless,
  because the writer is waiting on that very thread. The cost is that writers
  arriving back to back can hold readers up, which suits this index: writes come
  from a few jobs that spend most of their time embedding between batches.
"""

import heapq
import itertools
import threading
from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, Tuple


class FifoSemaphore:
    """A semaphore that serves waiters strictly in the order they arrived.

    threading.Semaphore lets a thread that is already running take a slot ahead
    of the ones asleep waiting for it. Under a long queue of slow scans that
    leaves some callers served again and again while others get almost nothing,
    and their latency never reaches a percentile because they never finish.

    A caller that has to step out of line can come back to its old place by
    passing it to acquire() again. Otherwise everyone who stepped out together
    would rejoin in whatever order they woke, losing the fairness above.
    """

    def __init__(self, slots: int) -> None:
        self._guard = threading.Lock()
        self._free = slots
        self._arrivals = itertools.count()
        self._waiting: List[Tuple[int, threading.Lock]] = []  # a heap, earliest place first

    def acquire(self, place: Optional[int] = None) -> int:
        """Wait for a slot, and return this caller's place in line."""
        with self._guard:
            if place is None:
                place = next(self._arrivals)
            if self._free:
                self._free -= 1
                return place
            ticket = threading.Lock()
            ticket.acquire()
            heapq.heappush(self._waiting, (place, ticket))
        ticket.acquire()  # released by release(), which hands its slot straight to this thread
        return place

    def release(self) -> None:
        with self._guard:
            if self._waiting:
                heapq.heappop(self._waiting)[1].release()
            else:
                self._free += 1


class ReadWriteLock:
    def __init__(self) -> None:
        self._changed = threading.Condition(threading.Lock())
        self._readers: Dict[int, int] = {}  # thread id -> times it holds shared access
        self._writer: Optional[int] = None
        self._writer_depth = 0
        self._writers_waiting = 0

    def acquire(self, blocking: bool = True) -> bool:
        """Take exclusive access. Without blocking, False if anyone else holds or awaits it."""
        me = threading.get_ident()
        with self._changed:
            if self._writer == me:
                self._writer_depth += 1
                return True
            if me in self._readers:
                if not blocking:
                    return False
                raise RuntimeError("cannot take exclusive access while holding shared access")
            if not blocking:
                if self._writer is not None or self._readers or self._writers_waiting:
                    return False
            else:
                self._writers_waiting += 1
                try:
                    while self._writer is not None or self._readers:
                        self._changed.wait()
                except BaseException:
                    # Readers may be held back by this writer alone.
                    self._writers_waiting -= 1
                    self._changed.notify_all()
                    raise
                self._writers_waiting -= 1
            self._writer = me
            self._writer_depth = 1
            return True

    def release(self) -> None:
        """Give back exclusive access."""
        with self._changed:
            if self._writer != threading.get_ident():
                raise RuntimeError("release of exclusive access this thread does not hold")
            self._writer_depth -= 1
            if self._writer_depth == 0:
                self._writer = None
                self._changed.notify_all()

    def __enter__(self) -> "ReadWriteLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()

    def acquire_shared(self) -> None:
        """Take shared access, waiting for any writer that holds or awaits the lock."""
        me = threading.get_ident()
        with self._changed:
            if self._writer == me:
                self._writer_depth += 1
                return
            if me not in self._readers:
                while self._writer is not None or self._writers_waiting:
                    self._changed.wait()
            self._readers[me] = self._readers.get(me, 0) + 1

    def try_acquire_shared(self) -> bool:
        """Take shared access if that needs no waiting; False if it would."""
        me = threading.get_ident()
        with self._changed:
            if self._writer == me:
                self._writer_depth += 1
                return True
            if me not in self._readers and (self._writer is not None or self._writers_waiting):
                return False
            self._readers[me] = self._readers.get(me, 0) + 1
            return True

    def wait_until_no_writers(self) -> None:
        """Block until no writer holds or awaits the lock. Takes nothing."""
        with self._changed:
            while self._writer is not None or self._writers_waiting:
                self._changed.wait()

    def release_shared(self) -> None:
        """Give back shared access."""
        me = threading.get_ident()
        with self._changed:
            if self._writer == me:
                self._writer_depth -= 1
                return
            held = self._readers.get(me)
            if held is None:
                raise RuntimeError("release of shared access this thread does not hold")
            if held > 1:
                self._readers[me] = held - 1
                return
            del self._readers[me]
            if not self._readers:
                self._changed.notify_all()

    @contextmanager
    def shared(self) -> Iterator[None]:
        """``with lock.shared():`` for access other readers can hold at the same time."""
        self.acquire_shared()
        try:
            yield
        finally:
            self.release_shared()

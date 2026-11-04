"""A reentrant readers-writer lock, written for guarding a FAISS index.

The standard library has none. FAISS allows any number of concurrent searches
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

import threading
from contextlib import contextmanager
from typing import Dict, Iterator, Optional


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

"""Index jobs left running by one test must not run into the next.

A route that answers 202 leaves its job running on an ``index-job-N`` thread
(services/upload_service.py), and a test that only checks the 202 returns
while it still runs. The job reads ``app_db.db_path`` and the indexer
registry on every call, so once the next test's fixtures repoint them it
writes into that test's fresh database while the fixture is initialising
it. CI saw that as "database is locked" in tests/test_local_source_roots.py.
"""

import threading
import time

JOB_THREAD_PREFIX = "index-job-"


def running_jobs() -> list:
    return [t for t in threading.enumerate()
            if t.name.startswith(JOB_THREAD_PREFIX) and t.is_alive()]


def drain_index_jobs(timeout: float) -> list:
    """Wait until no index job thread is alive; return the names still alive at the deadline.

    A finishing job dispatches the next queued one on a new thread, so this
    looks again after each join rather than joining a fixed list.
    """
    deadline = time.monotonic() + timeout
    while True:
        alive = running_jobs()
        remaining = deadline - time.monotonic()
        if not alive or remaining <= 0:
            return [t.name for t in alive]
        alive[0].join(timeout=min(remaining, 0.5))

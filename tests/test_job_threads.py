"""The suite waits for a test's index jobs before the next test starts (tests/job_threads.py)."""

import threading
import time

from tests.job_threads import drain_index_jobs, running_jobs


def _job(name, seconds, then=None):
    def body():
        time.sleep(seconds)
        if then:
            then()
    thread = threading.Thread(target=body, name=name, daemon=True)
    thread.start()
    return thread


def test_a_running_job_is_waited_for():
    job = _job("index-job-9001", 0.3)

    assert drain_index_jobs(timeout=5) == []
    assert not job.is_alive()


def test_a_job_queued_behind_it_is_waited_for_too():
    """A finishing job starts the next one in line on a fresh thread."""
    second = []
    _job("index-job-9002", 0.2, then=lambda: second.append(_job("index-job-9003", 0.2)))

    assert drain_index_jobs(timeout=5) == []
    assert second and not second[0].is_alive()


def test_a_hung_job_is_named_rather_than_waited_on_forever():
    stop = threading.Event()
    hung = threading.Thread(target=stop.wait, name="index-job-9004", daemon=True)
    hung.start()
    try:
        started = time.monotonic()
        assert drain_index_jobs(timeout=0.3) == ["index-job-9004"]
        assert time.monotonic() - started < 2
    finally:
        stop.set()
        hung.join()


def test_other_threads_are_not_mistaken_for_jobs():
    stop = threading.Event()
    other = threading.Thread(target=stop.wait, name="source-watcher", daemon=True)
    other.start()
    try:
        assert "source-watcher" not in [t.name for t in running_jobs()]
        assert drain_index_jobs(timeout=0.2) == []
    finally:
        stop.set()
        other.join()

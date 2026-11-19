"""Time spent in each stage of a search or an answer.

    with stage("embed_query"):
        vector = model.embed_query(text)

Every stage is observed in the ``clio_stage_duration_seconds`` histogram
(served at /metrics) and added to the trace of the request it runs in, when
one was started with ``trace()``. A search returns its own trace in the
``retrieval`` block; an answer logs one line with every stage it went
through.

The stage names are a fixed set written in the code, never anything from
the request, so the metric label cannot grow and carries no query text.
"""

from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

from prometheus_client import Histogram

from services.metrics import registry

STAGES = (
    "embed_query", "vector_search", "keyword_search", "rerank",
    "model_call", "first_text", "answer",
)

stage_seconds = Histogram(
    "clio_stage_duration_seconds",
    "Time spent in one stage of a search or an answer: embed_query, vector_search, "
    "keyword_search, rerank, model_call (each provider call), first_text (question "
    "received to the first answer text) and answer (the whole answer).",
    ["stage"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
    registry=registry,
)

_trace: contextvars.ContextVar[Optional[Dict[str, float]]] = contextvars.ContextVar("clio_stage_trace", default=None)


def observe(name: str, seconds: float) -> None:
    """Record ``seconds`` against stage ``name``."""
    stage_seconds.labels(name).observe(seconds)
    current = _trace.get()
    if current is not None:
        current[name] = current.get(name, 0.0) + seconds * 1000.0


@contextmanager
def stage(name: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        observe(name, time.perf_counter() - started)


@contextmanager
def trace() -> Iterator[Dict[str, float]]:
    """Collect the stages run inside the block, in milliseconds, into the
    dict it yields. A nested trace also adds to the outer one."""
    outer = _trace.get()
    mine: Dict[str, float] = {}
    token = _trace.set(mine)
    try:
        yield mine
    finally:
        _trace.reset(token)
        if outer is not None:
            for k, v in mine.items():
                outer[k] = outer.get(k, 0.0) + v


def rounded(timings: Dict[str, float]) -> Dict[str, float]:
    return {k: round(v, 1) for k, v in timings.items()}

"""Rank metrics shared by the retrieval benchmark and the coverage report.

Small and dependency-free on purpose: ``scripts/retrieval_bench.py`` (a
labelled fixture, run by hand) and ``services/coverage_report.py`` (probes
derived from a live collection) both ask the same questions of a ranked
result list — where did the thing I was looking for land, and how does that
average out — so the arithmetic lives here once.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence


def first_rank(wanted: Iterable[str], result_ids: Sequence[str]) -> Optional[int]:
    """1-based rank of the first result that is in ``wanted``; None when absent."""
    targets = set(wanted)
    for rank, rid in enumerate(result_ids, start=1):
        if rid in targets:
            return rank
    return None


def hit_at(rank: Optional[int], k: int) -> bool:
    return rank is not None and rank <= k


def reciprocal_rank(rank: Optional[int]) -> float:
    return 1.0 / rank if rank else 0.0


def mean(values: Iterable[Optional[float]]) -> Optional[float]:
    """Mean of the non-None values; None when there are none."""
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def percentile(values: Sequence[float], pct: float) -> float:
    """Linear-interpolated percentile (``pct`` in 0..1); NaN for no values."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)

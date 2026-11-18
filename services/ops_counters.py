"""Process-wide counts of things going wrong, for /metrics (services/metrics.py).

Failed sign-ins and lockouts are counted by the failed sign-in limiter
itself; this holds the rest: jobs that failed, by kind (index, report,
library), and model calls that raised, by provider. Labels come from short
fixed sets, never from a person, a query or a document.
"""

from __future__ import annotations

import threading
from collections import Counter
from typing import Dict

_lock = threading.Lock()
_counts: Counter = Counter()


def incr(name: str, label: str = "") -> None:
    with _lock:
        _counts[(name, label)] += 1


def snapshot(name: str) -> Dict[str, int]:
    """label -> count for one counter."""
    with _lock:
        return {label: n for (key, label), n in _counts.items() if key == name}


def reset() -> None:
    """Testing hook."""
    with _lock:
        _counts.clear()

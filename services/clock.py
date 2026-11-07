"""The current time in UTC, as the naive datetime the stores already hold.

The standard library's ``utcnow`` is deprecated (Python 3.12) and slated for
removal. Its drop-in, ``datetime.now(timezone.utc)``, is timezone-aware, which
would change every ``isoformat()`` written to SQLite and Postgres (a
``+00:00`` suffix) and make comparisons with timestamps already stored there
raise. This keeps the old value exactly: UTC, without tzinfo.
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Now, in UTC, as a naive datetime (what the deprecated call returned)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

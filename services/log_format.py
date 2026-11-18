"""Log setup: readable lines by default, one JSON object per line on request.

A log shipper or SIEM on an enterprise network parses LOG_FORMAT=json without a
custom grok pattern, and a multi-line traceback stays one event.
"""

import json
import logging
from datetime import datetime, timezone

_TEXT_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s%(rid_suffix)s"

# Attributes every LogRecord has. Anything else on a record came from `extra=`.
_STANDARD_ATTRIBUTES = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName", "rid_suffix"}


class RequestIdFilter(logging.Filter):
    """Puts the request's id on every line logged while it is handled
    (middleware/request_context.py): ``request_id`` for LOG_FORMAT=json, and
    " rid=..." at the end of a text line. Lines outside a request get none."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            try:
                from middleware.request_context import request_id

                rid = request_id()
            except Exception:  # noqa: BLE001 - a log line must never fail
                rid = None
            if rid:
                record.request_id = rid
        rid = getattr(record, "request_id", None)
        # The access line names it already (middleware/request_meta.py).
        record.rid_suffix = f" rid={rid}" if rid and "rid=" not in str(record.msg) else ""
        return True


class TextFormatter(logging.Formatter):
    """The text format, tolerant of a record that skipped RequestIdFilter."""

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "rid_suffix"):
            record.rid_suffix = ""
        return super().format(record)


_request_id_filter = RequestIdFilter()

# uvicorn configures these before it imports the app, with handlers of their own
# and propagation off, so they do not pick up the root logger's format.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRIBUTES:
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure(log_format: str, log_level: str = "INFO") -> None:
    level = getattr(logging, str(log_level or "INFO").upper(), logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(TextFormatter(_TEXT_FORMAT))
        root.addHandler(handler)
    # Set on its own: a root logger that already had handlers keeps them.
    root.setLevel(level)
    loggers = (root, *map(logging.getLogger, _UVICORN_LOGGERS))
    for logger in loggers:
        for handler in logger.handlers:
            if _request_id_filter not in handler.filters:
                handler.addFilter(_request_id_filter)
    if log_format != "json":
        return
    formatter = JsonFormatter()
    for logger in loggers:
        for handler in logger.handlers:
            handler.setFormatter(formatter)

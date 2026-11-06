"""Log setup: readable lines by default, one JSON object per line on request.

A log shipper or SIEM on an enterprise network parses LOG_FORMAT=json without a
custom grok pattern, and a multi-line traceback stays one event.
"""

import json
import logging
from datetime import datetime, timezone

_TEXT_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Attributes every LogRecord has. Anything else on a record came from `extra=`.
_STANDARD_ATTRIBUTES = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}

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


def configure(log_format: str) -> None:
    logging.basicConfig(level=logging.INFO, format=_TEXT_FORMAT)
    if log_format != "json":
        return
    formatter = JsonFormatter()
    for logger in (logging.getLogger(), *map(logging.getLogger, _UVICORN_LOGGERS)):
        for handler in logger.handlers:
            handler.setFormatter(formatter)

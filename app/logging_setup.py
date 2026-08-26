"""Structured JSON logging with a request-scoped trace id.

Principle 2 (everything is traceable): every log line emitted while handling a
request carries the same `trace_id` that is stored on the proposal, so a log
grep and a database row can be joined after the fact.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)

# Attributes LogRecord always carries; anything else was passed via `extra=`.
_STANDARD_RECORD_KEYS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def new_trace_id() -> str:
    return uuid.uuid4().hex


def get_trace_id() -> str | None:
    return _trace_id.get()


@contextmanager
def trace_context(trace_id: str) -> Iterator[str]:
    token = _trace_id.set(trace_id)
    try:
        yield trace_id
    finally:
        _trace_id.reset(token)


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with `extra=` fields merged in at top level."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "trace_id": get_trace_id(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_KEYS:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_OURS = "_mtm_json_handler"


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON handler on the root logger, idempotently.

    Only handlers this function installed are removed. Wiping every root
    handler would be rude to whatever else configured logging first -- a host
    application, or pytest's log capture -- and the resulting "the logs are
    gone" is an unpleasant thing to debug.
    """
    root = logging.getLogger()
    for existing in [h for h in root.handlers if getattr(h, _OURS, False)]:
        root.removeHandler(existing)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _OURS, True)
    root.addHandler(handler)
    root.setLevel(level.upper())

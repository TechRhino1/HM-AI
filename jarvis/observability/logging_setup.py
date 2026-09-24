"""Structured, consistently-keyed log records.

WHY THIS EXISTS
---------------
Logging here was `logging.basicConfig` with a bracketed format and ~141
f-string messages on the money path. That is fine to read and impossible to
query: no correlation id, no fixed vocabulary, and every call site choosing its
own spellings. A trade that failed at 03:14 could not be reconstructed from the
log alone.

What this module adds, and what it deliberately does not do:

* **It does not replace `logging`.** Every module keeps its own `logging.getLogger(...)`
  and its existing messages keep working byte-for-byte. Only the *rendering* changes,
  and only when someone calls :func:`configure_logging`.
* **It fixes the key vocabulary.** Every record carries the same top-level keys
  (`ts`, `level`, `logger`, `event`, `msg`) plus whatever was bound via
  `jarvis.observability.context` or passed as `extra=`. Fields are merged, not
  nested, so a query does not have to know where a field came from.
* **It never breaks the caller.** A field that will not serialise is rendered
  with `str()`, an unformattable record falls back to a minimal JSON object,
  and configuring twice is idempotent.

`extra=` is the intended way to attach per-call fields — it is already
supported by every `logger.*` call and needs no adapter, so instrumenting an
existing call is a one-line change.

This module is a leaf: it imports nothing from `jarvis.*`.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, TextIO

from .context import EVENT, ERROR_TYPE, bound

#: Attributes that belong to `LogRecord` itself. Computed from a real record
#: rather than hand-listed, because the set differs between Python versions
#: (`taskName` arrived in 3.12) and a stale list would silently publish
#: internals — or drop a field the caller meant to emit.
_RESERVED: frozenset = frozenset(
    logging.LogRecord("x", logging.INFO, "", 0, "", None, None).__dict__
) | {"message", "asctime"}

_ENV_LEVEL = "JARVIS_LOG_LEVEL"
_ENV_FORMAT = "JARVIS_LOG_FORMAT"

_HANDLER_TAG = "_jarvis_observability"


def _default_level() -> int:
    raw = (os.environ.get(_ENV_LEVEL) or "INFO").strip().upper()
    return getattr(logging, raw, logging.INFO) if raw.isalpha() else logging.INFO


def _json_requested(explicit: Optional[bool]) -> bool:
    if explicit is not None:
        return bool(explicit)
    return (os.environ.get(_ENV_FORMAT) or "json").strip().lower() == "json"


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with a fixed key set.

    Bound context is merged first and `extra=` fields win over it, so a call
    site can override a context field for one line without touching the
    binding. `event` is always present even when absent from the record: a
    consumer must be able to rely on the key existing.
    """

    def __init__(self) -> None:
        super().__init__(datefmt="%Y-%m-%dT%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        try:
            extras = {
                k: v for k, v in record.__dict__.items() if k not in _RESERVED
            }
            payload: Dict[str, Any] = {
                "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
            }
            merged: Dict[str, Any] = dict(bound())
            merged.update(extras)
            # `event` is hoisted so every line has the same leading keys.
            payload[EVENT] = merged.pop(EVENT, None)
            payload["msg"] = record.getMessage()
            payload.update(merged)

            if record.exc_info:
                payload[ERROR_TYPE] = record.exc_info[0].__name__
                payload["exc"] = self.formatException(record.exc_info)
            elif record.exc_text:
                payload["exc"] = record.exc_text

            return json.dumps(payload, default=str, ensure_ascii=False)
        except Exception:
            # A formatter that raises loses the record and, worse, raises inside
            # the caller's `logger.*` call. Degrade to the bare facts.
            try:
                return json.dumps(
                    {"ts": datetime.now(timezone.utc).isoformat(),
                     "level": record.levelname,
                     "logger": record.name,
                     EVENT: None,
                     "msg": record.getMessage()},
                    default=str,
                )
            except Exception:
                return "{}"


class KeyValueFormatter(logging.Formatter):
    """Human-readable rendering of the same fields, for a terminal.

    Kept because an operator debugging at the console should not have to read
    JSON, and because the two renderers must expose the same fields or the
    switch becomes a behavioural change rather than a formatting one.
    """

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        fields: Dict[str, Any] = dict(bound())
        fields.update(extras)
        if not fields:
            return base
        rendered = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
        return f"{base} {rendered}" if rendered else base


def configure_logging(
    level: Optional[int] = None,
    *,
    json_output: Optional[bool] = None,
    stream: Optional[TextIO] = None,
    logger_name: Optional[str] = None,
) -> logging.Handler:
    """Install the structured formatter. Idempotent, and safe to call twice.

    Attaches to (and only removes) handlers this function itself installed, so
    it composes with pytest's capture handlers and with any handler an embedder
    already configured.

    Returns the handler so a caller can remove it again.
    """
    target = logging.getLogger(logger_name) if logger_name else logging.getLogger()
    resolved_level = _default_level() if level is None else level

    for existing in list(target.handlers):
        if getattr(existing, _HANDLER_TAG, False):
            target.removeHandler(existing)

    handler = logging.StreamHandler(stream or sys.stderr)
    if _json_requested(json_output):
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            KeyValueFormatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
                              datefmt="%Y-%m-%d %H:%M:%S")
        )
    setattr(handler, _HANDLER_TAG, True)
    handler.setLevel(resolved_level)
    target.addHandler(handler)
    # Only raise the level when the caller asked for a louder one; never
    # silently quieten a logger an embedder set to DEBUG.
    if resolved_level < target.level or target.level == logging.NOTSET:
        target.setLevel(resolved_level)
    return handler


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    msg: Optional[str] = None,
    **fields: Any,
) -> None:
    """Log one named event with its fields, always carrying `event`.

    `logger.info(f"...")` still works; this exists so new call sites on
    instrumented paths get the canonical key without remembering to pass it.
    """
    if logger.isEnabledFor(level):
        logger.log(level, msg or event, extra={EVENT: event, **fields})


def log_exception(logger: logging.Logger, event: str, exc: BaseException, **fields: Any) -> None:
    """Log a failure with `error_type` set and the traceback attached."""
    logger.error(
        "%s: %s", event, exc,
        exc_info=exc,
        extra={EVENT: event, ERROR_TYPE: type(exc).__name__, **fields},
    )


def record_fields(record: logging.LogRecord) -> Mapping[str, Any]:
    """The merged fields a record would publish. For tests and for exporters."""
    fields: Dict[str, Any] = dict(bound())
    fields.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
    return fields

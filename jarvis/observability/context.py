"""Bound context: the fields that travel with a unit of work.

WHY THIS EXISTS
---------------
The platform logs with plain `logging` calls spread across ~40 modules. Two
things were impossible as a result:

* *Attribution.* A log line saying "order failed" did not say which symbol,
  which request, or which cycle produced it, because each call site decided
  for itself what to interpolate into the message. Reconstructing one failed
  trade meant grepping by timestamp and hoping.
* *Consistency.* The same fact was spelled `sym`, `symbol`, `SYMBOL` and
  `instr` in different modules, so no query over the log could be written once.

This module fixes both with a small amount of machinery: a `ContextVar` holding
a mapping of bound fields, and a fixed vocabulary of canonical keys. Bound
fields are merged into every log record by `jarvis.observability.logging_setup`
and are available to metric labels via `bound()`.

The vocabulary is deliberately small. A key earns a place here only if a query
needs to filter on it; everything else can be an ad-hoc `extra=` field on the
individual call, which is still emitted.

This module is a leaf: it imports nothing from `jarvis.*`.
"""
from __future__ import annotations

import contextvars
import uuid
from types import MappingProxyType
from typing import Any, Iterator, Mapping, Optional

# ── Canonical keys ──────────────────────────────────────────────────────────
# Use these constants rather than string literals so a rename is a findable
# edit. Each one is documented because "consistently keyed" is only useful if
# the meaning of each key is the same everywhere.
EVENT = "event"              #: What happened. Snake-case verb_object, e.g. order_submitted.
COMPONENT = "component"      #: Which subsystem emitted it, e.g. execution, news.
SYMBOL = "symbol"            #: The instrument, canonical form (XAUUSD, not XAUUSD.m).
REQUEST_ID = "request_id"    #: One id per HTTP request, tying every line it caused together.
ROUTE = "route"              #: Normalised HTTP path, or the operation name off the web path.
OUTCOME = "outcome"          #: What the operation decided/returned, e.g. filled, refused.
DURATION_MS = "duration_ms"  #: Wall-clock duration of the operation.
ERROR_TYPE = "error_type"    #: `type(exc).__name__` — never the message, which can carry a secret.
SOURCE = "source"            #: Where data came from: live_mt5, synthetic, cache, faireconomy...

CANONICAL_KEYS: frozenset = frozenset(
    {EVENT, COMPONENT, SYMBOL, REQUEST_ID, ROUTE, OUTCOME, DURATION_MS, ERROR_TYPE, SOURCE}
)

_EMPTY: Mapping[str, Any] = MappingProxyType({})

#: One mapping per context (thread, task, or any `contextvars` copy). The
#: default is an empty *immutable* mapping: `bind()` never mutates what another
#: context can see, so a binding made on a request thread cannot leak into the
#: next request served by that thread.
_bindings: contextvars.ContextVar[Mapping[str, Any]] = contextvars.ContextVar(
    "jarvis_observability_bindings", default=_EMPTY
)


def bound() -> Mapping[str, Any]:
    """The fields bound in this context. Empty mapping when none are."""
    return _bindings.get()


def bind(**fields: Any) -> contextvars.Token:
    """Bind fields for this context, returning a token for :func:`unbind`.

    `None` values are dropped rather than stored: an unset field must be absent
    from the log record, not present-and-null, so "symbol is unknown" and
    "symbol is the string 'None'" stay distinguishable.
    """
    current = dict(_bindings.get())
    current.update({k: v for k, v in fields.items() if v is not None})
    return _bindings.set(MappingProxyType(current))


def unbind(token: contextvars.Token) -> None:
    """Restore the bindings that existed before the matching :func:`bind`.

    A token from another context raises `ValueError`; that is swallowed here
    because a failure to unbind must never mask the error that got us to the
    `finally` block doing the unbinding.
    """
    try:
        _bindings.reset(token)
    except (ValueError, RuntimeError):
        pass


class bound_scope:
    """Bind fields for the duration of a `with` block.

    Use instead of bind/unbind when the block can raise — `contextvars` tokens
    are easy to drop otherwise, and a dropped token means the next unit of work
    inherits this one's symbol and request id.
    """

    __slots__ = ("_token", "_fields")

    def __init__(self, **fields: Any) -> None:
        self._fields = fields

    def __enter__(self) -> "bound_scope":
        self._token = bind(**self._fields)
        return self

    def __exit__(self, *exc_info: Any) -> None:
        unbind(self._token)
        return False


def clear() -> None:
    """Drop every binding in this context. For tests and for pool workers."""
    _bindings.set(_EMPTY)


def new_request_id() -> str:
    """A fresh, opaque correlation id."""
    return uuid.uuid4().hex[:16]


def current_request_id() -> Optional[str]:
    """The correlation id bound in this context, or None when there is none."""
    value = _bindings.get().get(REQUEST_ID)
    return str(value) if value is not None else None


def iter_bound() -> Iterator[tuple]:
    """`(key, value)` pairs bound in this context. Read-only view for emitters."""
    return iter(_bindings.get().items())

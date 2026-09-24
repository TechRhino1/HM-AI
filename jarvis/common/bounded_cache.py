"""A dict-shaped cache that cannot grow without limit.

WHY THIS EXISTS
---------------
Several caches in this platform are keyed, directly or indirectly, on values a
caller supplies — a symbol from an HTTP query string, a timeframe, a scan
universe. Each one was a plain `dict` with a TTL *check* but no eviction: an
expired entry was never removed, and a key that had never been seen before was
always inserted. So the cache grew monotonically for the life of the process,
and the only thing that bounded it was how many distinct keys someone happened
to ask for. `/api/candles?symbol=<anything>` is a one-line way to ask for
arbitrarily many.

A TTL alone does not fix this: it bounds staleness, not size. What bounds size
is a ceiling on entries plus a replacement policy, which is what this adds.

The public surface is the `dict` subset the call sites actually use
(`get`, `[]`, `in`, `len`, `pop`, `clear`, `items`), so swapping a plain dict
for this is a one-line change and does not ripple.

This module is a leaf: it imports nothing from `jarvis.*`, per the package
contract in `jarvis/common/__init__.py`.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Iterator, Optional, Tuple


class BoundedTTLCache:
    """LRU cache with a hard entry ceiling and an optional TTL.

    Thread-safety: this is deliberately NOT internally locked. Every cache in
    the platform is either touched under a lock the caller already holds or is a
    best-effort read cache where a torn update costs one extra fetch; adding a
    lock here would invite lock-ordering problems between subsystems that share
    nothing else. Make one per owner and guard it where the owner is guarded.
    """

    __slots__ = ("max_entries", "ttl_sec", "clock", "name", "_data", "_expiry", "evictions")

    def __init__(self, max_entries: int = 256, ttl_sec: Optional[float] = None,
                 clock: Callable[[], float] = time.monotonic, name: str = "cache") -> None:
        if max_entries < 1:
            raise ValueError(f"max_entries must be >= 1, got {max_entries!r}")
        if ttl_sec is not None and (ttl_sec < 0):
            raise ValueError(f"ttl_sec must be >= 0 or None, got {ttl_sec!r}")
        self.max_entries = int(max_entries)
        self.ttl_sec = ttl_sec
        self.clock = clock
        self.name = name
        self._data: Dict[Any, Any] = {}
        self._expiry: Dict[Any, float] = {}
        #: How many entries the ceiling has evicted. Non-zero means the working
        #: set is larger than the cache, which is a tuning signal rather than an
        #: error — but it also means hit rate is being traded for a memory bound.
        self.evictions = 0

    # ── dict protocol ───────────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: Any) -> bool:
        if key not in self._data:
            return False
        if self._is_expired(key):
            self._drop(key)
            return False
        return True

    def __getitem__(self, key: Any) -> Any:
        value = self.get(key, _MISSING)
        if value is _MISSING:
            raise KeyError(key)
        return value

    def __setitem__(self, key: Any, value: Any) -> None:
        now = self.clock()
        if key in self._data:
            # Re-insert to move it to the most-recently-used end.
            del self._data[key]
        self._data[key] = value
        if self.ttl_sec is None:
            self._expiry.pop(key, None)
        else:
            self._expiry[key] = now + self.ttl_sec
        self._enforce_ceiling()

    def get(self, key: Any, default: Any = None) -> Any:
        """The value for `key`, or `default` if absent or expired.

        A hit also refreshes the entry's position in the LRU order, so a
        repeatedly-read key survives a burst of one-off keys.
        """
        if key not in self._data:
            return default
        if self._is_expired(key):
            self._drop(key)
            return default
        value = self._data.pop(key)
        self._data[key] = value
        return value

    def pop(self, key: Any, default: Any = None) -> Any:
        self._expiry.pop(key, None)
        return self._data.pop(key, default)

    def clear(self) -> None:
        self._data.clear()
        self._expiry.clear()

    def items(self) -> Iterator[Tuple[Any, Any]]:
        return iter(list(self._data.items()))

    def keys(self) -> Iterator[Any]:
        return iter(list(self._data.keys()))

    # ── Maintenance ─────────────────────────────────────────────────────────
    def sweep(self, now: Optional[float] = None) -> int:
        """Drop every expired entry. Returns how many were removed.

        A TTL check only removes an entry when that key is asked for again, so
        a one-off key stays resident forever. Call this from a periodic task if
        the cache must reclaim memory without being touched.
        """
        stamp = self.clock() if now is None else now
        expired = [k for k, expiry in self._expiry.items() if expiry <= stamp]
        for key in expired:
            self._drop(key)
        return len(expired)

    def _is_expired(self, key: Any) -> bool:
        expiry = self._expiry.get(key)
        return expiry is not None and expiry <= self.clock()

    def _drop(self, key: Any) -> None:
        self._data.pop(key, None)
        self._expiry.pop(key, None)

    def _enforce_ceiling(self) -> None:
        while len(self._data) > self.max_entries:
            # `last=False` evicts the least-recently-used end, because both
            # `__setitem__` and a `get` hit move their key to the other end.
            oldest = next(iter(self._data))
            self._drop(oldest)
            self.evictions += 1


class _Missing:
    """Sentinel distinguishing "absent" from a legitimately stored `None`."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<missing>"


_MISSING = _Missing()

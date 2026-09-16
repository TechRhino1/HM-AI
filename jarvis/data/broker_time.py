"""MT5 reports times on the BROKER's clock, not in UTC.

`position.time`, `deal.time` and `symbol_info_tick().time` are all seconds on the
broker server's clock. XM runs EET/EEST - GMT+2 in winter, GMT+3 in summer - so a
value read as UTC is 2-3 hours ahead of the truth.

That error is not cosmetic, because most consumers subtract such a value from the
real UTC clock:

    duration = datetime.now(timezone.utc) - open_time      # 2-3 hours SHORT

and then clamp it with `max(0.0, ...)`, which turns the whole first three hours of
a position's life into a flat zero. In `position_monitor.py` that duration drives
the horizon-adaptive stagnation exits, so the SCALP "45 minutes without progress"
rule could not fire until 3h45m, and the `if open_dur_sec > 0:` guard skipped the
entire time-decay block - in exactly the window those rules exist to police.

The offset is derived from the broker's own freshest tick rather than hardcoded,
because it moves by an hour with the broker's DST and varies by broker (GMT+2/+3
for XM, +5:30 for India, and so on). Deriving it also means the fix holds if the
account is ever pointed at a different server.

Failure is explicit, never silent: if no trustworthy offset can be derived this
returns 0, which is the previous behaviour, and logs a warning once so the
degradation is visible rather than buried.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# Brokers sit on whole or half hours (GMT+2, +3, +5:30, +9:30). A freshly
# updated tick lands within seconds of one of those boundaries; a STALE tick
# (market closed over a weekend) lands at an arbitrary offset. Requiring the
# candidate to sit near a boundary is what separates the two.
_GRANULARITY_SEC = 1800
_TOLERANCE_SEC = 300

# Sanity range for a real broker offset. Anything outside this is a bad reading.
_MIN_OFFSET_SEC = -12 * 3600
_MAX_OFFSET_SEC = 14 * 3600

# The offset only changes at a DST transition, so it does not need re-deriving on
# every position sync - and each derivation costs an MT5 round-trip per symbol.
_CACHE_TTL_SEC = 600

# The offset is a property of the SERVER, so any symbol's fresh tick yields the
# same answer. That matters because the natural caller passes exactly one symbol
# (the position's), and a single slowly-updating symbol breaks the derivation:
# gold's tick runs ~17 minutes behind EURUSD's, which lands 799s away from the
# nearest 30-minute boundary and trips the 300s staleness tolerance. The result
# was a silent offset of 0 - the exact bug this module exists to prevent - for
# every caller that happened to hold gold. Retrying across liquid majors costs
# one extra round-trip only when the first attempt already failed.
_FALLBACK_OFFSET_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "US500", "US30", "AUDUSD")

_cache: dict = {"offset": None, "at": 0.0, "warned": False}


def _derive_offset(mt5_module, symbols: Iterable[str], clock: float) -> Optional[int]:
    """Return the broker offset in seconds, or None if no trustworthy reading.

    Uses the FRESHEST tick across the given symbols: ticks are server-stamped, so
    the newest one is the closest thing to "now" on the broker's clock.

    Two things have to be true before a tick is readable at all, and each was
    independently fatal here:

    1. **The terminal must be initialized in THIS process.** `symbol_info_tick`
       returns ``None`` for every symbol otherwise, which looks exactly like "no
       trustworthy reading" and silently degrades the offset to 0. The execution
       client only initializes the terminal for modes that place orders, so a
       paper/backtest process that merely *reads* data never had it. Same defect
       as the market-data path — see `broker_symbols.ensure_mt5_terminal`.
    2. **The symbol must be the BROKER's, not the canonical one.** MT5 knows gold
       as ``GOLD.i#``; ``symbol_info_tick("XAUUSD")`` is ``None``. Passing
       canonical names therefore dropped every symbol and returned 0 even with a
       healthy terminal. Callers that already hold broker symbols are unaffected;
       resolution is idempotent and cached.
    """
    # 1. Make sure the terminal exists in this process before asking it anything.
    try:
        from jarvis.data.broker_symbols import ensure_mt5_terminal  # local: avoid import cycle

        ensure_mt5_terminal()
    except Exception:
        pass

    # 2. Ask about the symbol the broker actually knows.
    try:
        from jarvis.data.broker_symbols import resolve_broker_symbol

        resolve = resolve_broker_symbol
    except Exception:
        resolve = lambda s: s  # noqa: E731 - degrade to the previous behaviour

    newest = 0
    for sym in symbols:
        try:
            broker_sym = resolve(sym) or sym
        except Exception:
            broker_sym = sym
        try:
            tick = mt5_module.symbol_info_tick(broker_sym)
        except Exception:
            continue
        if tick is None:
            continue
        try:
            newest = max(newest, int(getattr(tick, "time", 0) or 0))
        except (TypeError, ValueError):
            continue

    if newest <= 0:
        return None

    candidate = newest - int(clock)
    snapped = round(candidate / _GRANULARITY_SEC) * _GRANULARITY_SEC

    if abs(candidate - snapped) > _TOLERANCE_SEC:
        # Not near a boundary: the tick is stale, not the offset.
        return None
    if not (_MIN_OFFSET_SEC <= snapped <= _MAX_OFFSET_SEC):
        return None
    return int(snapped)


def broker_utc_offset(mt5_module=None, symbols: Iterable[str] = (), now: Optional[float] = None) -> int:
    """Seconds the broker's server clock is AHEAD of real UTC.

    Returns 0 when no trustworthy reading is available - the previous (wrong, but
    visible) behaviour - and warns once so the degradation is not silent.

    `now` is injectable for tests.
    """
    clock = time.time() if now is None else now
    if _cache["offset"] is not None and (clock - _cache["at"]) < _CACHE_TTL_SEC:
        return _cache["offset"]

    if mt5_module is None:
        try:
            import MetaTrader5 as mt5_module  # type: ignore
        except Exception:
            return _cache["offset"] or 0

    offset = _derive_offset(mt5_module, list(symbols), clock)
    if offset is None:
        # Retry across liquid majors. The offset is server-wide, so a fresh tick
        # from any instrument answers the same question, and a single slow symbol
        # should not be able to degrade the whole platform to offset 0.
        extra = [s for s in _FALLBACK_OFFSET_SYMBOLS if s not in set(symbols)]
        if extra:
            offset = _derive_offset(mt5_module, extra, clock)

    if offset is None:
        if not _cache["warned"]:
            _cache["warned"] = True
            logger.warning(
                "Could not derive the broker UTC offset from any tick. Times will be "
                "treated as UTC, which under-reports holding durations by the broker's "
                "offset (2-3h for XM). Passing the position symbols usually fixes this."
            )
        return _cache["offset"] or 0

    _cache["offset"] = offset
    _cache["at"] = clock
    return offset


def server_epoch_to_utc(server_epoch: float, mt5_module=None, symbols: Iterable[str] = ()) -> datetime:
    """Convert a broker-server epoch (MT5 `.time`) into a tz-aware UTC datetime."""
    offset = broker_utc_offset(mt5_module=mt5_module, symbols=symbols)
    return datetime.fromtimestamp(float(server_epoch) - offset, tz=timezone.utc)


def server_epoch_to_utc_str(server_epoch: float, mt5_module=None, symbols: Iterable[str] = ()) -> str:
    """Same as `server_epoch_to_utc`, formatted the way MT5 callers expect."""
    return server_epoch_to_utc(server_epoch, mt5_module=mt5_module, symbols=symbols).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def reset_cache() -> None:
    """Drop the cached offset. For tests and for a broker/server change."""
    _cache["offset"] = None
    _cache["at"] = 0.0
    _cache["warned"] = False

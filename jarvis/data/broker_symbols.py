"""
Broker symbol resolution.

WHY THIS MODULE EXISTS
----------------------
The project works in canonical names (XAUUSD, NAS100, GER40, BTCUSD...), but
brokers label the same instruments differently. On XMGlobal-MT5 for example:

    XAUUSD  -> GOLD.i#   (there is no "XAUUSD" at all)
    NAS100  -> US100Cash#
    GER40   -> GER40Cash#
    BTCUSD  -> BTCUSD#

The canonical name simply is not in the Market Watch, so
``copy_rates_from_pos("XAUUSD", ...)`` returns None with
``(-1, 'Terminal: Call failed')`` - which reads like a terminal fault rather
than the naming mismatch it actually is. That is the bug that made a live data
pull (and therefore the backtest on fresh data) fail.

Resolution order:
  1. the canonical name, if the broker has it
  2. each configured alias in order
  3. a fuzzy scan of the broker's symbol list, cached for the session

Call :func:`resolve_broker_symbol` instead of passing a canonical name straight
to ``mt5.copy_rates_*``.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, List, Optional

logger = logging.getLogger("JARVIS_BrokerSymbols")

__all__ = [
    "BROKER_ALIASES",
    "resolve_broker_symbol",
    "probe_symbol",
    "ensure_mt5_terminal",
    "terminal_ready",
    "reset_cache",
]

# Canonical -> ordered list of broker aliases for this account.
# Add a symbol here when a new broker uses a different label; no other code
# needs to change.
BROKER_ALIASES: Dict[str, List[str]] = {
    "XAUUSD": ["GOLD.i#", "GOLD24-7.i#", "XAUUSD#", "XAUUSD"],
    "XAGUSD": ["SILVER.i#", "XAGUSD#", "XAGUSD"],
    "NAS100": ["US100Cash#", "US100-SEP26", "NAS100Cash#", "US100", "NAS100"],
    "US30":   ["US30Cash#", "US30-SEP26", "US30"],
    "GER40":  ["GER40Cash#", "GER40-SEP26", "DE40Cash#", "GER40"],
    "UK100":  ["UK100Cash#", "UK100-SEP26", "UK100"],
    "US500":  ["US500Cash#", "SPXUSD", "US500"],
    "BTCUSD": ["BTCUSD#", "BTCUSD"],
    "ETHUSD": ["ETHUSD#", "ETHUSD"],
    "SOLUSD": ["SOLUSD#", "SOLUSD"],
}

# cache: canonical -> broker name confirmed to exist
_CACHE: Dict[str, str] = {}
# cache: canonical -> False when nothing resolved (avoid rescanning)
_FAILED: Dict[str, bool] = {}


def _mt5():
    import MetaTrader5 as mt5  # imported lazily so backtests without MT5 still work
    return mt5


# ── Terminal initialization ────────────────────────────────────────────────
# Market data needs an initialized terminal, and EXECUTION MODE MUST NOT GATE
# THAT. `MT5Client.init_connection()` returns early for paper mode (it simulates
# fills, so it needs no broker link) -- and that used to starve the *data* path
# too, because nothing else ever called `mt5.initialize()`:
#
#   paper mode -> resolve_broker_symbol() -> None  (every probe fails)
#              -> copy_rates_from_pos("XAUUSD")    (canonical name, not the
#                                                   broker's "GOLD.i#")
#              -> 0 rows -> SYNTHETIC_FALLBACK
#
# The platform then read as "broker offline / no data" with a live terminal
# sitting right there. Reading bars and placing orders are separate
# capabilities: establish the first regardless of the second.
_TERMINAL_READY = False
_LAST_INIT_ATTEMPT = 0.0
_INIT_RETRY_SEC = 30.0
_init_lock = threading.Lock()


def ensure_mt5_terminal() -> bool:
    """Initialise the MT5 terminal once per process so bars are readable.

    Independent of execution mode on purpose. Cached on success; a failure is
    retried at most once every :data:`_INIT_RETRY_SEC` so a 1 Hz poll loop
    cannot hammer ``initialize()`` (which attaches to the terminal and is not
    cheap). Returns True only when the terminal is genuinely up.
    """
    global _TERMINAL_READY, _LAST_INIT_ATTEMPT

    if _TERMINAL_READY:
        return True
    if os.environ.get("JARVIS_BACKTEST_MODE") == "1":
        # Backtests replay stored bars; never drag a terminal into that path.
        return False

    with _init_lock:
        if _TERMINAL_READY:
            return True
        now = time.time()
        if now - _LAST_INIT_ATTEMPT < _INIT_RETRY_SEC:
            return False
        _LAST_INIT_ATTEMPT = now
        try:
            mt5 = _mt5()
        except Exception:
            return False
        try:
            if not mt5.initialize():
                logger.warning("MT5 initialize() failed for market data: %s", mt5.last_error())
                return False
            if mt5.terminal_info() is None:
                return False
            _TERMINAL_READY = True
            logger.info("MT5 terminal initialized for market data.")
            return True
        except Exception as exc:
            logger.warning("MT5 initialize() raised for market data: %s", exc)
            return False


def terminal_ready() -> bool:
    """True when the terminal has been initialized by this process.

    Read-only: does not attempt an initialization, so a health check cannot
    block on attaching to the terminal.

    Note this latches on success and never re-checks: if the terminal dies
    later this still reports True. That is deliberate — every consumer uses it
    to decide whether an EMPTY frame means "broker does not offer this symbol",
    and staying True keeps that check firing (fail closed) rather than
    suspending it. It is not a liveness probe.
    """
    return _TERMINAL_READY


def reset_cache() -> None:
    """Forget every cached resolution and the terminal state.

    Mirrors `broker_time.reset_cache()`. Needed after a terminal restart or a
    server/account change, since both caches are otherwise process-lifetime:
    a symbol resolved against one broker stays resolved after switching to
    another, and `_TERMINAL_READY` would suppress re-initialization.
    """
    global _TERMINAL_READY, _LAST_INIT_ATTEMPT
    _CACHE.clear()
    _FAILED.clear()
    _TERMINAL_READY = False
    _LAST_INIT_ATTEMPT = 0.0


def probe_symbol(name: str) -> bool:
    """True if ``name`` exists at the broker and returns at least one H1 bar."""
    try:
        mt5 = _mt5()
        if mt5.symbol_info(name) is None:
            return False
        mt5.symbol_select(name, True)
        rates = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_H1, 0, 5)
        return rates is not None and len(rates) > 0
    except Exception:
        return False


def resolve_broker_symbol(symbol: str, verbose: bool = False) -> Optional[str]:
    """Return the broker's name for a canonical symbol, or None if unresolvable.

    None means "this instrument is not available from this broker" - callers
    should skip the symbol, not crash and not silently trade the wrong series.
    """
    sym = str(symbol or "").upper()
    if not sym:
        return None
    if sym in _CACHE:
        return _CACHE[sym]
    if _FAILED.get(sym):
        return None

    # An uninitialized terminal makes every probe fail, and a failed probe is
    # indistinguishable from "this broker has no such symbol". Ask for the
    # terminal FIRST, and when it is not up report UNKNOWN *without* writing the
    # failure cache -- otherwise one call before the terminal is ready pins every
    # symbol to None for the life of the process, long after the terminal came up.
    if not ensure_mt5_terminal():
        return None

    candidates = [sym] + list(BROKER_ALIASES.get(sym, []))
    for cand in candidates:
        if probe_symbol(cand):
            _CACHE[sym] = cand
            if verbose and cand != sym:
                print(f"  [broker-symbol] {sym} -> {cand}")
            return cand

    # Nothing configured worked: try a fuzzy scan of the broker's own list.
    # PREFIX ONLY, and logged loudly. A bare substring scan resolved COPPER to
    # `SouthernCopper` -- Southern Copper Corp, an EQUITY -- and handed it back
    # as the copper commodity. Nothing downstream could tell: the frame was
    # stamped LIVE_MT5 and the freshness gate certified it FRESH, so the
    # platform would have analysed (and could have traded) the wrong instrument
    # with a clean bill of health. A wrong instrument that looks healthy is far
    # worse than an unresolved symbol, which callers already handle.
    try:
        mt5 = _mt5()
        for info in (mt5.symbols_get() or []):
            n = str(info.name)
            if len(sym) >= 3 and n.upper().startswith(sym) and probe_symbol(n):
                _CACHE[sym] = n
                logger.warning(
                    "Fuzzy broker-symbol match %s -> %s (not in BROKER_ALIASES). "
                    "Confirm this is the intended instrument; if it is, add it to "
                    "BROKER_ALIASES so the match is explicit.",
                    sym, n,
                )
                if verbose:
                    print(f"  [broker-symbol] {sym} -> {n} (discovered)")
                return n
    except Exception:
        pass

    _FAILED[sym] = True
    if verbose:
        print(f"  [broker-symbol] {sym} -> NOT AVAILABLE at this broker")
    return None

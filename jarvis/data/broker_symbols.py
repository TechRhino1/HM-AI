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

from typing import Dict, List, Optional

__all__ = ["BROKER_ALIASES", "resolve_broker_symbol", "probe_symbol"]

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

    candidates = [sym] + list(BROKER_ALIASES.get(sym, []))
    for cand in candidates:
        if probe_symbol(cand):
            _CACHE[sym] = cand
            if verbose and cand != sym:
                print(f"  [broker-symbol] {sym} -> {cand}")
            return cand

    # Nothing configured worked: try a fuzzy scan of the broker's own list.
    try:
        mt5 = _mt5()
        for info in (mt5.symbols_get() or []):
            n = str(info.name)
            if sym in n.upper() and probe_symbol(n):
                _CACHE[sym] = n
                if verbose:
                    print(f"  [broker-symbol] {sym} -> {n} (discovered)")
                return n
    except Exception:
        pass

    _FAILED[sym] = True
    if verbose:
        print(f"  [broker-symbol] {sym} -> NOT AVAILABLE at this broker")
    return None

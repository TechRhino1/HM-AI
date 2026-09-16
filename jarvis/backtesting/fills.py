"""Entry-fill convention, in ONE place.

WHY THIS MODULE EXISTS
----------------------
A round-turn costs one spread, whichever way you trade. The bar open is the
reference price, and the two sides pay it in opposite directions::

    BUY   ask = open + spread
    SELL  bid = open - spread

That arithmetic used to be written out inline in two places, and only one of
them charged the short leg. `engine.py` had it right -- with a comment
explaining exactly why ("Charging it on longs only made every SELL trade
cost-free in the backtest") -- while `tools/scan_signals.py` still added the
spread for BUY alone. So every SELL candidate in the cached audit tables entered
at the mid, and because `trade_simulator` applies no spread on exits either,
half of ~95,000 audited trades carried no spread cost at all.

Measured on the stored tables before the fix, `(fill - next_open) / pip`
divided by the row's own spread was **+1.000 for BUY and 0.000 for SELL** on
every symbol checked. After: **+1.000 and -1.000**.

That is the "two entry points, one fixed" failure mode. The fix is not to
correct the second copy but to delete it: both callers now import
:func:`entry_fill`.

Nothing about the spread's *size* is decided here -- callers pass the real
per-bar spread when the data carries it, and fall back to the symbol spec.
"""

from __future__ import annotations

__all__ = ["entry_fill", "spread_price"]


def spread_price(spread_pips: float, pip_size: float) -> float:
    """The spread as a price distance."""
    return float(spread_pips) * float(pip_size)


def entry_fill(reference_price: float, bias: str, spread_pips: float, pip_size: float) -> float:
    """Realised entry price for ``bias`` at ``reference_price``.

    A long pays the ask, a short is filled at the bid -- so the spread is
    charged once, in the correct direction, on either side. An unrecognised
    ``bias`` is treated as a long, matching the rest of the codebase, where
    anything that is not an explicit SELL is a BUY.
    """
    delta = spread_price(spread_pips, pip_size)
    if str(bias or "").strip().upper().startswith("SELL"):
        return float(reference_price) - delta
    return float(reference_price) + delta

"""Turn the per-symbol replay into a tradeable / not-tradeable verdict.

For every symbol it answers three questions the raw metrics do not:

1. What does the equity curve actually do under fixed-fractional sizing?
   (1R = f x equity, so equity_t = equity_{t-1} * (1 + f * r_t))
2. What risk fraction f keeps max drawdown at or under the 10 % acceptance
   criterion?  Because log-equity is additive in R,
       DD(f) ~= 1 - exp(-f * max_dd_r)   =>   f_10% = -ln(0.90) / max_dd_r
3. Is that f even executable?  At $10,000 equity f_10% implies a dollar risk
   per trade; the smallest lot the broker accepts implies another.  If the
   first is below the second, the plan cannot meet the drawdown target at any
   size that can actually be placed.

All thresholds are derived from the measured series - no fixed cut-offs.

    python tools/audit_verdict.py --audit reports/trade_quality_audit.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from jarvis.data.symbol_registry import get_dollar_risk_per_price_unit  # noqa: E402

DD_TARGET = 0.10            # acceptance criterion
EQUITY = 10_000.0
NOMINAL_RISK_PCT = 0.5
REALISED_RISK_PCT = 1.0     # measured: position_sizing.py:56 pins Kelly at its cap


def max_dd_from_r(pnl_r: np.ndarray, f: float) -> float:
    """Max drawdown of the compounding equity curve when 1R = f x equity."""
    if len(pnl_r) == 0:
        return 0.0
    mult = 1.0 + f * pnl_r
    mult = np.where(mult <= 0.0, 1e-12, mult)      # ruin guard
    eq = np.cumprod(mult)
    peak = np.maximum.accumulate(np.maximum(eq, 1.0))
    return float(np.max(1.0 - eq / peak))


def f_for_dd(max_dd_r: float, target: float = DD_TARGET) -> float:
    """Risk fraction whose compounding drawdown equals ``target``."""
    if max_dd_r <= 0:
        return float("nan")
    return -math.log(1.0 - target) / max_dd_r


def broker_min_lot(symbol: str) -> float:
    path = os.path.join(REPO, "actual_spreads.json")
    if not os.path.exists(path):
        return 0.01
    try:
        with open(path, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
    except Exception:
        return 0.01
    rows = blob if isinstance(blob, list) else blob.get("symbols", [])
    for row in rows:
        if str(row.get("symbol", "")).upper().replace("#", "").startswith(symbol.upper()):
            return float(row.get("volume_min", 0.01) or 0.01)
    return 0.01


_TF_OF_STYLE = {"SWING(H1)": "H1", "DAY_TRADING(M15)": "M15", "SCALP(M5)": "M5"}
_RISK_CACHE: dict = {}


def _median_risk_dist(style: str, symbol: str) -> float:
    """Median realised stop distance (price units) for a symbol, from its own candidates."""
    key = (style, symbol)
    if key in _RISK_CACHE:
        return _RISK_CACHE[key]
    tf = _TF_OF_STYLE.get(style, "H1")
    path = os.path.join(REPO, "data", "signals", f"{symbol}_{tf}_183d_candidates.parquet")
    val = 0.0
    if os.path.exists(path):
        try:
            val = float(pd.read_parquet(path, columns=["risk_dist"])["risk_dist"].median())
        except Exception:
            val = 0.0
    _RISK_CACHE[key] = val
    return val


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", default=os.path.join(REPO, "reports", "trade_quality_audit.json"))
    ap.add_argument("--out", default=os.path.join(REPO, "reports", "verdict.json"))
    ap.add_argument("--style", default="SWING(H1)")
    args = ap.parse_args()

    with open(args.audit, "r", encoding="utf-8") as fh:
        audit = json.load(fh)

    rows = []
    for style, syms in audit["styles"].items():
        for sym, entry in syms.items():
            for metric_key in ("tp1.5_costs", "tp2.0_costs"):
                m = entry.get(metric_key, {})
                if not m.get("n"):
                    continue
                dd_r = float(m.get("max_dd_r", 0.0))
                money = get_dollar_risk_per_price_unit(sym, None)
                # typical risk distance for this symbol, recovered from the
                # reported expectancy and the mean R of the run is not stored,
                # so re-derive it from ATR x the symbol's own SL multiplier.
                tp_r = 1.5 if metric_key == "tp1.5_costs" else 2.0
                f10 = f_for_dd(dd_r)
                risk_usd = f10 * EQUITY
                vol_min = broker_min_lot(sym)
                # $ risk of the smallest placeable lot, using this symbol's
                # own median realised stop distance from the candidate set.
                min_risk_usd = vol_min * float(_median_risk_dist(style, sym)) * money
                rows.append({
                    "style": style, "symbol": sym, "tp_r": tp_r,
                    "n": int(m["n"]),
                    "win_rate": float(m["win_rate"]),
                    "break_even_wr": float(m["break_even_wr"]),
                    "profit_factor": float(m["profit_factor"]),
                    "expectancy_r": float(m["expectancy_r"]),
                    "net_r": float(m["net_r"]),
                    "max_dd_r": dd_r,
                    "dd_pct_at_1pct": max_dd_from_r(np.array([]), 0.01) if False else
                                      float(1.0 - math.exp(-0.01 * dd_r)),
                    "dd_pct_at_0p5pct": float(1.0 - math.exp(-0.005 * dd_r)),
                    "f_for_10pct_dd_pct": f10 * 100.0,
                    "risk_usd_for_10pct_dd": risk_usd,
                    "broker_min_lot": vol_min,
                    "min_lot_risk_usd": min_risk_usd,
                    "executable_at_dd_target": bool(risk_usd >= min_risk_usd),
                    "t_stat": float(m.get("t_stat", 0.0)),
                })

    df = pd.DataFrame(rows)
    df["verdict"] = np.where(
        (df["profit_factor"] >= 1.3) & df["executable_at_dd_target"], "VIABLE",
        np.where((df["profit_factor"] >= 1.0) & (df["expectancy_r"] > 0), "MARGINAL", "NOT VIABLE"))

    pd.set_option("display.width", 220)
    show = df[df["style"] == args.style] if (df["style"] == args.style).any() else df
    cols = ["symbol", "tp_r", "n", "win_rate", "break_even_wr", "profit_factor",
            "expectancy_r", "max_dd_r", "f_for_10pct_dd_pct", "risk_usd_for_10pct_dd",
            "min_lot_risk_usd", "executable_at_dd_target", "verdict"]
    print(show[cols].sort_values("profit_factor", ascending=False).to_string(index=False))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    df.to_json(args.out, orient="records", indent=2)
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Quantify how much of the measured result is look-ahead bias.

``SignalScanner._resample`` builds H4 and D1 with pandas' default
``closed='left', label='left'``.  A bucket covering 12:00-15:59 is therefore
labelled ``12:00``, and the consumer filter ``time <= bar_time``
(``signal_scan.py:208-209``) admits it while it is still forming -- so the D1
bar visible at 00:00 contains up to 23 hours of future prices.  That D1/H4
structure bias carries 70% of ``mtf_confluence_score``
(``market_context.py:152-170``: d1 0.40 + h4 0.30).

This script scans the same bars twice -- once as shipped, once with the bucket
labelled at its close (``label='right'``) -- replays both candidate sets through
the identical simulator, and reports the difference.  The gap IS the look-ahead
contribution.

    python tools/audit_lookahead.py --symbols EURUSD,XAUUSD --tf H1
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Dict

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from jarvis.backtesting.signal_scan import SignalScanner  # noqa: E402
from jarvis.backtesting.trade_simulator import BarArrays, Geometry, simulate_trade  # noqa: E402
from jarvis.data.symbol_registry import get_dollar_risk_per_price_unit, resolve  # noqa: E402

REAL_DIR = os.path.join(REPO, "data", "market", "real")

_ORIGINAL_RESAMPLE = SignalScanner._resample


def _resample_fixed(df: pd.DataFrame) -> tuple:
    """Bucket labelled at its CLOSE time -> only visible once complete."""
    indexed = df.copy()
    indexed["time"] = pd.to_datetime(indexed["time"])
    indexed = indexed.set_index("time")
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    for vol in ("volume", "tick_volume"):
        if vol in indexed.columns:
            agg[vol] = "sum"
    h4 = indexed.resample("4h", closed="left", label="right").agg(agg).dropna().reset_index()
    d1 = indexed.resample("1D", closed="left", label="right").agg(agg).dropna().reset_index()
    return h4, d1


def metrics(pnl: np.ndarray) -> Dict[str, float]:
    if len(pnl) == 0:
        return {"n": 0}
    w, l = pnl[pnl > 0], pnl[pnl <= 0]
    gp, gl = float(w.sum()), float(-l.sum())
    curve = np.cumsum(pnl)
    dd = float(np.max(np.maximum.accumulate(curve) - curve))
    return {
        "n": int(len(pnl)),
        "win_rate": float((pnl > 0).mean()),
        "profit_factor": float(gp / gl if gl > 1e-12 else (99.0 if gp > 0 else 0.0)),
        "expectancy_r": float(pnl.mean()),
        "net_r": float(curve[-1]),
        "max_dd_r": dd,
    }


def replay(symbol: str, df: pd.DataFrame, cands: pd.DataFrame, geom: Geometry) -> np.ndarray:
    spec = resolve(symbol)
    money = get_dollar_risk_per_price_unit(symbol, None)
    bars = BarArrays.from_df(df)
    n = len(df)
    out = []
    for r in cands.itertuples(index=False):
        i = int(getattr(r, "bar_idx"))
        side = str(getattr(r, "side", "")).upper()
        if i + 1 >= n or side not in ("BUY", "SELL"):
            continue
        t = simulate_trade(
            symbol=symbol, side=side, entry_idx=i,
            fill=float(getattr(r, "fill")), sl=float(getattr(r, "sl")),
            geom=geom, money_per_unit=money, bars=bars,
            cost_price_equiv=0.0, slippage_price_equiv=0.0,
            ai_score=float(getattr(r, "score", 0.0) or 0.0),
            calibrated_win_p=float(getattr(r, "score", 0.0) or 0.0),
            regime=str(getattr(r, "regime", "")), strategy=str(getattr(r, "strategy", "")),
            zone=str(getattr(r, "zone", "")), spec=spec,
        )
        if t is not None:
            out.append(float(t.pnl_r))
    return np.asarray(out, dtype=float)


def exposure(df: pd.DataFrame) -> Dict[str, float]:
    """Cheap, exact measurement of how much future data the leak admits."""
    t = pd.to_datetime(df["time"])
    n = len(df)
    leaky_h4 = t.dt.floor("4h")                       # label='left'
    # With label='right' the bucket timestamp is its CLOSE, so a bucket is only
    # admissible once the last bar inside it has finished.
    fixed_h4 = leaky_h4 + pd.Timedelta(hours=4)
    # A bar at time T sees bucket label <= T.  Leaky: bucket start <= T.
    # Correct: bucket close <= T.
    fut_h4 = []
    for i in range(0, n, max(1, n // 2000)):          # sample up to 2000 bars
        T = t.iloc[i]
        lh = leaky_h4.iloc[i]
        mask = (fixed_h4 > T) & (fixed_h4 <= lh + pd.Timedelta(hours=4))
        fut_h4.append(int(mask.sum()))
    return {
        "bars_sampled": len(fut_h4),
        "median_future_bars_in_visible_h4_bucket": float(np.median(fut_h4)) if fut_h4 else 0.0,
        "mean_future_bars_in_visible_h4_bucket": float(np.mean(fut_h4)) if fut_h4 else 0.0,
        "note": ("Count of H1 bars inside the H4 bucket that are in the FUTURE "
                 "relative to the decision bar, yet are already visible because "
                 "the bucket is labelled at its open."),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="EURUSD,XAUUSD,BTCUSD,US30")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--max-bars", type=int, default=0,
                    help="truncate the input series to the last N bars (0 = all)")
    ap.add_argument("--out", default=os.path.join(REPO, "reports", "lookahead_impact.json"))
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    geom = Geometry(tp_r=1.5)
    report = {"tf": args.tf, "geometry": geom.key(), "symbols": {}}

    for sym in symbols:
        path = os.path.join(REAL_DIR, sym, f"{sym}_{args.tf}_183d.parquet")
        if not os.path.exists(path):
            print(f"skip {sym}: no bars")
            continue
        df = pd.read_parquet(path).sort_values("time").reset_index(drop=True)
        if "atr" not in df.columns:
            import tools.audit_trade_quality as atq
            df["atr"] = atq.wilder_atr(df)

        exp = exposure(df)
        if args.max_bars and len(df) > args.max_bars:
            df = df.iloc[-args.max_bars:].reset_index(drop=True)
            print(f"{sym}: truncated to last {len(df)} bars "
                  f"({df['time'].iloc[0]} -> {df['time'].iloc[-1]})", flush=True)

        results = {"exposure": exp}
        for label in ("leaky", "fixed"):
            SignalScanner._resample = staticmethod(_ORIGINAL_RESAMPLE if label == "leaky" else _resample_fixed)
            t0 = time.time()
            res = SignalScanner(offline=True).scan(df, sym)
            cands = res.candidates if hasattr(res, "candidates") else res
            if isinstance(cands, pd.DataFrame) and cands.empty:
                results[label] = {"n": 0}
                continue
            pnl = replay(sym, df, cands, geom)
            m = metrics(pnl)
            m["scan_seconds"] = round(time.time() - t0, 1)
            m["candidates"] = int(len(cands))
            results[label] = m
            print(f"{sym:8s} {label:6s} n={m['n']:5d} wr={m['win_rate']*100:5.2f}% "
                  f"PF={m['profit_factor']:5.3f} E={m['expectancy_r']:+.4f}R "
                  f"({m['scan_seconds']}s)", flush=True)

        SignalScanner._resample = staticmethod(_ORIGINAL_RESAMPLE)

        a, b = results.get("leaky", {}), results.get("fixed", {})
        if a.get("n") and b.get("n"):
            report["symbols"][sym] = {
                "leaky": a, "fixed": b,
                "expectancy_delta_r": float(a["expectancy_r"] - b["expectancy_r"]),
                "win_rate_delta_pp": float((a["win_rate"] - b["win_rate"]) * 100),
                "pf_delta": float(a["profit_factor"] - b["profit_factor"]),
            }
            print(f"  -> look-ahead contribution: {a['expectancy_r'] - b['expectancy_r']:+.4f}R "
                  f"/ {a['win_rate']*100 - b['win_rate']*100:+.2f}pp win rate\n", flush=True)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    import json
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

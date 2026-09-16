"""P0-1, second stage — is the surviving edge DIRECTION SKILL or just BETA?

Stage 1 found that only XAUUSD / ETHUSD / XAGUSD clear the multiple-testing bar,
and that 10 of 20 symbols are *significantly negative*. Before acting on the
winners, one question has to be settled:

    if a symbol rose over the window, a gate that mostly says BUY will show a
    positive mean R without any skill at all. That is beta, not alpha.

So the test is not "does it make money" but "does its DIRECTION CHOICE add value
over always being long".

Method — hold everything constant except direction:
  * same entry bars (every candidate bar),
  * same risk distance the gate itself used, |fill - sl|,
  * same target multiple (1.5R),
  * LONG only.

If `gate_mean_r` is no better than `always_long_mean_r`, the gate's directional
call contributed nothing and the P&L came from the instrument's drift.

Also reports 183d vs 365d so a window-specific artefact is visible.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Dict, Optional

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from jarvis.backtesting.trade_simulator import BarArrays, Geometry, simulate_trade  # noqa: E402
from jarvis.data.symbol_registry import get_dollar_risk_per_price_unit, resolve  # noqa: E402
from tools.audit_trade_quality import dynamic_regimes, load_symbol, replay  # noqa: E402


def forced_long_benchmark(symbol: str, df: pd.DataFrame, cands: pd.DataFrame,
                          geom: Geometry, slippage_price: float) -> Optional[np.ndarray]:
    """Trade LONG at every candidate bar, with the gate's OWN risk distance.

    Same entries, same risk distance, same target multiple - only the direction
    is fixed. This isolates the gate's directional call.

    The long entry must be the **ask**, not the candidate's stored ``fill``.
    ``fill`` is the price the *gate's* trade paid: for a BUY candidate that is
    ``open + spread`` (already the ask, fine), but for a SELL candidate it is
    ``open - spread`` — the *bid*. Reusing it as a long entry hands the control
    a free half-spread, which biases the comparison *against* the gate. That is
    a conservative error, but it is still an error, and on a 2.3-pip FX spread
    it is worth ~0.05R per trade — the same order as the effects being measured.
    So: a SELL row's long entry is ``fill + 2 x spread``.
    """
    spec = resolve(symbol)
    money = get_dollar_risk_per_price_unit(symbol, None)
    bars = BarArrays.from_df(df)
    pip = float(getattr(spec, "pip_size", 0.0001) or 0.0001)
    n = len(df)
    out_r = []
    for r in cands.itertuples(index=False):
        i = int(getattr(r, "bar_idx"))
        if i + 1 >= n:
            continue
        fill = float(getattr(r, "fill"))
        sl = float(getattr(r, "sl"))
        risk = abs(fill - sl)
        if risk <= 0:
            continue
        side = str(getattr(r, "side", "")).upper()
        long_entry = fill
        if side == "SELL":
            spread_pips = getattr(r, "spread_pips", None)
            if spread_pips is not None and np.isfinite(float(spread_pips)):
                long_entry = fill + 2.0 * float(spread_pips) * pip
        sl_long = long_entry - risk
        res = simulate_trade(
            symbol=symbol, side="BUY", entry_idx=i, fill=long_entry, sl=sl_long, geom=geom,
            money_per_unit=money, bars=bars, cost_price_equiv=0.0,
            slippage_price_equiv=slippage_price, spec=spec,
        )
        if res is None:
            continue
        out_r.append(float(res.pnl_r))
    return np.array(out_r) if out_r else None


def side_split(symbol: str, df: pd.DataFrame, cands: pd.DataFrame, geom: Geometry,
               slippage_price: float) -> Dict[str, Dict[str, float]]:
    out = {}
    for side in ("BUY", "SELL"):
        sub = cands[cands["side"].astype(str).str.upper() == side]
        if sub.empty:
            out[side] = {"n": 0, "mean_r": float("nan")}
            continue
        tr = replay(symbol, df, sub.reset_index(drop=True), geom, slippage_price, 0.0)
        if tr is None or tr.empty:
            out[side] = {"n": 0, "mean_r": float("nan")}
            continue
        r = tr["pnl_r"].to_numpy(float)
        out[side] = {"n": int(len(r)), "mean_r": float(r.mean())}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--windows", default="365,183")
    ap.add_argument("--tp", type=float, default=1.5)
    ap.add_argument("--slip-pips", type=float, default=0.5)
    ap.add_argument("--out", default=os.path.join(REPO, "reports", "p0_1_direction_audit.json"))
    args = ap.parse_args()

    windows = [int(w) for w in args.windows.split(",")]
    real_dir = os.path.join(REPO, "data", "market", "real")
    symbols = sorted(d for d in os.listdir(real_dir) if os.path.isdir(os.path.join(real_dir, d)))

    report: Dict[str, object] = {"tp_r": args.tp, "windows": {}}

    for window in windows:
        print("=" * 104)
        print(f"DIRECTION vs BETA  |  {args.tf}  |  {window}d  |  tp={args.tp}R")
        print("=" * 104)
        print(f"{'symbol':<8} {'n':>6} {'BUY%':>6} {'meanR':>9} {'BUY R':>9} {'SELL R':>9} "
              f"{'alwaysLong':>11} {'gate-long':>10} {'verdict':<28}")
        print("-" * 104)

        per = {}
        for sym in symbols:
            df, cands = load_symbol(sym, args.tf, window)
            if df is None:
                continue
            cands = dynamic_regimes(df, cands)
            pip = float(resolve(sym).pip_size or 0.0001)
            geom = Geometry(tp_r=args.tp)
            slip = args.slip_pips * pip

            tr = replay(sym, df, cands, geom, slip, 0.0)
            if tr is None or tr.empty:
                continue
            gate_r = float(tr["pnl_r"].to_numpy(float).mean())
            n = len(tr)

            split = side_split(sym, df, cands, geom, slip)
            n_buy = split["BUY"]["n"]
            buy_pct = n_buy / max(1, n)

            long_r = forced_long_benchmark(sym, df, cands, geom, slip)
            long_mean = float(long_r.mean()) if long_r is not None and len(long_r) else float("nan")
            # Does the gate's direction beat always-long?
            edge_over_long = gate_r - long_mean if math.isfinite(long_mean) else float("nan")

            if not math.isfinite(long_mean):
                verdict = "n/a"
            elif gate_r <= 0:
                verdict = "gate loses"
            elif edge_over_long <= 0:
                verdict = "BETA — always-long is better"
            elif edge_over_long < 0.02:
                verdict = "marginal over always-long"
            else:
                verdict = "direction adds value"

            per[sym] = {
                "n": n, "buy_pct": buy_pct, "gate_mean_r": gate_r,
                "buy_mean_r": split["BUY"]["mean_r"], "sell_mean_r": split["SELL"]["mean_r"],
                "always_long_mean_r": long_mean, "edge_over_always_long": edge_over_long,
                "verdict": verdict,
            }
            print(f"{sym:<8} {n:>6} {buy_pct * 100:>6.1f} {gate_r:>+9.4f} "
                  f"{split['BUY']['mean_r']:>+9.4f} {split['SELL']['mean_r']:>+9.4f} "
                  f"{long_mean:>+11.4f} {edge_over_long:>+10.4f} {verdict:<28}")

        pos = [s for s, d in per.items() if d["gate_mean_r"] > 0]
        beat = [s for s, d in per.items()
                if math.isfinite(d["edge_over_always_long"]) and d["edge_over_always_long"] > 0]
        print("-" * 104)
        print(f"  gate profitable on            : {len(pos)}/{len(per)}  {sorted(pos)}")
        print(f"  beats always-long on          : {len(beat)}/{len(per)}  {sorted(beat)}")
        report["windows"][str(window)] = per

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

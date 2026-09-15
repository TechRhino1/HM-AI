#!/usr/bin/env python
"""Fit and validate a score -> win-probability calibration on honest data (P0-1).

The calibrations already on disk (``config/winrate_profiles.json``) cannot be
used: they were fitted on the cost-free backtest with n = 41-126 spread over
8 bins, evaluated in-sample, and their Brier is indistinguishable from simply
guessing the base rate.

This refits on the 183d real-bar candidate set with a purged + embargoed
chronological split, and asks whether the score carries ANY out-of-sample
signal *before* anything is wired into the live gate. If it does not, the
honest output is a constant base rate — and this tool says so rather than
shipping a noise-fit map.
"""
import argparse
import json
import os
import sys
from typing import Dict, Optional

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from jarvis.backtesting.trade_simulator import (  # noqa: E402
    BarArrays, Geometry, simulate_trade,
)
from jarvis.data.symbol_registry import (  # noqa: E402
    get_dollar_risk_per_price_unit, resolve,
)
from jarvis.intelligence.winrate_targeting import (  # noqa: E402
    isotonic_calibrate,
)

REAL_DIR = os.path.join(REPO, "data", "market", "real")
SIGNAL_DIR = os.path.join(REPO, "data", "signals")


def wilder_atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    prev = np.roll(close, 1)
    prev[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    return pd.Series(tr).ewm(alpha=1.0 / period, adjust=False, min_periods=1).mean().to_numpy()


def load_symbol(symbol: str, tf: str, window: int = 183):
    bars_path = os.path.join(REAL_DIR, symbol, f"{symbol}_{tf}_{window}d.parquet")
    cand_path = os.path.join(SIGNAL_DIR, f"{symbol}_{tf}_{window}d_candidates.parquet")
    if not (os.path.exists(bars_path) and os.path.exists(cand_path)):
        return None, None
    df = pd.read_parquet(bars_path).sort_values("time").reset_index(drop=True)
    if "atr" not in df.columns:
        df["atr"] = wilder_atr(df)
    cands = pd.read_parquet(cand_path).sort_values("bar_idx").reset_index(drop=True)
    return df, cands


def replay(symbol: str, df: pd.DataFrame, cands: pd.DataFrame, geom: Geometry,
           slippage_price: float, comm_price: float) -> Optional[pd.DataFrame]:
    spec = resolve(symbol)
    money = get_dollar_risk_per_price_unit(symbol, None)
    bars = BarArrays.from_df(df)
    n = len(df)
    rows = []
    for r in cands.itertuples(index=False):
        i = int(getattr(r, "bar_idx"))
        if i + 1 >= n:
            continue
        side = str(getattr(r, "side", "")).upper()
        if side not in ("BUY", "SELL"):
            continue
        score = float(getattr(r, "score", 0.0) or 0.0)
        out = simulate_trade(
            symbol=symbol, side=side, entry_idx=i,
            fill=float(getattr(r, "fill")), sl=float(getattr(r, "sl")),
            geom=geom, money_per_unit=money, bars=bars,
            cost_price_equiv=comm_price, slippage_price_equiv=slippage_price,
            ai_score=score, calibrated_win_p=score,
            regime=str(getattr(r, "regime", "UNKNOWN")),
            strategy=str(getattr(r, "strategy", "UNKNOWN")),
            zone=str(getattr(r, "zone", "UNKNOWN")), spec=spec,
        )
        if out is None:
            continue
        rows.append({"bar_idx": i, "pnl_r": float(out.pnl_r), "score": score})
    return pd.DataFrame(rows) if rows else None


def auc(scores: np.ndarray, wins: np.ndarray) -> float:
    """Rank-based AUC (Mann-Whitney U), tie-aware."""
    s = np.asarray(scores, float)
    w = np.asarray(wins, float)
    n1 = float(w.sum())
    n0 = float(len(w) - n1)
    if n1 == 0 or n0 == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), float)
    ss = s[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and ss[j + 1] == ss[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return float((ranks[w == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def brier(pred, wins) -> float:
    return float(np.mean((np.asarray(pred, float) - np.asarray(wins, float)) ** 2))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--window", type=int, default=183, help="data window in days")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--train-frac", type=float, default=0.60)
    ap.add_argument("--embargo-bars", type=int, default=250,
                    help="bars dropped either side of the split so no trade "
                         "straddles the boundary")
    ap.add_argument("--bins", type=int, default=10)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    tf = args.tf.upper()
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = sorted(d for d in os.listdir(REAL_DIR) if os.path.isdir(os.path.join(REAL_DIR, d)))

    args.out = args.out or os.path.join(REPO, "config", f"score_calibration_honest_{args.window}d.json")

    results: Dict[str, object] = {}
    deploy: Dict[str, object] = {}

    print(f"{'sym':8s} {'n_tr':>6s} {'n_te':>6s} {'AUC':>6s} "
          f"{'Br_cal':>8s} {'Br_base':>8s} {'BSS':>7s}  verdict")

    for sym in symbols:
        df, cands = load_symbol(sym, tf, args.window)
        if df is None:
            continue
        spec = resolve(sym)
        pip = float(spec.pip_size or 0.0001)
        tr = replay(sym, df, cands, Geometry(tp_r=1.5), 0.5 * pip, 0.0)
        if tr is None or len(tr) < 200:
            continue

        tr = tr.sort_values("bar_idx").reset_index(drop=True)
        tr["win"] = (tr["pnl_r"] > 0).astype(int)

        split_bar = float(tr["bar_idx"].quantile(args.train_frac))
        train = tr[tr["bar_idx"] <= split_bar - args.embargo_bars]
        test = tr[tr["bar_idx"] >= split_bar + args.embargo_bars]
        if len(train) < 100 or len(test) < 100:
            continue

        cal = isotonic_calibrate(train["score"].to_numpy(float),
                                 train["win"].to_numpy(int), n_bins=args.bins)
        base = float(train["win"].mean())

        pred_cal = np.array([cal.predict(float(s)) for s in test["score"]])
        pred_base = np.full(len(test), base)
        y = test["win"].to_numpy(float)

        b_cal = brier(pred_cal, y)
        b_base = brier(pred_base, y)
        bss = 1.0 - (b_cal / b_base) if b_base > 0 else float("nan")
        a = auc(test["score"].to_numpy(float), y)

        # Skill requires BOTH: the score must rank outcomes better than chance
        # (AUC) and the map must beat guessing (BSS). Either alone is noise.
        skillful = bool(a > 0.55 and bss > 0.02)
        verdict = "SKILL" if skillful else "no signal"

        print(f"{sym:8s} {len(train):6d} {len(test):6d} {a:6.3f} "
              f"{b_cal:8.4f} {b_base:8.4f} {bss:+7.3f}  {verdict}")

        results[sym] = {
            "n_train": int(len(train)), "n_test": int(len(test)),
            "auc_test": round(a, 4), "brier_cal": round(b_cal, 5),
            "brier_base": round(b_base, 5), "bss": round(bss, 4),
            "base_rate_train": round(base, 4), "skillful": skillful,
        }
        # Deployment artefact always fitted on the full sample; the flag says
        # whether it is worth wiring in.
        full = isotonic_calibrate(tr["score"].to_numpy(float),
                                  tr["win"].to_numpy(int), n_bins=args.bins)
        deploy[sym] = {
            "bin_edges": full.bin_edges, "bin_win_rate": full.bin_win_rate,
            "bin_count": full.bin_count, "brier": full.brier, "n": full.n,
            "skillful": skillful,
            "constant_fallback": round(float(tr["win"].mean()), 4),
        }

    n_skill = sum(1 for v in results.values() if v["skillful"])
    payload = {
        "generated": pd.Timestamp.utcnow().isoformat(),
        "timeframe": tf, "window_days": args.window, "train_frac": args.train_frac,
        "embargo_bars": args.embargo_bars, "bins": args.bins,
        "cost_basis": "slippage 0.5 pip, commission 0, tp_r=1.5 (matches audit_trade_quality)",
        "n_symbols": len(results), "n_skillful": n_skill,
        "validation": results, "calibrations": deploy,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    print(f"\n{n_skill}/{len(results)} symbols show out-of-sample skill "
          f"(AUC > 0.55 AND BSS > 0.02)")
    print(f"written -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

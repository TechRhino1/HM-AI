"""Per-symbol / per-regime trade-quality audit.

Replays the stored candidate sets (``data/signals/*_candidates.parquet``) forward
through the official ``trade_simulator.simulate_trade`` and reports, for every
symbol and every market regime, the five numbers that decide whether a plan is
tradeable:

    net profit (R and $), win rate, profit factor, expectancy, max drawdown

Regime buckets are derived **dynamically per symbol** (terciles of the symbol's
own ATR% and of its own normalised trend strength), never from fixed cut-offs.

Usage
-----
    python tools/audit_trade_quality.py                     # all symbols, 3 styles
    python tools/audit_trade_quality.py --tf H1             # swing only
    python tools/audit_trade_quality.py --symbols EURUSD,XAUUSD
    python tools/audit_trade_quality.py --out reports/x.json

The script is read-only with respect to the trading code; it only writes its
report file.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from jarvis.backtesting.trade_simulator import (  # noqa: E402
    BarArrays,
    Geometry,
    simulate_trade,
)
from jarvis.data.symbol_registry import (  # noqa: E402
    get_dollar_risk_per_price_unit,
    resolve,
)

REAL_DIR = os.path.join(REPO, "data", "market", "real")
SIGNAL_DIR = os.path.join(REPO, "data", "signals")

STYLE_TF = {"SWING": "H1", "DAY_TRADING": "M15", "SCALP": "M5"}

# Risk fraction actually realised by the sizer.  Nominal is 0.5 % but the
# quarter-Kelly blend in position_sizing.py:56 pins at its 1.50 cap, so the
# measured realised risk is 0.74-1.32 % depending on confidence.
NOMINAL_RISK_PCT = 0.5
REALISED_RISK_PCT = 1.0


# ── data loading ────────────────────────────────────────────────────────────
def wilder_atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    high, low, close = df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float)
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


# ── dynamic regime labelling ────────────────────────────────────────────────
def dynamic_regimes(df: pd.DataFrame, cands: pd.DataFrame) -> pd.DataFrame:
    """Attach volatility and trend buckets derived from each symbol's own distribution.

    No fixed cut-offs: the buckets are the symbol's own terciles, so a "high
    volatility" bar for EURUSD and one for BTCUSD both mean "the top third of
    *that* symbol's recent ATR% range".
    """
    close = df["close"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    atr_pct = np.where(close > 0, atr / close, 0.0)

    # Normalised trend strength: EMA separation expressed in ATR units, so it
    # is dimensionless and comparable across symbols and price levels.
    ema_f = pd.Series(close).ewm(span=20, adjust=False).mean().to_numpy()
    ema_s = pd.Series(close).ewm(span=60, adjust=False).mean().to_numpy()
    trend = np.where(atr > 0, np.abs(ema_f - ema_s) / atr, 0.0)

    idx = cands["bar_idx"].to_numpy(int)
    idx = np.clip(idx, 0, len(df) - 1)
    c_atrp = atr_pct[idx]
    c_trend = trend[idx]

    out = cands.copy()
    out["atr_pct"] = c_atrp
    out["trend_norm"] = c_trend

    def tercile(v: np.ndarray, labels: List[str]) -> np.ndarray:
        # With fewer than three points a tercile says nothing, so fall back to
        # this bucket's OWN middle label. It used to return the literal "MID",
        # which is not a member of either domain — vol_bucket would then group
        # as "MID" beside LOW_VOL/MID_VOL/HIGH_VOL in the reports that group by
        # it, and trend_bucket would carry a value outside its own label set.
        if len(v) < 3:
            return np.array([labels[1]] * len(v))
        lo, hi = np.percentile(v, [33.3, 66.7])
        return np.where(v <= lo, labels[0], np.where(v >= hi, labels[2], labels[1]))

    out["vol_bucket"] = tercile(c_atrp, ["LOW_VOL", "MID_VOL", "HIGH_VOL"])
    out["trend_bucket"] = tercile(c_trend, ["RANGING", "TRANSITIONAL", "TRENDING"])
    return out


# ── metrics ────────────────────────────────────────────────────────────────
def metrics(pnl_r: np.ndarray, risk_pct: float) -> Dict[str, float]:
    if len(pnl_r) == 0:
        return {"n": 0}
    wins = pnl_r[pnl_r > 0]
    losses = pnl_r[pnl_r <= 0]
    gp = float(wins.sum())
    gl = float(-losses.sum())
    pf = (gp / gl) if gl > 1e-12 else (99.0 if gp > 0 else 0.0)
    mean = float(pnl_r.mean())
    sd = float(pnl_r.std(ddof=1)) if len(pnl_r) > 1 else 0.0
    tstat = mean / (sd / math.sqrt(len(pnl_r))) if sd > 1e-12 and len(pnl_r) > 1 else 0.0
    curve = np.cumsum(pnl_r)
    peak = np.maximum.accumulate(curve)
    dd = float(np.max(peak - curve)) if len(curve) else 0.0
    return {
        "n": int(len(pnl_r)),
        "win_rate": float((pnl_r > 0).mean()),
        "profit_factor": float(pf),
        "expectancy_r": mean,
        "expectancy_pct": mean * risk_pct,
        "net_r": float(curve[-1]),
        "max_dd_r": dd,
        "max_dd_pct": dd * risk_pct,
        "t_stat": float(tstat),
        "avg_win_r": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_r": float(losses.mean()) if len(losses) else 0.0,
    }


def breakeven_wr(tp_r: float) -> float:
    return 1.0 / (1.0 + tp_r)


# ── replay ─────────────────────────────────────────────────────────────────
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
        out = simulate_trade(
            symbol=symbol,
            side=side,
            entry_idx=i,
            fill=float(getattr(r, "fill")),
            sl=float(getattr(r, "sl")),
            geom=geom,
            money_per_unit=money,
            bars=bars,
            cost_price_equiv=comm_price,
            slippage_price_equiv=slippage_price,
            ai_score=float(getattr(r, "score", 0.0) or 0.0),
            calibrated_win_p=float(getattr(r, "score", 0.0) or 0.0),
            regime=str(getattr(r, "regime", "UNKNOWN")),
            strategy=str(getattr(r, "strategy", "UNKNOWN")),
            zone=str(getattr(r, "zone", "UNKNOWN")),
            spec=spec,
        )
        if out is None:
            continue
        rows.append({
            "bar_idx": i,
            "side": side,
            "pnl_r": float(out.pnl_r),
            "result": out.result,
            "regime": str(getattr(r, "regime", "UNKNOWN")),
            "vol_bucket": getattr(r, "vol_bucket", "MID_VOL"),
            "trend_bucket": getattr(r, "trend_bucket", "TRANSITIONAL"),
            "score": float(getattr(r, "score", 0.0) or 0.0),
            "atr_pct": float(getattr(r, "atr_pct", 0.0)),
            "trend_norm": float(getattr(r, "trend_norm", 0.0)),
        })
    if not rows:
        return None
    return pd.DataFrame(rows)


def group_report(tr: pd.DataFrame, key: str, risk_pct: float) -> Dict[str, Dict[str, float]]:
    rep = {}
    for k, g in tr.groupby(key):
        m = metrics(g["pnl_r"].to_numpy(float), risk_pct)
        m["share_of_trades"] = float(len(g) / len(tr))
        rep[str(k)] = m
    return rep


# ── main ───────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="H1,M15,M5")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--out", default=os.path.join(REPO, "reports", "trade_quality_audit.json"))
    ap.add_argument("--risk-pct", type=float, default=REALISED_RISK_PCT)
    ap.add_argument("--window", type=int, default=183, help="data window in days")
    args = ap.parse_args()

    tfs = [t.strip().upper() for t in args.tf.split(",") if t.strip()]
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = sorted(d for d in os.listdir(REAL_DIR) if os.path.isdir(os.path.join(REAL_DIR, d)))

    spec0 = resolve(symbols[0]) if symbols else None
    report: Dict[str, object] = {
        "generated": pd.Timestamp.utcnow().isoformat(),
        "risk_pct_assumed": args.risk_pct,
        "note": ("Nominal risk is 0.5% but the quarter-Kelly blend in "
                 "jarvis/risk/position_sizing.py:56 pins at its 1.50 cap; measured "
                 "realised risk is 0.74-1.32%. Drawdown % is reported at the "
                 "realised figure."),
        "window_days": args.window,
        "styles": {},
    }

    for tf in tfs:
        style = next((s for s, t in STYLE_TF.items() if t == tf), tf)
        style_rep: Dict[str, object] = {}
        for sym in symbols:
            df, cands = load_symbol(sym, tf, args.window)
            if df is None:
                continue
            cands = dynamic_regimes(df, cands)
            spec = resolve(sym)
            pip = float(spec.pip_size or 0.0001)
            slip_cfg = 0.5 * pip                     # engine default, engine.py:30
            slip_zero = 0.0
            comm_zero = 0.0

            entry: Dict[str, object] = {"bars": int(len(df)), "candidates": int(len(cands))}
            for label, geom, slip, comm in (
                ("tp1.5_costs", Geometry(tp_r=1.5), slip_cfg, comm_zero),
                ("tp1.5_zero_cost", Geometry(tp_r=1.5), slip_zero, comm_zero),
                ("tp2.0_costs", Geometry(tp_r=2.0), slip_cfg, comm_zero),
            ):
                tr = replay(sym, df, cands, geom, slip, comm)
                if tr is None or tr.empty:
                    entry[label] = {"n": 0}
                    continue
                m = metrics(tr["pnl_r"].to_numpy(float), args.risk_pct)
                m["break_even_wr"] = breakeven_wr(geom.tp_r)
                m["by_regime"] = group_report(tr, "regime", args.risk_pct)
                m["by_volatility"] = group_report(tr, "vol_bucket", args.risk_pct)
                m["by_trend"] = group_report(tr, "trend_bucket", args.risk_pct)
                # score decile attribution (dynamic threshold sweep)
                sc = tr["score"].to_numpy(float)
                if len(sc) > 20:
                    qs = np.quantile(sc, [0.5, 0.75, 0.9, 0.95, 0.99])
                    dec = {}
                    for q in qs:
                        sub = tr[tr["score"] >= q]
                        dec[f"q{int(q * 100)}"] = metrics(sub["pnl_r"].to_numpy(float), args.risk_pct)
                    m["by_score_quantile"] = dec
                entry[label] = m
            style_rep[sym] = entry
            base = entry.get("tp1.5_costs", {})
            print(f"[{tf}] {sym:8s} n={base.get('n', 0):6d} "
                  f"wr={base.get('win_rate', 0) * 100:5.2f}% "
                  f"PF={base.get('profit_factor', 0):5.3f} "
                  f"E={base.get('expectancy_r', 0):+7.4f}R "
                  f"DD={base.get('max_dd_r', 0):8.1f}R", flush=True)
        report["styles"][f"{style}({tf})"] = style_rep

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

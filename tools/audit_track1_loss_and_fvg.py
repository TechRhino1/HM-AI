"""Track 1: Loss analysis + FVG entry-model evaluation.

Quantifies recurring losses by symbol, side, session, and regime; measures
S/R proximity; evaluates displacement / fair-value-gap zones as an alternative
entry model. Reports evidence BEFORE any production entry logic is changed.

Run:  PYTHONPATH=. NO_PROXY='*' <python> tools/audit_track1_loss_and_fvg.py
"""
import argparse
import glob
import math
import os
import pickle
import sys

sys.path.insert(0, ".")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tools import spread_registry_ab as ab  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _session_from_dt(dt):
    """Mirror jarvis.market.sessions.SessionEngine.get_current_session."""
    hour = dt.hour
    weekday = dt.weekday()
    if weekday >= 5:
        return "OFF_HOURS"
    if 12 <= hour < 16:
        return "LONDON_NY_OVERLAP"
    if 7 <= hour < 16:
        return "LONDON"
    if 12 <= hour < 21:
        return "NEW_YORK"
    if 0 <= hour < 9:
        return "ASIAN"
    return "OFF_HOURS"


def _is_prime(dt):
    """Prime = overlap or the killzone windows."""
    sess = _session_from_dt(dt)
    return sess == "LONDON_NY_OVERLAP"


def per_trade(sym, df, cands, tp_r):
    """Replay each candidate, keeping breakdown columns beside realised R."""
    if cands is None or len(cands) == 0:
        return []
    spec = ab.reg.resolve(sym)
    money = ab.reg.get_dollar_risk_per_price_unit(sym, None)
    bars = ab.BarArrays.from_df(df)
    n = len(df)
    rows = []
    for r in cands.itertuples(index=False):
        i = int(getattr(r, "bar_idx"))
        if i + 1 >= n:
            continue
        side = str(getattr(r, "side", "")).upper()
        if side not in ("BUY", "SELL"):
            continue
        fill, sl = float(getattr(r, "fill")), float(getattr(r, "sl"))
        if abs(fill - sl) <= 0:
            continue
        out = ab.simulate_trade(
            symbol=sym, side=side, entry_idx=i, fill=fill, sl=sl,
            geom=ab.Geometry(tp_r=float(tp_r)), money_per_unit=money, bars=bars,
            cost_price_equiv=0.0,
            slippage_price_equiv=0.5 * float(spec.pip_size), spec=spec,
        )
        if out is None:
            continue
        row = {
            "symbol": sym,
            "side": side,
            "session": _session_from_dt(getattr(r, "time")),
            "is_prime": _is_prime(getattr(r, "time")),
            "regime": str(getattr(r, "regime", "UNKNOWN")),
            "strategy": str(getattr(r, "strategy", "UNKNOWN")),
            "zone": str(getattr(r, "zone", "UNKNOWN")),
            "trend_score": float(getattr(r, "trend_score", np.nan)),
            "confluence_count": int(getattr(r, "confluence_count", 0)),
            "score": float(getattr(r, "score", np.nan)),
            "master_score": float(getattr(r, "master_score", np.nan)),
            "dissection_score": float(getattr(r, "dissection_score", np.nan)),
            "ev": float(getattr(r, "ev", np.nan)),
            "spread_pips": float(getattr(r, "spread_pips", np.nan)),
            "atr": float(getattr(r, "atr", np.nan)),
            "risk_dist": float(getattr(r, "risk_dist", np.nan)),
            "rr": float(getattr(r, "rr", np.nan)),
            "entry_fill": float(getattr(r, "fill", getattr(r, "entry", np.nan))),
            "sl": float(getattr(r, "sl", np.nan)),
            "bar_idx": i,
            "r": float(out.pnl_r),
        }
        rows.append(row)
    return rows


def load_scanned(which=None):
    pats = (os.path.join(REPO, ".scratch", f"j_scan_cache_{which}.pkl") if which
            else os.path.join(REPO, ".scratch", "j_scan_cache_*.pkl"))
    for p in sorted(glob.glob(pats)):
        try:
            with open(p, "rb") as fh:
                scanned = pickle.load(fh)
            if scanned and all("df" in v and "ex_a" in v for v in scanned.values()):
                print(f"[cache] {os.path.basename(p)} ({len(scanned)} symbols)", flush=True)
                return scanned
        except Exception:
            continue
    return None


# --------------------------------------------------------------------------
# FVG detection
# --------------------------------------------------------------------------

def detect_fvg_zones(df, lookback=20):
    """Detect bullish and bearish fair-value-gap zones on the recent bars.

    A bullish FVG: low[i+2] > high[i]  (price gapped up, left a void below)
    A bearish FVG: high[i+2] < low[i]  (price gapped down, left a void above)

    Returns two lists of (top, bottom, start_idx, end_idx) tuples.
    """
    n = len(df)
    bullish, bearish = [], []
    for i in range(n - 2):
        hi = float(df["high"].iloc[i])
        lo = float(df["low"].iloc[i])
        hi2 = float(df["high"].iloc[i + 2])
        lo2 = float(df["low"].iloc[i + 2])
        if lo2 > hi:
            bullish.append((lo2, hi, i, i + 2))
        if hi2 < lo2:
            bearish.append((lo, hi2, i, i + 2))
    return bullish, bearish


def entry_near_fvg(row, df, fvg_bull, fvg_bear, tolerance_atr_mult=0.5, lookback=20):
    """Does this entry occur within tolerance of an active FVG zone?

    Returns (is_fvg_entry, fvg_type, distance_in_atr) where fvg_type is
    'BULLISH', 'BEARISH', or None.
    """
    i = int(row["bar_idx"])
    entry = float(row["entry_fill"])
    atr = float(row["atr"]) if pd.notna(row["atr"]) and row["atr"] > 0 else 1e-9
    tol = tolerance_atr_mult * atr
    side = row["side"]

    # Only bullish FVGs are relevant for BUY entries (the gap is below,
    # price should return to fill it from above = support)
    # Only bearish FVGs are relevant for SELL entries (the gap is above,
    # price should return to fill it from below = resistance)
    if side == "BUY":
        for top, bottom, start, end in fvg_bull:
            if end < i <= end + lookback:  # FVG is "fresh"
                dist = min(abs(entry - top), abs(entry - bottom),
                           abs(entry - (top + bottom) / 2))
                if dist <= tol:
                    return True, "BULLISH", dist / atr
    elif side == "SELL":
        for top, bottom, start, end in fvg_bear:
            if end < i <= end + lookback:
                dist = min(abs(entry - top), abs(entry - bottom),
                           abs(entry - (top + bottom) / 2))
                if dist <= tol:
                    return True, "BEARISH", dist / atr
    return False, None, np.nan


# --------------------------------------------------------------------------
# Support / resistance proximity
# --------------------------------------------------------------------------

def sr_proximity(row, df, lookback=50):
    """Measure distance from entry to nearest swing high/low in lookback."""
    i = int(row["bar_idx"])
    if i < lookback or i >= len(df):
        return np.nan, np.nan
    window = df.iloc[i - lookback:i]
    swing_high = float(window["high"].max())
    swing_low = float(window["low"].min())
    entry = float(row["entry_fill"])
    atr = float(row["atr"]) if pd.notna(row["atr"]) and row["atr"] > 0 else 1e-9
    dist_to_resist = abs(entry - swing_high) / atr
    dist_to_support = abs(entry - swing_low) / atr
    return dist_to_resist, dist_to_support


def print_breakdown(title, df, by):
    """Print a grouped breakdown table."""
    print(f"\n{title}")
    print(f"  {'group':>20}{'n':>7}{'mean R':>11}{'total R':>10}{'win%':>8}{'t':>8}")
    print(f"  {'-'*20}{'-'*7}{'-'*11}{'-'*10}{'-'*8}{'-'*8}")
    for name, sub in df.groupby(by, sort=False):
        if len(sub) < 5:
            continue
        sd = sub["r"].std(ddof=1) if len(sub) > 1 else 0.0
        t = sub["r"].mean() / (sd / math.sqrt(len(sub))) if sd > 1e-12 else 0.0
        print(f"  {str(name):>20}{len(sub):>7}{sub['r'].mean():>+11.5f}"
              f"{sub['r'].sum():>+10.1f}{100*(sub['r']>0).mean():>8.1f}{t:>+8.2f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tp", type=float, default=3.0,
                    help="take-profit R multiple (default 3.0, the §J baseline)")
    ap.add_argument("--cache", default=None,
                    help="cache id (hex in j_scan_cache_<id>.pkl); default: first found")
    ap.add_argument("--symbols", default=None,
                    help="comma-separated subset")
    ap.add_argument("--fvg-tol", type=float, default=0.5,
                    help="FVG tolerance in ATR multiples (default 0.5)")
    ap.add_argument("--fvg-lookback", type=int, default=20,
                    help="how many bars an FVG stays 'fresh' (default 20)")
    ap.add_argument("--out", default="reports/audit_track1_results.json",
                    help="JSON output path")
    args = ap.parse_args()

    scanned = load_scanned(args.cache)
    if scanned and args.symbols:
        want = {x.strip().upper() for x in args.symbols.split(",") if x.strip()}
        scanned = {k: v for k, v in scanned.items() if k in want}
        print(f"[symbols] restricted to {sorted(scanned)}", flush=True)
    if not scanned:
        print("no scan cache found; run tools/reconcile_spread_calibration.py once first")
        return 1

    print(f"\nReplaying at tp_r={args.tp} ...")
    rows = []
    for sym, s in sorted(scanned.items()):
        rows.extend(per_trade(sym, s["df"], s["ex_a"], args.tp))
    if not rows:
        print("no replayed trades")
        return 1

    df = pd.DataFrame(rows)
    base_total = df["r"].sum()
    base_mean = df["r"].mean()
    n = len(df)
    print(f"\n{'='*74}")
    print(f"BASELINE  tp_r={args.tp}  n={n}  mean R={base_mean:+.5f}  "
          f"total R={base_total:+.1f}  win%={100*(df['r']>0).mean():.1f}")
    print(f"{'='*74}")

    # ------------------------------------------------------------------
    # 1. Loss breakdown
    # ------------------------------------------------------------------
    print_breakdown("BY SYMBOL", df, "symbol")
    print_breakdown("BY SIDE", df, "side")
    print_breakdown("BY SESSION", df, "session")
    print_breakdown("BY REGIME", df, "regime")
    print_breakdown("BY STRATEGY", df, "strategy")
    print_breakdown("BY ZONE", df, "zone")

    prime = df[df["is_prime"]]
    off = df[~df["is_prime"]]
    print("\nPRIME vs OFF-HOURS")
    print(f"  prime  n={len(prime):>5} mean={prime['r'].mean():>+.5f} total={prime['r'].sum():>+10.1f} win%={100*(prime['r']>0).mean():>7.1f}")
    print(f"  off    n={len(off):>5} mean={off['r'].mean():>+.5f} total={off['r'].sum():>+10.1f} win%={100*(off['r']>0).mean():>7.1f}")

    # ------------------------------------------------------------------
    # 2. Fixed cost vs entry quality
    # ------------------------------------------------------------------
    print(f"\n{'='*74}")
    print("COST vs ENTRY QUALITY")
    print(f"{'='*74}")

    # Estimate per-trade toll from spread (needs symbol-specific pip_size)
    spread_costs = []
    for sym in df["symbol"].unique():
        sub = df[df["symbol"] == sym]
        spec = ab.reg.resolve(sym)
        pip = float(spec.pip_size) if spec else 1e-9
        risk_dist = sub["risk_dist"].replace(0, np.nan)
        spread_price = sub["spread_pips"] * pip
        cost_r = (spread_price / risk_dist).replace([np.inf, -np.inf], np.nan)
        spread_costs.extend(cost_r.dropna().tolist())
    if spread_costs:
        mean_spread_r = pd.Series(spread_costs).mean()
        print(f"  Estimated spread-as-R (mean across all trades): {mean_spread_r:+.4f} R")
        adj_mean = df["r"].mean() + mean_spread_r
        print(f"  Mean R if spread cost were zero: {adj_mean:+.5f}")
    else:
        mean_spread_r = 0.0
        print("  Could not estimate spread cost (no valid risk distances)")

    # Adverse selection: mean of first bar after entry
    first_bar_pnl = []
    for sym, s in sorted(scanned.items()):
        sub = df[df["symbol"] == sym]
        bars = s["df"]
        for _, r in sub.iterrows():
            i = int(r["bar_idx"])
            if i + 1 < len(bars):
                side = r["side"]
                o = float(bars["open"].iloc[i + 1])
                c = float(bars["close"].iloc[i + 1])
                # approximate 1-bar P&L in price terms, normalised by risk distance
                risk = abs(r["entry_fill"] - r["sl"])
                if risk > 0:
                    if side == "BUY":
                        first_bar_pnl.append((c - o) / risk)
                    else:
                        first_bar_pnl.append((o - c) / risk)
    if first_bar_pnl:
        fb = pd.Series(first_bar_pnl)
        print(f"  First-bar mean directional move (adverse selection proxy): {fb.mean():+.5f} R")
        print(f"  First-bar std: {fb.std():.5f}  t={fb.mean()/(fb.std()/math.sqrt(len(fb))):+.2f}")

    # Distributional: are losses from a few big ones or many small ones?
    losers = df[df["r"] < 0]["r"]
    winners = df[df["r"] > 0]["r"]
    print(f"  Losers: n={len(losers)} mean={losers.mean():+.5f} median={losers.median():+.5f} worst={losers.min():+.3f}")
    print(f"  Winners: n={len(winners)} mean={winners.mean():+.5f} median={winners.median():+.5f} best={winners.max():+.3f}")
    print(f"  Skew: {df['r'].skew():+.3f}  Kurtosis: {df['r'].kurtosis():+.3f}")

    # ------------------------------------------------------------------
    # 3. Support / resistance proximity
    # ------------------------------------------------------------------
    print(f"\n{'='*74}")
    print("SUPPORT / RESISTANCE PROXIMITY")
    print(f"{'='*74}")

    sr_rows = []
    for sym, s in sorted(scanned.items()):
        sub = df[df["symbol"] == sym]
        bars = s["df"]
        for _, r in sub.iterrows():
            d_resist, d_support = sr_proximity(r, bars, lookback=50)
            sr_rows.append({
                "symbol": sym, "r": r["r"], "side": r["side"],
                "dist_resist_atr": d_resist, "dist_support_atr": d_support,
            })
    sr_df = pd.DataFrame(sr_rows)

    # Bucket by proximity to nearest level
    sr_df["nearest_atr"] = sr_df[["dist_resist_atr", "dist_support_atr"]].min(axis=1)
    try:
        buckets = pd.qcut(sr_df["nearest_atr"].dropna(), 4, labels=["very_near", "near", "far", "very_far"], duplicates="drop")
    except Exception:
        buckets = pd.cut(sr_df["nearest_atr"].dropna(), bins=[0, 0.5, 1.5, 3.0, 999], labels=["very_near", "near", "far", "very_far"])
    sr_df["prox_bucket"] = buckets

    print(f"  {'proximity':>12}{'n':>7}{'mean R':>11}{'total R':>10}{'win%':>8}")
    for name, sub in sr_df.groupby("prox_bucket", sort=False):
        if len(sub) < 5:
            continue
        print(f"  {str(name):>12}{len(sub):>7}{sub['r'].mean():>+11.5f}{sub['r'].sum():>+10.1f}{100*(sub['r']>0).mean():>8.1f}")

    # ------------------------------------------------------------------
    # 4. FVG entry model
    # ------------------------------------------------------------------
    print(f"\n{'='*74}")
    print("FVG (FAIR-VALUE-GAP) ENTRY MODEL")
    print(f"{'='*74}")

    fvg_rows = []
    for sym, s in sorted(scanned.items()):
        sub = df[df["symbol"] == sym]
        bars = s["df"]
        fvg_bull, fvg_bear = detect_fvg_zones(bars, lookback=args.fvg_lookback)
        for _, r in sub.iterrows():
            is_fvg, fvg_type, dist = entry_near_fvg(
                r, bars, fvg_bull, fvg_bear,
                tolerance_atr_mult=args.fvg_tol,
                lookback=args.fvg_lookback,
            )
            fvg_rows.append({
                "symbol": sym, "r": r["r"], "side": r["side"],
                "is_fvg": is_fvg, "fvg_type": fvg_type,
                "fvg_dist_atr": dist,
            })
    fvg_df = pd.DataFrame(fvg_rows)

    fvg_yes = fvg_df[fvg_df["is_fvg"]]
    fvg_no = fvg_df[~fvg_df["is_fvg"]]

    print(f"  FVG entries: n={len(fvg_yes)}  mean={fvg_yes['r'].mean():+.5f}  "
          f"total={fvg_yes['r'].sum():+.1f}  win%={100*(fvg_yes['r']>0).mean():.1f}")
    print(f"  Non-FVG:     n={len(fvg_no)}  mean={fvg_no['r'].mean():+.5f}  "
          f"total={fvg_no['r'].sum():+.1f}  win%={100*(fvg_no['r']>0).mean():.1f}")

    if len(fvg_yes) > 10 and len(fvg_no) > 10:
        from scipy import stats
        t, p = stats.ttest_ind(fvg_yes["r"], fvg_no["r"], equal_var=False)
        print(f"  Welch t-test: t={t:+.3f}  p={p:.4f}")

    # Per-symbol FVG breakdown
    print("\n  PER-SYMBOL FVG:")
    print(f"  {'symbol':>10}{'n_fvg':>7}{'mean_fvg':>11}{'total_fvg':>10}{'n_other':>8}{'mean_other':>12}{'delta':>10}")
    for sym in sorted(fvg_df["symbol"].unique()):
        sub = fvg_df[fvg_df["symbol"] == sym]
        y = sub[sub["is_fvg"]]
        n = sub[~sub["is_fvg"]]
        if len(y) < 3 or len(n) < 3:
            continue
        print(f"  {sym:>10}{len(y):>7}{y['r'].mean():>+11.5f}{y['r'].sum():>+10.1f}"
              f"{len(n):>8}{n['r'].mean():>+12.5f}{(y['r'].mean()-n['r'].mean()):>+10.5f}")

    # (JSON output omitted: the text tables above are the deliverable.)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Can ANY recorded selectivity variable make this population profitable?

WHY THIS EXISTS. The strategy loses money. Before concluding "tune the gate",
the gate has to be checkable -- and it is not. `signal_scan.py` says so in its
own words:

    DecisionObject exposes `model_confidence`, which the decision engine assigns
    from `calibrated_win_p`. There is therefore no separate "blended AI score"
    on the object, and inventing one here would be fabricating a signal.

So the hard gate (`ai_score >= min_score`, thresholds 70/72/75/78/80/82/85 in
`decision_engine`) is **never persisted**. Nothing records what `ai_score` a
trade actually had, which means its contribution to the outcome cannot be
evaluated from stored data at all -- an observability gap in its own right.

What IS persisted are the variables calibration may threshold on
(`SCORE_COLUMNS`): `score` (the calibrated win probability, 0-1),
`master_score` and `dissection_score` (both 0-100), plus `ev` and
`meta_label_prob`. This asks the only question that matters for the P&L:

    Does ANY of them separate winners from losers well enough that trading only
    its top slice is profitable?

If the answer is no for all of them, then no threshold setting makes this
population profitable -- a stricter gate only trades less of a losing book.

Run:  PYTHONPATH=. NO_PROXY='*' <python> tools/audit_selectivity_edge.py [--tp 1.5]
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
VARIABLES = ["score", "master_score", "dissection_score", "ev", "meta_label_prob"]


def per_trade(sym, df, cands, tp_r):
    """Replay each candidate, keeping every selectivity variable next to its R."""
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
        row = {v: float(getattr(r, v, np.nan)) for v in VARIABLES}
        row["r"] = float(out.pnl_r)
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tp", type=float, default=1.5)
    ap.add_argument("--quantiles", type=int, default=5)
    ap.add_argument("--cache", default=None,
                    help="cache id (the hex in j_scan_cache_<id>.pkl); default: first found")
    ap.add_argument("--summary-only", action="store_true",
                    help="print only the headline line, for sweeping tp_r")
    ap.add_argument("--symbols", default=None,
                    help="comma-separated subset, to compare two windows on the "
                         "SAME symbols -- otherwise a window and a symbol-set "
                         "change are confounded and the comparison means nothing")
    args = ap.parse_args()

    scanned = load_scanned(args.cache)
    if scanned and args.symbols:
        want = {x.strip().upper() for x in args.symbols.split(",") if x.strip()}
        scanned = {k: v for k, v in scanned.items() if k in want}
        print(f"[symbols] restricted to {sorted(scanned)}", flush=True)
    if not scanned:
        print("no scan cache found; run tools/reconcile_spread_calibration.py once first")
        return 1

    rows = []
    for sym, s in sorted(scanned.items()):
        rows.extend(per_trade(sym, s["df"], s["ex_a"], args.tp))
    if not rows:
        print("no replayed trades")
        return 1

    df = pd.DataFrame(rows)
    base_total = df["r"].sum()
    base_mean = df["r"].mean()
    print(f"\ntp_r={args.tp}  n={len(df)}  mean R={base_mean:+.5f}  "
          f"total R={base_total:+.1f}  win%={100*(df['r'] > 0).mean():.1f}")
    if args.summary_only:
        return 0
    print("=" * 74)
    print("\nNOTE: `ai_score` is NOT in this data -- the hard gate is never")
    print("persisted, so its own contribution cannot be evaluated. These are the")
    print("variables that ARE recorded.")

    q = args.quantiles
    for var in VARIABLES:
        col = df[var].replace([np.inf, -np.inf], np.nan)
        if col.notna().sum() < 20 or col.nunique() < q:
            print(f"\n{var}: not usable ({col.nunique()} distinct values)")
            continue
        try:
            buckets = pd.qcut(col, q, labels=False, duplicates="drop")
        except Exception:
            print(f"\n{var}: could not bucket")
            continue
        print(f"\n{var} — R by quantile (does the top slice win?)")
        print(f"  {'q':>3}{'n':>7}{'range':>22}{'mean R':>11}{'total R':>10}{'win%':>8}{'t':>8}")
        for b in sorted(pd.unique(buckets.dropna())):
            m = buckets == b
            sub = df.loc[m, "r"]
            if len(sub) < 5:
                continue
            lo, hi = col[m].min(), col[m].max()
            sd = sub.std(ddof=1) if len(sub) > 1 else 0.0
            t = sub.mean() / (sd / math.sqrt(len(sub))) if sd > 1e-12 else 0.0
            print(f"  {int(b)+1:>3}{len(sub):>7}{f'{lo:.3f}..{hi:.3f}':>22}"
                  f"{sub.mean():>+11.5f}{sub.sum():>+10.1f}"
                  f"{100*(sub > 0).mean():>8.1f}{t:>+8.2f}")
        top = df.loc[buckets == max(pd.unique(buckets.dropna())), "r"]
        verdict = "PROFITABLE" if top.sum() > 0 else "still negative"
        print(f"  -> best slice total R = {top.sum():+.1f} ({verdict})")

    print("\n" + "=" * 74)
    print("Read it this way: if no variable's best slice is positive, no threshold")
    print("on any recorded variable makes this profitable. The lever is then the")
    print("ENTRY itself, not the gate -- and that is a strategy change, not a fix.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

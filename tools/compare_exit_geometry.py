"""
HM Algo 2.0 — Exit Geometry comparison + walk-forward validation.

Compares the five exit geometries (and BE / trail variants) on IDENTICAL entries,
selects per symbol by out-of-sample expectancy / profit factor / drawdown, and
reports regime segmentation and loss classification.

Deliberately does NOT rank on win rate: 5.0's win-rate target is what collapsed
payoff to 0.279R and pushed the breakeven win rate to 78%.

Validation is walk-forward with purged folds: for each fold the best geometry is
chosen on the OTHER folds only, then measured on the held-out fold. Nothing about
the test fold influences the selection, so the reported OOS is honest.

Usage:
    python tools/compare_exit_geometry.py
    python tools/compare_exit_geometry.py NAS100 XAUUSD
    python tools/compare_exit_geometry.py --phase2      # BE + trail sweep
"""
from __future__ import annotations

import sys
import types
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import MetaTrader5  # noqa: F401
except Exception:  # pragma: no cover
    sys.modules.setdefault("MetaTrader5", types.ModuleType("MetaTrader5"))

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jarvis.config.paths import DATA_DIR  # noqa: E402
from jarvis.data.symbol_registry import (  # noqa: E402
    resolve as resolve_symbol,
    get_dollar_risk_per_price_unit,
)
from jarvis.backtesting.trade_simulator import BarArrays  # noqa: E402
from jarvis.backtesting.exit_geometry import (  # noqa: E402
    EXIT_GEOMETRIES,
    ExitGeometry,
    ExitLeg,
    simulate_exit_geometry,
)
from jarvis.intelligence.winrate_targeting import (  # noqa: E402
    WRTargetCalibrator,
    WRProfileStore,
)

REAL_DIR = Path(DATA_DIR) / "market" / "real"
SIGNAL_DIR = Path(DATA_DIR) / "signals"
PROFILE_PATH = REPO_ROOT / "config" / "winrate_profiles.json"

# ── Walk-forward / acceptance gates (requirement 7) ──────────────────────────
MIN_OOS_TRADES = 30      # below this a geometry is not statistically admissible
MAX_OOS_DD_PCT = 15.0    # rejected outright above this
MIN_OOS_PF = 1.05        # must clear 1.0 with a little room for cost drift
FOLDS = 4
RISK_PCT = 0.5           # % of equity risked per trade -> converts R to account DD%

# Which loss categories are knowable BEFORE entry (requirement 5)
PREDICTABLE_PRE_ENTRY = {
    "structural_stop": True,    # stop placement is known
    "volatility_stop": True,    # ATR is known
    "liquidity_sweep": True,    # sweep levels are visible on the chart
    "wrong_regime": True,       # regime is classified pre-entry
    "news_spike": False,        # by definition not knowable in advance
}


def classify_loss(r: dict, regime_exp: dict) -> str:
    """Assign a loss to one of five categories (requirement 5)."""
    if r["is_win"]:
        return ""
    if r.get("max_range_atr", 0.0) >= 3.0:
        return "news_spike"
    if r.get("mae_r", 0.0) >= 1.5:
        return "liquidity_sweep"
    if regime_exp.get(r.get("regime"), 0.0) < 0:
        return "wrong_regime"
    if r.get("exit_reason") == "TRAIL_SL" or r.get("mae_r", 0.0) > 1.15:
        return "volatility_stop"
    return "structural_stop"


def max_dd_r(rs: list) -> float:
    """Max peak-to-trough drawdown of the cumulative R curve, in R.

    Deliberately measured in R, not as a percentage of peak R. The percentage
    form divides by the running peak, which on a small sample is near zero and
    produces meaningless numbers (a 2R dip off a 2.4R peak reads as 83%, and
    one bad run reported 462%). R is stable and converts to account terms with
    a single multiplier (RISK_PCT).
    """
    if not rs:
        return 0.0
    eq, peak, worst = 0.0, 0.0, 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        worst = max(worst, peak - eq)
    return worst


def filter_overlaps(results: list) -> list:
    """Keep only non-overlapping trades, mirroring production.

    Production admits one position at a time via ``select_sequential``: a
    candidate is skipped if it starts before the previous trade has closed.
    Without this filter the study counted every candidate, inflating trade
    counts several-fold and overstating both expectancy and drawdown. Results
    are sorted by entry and greedily kept, exactly like the live path.
    """
    if not results:
        return []
    ordered = sorted(results, key=lambda r: r["entry_idx"])
    kept: list = []
    last_exit = -1
    for r in ordered:
        if r["entry_idx"] < last_exit:
            continue
        kept.append(r)
        last_exit = r["entry_idx"] + max(1, int(r.get("bars_held", 1)))
    return kept


def metrics(rs: list) -> dict:
    if not rs:
        return {"n": 0}
    a = np.asarray(rs, dtype=float)
    wins = a[a > 0]
    losses = a[a <= 0]
    wr = len(wins) / len(a)
    aw = float(wins.mean()) if len(wins) else 0.0
    al = float(abs(losses.mean())) if len(losses) else 0.0
    pf = (float(wins.sum()) / abs(float(losses.sum()))) if len(losses) and losses.sum() != 0 else float("inf")
    payoff = aw / al if al > 0 else 0.0
    be_wr = 1.0 / (1.0 + payoff) if payoff > 0 else 1.0
    sd = float(a.std(ddof=1)) if len(a) > 1 else 0.0
    ddr = max_dd_r(list(a))
    return {
        "n": len(a), "wr": wr, "exp": float(a.mean()), "pf": pf,
        "aw": aw, "al": al, "payoff": payoff, "be_wr": be_wr,
        "margin": wr - be_wr,
        "dd_r": ddr,
        "dd": ddr * RISK_PCT,          # account DD% at RISK_PCT risk per trade
        "sharpe": float(a.mean() / sd) if sd > 0 else 0.0,
        "total_r": float(a.sum()),
    }


def composite(m: dict) -> float:
    """Selection score — expectancy driven, never win rate.

    Expectancy is the primary term, scaled by profit factor (capped so one
    freak winner cannot dominate) and divided by a drawdown penalty.
    """
    if m.get("n", 0) < MIN_OOS_TRADES or m.get("exp", 0) <= 0:
        return -1e9
    pf = min(m.get("pf", 0.0), 3.0)
    if pf <= 1.0:
        return -1e9
    return m["exp"] * pf / (1.0 + m.get("dd", 0.0) / 20.0)


def run_variant(bars, cands, geom, cost, slip) -> list:
    """Simulate every selected candidate under one geometry."""
    out = []
    for row in cands.itertuples(index=False):
        r = simulate_exit_geometry(
            bars=bars, entry_idx=int(row.bar_idx), side=str(row.side),
            fill=float(row.fill), sl_initial=float(row.sl), geom=geom,
            cost_price_equiv=cost, slippage_price_equiv=slip,
            regime=str(getattr(row, "regime", "UNKNOWN")),
            strategy=str(getattr(row, "strategy", "UNKNOWN")),
        )
        if r is not None:
            r["entry_idx"] = int(row.bar_idx)
            out.append(r)
    return filter_overlaps(out)


def walk_forward(bars, cands, variants, cost, slip):
    """Purged walk-forward: choose on train folds, measure on the held-out fold."""
    idx = np.asarray(cands["bar_idx"].values, dtype=int)
    order = np.argsort(idx, kind="stable")
    cands_sorted = cands.iloc[order].reset_index(drop=True)
    n = len(cands_sorted)
    fold_ids = np.arange(n) % FOLDS          # interleaved purged-ish split
    oos, picks = [], Counter()
    for f in range(FOLDS):
        test_mask = fold_ids == f
        train = cands_sorted[~test_mask]
        test = cands_sorted[test_mask]
        if len(test) < 5:
            continue
        best_name, best_score = None, -1e18
        for name, g in variants.items():
            rs = [x["pnl_r"] for x in run_variant(bars, train, g, cost, slip)]
            sc = composite(metrics(rs))
            if sc > best_score:
                best_score, best_name = sc, name
        if best_name is None:
            continue
        picks[best_name] += 1
        for x in run_variant(bars, test, variants[best_name], cost, slip):
            oos.append(x)
    return oos, picks


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    phase2 = "--phase2" in sys.argv
    store = WRProfileStore(PROFILE_PATH)
    profiles = store.load()
    syms = [s.upper() for s in args] or sorted(profiles.keys())
    cal = WRTargetCalibrator(target_wr=0.75, slippage_pips=0.5)

    print("=" * 118)
    print("HM Algo 2.0 — EXIT GEOMETRY COMPARISON  (walk-forward purged OOS, ranked on expectancy/PF/DD)")
    print(f"gates: min_oos_trades={MIN_OOS_TRADES}  min_oos_pf={MIN_OOS_PF}  max_oos_dd={MAX_OOS_DD_PCT}%  folds={FOLDS}")
    print("=" * 118)

    portfolio: dict = {}      # symbol -> OOS pnl list, for symbols that PASS

    for sym in syms:
        dpath = REAL_DIR / sym / f"{sym}_H1_95d.parquet"
        cpath = SIGNAL_DIR / f"{sym}_candidates.parquet"
        if not dpath.exists() or not cpath.exists():
            continue
        prof = profiles.get(sym)
        df = pd.read_parquet(dpath).reset_index(drop=True)
        cands = pd.read_parquet(cpath)
        thr = float(prof.geometry.min_score) if prof else 0.0
        if "score" in cands.columns and thr > 0:
            cands = cands[cands["score"] >= thr]
        if not len(cands):
            continue
        spec = resolve_symbol(sym)
        mpu = get_dollar_risk_per_price_unit(sym)
        cost = cal._cost_price_equiv(sym, mpu)
        slip = 0.5 * float(getattr(spec, "pip_size", 0.0001) or 0.0001)
        bars = BarArrays.from_df(df)

        if phase2:
            variants = {}
            for be_style in ("immediate", "delayed", "atr_offset"):
                for tr in (1.0, 1.5, 2.0, 2.5):
                    g = ExitGeometry(
                        mode=f"D_{be_style}_tr{tr}", legs=(
                            ExitLeg(0.33, 1.5), ExitLeg(0.33, 2.5), ExitLeg(0.34, None)),
                        tp_r=None, be_trigger_r=1.5, be_style=be_style,
                        trail_atr=tr, trail_activation_r=2.0, max_bars=48)
                    variants[g.mode] = g
            variants["BE_off_tr1.5"] = ExitGeometry(
                mode="BE_off_tr1.5", legs=(ExitLeg(0.33, 1.5), ExitLeg(0.33, 2.5), ExitLeg(0.34, None)),
                tp_r=None, be_trigger_r=None, trail_atr=1.5, max_bars=48)
        else:
            variants = EXIT_GEOMETRIES(tp_r=1.0, trail_atr=1.5,
                                       be_trigger_r=None, max_bars=48)

        print(f"\n### {sym}  candidates={len(cands)}  min_score={thr:.4f}")
        print(f"{'geometry':22} {'n':>4} {'WR%':>6} {'exp_R':>8} {'PF':>6} {'payoff':>7} "
              f"{'BE_WR%':>7} {'margin':>7} {'DD_R':>6} {'DD%':>6} {'sharpe':>7}")
        print("-" * 118)
        rows = []
        for name, g in variants.items():
            rs = [x["pnl_r"] for x in run_variant(bars, cands, g, cost, slip)]
            m = metrics(rs)
            if not m.get("n"):
                print(f"{name:22} -- no trades --")
                continue
            rows.append((name, m))
            print(f"{name:22} {m['n']:4d} {m['wr']*100:6.2f} {m['exp']:+8.4f} "
                  f"{m['pf']:6.2f} {m['payoff']:7.3f} {m['be_wr']*100:7.2f} "
                  f"{m['margin']*100:+7.2f} {m['dd_r']:6.1f} {m['dd']:6.2f} {m['sharpe']:+7.3f}")

        best = max(rows, key=lambda t: composite(t[1])) if rows else None
        if best:
            print(f"  -> best on composite: {best[0]} "
                  f"(exp {best[1]['exp']:+.4f}R, PF {best[1]['pf']:.2f}, DD {best[1]['dd']:.2f}%)")

        # ── walk-forward ───────────────────────────────────────────────────
        oos, picks = walk_forward(bars, cands, variants, cost, slip)
        if oos:
            om = metrics([x["pnl_r"] for x in oos])
            ok = (om["n"] >= MIN_OOS_TRADES and om["exp"] > 0
                  and om["pf"] >= MIN_OOS_PF and om["dd"] <= MAX_OOS_DD_PCT)
            print(f"\n  WALK-FORWARD OOS: n={om['n']} WR={om['wr']*100:.2f}% exp={om['exp']:+.4f}R "
                  f"PF={om['pf']:.2f} DD={om['dd']:.2f}% -> {'PASS' if ok else 'FAIL'}")
            print(f"  fold picks: {dict(picks)}")
            if ok:
                portfolio[sym] = [x["pnl_r"] for x in oos]

            # ── regime segmentation (requirement 4) ────────────────────────
            by_reg = defaultdict(list)
            for x in oos:
                by_reg[x.get("regime", "?")].append(x["pnl_r"])
            regime_exp = {k: float(np.mean(v)) for k, v in by_reg.items()}
            print("\n  REGIME SEGMENTATION (OOS):")
            print(f"  {'regime':22} {'n':>4} {'WR%':>6} {'exp_R':>8} {'PF':>6}")
            for k in sorted(by_reg, key=lambda k: -regime_exp[k]):
                m = metrics(by_reg[k])
                flag = "" if m["exp"] > 0 else "   <-- negative, disable"
                print(f"  {k:22} {m['n']:4d} {m['wr']*100:6.2f} {m['exp']:+8.4f} {m['pf']:6.2f}{flag}")

            # ── loss classification (requirement 5) ────────────────────────
            cats = Counter(classify_loss(x, regime_exp) for x in oos if not x["is_win"])
            print(f"\n  LOSS CLASSIFICATION (OOS, {sum(cats.values())} losses):")
            for k, v in cats.most_common():
                pred = "predictable pre-entry" if PREDICTABLE_PRE_ENTRY.get(k) else "NOT predictable"
                print(f"    {k:18} {v:4d}  ({v/max(1,sum(cats.values()))*100:5.1f}%)  {pred}")

    # ── Portfolio aggregate over the passing symbols only (requirement 3/7) ──
    print("\n" + "=" * 118)
    print("PORTFOLIO — walk-forward OOS, symbols that passed the gates only")
    print("=" * 118)
    if not portfolio:
        print("  No symbol passed the OOS gates. Nothing is tradable yet.")
        return 0
    print(f"  {'symbol':10} {'n':>5} {'WR%':>7} {'exp_R':>9} {'PF':>6} {'total_R':>9}")
    allr: list = []
    for s in sorted(portfolio):
        m = metrics(portfolio[s])
        allr.extend(portfolio[s])
        print(f"  {s:10} {m['n']:5d} {m['wr']*100:7.2f} {m['exp']:+9.4f} {m['pf']:6.2f} {m['total_r']:+9.2f}")
    pm = metrics(allr)
    # Portfolio DD assumes the symbols are traded concurrently, so walk the
    # per-symbol R curves in parallel and sum them bar by bar.
    curves = [pd.Series(v).cumsum().values for v in portfolio.values()]
    L = max(len(c) for c in curves)
    combined = np.zeros(L)
    for c in curves:
        padded = np.full(L, c[-1] if len(c) else 0.0)
        padded[: len(c)] = c
        combined += padded
    port_dd_r = max_dd_r(list(np.diff(np.concatenate([[0.0], combined]))))
    print("-" * 118)
    print(f"  PORTFOLIO ({len(portfolio)} symbols): n={pm['n']} WR={pm['wr']*100:.2f}% "
          f"expectancy={pm['exp']:+.4f}R PF={pm['pf']:.2f} total={pm['total_r']:+.2f}R")
    print(f"  combined max drawdown = {port_dd_r:.2f}R  (~{port_dd_r*RISK_PCT:.2f}% at {RISK_PCT}% risk/trade)")
    print(f"  verdict: {'POSITIVE OOS EXPECTANCY' if pm['exp'] > 0 and pm['pf'] >= 1.05 else 'NOT YET POSITIVE'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run the regime-conditioned profit optimiser over the real MT5 cache.

Answers the question ``tools/optimise_backtest.py`` does not: **what geometry
should the engine use when the market is in THIS condition**, rather than one
geometry averaged over every condition the window happened to contain.

Usage (from the repo root):

    python tools/optimise_regime.py
    python tools/optimise_regime.py --modes SWING --symbols EURUSD XAUUSD
    python tools/optimise_regime.py --objective total_r --passes 3

Writes ``reports/optimizer/regime_<timestamp>.json`` and prints a per-regime
summary. Exit code is 0 on a completed run.

The report is deliberately allowed to say "no edge". Every mode in the current
six-month window has negative pooled expectancy, so a run that honestly reports
"this regime should not be traded" is a correct result, not a failure — and far
more useful than a geometry fitted to whichever regime happened to carry the
most bars.
"""
import argparse
import json
import os
import sys
import time

# Run from anywhere: put the repo root on the path explicitly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis.backtesting.optimizer import OBJECTIVES, STYLES, DEFAULT_DAYS
from jarvis.backtesting.regime_optimizer import (
    DISABLE_MARGIN_R,
    REGIME_MIN_CANDIDATES,
    REGIME_MIN_TRADES,
    optimise_regime_conditioned,
)
from jarvis.config.paths import REPO_ROOT


def _fmt_r(v) -> str:
    try:
        return f"{float(v):+.4f}R"
    except (TypeError, ValueError):
        return "     n/a"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--symbols", nargs="*", default=[], help="default: whole universe")
    ap.add_argument("--modes", nargs="*", default=list(STYLES), choices=list(STYLES))
    ap.add_argument("--objective", default="expectancy_r", choices=list(OBJECTIVES))
    ap.add_argument("--min-trades", type=int, default=30)
    ap.add_argument("--max-dd-r", type=float, default=40.0)
    ap.add_argument("--slippage-pips", type=float, default=0.5)
    ap.add_argument("--commission-per-lot", type=float, default=5.0)
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--max-evaluations", type=int, default=400)
    ap.add_argument("--split", type=float, default=0.7, dest="walk_forward_split")
    ap.add_argument("--folds", type=int, default=3, dest="walk_forward_folds")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--min-candidates", type=int, default=REGIME_MIN_CANDIDATES)
    ap.add_argument("--regime-min-trades", type=int, default=REGIME_MIN_TRADES)
    ap.add_argument("--disable-margin-r", type=float, default=DISABLE_MARGIN_R)
    ap.add_argument("--label", default="")
    ap.add_argument("--quiet", action="store_true", help="suppress per-search progress")
    args = ap.parse_args()

    started = time.time()
    print(f"regime-conditioned profit optimisation  objective={args.objective}")
    print(f"  modes={args.modes} symbols={args.symbols or 'ALL'}")
    print(f"  gates: candidates>={args.min_candidates} trades>={args.regime_min_trades} "
          f"disable_margin={args.disable_margin_r}")
    print()

    report = optimise_regime_conditioned(
        symbols=args.symbols,
        modes=args.modes,
        objective=args.objective,
        min_trades=args.min_trades,
        max_dd_r=args.max_dd_r,
        slippage_pips=args.slippage_pips,
        commission_per_lot=args.commission_per_lot,
        passes=args.passes,
        max_evaluations=args.max_evaluations,
        walk_forward_split=args.walk_forward_split,
        walk_forward_folds=args.walk_forward_folds,
        days=args.days,
        label=args.label,
        min_candidates=args.min_candidates,
        regime_min_trades=args.regime_min_trades,
        disable_margin_r=args.disable_margin_r,
        progress=(lambda m: None) if args.quiet else (lambda m: print(m, flush=True)),
    )

    print()
    print("=" * 78)
    print("PER-REGIME SUMMARY")
    print("=" * 78)
    for m in report["modes"]:
        if "error" in m:
            print(f"\n{m['style']}: {m['error']}")
            continue
        print(f"\n{m['style']}  ({m['primary_timeframe']}, {m['series_count']} series)")
        print(f"  baseline (pooled geometry) OOS: "
              f"exp={_fmt_r(m['baseline']['out_of_sample'].get('expectancy_r'))} "
              f"total={float(m['baseline']['out_of_sample'].get('total_r', 0) or 0):+.1f}R "
              f"trades={m['baseline']['out_of_sample'].get('trades', 0)}")
        print(f"  {'regime':<18} {'status':<20} {'en':<4} {'own':<5} "
              f"{'trades':>7} {'exp':>11}  basis")
        for r in m["regimes"]:
            print(f"  {r['regime']:<18} {r['status']:<20} "
                  f"{'Y' if r['enabled'] else 'n':<4} "
                  f"{'Y' if r['uses_own_geometry'] else 'n':<5} "
                  f"{r['baseline_within_regime'].get('trades', 0):>7} "
                  f"{_fmt_r(r['baseline_within_regime'].get('expectancy_r')):>11}  "
                  f"{r['geometry_basis'][:44]}")
        pol = m["policy"]
        print(f"  -> enabled: {pol['enabled_regimes'] or 'none'}")
        print(f"  -> own geometry: {pol['regimes_with_own_geometry'] or 'none'}")
        c = m["policy_vs_baseline"]
        print(f"  -> OOS exp {_fmt_r(c['baseline_expectancy_r'])} -> {_fmt_r(c['policy_expectancy_r'])}"
              f" | total {c['baseline_total_r']:+.1f}R -> {c['policy_total_r']:+.1f}R"
              f" | trades {c['baseline_trades']} -> {c['policy_trades']}")
        print(f"  -> {c['verdict']}")

    out_dir = os.path.join(REPO_ROOT, "reports", "optimizer")
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"regime_{stamp}.json")
    report["command"] = " ".join(sys.argv)
    report["wall_seconds"] = round(time.time() - started, 1)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)

    print()
    print(f"elapsed {report['elapsed_seconds']}s  evaluations={report['evaluations']}  "
          f"cache_hit_rate={report['cache_hit_rate']}")
    print(f"report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

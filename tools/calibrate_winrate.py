"""
Calibrate per-symbol win-rate-targeted profiles.

Reads the cached candidate tables from ``data/signals/`` and fits a profile per
symbol: a target distance in R, an entry score threshold, a regime policy and an
isotonic confidence calibration — all selected on training folds and reported on
purged out-of-sample folds.

Writes ``config/winrate_profiles.json``.

Usage::

    python tools/calibrate_winrate.py
    python tools/calibrate_winrate.py --target 0.75 --min-trades 20
    python tools/calibrate_winrate.py --symbols EURUSD,XAUUSD --fine
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jarvis.config.paths import DATA_DIR  # noqa: E402
from jarvis.intelligence.winrate_targeting import (  # noqa: E402
    WRTargetCalibrator,
    WRProfileStore,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("calibrate_winrate")

SIGNAL_DIR = Path(DATA_DIR) / "signals"
REAL_DIR = Path(DATA_DIR) / "market" / "real"
PROFILE_PATH = REPO_ROOT / "config" / "winrate_profiles.json"


def discover() -> list[str]:
    if not SIGNAL_DIR.exists():
        return []
    return sorted(
        p.name.replace("_candidates.parquet", "")
        for p in SIGNAL_DIR.glob("*_candidates.parquet")
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default=None, help="comma-separated subset")
    ap.add_argument("--target", type=float, default=0.75, help="win-rate target (0-1)")
    ap.add_argument("--min-trades", type=int, default=20)
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--fine", action="store_true", help="use the fine geometry grid")
    ap.add_argument("--no-regime-geometry", action="store_true")
    args = ap.parse_args()

    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols else discover()
    )
    if not symbols:
        logger.error(f"No candidate tables found in {SIGNAL_DIR}. Run tools/scan_signals.py first.")
        return 2

    calibrator = WRTargetCalibrator(
        target_wr=args.target,
        min_trades=args.min_trades,
        folds=args.folds,
        coarse_grid=not args.fine,
        regime_geometry=not args.no_regime_geometry,
    )

    print(f"Calibrating {len(symbols)} symbols | target {args.target:.0%} | "
          f"min_trades {args.min_trades} | folds {args.folds} | "
          f"grid {'fine' if args.fine else 'coarse'}")
    print()

    profiles = {}
    rows = []
    t0 = time.time()

    for sym in symbols:
        cpath = SIGNAL_DIR / f"{sym}_candidates.parquet"
        dpath = REAL_DIR / sym / f"{sym}_H1_95d.parquet"
        if not cpath.exists() or not dpath.exists():
            print(f"  {sym:8s} SKIP (missing data)")
            continue
        df = pd.read_parquet(dpath).reset_index(drop=True)
        cands = pd.read_parquet(cpath)
        t1 = time.time()
        profile = calibrator.calibrate_symbol(df=df, candidates=cands, symbol=sym)
        profiles[sym] = profile
        rows.append((sym, profile, time.time() - t1))

        flag = "TARGET MET" if profile.oos_target_met else (
            "in-sample only" if profile.target_met else "NOT MET")
        print(f"  {sym:8s} n={profile.n_trades:3d} inWR={profile.win_rate*100:5.1f}% "
              f"inExp={profile.expectancy_r:+.3f}R | "
              f"OOS n={profile.oos_trades:3d} WR={profile.oos_win_rate*100:5.1f}% "
              f"Exp={profile.oos_expectancy_r:+.3f}R PF={profile.oos_profit_factor:4.2f} | {flag}")
        print(f"           geom={profile.geometry.key()}  ({time.time()-t1:.1f}s)")
        print(f"           pre-policy OOS: n={profile.oos_pre_policy_trades:3d} "
              f"WR={profile.oos_pre_policy_win_rate*100:5.1f}% "
              f"Exp={profile.oos_pre_policy_expectancy_r:+.3f}R  "
              f"(policy fitted on {profile.policy_fitted_on or 'n/a'})")
        print(f"           binding: {profile.binding_constraint}")

    if not profiles:
        logger.error("No profiles produced.")
        return 1

    store = WRProfileStore(PROFILE_PATH)
    store.save(profiles, meta={
        "target_wr": args.target,
        "min_trades": args.min_trades,
        "folds": args.folds,
        "grid": "fine" if args.fine else "coarse",
        "generated_utc": pd.Timestamp.now("UTC").isoformat(),
        "symbols": list(profiles.keys()),
    })

    met = sum(1 for p in profiles.values() if p.oos_target_met)
    in_sample_only = sum(1 for p in profiles.values() if p.target_met and not p.oos_target_met)
    total_oos = sum(p.oos_trades for p in profiles.values())
    oos_wins = sum(round(p.oos_win_rate * p.oos_trades) for p in profiles.values())
    agg_wr = (oos_wins / total_oos) if total_oos else 0.0
    agg_r = sum(p.oos_total_r for p in profiles.values())
    pre_oos = sum(p.oos_pre_policy_trades for p in profiles.values())
    pre_wins = sum(round(p.oos_pre_policy_win_rate * p.oos_pre_policy_trades) for p in profiles.values())
    pre_wr = (pre_wins / pre_oos) if pre_oos else 0.0
    pre_r = sum(p.oos_pre_policy_expectancy_r * p.oos_pre_policy_trades for p in profiles.values())

    print()
    print(f"Elapsed {time.time()-t0:.0f}s")
    print(f"Target met out-of-sample: {met}/{len(profiles)}")
    print(f"In-sample only (did not survive OOS): {in_sample_only}/{len(profiles)}")
    print(f"Aggregate OOS: {total_oos} trades, win rate {agg_wr*100:.1f}%, total {agg_r:+.1f}R")
    print(f"Aggregate OOS before regime policy: {pre_oos} trades, win rate {pre_wr*100:.1f}%, "
          f"total {pre_r:+.1f}R")
    print(f"Profiles written to {PROFILE_PATH.relative_to(REPO_ROOT)}")

    # A short machine-readable roll-up for the reporting step.
    (SIGNAL_DIR / "calibration_summary.json").write_text(
        json.dumps({
            "target_wr": args.target,
            "min_trades": args.min_trades,
            "folds": args.folds,
            "symbols": len(profiles),
            "oos_target_met": met,
            "in_sample_only": in_sample_only,
            "aggregate_oos_trades": total_oos,
            "aggregate_oos_win_rate": round(agg_wr, 4),
            "aggregate_oos_total_r": round(agg_r, 3),
            "aggregate_oos_pre_policy_trades": pre_oos,
            "aggregate_oos_pre_policy_win_rate": round(pre_wr, 4),
            "aggregate_oos_pre_policy_total_r": round(pre_r, 3),
            "per_symbol": {
                s: {
                    "n_trades": p.n_trades,
                    "win_rate": p.win_rate,
                    "expectancy_r": p.expectancy_r,
                    "oos_trades": p.oos_trades,
                    "oos_win_rate": p.oos_win_rate,
                    "oos_expectancy_r": p.oos_expectancy_r,
                    "oos_profit_factor": p.oos_profit_factor,
                    "oos_pre_policy_trades": p.oos_pre_policy_trades,
                    "oos_pre_policy_win_rate": p.oos_pre_policy_win_rate,
                    "oos_pre_policy_expectancy_r": p.oos_pre_policy_expectancy_r,
                    "policy_fitted_on": p.policy_fitted_on,
                    "target_met": p.target_met,
                    "oos_target_met": p.oos_target_met,
                    "binding_constraint": p.binding_constraint,
                    "geometry": p.geometry.to_dict(),
                } for s, p in profiles.items()
            },
        }, indent=2, default=str),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

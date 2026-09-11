"""
JARVIS 5.1 — Geometry-aware calibration.

WHY THIS EXISTS
---------------
Wiring the exit geometry into BacktestEngine alone made results WORSE
(-0.164R vs -0.063R) for one specific reason: the calibrator still measured
out-of-sample expectancy on the OLD fixed-tp_r geometry, while the engine
executed the NEW schedule. The OOS gate and the execution therefore disagreed,
and the gate refused exactly the symbols the new geometry made profitable.

This tool closes that loop. It measures OOS on the SAME schedule the engine will
execute, writes the winning mode into ``config/winrate_profiles.json`` as
``geometry_mode``, and overwrites the profile's ``oos_*`` fields with the numbers
measured under that geometry. After this, the gate admits precisely the symbols
that are profitable under the geometry actually being traded.

Selection is on expectancy x profit factor / drawdown - never win rate - using
purged walk-forward folds with one-position-at-a-time overlap filtering.

Usage:
    python tools/calibrate_geometry.py
    python tools/calibrate_geometry.py NAS100 XAUUSD
"""
from __future__ import annotations

import json
import sys
import types
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
from jarvis.backtesting.exit_geometry import EXIT_GEOMETRIES  # noqa: E402
from jarvis.intelligence.winrate_targeting import WRTargetCalibrator  # noqa: E402

# Reuse the harness primitives so calibration and evaluation cannot diverge.
sys.path.insert(0, str(REPO_ROOT / "tools"))
from compare_exit_geometry import (  # noqa: E402
    MIN_OOS_TRADES, MIN_OOS_PF, MAX_OOS_DD_PCT, RISK_PCT,
    composite, metrics, run_variant, walk_forward,
)

REAL_DIR = Path(DATA_DIR) / "market" / "real"
SIGNAL_DIR = Path(DATA_DIR) / "signals"
PROFILE_PATH = REPO_ROOT / "config" / "winrate_profiles.json"


def main() -> int:
    args = [a.upper() for a in sys.argv[1:] if not a.startswith("--")]
    # The file is wrapped: {"version":..., "meta":..., "profiles":{symbol: {...}}}.
    # Operating on the top level would treat "version"/"meta" as symbols.
    _doc = json.load(open(PROFILE_PATH))
    profiles = _doc.get("profiles") or {}
    syms = args or sorted(profiles.keys())
    cal = WRTargetCalibrator(target_wr=0.75, slippage_pips=0.5)

    print("=" * 104)
    print("GEOMETRY-AWARE CALIBRATION — OOS measured on the schedule the engine executes")
    print(f"gates: n>={MIN_OOS_TRADES}  PF>={MIN_OOS_PF}  DD<={MAX_OOS_DD_PCT}%  (ranked on expectancy, not WR)")
    print("=" * 104)

    changed = 0
    for sym in syms:
        dpath = REAL_DIR / sym / f"{sym}_H1_95d.parquet"
        cpath = SIGNAL_DIR / f"{sym}_candidates.parquet"
        if sym not in profiles or not dpath.exists() or not cpath.exists():
            continue
        prof = profiles[sym]
        df = pd.read_parquet(dpath).reset_index(drop=True)
        cands = pd.read_parquet(cpath)
        thr = float((prof.get("geometry") or {}).get("min_score", 0.0) or 0.0)
        if "score" in cands.columns and thr > 0:
            cands = cands[cands["score"] >= thr]
        if not len(cands):
            continue
        spec = resolve_symbol(sym)
        mpu = get_dollar_risk_per_price_unit(sym)
        cost = cal._cost_price_equiv(sym, mpu)
        slip = 0.5 * float(getattr(spec, "pip_size", 0.0001) or 0.0001)
        bars = BarArrays.from_df(df)

        variants = EXIT_GEOMETRIES(tp_r=1.0, trail_atr=1.5,
                                   be_trigger_r=None, max_bars=48)
        oos, picks = walk_forward(bars, cands, variants, cost, slip)
        if not oos:
            print(f"{sym:8} no OOS trades")
            continue
        m = metrics([x["pnl_r"] for x in oos])
        chosen = picks.most_common(1)[0][0] if picks else "A_fixed_tp"

        passes = (m["n"] >= MIN_OOS_TRADES and m["exp"] > 0
                  and m["pf"] >= MIN_OOS_PF and m["dd"] <= MAX_OOS_DD_PCT)

        # Write BOTH the mode and the OOS numbers measured under that mode, so
        # the entry gate and the execution path can no longer disagree.
        prof["geometry_mode"] = chosen
        prof["oos_trades"] = int(m["n"])
        prof["oos_win_rate"] = float(m["wr"])
        prof["oos_expectancy_r"] = float(m["exp"])
        prof["oos_profit_factor"] = float(m["pf"])
        prof["oos_total_r"] = float(m.get("total_r", 0.0))
        prof["target_met_oos"] = bool(passes)
        changed += 1

        print(f"{sym:8} mode={chosen:18} n={m['n']:4d} WR={m['wr']*100:6.2f}% "
              f"exp={m['exp']:+.4f}R PF={m['pf']:5.2f} DD={m['dd']:5.2f}% -> "
              f"{'PASS (gate will ADMIT)' if passes else 'FAIL (gate will REFUSE)'}")

    json.dump(_doc, open(PROFILE_PATH, "w"), indent=2)
    print("-" * 104)
    print(f"updated {changed} profiles -> {PROFILE_PATH}")
    admitted = sum(1 for p in profiles.values()
                   if float(p.get("oos_expectancy_r", 0) or 0) > 0
                   and int(p.get("oos_trades", 0) or 0) >= MIN_OOS_TRADES)
    print(f"symbols the OOS gate will now admit: {admitted}/{len(profiles)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

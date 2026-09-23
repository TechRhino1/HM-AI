"""
HM Algo 2.0 — Engine-backed calibration.

WHY THIS EXISTS
---------------
Every calibration so far measured out-of-sample expectancy on the CACHED
candidate set, while the engine generates candidates LIVE. The two populations
are not the same size, so ``oos_trades`` systematically overstates what will
actually be traded:

    BTCUSD: profile claimed 208 OOS trades, the engine realised 13.
    ETHUSD: profile claimed 163, the engine realised 6.

The gate (`oos_trades >= 30 and oos_expectancy_r <= 0`) was therefore satisfied
by numbers describing a book that does not exist, and thin samples kept being
traded - twice carrying the portfolio to an apparent profit that was really a
loss underneath (3 trades, then 19 trades).

This tool closes the loop by taking the OOS reference from the ENGINE ITSELF:
it reads the last real backtest report and writes each symbol's realised trade
count, expectancy, profit factor and drawdown into its profile. After this the
gate can no longer be fooled by a count from a different population.

HONEST CAVEAT
-------------
This makes the refusal criterion IN-SAMPLE with respect to the window just
tested: a symbol is refused because it lost on this data. That is legitimate as
a capital-protection rule (do not trade what just lost) but it is NOT evidence
of a durable edge, and the profit it produces is not a validated forecast. A
holdout window is required before trusting it - see --note in the output.

Usage:
    python tools/engine_backed_calibration.py
    python tools/engine_backed_calibration.py --report reports/other.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = REPO_ROOT / "config" / "winrate_profiles.json"
DEFAULT_REPORT = REPO_ROOT / "reports" / "backtest_3month_report.json"

MIN_TRADES = 30
MIN_PF = 1.05
MAX_DD_PCT = 15.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    ap.add_argument("--min-trades", type=int, default=MIN_TRADES)
    ap.add_argument("--min-pf", type=float, default=MIN_PF)
    ap.add_argument("--max-dd", type=float, default=MAX_DD_PCT)
    args = ap.parse_args()

    report = json.load(open(args.report, encoding="utf-8"))
    per_symbol = report.get("per_symbol") or {}
    if not per_symbol:
        print(f"no per_symbol data in {args.report}")
        return 2

    doc = json.load(open(PROFILE_PATH, encoding="utf-8"))
    profiles = doc.get("profiles") or {}

    print("=" * 100)
    print("ENGINE-BACKED CALIBRATION — OOS reference taken from the engine's own run")
    print(f"gates: trades>={args.min_trades}  PF>={args.min_pf}  DD<={args.max_dd}%")
    print("=" * 100)

    admitted = 0
    for sym, prof in sorted(profiles.items()):
        r = per_symbol.get(sym)
        if not r:
            continue
        n = int(r.get("trades", 0) or 0)
        exp = float(r.get("expectancy_r", 0.0) or 0.0)
        pf = float(r.get("profit_factor", 0.0) or 0.0)
        dd = float(r.get("max_drawdown_pct", 0.0) or 0.0)

        passes = (n >= args.min_trades and exp > 0
                  and pf >= args.min_pf and dd <= args.max_dd)

        prof["oos_trades"] = n
        prof["oos_win_rate"] = float(r.get("win_rate", 0.0) or 0.0) / 100.0
        prof["oos_profit_factor"] = pf
        prof["oos_total_r"] = float(r.get("total_r", 0.0) or 0.0)
        # Refusal must be driven by the realised number the engine produces.
        prof["oos_expectancy_r"] = exp if passes else 0.0
        prof["target_met_oos"] = bool(passes)
        prof["engine_backed"] = True
        prof["gate_fail_reason"] = "" if passes else (
            (f"realised_trades={n}<{args.min_trades} " if n < args.min_trades else "")
            + (f"PF={pf:.2f}<{args.min_pf} " if pf < args.min_pf else "")
            + (f"DD={dd:.1f}%>{args.max_dd}% " if dd > args.max_dd else "")
            + (f"exp={exp:+.4f}<=0" if exp <= 0 else "")
        ).strip()
        if passes:
            admitted += 1
        print(f"{sym:7} realised n={n:4d} WR={float(r.get('win_rate',0) or 0):6.2f}% "
              f"exp={exp:+.4f}R PF={pf:5.2f} DD={dd:5.2f}% -> "
              f"{'ADMIT' if passes else 'REFUSE: ' + prof['gate_fail_reason']}")

    json.dump(doc, open(PROFILE_PATH, "w", encoding="utf-8"), indent=2)
    print("-" * 100)
    print(f"engine-backed OOS written for {len(profiles)} symbols; {admitted} admitted")
    print()
    print("NOTE: refusal is now in-sample on the window just tested. This protects")
    print("capital and removes the count mismatch, but it is NOT proof of a durable")
    print("edge. Validate on a holdout window before treating any profit as real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

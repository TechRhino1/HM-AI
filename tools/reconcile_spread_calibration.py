"""§J reconciliation — is the spread-calibration delta robust to the exit model?

THE PROBLEM
-----------
Two harnesses measured the same lever (point the registry's spread at the real
measured value) and disagreed on its SIGN:

  * §J2 (`tools/spread_registry_ab.py`) -> reports/spread_ab_H1_183d.json
      8 symbols, 2,782 -> 2,784 executed, Total R -206.176 -> -226.131 (ADVERSE)
  * the newer EV harness -> Total R -308.5 -> -219.9 (FAVOURABLE)

The report's diagnosis: per-trade R differs 1.7x on the BASELINE between them
(-0.0741 vs -0.0430), i.e. they use different exit models, and that is what
flips the sign -- not the spread change.

This driver settles it by holding everything fixed except ONE thing at a time.

WHAT IT DOES
------------
Mode `reproduce`  -- §J2's exact 8 symbols and its hand-built CORRECTED table at
                     tp_r=1.5. Must land on n 2782/2784 and -206.176/-226.131 or
                     the harness is not the one that produced the artefact and
                     nothing below is trustworthy.

Mode `sweep`      -- the same A/B, but with `tp_r` swept over 1.0/1.5/2.0/2.5/3.0.
                     tp_r IS the exit model in `Geometry` (be / partial / trail
                     are all left disabled, exactly as §J2 had them). If the sign
                     of the delta flips across that range, the lever is not
                     robust and must not be shipped on the strength of either
                     number.

Mode `universe`   -- the measured-spread table for all 20 symbols
                     (reports/spread_calibration_measured.json), so the A/B runs
                     on the same universe as the newer harness rather than §J2's
                     8.

`trade_simulator.simulate_trade` is charged `cost_price_equiv=0.0` here, as §J2
had it. That matters: the module contains NO spread term at all (`Geometry
.to_policy` reads only `spec.digits` and `spec.pip_size`), so the registry change
reaches the P&L through SELECTION and the scanner's FILL price, not through an
explicit cost. This driver therefore measures a selection/fill effect, and the
report says so.

Usage:
  python tools/reconcile_spread_calibration.py --mode reproduce
  python tools/reconcile_spread_calibration.py --mode sweep
  python tools/reconcile_spread_calibration.py --mode universe
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from typing import Dict, List

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

# The §J2 harness is the thing under test, so load it by path rather than
# re-implementing it -- a re-implementation would measure my understanding of
# §J2, not §J2. It now lives in tools/ because `.scratch/` is gitignored and the
# harness would otherwise be lost with the next clean.
_HARNESS = os.path.join(REPO, "tools", "spread_registry_ab.py")
if not os.path.exists(_HARNESS):  # the historical location, for an old checkout
    _HARNESS = os.path.join(REPO, ".scratch", "spread_ab.py")
_spec = importlib.util.spec_from_file_location("spread_registry_ab", _HARNESS)
assert _spec and _spec.loader, f"could not load the §J2 harness at {_HARNESS}"
spread_ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spread_ab)

TPS = [1.0, 1.5, 2.0, 2.5, 3.0]

#: §J2's own table, verbatim from `tools/spread_registry_ab.py`.
J2_CORRECTED: Dict[str, Dict[str, float]] = dict(spread_ab.CORRECTED)
J2_SYMBOLS: List[str] = list(J2_CORRECTED)


def measured_corrected() -> Dict[str, Dict[str, float]]:
    """Build the corrected table for all 20 symbols from the MEASURED spreads.

    `typical` becomes the measured median. `max` is raised to at least the
    measured p95, because the gate it feeds exists to reject an abnormal spread
    and the registry value is below what the market actually quotes -- leaving it
    would make the gate fire on ordinary bars, which is a different (and
    unintended) change from "use the real spread".
    """
    path = os.path.join(REPO, "reports", "spread_calibration_measured.json")
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    out: Dict[str, Dict[str, float]] = {}
    for r in rows:
        sym = str(r["symbol"]).upper()
        typ = float(r["median_pips_reg"])
        mx = max(float(r["registry_max"]), float(r["p95_pips_reg"]))
        out[sym] = {"typical_spread_pips": typ, "max_spread_pips": mx}
    return out


def _executed(cands):
    if cands is None or len(cands) == 0:
        return cands
    return cands[cands["decision"].astype(str).str.upper() == "EXECUTE"].copy()


def scan_universe(symbols: List[str], corrected: Dict[str, Dict[str, float]], tf: str, window: int,
                  use_cache: bool = True):
    """Scan each symbol under BOTH registries, ONCE.

    The scan is the expensive step (~1 min/symbol) and it does not depend on
    `tp_r` -- only the replay does. Hoisting it out of the tp sweep is what makes
    a 5-point sweep affordable: 2 scans per symbol instead of 10.

    The result is cached to disk because a re-scan is the dominant cost and the
    key fully determines it. The key includes a hash of the corrected table, so
    changing the table cannot silently reuse a stale scan.
    """
    import hashlib
    import pickle

    key = hashlib.sha256(json.dumps(
        {"s": sorted(symbols), "c": corrected, "tf": tf, "w": window}, sort_keys=True
    ).encode()).hexdigest()[:16]
    cache_path = os.path.join(REPO, ".scratch", f"j_scan_cache_{key}.pkl")

    if use_cache and os.path.exists(cache_path):
        try:
            with open(cache_path, "rb") as fh:
                scanned = pickle.load(fh)
            print(f"[cache] reusing {len(scanned)} scanned symbols from {os.path.basename(cache_path)}",
                  flush=True)
            return scanned
        except Exception as exc:  # a corrupt cache must not fail the run
            print(f"[cache] unusable ({exc}); rescanning", flush=True)

    scanned = {}
    for sym in symbols:
        df = spread_ab.load_bars(sym, tf, window)
        if df is None:
            print(f"[skip] {sym}: no bars", flush=True)
            continue
        spread_ab.apply_registry(None)
        base = spread_ab.scan(sym, df)
        spread_ab.apply_registry(corrected)
        corr = spread_ab.scan(sym, df)
        spread_ab.apply_registry(None)
        scanned[sym] = {
            "df": df,
            "ex_a": _executed(base.candidates), "ex_b": _executed(corr.candidates),
            "cand_a": int(len(base.candidates)) if base.candidates is not None else 0,
            "cand_b": int(len(corr.candidates)) if corr.candidates is not None else 0,
            "exec_a": int(base.executed), "exec_b": int(corr.executed),
        }
        print(f"  scanned {sym:8s} cand {scanned[sym]['cand_a']:5d}/{scanned[sym]['cand_b']:5d} "
              f"EXEC {scanned[sym]['exec_a']:4d}/{scanned[sym]['exec_b']:4d}", flush=True)

    if use_cache:
        try:
            with open(cache_path, "wb") as fh:
                pickle.dump(scanned, fh)
            print(f"[cache] wrote {os.path.basename(cache_path)}", flush=True)
        except Exception as exc:
            print(f"[cache] could not write ({exc})", flush=True)
    return scanned


def replay_all(scanned: Dict[str, dict], tp: float):
    """Replay the already-scanned candidate sets at one `tp_r`."""
    per: Dict[str, dict] = {}
    tot = {"n_a": 0, "n_b": 0, "r_a": 0.0, "r_b": 0.0,
           "exec_a": 0, "exec_b": 0, "cand_a": 0, "cand_b": 0}
    for sym, s in scanned.items():
        ra = spread_ab.replay(sym, s["df"], s["ex_a"], tp_r=tp)
        rb = spread_ab.replay(sym, s["df"], s["ex_b"], tp_r=tp)
        entry = {
            "cand_a": s["cand_a"], "cand_b": s["cand_b"],
            "exec_a": s["exec_a"], "exec_b": s["exec_b"],
            "n_a": ra["n"] if ra else 0, "n_b": rb["n"] if rb else 0,
            "r_a": ra["total_r"] if ra else 0.0, "r_b": rb["total_r"] if rb else 0.0,
            "mean_a": ra["mean_r"] if ra else 0.0, "mean_b": rb["mean_r"] if rb else 0.0,
        }
        per[sym] = entry
        for k in ("n_a", "n_b", "exec_a", "exec_b", "cand_a", "cand_b"):
            tot[k] += entry[k]
        tot["r_a"] += entry["r_a"]
        tot["r_b"] += entry["r_b"]
    return per, tot


def report(label: str, per: Dict[str, dict], tot: dict) -> dict:
    delta = tot["r_b"] - tot["r_a"]
    per_trade_a = tot["r_a"] / tot["n_a"] if tot["n_a"] else 0.0
    per_trade_b = tot["r_b"] / tot["n_b"] if tot["n_b"] else 0.0
    sign = "FAVOURABLE" if delta > 0 else ("ADVERSE" if delta < 0 else "NO CHANGE")
    print(f"\n=== {label} ===")
    print(f"  executed      : {tot['exec_a']} -> {tot['exec_b']}  (delta {tot['exec_b']-tot['exec_a']:+d})")
    print(f"  replay n      : {tot['n_a']} -> {tot['n_b']}")
    print(f"  total R       : {tot['r_a']:+.3f} -> {tot['r_b']:+.3f}   delta {delta:+.3f}  [{sign}]")
    print(f"  per-trade R   : {per_trade_a:+.5f} -> {per_trade_b:+.5f}")
    changed = [s for s, e in per.items() if abs(e["r_b"] - e["r_a"]) > 1e-9]
    print(f"  symbols changed: {len(changed)}/{len(per)}  {sorted(changed)}")
    return {"label": label, "delta_r": round(delta, 3), "sign": sign,
            "exec_a": tot["exec_a"], "exec_b": tot["exec_b"],
            "n_a": tot["n_a"], "n_b": tot["n_b"],
            "total_r_a": round(tot["r_a"], 3), "total_r_b": round(tot["r_b"], 3),
            "per_trade_a": round(per_trade_a, 5), "per_trade_b": round(per_trade_b, 5),
            "symbols_changed": sorted(changed), "per_symbol": per}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["reproduce", "sweep", "universe"], default="reproduce")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--window", type=int, default=183)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    results = []
    out_path = None
    if args.out:
        out_path = args.out if os.path.isabs(args.out) else os.path.join(REPO, args.out)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

    def _flush():
        """Write after every tp_r, so a long run yields partial results and a
        kill does not lose everything already measured."""
        if not out_path:
            return
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump({"mode": args.mode, "tf": args.tf, "window": args.window,
                       "tp_sweep": TPS, "results": results}, fh, indent=2, default=str)

    if args.mode == "reproduce":
        scanned = scan_universe(J2_SYMBOLS, J2_CORRECTED, args.tf, args.window)
        per, tot = replay_all(scanned, 1.5)
        results.append(report("§J2 REPRODUCE — 8 symbols, tp_r=1.5", per, tot))
        _flush()

    elif args.mode == "sweep":
        scanned = scan_universe(J2_SYMBOLS, J2_CORRECTED, args.tf, args.window)
        for tp in TPS:
            per, tot = replay_all(scanned, tp)
            results.append(report(f"§J2 symbols — tp_r={tp}", per, tot))
            _flush()

    elif args.mode == "universe":
        corr = measured_corrected()
        scanned = scan_universe(sorted(corr), corr, args.tf, args.window)
        for tp in TPS:
            per, tot = replay_all(scanned, tp)
            results.append(report(f"20 symbols, MEASURED spreads — tp_r={tp}", per, tot))
            _flush()

    spread_ab.apply_registry(None)

    if args.mode in ("sweep", "universe"):
        print("\n" + "=" * 72)
        print(f"SIGN STABILITY across the exit model ({args.mode})")
        print("=" * 72)
        for r in results:
            print(f"  {r['label']:44s} delta {r['delta_r']:+9.3f} R  {r['sign']}")
        signs = {r["sign"] for r in results}
        print(f"\n  distinct signs: {sorted(signs)}")
        print("  VERDICT: " + (
            "NOT ROBUST — the sign depends on the exit model, so neither number is a green light"
            if len(signs) > 1 else
            f"ROBUST across tp_r in {TPS} — {list(signs)[0]}"
        ))

    _flush()
    if out_path:
        print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

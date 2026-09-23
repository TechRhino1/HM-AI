"""Fetch 6 months (183d) of real MT5 history on every timeframe the three
trading modes consume.

The three styles differ ONLY by the timeframes they request:

    SWING        macro=D1 context=H4 primary=H1 setup=H4 timing=M15
    DAY_TRADING  macro=H4 context=H1 primary=M15 setup=H1 timing=M5
    SCALP        macro=H1 context=M15 primary=M5 setup=M5 timing=M1

Union = {M1, M5, M15, H1, H4, D1}.

Broker history depth is NOT uniform: M5/M15/M30/H1/H4/D1 reach the full window,
M1 is capped far shorter.  A short series is recorded honestly in the manifest
rather than being padded, interpolated or silently substituted.

Run:  python tools/fetch_multiframe_data.py [--days 183] [--symbols A,B] [--tf M5,H1]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

from jarvis.data.mt5_history import (  # noqa: E402
    DEFAULT_UNIVERSE,
    MT5HistoryFetcher,
    MT5UnavailableError,
    SyntheticDataError,
)

# Every timeframe any mode touches.
ALL_TIMEFRAMES: List[str] = ["M1", "M5", "M15", "H1", "H4", "D1"]

OUT = os.path.join("data", "multiframe_manifest.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=183)
    ap.add_argument("--symbols", default="", help="comma list; default = full registry universe")
    ap.add_argument("--tf", default="", help="comma list; default = all mode timeframes")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    # Prefer the registry (20 symbols) over DEFAULT_UNIVERSE (16) so WTI, US500,
    # EURJPY and GBPJPY get a chance to resolve too.
    try:
        from jarvis.data.symbol_registry import all_symbols  # type: ignore

        symbols = [s.canonical for s in all_symbols()]
    except Exception:
        symbols = list(DEFAULT_UNIVERSE)
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip().upper() for t in args.tf.split(",") if t.strip()] or ALL_TIMEFRAMES

    datasets, failures = [], []
    print(f"fetching {len(symbols)} symbols x {len(timeframes)} timeframes "
          f"({args.days}d) = {len(symbols) * len(timeframes)} series", flush=True)

    with MT5HistoryFetcher() as f:
        for sym in symbols:
            for tf in timeframes:
                tag = f"{sym:9s} {tf:4s}"
                try:
                    m = f.fetch_and_cache(sym, days=args.days, timeframe=tf)
                    m["requested_days"] = args.days
                    datasets.append(m)
                    cov = ""
                    if not m.get("full_window", True):
                        cov = f"  <-- SHORT (broker cap, {m.get('span_days')}d)"
                    print(f"  OK   {tag} rows={m['rows']:7d} "
                          f"{m['start'][:16]} -> {m['end'][:16]}{cov}", flush=True)
                except (MT5UnavailableError, SyntheticDataError) as e:
                    failures.append({"symbol": sym, "timeframe": tf, "error": str(e)})
                    print(f"  FAIL {tag} {e}", flush=True)
                except Exception as e:  # pragma: no cover
                    failures.append({"symbol": sym, "timeframe": tf,
                                     "error": f"{type(e).__name__}: {e}",
                                     "trace": traceback.format_exc()[-500:]})
                    print(f"  FAIL {tag} {type(e).__name__}: {e}", flush=True)

    # ── coverage summary: rows + span per symbol/timeframe ──────────────────
    coverage: Dict[str, Dict[str, dict]] = {}
    for d in datasets:
        coverage.setdefault(d["symbol"], {})[d["timeframe"]] = {
            "rows": d["rows"], "start": d["start"][:16], "end": d["end"][:16],
            "span_days": round(
                (__import__("pandas").Timestamp(d["end"]) -
                 __import__("pandas").Timestamp(d["start"])).total_seconds() / 86400, 1),
        }

    payload = {
        "requested_days": args.days,
        "timeframes": timeframes,
        "symbols": symbols,
        "series_ok": len(datasets),
        "series_failed": len(failures),
        "datasets": datasets,
        "coverage": coverage,
        "failures": failures,
    }
    os.makedirs("data", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    print()
    print(f"fetched {len(datasets)}/{len(symbols) * len(timeframes)} series, {len(failures)} failed")
    if failures:
        print("failures:")
        for x in failures:
            print(f"  {x['symbol']} {x['timeframe']}: {x['error'][:110]}")
    print(f"manifest -> {args.out}")
    return 0 if datasets else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Fetch real MT5 history for the full universe.

Run:  python tools/fetch_real_data.py [--days 365] [--symbols EURUSD,WTI]
"""
import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from jarvis.data.mt5_history import MT5HistoryFetcher, DEFAULT_UNIVERSE, MT5UnavailableError, SyntheticDataError

DEFAULT_DAYS = 95   # ~3 months of calendar time; override with --days


def main(days: int = DEFAULT_DAYS, symbols=None, out: str = None) -> int:
    universe = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else DEFAULT_UNIVERSE
    results, failed = [], []
    with MT5HistoryFetcher() as f:
        for sym in universe:
            try:
                m = f.fetch_and_cache(sym, days=days)
                results.append(m)
                print(f"  OK   {sym:9s} {m['broker_symbol']:13s} rows={m['rows']:5d} "
                      f"{m['start'][:10]} -> {m['end'][:10]}  "
                      f"weekend_bars={m['quality']['weekend_bars']:4d}")
            except (MT5UnavailableError, SyntheticDataError) as e:
                failed.append((sym, str(e)))
                print(f"  FAIL {sym:9s} {e}")
            except Exception as e:
                failed.append((sym, f"{type(e).__name__}: {e}"))
                print(f"  FAIL {sym:9s} {type(e).__name__}: {e}")

    print()
    print(f"fetched {len(results)}/{len(universe)} symbols, {len(failed)} failed")
    if failed:
        print("failures:")
        for s, e in failed:
            print(f"  {s}: {e}")

    out = out or os.path.join("data", f"real_universe_manifest_{days}d.json")
    os.makedirs("data", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"days": days, "count": len(results), "datasets": results,
                   "failures": [{"symbol": s, "error": e} for s, e in failed]}, fh, indent=2)
    print(f"manifest -> {out}")
    return 0 if results else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--symbols", default=None, help="comma-separated subset")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    raise SystemExit(main(days=a.days, symbols=a.symbols, out=a.out))

"""Fetch real 3-month H1 history for the full universe from MT5.

Run:  python tools/fetch_real_data.py
"""
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from jarvis.data.mt5_history import MT5HistoryFetcher, DEFAULT_UNIVERSE, MT5UnavailableError, SyntheticDataError

DAYS = 95   # ~3 months of calendar time


def main() -> int:
    results, failed = [], []
    with MT5HistoryFetcher() as f:
        for sym in DEFAULT_UNIVERSE:
            try:
                m = f.fetch_and_cache(sym, days=DAYS)
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
    print(f"fetched {len(results)}/{len(DEFAULT_UNIVERSE)} symbols, {len(failed)} failed")
    if failed:
        print("failures:")
        for s, e in failed:
            print(f"  {s}: {e}")

    out = os.path.join("data", "real_universe_manifest.json")
    os.makedirs("data", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"days": DAYS, "count": len(results), "datasets": results,
                   "failures": [{"symbol": s, "error": e} for s, e in failed]}, fh, indent=2)
    print(f"manifest -> {out}")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())

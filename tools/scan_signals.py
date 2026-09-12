"""
Scan the real-data universe and cache candidate signals.

Runs the production decision pipeline once per symbol over the real MT5 H1
history and writes the candidate table to ``data/signals/``. Calibration and the
final backtest both read this cache, so the expensive pass happens exactly once.

Usage::

    python tools/scan_signals.py                  # all symbols, 4 workers
    python tools/scan_signals.py --symbols EURUSD,XAUUSD
    python tools/scan_signals.py --workers 1
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jarvis.backtesting.signal_scan import SignalScanner  # noqa: E402
from jarvis.config.paths import DATA_DIR  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("scan_signals")

REAL_DIR = Path(DATA_DIR) / "market" / "real"
OUT_DIR = Path(DATA_DIR) / "signals"


def discover_symbols(days: int = 95) -> list[str]:
    if not REAL_DIR.exists():
        return []
    out = []
    for d in sorted(REAL_DIR.iterdir()):
        if d.is_dir() and any(d.glob(f"*_H1_{days}d.parquet")):
            out.append(d.name)
    return out


def parquet_for(symbol: str, days: int = 95) -> Path:
    return REAL_DIR / symbol / f"{symbol}_H1_{days}d.parquet"


def scan_one(symbol: str, days: int = 95) -> dict:
    """Scan a single symbol and persist its candidate table."""
    path = parquet_for(symbol, days)
    if not path.exists():
        return {"symbol": symbol, "ok": False, "error": f"missing {path}"}

    df = pd.read_parquet(path).reset_index(drop=True)
    scanner = SignalScanner(start_bar_idx=60)
    t0 = time.time()
    result = scanner.scan(df, symbol)
    elapsed = time.time() - t0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "" if days == 95 else f"_{days}d"
    out_path = OUT_DIR / f"{symbol}{suffix}_candidates.parquet"
    result.candidates.to_parquet(out_path, index=False)

    summary = result.summary()
    summary.update({
        "ok": True,
        "days": days,
        "seconds": round(elapsed, 1),
        "candidates_path": str(out_path.relative_to(REPO_ROOT)),
        "first_bar": str(result.candidates["time"].iloc[0]) if len(result.candidates) else None,
        "last_bar": str(result.candidates["time"].iloc[-1]) if len(result.candidates) else None,
    })
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default=None, help="comma-separated subset")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--days", type=int, default=95,
                    help="history window to read, matching the *_H1_<days>d.parquet cache")
    args = ap.parse_args()

    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols else discover_symbols(args.days)
    )
    if not symbols:
        logger.error(f"No symbols found under {REAL_DIR} for {args.days}d")
        return 2

    print(f"Scanning {len(symbols)} symbols ({args.days}d) with {args.workers} worker(s): "
          f"{', '.join(symbols)}")
    t0 = time.time()
    summaries: list[dict] = []

    if args.workers <= 1:
        for s in symbols:
            r = scan_one(s, args.days)
            summaries.append(r)
            print(f"  {s:8s} -> {r.get('candidates', 0):5d} candidates, "
                  f"{r.get('scanned', 0):5d} bars scanned, "
                  f"pipeline executed {r.get('executed_by_pipeline', 0)}  [{r.get('seconds', 0)}s]")
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(scan_one, s, args.days): s for s in symbols}
            for fut in as_completed(futures):
                s = futures[fut]
                try:
                    r = fut.result()
                except Exception as exc:  # pragma: no cover
                    r = {"symbol": s, "ok": False, "error": str(exc)}
                summaries.append(r)
                if r.get("ok"):
                    print(f"  {s:8s} -> {r.get('candidates', 0):5d} candidates, "
                          f"{r.get('scanned', 0):5d} bars scanned, "
                          f"pipeline executed {r.get('executed_by_pipeline', 0)}  [{r.get('seconds', 0)}s]")
                else:
                    print(f"  {s:8s} -> FAILED: {r.get('error')}")

    summaries.sort(key=lambda r: r.get("symbol", ""))
    manifest = {
        "generated_utc": pd.Timestamp.now("UTC").isoformat(),
        "elapsed_seconds": round(time.time() - t0, 1),
        "symbols": summaries,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "scan_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    ok = [r for r in summaries if r.get("ok")]
    total_candidates = sum(int(r.get("candidates", 0)) for r in ok)
    print(f"\nDone in {time.time()-t0:.0f}s. {len(ok)}/{len(symbols)} symbols, "
          f"{total_candidates} candidates total.")
    print(f"Manifest: {(OUT_DIR / 'scan_manifest.json').relative_to(REPO_ROOT)}")
    return 0 if len(ok) == len(symbols) else 1


if __name__ == "__main__":
    raise SystemExit(main())

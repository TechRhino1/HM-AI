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


def discover_symbols(days: int = 95, timeframe: str = "H1") -> list[str]:
    if not REAL_DIR.exists():
        return []
    out = []
    for d in sorted(REAL_DIR.iterdir()):
        if d.is_dir() and any(d.glob(f"*_{timeframe}_{days}d.parquet")):
            out.append(d.name)
    return out


def parquet_for(symbol: str, days: int = 95, timeframe: str = "H1") -> Path:
    return REAL_DIR / symbol / f"{symbol}_{timeframe}_{days}d.parquet"


def candidates_path(symbol: str, days: int = 95, timeframe: str = "H1") -> Path:
    """Cache path for a candidate table.

    The original H1/95d name (``<symbol>_candidates.parquet``) is preserved
    because calibration and the diagnostics read it; every other
    (timeframe, window) combination is namespaced so the per-mode tables cannot
    overwrite each other or the H1 one.
    """
    if timeframe == "H1" and days == 95:
        return OUT_DIR / f"{symbol}_candidates.parquet"
    return OUT_DIR / f"{symbol}_{timeframe}_{days}d_candidates.parquet"


def scan_one(symbol: str, days: int = 95, timeframe: str = "H1",
             since: str | None = None) -> dict:
    """Scan a single symbol and persist its candidate table.

    ``since`` trims the series before scanning. It exists because a mode's usable
    window is the intersection over its timeframes: SCALP's is capped by M1
    (~67d), so scanning the full 183d of M5 would spend most of its time
    producing candidates that the window filter then discards.
    """
    path = parquet_for(symbol, days, timeframe)
    if not path.exists():
        return {"symbol": symbol, "ok": False, "error": f"missing {path}"}

    df = pd.read_parquet(path).reset_index(drop=True)
    if since:
        df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
        df = df[df["time"] >= pd.Timestamp(since)].reset_index(drop=True)
    if len(df) < 100:
        return {"symbol": symbol, "ok": False,
                "error": f"only {len(df)} bars after --since {since}"}

    scanner = SignalScanner(start_bar_idx=60)
    t0 = time.time()
    result = scanner.scan(df, symbol)
    elapsed = time.time() - t0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = candidates_path(symbol, days, timeframe)
    result.candidates.to_parquet(out_path, index=False)

    summary = result.summary()
    summary.update({
        "ok": True,
        "days": days,
        "timeframe": timeframe,
        "seconds": round(elapsed, 1),
        "candidates_path": str(out_path.relative_to(REPO_ROOT)),
        "first_bar": str(result.candidates["time"].iloc[0]) if len(result.candidates) else None,
        "last_bar": str(result.candidates["time"].iloc[-1]) if len(result.candidates) else None,
    })
    return summary


def manifest_path(timeframe: str) -> Path:
    tf = str(timeframe).strip().upper()
    name = "scan_manifest.json" if tf == "H1" else f"scan_manifest_{tf}.json"
    return OUT_DIR / name


def write_manifest(timeframe: str, days: int, since: str | None, extra: dict) -> Path:
    """Write (or overwrite) the scan manifest for ``timeframe``.

    The manifest is published **before** the scan starts as well as after it.
    ``optimizer.manifest_since()`` replays ``since`` as an alignment-critical
    trim: candidate tables are indexed against whatever frame the scanner saw,
    so a ``since`` that does not match the current run shifts every ``bar_idx``
    onto the wrong bar. Candidate parquet files are written per symbol as they
    complete, while the manifest was previously written only at the very end —
    so during a long rescan the on-disk manifest still described the *previous*
    run while fresh tables were already being consumed. Publishing ``since`` up
    front keeps the two in step for the whole run. ``complete`` tells consumers
    whether the symbol list is trustworthy yet.
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_utc": pd.Timestamp.now("UTC").isoformat(),
        "timeframe": str(timeframe).strip().upper(),
        "days": days,
        "since": since,
    }
    payload.update(extra)
    path = manifest_path(timeframe)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default=None, help="comma-separated subset")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--days", type=int, default=95,
                    help="history window to read, matching the *_<tf>_<days>d.parquet cache")
    ap.add_argument("--timeframe", default="H1",
                    help="primary timeframe to scan (H1 for SWING, M15 for DAY_TRADING, M5 for SCALP)")
    ap.add_argument("--since", default=None,
                    help="ISO date; scan only bars at/after it (mode-window trimming)")
    args = ap.parse_args()

    timeframe = args.timeframe.strip().upper()
    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols else discover_symbols(args.days, timeframe)
    )
    if not symbols:
        logger.error(f"No symbols found under {REAL_DIR} for {args.days}d {timeframe}")
        return 2

    print(f"Scanning {len(symbols)} symbols ({args.days}d {timeframe}) with {args.workers} worker(s): "
          f"{', '.join(symbols)}")
    t0 = time.time()
    summaries: list[dict] = []

    # Publish the alignment-critical `since` before the first candidate table
    # lands on disk — see write_manifest().
    write_manifest(timeframe, args.days, args.since,
                   {"complete": False, "symbols": []})

    if args.workers <= 1:
        for s in symbols:
            r = scan_one(s, args.days, timeframe, args.since)
            summaries.append(r)
            print(f"  {s:8s} -> {r.get('candidates', 0):5d} candidates, "
                  f"{r.get('scanned', 0):5d} bars scanned, "
                  f"pipeline executed {r.get('executed_by_pipeline', 0)}  [{r.get('seconds', 0)}s]")
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(scan_one, s, args.days, timeframe, args.since): s
                       for s in symbols}
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
    mpath = write_manifest(timeframe, args.days, args.since, {
        "complete": True,
        "elapsed_seconds": round(time.time() - t0, 1),
        "symbols": summaries,
    })

    ok = [r for r in summaries if r.get("ok")]
    total_candidates = sum(int(r.get("candidates", 0)) for r in ok)
    print(f"\nDone in {time.time()-t0:.0f}s. {len(ok)}/{len(symbols)} symbols, "
          f"{total_candidates} candidates total.")
    print(f"Manifest: {mpath.relative_to(REPO_ROOT)}")
    return 0 if len(ok) == len(symbols) else 1


if __name__ == "__main__":
    raise SystemExit(main())

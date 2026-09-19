"""Operator tool: inspect or re-anchor a DrawdownGuard's baselines.

WHY THIS EXISTS
---------------
`DrawdownGuard` fails closed. It cannot tell a withdrawal from a crash — a flat
account that REALISED a 40% loss has `equity == balance` and is indistinguishable
from one where money was withdrawn — so it deliberately does not guess, and a big
drop is reported as the breach it is. The only supported way to move a baseline
down is `DrawdownGuard.reset_baselines()`, and until now nothing outside the test
suite called it.

That left a real dead end. The live account would not trade at all: its
`peak_equity` had been poisoned to 10150 by a *paper* session sharing the same
file, so a real equity of 777 read as a 92.34% drawdown against a 10% cap, and
every order was refused at `risk_engine.py:201`, `:405` and the orchestrator's
EXECUTE->WAIT backstop. The structural fix (mode-scoped files, see
`jarvis.config.paths.mode_scoped_db_path`) stops it recurring, but the *existing*
poisoned baseline still had to be re-anchored by an operator, deliberately.

USAGE
-----
    # inspect — never writes
    python tools/reset_risk_baseline.py --show

    # preview the change, still never writes
    python tools/reset_risk_baseline.py --equity 777.00

    # actually write it
    python tools/reset_risk_baseline.py --equity 777.00 --apply

Writes nothing unless `--apply` is passed, and refuses to write while a server is
listening on the API port unless `--force` is given: a *running engine* holds its
own in-memory baselines and will overwrite the file on its next save, so
re-anchoring underneath it silently does nothing.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis.config.paths import mode_scoped_db_path, resolve_db_path  # noqa: E402
from jarvis.risk.drawdown import DrawdownGuard  # noqa: E402

BASE_NAME = "jarvis_drawdown_state.db"


def _read_raw(db_path: str):
    """Read the persisted row without constructing a guard.

    Constructing a `DrawdownGuard` runs `_load_state()`, which may reset the daily
    baseline and SAVE — so `--show` must not do it. This is a read-only peek.
    """
    if not db_path or not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT daily_start_equity, peak_equity, last_saved_date FROM drawdown_state WHERE id = 1"
        )
        return cur.fetchone()
    finally:
        conn.close()


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", default="live", help="execution mode whose file to touch (default: live)")
    ap.add_argument("--equity", type=float, default=None,
                    help="re-anchor both baselines to this equity. Omit to preview only.")
    ap.add_argument("--show", action="store_true", help="print the persisted baselines and exit")
    ap.add_argument("--apply", action="store_true", help="actually write; without it nothing is modified")
    ap.add_argument("--force", action="store_true",
                    help="write even though something is listening on the API port")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("JARVIS_PORT", "8501")))
    args = ap.parse_args(argv)

    rel = mode_scoped_db_path(BASE_NAME, args.mode)
    if not rel:
        print(f"mode {args.mode!r} persists no drawdown state (backtest/offline). Nothing to do.")
        return 0
    db_path = resolve_db_path(rel)

    print(f"mode          : {args.mode}")
    print(f"database      : {db_path}")

    row = _read_raw(db_path)
    if row is None:
        print("state         : no persisted row (file missing or empty) — nothing to re-anchor")
        return 0

    daily, peak, saved = row
    print(f"daily_start   : {daily}")
    print(f"peak_equity   : {peak}")
    print(f"last_saved    : {saved}")

    if args.show:
        return 0

    if args.equity is None:
        print("\nNo --equity given: preview only. Pass --equity <n> [--apply] to re-anchor.")
        return 0

    if args.equity <= 0:
        print(f"\nRefusing: --equity must be positive (got {args.equity}).")
        return 2

    if peak and args.equity < peak:
        implied = (peak - args.equity) / peak * 100.0
        print(f"\nThis LOWERS peak_equity from {peak} to {args.equity} "
              f"({implied:.2f}% below the recorded peak).")
        print("That is only correct after a real deposit/withdrawal, or after a baseline")
        print("that another execution mode poisoned. If the account genuinely lost that")
        print("much, the breach is real and must NOT be re-anchored away.")

    print(f"\ndaily_start_equity -> {args.equity}")
    print(f"peak_equity        -> {args.equity}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to commit.")
        return 0

    if _port_in_use(args.host, args.port) and not args.force:
        print(f"\nRefusing to write: something is listening on {args.host}:{args.port}.")
        print("If that is a RUNNING ENGINE it holds its own in-memory baselines and will")
        print("overwrite this file on its next save, silently undoing the re-anchor.")
        print("Stop the engine and re-run, or pass --force if the listener is only the")
        print("dashboard (HM_dashboard.bat), which runs no engine.")
        return 3

    # Constructing the guard loads the row; reset_baselines then writes both
    # baselines and saves. This is the documented operator path.
    guard = DrawdownGuard(db_path=rel)
    guard.reset_baselines(args.equity)

    after = _read_raw(db_path)
    print(f"\nWritten. Row is now: {after}")
    if after and (after[0] != args.equity or after[1] != args.equity):
        print("WARNING: the persisted row does not match what was requested.")
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

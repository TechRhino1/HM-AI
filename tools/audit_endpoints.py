"""Cross-check every endpoint the UI calls against the running server.

A UI call that 404s is a dead button: the JavaScript asks for something the
server does not dispatch. This is the definitive test — it does not rely on
parsing either side correctly, it just asks the server.

Usage (from the repo root, with the server running):

    python tools/audit_endpoints.py                      # defaults to :8501
    python tools/audit_endpoints.py --base http://127.0.0.1:8599
    python tools/audit_endpoints.py --start-server       # boot one on :8599
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

JS_GLOBS = ["jarvis/ui/static/js/*.js"]
# Skip vendored libraries — they contain no first-party endpoint calls.
SKIP = {"lightweight-charts"}

# Endpoints that are POST-only. Probing these with GET returns 404 by design —
# the route exists but is not dispatched on GET — so the audit must use POST or
# it reports a working endpoint as dead.
POST_ONLY = {
    "/api/action/cancel_pending_order",
    "/api/action/close_all_positions",
    "/api/action/close_position",
    "/api/action/manual_trade",
    "/api/action/set_mode",
    "/api/action/set_trade_style",
    "/api/action/toggle_safe_mode",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/verify",
    "/api/backtest/cancel",
    "/api/backtest/run",
    "/api/copilot/ask",
}

# Endpoints that reach a live external provider (NSE / equity data). In a
# sandbox without outbound access these hang rather than fail, so they are
# reported separately instead of being mistaken for broken wiring.
EXTERNAL = {
    "/api/india/export_csv",
    "/api/india/heatmap",
    "/api/india/indices",
    "/api/india/options/single_signals",
    "/api/india/scanner",
    "/api/stocks/alerts",
}

# Endpoints that need a query parameter to be meaningful. Requesting them bare
# is expected to 400, not 404; the point is to prove the route is dispatched.
NEEDS_PARAMS = {
    "/api/candles": "symbol=EURUSD&timeframe=H1",
    "/api/radar": "symbol=EURUSD",
    "/api/history": "symbol=EURUSD",
    "/api/stocks/details": "symbol=AAPL",
    "/api/stocks/compare": "symbols=AAPL,MSFT",
    "/api/stocks/search": "q=a",
    "/api/india/details": "symbol=RELIANCE",
    "/api/india/search": "q=a",
    "/api/india/option_chain": "symbol=NIFTY",
    "/api/india/options/chain": "symbol=NIFTY",
    "/api/india/options/payoff": "symbol=NIFTY",
    "/api/india/options/single_signals": "symbol=NIFTY",
    "/api/india/scanner": "symbol=NIFTY",
}


def ui_endpoints() -> list[str]:
    found: set[str] = set()
    for pattern in JS_GLOBS:
        for path in glob.glob(os.path.join(ROOT, pattern)):
            if any(s in os.path.basename(path) for s in SKIP):
                continue
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for m in re.finditer(r"""["'`](/api/[A-Za-z0-9_/.\-]*)""", text):
                url = m.group(1)
                # Normalise template interpolation and trailing slashes.
                url = re.sub(r"\$\{[^}]*\}", "{X}", url)
                if url.endswith("/"):
                    continue
                found.add(url)
    return sorted(found)


def probe(base: str, path: str, method: str = "GET", timeout: int = 20) -> tuple[int, str]:
    url = base + path
    data = b"{}" if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, ""
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:160]
        except Exception:
            pass
        return exc.code, body
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8501")
    ap.add_argument("--start-server", action="store_true")
    ap.add_argument("--port", type=int, default=8599)
    args = ap.parse_args()

    proc = None
    base = args.base
    if args.start_server:
        base = f"http://127.0.0.1:{args.port}"
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT
        proc = subprocess.Popen(
            [sys.executable, "-c",
             "from jarvis.api.server import start_server; "
             f"start_server(host='127.0.0.1', port={args.port}, mt5_client=None).serve_forever()"],
            cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(6)

    try:
        endpoints = ui_endpoints()
        print(f"UI endpoints found : {len(endpoints)}")
        print(f"probing            : {base}")
        print("=" * 78)

        dead: list[tuple[str, int, str]] = []
        external: list[tuple[str, str]] = []
        ok = 0
        for path in endpoints:
            q = NEEDS_PARAMS.get(path, "")
            target = f"{path}?{q}" if q else path
            method = "POST" if path in POST_ONLY else "GET"
            # External-provider routes get a short leash: a sandbox with no
            # outbound access will hang on them, and that is not a wiring bug.
            timeout = 6 if path in EXTERNAL else 20
            status, body = probe(base, target, method=method, timeout=timeout)

            if path in EXTERNAL and status == -1:
                external.append((path, body))
                print(f"EXT   {path}  (needs a live external provider)")
            elif status == 404:
                dead.append((path, status, body))
                print(f"DEAD  {method} {path}")
                if body:
                    print(f"        {body[:120]}")
            elif status == -1:
                dead.append((path, status, body))
                print(f"ERR   {method} {path}  {body[:100]}")
            else:
                ok += 1
                print(f"  ok  {method} {path}  -> {status}")

        print("=" * 78)
        print(f"dispatched        : {ok}/{len(endpoints)}")
        print(f"external-provider : {len(external)}  (not a wiring fault)")
        print(f"dead/error        : {len(dead)}")
        return 1 if dead else 0
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())

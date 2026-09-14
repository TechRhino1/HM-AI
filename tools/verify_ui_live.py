"""Live end-to-end check of the redesigned console and the new API surface.

Starts the real HTTP server on a scratch port with no broker attached, then
exercises every new route the way a browser would.

Usage (from the repo root):

    python tools/verify_console_live.py

Exit code is 0 when every check passes, 1 otherwise, so it can gate a release.

Two things this script deliberately proves rather than assumes:

* ``/api/action/auto-select`` stays a dry run even when asked to go live. The
  HTTP surface must have no path that can open a trade.
* With no orchestrator attached, auto-selection reports ``UNAVAILABLE`` with a
  reason instead of returning an empty-but-successful selection. Failing safe
  and saying so is the correct behaviour; a 200 with no decisions would let a
  caller mistake "engine not wired" for "nothing to trade".
"""
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

# Run from anywhere: put the repo root on the path explicitly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis.api.server import start_server

PORT = 8599
BASE = f"http://127.0.0.1:{PORT}"

results = []


# Decimal literals that look like a market price or a monetary amount rather
# than a layout constant. Used to prove the dashboard does not bake values into
# its markup: a price rendered server-side is stale the moment it is sent.
_PRICE_RE = re.compile(r">\s*[-+]?\d{1,3}(?:,\d{3})*(?:\.\d{2,5})?\s*<")


def _static_market_values(html: str) -> list[str]:
    """Market-looking values sitting in element text rather than bound at runtime."""
    hits = []
    for m in _PRICE_RE.finditer(html or ""):
        literal = m.group(0).strip().strip("<>").strip()
        # Counters that start at zero are legitimate initial state, not data.
        if literal in {"0", "1", "100"}:
            continue
        hits.append(literal)
    return hits


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def request(path, payload=None, timeout=60):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def main():
    server = start_server(host="127.0.0.1", port=PORT, mt5_client=None, orchestrator=None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(1.5)

    try:
        # ── 1. Page routing ────────────────────────────────────────────────
        status, body = request("/")
        record(
            "GET / serves the advanced trading dashboard",
            status == 200 and "tt-app" in body and "dashboard.js" in body,
            f"status={status} bytes={len(body)}",
        )

        status, body = request("/dashboard.html")
        record(
            "GET /dashboard.html serves the same dashboard",
            status == 200 and "tt-app" in body,
            f"status={status} bytes={len(body)}",
        )

        # The dashboard must not bake market values into the markup. A price
        # rendered server-side would be stale the moment it left the process.
        status, body = request("/")
        leaks = _static_market_values(body)
        record(
            "dashboard HTML contains no hard-coded market values",
            status == 200 and not leaks,
            f"status={status} leaks={leaks[:6]}" if leaks else f"status={status} clean",
        )

        # Every template on disk, not only the ones served above. A fabricated
        # value in a page nobody checked is exactly how the legacy terminal came
        # to display 4380.00 for gold while the feed was down — the value was in
        # the markup, so it rendered before any request had been made.
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        tdir = os.path.join(repo_root, "jarvis", "ui", "templates")
        leaks_by_file = {}
        try:
            for name in sorted(os.listdir(tdir)):
                if not name.endswith(".html"):
                    continue
                with open(os.path.join(tdir, name), encoding="utf-8") as fh:
                    found = _static_market_values(fh.read())
                if found:
                    leaks_by_file[name] = found[:4]
        except OSError as exc:
            leaks_by_file = {"<unreadable>": [str(exc)]}
        record(
            "no template hard-codes a market value",
            not leaks_by_file,
            f"leaks={leaks_by_file}" if leaks_by_file else "all templates clean",
        )

        # The dashboard's timeframe selector was inert for a while: it sent
        # `timeframe=` while the handler read `tf=`, so every request silently
        # came back as H1 whatever the user picked. Assert that both spellings
        # are honoured AND that two different timeframes really do return
        # different series — accepting the parameter and then ignoring it would
        # pass a weaker check.
        series = {}
        for label, qs in (
            ("tf", "symbol=XAUUSD&tf=H1"),
            ("timeframe", "symbol=XAUUSD&timeframe=H1"),
            ("M5", "symbol=XAUUSD&tf=M5"),
        ):
            status, body = request("/api/candles?" + qs)
            try:
                payload = json.loads(body)
            except ValueError:
                payload = {}
            candles = payload.get("candles") or []
            series[label] = {
                "status": status,
                "tf": payload.get("timeframe"),
                "n": len(candles),
                "first": candles[0]["time"] if candles else None,
            }

        record(
            "GET /api/candles accepts both tf and timeframe",
            series["tf"]["tf"] == "H1" and series["timeframe"]["tf"] == "H1",
            f"tf={series['tf']['tf']} timeframe={series['timeframe']['tf']}",
        )
        record(
            "GET /api/candles honours the requested timeframe",
            series["M5"]["tf"] == "M5" and series["M5"]["first"] != series["tf"]["first"],
            f"H1 first={series['tf']['first']} M5 first={series['M5']['first']}",
        )

        # The chart surface is created at runtime, so a missing container is
        # invisible to every HTTP check above: the panel simply never draws.
        status, body = request("/dashboard")
        chart_ids = [
            "chart-live-price", "chart-legend", "chart-hud",
            "chart-tooltip", "chart-levels", "chart-timeframe",
        ]
        missing_ids = [i for i in chart_ids if f'id="{i}"' not in body]
        record(
            "the dashboard ships the full chart surface",
            status == 200 and not missing_ids,
            f"missing={missing_ids}" if missing_ids else "all present",
        )

        try:
            with open(os.path.join(repo_root, "jarvis", "ui", "static", "js", "dashboard.js"),
                      encoding="utf-8") as fh:
                dash_js = fh.read()
        except OSError as exc:
            dash_js = ""
            js_note = str(exc)
        else:
            js_note = ""
        record(
            "dashboard.js requests candles with the parameter the handler reads",
            "/api/candles?symbol=" in dash_js and "&tf=" in dash_js and "&timeframe=" not in dash_js,
            js_note or ("ok" if "&tf=" in dash_js else "no &tf= in the candles call"),
        )

        status, body = request("/api/pending_orders")
        record(
            "GET /api/pending_orders dispatches",
            status in (200, 503),
            f"status={status}",
        )

        status, body = request("/console")
        record(
            "GET /console serves the previous console",
            status == 200 and "cx-topbar" in body,
            f"status={status} bytes={len(body)}",
        )

        status, body = request("/classic")
        record(
            "GET /classic still serves the legacy terminal (rollback path)",
            status == 200 and "terminal.js" in body,
            f"status={status} bytes={len(body)}",
        )

        status, body = request("/classic.html")
        record(
            "GET /classic.html serves the same legacy terminal",
            status == 200 and "terminal.js" in body,
            f"status={status} bytes={len(body)}",
        )

        # The retired UI must be genuinely gone, not merely unrouted.
        status, _ = request("/static/js/app_shell.js")
        record(
            "retired app_shell.js is no longer served",
            status == 404,
            f"status={status}",
        )

        # ── 2. Assets ──────────────────────────────────────────────────────
        for asset, needle in (
            ("/static/css/theme_terminal.css", "tt-panel"),
            ("/static/css/console.css", "cx-topbar"),
            ("/static/js/dashboard.js", "JARVIS"),
            # console.js is an IIFE and deliberately exports no global, so match
            # a distinctive string from the file body instead.
            ("/static/js/console.js", "JARVIS Console"),
        ):
            status, body = request(asset)
            record(
                f"GET {asset}",
                status == 200 and needle in body,
                f"status={status} bytes={len(body)}",
            )

        # ── 3. Intelligence GET endpoints ──────────────────────────────────
        status, body = request("/api/intelligence/meta")
        ok = status == 200
        payload = {}
        if ok:
            try:
                payload = json.loads(body)
            except ValueError:
                ok = False
        record(
            "GET /api/intelligence/meta",
            ok,
            f"status={status} keys={sorted(payload)[:6]}",
        )

        status, body = request("/api/intelligence/reliability")
        ok = status == 200
        payload = {}
        if ok:
            try:
                payload = json.loads(body)
            except ValueError:
                ok = False
        # `styles` is a list of per-style records, not a mapping.
        styles = sorted(
            row.get("style") for row in (payload.get("styles") or []) if isinstance(row, dict)
        )
        weights = {
            row.get("style"): row.get("weight")
            for row in (payload.get("styles") or [])
            if isinstance(row, dict)
        }
        record(
            "GET /api/intelligence/reliability",
            ok and styles == ["DAY_TRADING", "SCALP", "SWING"],
            f"status={status} weights={weights}",
        )

        status, body = request("/api/intelligence/auto-selection")
        payload = {}
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {}
        # With no engine attached the endpoint must fail safe and say so, rather
        # than returning an empty-but-successful selection.
        record(
            "GET /api/intelligence/auto-selection fails safe without an engine",
            status == 503
            and payload.get("status") == "UNAVAILABLE"
            and bool(payload.get("error")),
            f"status={status} status_field={payload.get('status')!r}",
        )

        # ── 4. Backtest endpoints ──────────────────────────────────────────
        status, body = request("/api/backtest/meta")
        ok = status == 200
        payload = {}
        if ok:
            try:
                payload = json.loads(body)
            except ValueError:
                ok = False
        record(
            "GET /api/backtest/meta",
            ok,
            f"status={status} keys={sorted(payload)[:8]}",
        )

        status, body = request("/api/backtest/jobs")
        ok = status == 200
        record("GET /api/backtest/jobs", ok, f"status={status}")

        # ── Regime policy ──────────────────────────────────────────────────
        # Either answer is correct here and both are asserted: 200 with a policy
        # table once tools/optimise_regime.py has run, or 503 UNAVAILABLE with a
        # reason when it has not. What must never happen is a 404 (route not
        # wired) or a 500 (handler raised) — and a bare 200 with no policy would
        # let a caller mistake "never run" for "nothing tradeable".
        status, body = request("/api/backtest/regime-policy")
        payload = {}
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {}
        if status == 200:
            ok = payload.get("status") == "OK" and isinstance(payload.get("policy"), dict)
            detail = f"status={status} modes={sorted(payload.get('policy', {}))}"
        else:
            ok = status == 503 and payload.get("status") == "UNAVAILABLE" and bool(
                payload.get("error")
            )
            detail = f"status={status} status_field={payload.get('status')!r}"
        record("GET /api/backtest/regime-policy dispatches", ok, detail)

        # The style filter must not blow up on a missing parameter, and must not
        # return modes that were not asked for.
        status, body = request("/api/backtest/regime-policy?styles=SCALP")
        payload = {}
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {}
        returned = set((payload.get("policy") or {}).keys())
        record(
            "GET /api/backtest/regime-policy honours ?styles=",
            status in (200, 503) and returned <= {"SCALP"},
            f"status={status} returned={sorted(returned)}",
        )

        # ── 5. POST auto-select (dry run is hard-wired) ────────────────────
        status, body = request("/api/action/auto-select", {"dry_run": True})
        payload = {}
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {}
        # Same fail-safe as the GET: no engine, no selection, said out loud.
        record(
            "POST /api/action/auto-select fails safe without an engine",
            status == 503 and payload.get("status") == "UNAVAILABLE",
            f"status={status} status_field={payload.get('status')!r}",
        )

        # ── 6. Submit a real backtest job ──────────────────────────────────
        status, body = request(
            "/api/backtest/run",
            {
                "label": "live-check",
                "modes": ["SWING"],
                "symbols": ["EURUSD"],
                "objective": "expectancy_r",
                "min_trades": 10,
                "passes": 1,
                "max_evaluations": 12,
            },
            timeout=60,
        )
        payload = {}
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {}
        # A queued job is 202 Accepted, not 200.
        job_id = payload.get("job_id") or (payload.get("job") or {}).get("job_id")
        record(
            "POST /api/backtest/run queues a job (202 Accepted)",
            status == 202 and bool(job_id),
            f"status={status} job_id={job_id}",
        )

        if job_id:
            final = None
            for _ in range(60):
                status, body = request(f"/api/backtest/jobs/{job_id}")
                try:
                    final = json.loads(body)
                except ValueError:
                    final = {}
                state = (final.get("job") or final).get("status")
                if state in ("DONE", "FAILED", "CANCELLED"):
                    break
                time.sleep(2)
            state = (final.get("job") or final).get("status")
            record(
                "the job reaches a terminal state",
                state in ("DONE", "FAILED", "CANCELLED"),
                f"status={state}",
            )

            status, body = request(f"/api/backtest/jobs/{job_id}/result")
            ok = status in (200, 404)
            record(
                "GET /api/backtest/jobs/<id>/result responds",
                ok,
                f"status={status} bytes={len(body)}",
            )

        # ── 7. Attach a real orchestrator and re-check the live path ───────
        try:
            from jarvis.api.server import JarvisRequestHandler
            from jarvis.application.orchestrator import JarvisOrchestrator

            orchestrator = JarvisOrchestrator(
                symbols=["EURUSD"], mode="paper", trade_style="SWING"
            )
            JarvisRequestHandler.configure_orchestrator(orchestrator)

            status, body = request("/api/intelligence/meta")
            meta = json.loads(body) if status == 200 else {}
            record(
                "configure_orchestrator reaches the intelligence service",
                status == 200 and meta.get("orchestrator_attached") is True,
                f"status={status} attached={meta.get('orchestrator_attached')}",
            )

            status, body = request(
                "/api/intelligence/auto-selection?symbols=EURUSD&styles=SWING",
                timeout=240,
            )
            payload = {}
            try:
                payload = json.loads(body)
            except ValueError:
                payload = {}
            record(
                "GET /api/intelligence/auto-selection with an engine attached",
                status == 200 and payload.get("status") == "OK",
                f"status={status} field={payload.get('status')!r} "
                f"decisions={len(payload.get('decisions') or [])} "
                f"error={str(payload.get('error'))[:80]!r}",
            )

            # The HTTP path must never be able to open a trade.
            if status == 200:
                record(
                    "auto-selection is hard-wired to dry_run",
                    payload.get("dry_run") is True,
                    f"dry_run={payload.get('dry_run')!r}",
                )

            status, body = request(
                "/api/action/auto-select",
                {"dry_run": False, "symbols": ["EURUSD"], "styles": ["SWING"]},
                timeout=240,
            )
            payload = {}
            try:
                payload = json.loads(body)
            except ValueError:
                payload = {}
            record(
                "POST /api/action/auto-select ignores dry_run=False",
                status in (200, 503) and payload.get("dry_run") is True,
                f"status={status} dry_run={payload.get('dry_run')!r} "
                f"ignored={payload.get('requested_dry_run_ignored')!r}",
            )
        except Exception as exc:  # noqa: BLE001 - report, do not mask
            record(
                "attach a real orchestrator and re-check the live path",
                False,
                f"{type(exc).__name__}: {exc}",
            )

        # ── 8. Unknown route still 404s ────────────────────────────────────
        status, _ = request("/api/backtest/nonsense")
        record("unknown backtest route 404s", status == 404, f"status={status}")

    finally:
        server.shutdown()
        server.server_close()

    failures = [r for r in results if not r[1]]
    print()
    print(f"{len(results) - len(failures)}/{len(results)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

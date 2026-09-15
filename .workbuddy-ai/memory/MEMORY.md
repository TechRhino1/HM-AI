# HM-AI / JARVIS — durable project notes

Curated facts that outlive a session. Day-to-day detail lives in `YYYY-MM-DD.md`.

## Working in this environment

* The sandbox refuses writes outside the project dir. Keep backups/scratch inside `D:\HM_AI\HM-AI`.
* Use the Bash tool, not PowerShell (returns no output; `tasklist`/`Get-Process` blocked).
* Python is managed 3.13.12 on PATH. No project venv; `pytest`, `pandas`, `numpy` import directly.
* `rm -rf X && cmd` silently swallows the command's stdout — run the `rm` separately.

## Data-loss hazard (unresolved, 3x, all in the small hours)

| When | What vanished | Preceded by |
|---|---|---|
| 2026-09-13 ~03:28 | whole `jarvis/` package | `git rm` |
| 2026-09-14 03:50:52 | whole `jarvis/` package | `git rm` |
| 2026-09-15 ~05:31 | **git object store** — all `.pack` files, `refs/heads/main`, `refs/remotes/origin/main` | `git stash push` |

Cause unknown (AV/EDR quarantine and cloud-sync clients are the leading hypotheses; hooks,
`fsmonitor`, automations, disk pressure, repo scripts all ruled out). **`.git/` is not safe** — the
packfiles died while the working tree survived untouched.

Rules:
* **Commit and push early.** The remote is the only durable store; only uncommitted work is at risk.
* **Never `git stash` here.** To compare old vs new behaviour, use
  `git archive HEAD <path> | tar -x -C .scratch/oldtree`.
* Working tree lost → `git checkout -- jarvis/` (from the *index*, preserves staged deletions). Not
  `git checkout HEAD -- jarvis/`.
* Object store lost (the usual advice fails when `HEAD` itself is unreadable):
  `tail .git/logs/HEAD` → `git cat-file -t <sha>` to confirm it's gone → `git fetch origin` →
  `git update-ref refs/heads/main <sha>` + `git update-ref refs/remotes/origin/main <sha>`.
  A fetch recreates `origin/main` but **not** the local branch, so step 4 is required.
* Refresh the history bundle: `git bundle create .git/backup/repo-<ts>.bundle --all`. Single file,
  has survived all three incidents.

## Pushing to GitHub — a plain `git push` hangs

`git push` stalls silently (~40s+) because `helper-selector` sits first in the credential chain and
blocks before GCM runs. `git credential fill` hanging at rc=124 confirms it; `git ls-remote`
succeeding proves only the write path is broken. Always bypass the selector:

```bash
GCM="C:/Users/Itrai/.workbuddy-ai/binaries/PortableGit/versions/1.2.0/mingw64/bin/git-credential-manager.exe"
timeout 180 git -c credential.helper= -c credential.helper="!$GCM" push origin main > /tmp/pushout.txt 2>&1
echo "exit=$?"; cat /tmp/pushout.txt
```

Redirect to a file and read `$?` (piping masks the exit code); verify the remote SHA with
`git ls-remote` rather than trusting the local message. Remote:
`https://github.com/TechRhino1/HM-AI.git`. Setting upstream does **not** remove the need for this.
Backticks in a `git commit -m` argument get eaten by bash — use plain text.

## Conventions worth knowing

* Backtest job statuses: `QUEUED | RUNNING | DONE | FAILED | CANCELLED` — `DONE`, not `COMPLETED`.
  `POST /api/backtest/run` answers **202** with a `job_id`.
* Localhost requests authenticate as admin (`_is_local_request()`), so local curl needs no token.
  `/api/intelligence/*` and `/api/backtest/*` are deliberately not public GETs.
* `normalise_style()` maps any unrecognised style to `SWING` — a typo becomes a swing scan. Validate
  upstream. Style → primary timeframe: SWING→H1, DAY_TRADING→M15, SCALP→M5.
* Console UI: `/` and `/dashboard` → `dashboard.html`; `/console` → `console.html`; `/classic` →
  `index.html`. `console.js`/`dashboard.js` are IIFEs with no exported global.
* `_csv()` in `intelligence_api.py` returns `None`, not `[]`, for an absent param — use
  `set(_csv(q,"x") or [])`.
* The **broker** symbol is what the engine resolves, not the canonical one: `resolve("WTI")` is a
  registry key, `resolve("OILCash#")` can miss `_ALIAS_MAP` and silently fall back to a generic FX
  spec. Guarded by `tests/test_symbol_registry.py`.
* `max_evaluations` is a budget **per search**, not per optimiser (`_budget_start`/`_reset_budget`).
* Cross-style consensus: `jarvis/intelligence/mode_aggregator.py`. One style voting alone is never
  tradeable by construction. Measured weights: SWING 0.346, DAY_TRADING 0.1064, SCALP 0.1287 (all
  below neutral — all three lost money over the 6-month window).
* Regime-conditioned optimisation: `jarvis/backtesting/regime_optimizer.py` (`python
  tools/optimise_regime.py`, served at `/api/backtest/regime-policy`). Conditioning is free —
  simulations cache on geometry alone. Its disable thresholds deliberately duplicate
  `winrate_targeting.regime_edge_table`; a test enforces they agree.
* **The quote fallback must never call the profile hydrators.** `TradingViewDataProvider.fetch_quotes()`
  used to read its fallback from `get_india_profile()`/`get_stock_profile()`, which hydrate, and
  hydration calls back into `fetch_quotes()` — unbounded (233 re-entries for one NIFTY lookup) and
  silent, because `hydrate_batch` wraps it in `except Exception`. Result: `/api/india/*` never
  answered for any index and returned the last-resort 150.0 instead of 24175.65. Read
  `INDIA_UNIVERSE`/`STOCK_UNIVERSE` directly. Guarded by `tests/test_provider_recursion.py`.
* **Never seed a modelled value from `hash()`** — CPython salts it per process. Use
  `jarvis.data.determinism.stable_seed`. Eight sites did this (option-chain skew/OI/volume, the
  options `pcr` that picks BUY CALL vs BUY PUT, candle walks, Monte Carlo seed, and user-facing
  earnings dates, IV, `is_fno_ban`).
* **Verification entry points:** `tools/verify_ui_live.py` (44 live HTTP checks),
  `tools/verify_dashboard_render.js` (88 headless render checks), `tools/verify_dashboard_nav.js`
  (31 live checks, exits 2 when it cannot run), `tools/audit_wiring.py`, `tools/audit_endpoints.py`,
  plus `tests/test_mode_aggregator.py`, `test_backtest_optimizer.py`, `test_regime_optimizer.py`,
  `test_ui_wiring.py`, `test_provider_recursion.py`.

## Testing traps (each of these has produced a test that passes against the bug)

* A test that only looks for a raised exception misses unbounded recursion when an intermediate
  frame swallows it — **pin the call** (patch the callee with a recorder).
* `hash()` salting is constant *within* a process, so "call twice and compare" passes. The
  discriminating test must spawn a subprocess under a different `PYTHONHASHSEED`.
* Clear both caches in `setUp` — `_quote_cache` has a 15s TTL and a sibling test warming it made a
  re-entry test pass against broken code.
* Plain `pytest -q` exits 1 *after all tests pass* (a safe-delete hook blocks temp-dir cleanup) —
  use `--basetemp=.scratch/pttmp`.

## A self-retriggering MutationObserver freezes the page with no error

`setAttribute` queues a mutation record even when the value is unchanged, so an observer whose
callback writes an attribute inside its own `subtree` + `attributeFilter` re-queues itself forever;
microtasks drain before the browser may paint or dispatch input, so the main thread blocks
**permanently and silently** — no exception, no console error. It looks like a crash.

Rule: **an observer callback must write nothing it observes** — compare before writing
(`setIfChanged`) plus a re-entrancy flag. Deleting the observer is not a fix; assert the feature
still works, or "delete the feature" passes your test. It only fires on *interaction* when the
initial sync ran before the observer was attached, which is why an idle page looks healthy. Two live
examples were in `hm_ui.js` (tab ARIA sync); the other two observers there are safe.

## Diagnosing a blocked browser main thread

* `page.evaluate` ignores its own `timeout` — always race it against a timer.
* Run a control phase first: load, don't interact, probe 30s. Blocks while idle ⇒ poll loop; fine
  until a click ⇒ the click handler.
* `Debugger.enable` + `Debugger.pause` names the blocking frame (pause ⇒ JS, read `callFrames`; no
  pause ⇒ native, e.g. dialog or sync XHR).
* Chrome at `C:\Program Files\Google\Chrome\Application\chrome.exe`; `puppeteer-core` in the
  managed node workspace (set `PUPPETEER_ROOT`/`NODE_PATH`; launch `--no-sandbox --disable-gpu
  --disable-dev-shm-usage`). The `agent-browser` skill does **not** support Windows.
* `node --check` proves a file parses and says nothing about a blocked thread.

## Dashboard rail facts

* `POST /api/telemetry_state`'s `account` object already carries `login`, `name`, `server`,
  `company`, `balance`, `equity`, `margin`, `free_margin`, `margin_level`, `leverage`, `profit`,
  `currency`, `trade_allowed`, `last_sync_time` — the account dropdown needs no server change.
* Canonical destinations/labels (from `stocks.html`/`india.html`): `/` Forex·Crypto, `/stocks` US
  Stocks, `/india` India Stocks, `/options` India Options. `dashboard.html` carries them in a
  `.tt-dropdown`; `markActiveNav()` in `hm_ui.js` styles `.market-nav-item`.
* `.tt-rail` is a **top bar** (`grid-area: rail`, sticky), not a sidebar; the account strip is pushed
  right by `.tt-rail__spacer`.
* `[hidden]` is a weak UA rule — any `display` you declare on a panel beats it. Declare
  `.panel[hidden] { display: none; }` explicitly.

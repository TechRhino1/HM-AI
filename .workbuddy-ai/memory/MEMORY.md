# HM-AI / JARVIS — durable project notes

Curated facts that outlive a session. Day-to-day detail lives in `YYYY-MM-DD.md`.

## Working in this environment

* The sandbox refuses writes outside the project dir. Keep backups/scratch inside `D:\HM_AI\HM-AI`.
* Use the Bash tool, not PowerShell (returns no output; `tasklist`/`Get-Process` blocked).
* Python is managed 3.13.12 on PATH. No project venv; `pytest`, `pandas`, `numpy` import directly.
* `rm -rf X && cmd` silently swallows the command's stdout — run the `rm` separately.
* **`py-spy` is installed** in the managed venv
  (`…/python/envs/default/Scripts/py-spy.exe dump --pid <pid>`) and works on Windows against a
  3.13 process. It attaches **without restarting**, which is the whole point: restarting destroys
  the wedged state you are trying to inspect. `netstat -ano | grep 8501` gives the pid.
* Running a script *by path* puts the **script's** dir on `sys.path`, not the cwd — `.scratch/*.py`
  needs `sys.path.insert(0, <repo root>)` or `import jarvis` fails.

## Data-loss hazard (unresolved, 3x, all in the small hours)

| When | What vanished | Preceded by |
|---|---|---|
| 2026-09-13 ~03:28 | whole `jarvis/` package | `git rm` |
| 2026-09-14 03:50:52 | whole `jarvis/` package | `git rm` |
| 2026-09-15 ~05:31 | **git object store** — all `.pack` files, `refs/heads/main`, `refs/remotes/origin/main` | `git stash push` |

Cause unknown (AV/EDR quarantine and cloud-sync clients are the leading hypotheses; hooks,
`fsmonitor`, automations, disk pressure, repo scripts all ruled out). **`.git/` is not safe** — the
packfiles died while the working tree survived untouched.

Observed 2026-09-15 23:2x: **remote-tracking refs are wiped the moment they are written.** `git fetch
origin` printed `* [new branch] main -> origin/main` and exited 0, but `refs/remotes/origin/main` was
gone immediately after. `git update-ref refs/remotes/origin/main <sha>` also exits 0 and writes
nothing. Writing the file by hand (`mkdir -p .git/refs/remotes/origin` + `printf`) works and
`git rev-parse origin/main` resolves — until it too disappears. `refs/heads/main` and plain files in
`.git/` are unaffected; only `.git/refs/remotes/` is targeted. Consequences: upstream tracking
cannot be persisted, so `git status` will keep reporting `[gone]` — ignore it and push with an
explicit refspec (`git push origin main:refs/heads/main`) rather than relying on `-u`.

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

## The live server does not hot-reload — restart it after a fix

Started as an inline `python -c "…start_server(host='127.0.0.1', port=8501, mt5_client=None,
orchestrator=None)"` from the repo root, under `ThreadingHTTPServer`. **Edits on disk do nothing
until the process is restarted.** A long-running instance therefore keeps executing whatever the
code said when it booted — and can look like a live bug when it is really a stale one.

**The diagnostic rule:** a hang that does *not* reproduce in a fresh interpreter is a
stale-process problem, not a logic problem. Compare the two before reading any code. Worked example:
`get_indices_snapshot()` returned in 0.88s in a fresh process while `/api/india/indices` hung past
240s in the server. `py-spy` showed the server 997 frames deep in a recursion that commit `2c655c6`
had already removed — the dump was executing a line that is a *comment* in the working tree. Fix
was a restart; no code change.

Symptom that gives it away: the three index-bearing India routes hang while everything else is
healthy. `netstat -ano | grep 8501` showing `CLOSE_WAIT` on the server side means a handler thread
is still stuck and never closed its socket.

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
  **Note the failure mode:** a bare `except Exception` around a hydrating call turns a
  `RecursionError` into a silent ~1000-second stall. The fix landed in `2c655c6` but the server ran
  the old code for hours — see "The live server does not hot-reload" above.
* **Never seed a modelled value from `hash()`** — CPython salts it per process. Use
  `jarvis.data.determinism.stable_seed`. Eight sites did this (option-chain skew/OI/volume, the
  options `pcr` that picks BUY CALL vs BUY PUT, candle walks, Monte Carlo seed, and user-facing
  earnings dates, IV, `is_fno_ban`).
* **Verification entry points:** `tools/verify_ui_live.py` (44 live HTTP checks),
  `tools/verify_dashboard_render.js` (88 headless render checks), `tools/verify_dashboard_nav.js`
  (31 live checks, exits 2 when it cannot run), `tools/audit_wiring.py`, `tools/audit_endpoints.py`,
  plus `tests/test_mode_aggregator.py`, `test_backtest_optimizer.py`, `test_regime_optimizer.py`,
  `test_ui_wiring.py`, `test_provider_recursion.py`.
* **`docs/MARKETS_DATA_CONTRACTS.md`** — response shapes for the ten stocks/India endpoints the
  Markets view binds to, with the timeout token (`fast` 8s / `normal` 15s / `provider` 30s /
  `slow` 60s, from `dashboard.js:46`) and JS consumer for each. Two traps recorded there:
  `/api/stocks/screener` returns `count=47` with only 40 rows under `limit=40` (**`count` is the
  matching total, not `len(stocks)`**), and `/api/stocks/news` + `/api/stocks/recommended_buys`
  have **no UI consumer at all** — the grid renders `ai_recommended_buys` from the screener payload.

## The entry signal has no measured edge (audited 2026-09-15, 183d real bars, 20 symbols)

Profit factor **0.568–1.202** on SWING/H1; **1 of 40** symbol×target combos reaches 1.3. Win rate
28.2–44.8% where a 1.5R target needs 40.0%. At the realised ~1% risk/trade, drawdown is 62–100%;
holding DD to 10% needs **0.012–0.108% risk per trade**, below the broker minimum lot on most
symbols. Ruled out by measurement: costs (free execution still loses), exit geometry (best == seed
in every mode), target width (PF is invariant to tp), candidate ranking (no score quantile reaches
break-even). What remains is the directional call.

* Directional call = a 7-branch if/elif on `choch`/`bos`/`trend_score`
  (`decision_engine.py:105-122`). The gate "probability" is `0.45 ×` a hand-typed 6-bin table that
  *inflates* inputs (`confidence.py:13-20`) `+ 0.55 ×` an **untrained** prior clipped to [0.35,0.88]
  (`online_ml_predictor.py:564-573,417`). 55% of the gate weight has never seen a trade.
* The meta-label gate is inert in every backtest — `recent_candles` is never passed and the model
  loads `None` offline (`decision_engine.py:1033-1045`). **Left inert on purpose, measured:**
  trained on 62k samples with purged/embargoed splits, test AUC is **0.481** (train 0.746) with the
  shipped features and 0.479 (train 0.783) after adding the primary model's own outputs; selecting
  the top decile by P(win) *lowers* the win rate (0.341 vs 0.359 base). Horizon sweep 5/10/20/40 →
  test AUC 0.527/0.507/0.508/0.507. Meta-labelling needs information the primary model lacks
  (cross-asset, order flow, session); the 14 shipped features are the same 30-bar window. Evidence
  recorded in `meta_labeler.py::_load`. Tools: `tools/train_meta_labeler.py`,
  `tools/audit_meta_gate.py`.
* `signal_engine.py` (`REGIME_WEIGHTS`) is **dead code** — never imported anywhere. Don't cite it.
* Sizing realises 1.5–2.6× nominal risk: the quarter-Kelly term pins at its 1.50 cap, so it is a
  constant (`position_sizing.py:56`). `engine.py:711` applies the lot floor *after* the risk cap.
* Best regime is **COMPRESSION** (PF 1.203); worst is **TREND_BULL** (0.872). A "TREND_FOLLOWING"
  strategy that loses most in trends is a mean-reversion signal with the wrong label.
* Backtest costs: commission $0 (the `commission_per_lot=5.0` argument is dead), spread on BUY
  entry only, slippage on stop exits only, no swap, flat 2.0 pips misprices BTCUSD by 750×.
* Two registries both fall back silently to a generic FX spec; `GER40`/`UK100` are then sized
  **100,000×** wrong. `XAGUSD` ×20 wrong.
* **The H4/D1 resample look-ahead is real but measured ~zero impact** (Δ 0.0000R over 1,200 bars on
  3 symbols) — `market_structure.py:29` needs 5 confirming bars per pivot, so the contaminated
  newest bucket never forms one. Fix as a landmine; don't expect P&L to move.
* **The M5/SCALP candidate set is truncated to bar 60–14,398 of 37,440.** Replaying it gives
  PF 1.09–1.74 — the opposite sign to the live optimiser. Every SCALP number is unsafe.
* **Under an honest cost model only WTI (+0.087R) and XAUUSD (+0.042R) stay positive.** Correcting
  costs drains 0.162R/trade on average (median 0.118) and scales with spread ÷ stop distance, so
  it is brutal for SOLUSD (−0.69R) and trivial for XAUUSD (−0.0075R). GER40 (+0.044R) and NAS100
  (+0.015R) both go negative — a "3–5 symbol salvageable band" is an artefact of the broken
  cost model. PF ≥ 1.3 is reachable by no symbol at tp=1.5; WTI's 1.202 is the ceiling.
* A fabricated XAUUSD series is still on the default load path (`acquisition.py:73` →
  `"MT5_DefaultBroker"`); 4,320 rows, 1,216 weekend bars, spread ≡ 0.

Audit tools: `tools/audit_trade_quality.py`, `tools/audit_verdict.py`, `tools/audit_lookahead.py`,
`tools/build_audit_report.py` (→ `reports/trade_plan_audit.html`).

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

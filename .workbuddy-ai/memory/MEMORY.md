# HM-AI / JARVIS — durable project notes

Curated facts that outlive a session. Day-to-day detail lives in `YYYY-MM-DD.md`.
**Keep this file small** — it is injected every session and silently truncated at the tail.

## Git: push hangs; remote refs are eaten

`git push` stalls ~40s+ because `helper-selector` sits first in the credential chain. Bypass it:

```bash
GCM="C:/Users/Itrai/.workbuddy-ai/binaries/PortableGit/versions/1.2.0/mingw64/bin/git-credential-manager.exe"
timeout 180 git -c credential.helper= -c credential.helper="!$GCM" push origin main > /tmp/pushout.txt 2>&1
echo "exit=$?"; cat /tmp/pushout.txt
```

Redirect to a file (piping masks `$?`); verify with `git ls-remote`. Remote
`https://github.com/TechRhino1/HM-AI.git`. Backticks in `-m` are eaten by bash.

**`.git/refs/remotes/*` is wiped immediately after being written here.** `git fetch` /
`git update-ref` exit 0 and write nothing; only hand-writing the file works, and it too vanishes.
So `git status` will always report `[gone]` — ignore it, never rely on `-u`, push an explicit
refspec (`main:refs/heads/main`). Setting upstream does not help.

## Data-loss hazard (unresolved, 3x, all in the small hours)

| When | What vanished | Preceded by |
|---|---|---|
| 2026-09-13 ~03:28 | whole `jarvis/` | `git rm` |
| 2026-09-14 03:50:52 | whole `jarvis/` | `git rm` |
| 2026-09-15 ~05:31 | **object store** — all `.pack`, `refs/heads/main` | `git stash push` |

Cause unknown (AV/EDR quarantine and cloud-sync are the leading hypotheses; hooks, `fsmonitor`,
automations, disk pressure, repo scripts all ruled out). **`.git/` is not safe.**

* **Commit and push early** — the remote is the only durable store.
* **Never `git stash`.** Compare old vs new with
  `git archive HEAD <path> | tar -x -C .scratch/oldtree`.
* Working tree lost → `git checkout -- jarvis/` (from the *index*; preserves staged deletions).
  Not `git checkout HEAD -- jarvis/`.
* Object store lost: `tail .git/logs/HEAD` → `git cat-file -t <sha>` to confirm → `git fetch origin`
  → `git update-ref refs/heads/main <sha>`. A fetch recreates `origin/main` but **not** the local
  branch, so that last step is required.
* Refresh `git bundle create .git/backup/repo-<ts>.bundle --all` — one file, survived all 3.

## The live server does not hot-reload — restart after a fix

Started inline from the repo root under `ThreadingHTTPServer`; **edits on disk do nothing until the
process restarts.** Diagnostic rule: *a hang that does not reproduce in a fresh interpreter is a
stale process, not a logic bug* — compare the two before reading code. (`get_indices_snapshot()`
took 0.88s fresh vs >240s in the server; `py-spy` caught it 997 frames deep in recursion that a
commit had already deleted.)

`py-spy` is installed (`…/python/envs/default/Scripts/py-spy.exe dump --pid <pid>`) and attaches
**without restarting** — restarting destroys the wedged state you want to inspect.
`netstat -ano | grep 8501` for the pid; `CLOSE_WAIT` server-side = a handler thread is stuck.

## The entry signal has no measured edge (audited 2026-09-15, 183d real bars, 20 symbols)

PF **0.568–1.202** on SWING/H1; **1 of 40** symbol×target combos reaches 1.3. Win rate 28.2–44.8%
where 1.5R needs 40.0%. At ~1% risk/trade, DD is 62–100%; holding DD ≤10% needs **0.012–0.108%
risk per trade**, below the broker minimum lot on most symbols. Ruled out by measurement: costs
(free execution still loses), exit geometry (best == seed), target width (PF invariant to tp),
ranking (no quantile breaks even), learned filter (AUC 0.481). What remains is the directional call.

* Directional call = 7-branch if/elif on `choch`/`bos`/`trend_score` (`decision_engine.py:105-122`).
  Gate "probability" = `0.45 ×` a hand-typed 6-bin table that *inflates* inputs
  (`confidence.py:13-20`) `+ 0.55 ×` an **untrained** prior clipped to [0.35,0.88]
  (`online_ml_predictor.py:564-573,417`). 55% of the gate weight has never seen a trade.
* Meta-label gate stays **inert on purpose**: test AUC 0.481 (train 0.746) and 0.479 (train 0.783)
  after adding the primary model's outputs; top-decile selection *lowers* win rate (0.341 vs 0.359).
  Horizon sweep 5/10/20/40 → 0.527/0.507/0.508/0.507. It needs information the primary lacks
  (cross-asset, order flow, session). Evidence recorded in `meta_labeler.py::_load`.
* `signal_engine.py` (`REGIME_WEIGHTS`) is **dead code** — never imported. Don't cite it.
* Sizing realises 1.5–2.6× nominal: the quarter-Kelly term pins at its 1.50 cap, so it is a
  constant (`position_sizing.py:56`); `engine.py:711` applied the lot floor *after* the risk cap.
* Best regime is **COMPRESSION** (PF 1.203); worst **TREND_BULL** (0.872). A "trend-following"
  strategy that loses most in trends is mean reversion with the wrong label.
* **Under honest costs only WTI (+0.087R) and XAUUSD (+0.042R) stay positive.** Correcting costs
  drains 0.162R/trade (median 0.118), scaling with spread ÷ stop distance — brutal for SOLUSD
  (−0.69R), trivial for XAUUSD (−0.0075R). GER40 and NAS100 both go *negative*; the earlier "3–5
  symbol salvageable band" was an artefact of the broken cost model. **PF ≥1.3 is reachable by no
  symbol**; WTI's 1.202 is the ceiling.
* H4/D1 resample look-ahead is real but **measured ~zero impact** (Δ 0.0000R, 1,200 bars, 3 symbols)
  — `market_structure.py:29` needs 5 confirming bars per pivot so the newest contaminated bucket
  never forms one. Fix as a landmine; don't expect P&L to move.
* **M5/SCALP candidates are truncated to bar 60–14,398 of 37,440** → replaying gives PF 1.09–1.74,
  the opposite sign to the live optimiser. Every SCALP number is unsafe until re-scanned.

Audit tools: `tools/audit_trade_quality.py`, `audit_verdict.py`, `audit_lookahead.py`,
`audit_meta_gate.py`, `train_meta_labeler.py`, `build_audit_report.py`
(→ `reports/trade_plan_audit.html`).

## Live-execution defects found 2026-09-16 (post-restart, account 101059540)

* **Account is named "Demo Account"** in MT5 (`/api/diagnostics`) — LIVE mode runs live logic, but
  against demo money. Confirm in-terminal before assuming real capital is at risk.
* **Sizing happens before the broker's minimum stop distance is applied, and lots are never
  recomputed.** Sizer sold 0.69 EURUSD against a 0.7-pip stop (0.61% risk); `mt5_client.py:648`
  then widened it to ~5.2 pips at `execution_engine.py:95-108` → realised risk ~4.6%, **~7× target**.
  Both stops landed ~5 pips while the monitor reported a 2.0-pip spread — ~40% of the risk distance
  is spread cost.
* **The sizer rejects the only symbols with edge.** WTI (best PF) was refused:
  `position_sizing.py:96-105`, "Minimum lot size (0.01) would force 3.29% risk (target 0.61%)".
  The system then traded EURUSD + AUDUSD — the two worst-measured symbols. Sizing grid, not edge,
  decides what gets traded on a small account.
* `SR_RATCHET` fires every ~5s with 0.2-pip SL moves and MT5 answers "No changes" — no-op chatter.

## Conventions worth knowing

* Backtest statuses: `QUEUED | RUNNING | DONE | FAILED | CANCELLED` — **`DONE`**, not `COMPLETED`.
  `POST /api/backtest/run` answers **202** with a `job_id`.
* Localhost authenticates as admin (`_is_local_request()`), so local curl needs no token.
* `normalise_style()` maps anything unrecognised to `SWING` — a typo becomes a swing scan. Style →
  timeframe: SWING→H1, DAY_TRADING→M15, SCALP→M5.
* Console UI: `/`,`/dashboard` → `dashboard.html`; `/console` → `console.html`; `/classic` →
  `index.html`. `console.js`/`dashboard.js` are IIFEs with no exported global.
* `_csv()` in `intelligence_api.py` returns `None` (not `[]`) for an absent param — use
  `set(_csv(q,"x") or [])`.
* The **broker** symbol is what resolves, not the canonical one: `resolve("OILCash#")` can miss
  `_ALIAS_MAP` and silently fall back to a generic FX spec. Guarded by `test_symbol_registry.py`.
* `max_evaluations` is a budget **per search**, not per optimiser.
* Cross-style consensus: `intelligence/mode_aggregator.py`. Measured weights SWING 0.346,
  DAY_TRADING 0.1064, SCALP 0.1287 — all below neutral (all three lost money over 6 months).
* Regime optimisation: `backtesting/regime_optimizer.py` (`tools/optimise_regime.py`,
  `/api/backtest/regime-policy`). Its disable thresholds deliberately duplicate
  `winrate_targeting.regime_edge_table`; a test enforces they agree.
* **Quote fallback must never call the profile hydrators** — they call back into `fetch_quotes()`,
  unbounded (233 re-entries for one NIFTY lookup) and silent because `hydrate_batch` swallows
  exceptions. Read `INDIA_UNIVERSE`/`STOCK_UNIVERSE` directly. Guarded by
  `tests/test_provider_recursion.py`.
* **Never seed a modelled value from `hash()`** — CPython salts it per process. Use
  `jarvis.data.determinism.stable_seed`.
* `tools/audit_endpoints.py` marks six provider-backed India/stock routes `EXTERNAL` (6s leash) and
  used to **tolerate** a timeout on them as "needs a live external provider" — making it blind to
  the very defect it exists to catch, which is how three broken India routes stayed invisible. A
  timeout there is now `HANG` and fails the run; `--allow-provider-hang` restores the old tolerance
  for air-gapped hosts. If a check can pass on broken input, it is not a check.
* Verification: `tools/verify_ui_live.py` (44 checks), `verify_dashboard_render.js` (88),
  `verify_dashboard_nav.js` (31), `audit_wiring.py`, `audit_endpoints.py`; tests
  `test_mode_aggregator.py`, `test_backtest_optimizer.py`, `test_regime_optimizer.py`,
  `test_ui_wiring.py`, `test_provider_recursion.py`.
* **`docs/MARKETS_DATA_CONTRACTS.md`** — shapes for the ten stocks/India endpoints, plus the
  timeout token (`fast` 8s / `normal` 15s / `provider` 30s / `slow` 60s, `dashboard.js:46`). Two
  traps: `/api/stocks/screener` returns `count=47` with 40 rows (**`count` is the matching total**),
  and `/api/stocks/news` + `recommended_buys` have **no UI consumer**.

## Environment

* Sandbox refuses writes outside the project dir. Use the Bash tool, not PowerShell (no output;
  `tasklist`/`Get-Process` blocked). `taskkill` needs `MSYS_NO_PATHCONV=1` or MSYS mangles `/PID`.
* Python 3.13.12 managed, no venv; `pytest`/`pandas`/`numpy` import directly.
* `rm -rf X && cmd` swallows the command's stdout — run the `rm` separately.
* Running a script *by path* puts the **script's** dir on `sys.path` — `.scratch/*.py` needs
  `sys.path.insert(0, <repo root>)`.

## Testing traps (each has produced a test that passes against the bug)

* Hunting an exception misses unbounded recursion when a frame swallows it — **pin the call**
  (patch the callee with a recorder).
* `hash()` salting is constant *within* a process — the discriminating test must spawn a subprocess
  under a different `PYTHONHASHSEED`.
* Clear both caches in `setUp`; `_quote_cache` has a 15s TTL.
* Plain `pytest -q` exits 1 *after all tests pass* (safe-delete hook blocks temp-dir cleanup) — use
  `--basetemp=.scratch/pttmp`.
* `np.allclose` on microsecond epoch ints has an rtol far larger than a 4-hour shift — compare
  indexes with `.equals()` when testing resample labelling.

## Frontend

* **A self-retriggering MutationObserver freezes the page with no error.** `setAttribute` queues a
  record even when the value is unchanged, so an observer that writes inside its own `subtree` +
  `attributeFilter` re-queues forever; microtasks drain before paint, so the thread blocks
  permanently and silently. Rule: **a callback must write nothing it observes** — compare before
  writing (`setIfChanged`) plus a re-entrancy flag. Deleting the observer is not a fix; assert the
  feature still works. Only fires on *interaction* when the initial sync ran before attach, which is
  why an idle page looks healthy.
* Diagnosing a blocked main thread: `page.evaluate` ignores its own `timeout` — race it against a
  timer. Run a control phase (load, don't interact, probe 30s). `Debugger.enable` + `Debugger.pause`
  names the blocking frame (no pause ⇒ native, e.g. dialog/sync XHR). Chrome at
  `C:\Program Files\Google\Chrome\Application\chrome.exe`; `puppeteer-core` in the managed node
  workspace (`--no-sandbox --disable-gpu --disable-dev-shm-usage`). **`agent-browser` does not
  support Windows.** `node --check` proves a file parses, nothing more.
* `/api/telemetry_state`'s `account` already carries `login`, `name`, `server`, `company`, `balance`,
  `equity`, `margin`, `free_margin`, `margin_level`, `leverage`, `profit`, `currency`,
  `trade_allowed`, `last_sync_time` — the account dropdown needs no server change.
* Canonical nav: `/` Forex·Crypto, `/stocks` US, `/india` India, `/options` India Options; in a
  `.tt-dropdown` in `dashboard.html`, styled by `markActiveNav()` in `hm_ui.js`.
* `.tt-rail` is a sticky **top bar** (`grid-area: rail`); the account strip is pushed right by
  `.tt-rail__spacer`. `[hidden]` is a weak UA rule — declare `.panel[hidden] { display: none; }`.

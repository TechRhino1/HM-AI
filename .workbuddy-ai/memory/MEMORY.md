# HM-AI / JARVIS — durable project notes

Curated facts that outlive a session. Detail lives in `YYYY-MM-DD.md`.
**Keep this small** — it is injected every session and silently truncated at the tail.

## Git: push hangs; remote refs are eaten

`git push` stalls ~40s+ because `helper-selector` sits first in the credential chain. Bypass:

```bash
GCM="C:/Users/Itrai/.workbuddy-ai/binaries/PortableGit/versions/1.2.0/mingw64/bin/git-credential-manager.exe"
timeout 180 git -c credential.helper= -c credential.helper="!$GCM" push origin main > /tmp/pushout.txt 2>&1
echo "exit=$?"; cat /tmp/pushout.txt
```

Redirect to a file (piping masks `$?`); verify with `git ls-remote`. Remote
`https://github.com/TechRhino1/HM-AI.git`. Backticks in `-m` are eaten by bash.

**`.git/refs/remotes/*` is wiped immediately after being written here.** `git fetch`/`update-ref`
exit 0 and write nothing; even hand-writing the file vanishes. So `git status` always says `[gone]` —
ignore it, never rely on `-u`, push an explicit refspec (`main:refs/heads/main`).

## Data-loss hazard (unresolved, 3x, all in the small hours)

`2026-09-13 ~03:28` whole `jarvis/` (after `git rm`); `2026-09-14 03:50:52` whole `jarvis/`
(after `git rm`); `2026-09-15 ~05:31` **object store** — all `.pack` + `refs/heads/main`
(after `git stash push`). Cause unknown (AV/EDR quarantine and cloud-sync lead; hooks, `fsmonitor`,
automations, disk pressure, repo scripts all ruled out). **`.git/` is not safe.**

* **Commit and push early** — the remote is the only durable store.
* **Never `git stash`.** Use `git archive HEAD <path> | tar -x -C .scratch/oldtree` to diff old/new.
* Tree lost → `git checkout -- jarvis/` (from the *index*; preserves staged deletions), **not**
  `git checkout HEAD -- jarvis/`.
* Object store lost: `tail .git/logs/HEAD` → `git cat-file -t <sha>` to confirm → `git fetch origin`
  → `git update-ref refs/heads/main <sha>` (a fetch recreates `origin/main` but not the local branch).
* Refresh `git bundle create .git/backup/repo-<ts>.bundle --all` — survived all 3.

## The live server does not hot-reload — restart after a fix

Inline `python -c` from repo root under `ThreadingHTTPServer`; **disk edits do nothing until
restart.** Diagnostic rule: *a hang that does not reproduce in a fresh interpreter is a stale
process, not a logic bug* — compare before reading code. (`get_indices_snapshot()` 0.88s fresh vs
>240s in-server; `py-spy` caught it 997 frames deep in recursion a commit had already deleted.)

`py-spy` (`…/python/envs/default/Scripts/py-spy.exe dump --pid <pid>`) attaches **without
restarting** — restarting destroys the wedged state. `netstat -ano | grep 8501` for the pid;
server-side `CLOSE_WAIT` = a stuck handler thread.

## The entry signal has no measured edge (audited 2026-09-15, 183d real bars, 20 symbols)

PF **0.568–1.202** SWING/H1; **1 of 40** symbol×target combos reaches 1.3. Win rate 28.2–44.8% where
1.5R needs 40.0%. At ~1% risk/trade DD is 62–100%; DD ≤10% needs **0.012–0.108% risk per trade**,
below the broker minimum lot on most symbols. Ruled out by measurement: costs (free execution still
loses), exit geometry (best == seed), target width (PF invariant to tp), ranking (no quantile breaks
even), learned filter (AUC 0.481). What remains is the directional call.

* Directional call = 7-branch if/elif on `choch`/`bos`/`trend_score` (`decision_engine.py:105-122`).
  Gate "probability" = `0.45 ×` hand-typed 6-bin table that *inflates* inputs (`confidence.py:13-20`)
  `+ 0.55 ×` a hand-authored linear score clipped to [0.35,0.88]
  (`online_ml_predictor.py:564-573,417`). **Verified 2026-09-16: the whole gate is 100%
  hand-authored and 0% fitted** — `self.weights = DEFAULT_WEIGHTS.copy()` (literals),
  `bias = 0.20`, no `.fit()`, no persisted weights (`data/models/` holds only the meta-labeler).
* **Do NOT just wire up the "already-fitted" `ScoreCalibration`** — the obvious P0-1 fix is wrong.
  Measured on `config/winrate_profiles.json` (16 symbols): n is only **41–126 across 8 bins**
  (~5–16 trades/bin), the fit is **in-sample**, and it was done on the **cost-free backtest**.
  In-sample Brier ≈ `p(1-p)` (base-rate Brier) for nearly all symbols — the map extracts almost
  nothing; 2/16 collapse to a pure constant under PAV (AUDUSD all 8 bins = 0.6374). Base rates are
  inflated by the broken cost model (AUDUSD 0.6374 vs ~0.28–0.45 measured at honest costs). A third
  independent confirmation that the score carries no signal (after AUC 0.481 and flat isotonic here).
  The real P0-1 is to **refit on honest-cost data with adequate samples**, then wire in.
* Meta-label gate **inert on purpose**: test AUC 0.481 (train 0.746), 0.479 with primary outputs
  (train 0.783); top-decile selection *lowers* win rate (0.341 vs 0.359). Horizon 5/10/20/40 →
  0.527/0.507/0.508/0.507. Evidence in `meta_labeler.py::_load`.
* `signal_engine.py` (`REGIME_WEIGHTS`) is **dead code** — never imported. Don't cite it.
* **FIXED (`189c1e2`) — sizing was unresponsive to risk, two ways.** (a) The volatility rule was
  **dead**: no caller passed `atr_ratio`, so it defaulted to 1.0 and `atr_ratio > 1.5 -> *0.85`
  never fired. `risk_engine.authorize_execution` now derives it (realised ATR ÷ the symbol's
  `typical_atr_pct`) and passes it. (b) The quarter-Kelly term **pins at its 1.50 cap** for every
  plausible (p, R) — (0.60, 2.0) already yields 10.0 — so it added a flat +0.75pp, not information;
  now logged but unused. **Lesson: a "dynamic" knob whose argument is never passed is dead code —
  grep the callers before trusting it.** `engine.py:711` applied the lot floor *after* the risk cap
  (fixed earlier).
* Consequence of correct sizing: budgets fell ~2× (1.25% → 1.0% baseline), so the minimum-lot
  ceiling now **rejects more trades on small accounts**. That is the guard working, and it is the
  same conclusion as the DD arithmetic: a ~$780 account cannot run this system at safe risk.
* Best regime **COMPRESSION** (PF 1.203); worst **TREND_BULL** (0.872). A "trend-following" strategy
  that loses most in trends is mean reversion with the wrong label.
* **Under honest costs only WTI (+0.087R) and XAUUSD (+0.042R) stay positive.** Correcting costs
  drains 0.162R/trade (median 0.118), scaling with spread ÷ stop distance — brutal for SOLUSD
  (−0.69R), trivial for XAUUSD (−0.0075R). GER40 and NAS100 go *negative*; the "3–5 symbol
  salvageable band" was an artefact of the broken cost model. **No symbol reaches PF 1.3**; WTI 1.202
  is the ceiling.
* H4/D1 resample look-ahead is real but **measured ~zero impact** (Δ 0.0000R, 1,200 bars, 3 symbols)
  — `market_structure.py:29` needs 5 confirming bars per pivot so the newest contaminated bucket never
  forms one. Fix as a landmine; don't expect P&L to move.
* **M5/SCALP candidates truncated to bar 60–14,398 of 37,440** → replay gives PF 1.09–1.74, opposite
  sign to the live optimiser. Every SCALP number is unsafe until re-scanned.

Audit tools: `tools/audit_trade_quality.py`, `audit_verdict.py`, `audit_lookahead.py`,
`audit_meta_gate.py`, `train_meta_labeler.py`, `build_audit_report.py`
(→ `reports/trade_plan_audit.html`).

## Live-execution defects (found 2026-09-16, acct 101059540)

* **Account is named "Demo Account"** in MT5 (`/api/diagnostics`) — LIVE mode, live logic, demo
  money. Confirm in-terminal before assuming real capital is at risk.
* **FIXED (`955d40c`) — `sl_price` and `risk_dist` disagreed by ~7×.** `dynamic_levels.py` floored
  `risk_dist` at `pip_size * 5` but never widened `sl_price` to match. The sizer prices risk off
  `sl_price` while the post-fill re-anchor (`execution_engine.py:95`) uses `sl_distance`, so a
  0.7-pip stop was sized at 0.69 lots and then re-anchored to 5 pips on an already-filled position:
  realised 4.6% against a 0.61% target. The floor now applies to the **distance** before `sl_price`
  is derived, and is dynamic — `max(3 × live spread, 10% ATR)` — instead of a constant. Guarded by
  `test_sl_price_and_risk_dist_describe_the_same_level`.
* Both stops landed ~5 pips against a 2.0-pip spread — ~40% of the risk distance was spread cost.
  That is what the `3 × spread` floor now prevents. `execution_engine.py` still cannot resize after
  fill, so it now **warns** when the broker's minimum widens the stop past the plan.
* **The sizer rejects the only symbols with edge.** WTI refused at `position_sizing.py:96-105`:
  "Minimum lot size (0.01) would force 3.29% risk (target 0.61%)". It then traded EURUSD + AUDUSD —
  the two worst-measured symbols. On a small account the lot grid, not the edge, decides what trades.
* `SR_RATCHET` fires every ~5s with 0.2-pip SL moves; MT5 answers "No changes" — no-op chatter.

## Conventions

* Backtest statuses `QUEUED | RUNNING | DONE | FAILED | CANCELLED` — **`DONE`**, not `COMPLETED`.
  `POST /api/backtest/run` answers **202** with a `job_id`.
* Localhost authenticates as admin (`_is_local_request()`) — local curl needs no token.
* `normalise_style()` maps anything unrecognised to `SWING`. Style → timeframe: SWING→H1,
  DAY_TRADING→M15, SCALP→M5.
* Console UI: `/`,`/dashboard` → `dashboard.html`; `/console` → `console.html`; `/classic` →
  `index.html`. `console.js`/`dashboard.js` are IIFEs with no exported global.
* `_csv()` in `intelligence_api.py` returns `None` (not `[]`) for an absent param — use
  `set(_csv(q,"x") or [])`.
* The **broker** symbol is what resolves, not the canonical one: `resolve("OILCash#")` can miss
  `_ALIAS_MAP` and silently fall back to a generic FX spec. Guarded by `test_symbol_registry.py`.
* `max_evaluations` is a budget **per search**, not per optimiser.
* Cross-style consensus: `intelligence/mode_aggregator.py`. Weights SWING 0.346, DAY_TRADING 0.1064,
  SCALP 0.1287 — all below neutral (all three lost money over 6 months).
* Regime optimisation: `backtesting/regime_optimizer.py` (`tools/optimise_regime.py`,
  `/api/backtest/regime-policy`). Disable thresholds deliberately duplicate
  `winrate_targeting.regime_edge_table`; a test enforces they agree.
* **Quote fallback must never call the profile hydrators** — they call back into `fetch_quotes()`,
  unbounded (233 re-entries for one NIFTY lookup) and silent because `hydrate_batch` swallows
  exceptions. Read `INDIA_UNIVERSE`/`STOCK_UNIVERSE` directly. Guarded by
  `tests/test_provider_recursion.py`.
* **Never seed a modelled value from `hash()`** — CPython salts it per process. Use
  `jarvis.data.determinism.stable_seed`.
* `tools/audit_endpoints.py` marks six provider-backed routes `EXTERNAL` (6s leash) and used to
  **tolerate** a timeout as "needs a live provider" — blind to the defect it exists to catch, which
  is how three broken India routes stayed invisible. A timeout is now `HANG` and fails the run;
  `--allow-provider-hang` restores tolerance. If a check can pass on broken input, it is not a check.
* Verification: `tools/verify_ui_live.py` (44), `verify_dashboard_render.js` (88),
  `verify_dashboard_nav.js` (31), `audit_wiring.py`, `audit_endpoints.py`.
* **India latency = one hydration call, not the scan.** `analyze_india_instrument` is pure maths
  over synthetic candles; the 42-symbol scan re-runs in 0.14s once cached. Real cost is one batched
  `fetch_quotes()` per **60s hydrator TTL** (not the 15s scan TTL), 0.32–3.56s; `/api/india/heatmap`
  serves in 0.01–1.35s fresh. A one-off 17.8s outlier does not reproduce — an upper bound.
* **A spot-check right after another call measures the cache, not the endpoint** (first recorded
  timings were inside the TTL and wrong by 10×). Sample with gaps longer than the TTL.
* **`docs/MARKETS_DATA_CONTRACTS.md`** — shapes for the ten stocks/India endpoints plus the timeout
  token (`fast` 8s/`normal` 15s/`provider` 30s/`slow` 60s, `dashboard.js:46`). Traps: screener
  `count` is the **matching total**, not `len(stocks)`; `/api/stocks/news` + `recommended_buys` have
  **no UI consumer**.

## Environment

* Sandbox refuses writes outside the project dir. Use Bash, not PowerShell (no output;
  `tasklist`/`Get-Process` blocked). `taskkill` needs `MSYS_NO_PATHCONV=1` or MSYS mangles `/PID`.
* Python 3.13.12 managed, no venv; `pytest`/`pandas`/`numpy` import directly.
* `rm -rf X && cmd` swallows the command's stdout — run the `rm` separately.
* Running a script *by path* puts the **script's** dir on `sys.path` — `.scratch/*.py` needs
  `sys.path.insert(0, <repo root>)`.
* **Access:** the server binds **127.0.0.1 only**, so `http://<LAN-IP>:8501` never works.
* **Public tunnels:** serveo.net kills the SSH session every **~12m09s** (measured over 6
  consecutive drops) and its edge returns **502 with an empty body while still reporting CONNECTED**,
  so the supervisor never reacts. Cloudflare is now primary in `HM_start.py`; serveo is fallback.
  `cloudflared` 2026.9.1 is installed (also `C:\Users\Itrai\cloudflared.exe`), but quick-tunnel
  subdomains are **random per launch** — re-read `hm_cloudflared.log` after each start.
* `http_proxy=127.0.0.1:16119` with empty `no_proxy` — curl to any public host returns a bogus 502
  without `--noproxy '*'`.
* A "public URL is down" report is usually the tunnel, but **check `127.0.0.1:8501` first** — a
  wedged server gives the identical error.

## Testing traps (each has produced a test that passes against the bug)

* Hunting an exception misses unbounded recursion when a frame swallows it — **pin the call**.
* `hash()` salting is constant *within* a process — the discriminating test must spawn a subprocess
  under a different `PYTHONHASHSEED`.
* Clear both caches in `setUp`; `_quote_cache` has a 15s TTL.
* Plain `pytest -q` exits 1 *after all tests pass* (safe-delete hook blocks temp-dir cleanup) — use
  `--basetemp=.scratch/pttmp`.
* `np.allclose` on microsecond epoch ints has an rtol far larger than a 4-hour shift — compare
  indexes with `.equals()` when testing resample labelling.

## Frontend

* **A self-retriggering MutationObserver freezes the page with no error.** `setAttribute` queues a
  record even when the value is unchanged, so an observer writing inside its own `subtree` +
  `attributeFilter` re-queues forever; microtasks drain before paint, so the thread blocks
  permanently and silently. Rule: **a callback must write nothing it observes** — compare before
  writing (`setIfChanged`) plus a re-entrancy flag. Only fires on *interaction* when the initial sync
  ran before attach, which is why an idle page looks healthy.
* Blocked main thread: `page.evaluate` ignores its own `timeout` — race it against a timer. Run a
  control phase (load, don't interact, probe 30s). `Debugger.enable` + `Debugger.pause` names the
  frame (no pause ⇒ native). Chrome at `C:\Program Files\Google\Chrome\Application\chrome.exe`;
  `puppeteer-core` in the managed node workspace (`--no-sandbox --disable-gpu --disable-dev-shm-usage`).
  **`agent-browser` does not support Windows.** `node --check` proves a file parses, nothing more.
* `/api/telemetry_state`'s `account` already carries `login`, `name`, `server`, `company`, `balance`,
  `equity`, `margin`, `free_margin`, `margin_level`, `leverage`, `profit`, `currency`,
  `trade_allowed`, `last_sync_time` — the account dropdown needs no server change.
* Canonical nav: `/` Forex·Crypto, `/stocks` US, `/india` India, `/options` India Options; in a
  `.tt-dropdown` in `dashboard.html`, styled by `markActiveNav()` in `hm_ui.js`.
* `.tt-rail` is a sticky **top bar** (`grid-area: rail`). `[hidden]` is a weak UA rule — declare
  `.panel[hidden] { display: none; }`.

# HM-AI / JARVIS — durable project notes

Curated facts that outlive a session. Detail lives in `YYYY-MM-DD.md`.
**Keep this small** — it is injected every session and silently truncated at the tail.
Deep audit findings live in `AUDIT-2026-09.md` (read on demand, not injected).

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
  → `git update-ref refs/heads/main <sha>` (fetch recreates `origin/main` but not the local branch).
* Refresh `git bundle create .git/backup/repo-<ts>.bundle --all` — survived all 3.

## The live server does not hot-reload — restart after a fix

Inline `python -c` from repo root under `ThreadingHTTPServer`; **disk edits do nothing until
restart.** Diagnostic rule: *a hang that does not reproduce in a fresh interpreter is a stale
process, not a logic bug* — compare before reading code. (`get_indices_snapshot()` 0.88s fresh vs
>240s in-server; `py-spy` caught it 997 frames deep in recursion a commit had already deleted.)

`py-spy` (`…/python/envs/default/Scripts/py-spy.exe dump --pid <pid>`) attaches **without
restarting** — restarting destroys the wedged state. `netstat -ano | grep 8501` for the pid;
server-side `CLOSE_WAIT` = a stuck handler thread.

## Signal quality — the short version

**The entry signal has no measured edge.** PF 0.568–1.202 on SWING/H1, 0/20 reach the 1.3 bar at
183d *and* at 365d. The gate is 100% hand-authored, 0% fitted; the meta-label gate is inert
(AUC 0.481); refitting calibration on honest data yields 0/20 skillful and *regresses toward
chance* as n doubles. Full evidence, per-symbol numbers and the P0/P1/P2 backlog:
**`AUDIT-2026-09.md`**.

Two fixes already landed from that audit: `955d40c` (7× realised-risk defect in
`dynamic_levels.py` — `sl_price` and `risk_dist` disagreed) and `189c1e2` (volatility-targeted
sizing; `atr_ratio` was never passed by any caller, and quarter-Kelly pinned at its cap).

## Conventions

* Backtest statuses `QUEUED | RUNNING | DONE | FAILED | CANCELLED` — **`DONE`**, not `COMPLETED`.
  `POST /api/backtest/run` answers **202** with a `job_id`.
* Localhost authenticates as admin (`_is_local_request()`) — local curl needs no token.
* `normalise_style()` maps anything unrecognised to `SWING`. Style → timeframe: SWING→H1,
  DAY_TRADING→M15, SCALP→M5. Reports key results `SWING(H1)` / `DAY_TRADING(M15)` / `SCALP(M5)` —
  **filter by style when comparing**, a naive loop lets SCALP overwrite SWING.
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
  so the supervisor never reacts. A blocked ssh also stops sending keepalives — drain its stdout.
  Cloudflare is now primary in `HM_start.py`; serveo is fallback. `cloudflared` 2026.9.1 is
  installed (also `C:\Users\Itrai\cloudflared.exe`), but quick-tunnel subdomains are **random per
  launch** — re-read `hm_cloudflared.log` after each start.
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
* A "dynamic" knob whose argument is never passed is dead code — **grep the callers** before
  trusting it (this is how `atr_ratio` was found).

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

## Running the dashboard — it does not survive a session

`HM_dashboard.bat` runs the **UI + REST API only** (`start_server(mt5_client=None,
orchestrator=None)`, port 8501). `HM_start.bat` → `HM_start.py` is the bigger thing: it also boots
the autonomous trading engine, the MT5 client and a **public remote-access tunnel**. Use the former
for the web terminal.

**A server started as a background process of an agent session is reaped when the session ends** —
silently, with nothing in the log but `listening on 8501`. Observed twice: 21h23m and 3h40m, each
time leaving the dashboard dead with no explanation. Detaching from a session is **not possible
here**: launching `cmd.exe` from Bash is blocked by the sandbox, and `cmd //c start` through Git
Bash mangles the path into an interactive shell.

So: never finish a task with the dashboard alive only as a session background task. Point the user
at `HM_dashboard.bat` and say plainly that closing the window stops it.

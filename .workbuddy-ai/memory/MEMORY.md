# HM-AI / HM Algo 2.0 — durable project notes

Curated facts that outlive a session. **This file is injected every session and silently truncated at
the tail** — it has already lost its last two sections once, so keep it small. Detail lives in
`YYYY-MM-DD.md`; long diagnostics in `AUDIT-2026-09.md`; frontend and testing traps in `TRAPS.md`
(read on demand); recovery procedures in the `recover-vanished-working-tree` skill.

## Git

* **Push** stalls ~40s because `helper-selector` is first in the credential chain. Bypass, redirect to
  a file (piping masks `$?`), and verify with `git ls-remote` — never the local push message:
  `GCM="C:/Users/Itrai/.workbuddy-ai/binaries/PortableGit/versions/1.2.0/mingw64/bin/git-credential-manager.exe"`
  then `timeout 200 git -c credential.helper= -c credential.helper="!$GCM" push origin main:refs/heads/main`.
* `git ls-remote` returns a bogus **502** through the local proxy — retry with
  `env http_proxy= https_proxy= git ls-remote …`.
* Remote `https://github.com/TechRhino1/HM-AI.git`. Backticks in `-m` are eaten by bash — use
  `git commit -F <file>`. `/tmp` does not exist here; use `.scratch/`.
* **`.git/refs/remotes/*` is wiped immediately after being written.** `fetch`/`update-ref` exit 0 and
  write nothing, so `git status` always says `[gone]` — ignore it, push an explicit refspec.
* `core.autocrlf=true`, so a blob written raw shows ` M` while `git diff --numstat` is **empty** — CRLF
  normalisation, not a change.

## Data-loss hazard — files vanish, then cannot be recreated

Four incidents, all in the small hours; the last two followed `git rm` and `git stash push`. Cause
unknown (AV/EDR and cloud-sync lead). **`.git/` is not safe.** Full procedure: the
`recover-vanished-working-tree` skill.

* Nastiest signature: **`stat` says FILE_NOT_FOUND while `CreateFile(CREATE_ALWAYS)` says
  ACCESS_DENIED** = delete-pending, i.e. the name is reserved by an open handle with delete-on-close.
  A deny ACE would let `stat` succeed, so the pair rules out an ACL. `os.replace`, `open(p,'wb')`, the
  Write tool and shell redirect all fail; `git checkout` fails with `unable to unlink old`.
  **Recovery: hardlink through an alias** — `os.link(alias, target)`, and the SOURCE must exist first.
* **Commit and push early** — the remote is the only durable store. **Never `git stash`.**
* Diff an old tree with `git archive HEAD <path> | tar -x -C .scratch/oldtree`.
* Tree lost → `git checkout -- jarvis/` (from the *index*; preserves staged deletions), **not**
  `git checkout HEAD -- jarvis/`. Object store lost: `tail .git/logs/HEAD` → `git cat-file -t <sha>` →
  `git fetch origin` → `git update-ref refs/heads/main <sha>`.
* Refresh `git bundle create .git/backup/repo-<ts>.bundle --all` — it survived all four.

## The live server does not hot-reload

Inline `python -c` from repo root under `ThreadingHTTPServer`; **Python edits do nothing until
restart.** Static CSS/JS/templates *are* re-read per request — no restart for UI work. Rule: *a hang
that does not reproduce in a fresh interpreter is a stale process, not a logic bug.* A `py-spy` dump
once showed a provider **calling** a function that in the working tree is a **comment**.

`py-spy` (`…/python/envs/default/Scripts/py-spy.exe dump --pid <pid>`) attaches **without restarting** —
restarting destroys the wedged state. `netstat -ano | grep 8501` for the pid.

## Signal quality — the short version

**The entry signal has no measured edge.** PF 0.568–1.202 on SWING/H1; 0/20 reach the 1.3 bar at 183d
*and* 365d. The gate is 100% hand-authored, 0% fitted; the meta-label gate is inert (AUC 0.481);
refitting calibration on honest data gives 0/20 skillful and *regresses toward chance* as n doubles.
Full evidence and the P0/P1/P2 backlog: **`AUDIT-2026-09.md`**.

## Conventions

* Backtest statuses `QUEUED | RUNNING | DONE | FAILED | CANCELLED` — **`DONE`**, not `COMPLETED`.
  `POST /api/backtest/run` answers **202** with a `job_id`.
* Localhost authenticates as admin (`_is_local_request()`) — local curl needs no token.
* `normalise_style()` maps anything unrecognised to `SWING`. Style → timeframe: SWING→H1,
  DAY_TRADING→M15, SCALP→M5. Reports key results `SWING(H1)`/`DAY_TRADING(M15)`/`SCALP(M5)` —
  **filter by style when comparing**; a naive loop lets SCALP overwrite SWING.
* Routes: `/`,`/dashboard` → `dashboard.html`; `/console` → `console.html`; `/classic` → `index.html`.
  `console.js`/`dashboard.js` are IIFEs with no exported global.
* `_csv()` in `intelligence_api.py` returns `None` (not `[]`) for an absent param — use
  `set(_csv(q,"x") or [])`.
* The **broker** symbol is what resolves: `resolve("OILCash#")` can miss `_ALIAS_MAP` and fall back to
  a generic FX spec. Guarded by `test_symbol_registry.py`.
* `max_evaluations` is a budget **per search**, not per optimiser.
* Cross-style consensus: `intelligence/mode_aggregator.py`. Weights SWING 0.346, DAY_TRADING 0.1064,
  SCALP 0.1287 — all below neutral.
* Regime optimisation: `backtesting/regime_optimizer.py` (`tools/optimise_regime.py`,
  `/api/backtest/regime-policy`). Disable thresholds deliberately duplicate
  `winrate_targeting.regime_edge_table`; a test enforces they agree.
* **Quote fallback must never call the profile hydrators** — they call back into `fetch_quotes()`,
  unbounded and silent because `hydrate_batch` swallows exceptions. Read `INDIA_UNIVERSE`/
  `STOCK_UNIVERSE` directly. Guarded by `tests/test_provider_recursion.py`.
* **Never seed a modelled value from `hash()`** — CPython salts it per process. Use
  `jarvis.data.determinism.stable_seed`.
* **MT5 times are BROKER-SERVER time, not UTC** (`position.time`, `deal.time`,
  `symbol_info_tick().time`; XM = GMT+2/+3). Read as UTC they land 2-3h in the *future*, so
  `now_utc - open_time` comes out short and `max(0, …)` zeroes it — which silently disabled
  `position_monitor`'s stagnation exits (`if open_dur_sec > 0`) for the first 3h of every position and
  made a 25h position read `DAY_TRADING` instead of `SWING`. Use `jarvis/data/broker_time.py`
  (derives the offset from the freshest tick, rejects stale ones, warns once on failure). Never
  hardcode the offset — it moves with the broker's DST. `tests/test_broker_time.py`, 3 of 13 fail
  pre-fix.
* **India latency = one hydration call, not the scan.** `analyze_india_instrument` is pure maths over
  synthetic candles; the 42-symbol scan re-runs in 0.14s once cached. Real cost is one batched
  `fetch_quotes()` per **60s hydrator TTL** (not the 15s scan TTL). A 17.8s outlier never reproduced.
* **A spot-check right after another call measures the cache, not the endpoint.** Sample with gaps
  longer than the TTL.
* **`docs/MARKETS_DATA_CONTRACTS.md`** — shapes for the ten stocks/India endpoints plus the timeout
  token (`fast` 8s/`normal` 15s/`provider` 30s/`slow` 60s, `dashboard.js:46`). Traps: screener `count`
  is the **matching total**, not `len(stocks)`; `/api/stocks/news` + `recommended_buys` have **no UI
  consumer**.

## Verification

`tools/`: `verify_ui_live.py` (44) · `verify_dashboard_render.js` (88, pure-Node VM) ·
`verify_dashboard_nav.js` (31, live) · `verify_ui_layout.js` (238, puppeteer) · `audit_endpoints.py`
(44) · `audit_wiring.py`. `.scratch/probe_panels.js` = panel-clipping probe.
`verify_ui_layout.js` checks overflow, tap targets ≥44px, glass actually applied, **document must not
scroll**, **view must fit inside main**, and **no silently clipped content** (exempting ellipsis,
`.tt-sr-only`, form controls and the charting library's canvas).

Screenshots: `puppeteer-core` (managed node workspace) driving
`C:/Program Files/Google/Chrome/Application/chrome.exe`; `.scratch/shots.js <tag>` captures 6 pages ×
3 viewports. **`agent-browser` does not support Windows.** Both puppeteer tools resolve the managed
node workspace themselves — no `NODE_PATH` needed.

## Environment

* Sandbox refuses writes outside the project dir. Use Bash, not PowerShell (no output;
  `tasklist`/`Get-Process` blocked). `taskkill` needs `MSYS_NO_PATHCONV=1` or MSYS mangles `/PID`.
  Invoking `cmd.exe` from Bash is blocked outright.
* Python 3.13.12 managed, no venv; `pytest`/`pandas`/`numpy`/`psutil`/`py-spy` available. `wmic` is
  gone from this Windows build — use `psutil`.
* `rm -rf X && cmd` swallows the command's stdout — run the `rm` separately.
* Running a script *by path* puts the **script's** dir on `sys.path` — `.scratch/*.py` needs
  `sys.path.insert(0, <repo root>)`.
* **Access:** the server binds **127.0.0.1 only**, so `http://<LAN-IP>:8501` never works.
* `http_proxy=127.0.0.1:16119` with empty `no_proxy` — curl to any public host returns a bogus 502
  without `--noproxy '*'`. A "public URL is down" report is usually the tunnel, but **check
  `127.0.0.1:8501` first**.
* **Public tunnels:** serveo.net kills the SSH session every **~12m09s** and its edge returns **502
  with an empty body while still reporting CONNECTED**, so the supervisor never reacts. Cloudflare is
  primary in `HM_start.py`; serveo is fallback. Quick-tunnel subdomains are **random per launch** —
  re-read `hm_cloudflared.log`.

## Running the dashboard — it does not survive a session

`HM_dashboard.bat` runs the **UI + REST API only** (`start_server(mt5_client=None, orchestrator=None)`,
port 8501). `HM_start.bat` → `HM_start.py` also boots the autonomous trading engine, the MT5 client
and a **public remote-access tunnel**.

**A server started as a background process of an agent session is reaped when the session ends** —
silently, with nothing in the log but `listening on 8501`. Observed three times: 21h23m, 3h40m, then
**5m44s**. Detaching is **not possible here**. With `mt5_client=None`,
`/api/intelligence/auto-selection` answers **503** — expected in UI-only mode, not a fault.

Never finish a task with the dashboard alive only as a session background task. Point the user at
`HM_dashboard.bat` and say plainly that closing the window stops it.

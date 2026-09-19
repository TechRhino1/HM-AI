# HM-AI / HM Algo 2.0 — index

Injected every session and **silently truncated at the tail — keep it small.** Everything here is a
pointer or a rule that has already cost a session; detail lives elsewhere on purpose.

* **`TRAPS.md`** — server, frontend, data-source, testing, CSS/layout and data-integrity traps.
* `AUDIT-2026-09.md` — signal-quality evidence + P0/P1/P2 backlog. `YYYY-MM-DD.md` — per-session detail.
* `docs/PLAN-2026-09-17-six-ui-items.md` — the six UI items; **all six are implemented** (verified
  round 31).
* Skills: `diagnose-git-push-auth`, `recover-vanished-working-tree`,
  `audit-trading-system-integrity` (§5 = the **measurement** traps — check it before auditing
  signal quality; §§1-4 are wiring only).

## Non-negotiables

* **`.git/` is not safe here.** Files vanish in the small hours and cannot be recreated (4
  incidents; the last two followed `git rm` / `git stash push`; cause unknown). Commit and push
  early, **never `git stash`**, keep `git bundle create .git/backup/repo-<ts>.bundle --all` fresh.
  Some paths are readable but **not writable** (`HM_dashboard.bat`, `HM_start.py`,
  `jarvis/intelligence/decision_engine.py`) — write via a hardlink alias in `.scratch/_restore/`;
  `open(alias,'w')` **truncates the shared object**.
* **The live server does not hot-reload.** Python edits do nothing until restart; static
  CSS/JS/templates *are* re-read per request. *A hang that does not reproduce in a fresh interpreter
  is a stale process, not a logic bug.* `py-spy dump --pid <pid>` attaches without restarting;
  `netstat -ano | grep 8501` for the pid.
* **Push: always use the credential-selector bypass, immediately.** A plain `git push` here is not
  a 40s stall — it produced a 0-byte log for 6m37s and never completed. Run
  `GCM="C:/Users/Itrai/.workbuddy-ai/binaries/PortableGit/versions/1.2.0/mingw64/bin/git-credential-manager.exe";
  timeout 180 git -c credential.helper= -c credential.helper="!$GCM" push origin main` (finishes
  in ~23s). `git status` always says `[gone]` — `.git/refs/remotes/*` is wiped right after being
  written — so verify with `git ls-remote origin refs/heads/main` vs `git rev-parse HEAD`, never
  the push message or the exit code alone. Backticks in `-m` are eaten by bash: use `git commit -F
  <file>`. `/tmp` does not exist; use `.scratch/`.

## Running the platform

`HM_start.py [paper|live]` boots the engine + MT5 client + web server — **real MT5 market data;
`paper` = simulated fills**. `HM_dashboard.bat` is **UI + REST API only** (`mt5_client=None`, so
`auto-selection` answers **503** and `MT5` reads `DISCONNECTED` — both expected, not a fault). Check
`psutil.Process(pid).cmdline()` before concluding the data path is broken. **Never finish a task with
the dashboard alive only as a session background task** — point the user at `HM_dashboard.bat` and
say plainly that closing the window stops it.

**Routes:** `/` = `dashboard.html` (the primary surface). `/classic` = `index.html` (the old
terminal). `/stocks`, `/india`, `/options`, `/console`.

## Baselines

**pytest 2490 passed / 20 deselected.** Currently 2487/3 on a weekend — the 3 are
`tests/test_market_data_independence.py`, which assert `freshness == STALE` while
`SessionEngine.get_market_trading_status()` correctly answers `MARKET_CLOSED` on a Saturday. Not a
regression; they pass on weekdays. (History: 657 → 2490 across rounds; each new suite is listed in
its `YYYY-MM-DD.md`.)

`tools/` — current counts, all green:
`verify_ui_live.py` **46** (starts its own server on **:8599** — nothing may listen there, or its
probes hit an engine-less server and read as `attached=False`/`503`. Run browser suites against
**`.scratch/srv8611.py` on :8611** instead) ·
`verify_dashboard_render.js` **209** (chart stub must record `setMarkers`; a stub must mirror the
real markup's initial state — panels ship `hidden` + `data-state="loading"`; build nodes with
`elementFor(id)`, never `registry.get(id)`, a lazy Map) ·
`verify_terminal_render.js` **55** + 5 FIND/CLEAR (`terminal.js` registers `fetchHistory` *only*
inside `setInterval` — capture and tick the intervals or every history assertion is vacuous) ·
`verify_copilot_render.js` **23** (the answer renderer exists in **two** front ends; this asserts
they agree) · **`tools/dom_stub.js`** (shared stubbed DOM; **not** under `lib/` — a bare `lib/` in
`.gitignore` matches at any depth, so check new files with `git check-ignore -v <path>`; assert
against markup **as rendered**, never `html.replace(/\s+/g,'')`) ·
`verify_dashboard_nav.js` **31** (needs a server; `DASH_URL` overrides the base) ·
`verify_ui_layout.js` **238** (the total is *not* fixed — it counts controls per viewport; **read
the FAIL lines, never the total**. `JARVIS_BASE` overrides the base) ·
`audit_endpoints.py` **46** (add POST-only routes to its `POST_ONLY` set or they report DEAD; probe
with `--base` against a server built from the current tree) · `audit_wiring.py` **136 modules, 0
broken refs**.

Screenshots: `.scratch/shot_one.js <tag> <page> [w] [h]` (honours `JARVIS_PORT`;
**`agent-browser` does not support Windows** — drive real Chrome via `puppeteer-core` from the
managed node workspace). Run browser suites **one at a time**: three in parallel starved
`forex`/`options` into 45s navigation timeouts that read exactly like a regression.

## Rules worth repeating

* **Execution mode must not gate market data.** Paper mode skips `mt5.initialize()`, so the data path
  must call `broker_symbols.ensure_mt5_terminal()` itself or every frame silently becomes synthetic.
  Health flags must be **measured**, not inferred from the execution login.
* **The UI has no request timeout anywhere** — an empty result must still repaint, and a first-paint
  watchdog must state a stall.
* **MT5 times are BROKER-SERVER time, not UTC.** Never hardcode the offset; use
  `jarvis/data/broker_time.py`. Local probes: **`curl --noproxy '*'`** (a proxy is configured and
  otherwise answers "upstream connect failed" for a healthy server).
* **A frontend that reads a key the server never sends renders the empty state on success** — three
  instances (backtest `per_symbol`, `/api/history` read as an object, `grid_dimensions` never
  whitelisted). Read the real payload before writing its reader.
* **A refused order is answered with HTTP 200** — decide an action's outcome from the body's
  `status` (`FAILED`/`BLOCKED`), never from `res.ok`. The broker sends `reason`; the server's own
  validation sends `error` with HTTP 400. `tests/test_action_response_contract.py` keeps the UI's
  refusal set and the backend's emitted set in step.
* `executed_trades.timestamp` is **ambiguous** (entry for an engine-logged trade, exit for an
  MT5-synced one). Use `closed_at`. **When a defect is shared, grep the other front end before
  calling it done.**
* **`curl -s -o /dev/null -w '%{http_code}'` exits 23**, so an `&&` chain built on it silently skips
  every later step. Use `;` between probes.
* Testing discipline (mutations, tautological guards, un-failable assertions, CRLF round-trips,
  `innerHTML` escaping, `read_text()`): **`TRAPS.md` § Testing / § Frontend.**

## Signal quality

**The entry signal has no measured edge — now measured four independent ways.** It *loses* money on
real MT5 data (H1 365d, 94,937 trades: mean R −0.0509, cluster-robust t −3.04, p 0.0067, CI excludes
0); 8/20 per-symbol verdicts flip when the window is halved; only 3/20 beat always-long in both
windows against 5 expected by chance; the refitted score calibration is 0/20 skillful in both
windows (AUC→0.5 as n doubles); and **DSR > 0.95 is met by 0/20 symbols in either window** once
overlapping trades are counted honestly — 94,937 rows are worth **327 independent bets (0.3%)**.
Gate 100% hand-authored; meta-label gate inert (AUC 0.481). Full evidence + backlog:
**`AUDIT-2026-09.md`**.

**Cost basis is a measurement, not a detail.** Every candidate table had to be regenerated after the
SELL-leg fix (`fdf8e58`); before that, half of ~95k audited trades carried no spread at all. Two
independent errors in *my own* measurement tools each moved the verdict — a biased always-long
control (1/20 → 3/20 survivors) and a 10× units error. **Re-derive nothing the scanner already
stored: consume `spread_pips`, and never multiply the bars' raw `spread` by `pip_size` (MT5 reports
points).**

## Environment

Sandbox refuses writes outside the project dir. Bash, not PowerShell. `taskkill` needs
`MSYS_NO_PATHCONV=1`. Python 3.13.12 managed at
`…\binaries\python\versions\3.13.12\python.exe` — works without a venv, though
`…\binaries\python\envs\default\Scripts\python.exe` also exists and two skills cite it.
`rm -rf X && cmd` swallows the command's stdout — run the `rm` separately. Running a script *by
path* puts the **script's** dir on `sys.path`. The server binds **127.0.0.1 only**, so
`http://<LAN-IP>:8501` never works.

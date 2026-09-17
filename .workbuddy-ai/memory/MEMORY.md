# HM-AI / HM Algo 2.0 — index

Injected every session and **silently truncated at the tail — keep it small.** Everything here is a
pointer or a rule that has already cost a session; detail is elsewhere on purpose.

* **`TRAPS.md`** — frontend, testing, data-source and data-integrity traps + project conventions.
* `AUDIT-2026-09.md` — signal-quality evidence, P0/P1/P2 backlog. `YYYY-MM-DD.md` — per-session detail.
* Skills: `diagnose-git-push-auth`, `recover-vanished-working-tree`.
  `audit-trading-system-integrity` §5 now holds the **measurement** traps (stale scan manifest
  misaligning `bar_idx`, `cpu_percent` lying, 1/20-at-0.95 = false-positive rate, grid-boundary
  artefact, effective-n inflation, expiring conventions) — §§1-4 are wiring only. Check it before
  auditing signal quality.

## Non-negotiables

* **`.git/` is not safe here.** Files vanish in the small hours and cannot be recreated (4 incidents;
  the last two followed `git rm` / `git stash push`; cause unknown). Commit and push early, **never
  `git stash`**, keep `git bundle create .git/backup/repo-<ts>.bundle --all` fresh. Some paths are
  readable but **not writable** (`HM_dashboard.bat`, `HM_start.py`,
  `jarvis/intelligence/decision_engine.py`) — write via a hardlink alias in `.scratch/_restore/`;
  `open(alias,'w')` **truncates the shared object**.
* **The live server does not hot-reload.** Python edits do nothing until restart; static
  CSS/JS/templates *are* re-read per request. *A hang that does not reproduce in a fresh interpreter
  is a stale process, not a logic bug.* `py-spy dump --pid <pid>` attaches without restarting;
  `netstat -ano | grep 8501` for the pid.
* **Push** hangs a long time (observed 40s, but also **3m09s** — do not assume a failure at the
  40s mark) and `git status` always says `[gone]` (`.git/refs/remotes/*` is wiped
  immediately after being written). Bypass both: run the push in the **background** redirecting to a
  file, then verify with `git ls-remote` — never the push message and never the exit code alone
  (a hung-but-successful push has shown `EXIT=0` only after minutes). Backticks in `-m` are eaten by
  bash: use `git commit -F <file>`. `/tmp` does not exist; use `.scratch/`.

## Running the platform

`HM_start.py [paper|live]` boots the engine + MT5 client + web server — **real MT5 market data;
`paper` = simulated fills**. `HM_dashboard.bat` is **UI + REST API only** (`mt5_client=None`, so
`auto-selection` answers **503** and `MT5` reads `DISCONNECTED` — both expected, not a fault). Check
`psutil.Process(pid).cmdline()` before concluding the data path is broken. **Never finish a task with
the dashboard alive only as a session background task** — point the user at `HM_dashboard.bat` and say
plainly that closing the window stops it.

## Baselines

**pytest 914 passed / 20 deselected** (883 → 887 with `test_history_window_filter.py`, → 914 with
`test_copilot_intents.py`, which is the first test the copilot has ever had). `tools/`:
`verify_ui_live.py` (46 — was 44; two `/api/copilot/ask` probes. **Starts its own server on :8599** —
stop `.scratch/verify_server.py` first or its probes hit the engine-less scratch server and read as
`attached=False` / `503`, which looks exactly like a regression. Its total is *conditional*: 45 with
no engine, 46 with one) ·
`verify_dashboard_render.js` (**153** — was 88; the backtest fixture added 23, the history fixture 19,
auto-selection + regime-policy 22. Assert against markup **as rendered** — never
`html.replace(/\s+/g,'')`, which eats the space in `<span class="…">` and makes a correct string fail;
bind a value to its own label via `metricValue(html, label)` or a swapped counter passes) · `verify_dashboard_nav.js` (31) · `verify_ui_layout.js`
(**~229** — the total is *not* fixed: it counts controls per viewport, so hiding a control lowers it.
238 → 229 is the ticket's pending row correctly disappearing, not a lost check. Read the FAIL lines,
never the total) · `audit_endpoints.py` (**46** — rises when a new route is added; add POST-only
routes to its `POST_ONLY` set or they report DEAD, and **probe with `--base` against a server built
from the current tree** or a stale engine makes new routes look dead) · `audit_wiring.py` (135
modules, 0 broken refs).
Screenshots: `.scratch/shot_one.js <tag> <page>` — honours `JARVIS_PORT` to point at the standalone
`:8599` instance instead of the user's engine (**`agent-browser` does not support Windows**).
Run the browser suites **one at a time**: three in parallel plus a probe starved `forex`/`options`
into 45s navigation timeouts, which read exactly like a regression.

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
  instances now (backtest `per_symbol`, `/api/history` read as an object, `grid_dimensions` never
  whitelisted). Read the real payload before writing its reader.
* `executed_trades.timestamp` is **ambiguous** (entry for an engine-logged trade, exit for an
  MT5-synced one). Use `closed_at`, which is null unless the row really is closed.

## Signal quality

**The entry signal has no measured edge — now measured four independent ways.** It *loses* money on
real MT5 data (H1 365d, 94,937 trades: mean R −0.0509, cluster-robust t −3.04, p 0.0067, CI excludes
0); 8/20 per-symbol verdicts flip when the window is halved; only 3/20 beat always-long in both
windows against 5 expected by chance; the refitted score calibration is 0/20 skillful in both
windows (AUC→0.5 as n doubles); and **DSR > 0.95 is met by 0/20 symbols in either window** once
overlapping trades are counted honestly — 94,937 rows are worth **327 independent bets (0.3%)**.
Gate 100% hand-authored; meta-label gate inert (AUC 0.481). Full evidence + the P0/P1/P2 backlog:
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
`rm -rf X && cmd` swallows the command's stdout — run the `rm` separately. Running a script *by path*
puts the **script's** dir on `sys.path`. The server binds **127.0.0.1 only**, so
`http://<LAN-IP>:8501` never works.

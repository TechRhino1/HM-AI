# HM-AI / HM Algo 2.0 — index

Injected every session and **silently truncated at the tail — keep it small.** Everything here is a
pointer or a rule that has already cost a session; detail is elsewhere on purpose.

* **`TRAPS.md`** — frontend, testing, data-source and data-integrity traps + project conventions.
* `AUDIT-2026-09.md` — signal-quality evidence, P0/P1/P2 backlog. `YYYY-MM-DD.md` — per-session detail.
* Skills: `diagnose-git-push-auth`, `recover-vanished-working-tree`.

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
* **Push** stalls ~40s and `git status` always says `[gone]` (`.git/refs/remotes/*` is wiped
  immediately after being written). Bypass both, redirect to a file, verify with `git ls-remote` —
  never the push message. Backticks in `-m` are eaten by bash: use `git commit -F <file>`. `/tmp` does
  not exist; use `.scratch/`.

## Running the platform

`HM_start.py [paper|live]` boots the engine + MT5 client + web server — **real MT5 market data;
`paper` = simulated fills**. `HM_dashboard.bat` is **UI + REST API only** (`mt5_client=None`, so
`auto-selection` answers **503** and `MT5` reads `DISCONNECTED` — both expected, not a fault). Check
`psutil.Process(pid).cmdline()` before concluding the data path is broken. **Never finish a task with
the dashboard alive only as a session background task** — point the user at `HM_dashboard.bat` and say
plainly that closing the window stops it.

## Baselines

**pytest 830 passed / 20 deselected.** `tools/`: `verify_ui_live.py` (44) ·
`verify_dashboard_render.js` (88) · `verify_dashboard_nav.js` (31) · `verify_ui_layout.js` (238) ·
`audit_endpoints.py` (44) · `audit_wiring.py`. Screenshots: `.scratch/shot_one.js <tag> <page>`
(**`agent-browser` does not support Windows**).

## Rules worth repeating

* **Execution mode must not gate market data.** Paper mode skips `mt5.initialize()`, so the data path
  must call `broker_symbols.ensure_mt5_terminal()` itself or every frame silently becomes synthetic.
  Health flags must be **measured**, not inferred from the execution login.
* **The UI has no request timeout anywhere** — an empty result must still repaint, and a first-paint
  watchdog must state a stall.
* **MT5 times are BROKER-SERVER time, not UTC.** Never hardcode the offset; use
  `jarvis/data/broker_time.py`.

## Signal quality

**The entry signal has no measured edge.** PF 0.568–1.202 on SWING/H1; 0/20 reach the 1.3 bar at 183d
*and* 365d. Gate 100% hand-authored; meta-label gate inert (AUC 0.481); refitting calibration on
honest data gives 0/20 skillful and *regresses toward chance* as n doubles. See `AUDIT-2026-09.md`.

## Environment

Sandbox refuses writes outside the project dir. Bash, not PowerShell. `taskkill` needs
`MSYS_NO_PATHCONV=1`. Python 3.13.12 managed at
`…\binaries\python\versions\3.13.12\python.exe` — works without a venv, though
`…\binaries\python\envs\default\Scripts\python.exe` also exists and two skills cite it.
`rm -rf X && cmd` swallows the command's stdout — run the `rm` separately. Running a script *by path*
puts the **script's** dir on `sys.path`. The server binds **127.0.0.1 only**, so
`http://<LAN-IP>:8501` never works.

# HM-AI / HM Algo 2.0 — index

Injected every session and **hard-truncated at ~6,520 bytes** — keep under that or the tail vanishes.
Pointers and already-paid-for rules only; detail lives elsewhere.

* `TRAPS.md` — every trap (server/frontend/testing/data-source/data-integrity/CSS/tool-harnesses/
  risk/trade-data). `AUDIT-2026-09.md` — signal quality. **`AUDIT-TRADES-2026-09.md`** — trade-data
  audit. `YYYY-MM-DD.md` — per-session detail.
* Skills: `diagnose-git-push-auth`, `recover-vanished-working-tree`, `audit-trading-system-integrity`
  (§5 = measurement traps; §§1-4 wiring only).

## Non-negotiables

* **`.git/` is not safe here.** Files vanish in the small hours (4 incidents; the last two followed
  `git rm` / `git stash push`). Commit and push early, **never `git stash`**, keep
  `.git/backup/repo-<ts>.bundle --all` fresh. Some paths are readable but **not writable**
  (`HM_dashboard.bat`, `HM_start.py`, `jarvis/intelligence/decision_engine.py`) — write via a
  hardlink alias in `.scratch/_restore/`; `open(alias,'w')` **truncates the shared object**.
* **The live server does not hot-reload.** Python edits need a restart; static CSS/JS/templates are
  re-read per request. *A hang that does not reproduce in a fresh interpreter is a stale process.*
  `py-spy dump --pid <pid>` attaches without restarting; `netstat -ano | grep 8501` for the pid.
* **Push: credential-selector bypass, immediately.** A plain `git push` produced a 0-byte log for
  6m37s and never finished. The helper path **has a space in it**, so `-c credential.helper="!$GCM"`
  fails with `/c/Program: No such file or directory` (`!` goes through sh, which word-splits it). Use
  the tracked `tools/gcm_wrap.sh`: `git -c credential.helper= -c credential.helper='!tools/gcm_wrap.sh'
  push origin main`. `git status` always says `[gone]` — verify with
  `git ls-remote origin refs/heads/main` vs `git rev-parse HEAD`, never the push message or exit code.
  Backticks in `-m` are eaten by bash: use `git commit -F <file>`.

## Running the platform

`HM_start.py [paper|live]` boots engine + MT5 client + web server — **real MT5 data; `paper` =
simulated fills**. **The account is a DEMO one** (`trade_mode == 0`) despite LIVE execution mode, and
`XMGlobal-MT5 5` *looks* like XM's real-account naming — **read `trade_mode`, never the server name**.
`HM_dashboard.bat` is **UI + REST API only** (`mt5_client=None`, so `auto-selection` → **503** and
`MT5` → `DISCONNECTED`: expected, not a fault). Check `psutil.Process(pid).cmdline()` before calling
the data path broken. **Never leave the platform alive only as a session background task** — point
at `HM_dashboard.bat`.

**Routes:** `/` = `dashboard.html` (primary). `/classic` = `index.html` (old terminal). `/stocks`,
`/india`, `/options`, `/console`. **`verify_ui_layout.js` does not cover `/classic`** — measure it with
`.scratch/classic_tabs.js`; `terminal.css` loads *only* in `index.html`, `dashboard.html` uses
`theme_terminal.css`.

## Baselines

**pytest 2531 / 2528 passed / 3 failed / 20 deselected.** The 3 are weekend-only:
`test_market_data_independence.py` asserts `freshness == STALE` while
`SessionEngine.get_market_trading_status()` correctly answers `MARKET_CLOSED` on a Saturday — not a
regression.

`tools/` — `verify_ui_live` · `verify_dashboard_render` · `verify_terminal_render` ·
`verify_copilot_render` · `verify_dashboard_nav` · `verify_ui_layout` · `verify_phone_nav` ·
`audit_endpoints` · `audit_wiring` · `audit_encoding` · `audit_trades` (read-only; needs
`AUDIT_EQUITY=`); all green. Counts + each one's failure mode
(most look like a regression): `TRAPS.md` § Tool harnesses, § Measuring layout.
`tools/gcm_wrap.sh` = the required push credential helper.

## Rules worth repeating

* **Execution mode must not gate market data.** Paper skips `mt5.initialize()`, so the data path must
  call `broker_symbols.ensure_mt5_terminal()` itself or every frame becomes synthetic. Health flags
  must be **measured**, not inferred from the execution login.
* **The UI has no request timeout anywhere** — an empty result must still repaint, and a first-paint
  watchdog must state a stall.
* **MT5 times are BROKER-SERVER time, not UTC.** Never hardcode the offset; use
  `jarvis/data/broker_time.py`. Local probes: **`curl --noproxy '*'`** (a proxy otherwise answers
  "upstream connect failed").
* **A frontend that reads a key the server never sends renders the empty state on success** (3
  instances). Read the real payload before writing its reader.
* **A refused order is answered with HTTP 200** — decide from the body's `status`
  (`FAILED`/`BLOCKED`), never `res.ok`. The broker sends `reason`; the server's own validation sends
  `error` with 400. `tests/test_action_response_contract.py` keeps both sets in step.
* **`executed_trades.timestamp` is not the entry time** — `database.py:287` overwrites it with the
  EXIT time, so `closed_at == timestamp` on 109/109 rows; **neither column is safe**. Run
  `tools/audit_trades.py`. **When a defect is shared, grep the other front end first.**
* **`curl -s -o /dev/null -w '%{http_code}'` exits 23**, so an `&&` chain built on it silently skips
  every later step. Use `;` between probes.
* **Risk limits come from `config/settings.json`** (fixed in `38830eb`); risk state is scoped by
  execution mode. Re-anchor a baseline only via `tools/reset_risk_baseline.py`. Mechanism:
  **`TRAPS.md` § Risk control.**

## Signal quality — **the entry signal has no measured edge (several ways).**

It loses money on real MT5 data (94,937 trades: mean R −0.0509, t −3.04, p 0.0067, CI excludes 0);
3/20 symbols beat always-long in both windows vs 5 by chance; refitted calibration 0/20 skillful;
**DSR > 0.95 is met by 0/20** once overlapping trades are counted honestly (94,937 rows = **327
independent bets, 0.3%**). Evidence + backlog: **`AUDIT-2026-09.md`**. **Consume `spread_pips`; never
multiply the bars' raw `spread` by `pip_size` (MT5 reports points).**

## Environment

Writes outside the project dir are refused. Bash, not PowerShell; `taskkill` needs
`MSYS_NO_PATHCONV=1`. Python 3.13.12 managed at `…\binaries\python\versions\3.13.12\python.exe`.
`rm -rf X && cmd` swallows the command's stdout — run the `rm` separately. A script run *by path* puts
its own dir on `sys.path`. The server binds **127.0.0.1 only**.

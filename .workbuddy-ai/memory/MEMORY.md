# HM-AI / HM Algo 2.0 — index

Injected every session, **hard-truncated at ~6,520 bytes** — stay under or the tail is lost. Rules only;
detail lives elsewhere: **`MASTER_PLAN.md`** (backlog, M0–M5), **`AGENT_SYSTEM.md`** (multi-agent),
**`TRAPS.md`** (every trap), `AUDIT-2026-09.md` (signal quality), `AUDIT-TRADES-2026-09.md` (trade data),
`YYYY-MM-DD.md` (sessions). Skills: `diagnose-git-push-auth`, `recover-vanished-working-tree`,
`audit-trading-system-integrity`, `diagnose-layout-defects`.

## Signal quality — **the entry signal has no measured edge.**

Loses money on real MT5 data; 3/20 beat always-long vs 5 by chance; **DSR > 0.95 met by 0/20** (94,937
rows = **327 independent bets**). **`AUDIT-2026-09.md`**. **Consume `spread_pips`, never raw `spread` ×
`pip_size`.**

## Environment

Writes outside the project dir are refused. Bash, not the other Windows shell. Python 3.13.12 at
`…\binaries\python\versions\3.13.12\python.exe`. **`env` as a command wrapper silently swallows its
payload** — see Baselines.

## Non-negotiables

* **`.git/` is not safe here** — files vanish overnight (4 incidents; two after `git stash`). Commit and
  push early, **never `git stash`**. Some paths are **not writable** — hardlink alias in `.scratch/_restore/`.
* **No hot-reload.** Python edits need a restart; static files and templates are re-read per request.
  *A hang that does not reproduce in a fresh interpreter is a stale process.* `py-spy dump --pid <pid>`.
* **Push: bypass the credential selector.** Plain `git push` hung 6m37s; the helper path **has a space**.
  `git -c credential.helper= -c credential.helper='!tools/gcm_wrap.sh' push origin main`. `git status`
  always says `[gone]` — verify with `git ls-remote`. Use `git commit -F <file>`.

## Running the platform

`HM_start.py [paper|live]` — **real MT5 data; `paper` = simulated fills**; **default `live`**
(`HM_start.py:360`). **The account is DEMO** (`trade_mode == 0`) despite LIVE mode — **read `trade_mode`,
never the server name**. **Session background tasks are killed at end of turn** — launch detached via
`Start-Process` (PowerShell-from-bash and `cmd.exe`-from-PowerShell are both blocked). `HM_dashboard.bat`
is dashboard-only. Routes: **`TRAPS.md`**.

## Baselines

**pytest 3004 passed / 0 failed / 20 deselected** (junit `tests=3018 failures=0 errors=0 skipped=1`) —
green, not tolerated. Parse `--junit-xml`; the harness truncates stdout so `-rf` never prints.

**NEVER wrap a command in `env`** — `env FOO=bar python -c "print(1)"` prints **nothing**, exit 0: it
swallows whatever it wraps. That, not `--basetemp`, is why pytest "succeeded" with an empty log.
`python -m pytest` also no-ops here (yet `pytest --version` works). Working invocation:

`NO_PROXY='*' <python> -c "import pytest,sys; sys.exit(pytest.main(['-q','--junit-xml=.scratch/pytest.xml']))"`

~3.5 min. **A command that "succeeds" instantly with no output — suspect the wrapper, not the payload.**
`nohup &` / `run_in_background` do not survive here. `tools/` — 12 harnesses, all green.

## Rules worth repeating

* **Execution mode must not gate market data.** Paper skips `mt5.initialize()`, so the data path must call
  `broker_symbols.ensure_mt5_terminal()` itself or every frame is synthetic. **Never call
  `mt5.initialize()` on a request path** — no terminal ⇒ GIL held forever.
* **MT5 times are BROKER-SERVER time, not UTC** — `jarvis/data/broker_time.py`. Probes: `curl --noproxy '*'`.
* **A frontend reading a key the server never sends renders the empty state on success** (3×). **A refused
  order is answered with HTTP 200** — decide from the body's `status`, never `res.ok`.
* **Back up `jarvis_history.db` with `sqlite3.Connection.backup()`, never `cp`** (WAL + live writer).
* **`executed_trades.timestamp` is not the entry time** — `database.py:287` overwrites it with the EXIT
  time; **neither column is safe**. `tools/audit_trades.py`. **Shared defect? grep the other front end.**
* **`curl -s -o /dev/null -w '%{http_code}'` exits 23** — an `&&` chain on it silently skips later steps.
* **A price must be finite AND `> 0`.** `_is_finite(0.0)` is True; an empty frame gives `bid = 0.0`, from
  which a **negative** stop passed the last gate.
* **`fetch_recent_trades` calls `sync_mt5_history` on every read** — tests get real broker deals. Fix at
  the fixture boundary with `monkeypatch.setattr`, not by guarding production (broke 4 tests).
* **Unknown R: withhold a recorded quantity, default a hyperparameter.** Bandit `rewards` is in R → leave
  it; in `update_online` R only weights the gradient → omit the arg (`None` ⇒ +1R). AI6.
* **Never count a mutation as evidence until it turns something red** — it may be unreachable, and
  mutations on one file shadow each other. **"Dead" ≠ "unwired"** (AI9's calibration refuses 11 of 16
  symbols → opt-in). **No production caller may make a fallback branch the most dangerous code in the
  file.** **Latent is not a defence** (AI7). **Check the execution mode before the broker call** (A18).
  Hermeticity needs both ends (AI8). **Detail: `TRAPS.md`.**

## UI / mobile

* **Only `dashboard.html` loads `ios_mobile.css`.** `/` and `/dashboard` both serve it (not `index.html`
  — that is `/classic`), so `/` *looks* fixed while `/stocks /india /options /console` run their own page
  sheets + `ios_pages.css`. Check which sheet a page loads before believing a fix landed.
* **`@media (max-width: 1024px)` only *should* mean desktop is untouched — prove it.** At 1440px snapshot
  `getComputedStyle` per element, `sheet.disabled = true`, snapshot again, diff, in one page load
  (`.scratch/prove_desktop.js`). Caught a `<span>` wrap recolouring the brand at every width — fix at the
  source with `:not()`, never by patching colour back in the new sheet.
* **A page sheet's `!important` beats `hm_ui.css`'s specificity**, so several "unified" rules never
  applied. Visually-hidden text has 3 class names — `.tt-sr-only`, `.sr-only`, `.cx-visually-hidden`.
* **A page in `PAGES` is not a page that is measured.** The verifier's market pages ran 3 checks; its
  `GLASS_SELECTORS` are dashboard-only, so the four glass pages' glass was asserted nowhere. Now a
  per-page `hud` + 3 checks **gated to `vp.width <= 1024`**. 238/238 → **321/321**.
* **A server-rendered control whose handler is defined by a later blocking script is dead on arrival.**
  The dock's inline `onclick="switchMobileXView(...)"` resolves at *click* time, but the handler lives
  ~400 lines later — so every early tap threw `switchMobileXView is not defined` and did nothing. Reads
  as **intermittent** (warm-cache smoke test passes; ~1 run in 3 fails). Fixed by `mobile_dock.js` loaded
  **before** the dock, replaying an early tap via `window.registerMobileView`. `/console` and `/dashboard`
  were never affected — delegated listeners, no inline `onclick`.
* **A recorded field that is read but never written silently reverts user state** —
  `state.activeMobileView` was read by the resize handler and the init in all three controllers, written
  by only one. **Grep for the write, not just the read.**
* **`getComputedStyle` reports an animation on a `display:none` element** — a `querySelector` matched a
  hidden bottom-sheet modal and reported its `slideUpSheet` presentation animation as the content card's
  entry motion. Filter by `getBoundingClientRect().width > 0`. `slideUpSheet` there is *correct*.
* **Prove a fix is non-vacuous by reverting it** — removing the bootstrap turned 7 checks red.
  `tools/verify_mobile_dock.js` holds the controller to force the race. Detail: `2026-09-22.md`.

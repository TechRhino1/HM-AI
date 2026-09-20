# HM-AI / HM Algo 2.0 — index

Injected every session and **hard-truncated at ~6,520 bytes** — stay under or the tail is lost.
Pointers and paid-for rules only; detail lives elsewhere.

* Root: **`MASTER_PLAN.md`** — ranked backlog + milestones M0–M5. **`AGENT_SYSTEM.md`** — the
  multi-agent coding system (roster, workflow, Definition of Done).
* `TRAPS.md` — every trap (server/frontend/testing/data-source/data-integrity/CSS/tool-harnesses/
  risk/trade-data). `AUDIT-2026-09.md` — signal quality. **`AUDIT-TRADES-2026-09.md`** — trade-data
  audit. `YYYY-MM-DD.md` — per-session detail.
* Skills: `diagnose-git-push-auth`, `recover-vanished-working-tree`, `audit-trading-system-integrity`
  (§5 = measurement traps; §§1-4 wiring only).

## Non-negotiables

* **`.git/` is not safe here.** Files vanish overnight (4 incidents; two after `git stash`). Commit and
  push early, **never `git stash`**, keep `.git/backup/repo-<ts>.bundle --all` fresh. Some paths are
  **not writable** — write via a hardlink alias in `.scratch/_restore/`.
* **The live server does not hot-reload.** Python edits need a restart; static files are re-read per
  request. *A hang that does not reproduce in a fresh interpreter is a stale process.* `py-spy dump
  --pid <pid>`; `netstat -ano | grep 8501` for the pid.
* **Push: bypass the credential selector.** A plain `git push` hung 6m37s. The helper path **has a
  space in it**, so `-c credential.helper="!$GCM"` fails. Use `git -c credential.helper= -c
  credential.helper='!tools/gcm_wrap.sh' push origin main`. `git status` always says `[gone]` — verify
  with `git ls-remote origin refs/heads/main`. Backticks in `-m` are eaten: use `git commit -F <file>`.

## Running the platform

`HM_start.py [paper|live]` — **real MT5 data; `paper` = simulated fills**. **The account is DEMO**
(`trade_mode == 0`) despite LIVE mode — **read `trade_mode`, never the server name**. **Don't leave the
platform alive only as a session background task** — use `HM_dashboard.bat`. Routes and the `/classic`
gap: **`TRAPS.md` § Running the platform**.

## Baselines

**pytest 2920 passed / 0 failed / 20 deselected** (2026-09-20) — green, not tolerated. Parse
`--junit-xml=...`: the harness truncates pytest's stdout tail, so `-rf` never prints.

**Run the suite with `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy NO_PROXY='*'` and
`--basetemp=.scratch/ptmp-$TS`** — a proxy hangs localhost HTTP; cleaning >50 temp entries trips the
bulk-delete guard: exit 1 with every test passing. A *fixed* one is worse: pytest removes it.

`tools/` — 11 harnesses, all green; failure modes: **`TRAPS.md` § Tool harnesses**. `tools/gcm_wrap.sh`
= the push helper.

## Rules worth repeating

* **Execution mode must not gate market data.** Paper skips `mt5.initialize()`, so the data path must
  call `broker_symbols.ensure_mt5_terminal()` itself or every frame becomes synthetic. Health flags
  must be **measured**, not inferred. **Never call `mt5.initialize()` on a request path** — with no
  terminal it holds the GIL forever and one request wedges the server; only not making the call is a
  fix.
* **The UI has no request timeout** — an empty result must still repaint; a watchdog must state a
  stall.
* **MT5 times are BROKER-SERVER time, not UTC** — use `jarvis/data/broker_time.py`. Local probes:
  **`curl --noproxy '*'`** (else "upstream connect failed").
* **A frontend reading a key the server never sends renders the empty state on success** (3×) — read
  the real payload first.
* **A refused order is answered with HTTP 200** — decide from the body's `status`
  (`FAILED`/`BLOCKED`), never `res.ok`. Broker sends `reason`; server validation sends `error` with
  400.
* **`executed_trades.timestamp` is not the entry time** — `database.py:287` overwrites it with the
  EXIT time; **neither column is safe**. Run `tools/audit_trades.py`. **Shared defect? grep the other
  front end first.**
* **`curl -s -o /dev/null -w '%{http_code}'` exits 23** — an `&&` chain on it silently skips every
  later step. Use `;` between probes.
* **Risk limits come from `config/settings.json`**; risk state is scoped by execution mode. Re-anchor
  only via `tools/reset_risk_baseline.py`. **`TRAPS.md` § Risk control.**
* **A price must be finite AND `> 0`.** `_is_finite(0.0)` is True, and an empty frame gives
  `bid = 0.0` — from which an entry was minted and a **negative** stop passed the last gate.
* **A label must describe the thing it names.** Provenance of the anchor price, not the series;
  `expected_value` = outcome (112/112); `ai_score` = `85.0`.
* **Pruning per-ticket state destroys the only record of the path** — close hardcoded `mfe=0.0`.
  D19: retain, NULL when unsampled.
* **Truncating values cannot shrink a payload** — eliding every field over 256 B left the 63 KB
  snapshot at 94%. Drop *fields*: 33%.
* **A hung native call holds the MT5 lock forever** — `TimeoutGuard` bounds the *caller*, not the lock;
  5/5 wedged. `TrackedRLock` bounds the wait and names the holder. Keep serialisation: MT5 bindings
  are not thread-safe.
* **Unknown R: withhold a recorded quantity, default a hyperparameter.** Bandit `rewards` is
  denominated in R → leave it alone (the win/loss IS measured, so still record it). In
  `update_online` R only weights the gradient → omit the arg; `None` coerces to +1R. AI6.
* **Hermeticity needs both ends.** Stateful components load eagerly in `__init__`, so wrapping only a
  backtest's *run* is too late; `SelfLearningEngine` re-reads the journal at *call* time, so the
  constructor alone is not enough. AI8.
* **A test hardcoding a version number goes vacuous when it moves** — derive "newer"
  (`SCHEMA_VERSION + 1`), never write `2`. AI10: two D3 refusal tests silently stopped testing.

## Signal quality — **the entry signal has no measured edge.**

Loses money on real MT5 data; 3/20 beat always-long vs 5 by chance; **DSR > 0.95 met by 0/20** once
overlaps are counted honestly (94,937 rows = **327 independent bets**). **`AUDIT-2026-09.md`**.
**Consume `spread_pips`; never multiply raw `spread` by `pip_size`.**

## Environment

Writes outside the project dir are refused. Bash, not the other Windows shell. Python 3.13.12 at
`…\binaries\python\versions\3.13.12\python.exe`; binds **127.0.0.1 only**. Rest: **`TRAPS.md` §
Environment**.

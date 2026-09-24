# HM-AI / HM Algo 2.0 — index

Injected every session; the **tail** is what gets lost, so detail belongs in the files below, not here.
Rules only; detail elsewhere: **`MASTER_PLAN.md`** (backlog M0–M5), **`AGENT_SYSTEM.md`** (multi-agent),
**`TRAPS.md`** (every trap), `AUDIT-2026-09.md` (signal quality), `AUDIT-TRADES-2026-09.md` (trade data),
`YYYY-MM-DD.md` (sessions), `AUDIT-3-TRACKS-2026-09-23.md` (§J, 3-track audit). Skills:
`diagnose-git-push-auth`, `recover-vanished-working-tree`, `audit-trading-system-integrity`,
`diagnose-layout-defects`.

## Signal quality — **the entry signal has no measured edge.**

Loses money on real MT5 data; 3/20 beat always-long vs 5 by chance; **DSR > 0.95 met by 0/20** (94,937
rows = **327 independent bets**). **`AUDIT-2026-09.md`**. **Consume `spread_pips`, never raw `spread` ×
`pip_size`.**

## The news calendar is a FABRICATED input — **check `is_fallback`**

Both live feeds are down (FairEconomy **429**, MyFxBook **403 Cloudflare** — `urllib` runs no JS), so the
engine substituted a **hardcoded 7-event plan** and **padded** short real feeds with it, unlabelled. It cost
MACRO a constant **−25** (**−4.2 on `ai_score`**, a hard gate) and handed out **+8.0 / +0.20 conviction**
via `evaluate_post_news_sweep_reaction`. Now stamped; every consumer must check `is_fallback`.
**§O, `AUDIT-3-TRACKS-2026-09-23.md`.** *Generalises: "a labelled fallback beats an unlabelled
fabrication" — a fallback that invents data must say so.*

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

**pytest junit `tests=3339 failures=0 errors=0 skipped=2`, 0 failing testcases** (2026-09-24) — green,
not tolerated. Parse `--junit-xml`; the harness truncates stdout so `-rf` never prints. **The sandbox's
bulk-delete guard is per-TURN and cumulative** — after enough deletions a suite run returns ~169 bogus
`errors` (`SAFE_DELETE_BULK_REJECTED`), which is NOT a regression; a clean run needs an intact budget.

**NEVER wrap a command in `env`** — `env FOO=bar python -c "print(1)"` prints **nothing**, exit 0: it
swallows whatever it wraps. That, not `--basetemp`, is why pytest "succeeded" with an empty log.
`python -m pytest` also no-ops here (yet `pytest --version` works). Working invocation:

`NO_PROXY='*' <python> -c "import pytest,sys; sys.exit(pytest.main(['-q','--junit-xml=.scratch/pytest.xml']))"`

~3.5 min. **A command that "succeeds" instantly with no output — suspect the wrapper, not the payload.**
`nohup &` / `run_in_background` do not survive here. `tools/` — 12 harnesses, all green.

## Measurement instruments

* **A cache key that does not cover the instrument is a cache that lies.** Fixing a harness changes its
  outputs, so key on the harness version (a `salt`), not only on its inputs.
* **Check the instrument before believing the number.** §J's "noise in both directions" came from a
  harness whose `apply_registry(None)` was a no-op, so **only the FIRST symbol scanned had a genuine
  incumbent arm** and every later symbol was measured against itself. Signature: `A == B` for all but the
  first. Corrected, §J is **ADVERSE** (−47…−87 R, 7/8 symbols, 5/5 exit models) — and **96% of it is a
  VOLUME effect**: 412 extra trades at an unchanged ~−0.2 R each. **Trade count, not entry quality, is
  the lever here.**
* **A wall-clock timeout on GIL-bound thread work is load-dependent.** `ParallelAnalystCluster` allows
  MACRO 2.0s while the news fetch it calls allows 5–6s against a 90s TTL (a miss measured 1.32s = 66% of
  the budget), so the fallback fires on a merely slow network and substitutes a **fabricated** score-50
  into a live decision. One symbol+registry gave EXEC **51 / 53 / 56**. Retract any "deterministic" claim
  made under load.
* **A fallback must not claim confidence it does not have**, and say plainly when a fix is visibility only
  — `AnalystReport.confidence` has no consumer in `jarvis/`.

## Rules worth repeating

* **Execution mode must not gate market data.** Paper skips `mt5.initialize()`, so the data path must call
  `broker_symbols.ensure_mt5_terminal()` itself or every frame is synthetic. **Never call
  `mt5.initialize()` on a request path** — no terminal ⇒ GIL held forever.
* **MT5 times are BROKER-SERVER time, not UTC** — `jarvis/data/broker_time.py`. Probes: `curl --noproxy '*'`.
* **A frontend reading a key the server never sends renders the empty state on success** (3×). **A refused
  order is answered with HTTP 200** — decide from the body's `status`, never `res.ok`.
* **A test can pin a bug, so a green suite is not evidence the bug is gone.** `test_forecast_not_overwritten`
  asserted the buggy `timestamp = ?` literal; `test_parallel_runner` asserted the old `confidence == 0.50` —
  both would have gone red *on the fix*. When you fix a defect, grep the suite for the buggy literal.
  **`fetch_recent_trades` fires a real sync and stamps `_last_mt5_sync`** — a test that reads a row through
  it first gets its own sync throttled to a no-op.
* **Back up `jarvis_history.db` with `sqlite3.Connection.backup()`, never `cp`** (WAL + live writer).
* **`executed_trades.timestamp` is not the entry time** — `database.py:287` overwrote it with the EXIT time
  (fixed 2026-09-24); **neither column is safe**. `tools/audit_trades.py`. **Shared defect? grep the other front end.**
* **`curl -s -o /dev/null -w '%{http_code}'` exits 23** — an `&&` chain on it silently skips later steps.
* **A price must be finite AND `> 0`.** `_is_finite(0.0)` is True; an empty frame gives `bid = 0.0`, from
  which a **negative** stop passed the last gate.
* **`fetch_recent_trades` calls `sync_mt5_history` on every read** — tests get real broker deals. Fix at the
  fixture boundary with `monkeypatch.setattr`, not by guarding production (broke 4 tests).
* **Unknown R: withhold a recorded quantity, default a hyperparameter.** Bandit `rewards` is in R → leave it;
  in `update_online` R only weights the gradient → omit the arg (`None` ⇒ +1R). AI6.
* **Never count a mutation as evidence until it turns something red** — it may be unreachable, and mutations
  on one file shadow each other. **"Dead" ≠ "unwired"** (AI9's calibration refuses 11 of 16 symbols →
  opt-in). **No production caller may make a fallback branch the most dangerous code in the file.**
  **Latent is not a defence** (AI7). **Check the execution mode before the broker call** (A18). Hermeticity
  needs both ends (AI8). **Detail: `TRAPS.md`.**

## UI / mobile

Detail: **`UI-MOBILE.md`** (split out 2026-09-24 — it was being truncated off the tail of this
index). The two that bite most: **check which stylesheet a page loads before believing a fix
landed** (only `dashboard.html` loads `ios_mobile.css`; `/` *looks* fixed while `/stocks /india
/options /console` run their own sheets), and **a server-rendered control whose handler is
defined by a later blocking script is dead on arrival** (reads as *intermittent*).

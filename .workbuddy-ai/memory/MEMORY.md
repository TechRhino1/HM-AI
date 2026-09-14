# HM-AI / JARVIS — durable project notes

Curated facts that outlive a single session. Daily detail lives in `YYYY-MM-DD.md`.

## Working in this environment

* **The sandbox refuses writes outside the project directory.** `git bundle create /d/HM_AI/...`
  fails with *No such file or directory*. Keep any backup or scratch file inside `D:\HM_AI\HM-AI`.
* **Use Bash, not the PowerShell tool.** The PowerShell tool returns no output at all here, and
  `tasklist` / `Get-Process` are blocked, so process-level forensics are unavailable.
* **Python is the managed 3.13.12** (`python` on PATH). There is no project venv; `pytest`,
  `pandas`, `numpy` are importable directly.

## Data-loss hazard (unresolved, twice observed)

The entire `jarvis/` package has twice vanished from the working tree — 2026-09-13 (~03:28) and
2026-09-14 (03:50:52) — both times right after a `git rm`. Ruled out: git hooks, `hooksPath`,
`fsmonitor`, WorkBuddy automations, disk pressure, a destructive script in the repo. Cause unknown;
AV/EDR quarantine and cloud-sync clients remain the leading hypotheses.

Consequences to work by:

* **Commit every new file as soon as it is written.** Both incidents only cost rework for
  *untracked* files. Everything committed was recoverable.
* **Recovery recipe:** `git checkout -- jarvis/` restores the working tree **from the index**,
  which preserves any staged deletions. Prefer it over `git checkout HEAD -- jarvis/`, which would
  resurrect files you deliberately `git rm`-ed.
* A full history bundle lives at `.git/backup/repo-<timestamp>.bundle`. `.git/` survives these
  incidents. Refresh it periodically.

## Conventions worth knowing

* **Backtest job statuses are `QUEUED | RUNNING | DONE | FAILED | CANCELLED`** — `DONE`, not
  `COMPLETED`. `POST /api/backtest/run` answers **202 Accepted** with a `job_id`.
* **Localhost requests authenticate as admin.** `JarvisRequestHandler._get_auth_user()` returns an
  admin identity when `_is_local_request()` is true, so a local curl needs no token.
  `/api/intelligence/*` and `/api/backtest/*` are deliberately **not** public GET endpoints.
* **`normalise_style()` maps any unrecognised style name to `SWING`** rather than rejecting it. A
  typo becomes a swing scan. Validate style names upstream of the engine.
* **Style → primary timeframe:** SWING→H1, DAY_TRADING→M15, SCALP→M5. `optimizer.PRIMARY_TIMEFRAME`
  is asserted equal to `data_feed.style_timeframes(style)["primary"]` in the test suite.
* **Console UI:** `/` and `/console` serve `console.html`; `/classic` serves the legacy
  `index.html`. `console.js` is an IIFE with no exported global. Console CSS consumes the
  `hm_ui.css` token set and defines no new tokens.
* **Cross-style consensus** lives in `jarvis/intelligence/mode_aggregator.py`. One style voting
  alone is never tradeable, by construction. Measured mode weights: SWING 0.346, DAY_TRADING
  0.1064, SCALP 0.1287 (all below neutral — all three modes lost money over the 6-month window).
* **Verification entry points:** `python tools/verify_console_live.py` (21 live HTTP checks, exits
  non-zero on failure), `tests/test_mode_aggregator.py`, `tests/test_backtest_optimizer.py`.

## Pushing to GitHub — a plain `git push` will hang

`git push` on this machine **stalls silently** (no error, no prompt) because `helper-selector` is
first in the credential chain and blocks for ~40s+ before GCM ever runs. `git credential fill`
hanging at rc=124 is the confirming signature; `git ls-remote` succeeding proves the network is
fine and the problem is the write path only.

**Always push with the selector bypassed:**

```bash
GCM="C:/Users/Itrai/.workbuddy-ai/binaries/PortableGit/versions/1.2.0/mingw64/bin/git-credential-manager.exe"
timeout 180 git -c credential.helper= -c credential.helper="!$GCM" push origin main > /tmp/pushout.txt 2>&1
echo "exit=$?"; cat /tmp/pushout.txt
```

Setting upstream tracking does **not** fix this — the bypass is still required. Redirect to a file
and read `$?`; piping masks the exit code. Then verify the remote SHA via `git ls-remote` rather
than trusting the local push message. Remote: `https://github.com/TechRhino1/HM-AI.git`.

## Commit-message hazard

Backticks inside a `git commit -m` argument are interpreted by bash and get substituted away. Use
plain text or single quotes in commit messages.

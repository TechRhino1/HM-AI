# HM-AI / JARVIS — durable project notes

Curated facts that outlive a single session. Daily detail lives in `YYYY-MM-DD.md`.

## Working in this environment

* **The sandbox refuses writes outside the project directory.** `git bundle create /d/HM_AI/...`
  fails with *No such file or directory*. Keep any backup or scratch file inside `D:\HM_AI\HM-AI`.
* **Use Bash, not the PowerShell tool.** The PowerShell tool returns no output at all here, and
  `tasklist` / `Get-Process` are blocked, so process-level forensics are unavailable.
* **Python is the managed 3.13.12** (`python` on PATH). There is no project venv; `pytest`,
  `pandas`, `numpy` are importable directly.

## Data-loss hazard (unresolved, three times observed)

Three incidents, all clustered in the small hours, all following a git operation that rewrites
refs or the index:

| When | What vanished | Preceded by |
|---|---|---|
| 2026-09-13 ~03:28 | the whole `jarvis/` package | a `git rm` |
| 2026-09-14 03:50:52 | the whole `jarvis/` package | a `git rm` |
| 2026-09-15 ~05:31 | **the git object store** — all three `.pack` files, `refs/heads/main`, `refs/remotes/origin/main` | a `git stash push` |

Ruled out: git hooks, `hooksPath`, `fsmonitor`, WorkBuddy automations, disk pressure, a destructive
script in the repo. Cause unknown; AV/EDR quarantine and cloud-sync clients remain the leading
hypotheses. **Note the third incident: `.git/` is NOT safe.** The earlier claim that it was is
wrong — the packfiles inside it were deleted while the working tree survived untouched.

Consequences to work by:

* **Commit and push early.** The remote is the only durable store. Everything committed has always
  been recoverable; only uncommitted work has been at risk.
* **Recovery recipe, working tree lost:** `git checkout -- jarvis/` restores **from the index**,
  which preserves any staged deletions. Prefer it over `git checkout HEAD -- jarvis/`, which would
  resurrect files you deliberately `git rm`-ed.
* **Recovery recipe, object store lost** (what the third incident needed — this is the one to
  remember, because the usual advice is useless when `HEAD` itself is unreadable):

  ```bash
  # 1. Read the reflog. It survives, and its last entry names the commit you were on.
  cat .git/logs/HEAD | tail -5
  # 2. Prove the object is really gone before assuming so.
  git cat-file -t <sha>          # "could not get object info" == gone
  # 3. Re-download the objects. The remote is the source of truth.
  git fetch origin
  # 4. Recreate the refs by hand — fetch alone does not restore a deleted local branch.
  git update-ref refs/heads/main <sha>
  git update-ref refs/remotes/origin/main <sha>
  ```

  A `git fetch` recreates `origin/main` but leaves the local branch missing, so step 4 is required.
* **Never `git stash` in this repo.** It is the operation that triggered the third incident and it
  buys nothing: to compare old and new behaviour, edit the file back with the editor, run the test,
  and edit it forward again. That is what the provider-recursion work did, and it is safe.
* Refresh the history bundle at `.git/backup/repo-<timestamp>.bundle` (`git bundle create <path>
  --all`). The bundle is a single file and has survived all three incidents; the packs have not.

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
* **Console UI:** `/` and `/dashboard` serve `dashboard.html` (the advanced terminal); `/console`
  serves `console.html`; `/classic` serves the legacy `index.html`. `console.js` and `dashboard.js`
  are IIFEs with no exported global.
* **`_csv()` in `intelligence_api.py` returns `None`, not `[]`,** when a query parameter is absent.
  Iterating the result directly raises. Use `set(_csv(query, "x") or [])`.
* **The broker symbol is what the engine resolves, not the canonical one.** `resolve("WTI")` is
  always a registry key; `resolve("OILCash#")` is the lookup that can miss `_ALIAS_MAP` and silently
  fall back to a generic FX spec. `tests/test_symbol_registry.py` guards this for every manifest.
* **`max_evaluations` is a budget per search, not per optimiser** (`_budget_start`/`_reset_budget`).
  A low budget with an absolute counter made later modes return the unsearched seed geometry while
  reporting a full result.
* **Cross-style consensus** lives in `jarvis/intelligence/mode_aggregator.py`. One style voting
  alone is never tradeable, by construction. Measured mode weights: SWING 0.346, DAY_TRADING
  0.1064, SCALP 0.1287 (all below neutral — all three modes lost money over the 6-month window).
* **Regime-conditioned profit optimisation** lives in `jarvis/backtesting/regime_optimizer.py`
  (run: `python tools/optimise_regime.py`; served at `/api/backtest/regime-policy`). Candidate
  tables carry a real `regime` column and `select_sequential` already accepted a `regimes=` filter —
  conditioning is therefore free, because simulations cache on the geometry alone. Its disable
  thresholds deliberately duplicate `winrate_targeting.regime_edge_table`; a test enforces they
  agree.
* **The quote fallback must never call the profile hydrators.** `TradingViewDataProvider.
  fetch_quotes()` used to read its fallback price from `get_india_profile()` / `get_stock_profile()`,
  and those hydrate — hydration resolves quotes by calling back into `fetch_quotes()` for the same
  symbol. The cycle was unbounded (measured: 233 re-entries for one NIFTY lookup) and nothing raised,
  because `hydrate_batch` wraps its `fetch_quotes` call in `except Exception` and `RecursionError` is
  an `Exception`. So `/api/india/*` never answered for any symbol reaching that branch — every index,
  since NIFTY and BANKNIFTY match no earlier pattern — and the price that eventually came back was
  the last-resort 150.0 instead of NIFTY's own 24175.65. Read `INDIA_UNIVERSE` / `STOCK_UNIVERSE`
  directly. Guarded by `tests/test_provider_recursion.py`.
* **A test that only looks for a raised exception can miss an unbounded recursion** when an
  intermediate frame swallows it. Pin the *call* (patch the callee with a recorder) instead.
* **Clear both caches in `setUp` when testing anything that caches.** `_quote_cache` has a 15s TTL,
  and a sibling test warming it silently made a re-entry test pass against the broken code.
* **Never seed a modelled value from `hash()`.** CPython salts it per process (PYTHONHASHSEED), so
  the value changes on every interpreter start. Use `jarvis.data.determinism.stable_seed` — the one
  definition, in a module that imports nothing. Eight sites were seeding from `hash()`: option-chain
  skew/OI/volume, the options signal `pcr` (which picks BUY CALL vs BUY PUT), candle walks, the
  Monte Carlo seed, and — user-facing — earnings dates, implied volatility and `is_fno_ban`.
* **Salting is constant WITHIN a process, so "call it twice and compare" passes against this bug.**
  The discriminating test must spawn a subprocess under a different `PYTHONHASHSEED`. Same trap for
  `RecursionError`: an intermediate `except Exception` swallows it, so pin the *call*, not the
  exception. Five tests this cycle passed against the code they were meant to catch.
* **Test against pre-change code with `git archive HEAD <path> | tar -x -C .scratch/oldtree`,** never
  `git stash`.
* **Environment gotchas:** plain `pytest -q` exits 1 *after all tests pass* (a safe-delete hook
  blocks pytest's temp-dir cleanup) — use `--basetemp=.scratch/pttmp`. And prefixing a command with
  `rm -rf X &&` silently swallows the whole command's stdout; run the `rm` separately.
* **Verification entry points:** `python tools/verify_ui_live.py` (44 live HTTP checks, exits
  non-zero on failure), `python tools/verify_dashboard_render.js` (88 headless render checks, needs
  `node`), `python tools/audit_wiring.py`, `python tools/audit_endpoints.py`,
  `tests/test_mode_aggregator.py`, `tests/test_backtest_optimizer.py`,
  `tests/test_regime_optimizer.py`, `tests/test_ui_wiring.py`, `tests/test_provider_recursion.py`.

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

## A self-retriggering MutationObserver freezes the page with no error

**`setAttribute` queues a mutation record even when the value is unchanged.** So an observer whose
callback writes an attribute inside its own `subtree` + `attributeFilter` re-queues itself on every
pass, and because microtasks drain before the browser may paint or dispatch input, the main thread
blocks **permanently, silently** — no exception, no console error, no failed request. It looks like
a crash and is reported as one.

The rule: **an observer callback must write nothing it observes.** Guard every write with a
compare (`setIfChanged`) and add a re-entrancy flag. Deleting the observer is not a fix — assert the
feature still works, or "delete the feature" passes your test.

It only fires on *interaction* when the initial sync ran before the observer was attached, which is
why an idle page looks perfectly healthy. Two live examples were in `hm_ui.js` (the tab ARIA sync);
the other two observers there are safe because their callbacks write outside their filter.

## Diagnosing a blocked browser main thread

* **`page.evaluate` ignores its own `timeout` option.** A naive probe hangs the driver instead of
  failing. Always race it against a timer.
* **Run a control phase first** — load, do not interact, probe for 30s. Blocks while idle ⇒ poll
  loop; responsive while idle but blocks on click ⇒ the click handler. This halves the search space.
* **`Debugger.enable` + `Debugger.pause`** names the blocking frame. A pause arriving ⇒ JavaScript
  (read `callFrames`). No pause ⇒ native block (dialog or synchronous XHR).
* Chrome is installed at `C:\Program Files\Google\Chrome\Application\chrome.exe`, Edge too.
  `puppeteer-core` is in the managed node workspace — set `PUPPETEER_ROOT` / `NODE_PATH`; launch with
  `--no-sandbox --disable-gpu --disable-dev-shm-usage`. The `agent-browser` skill does **not**
  support Windows.
* `node --check` proves a file parses and says nothing about a blocked thread.
* **`tools/verify_dashboard_nav.js`** is the working harness (31 live checks). Run it with
  `PUPPETEER_ROOT=<node workspace>/node_modules node tools/verify_dashboard_nav.js`; it exits 2 when
  it cannot run (no server, no browser) so that is never mistaken for a pass.

## Dashboard rail facts

* `POST /api/telemetry_state`'s `account` object already carries `login`, `name`, `server`,
  `company`, `balance`, `equity`, `margin`, `free_margin`, `margin_level`, `leverage`, `profit`,
  `currency`, `trade_allowed`, `last_sync_time`. The account dropdown needs no server change.
* The canonical cross-page destinations and labels, copied from `stocks.html` / `india.html`, are
  `/` Forex·Crypto, `/stocks` US Stocks, `/india` India Stocks, `/options` India Options.
  `dashboard.html` carries them in a `.tt-dropdown`; `markActiveNav()` in `hm_ui.js` already styles
  `.market-nav-item`, so the current page is marked from either entry point.
* `.tt-rail` is a **top bar** (`grid-area: rail`, sticky), not a sidebar. The account strip is pushed
  right by `.tt-rail__spacer`.
* `[hidden]` is a weak UA rule: any `display` you declare on a panel silently beats it. Declare
  `.your-panel[hidden] { display: none; }` explicitly.


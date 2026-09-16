# HM-AI / JARVIS — durable project notes

Curated facts that outlive a session. Detail lives in `YYYY-MM-DD.md`.
**Keep this small** — it is injected every session and silently truncated at the tail, which
has already lost the last two sections once. Long diagnostics go to `AUDIT-2026-09.md`.

## Git: push hangs; remote refs are eaten

`git push` stalls ~40s because `helper-selector` sits first in the credential chain. Bypass, and
**redirect to a file** (piping masks `$?`); verify with `git ls-remote`, never the local message:

```bash
GCM="C:/Users/Itrai/.workbuddy-ai/binaries/PortableGit/versions/1.2.0/mingw64/bin/git-credential-manager.exe"
timeout 200 git -c credential.helper= -c credential.helper="!$GCM" push origin main:refs/heads/main > .scratch/pushout.txt 2>&1
```

Remote `https://github.com/TechRhino1/HM-AI.git`. Backticks in `-m` are eaten by bash — use
`git commit -F <file>`. `/tmp` does not exist in this Git Bash; use `.scratch/`.

**`.git/refs/remotes/*` is wiped immediately after being written.** `fetch`/`update-ref` exit 0 and
write nothing; even hand-writing the file vanishes. So `git status` always says `[gone]` — ignore it,
never rely on `-u`, push an explicit refspec.

## Data-loss hazard — files vanish, then cannot be recreated

Four incidents, all in the small hours: `09-13 ~03:28` and `09-14 03:50` whole `jarvis/` (after
`git rm`); `09-15 ~05:31` the **object store** (after `git stash push`); `09-16 ~05:4x` **six
individual files**. Cause unknown (AV/EDR and cloud-sync lead). **`.git/` is not safe.**

Incident four had a **new signature: the paths became uncreatable.**

* `stat` → `FILE_NOT_FOUND` **while** `CreateFile(CREATE_ALWAYS)` → `ACCESS_DENIED` = **delete-pending**
  (the name is reserved by an open handle with delete-on-close). A **deny ACE would let `stat`
  succeed**, so the two together rule out an ACL. `icacls` agrees: "cannot find the file specified".
* `psutil` `open_files()`/`memory_maps()` find nothing — but **181 of 301 processes raise AccessDenied**
  (SYSTEM-owned: Defender, OneDrive), so the holder is invisible from user mode.
* `os.replace`, `open(p,'wb')`, the Write tool, shell redirect all fail; `git checkout` fails with
  **`unable to unlink old`**.

**Recovery — a hardlink bypasses the block.** `os.link` goes through `CreateHardLink`, which is not
subject to what blocks `CreateFile`, and it creates the *reserved directory entry*:

```python
open(alias,'wb').write(git_show_index_bytes)   # SOURCE must exist FIRST or os.link gives a
os.link(alias, target)                         # misleading [WinError 2]
# writing through `alias` propagates: both names are one file object
```

Verify with a SHA-1 against `git show :<path>`. The restored file shares an inode with the alias, so
**git operations that rewrite those paths (`checkout`, `stash`) still fail** — commit and push instead.
`core.autocrlf=true`, so a blob written raw shows ` M` while `git diff --numstat` is **empty**: that is
CRLF normalisation, not a change.

* **Commit and push early** — the remote is the only durable store.
* **Never `git stash`.** Diff an old tree with `git archive HEAD <path> | tar -x -C .scratch/oldtree`.
* Tree lost → `git checkout -- jarvis/` (from the *index*; preserves staged deletions), **not**
  `git checkout HEAD -- jarvis/`.
* Object store lost: `tail .git/logs/HEAD` → `git cat-file -t <sha>` → `git fetch origin` →
  `git update-ref refs/heads/main <sha>`.
* Refresh `git bundle create .git/backup/repo-<ts>.bundle --all` — survived all four.

## The live server does not hot-reload — restart after a fix

Inline `python -c` from repo root under `ThreadingHTTPServer`; **edits to Python do nothing until
restart.** Static CSS/JS/templates *are* re-read per request — no restart needed for UI work.
Diagnostic rule: *a hang that does not reproduce in a fresh interpreter is a stale process, not a
logic bug* — compare before reading code. The tell can be absurd: a `py-spy` dump showed
`tradingview_provider.py:565` **calling** a function that in the working tree is a **comment**.

`py-spy` (`…/python/envs/default/Scripts/py-spy.exe dump --pid <pid>`) attaches **without restarting** —
restarting destroys the wedged state. `netstat -ano | grep 8501` for the pid.

## Signal quality — the short version

**The entry signal has no measured edge.** PF 0.568–1.202 on SWING/H1; 0/20 reach the 1.3 bar at 183d
*and* at 365d. The gate is 100% hand-authored, 0% fitted; the meta-label gate is inert (AUC 0.481);
refitting calibration on honest data yields 0/20 skillful and *regresses toward chance* as n doubles.
Full evidence and the P0/P1/P2 backlog: **`AUDIT-2026-09.md`**. Landed from it: `955d40c` (7×
realised-risk defect in `dynamic_levels.py`), `189c1e2` (volatility-targeted sizing; `atr_ratio` was
never passed by any caller).

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
* The **broker** symbol is what resolves: `resolve("OILCash#")` can miss `_ALIAS_MAP` and silently fall
  back to a generic FX spec. Guarded by `test_symbol_registry.py`.
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

`tools/verify_ui_live.py` (44), `verify_dashboard_render.js` (88, pure-Node VM — no browser),
`verify_dashboard_nav.js` (31, live), `verify_ui_layout.js` (154, puppeteer: overflow / tap targets /
glass applied / console errors), `audit_wiring.py`, `audit_endpoints.py` (44).

`tools/audit_endpoints.py` marks six provider-backed routes `EXTERNAL` and used to **tolerate** a
timeout as "needs a live provider" — blind to the defect it exists to catch, which is how three broken
India routes stayed invisible. A timeout is now `HANG` and fails; `--allow-provider-hang` restores
tolerance. **If a check can pass on broken input, it is not a check.**

Screenshots at real viewports: `puppeteer-core` (managed node workspace) driving the installed Chrome
at `C:/Program Files/Google/Chrome/Application/chrome.exe`. `.scratch/shots.js <tag>` captures
6 pages × 3 viewports, reports **horizontal overflow** and console/network errors, and is reusable as
a before/after harness. **`agent-browser` does not support Windows.** `node --check` proves a file
parses, nothing more.

## Testing traps (each has produced a test that passes against the bug)

* Hunting an exception misses unbounded recursion when a frame swallows it — **pin the call**.
* `hash()` salting is constant *within* a process — the discriminating test must spawn a subprocess
  under a different `PYTHONHASHSEED`.
* Clear both caches in `setUp`; `_quote_cache` has a 15s TTL.
* Plain `pytest -q` exits 1 *after all tests pass* (safe-delete hook blocks temp-dir cleanup) — use
  `python -m pytest -q --basetemp=.scratch/pttmp`. 765 pass / 20 deselected.
* `np.allclose` on microsecond epoch ints has an rtol far larger than a 4-hour shift — compare indexes
  with `.equals()`.
* A "dynamic" knob whose argument is never passed is dead code — **grep the callers** before trusting
  it (this is how `atr_ratio` was found).

## Frontend

* **A self-retriggering MutationObserver freezes the page with no error.** `setAttribute` queues a
  record even when the value is unchanged, so an observer writing inside its own `subtree` +
  `attributeFilter` re-queues forever; microtasks drain before paint, so the thread blocks permanently
  and silently. Rule: **a callback must write nothing it observes** (`setIfChanged` + re-entrancy flag).
  Only fires on *interaction*, which is why an idle page looks healthy.
* **An implicit `auto` grid column lets a child widen the whole document.** `.tt-app` declared rows but
  no column, so its single column was `auto` → sized by item min-content → a 404px tab strip made
  `documentElement.scrollWidth` 421px on a 390px viewport, on every page. Fix:
  `grid-template-columns: minmax(0, 1fr)` plus `min-width:0` + `overflow-x:auto` on the strip. **A
  scroll container still needs `min-width:0`** or it contributes its content width to the parent.
* **Liquid glass**: tokens + `.hm-glass` live in `hm_ui.css` (loaded by all six pages);
  `theme_terminal.css` (dashboard only) adds an ambient radial background — a blur over a flat colour
  is invisible. Two traps: the sheen/tint must be `background-image` **layers**, not an absolutely
  positioned `::before` (a positioned pseudo-element paints *above* non-positioned text); and an
  `@supports not (backdrop-filter…)` fallback must be declared **after** the rules it overrides or it
  loses on source order and a translucent unblurred panel ships.
* **`apiRequest` never rejects** — its `.catch` normalises every transport failure to a resolved
  `{ok:false, status:0, error}`. So a missing `.catch` on a caller is *not* a bug. The defect was that
  `error` was `err.message` = the browser's literal **"Failed to fetch"**, rendered verbatim into the
  panel. Fixed centrally in `dashboard.js:apiRequest` and `console.js:getJSON/postJSON` (the latter
  corrects ten catch sites without editing any). **The console line is Chrome's own network log, not
  an unhandled rejection** — do not "fix" it by adding catches.
* Blocked main thread: `page.evaluate` ignores its own `timeout` — race it against a timer; run a
  control phase. `Debugger.enable` + `Debugger.pause` names the frame (no pause ⇒ native).
* `/api/telemetry_state`'s `account` already carries `login`, `name`, `server`, `company`, `balance`,
  `equity`, `margin`, `free_margin`, `margin_level`, `leverage`, `profit`, `currency`,
  `trade_allowed`, `last_sync_time` — the account dropdown needs no server change.
* Canonical nav: `/` Forex·Crypto, `/stocks` US, `/india` India, `/options` India Options; in a
  `.tt-dropdown` in `dashboard.html`, styled by `markActiveNav()` in `hm_ui.js`.
* `.tt-rail` is a sticky **top bar** (`grid-area: rail`). `[hidden]` is a weak UA rule — declare
  `.panel[hidden] { display: none; }`. Templates are **static HTML** (0 Jinja placeholders), so layout
  edits need only a browser refresh.
* A **fixed-position descendant of `.tt-rail` cannot blur page content**: the rail's `z-index` creates
  a stacking context, and `backdrop-filter` on an ancestor also creates a containing block for fixed
  children. That is why the mobile nav is a top app bar, not a bottom tab bar.
* **Named grid areas beat source order.** `.tt-slot--<name>` classes + `grid-template-areas` state
  placement once; relying on DOM order then patching with breakpoint overrides is what produced two
  `max-width` blocks that disagreed about a view's column count.

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
* **Public tunnels:** serveo.net kills the SSH session every **~12m09s** and its edge returns **502 with
  an empty body while still reporting CONNECTED**, so the supervisor never reacts. Cloudflare is primary
  in `HM_start.py`; serveo is fallback. Quick-tunnel subdomains are **random per launch** — re-read
  `hm_cloudflared.log`.
* `http_proxy=127.0.0.1:16119` with empty `no_proxy` — curl to any public host returns a bogus 502
  without `--noproxy '*'`.
* A "public URL is down" report is usually the tunnel, but **check `127.0.0.1:8501` first**.

## Running the dashboard — it does not survive a session

`HM_dashboard.bat` runs the **UI + REST API only** (`start_server(mt5_client=None, orchestrator=None)`,
port 8501). `HM_start.bat` → `HM_start.py` is the bigger thing: it also boots the autonomous trading
engine, the MT5 client and a **public remote-access tunnel**.

**A server started as a background process of an agent session is reaped when the session ends** —
silently, with nothing in the log but `listening on 8501`. Observed three times: 21h23m, 3h40m, then
**5m44s**. Detaching is **not possible here**. Starting with `mt5_client=None` makes
`/api/intelligence/auto-selection` answer **503**, which is expected in UI-only mode, not a fault.

So: never finish a task with the dashboard alive only as a session background task. Point the user at
`HM_dashboard.bat` and say plainly that closing the window stops it.

# Frontend & testing traps — each one has cost a session

Companion to `MEMORY.md`, which is injected every session and so must stay small. Read this file on
demand: when touching the UI, when writing a test that has to discriminate, or when a page misbehaves
in a way that no error message explains.

## Server

* **"No data" is not one fault, and it is usually NOT the server.** Measured on a healthy process
  *while a page still sat on its `Loading…` placeholder*: `/api/telemetry_state` answered **200 in
  10-291ms**, threads steady at **33**, no `CLOSE_WAIT`, CPU normal. Two candidate server-side causes
  were tested and did **not** reproduce — a heavy backtest job (5 symbols x 3 modes, 365d, 400 evals,
  110s: **0 failures**, worst latency 400ms) and `TimeoutGuard` pool saturation (20 calls each hanging
  30s behind a 0.1s timeout all returned in ~0.1s; the pool released its workers). So **do not start
  from "the server is wedged"**. Sample the endpoint *while the page is stuck* — a latency probe plus
  `py-spy dump --pid <pid>` — before reading any code.
* **33 threads is the healthy baseline, not a symptom.** `psutil` reports 33 while `py-spy dump` lists
  only **5** Python threads (MainThread, 2 idle `jarvis_guard`, `web_bg_telemetry_syncer`,
  `backtest_job_worker`). The other ~28 are native threads py-spy cannot unwind (the BLAS/OpenBLAS
  pool). A thread-count gap is not evidence of anything.
* **The UI has no request timeout anywhere.** No `AbortController` and no `setTimeout` around a fetch,
  in any page script. A request that never settles therefore leaves the template's `Loading…`
  placeholder on screen indefinitely and silently — indistinguishable from a panel with no data. Two
  distinct failures produce that, with two different fixes:
  * `getJSON` **resolves** on transport failure (error envelope), so the placeholder is only replaced
    if the render is **unconditional**. `renderWatchlist`'s boot seed was `if (syms.length)`, so an
    empty radar — the normal state with no orchestrator attached — skipped the repaint entirely. **An
    empty result must still repaint.** (With telemetry aborted, the watchlist now paints its 8
    fallback symbols at `—` and the dot goes `is-down`.)
  * A fetch that never settles resolves *nothing*, so only a **watchdog** can repaint. `console.js`
    has `watchdogFirstPaint()` (12s): it states the stall, marks the connection down, and self-heals
    on the next successful poll because every render clears the body first.
  Prove it with `.scratch/probe_watchdog.js` — three phases (**hang / blocked / control**), because a
  test that only ever sees the healthy path pins nothing.
* **A short screenshot wait measures the provider timeout, not the page.** `.scratch/shot_one.js` waits
  2.5s, which is well inside the 30s `provider` budget, so the screener photographs as "Loading…" while
  the API has already returned 40 rows. Wait past the timeout before concluding a panel is empty.

## Frontend

* **A self-retriggering MutationObserver freezes the page with no error.** `setAttribute` queues a
  record even when the value is unchanged, so an observer writing inside its own `subtree` +
  `attributeFilter` re-queues forever; microtasks drain before paint, so the thread blocks permanently
  and silently. Rule: **a callback must write nothing it observes** (`setIfChanged` + re-entrancy flag).
  It only fires on *interaction*, which is why an idle page looks perfectly healthy.
* **A definite size that was never stated resolves from content.** Two instances, same family:
  * an implicit `auto` grid column is sized by item min-content — a 404px tab strip made
    `documentElement.scrollWidth` **421px on a 390px viewport**, on every page;
  * `.tt-app { min-height: 100vh }` leaves the grid container indefinite, so the `1fr` main row
    resolved against *content* and a view grew to **1263px on a 900px screen**.
  Fixes: `grid-template-columns: minmax(0, 1fr)` + `min-width: 0`; and a definite shell height
  (`100vh`/`100dvh`) + `overflow: hidden` on main, with the **view** owning the scroll. Trade opts out
  because its panes scroll internally. **A scroll container still needs `min-width: 0`** or it
  contributes its content width to the parent.
* **`overflow: hidden` deletes a flex row's surplus with no scrollbar.** A panel head needing 626px in
  a 372px panel silently dropped its last controls — present in the DOM, never drawn. Fix:
  `height: auto; min-height: <head token>; flex-wrap: wrap` — "wrap only if it does not fit", so a row
  with room is pixel-identical and there is no breakpoint to get wrong.
* **Liquid glass**: tokens + `.hm-glass` live in `hm_ui.css` (loaded by all six pages);
  `theme_terminal.css` (dashboard only) adds an ambient radial background — a blur over a flat colour
  is invisible. Two traps: the sheen/tint must be `background-image` **layers**, not an absolutely
  positioned `::before` (a positioned pseudo-element paints *above* non-positioned in-flow text); and
  an `@supports not (backdrop-filter…)` fallback must be declared **after** the rules it overrides, or
  it loses on source order and ships a translucent, unblurred panel.
* **`apiRequest` never rejects.** Its `.catch` normalises every transport failure — including
  `Failed to fetch` and aborts — into a **resolved** `{ok:false, status:0, error}`. So a missing
  `.catch` on a caller is *not* a bug and adding one is dead code. The real defect was that `error`
  was the browser's literal **"Failed to fetch"** rendered to a trader. Fixed centrally in
  `dashboard.js:apiRequest` and `console.js:getJSON/postJSON` (the latter corrects ten catch sites
  without editing any of them). **The console "Failed to fetch" line is Chrome's own network log, not
  an unhandled rejection** — do not "fix" it by adding catches.
* **A `position: fixed` descendant of `.tt-rail` cannot blur page content**: the rail's `z-index`
  creates a stacking context, and `backdrop-filter` on an ancestor also creates a containing block for
  fixed children. That is why the mobile nav is a top app bar, not a bottom tab bar.
* `.tt-rail` is a sticky **top bar** (`grid-area: rail`). `[hidden]` is a weak UA rule — declare
  `.panel[hidden] { display: none; }`. Templates are **static HTML** (0 Jinja placeholders), so layout
  edits need only a browser refresh, no restart.
* **Named grid areas beat source order.** `.tt-slot--<name>` + `grid-template-areas` states placement
  once. Relying on DOM order and patching it with breakpoint overrides is how two `max-width` blocks
  came to disagree about how many columns a view had.
* Canonical nav: `/` Forex·Crypto, `/stocks` US, `/india` India, `/options` India Options; held in a
  `.tt-dropdown` in `dashboard.html`, styled by `markActiveNav()` in `hm_ui.js`.
* `/api/telemetry_state`'s `account` already carries `login`, `name`, `server`, `company`, `balance`,
  `equity`, `margin`, `free_margin`, `margin_level`, `leverage`, `profit`, `currency`, `trade_allowed`
  and `last_sync_time` — the account dropdown needs no server change.
* Diagnosing a blocked main thread: `page.evaluate` ignores its own `timeout`, so race it against a
  timer and run a control phase. `Debugger.enable` + `Debugger.pause` names the blocking frame (no
  pause ⇒ the block is native).

## Data source / broker

* **EXECUTION MODE MUST NOT GATE MARKET DATA.** `MT5Client.init_connection()` early-returns for
  `paper`/`backtest`/`offline` (it simulates fills, so it wants no broker link) and **nothing else
  called `mt5.initialize()`**. So in the process where the data feed runs the terminal was never
  initialized, and the chain was silent end to end: `resolve_broker_symbol()` failed every probe →
  the *canonical* name (`XAUUSD`) reached `copy_rates_from_pos` instead of the broker's `GOLD.i#` →
  the broker answered 0 rows → **every frame became `SYNTHETIC_FALLBACK`**. The platform read as
  "broker offline / no data" with a live terminal sitting right there. Reading bars and placing
  orders are separate capabilities: `jarvis/data/broker_symbols.ensure_mt5_terminal()` establishes
  the first regardless of the second, and `data_feed._fetch` calls it. Measured: paper mode went
  from 0/20 live frames to 20/20. **Ask "does this flag gate *execution* or *reading*?" before
  short-circuiting on a mode.**
* **A failed probe cached forever is worse than a retry.** `broker_symbols._FAILED[sym] = True`
  recorded "not resolvable", which is *indistinguishable* from "this broker has no such symbol" —
  so one `resolve_broker_symbol()` call made before the terminal was ready pinned every symbol to
  `None` for the life of the process, long after the terminal came up. Do not cache a failure whose
  cause is "the dependency was not up yet".
* **A health flag derived from the wrong link lies in both directions.** `DATA_FEED` was set from the
  **execution** account's `login > 0`, so paper mode reported `OFFLINE` forever while real bars were
  streaming — and `dashboard.js` read `broker online/offline` off the *same* flag
  (`mt5 === 'CONNECTED' ? … : 'broker offline'`). Market-data health is now **measured** by the feed
  itself (`DataFeedEngine.data_health()` → `STREAMING | STALE | CLOSED | SYNTHETIC | OFFLINE`) and the
  chip keys off real bars being present. Keep the feed label honest even when the chip is green.
* **A UI-only server reports `MT5: DISCONNECTED` — and that is correct, not a defect.**
  `HM_dashboard.bat` / `start_server(mt5_client=None, orchestrator=None)` has no broker client at all.
  Before concluding "the product cannot reach MT5", check **how the process was started**: this was
  nearly misdiagnosed as a product bug while it was the CSS-verification harness. Only
  `HM_start.py` wires `orchestrator.mt5_client` in. Check the running process's cmdline
  (`psutil.Process(pid).cmdline()`) before reading any data-path code.
* **Cold history reads as a stalled feed.** MT5 pulls a symbol's history **on demand** and does it
  **asynchronously**: the first read after `symbol_select` returns a stale tail. Measured on 6
  untouched symbols — **5 read ~19.6h stale, unchanged across 7 back-to-back calls (30-47ms), then
  `FRESH` a few hundred ms later**. (Elapsed time fixes it, not a retry: rapid calls all return the
  same tail.) So a freshness gate refuses a perfectly good symbol on the first cycle after every
  restart — intermittently, and *only* right after a restart, which is what a phantom bug looks
  like. `data_feed` now runs a bounded warm-up (1.2s budget, 0.3s poll, paid once per symbol).
* **A substring symbol match can pick a different instrument.** The fuzzy broker scan was
  `if sym in n.upper()`, so **`COPPER` resolved to `SouthernCopper`** (Southern Copper Corp — an
  EQUITY) and was returned as the copper commodity, stamped `LIVE_MT5` and certified `FRESH`. Nothing
  downstream could tell. Now prefix-only, and every fuzzy discovery logs at WARNING. **A wrong
  instrument that looks healthy is worse than an unresolved symbol**, which callers already handle.
* **Fabricated bars were being traded on.** `WTI` is in `Orchestrator.symbols` and absent at XM, so
  the feed synthesised a WTI series and the platform analysed it as if real. A live session now
  refuses to decide on a `SYNTHETIC_FALLBACK` frame, **scoped to `terminal_ready()`** so it fires only
  when we *have* a working broker link that still answered nothing (i.e. the broker does not offer the
  symbol). With the terminal down the synthetic frame is kept for the UI and execution is already
  blocked because `get_account_snapshot()` reports `login 0`. `first_unusable_frame()` in `data_feed`.
* **A warning in a poll loop needs a once-per-key guard.** The 0-rates warning and the refusal fired
  on every scan — **21+ lines per cycle** for one unavailable symbol across 3 styles. `_stale_warned`
  already existed for exactly this; `_no_rates_warned` / `_unusable_warned` now do too.
* **Bar opens are stamped on the BROKER's clock**, like tick times, so freshness must be measured
  against now-in-broker-time. And `include_current_bar=False` means the newest returned bar is the
  last *closed* one, already 1-2 durations old — hence a 2.5-bar tolerance, not 1.

## Rules that keep biting (moved out of MEMORY.md)

* **MT5 times are BROKER-SERVER time, not UTC** (XM = GMT+2/+3). Read as UTC they land 2–3h in the
  *future*, so `now_utc - open_time` came out short and `max(0, …)` zeroed it — silently disabling
  `position_monitor`'s stagnation exits for the first 3h of every position. Use
  `jarvis/data/broker_time.py`; **never hardcode the offset** (broker DST moves it).
  `tests/test_broker_time.py`, 3 of 13 fail pre-fix.
* **Never seed a modelled value from `hash()`** — CPython salts it per process, so the same input
  gives a different series in a different run. Use `jarvis.data.determinism.stable_seed`.
* **Quote fallback must never call the profile hydrators.** They call back into `fetch_quotes()`,
  unbounded (233 re-entries for one NIFTY lookup) and silent because `hydrate_batch` swallows
  exceptions. Read `INDIA_UNIVERSE`/`STOCK_UNIVERSE` directly. Guarded by
  `tests/test_provider_recursion.py`.

## Project conventions (moved out of MEMORY.md to keep the injected file small)

* **CSS architecture.** `hm_ui.css` is the design system and is loaded **last** on all six pages, so a
  `:root` token bridge there wins on source order — that is how the four legacy page sheets
  (`stocks/india/india_options/terminal.css`) were unified without editing their 5k lines. Cards stay
  **solid** (`--hm-bg-surface`/`--hm-bg-raised`), matching the dashboard's `.tt-panel`; glass is for
  the shared chrome (HUD, nav, `.hm-card`, dropdowns, modals) over the ambient wash on `body`.
  Beware `!important` in a page sheet: it beats source order and silently discards the shared glass.
* **Routes:** `/`,`/dashboard` → `dashboard.html`; `/console` → `console.html`; `/classic` →
  `index.html`. `console.js`/`dashboard.js` are IIFEs with no exported global.
* Backtest statuses `QUEUED | RUNNING | DONE | FAILED | CANCELLED` — **`DONE`**, not `COMPLETED`.
  `POST /api/backtest/run` answers **202** with a `job_id`. Localhost authenticates as admin
  (`_is_local_request()`), so local curl needs no token.
* `normalise_style()` maps anything unrecognised to `SWING`. Style → timeframe: SWING→H1,
  DAY_TRADING→M15, SCALP→M5. Reports key results `SWING(H1)`/`DAY_TRADING(M15)`/`SCALP(M5)` —
  **filter by style when comparing**; a naive loop lets SCALP overwrite SWING.
* `_csv()` in `intelligence_api.py` returns `None` (not `[]`) for an absent param — use
  `set(_csv(q,"x") or [])`. `max_evaluations` is a budget **per search**, not per optimiser.
* Cross-style consensus: `intelligence/mode_aggregator.py`. Weights SWING 0.346, DAY_TRADING 0.1064,
  SCALP 0.1287 — all below neutral.
* Regime optimisation: `backtesting/regime_optimizer.py` (`tools/optimise_regime.py`,
  `/api/backtest/regime-policy`). Disable thresholds deliberately duplicate
  `winrate_targeting.regime_edge_table`; a test enforces they agree.
* **India latency = one batched `fetch_quotes()` per 60s hydrator TTL**, not the 42-symbol scan (which
  re-runs in 0.14s once cached). And **a spot-check right after another call measures the cache, not
  the endpoint** — sample with gaps longer than the TTL.
* **`docs/MARKETS_DATA_CONTRACTS.md`** — shapes for the ten stocks/India endpoints plus the timeout
  token (`fast` 8s/`normal` 15s/`provider` 30s/`slow` 60s, `dashboard.js:46`). Traps: screener `count`
  is the **matching total**, not `len(stocks)`; `/api/stocks/news` + `recommended_buys` have **no UI
  consumer**.
* **Public tunnels:** serveo.net kills the SSH session every **~12m09s** (6 consecutive drops) and its
  edge returns **502 with an empty body while still reporting CONNECTED**, so a supervisor never
  reacts. Cloudflare is primary in `HM_start.py`; quick-tunnel subdomains are **random per launch** —
  re-read `hm_cloudflared.log`. A blocked ssh also stops sending keepalives — drain its stdout.

## Testing

* **Prove the test fails on the pre-fix code** — temporarily restore the bug, re-run, revert. An
  assertion that never saw the bug pins nothing. (The broker-clock fix was verified this way: 3 of 13
  tests fail pre-fix, including `'DAY_TRADING' != 'SWING'`.)
* Hunting an exception misses unbounded recursion when a frame swallows it — **pin the call** instead.
* `hash()` salting is constant *within* a process, so an in-process test passes against the bug — the
  discriminating test must spawn a subprocess under a different `PYTHONHASHSEED`.
* Clear both caches in `setUp`; `_quote_cache` has a 15s TTL.
* Plain `pytest -q` exits 1 *after all tests pass* (a safe-delete hook blocks temp-dir cleanup) — use
  `python -m pytest -q --basetemp=.scratch/pttmp`.
* `np.allclose` on microsecond epoch ints has an rtol far larger than a 4-hour shift — compare indexes
  with `.equals()`.
* A "dynamic" knob whose argument is never passed is dead code — **grep the callers** before trusting
  it. This is how `atr_ratio` was found.
* **A silent clamp is a bug hider.** `max(0.0, negative)` did not merely produce a wrong number, it
  turned a loud sign error into a plausible zero that also satisfied the caller's `> 0` guard. If you
  clamp, warn.
* **A check that can pass on broken input is not a check.** `audit_endpoints.py` tolerated a timeout as
  "needs a live provider", which is how three broken India routes stayed invisible for weeks.
* **A test can pass on the broken code because the environment masks the bug.** The paper-book leak
  test passed pre-fix on this machine: with the MetaTrader5 package absent, *both* `MT5Client.__init__`
  ("Falling back to PAPER mode") *and* `init_connection()` (called from `_reconnect_if_needed()` on the
  way into `get_open_positions()`) downgrade `mode` to `"paper"`, so the paper book was returned
  legitimately. Fix: `monkeypatch.setattr(client, "_reconnect_if_needed", lambda: None)` **and** pin
  `client.mode = "live"` after construction. Ask "would this fail for the right reason here?" — a
  signature change alone makes a test fail, which proves nothing about the behaviour.
* **A pre-existing test can depend on the absence of a validation you are adding.**
  `test_a2_paper_modify_and_close_status` passed incoherent SL/TP and a positional `1.0950` that landed
  in `comment` instead of `tp_price`; it only "worked" because nothing checked. When adding a guard,
  grep the suite for callers that relied on it not existing.

## Data integrity

* **A class-level mutable default is a process-global cache.** `MT5Client._shared_paper_positions` is
  aliased into every instance and never cleared, and `get_open_positions()` returned it whenever
  `is_connected` was False — so a simulated position from an earlier paper run was reported as a live
  one in a live/demo session. It surfaced as "Positions 1" beside "broker offline", with a real cached
  XM account next to a position the broker never had. Guard the *mode*, not just the connection.
* **A fabricated number with a live source label is worse than a missing one.** `fetch_quotes`
  synthesised a baseline for every unresolved symbol and stamped it `"source": "tradingview"`, complete
  with a made-up RSI of 55.0. Gold hit that branch because `"XAUUSD"` is six characters ending in
  `"USD"`, so it matched the FOREX heuristic and was queried as the non-existent `FX:XAUUSD` — a primary
  instrument returning 150.0. Synthetic values now carry `source: "profile_reference"` +
  `is_fallback: True`. **Before consuming a provider value, check its provenance.**
* **A hardcoded price table is a fabricated fill.** `send_market_order` filled paper orders from
  `2400.0 if "XAU" in symbol else …`, and set `current_price` to the same constant, so `profit` was
  structurally `0.0` forever ("OPEN P&L 0.00" on a position in profit) while SL/TP — computed by the
  caller from the *real* price — sat on the wrong side of the recorded entry. Callers must pass
  `reference_price`; with none available, refuse the order.
* **`resolve()` falls back to a generic FX spec for unregistered symbols**, whose `contract_size` is
  1000× too big for gold. Check `is_registered()` before computing money from a spec; a wrong P&L is
  worse than none.
* **A three-character tag needs word boundaries; a long one does not.** `"ai" in comment_lower` matched
  "trailing", "pair", "main", "wait", "chair", so a manual trade filed as BOT (AI). But the fix must not
  over-apply: our own tag is the compound `ManualDesk`, which lowercases to `manualdesk` — word
  boundaries would never match it, so the manual side stays a substring test.
* **`open(alias, 'w')` on a hardlink truncates the shared object** — as does `CreateFile(CREATE_ALWAYS)`.
  `HM_dashboard.bat` was emptied this way while probing. It is also **readable but not writable**
  (normal inherited ACLs, no deny ACE; the same file object writes fine under `.scratch/`), so writes
  must go through a hardlink alias — `HM_start.py` and `jarvis/intelligence/decision_engine.py` are
  blocked the same way. `st_nlink == 2` is the tell.

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
* **`cpu_percent(None)` will tell you a busy process is idle.** Priming it and calling it again
  returned `0.0%` for four workers that were actually at ~93% of a core — I nearly declared a
  healthy 20-symbol scan "stalled" and killed it. The reliable measurement is a **delta**:
  snapshot `p.cpu_times().user`, sleep 10–12s, subtract. Also expect `multiprocessing` children to
  show as `spawn_main(parent_pid=…)` in `psutil` — identify them by the parent's cmdline, not their
  own.
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
* **`curl` to a local port can fail with "upstream connect failed".** `http_proxy`/`https_proxy` are
  set in this shell (`http://127.0.0.1:15838`), so curl routes even `127.0.0.1` through it and the
  error text names a proxy, not the server. **Use `curl --noproxy '*'` for any local probe** — the
  "connection refused" story it tells is otherwise entirely fictitious.
* **A background process started with `&` inside a Bash call dies when that call returns.** It looks
  alive for the rest of the same invocation (the first probe can succeed) and is gone by the next, so
  the failure reads as a server that crashed on request. **`nohup` does not save it** — a
  `nohup … &` server answered `health=200`, then the very next tool call got
  `net::ERR_CONNECTION_REFUSED` on every route while the log's last line was a normal startup
  message. Use the tool's own `run_in_background: true`, then verify with
  `netstat -ano | grep <port> | grep LISTENING` *and* a `curl --noproxy '*'` before trusting it.
* **The live server holds the *old* Python.** `/api/*` behaviour must be verified against a
  **second** instance on another port (`JARVIS_PORT` is honoured; `.scratch/verify_server.py` boots a
  standalone UI+REST server on :8599 with a paper client) rather than by killing the engine the user
  is running. `verify_ui_live.py` binds :8599 itself, so it needs that port free.
* **`data/jarvis_history.db` is the live journal; `jarvis_history.db` at the repo root is a stale
  copy.** `TRADE_DB.db_path` names the real one. Inspecting the root file gives you old rows and a
  missing `closed_at` column that makes a working migration look broken.

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
* **An enum map that starts at the wrong index is invisible until you read the broker's own literal.**
  `dashboard.js`'s `PENDING_TYPES` mapped `0:'BUY LIMIT', 1:'SELL LIMIT', 2:'BUY STOP'` — but MT5's
  `ORDER_TYPE_*` starts at **BUY=0 / SELL=1**, so the pending types begin at **2**. Every live LIMIT
  was labelled a STOP and vice versa. The repo already held the truth
  (`mt5_client.place_pending_order`: `"BUY_LIMIT": getattr(mt5, "ORDER_TYPE_BUY_LIMIT", 2)`) — check
  the placement code before writing a display map. Worse, `verify_dashboard_render.js` *asserted* the
  bug (`type: 2` expected to render `'BUY STOP'`), so the test was the thing keeping it alive.
* **A frontend that reads a key the server never sends renders the empty state on success.** Two of
  these in one day, both looking like "the feature is broken":
  * `renderBacktestResult` looked for `report.per_symbol || report.symbols`. The optimiser's report is
    `{modes[], series[]}` — **per trading style** — so a good run always said "no results". It also
    never unwrapped `payload.job.result` (the result is nested one level deeper than the wrapper).
    Rule: read the *report file* on disk before writing the reader.
  * `console.js` did `data.trades || data.history` against `/api/history`, which returns a **bare
    array**. Accept both shapes.
* **`Date.parse` reads a zoneless string as LOCAL time.** `"2026-09-11 08:09:50"` (the shape
  `PositionSnapshot.open_time` uses, and the broker's own UTC stamp) parses as machine-local unless a
  zone is appended. Any marker or countdown built from one lands hours off. Normalise: if there is no
  trailing `Z`/`±HH:MM`, insert `T` for the space and append `Z`.
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
* **A refused order comes back as HTTP 200, so `res.ok` says nothing about whether it happened.**
  `mt5_client` reports a refusal in the *body* — `{"status": "FAILED", "reason": …}` when the broker
  said no (market closed, invalid stops, insufficient margin, the coherence check, a timeout) and
  `{"status": "BLOCKED", "reason": …}` when the mode gate refuses it — and the route passes that dict
  through with `_send_json(res)`, whose default status is 200. Measured: a BUY with its stop above the
  fill price answers `{"status": "FAILED", "reason": "BUY stop-loss 2100.0 is not below the fill price
  2000.0"}` with **`HTTP=200`**. Two consequences, both real:
  - `manual_trade` guarded on **`if (res.ok)`** alone, so a **rejected market order was announced to
    the trader as "submitted"** — a false confirmation of a trade, green toast and all. The three
    pending-order handlers tested `status !== 'FAILED'`, so a **`BLOCKED`** order (never sent) was
    announced as **"placed"**. Fixed with one shared predicate, `actionRefused(data)`, over
    `{FAILED, BLOCKED, REJECTED, ERROR}`, plus `actionFailureMessage()` so `reason` is not dropped in
    favour of the nonsensical "Order rejected: HTTP 200".
  - The failure *message* chain matters too: the broker sends **`reason`**, the server's own validation
    sends **`error`** with **HTTP 400**, and the UI read only `error` on the market path.
  **Rule: for any action route, decide the outcome from the body's status, never from the HTTP code —
  and when a guard names failure sentinels, check the whole set the backend can emit.** A cross-language
  test now pins this (`tests/test_action_response_contract.py`): it reads the statuses out of
  `mt5_client.py`, reads the UI's set out of `dashboard.js`, and fails if the backend can refuse with a
  status the UI has never heard of — which is exactly the drift that produced the bug.

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
* **A test can assert the bug rather than catch it.** `verify_dashboard_render.js` asserted that
  MT5 order type `2` renders `'BUY STOP'` — encoding the off-by-two rather than exposing it, so
  fixing the map turned the suite red and the "correct" move looked like reverting. When a fix breaks
  a test, check whether the *fixture* agrees with the spec (here: `type: 2`, `comment: 'limit'`, and
  the backend's own `ORDER_TYPE_BUY_LIMIT = 2`) before believing the test. Rewrite it to assert the
  exact label **and** the absence of the neighbouring one, so it cannot pass by accident again.
* **`audit_endpoints.py` probes everything with GET.** A new `POST`-only action therefore reports
  **DEAD** with an HTML 404 even though it works — add it to the `POST_ONLY` set in that file. The
  count of discovered endpoints also rises, so "46/46" replacing "44/44" is the fix landing, not a
  regression.
* **A pre-existing test can depend on the absence of a validation you are adding.**
  `test_a2_paper_modify_and_close_status` passed incoherent SL/TP and a positional `1.0950` that landed
  in `comment` instead of `tp_price`; it only "worked" because nothing checked. When adding a guard,
  grep the suite for callers that relied on it not existing.
* **The three browser suites take three different env vars, and the obvious name is not one of
  them.** `verify_ui_layout.js` reads `JARVIS_BASE`; `verify_dashboard_nav.js` reads `DASH_URL`;
  `verify_dashboard_render.js` reads neither because it is **file-based** (it loads `dashboard.js`
  and `dashboard.html` from disk into a `vm`, so it needs no server at all). Setting `JARVIS_PORT`
  — the name the `.scratch/` screenshot helpers use — is silently ignored by all three, and every
  run quietly targets the default `http://127.0.0.1:8501`: **the user's live engine.** Frontend
  results are still valid there (CSS/JS/templates are re-read per request, unlike Python), but you
  are hammering the user's process, and once it gets busy a single-threaded dev server starts
  refusing connections — `net::ERR_CONNECTION_REFUSED` against a port that `netstat` still shows as
  LISTENING. Point them at a scratch server explicitly:
  `JARVIS_BASE=http://127.0.0.1:8599 node tools/verify_ui_layout.js`.
* **Run the browser suites one at a time.** Three suites plus a probe in parallel starved the
  `forex` and `options` pages into 45s navigation timeouts, which report identically to a real
  regression. Two "failures" that vanish on a solo re-run were contention, not code.

### Coverage gaps that a green suite hides

* **A renderer whose endpoint the suite stubs with an empty body has no test at all.**
  `verify_dashboard_render.js` answered every `/api/backtest/` request with
  `{status:'OK', jobs:[]}` and never fed `renderBacktestResult` a report. The suite was 88/88
  green through the entire period in which the backtest page was broken, because "the renderer
  was never called" and "the renderer works" are indistinguishable from the outside. **For every
  renderer, confirm the fixture supplies a payload with the real shape** — the backtest report is
  `{modes[], series[]}` built per *trading style* at `optimizer.py:888-995`, nested at
  `payload.job.result`. A stub returning `{}` or `[]` converts a test into a no-op.
* **Drive the view through its real wiring, never by calling the renderer.** The backtest is
  entered the way a user enters it — the `6` shortcut, then a click on `tr[data-job]` — so the
  test also covers `loadJobs()`, the delegated click handler, `pollJob()`'s DONE branch and
  `loadJobResult()`'s fetch. Calling `renderBacktestResult(payload)` directly proves the last hop
  only, which was the hop that was never broken.
* **A view-gated tick makes a harness detour silently inert.** `tickNews()` early-returns unless
  `state.view === 'news'`. Adding a backtest phase that switched views turned 5 clock-advance
  checks red — reading exactly like a broken calendar. When a harness phase changes the view,
  **hand the view back** before the assertions that depend on it (`drain()` re-enters news at
  tick 25 for this reason). The failure mode is nasty because the code under test is fine.
* **A missing property in the DOM stub crashes as if it were an app bug.** The `El` stub had no
  `.options`, so `loadBacktestMeta`'s `objSel.options.length` threw a `TypeError` and took the
  whole run down with a `dashboard.js:3891` stack — which reads as a crash in the dashboard. A
  real `<select>` always has `.options`; derive it from the tree
  (`get options() { return children.filter(tagName === 'OPTION') }`) so appending an `<option>` is
  observable. Note the controller's own `sel.options || []` guard is what let the gap survive
  until a select was actually populated.
* **Negative-test every new assertion, and report the count.** Reverting the reader to its pre-fix
  form turned **16** checks red with the user's own words in the detail (`No results in this job`);
  reverting the number grouping turned exactly **1** red. "Exactly the right checks, no more" is
  the evidence that a check is pinned to the behaviour rather than to the suite's mood.
* **The backtest was not the only renderer with no fixture.** `/api/history` had no branch in the
  stub at all, so it fell through to `{}` and `renderHistory()` rendered zero rows — meaning the ten
  columns, the four filters, the summary line and the `closed_at` handling of the *trade history the
  user asked to restore* were all untested. **Sweep the stub, not just the endpoint you are working
  on**: list every `apiGet`/`apiPost` path in the controller, then check the stub has a branch whose
  body has the real shape. Two of the six features in this work stream had the gap; assume there are
  more until the sweep says otherwise.
* **A filter applied to one of N merged sources is silently half-inert.** `/api/history` merges
  journal rows (`TRADE_DB.fetch_recent_trades`) with MT5 out-deals, but the route passed `days` only
  to `history_deals_get` — `fetch_recent_trades` had no `days` parameter at all. So the Window
  dropdown looked like it worked (the MT5 half responded) while every journal row ignored it:
  measured, `?days=1` returned **185 rows whose oldest was 24 days old**, 117 outside the window.
  When a handler concatenates sources, **enumerate every source and check the filter reaches each
  one**. And when adding the parameter, default it to "no window" so callers that never passed one
  (here the classic terminal and the console) keep their behaviour — then assert that default
  explicitly, because "I added a filter" and "I changed what three other callers see" are one
  keyword argument apart.
* **Restoring a feature from git history: diff the *label* against the data, not just the column
  list.** The original history table's first column was literally `Closed`, rendering
  `t.time || t.close_time`. The restoration kept the column count and renamed it "Execution time"
  over `t.timestamp` — which is the **entry** time for a journal row and the **exit** time for a
  synced MT5 deal, so one unlabelled column carried two meanings and the header was false for half
  the rows. `git show <old-sha>:<file>` gives the original in one command. A column list that matches
  hides a meaning that does not, and the server already emitted `closed_at` for exactly this
  disambiguation — the renderer just ignored it.
* **Sweep the stub, and check for logic modules that simply have no test file.** The sweep found
  three more untested surfaces (copilot, auto-selection, regime-policy) — and the copilot turned out
  to be worse than a frontend gap: `jarvis/api/copilot.py` is 391 lines of intent dispatch with **no
  test file at all**, and both bugs it has ever had were found by hand-probing a live session. Before
  adding coverage to a panel, run `ls tests/ | grep -i <module>` and
  `grep -rln "<module>" tests/` — "no test imports this" is invisible in every dashboard.
* **A routing module's failure mode is a wrong-but-plausible answer, so assert the handler, not the
  topic.** The copilot answered *"how are my trades doing?"* with *"you have no open position on
  XAUUSD"* — true, and not what was asked. Pin each phrasing to a **marker string only the intended
  handler emits** (`**How you stand**`, `Working orders`, `no broker client is attached`); asserting
  on the subject matter ("the answer mentions XAUUSD") passes for both the right and the wrong
  handler. Keep the historical bugs as named regressions, and test the **discriminator** rather than
  the example: here, a *named* instrument routes to that position while the on-screen **focus alone
  must not** flip a book-level question back to one instrument.
* **A help text is a promise about the router — test it as one.** Extract every quoted example from
  the help reply (`re.findall(r"\*'([^']+)'\*", help_text)`) and assert each reaches a handler. Not
  hypothetical: the copilot's help suggested *"how is my symbol doing?"* while that exact sentence
  fell through to the help text. The test now keeps the two in step automatically.
* **A count that names a category must count that category.** `_performance_answer` said "Realised
  over the last N **closed trade(s)**", where N counted only trades with a **non-zero** result — so
  with a break-even trade present it said "1 closed trade" while the history answer listed two rows
  for the same journal. Fix the label to match what was counted ("N trade(s) with a recorded result")
  rather than widening the filter, because widening it also feeds break-even trades into
  `len(realised) - len(wins)` and silently reclassifies them as losses.
* **Never normalise HTML by stripping whitespace before matching it.** `html.replace(/\s+/g, '')`
  removes the space in `<span class="…">` too, yielding `<spanclass="…">`, so a pattern like
  `/Modes<\/span><span class="tt-metric__value">3</` can never match — a **false failure that is
  indistinguishable from a rendering bug** (the printed detail showed the correct markup beside the
  FAIL). Cost a full debugging round; the string was correct and only the assertion was wrong. Assert
  against the markup as rendered, and bind a value to its own label rather than asserting the value
  appears somewhere: `metricValue(html, 'Modes')` reads the `tt-metric__value` span *following* the
  label, which catches two counters being **swapped** — the loose form
  (`indexOf('>3<') && indexOf('>2<')`) passes a swap, despite its own comment claiming otherwise.
  Negative-tested: swapping `Conditions`/`Tradeable` turns exactly 1 check red (`Conditions=2
  Tradeable=3`); under the loose form it turned **0**.
* **Two suites that bind the same port will silently test the wrong server.** `verify_ui_live.py`
  *starts its own* server on **:8599** (`start_server`, and it attaches an orchestrator itself) — it
  does not use `.scratch/verify_server.py`, which binds the same port. With the scratch server already
  listening, the suite's own server never binds, every probe lands on the engine-less scratch server,
  and the report reads `attached=False` / `503 UNAVAILABLE` — i.e. **exactly like a product
  regression** ("the engine is not wired"). It is not: stop the scratch server first, then the same
  suite reports `attached=True`, `decisions=1`, **46/46**. Corollary: this suite's **total is
  conditional** (45 with no engine, 46 with one), so a changing denominator here is not lost coverage.
  Before believing any live-suite failure, check `netstat -ano | grep :<port>` for a `LISTENING` squatter.
* **A stub default that disagrees with the real DOM invents a bug in the app.** The `El` stub had no
  `value`, so an untouched form control read `undefined`; the controller's guard is
  `slEl.value !== ''`, which is correct against a real input (an empty one reads `''`) but passed on
  `undefined`, so the harness posted `Number(undefined)` — `NaN`, serialised as `null` — for a level
  the user never typed. The check failed and looked like a frontend defect. In a real DOM **every form
  control's `value` is a string**, so the stub defaults to `''`. Same family as the missing `.options`
  above: when a check fails, first ask whether the *stub* is faithful before believing the app is wrong.
* **`curl -s -o /dev/null -w '%{http_code}'` exits 23**, so a `cmd && next` chain silently skips
  `next` and you get an empty result that reads like the second command produced nothing. Separate the
  probe from the command it guards, or use `;`.

## Data integrity

* **A stale `scan_manifest_<TF>.json` silently misaligns every `bar_idx`.** The manifest is written
  *last*, only after all symbols finish, but each symbol's candidate parquet is written as it
  completes — so during a long rescan the on-disk manifest still describes the *previous* run.
  `optimizer.manifest_since()` reads it and replays `since` as a trim on the price frame, and that
  trim is **alignment-critical**: candidates are indexed against whatever frame the scanner saw, so a
  wrong `since` does not merely shorten the window, it shifts every `bar_idx` onto the wrong bars.
  Seen live 2026-09-17: `scan_manifest_M5.json` still held `since='2026-07-06', symbols=1` from a
  2026-09-13 single-symbol WTI run while the fresh 20-symbol, untrimmed M5 183d rescan had already
  written 10 candidate tables. Concretely: the full M5 frame is 37,440 bars, the stale trim cut it to
  14,400, and fresh candidates were indexed up to bar_idx 37,438. **Never start a backtest/optimizer
  for a timeframe whose scan is still running**; confirm manifest `generated_utc` is newer than the
  scan start and that `since` matches what you passed (omit `--since` ⇒ expect `null`).
  `tests/test_backtest_optimizer.py::TestAgainstRealData::test_scalp_trim_replay_keeps_bar_idx_in_range`
  and `::test_trim_actually_shortens_the_frame` are the teeth — they fail while a scan is in flight,
  which is correct behaviour, not a regression. Note the existing guard in `optimizer.load_series`
  only refuses `bar_idx >= len(frame)`; **a shift small enough to stay in range is still silent**,
  so the guard catches gross breakage, not subtle misalignment.
  Fixed 2026-09-17: the scanner now publishes `since` (and `complete: false`) *before* the first
  candidate table lands, so alignment is right for the whole run. Legacy manifests have no
  `complete` key at all, so `complete is False` cannot detect them — the M5 manifest written by the
  2026-09-17 rescan is in that old format, because that process started before the fix landed.
* **"M5 is trimmed" was a convention that expired silently.** `--since 2026-07-06` existed because
  **M1 history was hard-capped at ~67-69 days**; the cap is gone and all 20 symbols now hold the
  full 183d M5 series (2026-03-14 → 2026-09-11, 34k-52k bars), so M5 is scanned untrimmed like M15.
  Three tests still *asserted* a trim exists (`assert since`, `assert bundle.since`,
  `assert n_bars < len(raw)`) and would have failed the moment the rescan landed. They now assert
  **consistency with whatever the manifest declares** instead. When a data limitation behind a
  convention disappears, tests that encode the convention — not just the data — go stale.
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
* **MT5's `spread` column is in POINTS, not pips.** `symbol_info.spread` is in units of
  `10^-digits`, so for a 5-digit FX pair 1 point = `0.00001` while `pip_size = 0.0001` — a factor of
  10. `signal_scan._spread_for_bar` converts correctly (`raw * point_size / pip_size`) and writes the
  result into the candidates table as `spread_pips`. **Consume that column; never re-derive from the
  bars.** Multiplying the raw column by `pip_size` — the obvious thing, and what I did — overstates
  every symbol's round-turn cost by exactly 10×, which is enough to "discover" that AUDUSD costs
  **1.89R** per trade when it costs **0.19R**. The wrong number is plausible in shape (worst for the
  tightest stop) and only the magnitude is absurd, so it survives a sanity glance.
* **Three frame/units errors in one session, all producing plausible numbers.** (1) A partially
  complete re-scan read as fresh because `ls --time-style=+%H:%M:%S` drops the date. (2) A tp sweep
  without a benchmark read drift as a target optimum (`best tp=6, E=+0.33R` — pure beta). (3) Points
  read as pips, a 10× cost overstatement. The common failure is not arithmetic, it is **failing to
  ask what else would produce this number.** For any measurement: state the control, and check one
  raw value by hand against its documented units, before believing an aggregate.
* **A check that fails OPEN reports success — which is worse than no check.** `broker_utc_offset`
  returned 0 whenever it could not derive the offset (no terminal in this process; canonical symbol
  instead of the broker's `GOLD.i#`; or one slowly-ticking symbol). `classify_bar_freshness` computes
  `age = (now + offset) - bar_epoch` against broker-stamped bars, so a 0 offset made `age` **negative**
  — the bars looked like they were in the *future* — which maps to `UNKNOWN`. And `first_stale_frame`
  blocks only on `STALE`, never `UNKNOWN`, deliberately, so synthetic frames can still be reasoned on.
  Net effect: a broken offset **silently disabled the stale-feed gate**, and four tests were passing
  *because of it*. Fixing the offset turned the ages truthful and the stale frames were then correctly
  refused. Two independent safety mechanisms shared one failure mode; always ask what a guard does when
  its *input* is unavailable, not just when the condition it tests is true.
  **Repairing the input was not the fix** — the guard still could not tell "real bars, age unknown"
  from "synthetic bars". `first_untrusted_frame` now blocks a `LIVE_MT5` frame whose age is
  `UNKNOWN` while still tolerating a non-live one. A guard's *decision* has to be a function of the
  facts it can actually establish, and `UNKNOWN` must not be routed to the permissive branch by
  default. Test it with a harness that runs the same input through the old and new gate — if both
  behave the same, you renamed something instead of fixing it.
* **`symbol_info_tick` needs BOTH an initialised terminal AND the broker's symbol.** It returns `None`
  for every symbol in a process that never called `mt5.initialize()` (the execution client only does so
  for modes that place orders), and `None` for canonical names — MT5 knows gold as `GOLD.i#`, so
  `symbol_info_tick("XAUUSD")` is `None` while `symbol_info_tick("GOLD.i#")` works. Either failure is
  indistinguishable from "no data", so it degrades silently. Call `ensure_mt5_terminal()` and
  `resolve_broker_symbol()` first.
* **A single slow symbol must not be able to degrade a server-wide value.** The broker offset is a
  property of the *server*, so any fresh tick answers the same question. Deriving it from the one
  symbol the caller happened to hold meant gold's tick running ~17 min behind EURUSD's pushed the
  candidate 799s from the nearest 30-minute boundary, past the 300s tolerance, and the whole platform
  fell back to offset 0. Retry across liquid majors when the supplied symbols yield nothing.
* **A benchmark must not inherit the treated unit's price basis.** The always-long control in
  `tools/p0_1_direction_audit.py` entered at the candidate's stored `fill`. But `fill` is
  **side-dependent** — `fills.entry_fill` gives `open + spread` for a BUY and `open − spread` for a
  SELL (the SELL leg is charged the spread up front to recover the exit spread the simulator does not
  model). So reusing `fill` as a long entry handed always-long a free half-spread on every SELL row
  and biased the comparison *against* the gate. Corrected to `fill + 2 × spread` for SELL rows, worth
  +0.0988R on NZDUSD — the same order as the effects being measured — and it moved the survivor count
  from 1/20 to 3/20. **The general form: when a control reuses a field produced by the treatment, ask
  what that field means for the treatment's *opposite* case.** A biased control hides real effects as
  easily as it invents them, and it looks conservative, which is why it survives review.
* **Re-deriving a value the pipeline already converted is how a 10× error gets in.** MT5's `spread`
  column is in **points** (`10^-digits`), not pips — for 5-digit FX, 1 point is 0.00001 while
  `pip_size` is 0.0001. `signal_scan._spread_for_bar` converts with `raw × point_size / pip_size` and
  stores `spread_pips`; a tool that instead multiplied the bars' raw `spread` by `pip_size` reported
  AUDUSD at 1.91R per trade when it costs 0.19R. **Consume the stored column.** If a stored value and
  a recomputed one disagree, suspect the recomputation — the scanner had it right.

## CSS: visibility and flex starvation

* **`[hidden]` loses to any class that declares `display`.** The UA rule is `[hidden]{display:none}`
  at the lowest specificity there is, so `.tt-row { display: flex }` beats it and a row that is
  *supposed* to be gone stays on screen at full height. This has now bitten three times in
  `theme_terminal.css` (the dropdown panel, the copilot dock, the ticket's pending row — which showed
  "Place order" beside the BUY/SELL pair while the type was still Market). Fixed once for all with
  `.tt-app [hidden] { display: none !important; }` in the reset block. **Assert the computed
  `display`, not the attribute** — the attribute was correct the whole time. `#ticket-pending-row`
  had `hidden` and still measured 44px.
* **In a flex column, two `flex: 1 1 auto; min-height: 0` siblings starve the first one.** The
  history panel stacks a filter toolbar and a table, both `.tt-panel__body`; the table won and the
  toolbar was squashed to **38px of its 116px** — controls rendered, clipped to an unlabelled sliver.
  `verify_ui_layout.js` reported "no silently clipped content" throughout, because that check only
  compared `scrollWidth > clientWidth` and only accepted `overflowX: hidden|clip`. **`overflow: auto`
  conceals a squashed box exactly as well as `hidden` does.** A toolbar is not a scroll region: it
  needs `flex: 0 0 auto`.
* **A panel can be over-subscribed and no flex setting fixes it.** The ticket holds the order form
  (526px), the pending table (144px) and the context strip (668px) in ~834px. Flex-competing cut the
  **form** to 252px, halving BUY/SELL and hiding volume/SL/TP entirely — the primary action of the
  app behind a scroll box. The form is not negotiable, so every region keeps its natural height and
  the panel scrolls as one column. Before adding a region to a panel, **sum the natural heights**;
  if they exceed the panel, decide which one scrolls rather than letting flex decide.
* **Designing a new guard: measure the thing that cannot be satisfied, not the thing that usually
  is.** The first attempt at the vertical-clip rule flagged any box shorter than its first child,
  which fired on `#selection-body` (35px over 442px of rows) and six other legitimate scroll lists —
  seven false positives, i.e. a check that gets ignored. The rule that works flags a box that cannot
  show **one whole control it contains** (`clientHeight < tallest input/select/button`): the squashed
  toolbar (38 < 44) fires, the lists (no controls) do not, and the table body (454 over a 276-row
  table) does not. **Always negative-test a new guard** — restore the defect, confirm it goes red,
  and confirm it stays green everywhere else.
* **`audit_endpoints.py` probes `http://127.0.0.1:8501` by default — the live engine.** Two brand-new
  routes reported DEAD with an HTML 404 purely because that process predated the code. Use
  `--base http://127.0.0.1:8599` against a server started from the current tree before believing a
  route is broken. Same run, same code: **2 dead against the stale process, 0 dead against a fresh
  one.** A stale process is the first hypothesis for any "route vanished" result.

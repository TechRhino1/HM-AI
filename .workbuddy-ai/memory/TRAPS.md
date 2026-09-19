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
* **A duplicated renderer in two front ends will diverge — audit the copy you did not change.** The
  copilot chat exists twice (`dashboard.js` and `terminal.js`), each with its own trimmed-down markdown.
  They had already drifted in opposite directions: the dashboard's bullet branch `return`ed early so the
  inline pass never ran, leaving `**bold**` as literal asterisks on most of a typical answer (nearly
  every bullet the copilot writes wraps its label); and the terminal interpolated the answer straight
  into `innerHTML` with **no escaping at all**, while the dashboard escapes first and says why. So the
  same feature had a cosmetic bug in one copy and an **XSS** in the other — `terminal.js`'s `bubble()`
  does `b.innerHTML = html`, and answers interpolate broker/journal strings (symbol names, deal comments,
  order types). The query is not echoed, so the surface is broker-controlled data rather than remote
  input — but that is exactly the data the dashboard deemed untrusted. **When a feature exists in both
  front ends, diff the two implementations before believing either is right.** `tools/verify_copilot_render.js`
  now extracts and *evaluates* both copies and asserts they produce byte-identical output, which is the
  only thing that stops the drift recurring.
* **`deepHtml` serialises `innerHTML`, never the element's own `className`.** A check like
  `deepHtml(el).indexOf('tt-copilot__msg--error')` can never pass, whatever the code does — the class is
  an attribute of the element, not part of the markup beneath it. Read `className` off the element (or
  capture the children's classes) instead. And when a panel is populated on boot as well as by the
  action under test, capture a **delta** and assert against the bubbles the drive added — reading the
  whole container lets a check pass on the greeting rather than on the answer.
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
* **Sweep the class, not the instance.** Finding one guard that reads the wrong signal is worth
  `grep -nE "res\.ok" jarvis/ui/static/js/dashboard.js` — five guards, and the last one was still wrong:
  `/api/backtest/cancel` answers **200** with `{"status": "NOOP", "cancelled": false}` for a job that had
  already finished, so `if (res.ok) toast('Cancel requested')` claimed a cancellation that did not
  happen. Not a money path, but not harmless: jobs are serialised because the box has under a gigabyte
  free, so the user stops watching a job that is still holding memory. **Check the route's body for a
  200 that does not mean success** (`NOOP`, `UNAVAILABLE`, `cancelled: false`) — the status code is not
  the outcome. Counter-example worth keeping: `runJob` in the same file was already right, checking
  `!res.ok || !res.data` *and* requiring a `job_id` — so validate the payload and say which handlers
  were the outliers, not that the controller was careless.

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
* **A timeout fallback on a CRITIC removes the criticism, and it is invisible.**
  `parallel_runner` substitutes `penalty_score=0.0` when the Devil's Advocate times out, and
  `decision_engine:644` gates on `penalty_score <= 43.0` — so 0.0 always passes. The adversarial
  check is not weakened, it is deleted, and every other gate still reads green. Two rules:
  **check whether the thing you are defaulting is a gate or a measurement** — 0 neutral, min, empty
  list and 1.0 are all "passing" values for some gate — and **if the value is also recorded**
  (here as `adversarial_penalty` in the scan columns), a fallback is indistinguishable from a real
  reading and will contaminate any analysis built on that column. Fail-open can be the right call;
  silence never is. Log it, and mark the object (here `critique_confidence=0.0`).
* **A `str`-Enum makes a type bug invisible.** `AnalystRole(str, Enum)` means `AnalystRole.RISK ==
  "RISK"`, so passing the bare string where the enum is typed works for every `==` comparison and
  only breaks on `.name` / `.value` / `isinstance`. Grep for `str, Enum` when a field looks unused.
* **A guard that "helpfully" re-anchors its own baseline fails open.** `DrawdownGuard` reset any
  baseline more than 1.5× current equity, on the theory that only a withdrawal moves equity that
  far. A loss of >33.3% moves it just as far, so a 40% crash reported `0.0%` and `passed=True`,
  and the same line erased `peak_equity` and cancelled the circuit breaker. **Ask of every
  heuristic: what does it do on the input it was built to catch?** If a test cannot separate the
  two cases in a single instant — and here it cannot, because a *realised* loss lowers balance
  exactly like a withdrawal — the guard must not guess. Fail **closed** and give the operator an
  explicit `reset_baselines()`. A spurious halt costs a day; a missed one costs the account.
* **A "process stays open across midnight" check that is gated on having a database never fires in
  the configuration that most needs it.** `DrawdownGuard`'s rollover read `last_saved_date` under
  `if self.db_path:`, so the in-memory guard (`is_offline()` forces `db_path=""` — the documented
  hermetic-backtest path) never rolled over at all. Track the day in memory.

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
* **Tests that build the app share the LIVE risk database — and you cannot fix it by moving
  `JARVIS_DATA_DIR`.** With no conftest, every `RiskEngine`/`JarvisOrchestrator` got a
  `DrawdownGuard` on `data/jarvis_drawdown_state.db`, the engine's own file: tests inherited the
  operator's baseline *and* wrote back into it (found holding `daily_start_equity = 487.36` beside
  `peak_equity = 10150.0` — a $500 mock account). Redirecting the data dir breaks **12 tests in
  `test_backtest_optimizer.py` / `test_regime_optimizer.py` that read real parquet out of
  `data/`**. The fix is `tests/conftest.py`: an autouse fixture forcing an **in-memory** guard
  (`db_path=""`), with `@pytest.mark.drawdown_persistence` to opt out.
* **Removing a defect can break a test that was passing because of it.** The drawdown fix made
  baselines sticky, and `test_d1_online_ml_and_trade_memory_learning_loop` started failing — not
  because the fix was wrong, but because the old re-anchor had been silently absorbing cross-test
  equity jumps. When a correct fix turns a suite red, suspect **shared state the bug was masking**
  before suspecting the fix. Isolate first, then re-measure.
* **Ordering tests need TWO valid candidates.** Two `broker_symbols` mutations survived because each
  fixture made only one candidate resolvable: dropping the canonical name or reversing the alias list
  changed nothing, since the fuzzy scan returned the single match either way. Same class as the
  "third value" rule — an ordering assertion is only real when the order decides the outcome.
* **When a module exists to fix a bug class, grep for other code doing the same job.** Symbol
  resolution is implemented **three** times; the hardened one (`broker_symbols`, prefix-only fuzzy,
  returns None on failure) serves the *live* path, while `mt5_history` still does a **substring**
  scan (the COPPER→SouthernCopper bug verbatim) and `MT5Client.resolve_symbol_name` returns the
  canonical name **in paper mode**, so `historical/acquisition.py` cannot download real bars unless
  running live. One `grep -rn "def resolve_" --include=*.py jarvis/` finds all of them.
* **A mutant survives when the fixture never contains the value the mutation changes.** The one miss
  in the 36-mutant `ai_dissector` battery was `v == "BULLISH"` → `v != "BEARISH"`: every alignment
  fixture used only BULLISH/BEARISH, where the two are identical. Adding a **third, unrecognised
  value** (`NEUTRAL`) killed it. Same class as the clamp: enumerations of a categorical need a case
  *outside* the enumerated set, not just each member of it.
* **A scored pillar can floor above zero, and if the total is persisted the floor is a data defect,
  not a style choice.** `ai_dissector` assigns `v = 7` *before* `if vol:`, so volatility is really
  7–15 — it can never penalise, and "no data" equals "worst data". Because `dissection_score` is
  written into the `Decision` schema and the backtest scan columns, the stored feature's range is
  **6.7–94.3, not 0–100**. Before dismissing a floor as cosmetic, grep where the score is *stored*.
* **A docstring's per-pillar maximum is not the achievable maximum.** Three of `ai_dissector`'s
  seven "0–15" pillars top out at 13, so a total of 105 is unreachable and the tier bands sit ~6%
  stricter than written. Derive the ceiling by summing the branches, as with any other expected
  value.

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
* **A bare `lib/` in `.gitignore` silently ignores `tools/lib/`.** `.gitignore:17` is `lib/`, which
  matches at *any* depth, so a shared helper placed at `tools/lib/dom_stub.js` worked locally and was
  absent from the repo — `git status` did not list it and nothing warned. The refactored
  `verify_dashboard_render.js` would have `require`d a file that never reached a clone: a green suite
  locally, a crash for everyone else. `git check-ignore -v <path>` is the one-command check, and it must
  be run for **every new file a tracked file depends on**, not just the file being added. The shared DOM
  stub now lives at `tools/dom_stub.js`, outside the ignored path. (`git add -f` would also work, but a
  file that every future tool has to remember to force-add is a trap of its own.)
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
  `--base http://127.0.0.1:8611` against `.scratch/srv8611.py` (started from the current tree) before
  believing a route is broken. Same run, same code: **2 dead against the stale process, 0 dead against a
  fresh one.** A stale process is the first hypothesis for any "route vanished" result. Use **8611**, not
  8599 — `verify_ui_live.py` binds 8599 for itself.
* **Python's `read_text()`/`write_text()` round-trip converts CRLF → LF silently.** Restoring a file after
  a mutation therefore left `git status` reporting it modified while `git diff` was **empty**
  (`git ls-files --eol` → `i/lf w/crlf`). `git checkout -- <file>` clears it. Never conclude a file is
  changed — or unchanged — from `git status` alone in this repo.
* **A no-op method in a stub makes a whole renderer unobservable, and every check still passes.**
  `verify_dashboard_render.js`'s chart stub had `setMarkers() {}`, so `drawTradeMarkers` was never seen:
  183 checks passed without asking whether any marker was drawn. When adding a stub, make every method the
  code under test calls *record*, and confirm by mutating the renderer that some check fails.
* **A multi-line mutation anchor in a bash heredoc gets mangled in transit.** Several batteries reported
  "0 matches" for anchors that were byte-for-byte present; the same anchors worked once the script was
  written to a file with Write and executed. Write mutation scripts to `.scratch/*.py` — do not paste
  them into a heredoc.
* **`re.escape()` escapes the newline itself**, so `re.escape(s).replace(re.escape("\n"), r"\r?\n")`
  silently does nothing and every multi-line anchor misses. Escape each *line* and join with `\r?\n`.
  Files here also mix CRLF and LF (e.g. `remote_auth.py`: 408 of each), so try both endings.
* **A mutation run that gets killed leaves the tree mutated, and the next run measures against it.**
  A foreground mutation battery hit the 120 s timeout and was SIGTERM'd mid-flight; it left
  `sl_long = long_entry + risk` in `tools/p0_1_direction_audit.py`. The retry then reported a meaningless
  "21/22 caught" because *every* run was measured against a baseline that was already broken. A pytest run
  costs ~5 s, so any battery over ~20 mutations exceeds the foreground limit — **run it in the background**:
  snapshot each file up front, restore in `finally` *and* `atexit`, and refuse to start unless the suite is
  green and `git diff` holds only the change you intend.
* **A helper that joins anchor lines silently misses when you pass it one string.** After the heredoc
  trap above I built `anchor(*lines) -> NL.join(lines)` and then called it as
  `anchor("line one:\n    line two")` — one argument containing `\n`. `NL.join([s])` returns `s`
  unchanged, so in a CRLF file **every multi-line anchor matched 0 times** and the battery reported
  five MISSED mutations that the tests would have caught. The tell: the misses are all multi-line and
  all report `matched 0 times` rather than a real failure. Make the helper split its inputs —
  `NL.join(l for chunk in chunks for l in chunk.split("\n"))` — and treat `count(old) != 1` as a
  harness bug to fix before reading the score.
* **A MISSED mutation is an equivalent-mutation candidate before it is a test gap.** Two of 24 in the
  `sessions.py` battery were unobservable: `is_prime = is_weekday and (...)` had become redundant once
  an early weekend return was added above it, and `max(0, ...)` clamped a difference that is always
  positive inside that branch. Both are harmless dead code, not missing coverage. Ask "can any input
  reach the mutated line and still differ?" before writing a test to chase it.
* **A name bound inside one `try` and read by the next turns one exception into a silent total
  failure.** `master_confluence.score()` assigned `reg`/`st`/`liq` inside its first `try`, then read
  them in four later blocks that each had `except Exception: <component> = 0`. A `regime` of `None`
  raised, the names stayed unbound, and every following block died with a `NameError` its own handler
  swallowed → **0/100 → WEAK → the decision gate blocked the order, with nothing logged**. Assign
  anything shared *before* the try blocks, and never let `except: x = 0` be silent when the value is
  a gate.
* **Derive expected scores from the source, not from the docstring's headline numbers.** Six of my
  first-run `master_confluence` failures were my own arithmetic: I missed the `elif adx >= 15:
  trend += 2` ladder and the `min(20, ...)` cap that made two bonuses invisible.
* **A docstring is a claim — check it against the code, and check `git log --diff-filter=A`.**
  `jarvis/risk/hrp_allocator.py` advertised Hierarchical Risk Parity and shipped inverse-variance
  weighting; the *adding* commit was already a 48-line stub and its one HRP-specific helper,
  `get_correlation_distance`, has never been called. Its output is still published as "HRP" in
  `reports/backtest_3month_report.md`. Before trusting a module named after a known algorithm, grep
  for unused helpers and see whether the file was ever anything else.
* **An epsilon added to avoid dividing by zero can invert the quantity's meaning.**
  `variances[variances <= 0] = 1e-6` gives a flat series an inverse variance of a **million**, so it
  took ~100% of the portfolio allocation — the asset the data says least about won. (In a backtest a
  flat series is exactly a symbol that never traded.) Always test a `1/epsilon` path with a
  degenerate input.
* **A test that asserts only the aggregate verdict can be satisfied by a *different* failure.** Two
  of 21 mutations survived the first `trade_guard` battery because the SELL "stop exactly at entry"
  tests asserted `passed is False` while leaving the default **BUY-shaped** take profit in the
  fixture — so when the stop check was mutated away the take-profit check failed instead and the test
  still passed. Give every input the assertion is *not* about a valid value, and assert on the
  reason text, not just the boolean.
* **Do not call `symbol_registry.resolve()` from a per-snapshot path.** It `logger.error`s for any
  unregistered symbol, so a loop over the universe turns into one ERROR line per symbol per snapshot.
  Use the registry for sizing/spread lookups (once per trade), not for classification in a hot loop.
* **A dict keyed by timeframe is populated per trade style, so an absent key means "not computed",
  never "opposes".** `market_context.py:115` fills `mtf_alignment` with D1/H4/H1/M15 for SWING,
  H4/H1/M15/M5 for DAY_TRADING and H1/M15/M5/M1 for SCALP. `structure_analyst` read
  `mtf.get("H4") != bias` as divergence, so **every SCALP decision** carried a fabricated
  "structural divergence (H4 is None)" risk factor, and its +10 confluence bonus — which needs *both*
  H4 and D1 — was dead code outside SWING. Whenever a lookup is `.get(...)`, ask which branch of the
  producer could have skipped that key before treating a `None` as a value.
* **A falsy default cannot express "explicitly empty".** `self.news_calendar = news_calendar or []`
  followed by `if not self.news_calendar:` meant `MacroAnalyst(news_calendar=[])` still hit the live
  `GLOBAL_NEWS_ENGINE` — there was no way to say "no news", and no way to unit-test the analyst
  offline. Keep the `None` and test `is None` when the sentinel has to mean "go ask".
* **A comparison against a string literal is a claim that the literal is ever produced — grep the
  producers before believing the branch.** `devil_advocate` penalised a fresh sweep with
  `sweep_type == "BUY_SIDE"` / `"SELL_SIDE"`, but the only producer (`liquidity.py:108,115`) writes
  `"BULLISH_SWEEP"` / `"BEARISH_SWEEP"`, so the branch had never run. The strings it wanted do exist
  — in `news.py:720`, for an unrelated narrative — which is exactly what makes this survive a
  read-through. Same shape as the correlation check needing an `"EURUSD"` key in `mtf_alignment`,
  which only ever holds timeframes. For every `== "CONSTANT"`, `grep -rn 'CONSTANT'` and confirm at
  least one *assignment*, not just another comparison.
* **Numpy integers are not Python integers, but numpy floats ARE Python floats.**
  `isinstance(np.int64(0), int)` is **False**; `isinstance(np.float64(0.0), float)` is **True**. So
  `isinstance(x, (int, float))` silently misses an int64 column while accepting a float one. In
  `market_context.py:104` that means a bar-time column of integer epoch seconds is discarded in
  favour of `datetime.now()` while the same value as floats is handled correctly. Use
  `numbers.Real` / `np.integer` explicitly, or convert the column, when dtype is not controlled.
* **A fallback to `datetime.now()` makes a result depend on the hour you run it.**
  `SessionEngine.get_active_killzone(None)` reads the wall clock, and
  `master_confluence:138` passes `getattr(context, "timestamp", None)` — so a context with no
  timestamp scores its killzone component from *now*. `test_a_none_context_does_not_raise` asserted a
  fixed total and therefore passed at 01:00 UTC and failed with 14 at 13:00 UTC, i.e. it was broken
  for ~8 hours of every weekday and looked fine overnight. When a test asserts a constant, check
  whether anything in the path defaults to `now()`; freeze it rather than assuming.

## Mutation batteries (round 28) — the harness must survive being killed

* **A mutation script that gets SIGTERM'd leaves a mutant applied to the real source.** It happened
  twice in one session: `hunt3.py` left `span_fast = self.ema_fast` and `hunt4.py` left
  `c_pdi = float(plus_di.iloc[-1])` in `jarvis/market/momentum.py`, both silently. Git does not
  notice if it normalises line endings, and the next script then copies the corrupt file into its
  own backup and measures against a *mutated baseline* — producing fixtures that are wrong in a way
  that looks plausible (base `pers=9` on a frame that really scores 4).
  **Every mutation script must install `signal.signal(signal.SIGTERM/SIGINT, restore)` that copies
  the backup back and exits, and every run must end by asserting `source == backup`.** Keep the
  pristine copy outside the script (`.scratch/_<module>_orig.py`) and restore from that, never from
  a backup the same run may have overwritten.
* **In-process mutation checks need `importlib.reload`.** Rewriting the source file does nothing to
  an already-imported module, so a "does this mutant change anything" probe reports *every* mutant
  as MISSED. Either reload or run pytest in a subprocess (the battery does the latter).
* **Reading with `open(..., encoding="utf-8")` translates CRLF to LF, but writing translates LF back
  to CRLF.** Multi-line mutation anchors therefore match on read and the file stays CRLF on write —
  fine, but never assume the on-disk bytes match what you read.
* **Batch, don't loop.** Applying a mutant once and comparing it against 1500 pre-computed baseline
  verdicts takes seconds; applying it per frame with a reload each time times out at ~2 minutes and
  is what triggered the kills above.
* **A missed mutant is often dead code, and that is a finding.** Proving it is better than chasing
  it: `np.clip(score, -100, 100)` never binds because its terms are bounded at 45+25+30; three of
  the four stack constants in the persistence loop can never carry a bar across zero. Record the
  proof *in the test suite* (assert the reachable value set) so the gap is not re-opened later.

## Mutation batteries (round 29) — anchors and fixtures, not just signals

* **A duplicated anchor silently mutates the WRONG occurrence.** `.replace(old, new, 1)` hits the
  first match, and `volatility.py` has `max_allowed_spread_pips=max_allowed_spread_pips` in both the
  early return (16-space) and the main path (12-space), and `current_spread_pips > max_allowed...`
  with and without spaces around `=`. Both reported MISSED against tests that could only see the
  main path. Anchor on the enclosing line, including its indentation.
* **An anchor with hardcoded indentation is a coin flip.** Two of 98 missed on
  `"* 100.0\n        eq_highs"` because the real code is 12 spaces, not 8. The battery already
  reports ANCHOR-MISS — trust it, and fix the anchor rather than deleting the mutant.
* **Symmetric fixtures make whole classes of mutant invisible.** A sine oscillation has four
  identical troughs, so `swing_lows[-1]` vs `[0]` is unobservable; a symmetric sweep candle makes
  `mag_high == mag_low`. Build fixtures where the discriminated quantity *differs* — a descending
  zigzag for swing selection, an asymmetric candle for magnitude.
* **`>=` vs `>` needs an exact hit, and floats will not give you one for free.** `97.9 - 97.0` is
  `0.9000000000000001`, so `body_pct` is `0.45000000000000007`, not `0.45` — the `>= 0.45` mutant
  survived until a search found `open=96.576, close=97.701, low=97.0, high=99.5`
  (`1.125 / 2.5 == 0.45` exactly). Conversely `vol_ratio = cur / (median + 1e-9)` can essentially
  never land exactly on a threshold: `0.625 / 0.25 == 2.5` but `0.625 / 0.250000001 < 2.5`.
  When an exact hit is impossible, mutate the constant's *value* instead and pin the band.
* **`numpy.bool_ is not Python `True`.** The engine reads levels with `float(...)`, so it compares
  Python floats and returns a Python bool — but passing a `np.float64` in as a threshold makes the
  comparison return `np.bool_`, and `np.True_ is True` is False. Convert with `float()`.
* **A survivor is often a proof, not a gap.** Ten survived: two swing comparisons (`==` vs `>=` is
  the same test when the element is inside the window), four boundary equalities that a later
  gate makes unreachable, `np.maximum` re-bracketing (associative), and three regime thresholds
  blocked by the `+1e-9` epsilon. Each now has its proof asserted in the suite.

## Picking the next module (round 30) — leverage, not size

The "largest untested module" heuristic is wrong for this codebase. Most of the big untested files
are one-off root scripts (`run_6month_backtest.py`, `verify_system.py`, `test_*_api.py` that never
got moved into `tests/`). The real library modules without suites are small. Prefer **how many
verdicts depend on the module**: `jarvis/backtesting/metrics.py` is 113 lines but produces every
headline number the project quotes about itself, and it feeds `engine.py`, `walk_forward.py`,
`optimizer.py` and `regime_optimizer.py`. When generating the inventory, strip root-level scripts
and `tests/`-named files first or the ranking is meaningless.

## Measurement-layer findings worth generalising (round 30)

* **Two return paths in the same function usually disagree on their keys.** `metrics.py` returns 12
  keys for empty input and 13 otherwise. Assert `set(full) - set(empty)` rather than eyeballing.
* **A "perfect" input often hits a degenerate guard.** Zero dispersion fails `std > 1e-6`, so a
  flawlessly profitable book reports Sharpe 0.0. Always test the all-winners and all-zeros cases.
* **Sentinels masquerading as measurements.** Profit factor 99.0 and Calmar 10.0 are magic
  constants for "undefined", and they then flow into a fitness score as if they were real.
* **Two independently-tracked maxima can describe different events.** `max_dd_dollars` and
  `max_dd_pct` are each updated on their own, against a running peak that never resets.
* **`>` vs `>=` on a running maximum is a no-op** (recording an equal value changes nothing) — as is
  clamping a value that an earlier `np.clip` has already bounded. Both are legitimate survivors.

## Frontend layout: the two-line nav, and why the CSS was not at fault (round 31)

* **A shared JS widget that picks its own parent will pick the wrong one.** `auth.js:137` resolves
  its host as `document.querySelector(".nav-links-wrapper") || ".hud-actions" || "header"` and
  *appends into it*. Three of the six pages declare a real `#auth-header-widget`; the other three
  did not, so the account pill landed **inside** the four-pill market nav — one `flex-wrap: wrap`
  row holding five items, which then broke onto two lines at 1440px and three at 390px. Fix at the
  source (declare the slot, in the header, as a **sibling** of the nav), then defensively
  (`flex-wrap: nowrap` + horizontal scroll). Grep every page for the element a shared widget
  targets before styling around its absence.
* **`flex: 1 0 auto` is the "never fits" flex shorthand.** `flex-shrink: 0` with an auto basis
  pins every item to its intrinsic width, so a 5-tab bar needed 567px inside a 390px viewport and
  the last tab was clipped off-screen. `flex: 1 1 0` + `min-width: 0` fits any count at any width.
  A `flex-shrink: 0` on a horizontal tab strip is almost always the clipping bug.
* **Load order differs per page, so an "override" layer only overrides where it loads last.**
  `stocks/india/india_options` load `page.css → auth.css → hm_ui.css` (design system last, so it
  wins); `dashboard.html` loads `hm_ui.css` **first**, then `theme_terminal.css`. A rule added to
  the design system governs the market pages and *not* the dashboard. Read the `<link>` order of
  the specific page first.
* **A page sheet's `!important` beats the design system's plain rule, however late the sheet
  loads.** `stocks.css` forces `display: flex !important` on `.nav-links-wrapper` inside its mobile
  query, defeating `hm_ui.css`'s `display: none`. `!important` in the later sheet wins; without it,
  source order wins. Both halves are needed to predict the result.
* **Do not "fix" a layout defect a bounding-box comparison cannot reproduce.** The rail *looked*
  overlapped in a 1440 screenshot — the account pill apparently sitting on the session badge.
  Measuring every child's rect proved zero overlap at 1440/1280/1024/820/390: it was the rail's
  secondary band wrapping to a second row, left-aligned under the brand. Screenshot-then-measure,
  never screenshot-then-edit.
* **`/` is `dashboard.html`, not `index.html`.** `server.py:381` maps
  `"/", "/index.html", "/dashboard", "/dashboard.html"` → `_serve_dashboard_ui()`; the classic
  terminal is `/classic` → `index.html`. Probing "root" and expecting the terminal reports a
  missing `.mobile-nav-bar` that was never in that template.
* **`networkidle2` never settles against these pages** — every one polls on a timer, so the wait
  burns its full timeout on every navigation. Use `waitUntil: 'load'` plus a fixed settle delay, or
  a six-page sweep takes minutes and looks hung.

## Frontend: a `data-*` attribute is not the class of the same name (round 31, part 2)

* **The attribute and the class are separate contracts, and a component can satisfy one while
  missing the other.** The nav drawer carries `data-dropdown-panel` (what `dashboard.js` reads to
  decide whether a click landed inside a panel) but deliberately not `.tt-dropdown__panel` (what
  the CSS targets). It therefore inherited **none** of that rule's properties — including
  `z-index`. The drawer computed to `z-index: auto` while its scrim sat at an explicit
  `--hm-z-drawer - 1`, so **the scrim painted over the drawer**. It opened, closed, switched
  views, moved focus and passed every functional assertion while being dimmed to unreadability.
  Before assuming a component inherits a rule, check which selector the rule actually uses.
* **Assert paint order, not just behaviour.** "It works" and "it is visible" are different
  claims. `getComputedStyle(el).zIndex` plus
  `document.elementFromPoint(centreOf(el))` catches "renders underneath its own backdrop" — a
  class of defect that no functional assertion and no contrast check will report. Add it to any
  probe for an overlay.
* **Prefer the controller that is already on the page to a second state machine.** The drawer
  reuses the existing `[data-dropdown-trigger]`/`aria-controls` handler (toggle, Escape,
  click-outside, `aria-expanded`) and the existing `[data-view-btn]`/`[data-pane-btn]` delegation
  (`setView`/`setPane` sync `aria-selected` across every carrier). Result: ~12 lines of new JS and
  no second source of truth for the active view. The only new logic needed is the case the old
  controller deliberately excludes — it ignores clicks *inside* a panel, which is right for a menu
  and wrong for a navigation drawer — and that handler is scoped to the new element so nothing
  existing changes.
* **An `aria-selected` that only a click can set is unset on first load.** `setView` syncs it on
  every change, but nothing runs at boot, so a new control that relies on it looks inactive until
  the user interacts. Mirror the inline controls' initial state in the markup.
* **A tool that measures a control only if you list it will exempt the control you forgot.** Add
  new interactive elements to `tools/verify_ui_layout.js`'s `TAP_SELECTORS`; the probe already
  skips elements with no box, so listing a control that is visible in only one state is correct —
  it gets measured in the state where it is reachable.



## Frontend: `display: contents` must be proved by unwrapping, not asserted

Adding a wrapper element to an existing flex row (so a phone can collapse it behind a
disclosure) risks changing the desktop layout. Asserting `getComputedStyle(wrap).display ===
'contents'` proves **nothing** — that is a claim about the property, not about layout, and it
passes even when the wrapper is being laid out as a box. Measure the live geometry, then
`unwrap()` the element in the DOM (`insertBefore` each child, `removeChild` the wrapper), measure
again, and require the two to be identical. That compares against the *actual* pre-change
structure rather than a guess at it. Also note `parent.children` still lists a
`display: contents` element — it affects layout, not the DOM tree.

Related: `display: contents` is only half the story when the same selector also needs to be a
positioned box at another breakpoint. Base `.panel { display: contents }` + a later media block
`.panel { position: absolute; display: none }` works purely on source order (equal specificity,
later wins) — but the media block must come *after* the base rule in the same file.

## Frontend: `nowrap` + `flex-shrink: 0` is the "can neither fit nor yield" combination

A row that cannot wrap and whose children cannot shrink has no way to resolve a shortage, so it
overflows. If an ancestor is `overflow: hidden` — `.terminal-panel` is — the overflow is **not**
visible as a scrollbar or a cramped layout: the trailing controls are simply gone. They remain in
the DOM, keep their handlers, and are absent from every screenshot at a width nobody tested.

This is invisible to any assertion that does not measure a box, and it is width-dependent, so a
single-viewport check misses it. On `/classic` the chart header needed ~1071px against panels of
786px (1440), 488px (1024), 365px (901) and 374px (390) — so the controls vanished at *every*
width tested, and the repo's layout harness does not cover `/classic` at all. **Sweep widths, and
sweep the pages the harness does not name.** The fixes, in order: let the row wrap; give children
`flex-shrink: 1` + `min-width: 0` so they may shrink; only then hide what still does not fit.

`min-width: 0` is load-bearing and non-obvious: a flex item's automatic minimum size is its
min-content size, so a strip containing a `white-space: nowrap` badge is pinned at that badge's
width no matter how small the container gets. `min-width: 0` lifts that floor and lets the strip
wrap its own children instead.

Do **not** combine `flex-direction: column` with `flex-wrap: wrap` on the same element — a column
container wraps into extra *columns*, which reintroduces exactly the horizontal overflow you were
trying to remove. Use `width: 100%` on the children and let the row wrap.

## Frontend: a flex item's computed `display` is blockified

`display: inline-flex` on an element that is itself a flex item computes as `flex`. Asserting the
authored value fails on a correct stylesheet. Assert `flex` or `inline-flex`, or assert the box.

## Tooling: text-mode `open()` rewrites CRLF, so a repair pass churns the whole file

`open(path, encoding='utf-8')` without `newline=''` enables universal-newline translation: a CRLF
file is read as LF, and a repair that rewrites only five lines also rewrites all 3429 line
endings. It is invisible in `git diff` when `core.autocrlf=true` (the index is normalised to LF
either way), so it looks harmless while the working tree silently diverges from the checkout.
Always pass `newline=''` on both the read and the write when a tool rewrites a file in place, and
verify with `raw.count(b'\r\n')` before and after — `terminal.js` and `terminal.css` are pure CRLF,
`index.html` is pure LF.

## Tooling: a unified diff is "corrupt" if it does not end with a newline

Filtering `git diff` output into per-hunk patches (to stage two independent fixes that live in one
file without interactive staging or `git stash`) fails with `corrupt patch at <last line>` when the
last kept hunk's final context line is a single space — a blank source line — and the join leaves
the file ending in that space. Append `"\n"` to the joined output. Also note the *content* lines of
a CRLF file's diff carry a trailing `\r`, so the patch must be read and written with `newline=''`
or the round-trip mangles them.

Staging a subset of hunks this way is worth the trouble here: `git stash` is forbidden in this
repo, and `git add -p` is interactive.

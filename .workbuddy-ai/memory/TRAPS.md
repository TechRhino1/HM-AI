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

## Tool harnesses (moved out of MEMORY.md to keep the injected file small)

Counts as of round 35: `verify_ui_live.py` 46 · `verify_dashboard_render.js` 209 ·
`verify_terminal_render.js` 55 + 5 FIND/CLEAR · `verify_copilot_render.js` 23 ·
`verify_dashboard_nav.js` 31 · `verify_ui_layout.js` 238 · `audit_endpoints.py` 46 ·
`audit_wiring.py` 136 modules / 0 broken · `audit_encoding.py` 0.

* **`verify_ui_live.py` starts its own server on :8599.** Nothing may be listening there. If
  something is, its probes hit that server instead — and an engine-less server answers
  `attached=False` / `503`, which reads exactly like a regression in the code you just changed.
  Run the browser suites against **`.scratch/srv8611.py` on :8611**.
* **`tools/dom_stub.js` is not under `lib/`** — a bare `lib/` line in `.gitignore` matches at *any*
  depth, so `tools/lib/dom_stub.js` worked locally and was absent from every clone. Check a new file
  with `git check-ignore -v <path>`.
* **A stub must mirror the real markup's initial state.** The Analyst/News strip's panels ship
  `hidden` + `data-state="loading"` in `dashboard.html`, and both renderers early-return on a hidden
  host — leave them visible and the full panel's refresh makes `setContext()`'s own render look
  redundant, so the assertion passes for the wrong reason.
* **Build nodes with `elementFor(id)`, never `registry.get(id)`** — the registry is a lazy Map, so
  an un-queried id returns `undefined`.
* **`verify_terminal_render.js`**: `terminal.js` registers `fetchHistory` *only* inside `setInterval`,
  so an inert `setInterval` stub makes the history table — and every assertion on it — a silent
  no-op. Capture the intervals and tick them.
* **The dashboard chart stub must record `setMarkers`**; a no-op stub there made `drawTradeMarkers`
  wholly unobservable.
* **Assert against markup as rendered.** Never `html.replace(/\s+/g,'')` — it eats the space in
  `<span class="…">` and makes a correct string fail. Bind a value to its own label via
  `metricValue(html, label)`, or a swapped counter passes.
* **`verify_ui_layout.js`'s total is not fixed** — it counts controls per viewport, so hiding a
  control lowers it (238 → 229 was a ticket's pending row correctly disappearing, not a lost check).
  Read the FAIL lines, never the total. `JARVIS_BASE` overrides the base; `verify_dashboard_nav.js`
  takes `DASH_URL`.
* **`audit_endpoints.py`**: add POST-only routes to its `POST_ONLY` set or they report DEAD, and
  probe with `--base` against a server built from the *current* tree, or a stale engine makes new
  routes look dead.
* **Run browser suites one at a time.** Three in parallel plus a probe starved `forex`/`options`
  into 45s navigation timeouts, which read exactly like a regression.
* Screenshots: `.scratch/shot_one.js <tag> <page> [w] [h]`, honours `JARVIS_PORT`.
  **`agent-browser` does not support Windows** — drive real Chrome via `puppeteer-core` from the
  managed node workspace.

## Risk control: paper and live share one drawdown database (round 35)

`RiskEngine.__init__` builds its guard with
`db_path="" if is_backtest else "jarvis_drawdown_state.db"` — so **paper and live share a file**, and
paper reports `equity = 10000.0 + paper_pnl`. A paper session that runs to 10,150 leaves that as
`peak_equity`; `peak_equity` moves **up only** and is **never reset daily** (only `daily_start_equity`
re-anchors on a date change). A live account of $777 then reads as a **92.34% drawdown against a 10%
cap**, so `check_limits().passed` is False, `get_risk_multiplier()` returns `0.0`, and the engine
refuses **every** trade at `risk_engine.py:201`, `:405` (`authorized: False, lots: 0.0`) and
`orchestrator.py:551` (the EXECUTE→WAIT backstop).

Note the asymmetry, which cost a wrong diagnosis for several rounds: `daily_start_equity` *does*
self-heal to the current equity on a new UTC day, so the **daily** cap was never the problem. Only
the portfolio peak is permanently poisoned. The re-anchor is operator-only, by design —
`DrawdownGuard.reset_baselines(current_equity)` is the sole supported way to move a baseline down
(the module docstring explains why the guard refuses to guess at a withdrawal).

**Second, independent defect in the same area:** `config/settings.json`'s entire `risk` block is
**dead configuration**. `jarvis/config/settings.py` loads it into `cfg.risk` and nothing in the tree
ever reads it — the only two references are the writes in that file. `RiskEngine()` is constructed
with **no arguments** at `orchestrator.py:98`, so the constructor's hardcoded literals win, and they
differ from the declared config in the *looser* direction (`max_open_positions` 2 declared → 3
effective; `max_symbol_positions` 1 → 2; `max_daily_loss_pct` 5.0 declared → 4.0 effective).

## Measuring layout: rows, vacuous selectors, and probes that blame the server (round 38)

Four traps, all of which produced a confident wrong answer before being caught.

* **Distinct `top` values are not distinct rows.** `.terminal-hud`'s children reported
  `tops=[10, 14]` at `h=50`, which reads as two lines. It is ONE row: two centre-aligned children of
  different heights get different `top` values by construction. Cluster rows by **vertical-interval
  overlap** (`[top, bottom]` intervals merged greedily), never by `top` equality. With the corrected
  detector the whole "the classic header is two lines" finding evaporated.
* **A selector that matches nothing passes vacuously.** The probe used `.stocks-hud` as the header on
  all four market pages; only `stocks.html` has that class (`india.html` → `.india-hud`,
  `india_options.html` → `.opt-hud`, `index.html` → `.terminal-hud`). Three pages were never measured
  and reported clean. **Assert the element was FOUND before asserting anything about it**, or "0
  failures" means "0 measurements".
* **`scrollWidth > clientWidth` is a scroller working, not a defect.** Flag overflow only when the
  computed `overflow-x` is not `auto`/`scroll`. A native tab strip and a swipeable metric strip are
  *supposed* to report overflow.
* **Assert each surface against its own documented bound, not a blanket rule.** `.stocks-hud`,
  `.india-hud` and `.opt-hud` are an explicit 2–3 row CSS grid at ≤900px (`hm_ui.css` § "Header: two
  rows, brand + account share the first"), row 3 being the search. A blanket "≤1 row / ≤120px" rule
  flagged all three as broken. Flagging deliberately-designed working code is how a "fix" becomes a
  regression — read the block comment above the rule before overriding it.

**A child cannot escape an ancestor's `display: none`.** `#auth-header-widget` sits inside
`.tt-rail__group--secondary`, and `theme_terminal.css:1252` sets that group to `display: none` at
≤767px. Overriding the child is impossible; the whole subtree is out of the box tree. `dashboard.html`
has zero other `HM_AUTH` references, so the dashboard had **no login/logout on a phone at all** — a
functional loss that no layout assertion catches, because "the element exists" stayed true throughout.
`auth.js` now renders one markup string into `#auth-header-widget` *and* every `[data-auth-mount]`.
**When hiding a container on mobile, enumerate what is inside it and check each thing is reachable
elsewhere.** `/classic` also has its auth widget inline-`display:none` (`index.html:266`) and that one
is *correct* — it has its own auth dropdown; the difference only shows up if you look.

**Probe flakiness, two kinds, both of which look like regressions:**
* **An engine-less server cannot bootstrap the app.** `auth.js` fills its mount only once auth state
  resolves, and `dashboard.js` wires the drawer controller; against `.scratch/srv8611.py` neither
  completes, and a fixed sleep reported "drawer does not open" + "no login control" — properties of the
  *server*. Gate the probe on the app actually bootstrapping and print a NOTE naming the cause, instead
  of asserting. Measured: the same probe was 13/13 on the live engine and 9/13 on the scratch server.
* **Sampling during a CSS transition catches `opacity: 0`.** A fixed sleep after a viewport change or a
  click intermittently failed a visibility check. Use `page.waitForFunction(<the condition>)` with a
  timeout, then assert. Fixed 3 flaky checks; 4 consecutive clean runs afterwards.


## Auditing trade data: sentinels, origin, and counting only what is real (round 39)

Tool: `tools/audit_trades.py` (read-only). Run:
`AUDIT_EQUITY=<equity> python tools/audit_trades.py --db data [--json OUT]`.
Full findings: `AUDIT-TRADES-2026-09.md`.

* **`0.0` is a sentinel, not a value.** `sl=0`/`tp=0` mean "unset"; `pnl=0` means "not closed".
  Excluding them cut a confident **44-row "inverted take-profit" false positive down to the real
  20**. Before any direction test, exclude `sl=0`/`tp=0`; before any realised-P&L test, exclude
  `pnl=0`. (One true negative did slip through: `sl = -40.28` on ticket 37779531 — a sentinel of
  0.0 hides it because 0.0 is filtered and negatives are not. Check `sl < 0 or tp < 0` too.)
* **Never compute risk from `|entry - sl|` on an inverted row** — that is not risk. And **never
  on a synthetic row**: doing so inflated the breach count **150 -> 27 (5.5x)**. Always split a
  finding real vs synthetic before quoting it.
* **Timestamp precision is a reliable origin discriminator.** Derived from the code, not guessed:
  `sync_mt5_history` (`database.py:228`) formats an int epoch -> **whole seconds**;
  `log_trade` (`database.py:161`) formats `datetime.now()` -> **microseconds**. So
  `timestamp like '%.%'` == locally stamped and never reconciled to a broker deal. 136/245 rows
  here. Those rows carry fabricated prices from the **still-live** fallback at
  `tradingview_provider.py:583` (`base_p = 1.0850` EURUSD, 65000 BTC, 3500 ETH, 150 SOL).
  `mt5_client._paper_fill_price` was already fixed for the same bug; the provider was not.
* **A defect's apparent cause is often not its real one.** "closed_at == timestamp on 109/109"
  looks like a bad close time; it is actually `database.py:287` writing the EXIT time into
  `timestamp`, destroying the entry time (which also breaks id-order chronology). Read the
  UPDATE, not just the symptom.
* **Let the outcome arbitrate an ambiguous field.** 20 rows had `sl` on the wrong side — could be
  an inverted bracket or a flipped `action`. The 16 closed ones were **16/16 winners**, which a
  real stop cannot produce, so `sl` is holding the take-profit. `tp` was never wrong (0/245),
  which localises the corruption to `sl`.
* **One root cause, one finding.** Four "two live copies of <db>" entries hid that a single
  path-resolution defect produces all four. Likewise do not report the same 15 `pnl=0` rows at
  two severities.
* **Quote a rate against the right denominator.** "114/245 rows have pnl == expected_value"
  understates a 100% defect, because 131 open rows have no realised result at all. It is
  **109/109 closed rows**.
* `executed_trades` has **no `exit_price`**, and neither table has `commission`/`swap` — so
  realised P&L cannot be recomputed and the gross-vs-net split
  (`database.py:226` vs `state_synchronizer.py:73`) cannot even be measured from the data.

## Running the suite: the sandbox exports a proxy, so localhost HTTP hangs (round 40)

`HTTP_PROXY`/`HTTPS_PROXY` are set to `http://127.0.0.1:8861` in every process this agent starts
(check with `psutil.Process(pid).environ()`). A test that talks to a **local** server through a
proxy-aware client therefore dials the sandbox proxy instead, and hangs. Measured: a full suite
stalled at 36% for 4+ minutes with `utime`/`stime` frozen; `psutil.Process(pid).net_connections()`
showed one socket, `127.0.0.1:<eph> -> 127.0.0.1:8861`, stuck in **CLOSE_WAIT** while the peer
(`sandbox-cli.exe`) sat in `FIN_WAIT_2`. Nothing was wrong with the code.

* **Run the suite with the proxy stripped**, or a hang here reads as a regression:
  `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
   NO_PROXY='*' no_proxy='*' python -m pytest -q -p no:cacheprovider`.
  Same rule as the existing `curl --noproxy '*'` note — the proxy is the trap, not the endpoint.
* **Diagnosing a hang without a stack dump** (no `py-spy` here, and `faulthandler` cannot be
  triggered from outside a running process): sample `cpu_times()` twice — unchanged ticks over 5s
  means blocked, not busy. Then `open_files()` and `net_connections()` name the resource. That
  trio identified this one without any instrumentation.
* **Never run two suites into the same log.** Two runs holding separate offsets on one file
  interleave and overwrite each other's progress lines, so the log shows percentages going
  *backwards* (36% followed by 22%). That cost a whole session of misdiagnosis.
* **A stale run stays alive.** A previous session's `-v` suite was still running 20 minutes later
  and silently competing with the new one. Before starting a suite, list `python` processes whose
  cmdline contains `pytest` and kill the leftovers.

## Starting the platform: with MT5 unreachable, LIVE never serves HTTP (round 40)

Measured on a live `HM_start.py live` run, MT5 down.

* **Do NOT conclude "MT5 is broken" from a missing-DLL listing.** `C:\Program Files\MetaTrader 5\`
  really does list only 9 entries with no `.dll`, and launching `terminal64.exe` directly really
  does exit `0xC0000135` (STATUS_DLL_NOT_FOUND) — and yet the terminal RUNS (measured: pid 23764,
  `terminal_info().connected == True`, 1645 symbols, account XMGlobal-MT5 5 / login 101059540).
  The MetaTrader5 *package* is what starts it, not a bare `Popen`. Concluding the install was dead
  cost a whole cycle of wrong advice. **The only test that matters is
  `mt5.initialize(timeout=5000)` — which is also the fast form:** the default 60s IPC wait is what
  makes a dead terminal look like a hang.
* **LIVE still starts, and looks healthy, but never binds :8501.** Tunnels come up
  (Cloudflare + serveo), orchestrator/watchdog/PositionMonitor all log "started", yet
  `netstat` shows no listener. `py-spy dump --pid <pid>` pinned it: **MainThread** is inside
  `run_web_server -> configure_orchestrator -> from jarvis.api.intelligence_api import INTELLIGENCE`
  (`intelligence_api.py:50`, the heavy `jarvis.backtesting.optimizer` import) while **six** workers
  sit in `mt5.initialize()` (`mt5_client.py:80`, `broker_symbols.py:149`). Each call blocks 60-100s
  **holding the GIL**, so the GIL is held almost continuously and the main thread is starved before
  `ThreadingHTTPServer` is ever constructed. This is the known GIL-starvation trap reproduced in the
  running platform, not in a test — so **"the process is alive and logging" is not evidence the
  server is up. Always check for a listening socket.**
* **Startup pays ~11 minutes before the banner**, because `JarvisOrchestrator.__init__` runs the
  full 6-attempt MT5 retry inline (each attempt ~100s: 1+2+4+8+16s backoff plus the 60s IPC timeout).
  The retry is *not* the 4s `TimeoutGuard` — a native call cannot be cancelled.
* **Market data silently goes synthetic while the banner says LIVE**: `JARVIS_DataFeed` logs
  "MT5 terminal unavailable ... XAUUSD D1 falls back to synthetic bars". Execution mode does not
  gate market data (by design), so LIVE + no terminal = real-looking UI on fabricated bars.
* **Detached launches die with the command here.** `Popen(..., DETACHED_PROCESS |
  CREATE_NEW_PROCESS_GROUP)` still vanished when the bash call returned; so did `./terminal64.exe &`.
  Long-lived processes must be started with the Bash tool's background mode, and for anything
  permanent the user has to run `HM_start.bat live` in their own console.
* `py-spy` is installed in the managed runtime:
  `C:/Users/Itrai/.workbuddy-ai/binaries/python/versions/3.13.12/Scripts/py-spy.exe dump --pid <pid>`.
  It works on Windows here and is the fastest way to prove where a live process is stuck.

## Historical/backtest and history API: readiness is per-process, and an inert filter is stale code (round 40k)

* **`MT5_AVAILABLE` is not readiness — it only means the package imported.** Every `mt5.*` call
  returns `None` with `(-10004, 'No IPC connection')` until `initialize()` has run **in this
  process**. A backtest/historical process never places an order, so nothing ever initialised it,
  and `copy_rates_range` "found no history" — indistinguishable from an empty range, so the run fell
  through to the refusal path. Measured: `copy_rates_from_pos("GOLD.i#")` **before** `initialize()`
  -> `None` / `(-10004, 'No IPC connection')`; **immediately after** -> 5 bars.
  `acquisition.download_range_from_mt5` now dials `broker_symbols.ensure_mt5_terminal()` first.
* **`MT5Client.resolve_symbol_name` returns the input UNCHANGED whenever the client is in paper mode
  or simply not connected** — i.e. in every historical process. Asking for `XAUUSD` returned 0 bars
  (this broker lists it as `GOLD.i#`). Only fill in a resolution the client failed to make; never
  override one it did make.
* **A query-param filter that answers identically for every value is almost always stale code, not a
  bug.** Measured: `/api/history?origin=broker|paper|synthetic|nonsense` all returned the same 50
  rows. Diagnosis is one comparison — file mtime vs the listener's `create_time()`: server started
  14:15:54, `server.py` edited 14:23:38. *The live server does not hot-reload.* After restart:
  `broker` -> 200 rows all broker, `paper`/`synthetic` -> 0, `nonsense` -> 0 (it selects nothing
  rather than widening), and every row carries `origin`.
* **A route that merges broker rows must tag them.** The MT5 deal merge appended rows with no
  `origin` key (16 of 50); a client filtering on `origin` then renders the empty state on success —
  the "frontend reads a field the server never sends" failure again.

## Serving the platform: three separate reasons a read never came back (round 40l)

Measured on `HM_start.py live` with the MT5 terminal down. All three produce the same symptom —
"this endpoint hangs" — and none of them was in the endpoint.

1. **`socket.getfqdn` on the bind path.** `http.server.HTTPServer.server_bind` ends with
   `self.server_name = socket.getfqdn(host)`, a REVERSE DNS lookup. `py-spy` put MainThread in
   `server_bind -> getfqdn (socket.py:811)` and `netstat` showed **no listener at all** — while the
   banner had printed and both tunnels were up, so it looked exactly like the GIL-starvation hang.
   `jarvis/api/server.py` now binds through `_NoReverseDNSHTTPServer`, which skips the lookup
   (`server_name` only fills the `Server:` header). `tests/test_server_bind_no_dns.py` also asserts
   the stock class *does* call it, so the override cannot become dead code silently.
2. **`mt5.initialize()` on a request thread.** `database.sync_mt5_history` called it directly —
   bypassing the one gate — from `fetch_recent_trades`, i.e. from `/api/history`. With nothing to
   attach to, that call tries to LAUNCH a terminal and blocks 60-100s holding the GIL. Two request
   threads were parked in it while `/api/history`, `/api/telemetry_state`, `/api/market-status` and
   `/api/radar` timed out and `/` and `/classic` served in 0.15s.
3. **`get_account_snapshot()` on a request thread.** `/api/telemetry_state` refreshed from the
   broker whenever the cached balance was 0; that connects on first use, and `init_connection`
   retries with 1+2+4+8+16s backoff ≈ 31s. Now a read only refreshes when a connection is ALREADY
   held. Connecting is the background synchroniser's job.

**The `timeout=` argument does not bound the launch path.** Measured: with no terminal running,
`mt5.initialize(timeout=8000)` had still not returned after 100s. A bounded timeout is not a fix for
this; only not making the call is. `broker_symbols.ensure_mt5_terminal` therefore asks the OS
whether a `terminal64.exe`/`terminal.exe` process is running (microseconds, cannot block) and
refuses to call `initialize()` when there is nothing to attach to. `JARVIS_MT5_ALLOW_LAUNCH=1`
restores the old behaviour. The check is skipped for stand-ins (no `__file__`), so a test fake never
depends on the developer's desktop.

**Also: the MT5 terminal is a CHILD of the process that called `initialize()`.** Killing the server
with `psutil.Process(pid).children(recursive=True)` (or `taskkill /T`) takes the terminal with it —
measured: the terminal logged `14:53:49 System: terminal stopped due to system shutdown` exactly when
the platform was killed. After that it could not be restarted from here at all: a direct
`terminal64.exe` launch exits immediately (bash reports 127; the earlier `0xC0000135`
STATUS_DLL_NOT_FOUND) and leaves **no entry in its own log**, and a `Start-Process` launch reports
RUNNING at 10s and is gone by the next tool call. Only a terminal started outside the sandbox stays
up. **Do not kill the platform with `/T` if you want to keep the broker session.**

Result after all three fixes: all 12 public endpoints answer **HTTP 200 in 0.13-0.22s** with no
terminal, and `/api/diagnostics` honestly reports `MT5: RECONNECTING`, `DATA_FEED: SYNTHETIC`.

## Round 40m — traps added

* **"Launch vs no launch" is the wrong question; "where" is the right one.** Gating the terminal
  launch out of *every* path is a regression, not a fix: `HM_start.bat` only runs
  `python HM_start.py live`, and launching `terminal64.exe` used to be a side effect of
  `initialize()`. The result was a platform that booted, served, and reported SYNTHETIC bars with no
  way to trade. **Boot may launch** (blocking 60-100s costs startup time and nothing else); **read
  paths must not** (that is what freezes the accept loop). The flag is
  `ensure_mt5_terminal(allow_launch=...)`, default False, True only in `HM_start.py`.

* **A throttle must not be stamped before the check it protects.** `_LAST_INIT_ATTEMPT` was set on
  the way *in*, so one "no terminal" answer refused every caller for 30s — including a caller with a
  usable injected module, which cannot block and has no reason to be refused. Symptom: 4 failures (3
  `test_position_id_join`, 1 `test_fill_origin`) that each **passed in isolation**, with a different
  set on the next run. Only an actual `initialize()` attempt may spend the window.

* **The user-visible "terminal64 error" is MT5's `-10003`, not a string in this repo.**
  `(-10003, "IPC initialize failed, Pipe server didn't answer in 60 sec")` = the terminal was
  launched but never answered its IPC pipe; `(-10004, 'No IPC connection')` is the follow-up.
  **Read the terminal's own log to tell "failed to start" from "started but never answered":**
  `%APPDATA%\MetaQuotes\Terminal\<hash>\logs\YYYYMMDD.log`. It is UTF-16. A launch that dies before
  MT5 logs anything means the failure is at/below process creation, not in the terminal. The data
  dir being present, writable and lock-free rules storage out.

* **A GUI application cannot be started from a non-interactive session — even with the sandbox
  disabled.** `terminal64.exe` launches, dies, and leaves no Windows Application-error event, so the
  OS never logged a crash: there is simply no desktop to draw on. This is why `MT5: RECONNECTING`
  persists here no matter what; it is not evidence the terminal is broken, and it is **not** something
  the code can work around.

* **When a test file passes alone but not in the suite, suspect shared state before logic** — and
  check it *both* with the platform running and stopped before blaming the platform. Here the failure
  set was byte-identical in both cases, which ruled out the live server in one measurement.

## Round 40n — traps added

* **A latched "is it up" flag cannot answer "is it up now".** `terminal_ready()` latches True on the
  first successful attach and never re-checks — correct for its documented job, but **all three of its
  consumers were asking whether the broker link is up right now**. Once the latch is True and the
  terminal dies, every synthetic frame is read as "the broker does not offer this symbol", so the
  orchestrator refuses to decide for *every* symbol: the radar empties and nothing trades, silently,
  with a log line that blames the symbol. Use `terminal_live()` (latch **and** process still running;
  1.96 ms measured). **Grep every consumer before trusting a cached health flag** — health must be
  measured, not inferred.

* **A weekend is not a stalled feed — and it is not a stable test either.** `classify_bar_freshness`
  answers `MARKET_CLOSED` inside the weekly close, so any test asserting `STALE` on an aged bar passes
  Mon-Fri and fails at the weekend. The cold-history warm-up is gated on STALE, which is how 3 tests
  in `test_market_data_independence` failed every weekend for no reason. Pin the calendar
  (`weekday_market` fixture); keep the weekend rule in its own test.

* **To prove a wall-clock dependency, extract the pre-fix file and force the clock** —
  `git show HEAD:tests/x.py > .scratch/orig/x.py`, then run it with a one-line pytest plugin that
  forces the branch. Arguing about it is slower and less convincing than watching the same 3
  assertions fail on demand.

* **`--junit-xml=` is the only reliable way to read a pytest result here.** The harness truncates the
  stdout tail (a `[safe-delete]` note about pytest's temp dir replaces the summary), so `-rf` and the
  final counts are simply never printed. Write XML and parse it.


## Round 40o — traps added

* **`0.0` is finite, so "is it a number" is not "is it a price".** `trade_guard`'s finiteness loop
  admitted a zero entry, and with the entry at ~0 the inverted-geometry comparisons
  (`stop_loss >= entry_price` for a BUY) are **all False** — so a *negative* stop loss passed the last
  gate before the broker. A zero price is not an edge case in this system: it is exactly what an empty
  primary frame is (`market_context:78-80` → `current_price = bid = 0.0`, `ask = spread × pip`).
  **A price predicate must be `isfinite(v) and v > 0`**, and it must be ONE definition shared by the
  producers and the gate (`schemas.is_observed_price`), or they drift.

* **A plausible-looking price is more dangerous than a missing one.** Minting an entry from an
  unobserved context produced `entry=0.02 / stop=-0.04` for BTCUSD — and the sizer, reading a risk
  distance of 0.06 against a 65 000 instrument, returned **100 lots: 6.5M USD of exposure for a 50 USD
  risk budget**. The 100-lot ceiling, not the geometry check, is what limited it. **Refuse to mint;
  do not fabricate a number that flows into sizing.** Order of magnitude checks: if the "price" of
  BTCUSD is 0.02, nothing downstream notices.

* **An empty frame propagates as `0.0`, never as `None`.** Every consumer of `context.current_price`
  sees a float, so nothing raises and no `except` runs. Guard on the value, not on a missing key.

* **A refusal must keep the shape its callers index.** `calculate_levels` returns a dict both callers
  read by key (`levels["entry_price"]`), so the refusal is a HOLD-shaped dict with `data_unavailable:
  True` rather than `None`. Changing the arity/shape of a refusal breaks callers exactly as hard as
  returning garbage.

* **Fixing the producer is not enough if the *direction* is decided elsewhere.** `decision_action`
  becomes EXECUTE on `gate_passed and bias in (BUY, SELL)` — the bias comes from the decision engine,
  not the levels engine, so a BUY/SELL verdict beside a zero entry would have been marked executable.
  **Refuse the direction in the same breath as the prices.**

* **A fallback anchor launders into an unlabelled series.** `fetch_candles` builds a full OHLCV series
  anchored to a quote that may be `is_fallback: True`, and the last bar's comment calls itself
  "genuine live TradingView OHLCV" while `fetch_real_candles` logs it as "Live TradingView candles".
  Labelling the *quote* does nothing for the *series*. Return `None` so the tier hierarchy falls
  through to a tier that labels itself.

* **Mutation-proof a new guard by neutering the predicate it calls.** Patching
  `is_observed_price` back to finiteness-only (and to always-True for the producers) turns 26 of the
  new tests red. If a test stays green under the mutation, it is pinning something else — e.g.
  `test_a_zero_take_profit_is_rejected` is satisfied by the geometry check, not the new positivity
  branch, so it is a behaviour pin and not proof of the fix.

## Round 40p — traps added

* **A label must describe the thing it names.** Both engines set `_last_data_source = "live"` when the
  **anchor price** came from a live quote, while every bar was generated from
  `np.random.RandomState(stable_seed(f"{symbol}_{tf}_{hour}"))`. A fully generated random walk was
  therefore published to the UI as `data_source: "live"` — and `dashboard.js` renders that as a live
  feed. **Ask which object a label is attached to, not just whether it is computed correctly.**
  Reproducing the series from its own seed (max deviation `0.000020` = the 2dp rounding) is how you
  *prove* a series was generated rather than observed.

* **Provenance must travel WITH the data, not live on a singleton.** `_last_data_source` is
  per-**instance** state on a module-level singleton (`STOCK_ENGINE`, `INDIA_ENGINE`) driven by a
  `ThreadPoolExecutor(max_workers=16)` (`stock_service.py:57`), so another symbol can overwrite it
  between the call and the read. A per-instance attribute is only safe if the instance is per-request.

* **A `list` subclass is the low-blast-radius way to attach metadata to a sequence.** `CandleSeries(list)`
  adds `source` / `anchor_source` / `is_synthetic` while keeping `pd.DataFrame()`, `len()`, iteration,
  slicing, `+` and `json.dumps` working unchanged — each pinned by its own test, because "it is still a
  list" is exactly the assumption a future refactor breaks.

* **`mt5.initialize()` on a request thread holds the GIL forever with no terminal — and no Python-side
  timeout can rescue it.** `TimeoutGuard` works by starting a thread, and no thread can start while the
  GIL is held; `faulthandler` cannot even dump. So the **only bounded proof is from outside the
  process**: run the call in a child, watch it stay alive past a deadline, kill it. Pre-fix: `child
  STILL RUNNING after 25s -- killed`; post-fix: `child exited after 4.1s with rc=0`. **Only not making
  the call is a fix** — `initialize()` takes no `timeout` in MetaTrader5 5.0.6180.

* **Pin the mechanism, not the wall clock.** `test_mt5_read_path_is_bounded` asserts *"`initialize()` was
  never called"*, not "returns within N seconds". A timing assertion **hangs the suite** when the guard
  regresses, and fails for the wrong reason; a mechanism assertion fails immediately and exactly. A
  probe that needs a real terminal to be *absent* should `pytest.skip` when one is running, or it is
  simply wrong on a live box.

* **When a mutation does not kill a test, decide which is wrong: the test or the mutation.** My first
  D5 mutation killed 8 of 10 expected tests. The two `TestProviderProvenance` tests survived because the
  mutation patched `se`/`ie`'s constants while the provider imports its **own** copy — a **gap in the
  mutation, not a weak test**. Adding a provider-level neuter turned them red (8 → 10). Never accept
  "it stayed green" as proof a test is worthless without checking that the mutation actually reached the
  code path.

* **A green test under mutation is not automatically a behaviour pin — classify it.** The 20 survivors
  are: the `CandleSeries` type contract (9), "real bars are still `live`" + series shape (2), the
  delegate consistency check (1), the MT5 short-circuit/unavailable/raising paths (6), a skipped probe
  (1), a parametrisation (1). One of them — `test_the_delegate_reports_the_same_provenance` — is a
  *consistency* pin and **not** a fix proof: under mutation both sides become `"live"` together and it
  still passes. Say so explicitly rather than counting it as coverage.

* **A repro that says "hypothesis disproved" may just be a flawed test.** My first D5 repro compared
  `log(close / last_close)` shapes across a 40% price move and printed `identical : False`. The
  hypothesis was right; the *metric* was wrong — 2dp rounding is relatively larger at the earlier bars.
  Rebuilt on the generator's own return path, which is the measurement that actually holds.

## Running the platform (round 40p — the detail moved out of MEMORY.md)

* **Routes:** `/` and `/dashboard` → `dashboard.html` (primary). `/classic` → `index.html` (the old
  terminal). `/stocks`, `/india`, `/options`, `/console`. `terminal.css` loads **only** in
  `index.html`; `dashboard.html` uses `theme_terminal.css`. **`tools/verify_ui_layout.js` does not
  cover `/classic`**, so a layout regression there is invisible to the harness that exists to catch
  layout regressions.
* **`HM_dashboard.bat` is UI + REST API only** — `start_server(mt5_client=None, orchestrator=None)`
  has no broker client at all. So `auto-selection` → **503** and `MT5` → **DISCONNECTED** are the
  *expected* answers from that launcher, not a fault. Before calling the data path broken, read
  `psutil.Process(pid).cmdline()` and confirm which launcher is actually serving.
* **Never leave the platform alive only as a session background task.** A background task dies with
  the session and leaves a half-served platform behind; point the user at `HM_dashboard.bat` (or
  `HM_start.py`) so the process has an owner.
* **Some paths are readable but NOT writable** — `HM_dashboard.bat`, `HM_start.py`,
  `jarvis/intelligence/decision_engine.py`. Write through a hardlink alias in `.scratch/_restore/`;
  `open(alias, 'w')` **truncates the shared object** (see § Data integrity). `HM_dashboard.bat` was
  emptied exactly this way while probing.

## Tool harness: one edit per file per message (round 40p)

* **Two `Edit` calls against the SAME file in one message: the FIRST one is silently lost.** Both
  report `Successfully edited`, and the second one's `old_string` still matches because it was read
  from the same pre-edit snapshot — so the first change is overwritten without any error. Observed
  three times in one session while trimming `MEMORY.md` (a `.git/` reflow and a proxy-wording trim
  both vanished; the paired second edit landed each time). **Symptom: you re-measure the file and the
  byte count has not moved.** Always re-read or re-measure after a multi-edit message, and when the
  edits are on one file, issue them one per message.

## Round 40q — traps added

* **A label derived at OPEN from a value that does not exist yet always takes the default.**
  `record_trade` wrote
  `trade_data.get("triple_barrier_label", 1 if pnl > 0 else (-1 if pnl < 0 else 0))` — but a trade
  being *opened* has no `pnl`, so the fallback **always evaluated to 0**. It minted "the vertical
  barrier was hit" for every trade before it had been closed. **The default of a `.get()` is the
  value that gets written whenever the caller is silent, so the default must be a value that is true
  when the caller is silent** — here, "unlabelled".

* **A column written on one path and not the other is silently frozen.** `update_closed_trade`
  updated `exit_price / pnl / is_win / mfe / mae` and **never touched `triple_barrier_label`**, so
  every row kept whatever it was born with. `is_win` *was* repaired (4 rows at 1), which is how you
  can tell the close path runs at all. **When a row is created and later closed, list every outcome
  column and check both ends** — the asymmetry is invisible from either end alone.

* **A float artifact can decide a comparison — use a relative tolerance below one tick.** Live row
  `938435830` stored `sl = 111.29999999999998` and filled at `111.30`, so `exit <= sl` answered
  "**not reached**" for a trade that *was* stopped out: the label would have been set by float noise
  rather than by the market. 1e-9 relative is safe because it is orders of magnitude below one tick
  (BTCUSD ~1.2e-7 relative, a EURUSD pip ~8.7e-6), so it cannot swallow a genuine near-miss. **Any
  "did the price reach the level" test on stored floats needs this.**

* **"Both barriers hit" is contradictory geometry, not a gap.** For a well-formed BUY,
  `exit >= tp` and `exit <= sl` cannot both hold: a gap fills *on or beyond* whichever barrier was
  touched, which classifies cleanly. So the condition can only mean `tp <= sl` — a malformed row —
  and the honest answer is "cannot label", not a guessed direction.

* **"Not measured" must not be stored as a value that means something else.** `mfe`/`mae` are passed
  as a literal `0.0`, and `0.0` is also a *legitimate* excursion (a trade that never went
  favourable). The column therefore cannot distinguish "we did not measure" from "we measured zero" —
  the same shape as C2 (`0.0` is finite), D5 (a generated series labelled `live`) and D10 (0 =
  "vertical barrier"). **Before fixing a zero, ask which of the two it is.**

* **Check that your test's premise is geometrically possible before writing it.** I asserted
  "a gap through both barriers labels adverse" with `exit=200, sl=99, tp=101` for a BUY — but that
  only satisfies `exit >= tp`, so the function correctly returned `+1`. The premise was impossible,
  not the code wrong.

* **A single `sl`/`tp` pair does not serve both sides.** A BUY needs `tp > entry > sl`; a SELL needs
  `sl > entry > tp`. A parametrised case-insensitivity test that reuses one pair across `BUY` and
  `SELL` gets "both barriers hit" → `None` for one of them. Carry per-side geometry in the params.

* **A store's on-disk `user_version` reflects when it was LAST OPENED, not whether the code stamps
  it.** `data/jarvis_drawdown_state_paper.db` read `uv=0` while its sibling read `uv=1`, which looks
  exactly like "the mode-scoped store was missed by the stamping code". **It was not** — constructing
  a `DrawdownGuard` on a paper-scoped path stamps it `1` on the spot; the on-disk file was simply last
  written before the stamping landed (mtime 03:42 vs 14:09 for the live one). I recorded this as a
  code gap from file state alone and had to retract it. **Auditing "is every store stamped?" by
  reading files conflates "never reopened since the change" with "the code does not stamp it" — the
  test is to open a fresh store through the real code path and check the version afterwards.** Still
  enumerate the `_paper` variants, since they are separate files with separate lifetimes.

* **`trade_records` is not in `jarvis_history.db`.** `data/jarvis_history.db` holds only
  `executed_trades`; the learning table lives in `data/jarvis_trade_memory.db`. Querying the wrong
  file returns "no such table" rather than an empty result, so a naive probe can look like missing
  data.

## Environment (round 40q — the detail moved out of MEMORY.md)

* **Writes outside the project dir are refused.** Keep scratch under `.scratch/` (gitignored).
* **Bash, not the other Windows shell.** `taskkill` needs `/PID` **with** `MSYS_NO_PATHCONV=1` — with
  that env var set, `//PID` reaches the tool literally and is rejected as an invalid option.
* **`rm -rf X && cmd` swallows stdout** — run the `rm` as its own command.
* **A script run *by path* puts its own dir on `sys.path`**, so a helper in `.scratch/` cannot import
  the package unless you add the repo root (or set `PYTHONPATH`). The same script run from the repo
  root by relative path behaves differently.
* **Python 3.13.12 managed** at `...binaries/python/versions/3.13.12/python.exe`; `py-spy` at
  `...versions/3.13.12/Scripts/py-spy.exe`.
* **The server binds 127.0.0.1 only**, so a probe from another host will not reach it.
* **A full-suite run can exit non-zero with EVERY test passing.** Measured: `2771 passed / 0 failed`
  and still `exit=1`, with no pytest summary line written. The cause is the sandbox's bulk-delete
  guard: pytest rotates its previous `tmp_path` root into `pytest-of-Itrai/garbage-<uuid>` under the
  OS temp dir and then deletes it, and once that directory holds more than **50** entries the guard
  refuses (`[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":54,"threshold":50}`) and the
  run exits 1. The garbage is not yours — it accumulates from every `tmp_path`-using test file
  (`tests/test_drawdown_guard.py` contributes ~19 dirs per run). **Never read a bare `exit=1` as a
  test failure — check the junit XML `failures`/`errors` first.**

  **The `--basetemp` fix must be UNIQUE per run.** `--basetemp=.scratch/ptmp` gave `exit=0` once and
  was recorded as the fix — then, two runs later, the SAME guard fired again with 34 setup errors:
  pytest **removes** an existing basetemp at session start, so a fixed one simply accumulates inside
  the project and re-trips once it passes 50 entries. Use `--basetemp=.scratch/ptmp-$TS`. A "verified
  fix" that only moves the accumulation is not a fix — re-run it a second time before believing it.

## Round 40r — traps added

* **An unnamed positional `INSERT ... VALUES (?, … ×N)` pins the write to this version's exact
  column count.** `record_trade` supplied 22 bare placeholders, so a 23-column file — exactly what a
  file written by **newer** code looks like — failed with `table trade_records has 23 columns but 22
  values were supplied`. The store could correctly decide to open a newer file and still be unable
  to write to it. **Name the columns in the INSERT**: it then tolerates extra columns (they take
  their defaults) and is immune to column reordering.

* **An "add whatever is missing" sweep cannot refuse, and it runs even when the version check
  refuses.** `if 'col' not in columns: ALTER TABLE ADD COLUMN` reads `PRAGMA table_info` and ignores
  `user_version` completely. Handed a newer file that no longer carries a column, it **re-adds it**,
  silently undoing the newer schema — while `ensure_version` logs "written by newer code" about a
  change the sweep has already made. The two mechanisms disagree and the sweep wins, because it runs
  first. Replacing it with a numbered `migrate()` step is what makes the refusal real.

* **When most of a new test file survives the mutation, say what the fix actually bought.** Of 8
  migration tests only **2** went red. The other 6 pass under both the sweep and `migrate()`, because
  the sweep happens to be correct for them: it adds missing columns, is idempotent on re-open, and
  refuses to stamp a number down. So the honest measure of the change is **the refusal** (and the
  write path), not "8 tests of coverage". Count the red, name the survivors, and do not let a green
  survivor inflate the claim.

* **"Refusing to migrate is not refusing to operate" is worth asserting.** It is easy to make a
  version guard so strict that the store becomes unusable for files it correctly decided to open, and
  no unit test of the guard itself will notice. Assert both halves: the version is left alone **and**
  a normal write still succeeds.

---

### 40s — When a change is invisible today, mutate the mistake it guards, not the code it replaced

Moving `circuit_breaker` / `drawdown` / `metadata_db` from `ensure_version` to `migrate()` changed
**nothing observable**: both stamp 1 on a fresh file, both refuse a newer one. A mutation plugin that
simply reverts the call therefore turns **zero** tests red — and reporting "0 red" would have read as
"the change is worthless" when the real answer is "the tests cannot see it yet".

The change exists for a scenario that has not happened: `SCHEMA_VERSION` bumped to 2 with no step
registered. So the mutation has to *create that scenario*. Two plugins, one run each:

* `bump_store_version.py` — bump only. Fixed code: **12 red** (refuses).
* `bump_store_version.py` + `revert_store_migration.py` — bump and revert. Old code: **3 red** (stamps).

The 12-vs-3 gap is the evidence. Neither number alone means anything.

**Rule: a structural change with no current behavioural difference cannot be mutation-proved by
reverting it.** Identify the future failure it prevents, simulate *that*, and run the simulation
against both the old and the new mechanism. Report the difference between the two runs, not the count
from either.

Corollary for the test suite: keep one test that pins the *contrast* by measurement — here
`test_ensure_version_would_have_stamped_it` asserts the old helper really does stamp the unbumpable
version. If that assertion ever fails, the new machinery has stopped buying anything and the change
should be reverted rather than kept on inertia.

---

### 40t — Look for the deletion before fixing the literal

`mfe=0.0, mae=0.0` at the close call site was the *symptom* of D19. The cause was three functions away:
`PositionMonitorEngine` tracked the favourable extreme per ticket and then, in the pruning step of
`_run_monitor_tick`, dropped it as soon as the ticket left `active_tickets`. By close time the data was
gone and the handler had nothing to write, so it invented a zero.

Fixing only the literal would have produced a *worse* store: the path would still have been destroyed
and every row would now say NULL, which is honest but useless — the measurement was available and was
thrown away.

**Rule: when a value is hardcoded at a write site, find out whether something upstream discarded the
real one.** Grep for the per-entity state that would have produced it and check whether it is cleaned up
before the writer runs. Pruning code is the usual culprit; it is written for memory hygiene and nobody
revisits it when a new consumer appears.

### 40t — An insertion between `@staticmethod` and its `def` steals the decorator

Adding a method immediately *above* an existing

```python
    @staticmethod
    def _coerce_positive_float(value, default=None): ...
```

by matching on the `def` line lands the new code between the decorator and the function, so
`@staticmethod` re-parents onto the **new** first method. Its own `self` then becomes a required
positional parameter, and a call that visibly passes every argument fails with:

    TypeError: _remember_closed_excursions() missing 1 required positional argument: 'ticket'

The message points at the wrong thing — it looks like too few arguments were passed to a function that
obviously takes one. **Check the decorator pairing above and below any insertion point.** If editing near
a decorated function, match the decorator line too, or insert after the whole pair.

---

### 40u — Measure the payload's shape before choosing how to shrink it

I assumed the 63 KB telemetry snapshot was big because of a few fat blobs, and designed a generic
"elide any single value over N bytes" rule. Prototyped against the real payload, it saved almost
nothing: **94% of the original at N=256, 84% at N=64**.

The reason is the distribution, not the threshold. A radar row is 2.6 KB spread across **~34 fields of
~77 bytes each**. No value dominates, so no value-level rule can help. What actually worked was
dropping *fields* (an explainability denylist): 63,276 → 21,019 bytes.

**Rule: before designing a reduction, print the size distribution — top-level section sizes, then the
per-key sizes of one representative element.** If the largest single field is a small fraction of the
total, every value-level approach is doomed and you need a field-level one. Five minutes of measurement
replaced a change that would have shipped as a fix while saving 6%.

Related: a **denylist** lets new small fields through automatically, so the digest cannot silently fall
behind the schema — but it cannot catch a new *heavy* field. Pair it with a byte-budget assertion in a
test, which is what turns that blind spot into a failing test instead of a surprise.

---

### 40v — A timeout guard bounds the CALLER, not the resource the caller needed

`TimeoutGuard.run_sync(_send, timeout_sec=5.0)` returns a default after 5s, so it looks like every
broker call is bounded. It is not. The worker thread it abandons is still inside a native
MetaTrader5 call **still holding `MT5Client._shared_lock`**, and Python cannot interrupt a thread
blocked in C. Every later call then blocks on `acquire()` — measured: **5/5 later workers blocked to
their full timeout**. `TimeoutGuard` even counts these as "stuck workers" and replaces the pool, which
is why the guard's own health looked like it was healing while the broker was dead.

**Rule: a timeout wrapper bounds the caller's WAIT, never the shared resource the work held.** Ask what
the abandoned worker still owns. If it holds a lock, a connection, or a slot, that resource is leaked
for as long as the native call lasts — which may be forever.

Corollary for the fix: you usually cannot reclaim the resource (you cannot kill the thread). So fix the
*visibility*, not the recovery — bound how long anyone else waits for it, and record who is holding it
so health can name it.

### 40v — Check whether the audit's own status table is stale

Before starting "the next open item", verify the item is actually open. The P0 table listed five
findings as unfixed (C1, D4, D2, A3, P2) that the M1 section below recorded as **6 of 6 done** with
pre/post measurements. The table simply had not been updated when each landed.

A stale status table is a real defect in an audit: it sends the next reader at work that is finished,
and hides what is genuinely left. **When a milestone table says N of N done, reconcile it against the
summary tables above before trusting either.**

---

### 40w — A test that drives its own helper proves nothing about the code it names

Writing `TestTheForecastSurvivesTheClose`, I wrote a `_close()` helper that mirrored the production
UPDATE and then asserted the close does not rewrite the forecast. It passed immediately — and proved
nothing, because the helper was written to match the fix. The production SQL lives inside
`sync_mt5_history`, which needs a broker, so nothing in that test touches it.

The tell: **the test went green on the first run and could not go red under any runtime mutation.**

**Rule: if your test's setup was written from the same understanding as the fix, it verifies your
understanding, not the code.** Ask "what would make this fail?" If the only answer is "editing my own
helper", it is a statement of intent, not a test.

Two acceptable responses, both used here:
* Pin the untestable code at the **source** level and say so — brittle under reformatting, but better
  than no evidence. Put it in its own class so the weaker evidence is visibly weaker.
* Rename the helper-driven test to something that admits it (`TestTheCloseContract`) and document in
  the docstring that it is not counted as coverage of the write sites.

Report the mutation count for the part that is genuinely behavioural (5 of 12 here) and state the
limitation for the rest. Do not quote 12 as if all 12 discriminate the fix.

### 40w — When the repair is destructive, ship the tool read-only

The 112 overwritten forecasts cannot be recovered — they were overwritten in place and stored nowhere
else. Nulling them is the honest repair (a NULL is skippable; a copied outcome is something a
calibration routine will silently learn from), but it rewrites live trade history.

`tools/repair_forecast_column.py` reports by default and only writes with `--apply`. Same shape as the
root `jarvis_history.db` question: offer the repair, do not perform it.

### 40x — A mutation must reproduce the OLD BEHAVIOUR, not merely break

Reverting `fetch_trade` by rewriting its WHERE clause to `WHERE ticket = -1` while still supplying the
parameter raised `sqlite3.ProgrammingError` (statement uses 0 bindings, 1 supplied). The orchestrator's
`except Exception` around the journal read swallowed it into `pending = None` — which is the failure
mode of a *different* mutation. Two mutations collapsed into one and produced two bogus reds
(`test_an_unknown_ticket_is_none` failed for a reason that had nothing to do with the fix).

**Rule: a mutation is only evidence if it restores what the code used to do.** "Makes it throw" is not
"makes it wrong" — an exception that a broad `except` swallows can masquerade as several different
regressions at once. Keep the shape legal (`WHERE ticket = ? AND 1 = 0`) and re-run the suite before
trusting the count.

### 40x2 — `float(x or 1.0)`: None and an honest 0.0 both become 1.0

Found in three places at once while fixing AI6:

* `strategy_bandit.py` — `r_mult = max(0.1, min(5.0, float(r_multiple or 1.0)))`
* `online_ml_predictor.py` — `return_weight = max(0.5, min(3.0, abs(float(r_multiple or 1.0))))`
* `ensemble_bandit.py` — `max(0.5, r_multiple)` → `TypeError` on None (no `or` guard at all)

So an explicit `None` ("not measured") and a real `0.0` ("measured, zero") were both banked as +1R.
**Grep `or 1.0` / `or 0.0` on any numeric path before claiming a value is optional** — the `or` idiom
silently deletes the distinction the codebase is trying to establish. A default belongs in the
signature (`r_multiple: Optional[float] = None`), not in a coercion.

### 40x3 — A missing stop is not a zero stop

`risk_dist = abs(entry - sl)` with `sl = 0.0` yields `entry` — on EURUSD that reads as 11,000 pips of
risk, and the resulting R is a tiny non-zero number that looks measured. `record_trade` defaults `sl`
to `0.0`, so an unstored stop is indistinguishable from a real one unless you test for it.

Same family as `_is_finite(0.0)` being True: **a sentinel that is a legal value cannot be detected by
arithmetic.** Test the field, not the result.

### 40x4 — Withhold a recorded quantity, default a hyperparameter

An unknown R reaches two consumers and must be treated differently in each:

* Bandit `rewards` is a **recorded quantity** denominated in R → an unknown R must leave it alone. But
  the win/loss IS measured, so still record the trade (`pulls`, Beta).
* `update_online`'s `return_weight` is a **hyperparameter of the update** — nothing later reads it as
  "this trade made 1R", and the label being learned (`is_win`) is measured regardless → run the update
  and **omit** the argument, letting the declared default apply.

I first skipped the ML update entirely; the suite caught it
(`test_d1_online_ml_and_trade_memory_learning_loop`, 197 != 198). **Discarding a real labelled sample
to avoid guessing a step size is the wrong trade** — and note the failing fixture was unrealistic (it
set only `features`, no geometry), so the temptation was to "fix" the test. Fix the fix instead; the
production path always stores `entry`/`sl`/`risk_dist`.

### 40y — Hermeticity needs BOTH ends: construction and the run

`offline_mode()` around a backtest's `run_backtest()` is **too late**. Every stateful component on
the decision path (`OnlineMLPredictor`, `MetaLabeler`, `ConfidenceCalibrationEngine`,
`SelfLearningEngine`, `RealtimeOptimizer`, and `StrategyBandit` via `StrategySelector`) loads
**eagerly in its own constructor** — measured: the predictor woke with 199 live training steps and
weights `[0.363, ...]` where the neutral prior is `[0.35, ...]` / 10 steps.

And wrapping only the constructor is **not enough**: `SelfLearningEngine.get_regime_multiplier` and
`get_pattern_win_rate_and_ev` consult `is_offline()` at *call* time and hit the live journal
otherwise (0.9 vs 1.0; 25 samples / 0.39 win rate vs 0 / 0.50).

**Rule: a hermeticity fix must cover where state is LOADED and where it is READ.** Check both.

### 40y2 — Do not assert "the value looks neutral" when the state file is untracked

`jarvis_online_ml_weights.json` is **untracked**, so a fresh clone has none: a test asserting
"the backtest's ML weights are the neutral prior" passes with the fix removed. It is a test that
cannot go red.

Fix: spy on the mechanism, not the artefact. The components do `from jarvis.config.runtime import
is_offline` *inside* their methods, so `monkeypatch.setattr(runtime, "is_offline", spy)` is picked up
at call time — then assert the spy saw `True`. Works on a machine with no learned state at all.

**Rule: before relying on a fixture that already exists, check whether it is committed.** A test
whose precondition is "someone has been trading" is not a test.

### 40y3 — `is_offline()` is read at CALL time, not construction time

I reported `SelfLearningEngine` as ignoring the flag, because my probe built the engine inside
`offline_mode()` and then called the getters *outside* it. The guards were correct; my harness was
wrong. When a component defers its guard check into the method, the probe must be inside the context
too — and this cuts the other way as well: it is exactly why the run has to be wrapped (40y).

### 40y4 — Grep for the CLASS, not the attribute name

Looking for a bandit on the backtest path, I grepped `self.bandit` / `self.strategy_bandit` and
found nothing. `DecisionEngine` actually holds `self.ensemble_bandit = EnsembleStrategyBandit()`
(no disk I/O) **and** builds `StrategySelector()`, which owns the persisting `StrategyBandit`.

**To find a dependency, grep the class name** (`StrategyBandit`), not the attribute you expect
(`self.bandit`). Attribute names are chosen by the caller and will not match your guess.

### 40y5 — Some defects are real but not mutation-visible

A leaky backtest and a hermetic one produce **identical** results when run twice in a row, because
both read the same static file. The damage is that today's backtest disagrees with next month's —
which no unit test can show.

**Rule: when a mutation cannot turn a test red, say so** rather than inflating the count. Pin what
is provable (the flag was engaged), document what is not (temporal reproducibility), and do not
claim the fix is proven by a test that would pass without it.

### 40z — A test that hardcodes a version number goes vacuous when the number moves

`tests/test_trade_memory_migration.py` used a literal `version=2` to mean "written by newer code".
Bumping `SCHEMA_VERSION` from 1 to 2 (AI10) turned those files into *current* files — so two
refusal tests passed while testing nothing at all. They were green throughout.

**Rule: derive relative positions, never write them.** `NEWER_VERSION = SCHEMA_VERSION + 1`. This
applies to any sequence — schema versions, migration indices, "the next id", "one more than the
limit". A literal that encodes a *relationship* silently becomes wrong the moment either side moves,
and it fails silently because the code still runs.

### 40z2 — A calibration fit must consume the forecast, never the pipeline's own output

`update_calibration_from_history` binned on `model_confidence`, which `decision_engine` sets to
`calibrated_win_p` — and that value is not even `calibrate_probability()`'s immediate output, but the
downstream composite after the ML blend and a dozen boosts/penalties. The curve decided the stored
value; the stored value picked the bin; the bin refit the curve.

Persisting the pre-calibration forecast (`raw_win_prob`) is what breaks the loop. **Rows with no
forecast must be SKIPPED, not defaulted** — a default manufactures a forecast the curve then learns
from (same shape as AI5's `ai_score = 85.0`).

### 40z3 — Three numbers that make a small-sample fit meaningless

* **n=2 per bin.** The observed rate can only be 0.0, 0.5 or 1.0, and the old rule moved the bin 60%
  of the way there. Measured: bins of 3–5 (SE 0.18–0.27) collapsed the whole curve — 0.59 → 0.236,
  0.86 → 0.464 — so a raw 0.60 mapped to 0.325 and the 55% gate became unreachable.
* **No shrinkage.** Replace a fixed `alpha` toward the observed rate with a Beta-style prior:
  `(n·observed + k·prior) / (n + k)`. At n == k a bin that just clears the bar moves halfway.
* **No monotonicity constraint.** Without one the refit inverted the curve: 0.75 → 0.496 but
  0.95 → 0.464, so a MORE confident forecast scored LOWER — the gate then rewards the worse trade.
  Enforce non-decreasing after every update; a reliability curve that is not monotonic is not a
  reliability curve.

### 40z4 — Reading live data can write to it

Opening the live journal to *measure* the calibration defect ran migration 2 on it. Additive and
correct (36 rows intact, one nullable column), but it was a write to a live file I had not intended
to make.

**Rule: a schema migration fires on first open.** If a repro must touch the real store, copy it
first (`cp` to `.scratch/`), and remember that `TradeMemory` resolves *relative* paths against
`DATA_DIR`, so pass an absolute one.

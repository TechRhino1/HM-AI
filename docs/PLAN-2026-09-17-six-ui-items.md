# Plan — six UI/trading enhancements (2026-09-17)

Codebase study completed before this plan. Every claim below was verified directly
(file:line), not inferred. **No code has been written yet** — implementing awaits your
confirmation.

Two of your premises turned out to be slightly different from reality. Flagged up front
because they change what "restore" means.

---

## Premise corrections (please read first)

**1. The limit-order feature was never deleted — it was never wired.**
`mt5_client.place_pending_order` (`jarvis/execution/mt5_client.py:550`), `get_pending_orders`
(`:643`) and `cancel_pending_order` (`:670`) all exist and are tested
(`tests/test_pending_orders.py`). They were added in `42a65dc` and have exactly one caller:
the autonomous AI path (`jarvis/execution/execution_engine.py:59`). `git log -S
"place_pending_order" -- jarvis/api/server.py` is **empty** — no API route ever called it, and
`git log -S "BUY_LIMIT" -- jarvis/ui` is empty — no UI ever referenced it. So this is
"finish the wiring", not "restore deleted code". Order *modify* has never existed at any layer
(`TRADE_ACTION_MODIFY` appears nowhere in the repo's history).

**2. The original trade history was never removed and never had filters.**
The 10-column table you remember is **still present and working** in `index.html:583-606`
(the `/classic` page), rendered by `terminal.js:1030-1078`. What is broken is that the
*dashboard* history panel (`dashboard.html:575-594`) is dead — `dashboard.js` never calls
`/api/history`, so `state.history` (declared at `dashboard.js:64`) is never assigned.
Separately, the *console* history is broken by a response-shape mismatch. And a search of all
git history for filter controls finds **none** — so "original filters" don't exist; they must
be designed new.

---

## Item 1 — Limit orders (place / modify / cancel / status)

### Current state
- Present: MT5 primitives for place/get/cancel; `GET /api/pending_orders`
  (`server.py:589`); `POST /api/action/cancel_pending_order` (`server.py:835`); pending-order
  tables in `index.html:560-576` (`terminal.js:1081-1137`) and `dashboard.js:757-810`.
- Missing: (a) any route to **place** a pending order; (b) **modify** at every layer;
  (c) any UI to choose order type or enter a price — the desk panel (`index.html:712-760`)
  hardcodes BUY/SELL market buttons.

### Logic plan
1. `jarvis/execution/mt5_client.py` — add `modify_pending_order(ticket, price=None, sl=None,
   tp=None)` using `TRADE_ACTION_MODIFY` (9), mirroring `cancel_pending_order` (`:670-685`).
   Must also update paper mode (where `place_pending_order` returns `PLACED`) so it is
   testable without a live account.
2. `jarvis/api/server.py` — add `POST /api/action/place_pending_order` and
   `POST /api/action/modify_pending_order` near the existing handler at `:842`. Validate
   `order_type ∈ {BUY_LIMIT, SELL_LIMIT, BUY_STOP, SELL_STOP}`, `price > 0`, volume > 0;
   reject a BUY_LIMIT above market / SELL_LIMIT below market with a clear message rather
   than letting MT5 return a bare retcode. Return `{ok, ticket, retcode, message}`.
3. UI — add an order-type selector + limit-price field to the desk panel
   (`index.html:712-760`) and the dashboard ticket panel; wire `executePendingOrder()` /
   `modifyPendingOrder()` in `terminal.js` and `dashboard.js`.
4. Status display — **no change needed**; reuse the existing pending tables.

### Regression risk
- **Deliberately additive.** The existing `manual_trade` handler (`server.py:842`) forces
  `action ∈ {BUY, SELL}`; I will add new routes rather than modify it, so market orders
  cannot be affected.
- `TRADE_ACTION_MODIFY` is brand-new code that touches real money → paper-mode tests first,
  mirroring `tests/test_pending_orders.py`.

---

## Item 2 — Trade history (original columns + filters)

### Current state
- `GET /api/history` (`server.py:538-583`) is live: merges SQLite `executed_trades`
  (23 columns) with MT5 `history_deals_get`, returns a **bare JSON array**. It ignores the
  `?limit=` the UI sends (hardcoded `limit=50` at `:541`) and supports no filters.
- `/classic` (`index.html:583-606`) — **the original 10 columns, working**: Ticket, Symbol,
  Action, Executor, Lots, Entry, SL, TP, Realized PnL, Execution Time.
- Dashboard panel — dead (no fetch). Console page — broken: `console.js:679` expects
  `data.trades || data.history` but gets a bare array, and reads fields the response does not
  contain (`lots`, `pnl`, `regime`, `strategy`, `model_confidence`, `close_time`).

### Logic plan
1. **Preserve the array response** — `terminal.js:876` depends on it and currently works.
   Fix `console.js:679` to accept an array instead of changing the server contract. Zero risk
   to the working page.
2. Map console columns onto fields that actually exist (`volume`→Lots,
   `realized_pnl`/`profit`→PnL). `regime`/`strategy`/`model_confidence` live in a *different*
   table (`jarvis_trade_memory.db → trade_records`); either merge server-side by ticket or
   drop those three columns. **Recommend dropping** — cheaper and the data is sparse.
3. Make `/api/history` honour `?symbol=&side=&from=&to=&limit=` server-side. Defaults
   reproduce today's behaviour exactly, so it is backward compatible.
4. Wire the dashboard: add `loadHistory()` → `/api/history` → `state.history`, and upgrade the
   panel to the **original 10 columns** from `index.html:589-598` (needs horizontal scroll on
   narrow screens).
5. Add filter controls (symbol, side, date range, result) — new, since none ever existed.

### Regression risk
- Low. Response shape preserved for `terminal.js`; filters are additive with
  current-behaviour defaults; `console.js` change is local to that page.

---

## Item 3 — Chat with the bot

### Current state
- **A complete floating chat UI already exists** — `index.html:907-934` (window, message
  list, input, FAB), `terminal.js:2346 sendChatMessage`, `:2292` toggle, `:2319` clear,
  `:2324` export, keyboard shortcut "C".
- The brain is `JarvisCopilot` (`jarvis/api/copilot.py`) — a **rule-based keyword matcher**
  (why/analyze/risk/setup intents, `copilot.py:13-97`). Verified: **zero** LLM calls
  (`grep -c "openai|anthropic|chat_completion"` → 0) and **no API-key field** anywhere in
  `config/*.json`. There is no LLM integration in the repo at all.
- Bug: `terminal.js:2359` posts to `/api/copilot/ask` with **no Authorization header**, so
  remote (non-local) users get 401 — local requests bypass via `_is_local_request`.
- The chat is only on `/classic`; the dashboard has none.

### This item needs your decision
A genuinely conversational bot needs an external model, and there is currently no provider,
no client and no key. Three options:

- **A — Enhance the rule-based copilot (no dependency).** Many more intents, grounded in live
  positions/history/decisions/risk, plus in-session conversation memory. Works offline, free,
  no secrets. Not a real LLM — it will not handle open-ended phrasing.
- **B — Real LLM.** Add a provider client (OpenAI / Anthropic / OpenAI-compatible), key from
  an env var or config, server-side prompt assembly with trade context, optional streaming.
  **Requires you to pick a provider and supply a key.**
- **C — Both (recommended).** Build A as the always-available layer, and put B behind config
  so it activates only when a key is present. No key → today's behaviour, no breakage.

### Logic plan (assuming C)
1. Ground `JarvisCopilot.ask` in the data it already has (`state_manager` snapshot,
   `/api/history`, `/api/pending_orders`) and expand intents; add multi-turn memory keyed by
   session.
2. Add an optional provider layer, used only when configured; never log the key; send no
   credentials in context.
3. Fix the missing `Authorization` header (`authHeaders()` pattern from `console.js:107`).
4. Surface the existing chat widget on the dashboard too.

### Regression risk
- Route `/api/copilot/ask` already exists — extend, don't collide. No `/api/chat*` exists.
- No `EventSource`/WebSocket in the JS (UI polls), so a plain POST chat coexists with the SSE
  loop at `server.py:599-635` untouched.

---

## Item 4 — Merge Analyst + News into the trade page

### Verdict: **feasible, but not by merging into the existing 3-column grid.**

### Current state
- Both are separate *views* in `dashboard.html` — `#view-news` (`:367-418`) and
  `#view-analyst` (`:424-476`), toggled by `setView()` (`dashboard.js:318`) which sets
  `data-active`/`hidden`; CSS `.tt-view` (`theme_terminal.css:234-235`). No separate routes.
- **Analyst needs no backend**: it reads `state.decisions` from telemetry, already polled
  every 3s. Render is gated `if (state.view === 'analyst')` (`dashboard.js:3080`).
- **News** has its own endpoint `/api/news` (`server.py:584`) and a countdown ticker that
  early-returns unless `state.view === 'news'` (`dashboard.js:2367`).
- The trade view is a full 3-column grid (`theme_terminal.css:1035-1038`) with **no spare
  space**. Its `grid-template-areas` does **not** define the `cal/next/detail/da/gate/obj`
  areas used by News/Analyst (`:1059-1074`) — dropping those panels in would auto-place into
  implicit rows and break the layout.
- No element-id or state collisions between the three (verified).

### Logic plan
Implement as **in-page sub-tabs inside the trade view**, not a literal grid merge:
1. Add a segmented control in `#view-trade` switching the ticket column between
   **Ticket / Analyst / News**, reusing `renderDevilAdvocate` / `renderQualityGate` /
   `renderObjections` / `renderNews` **unchanged**.
2. Relax the two view gates so they fire when the sub-panel is active; clear the News
   countdown interval when it is not.
3. Keep the standalone `#view-news` / `#view-analyst` containers intact (nothing deleted) —
   remove them from primary nav only if you want them gone. **This is why the merge is
   lossless.**
4. Minimal CSS: swap the ticket column's inner content rather than redefining grid areas.

### Regression risk
- Very low. Rendering functions are reused as-is; the only change is *when* they are called.
- Analyst adds no new network load (telemetry is already polled at 3s).

---

## Item 5 — Backtest page

### Root cause: a front/back parameter contract bug, not a broken route.
- Routes **are** registered and healthy: GET `server.py:379-382` →
  `intelligence_api.py:558-576`; POST `server.py:757-760` → `:734-746`. Your own
  `audit_endpoints` run reports all 44 UI endpoints dispatching, `POST /api/backtest/run`
  → 202.
- `dashboard.js:3420` sends **`grid_dimensions`** (a list of dimension *names*). The backend
  reads `raw["space"]` (`intelligence_api.py:483`) and the `_spec_kwargs` whitelist
  (`:452-456`) drops `grid_dimensions`. **Every checkbox in "Search grid" is therefore inert**
  and every run uses the full default `GeometrySpace` (≈1920 geometries/mode).
- Evidence: `.scratch/backtest_follow.txt` — a full run took **2195.6 s (36.6 min)**;
  `.scratch/backtest_run.txt` — the client poll died with `TimeoutError` at 60 s. From the UI
  this is indistinguishable from a hang.
- The **console** page works because `buildSpec` sends `space` (`console.js:743-761`), which
  *is* honoured.

### Logic plan
1. Have `dashboard.js` build and send a proper `space` object (mirroring `console.js:743-761`).
2. Make the backend also tolerate `grid_dimensions` for robustness — both keys accepted.
3. UX: show the resolved grid size (geometry count) **before** running, so a long run is a
   deliberate choice; surface job progress; wire the existing `cancel` route
   (`intelligence_api.py:740`) to an abort button; raise the client poll timeout and show
   elapsed time.
4. Verify end-to-end with a deliberately small space.

### Regression risk
- Low. `console.js` already sends `space` and is unaffected; accepting both keys server-side
  is additive. Note backtesting runs on cached series and does **not** need the orchestrator,
  so it works under `HM_dashboard.bat` too.

---

## Item 6 — Active trades on the symbol chart

### Verdict: **feasible, low risk.**
- Chart is vendored **TradingView Lightweight Charts v4.1.1**; `setMarkers` is present in the
  vendor bundle and **currently unused** anywhere in the app (verified).
- Chart created at `dashboard.js:1009` (`createChart`), candle series `:1047-1049`.
- `drawTradeOverlays` (`dashboard.js:1256-1311`) already draws entry/SL/TP **price lines** and
  already filters positions by symbol (`:1257-1259`). This is the natural insertion point.
- `state.positions` (telemetry, 3s) carries everything needed: `ticket, symbol, type, volume,
  open_price, current_price, sl, tp, profit, open_time`.
- `state.chartSymbol` / `state.chartTimeframe` exist; `selectSymbol` (`:595`) triggers
  `loadChart()`, and overlays re-run on every paint (`:1418`, `:1447`).

### Logic plan
1. In `drawTradeOverlays`, after the price lines, call `series.setMarkers([...])` for
   positions matching `state.chartSymbol`: marker at entry time, `belowBar` for buys /
   `aboveBar` for sells, colour by side, label `BUY/SELL @ price`.
2. Optionally add exit markers from closed history where a close time exists.
3. Add a show/hide toggle to avoid clutter.
4. **Trap to respect:** `open_time` is **broker-server time, not UTC** (documented in
   `TRAPS.md`). The offset must come from `jarvis/data/broker_time.py` — **never hardcoded** —
   or markers will land hours off.
5. Mirror into the `terminal.js` chart only if you want it on `/classic` too.

### Regression risk
- Minimal: additive, no backend change, symbol-filtered, uses an already-vendored API.
- Known caveat: markers only render when the entry time falls inside the loaded candle window
  (matters for M5 vs D1).

---

## Cross-cutting regression safeguards

- **Baseline to protect:** `pytest -q` → **883 passed, 20 deselected**; `verify_ui_live.py`
  → **44/44**; `audit_wiring.py` → 135 modules, 0 broken refs. All three will be re-run after
  implementation and must stay green.
- **Additive-first:** new API routes rather than edits to existing handlers; existing render
  functions reused unchanged where possible.
- **No shared-state collisions:** the only new state is `state.history` (already declared,
  currently unused) and a sub-tab index.
- **One important caveat:** the server **does not hot-reload** Python. Every backend change
  needs a restart before it can be tested, and I will say so explicitly rather than implying
  a change is live.
- **Do not run a scan and a backtest simultaneously** — a rescan rewrites
  `scan_manifest_<TF>.json` and misaligns `bar_idx` (`TRAPS.md`).

## What I need from you before writing code

1. **Item 3:** option A, B, or C — and if B/C, which provider and where the key should come
   from (env var vs config file).
2. **Item 4:** should the standalone Analyst/News tabs be removed from navigation, or kept
   alongside the new in-page panels?
3. **Item 1:** just limit orders, or limit **and** stop orders (primitives exist for both)?
4. **Item 2:** confirm the 10-column `/classic` schema is the "original" you want restored,
   and confirm dropping the three columns sourced from `trade_memory` on the console page.

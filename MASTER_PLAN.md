# HM-AI / Jarvis — Master Plan

**Date:** 2026-09-20 · **Scope:** end-to-end audit → prioritized plan → multi-agent delivery system
**Companion:** `AGENT_SYSTEM.md` (the coding system that executes this plan)

---

## 1. Executive summary

The platform is functionally broad and battle-tested at the edges — 40k LOC Python, 23k LOC tests,
69 verification tools, 199 commits — but it has four structural problems that cap its ceiling:

1. **The money path can fail silently and permanently.** A single defect (P1) can disable every
   broker call in the process — *including the calls that close positions* — with no error that
   reaches the UI. Two more (P4, P2) can produce duplicate or unknown-state orders.
2. **A configured risk limit is not actually enforced.** `max_risk_per_trade_pct = 0.5` is
   documented and displayed, but the sizer clamps to a hardcoded `1.50` and then multiplies
   *outside* that clamp. Measured: 27 real trades breach it; the worst risked 39% of equity and
   lost $305 on a $763 account (C1).
3. **More than half the trade history is fabricated.** 136 of 245 rows carry prices from a
   hardcoded fallback, and it is still writing today. Every statistic computed over the table is
   inflated (C2, D1, D2, D5).
4. **The learning loop reads its own output.** `expected_value` is overwritten with realised P&L on
   close (111/111 rows), so self-learning averages the outcome it was meant to forecast, and the
   only "labels" (`triple_barrier_label`, `mfe`, `mae`) are 0 on 35/35 rows (AI2, AI3, AI5, D10).

The good news: the highest-severity items are mostly **small**. The top four are each under a day.

---

## 2. Method

Four specialist agents audited in parallel, each with a bounded charter, each required to cite
`file:line` or a measurement for every claim, and each explicitly instructed to separate real
defects from deliberate design.

| Agent | Domain | Findings |
|---|---|---|
| Full-stack architect | topology, boundaries, flows, scalability, frontend | A1–A17 |
| Data specialist | stores, schemas, pipelines, provenance, retention | D1–D17 |
| AI engineer | signal, models, labels, learning, backtest validity | AI1–AI16 |
| Python expert | concurrency, error handling, deps, test quality | P1–P17 |

**Every finding below was re-verified by the lead before being planned.** Two agent
recommendations were rejected on verification (see §6) — one of them would have caused active harm.

---

## 3. Severity model

**Impact** — worst credible outcome if left alone:
- **H** — loses money, or disables the platform, or invalidates a decision the system makes.
- **M** — degrades correctness, cost, or the ability to detect the next failure.
- **L** — hygiene, cost, or ergonomics.

**Effort** — S ≈ under a day · M ≈ a few days · L ≈ a week+.

---

## 4. P0 — do first (money, paralysis, or silent corruption)

| # | Finding | Impact | Effort | Evidence | Fix |
|---|---|---|---|---|---|
| **P1** | Timeout-guard pool wedges permanently after 16 hangs | H | S | `timeout_guard.py:37-47` — `future.result(timeout=)` never cancels; `_get_executor` only rebuilds on `_shutdown`, which never flips. **Proved:** 16 hung calls → every later call returns its default forever | ✅ **FIXED** — per-pool stuck counter + `done_callback` release + pool replacement; `health()` exposed |
| **P4** | Execution guard is a TOCTOU → duplicate orders | H | S | `orchestrator.py:493-496` reads under lock then **releases**; claim happens 90 lines later at `:586`. Two sweeps both see "free" | ✅ **FIXED** — atomic compare-and-claim at the send site; loser releases risk and skips |
| **C1** | Configured risk limit is not enforced | H | S | `position_sizing.py:86-89` clamps to literal `1.50` vs configured `0.5`, then multiplies by two more factors *outside* the clamp. Measured 27 real breaches; ticket 935634011 XAUUSD risked $299 = 39.2% of equity, lost $305 | Clamp to the configured limit; apply multipliers inside it; hard pre-trade assert |
| **C2** | Fabricated prices are still being persisted | H | S | `tradingview_provider.py:583` — `base_p = 1.0850` EURUSD, 65000 BTC, 3500 ETH, 150 SOL. 136/245 rows; newest is today | ✅ **FIXED** — see the M1 table below: the entry price is now refused at three layers when it derives from an unobserved price |
| **D4** | 54% of `trade_records` lives only in an uncheckpointed WAL | H | S | `.db` alone = 16 rows; `.db`+`-wal` = 35. `-wal` is 201,912 B, never checkpointed | `wal_checkpoint(TRUNCATE)` on shutdown; back up `-wal`/`-shm` |
| **D2** | Reconciliation joins on the wrong MT5 identifier | H | M | `log_trade` stores `result.order`; `sync_mt5_history` matches `WHERE ticket = position_id` (`database.py:195,266`). 212 tickets can never close | Persist order ticket *and* position id; join on position id |
| **A3** | Symbol universe is dead config | H | S | `settings.json` `allowed_symbols` has **no reader**; orchestrator hardcodes 13 (`orchestrator.py:51-55`); `_bg_loop` hardcodes a third list | `JarvisOrchestrator.__init__` defaults to `SETTINGS.trading.symbols` |
| **P2** | Timeout returns `FAILED` on the money path | H | M | `mt5_client.py:548` → `{"status":"FAILED"}`; orchestrator releases risk and **skips cooldown**. A timed-out order may have filled | `status:"UNKNOWN"`; never retry without broker reconciliation |
| **P3** | A timed-out worker keeps the process-wide MT5 RLock | H | M | `mt5_client.py:29,40,447,512` — one hung call blocks all 52 threads | Stop holding a global lock across the broker call |

---

## 5. Ranked backlog

### P1 — correctness and trust (weeks 1–2)

| # | Finding | I | E | Note |
|---|---|---|---|---|
| AI5 | Self-learning averages the outcome, not the forecast | H | M | `self_learning.py:40`; drives live conviction 0.80–1.25 and sizing |
| AI8 | `BacktestEngine` is not hermetic | H | M | Measured: live weights, live trade memory, live bandit state leak into backtests |
| AI6 | Learning loop dies on restart; fallback R is fabricated | H | S | `_pending_features` is process-local; `r_multiple = 2.0/-1.0` invented |
| C8 | Gross vs net P&L; no fee columns anywhere | H | M | `database.py:226` vs `state_synchronizer.py:73`; unmeasurable by construction |
| D5 | Bar fallback is a synthetic random walk | H | M | ✅ **FIXED** 2026-09-20 — provenance now travels *with* the series (`CandleSeries.source`); see M2 below |
| D3 | No migration mechanism; two copies already drifted | H | M | ✅ **FIXED** 2026-09-20 — all 4 versioned stores on `migrate()`; refusal proved 12-red vs 3 |
| A1 | Two radar producers, two contracts, three sort keys | H | M | UI can rank a different "best" than the engine that trades |
| A2 | Parallel scan is a no-op — two process-wide MT5 locks | H | M | 39-task fan-out serializes; measured 2.16s cold sweep |
| D1 | Paper and live fills share one table, no discriminator | H | S | 135/246 rows are paper; no column to filter |
| **A18** | **Paper mode sizes against the live broker balance** | **H** | **S** | *Found during implementation, not in any agent report.* `get_account_snapshot()` queries `mt5.account_info()` before checking `mode` (`mt5_client.py:150-169`), so a paper client that is connected returns the **real** account — measured 762.51 while a test had mocked 10,000. `MT5StateSynchronizer` then caches it into `state_manager.account`, and every sizing decision is made against the broker's equity. It also makes test outcomes depend on test order and on the size of a real account. |

### P2 — capability and ceiling (weeks 3–6)

| # | Finding | I | E |
|---|---|---|---|
| AI2 / AI3 | `triple_barrier_label` and MFE/MAE are 0 on every row | H | S | ✅ **label FIXED** 2026-09-20 (D10) — derived at close from the stored geometry; **MFE/MAE still 0** (D19) |
| AI9 | Walk-forward validator is dead; optimizer output never reaches trading | H | M |
| AI10 | Calibration fitted to its own output on ≤20 rows | H | M |
| AI4 | Gate/sizing probability excludes the only fitted model | H | M |
| A10 / A9 | `/api/history` re-syncs 30 days on every read; SSE pushes 144KB/s | M | M |
| ~~P5~~ | ~~MT5 connection at import time~~ | H | S | **Fixed 2026-09-20** (partial — see below). `server.py:34` built `MT5Client(mode="live")` in the **class body** and `historical_engine.py:265` built one at **module scope**. With no terminal running `mt5.initialize()` blocks in a native call **holding the GIL**, so `Thread.start()` can never complete: `import jarvis.api.server` never returned and **pytest could not even collect** (13min+, no output — and `faulthandler` itself could not fire, which is how you know it is the GIL). Both now pass `auto_init=False`; `MT5Client._reconnect_if_needed()` already connects on first use, so nothing that talks to the broker changes behaviour. **Remaining:** any *runtime* path that really calls `mt5.initialize()` without a terminal still wedges the interpreter — `initialize()` takes no `timeout` argument in MetaTrader5 5.0.6180, so it cannot be bounded from Python. Running the suite needs a terminal (or `JARVIS_BACKTEST_MODE=1`). |
| P6 | sklearn on the critical import path (2.35s of 2.91s) | M | S |
| P10 | No `pyproject.toml`, no lockfile, undeclared `scipy`/`tabulate` | M | M |
| P11 | No coverage / lint / type / timeout tooling | M | S |
| P14 | God functions: `run_backtest` 742 lines, `evaluate` 570 | M | L |
| P15 | 12+ tests silently skip on a fresh clone | M | M |
| A11 | Layering inversion `data → india/stocks` (real 2-cycles) | M | L |
| A13 | 45 duplicated frontend function names across pages | M | L |
| D7–D17 | Registry 99% empty, `bar_idx` window-relative, no retention, write-only learning columns | M | S–M |
| **D18** | **`/api/stocks/candles` calls `mt5.initialize()` unguarded — one request wedges the server** | **H** | **S** | ✅ **FIXED** 2026-09-20 — *found while building D5, not in any agent report.* `_ensure_mt5_connected` ended in a bare `initialize()`; it now asks `broker_symbols.ensure_mt5_terminal()`, which does not launch a terminal. See M2 below. |
| **D19** | **`mfe` / `mae` are hardcoded `0.0` at the call site — "not measured" reads as "measured zero"** | **M** | **M** | Open. `orchestrator.py:285-286` passes `mfe=0.0, mae=0.0` literally; the live path never computes an excursion. Unlike D10 these cannot be derived from the exit alone — they need the intra-trade bar path. See M2 below. |

### P3 — polish (ongoing)
`A14–A17`, `D17`, `P8`, `P16`, and the long tail of hygiene findings.

---

## 6. Rejected agent recommendations (verified, then overruled)

Recording these so nobody re-derives them and "fixes" working code:

- **AI1 — "point `MetaLabeler` at `data/models/`."** Overruled. `_load()`
  (`meta_labeler.py:184-203`) documents that the gate is *deliberately* inert, with measurements:
  test AUC 0.481/0.479, and selecting the top decile **lowered** the win rate (0.341 vs 0.359).
  Fixing the path would have silently activated a harmful veto on a live account. The residual real
  issue — the safe state is achieved *accidentally* via a wrong path — was fixed by making the
  intent explicit instead (comment + log), with no behaviour change.
- **P12 — "147 silently-swallowed handlers."** Overruled as a defect. Verified: the
  money-path handlers are documented fail-safe defaults (`risk_engine.py:395` → atr_ratio 1.0;
  `order_manager.py:79` → spec None), not bugs. Narrowed to the genuinely silent ones.
- **Architect's "verified NOT defects"** list stands: config is genuinely centralised (one reader);
  there is no asyncio loop in the critical path; `_CANDLES_CACHE` is bounded and GIL-atomic;
  `demo` mode connecting to the demo account is by design.

---

## 7. Milestones

Each milestone has an **exit criterion** — a measurement, not a feeling.

### M0 — Money-path concurrency *(done, this session)*
P1, P4, P9 (`_initial_sl` race), P7 (dead cached-regime branch).
**Exit:** regression tests prove the wedge and its recovery; 300 targeted tests green.
**Result:** 6 new tests; wedge reproduced against pre-fix logic and shown fixed after.

### M1 — Stop the bleeding *(~2 days)*
C1, C2, D4, D2, A3, P2.
**Exit:** no trade can open that exceeds `equity × max_risk_per_trade_pct`; zero new rows with a
fabricated price over a 24h run; `.db` alone contains 100% of `trade_records`.

**Status (2026-09-20): 6 of 6 done.**

| Item | Status | Result |
|---|---|---|
| **C1** risk ceiling | ✅ Done | `position_sizing.py` now clamps to the configured `max_risk_per_trade_pct` with multipliers applied *inside* the clamp. Verified pre-fix → post-fix on the worst case (conviction 1.35 × evidence 1.15): **$70.00 → $50.00** on $10k, i.e. 1.40% → exactly 0.500%. Also required a test-isolation fix — see the note below. |
| **D4** WAL checkpoint | ✅ Done | `TradeMemory` checkpoints (TRUNCATE) after every write and before close. Verified pre-fix: a copy of the `.db` alone did not even contain the `trade_records` **table** — schema and rows were both trapped in a 16KB WAL. |
| **A3** symbol universe | ✅ Done | `JarvisOrchestrator` defaults to `SETTINGS.trading.symbols`. **⚠ Behaviour change: the effective universe drops 13 → 5** (`XAUUSD, EURUSD, GBPUSD, USDJPY, BTCUSD`). If the wider set is wanted, add it to `trading.allowed_symbols`. |
| **P2** timeout status | ✅ Done | `send_market_order` timeout now returns `UNKNOWN`, not `FAILED`; the orchestrator holds the risk reservation and starts the cooldown instead of releasing and retrying; the UI treats `UNKNOWN` as "not confirmed" so it can never render as a filled trade. Classified in the response-contract test as its own `INDETERMINATE` class. |
| **C2** fabricated prices | ✅ Done | The entry price is now refused when it derives from an unobserved price, at three layers — see below. |
| **D2** position-id join | ✅ Done | `executed_trades` gains `position_id` (migrated via the existing `ALTER TABLE` map); `send_market_order` resolves and returns it from the deal; both `log_trade` callers persist it; `sync_mt5_history` joins `position_id = ? OR (position_id IS NULL AND ticket = ?)` so legacy rows still close. Measured on `jarvis_history.db`: **155 rows** with `closed_at` NULL and `realized_pnl` 0.0. Proven pre-fix: the sync INSERTED a duplicate row instead of closing the original. |

**C1 — what it exposed.** Tightening the clamp made `test_d1_online_ml_and_trade_memory_learning_loop`
red. It was not the clamp. The test mocks `mt5_client.get_account_snapshot` for $10,000, but the
mock is installed *after* construction and `MT5StateSynchronizer` (`state_synchronizer.py:56`) has
already cached an account — and a paper client whose terminal is connected falls through to
`mt5.account_info()` (`mt5_client.py:150`), so the cached value is the **real demo balance**
(measured: **762.51**, not 10,000). The cycle was therefore sized against the broker's account, and
outcome depended on whether an earlier test in the same process had initialised MT5 and on how big
that account happened to be. At 0.01 lots XAUUSD risks 1.31% of $762.51 — inside the old 2× grace
(2 × 0.776% = 1.55%) and outside the honest one (2 × 0.575% = 1.15%), so the test had been passing
by arithmetic accident. Fixed by pinning `orch.state_manager.update_account(...)` in the test; the
underlying leak (paper mode reads the live balance) is now **finding A18**, below.

**C2 — what it turned out to be.** The deferral note above was right that the fabricated *rows* come from
the **decision engine's** price rather than the provider's fallback, and that this chain needed its own
investigation. Following that chain end to end located the mechanism, and it is not the provider at all:

`market_context.build_context` sets `current_price = bid = 0.0` and `ask = spread × pip_size` when the
primary frame is **empty** (feed down, cold symbol). Every entry price is minted from `context.ask` (BUY) or
`context.bid` (SELL), so an unobserved market became a real-looking order. Measured for BTCUSD:
`entry_price = 0.02`, `stop_loss = -0.04` — and `position_sizing` read a risk distance of 0.06 against a
65 000 instrument and returned **100 lots**, i.e. **6.5M USD of exposure for a 50 USD risk budget**
(EURUSD 90k, XAUUSD 185k for the same 50 USD). All of it passed `trade_guard`, because `_is_finite(0.0)` is
True and the inverted-geometry comparisons (`stop_loss >= entry_price` for a BUY) are False when the entry
itself is ~0.

Fixed at three layers, at the three places the invariant can be broken, plus the provider's own laundering:

* **`schemas.is_observed_price`** — one predicate: a tradeable price is finite **and strictly positive**.
* **`dynamic_levels.calculate_levels`** — refuses to *mint* an entry price, returning a HOLD-shaped result
  with `data_unavailable: True` (the same key set the callers index, so no `None` to guard against).
* **`decision_engine._compute_bias_and_levels`** — refuses to emit a *direction*, because
  `decision_action` becomes EXECUTE on `gate_passed and bias in (BUY, SELL)`; a BUY/SELL verdict beside a
  zero entry would have been marked executable.
* **`trade_guard.validate_pre_execution`** — refuses to *authorize*: the finiteness loop now also requires
  a positive price, so any other producer of a `DecisionObject` is caught too.
* **`tradingview_provider.fetch_candles`** — returns `None` when its anchor quote is a fallback
  (`is_fallback: True`). The series is anchored to that quote bar by bar, so returning it would be a
  fabrication indistinguishable from real data — and `fetch_real_candles` logs whatever comes back as
  "Live TradingView candles". Refusing lets the tier hierarchy fall through to Tier 4, which labels itself.

The provider's *display* fallback is untouched, deliberately: it is labelled (`source: "profile_reference"`,
`is_fallback: True`) and both its consumers already refuse it (`mt5_client.py:360`, `execution_engine.py:27`).
Deleting it would break the stocks/india panels, which legitimately need a reference price.

Pinned by `tests/test_unobserved_price_refusal.py` (49 tests) and `TestNonPositivePrices` in
`tests/test_trade_guard.py`. **Mutation-proved:** neutering the three predicates turns **26** of the new
tests red.

### M2 — Make the data trustworthy *(~1 week)*
D3 migrations, D1 `origin` column, D5 bar provenance, D10/D11 real labels, A10 read-only reads.
**Exit:** `user_version` set on every store; every row tagged `broker|paper|synthetic`; no read
path performs a write; `triple_barrier_label` non-zero on closed rows.

**Done so far:** D1 (`origin`, shipped), D5 (bar provenance, shipped), D10 (the triple-barrier
label, shipped), D18 (the read path, shipped), **D3 closed** — all four versioned stores
(`executed_trades`, `trade_records`, `circuit_state`, `drawdown_state`, `metadata`) on `migrate()`.
**Still to do:** reconciling the two `jarvis_history.db` copies, D19 (MFE/MAE), A10's SSE half.

**Status (2026-09-20): D3 half done — the version stamp.** All 11 databases reported
`user_version = 0`, so no file could declare what shape it was in, and two copies of
`jarvis_history.db` (root and `data/`) have already drifted — the root copy has no `closed_at`.
`jarvis/data/schema_version.py` now provides `read_version` / `write_version` / `ensure_version`,
and all five stores stamp themselves: `executed_trades`, `trade_records`, `metadata`,
`circuit_state`, `drawdown_state`. Version 0 is treated as "predates versioning", not as an error.
The rule that matters is the other direction: a file written by **newer** code is *not* stamped
down to what this version knows — it is left alone and logged, because stamping it down is how
columns get silently dropped. Guarded by `tests/test_schema_version.py` (8 tests).
Then the **migration runner** (`migrate()` / `add_columns()`, `MIGRATIONS = {1: _migration_1}`):
numbered steps, version written after each one, a failing step raises instead of half-applying, and
a file from newer code is refused rather than migrated down. 13 tests, one of which builds the
original 12-column table and proves migration 1 reconstructs all 13 added columns.

**Also fixed: the suite was writing into the live trade journal.** Measured —
`data/jarvis_history.db` gained exactly one EURUSD BUY row per run (rows 264–269, timestamps
matching six consecutive runs). `ExecutionEngine.execute_decision` imports the `TRADE_DB` singleton
at *call* time, so any test driving a real execution path journalled a fake trade into the same
table every realised-P&L statistic is read from. An autouse conftest fixture now swaps
`database.TRADE_DB` for a per-test temp file; verified 269 → 269 across a full run. `JARVIS_DATA_DIR`
could not be redirected for this — conftest already records that 12 parquet-reading tests break.

**Open, needs your call:** the root `jarvis_history.db` is an abandoned **3-row** artifact
(`user_version=0`, no `closed_at`, no `position_id`, all from 2026-09-11), left over from before
path anchoring. The app resolves to `data/jarvis_history.db` (268+ rows, complete). I have not
touched it — say the word and I'll archive it rather than delete it.

**D3 remainder — `trade_records` now migrates in numbered steps.** It used to run this on
**every open**:

```python
cur.execute("PRAGMA table_info(trade_records)")
columns = [info[1] for info in cur.fetchall()]
if 'ml_features' not in columns:
    cur.execute("ALTER TABLE trade_records ADD COLUMN ml_features TEXT")
if 'triple_barrier_label' not in columns:
    cur.execute("ALTER TABLE trade_records ADD COLUMN triple_barrier_label INTEGER DEFAULT 0")
```

That sweep can express neither ordering, a backfill, nor a refusal, and it ignores `user_version`
entirely. The refusal is the part that bites: given a file from **newer** code that no longer carries
`triple_barrier_label` (renamed or dropped), the sweep **re-adds it** — silently undoing the newer
schema — while `ensure_version` logs "written by newer code" about a change the sweep has already
made. It is now `migrate()` with `MIGRATIONS = {1: _migration_1}`, so the step runs at most once and
a newer file is refused rather than patched back.

Writing to that newer file had a second, independent failure: `record_trade` used
`INSERT OR REPLACE INTO trade_records VALUES (?, … ×22)` — **22 bare placeholders, no column names** —
so it is pinned to this version's exact column count and fails outright on a 23-column file
(`table trade_records has 23 columns but 22 values were supplied`). The insert now names its columns,
which tolerates extra ones and is immune to reordering.

`tests/test_trade_memory_migration.py` (8 tests). **Mutation-proved: only 2 go red** — and they are
exactly the two cases that distinguish the mechanisms (`a_newer_file_is_not_patched_back_into_this_
schema`, `a_newer_file_with_an_extra_column_is_still_writable`). The other 6 pass under both
implementations because the sweep happened to be correct for them: it does add missing columns and
does refuse to stamp a number down. That is the honest measure of what `migrate()` bought here —
the *refusal*, not the mechanics.

**D3 closed — the last three stores.** `circuit_breaker`, `drawdown` and `metadata_db` were
`CREATE TABLE` + `ensure_version` with no sweep, so there was nothing to remove; each now declares
`MIGRATIONS = {1: _migration_1}` and calls `migrate()`. What that buys is one thing, and it is not
visible today:

`ensure_version(conn, SCHEMA_VERSION, name)` stamps **whatever number the code declares**, whether or
not anything was done to earn it. Bump `SCHEMA_VERSION` to 2 to add a column, forget the `ALTER`, and
every existing file is labelled 2 while still missing the column — the store then reads a column that
does not exist and gets `None` where it expects a value, which is how a risk gate fails open.
`migrate()` refuses: with no step registered for 2 it stops at 1 and says so.

Because the two are behaviourally identical *today*, the mutation proof has to simulate the mistake
it guards against rather than the code it replaced. With `SCHEMA_VERSION` bumped to 2 and no step
registered: **12 tests go red under `migrate()`** (the registry has no step; a fresh file stops at 1;
an unversioned file stops at 1; opening twice stops at 1 — each × 3 stores). With the same bump under
the old `ensure_version`: **3 go red**, because the store stamps 2 anyway and the only thing left to
notice is the registry's shape. That 12-vs-3 gap is the whole argument for this change.

`tests/test_store_migration.py` (24 tests). `test_ensure_version_would_have_stamped_it` pins the
contrast by measurement, so if the machinery ever stops buying anything these stores can go back.

**Still to do for D3:** reconciling the two `jarvis_history.db` copies (see *Open, needs your call*).

**Retracted:** an earlier pass recorded "the paper-scoped store is not stamped" as a D3 gap, from
`data/jarvis_drawdown_state_paper.db` reading `uv=0` while its sibling read `uv=1`. **It is not a
defect** — constructing a `DrawdownGuard` on a paper-scoped path stamps it `1` immediately. The file
merely was not reopened after the stamping landed (mtime 03:42 vs 14:09). A store's on-disk
`user_version` records when it was last opened, not whether the code stamps it.

**D1 — the `origin` column: shipped.** `executed_trades` is at version 2 and every row now carries
`broker|paper|synthetic|unknown`. Migration 2 adds the column and backfills from the only evidence
that survives: `sync_mt5_history` formats an **int epoch** (whole seconds) while `log_trade` uses
`datetime.now()` (microseconds), so `timestamp NOT LIKE '%.%'` identifies a broker-synced row.
That classification is corroborated by the outcome — of 269 rows, the 158 microsecond ones have
**0 closes between them** and 110 of the 111 whole-second ones are closed.

The other half of D1 was the labeller, and it was broken in a way that mattered:
`_price_origin` branched on `res.get("is_fallback")`, and **no fill ever set that key** — the only
`is_fallback` in `mt5_client.py` was inside the quote *refusal* path, which returns `None` and never
produces a fill. So `synthetic` was unreachable and a live client that silently simulated an order
was journalled as `broker`. Fills now report it. The subtlety is that `init_connection()` rewrites
`self.mode` to `"paper"` when the terminal is missing, and `send_market_order` called
`_reconnect_if_needed()` **before** checking the mode — so the client had already forgotten it was
ever live. The requested mode is captured first; `tests/test_fill_origin.py` proves the pre-fix
value was `False` in exactly the case the flag exists to catch.

Two live bugs fell out of writing it, both silent because `log_trade` swallows its exceptions into
a log line:

* `ORIGINS` is a **tuple**, but the code called `ORIGINS.get(...)` — `AttributeError` on every call,
  which meant the journal would have stopped recording trades with no crash anywhere.
* The 24-column INSERT had **24 placeholders for 23 columns** (`24 values for 23 columns`).

Both are now covered by tests. `MT5_AVAILABLE` was hiding a third: a live/demo client silently
takes the paper branch and, before this change, reported it as a normal fill.

**Environment hazard, measured:** with no terminal running, `mt5.initialize()` **blocks forever**
inside the native call while holding the GIL (killed at 25s, still running). No Python-side timeout
can rescue it — `TimeoutGuard` works by starting a thread and no thread can start while the GIL is
held, so `faulthandler` cannot even dump. It stalled the whole suite at
`test_paper_book_is_not_reported_by_a_disconnected_live_session`, which constructed a live client
purely to assert it does *not* connect; that test now uses `auto_init=False`. This is the residual
of P5: import-time construction is fixed, runtime `initialize()` cannot be bounded from Python.

---

**D5 — bar provenance: shipped.** The label described the wrong object. Both engines set
`_last_data_source = "live"` when the **anchor price** came from a live quote
(`stock_engine.py:59`, `india_engine.py:46`), while every bar was generated from
`np.random.RandomState(stable_seed(f"{symbol}_{tf}_{hour}"))`. Measured
(`.scratch/repro_d5_bar_provenance.py`): the returned series **reproduces from that seed** to within
the 2dp rounding (max deviation `0.000020`), a 40% market move changes only its *scale*, and the last
12% of bars is a **guaranteed monotonic surge** (`stock_engine.py:98`) — `rising=True`. So a fully
generated random walk was published to the UI as `data_source: "live"`, and `dashboard.js` renders
that as a live feed.

Provenance now travels **with the data**, not on the engine: `CandleSeries(list)` carries `source`,
`anchor_source` and `is_synthetic`. Being a `list` subclass keeps `pd.DataFrame()`, `len()`,
iteration, slicing, `+` and `json.dumps` working unchanged. The vocabulary is
`live` / `synthetic_anchored` (generated, anchored to a live quote) / `calibrated_feed` (generated
from a static reference) — `calibrated_feed` was kept as its existing wire value deliberately,
because `dashboard.js`'s `SOURCE_META` already renders it as "modelled". India's engine has **no
live-bar branch at all**, so `live` was never correct there.

That also removes a **race**: the engines are module-level singletons and the stocks screener drives
them from a `ThreadPoolExecutor(max_workers=16)` (`stock_service.py:57`), so a per-instance
`_last_data_source` could be overwritten by another symbol between the call and the read. The
response now reads `getattr(candles, "source", ...)` off its own series.
`tests/test_candle_provenance.py` (22 tests).

**D18 — the read path called `initialize()`: shipped.** Found while building D5, because the D5
repro **hung for two minutes**. `py-spy dump --pid 15468` showed the frame:
`/api/stocks/candles` → `generate_candles` → `_try_mt5` → `_resolve_mt5_symbol` →
`_ensure_mt5_connected` → **unguarded `mt5.initialize()`**. One HTTP request wedged the entire
server — the same GIL hazard as P5, but now reachable at *runtime* from a route. Bounded proof from
**outside** the process (`.scratch/prove_mt5_block.py`): pre-fix `child STILL RUNNING after 25s —
killed`; post-fix `child exited after 4.1s with rc=0` returning `RETURNED 120`.
`_ensure_mt5_connected` now checks `terminal_info().connected` and otherwise delegates to
`broker_symbols.ensure_mt5_terminal()`, which never launches a terminal.
`tests/test_mt5_read_path_is_bounded.py` pins the **mechanism** — "`initialize()` was never called" —
rather than a wall-clock bound, so a regression fails for the right reason instead of hanging the
suite.

**Mutation-proved (both):** `.scratch/revert_d5_fixes.py` restores the pre-fix behaviour in four
places (the read path's bare `initialize()`; both engines' `"live"` labels; `analyze_stock`'s read
site; the providers returning a plain `list`) and turns **10** of the new tests red. The 20 that stay
green are contract and invariant pins the mutation does not touch by design — the `CandleSeries`
type contract (9), the "real bars are still `live`" and series-shape invariants (2), the delegate
consistency check (1), and the MT5 short-circuit / unavailable / raising paths (6), plus the
skipped-when-a-terminal-is-running probe (1) and one parametrisation. `test_the_delegate_reports_
the_same_provenance` is honestly a *consistency* pin, not a fix proof: under mutation both sides
become `"live"` together and it still passes.

---

**D10 — the triple-barrier label: shipped.** The column was 0 on **36/36** live rows (one distinct
value), including 8 whose exit sits on or through a stored barrier. Two defects stacked:

1. `record_trade` derived the label at **open**, from
   `1 if pnl > 0 else (-1 if pnl < 0 else 0)` — but a trade being *opened* has no `pnl`, so the
   fallback always evaluated to **0**. It minted "the vertical barrier was hit" for every trade
   before it had been closed.
2. `update_closed_trade` updated `exit_price / pnl / is_win / mfe / mae` and **never touched the
   label**, so the row kept the 0 it was born with. (`is_win` *was* repaired on close — 4 rows carry
   `is_win = 1` — which is how we know the close path runs.)

The label is now derived at close from the row's own stored geometry
(`derive_triple_barrier_label`): +1 if the exit reached the take-profit barrier, −1 if it reached the
stop, 0 if it stopped between them (the vertical barrier), and **`None` when the row cannot answer**
— so "unlabelled" stays distinguishable from "vertical barrier hit". `COALESCE` in the UPDATE means
an explicit caller-supplied label is never downgraded to NULL.

Two things fell out of building it:

* **A float artifact was deciding the label.** Live row `938435830` stored
  `sl = 111.29999999999998` and filled at `111.30`, so a plain `exit <= sl` answered "not reached"
  for a trade that *was* stopped out. The comparison now carries a **relative 1e-9 tolerance** — far
  below one tick (a BTCUSD tick is ~1.2e-7 relative, a EURUSD pip ~8.7e-6), so it cannot swallow a
  genuine near-miss. Pinned by `test_a_genuine_near_miss_is_still_a_vertical_barrier`.
* **"Both barriers hit" is not a gap.** For a well-formed BUY, `exit >= tp` and `exit <= sl` cannot
  both hold — a gap fills *on or beyond* whichever barrier was touched, which classifies cleanly. The
  condition therefore means `tp <= sl`, i.e. **contradictory barriers**. The first version of the fix
  guessed `-1` there; it now refuses (`None`), because a malformed row cannot be labelled.

**Projected over the live journal: 5 × −1, 1 × +1, 16 × 0, 14 × unlabelled** (the unclosed rows).
All six non-zero labels agree in sign with the realised `pnl` — an independent check that the
derivation reads the right thing.

**Scope, stated honestly:** nothing reads this column today — `strategy_memory.py:28-32` learns from
`is_win` and `pnl` only, so the zeros never changed a decision. This is a *data-honesty* defect: a
future learner reading the column would have been fed all-zeros, the same shape as C2 and D5 (a
plausible value that actually means "unknown"). `tests/test_triple_barrier_label.py` (37 tests);
**mutation-proved: 22 go red** under `.scratch/revert_d10_fixes.py`. The 15 survivors are the
"expects 0", "caller-supplied label respected" and "other columns unchanged" invariants the mutation
cannot disturb by construction.

**D19 — `mfe` / `mae`: open, and *not* the same fix.** `orchestrator.py:285-286` passes
`mfe=0.0, mae=0.0` **literally**. These are maximum favourable / adverse excursion — they need the
trade's *path*, which the live close handler does not retain, so unlike D10 they cannot be derived
from the exit. Two honest options: reconstruct the path from `mt5.copy_rates_range` between entry and
exit at close time, or record `NULL` so "not measured" stops reading as "measured zero". Left open
deliberately rather than papered over with a plausible number.

### M3 — Make learning real *(~2 weeks)*
AI2, AI3, AI5, AI6, AI7, AI8, AI9.
**Exit:** a backtest run twice produces byte-identical results; the learning loop survives a
restart; walk-forward geometry either reaches live levels or is labelled advisory in the UI.

### M4 — Raise the architecture ceiling *(~3 weeks)*
A2/A4 engine process split, A1 one radar contract, A13 shared frontend modules, P10 packaging,
P11 coverage/lint/timeout.
**Exit:** sweep wall-time scales with workers; one `to_radar_item()`; `pyproject.toml` +
lockfile; coverage measured and a threshold enforced in CI.

### M5 — Guardrails *(continuous)*
P17 — a regression test for every fixed defect.
**Exit:** every P0/P1 item has a test that fails if reverted.

---

## 8. Suggested sequencing rationale

Fix **C1 before anything else in M1** — it is the only finding that can lose money *today*, and it
is a one-line clamp. Then **C2**, because until fabricated prices stop entering the table, every
downstream statistic (including the ones M2 and M3 depend on) is unreliable. M2 and M3 are
gated on that.

Do **not** start M4 before M1/M2: splitting the engine into its own process while the data layer
still fabricates prices would parallelise the wrong numbers.

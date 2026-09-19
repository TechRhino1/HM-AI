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
| **C2** | Fabricated prices are still being persisted | H | S | `tradingview_provider.py:583` — `base_p = 1.0850` EURUSD, 65000 BTC, 3500 ETH, 150 SOL. 136/245 rows; newest is today | Return `None`, refuse the order; add `origin` column |
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
| D5 | Bar fallback is a synthetic random walk | H | M | `tradingview_provider.py:718-757`; callers cannot tell it from real data |
| D3 | No migration mechanism; two copies already drifted | H | M | `user_version=0` on all 9 DBs; root copy lacks `closed_at` |
| A1 | Two radar producers, two contracts, three sort keys | H | M | UI can rank a different "best" than the engine that trades |
| A2 | Parallel scan is a no-op — two process-wide MT5 locks | H | M | 39-task fan-out serializes; measured 2.16s cold sweep |
| D1 | Paper and live fills share one table, no discriminator | H | S | 135/246 rows are paper; no column to filter |

### P2 — capability and ceiling (weeks 3–6)

| # | Finding | I | E |
|---|---|---|---|
| AI2 / AI3 | `triple_barrier_label` and MFE/MAE are 0 on 35/35 rows | H | S |
| AI9 | Walk-forward validator is dead; optimizer output never reaches trading | H | M |
| AI10 | Calibration fitted to its own output on ≤20 rows | H | M |
| AI4 | Gate/sizing probability excludes the only fitted model | H | M |
| A10 / A9 | `/api/history` re-syncs 30 days on every read; SSE pushes 144KB/s | M | M |
| P5 | Live MT5 connection at **import** time | M | S |
| P6 | sklearn on the critical import path (2.35s of 2.91s) | M | S |
| P10 | No `pyproject.toml`, no lockfile, undeclared `scipy`/`tabulate` | M | M |
| P11 | No coverage / lint / type / timeout tooling | M | S |
| P14 | God functions: `run_backtest` 742 lines, `evaluate` 570 | M | L |
| P15 | 12+ tests silently skip on a fresh clone | M | M |
| A11 | Layering inversion `data → india/stocks` (real 2-cycles) | M | L |
| A13 | 45 duplicated frontend function names across pages | M | L |
| D7–D17 | Registry 99% empty, `bar_idx` window-relative, no retention, write-only learning columns | M | S–M |

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

### M2 — Make the data trustworthy *(~1 week)*
D3 migrations, D1 `origin` column, D5 bar provenance, D10/D11 real labels, A10 read-only reads.
**Exit:** `user_version` set on every store; every row tagged `broker|paper|synthetic`; no read
path performs a write; `triple_barrier_label` non-zero on closed rows.

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

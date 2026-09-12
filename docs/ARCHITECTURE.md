# JARVIS AI 5.0 — Architecture Reference

> Status: post-refactor. This document is the entry point for anyone modifying
> the system. Read it before touching execution, learning or configuration.

---

## 1. The two codebases problem (RESOLVED — read this first)

The repository historically contained **two parallel implementations**:

| Tree | Reached from | Status |
|---|---|---|
| `jarvis/` | `main.py` → `JarvisOrchestrator` (98 modules) | **LIVE — this is the system** |
| `engines/`, `core/`, `strategies/` | only `core/jarvis_supervisor.py` + some tests | **DELETED** |

The dead tree has been **removed**: 29 files / 3,667 lines across `core/` (5),
`engines/` (21), `strategies/` (1) and two test modules that imported only dead
code. Removal was gated on an AST reachability analysis
(`tools/dead_code_audit.py`) that walks the import graph from every real entry
point, plus a scan for dynamic references (`importlib`, `__import__`, module-name
string literals). Only modules with **zero live importers** were deleted.

**Rule for all future work: modify `jarvis/` only.** If you add a module, add its
entry point to `tools/dead_code_audit.py::ENTRY_POINTS` or the next audit will
report it as dead.

---

## 2. Layer map (live tree)

```
main.py
  └─ jarvis.application.orchestrator.JarvisOrchestrator
       ├─ jarvis.market.*        market context, data feed, regime
       ├─ jarvis.intelligence.*  decision engine, analysts, order flow, symbol profiles
       ├─ jarvis.learning.*      online ML, bandit, trade memory
       ├─ jarvis.risk.*          sizing, drawdown, circuit breaker
       ├─ jarvis.execution.*     order placement, position monitor, EXIT POLICY
       ├─ jarvis.backtesting.*   event-driven backtester
       ├─ jarvis.data.*          schemas, registry, database
       └─ jarvis.config.*        settings, canonical paths
```

### Dependency direction is strictly downward.
`config` ← `data` ← everything. Nothing in `jarvis.execution` imports
`jarvis.application`. Violating this creates import cycles and makes the
backtester unable to run standalone.

---

## 3. The exit-policy contract (the single most important invariant)

**Every stop-loss decision in the entire system is computed by one pure
function:**

```python
from jarvis.execution.exit_policy import ExitPolicy, evaluate_exit
```

Three consumers, one implementation:

| Consumer | File | Why |
|---|---|---|
| Backtester | `jarvis/backtesting/engine.py` | must reproduce live behaviour |
| Live monitor | `jarvis/execution/position_monitor.py` | 2-second authoritative loop |
| Order manager | `jarvis/execution/order_manager.py` | orchestrator's manage path |

### Why this matters (the defect it fixes)
Before the refactor there were **three divergent implementations**:

| Implementation | Breakeven trigger | Trail width |
|---|---|---|
| Backtest engine | `be_trigger_r = 1.00` (hardcoded for gold) | 2.2–2.6 × ATR |
| Position monitor | `STAGE1_ATR_TRIGGER = 0.9` × ATR × conviction | 0.85 × ATR |
| Order manager | `STAGE1_ATR_TRIGGER = 1.0` × ATR | 0.85 × ATR |

Consequences:
- Locking breakeven at **+1R** closed trades near zero that later ran **+20R**.
- The backtest trail (2.2–2.6 ATR) was **wider than the initial stop**, so it
  sat *behind* the stop and could never ratchet.
- Live and backtest could never agree, so a passing backtest proved nothing.

### Hard rules for `evaluate_exit`
1. It must remain **pure** — no I/O, no mutation of inputs.
2. All thresholds are **R-multiples** (`R = |entry − initial_sl|`). ATR is used
   **only** for the trailing width, where volatility-relative sizing is correct.
3. `1R` is **frozen at position open** and never recomputed. Recomputing against
   the current stop makes R drift as the stop ratchets and the thresholds
   silently change meaning.
4. Ratchets are **one-way**. A stop never moves backwards, never crosses price.
5. Degenerate risk (`entry == initial_sl`) → **no-op**. Never manufacture a stop.
6. `risk_dist` is a **price distance, not pips**. Mixing units caused a prior
   defect where an ATR multiple was compared against a pip count.
7. **The caller owns the "partial already taken" flag.** `evaluate_exit` is a
   pure function; it cannot know whether the partial was actually executed. A
   caller that cannot split the lots (micro size) must still pass
   `partial_already_taken=True` on subsequent calls, or the partial will be
   reported as due on every tick. `ExitDecision.partial_due` exposes this state.

### Monitor-loop robustness
The 2-second monitoring loop **must never die on one malformed decision**. When
reading optional fields off a decision object, coerce via
`PositionMonitorEngine._coerce_positive_float` rather than comparing directly —
a `None`/mock/non-numeric attribute previously raised `TypeError` mid-loop and
aborted position management entirely.

### Adding a new exit rule
Add it to `evaluate_exit` only. Append to `ExitDecision.actions` for telemetry.
Then add a case to `tests/test_regression_fixes.py::test_e1_*`.

---

## 4. Order flow when a decision is made

```
Orchestrator scan tick
  1. data feed            → MarketContext (price, ATR, spread, structure, momentum)
  2. regime classifier    → RegimeOutput
  3. analyst cluster      → AnalystReports (per-specialist bias)
  4. devil's advocate     → DevilAdvocateReport (threat level)
  5. decision engine      → DecisionObject (bias, entry, SL, TP, lots, score)
       └─ _compute_bias_and_levels(context, regime, reports,
                                   account_balance=<REAL BALANCE>,
                                   risk_per_trade_pct=<REAL RISK>)
  6. risk engine          → approve / veto (daily loss, drawdown, exposure)
  7. order manager        → execute
  8. position monitor     → 2 s loop: evaluate_exit() ratchets the stop
  9. on close             → orchestrator._on_trade_closed()
       └─ learning loop    → online ML weights + bandit update
```

### Known-good data-flow contracts (verify these when refactoring)
- `account_balance` **must** propagate from the caller into position sizing.
  A hardcoded `10000.0` previously caused a small account to be sized as if it
  held $10 000.
- Every value written into a trade dict/plan must be **read** by the next stage.
  `runner_trail_distance_atr` was written but never read — the exit logic used a
  different attribute instead. Dead values that look live are the most dangerous
  kind of bug. Grep for a key's name before adding it.

---

## 5. AI / self-learning design

| Component | File | Purpose |
|---|---|---|
| Online ML predictor | `jarvis/learning/online_ml_predictor.py` | online SGD + L2 ridge, return-weighted |
| Drift protection | same | Brier-score trigger, **non-destructive** shrink |
| Strategy bandit | `jarvis/learning/strategy_bandit.py` | Thompson-sampling arm selection |
| Trade memory | `jarvis/learning/trade_memory.py` | persistent journal |
| Meta-labeling | `jarvis/intelligence/meta_labeler.py` | model-based entry confirmation |
| Isotonic calibration | `jarvis/intelligence/winrate_targeting.py` | score → realised win probability (PAV) |
| Regime-edge policy | same | learned symbol×regime enable/disable |
| Walk-forward CV | `jarvis/learning/walk_forward.py` | `PurgedKFold`, purged OOS folds |
| Sample uniqueness | `jarvis/learning/sample_weights.py` | overlap-corrected weighting |
| HRP allocation | `jarvis/risk/hrp_allocator.py` | inverse-variance portfolio weights |

### Learning-loop invariants
1. **R-multiple sign must derive from trade direction**, never from win/loss.
   `(exit − entry)` is only correct for BUY. Using win/loss to pick the formula
   gave SELL winners a negative R and BUY losers a positive R, poisoning the
   return-weighted gradient.
2. **Drift protection must never destroy learned weights.** The old code did
   `weights *= 0.70` on consecutive steps, compounding to ~50 % signal loss, and
   reset `training_steps` to 10 (restoring a large learning rate). Now: threshold
   `0.28 + 0.07 = 0.35`, window ≥ 30, **100-step cooldown**, shrink `0.92`. Under
   40 consecutive losses the model retains **78.4 %** of signal (was 30–50 %).
3. **Model file paths must be absolute.** A CWD-relative weights file meant the
   live loop and the backtest could load different models. Resolved via
   `_resolve_model_path`.
4. **Learning must be disabled during backtests.** See §11.

---

## 6. The calibrated win-rate pipeline (the four-stage split)

Win rate is *not* an independent objective:

```
expectancy (R) = WR × avg_win_R − (1 − WR) × avg_loss_R
```

so any win rate can be manufactured by shrinking the target relative to the stop.
The pipeline therefore treats "75 %" as a **constrained** objective — maximise
expectancy subject to `win_rate >= target` — and is split into four stages so that
the expensive step runs once and the searchable step stays cheap:

| Stage | Module | Cost | Output |
|---|---|---|---|
| 1. Signal scan | `jarvis/backtesting/signal_scan.py` | ~30 s/symbol | every directional candidate the live pipeline considered |
| 2. Trade simulation | `jarvis/backtesting/trade_simulator.py` | ~1–2 s/geometry | outcome in R for one exit geometry |
| 3. Calibration | `jarvis/intelligence/winrate_targeting.py` | seconds | per-symbol profile (walk-forward) |
| 4. Entry selection | `jarvis/execution/entry_policy.py` | — | allow / deny at runtime |

### Why the split exists
The live decision pipeline costs **~78 ms per bar**. Re-running it once per
candidate geometry (64 geometries × 16 symbols) would take days. Instead:

* stage 1 runs the real pipeline **once per symbol** and records every candidate
  (bias, levels, score, regime, failing gates) — ~1,200 candidates per symbol
  instead of ~4 trades;
* a trade's outcome depends only on the geometry and the forward path, **never on
  which other trades were taken**, so stage 2's results are reusable across every
  entry threshold. That turns a `geometries × thresholds` search into
  `geometries` simulations plus a cheap filter.

### Hard rules
1. **`Geometry` must never encode entry thresholds in the simulation.** Thresholds
   only filter; they must not change a simulated path. `tests/test_winrate_targeting.py::test_threshold_does_not_change_simulated_path` pins this.
2. **Intrabar ordering is conservative.** A bar spanning both the stop and the
   target is booked as a **loss**. The live engine previously advanced the stop to
   breakeven using the bar's favourable excursion and *then* asked whether the stop
   was hit on the same bar — look-ahead that inflated win rate exactly where the
   target lives.
3. **The simulator and the live monitor share `evaluate_exit`.** Never re-derive
   stop arithmetic here.
4. **Configuration is selected on training folds only.** Each fold votes; the
   deployed configuration is the one the folds agree on most often, with ties
   broken on the *average train* summary. Selecting on the full sample and then
   reporting folds as "out-of-sample" is selection leakage.
5. **`target_met` must require positive expectancy.** A 75 % profile with negative
   expectancy is a failure, not a success.
6. **A symbol that cannot reach the target must say so.** `binding_constraint`
   names the cause (sample size / expectancy / selection stability / win rate)
   rather than tuning to the target in-sample. The `frontier` field records, per
   target size, the best win rate with and without positive expectancy — the
   evidence for the verdict.
7. **The learned regime policy is fitted on training folds only.** It makes a hard
   include/exclude decision, so fitting it on the same out-of-sample trades it
   filters lets it delete exactly the losers it has already seen. That is a filter
   tuned to the test set. Measured on this data it moved the aggregate
   out-of-sample result from **−33.9 R to +51.8 R** — the size of the artefact.
   Fitted on train folds instead, the honest figure is **+17.3 R**. The profile
   publishes both (`oos_pre_policy_*` and `oos_*`) plus `policy_fitted_on`, so the
   contribution is visible rather than assumed.
8. **Report the system that ships.** The engine applies the regime policy before
   choosing a position, so calibration replays the identical per-fold selection
   with the disabled regimes removed. Filtering *after* the one-position-at-a-time
   walk would drop later eligible trades too and understate the system — the exact
   mismatch that once had calibration claim 192 out-of-sample EURUSD trades while
   the engine produced 7.
9. **Selection must not use a geometry whose target cannot break even at the
   win-rate target.** For a 1R stop, `WR_breakeven = 1 / (1 + tp_r)`, so requiring
   break-even to fall at or below the target gives `tp_r >= 1 / target_wr - 1`
   (0.3333 at 75 %). The calibrator is asked to *reach* a win rate, and the
   cheapest way to raise one is to move the target closer — unconstrained, the
   search walks `tp_r` downward into the region where the target is unreachable at
   a profit. Measured on the 16-symbol portfolio, **7 of 16 symbols were
   calibrated to `tp_r < 0.3333` and all 7 lost out of sample**, with a mean
   in-sample win rate of 76.5 % and a mean in-sample expectancy already negative
   at −0.0279 R; of the 8 symbols that reached 75 % in-sample, only 1 was
   profitable out of sample. Enforcing the floor improved aggregate
   out-of-sample from **−54.74 R to −37.65 R** while *lowering* the win rate by
   9.5 points — 309 trades removed, every one net-negative. The low-`tp_r`
   geometries stay in `default_geometry_grid` so the frontier diagnostic can still
   show what they cost; only `_grid_for` (selection) excludes them. See
   `min_tp_r_for_target()` and `tests/test_winrate_targeting.py`.
   The bare floor is a *correctness* bound, not a safety bound: at exactly 0.3333
   the target win rate equals the break-even win rate, leaving nothing for spread,
   slippage or trailing exits realising less than the nominal target. Use
   `--reachability-margin` to require real headroom (0.17 gives `tp_r >= 0.5`).

### Reporting contract
`tools/run_3month_backtest.py` renders seven sections; two of them exist purely
to keep the headline honest:

* **§2a** puts the out-of-sample sample side by side with and without the regime
  policy. The gap is the policy's entire contribution.
* **§3** is the win-rate / expectancy frontier, which turns "we did not reach
  75 %" into "75 % is or is not reachable, and here is what it costs".

### Retraining / self-learning
`WRProfileStore.merge_realised(symbol, outcomes)` refits the **isotonic
calibration and the regime policy** from realised trades while holding the
geometry fixed. The geometry is chosen on a long history; a short run of live
results must not silently rewrite the strategy.

`score_calibration` is a **telemetry / interpretation artefact, not a gate**.
Isotonic calibration is monotone, so `score >= threshold` and
`calibrated_p >= calibrated_p(threshold)` are the same test — it cannot change
which candidates pass. It exists so the score is interpretable as a probability
and so the AI layer has one to reason with; §4 of the report prints
`P(win) @ threshold` and the Brier score from it.

---

## 7. Hermetic execution mode (backtests must be reproducible)

Several components on the decision path are **stateful against disk**:

| Component | Reads / writes |
|---|---|
| `RealtimeOptimizer` | reads recent realised PnL from SQLite and shifts `min_score` / `min_rr` / `required_win_p` |
| `OnlineMLPredictor` | loads **and saves** model weights (JSON) |
| `SelfLearningEngine` | reads/writes pattern statistics (SQLite) |
| `MetaLabeler` | loads/saves a fitted model (joblib) |

That is correct for live trading — the system is supposed to adapt. It produced
two concrete defects in backtests:

1. **Non-reproducibility.** Running the same backtest twice gave different
   results, because the first run wrote state the second read. Measured directly:
   the same EURUSD scan reported `executed=3` then `executed=2`.
2. **Live/history contamination.** A backtest could read the *live* trade
   database, letting today's realised results change gate thresholds for bars
   dated months earlier.

`jarvis.config.runtime.offline_mode()` puts the process into a hermetic state:
each component starts from its neutral prior and writes nothing. **Every
backtest and every scan must run inside it.**

```python
from jarvis.config.runtime import offline_mode

with offline_mode():
    result = engine.run_backtest(...)
```

`SignalScanner` wraps its pass automatically; `tools/run_3month_backtest.py`
wraps the whole run. Determinism is verified by hashing the candidate table
across two consecutive scans.

**Rule: if you add a component that reads or writes persistent state on the
decision path, it must honour `is_offline()`.**

---

## 8. Persistence & paths

All databases resolve through `jarvis.config.paths.resolve_db_path()`:

```
database.py            → data/jarvis_history.db
trade_memory.py       → data/jarvis_trade_memory.db
circuit_breaker.py    → data/jarvis_circuit_state.db
drawdown.py           → data/jarvis_drawdown_state.db
self_learning.py      → data/jarvis_history.db
realtime_optimizer.py → data/jarvis_history.db
```

**Never use a bare relative filename for persistence.** A relative path resolves
against the process CWD, so the same code wrote to different files depending on
the launch directory — the learning engine could read a DB the live loop never
wrote to, and the circuit breaker could "forget" it had tripped.

Override the location with `JARVIS_DATA_DIR` (used by tests/CI for isolation).

---

## 9. Configuration

`jarvis.config.settings.JarvisConfig.load()` reads `config/settings.json`, then
applies `JARVIS_*` environment overrides.

**Every key in `settings.json` must be mapped in the loader.** Previously only
three of ten risk keys were read; the others silently did nothing. The mapping is
now an explicit table in `load()` — add new keys there or they are dead.

`risk.breakeven_atr_trigger` is **deliberately ignored** and warns if present:
breakeven timing is owned by `exit_policy`. Leaving a dead key with that name
invites someone to "fix" it and reintroduce premature breakeven.

---

## 10. Testing

```bash
# Fast focused suite (must pass before any commit)
python -m pytest tests/test_regression_fixes.py tests/test_ai_decision.py \
                 tests/test_integrity_fixes.py -q

# Full suite
python -m pytest tests/ -q --ignore=tests/test_india_perf.py
```

### What the regression suite pins
- `test_e1_*` — the monitor must **not** re-declare exit thresholds, and its
  adapter must produce byte-identical stops to `evaluate_exit`.
- `test_position_monitor.py` — BUY/SELL ratchet: no tightening before +2R, a real
  profit lock beyond +2R, one-way stop on retracement.
- `test_institutional_entry_exit.py` — SCALP/DAY_TRADING must route through the
  canonical policy, not a private stage table.
- Learning-loop R sign correctness.
- Drift protection retains ≥ 70 % of signal under a 40-loss streak.

When you change exit behaviour, expect the ratchet tests to fail first — that is
the guard working.

### Two known environmental failures (NOT regressions)
1. `test_apex_master_trader_optimization.py::...quality_gate...` — asserts Forex
   `required_win_p` should be 0.55; the gate computes `floor_win_p = 0.46` for FX
   plus a Kelly-derived term. The test's hardcoded expectation has drifted from
   the gate logic. Pre-existing; unrelated to exit/learning/path work. Needs a
   product decision on the intended Forex floor before anyone "fixes" it.
2. `test_micro_scalp_engine.py::test_strategy_bandit_micro_arms` — the sandbox's
   `safe-delete` shim raises `SystemExit(1)` on the test's own
   `os.remove("test_bandit_state.json")` (bulk-delete guard). Passes in isolation.

---

## 11. Refactor checklist for a new feature

1. Decide the layer. Only `jarvis/` counts.
2. If it affects a stop → put the arithmetic in `exit_policy.evaluate_exit`.
3. If it affects entry selection → put it in `entry_policy.evaluate_entry`, and
   keep capital protection separate from edge selection.
4. If it touches win-rate/exit geometry → it belongs in `Geometry`, not as a
   module constant, and it must be reachable by the calibrator's grid.
5. If it needs a parameter → add it to `ExitPolicy` / `SymbolProfileConfig`,
   not as a module constant in three files.
6. If it persists → use `resolve_db_path`, **and honour `is_offline()`**.
7. If it is configurable → add it to `config/settings.json` **and** the loader
   mapping table.
8. If it is a value passed between stages → confirm something **reads** it.
9. If it is a new entry point → add it to
   `tools/dead_code_audit.py::ENTRY_POINTS`, or the next audit reports it dead.
10. Add/extend a test. For anything touching win rate, prefer
    `tests/test_winrate_targeting.py`.
11. Run the focused suite.

---

## 12. Known remaining debt

| Item | Impact | Location |
|---|---|---|
| Per-symbol samples of 20–330 trades | Win rates carry wide confidence intervals | 3-month H1 window |
| `jarvis/india`, `jarvis/stocks` reachable from orchestrator | Scope creep; unrelated market | `jarvis/` |
| Optimiser selects on very small samples | Overfit parameters (USDJPY PF 99.0 on 3 trades) | `jarvis/intelligence/realtime_optimizer.py` |
| `master_score` has no discriminative power | Winners median 47 vs losers 48 | decision engine scoring |
| Legacy gate stack blocked 10/16 symbols entirely | Resolved for calibrated runs; legacy path remains the default when no profile is supplied | `jarvis/intelligence/gate_policy.py` |
| `SymbolProfileConfig` duplicates `symbol_registry` metadata | Two sources of truth for pip/contract values; the broker's real values disagree with both (XAGUSD pip value 50 vs 10, GER40 0.01 vs 10) | `jarvis/intelligence/symbol_profile_config.py` vs `jarvis/data/symbol_registry.py` |
| Swap/financing not modelled | Long-hold results are optimistic | backtest cost model |
| `data/signals/` cache must be regenerated after data changes | Stale cache silently calibrates on old candidates | `tools/scan_signals.py` |


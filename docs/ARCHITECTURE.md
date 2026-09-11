# JARVIS AI 4.0 — Architecture Reference

> Status: post-refactor. This document is the entry point for anyone modifying
> the system. Read it before touching execution, learning or configuration.

---

## 1. The two codebases problem (RESOLVED — read this first)

The repository historically contained **two parallel implementations**:

| Tree | Reached from | Status |
|---|---|---|
| `jarvis/` | `main.py` → `JarvisOrchestrator` (98 modules) | **LIVE — this is the system** |
| `engines/`, `core/`, `strategies/` | only `core/jarvis_supervisor.py` + some tests | **DEAD CODE** |

`core/jarvis_supervisor.py` is imported by **nothing**. It is the only thing that
imports `engines/`. A static import walk from `main.py` confirms that the live
system never touches `engines/`, `core/`, or `strategies/`.

**Rule for all future work: modify `jarvis/` only.** The `engines/` tree is
retained purely because a handful of legacy tests import it. Treat any edit to
`engines/` as having no effect on production behaviour. If you need to delete it,
first port `tests/test_system_production.py` and `tests/test_risk_engine.py`.

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
| Meta-labeling | `jarvis/intelligence/*` | confidence calibration |

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

---

## 6. Persistence & paths

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

## 7. Configuration

`jarvis.config.settings.JarvisConfig.load()` reads `config/settings.json`, then
applies `JARVIS_*` environment overrides.

**Every key in `settings.json` must be mapped in the loader.** Previously only
three of ten risk keys were read; the others silently did nothing. The mapping is
now an explicit table in `load()` — add new keys there or they are dead.

`risk.breakeven_atr_trigger` is **deliberately ignored** and warns if present:
breakeven timing is owned by `exit_policy`. Leaving a dead key with that name
invites someone to "fix" it and reintroduce premature breakeven.

---

## 8. Testing

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
- Learning-loop R sign correctness.
- Drift protection retains ≥ 70 % of signal under a 40-loss streak.

When you change exit behaviour, expect `test_e1_*` to be the first to fail — that
is the guard working.

---

## 9. Refactor checklist for a new feature

1. Decide the layer. Only `jarvis/` counts.
2. If it affects a stop → put the arithmetic in `exit_policy.evaluate_exit`.
3. If it needs a parameter → add it to `ExitPolicy` / `SymbolProfileConfig`,
   not as a module constant in three files.
4. If it persists → use `resolve_db_path`.
5. If it is configurable → add it to `config/settings.json` **and** the loader
   mapping table.
6. If it is a value passed between stages → confirm something **reads** it.
7. Add/extend a test in `tests/test_regression_fixes.py`.
8. Run the focused suite.

---

## 10. Known remaining debt

| Item | Impact | Location |
|---|---|---|
| `engines/` + `core/` dead tree | Confusion; edits have no effect | repo root |
| `tests/test_system_production.py` and `tests/test_risk_engine.py` still import `engines/` | Blocks deletion | `tests/` |
| `jarvis/india`, `jarvis/stocks` reachable from orchestrator | Scope creep; unrelated market | `jarvis/` |
| Optimiser selects on very small samples | Overfit parameters (USDJPY PF 99.0 on 3 trades) | `jarvis/intelligence/realtime_optimizer.py` |
| `master_score` has no discriminative power | Winners median 47 vs losers 48 | decision engine scoring |
| `BREAKOUT_EXPANSION` regime 27.8 % WR / `WEAK_TREND` 14.3 % WR | Entry-side problem, not exit | regime routing |

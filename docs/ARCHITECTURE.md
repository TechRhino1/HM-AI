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
10. **`trail_atr = None` means "no trail" — and it must survive to `to_policy`.**
   Both `Geometry.to_policy` and `ExitGeometry.to_policy` implement that by pushing
   `trail_activation_r` to `1e9`. `BacktestEngine` used to write
   `getattr(geom, "trail_atr", 1.5) or 1.5`, which coerces `None` into a live
   1.5×ATR trail, so the engine traded a different exit schedule from the one the
   calibrator measured its out-of-sample expectancy on — the exact mismatch the
   shared-schedule design exists to prevent. It is dormant below `tp_r = 2.0`
   because the trail only engages at `trail_activation_r = 2.0`, which a 1.5R
   trade never reaches; at and above 2.0 it was severe (NAS100: calibrator
   predicted 44.2% WR / +0.125R, engine realised 23.3% WR / −0.246R). Never
   substitute a default for `None` on the way to `to_policy`.
   Pinned by `tests/test_winrate_targeting.py::test_engine_and_simulator_resolve_the_same_exit_policy`.
11. **A pile-up on the grid boundary is not evidence the optimum lies beyond it.**
   When 10 of 16 symbols chose `tp_r = 1.5` (the widest coarse-grid value), the
   obvious inference was that the boundary was binding and should be widened.
   Tested via `--wide-grid` (to 2.5): the result was **worse by 5.46 R**, and the
   pile-up simply **moved** — the same 10 symbols pinned at the new boundary. The
   pile-up reflects an in-sample selection criterion that is monotone in `tp_r`,
   not an optimum just outside the grid. Extending a grid relocates the artefact.
   Keep the coarse grid capped at 1.5.
12. **Every cost the engine charges, the simulator must charge — and the argument
   must actually arrive.** `BacktestEngine` slips every protective-stop fill by
   `slippage_pips * pip_size` (`actual_slippage_delta`) and fills targets at
   exactly `tp`; the simulator must match on both branches. It did not:
   `simulate_all_candidates` accepted `slippage_price_equiv` and never forwarded
   it to `simulate_trade`, and `calibrate_symbol` computed
   `slip = slippage_pips * pip_size` and dropped it — that line was the only
   reference to `slip` in the module. A **1000-pip** argument left every outcome
   bit-identical, so the parameter was provably inert, and the calibration
   charged no stop slippage at all. Two tools (`tools/sweep_tp_r.py`,
   `tools/diag_engine_vs_sim.py`) had been passing the argument correctly and
   silently getting nothing. Measured cost of the omission: **−114.31 R over
   18,998 trades** (−0.00602 R/trade, 54.4 % stop exits), worth **−3.23 R** on the
   aggregate out-of-sample sample (−28.64 R → −31.87 R) with **no** geometry
   changing — a pure accounting error. This is the same class as Rule 10: the
   leaf function was correct and only the plumbing to it was wrong. It also
   poisons any feature attribution, because slippage is a fixed *pip* distance
   and so costs `slip / risk_dist` in R — it falls as the stop widens, making
   `risk_dist` and `atr` look like entry edges (rho +0.20 and +0.19) when ~80 %
   of that is the cost term (`corr(delta, 1/risk_dist) = −0.40`; both collapse to
   ~+0.04 at zero slippage). Pinned by
   `tests/test_winrate_targeting.py::test_simulate_all_candidates_forwards_slippage`,
   `::test_evaluate_geometry_forwards_slippage`,
   `::test_aggregator_slippage_never_touches_target_fills` and
   `::test_calibrator_charges_stop_slippage`.
13. **A profile file must record the settings that produced it.** The reachability
   guard makes the same target calibrate very differently at margin 0.0 and 0.17,
   so a deployed file that does not record `enforce_reachable_target`,
   `reachability_margin`, `min_tp_r_floor`, `wide_grid` and `slippage_pips` cannot
   be traced back to its own provenance or reproduced. All five are written to the
   profile meta.

14. **A trade style is a timeframe map — and only a timeframe map.** `SWING`,
   `DAY_TRADING` and `SCALP` differ in nothing else: they pick which timeframe
   fills each of the five roles (`macro`, `context`, `primary`, `setup`,
   `timing`). The map lives once, in `jarvis.market.data_feed.STYLE_TIMEFRAMES`,
   and both the live feed (`fetch_multi_timeframe`) and the backtest engine read
   it from there. It was previously inlined inside the live fetch, which meant a
   backtest could silently test a different mode than the one that ships. Because
   the style is *only* a timeframe map, `BacktestEngine` cannot express it by
   swapping a flag: the caller must pass the mode's primary series as `df_h1`
   **and** the real per-timeframe frames as `mtf_source`, or the engine will
   resample H4/D1 off the primary and quietly test the wrong mode.

15. **Higher-timeframe context must be sliced from completed bars only.** A bar
   is visible to a decision at `bar_start + timeframe_duration`, never at
   `bar_start`. The legacy resample path selected `time <= bar_time`, which lets
   a partially-formed H4/D1 bar contribute its future close — lookahead that
   inflates results. The style-aware path (`BacktestEngine._prepare_mtf` /
   `_slice_mtf`) therefore indexes each frame by its *close* time and
   binary-searches it, so only closed bars are ever visible. It is also the only
   formulation that stays fast: a boolean-mask slice per bar is O(bars × rows),
   which is quadratic and unusable at M5 scale (~35 k bars).

### Broker history depth is not uniform — and it caps what can be backtested

Measured against the live feed (XMGlobal-MT5, 2026-09):

| Timeframe | Real history available | Rows over that span |
|---|---|---|
| M1 | **~67–69 days** (hard date floor at 2026-07-06) | ~67 000–99 000 |
| M5 / M15 / H1 / H4 / D1 | full ~180–183 days | 34 000 / 12 000 / 3 100 / 780 / 130 |

Two operational consequences, both encoded in the fetcher:

  * **A range query that reaches past the stored history fails outright** with
    `(-2, 'Terminal: Invalid params')` rather than returning a truncated series.
    `MT5HistoryFetcher.fetch_bars` therefore binary-searches the largest window
    the broker will serve instead of assuming the requested one exists. Asking
    for 183 days of M1 silently yields 67 days *of real data*, never padding.
  * **`copy_rates_from_pos` caps at 50 000 bars.** Anything larger also returns
    `Invalid params`, so the positional fallback must not exceed it.

The practical limit: **SCALP cannot be backtested over six months on this feed.**
Its `timing` role is M1, so its usable window is ~67 days. `run_mode_backtest.py`
truncates SCALP's primary series to that intersection rather than letting the M5
loop run on past it — past that point the M1 frame is empty and the context
builder degrades to a NEUTRAL timing bias instead of raising, which would be a
quiet falsification of the mode under test.

### Known limitation: the entries carry no measured edge

The geometry stack is now calibrated, cost-accurate and guard-railed. It is also
solving the wrong problem. An attribution scan over all **18,998** candidates
(`tools/entry_edge_diagnostic.py`, deployed geometry held fixed so only the entry
varies) finds:

* **no numeric feature with a best-bucket expectancy meaningfully above zero.**
  The strongest gradients (`risk_dist` +0.2039, `atr` +0.1942) are ~80 % the
  slippage cost term of Rule 12 and collapse to ~+0.04 at zero slippage; their
  best quintiles are +0.0009 R and +0.0025 R, i.e. break-even. `score`, the
  primary entry filter, has rho +0.0323 and a best quintile of −0.0608 R — it
  sorts *how badly* trades lose, not how well they win.
* **no categorical feature to select into.** Every pooled value of `regime` and
  `strategy` is negative (best: `BREAKOUT` −0.0424 R, `RANGE_MEAN_REVERSION`
  −0.0547 R), and the "best" value differs on nearly every symbol. The large
  spreads (+0.50 R, +0.43 R) are dispersion among small-n categories, not an edge.
* **`meta_label_prob` is a constant `-1.0`** — the meta-label gate is inert until
  a model is trained (`decision_engine.py`), so the adaptive layer contributes
  nothing on the backtest path. The sentinel is handled safely
  (`opportunity_arbiter.py` treats `<= 0` as absent).
* **the score is not usable for sizing either.** The monotone-but-negative bucket
  ladder looked like a lead — if the score ranks *loss severity* it might still
  work as a position-size input. Tested on 1,005 non-overlapping traded positions
  with weights normalised per symbol so the risk budget is unchanged
  (`tools/score_sizing_test.py`): every rule favouring high scores makes the
  result **worse**, and the **inverted** rule is the best performer (+0.0093 R,
  p=0.32, all variants insignificant). Reversed signs mean no directional signal,
  and the ladder seen over all candidates does not survive inside the traded
  region — the only region a sizing rule operates in.

Conclusion: further geometry or threshold search is not where the remaining
variance lives. The next work is **signal research** — new information at entry
(order-flow, session, volatility regime at entry, cross-asset confirmation), not
new transforms of the existing features. See `reports/entry_edge_verdict.md`.

### Regime-conditioned optimisation (`jarvis/backtesting/regime_optimizer.py`)

The attribution scan above is a *pooled* statement: it says every regime is
negative on average. That leaves an obvious question unanswered — a regime can be
negative under one exit geometry and positive under another, and a pooled average
cannot see the difference. `regime_optimizer.py` answers it by re-running the
geometry search **inside each regime** rather than once across the window.

Three design points make it affordable and honest:

* **Conditioning is free.** `_outcomes` caches a simulation on the geometry
  alone, because a trade's result depends on the forward path and the exit
  schedule, never on the market-condition label attached afterwards. Scoring one
  geometry under six regimes therefore costs one simulation plus cheap filtering
  passes — the same "simulate once, threshold for free" trick the parent module
  uses for `min_score`, applied to a third axis.
* **Selectivity is measured inside the regime.** `min_score` is a quantile of the
  symbol's own score distribution, and the scores in COMPRESSION are not the
  scores in TREND_BULL. Resolving the quantile against the pooled distribution and
  then filtering by regime would silently rescale the selectivity while still
  reporting a "top 10%" run.
* **One blocking walk across regimes.** The engine holds at most one position per
  symbol whatever the regime is. Selecting each regime independently would book
  overlapping trades it can never take, and the phantom overlap would cluster at
  regime turns — where the P&L is. Regime-eligible candidates from every regime
  are merged and put through a single `select_sequential` call.

The deploy rule is deliberately conservative: a regime-specific geometry replaces
the pooled one **only** when it wins a like-for-like comparison (same regime, same
full window, same objective, same constraints), and the enable/disable decision is
made last, from the geometry that will actually be deployed. A regime below the
candidate gate is reported `insufficient_sample` and inherits the pooled geometry
— it is never handed a geometry fitted to a handful of bars.

The disable thresholds (`disable_margin_r=0.05`, `min_trades_to_disable=12`) are
deliberately identical to `jarvis.intelligence.winrate_targeting.regime_edge_table`
and are **re-implemented rather than imported**, because `jarvis.intelligence`
already imports `jarvis.backtesting` and §2 requires the dependency direction to
be downward. `tests/test_regime_optimizer.py::test_gates_match_winrate_targeting`
fails if the two ever drift apart, which is the only thing that makes the
duplication safe. **If one is changed, change both.**

Run it with `python tools/optimise_regime.py`. The result is served at
`GET /api/backtest/regime-policy` and rendered on the dashboard's analytics view.

**Measured result (2026-09).** Two runs: SWING over all 20 symbols at `--passes 3`, then all
three modes over 8 liquid symbols at `--passes 1`. The smaller universe is less converged, but the
direction is identical in both, so the finding does not depend on the sample choice.

| Mode | Baseline OOS total R | Policy OOS total R | Baseline trades | Policy trades | Verdict |
|---|---|---|---|---|---|
| SWING (20 sym) | −122.9 | −6.4 | 816 | 20 | improves total R |
| SWING (8 sym) | −12.9 | −1.6 | 342 | 105 | improves both |
| DAY_TRADING | −127.9 | −21.8 | 1220 | 262 | improves both |
| SCALP | −313.4 | **0.0** | 1419 | **0** | takes no trades |

The pooled optimum is not a profitable configuration in any mode — in SWING the search finds no
feasible geometry at all and falls back to its seed (`tp 1.5`, no selectivity). The policy's value is
therefore **loss avoidance, not profit generation**: it cuts SWING's out-of-sample loss by 95 % and
DAY_TRADING's by 83 % by *refusing to trade* conditions whose expectancy is clearly negative.

The SCALP row is the clearest result in the table. All seven conditions are clearly negative **and**
every regime-specific geometry fails the constraints on the full window, so the optimiser's answer is
to take **zero trades** — eliminating a −313.4 R loss outright. This independently reproduces, by a
different method, the measured mode-reliability ordering (`mode_aggregator`: SWING 0.346,
SCALP 0.1287, DAY_TRADING 0.1064 — all below neutral): SWING is the least-bad mode and SCALP is not
tradeable on this window.

No regime's own geometry generalises out-of-sample in any mode. This **independently confirms the
attribution finding above** by a different method, and it is the honest answer to "maximise profit in
any condition" on this data: the achievable maximum is bounded by the data. Re-run whenever new data
lands — the module reports "no edge" as a result, not a failure.

The 90.6 % simulation cache-hit rate across the run is the evidence that the "conditioning is free"
design holds in practice.

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
- `test_symbol_registry.py::test_broker_symbol_resolves_like_its_canonical_name` —
  the **broker** symbol (what the engine passes to `resolve()`) must give the same
  spec as its canonical name. The pre-existing manifest test only checked the
  canonical name, which is always a registry key, so it could not see that
  `OILCash#` was falling through to the generic FX spec.
- `test_regime_optimizer.py::test_policy_blocks_across_regimes_not_within` — one
  position at a time must hold across regimes, not within each.
- `test_regime_optimizer.py::test_gates_match_winrate_targeting` — the
  re-implemented disable thresholds must not drift from the calibrator's.
- `test_ui_wiring.py` — the UI↔API contract, now 24 tests. Three defects that
  returned HTTP 200 and rendered a blank or frozen panel, so no HTTP check could
  see them: the candles call sending `timeframe=` while the handler read `tf=`
  (the timeframe selector was inert); `renderPositions` reading `price_open` /
  `price_current` when the schema serialises `open_price` / `current_price` (the
  Entry and Now columns were permanently dashes); and the controller querying
  element ids the template need not define. It also pins the six-view rail (a tab
  needs a button, a panel and a `VIEWS` entry — miss one and it renders but does
  nothing), that the news countdown is derived locally rather than read from the
  server's stale `status_badge`, and that every response returning modelled values
  declares it. Mutation-tested: reverting either fix fails the suite.
- `tools/verify_dashboard_render.js` — runs the real `dashboard.js` in a Node
  `vm` against a stubbed DOM and a recording chart library, then asserts on what
  the module asked the library to draw. This is the only layer that can see the
  chart, because the chart is built at runtime: it proves the swing pivots
  become R1/R2/S1/S2 price lines, that the volume series is populated, that the
  open position's entry/stop/target lines carry the trade details, and that the
  in-chart HUD is filled. 88 checks, including the TradingView switch's lazy load
  and its explicit failure state.
- `test_provider_recursion.py` — `fetch_quotes()` must not call the profile
  hydrators. It used to, and hydration calls back into `fetch_quotes()`, so the
  two recurred without bound. Nothing raised, because `hydrate_batch` wraps that
  call in `except Exception` and `RecursionError` is an `Exception`. See §13.
- `test_synthetic_determinism.py` / `test_candle_seed_stability.py` — modelled
  values must be a function of the instrument, not of the process. Both
  generators seeded from `hash()`, which CPython salts per interpreter start, so
  a symbol's earnings date, F&O-ban status and candle history all changed on
  restart. These spawn subprocesses under different `PYTHONHASHSEED` values,
  because salting is constant *within* one process — an in-process test passes
  against the broken code and pins nothing.
- Learning-loop R sign correctness.
- Drift protection retains ≥ 70 % of signal under a 40-loss streak.

### The bar for a new test

**A test must be shown to fail against the code it is meant to catch.** Three
tests written this cycle passed against the broken code on first attempt and had
to be rewritten:

| Test | Why it passed while broken |
|---|---|
| "expect no `RecursionError`" | An intermediate frame swallowed it (`except Exception`) |
| re-entry recorded via a cache | A sibling test had warmed the 15s quote cache, so the fallback was never reached |
| "same result twice in one process" | `hash()` salting is constant within a process |

The reliable way to check is to revert the fix with the editor, run the test, and
restore it — **not** `git stash`, which is implicated in this repo's data-loss
incidents (see the project memory file). For source-level checks, prefer parsing
(`ast`) over substring search: the fixes carry comments naming the very
identifiers a grep would flag.

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
| `jarvis/intelligence/signal_engine.py` is orphaned — 545 lines, zero references anywhere | Its regime-adaptive evidence weights (`REGIME_WEIGHTS`, `weights_for_regime`) are **not** wired into `DecisionEngine`, which has no evidence weighting at all. It is a third, unused approach to regime adaptation alongside `realtime_optimizer.get_adjustments(symbol, regime)` (DB-driven P&L adjustment, live) and `regime_optimizer.py` (per-regime exit geometry). Either wire it or delete it — leaving it invites someone to assume it runs | `jarvis/intelligence/signal_engine.py` |
| A full 3-mode regime sweep over all 20 symbols takes hours | `--passes 3` over M15/M5 means ~200 geometry evaluations per search and 8 searches per mode, each simulating 182k–209k candidates. Budget `--passes 1` and a reduced `--symbols` list for an interactive run | `tools/optimise_regime.py` |
| `_static_market_values` only catches bare numeric literals | A fabricated value written as a formatted string (`"4,380.00"` built in JS) is invisible to it. The check is a strong net, not a proof | `tools/verify_ui_live.py` |
| The India option chain's `iv_rank` is a random draw | It gates the `iv_rank < 50` branch in `options_signal_engine`, so *which* branch runs is arbitrary. Deliberately left in place, because removing the draw changes the branch rather than fixing it; the UI labels it "IV rank (modelled)" and the live verifier checks the chain declares itself modelled. It should eventually be computed from the chain's own implied vols | `jarvis/india/options_engine.py` |
| A provider route's own timeout is not bounded | The recursion bug that made three endpoints hang is fixed, but a route that genuinely stalls still pins a `ThreadingHTTPServer` thread. `tools/verify_ui_live.py` now gives provider routes a 60s client budget and reports a hang as a named failure instead of crashing | `jarvis/api/server.py` |
| `tools/verify_dashboard_render.js` stubs the DOM | It proves the controller issues the right drawing calls and queries only ids the template defines; it cannot prove the result *looks* right. No browser is installed in this environment, so layout, overlap and colour contrast remain unverified by machine | `tools/verify_dashboard_render.js` |

---

## 13. The dashboard UI contract

The dashboard (`jarvis/ui/templates/dashboard.html` + `static/js/dashboard.js`,
served at `/`, `/dashboard`) is a six-view terminal — Trade, News, Analyst,
Markets, Analytics, Backtest — sitting on the `hm_ui.css` token set with
`theme_terminal.css` as its component layer. `/console` and `/classic` still
serve the older surfaces.

A view is reachable only if three things agree: the rail has a
`data-view-btn="x"`, the template has a `data-view-panel="x"` and an
`id="view-x"`, and the controller's `VIEWS` array accepts the name. Any one of
them missing leaves the tab either absent or **inert** — and inert is the
dangerous case, because the tab renders, the click does nothing, and nothing
raises. `tests/test_ui_wiring.py` asserts all three for every view.

### The one rule

**No element carries a market value.** Every price, P&L, count and timestamp is
written by `dashboard.js` from an API response; where a value is unknown the
element shows a dash or an explicit state. A trading UI that renders a
plausible-looking price when its feed is down is worse than one that renders
nothing, because the fabricated value is indistinguishable from a real one.
`tools/verify_ui_live.py` scans every template on disk for bare numeric literals
and fails if one appears.

### The provenance rule

The rule above catches fabricated values **in the markup**. It cannot catch a
fabricated value **arriving from the API**, because such a value is a perfectly
ordinary JSON number by the time the UI sees it. That gap is closed at the
boundary instead:

> A response that returns modelled, fixed or placeholder values must say so in
> its own payload. A UI cannot label what the backend does not describe.

The vocabulary is the one already in use — `live`, `calibrated_feed`,
`synthetic`, `synthetic_fallback`, `profile_reference`, `sample`, `mixed`,
`unknown` (see `gamma_exposure.py` and `india_engine.py` for the precedent).
Each panel renders its source as a chip via `renderProvChip()`, and a panel
reports the **weakest** source among its rows rather than the majority one: if a
single row is a static reference price, the panel is not wholly live, and
labelling it "live" because most rows were would be exactly the mistake the
markers exist to prevent (`weakestSource()`).

Three consequences worth keeping:

* **A failed analysis is not a setup.** `stock_service.py` marks a row whose
  analysis failed with `analysis_source: "fallback"` and `data_source:
  "profile_reference"`; the renderer then suppresses the grade, probability,
  bias, entry, stop and target entirely and marks the price as a reference. A
  screener that invents a "GRADE B" breakout for a symbol it could not analyse
  is worse than one that says the symbol was not analysed.
* **A number that picks a direction is labelled.** The India options chain's
  `iv_rank` is drawn from a local RNG and gates the `iv_rank < 50` branch in
  `options_signal_engine`, so the UI labels it "IV rank (modelled)" rather than
  presenting it as measured. The value is not removed — removing it would change
  which branch runs — it is *described*.
* **Only what is computed is shown.** The India heatmap deliberately has no
  `avg_cmf` tile even though the US heatmap does: the India engine computes no
  money-flow index, so a tile would be invented. The asymmetry is intentional and
  `tools/verify_ui_live.py` asserts it.

### The chart

Built at runtime against the vendored `lightweight-charts` v4, which means a
missing container or an unloaded library is silent — hence
`tools/verify_dashboard_render.js`, which drives the real controller against a
stubbed DOM and asserts on the drawing calls it makes.

Four properties are load-bearing:

1. **Live ticks update, they do not reload.** The first paint calls `setData()`;
   later polls call `update()` on the forming bar only. `setData()` on every poll
   resets the viewport and throws the user out of wherever they had scrolled to.
2. **Levels are derived, never invented.** `computeLevels()` finds swing pivots —
   a bar whose high exceeds the two bars either side — and takes the two nearest
   above the last close as R1/R2 and the two nearest below as S1/S2. Where a side
   has no pivot that level is *absent*: `null`, no line drawn, and the legend
   reads "No swing pivot in range". A synthesised level reads as a real price.
3. **Overlays are the trade.** Entry, stop and target are price lines whose axis
   labels carry side, size and price, with a floating HUD repeating them and the
   live P&L. A stop that has moved past entry is drawn amber and labelled
   "locked" — a stop in profit is a different fact from a stop at risk.
4. **The chart and the table are the same data.** Candle precision comes from
   `priceDigits()`, which mirrors the backend's per-symbol resolution, so a price
   read off the axis and the same price read off a table row agree digit for
   digit.

### Switching the chart to TradingView

A segmented control (`#chart-src-native`, `#chart-src-tv`) swaps the vendored
chart for an embedded TradingView widget. Three properties:

1. **The external script is not fetched until it is asked for.** `tv.js` is
   loaded lazily by `ensureTvScript()`; the render harness asserts it is not
   requested on page load, because a third-party script is a network dependency
   and a privacy consideration the user did not opt into by opening the page.
2. **A blocked CDN fails loudly.** `ensureTvScript()` resolves `false` on error
   or timeout, the panel renders an explicit failure state naming the native
   chart as the fallback, and a later click retries. An empty box where a chart
   should be is indistinguishable from a chart that has not loaded yet.
3. **The symbol mapping is visible.** `tradingViewSymbol()` maps the internal
   symbol to an exchange-qualified ticker (`XAUUSD` → `OANDA:XAUUSD`, `NIFTY` →
   `NSE:NIFTY`, and so on). The resolved ticker is printed next to the switch, so
   a wrong mapping is a visible fact rather than a chart showing the wrong
   instrument.

### The news calendar

`/api/news` returns each event with both a `diff_seconds` and a pre-rendered
`status_badge` string ("IN 14h 33m"), computed at generation time. **The badge is
not rendered.** It is stale the moment it arrives, so the countdown is derived
locally instead: each row is anchored to its own `timestamp_iso`, corrected for
clock skew against the payload's `timestamp`, and the backend's own live window
(−5 min / +15 min, `NEWS_LIVE_BEFORE` / `NEWS_LIVE_AFTER`) is applied client-side.
A row therefore flips live → released while the page is open rather than holding
the payload's snapshot. `tools/verify_ui_live.py` asserts the server's
`diff_seconds` agrees in sign with its own `timestamp_iso`, which is what makes
deriving it locally safe.

### Restored surfaces

The redesign had dropped three surfaces the previous terminal had. All three are
back, each bound to a real endpoint and each with an honest empty state:

| Surface | Source | Empty state |
|---|---|---|
| Support/resistance + trade overlays | computed from the candle series | "No swing pivot in range" |
| Scanner radar | `telemetry.radar_opportunities` | "No scan published" |
| Pending orders | `GET /api/pending_orders` | "No working orders" |

The radar's accent comes from `is_actionable` on the candidate, so a row the
engine would not trade renders without it and the list never implies that
everything in it is tradeable. MT5 reports a pending order's type as a numeric
enum; `pendingTypeName()` maps it to a name, because "2" under a column headed
Type tells the user nothing.

### The devil's advocate panel

The Analyst view renders `DecisionObject`'s adversarial half — the penalty, the
bull and bear cases, the risk factors, the invalidation levels, the quality-gate
checks and the objections. Two properties:

1. **An empty list is rendered as empty.** `daList()` writes "the engine returned
   none" rather than an all-clear. A panel that renders nothing when it has no
   evidence looks identical to a panel that has considered the evidence and found
   it clean.
2. **Failures sort first.** `renderQualityGate()` orders the failing checks above
   the passing ones, so the reason a decision was refused is at the top of the
   list rather than below thirty green rows.


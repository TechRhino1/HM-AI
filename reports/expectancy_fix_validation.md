# Expectancy Fix — Validation Report

**Date:** 2026-09-11 (last updated 2026-09-11, post Fix A + Fix B)
**Trigger:** User chose "fix the negative expectancy problem" (option C) before enabling live trading.
**Standing safety rule:** **Do NOT enable live trading.** No validated positive-expectancy portfolio exists yet (see §6–§7).

---

## 1. What the OOS edge-refusal gate does (and that it works)

`evaluate_entry` refuses a symbol whose **purged out-of-sample** expectancy is non-positive
(`oos_trades >= 10 and oos_expectancy_r <= 0`). This is a capital-protection-grade refusal: it can
only prevent trading a proven loser, never add risk. A thin OOS sample falls through to the normal
edge gate.

Regression tests added to `tests/test_winrate_targeting.py`:
`test_oos_negative_expectancy_refused`, `test_oos_positive_expectancy_allowed_past_gate`,
`test_oos_negative_but_thin_sample_not_refused`.

In the original re-run, the gate correctly drove 6 symbols to **0 trades** (they were previously
trading with negative expectancy): EURUSD, SOLUSD, USDCHF, USDJPY, NZDUSD, US30.

## 2. The backtest result: portfolio was STILL NEGATIVE (original finding)

Original re-run portfolio (post-gate): **expectancy −0.0197 R, profit factor 0.94, net −$881.71**
over 1,253 trades / 16 symbols. The gate removed the worst offenders, but most symbols whose
calibration OOS expectancy is positive were negative in the engine's actual backtest — the
calibration's walk-forward OOS estimate was essentially uncorrelated with what the engine realized.

## 3. Root cause (the original framing)

The divergence is in how a candidate becomes a realized trade:
- Calibration's OOS expectancy is produced by `jarvis.backtesting.trade_simulator`.
- The engine's expectancy is produced by `jarvis.backtesting.engine.BacktestEngine`.

The systematic sign flip (almost every positive-OOS symbol turns negative in the engine) indicated
the **simulator was more optimistic than the engine** — a trade-resolution / cost-model mismatch.

## 4. Projection of a second ("refuse engine-negative") gate

If we additionally refused every symbol whose **engine** backtest expectancy ≤ 0, only 3 symbols
survive (AUDUSD, GBPUSD, XAUUSD) — projected expectancy **+0.049 R over 134 trades**. That is a tiny,
undiversified, and fragile system, tuned on the same 95-day window (overfitting risk). A band-aid,
not a fix.

## 4b. Alignment attempt — cost + double-hit (2026-09-11, PARTIALLY VALID)

Genuine, still-correct changes made:
- `trade_simulator.simulate_trade` now models **stop slippage** (`slippage_price_equiv`, applied to
  every protective-stop fill, mirroring `BacktestEngine.actual_slippage_delta = 0.5·pip_size`).
  Breakeven scratches in the simulator are now small losses, as in the engine.
- `WRTargetCalibrator` gained `slippage_pips=0.5`; calibration passes it through.
- `BacktestEngine`'s double-hit intrabar resolution was changed to the simulator's conservative
  **stop-first** convention (resolves to SL, never TP, when a bar spans both). Fixes the project's
  own documented bug (optimistic double-hit inflated win rate).
- 3 regression tests added for slippage modelling; 45/45 pass.

**Re-ran `tools/run_3month_backtest.py` with the new profiles. Result: PORTFOLIO STILL NEGATIVE —
expectancy −0.032 R, PF 0.87, net −$1,547, 925 trades.** A second, larger divergence remained
(NAS100 +0.063 OOS vs **−0.094** engine; GER40 +0.019 vs **−0.085**; SOLUSD +0.091 vs **−0.069**).

> **CORRECTION (this update):** The prior version of this section claimed the simulator "models only
> pure SL/TP-with-slippage" and that the divergence was the missing exit policy / cooldown / costs.
> **That claim is false and has been superseded by code reading + a head-to-head diagnostic.**
> The simulator (`trade_simulator.py:55,270,353`) **already imports and calls `evaluate_exit` +
> `geom.to_policy`** — breakeven lock, partial scale-out, and ATR trail are already modelled. The
> real bugs were two specific defects (§4c), not a missing policy.

## 4c. THE ACTUAL ROOT CAUSE — two concrete bugs (2026-09-11)

A head-to-head diagnostic (`tools/diag_engine_vs_sim.py`, engine vs simulator on the same H1_95d
data + same profile) proved the divergence is **not** a missing exit policy, cooldown, or cost.
It is two specific defects:

### Bug A — off-by-one "phantom target" hits in `trade_simulator.simulate_trade` (MASTER bug)
`signal_scan` fills the entry at the **next bar's open** (`fill`), so the trade is not live until
`entry_idx + 1`. But `simulate_trade` started its loop at `entry_idx` (the **signal bar**) and
tested that bar for SL/TP. For tight `tp_r` the signal bar frequently hits the target *before* the
real entry exists → **phantom TPs** that the live engine can never take. This inflated the
simulator's win rate.

Diagnostic proof (before fix, NAS100): simulator WR **87.66%** vs engine **65.91%**; walk-overlap
Jaccard **0.131**; sim TP count **135** vs engine **42**.

**Fix A applied** (`trade_simulator.py`): loop now starts at `entry_idx + 1`
(`entry_time = bars.time[entry_idx + 1]` / `for j in range(entry_idx + 1, n):`). After the fix,
NAS100 sim WR dropped to **76.27%** and now matches the engine's **76.60%**; walk-overlap Jaccard
rose to **0.48**; expectancy engine **−0.0374R** vs sim **−0.0396R** (agree to 0.002R).

### Bug B — engine applied UNVALIDATED per-regime geometries
`engine.py` executed `geom = wr_profile.geometry_for(regime_name)`, which applies per-regime
overrides the calibrator **never measured as an ensemble** (e.g. NAS100 TREND_BULL `tp_r=1.5` vs
base `tp_r=0.25`, BREAKOUT `tp_r=0.6`, TREND_BEAR `tp_r=0.4`). The calibrator's OOS is measured on
the **validated base geometry**, so the engine was executing geometries the OOS did not validate.

**Fix B applied** (`engine.py:512`): `geom = wr_profile.geometry` — the engine now executes the
validated base geometry. Verified: NAS100 engine WR is now **76.60%** (identical to the earlier
`--probe` base-geometry run), confirming the per-regime override is no longer applied.

### Why this is the faithful realization of "calibrate on engine backtest"
The user approved making OOS expectancy match the engine by construction. Fix A makes the simulator
count only real post-entry bars; Fix B makes the engine use the validated geometry. Together they
make **OOS ≈ engine per-trade edge by construction** — without the infeasible per-geometry engine
call the original §5 proposal envisioned. The big refactor is **not** required.

## 5. Re-validation after Fix A + Fix B (2026-09-11)

### Step 1 — re-calibrate with the fixed simulator
`tools/calibrate_winrate.py --folds 4` (16 symbols, walk-forward). With honest simulation the
buggy `tp_r=0.25`-needs->80%-WR selection is gone. Result: **only 4/16 symbols meet the 75% target
out-of-sample** (NAS100 +0.063R/PF1.32, UK100 +0.037R/PF1.18, XAUUSD +0.025R/PF1.12, AUDUSD
+0.009R/PF1.05). Aggregate OOS is now **honestly negative (−43.6R)** — the buggy calibration was
hiding this.

### Step 2 — full-sample engine backtest (ground truth for live)
`tools/run_3month_backtest.py` (16 symbols, new profiles, OOS gate on, base geometry).
The OOS gate correctly refuses the clearly-negative symbols → **0 trades**: BTCUSD, ETHUSD, EURUSD,
GBPUSD, GER40, SOLUSD, US30, USDCAD, USDCHF, XAGUSD.

Only 6 symbols trade:

| Symbol | Engine trades | WR | Engine exp (R) | PF | net $ | OOS gate admitted? |
|---|---:|---:|---:|---:|---:|---|
| NAS100 | 107 | 77.6% | **+0.018** | 1.04 | +45.32 | yes (OOS +0.063) |
| XAUUSD | 84 | 77.4% | **+0.008** | 1.09 | +87.32 | yes (OOS +0.025) |
| UK100 | 26 | 76.9% | **−0.045** | 1.22 | +67.87 | yes (OOS +0.037) |
| AUDUSD | 149 | 77.8% | **−0.027** | 0.85 | −184.60 | yes (OOS +0.009) |
| NZDUSD | 61 | 67.2% | **−0.067** | 0.80 | −192.18 | yes (OOS +0.002) |
| USDJPY | 63 | 69.8% | **−0.178** | 0.75 | −224.01 | yes (OOS +0.033) |

**PORTFOLIO: 490 trades | WR 75.3% | expectancy −0.037R | PF 0.93 | net −$400.28 | maxDD 7.09%**
(uniqueness-weighted expectancy: −0.3156R).

### Step 3 — the RESIDUAL divergence (what remains)
The catastrophic 87%→76% WR inflation is **gone**; per-trade edge now agrees between engine and
simulator (§4c). But a **smaller residual** remains: 4 symbols that passed the walk-forward OOS
gate (AUDUSD/NZDUSD/USDJPY/UK100, OOS barely > 0) turned **negative** in the engine's full-sample
backtest. Causes:
1. **Walk-forward fold luck** — the geometry is selected to maximize OOS on a particular fold split;
   thin edges (OOS ≈ 0) flip sign on the full sample.
2. **Candidate path mismatch** — the calibrator scores a *cached* `signal_scan` set with
   threshold-only `select_sequential`; the engine generates candidates **live** via
   `decision_engine.evaluate` (engine.py:429) with regime policy + fractional-Kelly sizing + the
   OOS gate. The engine trades a somewhat different (larger) set (NAS100: 141 vs 118), and the
   extra trades are net losers, pushing the engine below the OOS estimate.

This residual is exactly the user-approved **"calibrate on engine backtest"** item: make the
calibrator measure expectancy on the engine's actual execution path so the OOS gate validates
against what the engine really does.

## 6. Conclusion & recommendation (2026-09-11, post-fix)

**The two catastrophic bugs are fixed and verified.** The calibration is now **honest** — it no
longer reports a phantom +30R aggregate. The OOS edge-refusal gate works and now refuses ~10
clearly-negative symbols.

**However, the portfolio is still negative** (expectancy −0.037R, PF 0.93) on the 95-day real data.
After honest accounting, only **NAS100 (+0.018R)** and **XAUUSD (+0.008R)** have a stable positive
engine backtest; the other symbols that passed the OOS gate are thin edges that flip negative under
the engine's live path. The genuine edge on this dataset is **fragile / non-robust**, not the
strong edge the buggy calibration implied.

**Do NOT enable live trading.** There is no validated positive-expectancy, diversified portfolio.

### Recommended next step (user-approved direction, remaining item)
Close the residual divergence by making the calibrator's OOS measurement use the engine's real
execution path (candidate generation + cooldown + admission + costs), so the OOS gate refuses
exactly the symbols the engine actually loses on. Two implementation options:
- **(A) Engine-backed calibrator** — replace `simulate_all_candidates` in the geometry/threshold
  search with actual `BacktestEngine` runs. Most faithful; currently expensive (engine ≈ 22 s/symbol,
  so the grid needs a fast/vectorized engine or coarse search).
- **(B) Engine-full-sample-backed OOS gate** — keep the lightweight sim for geometry search but set
  the gate's refusal criterion from the engine's *realized full-sample* expectancy (which we already
  compute). Lighter; refuses AUDUSD/NZDUSD/USDJPY/UK100 and leaves NAS100+XAUUSD (positive but tiny
  & undiversified — still not a tradeable live system without more edge).

Either way, the immediate, safe outcome is: **the system is confirmed not-ready, and stays in
paper/simulation.** Before any live consideration we need either a genuinely positive, diversified
engine backtest (expectancy > 0, PF > 1 across multiple symbols) or a longer/cleaner dataset.

See `config/winrate_profiles.PRE_FIX.json` (buggy pre-fix profiles, A/B baseline) and
`reports/backtest_after_fix.log` / `reports/diag_after_fix.txt` / `reports/calibrate_after_fix.log`
(current run artifacts).

---

## 7. Per-symbol root-cause analysis (2026-09-11)

### 7.1 The single identity that explains the negative portfolio

Portfolio realised **payoff ratio = 0.279** (avg win **0.2765R** vs avg loss **−0.9917R**).

        breakeven win rate = 1 / (1 + payoff) = 1 / 1.279 = 78.2 %
        achieved win rate                                = 75.31 %

**The system is structurally below breakeven.** Every other symptom follows from this.

### 7.2 Why payoff is 0.279 — the win-rate target is the cause

The calibrator's objective is `target_wr = 0.75`, and `_rank_key` ranked Tier 1
(target missed, positive expectancy) and Tier 0 by **win rate**. The 75% target is
missed far more often than it is met, so the calibrator almost always landed in
those tiers and therefore almost always selected the **tightest `tp_r` on the grid
(0.25) — chosen for 13 of 16 symbols**.

Tiny `tp_r` ⇒ tiny payoff ⇒ breakeven win rate 76.9–80% ⇒ no margin at all:

| Symbol | tp_r | WR | Breakeven WR | Margin | Engine exp (R) |
|---|---:|---:|---:|---:|---:|
| AUDUSD | 0.25 | 77.85 | 80.00 | **−2.15** | −0.027 |
| NZDUSD | 0.40 | 67.21 | 71.43 | **−4.22** | −0.067 |
| NAS100 | 0.30 | 77.57 | 76.92 | +0.65 | +0.018 |
| XAUUSD | 0.30 | 77.38 | 76.92 | +0.46 | +0.008 |
| USDJPY | 0.60 | 69.84 | 62.50 | +7.34 | **−0.178** |

USDJPY is the tell: margin is +7.34 yet expectancy is −0.178, because its *realised*
avg win is only ~0.18R, not 0.6R — winners are cut far below target by time-stops.

### 7.3 Tooling

`tools/sweep_tp_r.py` (new) sweeps `tp_r` per symbol and reports WR, realised payoff,
breakeven WR, **margin**, expectancy, PF and the exit-reason mix, plus what each
objective would pick. Output: `reports/sweep_tp_r.txt`.

Note `tp_r` is expressed in R, so it is **already volatility-adaptive** (R scales with
the ATR-derived stop). Volatility adaptation is therefore not a separate gap.

## 8. Optimization attempts — measured, and why they did not work

Three objectives were A/B tested end-to-end (calibrate → engine backtest):

| Variant | Expectancy | PF | Net |
|---|---:|---:|---:|
| Baseline (win-rate chasing) | −0.037 R | 0.93 | −$400 |
| Expectancy-max + margin guard | −0.089 R | 0.84 | −$692 |
| t-statistic of expectancy | −0.065 R | 0.82 | −$1,063 |

**None is a fix, and the reason is not the objective.** Two measured effects dominate:

1. **The walk-forward OOS does not predict the engine.** OOS→engine gaps of
   **−0.19R to −0.26R**: ETHUSD +0.053→**−0.202**, USDCAD +0.013→**−0.175**,
   UK100 +0.042→**−0.147**, NAS100 +0.001→−0.035. Only GER40 matched exactly
   (+0.130→+0.130). The OOS gate therefore admits symbols the engine then loses on.
   Optimising harder against an unfaithful instrument only selects more precisely
   for the instrument's bias.
2. **In-sample expectancy is a winner's curse.** XAUUSD's best in-sample config
   reported +0.101R and delivered −0.123R out of sample.

`tp_r = 2.0` was trialled and removed: it holds trades into the breakeven/trail region
(`trail_activation_r = 2.0`) where simulator and engine diverge violently
(NAS100: OOS 44.2% WR vs engine **23.3%**).

**All three objectives were reverted to baseline**, because baseline is the best
measured and leaving an experiment that scores worse would degrade the system.
`breakeven_win_rate()`, `expectancy_tstat()` and `min_margin_for_symbol()`
(asset-class margins: CRYPTO 0.08 / INDEX 0.06 / COMMODITY 0.05 / FOREX 0.04) are kept
as documented diagnostics — they become usable for selection **only once the OOS is
measured on the engine's own execution path**.

## 9. Reproducibility defect (found during A/B testing)

Two identical-code runs produced materially different results (−0.037R/−$400 vs
−0.063R/−$1,095), with different per-symbol trade counts (NAS100 107 vs 118,
XAUUSD 84 vs 97). `run_3month_backtest.py` does use `offline_mode()` (line 133), yet
`jarvis_bandit_state.json` and `jarvis_online_ml_weights.json` are rewritten by
backtest runs, so the engine's decisions drift between runs.

**Consequence:** differences smaller than ~0.03R / ~$700 between runs are not
statistically meaningful, and A/B validation is unreliable until this is fixed. This
must be resolved before any future tuning can be trusted.

### 9.1 Fixes applied

Two distinct causes were found and fixed:

**(a) Backtests persisted learned state.** `offline_mode()` sets a global flag, but
several persistence layers never checked it, so each backtest left state that the
*next* run loaded:

| Module | Problem | Fix |
|---|---|---|
| `learning/strategy_bandit.py` | No guard; saved on every trade; `state_file` was CWD-relative | `resolve_db_path()` anchoring + `is_offline()` guard on both load and save |
| `risk/circuit_breaker.py` | No guard; a tripped breaker / symbol pauses persisted | `is_offline()` → `db_path = ""` (the documented in-memory sentinel) |
| `risk/drawdown.py` | No guard; leftover daily/peak equity tripped the loss cap early | same in-memory sentinel |
| `learning/online_ml_predictor.py` | — | already guarded ✓ |

Verified: the state files are no longer rewritten by backtests.

**(b) Risk gating ran on wall-clock time, not bar time.** `circuit_breaker.py` computed
45/60-minute symbol/regime pauses as `time.time() + 2700` and tested them with
`time.time()`, and `risk_engine.py` expired risk reservations on a 15-second real-time
TTL. During a backtest the bars are historical, so whether a pause had expired depended
on **how fast the machine ran**, not on market time.

Fix: an injectable clock. `CircuitBreaker` gained `set_clock()` (default `time.time`),
`RiskEngine` gained `set_clock()` forwarding to it and to the reservation TTL, and
`BacktestEngine` now sets it to the current bar's epoch seconds on every bar
(`engine.py`, using the existing `bar_time`). Live behaviour is unchanged (no caller
sets a clock, so it stays the wall clock).

### 9.2 Verification

Two consecutive backtests on identical inputs now produce **identical results**
(`reports/repro2_run1.log` vs `repro2_run2.log`, compared with wall-clock timings
normalised): every per-symbol figure and the portfolio line match exactly.

    PORTFOLIO: 529 trades | WR 73.3% | expectancy -0.063R | PF 0.83 | net -$1,095.04 | maxDD 12.01%

(`reports/final_confirm.log` vs `final_confirm2.log` — two consecutive runs of the
final code, identical. Note the earlier pair, `repro2_run1/2`, was taken before the
`RiskEngine.__init__` ordering bug described below was fixed, and reported
−0.057R / −$840.73; the correct figure for the shipped code is the one above.)

A/B testing is now meaningful: any future difference between two runs is a real effect
of the change, not drift. Before this fix the same code reported anywhere between
−0.037R / −$400 and −0.063R / −$1,095.

**Defect found while fixing this:** the first version of the change placed
`def set_clock` at 4-space indent inside `RiskEngine.__init__`, which silently
terminated `__init__` early and made the remaining initialisers
(`position_sizer`, `trade_guard`, `correlation_engine`, `_reserved_risk`,
`_reserved_lock`) part of `set_clock`'s body — so `_reserved_risk` was being reset on
every bar. Caught by `tests/test_risk_engine_j3.py` and
`tests/test_adaptive_same_symbol_risk.py` (12 failures, `AttributeError:
'_reserved_lock'`). Fixed and re-verified: **62 tests pass**.

## 10. Final recommendation

1. **Do NOT enable live trading.** No validated positive-expectancy portfolio exists.
   Every measured variant is negative, and the best is only ~ −0.04R.
2. **Fix OOS fidelity first** — make the calibrator measure expectancy with
   `BacktestEngine` (the engine-backed calibrator). This is the prerequisite; until
   OOS predicts the engine, objective tuning cannot help and the OOS gate will keep
   admitting losers.
3. **Fix reproducibility** (stop backtests mutating bandit / online-ML state) so that
   A/B results are interpretable.
4. Only then revisit payoff improvement (larger `tp_r`, loss-cutting exits) using the
   margin and t-statistic diagnostics already in the codebase.

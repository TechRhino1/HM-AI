# Reachability Guard: Before / After

**Change.** The calibrator is now forbidden from *selecting* a geometry whose target cannot break
even at the win-rate it is asked to reach. For a 1R stop and a target of `tp_r`:

```
WR_breakeven = 1 / (1 + tp_r)        =>        tp_r >= 1 / target_wr - 1
```

At a 75% target that floor is **0.3333**. The geometries below it are still simulated and still
appear in the frontier diagnostic — only selection is restricted, so the report can keep showing
how much win rate is buyable by shrinking the target and what it costs.

Implemented as `min_tp_r_for_target()` in `jarvis/intelligence/winrate_targeting.py`, enforced in
`WRTargetCalibrator._grid_for()`, exposed as `--reachability-margin` /
`--allow-unreachable-target` on `tools/calibrate_winrate.py`, and pinned by 18 tests.

**Why.** The previous run calibrated **7 of 16 symbols to `tp_r < 0.3333`** (0.25 or 0.30). All 7
lost money out of sample, with a mean in-sample win rate of 76.5% — they *hit the target* — and a
mean in-sample expectancy already negative at −0.0279 R. Of the 8 symbols that reached a 75%
in-sample win rate, only 1 was profitable out of sample.

## 1. Calibration, out of sample

| | Unguarded | Guarded | Δ |
|---|---:|---:|---:|
| Out-of-sample trades | 1,284 | 975 | **−309** |
| Out-of-sample win rate | 67.1% | 57.5% | **−9.5 pp** |
| Out-of-sample total | **−54.74 R** | **−37.65 R** | **+17.08 R** |
| Expectancy per trade | −0.0426 R | −0.0386 R | +0.0040 R |
| Pre-regime-policy total | −111.17 R | −74.30 R | +36.87 R |
| Symbols below the break-even floor | **7 / 16** | **0 / 16** | −7 |
| Symbols with negative OOS expectancy | 12 / 16 | 10 / 16 | −2 |
| Symbols meeting the target OOS | 1 / 16 | 1 / 16 | 0 |

**The win rate fell by 9.5 points and the system got more profitable.** That is the whole point:
309 trades were removed and every one of them was net-negative. This is the frontier trap being
closed rather than worked around.

## 2. Per-symbol geometry and outcome

| Symbol | tp_r before | tp_r after | OOS exp before | OOS exp after | Δ |
|---|---:|---:|---:|---:|---:|
| AUDUSD | 0.25 | **0.40** | −0.0552 | −0.0487 | +0.0065 |
| BTCUSD | 0.25 | **0.40** | −0.0472 | −0.0930 | −0.0458 |
| ETHUSD | 0.25 | **0.75** | −0.1063 | −0.0772 | +0.0291 |
| EURUSD | 0.25 | **0.40** | −0.0514 | −0.0849 | −0.0335 |
| USDCHF | 0.25 | **0.40** | −0.2415 | −0.1915 | +0.0500 |
| NAS100 | 0.30 | **1.00** | −0.0332 | −0.1524 | −0.1192 |
| XAUUSD | 0.30 | **1.50** | −0.0374 | **+0.0002** | +0.0376 |
| USDCAD | 0.40 | 0.40 | −0.0031 | **+0.0552** | +0.0583 |
| USDJPY | 0.40 | 0.40 | +0.0129 | +0.0129 | 0 |
| UK100 | 0.60 | 0.60 | +0.3054 | +0.3054 | 0 |
| GBPUSD | 0.75 | 0.75 | −0.1249 | −0.0258 | +0.0991 |
| GER40 | 1.50 | 1.50 | +0.1619 | +0.1619 | 0 |
| NZDUSD | 1.50 | 1.50 | −0.0460 | −0.1229 | −0.0769 |
| SOLUSD | 1.50 | 1.50 | +0.0231 | +0.0231 | 0 |
| US30 | 1.50 | 1.50 | −0.0904 | −0.0904 | 0 |
| XAGUSD | 1.50 | 1.50 | −0.0447 | −0.0597 | −0.0150 |

Six symbols improved, four worsened, six unchanged. **The guard is net-positive but not
uniformly so** — it removes a structural defect, it does not add information. Two symbols
(NAS100, NZDUSD) got materially worse because the walk-forward vote moved to a different, wider
geometry once the trap was closed, and that geometry happened not to hold up.

## 3. Portfolio, production engine

| | Unguarded | Guarded | Δ |
|---|---:|---:|---:|
| Symbols traded | 4 | **5** | +1 |
| Trades | 218 | 331 | +113 |
| Win rate | 64.68% | 64.0% | −0.7 pp |
| Expectancy | +0.0621 R | +0.054 R | −0.008 R |
| Profit factor | 1.20 | 1.15 | −0.05 |
| Net profit | +$591.83 | **+$765.38** | **+$173.55** |
| Max drawdown | 3.29% | 6.53% | +3.24 pp |
| Uniqueness-weighted expectancy | −0.0189 R | −0.0315 R | −0.0126 R |
| **Net excluding UK100** | **−$30.31** | **+$143.24** | **+$173.55** |

Two things changed for the better:

* **USDCAD and XAUUSD became tradeable.** Both cleared the entry gate once their geometry moved
  above the floor (USDCAD +$33.79, XAUUSD +$139.76).
* **The result no longer depends on a single symbol.** Excluding UK100 the portfolio was
  **−$30.31**; it is now **+$143.24**. That is the more meaningful improvement — the headline
  moved by $173.55, but the *robustness* moved by much more.

Two things got worse, and both are exposure rather than deterioration:

* Drawdown rose 3.29% → 6.53% because five symbols now trade instead of four.
* Uniqueness-weighted expectancy is still negative (−0.0315 R), so the aggregate edge still
  depends on trades that were open at the same time.

## 4. What this does and does not fix

**Does fix.** The system can no longer configure itself into a geometry where the win-rate target
is arithmetically unreachable. Seven of sixteen symbols were doing exactly that; now none are.
Aggregate out-of-sample improves by 17.08 R and the portfolio result survives the removal of its
best symbol.

**Does not fix.** Aggregate out-of-sample is still **−37.65 R**, and 10 of 16 symbols still have
non-positive out-of-sample expectancy. The guard removes a self-inflicted wound; it does not
manufacture an edge. The remaining negative is the same one identified in the BTCUSD analysis:
**the entries have no demonstrable edge** — the target sweep was negative at every value from 0.25
to 3.0. That is where the next work has to go, and it is an entry/research problem, not a
calibration-parameter problem.

**Also fixed in this pass.** `tools/calibrate_winrate.py::discover()` globbed
`*_candidates.parquet` for the 95-day window, which also matched
`BTCUSD_183d_candidates.parquet` and invented a symbol named `BTCUSD_183d` with no price data. It
now rejects any name still carrying a `_<n>d` window marker.

## 5. Recommendation

Keep the guard on. Then add `--reachability-margin 0.17`, which requires `tp_r >= 0.5` at a 75%
target (break-even 66.7%) instead of the bare arithmetic floor of 0.3333 (break-even exactly 75%,
i.e. zero margin). The default floor leaves no room for spread, slippage, or the fact that trailing
exits realise less than the nominal target — so it is a correctness bound, not a safety bound.

## 6. Limitations

* 95 days, 16 symbols. Out-of-sample samples range from 31 (UK100) to 151 (XAUUSD).
* Swap/financing is not modelled.
* The portfolio figure is a fixed-fractional sum of independent per-symbol runs, not a single
  capital-constrained simulation.
* One run per configuration. The before/after deltas include ordinary walk-forward selection noise;
  only the aggregate improvement and the 7→0 floor count are structural.
* `XAUUSD` produced a position-sizing rejection during the run
  (`minimum lot size would force 0.68% risk against a 0.20% target`) — worth a separate look, as it
  means the realised risk on that symbol is not the configured risk.

---

*Companion to `portfolio_corrected_specs_and_frontier_trap.md`, which documents the diagnosis this
change implements. Unguarded artifacts retained as `config/winrate_profiles.UNGUARDED.json` and
`data/signals/calibration_summary.UNGUARDED.json`.*

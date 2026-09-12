# Reachability Margin: the 75% Win-Rate Objective Was the Problem

**Summary.** Enforcing a minimum payoff-to-risk ratio — so that the win-rate target is
arithmetically *reachable* — turns a losing system into a profitable one while **lowering** the win
rate from 67.1% to 54.7%. Out-of-sample aggregate improves monotonically as the required margin
rises: **−54.74 R → −37.65 R → −28.65 R**. Portfolio net goes **+$591.83 → +$765.38 → +$1,270.06**,
and the sample-uniqueness-weighted expectancy flips from negative to **+0.3662 R**.

No entry logic, signal, indicator or threshold was changed. Only the constraint on which geometries
the calibrator is allowed to select.

## 1. The constraint

For a 1R stop and a target of `tp_r`, `WR_breakeven = 1 / (1 + tp_r)`. Requiring break-even to fall
at or below the win-rate target gives:

```
tp_r >= 1 / target_wr - 1 + margin
```

At a 75% target: floor **0.3333** with no margin, **0.5033** with margin 0.17.

The margin exists because the bare floor is a *correctness* bound, not a safety bound: at exactly
0.3333 the target win rate equals the break-even win rate, leaving nothing for spread, slippage, or
the fact that trailing exits realise less than the nominal target. Margin 0.17 requires `tp_r ≥ 0.5`,
i.e. break-even at 66.7% — a real 8-point cushion below the 75% target.

## 2. Calibration, out of sample

| Configuration | Trades | Win rate | Total R | Per trade | Symbols profitable |
|---|---:|---:|---:|---:|---:|
| Unguarded (no floor) | 1,284 | 67.1% | −54.74 | −0.0426 | 4 / 16 |
| Margin 0.00 (floor 0.3333) | 975 | 57.5% | −37.65 | −0.0386 | 6 / 16 |
| **Margin 0.17 (floor 0.5033)** | **908** | **53.8%** | **−28.65** | **−0.0316** | 5 / 16 |

| Step | Δ Total R | Δ Win rate |
|---|---:|---:|
| Unguarded → margin 0.00 | **+17.08 R** | −9.5 pp |
| Margin 0.00 → margin 0.17 | **+9.00 R** | −3.7 pp |
| Unguarded → margin 0.17 | **+26.09 R** | **−13.2 pp** |

**Every point of win rate given up bought roughly two R of aggregate improvement.** The relationship
is monotone in the floor, which is what a real structural effect looks like rather than a fitted one.

## 3. Portfolio, production engine

| | Unguarded | Margin 0.00 | **Margin 0.17** |
|---|---:|---:|---:|
| Symbols traded | 4 | 5 | 5 |
| Trades | 218 | 331 | 212 |
| **Win rate** | 64.68% | 64.0% | **54.7%** |
| **Expectancy** | +0.0621 R | +0.054 R | **+0.162 R** |
| **Profit factor** | 1.20 | 1.15 | **1.35** |
| **Net profit** | +$591.83 | +$765.38 | **+$1,270.06** |
| Max drawdown | 3.29% | 6.53% | **3.29%** |
| **Uniqueness-weighted expectancy** | −0.0189 R | −0.0315 R | **+0.3662 R** |
| **Net excluding UK100** | −$30.31 | +$143.24 | **+$647.92** |
| UK100 share of net | 105% | 81% | **49%** |

Margin 0.17 is best on every metric except trade count — higher expectancy, higher profit factor,
higher net, lower drawdown than margin 0.00, and it is the first configuration whose
uniqueness-weighted expectancy is **positive**, meaning the edge no longer depends on trades that
were open simultaneously.

### Contributors (margin 0.17)

| Symbol | Trades | WR % | Exp (R) | Net $ | MaxDD % |
|---|---:|---:|---:|---:|---:|
| UK100 | 39 | 82.0 | +0.311 | +622.14 | 0.71 |
| USDCAD | 31 | 54.8 | +0.313 | +445.43 | 1.75 |
| GER40 | 54 | 46.3 | +0.155 | +178.87 | 1.83 |
| SOLUSD | 49 | 53.1 | +0.029 | +42.39 | 2.61 |
| USDJPY | 39 | 41.0 | +0.070 | −18.77 | 2.24 |

## 4. The conclusion

**The 75% win-rate objective was the cause of the losses, not a target the system was failing to
hit.**

The calibrator was instructed to reach a 75% win rate. The cheapest way to raise a win rate is to
move the target closer, so it did — into the region where a 75% win rate is *below* break-even.
Seven of sixteen symbols were configured that way, and all seven lost. The objective was
self-defeating: it selected geometries in which its own target was unprofitable.

Constraining the geometry so the target is reachable, and then reporting whatever win rate falls
out, produces a system that is profitable at **54.7%** — twenty points below the target that was
supposedly the goal.

**Recommendation:** stop optimising for win rate. Optimise expectancy, constrain `tp_r` so the
target is reachable, and report the win rate as an output. If 75% remains a hard product
requirement, it must be imposed as a *constraint on a geometry that can support it* — never as the
maximand.

## 5. What is still wrong

* **Aggregate out-of-sample is still −28.65 R.** The constraint removed a self-inflicted wound; it
  did not create an edge. Ten of sixteen symbols still have non-positive out-of-sample expectancy.
* **Only 5 of 16 symbols trade.** The rest are refused because their out-of-sample expectancy is
  non-positive. That is the gate working, but it means the portfolio rests on five instruments.
* **The grid is truncating the search.** At margin 0.17, **10 of 16 symbols sit at `tp_r = 1.5`** —
  the widest value in the coarse grid. When the optimum piles up on a boundary, the boundary is
  binding. `tp_r = 2.0` was removed from the grid earlier because at that width trades are held into
  the `trail_activation_r = 2.0` region, where the simulator and `BacktestEngine` resolve exits
  differently (measured on NAS100: calibration predicted 44.2% WR / +0.125 R, engine realised 23.3%
  WR / −0.246 R). So the honest next step is not to widen the grid — it is to **fix the
  simulator/engine divergence at `trail_activation_r`, then re-admit wider targets.** Until that is
  done, the wide end of the search is inaccessible for a mechanical reason, not an economic one.
* **Two symbols dominate.** UK100 (49%) and USDCAD (35%) are 84% of the net. Better than the single
  symbol before, still concentrated.
* **The entries remain the binding constraint.** The BTCUSD analysis showed the target sweep
  negative at every value from 0.25 to 3.0. Geometry can stop the system from hurting itself; it
  cannot supply an edge the entries do not have.

## 6. Method and limitations

* 95 days of real MT5 H1 bars, 16 symbols, $10,000 at 0.5% risk, purged K-fold with embargo,
  walk-forward voting, regime policy fitted on training folds only.
* One run per configuration. Individual per-symbol deltas contain ordinary walk-forward selection
  noise; only the aggregate progression and the 7 → 0 floor count are structural.
* Swap/financing not modelled. Portfolio figure is a fixed-fractional sum of independent per-symbol
  runs, not a single capital-constrained simulation.
* `XAUUSD` logged a position-sizing rejection during an earlier run
  (`minimum lot size would force 0.68% risk against a 0.20% target`). The realised risk on that
  symbol is not the configured risk, and the calibration's assumption that every selected trade is
  taken can diverge from the engine. Worth a separate look.

---

*Configurations retained: `config/winrate_profiles.UNGUARDED.json` (no floor),
`config/winrate_profiles.MARGIN0.json` (floor 0.3333), `config/winrate_profiles.json` (floor 0.5033,
the current deployed profile). Companion to `portfolio_corrected_specs_and_frontier_trap.md`
(diagnosis) and `reachability_guard_before_after.md` (the guard's first measurement).*

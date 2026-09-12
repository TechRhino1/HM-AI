# Engine/Simulator Exit Divergence: Fixed, and the Grid-Widening Hypothesis Refuted

Two results, one positive and one negative. The positive one is a real bug fix. The negative one
retracts a recommendation I made in `reachability_margin_sweep.md` — widening the geometry grid does
**not** help, and I should not have proposed it as the next step.

## 1. The bug: the engine and the calibrator traded different exit schedules

`trail_atr = None` means "no runner trail". The simulator implements that convention correctly:
`Geometry.to_policy` pushes `trail_activation_r` to `1e9`, disabling the trail. `ExitGeometry.to_policy`
does the same. But `BacktestEngine` destroyed the signal before it got there:

```python
geom_trail = getattr(geom, "trail_atr", 1.5) or 1.5     # None or 1.5 -> 1.5
```

`None or 1.5` evaluates to `1.5`, so a *disabled* trail became a **live 1.5×ATR trail** with
activation at 2.0R. Every geometry in the calibration grid sets `trail_atr=None`, and all 16 deployed
profiles are `A_fixed_tp` with `trail_atr: null` — so the engine was systematically trading an exit
schedule the calibrator had never measured.

This is precisely what the comment immediately above that line was trying to prevent
(*"Using the validated geometry is what makes the engine's realized expectancy match the calibrated
OOS"*). The fix is to pass `None` through:

```python
geom_trail = getattr(geom, "trail_atr", None)
...
trail_atr=None if geom_trail is None else float(geom_trail),
```

**Why it was invisible until now.** The trail only engages at `trail_activation_r = 2.0`. A trade
targeting 1.5R exits before it ever gets there, so for every geometry on the grid the divergence was
dormant. It bites exactly at and above `tp_r = 2.0` — which is where the original author found it
(NAS100: calibrator predicted 44.2% WR / +0.125 R, engine realised 23.3% WR / −0.246 R) and why the
grid was capped at 1.5 in the first place.

Pinned by 6 new tests in `tests/test_winrate_targeting.py`, including a parametrised parity test that
asserts the engine policy and the simulator policy agree on `trail_activation_r`, `runner_trail_atr`
and `be_trigger_r` for `trail_atr` of `None`, `1.5` and `2.0`.

## 2. The hypothesis I proposed, and its refutation

In `reachability_margin_sweep.md` I observed that **10 of 16 symbols had piled up on `tp_r = 1.5`**,
the widest value in the coarse grid, and concluded that the grid boundary was binding — proposing
that once the divergence above was fixed, wider targets should be re-admitted. That was a reasonable
inference and it is **wrong**.

I added `--wide-grid` (extending the coarse grid to 2.0 and 2.5) and re-ran the identical
calibration at margin 0.17:

| Configuration | Trades | Win rate | Total R | Per trade |
|---|---:|---:|---:|---:|
| Margin 0.17, grid ≤ 1.5 | 908 | 53.8% | **−28.65** | −0.0316 |
| Margin 0.17, grid ≤ 2.5 | 757 | 49.3% | **−34.10** | −0.0451 |
| **Δ (wide − narrow)** | **−151** | **−4.6 pp** | **−5.46 R** | −0.0135 |

The wider grid is worse on every aggregate measure. Per symbol: 6 improved, 8 worsened, 2 unchanged.

### The lesson: boundary pinning is not evidence the optimum lies beyond it

The pile-up did not go away — it **moved**:

| | `tp_r` distribution | Pinned at the widest value |
|---|---|---|
| Narrow (≤1.5) | 0.6×2, 0.75×2, 1.0×2, 1.5×10 | **10 / 16 at 1.5** |
| Wide (≤2.5) | 0.6×2, 1.5×2, 2.0×2, 2.5×10 | **10 / 16 at 2.5** |

Exactly the same 10 symbols, pinned at the new boundary. So the pile-up is not a signal that the
optimum sits just outside the grid — it is a signal that **the calibrator's in-sample selection
criterion is monotone in `tp_r`**: wider is always better *in sample*. Extending the boundary simply
relocates the pile-up, and out of sample the result gets worse.

That is the same overfitting signature as the win-rate trap, mirrored. The win-rate objective pushed
`tp_r` down into a region where the target was unreachable; the in-sample expectancy criterion pushes
`tp_r` up into a region that does not generalise. Both are artefacts of the selection objective, and
neither is fixed by changing the grid.

**Recommendation: keep the coarse grid capped at 1.5.** The `--wide-grid` flag stays for
reproducibility, off by default.

## 3. State after this change

The deployed configuration is unchanged from `reachability_margin_sweep.md` — margin 0.17, grid
≤ 1.5 — because that remains the best measured configuration:

| | Value |
|---|---:|
| Portfolio trades | 212 |
| Portfolio win rate | 54.7% |
| Portfolio expectancy | +0.162 R |
| Profit factor | 1.35 |
| Net profit | **+$1,270.06** |
| Max drawdown | 3.29% |
| Uniqueness-weighted expectancy | **+0.3662 R** |

The exit-policy fix is strictly a correctness improvement: it makes the engine reproduce the schedule
the calibrator measured. It does not change the calibrated numbers, because no deployed geometry has
`tp_r ≥ 2.0`.

## 4. Where the constraint actually is

Three hypotheses about the binding constraint have now been tested:

| Hypothesis | Test | Verdict |
|---|---|---|
| Symbol metadata was wrong | Registry audit | **Confirmed** — 8 of 16 symbols had zero trades |
| The win-rate objective was self-defeating | Reachability floor | **Confirmed** — +26.09 R, monotone |
| The geometry grid was too narrow | `--wide-grid` | **Refuted** — −5.46 R |

What is left is the one thing no geometry constraint can supply: **the entries have no demonstrable
edge.** The BTCUSD analysis found the target sweep negative at every value from 0.25 to 3.0; the
aggregate out-of-sample result is still −28.65 R over 908 trades. Geometry selection can stop the
system from hurting itself — and it now does — but it cannot manufacture a signal that is not there.

The next work belongs in entry research (signal quality, feature selection, regime conditioning),
not in further parameter search. A useful discipline from here: **any further change should be
justified by a mechanism, and tested the same way** — one variable, identical entries, aggregate
out-of-sample as the arbiter.

## 5. Method and limitations

* 95 days of real MT5 H1 bars, 16 symbols, $10,000 at 0.5% risk, purged K-fold with embargo,
  walk-forward voting, regime policy fitted on training folds only.
* One run per configuration. The −5.46 R delta between narrow and wide is a single comparison; the
  per-symbol changes contain ordinary walk-forward noise. The *aggregate* direction and the pile-up
  relocation are the reliable signals.
* Reproduce with:
  `python tools/calibrate_winrate.py --days 95 --reachability-margin 0.17 [--wide-grid]`
* Artifacts: `config/winrate_profiles.WIDEGRID.json`, `data/signals/calibration_summary.WIDEGRID.json`,
  `reports/widgrid_calib.log`.

---

*Supersedes the "grid boundary is binding → widen it" recommendation in
`reachability_margin_sweep.md` §5. That document's diagnosis and its measured +26.09 R result stand;
only its proposed next step was wrong.*

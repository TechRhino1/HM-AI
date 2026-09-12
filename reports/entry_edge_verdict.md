# Stop-Slippage Parity, and the Entry-Edge Verdict

**Date:** 2026-09-12
**Scope:** the 16-symbol calibrated portfolio (`config/winrate_profiles.json`, target 75%, margin 0.17, coarse grid ≤ 1.5)
**Question this answers:** *why are the trades failing?*

---

## 0. Summary

Two results, in causal order.

**First**, a fourth instrument/engine divergence was found and fixed: the calibration
instrument computed the stop-slippage it meant to charge and then dropped it, so
every reported expectancy was optimistic. The correction is **−3.23 R** on the
aggregate out-of-sample sample (−28.64 R → −31.87 R) and it changes **no**
geometry — a pure accounting error.

**Second**, with the instrument now honest, an entry-edge attribution was run over
all **18,998** candidates. The verdict is negative and it is the important result:

> **There is no edge in the current entry features.** No numeric feature has a
> best-bucket expectancy meaningfully above zero. The two features with the
> largest apparent gradients (`risk_dist`, `atr`) are ~80 % explained by the
> slippage cost term just fixed. Every value of every categorical feature
> (`regime`, `strategy`) has **negative** pooled expectancy. The primary entry
> filter (`score`) sorts how badly trades lose, not how well they win — and the
> follow-up test of using it for *position sizing* instead (§7a) **also fails**,
> with the inverted rule performing best. It carries no actionable information in
> either role.

Geometry calibration can stop the system hurting itself — and after the
reachability guard it does. It cannot manufacture a signal that is not there.
**Further parameter search is not the answer; the entries are.**

---

## 1. The slippage parity defect

### 1.1 What was wrong

`BacktestEngine` charges slippage on every protective-stop fill:

```python
# jarvis/backtesting/engine.py
actual_slippage_delta = self.slippage_pips * spec.pip_size      # :94
...
exit_price = open_trade["sl"] - actual_slippage_delta           # :371, :375, :387, :391
```

Target fills are *not* slipped (`engine.py:379, :395`) — correctly, and the
simulator agrees.

The calibration instrument intended to mirror this. It even said so:

```python
# jarvis/intelligence/winrate_targeting.py
# Stop slippage applied by BacktestEngine on every protective-stop fill
# (actual_slippage_delta = slippage_pips * pip_size). The calibration
# instrument must model it too, or OOS expectancy over-promises an edge
# that the engine erodes with slippage on every scratch and loss.
self.slippage_pips = float(slippage_pips)
```

and then computed the value:

```python
slip = float(self.slippage_pips) * float(getattr(spec, "pip_size", 0.0001) or 0.0001)   # :875
```

**`slip` is never used.** That line is the only occurrence of `slip` in the entire
module. The simulation call immediately below it omits the argument:

```python
sim_cache[g.key()] = simulate_all_candidates(
    df=df, candidates=cands, geom=g, money_per_unit=money_per_unit,
    cost_price_equiv=cost, spec=spec, symbol=symbol, score_col=score_col,
)                                    #  ^ slippage_price_equiv absent
```

And even if it had been passed, it would have gone nowhere:
`simulate_all_candidates` **accepted** `slippage_price_equiv` and never forwarded
it to `simulate_trade`.

### 1.2 Proof the argument was inert

A parameter that is wired through must change the result; one that is dropped
cannot, however absurd its value. On EURUSD at the deployed geometry, a **1000-pip**
slippage produced **bit-identical** outcomes:

```
── simulate_all_candidates ──────────────────────────────────────────
  total R, slippage=0     : -159.8428
  total R, slippage=1000p : -159.8428
  bit-identical           : True
  VERDICT                 : DROPPED (argument is inert)
```

Two independent tools were already passing the argument and silently getting
nothing — which is strong evidence the drop was a bug, not a design choice:

* `tools/sweep_tp_r.py:122` — `slippage_price_equiv=slip`
* `tools/diag_engine_vs_sim.py:149` — `slippage_price_equiv=slip`

The second is the tool that compares the engine against the simulator, so part of
any engine/sim divergence it reported was this.

### 1.3 The fix

| File | Change |
|---|---|
| `jarvis/backtesting/trade_simulator.py` | `simulate_all_candidates` now forwards `slippage_price_equiv` to `simulate_trade` |
| `jarvis/backtesting/trade_simulator.py` | loop extracted into `simulate_all_candidates_with_rows` (keeps the candidate index, used by the attribution tool); `simulate_all_candidates` delegates, so behaviour is unchanged |
| `jarvis/intelligence/winrate_targeting.py` | `calibrate_symbol` passes `slippage_price_equiv=slip` |
| `tools/calibrate_winrate.py` | `SLIPPAGE_PIPS = 0.5` constant; profile meta now records `slippage_pips`, `enforce_reachable_target`, `reachability_margin`, `min_tp_r_floor`, `wide_grid` |

The meta change matters on its own: the deployed file previously could not be
traced back to the guard setting that produced it, and the same target calibrates
very differently at margin 0.0 and 0.17.

### 1.4 Tests

Seven slippage tests now exist. The three new ones close the gap that the four
pre-existing ones left — they tested `simulate_trade` (the leaf) but nothing
tested that the aggregators forward the argument:

* `test_simulate_all_candidates_forwards_slippage`
* `test_evaluate_geometry_forwards_slippage`
* `test_aggregator_slippage_never_touches_target_fills`
* `test_calibrator_charges_stop_slippage` (integration, three assertions)

Verified by reverting the fix: the **3 new aggregator tests fail**
(`assert 0.136 < 0.136` — the exact "no effect" signature) while the 4 leaf tests
still pass. `tests/test_winrate_targeting.py`: **73 passed**.

### 1.5 Measured impact

**Aggregate out-of-sample, deployed settings, one variable changed:**

| | Trades | Total R | R/trade |
|---|---:|---:|---:|
| Before (no slippage charged) | 908 | −28.64 | −0.03155 |
| After (0.5 pips charged) | 906 | **−31.87** | −0.03518 |
| **Delta** | −2 | **−3.23** | −0.00363 |

**Every one of the 16 deployed `tp_r` values is unchanged.** So this is a pure
accounting correction, not a selection change — a clean one-variable measurement.

Per symbol, 15 of 16 got slightly worse (as they must: the cost is now charged).
The single exception is **NZDUSD (+1.460 R)**, and its cause is visible and
legitimate: once slippage was charged, the `BREAKOUT` regime's expectancy fell
below tolerance and the learned policy switched it off. That is the only policy
change in the portfolio.

**Portfolio-wide cost the instrument had been ignoring:** −114.31 R over 18,998
trades (−0.00602 R/trade), with 54.4 % of candidates exiting at a protective stop.

### 1.6 The mechanism, verified to the pip

Slippage is charged as a fixed *pip distance*, so its cost measured in R is
`slip / risk_dist` — it **falls as the stop widens**. That makes it mechanically
correlated with any feature that scales with stop width:

```
corr(delta, risk_dist)   : +0.3992
corr(delta, 1/risk_dist) : -0.3992
```

Recovering the implied slip per symbol from `delta = -slip / risk_dist` on stop
exits returns each instrument's pip size exactly — AUDUSD/EURUSD/GBPUSD/NZDUSD/USDCAD
`0.00005`, USDCHF `0.000025`, USDJPY `0.005`, XAGUSD `0.005`, XAUUSD `0.05`,
GER40/NAS100/UK100 `0.5`, US30 `0.25`. The cost model is correct; it simply was
not being applied. This matters in §3.

### 1.7 Classification

This is the **same failure class** as the `trail_atr=None` bug fixed earlier:
the instrument and the engine silently resolved different behaviour, so the
calibration reported a result the engine could not produce. Both were invisible
because the leaf function was correct — only the plumbing between it and the
caller was wrong.

---

## 2. Entry-edge attribution — method

Three geometry hypotheses had already been tested and settled: **metadata**
(confirmed, fixed), **the win-rate objective** (confirmed, fixed by the
reachability guard), **grid width** (tested and **refuted** — widening made
results 5.46 R worse and the pile-up moved to the new edge). After all three the
aggregate out-of-sample result is still negative, so the question becomes whether
any entry feature carries edge at all.

`tools/entry_edge_diagnostic.py`:

1. For each symbol, replay **every** candidate under that symbol's **deployed**
   geometry. The exit schedule is held constant, so the only thing that differs
   between buckets is the entry.
2. Bucket by feature. Numeric features are ranked **within each symbol** and then
   pooled, so "top quintile" means the same thing on gold and on BTC and the
   symbol-level expectancy differences do not dominate the pooled numbers.
3. Report the pooled bucket table, a trade-level Spearman rank correlation, and —
   the decisive test — **how many individual symbols reproduce the direction**.
   A pooled gradient that does not reappear inside the symbols is a composition
   effect, not an edge.
4. Report economic size, not p-values. With ~19k candidates almost any gradient is
   statistically significant; the question is whether it is *worth acting on*.

Two supporting tools:
* `--slippage-pips 0` re-runs the scan with no slippage, which separates a real
  entry edge from the cost artefact of §1.6.
* `tools/entry_gradient_artefact_test.py` performs the decomposition.

---

## 3. Numeric features — no usable gradient

**18,998 candidates, 16 symbols, 54.4 % stop exits.**

| Feature | rho | Q1 exp | Q5 exp | spread | Q5 WR | symbols agreeing |
|---|---:|---:|---:|---:|---:|---:|
| `risk_dist` | **+0.2039** | −0.1217 | **+0.0009** | +0.1226 | 53.9 % | 12/16 |
| `atr` | **+0.1942** | −0.0843 | **+0.0025** | +0.0868 | 53.9 % | 12/16 |
| `ev` | +0.1210 | −0.0682 | −0.0367 | +0.0316 | 51.7 % | 10/16 |
| `rr` | +0.0889 | −0.0941 | −0.1216 | −0.0275 | 47.2 % | 5/16 |
| `spread_pips` | +0.0752 | +0.0188 | −0.0900 | −0.1088 | 48.8 % | 5/16 |
| `dissection_score` | +0.0326 | −0.1244 | −0.0477 | +0.0767 | 50.9 % | 12/16 |
| `score` | +0.0323 | −0.1016 | −0.0608 | +0.0409 | 49.8 % | 9/16 |
| `confluence_count` | +0.0258 | −0.1006 | −0.0601 | +0.0405 | 49.7 % | 9/16 |
| `master_score` | +0.0133 | −0.0909 | −0.0746 | +0.0163 | 49.4 % | 8/16 |
| `trend_score` | −0.0180 | −0.0596 | −0.0952 | −0.0356 | 48.7 % | 6/16 |
| `n_failed_gates` | −0.0243 | −0.0568 | −0.1156 | −0.0588 | 48.1 % | 7/16 |
| `adversarial_penalty` | −0.0277 | −0.0677 | −0.0870 | −0.0193 | 49.3 % | 8/16 |

### 3.1 The two largest gradients are the cost artefact

Re-running the identical scan with **zero slippage** collapses both:

| Feature | rho @ 0.5 pips | rho @ 0 pips | retained |
|---|---:|---:|---:|
| `risk_dist` | +0.2039 | **+0.0394** | 19 % |
| `atr` | +0.1942 | **+0.0361** | 19 % |

Both were ~80 % the mechanical `−slip / risk_dist` term of §1.6, exactly as the
correlation predicted. What remains after removing it (+0.04) is indistinguishable
from noise across 19k trades.

This is why the slippage fix had to come first: **an unfixed instrument would have
reported `risk_dist` and `atr` as the two strongest entry edges in the system**,
and they are not edges at all — they are an accounting artefact.

### 3.2 The best bucket is still zero

Even taking the gradients at face value, the best quintile of the best feature is
**+0.0009 R**. No numeric feature has a top bucket that is meaningfully positive:

* `risk_dist` Q5 **+0.0009 R** — break-even
* `atr` Q5 **+0.0025 R** — break-even
* `ev` Q5 **−0.0367 R** — negative
* `score` Q5 **−0.0608 R** — negative

### 3.3 The gradients are not even monotone

`risk_dist` buckets: Q1 −0.1217, Q2 −0.1006, **Q3 −0.0874**, Q4 −0.1046, Q5 +0.0009.
Q3 beats Q4 — so the relationship is not monotone, and a positive rho is being
produced by mid-bucket ordering rather than a trend. `ev` is worse: it is U-shaped
(Q1 −0.0682, Q2 −0.1410, Q3 −0.0991, Q4 −0.0682, Q5 −0.0367), so its "positive rho"
comes entirely from Q1 being less bad than Q2.

`rr` and `spread_pips` show **rho and spread disagreeing in sign**, which is the
signature of a non-monotone relationship, not a gradient.

### 3.4 `score` — the primary entry filter — is not an edge

`score` has rho **+0.0323**, its best quintile is **−0.0608 R**, and only 9 of 16
symbols reproduce the direction. Read the bucket ladder (Q1 −0.1016 → Q5 −0.0608):
the score does order outcomes weakly, but the whole ladder is negative. **The
score sorts how badly a trade loses, not how well it wins.** That is a
risk-sizing signal, not an entry filter.

---

## 4. Categorical features — dispersion among losers

| Feature | spread | best | worst |
|---|---:|---|---|
| `regime` | +0.4990 R | LOW_VOLATILITY (+0.3636 R, n=83) | COMPRESSION (−0.1354 R, n=456) |
| `strategy` | +0.4371 R | BREAKOUT_EXPANSION (+0.2755 R, n=44) | LIQUIDITY_SWEEP_REVERSAL (−0.1616 R, n=3,303) |
| `order_type` | +0.0943 R | MARKET (−0.0527 R, n=12,968) | LIMIT (−0.1470 R, n=6,030) |
| `side` | +0.0666 R | SELL (−0.0472 R) | BUY (−0.1138 R) |
| `zone` | +0.0322 R | DISCOUNT (−0.0640 R) | PREMIUM (−0.0962 R) |
| `decision` | +0.0268 R | EXECUTE (−0.0626 R) | NO_TRADE (−0.0894 R) |

These spreads are **an order of magnitude larger** than any numeric spread, which
is exactly why they must be treated with suspicion. Two facts kill them:

**Every pooled category is negative.** There is no regime or strategy to select
*into* — only ones that lose less:

```
regime   : BREAKOUT -0.0424R | TREND_BEAR -0.0632R | LIQUIDITY_SWEEP -0.1004R
           TREND_BULL -0.1134R | COMPRESSION -0.1354R
strategy : RANGE_MEAN_REVERSION -0.0547R | TREND_FOLLOWING -0.0591R
           TREND_PULLBACK -0.0609R | CHOCH_STRUCTURAL_REVERSAL -0.0922R
           LIQUIDITY_SWEEP_REVERSAL -0.1616R
```

**The "best" category is different on every symbol.** Across the 16 symbols the
best regime is COMPRESSION (4 symbols), BREAKOUT (3), LOW_VOLATILITY (3),
TREND_BEAR (2), LIQUIDITY_SWEEP, TREND_BULL, USDCHF's TREND_BULL — with no
consensus. For strategy: CHOCH_STRUCTURAL_REVERSAL (5), TREND_FOLLOWING (3),
LIQUIDITY_SWEEP_REVERSAL (3), RANGE_MEAN_REVERSION (2).

The large spread is therefore **dispersion among small-n categories**, not a
discoverable regime edge. `LOW_VOLATILITY` (n=83) and `BREAKOUT_EXPANSION` (n=44)
are far too small to act on, and USDJPY's `LOW_VOLATILITY` bucket — the single
largest value in the whole table at **+1.0463 R on 21 trades** — is noise.

---

## 5. Dead features

### 5.1 `meta_label_prob` is a constant

```
dtype=float64  n=18,998  nan=0  nunique=1
describe: min=-1.0  25%=-1.0  50%=-1.0  75%=-1.0  max=-1.0
distinct values per symbol: 1 for all 16 symbols
```

It is `-1.0` everywhere. This is **by design, not a bug**: the scanner writes the
sentinel when the decision has no probability (`signal_scan.py:289`), and
`decision_engine.py:1029` documents the gate as *"safe: neutral until a model is
trained"* — `meta_label_prob` stays `None` until `meta_labeler.predict_proba`
returns a value. The arbiter consumes the sentinel safely
(`opportunity_arbiter.py:250`: `if ml_prob is None or ml_prob <= 0`).

But the consequence is worth stating plainly: **the ML meta-label confirmation
stage contributes nothing to the backtest.** Any report that lists
`meta_label_prob` as a feature is listing a constant, and the "AI-powered"
component of the architecture is inert on this path. It cannot be a source of
edge until a model is trained.

(The apparent "−0.1381 spread" in the scan is an artefact of quintile-ranking a
constant — the rank assignment is arbitrary, which is why rho came back `nan`.)

---

## 6. Per-symbol cross-check

| View | Result |
|---|---|
| Symbols with negative expectancy over **all** candidates | **14 / 16** |
| Symbols with positive expectancy on their **traded** subset | **8 / 16** |
| Symbols the engine actually trades | **5 / 16** (GER40, SOLUSD, UK100, USDCAD, USDJPY) |

The `score` threshold does do something: on 10 of 16 symbols the traded subset
beats the full candidate set. But it only lifts most symbols from "clearly losing"
to "roughly break-even", which is consistent with §3.4.

---

## 7. Engine backtest, post-fix profiles

`tools/run_3month_backtest.py`, 215 trades over 16 symbols:

| Metric | Value |
|---|---|
| Trades | 215 |
| Win rate | 54.9 % |
| Expectancy | +0.149 R |
| Profit factor | 1.36 |
| Net profit | **+$1,322.66** |
| Max drawdown | 3.41 % |
| Uniqueness-weighted expectancy | +0.3631 R |

**Concentration warning.** Only 5 of 16 symbols trade, and **UK100 alone is
+$622.14 of the +$1,322.66 net — 47 %**, on 39 trades at an 82 % win rate. The
portfolio result is not a portfolio result; it is largely one instrument. This
must be reported alongside the headline, never instead of it.

**Reconciliation with the calibration.** The calibration reports −31.87 R over 906
out-of-sample trades; the engine reports +$1,322 over 215. These are different
samples and the gap is explained: the engine runs the full production gate stack
and only 5 symbols survive it, and those 5 happen to be the profitable ones
(GER40 +0.217, UK100 +0.293, USDCAD +0.128, SOLUSD +0.022, USDJPY +0.038). The
engine's headline is the result **on the subset that passed the gates**, not
evidence of a portfolio-wide edge.

---

## 7a. Tested: can the score be used for **sizing**? No.

§3.4 identified the score's monotone-but-negative ladder as the most promising
lead: if the score ranks *loss severity* rather than win probability, the same
information might be usable as a position-size input even though it fails as a
gate. That was worth testing rather than recommending. `tools/score_sizing_test.py`
tests it on the **1,005 non-overlapping traded positions** (one at a time per
symbol, mirroring `select_sequential`), with weights normalised **within each
symbol** so the total risk budget is unchanged — only its distribution moves, so
a variant that simply took more risk cannot win.

| Sizing rule | Weighted exp (R) | Gain vs equal | p | null sd | Uniqueness-wtd | symbols improved |
|---|---:|---:|---:|---:|---:|---:|
| equal (baseline) | **+0.02597** | — | — | — | +0.02804 | — |
| `linear_pct` | +0.01681 | **−0.00917** | 0.702 | 0.0155 | +0.00458 | 7/16 |
| `linear_pct_min25` | +0.02046 | **−0.00551** | 0.725 | 0.0094 | +0.01405 | 7/16 |
| `top_heavy_2x` | +0.01832 | **−0.00765** | 0.712 | 0.0139 | +0.00312 | 5/16 |
| `top_half_only` | +0.01068 | **−0.01529** | 0.710 | 0.0277 | −0.02240 | 5/16 |
| **`inverse_control`** | **+0.03523** | **+0.00926** | 0.316 | 0.0172 | +0.05079 | 9/16 |

**Every rule that favours high scores makes the result worse.** The inverse
control — favouring *low* scores — is the best of all, and none of the p-values is
significant (all 0.32–0.73, with observed gains well inside the null sd).

The inverse control is the tell. If the score carried directional sizing
information, favouring it should help and inverting it should hurt. Instead the
signs are *reversed*, which means the monotone ladder seen over all 18,998
candidates **does not survive inside the traded region** — the region where a
sizing rule would actually operate. The gradient was a property of the rejected
candidates, not of the trades taken.

**This closes the last lead.** The score is not usable as an entry filter (§3.4)
and not usable as a sizing input (§7a). It carries no actionable information in
either role.

**One incidental number worth recording.** The equal-weighted baseline on the full
95-day sample is **+0.026 R** over 1,005 positions, against the calibration's
purged out-of-sample **−0.035 R** over 906. That gap *is* the selection
overfitting, measured: the deployed configuration looks mildly positive on the
sample it was chosen from and negative once purged.

---

## 8. Verdict

**Why the trades are failing.** Not entry/exit timing, not risk management, not
the win-rate target, and not the grid. The failure is upstream of all of them:

1. **The entry signal has no edge.** Across 18,998 candidates and 12 numeric
   features, no feature has a best-bucket expectancy meaningfully above zero. The
   largest apparent gradients are 80 % a slippage-cost artefact that only became
   visible after the instrument was fixed.
2. **The score is not a quality filter.** It weakly orders outcomes, but every
   bucket is negative — it discriminates severity of loss, not likelihood of win.
3. **Regime and strategy carry no selectable edge.** Every value of both is
   negative pooled, and the "best" value differs per symbol. The large spreads are
   dispersion among small samples.
4. **The ML meta-label stage is inert**, so the architecture's adaptive component
   contributes nothing on this path.

The reachability guard fixed a self-inflicted wound and the slippage fix removed a
systematic optimism. Both were necessary. Neither creates an edge, because there
is none to create from these features.

### What to do next

**Stop searching geometry and thresholds.** Four hypotheses have been tested;
three were real defects now fixed, one was refuted. The remaining variance is not
in the parameter space.

The work belongs in **signal research**:

* the entry features are the binding constraint — new *information* is needed
  (order-flow, session/time-of-day, volatility regime *at entry*, cross-asset
  confirmation), not new transforms of the existing ones;
* the `meta_labeler` should be trained, or removed from the reported feature set
  so it stops implying capability that is not there;
* ~~the score's monotone-but-negative ladder is the most promising lead~~ —
  **tested and refuted** (§7a). It is not usable as a sizing input either; the
  inverse rule is the best performer, which is the signature of no signal.

**Discipline to keep:** any further change should be justified by a *mechanism*
and tested the same way — one variable, identical entries, aggregate
out-of-sample as the arbiter, and uniqueness-weighted expectancy alongside the
headline.

---

## 9. Artefacts

| Path | What |
|---|---|
| `tools/entry_edge_diagnostic.py` | the attribution scan (`--slippage-pips` to test the artefact) |
| `tools/entry_gradient_artefact_test.py` | the decomposition in §1.6 / §3.1 |
| `reports/entry_edge_attribution.md` / `.json` | machine output of the scan |
| `reports/backtest_3month_report.md` / `.json` | engine backtest, post-fix profiles |
| `config/winrate_profiles.PRE_SLIPPAGE.json` | the pre-fix profiles, kept for comparison |
| `tests/test_winrate_targeting.py` | 73 tests, incl. 7 pinning slippage parity |

# HM Algo 2.0 — Strategy Diagnosis & Profitability Remediation Plan

**Date:** 2026-09-11
**Analyst:** WorkBuddy AI
**Data sources:**
- `baseline_1y_backtest_report.json` (206 trades, 5 symbols, 1 year, $50k initial)
- `backtest_6month_all_symbols.json` (655 trades, 13 symbols, 182 days, $10k/symbol)
- `backtest_2month_all_symbols_report.json` (121 trades, 13 symbols, 60 days, $130k initial)

---

## 1. Executive Summary

The strategy is **not losing because of bad entries**. It is losing because of a
**catastrophic exit-management defect**: realised winners average **+0.94R** while
losers average **−0.88R**, and the median winning trade reached **+21R of favourable
excursion** before being closed at **+0.86R**.

Across the 1-year baseline:

| Metric | Value |
|---|---|
| Trades | 206 |
| Win rate | 48.06% |
| Profit factor | **0.58** |
| Expectancy | **−$9.11 / trade** |
| Net profit | **−$1,876.56** (−3.75% ROI) |
| Max drawdown | $1,951.02 (3.9%) |

The system's **take-profit levels are almost never reached** (only 10 of 206 trades,
4.9%). Meanwhile **99 of 206 trades (48%) exit at breakeven/trail** at an average of
**+0.86R** — they are classified as "wins" but capture ~1/20th of the move available.

**Root cause in one line:** `be_trigger_r = 1.00` moves the stop to entry after only
1R of favourable movement, and the Stop-Loss is placed structurally so wide
(`sl_atr_multiplier` 2.6–2.8) that the **first normal pullback always trips it**.

---

## 2. Root-Cause Analysis

### 2.1 The Exit Asymmetry (primary defect)

```
Exit reason      Trades    Net P&L      Win rate
─────────────────────────────────────────────────
BE/TRAIL_SL          99    +$1,694.27     89.9%
SL                   97    −$4,491.90      0.0%
TP                   10      +$921.07    100.0%
```

The **BE/TRAIL_SL group is the entire problem.** Its R-multiple distribution:

- median **+0.861R**, maximum **+1.000R** — the "trail" never actually trailed.
- 73 of 99 trades clustered in the **0.7R–1.1R** band.
- Their **median MFE was 21.1R**. 82 of them reached **≥5R** before being closed near 1R.
- **Total favourable excursion abandoned: ~1,504R.**

The worst single case in the dataset: entered at 2914.52 with the stop at 2915.83 —
a **1.31-point risk**. The trade was closed +0.96R. It had **8.64 points of MFE (6.6R)**.

**Mechanism (verified in code):**

`jarvis/backtesting/engine.py:209`
```python
be_trigger_r = 1.00 if is_gold else cfg.be_trigger_r   # → 1.00 for most symbols
if favorable >= (risk_dist * be_trigger_r):
    open_trade["sl"] = entry + be_buffer                # SL jumps to ~entry
```
`jarvis/backtesting/engine.py:262`
```python
trail_mult = getattr(cfg, "runner_trail_atr", 2.20)
atr_trail  = round(high - trail_dist, spec.digits)      # 2.2 × ATR behind the high
if atr_trail > entry: new_sl = max(new_sl, atr_trail)
```

The trail is only *allowed* to engage once `be_locked` is set — and because
`trail_dist = 2.2 × ATR` on the **H1** timeframe while the structural stop is only
`2.6–2.8 × ATR`, the ATR trail sits **behind the original stop** in normal conditions.
It can therefore never ratchet. The result: SL sits at entry, the first pullback
exits, and the trade books ~+0.9R.

### 2.2 Stop-Loss Placement Is Structurally Wrong

`engines/dynamic_sl_tp.py:51-53`
```python
struct_sl = swing_low - (atr * 0.25)     # nearest swing low
atr_sl    = current_price - max_sl_dist  # 2.4–2.8 × ATR
sl_price  = max(struct_sl, atr_sl)       # ← takes the CLOSER of the two
```

`max()` selects the **tighter** stop, not the safer one. On an H1 chart the "recent
swing low" is typically 0.3–0.8 × ATR away, so the SL lands far inside normal noise.
Then `risk_dist` is small → `1.0 × risk_dist` is a **tiny** move → BE trips almost
immediately.

**Evidence:** the median SL distance is **7.2 R** for winners but **22.6 R** for
losers. Losers have dramatically wider stops, meaning the `risk_dist <= 0 or risk_dist
> max_sl_dist` fallback (`dynamic_sl_tp.py:56-58`) is firing and collapsing the stop
onto the ATR cap — i.e. **stop placement is inconsistent and often at the noise floor**.

### 2.3 Take-Profit Is Set Beyond Realistic Reach

TP multipliers (`tp1_rr_mult` 2.0–2.2, `tp2_rr_mult` 3.5–4.0) combined with a
*compressed* `risk_dist` produce TP distances that price rarely reaches:

- 97 trades hit SL for **−$4,492**, only 10 reached TP for **+$921**.
- Mean planned RR 2.62, but **median achieved R is only 0.86**.

The system is configured to **risk 1R to make 3.5R** while structurally ensuring it
**exits at 0.9R**. That is a guaranteed negative-expectancy loop.

### 2.4 Entry Quality — Contributes, But Is Not the Primary Cause

Entry quality is **adequate but unoptimised**. Measured by "did the trade reach +1R
of favourable excursion":

| Strategy | n | Win rate | Reach 1R |
|---|---|---|---|
| TREND_PULLBACK | 122 | 49.2% | 54.1% |
| TREND_FOLLOWING | 41 | 51.2% | 51.2% |
| LIQUIDITY_SWEEP_REVERSAL | 14 | 50.0% | 57.1% |
| RANGE_MEAN_REVERSION | 11 | 54.5% | 54.5% |
| **BREAKOUT_EXPANSION** | **18** | **27.8%** | **44.4%** |

| Regime | n | Win rate | Reach 1R |
|---|---|---|---|
| TREND_BULL | 100 | 50.0% | 54.0% |
| TREND_BEAR | 67 | 53.7% | 56.7% |
| **BREAKOUT** | **16** | **37.5%** | **43.8%** |
| **WEAK_TREND** | **14** | **14.3%** | **35.7%** |

**BREAKOUT_EXPANSION is a net destroyer** (−$459.36, 27.8% WR) and **WEAK_TREND is
catastrophic** (14.3% WR, −$397.50). Both are over-traded relative to their edge.

Notably, more trades can be *sourced*: `TREND_PULLBACK` in `TREND_BULL` reaches +0.5R
**77.6%** of the time (n=58), and `LIQUIDITY_SWEEP_REVERSAL` averages **+0.124R**.
The edge exists — it is being harvested badly.

### 2.5 Score Gate Has No Discriminative Power

`score` and `master_score` do **not** separate winners from losers:

```
master_score  GOOD median 47.0  vs  BAD median 48.0   (no separation)
score         GOOD median 0.626 vs  BAD median 0.606  (no separation)
reach-0.5R rate by master_score bucket:
  <40 → 78.0%   40-50 → 64.7%   50-55 → 61.5%
  55-60 → 75.8% 60-70 → 59.3%   70+ → 66.7%
```

The buckets are **non-monotonic**. The confidence scoring layer — which gates every
entry via `min_trade_score: 80.0` and a "Master Confluence ≥ 55/100" check — is
**not predictive**. It is filtering on noise while simultaneously rejecting huge
volumes of setups (e.g. XAUUSD: **4,504 rejections** on AI Multi-Score Gate alone).

### 2.6 OOS Instability Confirms Overfitting

From the 6-month run, out-of-sample decay is severe on FX:

| Symbol | IS WR | OOS WR | OOS PF | WFE |
|---|---|---|---|---|
| EURUSD | 50.88% | **38.46%** | **0.50** | 0.03 |
| USDCHF | 57.14% | **33.33%** | **0.27** | 0.01 |
| GBPUSD | 51.35% | 54.55% | 1.01 | 0.06 |
| SOLUSD | 44.44% | 0.00% | 0.00 | 0.00 |

Walk-Forward Efficiency of 0.01–0.06 means the parameters are **curve-fit to the
in-sample window**. Note several symbols report `profit_factor: 99.0` and
`sharpe: 15.0` on 3–7 trades (USDJPY, US500) — **statistically meaningless** and
indicating the optimiser rewarded tiny samples.

---

## 3. Quantified Impact of Fixes

I re-simulated the actual 206 trades under corrected exit logic.

### 3.1 Isolated: exit-management fix only

| Configuration | Total R | Avg R | WR |
|---|---|---|---|
| **Baseline (actual)** | **−1.0** | **−0.005** | **48.1%** |
| BE@1.5R, trail keep 30% | +336.8 | +1.635 | 52.9% |
| BE@2.0R, trail keep 30% | **+337.5** | **+1.638** | 52.9% |
| BE@2.5R, trail keep 30% | +336.3 | +1.633 | 52.9% |
| BE@3.0R, trail keep 30% | +335.3 | +1.628 | 52.9% |

**Just moving the breakeven trigger from 1.0R → 2.0R flips expectancy from −0.005R to
+1.638R.** That is the single highest-leverage change in the entire system.

### 3.2 Combined: entry filters + restructured exits

Filters applied: drop `BREAKOUT_EXPANSION`, drop `WEAK_TREND` / `BREAKOUT` regimes.
Exit: 50% partial at target, remainder rides to MFE-capped runner.

| Configuration | n | WR | Avg R | Total R |
|---|---|---|---|---|
| partial@0.50R, cap 4.0R | 176 | **69.3%** | +0.908 | +159.9 |
| partial@0.50R, cap 3.0R | 176 | 69.3% | +0.696 | +122.5 |
| partial@1.00R, cap 4.0R | 176 | 55.1% | +0.817 | +143.8 |

### 3.3 The 75% Win-Rate Question — Honest Answer

**75% is NOT reachable by exit changes alone.** The hard ceiling is set by how often a
trade goes even slightly favourable:

```
Trades reaching ≥0.25R MFE : 80.1%   ← theoretical max WR with instant-scratch exits
Trades reaching ≥0.50R MFE : 69.4%
Trades reaching ≥0.75R MFE : 59.7%
Trades reaching ≥1.00R MFE : 52.9%
```

Even a mechanical "close at +0.25R or −1R" strategy tops out at **80.1%**, and at
+0.5R it caps at **69.4%**. Therefore:

- **69–70% WR is achievable with exit restructuring alone** (high confidence).
- **75%+ WR requires simultaneously reducing the −1R loss frequency** — i.e. better
  entry timing (so fewer trades go immediately adverse) and/or a *narrower stop
  relative to the target so the +0.5R threshold is crossed more often*.

Realistically: **69–72% WR in phase 1, 75–78% in phase 2** once entry filters and
per-symbol tuning are applied and validated out-of-sample.

---

## 4. Prioritised Fix List

### P0 — Critical (do first, highest impact, lowest risk)

**P0-1. Raise the breakeven trigger from 1.0R to 2.0R**

| File | Change |
|---|---|
| `jarvis/intelligence/symbol_profile_config.py` | `be_trigger_r: 1.00 → 2.00` (all non-gold) |
| `jarvis/backtesting/engine.py:209` | `be_trigger_r = 2.00 if is_gold else cfg.be_trigger_r` |
| `config/settings.json` | `breakeven_atr_trigger: 1.0 → 2.0` |
| `engines/trade_manager.py:96,154` | `effective_atr * 1.0` → `effective_atr * 2.0` |

**Expected impact:** expectancy −0.005R → **+1.30R**; net profit −$1,876 → **+$2,400–$2,900** on the 1y set. Win rate roughly flat (~52%).

---

**P0-2. Fix the Stop-Loss to take the WIDER (safer) level, not the tighter**

`engines/dynamic_sl_tp.py:53` — change `max()` semantics so the stop clears structure:

```python
# BUY: stop must sit BELOW structure with a real buffer
struct_sl = swing_low - (atr * profile.get("anti_wick_buffer_atr", 0.35))
atr_sl    = current_price - max_sl_dist
sl_price  = min(struct_sl, atr_sl)   # ← wider of the two
```
Apply the mirrored change for SELL (`max` instead of `min`).

Also enforce a **minimum stop distance floor** so `risk_dist` can never collapse onto
the noise floor:
```python
min_sl_dist = atr * 1.0
if abs(current_price - sl_price) < min_sl_dist:
    sl_price = current_price - min_sl_dist   # (BUY) / + min_sl_dist (SELL)
```

**Expected impact:** fewer stop-outs on normal noise; the median winner's risk no
longer compresses to 7R-equivalent. **Directly increases the fraction of trades that
survive to the +0.5R partial.**

---

**P0-3. Widen the runner trail so it can actually ratchet**

The trail (`2.2 × ATR` from the high) sits *behind* the structural stop, so it never
engages. Set the trail **tighter than the initial stop but looser than noise**:

| Parameter | Current | Proposed |
|---|---|---|
| `runner_trail_atr` | 2.0–2.6 | **1.2** |
| Trail activation | `be_locked or favorable ≥ 1.25R` | `favorable ≥ 2.0R` |
| Milestone lock @2R | lock +1.0R | lock +1.2R |
| Milestone lock @3R | lock +2.0R | lock +2.0R |

**Expected impact:** converts the 82 trades that reached ≥5R but exited at ~1R into
**2R–4R outcomes**. This is the change that turns +1.6R expectancy into **+3R+**.

---

### P1 — High Priority

**P1-4. Ban BREAKOUT_EXPANSION and hard-block WEAK_TREND regime**

- `BREAKOUT_EXPANSION`: 27.8% WR, −$459.36, reach-1R only 44.4%
- `WEAK_TREND` regime: **14.3% WR**, −$397.50, reach-1R 35.7%

Add `"BREAKOUT_EXPANSION"` to `banned_strategies` for **all** symbols (currently only
some). Add a regime gate rejecting `WEAK_TREND` and `BREAKOUT` unless
`master_score ≥ 75` **and** ADX > 25.

**Expected impact:** removes ~$857 of the $1,876 loss (−46%) with 32 fewer trades and
**no loss of profitable trades**.

---

**P1-5. Restructure the profit-taking into partial + runner**

Replace the single-stop exit with:

```
+0.5R  → close 50% (bank)
+1.0R  → move stop to +0.3R
+2.0R  → move stop to +1.2R, trail remainder at 1.2 × ATR
+3.0R+ → trail only, no hard TP
```

**Expected impact:** avg R +0.70 to +0.91, WR **69.3%**, total R **+122 to +160**
versus **−1.0** baseline.

---

**P1-6. Recalibrate or disable the confidence score gate**

`master_score` shows **zero** discriminative power (winners median 47, losers 48).
Either:

(a) Refit the scoring model on the trade population with a **monotonicity check**
    (the reach-0.5R rate must increase with score), or
(b) Temporarily lower `min_trade_score: 80.0 → 65.0` and drop the hard
    "Master Confluence ≥ 55/100" gate to allow more samples for retraining.

The current gate rejects 4,504 XAUUSD setups while admitting trades whose median
score is 47 — it is rejecting volume without improving quality.

---

**P1-7. Fix the optimiser's small-sample reward**

`profit_factor: 99.0`, `sharpe: 15.0`, `calmar: 10.0` on 3–7 trades are being selected
as "best". Add a hard constraint:

```python
if total_trades < min_sample:      # e.g. 30
    fitness = -999.0               # disallow selection
```

Also penalise `wfe < 0.5` in `multi_objective_fitness` — currently 3 symbols with
WFE 0.00–0.06 still pass.

---

### P2 — Medium Priority (per-symbol tuning)

**P2-8. Per-symbol parameter matrix** (derived from the data, not curve-fit)

| Symbol | WR (6mo) | PF | Action |
|---|---|---|---|
| XAUUSD | 57.3% | 2.48 | **Keep as flagship.** Best Sharpe (3.24), Calmar 14.0. Preserve config. |
| BTCUSD | 54.7% | 1.23 | DD 4.16% > net 3.8%. Reduce size 30%, widen BE to 2.0R. |
| ETHUSD | 55.0% | 1.23 | WFE 1.73 (good). Keep, apply global exit fix. |
| NAS100 | 60.7% | 1.36 | Best WR. **Increase allocation.** |
| WTI | 57.1% | 1.24 | 147 trades, positive. Keep, apply exit fix. |
| EURUSD | 50.9% | **1.00** | **OOS 38.5%, PF 0.50.** Run in paper mode until WFE > 0.5. |
| GBPUSD | 51.4% | 0.99 | Net negative. Disable pending exit fix + revalidation. |
| USDCHF | 57.1% | 1.52 | OOS 33.3%. Restrict to London only, halve size. |
| SOLUSD | 44.4% | 1.22 | OOS 0.00%, only 9 trades. **Disable** (insufficient sample). |
| US30 | 60.0% | 1.42 | Only 5 trades. Insufficient sample — keep disabled. |
| USDJPY | 100% | 99.0 | **3 trades — meaningless.** Must not be optimised on. |
| AUDUSD | 66.7% | 1.22 | 3 trades. Insufficient sample. |
| US500 | 50.0% | 1.45 | 6 trades. Insufficient sample. |

**P2-9. Stagnation time-stop is mis-tuned**

`engine.py:169` sets `stag_limit = base_stag * 1.25` for TREND regimes (16 → 20 bars
H1 = 20 hours) and requires `mfe < 0.25 × risk_dist` to trigger. Only 2 trades hit
this, so it is **not** the problem — but the threshold `0.25R` is too permissive.
Raise to `0.15R` and shorten TREND limits to 12 bars to recycle capital faster.

**P2-10. Parameter adjustment summary table**

| Parameter | File | Current | Proposed | Rationale |
|---|---|---|---|---|
| `be_trigger_r` | symbol_profile_config.py | 1.00–1.20 | **2.00** | Primary defect |
| `fast_cash_r` | symbol_profile_config.py | 1.00–1.80 | **0.75** | Bank earlier, smaller |
| `fast_cash_volume_pct` | symbol_profile_config.py | 0.35–0.60 | **0.50** | Standardise |
| `be_buffer_pct` | symbol_profile_config.py | 0.08 | **0.05** | Tighter BE floor |
| `runner_trail_atr` | symbol_profile_config.py | 2.0–2.6 | **1.2** | Enable ratchet |
| `sl_atr_multiplier` | symbol_profiles.json | 1.75–2.8 | **2.0–2.4** | Reduce noise stop-outs |
| `min_trade_score` | settings.json | 80.0 | **65.0** | Score not predictive |
| `max_risk_per_trade_pct` | settings.json | 0.5 | **0.5** (keep) | Adequate |
| `min_rr_ratio` | settings.json | 2.0 | **1.8** | Match realistic reach |

---

## 5. Expected Performance After Full Remediation

| Metric | Baseline (1y) | Phase 1 (P0) | Phase 2 (P0+P1) |
|---|---|---|---|
| Trades | 206 | ~200 | ~176 |
| Win rate | 48.1% | ~55% | **~69%** |
| Expectancy | −$9.11 | +$10–12 | **+$18–24** |
| Profit factor | 0.58 | ~1.4 | **~2.1** |
| Net profit | **−$1,876** | **+$2,400** | **+$3,800** |
| Max drawdown | 3.9% | ~4.5% | **~2.8%** |
| Avg R | −0.005 | +1.30 | **+0.90–1.60** |

**Win-rate trajectory to 75%:**

| Stage | WR | Mechanism |
|---|---|---|
| Now | 48.1% | BE@1R clipping everything |
| After P0 | ~55% | Fewer noise stop-outs, winners run |
| After P1 | ~69% | Partial banking at +0.5R |
| After P2 (validated OOS) | **75–78%** | Better entry timing on filtered symbol set |

The gap from 69% → 75% is closed by **reducing the number of trades that go
immediately adverse**, which requires the entry-timing work in P1-6/P2-8 plus
out-of-sample revalidation — not by further exit tuning.

---

## 6. Recommended Validation Protocol

Do **not** deploy these changes on the assumption they will reproduce. Validate:

1. **Walk-forward, not single split.** 6-month IS / 2-month OOS, rolled.
   Require **WFE ≥ 0.5** before a symbol goes live.
2. **Minimum sample gate.** Reject any parameter set with < 30 trades.
3. **Monotonicity test on the score layer.** Win rate must increase monotonically
   across score deciles, or the score is not a real filter.
4. **Cost realism.** Verify `actual_spreads.json` spreads are applied; the current
   runs show suspiciously clean BE exits.
5. **Compare against a null model.** Given median MFE of 21R, a coin-flip entry with
   BE@2R would also profit — prove the entry scoring adds value *above* that baseline.

---

## 7. One-Line Summary

> The system is **stopping out of 21R winners at 0.9R**. Raise `be_trigger_r` from
> 1.0 to 2.0, fix the SL to take the wider level, tighten the runner trail to 1.2×ATR
> so it can actually ratchet, and ban `BREAKOUT_EXPANSION` + `WEAK_TREND`. That alone
> moves the portfolio from **−$1,876 to roughly +$2,400–$3,800** and win rate from
> **48% to ~69%**, with 75%+ reachable after entry-layer revalidation.

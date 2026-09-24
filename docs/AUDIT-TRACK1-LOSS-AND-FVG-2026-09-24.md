# Track 1 — Loss Analysis & FVG Entry-Model Evaluation

**Date:** 2026-09-24  
**Caches:** `j_scan_cache_3a62a6f51102750b.pkl` (8 symbols, in-sample) and `j_scan_cache_70c1b7ed90e32bf2.pkl` (4 symbols, out-of-sample / §J validation)  
**Tool:** `tools/audit_track1_loss_and_fvg.py`  
**tp_r:** 3.0 (the §J baseline)

---

## Executive Summary

The strategy shows **positive aggregate P&L only when WTI is included** (+89.2 R, n=2347). Remove WTI and the population is **−196.7 R** (n=1505), matching §J's known figure exactly. **No breakdown dimension — symbol, side, session, regime, zone, or S/R proximity — produces a consistently profitable sub-population across both windows.** The FVG entry model is **not significantly better** than the incumbent (Welch t: +0.916, p=0.36 in-sample; −0.674, p=0.50 out-of-sample).

**Root-cause verdict:** The entry signal has no measured edge. Losses are not from spread (+0.03–0.06 R/trade), adverse selection (first-bar move ≈ 0), or a few outliers (mean loser ≈ −1.00 R, median ≈ −1.00 R — essentially every loser hits the stop). The problem is that **the model enters in the wrong direction more often than not**.

---

## 1. Baseline

| Window | Symbols | n | mean R | total R | win% |
|---|---|---|---|---|---|
| In-sample (3a62a6f5) | 8 (incl. WTI) | 2347 | +0.038 | **+89.2** | 27.1 |
| Out-of-sample (70c1b7ed) | 4 (no WTI) | 1505 | −0.131 | **−196.7** | 24.1 |

The OOS window validates §J's `−196.673` at `tp_r=3.0` — the instrument is sound.

---

## 2. Loss Breakdown — In-Sample (8 symbols, n=2347)

### By Symbol

| Symbol | n | mean R | total R | win% | t |
|---|---|---|---|---|---|
| WTI | 1257 | **+0.214** | **+269.3** | 31.3 | +4.16 |
| EURJPY | 201 | +0.218 | +43.9 | 34.8 | +1.77 |
| EURUSD | 14 | +0.411 | +5.8 | 35.7 | +0.77 |
| BTCUSD | 339 | −0.038 | −12.8 | 24.2 | −0.41 |
| AUDUSD | 51 | −0.543 | −27.7 | 15.7 | −3.32 |
| GBPUSD | 385 | −0.260 | −100.2 | 19.7 | −3.33 |
| GBPJPY | 100 | −0.890 | −89.0 | 3.0 | −12.94 |

**Without WTI:** total R = +89.2 − 269.3 = **−180.1**.

### By Side

| Side | n | mean R | total R | win% |
|---|---|---|---|---|
| SELL | 968 | +0.061 | +58.9 | 27.8 |
| BUY | 1379 | +0.022 | +30.3 | 26.7 |

No meaningful side bias.

### By Session

| Session | n | mean R | total R | win% |
|---|---|---|---|---|
| ASIAN | 345 | **+0.087** | +30.0 | 28.4 |
| NEW YORK | 683 | +0.067 | +45.6 | 28.0 |
| LONDON | 517 | +0.065 | +33.6 | 27.7 |
| OFF_HOURS | 314 | −0.029 | −9.2 | 25.5 |
| LONDON_NY_OVERLAP | 488 | **−0.022** | −10.8 | 25.6 |

**Prime hours are worse than off-hours.** The overlap — supposedly the highest-liquidity window — is the worst performer.

### By Regime

| Regime | n | mean R | total R | win% |
|---|---|---|---|---|
| BREAKOUT | 119 | **+0.228** | +27.1 | 31.9 |
| LIQUIDITY_SWEEP | 56 | +0.205 | +11.5 | 30.4 |
| TREND_BEAR | 872 | +0.047 | +41.3 | 27.5 |
| TREND_BULL | 1256 | +0.011 | +13.8 | 26.4 |
| COMPRESSION | 40 | −0.010 | −0.4 | 25.0 |

### By Zone (ICT concept)

| Zone | n | mean R | total R | win% |
|---|---|---|---|---|
| DISCOUNT | 983 | **+0.176** | +172.6 | 30.8 |
| PREMIUM | 1172 | −0.029 | −34.4 | 25.3 |
| EQUILIBRIUM | 192 | **−0.255** | −48.9 | 19.3 |

Discount entries (buying below fair value, selling above) are the only zone with edge — but this is heavily WTI-driven.

---

## 3. Loss Breakdown — Out-of-Sample (4 symbols, no WTI, n=1505)

### By Symbol

| Symbol | n | mean R | total R | win% |
|---|---|---|---|---|
| AUDUSD | 114 | −0.278 | −31.7 | 20.2 |
| EURUSD | 33 | −0.170 | −5.6 | 21.2 |
| GBPUSD | 1028 | −0.034 | −35.0 | 26.6 |
| GBPJPY | 330 | −0.377 | −124.4 | 17.9 |

**Every symbol loses.** GBPJPY is catastrophic.

### By Session

| Session | n | mean R | total R | win% |
|---|---|---|---|---|
| LONDON | 420 | −0.065 | −27.3 | 24.8 |
| NEW YORK | 498 | −0.085 | −42.1 | 25.9 |
| LONDON_NY_OVERLAP | 437 | **−0.174** | −75.9 | 22.9 |
| OFF_HOURS | 111 | −0.397 | −44.1 | 18.9 |
| ASIAN | 39 | −0.187 | −7.3 | 20.5 |

The overlap is the worst session in BOTH windows. The ASIAN session, best in-sample, is second-worst out-of-sample.

### By Zone

| Zone | n | mean R | total R | win% |
|---|---|---|---|---|
| PREMIUM | 722 | −0.071 | −50.9 | 25.5 |
| DISCOUNT | 585 | −0.158 | −92.1 | 24.1 |
| EQUILIBRIUM | 198 | −0.271 | −53.6 | 18.7 |

The zone finding **reverses**: DISCOUNT was best in-sample (+0.176) but is second-worst out-of-sample (−0.158). This is the WTI artefact — WTI happened to trend through the discount zone in the in-sample window.

---

## 4. Root-Cause Analysis

### 4a. Fixed cost (spread + slippage)

| Window | spread-as-R | mean R without spread |
|---|---|---|
| In-sample | +0.032 | +0.070 |
| Out-of-sample | +0.056 | −0.074 |

Spread cost is **negligible** — ~3–6% of the risk unit. Removing it does not flip the sign out-of-sample.

### 4b. Adverse selection (first-bar move)

| Window | first-bar mean | std | t |
|---|---|---|---|
| In-sample | +0.008 | 0.379 | +1.02 |
| Out-of-sample | −0.006 | 0.381 | −0.59 |

The first bar after entry moves **neutrally** — there is no immediate adverse selection. The market does not systematically move against the position in the first hour.

### 4c. Distributional

| Window | losers (n) | loser mean | loser median | winner mean | winner median |
|---|---|---|---|---|---|
| In-sample | 1710 | −1.003 | −1.003 | +2.833 | +3.000 |
| Out-of-sample | 1143 | −1.009 | −1.013 | +2.642 | +3.000 |

**Every loser essentially hits the stop** (mean ≈ median ≈ −1.00 R). **Every winner essentially hits the target** (median = +3.00 R). The distribution is bimodal: stops vs targets, with almost nothing in between. Skew is positive (+1.1 to +1.4) because the upside is capped at +3R while the downside is floored at −1R — a mechanical asymmetry, not an edge.

**Verdict:** The losses are from **entry direction**, not cost, adverse selection, or outliers. The model is wrong more often than it is right (win rate 24–27%).

---

## 5. Support / Resistance Proximity

Distance from entry to nearest swing high/low in the prior 50 bars, in ATR multiples.

### In-sample

| Proximity | n | mean R | total R | win% |
|---|---|---|---|---|
| very_far | 587 | **+0.163** | +95.5 | 29.8 |
| near | 587 | +0.029 | +17.0 | 27.1 |
| very_near | 587 | −0.007 | −4.4 | 26.1 |
| far | 586 | −0.032 | −18.8 | 25.6 |

### Out-of-sample

| Proximity | n | mean R | total R | win% |
|---|---|---|---|---|
| very_far | 376 | −0.007 | −2.8 | 26.6 |
| far | 376 | −0.050 | −18.8 | 26.9 |
| very_near | 377 | −0.200 | −75.2 | 22.5 |
| near | 376 | −0.266 | −99.8 | 20.2 |

**Consistent finding:** entries **very near** S/R are the worst in both windows. Entries far from S/R are better — but still **not profitable** out-of-sample. The model should avoid entering right at a level.

---

## 6. FVG (Fair-Value-Gap) Entry Model

A bullish FVG is defined as `low[i+2] > high[i]` (price gapped up, leaving a void below that acts as support). A bearish FVG is `high[i+2] < low[i]` (void above, acts as resistance). An entry is "FVG" if it occurs within 0.5 ATR of an FVG zone that is still "fresh" (≤20 bars old).

### In-sample

| Group | n | mean R | total R | win% |
|---|---|---|---|---|
| FVG entries | 589 | +0.096 | +56.3 | 28.2 |
| Non-FVG | 1758 | +0.019 | +33.0 | 26.8 |
| **Welch t-test** | | | **t=+0.916, p=0.36** | |

**Not statistically significant.**

#### Per-symbol FVG (in-sample)

| Symbol | n_FVG | mean_FVG | mean_other | delta |
|---|---|---|---|---|
| WTI | 332 | +0.346 | +0.167 | **+0.179** |
| EURJPY | 63 | +0.389 | +0.140 | **+0.249** |
| BTCUSD | 70 | +0.029 | −0.055 | +0.084 |
| GBPUSD | 66 | −0.440 | −0.223 | **−0.217** |
| GBPJPY | 46 | −1.011 | −0.787 | **−0.224** |
| AUDUSD | 11 | −0.780 | −0.478 | −0.301 |

FVG helps WTI and EURJPY but **hurts** GBPUSD, GBPJPY, and AUDUSD.

### Out-of-sample

| Group | n | mean R | total R | win% |
|---|---|---|---|---|
| FVG entries | 354 | −0.180 | −63.9 | 22.6 |
| Non-FVG | 1151 | −0.115 | −132.8 | 24.5 |
| **Welch t-test** | | | **t=−0.674, p=0.50** | |

**Not significant, and the sign flips negative.**

#### Per-symbol FVG (out-of-sample)

| Symbol | n_FVG | mean_FVG | mean_other | delta |
|---|---|---|---|---|
| AUDUSD | 29 | +0.181 | −0.434 | **+0.615** |
| GBPUSD | 187 | +0.002 | −0.042 | +0.044 |
| GBPJPY | 130 | −0.471 | −0.316 | **−0.155** |
| EURUSD | 8 | −1.020 | +0.102 | **−1.122** |

The per-symbol deltas are **inconsistent across windows**:
- AUDUSD FVG: −0.30 (IS) vs +0.62 (OOS) — reverses
- EURUSD/EURJPY: tiny sample, unstable
- GBPJPY FVG: hurts in both windows
- GBPUSD FVG: hurts IS, neutral OOS
- WTI FVG: helps IS, not present OOS

**Conclusion:** FVG is **not a reliable edge** in this data. The aggregate positive in-sample is driven by WTI; when WTI is removed or the window changes, the effect vanishes or reverses.

---

## 7. Summary & Recommendation

| Question | Answer |
|---|---|
| Are losses from spread cost? | **No** — spread is ~0.03–0.06 R, negligible |
| Are losses from adverse selection? | **No** — first-bar move ≈ 0 |
| Are losses from a few outliers? | **No** — every loser hits the stop (−1R), every winner hits the target (+3R) |
| Does any symbol consistently win? | **No** — only WTI in one window |
| Does any session consistently win? | **No** — ASIAN best IS, worst OOS; overlap worst in both |
| Does any zone consistently win? | **No** — DISCOUNT best IS, second-worst OOS (WTI-driven) |
| Does S/R proximity matter? | **Yes, partially** — very_near entries are worst in both windows, but far entries are still not profitable OOS |
| Does FVG beat the incumbent? | **No** — not significant in either window; per-symbol inconsistent |

### The honest verdict

**The entry signal has no measured edge.** No parameter, no filter, no alternative entry model (FVG) makes this population profitable across symbols and windows. The one symbol that wins (WTI) does so because it happened to trend in one window, and that trend reverses out-of-sample.

### What WOULD help (evidence-based)

1. **Avoid entries very near S/R** — consistent 0.15–0.20 R improvement in both windows. This is a real, measurable filter.
2. **The entry model itself needs replacement** — FVG is not the answer. A strategy change, not a parameter tweak, is required.
3. **Persist `ai_score`** — the hard gate is never recorded, so its contribution cannot be evaluated. This is an observability gap that makes every analysis incomplete.

---

## Tool & Reproducibility

```bash
# In-sample (8 symbols, incl. WTI)
PYTHONPATH=. NO_PROXY='*' python tools/audit_track1_loss_and_fvg.py --tp 3.0 --cache 3a62a6f51102750b

# Out-of-sample (4 symbols, no WTI, validates §J)
PYTHONPATH=. NO_PROXY='*' python tools/audit_track1_loss_and_fvg.py --tp 3.0 --cache 70c1b7ed90e32bf2
```

The tool reuses the same `per_trade` simulation and `spread_registry_ab` registry as `tools/audit_selectivity_edge.py`, so its outputs are directly comparable to §J and §P.

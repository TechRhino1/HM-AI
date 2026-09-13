# Six-Month Multi-Mode Backtest on Real MT5 Data

Source: live XMGlobal-MT5 feed. No synthetic bars anywhere in this report — `MT5HistoryFetcher` refuses to substitute them, and the fetch manifest records `"synthetic": false` per series.

## Executive summary

- **All 3 modes lost money over the window tested.** Negative expectancy in: SWING, DAY_TRADING, SCALP.
- Best mode: **SWING** at -0.0830 R per trade (752 trades). Worst: **DAY_TRADING** at -0.2839 R (917 trades).
- No mode reached the 75 % win-rate target: SWING 46.4%, DAY_TRADING 41.3%, SCALP 43.0%.
- Prioritization in **SWING**: does NOT rank correctly (spread +0.0068 R, 9/20 symbols agree, top-1 +0.0410 R).
- Prioritization in **DAY_TRADING**: ranks correctly (spread +0.0636 R, 13/20 symbols agree, top-1 +0.0958 R).
- Prioritization in **SCALP**: does NOT rank correctly (spread -0.0070 R, 9/20 symbols agree, top-1 +0.0138 R).
- **The utility score's magnitude is nearly uninformative** (see section 4). Raising the utility cut from 0.5 to 25 moves expectancy by at most 0.02 R in any mode; essentially all of the separation comes from the binary `ev > 0 and directional` gate. The ranking's *direction* is right, its *magnitude* is not usable as a filter.

### Scope and caveats

- **SCALP is not a six-month result.** Its `timing` role is M1, which this broker stores for only ~67 days. SCALP is therefore reported over ~67 days and is not comparable in window length to the other two modes.
- **No per-mode recalibration was applied.** Profiles in `config/winrate_profiles.json` are keyed by symbol only and were calibrated on H1, so they are not valid for the M15 and M5 primaries. All three modes were therefore run with `wr_profile=None` (the legacy entry stack), which keeps them comparable to each other.
- **The arbiter is not exercised by the backtest.** It lives only in the live radar sweep, so it is validated separately in section 2 against counterfactual outcomes.

## 1. Performance by trading mode

| Mode | Timeframes | Symbols | Trades | Expectancy R | Total R | WR % | PF | MaxDD R | Positive symbols |
|---|---|---|---|---|---|---|---|---|---|
| **SWING** | D1 / H4 / H1 / H4 / M15  (primary H1) | 20 | 752 | -0.0830 | -62.45 | 46.4 | 0.84 | 83.45 | 4/20 |
| **DAY_TRADING** | H4 / H1 / M15 / H1 / M5  (primary M15) | 20 | 917 | -0.2839 | -260.29 | 41.3 | 0.53 | 263.75 | 0/20 |
| **SCALP** | H1 / M15 / M5 / M5 / M1  (primary M5) | 20 | 855 | -0.2542 | -217.34 | 43.0 | 0.58 | 223.30 | 2/20 |

### Per-symbol detail

#### SWING — D1 / H4 / H1 / H4 / M15  (primary H1)

Pooled 752 trades, expectancy **-0.0830 R**, total **-62.45 R**, WR **46.4%**, payoff **0.97**, max drawdown **83.45 R**. **4/20** symbols positive.

> Concentration: **WTI** supplies **53.6%** of gross positive R.

| Symbol | Window | Bars | Trades | Exp R | Total R | WR % | PF | MaxDD R | Net $ |
|---|---|---|---|---|---|---|---|---|---|
| WTI | 179.0d | 2932 | 96 | +0.1540 | +14.78 | 56.2 | 1.35 | 6.91 | +315.20 |
| ETHUSD | 182.0d | 4369 | 5 | +1.0594 | +5.30 | 100.0 | inf | 0.00 | +254.51 |
| SOLUSD | 182.0d | 4369 | 32 | +0.1622 | +5.19 | 56.2 | 1.41 | 6.79 | +95.52 |
| NAS100 | 179.0d | 2944 | 20 | +0.1167 | +2.33 | 55.0 | 1.26 | 4.02 | +53.36 |
| US500 | 179.0d | 2944 | 26 | -0.0068 | -0.18 | 53.9 | 0.98 | 4.56 | -64.23 |
| EURJPY | 179.0d | 3097 | 49 | -0.0137 | -0.67 | 51.0 | 0.97 | 6.62 | -242.22 |
| USDJPY | 179.0d | 3097 | 1 | -1.0152 | -1.02 | 0.0 | 0.00 | 0.00 | -50.42 |
| GER40 | 179.0d | 2867 | 17 | -0.1784 | -3.03 | 47.1 | 0.66 | 4.46 | -115.95 |
| GBPJPY | 179.0d | 3097 | 21 | -0.1579 | -3.31 | 47.6 | 0.64 | 5.88 | -100.44 |
| XAGUSD | 179.0d | 2932 | 9 | -0.4478 | -4.03 | 33.3 | 0.33 | 5.86 | -468.81 |
| EURUSD | 179.0d | 3097 | 12 | -0.3394 | -4.07 | 33.3 | 0.39 | 5.33 | -146.91 |
| USDCHF | 179.0d | 3097 | 11 | -0.4364 | -4.80 | 36.4 | 0.30 | 4.17 | -144.94 |
| GBPUSD | 179.0d | 3097 | 96 | -0.0602 | -5.78 | 52.1 | 0.86 | 10.74 | -347.61 |
| XAUUSD | 179.0d | 2932 | 27 | -0.2208 | -5.96 | 33.3 | 0.66 | 7.96 | -439.96 |
| US30 | 179.0d | 2944 | 14 | -0.4285 | -6.00 | 42.9 | 0.28 | 7.98 | -190.31 |
| UK100 | 179.0d | 2820 | 20 | -0.3119 | -6.24 | 40.0 | 0.49 | 8.50 | -144.00 |
| USDCAD | 179.0d | 3097 | 77 | -0.0907 | -6.98 | 49.4 | 0.82 | 8.69 | -446.77 |
| AUDUSD | 179.0d | 3097 | 34 | -0.2744 | -9.33 | 41.2 | 0.53 | 12.97 | -222.07 |
| NZDUSD | 179.0d | 3097 | 87 | -0.1077 | -9.37 | 43.7 | 0.80 | 12.70 | -327.17 |
| BTCUSD | 182.0d | 4369 | 98 | -0.1967 | -19.28 | 30.6 | 0.72 | 20.15 | -330.92 |

#### DAY_TRADING — H4 / H1 / M15 / H1 / M5  (primary M15)

Pooled 917 trades, expectancy **-0.2839 R**, total **-260.29 R**, WR **41.3%**, payoff **0.75**, max drawdown **263.75 R**. **0/20** symbols positive.

> Concentration: no symbol produced positive total R.

| Symbol | Window | Bars | Trades | Exp R | Total R | WR % | PF | MaxDD R | Net $ |
|---|---|---|---|---|---|---|---|---|---|
| USDJPY | 179.9d | 12465 | 0 | +0.0000 | +0.00 | 0.0 | 0.00 | 0.00 | +0.00 |
| ETHUSD | 182.8d | 17520 | 30 | -0.2452 | -7.36 | 40.0 | 0.57 | 6.91 | -254.95 |
| GER40 | 179.8d | 11415 | 85 | -0.1081 | -9.19 | 48.2 | 0.77 | 14.75 | -261.85 |
| UK100 | 179.8d | 11352 | 70 | -0.1464 | -10.25 | 48.6 | 0.72 | 10.68 | -418.77 |
| XAUUSD | 179.8d | 11801 | 99 | -0.1044 | -10.34 | 48.5 | 0.80 | 13.88 | -409.25 |
| NAS100 | 179.8d | 11850 | 32 | -0.3847 | -12.31 | 43.8 | 0.33 | 14.89 | -440.95 |
| US500 | 179.8d | 11850 | 52 | -0.2395 | -12.45 | 48.1 | 0.54 | 13.19 | -409.88 |
| XAGUSD | 179.8d | 11800 | 90 | -0.1480 | -13.32 | 50.0 | 0.70 | 14.36 | -404.49 |
| WTI | 179.8d | 11735 | 60 | -0.2301 | -13.80 | 48.3 | 0.56 | 15.72 | -425.20 |
| AUDUSD | 179.9d | 12465 | 42 | -0.3319 | -13.94 | 40.5 | 0.48 | 16.39 | -412.64 |
| EURJPY | 179.9d | 12465 | 25 | -0.5613 | -14.03 | 32.0 | 0.22 | 12.97 | -405.02 |
| NZDUSD | 179.9d | 12458 | 22 | -0.6379 | -14.03 | 22.7 | 0.23 | 12.98 | -406.64 |
| BTCUSD | 182.8d | 17520 | 36 | -0.4027 | -14.50 | 19.4 | 0.50 | 15.50 | -416.53 |
| USDCHF | 179.9d | 12463 | 48 | -0.3021 | -14.50 | 41.7 | 0.53 | 15.25 | -416.59 |
| US30 | 179.8d | 11850 | 49 | -0.2970 | -14.55 | 34.7 | 0.55 | 15.77 | -287.24 |
| GBPUSD | 179.9d | 12465 | 40 | -0.3711 | -14.84 | 40.0 | 0.42 | 13.75 | -407.53 |
| GBPJPY | 179.9d | 12463 | 24 | -0.6888 | -16.53 | 20.8 | 0.16 | 16.73 | -416.21 |
| USDCAD | 179.9d | 12462 | 22 | -0.7753 | -17.06 | 18.2 | 0.14 | 17.03 | -403.89 |
| SOLUSD | 182.8d | 17520 | 47 | -0.3700 | -17.39 | 34.0 | 0.44 | 17.62 | -406.96 |
| EURUSD | 179.9d | 12465 | 44 | -0.4525 | -19.91 | 36.4 | 0.37 | 22.01 | -419.54 |

#### SCALP — H1 / M15 / M5 / M5 / M1  (primary M5)

Pooled 855 trades, expectancy **-0.2542 R**, total **-217.34 R**, WR **43.0%**, payoff **0.77**, max drawdown **223.30 R**. **2/20** symbols positive.

> Concentration: **ETHUSD** supplies **83.9%** of gross positive R.

| Symbol | Window | Bars | Trades | Exp R | Total R | WR % | PF | MaxDD R | Net $ |
|---|---|---|---|---|---|---|---|---|---|
| ETHUSD | 69.0d | 19811 | 15 | +0.1285 | +1.93 | 53.3 | 1.27 | 2.00 | +9.56 |
| SOLUSD | 69.0d | 19811 | 41 | +0.0091 | +0.37 | 56.1 | 1.02 | 8.00 | -3.03 |
| USDJPY | 67.2d | 14171 | 0 | +0.0000 | +0.00 | 0.0 | 0.00 | 0.00 | +0.00 |
| WTI | 67.2d | 13469 | 0 | +0.0000 | +0.00 | 0.0 | 0.00 | 0.00 | +0.00 |
| GER40 | 67.2d | 13222 | 48 | -0.1130 | -5.42 | 52.1 | 0.76 | 4.75 | -414.19 |
| US30 | 67.2d | 13534 | 58 | -0.1007 | -5.84 | 51.7 | 0.80 | 8.74 | -268.85 |
| XAUUSD | 67.2d | 13552 | 278 | -0.0309 | -8.60 | 56.1 | 0.93 | 17.78 | -411.84 |
| NZDUSD | 67.2d | 14150 | 11 | -1.0603 | -11.66 | 18.2 | 0.08 | 9.83 | -414.78 |
| USDCAD | 67.2d | 14171 | 8 | -1.4840 | -11.87 | 0.0 | 0.00 | 10.04 | -423.81 |
| EURUSD | 67.2d | 14171 | 11 | -1.2081 | -13.29 | 9.1 | 0.08 | 12.03 | -418.87 |
| XAGUSD | 67.2d | 13551 | 49 | -0.2857 | -14.00 | 46.9 | 0.48 | 14.52 | -410.94 |
| GBPJPY | 67.2d | 14166 | 22 | -0.6498 | -14.30 | 27.3 | 0.24 | 13.03 | -417.62 |
| BTCUSD | 69.0d | 19811 | 89 | -0.1614 | -14.36 | 32.6 | 0.76 | 18.76 | -407.44 |
| AUDUSD | 67.2d | 14171 | 21 | -0.7294 | -15.32 | 23.8 | 0.31 | 14.14 | -401.36 |
| US500 | 67.2d | 13534 | 36 | -0.4465 | -16.08 | 33.3 | 0.33 | 16.03 | -403.11 |
| EURJPY | 67.2d | 14171 | 30 | -0.5543 | -16.63 | 30.0 | 0.31 | 15.51 | -403.39 |
| GBPUSD | 67.2d | 14170 | 27 | -0.6259 | -16.90 | 22.2 | 0.27 | 15.51 | -409.87 |
| UK100 | 67.2d | 13116 | 46 | -0.3676 | -16.91 | 34.8 | 0.44 | 15.83 | -405.35 |
| USDCHF | 67.2d | 14167 | 14 | -1.2383 | -17.34 | 7.1 | 0.05 | 16.14 | -430.03 |
| NAS100 | 67.2d | 13534 | 51 | -0.4143 | -21.13 | 31.4 | 0.41 | 24.04 | -418.25 |

## 2. Does the arbiter select the highest-priority trades?

The arbiter is invoked only in the live radar sweep, never by `BacktestEngine`, so it is validated against counterfactual per-candidate outcomes: every candidate is replayed under one fixed exit schedule and scored with the production utility formula, then tested against its realised R.

| Mode | Candidates | Q5−Q1 R | Symbols agreeing | Top-1 vs rest | Inverse control | Pick beats rest % |
|---|---|---|---|---|---|---|
| **SWING** | 46583 | +0.0068 | 9/20 | +0.0410 | -0.1180 | 44.5 |
| **DAY_TRADING** | 182358 | +0.0636 | 13/20 | +0.0958 | -0.1272 | 44.7 |
| **SCALP** | 203534 | -0.0070 | 9/20 | +0.0138 | -0.0754 | 47.2 |

### SWING

Verdict: **Does NOT rank correctly**. Utility quintile spread **+0.0068 R**, **9/20** symbols agree on its direction, top-1 pick vs rejected set **+0.0410 R** (better than the rest in only **44.5%** of sets), inverse control **-0.1180 R**.

| Utility quintile | n | median utility | Exp R | WR |
|---|---|---|---|---|
| Q1 | 9324 | 21.50 | -0.0742 | 0.374 |
| Q2 | 9312 | 45.45 | -0.0114 | 0.400 |
| Q3 | 9312 | 67.19 | -0.1398 | 0.348 |
| Q4 | 9312 | 96.78 | -0.0956 | 0.366 |
| Q5 | 9323 | 152.83 | -0.0674 | 0.377 |

| Grade | n | Exp R | Total R | WR |
|---|---|---|---|---|
| GRADE A | 9387 | -0.0791 | -742.83 | 0.372 |
| GRADE A+ | 15641 | -0.0386 | -603.21 | 0.388 |
| GRADE B | 12463 | -0.0339 | -422.79 | 0.390 |
| GRADE C | 9092 | -0.2034 | -1849.02 | 0.325 |

| Regime | n | Exp R | Q5−Q1 R | Symbols agreeing | Top-1 vs rest |
|---|---|---|---|---|---|
| BREAKOUT | 5730 | -0.0549 | +0.1068 | 10/20 | +0.0425 |
| COMPRESSION | 1218 | +0.0060 | +0.0938 | 9/17 | -0.0194 |
| LIQUIDITY_SWEEP | 1142 | -0.0834 | +0.3112 | 15/20 | -0.1126 |
| LOW_VOLATILITY | 268 | +0.1376 | +0.3020 | 5/6 | -0.3400 |
| TREND_BEAR | 18134 | -0.0829 | +0.0542 | 12/20 | +0.0949 |
| TREND_BULL | 20076 | -0.0874 | -0.0967 | 5/20 | +0.0032 |

Actionable subset: n=14186 expR=-0.0361  |  Non-actionable: n=32397 expR=-0.0959

### DAY_TRADING

Verdict: **Ranks correctly**. Utility quintile spread **+0.0636 R**, **13/20** symbols agree on its direction, top-1 pick vs rejected set **+0.0958 R** (better than the rest in only **44.7%** of sets), inverse control **-0.1272 R**.

| Utility quintile | n | median utility | Exp R | WR |
|---|---|---|---|---|
| Q1 | 36479 | 18.07 | -0.1342 | 0.354 |
| Q2 | 36468 | 41.40 | -0.1008 | 0.367 |
| Q3 | 36466 | 59.60 | -0.0946 | 0.370 |
| Q4 | 36468 | 85.04 | -0.0983 | 0.368 |
| Q5 | 36477 | 135.30 | -0.0706 | 0.378 |

| Grade | n | Exp R | Total R | WR |
|---|---|---|---|---|
| GRADE A | 40986 | -0.0770 | -3154.22 | 0.376 |
| GRADE A+ | 54756 | -0.0577 | -3160.83 | 0.382 |
| GRADE B | 51892 | -0.0698 | -3620.42 | 0.379 |
| GRADE C | 34724 | -0.2375 | -8248.32 | 0.316 |

| Regime | n | Exp R | Q5−Q1 R | Symbols agreeing | Top-1 vs rest |
|---|---|---|---|---|---|
| BREAKOUT | 25080 | -0.1203 | -0.0114 | 11/20 | +0.0649 |
| COMPRESSION | 7057 | -0.1702 | +0.2049 | 12/20 | +0.1484 |
| HIGH_VOLATILITY | 545 | -0.1774 | +0.3276 | 5/9 | +0.3088 |
| LIQUIDITY_SWEEP | 5347 | -0.0825 | +0.0168 | 8/20 | +0.1223 |
| LOW_VOLATILITY | 2305 | -0.1527 | +0.2652 | 14/20 | +0.1876 |
| TREND_BEAR | 68956 | -0.0269 | +0.0750 | 12/20 | +0.0520 |
| TREND_BULL | 73068 | -0.1536 | +0.0331 | 12/20 | +0.0937 |

Actionable subset: n=54069 expR=-0.0506  |  Non-actionable: n=128289 expR=-0.1204

### SCALP

Verdict: **Does NOT rank correctly**. Utility quintile spread **-0.0070 R**, **9/20** symbols agree on its direction, top-1 pick vs rejected set **+0.0138 R** (better than the rest in only **47.2%** of sets), inverse control **-0.0754 R**.

| Utility quintile | n | median utility | Exp R | WR |
|---|---|---|---|---|
| Q1 | 40715 | 13.78 | +0.1327 | 0.465 |
| Q2 | 40703 | 34.24 | +0.1040 | 0.451 |
| Q3 | 40703 | 49.69 | +0.1397 | 0.466 |
| Q4 | 40703 | 71.42 | +0.0914 | 0.447 |
| Q5 | 40710 | 112.62 | +0.1257 | 0.459 |

| Grade | n | Exp R | Total R | WR |
|---|---|---|---|---|
| GRADE A | 44364 | +0.1190 | +5280.10 | 0.457 |
| GRADE A+ | 57926 | +0.1440 | +8340.25 | 0.466 |
| GRADE B | 60992 | +0.1250 | +7625.97 | 0.460 |
| GRADE C | 40252 | +0.0723 | +2910.84 | 0.443 |

| Regime | n | Exp R | Q5−Q1 R | Symbols agreeing | Top-1 vs rest |
|---|---|---|---|---|---|
| BREAKOUT | 30546 | +0.1223 | +0.0181 | 11/20 | +0.0305 |
| COMPRESSION | 11634 | +0.1120 | -0.0047 | 12/20 | +0.0177 |
| HIGH_VOLATILITY | 1516 | +0.0635 | -0.3302 | 9/19 | +0.0374 |
| LIQUIDITY_SWEEP | 5938 | +0.0990 | -0.0005 | 11/20 | +0.0423 |
| LOW_VOLATILITY | 3596 | +0.1592 | +0.1082 | 14/20 | +0.0178 |
| TREND_BEAR | 73095 | +0.0933 | -0.0471 | 8/20 | +0.0402 |
| TREND_BULL | 77209 | +0.1430 | -0.0091 | 9/20 | +0.0212 |

Actionable subset: n=59456 expR=+0.1521  |  Non-actionable: n=144078 expR=+0.1049

## 3. Candidate-level vs engine-level expectancy

These two numbers measure different things and are **not** a like-for-like comparison. The candidate figure replays *every* signal the scanner emitted under one fixed schedule (`tp_r = 1.5`, no score floor, no trailing, no breakeven). The engine figure is what the deployed entry stack and exit policy actually produced. A gap therefore says the deployed execution differs from a naive fixed-target execution — it does not by itself say which is wrong.

| Mode | Candidate-level Exp R | Engine Exp R | Gap |
|---|---|---|---|
| SWING | -0.0777 | -0.0830 | -0.0053 |
| DAY_TRADING | -0.0997 | -0.2839 | -0.1842 |
| SCALP | +0.1187 | -0.2542 | -0.3729 |

## 4. Robustness of the prioritization verdicts

A verdict that survives only one arbitrary parameter choice is not a finding. These re-run the same question under different choices, from the saved replay frames (no new replay).

### 4.1 Top-k vs the rejected set

The arbiter's contract is top-1, but if its information sits in the top few, top-1 alone understates it. Delta is the selected set's mean R minus the rejected set's.

| Mode | k=1 | k=2 | k=3 | k=5 | k=10 |
|---|---|---|---|---|---|
| SWING | +0.0410 | +0.0232 | +0.0229 | +0.0240 | +0.0664 |
| DAY_TRADING | +0.0958 | +0.0872 | +0.0830 | +0.0804 | +0.1200 |
| SCALP | +0.0138 | +0.0200 | +0.0230 | +0.0299 | +0.0332 |

### 4.2 Bucket-count sensitivity

A quintile spread can be an artefact of the cut points. Stable sign + stable symbol agreement across 3/4/5/10 buckets means it is not.

| Mode | q=3 | q=4 | q=5 | q=10 |
|---|---|---|---|---|
| SWING | -0.0288 (9/20) | +0.0067 (12/20) | +0.0068 (9/20) | -0.0478 (8/20) |
| DAY_TRADING | +0.0401 (12/20) | +0.0590 (14/20) | +0.0636 (13/20) | +0.0535 (14/20) |
| SCALP | -0.0122 (9/20) | -0.0070 (9/20) | -0.0071 (9/20) | -0.0037 (9/20) |

### 4.3 Per-symbol rank correlation, and the actionable-only population

Spearman between utility and realised R, computed inside each symbol. The `actionable-only` column repeats the exercise on the population the arbiter actually trades.

| Mode | Per-symbol rho (mean) | Symbols positive | Actionable n | Actionable exp R | Actionable top-1 delta | Actionable spread |
|---|---|---|---|---|---|---|
| SWING | +0.0456 | 14/20 | 14186 | -0.0361 | -0.0064 | -0.0668 |
| DAY_TRADING | +0.0792 | 18/20 | 54069 | -0.0506 | +0.0345 | +0.0286 |
| SCALP | +0.0479 | 18/20 | 59456 | +0.1521 | -0.0096 | +0.0231 |

### 4.4 Threshold sweep — the decisive test

If the utility score ranks well, cutting higher should monotonically improve the surviving subset. It does not: the entire step is at the first cut (0.0 → 0.5), which merely drops the candidates the arbiter already assigns utility 0.

| Utility ≥ | SWING n | SWING exp R | DAY_TRADING n | DAY_TRADING exp R | SCALP n | SCALP exp R |
|---|---|---|---|---|---|---|
| 0 | 46583 | -0.0777 | 182358 | -0.0997 | 203534 | +0.1187 |
| 0.5 | 37491 | -0.0472 | 147668 | -0.0673 | 163388 | +0.1303 |
| 1 | 37491 | -0.0472 | 147626 | -0.0673 | 163266 | +0.1301 |
| 1.3 | 37490 | -0.0472 | 147604 | -0.0673 | 163209 | +0.1301 |
| 1.8 | 37487 | -0.0473 | 147540 | -0.0673 | 163051 | +0.1300 |
| 2.5 | 37473 | -0.0474 | 147442 | -0.0672 | 162833 | +0.1300 |
| 3.5 | 37453 | -0.0473 | 147276 | -0.0671 | 162452 | +0.1299 |
| 5 | 37402 | -0.0475 | 146896 | -0.0667 | 161746 | +0.1304 |
| 7.5 | 37256 | -0.0477 | 145908 | -0.0659 | 160208 | +0.1313 |
| 10 | 37060 | -0.0491 | 144554 | -0.0652 | 158214 | +0.1322 |
| 15 | 36395 | -0.0491 | 141125 | -0.0643 | 153215 | +0.1323 |
| 25 | 34225 | -0.0504 | 131200 | -0.0620 | 139471 | +0.1306 |

**Reading:** across a 50× range of the utility threshold, expectancy moves by at most 0.02 R in any mode. The utility score is therefore not usable as a continuous filter — what separates the populations is the binary `ev > 0 and directional` condition, worth roughly 0.03–0.05 R.

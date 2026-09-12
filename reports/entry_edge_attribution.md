# Entry-Edge Attribution — where does the signal actually carry edge?

**Data:** 16 symbols, 18,998 candidates, 18,998 simulated outcomes  
**Method:** every candidate replayed under its symbol's deployed geometry (identical exits), then bucketed by feature  
**Stop slippage charged:** 0.5 pips  
**Generated:** 2026-09-12T18:00:35.557167+00:00

## 0. Why this exists

Three geometry hypotheses have been tested and settled: **metadata** (confirmed, fixed), **the win-rate objective** (confirmed, fixed by the reachability guard), and **grid width** (tested and refuted — widening the grid made results 5.46R worse and the pile-up simply moved to the new edge). The aggregate out-of-sample result is still negative. Geometry can stop the system hurting itself; it cannot manufacture an edge. So the question is now whether any entry feature carries one.

**How to read this.** `rho` is the trade-level Spearman rank correlation between the feature and R. With ~19k candidates almost any gradient is statistically significant, so significance is not the test — the test is the *size* of the expectancy spread and, decisively, whether it reappears **inside the individual symbols**. A pooled gradient that vanishes within the symbols is a composition effect, not an edge. That is what the `symbols` column counts.

## 1. Numeric features, ranked by rank-correlation with R

| Feature | rho | n | bottom bucket exp | top bucket exp | spread | top WR | top n | symbols agreeing |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `risk_dist` | +0.2039 | 18,998 | -0.1217 | +0.0009 | +0.1226 | 53.9% | 3,810 | 12/16 |
| `atr` | +0.1942 | 18,998 | -0.0843 | +0.0025 | +0.0868 | 53.9% | 3,810 | 12/16 |
| `ev` | +0.1210 | 18,998 | -0.0682 | -0.0367 | +0.0316 | 51.7% | 3,810 | 10/16 |
| `rr` | +0.0889 | 18,998 | -0.0941 | -0.1216 | -0.0275 | 47.2% | 3,810 | 5/16 |
| `spread_pips` | +0.0752 | 18,998 | +0.0188 | -0.0900 | -0.1088 | 48.8% | 3,810 | 5/16 |
| `dissection_score` | +0.0326 | 18,998 | -0.1244 | -0.0477 | +0.0767 | 50.9% | 3,810 | 12/16 |
| `score` | +0.0323 | 18,998 | -0.1016 | -0.0608 | +0.0409 | 49.8% | 3,810 | 9/16 |
| `adversarial_penalty` | -0.0277 | 18,998 | -0.0677 | -0.0870 | -0.0193 | 49.3% | 3,810 | 8/16 |
| `confluence_count` | +0.0258 | 18,998 | -0.1006 | -0.0601 | +0.0405 | 49.7% | 3,810 | 9/16 |
| `master_score` | +0.0133 | 18,998 | -0.0909 | -0.0746 | +0.0163 | 49.4% | 3,810 | 8/16 |
| `meta_label_prob` | +nan | 18,998 | +0.0200 | -0.1197 | -0.1397 | 47.4% | 3,810 | 3/16 |
| `n_failed_gates` | -0.0243 | 18,998 | -0.0568 | -0.1156 | -0.0588 | 48.1% | 3,810 | 7/16 |
| `trend_score` | -0.0180 | 18,998 | -0.0596 | -0.0952 | -0.0356 | 48.7% | 3,810 | 6/16 |

## 2. Categorical features

### `regime` (spread +0.4990R, n=18,998)

| Value | n | WR | Expectancy (R) | Total R |
|---|---:|---:|---:|---:|
| LOW_VOLATILITY | 83 | 72.3% | +0.3636 | +30.18 |
| BREAKOUT | 2,432 | 52.1% | -0.0424 | -103.20 |
| TREND_BEAR | 7,387 | 49.4% | -0.0632 | -467.02 |
| LIQUIDITY_SWEEP | 511 | 49.5% | -0.1004 | -51.31 |
| TREND_BULL | 8,124 | 48.5% | -0.1134 | -921.32 |
| COMPRESSION | 456 | 48.7% | -0.1354 | -61.75 |

### `strategy` (spread +0.4371R, n=18,998)

| Value | n | WR | Expectancy (R) | Total R |
|---|---:|---:|---:|---:|
| BREAKOUT_EXPANSION | 44 | 59.1% | +0.2755 | +12.12 |
| RANGE_MEAN_REVERSION | 360 | 56.4% | -0.0547 | -19.71 |
| TREND_FOLLOWING | 3,725 | 56.6% | -0.0591 | -220.01 |
| TREND_PULLBACK | 8,252 | 48.8% | -0.0609 | -502.82 |
| CHOCH_STRUCTURAL_REVERSAL | 3,314 | 49.6% | -0.0922 | -305.39 |
| LIQUIDITY_SWEEP_REVERSAL | 3,303 | 42.1% | -0.1616 | -533.62 |

### `order_type` (spread +0.0943R, n=18,998)

| Value | n | WR | Expectancy (R) | Total R |
|---|---:|---:|---:|---:|
| MARKET | 12,968 | 50.8% | -0.0527 | -683.31 |
| LIMIT | 6,030 | 46.6% | -0.1470 | -886.11 |

### `side` (spread +0.0666R, n=18,998)

| Value | n | WR | Expectancy (R) | Total R |
|---|---:|---:|---:|---:|
| SELL | 8,902 | 50.6% | -0.0472 | -420.23 |
| BUY | 10,096 | 48.5% | -0.1138 | -1149.19 |

### `zone` (spread +0.0322R, n=18,998)

| Value | n | WR | Expectancy (R) | Total R |
|---|---:|---:|---:|---:|
| DISCOUNT | 7,504 | 49.5% | -0.0640 | -480.61 |
| EQUILIBRIUM | 1,580 | 50.5% | -0.0853 | -134.72 |
| PREMIUM | 9,914 | 49.3% | -0.0962 | -954.09 |

### `decision` (spread +0.0268R, n=18,998)

| Value | n | WR | Expectancy (R) | Total R |
|---|---:|---:|---:|---:|
| EXECUTE | 2,730 | 43.8% | -0.0626 | -170.88 |
| WAIT | 7,474 | 51.0% | -0.0820 | -612.60 |
| NO_TRADE | 8,794 | 50.0% | -0.0894 | -785.94 |

## 3. Bucket detail for the strongest numeric gradients

### `risk_dist` (rho +0.2039, spread +0.1226R)

| Bucket (per-symbol quantile) | n | WR | Expectancy (R) | Total R | Payoff |
|---|---:|---:|---:|---:|---:|
| Q1 | 3,792 | 46.4% | -0.1217 | -461.55 | 0.888 |
| Q2 | 3,798 | 48.4% | -0.1006 | -382.24 | 0.842 |
| Q3 | 3,800 | 50.3% | -0.0874 | -331.93 | 0.800 |
| Q4 | 3,798 | 48.5% | -0.1046 | -397.10 | 0.820 |
| Q5 | 3,810 | 53.9% | +0.0009 | +3.39 | 0.858 |

### `atr` (rho +0.1942, spread +0.0868R)

| Bucket (per-symbol quantile) | n | WR | Expectancy (R) | Total R | Payoff |
|---|---:|---:|---:|---:|---:|
| Q1 | 3,792 | 48.6% | -0.0843 | -319.65 | 0.872 |
| Q2 | 3,798 | 46.9% | -0.1358 | -515.58 | 0.823 |
| Q3 | 3,800 | 47.3% | -0.1243 | -472.28 | 0.828 |
| Q4 | 3,798 | 50.7% | -0.0714 | -271.29 | 0.817 |
| Q5 | 3,810 | 53.9% | +0.0025 | +9.39 | 0.859 |

### `ev` (rho +0.1210, spread +0.0316R)

| Bucket (per-symbol quantile) | n | WR | Expectancy (R) | Total R | Payoff |
|---|---:|---:|---:|---:|---:|
| Q1 | 3,792 | 50.4% | -0.0682 | -258.80 | 0.837 |
| Q2 | 3,798 | 47.0% | -0.1410 | -535.59 | 0.802 |
| Q3 | 3,800 | 48.7% | -0.0991 | -376.40 | 0.831 |
| Q4 | 3,798 | 49.6% | -0.0682 | -258.88 | 0.865 |
| Q5 | 3,810 | 51.7% | -0.0367 | -139.74 | 0.853 |

### `rr` (rho +0.0889, spread -0.0275R)

| Bucket (per-symbol quantile) | n | WR | Expectancy (R) | Total R | Payoff |
|---|---:|---:|---:|---:|---:|
| Q1 | 3,792 | 49.1% | -0.0941 | -356.90 | 0.832 |
| Q2 | 3,798 | 51.9% | -0.0270 | -102.63 | 0.871 |
| Q3 | 3,800 | 50.7% | -0.0796 | -302.66 | 0.795 |
| Q4 | 3,798 | 48.6% | -0.0906 | -343.95 | 0.851 |
| Q5 | 3,810 | 47.2% | -0.1216 | -463.28 | 0.834 |

## 4. Verdict

The largest rank correlation in the set is `risk_dist` at rho +0.2039, worth +0.1226R between the bottom and top quintile. Read against a per-trade expectancy of a few hundredths of an R, and against the 12/16 symbols that reproduce the direction internally.

A feature only justifies acting on it if **all three** hold: the spread is economically meaningful, most symbols agree, and the top bucket's expectancy is positive on its own. A high rho with a negative top bucket means the feature sorts *how badly* a trade loses, not how well it wins — that is a risk-sizing signal, not an entry filter.

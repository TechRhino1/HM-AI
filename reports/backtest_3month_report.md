# JARVIS AI — 3-Month Backtest on Real MT5 Data

**Mode:** calibrated per-symbol win-rate profiles  
**Data:** MT5 terminal, real H1 bars (validated non-synthetic)  
**Initial balance:** $10,000.00 at 0.5% risk per trade  
**Generated:** 2026-09-12T14:33:40.884601+00:00

## 1. Headline

| Metric | Value |
|---|---|
| Symbols traded | 16 |
| Total trades | 331 |
| Win rate | 64.05% |
| Expectancy | +0.0536 R per trade |
| Average win / loss | +0.641 R / -0.994 R |
| Payoff ratio | 0.646 |
| Total R | +17.75 R |
| Profit factor | 1.15 |
| Net profit | $+765.38 |
| Max drawdown | 6.53% |
| Sharpe / Sortino / Calmar | 0.94 / 2.66 / 1.06 |
| Expectancy (sample-uniqueness weighted) | -0.0315 R |

**Win-rate target outcome:** 1/16 symbols met the target out-of-sample. 1 met it in-sample but failed out-of-sample (i.e. the target was reachable only by overfitting).

## 2. Per-symbol results

| Symbol | Trades | WR % | Exp (R) | PF | Net $ | MaxDD % | OOS WR % | OOS Exp (R) | Target | Binding constraint |
|---|---:|---:|---:|---:|---:|---:|---:|---:|:--:|---|
| USDCAD | 73 | 74.0 | +0.024 | 1.03 | +33.79 | 3.81 | 66.7 | +0.055 | in-sample | out-of-sample - met in-sample (75.4%) but not on purged folds; OOS win |
| USDJPY | 68 | 70.6 | -0.009 | 0.95 | -50.09 | 3.28 | 54.4 | +0.013 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| GER40 | 62 | 56.5 | +0.010 | 0.98 | -22.61 | 2.77 | 61.0 | +0.162 | ✗ | win rate - best achievable in-sample with positive expectancy is 72.5% |
| SOLUSD | 49 | 53.1 | +0.029 | 1.06 | +42.39 | 2.61 | 48.5 | +0.023 | ✗ | win rate - best achievable in-sample with positive expectancy is 65.0% |
| XAUUSD | 40 | 42.5 | +0.062 | 1.12 | +139.76 | 4.44 | 46.8 | +0.000 | ✗ | win rate - best achievable in-sample with positive expectancy is 74.5% |
| UK100 | 39 | 82.0 | +0.311 | 2.70 | +622.14 | 0.71 | 80.7 | +0.305 | OOS ✓ | none - target met in-sample AND out-of-sample (OOS 80.7% on 31 trades) |
| AUDUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 68.0 | -0.049 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| BTCUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 61.9 | -0.093 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| ETHUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 51.7 | -0.077 | ✗ | win rate - best achievable in-sample with positive expectancy is 69.4% |
| EURUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 64.7 | -0.085 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| GBPUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 50.9 | -0.026 | ✗ | win rate - best achievable in-sample with positive expectancy is 72.0% |
| NAS100 | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 46.3 | -0.152 | ✗ | win rate - best achievable in-sample with positive expectancy is 68.1% |
| NZDUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 48.9 | -0.123 | ✗ | win rate - best achievable in-sample with positive expectancy is 46.9% |
| US30 | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 58.7 | -0.090 | ✗ | win rate - best achievable in-sample with positive expectancy is 71.7% |
| USDCHF | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 53.5 | -0.192 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| XAGUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 59.0 | -0.060 | ✗ | win rate - best achievable in-sample with positive expectancy is 63.3% |

### 2a. What the learned regime policy contributes

Calibration reports the same out-of-sample sample twice: once with every regime enabled, and once with the regimes the learned policy has switched off removed. Both are purged out-of-sample w.r.t. the geometry and threshold; the policy itself is fitted on **training folds only**, so it has never seen the out-of-sample bars it filters. The gap between the two columns is the policy's entire contribution — read it as the honest size of the effect, not as free money.

| Symbol | OOS n (all regimes) | OOS WR | OOS Exp (R) | OOS n (policy applied) | OOS WR | OOS Exp (R) | Policy fitted on |
|---|---:|---:|---:|---:|---:|---:|---|
| USDCAD | 51 | 66.7 | +0.055 | 51 | 66.7 | +0.055 | train folds |
| USDJPY | 57 | 54.4 | +0.013 | 57 | 54.4 | +0.013 | train folds |
| GER40 | 59 | 61.0 | +0.162 | 59 | 61.0 | +0.162 | train folds |
| SOLUSD | 69 | 47.8 | +0.008 | 68 | 48.5 | +0.023 | train folds |
| XAUUSD | 47 | 46.8 | +0.000 | 47 | 46.8 | +0.000 | train folds |
| UK100 | 53 | 66.0 | +0.050 | 31 | 80.7 | +0.305 | train folds |
| AUDUSD | 135 | 65.2 | -0.087 | 78 | 68.0 | -0.049 | train folds |
| BTCUSD | 142 | 62.0 | -0.088 | 105 | 61.9 | -0.093 | train folds |
| ETHUSD | 60 | 51.7 | -0.077 | 60 | 51.7 | -0.077 | train folds |
| EURUSD | 109 | 60.6 | -0.147 | 68 | 64.7 | -0.085 | train folds |
| GBPUSD | 55 | 50.9 | -0.026 | 55 | 50.9 | -0.026 | train folds |
| NAS100 | 54 | 46.3 | -0.152 | 54 | 46.3 | -0.152 | train folds |
| NZDUSD | 47 | 48.9 | -0.123 | 47 | 48.9 | -0.123 | train folds |
| US30 | 46 | 58.7 | -0.090 | 46 | 58.7 | -0.090 | train folds |
| USDCHF | 80 | 51.2 | -0.235 | 71 | 53.5 | -0.192 | train folds |
| XAGUSD | 80 | 56.2 | -0.092 | 78 | 59.0 | -0.060 | train folds |

## 3. Win-rate / expectancy frontier

For each target size (`tp_r`, as a multiple of initial risk) the table gives the highest win rate found at all, and the highest win rate that still carries positive expectancy. Where those two diverge, the gap is win rate that can only be bought by running a losing system. This is the direct evidence for whether the 75% target is reachable.

**AUDUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.4 | 72.2% | +0.017 | 36 | 72.2% | +0.017 | 36 |
| 0.5 | 65.6% | -0.015 | 32 | — | — | — |
| 0.6 | 66.7% | +0.020 | 42 | 66.7% | +0.022 | 42 |
| 0.75 | 63.3% | +0.016 | 30 | 63.3% | +0.016 | 30 |
| 1 | 63.2% | +0.012 | 38 | 63.2% | +0.012 | 38 |
| 1.5 | 65.4% | +0.067 | 26 | 65.4% | +0.067 | 26 |

**BTCUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.4 | 67.2% | -0.052 | 171 | — | — | — |
| 0.5 | 65.6% | -0.004 | 93 | — | — | — |
| 0.6 | 67.5% | +0.068 | 40 | 67.5% | +0.068 | 40 |
| 0.75 | 65.8% | +0.034 | 38 | 65.8% | +0.034 | 38 |
| 1 | 64.2% | +0.032 | 81 | 64.2% | +0.032 | 81 |
| 1.5 | 66.7% | +0.009 | 30 | 66.7% | +0.009 | 30 |

**ETHUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.4 | 69.4% | +0.004 | 85 | 69.4% | +0.004 | 85 |
| 0.5 | 65.4% | +0.020 | 78 | 65.4% | +0.020 | 78 |
| 0.6 | 63.6% | +0.018 | 77 | 63.6% | +0.018 | 77 |
| 0.75 | 64.1% | +0.070 | 53 | 64.1% | +0.070 | 53 |
| 1 | 68.6% | +0.050 | 70 | 68.6% | +0.050 | 70 |
| 1.5 | 67.7% | -0.010 | 68 | 64.9% | +0.042 | 74 |

**EURUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.4 | 69.8% | -0.023 | 86 | — | — | — |
| 0.5 | 62.3% | -0.041 | 77 | — | — | — |
| 0.6 | 63.8% | -0.018 | 94 | — | — | — |
| 0.75 | 59.1% | -0.079 | 93 | — | — | — |
| 1 | 62.7% | -0.044 | 51 | — | — | — |
| 1.5 | 62.2% | -0.048 | 45 | — | — | — |

**GBPUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.4 | 72.0% | +0.012 | 82 | 72.0% | +0.012 | 82 |
| 0.5 | 65.3% | -0.007 | 72 | — | — | — |
| 0.6 | 64.6% | +0.038 | 65 | 64.6% | +0.038 | 65 |
| 0.75 | 63.5% | +0.021 | 63 | 63.5% | +0.021 | 63 |
| 1 | 64.5% | +0.035 | 62 | 64.5% | +0.035 | 62 |
| 1.5 | 67.3% | +0.034 | 55 | 67.3% | +0.034 | 55 |

**GER40**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.4 | 72.5% | +0.015 | 80 | 72.5% | +0.015 | 80 |
| 0.5 | 70.3% | +0.067 | 74 | 70.3% | +0.067 | 74 |
| 0.6 | 69.8% | +0.091 | 106 | 69.8% | +0.091 | 106 |
| 0.75 | 70.0% | +0.131 | 70 | 70.0% | +0.131 | 70 |
| 1 | 69.7% | +0.161 | 66 | 69.7% | +0.161 | 66 |
| 1.5 | 70.1% | +0.125 | 87 | 70.1% | +0.125 | 87 |

**NAS100**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.4 | 71.0% | -0.005 | 76 | — | — | — |
| 0.5 | 66.1% | -0.000 | 65 | — | — | — |
| 0.6 | 65.1% | +0.005 | 126 | 65.1% | +0.005 | 126 |
| 0.75 | 67.2% | +0.052 | 61 | 67.2% | +0.052 | 61 |
| 1 | 66.7% | +0.041 | 54 | 66.7% | +0.041 | 54 |
| 1.5 | 68.1% | +0.033 | 47 | 68.1% | +0.033 | 47 |

**NZDUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.4 | 68.7% | -0.039 | 83 | — | — | — |
| 0.5 | 63.6% | -0.045 | 77 | — | — | — |
| 0.6 | 58.6% | -0.102 | 58 | — | — | — |
| 0.75 | 60.3% | -0.043 | 73 | — | — | — |
| 1 | 59.2% | -0.055 | 71 | — | — | — |
| 1.5 | 56.9% | -0.083 | 65 | 46.9% | +0.021 | 49 |

**SOLUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.4 | 64.4% | -0.073 | 118 | — | — | — |
| 0.5 | 60.9% | -0.042 | 87 | — | — | — |
| 0.6 | 58.8% | -0.047 | 85 | — | — | — |
| 0.75 | 59.5% | -0.010 | 79 | — | — | — |
| 1 | 61.4% | +0.012 | 88 | 61.4% | +0.012 | 88 |
| 1.5 | 65.0% | +0.073 | 60 | 65.0% | +0.073 | 60 |

**UK100**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.4 | 84.4% | +0.181 | 32 | 84.4% | +0.181 | 32 |
| 0.5 | 81.2% | +0.219 | 32 | 81.2% | +0.219 | 32 |
| 0.6 | 81.2% | +0.300 | 32 | 81.2% | +0.300 | 32 |
| 0.75 | 76.7% | +0.219 | 30 | 76.7% | +0.219 | 30 |
| 1 | 82.1% | +0.316 | 28 | 82.1% | +0.316 | 28 |
| 1.5 | 84.0% | +0.291 | 25 | 84.0% | +0.291 | 25 |

**US30**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.4 | 71.7% | +0.004 | 53 | 71.7% | +0.004 | 53 |
| 0.5 | 67.3% | +0.010 | 52 | 67.3% | +0.010 | 52 |
| 0.6 | 64.6% | +0.026 | 48 | 64.6% | +0.026 | 48 |
| 0.75 | 63.8% | +0.028 | 47 | 63.8% | +0.028 | 47 |
| 1 | 64.5% | +0.009 | 110 | 64.5% | +0.009 | 110 |
| 1.5 | 69.4% | +0.033 | 36 | 69.4% | +0.033 | 36 |

**USDCAD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.4 | 75.9% | +0.056 | 58 | 75.9% | +0.056 | 58 |
| 0.5 | 73.6% | +0.095 | 53 | 73.6% | +0.095 | 53 |
| 0.6 | 73.5% | +0.090 | 49 | 73.5% | +0.090 | 49 |
| 0.75 | 72.0% | +0.086 | 50 | 72.0% | +0.086 | 50 |
| 1 | 69.6% | +0.037 | 46 | 69.6% | +0.037 | 46 |
| 1.5 | 69.0% | +0.053 | 42 | 69.0% | +0.053 | 42 |

**USDCHF**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.4 | 64.5% | -0.100 | 138 | — | — | — |
| 0.5 | 59.0% | -0.115 | 78 | — | — | — |
| 0.6 | 61.1% | -0.067 | 72 | — | — | — |
| 0.75 | 62.1% | -0.052 | 66 | — | — | — |
| 1 | 62.5% | -0.038 | 64 | — | — | — |
| 1.5 | 65.0% | -0.029 | 60 | — | — | — |

**USDJPY**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.4 | 70.7% | -0.005 | 58 | — | — | — |
| 0.5 | 66.4% | -0.003 | 128 | 63.5% | +0.008 | 74 |
| 0.6 | 65.9% | +0.017 | 129 | 65.9% | +0.017 | 129 |
| 0.75 | 66.1% | +0.037 | 121 | 66.1% | +0.037 | 121 |
| 1 | 67.3% | +0.064 | 110 | 67.3% | +0.064 | 110 |
| 1.5 | 64.7% | +0.008 | 102 | 64.7% | +0.008 | 102 |

**XAGUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.4 | 69.1% | -0.016 | 81 | — | — | — |
| 0.5 | 63.1% | -0.038 | 84 | — | — | — |
| 0.6 | 63.4% | -0.033 | 112 | 61.5% | +0.002 | 78 |
| 0.75 | 62.8% | -0.028 | 113 | 58.9% | +0.046 | 73 |
| 1 | 63.3% | +0.001 | 79 | 63.3% | +0.001 | 79 |
| 1.5 | 64.1% | -0.017 | 64 | 50.0% | +0.112 | 52 |

**XAUUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.4 | 74.5% | +0.048 | 51 | 74.5% | +0.048 | 51 |
| 0.5 | 65.2% | +0.017 | 46 | 65.2% | +0.017 | 46 |
| 0.6 | 66.7% | +0.060 | 45 | 66.7% | +0.060 | 45 |
| 0.75 | 66.7% | +0.095 | 45 | 66.7% | +0.095 | 45 |
| 1 | 64.3% | +0.010 | 42 | 64.3% | +0.010 | 42 |
| 1.5 | 67.6% | +0.069 | 37 | 67.6% | +0.069 | 37 |

## 4. Calibrated geometry per symbol

`tp_r` is the target distance in multiples of initial risk; `be` is the breakeven trigger (off = never lock breakeven); `pc` the partial bank; `mb` the time stop in bars; `thr` the entry score threshold.

The last two columns come from the isotonic score calibration: the realised win probability the fitted map assigns to a score sitting exactly at the deployed threshold, and the Brier score of that map (lower is better; 0.25 is the skill-free baseline for a balanced sample). The calibration is monotone, so it cannot change which candidates pass — it exists to make the score interpretable and to give the AI layer a probability rather than an arbitrary index.

| Symbol | tp_r | be | pc | mb | threshold | P(win) @ thr | Brier | Enabled regimes |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| AUDUSD | 0.4 | off | off | 48 | 0.425 | 65.2% | 0.227 | TREND_BEAR, LIQUIDITY_SWEEP, COMPRESSION, LOW_VOLATILITY |
| BTCUSD | 0.4 | off | off | 48 | 0.400 | 61.0% | 0.235 | TREND_BULL, BREAKOUT, COMPRESSION, LOW_VOLATILITY |
| ETHUSD | 0.75 | off | 0.5 | 48 | 0.756 | 51.1% | 0.250 | TREND_BULL, TREND_BEAR, BREAKOUT, COMPRESSION |
| EURUSD | 0.4 | off | off | 48 | 0.618 | 56.8% | 0.233 | TREND_BEAR, BREAKOUT, COMPRESSION, LIQUIDITY_SWEEP |
| GBPUSD | 0.75 | off | off | 48 | 0.698 | 50.9% | 0.250 | TREND_BEAR, TREND_BULL, BREAKOUT, LIQUIDITY_SWEEP |
| GER40 | 1.5 | 1 | off | 48 | 0.738 | 61.0% | 0.238 | TREND_BEAR, TREND_BULL, BREAKOUT, LIQUIDITY_SWEEP, COMPRESSION |
| NAS100 | 1 | off | off | 48 | 0.665 | 35.7% | 0.245 | TREND_BEAR, BREAKOUT, TREND_BULL, LIQUIDITY_SWEEP, COMPRESSION |
| NZDUSD | 1.5 | off | off | 48 | 0.686 | 16.7% | 0.216 | TREND_BEAR, LIQUIDITY_SWEEP, TREND_BULL, BREAKOUT |
| SOLUSD | 1.5 | 1 | off | 24 | 0.668 | 46.2% | 0.249 | BREAKOUT, TREND_BULL, TREND_BEAR, LIQUIDITY_SWEEP, LOW_VOLATILITY |
| UK100 | 0.6 | off | off | 24 | 0.723 | 42.9% | 0.195 | TREND_BEAR, LIQUIDITY_SWEEP, BREAKOUT, COMPRESSION |
| US30 | 1.5 | off | 0.5 | 48 | 0.757 | 58.3% | 0.232 | TREND_BEAR, BREAKOUT, TREND_BULL, COMPRESSION, LIQUIDITY_SWEEP |
| USDCAD | 0.4 | off | off | 24 | 0.742 | 28.6% | 0.199 | TREND_BULL, TREND_BEAR, BREAKOUT, LIQUIDITY_SWEEP |
| USDCHF | 0.4 | off | off | 48 | 0.644 | 48.0% | 0.235 | TREND_BEAR, TREND_BULL, LIQUIDITY_SWEEP, COMPRESSION |
| USDJPY | 0.4 | off | off | 48 | 0.748 | 52.4% | 0.237 | TREND_BULL, BREAKOUT, TREND_BEAR, LIQUIDITY_SWEEP, COMPRESSION, LOW_VOLATILITY |
| XAGUSD | 1.5 | off | off | 24 | 0.700 | 56.0% | 0.246 | TREND_BULL, TREND_BEAR, LIQUIDITY_SWEEP, COMPRESSION |
| XAUUSD | 1.5 | off | off | 24 | 0.690 | 41.5% | 0.229 | TREND_BEAR, TREND_BULL, BREAKOUT, LIQUIDITY_SWEEP, COMPRESSION |

## 5. Portfolio risk allocation (HRP)

Inverse-variance weights from the HRP allocator over per-symbol daily R:

| Symbol | Risk weight |
|---|---:|
| UK100 | 25.84% |
| USDJPY | 24.61% |
| USDCAD | 21.02% |
| GER40 | 10.54% |
| SOLUSD | 10.12% |
| XAUUSD | 7.88% |

## 6. Entry rejection analysis

Why candidates were not traded under the calibrated profile. In calibrated mode these are the capital-protection gates plus the calibrated edge filter; the legacy 29-check stack no longer decides.

Counts are per **bar evaluated**, not per candidate: a bar on which the pipeline found no setup at all (`no directional bias`) is counted here too, because the engine has to consider and decline it. A large `no directional bias` count therefore means the pipeline rarely formed a view, not that a good trade was vetoed.

| Symbol | Top rejection reasons |
|---|---|
| USDCAD | capital protection: Directional Bias (304); score 0.520 below calibrated threshold 0.742 (35); score 0.540 below calibrated threshold 0.742 (28) |
| USDJPY | capital protection: Directional Bias (268); score 0.520 below calibrated threshold 0.748 (40); score 0.560 below calibrated threshold 0.748 (26) |
| GER40 | capital protection: Directional Bias (234); score 0.520 below calibrated threshold 0.738 (46); score 0.480 below calibrated threshold 0.738 (23) |
| SOLUSD | capital protection: Directional Bias (188); score 0.520 below calibrated threshold 0.668 (30); score 0.480 below calibrated threshold 0.668 (13) |
| XAUUSD | capital protection: Directional Bias (76); score 0.520 below calibrated threshold 0.690 (16); score 0.480 below calibrated threshold 0.690 (10) |
| UK100 | capital protection: Directional Bias (437); regime TREND_BULL disabled by learned policy (expectancy -0.235R worse than -0.050R over 77 trades) (338); |
| AUDUSD | calibrated profile has no validated edge (OOS expectancy -0.049R over 78 trades) - refusing symbol (1083); capital protection: Directional Bias (412); |
| BTCUSD | calibrated profile has no validated edge (OOS expectancy -0.093R over 105 trades) - refusing symbol (1588); capital protection: Directional Bias (569) |
| ETHUSD | calibrated profile has no validated edge (OOS expectancy -0.077R over 60 trades) - refusing symbol (1476); capital protection: Directional Bias (681) |
| EURUSD | calibrated profile has no validated edge (OOS expectancy -0.085R over 68 trades) - refusing symbol (1096); capital protection: Directional Bias (401); |
| GBPUSD | calibrated profile has no validated edge (OOS expectancy -0.026R over 55 trades) - refusing symbol (1115); capital protection: Directional Bias (382); |
| NAS100 | calibrated profile has no validated edge (OOS expectancy -0.152R over 54 trades) - refusing symbol (1026); capital protection: Directional Bias (399); |
| NZDUSD | calibrated profile has no validated edge (OOS expectancy -0.123R over 47 trades) - refusing symbol (1118); capital protection: Directional Bias (373); |
| US30 | calibrated profile has no validated edge (OOS expectancy -0.090R over 46 trades) - refusing symbol (998); capital protection: Directional Bias (427);  |
| USDCHF | calibrated profile has no validated edge (OOS expectancy -0.192R over 71 trades) - refusing symbol (1116); capital protection: Directional Bias (381); |
| XAGUSD | calibrated profile has no validated edge (OOS expectancy -0.060R over 78 trades) - refusing symbol (1131); capital protection: Directional Bias (290); |

## 7. Method and honesty notes

**Calibration is walk-forward.** Each symbol's geometry and entry threshold were chosen only on training folds and reported on purged out-of-sample folds (`PurgedKFold`, with the embargo excluded from both train and test). The deployed configuration is the one the folds agree on most often, not the best in-sample point.

**Win rate is a constrained objective, not a bare target.** The calibrator maximises expectancy subject to `win_rate >= target`, `trades >= min_trades` and `expectancy > 0`. A high win rate is trivially obtainable by shrinking the target relative to the stop, so a profile that reached 75% with negative expectancy would be rejected.

**The regime policy is fitted out-of-sample.** Which regimes are switched off is learned from trades the fold-selected configuration would have taken *inside its training windows*, never from the out-of-sample bars it then filters. Fitting the policy on the same sample it filters is a filter tuned to the test set: measured on this data it moved the aggregate out-of-sample result from roughly break-even to about +52R, which is the size of the artefact. Section 2a publishes both figures so the contribution can be judged rather than assumed.

**Intrabar ordering is conservative.** A bar whose range spans both the stop and the target is booked as a loss, because OHLC data does not reveal which came first. The previous engine resolved this optimistically, which inflated win rate exactly where the target lives.

**The backtest is hermetic.** All persistent-state reads and writes on the decision path are disabled during simulation (`jarvis.config.runtime`), so results are reproducible and cannot be influenced by live trade history.

### Known limitations

* Three months of H1 data bounds the achievable sample: with one-position-at-a-time and a time stop, a symbol yields roughly `bars / max_bars` trades. Per-symbol win rates on 20-40 trades carry wide confidence intervals.
* Costs are modelled as the realised spread from the data plus the symbol profile's commission (zero on XM Ultra Low). Swap/financing is not modelled.
* Regime labels and the candidate set come from the same pipeline under test; a regime that the pipeline cannot detect will not appear here.

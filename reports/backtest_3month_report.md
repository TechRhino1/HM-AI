# JARVIS AI — 3-Month Backtest on Real MT5 Data

**Mode:** calibrated per-symbol win-rate profiles  
**Data:** MT5 terminal, real H1 bars (validated non-synthetic)  
**Initial balance:** $10,000.00 at 0.5% risk per trade  
**Generated:** 2026-09-12T14:14:23.535224+00:00

## 1. Headline

| Metric | Value |
|---|---|
| Symbols traded | 16 |
| Total trades | 218 |
| Win rate | 64.68% |
| Expectancy | +0.0621 R per trade |
| Average win / loss | +0.633 R / -0.983 R |
| Payoff ratio | 0.644 |
| Total R | +13.54 R |
| Profit factor | 1.20 |
| Net profit | $+591.83 |
| Max drawdown | 3.29% |
| Sharpe / Sortino / Calmar | 1.12 / 2.69 / 1.78 |
| Expectancy (sample-uniqueness weighted) | -0.0189 R |

**Win-rate target outcome:** 1/16 symbols met the target out-of-sample. 2 met it in-sample but failed out-of-sample (i.e. the target was reachable only by overfitting).

## 2. Per-symbol results

| Symbol | Trades | WR % | Exp (R) | PF | Net $ | MaxDD % | OOS WR % | OOS Exp (R) | Target | Binding constraint |
|---|---:|---:|---:|---:|---:|---:|---:|---:|:--:|---|
| USDJPY | 68 | 70.6 | -0.009 | 0.95 | -50.09 | 3.28 | 54.4 | +0.013 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| GER40 | 62 | 56.5 | +0.010 | 0.98 | -22.61 | 2.77 | 61.0 | +0.162 | ✗ | selection stability - 81.3% is reachable in-sample (tp0.25_beoff_pcoff |
| SOLUSD | 49 | 53.1 | +0.029 | 1.06 | +42.39 | 2.61 | 48.5 | +0.023 | ✗ | win rate - best achievable in-sample with positive expectancy is 65.0% |
| UK100 | 39 | 82.0 | +0.311 | 2.70 | +622.14 | 0.71 | 80.7 | +0.305 | OOS ✓ | none - target met in-sample AND out-of-sample (OOS 80.7% on 31 trades) |
| AUDUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 75.6 | -0.055 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| BTCUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 71.8 | -0.047 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| ETHUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 64.6 | -0.106 | in-sample | out-of-sample - met in-sample (78.6%) but not on purged folds; OOS exp |
| EURUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 74.8 | -0.051 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| GBPUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 52.4 | -0.125 | ✗ | win rate - best achievable in-sample with positive expectancy is 72.0% |
| NAS100 | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 73.8 | -0.033 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| NZDUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 70.0 | -0.046 | ✗ | sample size - only 18 trades in the sample (20 required to assert a ra |
| US30 | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 58.7 | -0.090 | ✗ | selection stability - 77.0% is reachable in-sample (tp0.3_beoff_pcoffx |
| USDCAD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 71.9 | -0.003 | in-sample | out-of-sample - met in-sample (75.4%) but not on purged folds; OOS exp |
| USDCHF | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 56.7 | -0.241 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| XAGUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 66.7 | -0.045 | ✗ | win rate - best achievable in-sample with positive expectancy is 63.3% |
| XAUUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 74.8 | -0.037 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |

### 2a. What the learned regime policy contributes

Calibration reports the same out-of-sample sample twice: once with every regime enabled, and once with the regimes the learned policy has switched off removed. Both are purged out-of-sample w.r.t. the geometry and threshold; the policy itself is fitted on **training folds only**, so it has never seen the out-of-sample bars it filters. The gap between the two columns is the policy's entire contribution — read it as the honest size of the effect, not as free money.

| Symbol | OOS n (all regimes) | OOS WR | OOS Exp (R) | OOS n (policy applied) | OOS WR | OOS Exp (R) | Policy fitted on |
|---|---:|---:|---:|---:|---:|---:|---|
| USDJPY | 57 | 54.4 | +0.013 | 57 | 54.4 | +0.013 | train folds |
| GER40 | 59 | 61.0 | +0.162 | 59 | 61.0 | +0.162 | train folds |
| SOLUSD | 69 | 47.8 | +0.008 | 68 | 48.5 | +0.023 | train folds |
| UK100 | 53 | 66.0 | +0.050 | 31 | 80.7 | +0.305 | train folds |
| AUDUSD | 115 | 68.7 | -0.141 | 86 | 75.6 | -0.055 | train folds |
| BTCUSD | 174 | 70.1 | -0.066 | 131 | 71.8 | -0.047 | train folds |
| ETHUSD | 82 | 64.6 | -0.106 | 82 | 64.6 | -0.106 | train folds |
| EURUSD | 146 | 67.1 | -0.149 | 99 | 74.8 | -0.051 | train folds |
| GBPUSD | 63 | 52.4 | -0.125 | 63 | 52.4 | -0.125 | train folds |
| NAS100 | 107 | 73.8 | -0.033 | 107 | 73.8 | -0.033 | train folds |
| NZDUSD | 83 | 59.0 | -0.175 | 50 | 70.0 | -0.046 | train folds |
| US30 | 46 | 58.7 | -0.090 | 46 | 58.7 | -0.090 | train folds |
| USDCAD | 64 | 71.9 | -0.003 | 64 | 71.9 | -0.003 | train folds |
| USDCHF | 97 | 56.7 | -0.241 | 97 | 56.7 | -0.241 | train folds |
| XAGUSD | 89 | 62.9 | -0.080 | 93 | 66.7 | -0.045 | train folds |
| XAUUSD | 151 | 74.8 | -0.037 | 151 | 74.8 | -0.037 | train folds |

## 3. Win-rate / expectancy frontier

For each target size (`tp_r`, as a multiple of initial risk) the table gives the highest win rate found at all, and the highest win rate that still carries positive expectancy. Where those two diverge, the gap is win rate that can only be bought by running a losing system. This is the direct evidence for whether the 75% target is reachable.

**AUDUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 85.2% | +0.065 | 54 | 85.2% | +0.065 | 54 |
| 0.3 | 84.6% | +0.110 | 52 | 84.6% | +0.110 | 52 |
| 0.4 | 71.7% | +0.030 | 46 | 71.7% | +0.030 | 46 |
| 0.5 | 63.4% | -0.033 | 41 | — | — | — |
| 0.6 | 63.4% | +0.006 | 41 | 63.4% | +0.006 | 41 |
| 0.75 | 61.5% | -0.017 | 39 | — | — | — |
| 1 | 60.0% | -0.034 | 35 | — | — | — |
| 1.5 | 64.7% | +0.077 | 34 | 64.7% | +0.077 | 34 |

**BTCUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 78.2% | -0.020 | 229 | — | — | — |
| 0.3 | 76.0% | -0.013 | 208 | — | — | — |
| 0.4 | 67.2% | -0.052 | 171 | — | — | — |
| 0.5 | 65.6% | -0.004 | 93 | — | — | — |
| 0.6 | 67.5% | +0.068 | 40 | 67.5% | +0.068 | 40 |
| 0.75 | 65.8% | +0.034 | 38 | 65.8% | +0.034 | 38 |
| 1 | 64.2% | +0.032 | 81 | 64.2% | +0.032 | 81 |
| 1.5 | 66.7% | +0.009 | 30 | 66.7% | +0.009 | 30 |

**ETHUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 80.4% | +0.022 | 97 | 80.4% | +0.022 | 97 |
| 0.3 | 76.3% | +0.014 | 97 | 76.3% | +0.014 | 97 |
| 0.4 | 69.4% | +0.004 | 85 | 69.4% | +0.004 | 85 |
| 0.5 | 65.4% | +0.020 | 78 | 65.4% | +0.020 | 78 |
| 0.6 | 63.6% | +0.018 | 77 | 63.6% | +0.018 | 77 |
| 0.75 | 64.1% | +0.070 | 53 | 64.1% | +0.070 | 53 |
| 1 | 68.6% | +0.050 | 70 | 68.6% | +0.050 | 70 |
| 1.5 | 67.7% | -0.010 | 68 | 64.9% | +0.042 | 74 |

**EURUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 77.8% | -0.028 | 63 | — | — | — |
| 0.3 | 75.8% | -0.015 | 62 | — | — | — |
| 0.4 | 69.5% | -0.027 | 82 | — | — | — |
| 0.5 | 63.6% | -0.045 | 55 | — | — | — |
| 0.6 | 64.2% | +0.011 | 67 | 64.2% | +0.011 | 67 |
| 0.75 | 59.4% | -0.052 | 64 | — | — | — |
| 1 | 64.6% | -0.011 | 48 | 52.0% | +0.040 | 25 |
| 1.5 | 64.3% | -0.010 | 42 | 42.9% | +0.004 | 21 |

**GBPUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 77.0% | -0.032 | 187 | — | — | — |
| 0.3 | 74.2% | -0.030 | 167 | — | — | — |
| 0.4 | 72.0% | +0.012 | 82 | 72.0% | +0.012 | 82 |
| 0.5 | 65.3% | -0.007 | 72 | — | — | — |
| 0.6 | 64.6% | +0.038 | 65 | 64.6% | +0.038 | 65 |
| 0.75 | 63.5% | +0.021 | 63 | 63.5% | +0.021 | 63 |
| 1 | 64.5% | +0.035 | 62 | 64.5% | +0.035 | 62 |
| 1.5 | 67.3% | +0.034 | 55 | 67.3% | +0.034 | 55 |

**GER40**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 81.3% | +0.017 | 91 | 81.3% | +0.017 | 91 |
| 0.3 | 79.1% | +0.028 | 86 | 79.1% | +0.028 | 86 |
| 0.4 | 72.5% | +0.015 | 80 | 72.5% | +0.015 | 80 |
| 0.5 | 70.3% | +0.067 | 74 | 70.3% | +0.067 | 74 |
| 0.6 | 69.8% | +0.091 | 106 | 69.8% | +0.091 | 106 |
| 0.75 | 70.0% | +0.131 | 70 | 70.0% | +0.131 | 70 |
| 1 | 69.7% | +0.161 | 66 | 69.7% | +0.161 | 66 |
| 1.5 | 70.1% | +0.125 | 87 | 70.1% | +0.125 | 87 |

**NAS100**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 78.1% | -0.024 | 137 | — | — | — |
| 0.3 | 76.4% | -0.006 | 123 | — | — | — |
| 0.4 | 71.0% | -0.005 | 76 | — | — | — |
| 0.5 | 66.1% | -0.000 | 65 | — | — | — |
| 0.6 | 65.1% | +0.005 | 126 | 65.1% | +0.005 | 126 |
| 0.75 | 67.2% | +0.052 | 61 | 67.2% | +0.052 | 61 |
| 1 | 66.7% | +0.041 | 54 | 66.7% | +0.041 | 54 |
| 1.5 | 68.1% | +0.033 | 47 | 68.1% | +0.033 | 47 |

**NZDUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 77.6% | -0.030 | 125 | — | — | — |
| 0.3 | 78.3% | +0.017 | 46 | 78.3% | +0.017 | 46 |
| 0.4 | 73.5% | +0.035 | 49 | 73.5% | +0.035 | 49 |
| 0.5 | 68.2% | +0.025 | 44 | 68.2% | +0.025 | 44 |
| 0.6 | 61.3% | -0.087 | 31 | — | — | — |
| 0.75 | 61.0% | -0.041 | 41 | 60.0% | +0.020 | 35 |
| 1 | 64.5% | -0.011 | 31 | 62.5% | +0.005 | 24 |
| 1.5 | 62.2% | -0.021 | 37 | 53.3% | +0.074 | 30 |

**SOLUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 75.2% | -0.045 | 153 | — | — | — |
| 0.3 | 72.2% | -0.043 | 115 | — | — | — |
| 0.4 | 64.4% | -0.073 | 118 | — | — | — |
| 0.5 | 60.9% | -0.042 | 87 | — | — | — |
| 0.6 | 58.8% | -0.047 | 85 | — | — | — |
| 0.75 | 59.5% | -0.010 | 79 | — | — | — |
| 1 | 61.4% | +0.012 | 88 | 61.4% | +0.012 | 88 |
| 1.5 | 65.0% | +0.073 | 60 | 65.0% | +0.073 | 60 |

**UK100**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 86.1% | +0.076 | 36 | 86.1% | +0.076 | 36 |
| 0.3 | 85.7% | +0.114 | 35 | 85.7% | +0.114 | 35 |
| 0.4 | 84.4% | +0.181 | 32 | 84.4% | +0.181 | 32 |
| 0.5 | 81.2% | +0.219 | 32 | 81.2% | +0.219 | 32 |
| 0.6 | 81.2% | +0.300 | 32 | 81.2% | +0.300 | 32 |
| 0.75 | 76.7% | +0.219 | 30 | 76.7% | +0.219 | 30 |
| 1 | 82.1% | +0.316 | 28 | 82.1% | +0.316 | 28 |
| 1.5 | 84.0% | +0.291 | 25 | 84.0% | +0.291 | 25 |

**US30**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 76.6% | -0.043 | 64 | — | — | — |
| 0.3 | 77.0% | +0.002 | 61 | 77.0% | +0.002 | 61 |
| 0.4 | 71.7% | +0.004 | 53 | 71.7% | +0.004 | 53 |
| 0.5 | 67.3% | +0.010 | 52 | 67.3% | +0.010 | 52 |
| 0.6 | 64.6% | +0.026 | 48 | 64.6% | +0.026 | 48 |
| 0.75 | 63.8% | +0.028 | 47 | 63.8% | +0.028 | 47 |
| 1 | 64.5% | +0.009 | 110 | 64.5% | +0.009 | 110 |
| 1.5 | 69.4% | +0.033 | 36 | 69.4% | +0.033 | 36 |

**USDCAD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 81.0% | +0.010 | 100 | 81.0% | +0.010 | 100 |
| 0.3 | 80.0% | +0.036 | 60 | 80.0% | +0.036 | 60 |
| 0.4 | 75.9% | +0.056 | 58 | 75.9% | +0.056 | 58 |
| 0.5 | 73.6% | +0.095 | 53 | 73.6% | +0.095 | 53 |
| 0.6 | 73.5% | +0.090 | 49 | 73.5% | +0.090 | 49 |
| 0.75 | 72.0% | +0.086 | 50 | 72.0% | +0.086 | 50 |
| 1 | 69.6% | +0.037 | 46 | 69.6% | +0.037 | 46 |
| 1.5 | 69.0% | +0.053 | 42 | 69.0% | +0.053 | 42 |

**USDCHF**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 73.6% | -0.080 | 201 | — | — | — |
| 0.3 | 70.5% | -0.084 | 173 | — | — | — |
| 0.4 | 65.5% | -0.085 | 87 | — | — | — |
| 0.5 | 61.0% | -0.085 | 82 | — | — | — |
| 0.6 | 60.5% | -0.073 | 81 | — | — | — |
| 0.75 | 60.3% | -0.075 | 73 | — | — | — |
| 1 | 57.7% | -0.119 | 111 | — | — | — |
| 1.5 | 61.3% | -0.083 | 62 | — | — | — |

**USDJPY**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 77.3% | -0.031 | 181 | — | — | — |
| 0.3 | 73.7% | -0.040 | 167 | — | — | — |
| 0.4 | 70.7% | -0.005 | 58 | — | — | — |
| 0.5 | 66.4% | -0.003 | 128 | 63.5% | +0.008 | 74 |
| 0.6 | 65.9% | +0.017 | 129 | 65.9% | +0.017 | 129 |
| 0.75 | 66.1% | +0.037 | 121 | 66.1% | +0.037 | 121 |
| 1 | 67.3% | +0.064 | 110 | 67.3% | +0.064 | 110 |
| 1.5 | 64.7% | +0.008 | 102 | 64.7% | +0.008 | 102 |

**XAGUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 76.5% | -0.039 | 196 | — | — | — |
| 0.3 | 74.3% | -0.031 | 179 | — | — | — |
| 0.4 | 69.1% | -0.016 | 81 | — | — | — |
| 0.5 | 63.1% | -0.038 | 84 | — | — | — |
| 0.6 | 63.4% | -0.033 | 112 | 61.5% | +0.002 | 78 |
| 0.75 | 62.8% | -0.028 | 113 | 58.9% | +0.046 | 73 |
| 1 | 63.3% | +0.001 | 79 | 63.3% | +0.001 | 79 |
| 1.5 | 64.1% | -0.017 | 64 | 50.0% | +0.112 | 52 |

**XAUUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 80.0% | +0.002 | 200 | 80.0% | +0.002 | 200 |
| 0.3 | 76.8% | +0.003 | 56 | 76.8% | +0.003 | 56 |
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
| AUDUSD | 0.25 | off | off | 48 | 0.425 | 64.9% | 0.214 | TREND_BEAR, BREAKOUT, LIQUIDITY_SWEEP, LOW_VOLATILITY, COMPRESSION |
| BTCUSD | 0.25 | off | off | 48 | 0.400 | 69.7% | 0.209 | TREND_BULL, BREAKOUT, COMPRESSION, LOW_VOLATILITY |
| ETHUSD | 0.25 | off | off | 48 | 0.756 | 64.6% | 0.229 | TREND_BULL, TREND_BEAR, BREAKOUT, COMPRESSION |
| EURUSD | 0.25 | off | off | 48 | 0.412 | 65.3% | 0.219 | TREND_BEAR, BREAKOUT, COMPRESSION |
| GBPUSD | 0.75 | off | off | 48 | 0.698 | 50.0% | 0.249 | TREND_BEAR, TREND_BULL, BREAKOUT, LIQUIDITY_SWEEP |
| GER40 | 1.5 | 1 | off | 48 | 0.738 | 61.0% | 0.238 | TREND_BEAR, TREND_BULL, BREAKOUT, LIQUIDITY_SWEEP, COMPRESSION |
| NAS100 | 0.3 | off | off | 48 | 0.611 | 70.4% | 0.193 | TREND_BEAR, BREAKOUT, TREND_BULL, LIQUIDITY_SWEEP, COMPRESSION |
| NZDUSD | 1.5 | off | off | 48 | 0.728 | 56.9% | 0.221 | TREND_BEAR, LIQUIDITY_SWEEP |
| SOLUSD | 1.5 | 1 | off | 24 | 0.668 | 46.2% | 0.249 | BREAKOUT, TREND_BULL, TREND_BEAR, LIQUIDITY_SWEEP, LOW_VOLATILITY |
| UK100 | 0.6 | off | off | 24 | 0.723 | 42.9% | 0.195 | TREND_BEAR, LIQUIDITY_SWEEP, BREAKOUT, COMPRESSION |
| US30 | 1.5 | off | 0.5 | 48 | 0.757 | 58.3% | 0.232 | TREND_BEAR, BREAKOUT, TREND_BULL, COMPRESSION, LIQUIDITY_SWEEP |
| USDCAD | 0.4 | off | off | 24 | 0.742 | 65.2% | 0.199 | TREND_BULL, TREND_BEAR, BREAKOUT, LIQUIDITY_SWEEP |
| USDCHF | 0.25 | off | off | 48 | 0.644 | 33.3% | 0.233 | TREND_BEAR, TREND_BULL, BREAKOUT, LIQUIDITY_SWEEP, COMPRESSION |
| USDJPY | 0.4 | off | off | 48 | 0.748 | 52.4% | 0.237 | TREND_BULL, BREAKOUT, TREND_BEAR, LIQUIDITY_SWEEP, COMPRESSION, LOW_VOLATILITY |
| XAGUSD | 1.5 | off | off | 24 | 0.700 | 62.9% | 0.233 | TREND_BULL, TREND_BEAR, LIQUIDITY_SWEEP, COMPRESSION |
| XAUUSD | 0.3 | off | off | 48 | 0.417 | 71.0% | 0.187 | TREND_BEAR, BREAKOUT, TREND_BULL, LIQUIDITY_SWEEP, COMPRESSION |

## 5. Portfolio risk allocation (HRP)

Inverse-variance weights from the HRP allocator over per-symbol daily R:

| Symbol | Risk weight |
|---|---:|
| UK100 | 36.34% |
| USDJPY | 34.61% |
| GER40 | 14.82% |
| SOLUSD | 14.23% |

## 6. Entry rejection analysis

Why candidates were not traded under the calibrated profile. In calibrated mode these are the capital-protection gates plus the calibrated edge filter; the legacy 29-check stack no longer decides.

Counts are per **bar evaluated**, not per candidate: a bar on which the pipeline found no setup at all (`no directional bias`) is counted here too, because the engine has to consider and decline it. A large `no directional bias` count therefore means the pipeline rarely formed a view, not that a good trade was vetoed.

| Symbol | Top rejection reasons |
|---|---|
| USDJPY | capital protection: Directional Bias (268); score 0.520 below calibrated threshold 0.748 (40); score 0.560 below calibrated threshold 0.748 (26) |
| GER40 | capital protection: Directional Bias (234); score 0.520 below calibrated threshold 0.738 (46); score 0.480 below calibrated threshold 0.738 (23) |
| SOLUSD | capital protection: Directional Bias (188); score 0.520 below calibrated threshold 0.668 (30); score 0.480 below calibrated threshold 0.668 (13) |
| UK100 | capital protection: Directional Bias (437); regime TREND_BULL disabled by learned policy (expectancy -0.235R worse than -0.050R over 77 trades) (338); |
| AUDUSD | calibrated profile has no validated edge (OOS expectancy -0.055R over 86 trades) - refusing symbol (1083); capital protection: Directional Bias (412); |
| BTCUSD | calibrated profile has no validated edge (OOS expectancy -0.047R over 131 trades) - refusing symbol (1588); capital protection: Directional Bias (569) |
| ETHUSD | calibrated profile has no validated edge (OOS expectancy -0.106R over 82 trades) - refusing symbol (1476); capital protection: Directional Bias (681) |
| EURUSD | calibrated profile has no validated edge (OOS expectancy -0.051R over 99 trades) - refusing symbol (1096); capital protection: Directional Bias (401); |
| GBPUSD | calibrated profile has no validated edge (OOS expectancy -0.125R over 63 trades) - refusing symbol (1115); capital protection: Directional Bias (382); |
| NAS100 | calibrated profile has no validated edge (OOS expectancy -0.033R over 107 trades) - refusing symbol (1026); capital protection: Directional Bias (399) |
| NZDUSD | calibrated profile has no validated edge (OOS expectancy -0.046R over 50 trades) - refusing symbol (1118); capital protection: Directional Bias (373); |
| US30 | calibrated profile has no validated edge (OOS expectancy -0.090R over 46 trades) - refusing symbol (998); capital protection: Directional Bias (427);  |
| USDCAD | calibrated profile has no validated edge (OOS expectancy -0.003R over 64 trades) - refusing symbol (1131); capital protection: Directional Bias (360); |
| USDCHF | calibrated profile has no validated edge (OOS expectancy -0.241R over 97 trades) - refusing symbol (1116); capital protection: Directional Bias (381); |
| XAGUSD | calibrated profile has no validated edge (OOS expectancy -0.045R over 93 trades) - refusing symbol (1131); capital protection: Directional Bias (290); |
| XAUUSD | calibrated profile has no validated edge (OOS expectancy -0.037R over 151 trades) - refusing symbol (1132); capital protection: Directional Bias (295) |

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

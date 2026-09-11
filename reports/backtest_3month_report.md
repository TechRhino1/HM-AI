# JARVIS AI — 3-Month Backtest on Real MT5 Data

**Mode:** calibrated per-symbol win-rate profiles  
**Data:** MT5 terminal, real H1 bars (validated non-synthetic)  
**Initial balance:** $10,000.00 at 0.5% risk per trade  
**Generated:** 2026-09-11T11:34:18.115549+00:00

## 1. Headline

| Metric | Value |
|---|---|
| Symbols traded | 16 |
| Total trades | 698 |
| Win rate | 71.78% |
| Expectancy | -0.0361 R per trade |
| Average win / loss | +0.330 R / -0.967 R |
| Payoff ratio | 0.341 |
| Total R | -25.18 R |
| Profit factor | 0.91 |
| Net profit | $-670.75 |
| Max drawdown | 12.63% |
| Sharpe / Sortino / Calmar | -0.56 / -0.99 / -0.52 |
| Expectancy (sample-uniqueness weighted) | -0.2679 R |

**Win-rate target outcome:** 7/16 symbols met the target out-of-sample. 6 met it in-sample but failed out-of-sample (i.e. the target was reachable only by overfitting).

## 2. Per-symbol results

| Symbol | Trades | WR % | Exp (R) | PF | Net $ | MaxDD % | OOS WR % | OOS Exp (R) | Target | Binding constraint |
|---|---:|---:|---:|---:|---:|---:|---:|---:|:--:|---|
| BTCUSD | 309 | 75.4 | -0.030 | 0.88 | -296.67 | 4.32 | 79.7 | +0.040 | OOS ✓ | none - target met in-sample AND out-of-sample (OOS 79.7% on 330 trades |
| SOLUSD | 72 | 55.6 | -0.081 | 0.80 | -216.43 | 3.93 | 66.1 | -0.044 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| USDCHF | 71 | 63.4 | -0.151 | 0.57 | -417.52 | 4.98 | 79.2 | -0.010 | in-sample | out-of-sample - met in-sample (79.8%) but not on purged folds; OOS exp |
| AUDUSD | 65 | 78.5 | +0.023 | 1.07 | +46.07 | 2.01 | 80.4 | +0.020 | OOS ✓ | none - target met in-sample AND out-of-sample (OOS 80.4% on 92 trades) |
| XAUUSD | 55 | 74.5 | +0.077 | 1.46 | +317.47 | 2.68 | 80.6 | +0.025 | OOS ✓ | none - target met in-sample AND out-of-sample (OOS 80.6% on 144 trades |
| USDJPY | 47 | 63.8 | -0.158 | 0.68 | -230.41 | 4.04 | 66.7 | -0.052 | ✗ | selection stability - 80.5% is reachable in-sample (tp0.25_beoff_pcoff |
| EURUSD | 43 | 76.7 | -0.013 | 0.98 | -8.98 | 2.90 | 74.4 | -0.042 | in-sample | out-of-sample - met in-sample (87.2%) but not on purged folds; OOS exp |
| GBPUSD | 36 | 77.8 | +0.079 | 1.33 | +135.72 | 1.02 | 80.0 | +0.047 | OOS ✓ | none - target met in-sample AND out-of-sample (OOS 80.0% on 45 trades) |
| ETHUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 70.6 | +0.050 | in-sample | out-of-sample - met in-sample (80.0%) but not on purged folds; OOS win |
| GER40 | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 80.0 | +0.036 | OOS ✓ | none - target met in-sample AND out-of-sample (OOS 80.0% on 135 trades |
| NAS100 | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 77.6 | +0.010 | OOS ✓ | none - target met in-sample AND out-of-sample (OOS 77.6% on 76 trades) |
| NZDUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 78.2 | -0.016 | in-sample | out-of-sample - met in-sample (80.7%) but not on purged folds; OOS exp |
| UK100 | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 72.3 | +0.010 | in-sample | out-of-sample - met in-sample (80.6%) but not on purged folds; OOS win |
| US30 | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 74.7 | -0.060 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| USDCAD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 71.0 | +0.027 | in-sample | out-of-sample - met in-sample (81.7%) but not on purged folds; OOS win |
| XAGUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 80.0 | +0.035 | OOS ✓ | none - target met in-sample AND out-of-sample (OOS 80.0% on 200 trades |

### 2a. What the learned regime policy contributes

Calibration reports the same out-of-sample sample twice: once with every regime enabled, and once with the regimes the learned policy has switched off removed. Both are purged out-of-sample w.r.t. the geometry and threshold; the policy itself is fitted on **training folds only**, so it has never seen the out-of-sample bars it filters. The gap between the two columns is the policy's entire contribution — read it as the honest size of the effect, not as free money.

| Symbol | OOS n (all regimes) | OOS WR | OOS Exp (R) | OOS n (policy applied) | OOS WR | OOS Exp (R) | Policy fitted on |
|---|---:|---:|---:|---:|---:|---:|---|
| BTCUSD | 330 | 79.7 | +0.040 | 330 | 79.7 | +0.040 | train folds |
| SOLUSD | 118 | 66.1 | -0.044 | 118 | 66.1 | -0.044 | train folds |
| USDCHF | 218 | 78.9 | -0.012 | 202 | 79.2 | -0.010 | train folds |
| AUDUSD | 131 | 77.9 | -0.004 | 92 | 80.4 | +0.020 | train folds |
| XAUUSD | 144 | 80.6 | +0.025 | 144 | 80.6 | +0.025 | train folds |
| USDJPY | 63 | 66.7 | -0.052 | 63 | 66.7 | -0.052 | train folds |
| EURUSD | 192 | 70.3 | -0.095 | 121 | 74.4 | -0.042 | train folds |
| GBPUSD | 89 | 68.5 | -0.095 | 45 | 80.0 | +0.047 | train folds |
| ETHUSD | 68 | 70.6 | +0.050 | 68 | 70.6 | +0.050 | train folds |
| GER40 | 135 | 80.0 | +0.036 | 135 | 80.0 | +0.036 | train folds |
| NAS100 | 76 | 77.6 | +0.010 | 76 | 77.6 | +0.010 | train folds |
| NZDUSD | 124 | 72.6 | -0.078 | 87 | 78.2 | -0.016 | train folds |
| UK100 | 101 | 72.3 | +0.010 | 101 | 72.3 | +0.010 | train folds |
| US30 | 146 | 67.8 | -0.149 | 91 | 74.7 | -0.060 | train folds |
| USDCAD | 69 | 71.0 | +0.027 | 69 | 71.0 | +0.027 | train folds |
| XAGUSD | 200 | 80.0 | +0.035 | 200 | 80.0 | +0.035 | train folds |

## 3. Win-rate / expectancy frontier

For each target size (`tp_r`, as a multiple of initial risk) the table gives the highest win rate found at all, and the highest win rate that still carries positive expectancy. Where those two diverge, the gap is win rate that can only be bought by running a losing system. This is the direct evidence for whether the 75% target is reachable.

**AUDUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 88.5% | +0.107 | 61 | 88.5% | +0.107 | 61 |
| 0.3 | 86.2% | +0.130 | 58 | 86.2% | +0.130 | 58 |
| 0.4 | 74.5% | +0.066 | 51 | 74.5% | +0.066 | 51 |
| 0.5 | 61.0% | -0.082 | 105 | — | — | — |
| 0.6 | 59.7% | -0.050 | 57 | — | — | — |
| 0.75 | 59.4% | -0.112 | 96 | — | — | — |
| 1 | 58.8% | -0.087 | 51 | — | — | — |
| 1.5 | 56.9% | -0.088 | 51 | — | — | — |

**BTCUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 84.4% | +0.061 | 391 | 84.4% | +0.061 | 391 |
| 0.3 | 81.0% | +0.055 | 331 | 81.0% | +0.055 | 331 |
| 0.4 | 73.6% | +0.044 | 178 | 73.6% | +0.044 | 178 |
| 0.5 | 65.8% | +0.005 | 196 | 65.8% | +0.005 | 196 |
| 0.6 | 66.3% | +0.040 | 184 | 66.3% | +0.040 | 184 |
| 0.75 | 65.6% | +0.038 | 131 | 65.6% | +0.038 | 131 |
| 1 | 65.8% | +0.027 | 155 | 65.8% | +0.027 | 155 |
| 1.5 | 66.3% | +0.042 | 104 | 66.3% | +0.042 | 104 |

**ETHUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 81.3% | +0.028 | 91 | 81.3% | +0.028 | 91 |
| 0.3 | 80.0% | +0.055 | 90 | 80.0% | +0.055 | 90 |
| 0.4 | 73.3% | +0.051 | 75 | 73.3% | +0.051 | 75 |
| 0.5 | 66.7% | +0.045 | 66 | 66.7% | +0.045 | 66 |
| 0.6 | 65.0% | +0.067 | 60 | 65.0% | +0.067 | 60 |
| 0.75 | 67.9% | +0.142 | 56 | 67.9% | +0.142 | 56 |
| 1 | 67.5% | +0.100 | 77 | 67.5% | +0.100 | 77 |
| 1.5 | 70.8% | +0.190 | 48 | 70.8% | +0.190 | 48 |

**EURUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 87.2% | +0.090 | 47 | 87.2% | +0.090 | 47 |
| 0.3 | 84.1% | +0.101 | 44 | 84.1% | +0.101 | 44 |
| 0.4 | 76.5% | +0.081 | 34 | 76.5% | +0.081 | 34 |
| 0.5 | 71.4% | +0.058 | 28 | 71.4% | +0.058 | 28 |
| 0.6 | 67.9% | +0.045 | 28 | 67.9% | +0.045 | 28 |
| 0.75 | 63.2% | -0.024 | 68 | 61.3% | +0.001 | 75 |
| 1 | 66.7% | +0.031 | 24 | 66.7% | +0.031 | 24 |
| 1.5 | 60.9% | -0.016 | 92 | — | — | — |

**GBPUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 87.9% | +0.099 | 58 | 87.9% | +0.099 | 58 |
| 0.3 | 84.6% | +0.100 | 52 | 84.6% | +0.100 | 52 |
| 0.4 | 81.2% | +0.152 | 48 | 81.2% | +0.152 | 48 |
| 0.5 | 72.1% | +0.081 | 43 | 72.1% | +0.081 | 43 |
| 0.6 | 71.0% | +0.101 | 38 | 71.0% | +0.101 | 38 |
| 0.75 | 71.0% | +0.096 | 38 | 71.0% | +0.096 | 38 |
| 1 | 65.7% | -0.006 | 35 | 61.4% | +0.007 | 70 |
| 1.5 | 70.0% | +0.082 | 30 | 70.0% | +0.082 | 30 |

**GER40**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 85.0% | +0.063 | 127 | 85.0% | +0.063 | 127 |
| 0.3 | 83.0% | +0.080 | 118 | 83.0% | +0.080 | 118 |
| 0.4 | 75.0% | +0.050 | 100 | 75.0% | +0.050 | 100 |
| 0.5 | 70.2% | +0.054 | 134 | 70.2% | +0.054 | 134 |
| 0.6 | 63.9% | +0.022 | 86 | 63.9% | +0.022 | 86 |
| 0.75 | 64.0% | +0.049 | 111 | 64.0% | +0.049 | 111 |
| 1 | 61.8% | +0.038 | 102 | 61.8% | +0.038 | 102 |
| 1.5 | 62.2% | +0.067 | 90 | 62.2% | +0.067 | 90 |

**NAS100**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 83.0% | +0.038 | 264 | 83.0% | +0.038 | 264 |
| 0.3 | 80.0% | +0.060 | 100 | 80.0% | +0.060 | 100 |
| 0.4 | 73.2% | +0.044 | 108 | 73.2% | +0.044 | 108 |
| 0.5 | 68.5% | +0.035 | 92 | 68.5% | +0.035 | 92 |
| 0.6 | 64.9% | +0.019 | 131 | 64.9% | +0.019 | 131 |
| 0.75 | 64.8% | +0.005 | 122 | 64.8% | +0.005 | 122 |
| 1 | 63.5% | +0.011 | 74 | 63.5% | +0.011 | 74 |
| 1.5 | 60.3% | -0.006 | 58 | 52.0% | +0.047 | 50 |

**NZDUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 80.7% | +0.008 | 124 | 80.7% | +0.008 | 124 |
| 0.3 | 76.8% | -0.001 | 112 | — | — | — |
| 0.4 | 70.0% | -0.020 | 30 | — | — | — |
| 0.5 | 63.0% | -0.056 | 27 | — | — | — |
| 0.6 | 61.5% | -0.051 | 26 | — | — | — |
| 0.75 | 60.0% | -0.051 | 25 | — | — | — |
| 1 | 62.5% | -0.015 | 24 | 50.0% | +0.008 | 20 |
| 1.5 | 59.2% | -0.085 | 71 | — | — | — |

**SOLUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 77.8% | -0.014 | 176 | — | — | — |
| 0.3 | 74.8% | -0.007 | 159 | — | — | — |
| 0.4 | 66.7% | -0.034 | 132 | — | — | — |
| 0.5 | 60.4% | -0.063 | 111 | — | — | — |
| 0.6 | 60.9% | -0.015 | 92 | — | — | — |
| 0.75 | 60.9% | -0.025 | 87 | — | — | — |
| 1 | 63.2% | +0.031 | 76 | 63.2% | +0.031 | 76 |
| 1.5 | 69.2% | +0.099 | 52 | 69.2% | +0.099 | 52 |

**UK100**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 87.1% | +0.089 | 271 | 87.1% | +0.089 | 271 |
| 0.3 | 86.4% | +0.122 | 249 | 86.4% | +0.122 | 249 |
| 0.4 | 80.6% | +0.129 | 160 | 80.6% | +0.129 | 160 |
| 0.5 | 72.7% | +0.090 | 128 | 72.7% | +0.090 | 128 |
| 0.6 | 71.8% | +0.114 | 117 | 71.8% | +0.114 | 117 |
| 0.75 | 69.1% | +0.122 | 55 | 69.1% | +0.122 | 55 |
| 1 | 68.5% | +0.145 | 54 | 68.5% | +0.145 | 54 |
| 1.5 | 67.4% | +0.088 | 89 | 67.4% | +0.088 | 89 |

**US30**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 78.6% | +0.007 | 28 | 78.6% | +0.010 | 28 |
| 0.3 | 80.8% | +0.075 | 26 | 80.8% | +0.080 | 26 |
| 0.4 | 73.9% | +0.043 | 23 | 73.9% | +0.052 | 23 |
| 0.5 | 72.7% | +0.085 | 22 | 72.7% | +0.085 | 22 |
| 0.6 | 61.9% | +0.010 | 21 | 61.9% | +0.043 | 21 |
| 0.75 | 65.0% | +0.062 | 20 | 65.0% | +0.092 | 20 |
| 1 | 66.7% | -0.030 | 18 | — | — | — |
| 1.5 | 64.7% | -0.044 | 17 | — | — | — |

**USDCAD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 83.6% | +0.045 | 128 | 83.6% | +0.045 | 128 |
| 0.3 | 81.7% | +0.061 | 71 | 81.7% | +0.061 | 71 |
| 0.4 | 75.0% | +0.062 | 64 | 75.0% | +0.062 | 64 |
| 0.5 | 71.9% | +0.091 | 57 | 71.9% | +0.091 | 57 |
| 0.6 | 71.4% | +0.085 | 56 | 71.4% | +0.087 | 56 |
| 0.75 | 70.6% | +0.079 | 51 | 70.6% | +0.081 | 51 |
| 1 | 69.4% | +0.082 | 49 | 69.4% | +0.085 | 49 |
| 1.5 | 68.1% | +0.073 | 47 | 68.1% | +0.073 | 47 |

**USDCHF**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 79.8% | +0.001 | 248 | 79.8% | +0.001 | 248 |
| 0.3 | 74.6% | -0.030 | 67 | — | — | — |
| 0.4 | 69.5% | -0.013 | 59 | — | — | — |
| 0.5 | 61.5% | -0.061 | 52 | — | — | — |
| 0.6 | 62.0% | -0.038 | 50 | — | — | — |
| 0.75 | 57.6% | -0.085 | 132 | — | — | — |
| 1 | 56.8% | -0.078 | 125 | 50.0% | +0.009 | 42 |
| 1.5 | 60.5% | -0.045 | 43 | — | — | — |

**USDJPY**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 80.5% | +0.017 | 113 | 80.5% | +0.017 | 113 |
| 0.3 | 77.0% | +0.013 | 100 | 77.0% | +0.013 | 100 |
| 0.4 | 69.0% | -0.027 | 142 | — | — | — |
| 0.5 | 66.2% | +0.010 | 77 | 66.2% | +0.010 | 77 |
| 0.6 | 67.4% | +0.041 | 43 | 67.4% | +0.041 | 43 |
| 0.75 | 66.7% | +0.091 | 42 | 66.7% | +0.091 | 42 |
| 1 | 65.8% | +0.032 | 38 | 65.8% | +0.032 | 38 |
| 1.5 | 72.2% | +0.148 | 36 | 72.2% | +0.148 | 36 |

**XAGUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 86.3% | +0.079 | 234 | 86.3% | +0.079 | 234 |
| 0.3 | 84.2% | +0.095 | 272 | 84.2% | +0.095 | 272 |
| 0.4 | 76.3% | +0.074 | 139 | 76.3% | +0.074 | 139 |
| 0.5 | 70.8% | +0.062 | 72 | 70.8% | +0.062 | 72 |
| 0.6 | 68.9% | +0.066 | 135 | 68.9% | +0.066 | 135 |
| 0.75 | 66.4% | +0.072 | 149 | 66.4% | +0.072 | 149 |
| 1 | 66.4% | +0.142 | 113 | 66.4% | +0.142 | 113 |
| 1.5 | 61.9% | +0.071 | 97 | 61.9% | +0.071 | 97 |

**XAUUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 85.8% | +0.075 | 127 | 85.8% | +0.075 | 127 |
| 0.3 | 82.4% | +0.076 | 74 | 82.4% | +0.076 | 74 |
| 0.4 | 76.4% | +0.072 | 182 | 76.4% | +0.072 | 182 |
| 0.5 | 69.4% | +0.046 | 144 | 69.4% | +0.046 | 144 |
| 0.6 | 67.7% | +0.048 | 65 | 67.7% | +0.048 | 65 |
| 0.75 | 68.4% | +0.056 | 76 | 68.4% | +0.056 | 76 |
| 1 | 70.8% | +0.106 | 72 | 70.8% | +0.106 | 72 |
| 1.5 | 66.1% | +0.037 | 62 | 66.1% | +0.037 | 62 |

## 4. Calibrated geometry per symbol

`tp_r` is the target distance in multiples of initial risk; `be` is the breakeven trigger (off = never lock breakeven); `pc` the partial bank; `mb` the time stop in bars; `thr` the entry score threshold.

The last two columns come from the isotonic score calibration: the realised win probability the fitted map assigns to a score sitting exactly at the deployed threshold, and the Brier score of that map (lower is better; 0.25 is the skill-free baseline for a balanced sample). The calibration is monotone, so it cannot change which candidates pass — it exists to make the score interpretable and to give the AI layer a probability rather than an arbitrary index.

| Symbol | tp_r | be | pc | mb | threshold | P(win) @ thr | Brier | Enabled regimes |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| AUDUSD | 0.3 | off | off | 48 | 0.655 | 75.5% | 0.172 | BREAKOUT, TREND_BEAR, LIQUIDITY_SWEEP, COMPRESSION |
| BTCUSD | 0.3 | off | off | 48 | 0.400 | 75.0% | 0.162 | TREND_BULL, COMPRESSION, TREND_BEAR, BREAKOUT, LIQUIDITY_SWEEP, LOW_VOLATILITY |
| ETHUSD | 0.3 | off | off | 48 | 0.694 | 69.5% | 0.207 | TREND_BULL, TREND_BEAR, LIQUIDITY_SWEEP, COMPRESSION, BREAKOUT |
| EURUSD | 0.25 | off | off | 24 | 0.728 | 70.8% | 0.199 | BREAKOUT, TREND_BEAR, LIQUIDITY_SWEEP, COMPRESSION |
| GBPUSD | 0.3 | off | off | 48 | 0.741 | 69.1% | 0.212 | TREND_BEAR, BREAKOUT, LIQUIDITY_SWEEP |
| GER40 | 0.3 | off | off | 24 | 0.657 | 77.6% | 0.157 | TREND_BULL, TREND_BEAR, BREAKOUT, LIQUIDITY_SWEEP, COMPRESSION |
| NAS100 | 0.25 | off | off | 24 | 0.630 | 77.3% | 0.174 | TREND_BEAR, TREND_BULL, BREAKOUT, LIQUIDITY_SWEEP, COMPRESSION |
| NZDUSD | 0.25 | off | off | 48 | 0.538 | 70.4% | 0.196 | COMPRESSION, TREND_BEAR, BREAKOUT |
| SOLUSD | 1.5 | 1 | off | 24 | 0.641 | 64.1% | 0.221 | TREND_BEAR, TREND_BULL, BREAKOUT, LIQUIDITY_SWEEP, LOW_VOLATILITY, COMPRESSION |
| UK100 | 0.4 | off | off | 24 | 0.561 | 66.7% | 0.193 | TREND_BULL, BREAKOUT, TREND_BEAR, LIQUIDITY_SWEEP, COMPRESSION |
| US30 | 0.25 | off | off | 48 | 0.604 | 66.7% | 0.217 | BREAKOUT, TREND_BEAR, LOW_VOLATILITY |
| USDCAD | 0.3 | off | off | 48 | 0.607 | 71.0% | 0.206 | TREND_BULL, TREND_BEAR, LIQUIDITY_SWEEP, BREAKOUT, COMPRESSION |
| USDCHF | 0.25 | off | off | 48 | 0.400 | 78.7% | 0.166 | TREND_BULL, TREND_BEAR, LIQUIDITY_SWEEP, COMPRESSION |
| USDJPY | 0.75 | off | 0.5 | 48 | 0.724 | 54.2% | 0.213 | TREND_BULL, TREND_BEAR, BREAKOUT |
| XAGUSD | 0.3 | off | off | 24 | 0.609 | 80.0% | 0.160 | TREND_BEAR, TREND_BULL, BREAKOUT, LIQUIDITY_SWEEP, COMPRESSION |
| XAUUSD | 0.3 | off | off | 24 | 0.744 | 83.3% | 0.156 | TREND_BULL, TREND_BEAR, BREAKOUT, LIQUIDITY_SWEEP, COMPRESSION, WEAK_TREND |

## 5. Portfolio risk allocation (HRP)

Inverse-variance weights from the HRP allocator over per-symbol daily R:

| Symbol | Risk weight |
|---|---:|
| GBPUSD | 30.92% |
| EURUSD | 22.59% |
| AUDUSD | 13.51% |
| USDJPY | 12.21% |
| USDCHF | 6.74% |
| XAUUSD | 6.22% |
| SOLUSD | 5.04% |
| BTCUSD | 2.76% |

## 6. Entry rejection analysis

Why candidates were not traded under the calibrated profile. In calibrated mode these are the capital-protection gates plus the calibrated edge filter; the legacy 29-check stack no longer decides.

Counts are per **bar evaluated**, not per candidate: a bar on which the pipeline found no setup at all (`no directional bias`) is counted here too, because the engine has to consider and decline it. A large `no directional bias` count therefore means the pipeline rarely formed a view, not that a good trade was vetoed.

| Symbol | Top rejection reasons |
|---|---|
| BTCUSD | capital protection: Directional Bias (216); Max 8 trades per day reached for symbol BTCUSD. (30); Cooldown active for 2 more bars. (4) |
| SOLUSD | capital protection: Directional Bias (211); score 0.520 below calibrated threshold 0.641 (30); score 0.540 below calibrated threshold 0.641 (16) |
| USDCHF | Max Daily Loss breached (4.18% >= 4.00%). Trading halted for today. (612); capital protection: Directional Bias (345); regime BREAKOUT disabled by lea |
| AUDUSD | regime TREND_BULL disabled by learned policy (expectancy -0.068R worse than -0.050R over 183 trades) (444); capital protection: Directional Bias (371) |
| XAUUSD | capital protection: Directional Bias (192); score 0.520 below calibrated threshold 0.744 (35); score 0.480 below calibrated threshold 0.744 (25) |
| USDJPY | capital protection: Directional Bias (286); score 0.520 below calibrated threshold 0.724 (48); score 0.480 below calibrated threshold 0.724 (41) |
| EURUSD | regime TREND_BULL disabled by learned policy (expectancy -0.076R worse than -0.050R over 259 trades) (484); capital protection: Directional Bias (397) |
| GBPUSD | regime TREND_BULL disabled by learned policy (expectancy -0.052R worse than -0.050R over 151 trades) (565); capital protection: Directional Bias (389) |
| ETHUSD | capital protection: Spread Protection (1514); capital protection: Spread Protection, Directional Bias (715) |
| GER40 | capital protection: Spread Protection (1220); capital protection: Spread Protection, Directional Bias (274); capital protection: Market Session Open,  |
| NAS100 | capital protection: Spread Protection (1090); capital protection: Spread Protection, Directional Bias (401); capital protection: Market Session Open,  |
| NZDUSD | capital protection: Spread Protection (1159); capital protection: Spread Protection, Directional Bias (407); capital protection: Market Session Open,  |
| UK100 | capital protection: Spread Protection (1018); capital protection: Spread Protection, Directional Bias (451); capital protection: Market Session Open,  |
| US30 | capital protection: Spread Protection (1065); capital protection: Spread Protection, Directional Bias (426); capital protection: Market Session Open,  |
| USDCAD | capital protection: Spread Protection (1194); capital protection: Spread Protection, Directional Bias (372); capital protection: Market Session Open,  |
| XAGUSD | Risk Ceiling Exceeded: Minimum tradeable lot size exceeds safe account risk limits. (694); capital protection: Directional Bias (307); score 0.520 bel |

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

# JARVIS AI — 3-Month Backtest on Real MT5 Data

**Mode:** calibrated per-symbol win-rate profiles  
**Data:** MT5 terminal, real H1 bars (validated non-synthetic)  
**Initial balance:** $10,000.00 at 0.5% risk per trade  
**Generated:** 2026-09-11T20:34:12.983630+00:00

## 1. Headline

| Metric | Value |
|---|---|
| Symbols traded | 16 |
| Total trades | 55 |
| Win rate | 58.18% |
| Expectancy | +0.1557 R per trade |
| Average win / loss | +1.000 R / -1.019 R |
| Payoff ratio | 0.981 |
| Total R | +8.56 R |
| Profit factor | 1.19 |
| Net profit | $+199.75 |
| Max drawdown | 1.80% |
| Sharpe / Sortino / Calmar | 0.62 / 2.21 / 1.11 |
| Expectancy (sample-uniqueness weighted) | -0.0198 R |

**Win-rate target outcome:** 4/16 symbols met the target out-of-sample. 3 met it in-sample but failed out-of-sample (i.e. the target was reachable only by overfitting).

## 2. Per-symbol results

| Symbol | Trades | WR % | Exp (R) | PF | Net $ | MaxDD % | OOS WR % | OOS Exp (R) | Target | Binding constraint |
|---|---:|---:|---:|---:|---:|---:|---:|---:|:--:|---|
| GBPUSD | 55 | 58.2 | +0.156 | 1.19 | +199.75 | 1.80 | 58.2 | +0.156 | ✗ | win rate - best achievable in-sample with positive expectancy is 65.2% |
| AUDUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 34.6 | +0.000 | OOS ✓ | expectancy - the walk-forward-selected configuration has no edge out o |
| BTCUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 7.7 | +0.000 | in-sample | out-of-sample - met in-sample (79.6%) but not on purged folds; OOS exp |
| ETHUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 16.7 | +0.000 | in-sample | out-of-sample - met in-sample (76.0%) but not on purged folds; OOS exp |
| EURUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 50.0 | +0.000 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| GER40 | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 0.0 | +0.000 | ✗ | win rate - best achievable in-sample with positive expectancy is 70.2% |
| NAS100 | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 43.5 | +0.000 | OOS ✓ | none - target met in-sample AND out-of-sample (OOS 80.0% on 90 trades) |
| NZDUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 39.0 | +0.000 | ✗ | selection stability - 77.8% is reachable in-sample (tp0.3_beoff_pcoffx |
| SOLUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 45.8 | +0.000 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| UK100 | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 0.0 | +0.000 | OOS ✓ | selection stability - 82.0% is reachable in-sample (tp0.25_beoff_pcoff |
| US30 | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 54.8 | +0.000 | ✗ | win rate - best achievable in-sample with positive expectancy is 65.8% |
| USDCAD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 50.0 | +0.000 | in-sample | out-of-sample - met in-sample (75.4%) but not on purged folds; OOS exp |
| USDCHF | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 41.2 | +0.000 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| USDJPY | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 69.0 | +0.000 | ✗ | win rate - best achievable in-sample with positive expectancy is 69.8% |
| XAGUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 48.2 | +0.000 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| XAUUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 46.0 | +0.000 | OOS ✓ | none - target met in-sample AND out-of-sample (OOS 78.7% on 136 trades |

### 2a. What the learned regime policy contributes

Calibration reports the same out-of-sample sample twice: once with every regime enabled, and once with the regimes the learned policy has switched off removed. Both are purged out-of-sample w.r.t. the geometry and threshold; the policy itself is fitted on **training folds only**, so it has never seen the out-of-sample bars it filters. The gap between the two columns is the policy's entire contribution — read it as the honest size of the effect, not as free money.

| Symbol | OOS n (all regimes) | OOS WR | OOS Exp (R) | OOS n (policy applied) | OOS WR | OOS Exp (R) | Policy fitted on |
|---|---:|---:|---:|---:|---:|---:|---|
| GBPUSD | 75 | 54.7 | -0.126 | 55 | 58.2 | +0.156 | train folds |
| AUDUSD | 144 | 70.1 | -0.123 | 52 | 34.6 | +0.000 | train folds |
| BTCUSD | 193 | 72.5 | -0.045 | 13 | 7.7 | +0.000 | train folds |
| ETHUSD | 64 | 67.2 | -0.037 | 6 | 16.7 | +0.000 | train folds |
| EURUSD | 118 | 60.2 | -0.213 | 2 | 50.0 | +0.000 | train folds |
| GER40 | 160 | 65.6 | -0.053 | 0 | 0.0 | +0.000 | train folds |
| NAS100 | 90 | 80.0 | +0.063 | 62 | 43.5 | +0.000 | train folds |
| NZDUSD | 89 | 70.8 | -0.034 | 41 | 39.0 | +0.000 | train folds |
| SOLUSD | 76 | 60.5 | -0.083 | 72 | 45.8 | +0.000 | train folds |
| UK100 | 115 | 65.2 | -0.142 | 0 | 0.0 | +0.000 | train folds |
| US30 | 61 | 54.1 | -0.198 | 31 | 54.8 | +0.000 | train folds |
| USDCAD | 64 | 70.3 | -0.020 | 42 | 50.0 | +0.000 | train folds |
| USDCHF | 165 | 73.3 | -0.083 | 51 | 41.2 | +0.000 | train folds |
| USDJPY | 49 | 67.3 | +0.033 | 58 | 69.0 | +0.000 | train folds |
| XAGUSD | 118 | 72.0 | -0.076 | 56 | 48.2 | +0.000 | train folds |
| XAUUSD | 136 | 78.7 | +0.025 | 37 | 46.0 | +0.000 | train folds |

## 3. Win-rate / expectancy frontier

For each target size (`tp_r`, as a multiple of initial risk) the table gives the highest win rate found at all, and the highest win rate that still carries positive expectancy. Where those two diverge, the gap is win rate that can only be bought by running a losing system. This is the direct evidence for whether the 75% target is reachable.

**AUDUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 83.6% | +0.045 | 55 | 83.6% | +0.045 | 55 |
| 0.3 | 80.4% | +0.045 | 51 | 80.4% | +0.045 | 51 |
| 0.4 | 71.7% | +0.004 | 46 | 71.7% | +0.004 | 46 |
| 0.5 | 59.5% | -0.107 | 42 | — | — | — |
| 0.6 | 62.5% | -0.053 | 48 | — | — | — |
| 0.75 | 61.4% | -0.088 | 70 | — | — | — |
| 1 | 63.2% | -0.071 | 38 | — | — | — |
| 1.5 | 64.7% | -0.057 | 17 | — | — | — |

**BTCUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 79.6% | +0.000 | 152 | 79.6% | +0.000 | 152 |
| 0.3 | 76.2% | -0.009 | 80 | — | — | — |
| 0.4 | 69.0% | -0.027 | 113 | — | — | — |
| 0.5 | 65.8% | -0.002 | 41 | — | — | — |
| 0.6 | 68.3% | +0.064 | 41 | 68.3% | +0.064 | 41 |
| 0.75 | 69.2% | +0.091 | 39 | 69.2% | +0.091 | 39 |
| 1 | 63.9% | +0.003 | 83 | 63.9% | +0.003 | 83 |
| 1.5 | 66.7% | +0.080 | 27 | 66.7% | +0.080 | 27 |

**ETHUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 79.2% | +0.005 | 72 | 79.2% | +0.005 | 72 |
| 0.3 | 76.8% | +0.011 | 69 | 76.8% | +0.011 | 69 |
| 0.4 | 71.9% | +0.031 | 64 | 71.9% | +0.031 | 64 |
| 0.5 | 65.5% | +0.020 | 58 | 65.5% | +0.020 | 58 |
| 0.6 | 63.6% | +0.022 | 55 | 63.6% | +0.022 | 55 |
| 0.75 | 66.0% | +0.082 | 53 | 66.0% | +0.082 | 53 |
| 1 | 66.0% | +0.076 | 47 | 66.0% | +0.076 | 47 |
| 1.5 | 70.5% | +0.126 | 44 | 70.5% | +0.126 | 44 |

**EURUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 78.0% | -0.025 | 50 | — | — | — |
| 0.3 | 75.0% | -0.028 | 108 | — | — | — |
| 0.4 | 67.5% | -0.055 | 40 | — | — | — |
| 0.5 | 66.0% | -0.012 | 47 | — | — | — |
| 0.6 | 68.2% | +0.042 | 44 | 68.2% | +0.042 | 44 |
| 0.75 | 64.3% | +0.003 | 42 | 64.3% | +0.003 | 42 |
| 1 | 65.0% | +0.007 | 40 | 65.0% | +0.007 | 40 |
| 1.5 | 63.9% | -0.030 | 36 | 46.2% | +0.017 | 26 |

**GBPUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 75.4% | -0.047 | 110 | — | — | — |
| 0.3 | 73.1% | -0.034 | 104 | — | — | — |
| 0.4 | 68.8% | -0.033 | 77 | — | — | — |
| 0.5 | 60.7% | -0.072 | 84 | — | — | — |
| 0.6 | 60.3% | -0.047 | 78 | — | — | — |
| 0.75 | 61.8% | -0.003 | 76 | — | — | — |
| 1 | 65.2% | +0.053 | 69 | 65.2% | +0.053 | 69 |
| 1.5 | 62.1% | -0.011 | 58 | 56.9% | +0.030 | 51 |

**GER40**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 77.7% | -0.029 | 130 | — | — | — |
| 0.3 | 75.8% | -0.015 | 165 | — | — | — |
| 0.4 | 69.1% | -0.033 | 152 | — | — | — |
| 0.5 | 69.1% | +0.036 | 139 | 69.1% | +0.036 | 139 |
| 0.6 | 70.2% | +0.092 | 134 | 70.2% | +0.092 | 134 |
| 0.75 | 66.1% | +0.062 | 124 | 66.1% | +0.062 | 124 |
| 1 | 64.7% | +0.052 | 119 | 64.7% | +0.052 | 119 |
| 1.5 | 68.3% | +0.127 | 104 | 68.3% | +0.127 | 104 |

**NAS100**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 82.2% | +0.031 | 101 | 82.2% | +0.031 | 101 |
| 0.3 | 83.0% | +0.084 | 94 | 83.0% | +0.084 | 94 |
| 0.4 | 76.5% | +0.076 | 81 | 76.5% | +0.076 | 81 |
| 0.5 | 66.7% | +0.010 | 72 | 66.7% | +0.010 | 72 |
| 0.6 | 66.7% | +0.025 | 69 | 66.7% | +0.025 | 69 |
| 0.75 | 67.2% | +0.029 | 64 | 67.2% | +0.029 | 64 |
| 1 | 67.8% | +0.053 | 59 | 67.8% | +0.053 | 59 |
| 1.5 | 69.4% | +0.058 | 36 | 69.4% | +0.058 | 36 |

**NZDUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 78.6% | -0.011 | 56 | — | — | — |
| 0.3 | 77.8% | +0.011 | 36 | 77.8% | +0.011 | 36 |
| 0.4 | 75.5% | +0.057 | 49 | 75.5% | +0.057 | 49 |
| 0.5 | 63.0% | -0.056 | 27 | — | — | — |
| 0.6 | 61.7% | -0.066 | 47 | — | — | — |
| 0.75 | 61.7% | -0.063 | 47 | — | — | — |
| 1 | 62.5% | +0.002 | 32 | 62.5% | +0.002 | 32 |
| 1.5 | 63.6% | -0.004 | 33 | 63.3% | +0.006 | 30 |

**SOLUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 73.2% | -0.060 | 97 | — | — | — |
| 0.3 | 70.8% | -0.053 | 89 | — | — | — |
| 0.4 | 64.3% | -0.071 | 84 | — | — | — |
| 0.5 | 59.5% | -0.070 | 74 | — | — | — |
| 0.6 | 59.8% | -0.036 | 92 | — | — | — |
| 0.75 | 59.8% | -0.029 | 87 | 53.8% | +0.001 | 78 |
| 1 | 61.3% | +0.017 | 75 | 61.3% | +0.017 | 75 |
| 1.5 | 69.2% | +0.069 | 52 | 69.2% | +0.069 | 52 |

**UK100**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 82.0% | +0.025 | 50 | 82.0% | +0.025 | 50 |
| 0.3 | 77.3% | +0.004 | 44 | 77.3% | +0.004 | 44 |
| 0.4 | 76.2% | +0.067 | 42 | 76.2% | +0.067 | 42 |
| 0.5 | 71.0% | +0.066 | 38 | 71.0% | +0.066 | 38 |
| 0.6 | 69.4% | +0.111 | 36 | 69.4% | +0.111 | 36 |
| 0.75 | 66.7% | +0.031 | 21 | 66.7% | +0.031 | 21 |
| 1 | 70.0% | +0.075 | 20 | 70.0% | +0.075 | 20 |
| 1.5 | 73.7% | +0.149 | 19 | 67.9% | +0.087 | 84 |

**US30**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 76.0% | -0.049 | 150 | — | — | — |
| 0.3 | 71.9% | -0.064 | 192 | — | — | — |
| 0.4 | 67.7% | -0.044 | 158 | — | — | — |
| 0.5 | 61.8% | -0.074 | 89 | — | — | — |
| 0.6 | 62.6% | -0.002 | 83 | — | — | — |
| 0.75 | 63.9% | +0.023 | 83 | 63.9% | +0.023 | 83 |
| 1 | 65.8% | +0.065 | 76 | 65.8% | +0.065 | 76 |
| 1.5 | 66.7% | -0.008 | 39 | 65.7% | +0.002 | 67 |

**USDCAD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 81.0% | +0.012 | 126 | 81.0% | +0.012 | 126 |
| 0.3 | 77.4% | +0.005 | 115 | 77.4% | +0.005 | 115 |
| 0.4 | 75.4% | +0.071 | 57 | 75.4% | +0.073 | 57 |
| 0.5 | 73.1% | +0.115 | 52 | 73.1% | +0.115 | 52 |
| 0.6 | 72.9% | +0.117 | 48 | 72.9% | +0.117 | 48 |
| 0.75 | 71.0% | +0.102 | 69 | 71.0% | +0.102 | 69 |
| 1 | 66.7% | +0.021 | 45 | 66.7% | +0.021 | 45 |
| 1.5 | 67.5% | +0.027 | 40 | 67.5% | +0.027 | 40 |

**USDCHF**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 76.6% | -0.043 | 47 | — | — | — |
| 0.3 | 69.8% | -0.076 | 43 | — | — | — |
| 0.4 | 64.9% | -0.072 | 37 | — | — | — |
| 0.5 | 61.1% | -0.045 | 36 | — | — | — |
| 0.6 | 63.0% | -0.045 | 27 | — | — | — |
| 0.75 | 64.0% | +0.004 | 25 | 64.0% | +0.004 | 25 |
| 1 | 64.3% | +0.002 | 28 | 64.3% | +0.002 | 28 |
| 1.5 | 71.4% | +0.035 | 21 | 71.4% | +0.035 | 21 |

**USDJPY**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 75.4% | -0.044 | 57 | — | — | — |
| 0.3 | 74.1% | -0.023 | 54 | — | — | — |
| 0.4 | 69.6% | -0.015 | 79 | — | — | — |
| 0.5 | 69.6% | +0.060 | 46 | 69.6% | +0.060 | 46 |
| 0.6 | 69.8% | +0.078 | 43 | 69.8% | +0.078 | 43 |
| 0.75 | 69.0% | +0.090 | 42 | 69.0% | +0.090 | 42 |
| 1 | 68.4% | +0.085 | 38 | 68.4% | +0.085 | 38 |
| 1.5 | 69.4% | +0.079 | 36 | 69.4% | +0.079 | 36 |

**XAGUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 86.7% | +0.083 | 30 | 86.7% | +0.083 | 30 |
| 0.3 | 85.7% | +0.114 | 28 | 85.7% | +0.114 | 28 |
| 0.4 | 83.3% | +0.167 | 24 | 83.3% | +0.167 | 24 |
| 0.5 | 81.0% | +0.233 | 21 | 81.0% | +0.233 | 21 |
| 0.6 | 80.0% | +0.238 | 20 | 80.0% | +0.238 | 20 |
| 0.75 | 83.3% | +0.280 | 18 | 60.0% | +0.012 | 30 |
| 1 | 83.3% | +0.277 | 18 | 51.9% | +0.021 | 52 |
| 1.5 | 87.5% | +0.274 | 16 | 58.3% | +0.001 | 24 |

**XAUUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 80.0% | +0.004 | 220 | 80.0% | +0.004 | 220 |
| 0.3 | 80.0% | +0.046 | 60 | 80.0% | +0.046 | 60 |
| 0.4 | 71.4% | +0.012 | 56 | 71.4% | +0.012 | 56 |
| 0.5 | 65.4% | -0.019 | 78 | 65.1% | +0.005 | 152 |
| 0.6 | 65.6% | +0.014 | 61 | 65.6% | +0.014 | 61 |
| 0.75 | 64.4% | +0.008 | 73 | 64.4% | +0.008 | 73 |
| 1 | 65.7% | +0.056 | 70 | 65.7% | +0.056 | 70 |
| 1.5 | 62.9% | +0.028 | 35 | 62.9% | +0.028 | 35 |

## 4. Calibrated geometry per symbol

`tp_r` is the target distance in multiples of initial risk; `be` is the breakeven trigger (off = never lock breakeven); `pc` the partial bank; `mb` the time stop in bars; `thr` the entry score threshold.

The last two columns come from the isotonic score calibration: the realised win probability the fitted map assigns to a score sitting exactly at the deployed threshold, and the Brier score of that map (lower is better; 0.25 is the skill-free baseline for a balanced sample). The calibration is monotone, so it cannot change which candidates pass — it exists to make the score interpretable and to give the AI layer a probability rather than an arbitrary index.

| Symbol | tp_r | be | pc | mb | threshold | P(win) @ thr | Brier | Enabled regimes |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| AUDUSD | 0.25 | off | off | 48 | 0.417 | 68.9% | 0.209 | TREND_BEAR, LIQUIDITY_SWEEP, COMPRESSION, LOW_VOLATILITY |
| BTCUSD | 0.25 | off | off | 48 | 0.605 | 74.0% | 0.198 | TREND_BULL, COMPRESSION, BREAKOUT, LIQUIDITY_SWEEP, LOW_VOLATILITY |
| ETHUSD | 0.25 | off | off | 24 | 0.670 | 61.3% | 0.217 | TREND_BULL, TREND_BEAR, COMPRESSION, LIQUIDITY_SWEEP, BREAKOUT |
| EURUSD | 0.25 | off | off | 48 | 0.711 | 55.7% | 0.234 | TREND_BEAR, BREAKOUT, WEAK_TREND, COMPRESSION |
| GBPUSD | 1.5 | off | off | 48 | 0.678 | 54.4% | 0.235 | TREND_BULL, TREND_BEAR, LIQUIDITY_SWEEP, COMPRESSION |
| GER40 | 1.5 | off | 0.5 | 24 | 0.656 | 65.7% | 0.226 | TREND_BULL, TREND_BEAR, BREAKOUT, COMPRESSION |
| NAS100 | 0.3 | off | off | 24 | 0.657 | 79.5% | 0.160 | TREND_BEAR, TREND_BULL, BREAKOUT, LIQUIDITY_SWEEP, COMPRESSION |
| NZDUSD | 0.4 | off | off | 48 | 0.635 | 68.8% | 0.204 | TREND_BEAR, LIQUIDITY_SWEEP, BREAKOUT |
| SOLUSD | 1.5 | 1 | off | 24 | 0.636 | 50.0% | 0.236 | TREND_BEAR, TREND_BULL, BREAKOUT, COMPRESSION |
| UK100 | 1 | off | 0.5 | 24 | 0.693 | 58.8% | 0.215 | TREND_BEAR, LIQUIDITY_SWEEP, COMPRESSION, LOW_VOLATILITY |
| US30 | 1.5 | off | 0.5 | 48 | 0.638 | 50.0% | 0.248 | TREND_BULL, LIQUIDITY_SWEEP, TREND_BEAR, BREAKOUT |
| USDCAD | 0.4 | off | off | 48 | 0.724 | 66.7% | 0.208 | TREND_BULL, TREND_BEAR, LIQUIDITY_SWEEP, BREAKOUT |
| USDCHF | 0.25 | off | off | 48 | 0.400 | 73.2% | 0.196 | TREND_BEAR, LIQUIDITY_SWEEP, COMPRESSION |
| USDJPY | 0.6 | off | 0.5 | 48 | 0.724 | 58.3% | 0.217 | TREND_BULL, TREND_BEAR, BREAKOUT |
| XAGUSD | 0.25 | off | off | 48 | 0.400 | 71.4% | 0.201 | TREND_BEAR, LIQUIDITY_SWEEP, COMPRESSION |
| XAUUSD | 0.3 | off | off | 24 | 0.744 | 79.0% | 0.168 | TREND_BULL, TREND_BEAR, BREAKOUT, COMPRESSION, LIQUIDITY_SWEEP, WEAK_TREND |

## 5. Portfolio risk allocation (HRP)

Not enough aligned per-symbol return history to compute HRP weights.

## 6. Entry rejection analysis

Why candidates were not traded under the calibrated profile. In calibrated mode these are the capital-protection gates plus the calibrated edge filter; the legacy 29-check stack no longer decides.

Counts are per **bar evaluated**, not per candidate: a bar on which the pipeline found no setup at all (`no directional bias`) is counted here too, because the engine has to consider and decline it. A large `no directional bias` count therefore means the pipeline rarely formed a view, not that a good trade was vetoed.

| Symbol | Top rejection reasons |
|---|---|
| GBPUSD | capital protection: Directional Bias (187); regime BREAKOUT disabled by learned policy (expectancy -0.153R worse than -0.050R over 35 trades) (102); s |
| AUDUSD | calibrated profile has no validated edge (OOS expectancy +0.000R over 52 trades) - refusing symbol (1083); capital protection: Directional Bias (412); |
| BTCUSD | calibrated profile has no validated edge (OOS expectancy +0.000R over 13 trades) - refusing symbol (1588); capital protection: Directional Bias (569) |
| ETHUSD | calibrated profile has no validated edge (OOS expectancy +0.000R over 6 trades) - refusing symbol (1476); capital protection: Directional Bias (681) |
| EURUSD | calibrated profile has no validated edge (OOS expectancy +0.000R over 2 trades) - refusing symbol (1096); capital protection: Directional Bias (401);  |
| GER40 | calibrated profile has no validated edge (OOS expectancy +0.000R over 0 trades) - refusing symbol (1144); capital protection: Directional Bias (283);  |
| NAS100 | calibrated profile has no validated edge (OOS expectancy +0.000R over 62 trades) - refusing symbol (1026); capital protection: Directional Bias (399); |
| NZDUSD | calibrated profile has no validated edge (OOS expectancy +0.000R over 41 trades) - refusing symbol (1118); capital protection: Directional Bias (373); |
| SOLUSD | calibrated profile has no validated edge (OOS expectancy +0.000R over 72 trades) - refusing symbol (1562); capital protection: Directional Bias (589) |
| UK100 | calibrated profile has no validated edge (OOS expectancy +0.000R over 0 trades) - refusing symbol (949); capital protection: Directional Bias (453); c |
| US30 | calibrated profile has no validated edge (OOS expectancy +0.000R over 31 trades) - refusing symbol (998); capital protection: Directional Bias (427);  |
| USDCAD | calibrated profile has no validated edge (OOS expectancy +0.000R over 42 trades) - refusing symbol (1131); capital protection: Directional Bias (360); |
| USDCHF | calibrated profile has no validated edge (OOS expectancy +0.000R over 51 trades) - refusing symbol (1116); capital protection: Directional Bias (381); |
| USDJPY | calibrated profile has no validated edge (OOS expectancy +0.000R over 58 trades) - refusing symbol (1167); capital protection: Directional Bias (330); |
| XAGUSD | calibrated profile has no validated edge (OOS expectancy +0.000R over 56 trades) - refusing symbol (1131); capital protection: Directional Bias (290); |
| XAUUSD | calibrated profile has no validated edge (OOS expectancy +0.000R over 37 trades) - refusing symbol (1132); capital protection: Directional Bias (295); |

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

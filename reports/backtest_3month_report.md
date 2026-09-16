# HM Algo 2.0 — 3-Month Backtest on Real MT5 Data

**Mode:** calibrated per-symbol win-rate profiles  
**Data:** MT5 terminal, real H1 bars (validated non-synthetic)  
**Initial balance:** $10,000.00 at 0.5% risk per trade  
**Generated:** 2026-09-12T17:59:48.640222+00:00

## 1. Headline

| Metric | Value |
|---|---|
| Symbols traded | 16 |
| Total trades | 215 |
| Win rate | 54.88% |
| Expectancy | +0.1487 R per trade |
| Average win / loss | +1.072 R / -0.974 R |
| Payoff ratio | 1.100 |
| Total R | +31.96 R |
| Profit factor | 1.36 |
| Net profit | $+1,322.66 |
| Max drawdown | 3.41% |
| Sharpe / Sortino / Calmar | 1.98 / 5.73 / 3.76 |
| Expectancy (sample-uniqueness weighted) | +0.3631 R |

**Win-rate target outcome:** 1/16 symbols met the target out-of-sample. 0 met it in-sample but failed out-of-sample (i.e. the target was reachable only by overfitting).

## 2. Per-symbol results

| Symbol | Trades | WR % | Exp (R) | PF | Net $ | MaxDD % | OOS WR % | OOS Exp (R) | Target | Binding constraint |
|---|---:|---:|---:|---:|---:|---:|---:|---:|:--:|---|
| GER40 | 54 | 46.3 | +0.155 | 1.16 | +178.87 | 1.83 | 57.4 | +0.217 | ✗ | win rate - best achievable in-sample with positive expectancy is 70.1% |
| SOLUSD | 52 | 53.9 | -0.018 | 1.13 | +94.99 | 2.59 | 48.5 | +0.022 | ✗ | win rate - best achievable in-sample with positive expectancy is 65.0% |
| UK100 | 39 | 82.0 | +0.311 | 2.70 | +622.14 | 0.71 | 80.7 | +0.293 | OOS ✓ | none - target met in-sample AND out-of-sample (OOS 80.7% on 31 trades) |
| USDJPY | 39 | 41.0 | +0.070 | 0.97 | -18.77 | 2.24 | 46.6 | +0.038 | ✗ | win rate - best achievable in-sample with positive expectancy is 67.3% |
| USDCAD | 31 | 54.8 | +0.313 | 1.63 | +445.43 | 1.75 | 56.1 | +0.128 | ✗ | win rate - best achievable in-sample with positive expectancy is 73.5% |
| AUDUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 63.3 | -0.075 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| BTCUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 63.0 | -0.016 | ✗ | win rate - best achievable in-sample with positive expectancy is 67.5% |
| ETHUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 51.7 | -0.077 | ✗ | win rate - best achievable in-sample with positive expectancy is 68.6% |
| EURUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 46.9 | -0.250 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| GBPUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 50.9 | -0.034 | ✗ | win rate - best achievable in-sample with positive expectancy is 67.3% |
| NAS100 | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 46.3 | -0.153 | ✗ | win rate - best achievable in-sample with positive expectancy is 68.1% |
| NZDUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 45.0 | -0.119 | ✗ | win rate - best achievable in-sample with positive expectancy is 51.0% |
| US30 | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 58.7 | -0.091 | ✗ | win rate - best achievable in-sample with positive expectancy is 69.4% |
| USDCHF | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 51.8 | -0.184 | ✗ | expectancy - the walk-forward-selected configuration has no edge out o |
| XAGUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 52.5 | -0.079 | ✗ | win rate - best achievable in-sample with positive expectancy is 63.0% |
| XAUUSD | 0 | 0.0 | +0.000 | 0.00 | +0.00 | 0.00 | 40.0 | -0.056 | ✗ | win rate - best achievable in-sample with positive expectancy is 67.6% |

### 2a. What the learned regime policy contributes

Calibration reports the same out-of-sample sample twice: once with every regime enabled, and once with the regimes the learned policy has switched off removed. Both are purged out-of-sample w.r.t. the geometry and threshold; the policy itself is fitted on **training folds only**, so it has never seen the out-of-sample bars it filters. The gap between the two columns is the policy's entire contribution — read it as the honest size of the effect, not as free money.

| Symbol | OOS n (all regimes) | OOS WR | OOS Exp (R) | OOS n (policy applied) | OOS WR | OOS Exp (R) | Policy fitted on |
|---|---:|---:|---:|---:|---:|---:|---|
| GER40 | 54 | 57.4 | +0.217 | 54 | 57.4 | +0.217 | train folds |
| SOLUSD | 69 | 47.8 | +0.007 | 68 | 48.5 | +0.022 | train folds |
| UK100 | 54 | 64.8 | +0.029 | 31 | 80.7 | +0.293 | train folds |
| USDJPY | 58 | 46.6 | +0.038 | 58 | 46.6 | +0.038 | train folds |
| USDCAD | 41 | 56.1 | +0.128 | 41 | 56.1 | +0.128 | train folds |
| AUDUSD | 91 | 63.7 | -0.066 | 90 | 63.3 | -0.075 | train folds |
| BTCUSD | 126 | 59.5 | -0.055 | 100 | 63.0 | -0.016 | train folds |
| ETHUSD | 60 | 51.7 | -0.077 | 60 | 51.7 | -0.077 | train folds |
| EURUSD | 83 | 48.2 | -0.245 | 49 | 46.9 | -0.250 | train folds |
| GBPUSD | 55 | 50.9 | -0.034 | 55 | 50.9 | -0.034 | train folds |
| NAS100 | 54 | 46.3 | -0.153 | 54 | 46.3 | -0.153 | train folds |
| NZDUSD | 42 | 42.9 | -0.163 | 40 | 45.0 | -0.119 | train folds |
| US30 | 46 | 58.7 | -0.091 | 46 | 58.7 | -0.091 | train folds |
| USDCHF | 62 | 45.2 | -0.302 | 54 | 51.8 | -0.184 | train folds |
| XAGUSD | 62 | 53.2 | -0.072 | 61 | 52.5 | -0.079 | train folds |
| XAUUSD | 45 | 40.0 | -0.056 | 45 | 40.0 | -0.056 | train folds |

## 3. Win-rate / expectancy frontier

For each target size (`tp_r`, as a multiple of initial risk) the table gives the highest win rate found at all, and the highest win rate that still carries positive expectancy. Where those two diverge, the gap is win rate that can only be bought by running a losing system. This is the direct evidence for whether the 75% target is reachable.

**AUDUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.6 | 59.6% | -0.100 | 109 | — | — | — |
| 0.75 | 59.5% | -0.119 | 79 | — | — | — |
| 1 | 61.0% | -0.098 | 77 | — | — | — |
| 1.5 | 65.3% | -0.036 | 72 | — | — | — |

**BTCUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.6 | 67.5% | +0.068 | 40 | 67.5% | +0.068 | 40 |
| 0.75 | 65.8% | +0.034 | 38 | 65.8% | +0.034 | 38 |
| 1 | 67.1% | +0.051 | 82 | 67.1% | +0.051 | 82 |
| 1.5 | 66.7% | +0.009 | 30 | 66.7% | +0.009 | 30 |

**ETHUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.6 | 63.6% | +0.018 | 77 | 63.6% | +0.018 | 77 |
| 0.75 | 64.1% | +0.070 | 53 | 64.1% | +0.070 | 53 |
| 1 | 68.6% | +0.050 | 70 | 68.6% | +0.050 | 70 |
| 1.5 | 67.7% | -0.010 | 68 | 64.9% | +0.042 | 74 |

**EURUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.6 | 63.8% | -0.028 | 94 | — | — | — |
| 0.75 | 59.1% | -0.091 | 93 | — | — | — |
| 1 | 62.7% | -0.057 | 51 | — | — | — |
| 1.5 | 62.2% | -0.061 | 45 | — | — | — |

**GBPUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.6 | 64.6% | +0.032 | 65 | 64.6% | +0.032 | 65 |
| 0.75 | 63.5% | +0.013 | 63 | 63.5% | +0.013 | 63 |
| 1 | 64.5% | +0.026 | 62 | 64.5% | +0.026 | 62 |
| 1.5 | 67.3% | +0.025 | 55 | 67.3% | +0.025 | 55 |

**GER40**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.6 | 69.5% | +0.084 | 105 | 69.5% | +0.084 | 105 |
| 0.75 | 70.0% | +0.129 | 70 | 70.0% | +0.129 | 70 |
| 1 | 69.7% | +0.159 | 66 | 69.7% | +0.159 | 66 |
| 1.5 | 70.1% | +0.123 | 87 | 70.1% | +0.123 | 87 |

**NAS100**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.6 | 65.1% | +0.004 | 126 | 65.1% | +0.004 | 126 |
| 0.75 | 67.2% | +0.051 | 61 | 67.2% | +0.051 | 61 |
| 1 | 66.7% | +0.040 | 54 | 66.7% | +0.040 | 54 |
| 1.5 | 68.1% | +0.032 | 47 | 68.1% | +0.032 | 47 |

**NZDUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.6 | 60.7% | -0.080 | 56 | — | — | — |
| 0.75 | 62.9% | -0.035 | 62 | — | — | — |
| 1 | 61.7% | -0.029 | 60 | 51.0% | +0.023 | 49 |
| 1.5 | 59.3% | -0.043 | 54 | 50.0% | +0.109 | 44 |

**SOLUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.6 | 58.8% | -0.048 | 85 | — | — | — |
| 0.75 | 59.5% | -0.011 | 79 | — | — | — |
| 1 | 61.4% | +0.011 | 88 | 61.4% | +0.011 | 88 |
| 1.5 | 65.0% | +0.072 | 60 | 65.0% | +0.072 | 60 |

**UK100**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.6 | 81.2% | +0.298 | 32 | 81.2% | +0.298 | 32 |
| 0.75 | 76.7% | +0.215 | 30 | 76.7% | +0.215 | 30 |
| 1 | 82.1% | +0.311 | 28 | 82.1% | +0.311 | 28 |
| 1.5 | 84.0% | +0.285 | 25 | 84.0% | +0.285 | 25 |

**US30**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.6 | 64.6% | +0.025 | 48 | 64.6% | +0.025 | 48 |
| 0.75 | 63.8% | +0.027 | 47 | 63.8% | +0.027 | 47 |
| 1 | 64.5% | +0.008 | 110 | 64.5% | +0.008 | 110 |
| 1.5 | 69.4% | +0.032 | 36 | 69.4% | +0.032 | 36 |

**USDCAD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.6 | 73.5% | +0.082 | 49 | 73.5% | +0.082 | 49 |
| 0.75 | 72.0% | +0.078 | 50 | 72.0% | +0.078 | 50 |
| 1 | 69.6% | +0.026 | 46 | 69.6% | +0.026 | 46 |
| 1.5 | 69.0% | +0.042 | 42 | 69.0% | +0.042 | 42 |

**USDCHF**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.6 | 61.1% | -0.076 | 72 | — | — | — |
| 0.75 | 62.1% | -0.063 | 66 | — | — | — |
| 1 | 62.5% | -0.050 | 64 | — | — | — |
| 1.5 | 65.0% | -0.042 | 60 | — | — | — |

**USDJPY**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.6 | 65.9% | +0.011 | 129 | 65.9% | +0.011 | 129 |
| 0.75 | 66.1% | +0.030 | 121 | 66.1% | +0.030 | 121 |
| 1 | 67.3% | +0.057 | 110 | 67.3% | +0.057 | 110 |
| 1.5 | 64.7% | -0.000 | 102 | 55.2% | +0.113 | 96 |

**XAGUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.6 | 63.4% | -0.035 | 112 | 61.5% | +0.000 | 78 |
| 0.75 | 62.8% | -0.029 | 113 | 58.9% | +0.044 | 73 |
| 1 | 63.3% | -0.001 | 79 | 63.0% | +0.004 | 73 |
| 1.5 | 64.1% | -0.019 | 64 | 50.0% | +0.111 | 52 |

**XAUUSD**

| tp_r | best WR | exp at best WR | trades | best WR with exp > 0 | exp | trades |
|---:|---:|---:|---:|---:|---:|---:|
| 0.6 | 66.7% | +0.060 | 45 | 66.7% | +0.060 | 45 |
| 0.75 | 66.7% | +0.095 | 45 | 66.7% | +0.095 | 45 |
| 1 | 64.3% | +0.009 | 42 | 64.3% | +0.009 | 42 |
| 1.5 | 67.6% | +0.068 | 37 | 67.6% | +0.068 | 37 |

## 4. Calibrated geometry per symbol

`tp_r` is the target distance in multiples of initial risk; `be` is the breakeven trigger (off = never lock breakeven); `pc` the partial bank; `mb` the time stop in bars; `thr` the entry score threshold.

The last two columns come from the isotonic score calibration: the realised win probability the fitted map assigns to a score sitting exactly at the deployed threshold, and the Brier score of that map (lower is better; 0.25 is the skill-free baseline for a balanced sample). The calibration is monotone, so it cannot change which candidates pass — it exists to make the score interpretable and to give the AI layer a probability rather than an arbitrary index.

| Symbol | tp_r | be | pc | mb | threshold | P(win) @ thr | Brier | Enabled regimes |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| AUDUSD | 1.5 | off | 0.5 | 48 | 0.425 | 63.7% | 0.231 | TREND_BEAR, TREND_BULL, COMPRESSION, LOW_VOLATILITY |
| BTCUSD | 0.6 | off | 0.5 | 48 | 0.400 | 58.7% | 0.241 | TREND_BULL, BREAKOUT, LIQUIDITY_SWEEP, COMPRESSION, LOW_VOLATILITY |
| ETHUSD | 0.75 | off | 0.5 | 48 | 0.756 | 51.1% | 0.250 | TREND_BULL, TREND_BEAR, BREAKOUT, COMPRESSION |
| EURUSD | 1 | off | 0.5 | 48 | 0.664 | 43.5% | 0.243 | TREND_BEAR, BREAKOUT, LIQUIDITY_SWEEP, COMPRESSION |
| GBPUSD | 0.75 | off | off | 48 | 0.698 | 50.9% | 0.250 | TREND_BEAR, TREND_BULL, BREAKOUT, LIQUIDITY_SWEEP |
| GER40 | 1.5 | off | off | 24 | 0.738 | 57.1% | 0.245 | TREND_BEAR, TREND_BULL, BREAKOUT, LIQUIDITY_SWEEP |
| NAS100 | 1 | off | off | 48 | 0.665 | 35.7% | 0.245 | TREND_BEAR, BREAKOUT, TREND_BULL, LIQUIDITY_SWEEP, COMPRESSION |
| NZDUSD | 1.5 | off | off | 48 | 0.686 | 16.7% | 0.211 | TREND_BEAR, LIQUIDITY_SWEEP, TREND_BULL |
| SOLUSD | 1.5 | 1 | off | 24 | 0.668 | 46.2% | 0.249 | BREAKOUT, TREND_BULL, TREND_BEAR, LIQUIDITY_SWEEP, LOW_VOLATILITY |
| UK100 | 0.6 | off | off | 24 | 0.723 | 42.9% | 0.199 | TREND_BEAR, LIQUIDITY_SWEEP, BREAKOUT, COMPRESSION |
| US30 | 1.5 | off | 0.5 | 48 | 0.757 | 58.3% | 0.232 | TREND_BEAR, BREAKOUT, TREND_BULL, COMPRESSION, LIQUIDITY_SWEEP |
| USDCAD | 1.5 | off | off | 24 | 0.742 | 0.0% | 0.194 | TREND_BULL, TREND_BEAR, BREAKOUT, LIQUIDITY_SWEEP |
| USDCHF | 1.5 | off | 0.5 | 48 | 0.644 | 43.5% | 0.231 | TREND_BEAR, TREND_BULL, LIQUIDITY_SWEEP, COMPRESSION |
| USDJPY | 1.5 | off | off | 24 | 0.748 | 42.9% | 0.242 | TREND_BULL, BREAKOUT, TREND_BEAR, LIQUIDITY_SWEEP, COMPRESSION, LOW_VOLATILITY |
| XAGUSD | 1.5 | off | off | 24 | 0.700 | 54.5% | 0.248 | TREND_BULL, LIQUIDITY_SWEEP, TREND_BEAR, COMPRESSION |
| XAUUSD | 1.5 | off | off | 24 | 0.690 | 38.5% | 0.238 | TREND_BEAR, TREND_BULL, BREAKOUT, LIQUIDITY_SWEEP, COMPRESSION |

## 5. Portfolio risk allocation (HRP)

Inverse-variance weights from the HRP allocator over per-symbol daily R:

| Symbol | Risk weight |
|---|---:|
| UK100 | 38.40% |
| USDCAD | 17.03% |
| SOLUSD | 15.47% |
| USDJPY | 15.42% |
| GER40 | 13.69% |

## 6. Entry rejection analysis

Why candidates were not traded under the calibrated profile. In calibrated mode these are the capital-protection gates plus the calibrated edge filter; the legacy 29-check stack no longer decides.

Counts are per **bar evaluated**, not per candidate: a bar on which the pipeline found no setup at all (`no directional bias`) is counted here too, because the engine has to consider and decline it. A large `no directional bias` count therefore means the pipeline rarely formed a view, not that a good trade was vetoed.

| Symbol | Top rejection reasons |
|---|---|
| GER40 | capital protection: Directional Bias (212); score 0.520 below calibrated threshold 0.738 (42); score 0.480 below calibrated threshold 0.738 (18) |
| SOLUSD | capital protection: Directional Bias (178); score 0.520 below calibrated threshold 0.668 (20); score 0.540 below calibrated threshold 0.668 (9) |
| UK100 | capital protection: Directional Bias (437); regime TREND_BULL disabled by learned policy (expectancy -0.251R worse than -0.050R over 78 trades) (338); |
| USDJPY | capital protection: Directional Bias (168); score 0.520 below calibrated threshold 0.748 (33); capital protection: Market Session Open (19) |
| USDCAD | capital protection: Directional Bias (227); score 0.520 below calibrated threshold 0.742 (25); score 0.480 below calibrated threshold 0.742 (24) |
| AUDUSD | calibrated profile has no validated edge (OOS expectancy -0.075R over 90 trades) - refusing symbol (1083); capital protection: Directional Bias (412); |
| BTCUSD | calibrated profile has no validated edge (OOS expectancy -0.016R over 100 trades) - refusing symbol (1588); capital protection: Directional Bias (569) |
| ETHUSD | calibrated profile has no validated edge (OOS expectancy -0.077R over 60 trades) - refusing symbol (1476); capital protection: Directional Bias (681) |
| EURUSD | calibrated profile has no validated edge (OOS expectancy -0.250R over 49 trades) - refusing symbol (1096); capital protection: Directional Bias (401); |
| GBPUSD | calibrated profile has no validated edge (OOS expectancy -0.034R over 55 trades) - refusing symbol (1115); capital protection: Directional Bias (382); |
| NAS100 | calibrated profile has no validated edge (OOS expectancy -0.153R over 54 trades) - refusing symbol (1026); capital protection: Directional Bias (399); |
| NZDUSD | calibrated profile has no validated edge (OOS expectancy -0.119R over 40 trades) - refusing symbol (1118); capital protection: Directional Bias (373); |
| US30 | calibrated profile has no validated edge (OOS expectancy -0.091R over 46 trades) - refusing symbol (998); capital protection: Directional Bias (427);  |
| USDCHF | calibrated profile has no validated edge (OOS expectancy -0.184R over 54 trades) - refusing symbol (1116); capital protection: Directional Bias (381); |
| XAGUSD | calibrated profile has no validated edge (OOS expectancy -0.079R over 61 trades) - refusing symbol (1131); capital protection: Directional Bias (290); |
| XAUUSD | calibrated profile has no validated edge (OOS expectancy -0.056R over 45 trades) - refusing symbol (1132); capital protection: Directional Bias (295); |

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

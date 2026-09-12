# 16-Symbol Portfolio Re-run on Corrected Symbol Specs

**Why this report exists.** The previous portfolio report (`backtest_3month_report.md`, generated
2026-09-11) was produced with a broken symbol registry: `GER40`, `UK100` and `XAGUSD` were not
registered at all, `NAS100`/`US30`/`US500` carried `digits=1` instead of `2`, and several spread
caps were far tighter than the real quoted spreads. An unregistered symbol silently receives a
generic-FX fallback (`contract_size=100_000`, `pip_size=0.0001`, `max_spread_pips=5.0`), which
rejects 100% of bars. Eight of sixteen symbols therefore produced zero trades for an entire quarter,
and the candidates that fed the calibration had been gated by those wrong specs.

Everything below was regenerated end-to-end on the corrected registry.

**Window:** 95 days (3 months) of real MT5 H1 bars, 16 symbols
**Account:** $10,000, 0.5% risk per trade
**Generated:** 2026-09-12

---

## 1. Pipeline re-run

| Stage | Result |
|---|---|
| Signal scan | **16/16 symbols, 18,998 candidates** (previously 8 symbols scanned to nothing) |
| Calibration | 16 profiles, walk-forward, purged K-fold with embargo |
| Portfolio backtest | 16 symbols, production `BacktestEngine` |

The scan now produces candidates for every symbol — including the three that previously did not
exist in the registry:

| Symbol | Candidates | Bars scanned |
|---|---:|---:|
| AUDUSD | 1,096 | 1,521 |
| BTCUSD | 1,578 | 2,147 |
| ETHUSD | 1,466 | 2,147 |
| EURUSD | 1,113 | 1,523 |
| GBPUSD | 1,125 | 1,523 |
| GER40 | 1,161 | 1,441 |
| NAS100 | 1,049 | 1,445 |
| NZDUSD | 1,135 | 1,517 |
| SOLUSD | 1,552 | 2,141 |
| UK100 | 961 | 1,416 |
| US30 | 1,006 | 1,445 |
| USDCAD | 1,147 | 1,517 |
| USDCHF | 1,134 | 1,523 |
| USDJPY | 1,186 | 1,523 |
| XAGUSD | 1,147 | 1,441 |
| XAUUSD | 1,142 | 1,447 |

## 2. Calibration outcome

| Metric | Value |
|---|---:|
| Symbols meeting the 75% target out-of-sample | **1 / 16** (UK100) |
| Met in-sample but failed out-of-sample | 2 / 16 (ETHUSD, USDCAD) |
| Aggregate out-of-sample trades | 1,284 |
| Aggregate out-of-sample win rate | 67.06% |
| Aggregate out-of-sample total | **−54.74 R** |
| Same, before the learned regime policy | 1,455 trades, 64.95% WR, **−111.17 R** |

The regime policy improves the out-of-sample total from −111.17 R to −54.74 R. It is fitted on
training folds only, so it has never seen the out-of-sample bars it filters. It is doing real work —
but it is not large enough to turn the aggregate positive.

## 3. Portfolio result

| Metric | Value |
|---|---:|
| Symbols traded | 4 of 16 |
| Total trades | 218 |
| Win rate | 64.68% |
| Expectancy | +0.0621 R |
| Average win / loss | +0.633 R / −0.983 R |
| Payoff ratio | 0.644 |
| Profit factor | 1.20 |
| Net profit | **+$591.83** |
| Max drawdown | 3.29% |
| Sharpe / Sortino / Calmar | 1.12 / 2.69 / 1.78 |
| Expectancy, sample-uniqueness weighted | **−0.0189 R** |

### Per-symbol (the four that traded)

| Symbol | Trades | WR % | Exp (R) | PF | Net $ | MaxDD % | OOS WR % | OOS Exp (R) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| UK100 | 39 | 82.0 | +0.311 | 2.70 | **+622.14** | 0.71 | 80.7 | +0.305 |
| SOLUSD | 49 | 53.1 | +0.029 | 1.06 | +42.39 | 2.61 | 48.5 | +0.023 |
| USDJPY | 68 | 70.6 | −0.009 | 0.95 | −50.09 | 3.28 | 54.4 | +0.013 |
| GER40 | 62 | 56.5 | +0.010 | 0.98 | −22.61 | 2.77 | 61.0 | +0.162 |

The other twelve symbols are **refused** by the entry policy, which declines any symbol whose
out-of-sample expectancy is non-positive over a sufficient sample. That refusal is the gate working
as designed, not a defect — see §4.

### The headline is carried by one symbol

UK100 contributes **+$622.14** of the **+$591.83** portfolio net. Excluding it, the portfolio is
**−$30.31**. UK100's out-of-sample sample is only **31 trades**, so the symbol that produces the
entire profit is the one with the weakest statistical support. Treat +$591.83 as a single-symbol
result, not a portfolio result.

The sample-uniqueness-weighted expectancy (**−0.0189 R**) points the same way: once overlapping
trades are down-weighted, the aggregate edge disappears. The headline figure depends on trades that
were open at the same time.

## 4. The core finding: the 75% win-rate objective is arithmetically self-defeating

For a trade with a stop of 1R and a target of `tp_r`, the break-even win rate is

```
WR_breakeven = 1 / (1 + tp_r)
```

| tp_r | WR needed to break even |
|---:|---:|
| 0.25 | **80.0%** |
| 0.30 | **76.9%** |
| 0.333 | 75.0% |
| 0.40 | 71.4% |
| 0.50 | 66.7% |
| 0.60 | 62.5% |
| 0.75 | 57.1% |
| 1.00 | 50.0% |
| 1.50 | 40.0% |

Rearranged: **to break even at a 75% win rate, `tp_r` must be at least 1/0.75 − 1 = 0.3333.**
Any geometry with `tp_r < 0.3333` requires *more than* 75% just to break even — before costs.

### What the calibrator actually chose

| Symbol | tp_r | In-sample WR | OOS WR | OOS Exp (R) | `tp_r ≥ 0.3333`? | OOS profitable? |
|---|---:|---:|---:|---:|:--:|:--:|
| AUDUSD | 0.25 | 76.2% | 75.6% | −0.0552 | ✗ | ✗ |
| BTCUSD | 0.25 | 78.2% | 71.8% | −0.0472 | ✗ | ✗ |
| ETHUSD | 0.25 | 78.6% | 64.6% | −0.1063 | ✗ | ✗ |
| EURUSD | 0.25 | 77.1% | 74.8% | −0.0514 | ✗ | ✗ |
| USDCHF | 0.25 | 72.8% | 56.7% | −0.2415 | ✗ | ✗ |
| NAS100 | 0.30 | 76.4% | 73.8% | −0.0332 | ✗ | ✗ |
| XAUUSD | 0.30 | 76.4% | 74.8% | −0.0374 | ✗ | ✗ |
| USDCAD | 0.40 | 75.4% | 71.9% | −0.0031 | ✓ | ✗ |
| USDJPY | 0.40 | 70.7% | 54.4% | **+0.0129** | ✓ | **✓** |
| UK100 | 0.60 | 81.2% | 80.7% | **+0.3054** | ✓ | **✓** |
| GBPUSD | 0.75 | 62.1% | 52.4% | −0.1249 | ✓ | ✗ |
| GER40 | 1.50 | 61.5% | 61.0% | **+0.1619** | ✓ | **✓** |
| NZDUSD | 1.50 | 44.4% | 70.0% | −0.0460 | ✓ | ✗ |
| SOLUSD | 1.50 | 49.3% | 48.5% | **+0.0231** | ✓ | **✓** |
| US30 | 1.50 | 69.4% | 58.7% | −0.0904 | ✓ | ✗ |
| XAGUSD | 1.50 | 50.0% | 66.7% | −0.0447 | ✓ | ✗ |

**Seven of sixteen symbols were calibrated to a geometry that cannot break even at the target win
rate. All seven lose money out of sample (7/7).** Their mean in-sample win rate is **76.5%** — they
hit the target in-sample — while their mean in-sample expectancy is already **negative (−0.0279 R)**.

**Eight symbols reached an in-sample win rate at or above 75%. Only one of the eight has positive
out-of-sample expectancy.** Hitting the win-rate target in-sample is *anti-predictive* of
out-of-sample profitability.

**The four symbols that are profitable out of sample — GER40, SOLUSD, UK100, USDJPY — are exactly
the four that the portfolio report trades.** The gate and the arithmetic agree.

### Why this happens

The calibrator is asked to reach a 75% win rate while keeping expectancy positive. The cheapest way
to raise a win rate is to move the target closer, so the search walks `tp_r` downward. But
`WR_breakeven = 1/(1 + tp_r)` rises as `tp_r` falls. Below `tp_r = 0.3333` the target is
**arithmetically unreachable at a profit**: the geometry is chosen precisely to hit 75%, and that
same choice is what makes 75% insufficient. The objective defeats itself.

This is the same pathology proven trade-by-trade on BTCUSD in
`btcusd_183d_failure_analysis_legacy.md`, where `tp_r=0.25` delivered a **78.5% win rate and still
lost money** (−0.0406 R/trade). The portfolio re-run shows it is not a BTCUSD quirk — it is the
systematic behaviour of the calibration objective.

## 5. Recommendations

**P0 — Constrain the geometry so the target is reachable.**
Reject any candidate geometry with `tp_r < 1/target_wr − 1` (= 0.3333 for a 75% target). This is a
one-line constraint in the calibration search and it removes all seven structurally-unprofitable
configurations outright. For a real cost margin, require `tp_r ≥ 0.5` (break-even 66.7%).

**P0 — Optimise expectancy, report win rate.**
Win rate is a consequence of geometry, not an independent objective. Rank candidate configurations
by out-of-sample expectancy and report the win rate they produce. If a 75% win rate is a hard
product requirement, it must be imposed as a *constraint* on a geometry that can support it — never
as the maximand.

**P1 — Require the target to be met out-of-sample, not in-sample.**
`target_met` currently reports an in-sample rate. Given that 7 of 8 in-sample target-hitters lose
out of sample, the in-sample flag is actively misleading. Report `oos_target_met` only.

**P1 — Do not ship a portfolio whose profit comes from one symbol.**
UK100 is 105% of the net result on 31 out-of-sample trades. Add a concentration guard: a portfolio
result is not reportable as positive unless at least N symbols each contribute positively, or unless
the result survives dropping the largest contributor.

**P2 — Re-examine the aggregate.**
1,284 out-of-sample trades at −54.74 R is a large, well-powered negative result. The regime policy
recovers 56 R of it. That is the strongest evidence in the project that the *entries* — not the
parameters — are where the remaining edge has to come from.

## 6. Limitations

* 95 days is a short window; per-symbol out-of-sample samples range from 31 (UK100) to 151 (XAUUSD).
* Swap/financing is not modelled, so held positions carry an unmodelled cost.
* The portfolio figure is a fixed-fractional sum of independent per-symbol runs, not a single
  capital-constrained simulation; position sizing is not competed for across symbols.
* The `tp_r` threshold of 0.3333 ignores costs. With spread and slippage the true threshold is
  higher, so 0.3333 is a floor, not a safe value.

---

*Supersedes `backtest_3month_report.md` generated 2026-09-11. The prior report's numbers were
produced with the defective symbol registry and are retained only as
`backtest_3month_report.PREFIX_STALE.md`.*

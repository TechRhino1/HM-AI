# BTCUSD# — 6-Month Backtest and Trade-Failure Analysis

**Instrument:** BTCUSD# (canonical `BTCUSD`), CRYPTO, broker `XMGlobal-MT5 5`
**Window:** 2026-03-13 14:00:00+00:00 → 2026-09-12 13:00:00+00:00  (183 days, 4392 H1 bars)
**Data:** MT5_TERMINAL_REAL — real MT5 history, structural validation passed
**Configuration:** uncalibrated baseline (legacy 29-gate stack)  
**Account:** $10,000, 0.5% risk per trade, median spread 2250.0 pips, slippage 0.5 pips

## 1. Headline result

| Metric | Value |
|---|---:|
| Trades | 80 |
| Wins / Losses | 37 / 43 |
| Win rate | 46.25% |
| Expectancy | -0.0668 R per trade |
| Total R | -5.34 R |
| Average win / loss | +0.986 R / -0.972 R |
| Payoff ratio | 1.014 |
| Profit factor | 0.820 |
| Net profit | $-216.67 |
| Max drawdown | 3.40% |
| Average bars held | 35.2 |

**Verdict.** Losing system: -0.0668 R per trade over 80 trades. The losses are analysed below.

### The calibrated edge test on this window

| Stage | Trades | Win rate | Expectancy (R) |
|---|---:|---:|---:|
| In-sample (training folds) | 255 | 76.5% | +0.0203 |
| Purged out-of-sample | 295 | 77.0% | -0.0080 |

**The calibrated system refuses to trade this symbol.** Its out-of-sample expectancy is non-positive (-0.0080 R over 295 trades) despite a positive in-sample figure (+0.0203 R) — i.e. the edge did not survive the walk-forward split. The entry policy declines the symbol rather than trading it at a smaller size, so the engine takes **zero** trades when the calibrated profile is active.

> out-of-sample - met in-sample (76.5%) but not on purged folds; OOS expectancy is non-positive (-0.008R)

This is the single most important result on this page: the strategy *as configured by the legacy gates* does trade, and those trades are analysed below — but the calibrated configuration judges the symbol unprofitable and stands aside. The two are not in conflict; the second is what the evidence says about the first.

## 2. How trades ended

| Exit | Count | Share | Avg R | Total R | Win rate |
|---|---:|---:|---:|---:|---:|
| STOP | 41 | 51.2% | -1.000 | -41.00 | 0.0% |
| TRAIL_OR_BE | 33 | 41.2% | +0.677 | +22.36 | 100.0% |
| TARGET | 4 | 5.0% | +3.529 | +14.12 | 100.0% |
| END_OF_TEST | 1 | 1.2% | -0.087 | -0.09 | 0.0% |
| TIME_STOP | 1 | 1.2% | -0.726 | -0.73 | 0.0% |

## 3. Why the losing trades lost

Of 43 losing trades, every one is attributed to a primary cause by explicit measurable rules (no discretion). A trade can trip several conditions; the table counts *primary* cause and lists all contributing flags separately.

| Primary cause | Trades | Share of losses | Total R lost | Avg R |
|---|---:|---:|---:|---:|
| GAVE_BACK_FAVOURABLE_MOVE | 14 | 32.6% | -14.00 | -1.000 |
| STOPPED_AFTER_MINOR_PROGRESS | 13 | 30.2% | -13.00 | -1.000 |
| IMMEDIATE_ADVERSE_MOVE | 12 | 27.9% | -12.00 | -1.000 |
| STOPPED_WITHOUT_PROGRESS | 2 | 4.7% | -2.00 | -1.000 |
| TIME_STOP_NO_PROGRESS | 1 | 2.3% | -0.73 | -0.726 |
| UNCLASSIFIED | 1 | 2.3% | -0.09 | -0.087 |

- **GAVE_BACK_FAVOURABLE_MOVE** — Gave back a favourable move: the trade ran into profit, then reversed all the way to the stop. Exit management, not entry selection.
- **STOPPED_AFTER_MINOR_PROGRESS** — Stopped out after a small favourable move that fell short of the target.
- **IMMEDIATE_ADVERSE_MOVE** — Entry timing: price moved straight against the position and never recovered. The entry was taken into immediate adverse flow.
- **STOPPED_WITHOUT_PROGRESS** — Stopped out with essentially no favourable excursion — the entry had no immediate follow-through.
- **TIME_STOP_NO_PROGRESS** — Time stop: price never travelled far enough within the bar budget. The target was outside what the holding period could realistically deliver.
- **UNCLASSIFIED** — Did not match a specific failure pattern.

### Contributing conditions across all losses

| Condition | Losses where it fired | Share |
|---|---:|---:|
| STOPPED_WITHOUT_PROGRESS | 14 | 32.6% |
| ENTRY_AT_RANGE_EXTREME | 14 | 32.6% |
| GAVE_BACK_FAVOURABLE_MOVE | 14 | 32.6% |
| STOPPED_AFTER_MINOR_PROGRESS | 13 | 30.2% |
| IMMEDIATE_ADVERSE_MOVE | 12 | 27.9% |
| VOLATILITY_EXPANDING_AT_ENTRY | 10 | 23.3% |
| HIGH_VOLATILITY_REGIME | 7 | 16.3% |
| COUNTER_TREND_ENTRY | 7 | 16.3% |
| STOP_WIDER_THAN_3_ATR | 4 | 9.3% |
| TIME_STOP_NO_PROGRESS | 1 | 2.3% |

## 4. Trade-by-trade failure ledger

Every losing trade with the evidence that produced its label. `MFE` is the best unrealised excursion (how far the trade ever went in favour) and `MAE` the worst, both in R. `stop/ATR` below 1.0 means the stop sat inside one bar's normal range.

| # | Entry time | Side | Entry | Stop | R | MFE (R) | MAE (R) | Bars | Exit | stop/ATR | range pos | counter-trend | Primary cause |
|---:|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|:--:|---|
| 1 | 2026-03-20 11:00 | BUY | 71048.7 | 69729.8 | -1.00 | 0.00 | 1.12 | 6 | STOP | 2.44 | 0.69 | no | IMMEDIATE_ADVERSE_MOVE |
| 2 | 2026-03-23 04:00 | SELL | 67900.0 | 69325.0 | -1.00 | 0.28 | 2.51 | 10 | STOP | 2.88 | 0.43 | no | STOPPED_AFTER_MINOR_PROGRESS |
| 3 | 2026-03-23 16:00 | BUY | 71500.1 | 69175.9 | -1.00 | 0.13 | 1.12 | 28 | STOP | 3.04 | 0.92 | no | IMMEDIATE_ADVERSE_MOVE |
| 4 | 2026-03-25 14:00 | BUY | 71695.5 | 70882.0 | -1.00 | 0.32 | 1.40 | 4 | STOP | 1.59 | 0.90 | no | STOPPED_AFTER_MINOR_PROGRESS |
| 5 | 2026-03-30 04:00 | SELL | 66265.4 | 67476.5 | -1.00 | 0.00 | 1.13 | 4 | STOP | 2.58 | 0.78 | yes | IMMEDIATE_ADVERSE_MOVE |
| 6 | 2026-03-30 12:00 | BUY | 67680.4 | 66570.5 | -1.00 | 0.44 | 1.06 | 10 | STOP | 2.54 | 0.85 | no | STOPPED_AFTER_MINOR_PROGRESS |
| 7 | 2026-04-01 11:00 | BUY | 68696.2 | 67203.3 | -1.00 | 0.31 | 1.12 | 18 | STOP | 2.62 | 0.80 | no | STOPPED_AFTER_MINOR_PROGRESS |
| 8 | 2026-04-06 09:00 | BUY | 69207.0 | 68189.8 | -1.00 | 1.12 | 1.13 | 30 | STOP | 2.82 | 0.79 | no | GAVE_BACK_FAVOURABLE_MOVE |
| 9 | 2026-04-07 19:00 | SELL | 68153.1 | 69348.1 | -1.00 | 0.07 | 1.15 | 5 | STOP | 2.71 | 0.23 | no | IMMEDIATE_ADVERSE_MOVE |
| 10 | 2026-04-12 06:00 | SELL | 71744.2 | 73036.0 | -1.00 | 0.96 | 1.34 | 41 | STOP | 2.92 | 0.12 | no | GAVE_BACK_FAVOURABLE_MOVE |
| 11 | 2026-04-16 18:00 | SELL | 73956.8 | 75254.3 | -1.00 | 0.05 | 1.11 | 5 | STOP | 2.38 | 0.65 | yes | IMMEDIATE_ADVERSE_MOVE |
| 12 | 2026-04-20 01:00 | SELL | 74410.5 | 75724.0 | -1.00 | 0.54 | 1.02 | 17 | STOP | 2.99 | 0.11 | no | GAVE_BACK_FAVOURABLE_MOVE |
| 13 | 2026-04-29 15:00 | BUY | 77604.6 | 76623.4 | -1.00 | 0.00 | 1.21 | 2 | STOP | 2.70 | 0.66 | no | IMMEDIATE_ADVERSE_MOVE |
| 14 | 2026-04-29 22:00 | SELL | 75496.9 | 76795.8 | -0.73 | 0.16 | 0.90 | 30 | TIME_STOP | 2.72 | 0.21 | no | TIME_STOP_NO_PROGRESS |
| 15 | 2026-05-04 06:00 | BUY | 80190.6 | 79060.9 | -1.00 | 0.38 | 1.76 | 8 | STOP | 2.59 | 0.94 | no | STOPPED_AFTER_MINOR_PROGRESS |
| 16 | 2026-05-08 13:00 | SELL | 79866.3 | 80831.2 | -1.00 | 0.34 | 1.09 | 32 | STOP | 2.59 | 0.51 | no | STOPPED_AFTER_MINOR_PROGRESS |
| 17 | 2026-05-10 20:00 | BUY | 81376.7 | 80855.3 | -1.00 | 0.37 | 2.16 | 4 | STOP | 2.30 | 0.90 | no | STOPPED_AFTER_MINOR_PROGRESS |
| 18 | 2026-05-11 03:00 | BUY | 82232.0 | 81038.0 | -1.00 | 0.12 | 1.45 | 4 | STOP | 2.65 | 0.54 | no | IMMEDIATE_ADVERSE_MOVE |
| 19 | 2026-05-13 19:00 | SELL | 78818.3 | 79865.3 | -1.00 | 0.07 | 1.12 | 14 | STOP | 2.43 | 0.14 | no | IMMEDIATE_ADVERSE_MOVE |
| 20 | 2026-05-20 19:00 | BUY | 77472.0 | 76494.2 | -1.00 | 0.73 | 1.42 | 51 | STOP | 2.48 | 0.61 | no | GAVE_BACK_FAVOURABLE_MOVE |
| 21 | 2026-05-24 19:00 | SELL | 76363.3 | 77217.0 | -1.00 | 0.32 | 1.08 | 9 | STOP | 2.46 | 0.61 | yes | STOPPED_AFTER_MINOR_PROGRESS |
| 22 | 2026-06-05 18:00 | SELL | 60859.5 | 63628.4 | -1.00 | 0.63 | 1.22 | 56 | STOP | 2.96 | 0.09 | no | GAVE_BACK_FAVOURABLE_MOVE |
| 23 | 2026-06-08 04:00 | BUY | 63652.6 | 61802.7 | -1.00 | 0.30 | 1.36 | 38 | STOP | 2.69 | 0.66 | no | STOPPED_AFTER_MINOR_PROGRESS |
| 24 | 2026-06-09 18:00 | SELL | 61535.1 | 62756.2 | -1.00 | 0.65 | 1.08 | 25 | STOP | 2.31 | 0.03 | no | GAVE_BACK_FAVOURABLE_MOVE |
| 25 | 2026-06-17 13:00 | SELL | 64909.6 | 65836.2 | -1.00 | 0.39 | 1.00 | 6 | STOP | 2.63 | 0.02 | no | STOPPED_AFTER_MINOR_PROGRESS |
| 26 | 2026-06-25 09:00 | BUY | 61605.1 | 59929.1 | -1.00 | 0.21 | 2.09 | 8 | STOP | 3.45 | 0.63 | yes | STOPPED_WITHOUT_PROGRESS |
| 27 | 2026-06-25 20:00 | SELL | 59298.0 | 61393.4 | -1.00 | 0.71 | 1.01 | 161 | STOP | 2.76 | 0.41 | yes | GAVE_BACK_FAVOURABLE_MOVE |
| 28 | 2026-07-06 17:00 | SELL | 61756.3 | 62827.4 | -1.00 | 0.06 | 1.66 | 2 | STOP | 2.61 | 0.27 | no | IMMEDIATE_ADVERSE_MOVE |
| 29 | 2026-07-10 06:00 | BUY | 63983.9 | 62805.2 | -1.00 | 0.60 | 1.07 | 73 | STOP | 3.06 | 0.96 | no | GAVE_BACK_FAVOURABLE_MOVE |
| 30 | 2026-07-16 12:00 | SELL | 64116.0 | 64816.0 | -1.00 | 0.39 | 1.04 | 6 | STOP | 2.08 | 0.12 | no | STOPPED_AFTER_MINOR_PROGRESS |
| 31 | 2026-07-24 18:00 | SELL | 64045.0 | 64992.6 | -1.00 | 0.27 | 1.53 | 56 | STOP | 2.73 | 0.17 | no | STOPPED_AFTER_MINOR_PROGRESS |
| 32 | 2026-07-27 03:00 | BUY | 65417.4 | 64910.0 | -1.00 | 0.00 | 1.03 | 2 | STOP | 2.54 | 0.66 | no | IMMEDIATE_ADVERSE_MOVE |
| 33 | 2026-07-27 22:00 | BUY | 64890.5 | 64010.3 | -1.00 | 0.21 | 1.26 | 4 | STOP | 2.70 | 0.42 | no | STOPPED_WITHOUT_PROGRESS |
| 34 | 2026-07-28 09:00 | SELL | 63437.7 | 64416.3 | -1.00 | 0.72 | 1.04 | 25 | STOP | 3.05 | 0.22 | no | GAVE_BACK_FAVOURABLE_MOVE |
| 35 | 2026-07-30 14:00 | BUY | 64588.4 | 63675.1 | -1.00 | 0.89 | 1.06 | 23 | STOP | 2.65 | 0.94 | no | GAVE_BACK_FAVOURABLE_MOVE |
| 36 | 2026-07-31 21:00 | SELL | 63234.9 | 64144.2 | -1.00 | 1.07 | 1.11 | 83 | STOP | 2.60 | 0.22 | no | GAVE_BACK_FAVOURABLE_MOVE |
| 37 | 2026-08-19 19:00 | BUY | 68552.9 | 67862.3 | -1.00 | 0.60 | 1.07 | 2 | STOP | 1.33 | 0.78 | no | GAVE_BACK_FAVOURABLE_MOVE |
| 38 | 2026-08-27 18:00 | BUY | 80227.1 | 78862.5 | -1.00 | 0.89 | 1.33 | 24 | STOP | 2.42 | 0.83 | no | GAVE_BACK_FAVOURABLE_MOVE |
| 39 | 2026-08-31 22:00 | BUY | 79032.6 | 77609.9 | -1.00 | 0.15 | 1.08 | 19 | STOP | 2.89 | 0.84 | no | IMMEDIATE_ADVERSE_MOVE |
| 40 | 2026-09-08 06:00 | BUY | 78947.0 | 78155.3 | -1.00 | 0.06 | 1.66 | 11 | STOP | 2.66 | 0.19 | yes | IMMEDIATE_ADVERSE_MOVE |
| 41 | 2026-09-08 18:00 | SELL | 78552.8 | 79602.5 | -1.00 | 0.27 | 1.15 | 18 | STOP | 2.65 | 0.69 | yes | STOPPED_AFTER_MINOR_PROGRESS |
| 42 | 2026-09-10 17:00 | SELL | 77177.4 | 78220.4 | -1.00 | 1.08 | 2.06 | 24 | STOP | 2.36 | 0.27 | no | GAVE_BACK_FAVOURABLE_MOVE |
| 43 | 2026-09-12 13:00 | BUY | 77364.9 | 76892.2 | -0.09 | 0.00 | 0.00 | 0 | END_OF_TEST | 1.58 | 0.33 | no | UNCLASSIFIED |

## 5. Market conditions

### By market regime

| Regime | Trades | Win rate | Avg R | Total R |
|---|---:|---:|---:|---:|
| TREND_BULL | 36 | 55.6% | +0.025 | +0.89 |
| TREND_BEAR | 34 | 41.2% | -0.108 | -3.66 |
| BREAKOUT | 7 | 28.6% | -0.400 | -2.80 |
| LIQUIDITY_SWEEP | 2 | 50.0% | +0.164 | +0.33 |
| COMPRESSION | 1 | 0.0% | -0.087 | -0.09 |

### By volatility at entry (ATR percentile over the trailing 200 bars)

| ATR percentile | Trades | Win rate | Avg R | Total R |
|---|---:|---:|---:|---:|
| Q1 lowest vol | 5 | 80.0% | +1.485 | +7.43 |
| Q2 | 27 | 25.9% | -0.570 | -15.38 |
| Q3 | 19 | 57.9% | -0.055 | -1.05 |
| Q4 highest vol | 29 | 51.7% | +0.126 | +3.66 |

### By direction and trend alignment

| Group | Trades | Win rate | Avg R | Total R |
|---|---:|---:|---:|---:|
| BUY | 43 | 48.8% | -0.067 | -2.87 |
| SELL | 37 | 43.2% | -0.067 | -2.47 |
| with-trend | 68 | 47.1% | -0.072 | -4.89 |
| counter-trend | 12 | 41.7% | -0.038 | -0.46 |

## 6. Parameter counterfactuals (same entries, different parameters)

Each row re-simulates the **identical entries** — same entry price, same entry bar, same original stop — changing only the parameter named. This separates 'the entries were bad' from 'the parameters were wrong'.

### 6a. Target distance (`tp_r`, in multiples of initial risk)

| tp_r | Trades | Win rate | Expectancy (R) | Total R | Avg win | Avg loss | PF |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 79 | 78.5% | -0.0406 | -3.20 | +0.229 | -1.023 | 0.82 |
| 0.3 | 79 | 73.4% | -0.0633 | -5.00 | +0.279 | -1.008 | 0.76 |
| 0.4 | 79 | 63.3% | -0.1257 | -9.93 | +0.379 | -0.995 | 0.66 |
| 0.5 | 79 | 60.8% | -0.0968 | -7.65 | +0.478 | -0.987 | 0.75 |
| 0.75 | 79 | 54.4% | -0.0630 | -4.98 | +0.693 | -0.966 | 0.86 |
| 1 | 79 | 50.6% | -0.0183 | -1.44 | +0.910 | -0.970 | 0.96 |
| 1.5 | 79 | 43.0% | -0.0345 | -2.72 | +1.186 | -0.956 | 0.94 |
| 2 | 79 | 40.5% | -0.0063 | -0.50 | +1.367 | -0.941 | 0.99 |
| 3 | 79 | 36.7% | -0.0567 | -4.48 | +1.444 | -0.927 | 0.90 |

### 6b. Stop distance (multiple of the original stop, target held fixed in R:R)

The target widens with the stop, so this scales the whole trade envelope to a different volatility band while keeping reward:risk unchanged.

| Stop × | Trades | Win rate | Expectancy (R) | Total R |
|---|---:|---:|---:|---:|
| 0.75× | 79 | 31.6% | -0.0730 | -5.77 |
| 1× | 79 | 36.7% | -0.0224 | -1.77 |
| 1.5× | 79 | 44.3% | +0.0487 | +3.85 |
| 2× | 79 | 48.1% | +0.0827 | +6.53 |
| 3× | 79 | 48.1% | +0.1316 | +10.40 |

### 6c. Stop distance with the target held at its ORIGINAL price

Here only the stop moves — the target stays exactly where the strategy put it. If expectancy improves, the stop was genuinely too tight rather than the target too close. This is the cleanest test of 'are we being stopped out by noise?'.

| Stop × | Trades | Win rate | Expectancy (R) | Total R |
|---|---:|---:|---:|---:|
| 0.75× | 79 | 31.6% | -0.0112 | -0.89 |
| 1× | 79 | 36.7% | -0.0224 | -1.77 |
| 1.5× | 79 | 44.3% | +0.0110 | +0.87 |
| 2× | 79 | 48.1% | +0.0122 | +0.97 |
| 3× | 79 | 48.1% | +0.0340 | +2.68 |

## 7. What is actually wrong

**Dominant failure mode: `GAVE_BACK_FAVOURABLE_MOVE`** — 14 of 43 losses (33%), costing -14.00 R. Gave back a favourable move: the trade ran into profit, then reversed all the way to the stop. Exit management, not entry selection.

**41 of 43 losses are stop-outs** and 1 are time stops — so the loss mix is dominated by premature stop-outs.

**The stop is too tight for this instrument.** Holding the target at exactly the price the strategy chose and moving only the stop, expectancy rises from -0.0224 R at 1× to +0.0340 R at 3× (win rate 36.7% → 48.1%). Because only the stop moved, this is noise stop-out, not a target that was set too close.

**The payoff is inverted.** 33 trades (41%) closed on the trail/breakeven stop for an average of +0.677 R, while 41 (51%) took the full -1.000 R loss. Only 4 trades reached the target. Winners are being cut short while losers run to the stop — that structure loses money at any win rate below roughly 60%.

**7 losses were taken against the 24-bar trend** and went at least 0.75R against immediately afterwards.

**14 losses were entered at the extreme of the 24-bar range** (bought in the top 15% / sold in the bottom 15%) — chasing.

**7 losses occurred in the highest-volatility quartile** (ATR ≥ 85th percentile of the trailing 200 bars).

## 8. Method and limitations

* Real MT5 H1 bars for `BTCUSD` (BTCUSD#), 4392 bars, 2026-03-13 14:00:00+00:00 → 2026-09-12 13:00:00+00:00; provenance `MT5_TERMINAL_REAL`.
* Execution is the production `BacktestEngine`: entry at the next bar's open, spread paid on the traded side, stop tested before target within a bar (conservative — a bar spanning both is booked as a loss).
* Costs: median spread 2250.00 pips from the data plus 0.5 pips slippage per side. Swap/financing on crypto positions is NOT modelled, so a real long-held BTC position would carry additional financing cost.
* Failure attribution uses only the engine's own outputs (exit reason, MFE, MAE, bars held) plus market context computed from the same bars. Each rule is a numeric predicate; the ledger shows the numbers.
* MFE/MAE are bar-resolution excursions, so they understate true intra-bar extremes on a volatile instrument like BTC.
* One instrument, one window. 80 trades is a small sample: per-bucket win rates below carry wide confidence intervals.

# JARVIS AI — Complete Project Analysis

**Generated:** 2026-09-12
**Scope:** full codebase review, defect hunt, and a realistic 6-month backtest of
`BTCUSD#` with per-trade failure attribution.

This document answers two questions:

1. **What is the state of the project, and what is actually wrong with it?**
2. **Why do the BTCUSD trades fail?**

The second is a concrete instance of the first. Companion document:
`reports/btcusd_183d_failure_analysis_legacy.md`.

---

## 1. What the system is

`jarvis/` — 127 modules, ~33,000 lines. A multi-asset algorithmic trading system
(FX majors, metals, indices, crypto) with an H1 decision loop.

| Layer | Package | Role |
|---|---|---|
| Data | `jarvis/data` | symbol registry, MT5 acquisition, schemas |
| Context | `jarvis/market`, `jarvis/analysts` | market structure, liquidity, momentum, regime classification |
| Decision | `jarvis/intelligence` | 29-check quality gate, calibration, self-learning |
| Execution | `jarvis/execution` | exit policy, calibrated entry policy |
| Risk | `jarvis/risk` | trade guard, HRP allocation, circuit breakers |
| Backtest | `jarvis/backtesting` | engine, trade simulator, signal scanner |
| Learning | `jarvis/learning` | purged K-fold, online ML, sample weights |
| UI / API | `jarvis/ui`, `jarvis/api` | dashboards, remote access |
| Other | `jarvis/india`, `jarvis/stocks` | separate market modules |

Dependency direction is strictly downward. `docs/ARCHITECTURE.md` is the
authoritative design reference and is kept current.

### The decision pipeline

```
bars ──► context engine ──► regime classifier ──► analyst cluster (parallel)
                                                          │
                                                          ▼
                                          29-check quality gate + scoring
                                                          │
                                                          ▼
                              entry policy (capital protection + calibrated edge)
                                                          │
                                                          ▼
                                        risk guard ──► position sizer ──► order
                                                          │
                                            exit policy (stop / target / trail / time)
```

### The calibrated win-rate pipeline (added, 4 stages)

Win rate is **not** an independent objective:
`expectancy(R) = WR·avg_win_R − (1−WR)·avg_loss_R`. Any win rate is obtainable by
shrinking the target relative to the stop, so the system treats "75%" as a
*constraint* — maximise expectancy subject to `WR ≥ target`, `trades ≥ min`,
`expectancy > 0`.

| Stage | Module | Cost |
|---|---|---|
| 1. Signal scan | `backtesting/signal_scan.py` | ~30 s/symbol |
| 2. Trade simulation | `backtesting/trade_simulator.py` | ~1–2 s/geometry |
| 3. Calibration | `intelligence/winrate_targeting.py` | seconds |
| 4. Entry selection | `execution/entry_policy.py` | runtime |

The split exists because a trade's outcome depends only on the geometry and the
forward path — never on which other trades were taken — so stage 2's results are
reusable across every entry threshold.

---

## 2. Defects found and fixed in this review

Every item below was found by testing, reproduced, fixed, and pinned with a
regression test. Ordered by impact.

### 2.1 Symbol metadata: 8 of 16 symbols were structurally untradeable

**Symptom.** In the 3-month portfolio backtest, 8 of 16 symbols produced **zero
trades for the entire quarter** while the calibrator expected 68–200 trades each.

**Root cause.** `GER40`, `UK100` and `XAGUSD` were **absent from
`jarvis/data/symbol_registry.py`**. `resolve()` silently returned a generic-FX
fallback: `contract_size=100_000`, `pip_size=0.0001`, `max_spread_pips=5.0`,
`asset_class="FOREX"`. For an index whose real spread is ~2 index points, an
FX-sized spread cap rejects **100% of bars**. `NAS100` and `US30` were present
but declared `digits=1` where the broker publishes `digits=2` — the same
ten-fold unit error, same symptom.

**Evidence** (spread vs cap, before the fix):

| Symbol | Median spread | Spec cap | Bars rejected |
|---|---:|---:|---:|
| GER40 | 19.50 | 5.0 | **100%** |
| NAS100 | 19.50 | 7.0 | **100%** |
| UK100 | 16.00 | 5.0 | **100%** |
| US30 | 39.00 | 8.0 | **100%** |
| NZDUSD | 2.80 | 2.5 | **100%** |
| USDCAD | 2.70 | 2.5 | **100%** |

**Fix.** Registered the missing instruments from the broker's own manifest
metadata, corrected `digits`, and derived every spread cap from the actual quoted
spread (`cap ≥ p95`). A cap below an instrument's typical spread is a
prohibition, not a filter.

**Structural fix.** `resolve()` now **logs an error** on fallback instead of
failing silently, `registry_mismatches()` compares the registry against the
broker manifests, and `tests/test_symbol_registry.py` fails the moment they
drift. The 95-day data was re-scanned and re-calibrated afterwards; this alone
moved the portfolio from 698 trades / 71.8% WR / −$671 to a materially different
book.

### 2.2 Latent case-sensitivity bug in the alias map

`resolve()` upper-cases its lookup key, but alias keys were written in broker
case (`"GER40Cash#"`, `"SILVER.i#"`, `"US100Cash#"`). Every mixed-case alias
therefore **could never match** and fell through to the fuzzy/generic fallback —
so a correctly written alias still produced the wrong spec. Fixed by normalising
the map at import. Caught by the new alias test.

### 2.3 Crypto rejected as synthetic data — the 6-month fetch failed

**Symptom.** Fetching 6 months of `BTCUSD#` raised
`SyntheticDataError: 1248/4378 weekend bars — markets are closed Sat/Sun`.

**Root cause.** Bitcoin trades 24/7 and the validator knows this
(`trades_weekends = cat == "CRYPTO"`) — but `_category_of("BTCUSD#")` returned
`"UNKNOWN"` because the category table only held `"BTCUSD"` and the broker suffix
`#` was never stripped. The exemption did not apply, so 1,248 valid Saturday and
Sunday bars were flagged as generator artefacts.

**Fix.** Strip the broker suffix, then consult the **authoritative symbol
registry** (`asset_class_of`) rather than a duplicate table. Note the subtlety:
the exact name must be tried *first*, because broker aliases legitimately end in
`#` — stripping before lookup destroys the key (`GER40Cash#` → `GER40CASH` ✗).

### 2.4 Broker suffix leaked into the filesystem layout

The fetch cached to `data/market/real/BTCUSD#/BTCUSD#_H1_183d.parquet`. Every
downstream tool looks up `<canonical>/`, so the scan reported **"0 bars scanned"**
— a silent no-op rather than an error. Fixed by canonicalising the cache path;
`canonical_name()` is now the single place that decides the on-disk name.

### 2.5 R-multiple used the trailed stop

The engine recorded the stop *as it stood at close*. Using that as the risk
denominator shrinks 1R towards zero and reports absurd values — XAUUSD showed
**+11.717 R** expectancy. Fixed by recording the immutable `initial_sl` and
`risk_dist` on every trade. XAUUSD corrected to +0.322 R.

### 2.6 The regime policy was fitted on the sample it filtered

The learned regime policy (which regimes to switch off) was fitted on the
out-of-sample trades and then applied to those same trades — a filter tuned to
the test set. **Measured cost of the artefact: the aggregate out-of-sample result
moved from −33.9 R to +51.8 R.** Fitted honestly on training folds instead, the
figure is **+17.3 R**. The profile now publishes both figures
(`oos_pre_policy_*` and `oos_*`) plus `policy_fitted_on`.

### 2.7 Calibration reported numbers the engine did not produce

Calibration claimed 192 out-of-sample EURUSD trades; the engine produced 7. The
regime policy was applied by the engine but **not** by the calibration's
selection. Fixed by replaying the identical per-fold selection with the disabled
regimes removed *before* the one-position-at-a-time walk (so a blocked trade frees
its slot rather than cascading).

### 2.8 Non-deterministic backtests

Repeated runs gave different trade counts. Root cause: four components on the
decision path read or wrote persistent state (`RealtimeOptimizer` reads SQLite,
`OnlineMLPredictor` writes JSON, `SelfLearningEngine` reads SQLite, `MetaLabeler`
loads joblib) — so a backtest could be influenced by live trading history. Fixed
with `jarvis/config/runtime.py::offline_mode()`; results now reproduce
bit-for-bit.

### 2.9 The legacy stack vetoed the calibrated configuration

In calibrated mode the engine produced 2 trades instead of ~65. Two causes: the
risk guard vetoed any decision that was not `EXECUTE` (by design the calibrated
path does not set `EXECUTE`), and the engine read `reason` (singular) while the
risk engine returns `reasons` (a list) — **swallowing every cause**. Fixed with
`entry_authorized_override` threaded engine → risk_engine → trade_guard, plus the
plural fix.

### 2.10 Intrabar ordering was optimistic (look-ahead)

The engine advanced the stop to breakeven using the same bar's favourable
excursion and *then* asked whether the stop was hit on that bar. For a bar whose
range spans both the stop and the target, OHLC does not reveal the order. Booking
it as a win inflates the win rate **precisely where a 75% target lives**. The
simulator now books such bars as losses.

### 2.11 Wall-clock read on the decision path

`trade_guard` read `datetime.now()` for the session filter, so the spread
allowance depended on when the backtest was run. Now reads the bar timestamp.

### 2.12 Assorted

* `hrp_allocator` crashed on `np.diag` (read-only view in modern numpy).
* Isotonic calibration indexed out of range after PAV pooling shortened the
  rate array.
* The engine recorded a *success* reason ("calibrated edge filter passed") for
  bars it declined for having no directional bias.
* `pd.Timestamp.utcnow()` deprecation.

---

## 3. BTCUSD# — 6-month realistic backtest

Full report: `reports/btcusd_183d_failure_analysis_legacy.md`.

**Data.** Real MT5 H1 bars, `BTCUSD#` on XMGlobal-MT5 5, **4,392 bars**,
2026-03-13 → 2026-09-12 (183 days), structural validation passed, provenance
`MT5_TERMINAL_REAL`. Median spread $22.50, slippage 0.5 pips per side.

**Execution.** The production `BacktestEngine`: entry at the next bar's open,
spread paid on the traded side, stop tested before target within a bar
(conservative), one position at a time.

### Headline

| Metric | Value |
|---|---:|
| Trades | 80 |
| Win rate | **46.25%** |
| Expectancy | **−0.0668 R** per trade |
| Profit factor | 0.82 |
| Net profit | **−$216.67** |
| Max drawdown | 3.40% |
| Avg bars held | 35.2 |

### The calibrated system refuses to trade BTCUSD

On this window the calibrator's purged out-of-sample expectancy is
**−0.0080 R over 295 trades**, despite a positive in-sample figure (+0.0203 R
over 255) — the edge did not survive the walk-forward split. The entry policy
therefore **declines the symbol entirely** and the engine takes zero trades. That
is the correct behaviour and is itself the headline finding: *the evidence says
BTCUSD has no tradable edge here.*

The 80 trades analysed below are what the **legacy 29-gate stack** produces — i.e.
the trades the calibrated layer exists to suppress.

### How the trades ended

| Exit | Count | Share | Avg R | Total R |
|---|---:|---:|---:|---:|
| STOP | 41 | 51.2% | −1.000 | −41.00 |
| TRAIL / BREAKEVEN | 33 | 41.2% | +0.677 | +22.36 |
| TARGET | 4 | 5.0% | +3.529 | +14.12 |
| TIME STOP | 1 | 1.2% | −0.726 | −0.73 |
| END OF TEST | 1 | 1.2% | −0.087 | −0.09 |

### Why the losses happened

43 losing trades, each attributed by explicit measurable rules:

| Primary cause | Trades | Share | Total R |
|---|---:|---:|---:|
| `GAVE_BACK_FAVOURABLE_MOVE` | 14 | 32.6% | −14.00 |
| `STOPPED_AFTER_MINOR_PROGRESS` | 13 | 30.2% | −13.00 |
| `IMMEDIATE_ADVERSE_MOVE` | 12 | 27.9% | −12.00 |
| `STOPPED_WITHOUT_PROGRESS` | 2 | 4.7% | −2.00 |
| `TIME_STOP_NO_PROGRESS` | 1 | 2.3% | −0.73 |
| `UNCLASSIFIED` | 1 | 2.3% | −0.09 |

Contributing conditions across all 43 losses: entered at the extreme of the
24-bar range **14 (33%)**; volatility expanding at entry **10 (23%)**; highest
volatility quartile **7 (16%)**; taken against the 24-bar trend **7 (16%)**.

### The three root causes, in order of impact

**1. The payoff structure is inverted.** 33 trades (41%) closed on the
trail/breakeven stop for an average of **+0.677 R**, while 41 (51%) took the full
**−1.000 R** loss. Only 4 trades reached the target. Winners are cut short; losers
run to the stop. That structure loses money at any win rate below ~60%.

**2. The stop is too tight for Bitcoin's volatility.** Holding the target at
exactly the price the strategy chose and moving *only* the stop:

| Stop × | Win rate | Expectancy (R) | Total R |
|---|---:|---:|---:|
| 1× (as traded) | 36.7% | −0.0224 | −1.77 |
| 1.5× | 44.3% | +0.0110 | +0.87 |
| 2× | 48.1% | +0.0122 | +0.97 |
| 3× | 48.1% | **+0.0340** | +2.68 |

Because only the stop moved, this is **noise stop-out**, not a target set too
close. BTC's hourly range routinely spans a stop sized for FX-like behaviour.

**3. The entries have no edge.** Sweeping the target distance over the same
entries gives a **negative expectancy at every single value**:

| tp_r | 0.25 | 0.3 | 0.5 | 0.75 | 1.0 | 1.5 | 2.0 | 3.0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Win rate | 78.5% | 73.4% | 60.8% | 54.4% | 50.6% | 43.0% | 40.5% | 36.7% |
| Expectancy | −0.041 | −0.063 | −0.097 | −0.063 | −0.018 | −0.035 | −0.006 | −0.057 |

Note the trap this exposes: `tp_r=0.25` delivers a **78.5% win rate** — above the
75% target — and still **loses money**. That is the whole reason the calibrator
maximises expectancy subject to a win-rate constraint rather than the win rate
itself.

### Market conditions

| Regime | Trades | Win rate | Avg R |
|---|---:|---:|---:|
| TREND_BULL | 36 | 55.6% | +0.025 |
| TREND_BEAR | 34 | 41.2% | −0.108 |
| BREAKOUT | 7 | 28.6% | −0.400 |
| LIQUIDITY_SWEEP | 2 | 50.0% | +0.164 |

By volatility at entry (ATR percentile, trailing 200 bars):

| Bucket | Trades | Win rate | Avg R |
|---|---:|---:|---:|
| Q1 lowest | 5 | 80.0% | +1.485 |
| Q2 | 27 | 25.9% | −0.570 |
| Q3 | 19 | 57.9% | −0.055 |
| Q4 highest | 29 | 51.7% | +0.126 |

The damage is concentrated in **Q2 — moderate volatility**. Quiet markets work;
the highest-volatility quartile is roughly break-even; the moderate-volatility
band is where the strategy is chopped apart. Breakout entries (28.6% WR, −0.400 R
average) are the worst single regime.

---

## 4. Prioritized recommendations

**P0 — Fix the stop sizing (measured, +0.056 R/trade).** The isolated stop sweep
is unambiguous: widening only the stop takes expectancy from −0.0224 R to
+0.0340 R. Size the stop from realised volatility with a floor well above 1×ATR
for crypto, and re-derive the target from the widened stop rather than holding
the old absolute level.

**P0 — Stop cutting winners short.** 41% of trades exit on the trail/breakeven
stop for +0.677 R while losers run the full −1 R. Either the trail activation is
too early or the trail distance is too tight. The counterfactual shows the target
is *not* the problem, so the fix is in the trail, not `tp_r`.

**P1 — Do not trade BTCUSD on this evidence.** The calibrated edge test already
refuses it (OOS −0.008 R over 295 trades). Do not override that refusal without a
new reason to believe the edge exists.

**P1 — Filter entry location and regime.** 33% of losses were entered at the
extreme of the 24-bar range and the `BREAKOUT` regime lost 0.400 R per trade over
7 trades. Requiring entries to sit in the favourable half of the recent range, and
suppressing breakout entries, are cheap, testable filters.

**P2 — Keep the metadata layer honest.** The registry and the fetch manifests
were two sources of truth for the same facts, and when they drifted nothing
complained — the wrong spec was simply used and the symptom was attributed to the
strategy. `tests/test_symbol_registry.py` now pins them together; keep it green.

**P2 — Extend the calibration window.** BTCUSD's 3-month calibration looked
positive and its 6-month calibration did not. Three months of H1 data yields
~20–40 trades per symbol after the time stop — far too few to distinguish an edge
from luck. Treat any 3-month verdict as provisional.

---

## 5. Honest limitations

* **One instrument, one window, 80 trades.** Per-bucket win rates carry wide
  confidence intervals; the Q1 bucket has 5 trades and should not be trusted.
* **Swap/financing is not modelled.** A multi-day BTC position carries real
  financing cost that this backtest omits, so the true result is somewhat worse
  than reported.
* **MFE/MAE are bar-resolution.** They understate true intra-bar extremes on a
  volatile instrument.
* **Costs.** Median spread $22.50 plus 0.5 pips slippage per side. Crypto spreads
  widen sharply in fast markets, which a median does not capture.
* **The failure taxonomy is rule-based.** Every rule is a numeric predicate shown
  in the ledger, so labels can be checked — but a trade can trip several
  conditions and only the highest-priority one is counted as primary.

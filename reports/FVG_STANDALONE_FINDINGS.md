# FVG as a standalone entry trigger — measured verdict

**Date:** 2026-09-23
**Question:** if a fair-value-gap / displacement entry is run **standalone** as the
entry trigger (not as a filter on the incumbent), does it have a measurable edge
that survives the two bars the project already applies to the incumbent?

**Answer: DO NOT PROCEED.** FVG standalone is unprofitable on **0 of 20** symbols
and clears the multiple-testing bar on **0 of 20**. It does beat the always-long
control on 20/20 — but only because the always-long control loses *more*
(−0.659R vs −0.463R per trade). It is less bad, not better than nothing.

Reproduce with:

```
python tools/fvg_standalone_backtest.py --entry-bar-mode stop-only --spread-mode data
```

---

## 1. What was measured

| Element | Setting |
|---|---|
| Signal + execution timeframe | **M15 183d** (`data/market/real/<SYM>/<SYM>_M15_183d.parquet`) |
| Structure timeframe | H1 365d (used only for the optional `--htf-filter` variant) |
| Trigger | 3-candle FVG from the **shipped** detector `jarvis/market/fair_value_gap.py` (`FairValueGapEngine.analyze`), called on a window ending at each bar so the gap is causal |
| Displacement filter | middle (c2) candle body/range **≥ 0.55** |
| Entry | gap **50% consequent encroachment (CE) on retrace** — a trade is only counted if a later bar actually trades back into the CE (limit at the CE mid) |
| Retrace wait | 30 M15 bars |
| Stop | far gap edge ± 0.25 × ATR14 (ATR at the gap bar) |
| Target | 1.5R (production `tp_r`) |
| Costs | `jarvis/backtesting/fills.py`: `entry_fill(CE, side, spread_pips, pip)` → ask = CE + spread, bid = CE − spread; stop fills additionally pay 0.5 pip slippage (matches `tools/audit_trade_quality.py`) |
| Deflation | `jarvis/learning/deflated_sharpe.py` at `n_trials = 8` tp values/symbol (the project's own `TP_GRID_DEFAULT`), portfolio `n_trials = 160` |
| Effective n | `SampleUniquenessWeightEngine.effective_sample_size` over trade spans |

Data verified on disk before use: all 20 symbols have M15/M5 `_183d` and H1
`_365d`/`_183d`; M15 183d spans 2026-03-15 → 2026-09-11, H1 365d spans
2025-09-15 → 2026-09-15. The report's claim about coverage is correct.

### Spread column semantics (checked first, as instructed)

The parquet files carry **`spread`, not `spread_pips`**. The raw column is an
integer **in points**: EURUSD M15 median raw = 19 → **1.9 pips**, and the manifest
documents `point=1e-05`, `pip_size=1e-04`, `typical_spread_pips=3.3`.
`jarvis/data/schemas.py:320` derives the registry's own
`typical_spread_pips = info.spread * point / pip_size`, and
`SignalScanner._spread_for_bar` converts the same way. The conversion used here is
therefore `raw * point / pip_size`. **Multiplying raw by `pip_size` instead would
give 0.0019 price = 19 pips — a 10× over-charge** — so the warning in the brief is
real and was honoured. (Cross-check: `actual_spreads.json` records a live EURUSD
ask−bid of 0.00011 = 1.1 pips, same order.)

This feed's M15 FX spread is **wide**: EURUSD median 1.90 pips, against a registry
`max_spread_pips` of 2.0. The production spread gate would reject most of these
bars.

---

## 2. Per-symbol results (production tp = 1.5R, recorded per-bar spread)

| Symbol | FVG sigs | trades | n_eff | uniq | mean R | win% | SR/obs | best tp | PSR | DSR(prod) | DSR eff-n | always-long R | delta | paired t |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| AUDUSD | 1925 | 1610 | 1288 | 0.80 | -0.644 | 17.9 | -0.642 | 6 | 0.000 | 0.000 | 0.000 | -0.832 | +0.188 | +8.0 |
| BTCUSD | 3008 | 2523 | 2130 | 0.84 | -0.281 | 28.8 | -0.248 | 6 | 0.000 | 0.000 | 0.000 | -0.502 | +0.221 | +10.3 |
| ETHUSD | 2657 | 2198 | 1745 | 0.79 | -0.546 | 18.2 | -0.565 | 6 | 0.000 | 0.000 | 0.000 | -0.689 | +0.143 | +7.8 |
| EURJPY | 1760 | 1491 | 1233 | 0.83 | -0.451 | 24.8 | -0.402 | 6 | 0.000 | 0.000 | 0.000 | -0.698 | +0.247 | +10.0 |
| EURUSD | 1939 | 1636 | 1350 | 0.83 | -0.589 | 20.4 | -0.556 | 6 | 0.000 | 0.000 | 0.000 | -0.799 | +0.211 | +8.6 |
| GBPJPY | 1838 | 1565 | 1282 | 0.82 | -0.421 | 25.4 | -0.375 | 6 | 0.000 | 0.000 | 0.000 | -0.650 | +0.229 | +9.1 |
| GBPUSD | 1991 | 1656 | 1354 | 0.82 | -0.561 | 20.8 | -0.531 | 6 | 0.000 | 0.000 | 0.000 | -0.792 | +0.231 | +10.3 |
| GER40 | 1948 | 1617 | 1356 | 0.84 | -0.352 | 26.9 | -0.313 | 6 | 0.000 | 0.000 | 0.000 | -0.529 | +0.177 | +6.6 |
| NAS100 | 2063 | 1674 | 1443 | 0.86 | -0.240 | 31.1 | -0.205 | 6 | 0.000 | 0.000 | 0.000 | -0.457 | +0.217 | +8.5 |
| NZDUSD | 2047 | 1673 | 1270 | 0.76 | -0.690 | 15.9 | -0.724 | 6 | 0.000 | 0.000 | 0.000 | -0.878 | +0.188 | +9.1 |
| SOLUSD | 2748 | 2261 | 1594 | 0.71 | -0.704 | 12.2 | -0.859 | 6 | 0.000 | 0.000 | 0.000 | -0.810 | +0.105 | +7.0 |
| UK100 | 1999 | 1677 | 1376 | 0.82 | -0.421 | 25.5 | -0.375 | 6 | 0.000 | 0.000 | 0.000 | -0.639 | +0.218 | +8.7 |
| US30 | 1889 | 1567 | 1320 | 0.84 | -0.317 | 28.0 | -0.280 | 6 | 0.000 | 0.000 | 0.000 | -0.520 | +0.203 | +7.7 |
| US500 | 1923 | 1588 | 1367 | 0.86 | -0.310 | 31.6 | -0.252 | 6 | 0.000 | 0.000 | 0.000 | -0.560 | +0.250 | +9.1 |
| USDCAD | 2100 | 1768 | 1368 | 0.77 | -0.657 | 17.1 | -0.670 | 6 | 0.000 | 0.000 | 0.000 | -0.793 | +0.137 | +6.8 |
| USDCHF | 2161 | 1835 | 1400 | 0.76 | -0.700 | 15.8 | -0.734 | 6 | 0.000 | 0.000 | 0.000 | -0.797 | +0.097 | +5.1 |
| USDJPY | 1840 | 1571 | 1279 | 0.81 | -0.509 | 22.6 | -0.469 | 6 | 0.000 | 0.000 | 0.000 | -0.648 | +0.139 | +6.0 |
| WTI | 1839 | 1508 | 1309 | 0.87 | -0.290 | 29.1 | -0.253 | 6 | 0.000 | 0.000 | 0.000 | -0.501 | +0.211 | +7.4 |
| XAGUSD | 1865 | 1524 | 1286 | 0.84 | -0.316 | 28.3 | -0.277 | 6 | 0.000 | 0.000 | 0.000 | -0.584 | +0.268 | +9.9 |
| XAUUSD | 1879 | 1514 | 1318 | 0.87 | -0.140 | 34.7 | -0.117 | 4 | 0.000 | 0.000 | 0.000 | -0.462 | +0.322 | +11.0 |
| **PORTFOLIO** | — | 34456 | 4503 | 0.13 | -0.463 | 23.4 | -0.426 | — | 0.000 | 0.000 | 0.000 | -0.659 | +0.196 | — |

`mean R` is net of spread and slippage. `best tp` is the tp_r that maximised the
in-sample Sharpe out of the 8-value grid; the DSR columns are deflated for having
searched that grid.

---

## 3. Bar 1 — DSR > 0.95 on effective n

**Result: 0/20 pass. Portfolio DSR = 0.000.**

The observed per-observation Sharpe is **negative on all 20 symbols**
(portfolio SR = −0.426, annualised −66). PSR — the *undeflated* bar, which is the
most generous number available — is **0.000 on all 20**. The DSR cannot rescue a
negative Sharpe: deflation only raises the benchmark. FVG does not fail bar 1
narrowly; it fails it by having the wrong sign.

The one mildly interesting structural number: unlike the incumbent, FVG's
effective-n ratio is high (per-symbol **0.71–0.87**; the portfolio figure of 0.13
is an artifact — see bug P2 in §7). 34,456 trades are worth ~28,069 independent
bets when each symbol's own timeline is used. So the failure is not a
sample-size problem.

Breakeven for a 1.5R target, `p = (1 + c) / 2.5` where `c` is round-turn cost in R,
is **~42% on the cheapest symbols** (XAUUSD, c ≈ 0.05R) **to ~59% on EURUSD**
(c ≈ 0.48R). Observed win rates are **12.2%–34.7%**, median ~25%. Every symbol is
short of its own breakeven by 20–39 percentage points.

## 4. Bar 2 — beat always-long

**Result: 20/20 beat the control — and it does not mean what it looks like.**

The control trades **long at every FVG entry bar**, with the same risk distance
and the same 1.5R target, re-priced to the ask (so a SELL candidate's bid fill is
not reused as a free half-spread). Delta is positive on every symbol
(+0.097R to +0.322R, paired t between +5.1 and +11.0).

But the control's own expectancy is **−0.659R per trade** and FVG's is
**−0.463R**. Both lose. The direction call adds value *relative to always being
long in a market where the always-long control is shredded by a tight stop*, and
that is all it shows. Bar 2 was designed to rule out **beta** — P&L that comes
from the instrument's drift rather than the model's direction choice. Here the
drift is negative for this control, so the bar is passed vacuously. A positive
delta over a losing control is not an edge.

Because a BUY FVG and its always-long control are *the identical trade*
(same CE reference, same spread, same risk, same stop), the entire delta comes
from the SELL entries: FVG's bearish-gap shorts lose less than longs on the same
bars. That is the only directional information in the result.

## 5. Where it failed, precisely

| Failure | Magnitude |
|---|---|
| Sign of the edge | negative Sharpe on 20/20; portfolio −0.426 per-obs |
| Win rate vs breakeven | 12–35% observed vs 42–59% needed at 1.5R after cost (20–39 pp short) |
| Cost as a fraction of risk | EURUSD median **0.48R per trade** in spread alone (median risk 0.83×ATR, median spread 1.90 pips) |
| Instant stop-outs | **33–37% of all entries are stopped on the retrace bar itself** (EURUSD 36.7%, XAUUSD 33.4%, US500 36.4%, BTCUSD 35.4%) |
| Stop size | median risk 0.42–0.83 × ATR — the FVG's own "gap + 0.25 ATR" stop is still inside the noise band the audit flagged |
| Best achievable | the best tp_r in the grid is 6.0 for 19/20 symbols, i.e. only "never take profit" improves a losing signal |

---

## 6. Sensitivity — FVG given every benefit of the doubt

Four variants were run against the primary. None of them changes the verdict;
every variant except the lookahead one would *help* FVG if its failure were an
artifact of my choices.

| Variant | profitable | pass DSR>0.95 | clear both bars | portfolio mean R | portfolio DSR |
|---|---:|---:|---:|---:|---:|
| **Primary** — honest retrace bar, recorded per-bar spread | 0/20 | 0/20 | 0/20 | −0.463 | 0.000 |
| Lookahead retrace bar (stop **and** target tested on the entry bar) | 3/20 | 1/20 | 1/20 | −0.321 | 0.000 |
| Registry `typical_spread_pips` instead of the wide recorded spread | 0/20 | 0/20 | 0/20 | −0.430 | 0.000 |
| H1 EMA-aligned FVGs only (`--htf-filter`) | 0/20 | 0/20 | 0/20 | −0.470 | 0.000 |
| Best shot: 1.0×ATR stop buffer **and** registry spread | 0/20 | 0/20 | 0/20 | −0.206 | 0.000 |

**Lookahead variant.** Letting the simulator test the target on the entry bar —
which is genuine lookahead, because that bar's high may print before price
retraces to the CE — is the only variant that manufactures winners, and it
manufactures exactly **one**: XAUUSD (mean +0.220R, DSR_eff 0.981). NAS100
(+0.095R) and WTI (+0.011R) turn marginally positive but fail DSR. The
portfolio still fails (DSR 0.000). This is the single most important robustness
number: **the only way FVG passes is by cheating on intrabar ordering, and even
then only 1/20 symbols pass.**

**Cost variant.** Replacing the feed's recorded spread with the registry's own
`typical_spread_pips` is a real improvement — EURUSD moves from −0.589R to
−0.433R and its win rate from 20.4% to 28.1% — and it is still **negative on
20/20**. The wide feed spread is a contributing factor worth ~0.15R on FX, not
the cause of failure.

**HTF variant.** Restricting to FVGs aligned with the causal H1 EMA(20/50) trend
halves the trade count (34,456 → 17,760) and makes expectancy slightly *worse*
(−0.470R). The higher-timeframe context adds nothing.

**Best shot.** Widening the stop buffer from 0.25 to 1.0 × ATR *and* charging the
registry's cheaper spread lifts portfolio expectancy from −0.463R to −0.206R and
the win rate from 23.4% to 32.7% — and it is **still 0/20 profitable and 0/20
passing DSR**. The stop was indeed too tight, but fixing it does not create an
edge; it only stops some of the bleeding. No combination tested produces a
single symbol that clears the multiple-testing bar.


## 7. Production bugs found (reported, not fixed)

**P1 — two dead gates in the shipped FVG entry engine.**
`jarvis/intelligence/institutional_entry_engine.py`:
* `_execute_scalp_protocol` computes `has_mss, disp_ratio, ... = self._detect_mss_displacement(...)`
  at line 97. `has_mss` — the boolean that enforces the **0.55 displacement**
  requirement — is **never read**. It is only written into the result dict as
  `mss_displacement_ratio` (line 174). The displacement gate does not gate.
* `_execute_day_trading_protocol` computes `h1_aligned = self._check_h1_alignment(...)`
  at line 212. It is **never read** either — only stored as
  `h1_structure_aligned` (line 281). The "H1 structure alignment" gate does not gate.
* Same pattern one more time: `in_kill_zone` (line 209) only selects the
  *label* (`KILL_ZONE_SNIPER` vs `M15_FVG_CE`); both branches enter at market, so
  the kill-zone check changes nothing about whether a trade is taken.

Net effect: the SCALP/DAY entry path advertises displacement, HTF alignment and
kill-zone filters and enforces none of them. Any claim that the live FVG entry is
"displacement-filtered" or "H1-aligned" is not supported by the code.

**P2 — the portfolio "independent bets" number is an artifact.**
`tools/deflated_sharpe_report.py:239-259` pools every symbol's trade spans into one
`SampleUniquenessWeightEngine` call. `_concurrency_curve` builds concurrency over a
single bar-index timeline, so a trade at bar index 5000 in EURUSD and one at bar
index 5000 in BTCUSD are counted as **concurrent** — they are different
instruments at different wall-clock times. Measured impact:

| Report | pooled n_eff (quoted) | sum of per-symbol n_eff | understatement |
|---|---:|---:|---:|
| `deflated_sharpe_365d.json` (94,937 rows) | 327.5 | 3,944.3 | **12.0×** |
| `deflated_sharpe_M15_183d.json` (182,508 rows) | 542.0 | 5,711.1 | **10.5×** |
| this run (34,456 rows) | 4,503.4 | 28,069.0 | **6.2×** |

The headline "94,937 rows → 327 independent bets (0.3%)" should read
**"94,937 rows → 3,944 independent bets (4.2%)"**. The error is *conservative*
(it lowers the portfolio DSR), so it cannot have manufactured a false pass and
the audit's 0/20 conclusion stands unchanged — but the quoted number is wrong by
an order of magnitude and should not be repeated.

## 8. Caveats — where this measurement could be wrong

1. **The 0.55 threshold is not applied where the shipped code applies it.** The
   brief cites `institutional_entry_engine.py:563`, but that line lives in
   `_detect_mss_displacement`, which measures the max body/range over the **last
   5 bars** — not the FVG's middle candle — and (per P1) does not enforce it
   anyway. This backtest applies 0.55 to the **middle candle of the 3-candle
   pattern**, which is the textbook displacement leg and the natural reading of
   the brief, but it is *not* byte-identical to shipped logic. It is a stricter
   filter than the code actually applies.
2. **Cost model.** The feed's recorded M15 spread is wide (EURUSD median 1.9 pips;
   the registry's own `max_spread_pips` is 2.0). A cheaper feed would change the
   magnitudes — the `--spread-mode spec` run in §6 bounds this.
3. **Entry-bar convention.** The entry is a limit at the CE that fills intrabar,
   so the rest of the retrace bar is live. The primary run tests the **stop** on
   that bar but never the **target** (the bar's high may have printed before the
   retrace). Testing the target too flips XAUUSD from −0.14R to +0.22R — i.e. the
   optimistic convention manufactures a winner. §6 quantifies this.
4. **Exit geometry** is fixed at the production 1.5R with no scale-out, no
   breakeven, no trail. A different geometry might help, but the tp grid (1.0–6.0)
   already shows the best in-sample choice is 6.0 on 19/20 symbols, so the signal
   does not support a tighter target either.
5. **One entry per gap**, first retrace, 30-bar wait. Longer waits add trades but
   not, on this evidence, edge.
6. **The deflation counts the 8-value tp grid, not the 5 design variants** run in
   §6. A fully honest deflation would raise `n_trials` and lower every DSR
   further. It cannot change the verdict — in the primary configuration the
   portfolio DSR is 0.000 and no symbol has a positive Sharpe — but the DSR
   numbers should not be read as "0.95 was nearly reached". The one number that
   *does* come close, XAUUSD's 0.981 in the lookahead variant, collapses to 0.000
   under the honest entry-bar convention, so it is an artifact of the lookahead
   rather than a near miss.

## 9. Verdict

**DO NOT PROCEED.**

FVG run standalone has no measured edge: negative Sharpe on 20/20 symbols,
0/20 clear DSR > 0.95, portfolio DSR = 0.000, and 0/20 are even profitable before
deflation. Its 20/20 win over always-long is a win over a *losing* control and
carries no evidence of alpha.

Per the audit's own §C: the incumbent is unproven and FVG is now measured to be
unproven as well. Swapping one for the other is not justified. Wiring FVG as an
*additional* entry (the audit's fallback) is also not supported — an additional
losing signal does not improve a book.

The next move on the entry model should be to **overturn or accept
`entry_edge_verdict.md`** ("there is no edge in the current entry features") with
evidence, not to add another unmeasured trigger. The mechanical stop-floor fix
(ranked action #1) remains the only change with a measured, if modest, basis.

---

## Artifacts

| File | Contents |
|---|---|
| `tools/fvg_standalone_backtest.py` | the backtest; reproduces every number here |
| `reports/fvg_standalone_backtest.json` | primary run (per-symbol + portfolio) |
| `reports/fvg_sens_lookahead.json` | `--entry-bar-mode both` |
| `reports/fvg_sens_spread.json` | `--spread-mode spec` |
| `reports/fvg_sens_htf.json` | `--htf-filter` |
| `reports/fvg_sens_bestshot.json` | `--buffer-atr 1.0 --spread-mode spec` |

Primary command:

```
python tools/fvg_standalone_backtest.py --entry-bar-mode stop-only --spread-mode data
```


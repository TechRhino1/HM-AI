# Stop-Floor A/B/C — does raising the floor actually help?

*Read-only measurement. No file under `jarvis/` was modified. Instrument: `tools/stop_floor_ab.py`.*

**Verdict: the proposed one-line change is a measured no-op on the population the audit's own
instruments replay, and the premise it is built on (median stop 0.21×ATR) does not reproduce.**
Raising the floor from `0.10×ATR` to `1.11×ATR` moves portfolio expectancy by **+0.0009R per
trade** (183d) / **+0.0012R** (365d) and the stop-out rate by **−0.04 pp**. It does not make the
system profitable, does not beat always-long, and leaves DSR on effective n at **0.000**. The
reason is mechanical and unambiguous: the stored production stops are already **2.58×ATR_H1** at
the median, so a floor at 1.11×ATR binds on only **0.2–3%** of trades.

---

## 1. Method, and what was verified before trusting it

Vary **one** variable — the stop floor — with entry signal, entry bar, entry fill, target rule and
cost model all held fixed.

For a candidate whose stored stop is at distance `d0 = |fill − sl|`, raising the floor to `k·ATR`
is exactly `d_new = max(d0, k·ATR)`. That is algebraically identical to re-running production with
the new floor (`max(underlying, old_floor, new_floor) == max(underlying, new_floor)` when
`new_floor ≥ old_floor`), so no pre-floor quantity is invented.

Two stop modes:
* **`floor`** — `max(stored, k·ATR)`: *the actual proposed change*, on the real stop distribution.
* **`width`** — `k·ATR`: a counterfactual that answers "if the floor were the binding term, how
  does expectancy move with width?" This is the break-even question.

Two target conventions (both reported):
* **`rr`** — target = `tp_r · risk` (production fallback; the audit's established basis).
* **`abs`** — target = the candidate's own stored absolute `tp` price, so widening the stop
  mechanically *lowers* the R:R. The harsher reading of "hold the target fixed".

### Spread semantics — verified, not assumed

The parquet `spread` column is MT5 **integer points**, not pips. The candidate tables store
`spread_pips` already converted by `signal_scan._spread_for_bar` as `raw · 10^-digits / pip_size`.
This tool consumes the **stored `spread_pips`** column and never re-derives spread from the raw
column, so cost cannot be double-charged. Spot checks, raw → stored pips: EURUSD 19 → 1.9
(digits 5, pip 1e-4); USDJPY 20 → 2.0; XAUUSD 16 → 1.6 (pip 0.1); US500 55 → 0.55 (pip 1.0);
GER40 195 → 1.95; BTCUSD 2250 → 2250 (point == pip). `spread_price = spread_pips · pip_size`
equals `raw · point` on every feed checked.

### Cost model — proven arm-invariant

Entry fill carries the spread once; protective-stop exits pay the engine default 0.5-pip slippage;
target fills pay none; commission 0. The tool reports **cost in price terms per arm**. Across all
arms it is constant to 5 significant figures (e.g. 183d floor-mode: 0.071497 → 0.071441 over the
whole sweep). The cost model did **not** quietly stop charging the spread. Only the
R-normalised drag falls, because a fixed price cost is a smaller fraction of a wider 1R — a
mechanical, edge-free improvement, and it is small: cost drag is 0.0005R/trade at these widths.

### Data

20 symbols, H1, `data/market/real/<SYM>/` + cached candidates `data/signals/<SYM>_H1_<W>d_candidates.parquet`.
183d: 46,939 trades. 365d: 94,937 trades — **the same row count the audit quotes**, confirming the
same population and instrument.

---

## 2. Primary result — floor mode, 183d, `rr` (the proposed change)

Portfolio, pooled across 20 symbols:

| floor (×ATR) | trades | stop-out | target | win rate | E[R] | total R | avg loss $/lot | med risk ×ATR | DSR(eff n) | vs always-long |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **0.10 (A, current)** | 46,939 | 63.55% | 35.3% | 36.0% | **−0.11068** | −5195.3 | −903.97 | 2.580 | 0.000 | −0.0517 |
| 0.30 | 46,939 | 63.55% | 35.3% | 36.0% | −0.11068 | −5195.3 | −903.97 | 2.580 | 0.000 | −0.0517 |
| 0.50 | 46,939 | 63.55% | 35.3% | 36.0% | −0.11068 | −5195.3 | −903.97 | 2.580 | 0.000 | −0.0517 |
| 0.70 | 46,939 | 63.5% | 35.4% | 36.0% | −0.11051 | −5187.3 | −904.09 | 2.580 | 0.000 | −0.0519 |
| 0.85 (C) | 46,939 | 63.5% | 35.4% | 36.0% | −0.11034 | −5179.2 | −904.23 | 2.580 | 0.000 | −0.0517 |
| 1.00 | 46,939 | 63.5% | 35.4% | 36.0% | −0.10978 | −5153.0 | −904.87 | 2.580 | 0.000 | −0.0517 |
| **1.11 (B, proposed)** | 46,939 | 63.51% | 35.4% | 36.0% | **−0.10976** | −5152.2 | −905.28 | 2.580 | 0.000 | −0.0515 |
| 1.30 | 46,939 | 63.5% | 35.4% | 36.0% | −0.10934 | −5132.3 | −907.68 | 2.580 | 0.000 | −0.0515 |
| 1.50 | 46,939 | 63.5% | 35.4% | 36.0% | −0.10933 | −5131.6 | −911.59 | 2.580 | 0.000 | −0.0512 |

**A → B: expectancy +0.00092R, stop-out rate −0.04 pp, loss per loser +$1.31/lot.** Arms 0.10, 0.30
and 0.50 are *bit-identical* — direct evidence that the floor is not the binding constraint.

### Per symbol (A = 0.10, B = 1.11)

| symbol | n | stop-out A | stop-out B | Δstop (pp) | E[R] A | E[R] B | ΔE[R] | med risk ×ATR | trades widened | DSR(eff) | vs always-long |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| AUDUSD | 2309 | 73.2% | 73.2% | −0.09 | −0.35876 | −0.35650 | +0.00226 | 2.589 | 0.95% | 0.000 | −0.1465 |
| BTCUSD | 3191 | 63.6% | 63.5% | −0.04 | −0.09424 | −0.09345 | +0.00079 | 1.783 | 0.47% | 0.000 | −0.0833 |
| ETHUSD | 2977 | 62.5% | 62.5% | 0.00 | −0.08507 | −0.08507 | +0.00000 | 2.989 | 0.00% | 0.001 | −0.0523 |
| EURJPY | 2266 | 66.6% | 66.7% | +0.04 | −0.18407 | −0.18505 | −0.00098 | 2.447 | 2.29% | 0.000 | −0.1194 |
| EURUSD | 2151 | 67.0% | 67.0% | −0.09 | −0.21058 | −0.20798 | +0.00260 | 2.496 | 2.00% | 0.000 | −0.0250 |
| GBPJPY | 2190 | 62.2% | 62.1% | −0.09 | −0.07693 | −0.07457 | +0.00236 | 2.453 | 1.96% | 0.001 | −0.0320 |
| GBPUSD | 2198 | 66.3% | 66.3% | +0.04 | −0.19196 | −0.19294 | −0.00098 | 2.497 | 2.00% | 0.000 | −0.0874 |
| GER40 | 2277 | 57.7% | 57.6% | −0.09 | +0.04067 | +0.04291 | +0.00224 | 2.607 | 1.89% | 0.025 | +0.0473 |
| NAS100 | 2185 | 59.2% | 59.2% | −0.04 | +0.01483 | +0.01600 | +0.00117 | 2.591 | 2.29% | 0.011 | +0.0273 |
| NZDUSD | 2268 | 66.6% | 66.6% | 0.00 | −0.19872 | −0.19870 | +0.00002 | 2.643 | 0.66% | 0.000 | +0.0588 |
| SOLUSD | 3133 | 69.1% | 69.1% | 0.00 | −0.26770 | −0.26770 | +0.00000 | 3.360 | 0.00% | 0.000 | −0.0508 |
| UK100 | 1900 | 64.6% | 64.5% | −0.11 | −0.15338 | −0.15066 | +0.00272 | 2.652 | 1.42% | 0.000 | −0.0026 |
| US30 | 2096 | 60.4% | 60.3% | −0.04 | −0.01875 | −0.01752 | +0.00123 | 2.549 | 2.62% | 0.005 | −0.1248 |
| US500 | 2235 | 60.5% | 60.4% | −0.09 | −0.02936 | −0.02700 | +0.00236 | 2.577 | 1.79% | 0.004 | −0.0542 |
| USDCAD | 2318 | 60.7% | 60.7% | 0.00 | −0.03889 | −0.03878 | +0.00011 | 2.523 | 1.90% | 0.003 | −0.0273 |
| USDCHF | 2269 | 67.0% | 67.0% | 0.00 | −0.20182 | −0.20174 | +0.00008 | 2.547 | 0.88% | 0.000 | −0.2581 |
| USDJPY | 2350 | 67.5% | 67.5% | 0.00 | −0.21065 | −0.21054 | +0.00011 | 2.565 | 1.66% | 0.000 | −0.2256 |
| WTI | 2168 | 55.4% | 55.4% | 0.00 | +0.10217 | +0.10219 | +0.00002 | 2.517 | 3.04% | 0.063 | −0.0436 |
| XAGUSD | 2231 | 60.6% | 60.6% | −0.04 | −0.03580 | −0.03466 | +0.00114 | 2.647 | 2.20% | 0.003 | +0.0661 |
| XAUUSD | 2227 | 57.7% | 57.6% | −0.09 | +0.04928 | +0.05154 | +0.00226 | 2.653 | 1.71% | 0.025 | +0.1304 |

No symbol moves materially. 7 of 20 have ΔE < +0.0002R; the largest is +0.0027R (UK100). Only 5 of
20 beat always-long, and none of those gains come from the floor change. DSR on effective n is
0.000–0.063 everywhere — **0 of 20 pass 0.95**.

---

## 3. The break-even — pure stop-width curve (183d, `rr`)

Expectancy never turns down in the tested range, but the **marginal** return per unit of width
collapses by ~50× after ≈0.7×ATR:

| width (×ATR) | stop-out | win rate | E[R] | total R | avg loss $/lot | ΔE | **ΔE per +0.01×ATR** |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.10 | 95.3% | 4.7% | −1.10978 | −52092.0 | −41.4 | — | — |
| 0.21 | 87.2% | 12.8% | −0.78157 | −36686.0 | −78.0 | +0.32821 | **+0.02984** |
| 0.30 | 81.1% | 18.9% | −0.59476 | −27917.3 | −107.2 | +0.18681 | +0.02076 |
| 0.50 | 73.6% | 26.3% | −0.37805 | −17745.4 | −177.3 | +0.21671 | +0.01084 |
| **0.70** | 69.8% | 30.1% | −0.27127 | −12733.0 | −249.8 | +0.10678 | **+0.00534 ← knee** |
| 0.85 | 68.5% | 31.5% | −0.23327 | −10949.6 | −308.2 | +0.03800 | +0.00253 |
| 1.00 | 67.5% | 32.5% | −0.20534 | −9638.5 | −365.4 | +0.02793 | +0.00186 |
| **1.11** | 67.0% | 32.9% | −0.19192 | −9008.5 | −409.5 | +0.01342 | **+0.00122** |
| 1.30 | 66.1% | 33.8% | −0.16877 | −7921.8 | −484.1 | +0.02315 | +0.00122 |
| 1.50 | 65.5% | 34.4% | −0.15017 | −7049.0 | −557.5 | +0.01860 | +0.00093 |
| 2.00 | 64.3% | 35.5% | −0.12012 | −5638.2 | −734.4 | +0.03005 | +0.00060 |

**Break-even answer.** There is no floor at which "fewer stop-outs stop paying for the larger loss"
— the curve does not turn. What there *is*, is a **knee at ≈0.70×ATR**: below it, widening buys
0.005–0.030 R per 0.01×ATR; above it, 0.0006–0.0025 R per 0.01×ATR. By 1.11×ATR the marginal
return is **4% of its peak value**. So 1.11 sits inside the flat region: it is past the point where
width pays, not at the point where it starts to. And the curve never crosses zero — the best
achievable expectancy at any width is **−0.120R**, still a loss, still DSR 0.

The `abs`-target width curve shows the same shape (knee ≈0.70×ATR; E from −0.910R at 0.10 to
−0.146R at 2.00; never positive).

---

## 4. Robustness

| run | A (0.10) E[R] | B (1.11) E[R] | ΔE[R] | stop-out A→B | DSR(eff) | trades |
|---|---:|---:|---:|---:|---:|---:|
| 183d, `rr` (primary) | −0.11068 | −0.10976 | **+0.00092** | 63.55% → 63.51% | 0.000 | 46,939 |
| 183d, `abs` target fixed in price | −0.11718 | −0.11675 | **+0.00043** | 73.2% → 73.2% | 0.010 | 46,939 |
| 365d, `rr` | −0.05094 | −0.04974 | **+0.00120** | 61.4% → 61.3% | 0.003 | 94,937 |

All three agree: no-op. The conclusion does not depend on the target convention or the window.

---

## 5. Independent check of the premise (median stop 0.21×ATR)

**It does not reproduce.** Two independent measurements:

* **Candidate population (the audit's own instrument):** median stored stop is
  **2.58×ATR_H1** (per-symbol medians 1.78–3.36). Share below 0.85×ATR_H1: **0.0–1.0%**. Share
  below 1.11×ATR_H1: **0.0–3.0%**.
* **Entry-bar adverse range:** the share of trades whose stop is breached by the *entry bar's own*
  adverse excursion, pooled (n-weighted): k=0.10 → **95.3%**, 0.21 → 85.5%, 0.50 → 53.2%,
  0.85 → **26.3%**, 1.11 → **15.3%**, 1.50 → 6.7%. The audit quotes 16.9% at 0.85 and 10% at 1.11;
  the true threshold for 10% is ≈1.35×ATR_H1, and for 16.9% ≈1.07×ATR_H1.

**Why the 0.21 figure is almost certainly a unit error.** `symbol_registry.py:22` documents
`typical_atr_pct` as *"Typical **daily** ATR as % of price"*. Measured H1 ATR is **4.0–6.0×**
smaller than `price · typical_atr_pct/100` (EURUSD 4.45, BTCUSD 5.03, GBPUSD 4.87, US500 4.00,
SOLUSD 5.99). Dividing an H1 stop by a **daily** ATR shrinks it by ~4.5×:
`1.19 / 4.5 ≈ 0.26 ≈ 0.21`.

**Internal-consistency refutation.** If stops really were 0.21×ATR, the width curve (§3) puts
expectancy at **≈−0.78R**. The system's measured expectancy is **−0.111R** (183d) and **−0.050R**
(365d) — which is what the curve predicts for stops at ≈2.6×ATR. The audit's own P&L contradicts
its own 0.21×ATR premise.

*(A third source, `data/jarvis_trade_memory.db`, cannot arbitrate: 42/93 rows have `exit_price=0`
and `pnl=0`, and XAUUSD rows carry `entry_price=2400` while the market prints ~5000.)*

---

## 6. Production bugs found (reported, not fixed)

1. **`dynamic_levels.py:141-144` — ATR timeframes mixed; the volatility-adaptive buffer is dead.**
   `typical_atr = c_price · typical_atr_pct/100` is a **daily** ATR but is used as `atr_median`
   against an **H1** `atr`. The ratio is therefore ≈0.26, and `atr_ratio = clamp(ratio, 0.33, 3.0)`
   **pins at its 0.33 floor on 69.8% of all bars** (FX majors 90–99.6%: EURJPY 92.1%, USDJPY 79.2%,
   USDCAD 99.6%). `buffer_mult = alpha_base + beta_vol·atr_ratio + …` therefore has a *constant*
   volatility term — the adaptivity it is named for does not happen. This is also the likely origin
   of the audit's 0.21×ATR figure.
2. **`institutional_entry_engine.py:131, 341, 348` — risk floored without moving the stop.**
   `risk_dist = max(pip_size·5, entry − sl)` lifts `risk_dist` while `sl_price` stays at the tight
   structural level. Sizing, the reported R:R and the actual stop level then describe different
   trades — the mechanism behind the audit's "0.06×ATR stop with R:R 13.8" and the same class as
   the 0.7-pip → 5-pip re-anchor defect. Unlike `dynamic_levels.py:265-276` (which has a comment
   explaining exactly why the distance and not just `risk_dist` must be floored), this path still
   has the bug.
3. **`dynamic_levels.py:514-539` — the baseline levels are discarded.** Whenever `mtf_data` is
   present (always, live and in the scan) and the institutional engine returns a dict, the function
   returns *its* levels. Lines **273 and 403 — the two lines the audit proposes to edit — are on the
   baseline path and are not returned.** Editing them may not change live stops at all. (The stored
   candidate stops are ~2.5×ATR_H1, consistent with the structural/institutional stop dominating,
   not with either floor binding.)
4. **`symbol_registry` spread specs disagree with the data.** `typical_spread_pips` understates
   measured FX spreads ~2–3×: EURUSD 0.7 (registry) vs 1.90 (measured median) vs 3.30 (manifest);
   GBPJPY 1.2 / 2.0 / 7.6; USDJPY 0.8 / 2.1 / 3.7. Separately, the manifest's same-named field is in
   **points** for indices while the registry's is in **pips** (GER40 205 vs 1.95; US30 400 vs 3.90;
   XAUUSD 24 vs 2.0). Anything pricing cost or a spread ratio off the registry — e.g.
   `dynamic_levels.py:146-148` `spread_ratio = cur/typ_spread` — is mis-scaled.
5. **`data/jarvis_trade_memory.db` is not a trustworthy evidence base.** 42/93 rows have
   `exit_price=0` and `pnl=0`; XAUUSD rows carry `entry_price=2400` while the market prints ~5000;
   `pnl` sums to −$8.65 against the audit's −$633.33 for "137 real closed trades". Any conclusion
   drawn from it (including the 0.21×ATR figure) needs a different source.

---

## 7. Recommendation

**Do not ship the one-line change as a fix.** On the population the audit's own instruments replay
it is measurably inert (ΔE +0.0009R, Δstop-out −0.04 pp, DSR 0.000, still loses to always-long), and
on the code path it edits it may not even be reached (bug 3). It also cannot be validated as helpful
because the defect it targets is not present in that population (median stop 2.58×ATR_H1, not 0.21).

If the intent is still to move the floor, the evidence says the economically meaningful setting is
**≈0.70×ATR**, not 1.11 — that is the knee of the width curve, above which ~95% of the benefit per
unit of width is already gone. But even the best point on that curve is **−0.120R/trade** with
DSR 0, so a floor change is a *loss-reduction* lever, never a profitability lever. Consistent with
the audit's own warning: expect fewer instant stop-outs; expect no profitability.

The higher-value next step is **not** the floor. It is (a) fixing bugs 1–3 so that the stop the
system reports is the stop the system places, and (b) re-deriving the defect from a trustworthy
trade source before any floor is chosen — the current 0.21×ATR premise is a ~4.5× unit error and the
P&L contradicts it.

---

## 8. Reproduce

```bash
PY=C:/Users/Itrai/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe
# primary: the proposed change on the real stop distribution
$PY tools/stop_floor_ab.py --tf H1 --window 183 --stop-mode floor --tp-mode rr
# break-even: pure stop-width curve
$PY tools/stop_floor_ab.py --tf H1 --window 183 --stop-mode width --tp-mode rr \
    --floors 0.10,0.21,0.30,0.50,0.70,0.85,1.00,1.11,1.30,1.50,2.00
# robustness: target fixed in price; 365d window
$PY tools/stop_floor_ab.py --tf H1 --window 183 --stop-mode floor --tp-mode abs --floors 0.10,0.85,1.11
$PY tools/stop_floor_ab.py --tf H1 --window 365 --stop-mode floor --tp-mode rr --floors 0.10,0.85,1.11
# entry-bar adverse-range probe (no simulation)
$PY tools/stop_floor_ab.py --tf H1 --window 183 --probe-only \
    --floors 0.10,0.21,0.50,0.85,1.11,1.50 --out reports/stop_floor_instant_stopout.json
```

Artifacts: `reports/stop_floor_ab_H1_183d_rr_floor.json`,
`reports/stop_floor_ab_H1_183d_rr_width.json`,
`reports/stop_floor_ab_H1_183d_abs_floor.json`,
`reports/stop_floor_ab_H1_183d_abs_width.json`,
`reports/stop_floor_ab_H1_365d_rr_floor.json`,
`reports/stop_floor_instant_stopout.json`.

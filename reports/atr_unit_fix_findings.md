# ATR-unit-fix A/B/C — findings

**Question:** if `atr_median` were computed on the same timeframe as `atr` (so
`atr_ratio` becomes a genuine same-unit ratio and stops being pinned at its 0.33
clamp), what actually changes?

**Answer: almost nothing — and the reason is not the one the bug report implies.**

Tool: `tools/atr_unit_fix_ab.py`. Raw results:
`reports/atr_unit_fix_ab_H1_183d.json`, `reports/atr_unit_fix_ab_H1_365d.json`.
All numbers below are H1, 20 symbols, production `tp_r = 1.5`, 0.5-pip stop
slippage, spread consumed from the candidate table's stored `spread_pips` column.

---

## 1. The clamp is real and reproduced

`atr_ratio` distribution over the 46,939 (183d) / 94,937 (365d) candidate bars:

| arm | window | n | @0.33 | @3.0 | between | median | mean |
|---|---|---|---|---|---|---|---|
| A (current, daily registry) | 183d | 46,939 | **68.62%** | 0.00% | 31.38% | 0.330 | 0.384 |
| A (current, daily registry) | 365d | 94,937 | **67.12%** | 0.05% | 32.83% | 0.330 | — |
| B (same-TF trailing median, 100 bars) | 183d | 46,939 | 0.00% | 0.49% | 99.51% | 0.997 | 1.035 |
| B (same-TF trailing median, 100 bars) | 365d | 94,937 | 0.00% | 0.31% | 99.69% | 1.000 | — |
| C (daily ÷ √24) | 183d | 46,939 | 0.09% | 5.68% | 94.24% | 1.267 | 1.448 |

**Confirmed, with a caveat.** The clamp is pervasive — 67–69% overall, and
per-symbol up to 99.7% (USDCAD) / 95.5% (GBPJPY) / 92.3% (EURJPY). My measured
overall figure is **68.6% (183d) / 67.1% (365d)**, a little below the 69.8%
quoted. The per-symbol FX range I measure is 63–96%, not 90–99.6%; the low-FX
outliers are NZDUSD (64.2%) and USDCHF (63.0%). Changing the `c_price` proxy from
the signal bar's close to the candidate's own `entry` does **not** move the number
(identical to 2 dp), so the gap is most likely a different window/symbol set in
the original measurement, not a definitional one.

**Measured daily↔timeframe ratio (arm C context).** `median(atr_H1 / registry_daily_atr)`
= **0.288 pooled** (0.16 EURJPY … 0.66 WTI). So the registry's `typical_atr_pct`
is on average **≈3.5× the actual H1 ATR**. That is the direct measurement of the
unit error. It also means √24 = 4.90 over-corrects: arm C's median `atr_ratio` is
1.27, i.e. ~27% too high. The empirically correct rescale divisor is **≈3.5**,
not √24.

---

## 2. Why the clamp is a red herring — `anti_wick_buffer` always wins

`atr_ratio` enters the stop through exactly one expression
(`dynamic_levels.py:151-169`):

```
buffer_mult      = 0.12 + 0.05·atr_ratio + 0.05·spread_ratio
dynamic_buffer   = atr · buffer_mult
anti_wick_buffer = atr · anti_wick_mult          # 0.35, 0.40 or 0.45
effective_buffer = max(dynamic_buffer, anti_wick_buffer)
```

With `atr_ratio ∈ [0.33, 3.0]` and `spread_ratio ∈ [0.5, 4.0]`,
`buffer_mult ∈ [0.14, 0.47]` — but it only exceeds `anti_wick_mult = 0.35` when
`atr_ratio + spread_ratio > 4.6`. For a normal spread (`spread_ratio ≈ 1`) that
needs `atr_ratio > 3.6`, which the 3.0 clamp forbids. **So `dynamic_buffer` never
wins, in either arm, and `effective_buffer = anti_wick_buffer = 0.35·ATR`.**

| quantity | arm A | arm B | arm C |
|---|---|---|---|
| `anti_wick_buffer` binds (183d) | 100.0% (99.4–100% per symbol) | ~99.5% | ~99% |
| median `effective_buffer` / ATR | **0.3500** | **0.3500** | **0.3500** |
| median stop distance / ATR (183d) | **2.5805** | 2.5810 | 2.5812 |
| median stop distance / ATR (365d) | **2.5756** | 2.5759 | 2.5764 |
| candidates with *any* buffer change | — | **2.41%** | — |
| max buffer delta observed | — | **0.12 ATR** | — |

The 2.41% that do change are the bars with an abnormally wide spread
(`spread_ratio ≳ 3.6`), concentrated in EURJPY/GBPJPY/USDJPY/UK100/GER40. The
stop moves **very slightly wider** (never narrower in aggregate), by at most
0.12 ATR on those bars.

---

## 3. Trade outcomes — the fix is a no-op

Pooled replay, identical cost model in every arm (entry fill carries the spread;
stops pay 0.5-pip slippage; commission 0). The cost check is arm-invariant as
required: `cost_price_per_trade` = 0.0720227 / 0.0720176 / 0.0720225 (365d).

**365d (94,937 candidates):**

| arm | trades | stop% | WR% | E[R] | total R | median stop (ATR) | DSR eff-n |
|---|---|---|---|---|---|---|---|
| A | 94,937 | 61.4 | 38.3 | **−0.05086** | −4828.1 | 2.5756 | 0.0048 |
| B | 94,937 | 61.4 | 38.3 | **−0.05088** | −4830.6 | 2.5759 | 0.0048 |
| C | 94,937 | 61.3 | 38.3 | **−0.05070** | −4813.0 | 2.5764 | 0.0048 |

**183d (46,939 candidates):**

| arm | trades | stop% | WR% | E[R] | total R | median stop (ATR) | DSR eff-n |
|---|---|---|---|---|---|---|---|
| A | 46,939 | 63.5 | 36.0 | **−0.11056** | −5189.7 | 2.5805 | 0.0006 |
| B | 46,939 | 63.5 | 36.0 | **−0.11062** | −5192.2 | 2.5810 | 0.0006 |
| C | 46,939 | 63.5 | 36.0 | **−0.11051** | −5187.2 | 2.5812 | 0.0006 |

**Per-trade diff (183d, arm A vs B):** 46,939 trades compared — **3 outcome flips
(0.0064%)**, 218 R changes (0.46%), 1,129 candidates (2.41%) with any buffer delta.

**Sign:** the fix makes stops a hair *wider*, and a hair wider is a hair *worse*
(ΔE[R] = −0.00002 R pooled 365d, −0.00006 R pooled 183d). Directionally this
reproduces the earlier "widening a stop on a no-edge signal loses a little more"
result, at a magnitude ~2,000× smaller than the 1.11×ATR floor experiment.

### Per-symbol (365d)

| sym | n | clampA@0.33 | clampB@0.33 | antiWick binds A | buf changed | maxΔ (ATR) | medStopA | medStopB | E[R] A | E[R] B | ΔE[R] | n_eff |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AUDUSD | 4636 | 68.3% | 0.0% | 100.0% | 2.63% | 0.0582 | 2.585 | 2.586 | −0.23741 | −0.23740 | +0.00001 | 246 |
| BTCUSD | 6416 | 78.5% | 0.0% | 100.0% | 0.09% | 0.0103 | 1.790 | 1.790 | +0.01516 | +0.01516 | +0.00000 | 634 |
| ETHUSD | 6245 | 65.7% | 0.0% | 100.0% | 0.00% | 0.0000 | 2.922 | 2.922 | +0.03995 | +0.03995 | +0.00000 | 269 |
| EURJPY | 4623 | 92.3% | 0.0% | 100.0% | 4.87% | 0.1200 | 2.488 | 2.489 | −0.12599 | −0.12653 | −0.00054 | 263 |
| EURUSD | 4419 | 87.0% | 0.0% | 100.0% | 3.91% | 0.0608 | 2.518 | 2.518 | −0.07108 | −0.07108 | +0.00000 | 251 |
| GBPJPY | 4575 | 95.5% | 0.0% | 100.0% | 4.50% | 0.1154 | 2.480 | 2.480 | −0.07073 | −0.07128 | −0.00055 | 259 |
| GBPUSD | 4559 | 91.3% | 0.0% | 100.0% | 3.60% | 0.0489 | 2.489 | 2.489 | −0.08718 | −0.08718 | +0.00000 | 260 |
| GER40 | 4430 | 65.1% | 0.0% | 99.6% | 7.27% | 0.0624 | 2.610 | 2.610 | +0.01109 | +0.01165 | +0.00056 | 240 |
| NAS100 | 4497 | 71.9% | 0.0% | 100.0% | 0.00% | 0.0000 | 2.575 | 2.575 | −0.07377 | −0.07377 | +0.00000 | 267 |
| NZDUSD | 4693 | 64.2% | 0.0% | 100.0% | 0.00% | 0.0000 | 2.635 | 2.635 | −0.09873 | −0.09873 | +0.00000 | 253 |
| SOLUSD | 6292 | 80.3% | 0.0% | 100.0% | 0.00% | 0.0000 | 3.233 | 3.233 | −0.15836 | −0.15836 | +0.00000 | 251 |
| UK100 | 4009 | 59.3% | 0.0% | 99.6% | 9.75% | 0.0528 | 2.638 | 2.640 | −0.04038 | −0.04038 | +0.00000 | 241 |
| US30 | 4200 | 81.3% | 0.0% | 100.0% | 0.00% | 0.0000 | 2.575 | 2.575 | −0.02319 | −0.02319 | +0.00000 | 273 |
| US500 | 4416 | 69.4% | 0.0% | 100.0% | 0.00% | 0.0000 | 2.583 | 2.583 | −0.06347 | −0.06347 | +0.00000 | 245 |
| USDCAD | 4492 | 96.0% | 0.0% | 100.0% | 0.22% | 0.0347 | 2.522 | 2.522 | −0.01726 | −0.01726 | +0.00000 | 248 |
| USDCHF | 4567 | 63.0% | 0.0% | 100.0% | 0.92% | 0.0425 | 2.571 | 2.571 | −0.06502 | −0.06502 | +0.00000 | 245 |
| USDJPY | 4687 | 63.1% | 0.0% | 99.9% | 7.15% | 0.1084 | 2.622 | 2.623 | −0.08053 | −0.08052 | +0.00001 | 242 |
| WTI | 4133 | 18.4% | 0.0% | 100.0% | 0.00% | 0.0000 | 2.560 | 2.560 | +0.00894 | +0.00894 | +0.00000 | 250 |
| XAGUSD | 4509 | 16.0% | 0.0% | 100.0% | 0.02% | 0.0334 | 2.632 | 2.632 | +0.03029 | +0.03029 | +0.00000 | 254 |
| XAUUSD | 4539 | 2.3% | 0.0% | 100.0% | 0.00% | 0.0000 | 2.636 | 2.636 | +0.08892 | +0.08892 | +0.00000 | 253 |

`n_eff` is per-symbol (López de Prado average uniqueness). Uniqueness ratio is
**4–10%** — 4,000–6,400 candidate rows are worth only ~240–640 independent bets.
The pooled book effective-n from `deflated_sharpe_report.py` is not reported here
(it pools `entry_bar` indices across symbols, whose per-symbol bar ranges overlap,
so the concurrency curve is meaningless); per-symbol DSR-eff is 0.000–0.31, all
far below the 0.95 gate.

---

## 4. Skepticism checks

* **Look-ahead?** No. Arm B's median ATR is a *trailing* window ending at the
  signal bar (window `[i-99, i]`); `atr[i]` itself only uses bars ≤ i. Entries and
  exits are unchanged from the production harness, which is already strictly
  causal (`trade_simulator` tests the stop at the start of the bar and starts the
  trade on `entry_idx + 1`).
* **Did the cost model stop charging spread?** No. `cost_price_per_trade` is
  identical across arms to 5 dp, and the spread enters through the candidate
  table's `fill` (produced by `entry_fill`), which the arms do not touch. The raw
  parquet `spread` column is never re-derived.
* **Is the arm-B stop model inventing a change?** It only ever *adds* the measured
  `Δeffective_buffer`, capped at the SWING `max_swing_sl`. Since every style
  branch multiplies `struct_sl_dist` by a factor ≤ 1, this is an **upper bound**
  on the true stop change. The bound is already ~0, so the conclusion is robust.
* **Is the "no-op" an artefact of my reconstruction?** No — the no-op comes from
  `effective_buffer` being *identical* (median 0.3500 ATR in both arms), which is
  computed directly from the production formula, not reconstructed.

---

## 5. Conclusion / recommendation

* **Ship it as a pure bug fix — it is behaviourally inert.** 46,939 (183d) and
  94,937 (365d) candidates, 3 outcome flips, ΔE[R] ≤ 6×10⁻⁵ R. It cannot break
  anything and it removes a misleading "daily vs timeframe" unit comparison.
* **It buys nothing on its own.** The docstring's "volatility-adaptive buffer" is
  not delivered by the fix, because `dynamic_buffer` never beats `anti_wick_buffer`.
  `atr_ratio` is effectively **dead code** today: it can be replaced by any
  constant in [0.33, 3.0] with no measurable effect on stops.
* **If adaptivity is actually wanted, that is a separate change and needs its own
  backtest gate.** The lever is `effective_buffer = max(dynamic_buffer, anti_wick_buffer)`
  — either raise `beta_vol`/`alpha_base` enough that `dynamic_buffer` can win, or
  replace the `max()` with a blend. That change *does* move stops, and §3 shows
  wider stops on this candidate population cost a little, so it must be measured
  on its own (and against a real edge, not this no-edge candidate book).
* **Also worth noting:** the same no-op applies to arm C. Rescaling the registry
  ATR by √24 gives a more sensible `atr_ratio` (median 1.27) and still changes
  nothing, for the same reason.

---

## 6. Production observations (reported, not fixed)

1. **`atr_ratio` is dead.** `jarvis/intelligence/dynamic_levels.py:144` computes
   it, `:151` consumes it, and `:169` discards it because `anti_wick_buffer`
   always dominates. The comment at `:150` ("Dynamic Volatility Buffer") describes
   behaviour the code does not exhibit.
2. **`typical_atr_pct` is a daily number compared against a timeframe ATR**
   (`:142` vs `symbol_registry.py:22`). Measured: the registry value is ≈3.5× the
   real H1 ATR (median ratio 0.288). The docstring is right; the use is wrong.
3. **BUY/SELL stop asymmetry.** BUY's `struct_sl_dist` (`:227`, `:229`) omits
   `spread_dist`; SELL's (`:363`, `:365`) adds it. SELL stops are systematically
   one spread wider than the mirrored BUY stop for the same structure. Small, but
   it is a real asymmetry in a "purely structural" engine.
4. **`deflated_sharpe_report.py` pooled effective-n is unreliable** (already
   known): `pooled_spans` mixes per-symbol `entry_bar` indices, whose bar ranges
   overlap, so the concurrency curve and the resulting `book_n_eff` are not
   meaningful. Per-symbol effective-n is the trustworthy number.

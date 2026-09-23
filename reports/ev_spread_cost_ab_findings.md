# EV spread-cost A/B — should `spread_cost` use the real per-bar spread?

**Tool:** `tools/ev_spread_cost_ab.py` · **JSON:** `reports/ev_spread_cost_ab.json` ·
**Per-candidate table:** `.scratch/ev_spread_cost_ab_candidates.parquet` ·
**Config:** H1, 183d, 20 symbols, 46,939 directional candidates, 3,059 bars/symbol,
`start_bar_idx=60`, `analyst_timeout=60s`, `slip=0.5 pip`, balance $10k / risk 0.5%.

---

## RECOMMENDATION — **IMMATERIAL. Do not expect a P&L change from this edit.**

Pointing the line-261 `spread_cost` at the real per-bar spread changes **nothing that
reaches a trade**. The isolated version of the proposed change (Arm **Bpure** — only
line 261 moves) produces a **bit-identical selected-trade set** to the baseline:

| A vs Bpure | value |
|---|---|
| selected trades added / dropped | **0 / 0** |
| common trades | 7,177 |
| paired ΔE[R] | **0.00000** (sd 0.0, t 0.00) |
| ΔTotal R | **+0.0** |

The reason is structural, and it is the single most important finding in this report:

> **The `ev` computed at line 261 is overwritten at line 1030 (`ev = selected_eval["ev"]`)
> before the four EXECUTE gates read it.** The gates that actually gate a trade
> (`:364`, `:411`, `:413`, `:660`) are fed by the *strategy* EV built from a **second,
> independent cost copy at line 954**. Line 261's EV only reaches the AI-dissection
> pillar (`:874`) and the master-confluence predicate (`:904`) — neither of which
> changes a single EXECUTE decision in this window.

Consequences:

* The literal instructed patch (Arm **B**) moves 2 trades (**−2.0 R**) — but that
  movement comes from the **ML "Spread Friction Ratio" feature**
  (`online_ml_predictor.py:215`), which the method-level swap also perturbs. It is
  *not* the cost term.
* The version that *does* reach the gates (Arm **D** — both cost copies real) adds
  6 trades for **+0.3 R**. Immaterial.
* If the goal is a correct cost model, the edit is **safe but inert** on this window.
  If the goal is a P&L effect, line 261 is the wrong seam — line 954 is the one the
  gates read.

**Ship/withhold:** per the standing "ship-if-immaterial" rule this edit is
**shippable** (zero measured P&L impact, one fewer place that lies about spread).
But be explicit in any changelog that it is a *correctness* change with **no measured
benefit** — and that it does **not** touch the four gates the task named.

---

## 1. What was measured

`jarvis/intelligence/decision_engine.py:261`:

```python
spread_cost = context.volatility.current_spread_pips * pip_val_per_lot * est_lots
```

On the live path `current_spread_pips` is the registry constant `spec.typical_spread_pips`
(registry understates real FX-major spread ~2.6–2.9×). The question: what happens to
selection and R if that term is fed the **real per-bar** spread?

Real spread is recovered from the `spread` column (MT5 **points**) with the canonical
conversion `pips = points * 10**-digits / pip_size`
(`signal_scan._spread_for_bar` / `market_context._live_spread_pips_from_frame`).
`spread * pip_size` is **not** used.

### Arms (identical bars, identical candidate universe)

| Arm | Definition |
|---|---|
| **A** | **Baseline** — registry constant everywhere (live path today). |
| **B** | **Literal** — instructed monkeypatch: swap `current_spread_pips`→real for the duration of `_compute_blended_probability`. Also moves the ML spread-friction feature. |
| **Bpure** | **Pure cost** — ONLY line 261 sees the real spread; ML feature pinned to registry. *This is the faithful model of the proposed edit.* |
| **D** | **Full cost** — BOTH cost copies (line 261 **and** line 954) real; gates/geometry/ML stay registry. The first arm that reaches the named gates. |
| **C** | **Full real** — real spread everywhere (gates + stops + geometry + EV + ML). Reference / sanity check. |
| **R** | **Corrected constant** — registry fed a constant = observed series median (§J2 reproduction attempt). |

---

## 2. Pooled results

| Arm | cand | ΔfinalEV | ΔblendedEV | gate flips | dec flips | selected | Total R | E[R] | t-stat | maxDD |
|---|---|---|---|---|---|---|---|---|---|---|
| **A** | 46,939 | 0 | 0 | 0 | 0 | **7,177** | **−308.493** | −0.04298 | −2.032 | 501.4 |
| **B** | 46,939 | 9,086 | 32,234 | 2 | 12 | 7,179 | −310.505 | −0.04325 | −2.045 | 501.4 |
| **Bpure** | 46,939 | 20 | 32,234 | 0 | 3 | **7,177** | **−308.493** | −0.04298 | −2.032 | 501.4 |
| **D** | 46,939 | 24,126 | 32,234 | 7 | 38 | 7,183 | −308.189 | −0.04291 | −2.030 | 501.4 |
| **C** | 46,939 | 25,131 | 33,353 | 730 | 2,728 | 6,876 | −219.887 | −0.03198 | −1.466 | 440.5 |
| **R** | 46,939 | 22,551 | 31,768 | 706 | 2,563 | 6,900 | −206.686 | −0.02995 | −1.375 | 435.0 |

**Reading:** the blended (line-261) EV moves for 32,234 candidates — but the *final*
EV survives that change for only **20** of them under Bpure. 32,214 candidates had
their line-261 EV computed and then discarded at line 1030. That single number is the
whole story.

---

## 3. Gate crossings (either direction, A→arm)

| Predicate | source of `ev` | A→Bpure | A→B | A→D | A→C | A→R |
|---|---|---|---|---|---|---|
| `:364` `not is_fx and ev>=1.5 and rr>=2.0` | final (line 954) | **0** | 2 | 12 | 344 | 289 |
| `:411` `rr>=3.0 and ev>0` | final | **0** | 0 | 0 | 527 | 455 |
| `:413` `rr>=2.0 and ev>0` | final | **0** | 2 | 12 | 841 | 751 |
| `:660` `ev>0 and ev>=effective_min_ev` | final | **1** | 39 | 162 | 169 | 165 |
| `:904` `rr>=2.0 and ev_blended>0` | **blended (line 261)** | **33** | 33 | 33 | 1,217 | 1,090 |

This table is the direct evidence for §RECOMMENDATION:

* The four predicates fed by the **final** EV (`:364/:411/:413/:660`) are essentially
  untouched by the line-261 change (Bpure: 0/0/0/1). They move only when the
  **line-954** cost moves (Arm D).
* The one predicate fed by the **blended** EV (`:904`) is the only one line 261 can
  reach — and its 33 flips (identical for B, Bpure, D) never convert into an EXECUTE
  difference, because `:904` is an *additional* OR-branch (`or is_micro_mode`) that
  does not by itself select a trade in this window.

---

## 4. Selected-set delta and paired statistics (A is the reference)

| A vs | added | dropped | common | ΔR from selection | paired n | paired ΔE[R] | sd | t | %improved | %worsened |
|---|---|---|---|---|---|---|---|---|---|---|
| **B** | 2 | 0 | 7,177 | **−2.013** | 7,177 | 0.00000 | 0.0 | 0.00 | 0.0 | 0.0 |
| **Bpure** | **0** | **0** | **7,177** | **0.000** | 7,177 | 0.00000 | 0.0 | 0.00 | 0.0 | 0.0 |
| **D** | 6 | 0 | 7,177 | +0.304 | 7,177 | 0.00000 | 0.0 | 0.00 | 0.0 | 0.0 |
| **C** | 199 | 500 | 6,677 | +61.22 | 6,677 | +0.00410 | 0.305 | +1.10 | 20.8 | 23.4 |
| **R** | 201 | 478 | 6,699 | +77.70 | 6,699 | +0.00360 | 0.304 | +0.97 | 13.2 | 17.1 |

* Paired stats are computed on the trades **both** arms selected (same bar) — for
  B/Bpure/D that is all 7,177 common trades, and they are **byte-identical** (sd 0.0).
  The A-vs-B difference is entirely in the 2 extra trades B *adds*.
* The C/R paired ΔE[R] is positive but **not significant** (|t| < 1.2) — and the
  selected-set delta dominates their total-R change (selection, not per-trade edge).

---

## 5. FX-majors breakdown

| Symbol | registry | real median | ratio | A sel | A Total R | Bpure sel | Bpure Total R | D sel | C sel | C Total R | R sel | R Total R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **EURUSD** | 0.70 | 1.90 | 2.71× | 18 | −0.765 | 18 | −0.765 | 18 | 19 | **+2.337** | 19 | +2.338 |
| **GBPUSD** | 0.90 | 2.10 | 2.33× | 652 | −164.237 | 652 | −164.237 | 652 | 407 | **−119.979** | 394 | −111.818 |
| **USDJPY** | 0.80 | 2.10 | 2.62× | 0 | 0.0 | 0 | 0.0 | 0 | 0 | 0.0 | 0 | 0.0 |
| **AUDUSD** | 0.90 | 2.30 | 2.56× | 77 | −32.480 | 77 | −32.480 | 77 | 59 | −30.682 | 59 | −30.682 |

* **Bpure is bit-identical to A on every FX major** (same selection count, same R) —
  the line-261 cost change is inert there, exactly as pooled.
* **USDJPY produces zero EXECUTE trades** in any arm (registry 0.80 vs real 2.10);
  the symbol never clears the gates in this window regardless of spread source.
* Under **Arm C** the FX majors lose *fewer* R, not more (GBPUSD −164→−120,
  AUDUSD −32→−31, EURUSD −0.8→+2.3) — the opposite of the §J2 adverse claim. See §6.

Full 20-symbol table (registry vs real median, A/Bpure/C/R selections and R) is in
`reports/ev_spread_cost_ab.json` under `per_symbol`.

---

## 6. ⚠️ Arm C SANITY CHECK — **FAILED to reproduce the known adverse result**

The task set Arm C as a reference: if it did not reproduce the adverse direction
recorded in `reports/gp11_real_spread_investigation.md` §J /
`docs/AUDIT-3-TRACKS-2026-09-23.md` §J2, the harness was to be declared wrong.
**It did not reproduce it. Reporting loudly:**

| | §J2 claim | This harness, Arm C | This harness, Arm R |
|---|---|---|---|
| EXECUTE / selected | 2,440 → **2,839 (+16%)** | 7,177 → **6,876 (−4.2%)** | 7,177 → 6,900 (−3.9%) |
| Total R | −115.0 → **−240.3 (adverse)** | −308.5 → **−219.9 (favourable)** | −308.5 → **−206.7 (favourable)** |
| EURUSD | 14 → 20 | 18 → 19 | 18 → 19 |
| GBPUSD | 383 → 648 (+69%) | 652 → **407 (−38%)** | 652 → 394 (−40%) |
| AUDUSD | 52 → 78 | 77 → **59** | 77 → 59 |

**Both spread-correction arms are FAVOURABLE here — the direction is opposite to §J2.**
Per the task's instruction this must be surfaced, not buried:

1. **The magnitude scale is off by ~3×** (7,177 selected vs §J2's 2,440 EXECUTE on the
   same 46,939-candidate universe). §J2 was therefore measured on a **different
   selection config**, not merely a different spread input. Candidates for the
   difference: a stricter EXECUTE definition / gate set, a different
   `analyst_timeout` (the 2.0 s default that degrades analysts to NEUTRAL score-50 —
   AUDIT §J3), or a different entry-point (`backtesting/engine.py` uses a scalar
   `spread_pips=2.0` and `gp11` §5 notes the two backtest entry points do **not** agree
   on spread).
2. **Arm R is the closest reproduction of §J2's literal change** ("correct the registry
   to the observed spreads") and it is **also favourable** (−206.7). So the
   discrepancy is not explained by "real per-bar vs corrected constant" — the two
   agree here.
3. **Consequence:** the §J2 adverse result is **not reproducible in this harness**.
   Either the §J2 config differs materially, or one of the two measurements is wrong.
   This must be resolved (re-run §J2's exact config and compare candidate/EXECUTE
   counts before any spread change is trusted) — it is out of scope for this tool,
   which is deliberately pinned to the `signal_scan` path.

The **A-vs-B decision is unaffected** by this discrepancy: A, B, Bpure and D are all
evaluated on the *same* context, *same* bar, *same* process, so the comparison is
internally paired and config-robust. Only the absolute totals and the C/R sanity
check carry the config caveat.

---

## 7. Determinism — verified

Two identical cross-process runs (`--symbols EURUSD --limit-bars 400 --workers 1`)
produced **byte-identical** payloads (all arms, all counters; JSON equal except the
`generated` timestamp). The harness is reproducible.

* The earlier run-to-run drift (A selected 7,161 vs 7,177) was a **config difference**
  (analyst timeout), not nondeterminism. Within-process `det_mismatch = 0` on all 20
  symbols.
* The unseeded `np.random.beta` in `learning/ensemble_bandit.py:36` and
  `learning/strategy_bandit.py:89,101` is a **red herring for this harness**: those
  methods are never called on the scan path. The only bandit method in the decision
  path is `StrategySelector.get_strategy_boosts()` (`strategy_selector.py:367`), which
  is pure UCB1 over counts/rewards — no RNG.

---

## 8. Honesty / limitations

* Read-only w.r.t. `jarvis/` and `tests/`. Every monkeypatch lives inside the tool and
  is restored in `finally`; only `reports/` and `.scratch/` are written.
* Absolute totals carry the config caveat of §6; **the A-vs-Bpure result does not**
  (it is a paired, same-bar, same-process comparison).
* Arm B's 2-trade movement is attributable to the ML feature channel, not the cost
  term — do not read it as a cost effect.
* `effective_min_ev` = 12.5 for all symbols at this balance/risk; the `:660` predicate
  is therefore rarely binding, which further mutes any EV-cost change.

## 9. Artefacts

* `tools/ev_spread_cost_ab.py` — harness (arms A/B/Bpure/D/C/R).
* `reports/ev_spread_cost_ab.json` — full payload (pooled + per-symbol).
* `.scratch/ev_spread_cost_ab_candidates.parquet` — per-candidate, per-arm table.
* `reports/ev_spread_cost_ab_findings.md` — this report.

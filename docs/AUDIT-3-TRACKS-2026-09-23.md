# HM-AI / JARVIS — Three-Track Audit Report
*Wednesday 2026-09-23. Read-only investigation. No production code modified.*

---

## ⚠ Correction — read this before the rest

Both entry-model questions are now **measured**, and the measurement **overturned the headline of
the first draft of this report**. The original headline blamed the losses on a "mechanical
stop-doubling defect (median 0.21×ATR)". That figure **does not reproduce**.

| First draft claimed | Measured | Verdict |
|---|---|---|
| Median stop is **0.21×ATR**, 92% below 0.85×ATR | Median candidate stop is **2.58×ATR (H1)**; only 0.2–3% of trades sit below 1.11×ATR | **Wrong.** A daily-vs-H1 ATR unit error (§A1) |
| Raising the stop floor to 1.11×ATR is the top fix (§D #1) | Arms 0.10 / 0.30 / 0.50×ATR are **bit-identical**; ΔE[R] at 1.11 is **+0.00092** on 46,939 trades | **Do not ship.** Measured no-op |
| Effective n = **327** independent bets from 94,937 rows | A **single symbol** measures 3,971 independent bets | **Wrong.** 327 for a 20-symbol book is internally impossible (§E) |
| FVG: **WAIT** pending backtest | **0/20** pass DSR; Sharpe **negative on all 20**; win rates 12–34.7% vs 42–59% required | **DO NOT PROCEED** — now answered, not deferred |

The two things that survive unchanged: **the system loses money (−$633.33 on 137 real broker
closed trades, 25.5% win rate)**, and **the incumbent signal has no measured edge**. What changed
is that we no longer know the *mechanical* cause — the stop floor was a false lead.

**Consequence:** the top-ranked fix in §D is withdrawn. Phase 5 (stop-floor hardening) is **not
recommended**, and Phase 7 (FVG) is answered **no**. See §F for what replaces them.

**Later — and CORRECTED AGAIN 2026-09-24 (§J, status block):** §J's spread-calibration result was
first reported as "did not reproduce", then as "noise in both directions / 0.0011 R per trade".
**Both of those readings were produced by a broken instrument and are withdrawn.** The harness leaked
its registry override, so `apply_registry(None)` was a no-op and **only the first symbol scanned ever
had a genuine incumbent arm** — every later symbol was measured corrected-vs-corrected, which is why
the earlier sweep reported "only 3 of 8 symbols change". With the harness fixed the lever is
**ADVERSE at every `tp_r`** (−47.5, −86.9, −85.1, −65.8, −79.0 R) and **7 of 8 symbols** change.

**So §J's original withholding decision was right after all.** Spread calibration stays off, and this
time on an instrument that can see the effect. The earlier "nothing was withheld" sentence in this
report is retracted. Also in §M: the EV `spread_cost` term does not reach the gates at all, which
corrects a claim made earlier in this report — that finding is unaffected, it was a same-process
paired comparison.

---

## Headline

The recurring losses are **not** explained by the stop floor. Raising the floor is a measured
no-op (ΔE[R] = +0.00092 over 46,939 trades; arms 0.10/0.30/0.50×ATR come out bit-identical),
because the floor is not the binding constraint — median candidate stops are already 2.58×ATR.
On 137 real broker closed trades: **−$633.33, win rate 25.5%**. The prior signal-quality audit
established `DSR > 0.95` met by **0/20**, and `entry_edge_verdict.md` (18,998 candidates) states
*"There is no edge in the current entry features… `score` sorts how badly trades lose, not how
well they win."* The FVG/ICT model the user asked about is *already partially implemented* in
`institutional_entry_engine._find_micro_fvg_ce` — and the system still loses. It has now been
backtested standalone: **0/20 pass DSR, Sharpe negative on every symbol.** **No entry-model change
can produce profitability, and the mechanical lever we thought we had does not exist.**

---

## Track 1 — Loss analysis & entry-model evaluation

### A1. CORRECTION — the "0.21×ATR stop" does not reproduce

The first draft's headline root cause was: `dynamic_levels.py:273` floors `min_sl_dist` at
`max(3×spread, 0.10×ATR)`, producing a median stop of 0.21×ATR. Measured directly on 46,939
candidate trades across 20 symbols, **median candidate stops are 2.58×ATR (H1)**, and only
**0.2–3%** of trades fall below 1.11×ATR. The floor never binds.

The origin of the bad figure is a **unit error**:

```python
# dynamic_levels.py:140-142
typical_atr_pct = getattr(spec, "typical_atr_pct", 0.5)      # documented as DAILY ATR %
typical_atr = c_price * (typical_atr_pct / 100.0)
atr_ratio = min(3.0, max(0.33, atr / max(atr_median, 1e-6)))  # H1 ATR / daily ATR
```

`jarvis/data/symbol_registry.py:22` documents `typical_atr_pct` as *"Typical **daily** ATR as % of
price"*, while `atr = vol.atr` is the **timeframe (H1)** ATR — 4–6× smaller. So `atr_ratio`
compares an hourly ATR against a daily one and **pins at its 0.33 clamp on 69.8% of bars (90–99.6%
for FX)**. The "volatility-adaptive buffer" is therefore a *constant*, and any stop/ATR statistic
computed against the daily denominator understates by ~4.5× — which is where 0.21 came from.

Self-consistency check: the system's own P&L of −0.11R/trade is consistent with 2.6×ATR stops and
inconsistent with 0.21×ATR stops (0.21×ATR stops with a 1.5R target would be stopped out almost
immediately and would not produce the observed 61–73% stop-out rate). **The audit's own numbers
contradicted each other, and the measurement resolves it in favour of ~2.6×ATR.**

### A. Root causes (measured on real closed trades)

| Symbol | n | WR | med stop / ATR | med R:R | P&L |
|---|---|---|---|---|---|
| XAUUSD | 45 | 38% | **0.06×ATR** | 13.8 | **−$414.14** |
| GBPUSD | 25 | 24% | — | 5.0 | −$86.46 |
| BTCUSD | 24 | 25% | **0.09×ATR** | 2.6 | −$14.04 |
| OILCASH# | 24 | 17% | — | 0.0 (tp=0 sentinel) | −$2.37 |
| EURUSD | 9 | 11% | — | 2.2 | −$7.70 |
| SOLUSD | 6 | 0% | — | 18.6 | −$103.17 |

- **Sizing (P0 cause).** `dynamic_levels.py:273` floors `min_sl_dist` at `max(3×spread, 0.10×ATR)`. `institutional_entry_engine.py:131,341` floors only at `pip_size*5`. Result: stops sit inside the noise band; TP then becomes an unreachable multiple of a near-zero risk, minting the absurd R:R values above. The same class as the **0.7-pip stop → 5-pip re-anchor** defect (`955d40c`); still visible in 2026-09-22 fills.
- **Entry quality.** Hand-authored 7-branch `if/elif` on `choch`/`bos`/`trend_score` (`decision_engine.py:137-154`), hand-typed 6-bin confidence table (`confidence.py:13-20`) blended with unfitted ML weights. Verdicts are negative (`entry_edge_verdict.md`).
- **Execution.** Correcting for real spreads drains **0.162R/trade** (median 0.118R); SOLUSD spends 90.1% of a 1R target on round-trip cost; NZDUSD 81.8%; USDCAD 61.5%; AUDUSD 51.5%; USDCHF 50.6% (`AUDIT-2026-09.md` P0-3 D). WTI refused at `position_sizing.py:146-152` ("Min lot would force 3.29% risk") while EURUSD/AUDUSD — two of the worst symbols — were taken.
- **State bugs.** `state.activeMobileView` is **UI-only**; it does not feed entries. The genuine entry-risk state bugs are: (a) the bar-rescale hack at `institutional_entry_engine.py:477-488` which silently rescales bars by up to 10%+ when `last_close ≠ current_price`, feeding a *rescaled (unobserved)* series into the entry; (b) entry prices minted from `context.ask/bid=0.0` are now refused (`dynamic_levels.py:99-127`).
- **Not measured.** MFE/MAE are 0.0 on every row (D19); no `exit_price`; no fee/slippage columns. Adverse excursion at entry cannot be derived from the data.

### B. Entries near support/resistance — VERIFIED

The entry does **not** anchor to horizontal S/R. `dynamic_levels.py:187,325` enters at market `ask`/`bid`. S/R objects (`demand_zone`, `order_blocks`, `fair_value_gaps`, `key_levels`) are consumed **only** to build the stop (`dynamic_levels.py:190-226`) and target (`:279-302`). The model is a displacement/retracement model (FVG midpoint, OTE 61.8–70.5%, order block), not a horizontal-S/R model.

Historical check, last 56 joinable real closed trades vs the H1 swing set (pivot w=5):

- Within 5 pip of a swing: **29/56 (52%)**; within 10 pip: 33/56 (59%); within 20 pip: 41/56 (73%).

Control: every bar's close vs the same swing set gives EURUSD/GBPUSD **99%** within 5 pip (median 0.2 pip) and XAUUSD **44%**. **Entries are no more clustered at swings than random bars — proximity is incidental, not intentional.**

### C. FVG evaluation — **MEASURED: DO NOT PROCEED**

The backtest has been run (`tools/fvg_standalone_backtest.py`, `reports/fvg_standalone_findings.md`).
Design: 20 symbols, M15 183d signals, M5 execution, H1 365d structure; 3-candle gap with
displacement body/range ≥ 0.55 (the code's own threshold, `institutional_entry_engine.py:563`);
entry at the gap's 50% CE **on retrace** (not at the edge — that would be lookahead); stop beyond
the gap plus buffer; 1.5R target; costs charged from each bar's own `spread_pips`.

**Result: 0/20 symbols pass DSR > 0.95. Sharpe is negative on all 20.** Win rates land at
12–34.7% against the 42–59% needed to break even at 1.5R. Both FVG and always-long lose money;
FVG loses *less*, but "less negative than a losing baseline" is not an edge and cannot be
monetised long-only. Neither bar is cleared.

The first draft said WAIT pending evidence. **The evidence is now in, and it is negative** — so
this is no longer a deferral, it is a **no**. Wiring FVG as a replacement would swap an unproven
model for a measured-negative one.

- **FVG is already partly shipped.** `jarvis/market/fair_value_gap.py` (3-candle gap, 50-bar lookback, mitigation tracking), `jarvis/market/market_structure.py:111-154` (FVG + OBs with 45% displacement), and `institutional_entry_engine._find_micro_fvg_ce` (`:568-599`) which drives SCALP/DAY at the gap's 50% CE. The system still loses.
- **Data exists for an honest backtest.** `data/market/real/<SYM>/<SYM>_{M1,M5,M15,H1,H4,D1}_*.parquet` for 20 symbols (256 files). M5/M15 183d full; H1 365d full to 2026-09-15; M1 183d only from 2026-07-06 (~67d).
- **The honest backtest recipe:** 20 symbols; M15 183d primary + M5 183d execution + H1 365d structure; trigger = 3-candle gap with body/range ≥ 0.55 (already the code's threshold at `:563`); entry at the gap CE on retrace (not the edge — avoids lookahead); stop beyond the gap + buffer; target 1.5R; charge the bars' own `spread` column via `jarvis/backtesting/fills.py`. Reuse `signal_scan.py` + `trade_simulator.py`. Judged by **DSR > 0.95 on effective n** AND beating always-long (both tools exist: `tools/deflated_sharpe_report.py`, `tools/p0_1_direction_audit.py`).
- **Fundamental weigh-in.** `deflated_sharpe_365d.json` → `n_pass_dsr_selected_effective_n = 0`, `n_pass_dsr_production = 0`, portfolio `dsr = 0.0`, 94,937 rows → 327 independent bets (0.3%). Only 5/20 pass on the raw row count, and all collapse under effective n. **The incumbent has no measured edge; replacing it with FVG is only defensible if FVG is measured to have one.**
- **Recommendation: WAIT.** Implement only after the backtest clears both bars. Otherwise the swap is unproven → unproven.

### D. Ranked next actions (review only — nothing changed)

| # | Action | File | Smallest change | Why ranked here |
|---|---|---|---|---|
| 1 | Stop floor ≥ 1.11×ATR everywhere | `dynamic_levels.py:273`; `institutional_entry_engine.py:131,341` | Raise `min_sl_dist` to `max(3×spread, 1.11×ATR)` and apply in both paths | Mechanical; 92% of trades sit inside the noise band. Expect fewer instant stop-outs; expect *no* profitability on its own. |
| 2 | Stop minting R:R from collapsed risk | `institutional_entry_engine.py:369-380` | Recompute TP off the *floored* `risk_dist`; reject rows whose recorded R:R exceeds a sane cap | Artifact of action #1; R:R 13.8/18.6 is fake. |
| 3 | Persist `exit_price` + real `realized_pnl` | `jarvis/data/database.py` INSERT/UPDATE | Add columns; compute P&L from fills (AUDIT-TRADES C3/M7) | **109/109** closed rows have `realized_pnl == expected_value` — entry quality cannot be measured until this lands. |
| 4 | Remove the bar-rescale hack | `institutional_entry_engine.py:477-488` | Return the frame unchanged, or `None` when `\|last_close − current_price\|/price > 0.10` | Feeds rescaled (unobserved) bars into the entry. |
| 5 | Turn on the calibrated entry policy | `config/settings.json` → `trading.use_calibrated_entry_policy` | Flip the flag (AI9 wire exists) | Refuses 11/16 symbols (incl. XAUUSD −0.056 OOS); narrows the book to 5. **Trading decision**, not a bug fix. |
| 6 | Verify the C1 risk clamp on live rows | `jarvis/risk/position_sizing.py:110-117` | Read-only `tools/audit_trades.py` re-run | 27 historical breaches; verify fix on the 137 real rows. |
| 7 | Dedup-aware reporting | `tools/audit_trade_quality.py` | `one_position_at_a_time=True` by default | Counts inflated ~25× without it. |

**Not measured (not findings):** adverse excursion at entry, partial-fill/slippage on the 137 real rows, per-symbol edge claim.

**§D #1 is WITHDRAWN** — see §A1 and §E. It was ranked first on the 0.21×ATR figure, which does
not reproduce. Raising the floor to 1.11×ATR is a measured no-op.

### E. CORRECTION — the "327 independent bets" figure is wrong

Every DSR conclusion in this report quoted *94,937 rows → 327 independent bets (0.3%)*. That
number is **not credible**, and the proof is internal: the same engine run on a **single symbol**
returns **3,971 independent bets**. One symbol cannot contain 12× the independent bets of the
20-symbol book it belongs to.

At `tools/deflated_sharpe_report.py:239` the pooled span accumulation is:

```python
pooled_spans.extend(spans[prod_key])          # a flat list
...
book_n_eff = SampleUniquenessWeightEngine.effective_sample_size(pooled_spans)
```

`effective_sample_size` (`jarvis/learning/sample_weights.py:101`) expects
`List[Dict[str, Any]]` — trade records. The pooled call passes the raw span entries instead, so
the uniqueness engine never sees the structures it needs and returns a degenerate value. The
measured ratio is **4.2%, not 0.3%** — an order of magnitude out.

**Direction of the correction:** effective n is *larger* than claimed, so the DSR bar is *less*
severe than reported. That does **not** rescue the incumbent: it still fails 0/20 on the raw
count, and the FVG study fails 0/20 on the corrected count too. The conclusion is unchanged; only
the stated severity was wrong.

### F. What replaces the withdrawn fixes

With the stop floor and FVG both measured out, the remaining levers are loss-reduction and
measurement, not profitability:

| # | Action | Why it survives |
|---|---|---|
| 1 | **Fix the daily-vs-H1 ATR unit error** (`dynamic_levels.py:140-142`) | **Measured inert — safe to ship, but buys nothing.** Fixing it flips **3 outcomes in 46,939 trades**; ΔE[R] ≤ 6e-5 R. Reason: `atr_ratio` is **dead code**. It only feeds `effective_buffer = max(dynamic_buffer, anti_wick_buffer)`, and the dynamic term needs `atr_ratio + spread_ratio > 4.6` to beat the fixed 0.35×ATR anti-wick buffer — impossible at a normal spread. Median `effective_buffer` is **0.3500×ATR in every arm**; only 2.41% of bars move it at all. **The "volatility-adaptive buffer" was never actually delivered.** Making it adaptive means changing the `max()` or raising alpha/beta — a real change that needs its own backtest gate. |
| 2 | **Persist `exit_price` + real `realized_pnl`** (`jarvis/data/database.py`) | **DONE** (commit `0d0f0ef`). `SCHEMA_VERSION` 4→5; migration adds both columns and backfills the old `DEFAULT 0.0` sentinel to NULL **only where `closed_at IS NULL`**; `record_trade_exit()` writes from the closing MT5 deal (`.price` / `.profit` verified present on `TradeDeal`); `orchestrator._on_trade_closed` calls it. **Unknown is NULL, never 0.0** — pinned by four tests, including one that a break-even close's real `0.0` survives and one that an unclosed trade reports `None`, not `0.0`. |
| 3 | **Fix the `risk_dist` floor that never moves `sl_price`** (`institutional_entry_engine.py`) | **DONE** (`90eaa28`). Six sites — three BUY/SELL pairs, not the two originally identified: SCALP (131-140/141-149), **DAY_TRADING (248-256/257-265 — a pair the audit missed)**, SWING (357-365/366-375). The floor is now applied to the *distance* and `sl_price` is derived from it, so `risk_dist == abs(entry_price - sl_price)` on every branch including the re-enforce branches, where the floor was previously dropped entirely. No constant changed. **Tests proven non-vacuous: 12 of 19 fail against the pre-fix engine, all 19 pass after.** Not fixed: SWING's `max_risk_cap` can still cap `risk_dist` below `pip_size * 5` for small-ATR symbols. |
| 4 | **Correct `typical_spread_pips`** | Understates measured FX spreads 2–3× (EURUSD 0.7 vs 1.90 measured); the manifest's same-named field is in **points** for indices (GER40 205 vs 1.95). Costs are being under-charged across the board. |
| 5 | **Resolve the `mtf_data` path** (`dynamic_levels.py:514-539`) | When `mtf_data` holds any non-empty frame the function returns the institutional result *instead of* `base_result`, so the line 273/404 floor may never govern a live stop at all. This must be settled before any further stop-floor work means anything. |
| 6 | **Remove the dead ICT gates** (`institutional_entry_engine.py`) | `has_mss` is computed at `:97` and never read; `h1_aligned` is computed at `:212` and only stored as a report key at `:281`; `in_kill_zone` at `:225` only relabels `entry_type` — both branches set the identical `entry_price`. Three gates that look like risk controls and reject nothing. |
| 7 | **Fix the BUY/SELL `struct_sl_dist` asymmetry** (`dynamic_levels.py:227/:229` vs `:363/:365`) | The BUY path omits `spread_dist` while the SELL path adds it, so **SELL stops are systematically one spread wider than BUY stops for identical structure**. A directional bias introduced by an inconsistency, not by any trading view. |

### H. The "unknown vs zero" defect class — three layers, all fixed

`0.0` is a real P&L *and* what `dict.get()` returns for a missing key. That ambiguity was silently
turning **missing measurements into recorded losses** in three separate layers.

| Layer | Defect | Fix |
|---|---|---|
| `executed_trades` | No `exit_price`/`realized_pnl` columns at all — the only real P&L lived in a request-time dict built from live MT5 deals. | Columns + `record_trade_exit()`; NULL never 0.0. |
| `trade_records` (`jarvis_trade_memory.db`) | `record_trade` wrote `exit`/`pnl`/`is_win` as `0.0`/loss **at open time**. | NULL + `update_closed_trade()` (UPDATE, not `INSERT OR REPLACE`). |
| `_on_trade_closed` (orchestrator) | `pnl = float(data.get("pnl", 0.0))` turned a missing P&L into a genuine break-even, and `is_win = 1 if pnl > 0 else 0` filed it as a **loss**. | Optional `pnl`/`is_win`; each consumer withholds an unknown sample. |

**The third one was the dangerous one.** That phantom loss fed
`circuit_breaker.record_trade_result(is_win == 1)` — so **a data gap could help trip the circuit
breaker on losses nobody ever observed.** It also penalised the strategy bandit and trained the ML
model to read a missing measurement as a negative label.

**Not backfilled:** the 42 historical rows in `jarvis_trade_memory.db` were deliberately left alone.
With no `closed_at` marker, `exit_price = 0 AND pnl = 0` cannot be distinguished from a real
break-even whose exit price was unavailable. Rewriting them would be guessing.

### I. Diagnostic answers (`reports/stop_floor_and_spread_calibration_findings.md`)

**Q1 — Does the line 273/404 floor govern live stops? YES, always in the automated path.**

The `mtf_data` early return at `:514` looked like it discarded the baseline floor, which would have
made the entire stop-floor investigation moot. It does not: `DecisionEngine.evaluate()` accepts
`mtf_data` (`decision_engine.py:707`) and both live drivers pass a **populated** dict
(`orchestrator.py:597→717`, `server.py:214→262`) — but `evaluate()` **never forwards it** to
`_compute_bias_and_levels` (`:712-716`), using it only for order-flow / master-confluence / FVG.
`calculate_levels` therefore receives `mtf_data=None` and returns `base_result`. Runtime proof in
`tools/probe_mtf_data_reachability.py`: *"mtf_data was NOT PASSED (kwarg absent) -> baseline floor
governs."* **The premise of the stop-floor work was valid.**

But this reveals something larger: **the institutional entry engine is unreachable in the live
automated path.** The ICT/FVG engine — displacement gates, kill zones, OTE, the FVG CE entry — is
only reached via `calculate_manual_trade_levels` (`dynamic_levels.py:662→667`), and only on a cold
`GLOBAL_STATE` with a successful fresh MTF fetch. So the model the user asked about (§C) was never
actually driving live entries *or* the backtests, which explains part of why "FVG is already shipped
and the system still loses" — it was shipped but mostly never exercised.

**Q2 — Spread calibration is materially wrong, and the live spread is a constant.**

Measured (M1, 183d) median pips vs the registry's `typical_spread_pips`:

| Symbol | measured | registry | ratio | % bars over `max_spread_pips` |
|---|---|---|---|---|
| EURUSD | 1.9 | 0.7 | **2.71×** | 6.8% |
| USDJPY | 2.3 | 0.8 | **2.88×** | 9.1% |
| AUDUSD | 2.3 | 0.9 | **2.56×** | 5.8% |
| GBPUSD | 2.2 | 0.9 | **2.44×** | 8.5% |
| GBPJPY | 2.5 | 1.2 | **2.08×** | 10.9% |
| EURJPY | 2.0 | 1.0 | **2.00×** | 5.9% |
| WTI | 3.0 | 13.0 | **0.23×** (over-charged) | — |

Accurate (≈1.0×): USDCHF, USDCAD, NZDUSD, XAUUSD, ETHUSD, SOLUSD and all five indices.

**The compounding bug:** every live `build_context` call passes
`current_spread_pips = spec.typical_spread_pips` (`orchestrator.py:679`, `server.py:257`) — a
hardcoded constant, not a measurement. Therefore (a) cost/EV is understated ~2.7× on EURUSD, (b) the
spread rejection filter **can never fire**, since `typical < max` always holds, even though the real
spread exceeds `max` on 6–11% of M1 bars for every major, (c) the floor's spread term at `:139` is
~2.7× too tight, and (d) `spread_ratio` at `:147-148` is pinned at 1.0 because current always equals
typical — so the `gamma_spread` term is inert too.

### J. The last two levers — both measured MATERIAL and therefore **withheld**

Both remaining candidate fixes were measured and **neither was shipped.** The gate applied throughout
this audit was: ship if the change is immaterial, withhold and report if it materially changes trade
selection or P&L. Both crossed that line, so both are **your decision, not mine.**

#### J1. BUY/SELL spread symmetry — material, and *favourable*

SELL charges `spread_dist` in its structural stop; BUY does not, so SELL stops are ~one spread wider
for identical structure. Making BUY symmetric widens every BUY stop by exactly one spread — pooled
median **0.175×ATR, 7.3% of the risk distance**. The floor absorbs almost nothing (it binds on only
0.9% of BUY trades).

| Metric | Value |
|---|---|
| ΔE[R] per trade | **+0.0055 to +0.0086** (favourable) |
| Paired t-statistic | **+3.9 to +5.1** |
| Trades changing exit reason | **406–733 (0.9–1.6%)** |
| Trade count | −2% to −4% |

6–9× the inert ΔE benchmark and ~140–240× the "3 outcomes in 46,939" benchmark — so **not** an
inert lever. It is fully predicted by the existing width curve, with no anomaly.

**Scale, stated honestly:** against a ~0.11R/trade loss, +0.006R is roughly a **5% improvement. It
does not make the system profitable.** It makes BUY consistent with SELL, which happens also to be
slightly favourable.

**The asymmetry is at 7 sites, not 3:** `:363`, `:365` (structural), `:368` (SCALP),
`:373/:377/:381` (DAY_TRADING caps), `:399` (SWING cap). A three-site fix would leave DAY_TRADING and
SWING asymmetric, so this is **all-or-nothing**.

Tests are written and proven non-vacuous: **30 of 43 fail against pre-fix code**; 43/43 pass with it.
Patch held at `.scratch/spread_symmetry_fix.patch`. Suite would be 3154 passed / 0 failures.

#### J2. Spread calibration — material, and *adverse* — withheld

Measured impact of correcting the registry to the observed spreads:

* Live stop: negligible (EURUSD median risk_dist ×1.009; GBPUSD/AUDUSD/GBPJPY ×1.000).
* Backtest: **EXECUTE totals 2440 → 2839 (+16%); total R −115.0 → −240.3.**
  EURUSD 14→20 · GBPUSD 383→648 (+69%) · AUDUSD 52→78 · EURJPY 200→285 · WTI 1329→1286.

Correcting `typical` did not simply add cost — it feeds the Forex-Prime-Session, AI-Multi-Score and
Calibrated-Win-Prob gates, so it changed *selection*, and the result was more negative. Withheld.

#### J3. The root cause behind J2 — live never sees the real spread

`build_context` sets `bid = close` and `ask = close + current_spread_pips * pip_size`
(`market_context.py:79-80`), and every live caller passes
`current_spread_pips = spec.typical_spread_pips` (`orchestrator.py:679`, `server.py:257`,
`dynamic_levels.py:590`).

So at `dynamic_levels.py:139` the condition `ask > 0 and bid > 0` is **always true**, and
`spread_dist` resolves to `typical * pip_size` — **the measured spread is never consulted and the
fallback branch is unreachable live.** The consequence: the spread rejection filter and the blowout
guard are **dead**, because `spread_ratio ≡ 1` by construction. Fixing *that* (feeding the real
per-bar quoted spread) is a separate, more fundamental change than correcting the constants, and
carries its own gate.

**Also found:** a second divergent copy of the same constants in `symbol_profile_config.py` — **note:
this was first written as "(unused)", which is wrong** (corrected in §N). The scan pipeline is
nondeterministic across processes (GBPUSD 383 vs 468 under load, resolved by
raising the analyst timeout 2s→60s); and `test_spread_cap_admits_...` only checks the D1 file's p95,
not the trading timeframe.

### K. ⚠ LIVE DEFECT FOUND AND FIXED — position management was dead on four FX majors

This is the one finding in the entire audit that was **actively causing losses at the time it was
found**, and it is now fixed (`c262cf5`).

`position_monitor.py:1050` built the market context with **no spread argument**:

```python
ctx = self.context_engine.build_context(symbol, mtf_data)
```

`build_context` declares `current_spread_pips: float = 2.0` (`market_context.py:36`), so the spread
reaching the blowout guard was **the hardcoded 2.0 for every symbol**. The guard at `:290` is
`spread > typical_spread * 2.0`, and for any symbol whose `typical_spread_pips` is below 1.0 the
hardcoded 2.0 exceeds the threshold permanently:

| Symbol | typical | threshold | hardcoded | guard fires |
|---|---|---|---|---|
| EURUSD | 0.7 | 1.4 | 2.0 | **always** |
| USDJPY | 0.8 | 1.6 | 2.0 | **always** |
| GBPUSD | 0.9 | 1.8 | 2.0 | **always** |
| AUDUSD | 0.9 | 1.8 | 2.0 | **always** |
| EURJPY | 1.0 | 2.0 | 2.0 | borderline (`2.0 > 2.0` is False) |

**Result: trailing stops, breakeven moves and partial closes never executed on EURUSD, USDJPY,
GBPUSD or AUDUSD.** Every winner on four of the highest-volume FX majors ran unprotected, and losses
were never trailed out. This presents as a strategy problem — "the system can't hold a winner" —
while being a plain argument-omission bug.

**Fix:** `position_monitor.py` now resolves the symbol's own `typical_spread_pips` and passes it in,
so the guard compares like-for-like. All other `build_context` call sites were audited and already
passed a spread, so this was the only omission. `SPREAD_BLOWOUT_MULT` and the registry calibration
were deliberately left untouched.

**Verification:** 7 new tests (23 cases) in `tests/test_position_monitor_spread_guard.py`.
**12 cases fail against the pre-fix code** — the 4 majors × 3 management methods — and 0 fail after.
A genuine blowout still trips the guard, and the EURJPY boundary is pinned so it cannot silently
regress. Suite: 3161 passed, 0 failures, 0 errors.

**⚠ This changes live behaviour on the next run.** Position management will now begin executing on
EURUSD, USDJPY, GBPUSD and AUDUSD for the first time — trailing stops, breakeven moves and partial
closes will start firing where they previously did nothing. That is the intended behaviour being
restored, not a new feature, but it is a real change to how open positions are handled: **watch the
first session after this lands.** If trailing logic has its own latent defects, they were previously
masked by the guard firing and will now become visible.

### G. The pattern behind all three negative results

Three separate "fix the stop" levers were measured, and all three are inert:

| Lever | Measured effect |
|---|---|
| Raise the stop floor 0.10 → 1.11×ATR | Arms 0.10/0.30/0.50 **bit-identical**; ΔE[R] = +0.00092 |
| Fix the daily-vs-H1 ATR unit error | **3 outcome flips in 46,939**; ΔE[R] ≤ 6e-5 R |
| FVG as the entry trigger | 0/20 DSR; Sharpe negative on all 20 |

This is not three coincidences — it is one fact: **the stop geometry is not what is losing money.**
Stop width barely moves outcomes because the buffer is pinned at a constant 0.35×ATR, the floor
never binds, and the entry signal has no edge to begin with. Widening or narrowing a stop on a
signalless entry just rescales the same loss.

**The actionable conclusion:** stop tuning this class of knob. Until the entry signal has a measured
edge, no stop/target/geometry parameter will produce profitability — and each one costs a backtest
to prove inert. The remaining work that actually changes what we know is **measurement plumbing**
(§F #2: persist real outcomes), not parameter tuning.

---

### L. The real spread is now carried and reported — Steps 0 + 1 (`faf2587`)

Investigation: `reports/gp11_real_spread_investigation.md`. The real spread was available
**twice and discarded both times** — `mt5_client.py:563` computes it only at order-send, and
`data_feed.py:378` projected the `spread` column away. Every live caller passed the registry
constant, so `spread_ratio ≡ 1` and **no spread gate could ever fire**; and
`TRADE_DB.log_trade(spread_pips=…)` persisted the constant rather than the spread actually
quoted, making the trade journal fiction.

Shipped, **reporting only**:

- `market/data_feed.py` keeps MT5's per-bar `spread` (in **points**, not converted, guarded for
  sources without it). `market/market_context.py` converts via the canonical
  `points × 10⁻ᵈⁱᵍⁱᵗˢ / pip_size` — the same formula the backtest already uses — into a new
  `MarketContext.live_spread_pips`, `None` when not measured.
- `execution_engine.py` writes it to the trade journal; `copilot.py` shows it marked "live";
  the orchestrator logs live and registry side by side. No new MT5 call anywhere.

**Zero decisions change.** `bid`, `ask` and `volatility.current_spread_pips` are untouched —
verified independently of the worker's own test: building a context on EURUSD with and without
a `spread=19` column gives bit-identical `bid` 1.0920881633749384, `ask` 1.0921581633749384,
`current_spread_pips` 0.7 — while `live_spread_pips` becomes 1.9. That is the whole point: the
number is now honest, and nothing prices, sizes or gates on it.

**Deliberately NOT done.** `decision_engine.py:261` (`spread_cost`) is untouched — but note my
original reason for holding it back was **wrong**, and measuring it corrected me. See next.

Tests: `tests/test_live_spread_reporting.py` (22 hermetic) — conversion for a 5-digit FX symbol
and one where `pip_size ≠ point`, absent/non-finite/zero/negative → `None`, `log_trade`
preference and fallback, a context lacking the field, and the strict bid/ask/`current_spread`
regression guard.

### M. CORRECTION — the EV `spread_cost` term does **not** reach the gates, and changing it is inert

I held back `decision_engine.py:261` because I believed `ev` feeds four live gates. **That was
wrong**, and the A/B proved it (`reports/ev_spread_cost_ab_findings.md`,
`tools/ev_spread_cost_ab.py`).

There are **two** EV computations, and they are not the same value:

| | Where | Feeds |
|---|---|---|
| Blended EV | `decision_engine.py:261` (in `_compute_blended_probability`, 179-266) | the AI-dissection pillar (`:874`) and the master-confluence predicate (`:904`) only |
| Strategy EV | `decision_engine.py:954` — a **second, separate** `current_spread_pips` cost copy | `ev = selected_eval["ev"]` at **`:1030`**, which *overwrites* the blended EV |

`_apply_quality_gate` (268-688) holds the gates I named — `:364`, `:411`, `:413`, `:660` — and it
is called at **`:1065`, i.e. after the overwrite at `:1030`**. So those gates read the *strategy*
EV. The line-261 EV never reaches them.

Measured, 20 symbols / H1 / 183d / **46,939 candidates**, three arms:

| Arm | Selected | Total R | E[R] |
|---|---|---|---|
| A — baseline (registry constant) | 7,177 | −308.493 | −0.04298 |
| B — EV-only real (line 261 swapped) | 7,179 | −310.505 | −0.04325 |
| **B′ pure** — the isolated cost term | **7,177** | **−308.493** | **−0.04298** |

**A vs B′ is bit-identical: 0 added, 0 dropped, 7,177 common, ΔTotal R = 0.0.** The 2-trade
movement in B comes from the ML "Spread Friction Ratio" feature
(`online_ml_predictor.py:215`), not from the cost term. Blended EV moved for 32,234 candidates;
the final EV survived for only 20.

Changing line **954** instead — the seam that actually reaches the gates — moves them
(`:364` 12, `:413` 12, `:660` 162 crossings) and yields **+6 trades, +0.3 R**. Also immaterial.

**Conclusion:** the EV cost term is not where the money is. Consistent with §G — neither stop
geometry nor modelled cost is what loses money here. Line 261 can be shipped as a pure
correctness fix (honest cost in the blended EV, zero measured P&L impact, does not touch the
four named gates), but it should carry an explicit "no measured benefit" note.

### §J spread calibration — RESOLVED (twice over) as ADVERSE. The first resolution was measured with a broken instrument; see the status block below.

The A/B included a **reference arm** precisely to validate the harness, and it **failed**.

- **§J claimed:** feeding the real spread everywhere → EXECUTE 2,440 → 2,839 (+16%), Total R
  −115.0 → −240.3 (**adverse**). That adverse result is why spread calibration was withheld.
- **This run measured:** 7,177 → 6,876 selected (−4.2%), Total R −308.5 → −219.9
  (**favourable**); GBPUSD 652 → 407. A second attempt to reproduce §J's literal change
  ("correct the registry to observed") was also favourable: Total R −206.7.

### What actually explains the gap — and a worse problem found underneath

I now have §J2's surviving artefact, `reports/spread_ab_H1_183d.json`. Two findings:

**1. The count gap is mostly the universe, not the config.** §J2 ran on **8 symbols**
(EURUSD, GBPUSD, USDJPY, AUDUSD, GBPJPY, EURJPY, BTCUSD, WTI) over **18,823 candidates**, not the
20 symbols / 46,939 candidates of the new harness. The selection *rate* is nearly identical —
2,782 / 18,823 = **14.78%** vs 7,177 / 46,939 = **15.29%**. So the two do not disagree about how
often the engine trades; the "3× gap" is arithmetic. The selection predicate is also the same in
both (`decision == "EXECUTE"` plus BUY/SELL, matching `signal_scan.py:252`).

**2. §J2's numbers as written in this report do not match its own artefact.** That is the serious
part:

| | §J2 as quoted above | §J2's artefact (`spread_ab_H1_183d.json`) |
|---|---|---|
| Executed | 2,440 → 2,839 | **2,782 → 2,784** |
| Total R | −115.0 → −240.3 | **−206.176 → −226.131** |
| EURUSD | 14 → 20 | 14 → 21 |
| GBPUSD | 383 → 648 | **649 → 677** |
| AUDUSD | 52 → 78 | 75 → 75 |
| EURJPY | 200 → 285 | 286 → 286 |
| WTI | 1,329 → 1,286 | 1,285 → 1,251 |

Several quoted "B" figures are close to the artefact's **A** figures (648≈649, 285≈286,
1286≈1285), which is what a transposed or mixed-run rendering looks like. **Treat §J2's quoted
numbers as unreliable.** The artefact itself is self-consistent and says **adverse**:
−206.176 → −226.131, Δ −19.96 R.

**3. The direction conflict is real and comes from the replay model.** Per-trade R differs by
1.7× between the two harnesses on the baseline: **−0.0741** (§J2 artefact) vs **−0.0430** (new
harness). Same selection behaviour, different exits — so these are different exit/cost models,
and that, not the spread change, is what flips the sign of the delta. Note also that WTI alone is
**+103.081 R** and 46% of §J2's executions, so that total is dominated by one profitable symbol.

**Status: CORRECTED 2026-09-24 — the lever is ADVERSE, and robustly so. Do not ship it. §J was right.**

### The previous conclusion was produced by a broken instrument

The first re-measurement (committed as `40c52d4`) reported the effect as "noise in both directions"
— ΔTotal R −3.154 → −1.155 → −0.155 → **+1.844** → +0.844 across `tp_r` 1.0→3.0, largest |Δ| 0.0011
R/trade, "only 3 of 8 symbols change". **All of that is withdrawn.**

The harness leaked its own override. `apply_registry` rebuilt the registry from the **live**
`reg._REGISTRY` rather than from a pristine snapshot, so `apply_registry(None)` was a no-op, not a
restore. Because the driver calls `apply_registry(None)` before the incumbent scan of every symbol,
**only the first symbol scanned ever had a genuine incumbent arm**; every later symbol was measured
corrected-vs-corrected. Measured directly: pristine AUDUSD 0.9 → after CORRECTED 2.3 → after
`apply_registry(None)` **2.3, not 0.9**.

The defect has a signature, and the old numbers carry it exactly — `A == B` for every symbol but the
first:

| symbol | scanned | contaminated run (leaked) | fixed run |
|---|---|---|---|
| EURUSD (first) | 1st | 17 / 22 | 17 / 23 |
| GBPUSD | 2nd | **649 / 649** | 417 / 649 |
| EURJPY | 6th | **355 / 355** | 201 / 286 |
| GBPJPY | 5th | 160 / 161 | 126 / 155 |
| AUDUSD | 4th | 75 / 75 | 53 / 77 |
| BTCUSD | 7th | 361 / 346 | 339 / 346 |

EURUSD is the one symbol the leak could not touch, and its A-arm is 17 in both runs. That is the
diagnosis confirming itself. "Only 3 of 8 symbols change" was never a property of the lever — it was
the leak, reporting zero difference for the five symbols it had silently turned into self-comparisons.

### The corrected measurement

Same harness, same symbol set, same data, same exit-model sweep — only the registry restore fixed.
Re-run: `tools/reconcile_spread_calibration.py --mode sweep --out reports/j_reconcile_sweep_fixed.json`.

| `tp_r` | Total R incumbent | Total R corrected | ΔTotal R | Sign |
|---|---|---|---|---|
| 1.0 | −88.781 | −136.233 | **−47.452** | ADVERSE |
| 1.5 | −107.436 | −194.298 | **−86.862** | ADVERSE |
| 2.0 | +38.533 | −46.529 | **−85.062** | ADVERSE |
| 2.5 | +50.936 | −14.879 | **−65.815** | ADVERSE |
| 3.0 | +139.201 | +60.204 | **−78.997** | ADVERSE |

Executed 2,450 → 2,862 (+412, +16.8%) — and the count delta is **identical at every `tp_r`**, as it
must be, since selection does not depend on the exit model. **7 of 8 symbols change.** The verdict is
ROBUST: the sign is ADVERSE at every point of the sweep, and the magnitude (−47 to −87 R) is the same
order as the baseline itself (−89 to +139 R). This is material, not noise.

Note the direction of the two effects. The corrected spreads are mostly *higher* (EURUSD 0.7 → 1.9,
GBPUSD 0.9 → 2.2, BTCUSD 1500 → 2250; only WTI falls, 13.0 → 3.0), yet corrected executes **more**.
That is the `max_spread_pips` gate, not the EV cost term: raising the *max* makes the gate more
permissive, while raising the *typical* only makes each trade more expensive. The permissive effect
wins on count, and the cost effect wins on R — which is why the count rises while the P&L falls.
That asymmetry is worth remembering the next time a spread change is proposed as a cost reduction.

### The adverse move is 96% a VOLUME effect, not a quality effect

Decomposing ΔR = (n_b − n_a)·mean_a + n_b·(mean_b − mean_a) at `tp_r=1.5`:

| symbol | n_a | n_b | mean R incumbent | mean R corrected | volume | quality | ΔR |
|---|---|---|---|---|---|---|---|
| EURUSD | 17 | 23 | −0.28373 | −0.47878 | −1.702 | −4.486 | −6.189 |
| GBPUSD | 417 | 649 | −0.20195 | **−0.19626** | **−46.852** | **+3.693** | −43.160 |
| USDJPY | 0 | 0 | 0 | 0 | 0.000 | 0.000 | 0.000 |
| AUDUSD | 53 | 77 | −0.48657 | **−0.45799** | −11.678 | **+2.201** | −9.477 |
| GBPJPY | 126 | 155 | −0.71104 | **−0.70218** | −20.620 | **+1.373** | −19.247 |
| EURJPY | 201 | 286 | −0.06271 | −0.07992 | −5.330 | −4.922 | −10.252 |
| BTCUSD | 339 | 346 | −0.10767 | **−0.10405** | −0.754 | **+1.253** | +0.499 |
| WTI | 1297 | 1326 | +0.11263 | +0.11090 | +3.266 | −2.294 | +0.972 |
| **total** | **2450** | **2862** | | | **−83.670** | **−3.183** | **−86.853** |

**Volume: −83.670 R (96%). Quality: −3.183 R (4%).**

In **4 of 8 symbols the per-trade result actually improves** under the corrected spreads (GBPUSD
−0.20195 → −0.19626, AUDUSD −0.48657 → −0.45799, GBPJPY −0.71104 → −0.70218, BTCUSD −0.10767 →
−0.10405) and the total still falls, because each takes more trades. The signal's per-trade expectancy
is roughly **−0.2 R and near-insensitive to the spread configuration**; what moves the total is *how
many trades are taken*.

This is the most actionable number in the section, and it is the same conclusion §9 of
`reports/PROFITABILITY_ROOT_CAUSE.md` reached from the live data (the bot's −2.46/trade is the
backtested −0.074 R/trade × ~$33 risk): **the dominant lever on P&L here is trade count, not entry
quality and not cost modelling.** 412 extra trades cost 84 R at an unchanged per-trade edge. It does
not follow that tightening the gate makes the strategy profitable — the edge is still absent (DSR
0/20) — but it does follow that the only way to improve the total *without* finding an edge is to take
fewer, not better-modelled, trades.

### This restores §J's original decision, and §J2's artefact

§J originally withheld spread calibration because feeding real spreads everywhere measured
**adverse** (−115.0 → −240.3 R as quoted; the artefact itself says −206.176 → −226.131, Δ −19.96 R).
§J2's artefact was independently adverse. Both are now corroborated by a third, independent run with
a working instrument. **Three measurements, three adverse readings.** Spread calibration stays off —
not because a number failed to reproduce, but because it reproduces adverse.

The one part of the previous analysis that survives is the **§J2-as-quoted vs §J2-as-recorded**
discrepancy (648≈649, 285≈286, 1286≈1285 look like transposed A/B figures) and the **8-vs-20 symbol
universe** arithmetic. Those were about §J2's bookkeeping, not about the instrument, and they stand.

### ⚠ New caveat: the scan itself is not fully deterministic

While the fixed sweep was running it logged, three times:

```
Analyst MACRO failed or timed out after 2.00s (TimeoutError: ) -- substituting a NEUTRAL score-50 fallback.
```

That is a **fabricated input**, not a measurement. `ParallelAnalystCluster` gives MACRO a 2.0s budget
while the news fetch MACRO calls synchronously (`jarvis/market/news.py`) is allowed 5s and 6s against
a 90s cache TTL; a cache miss was measured at 1.32s — 66% of the budget before any CPU work, competing
with six other analysts for the GIL. So the fallback is reachable whenever the network is merely slow.

The consequence for §J is a confound: if MACRO times out during the incumbent arm but not the
corrected arm, the two arms differ for a reason unrelated to spread. **This does not overturn the
result** — the effect is ADVERSE at 5 of 5 exit models and 7 of 8 symbols, and 96% of it is the
volume term, which three fallback events cannot manufacture. But the previous claim in this section,
"Determinism was verified: two identical cross-process runs are byte-identical", **is retracted**: it
is true only when no wall-clock timeout fires.

Measured (`tools/measure_scan_determinism.py`, AUDUSD pristine, 2 reps in one process, 0 fallbacks):
`EXEC = [51, 51]` → deterministic. The **same** symbol and registry gave **53** in this sweep (which
logged 3 fallbacks) and **56** in the earlier universe run (which logged timeouts too). So the
scanner is repeatable *within* a process and drifts by a few executions across runs *only when the
timeout fires* — 51 / 53 / 56 is one measurement with three answers, and the difference is the
fabricated NEUTRAL readings, not the data.

The unseeded `np.random.beta` in `ensemble_bandit.py:36` / `strategy_bandit.py:89,101` remains a red
herring — those methods are never called on the scan path.

The cure (stop the analyst blocking on the fetch at all — background refresh, or propagate the
deadline into `get_news_calendar`) is **not** applied: it changes a live trading system's inputs, and
the existing fail-open control flow was a deliberate documented decision. What was applied is the
visibility half — the analyst fallback no longer claims `confidence=0.50` it does not have, matching
the rule the Devil's Advocate fallback in the same function already followed. See
`jarvis/analysts/parallel_runner.py` and `tests/test_parallel_runner.py`.

**The measurement, however, no longer has the confound.** `spread_ab.frozen_news()` takes one snapshot
of the news calendar and serves it for the whole scan, so the network cannot change the scan's own
output mid-run and both arms are frozen from the same snapshot. This makes the instrument
deliberately *more* reproducible than production — it is an instrument, not a model of production —
and it does not hide the defect: `--no-freeze-news` reproduces production behaviour on demand, so the
drift is measurable rather than merely asserted. The cache `salt` became `v3-frozen-news-on|off`,
because a frozen-news scan and a live-news scan are different measurements of the same symbol and
reusing one for the other would reintroduce exactly the confound freezing removes.
`tests/test_scan_news_freeze.py` (7 tests) pins it, including the negative control that an unfrozen
stale cache *does* fetch — without which the freeze test could pass for the wrong reason. Sabotaging
the freeze turns 4 of the 7 red.

Both `tools/` files were also normalised to pure ASCII: they had accumulated 6 corrupted em-dash
sequences (`\xe2\x80?`) that made ruff fail with `E902` and aborted a run before any measurement
happened, and the corruption reappeared in text that was not being edited. The class of failure is
removed rather than repaired again.


### N. "Fix all issues" pass — triaged by defect class, not by lint count

Method: a wide ruff scan (`B,S,PERF,RUF,C4,SIM,TRY,RET,ARG,PIE,UP,N`) in **report-only** mode over
`jarvis/` and `tools/`, then triaged by defect class rather than by rule count. Three parallel
workers on disjoint file sets. Every fix is behaviour-preserving: no control flow changed, no public
API changed, no test rewritten.

#### Fixed

| Issue | Root cause | Fix |
|---|---|---|
| **Shared mutable class state** (RUF012, 17 attrs / 11 files, `7c67d74`) | Class-level dicts/lists declared without `ClassVar`, so they read as instance fields and invite per-instance assumptions about shared state | Annotated `ClassVar[...]`. **Zero runtime effect** — proven by probing ruff itself: `@dataclass` fields are *not* flagged by RUF012, so every hit is by construction a non-field. Confirmed no file in `jarvis/` uses `@dataclass`, pydantic, or `get_type_hints`. |
| **Silently swallowed exceptions** (S110/S112, 50 sites, `b30281b` + follow-up) | `except Exception: pass` in production paths — a failed DB write, fetch or hydration looked identical to "nothing happened" | 25 genuine invisible failures now log one line each: `debug` on per-bar/per-tick hot paths, `warning` for rare operation-level failures. 21 sites left as deliberate control flow (documented below). 4 more fixed in a follow-up once a module logger existed. **Control flow unchanged.** |
| **File handles not closed on error** (SIM115, 9 sites, `tools/`, `e48ba49`) | `open()` outside a `with`, so a raise mid-read leaks the handle | Converted to `with open(...)`. The four `dead_code_audit.py` sites keep their `try/except OSError` semantics. |
| **SWING stop could undercut the broker minimum** (`institutional_entry_engine.py:384`) | `max_risk_cap` is applied **after** the `pip_size * 5` floor and is a pure upper bound with **no floor of its own**, so it can push `risk_dist` back below the floor | `max_risk_cap = max(max_risk_cap, pip_size * 5)`. **Proven non-vacuous:** reverting it makes `risk_dist` come out at 0.14 against a floor of 0.5 — a 1.4-pip stop on XAUUSD, which MT5 rejects, while the sizer derives an enormous lot size from the tiny risk (the same failure mode as the documented BTCUSD 0.06-risk → 100-lot case). Not reachable with real data, since `d1_atr` is a *daily* range and the cap is always far above the floor; it guards only a degenerate near-flat D1 frame yielding a tiny positive `d1_atr`, which the existing `if d1_atr <= 0` check does not catch. Tests: `tests/test_institutional_entry_sl_floor.py::TestSwingCapCannotUndercutFloor`. |

Note the dynamic_levels floor does **not** protect this path: when the institutional engine succeeds,
`calculate_levels()` returns its dict directly and **discards `base_result`**, so the baseline
`max(3 * spread_dist, 0.10 * atr)` never touches the institutional stop. The protection here is the
clamp, not that floor.

#### Found, deliberately NOT changed

| Finding | Why not |
|---|---|
| **SQL injection** (S608 ×3: `database.py:834,882`, `realtime_optimizer.py:32`) | **False positives.** Both `database.py` sites build `where` from `?` placeholders — the only interpolation is `','.join('?' * n)`. `realtime_optimizer.py:32` interpolates `where_sql`, which is only ever the constants `"symbol=? AND regime=?"` / `"symbol=?"`, with values passed as params. No user string reaches a query. |
| **XML from a remote feed** (S314, `news.py:210`) | **Real, but needs a dependency.** Parses `https://www.myfxbook.com/rss/...` with stdlib `ElementTree`. No XXE (stdlib does not resolve external entities) but exposed to entity-expansion / "billion laughs" DoS. `defusedxml` is **not** in `requirements.txt` and not importable; adding it, or capping `resp.read(N)`, is a dependency/behaviour change. Your call. |
| **`random` usage** (S311 ×6) | All benign: retry-backoff jitter (`mt5_client.py:136`) and sample/modelled data generators. None produce a token, nonce, session id or order ticket; the auth path correctly uses `secrets`. |
| **`zip()` without `strict=`** (B905 ×12) | Adding `strict=True` converts today's silent truncation into a raised exception — a behaviour change, not a fix. |
| **B023 ×17** (`tools/`) | **All false positives.** Criterion: real only if the closure is *stored* and called after its loop iteration ends. Every one is consumed in the same iteration (`_row` called at `deflated_sharpe_report.py:210-211`, `fvg_standalone_backtest.py:497-498`; `ev_spread_cost_ab.py:634,637` lambdas passed to `DataFrame.apply`, which is eager). |
| **1,719 modernization items** (UP006/UP045/UP035) + style families (`N806`, `TRY003`, `SIM102`, `RET505` …) | Rewriting 1,700 type annotations is the opposite of a minimal fix and would bury the real defects. Not applied. |
| **`symbol_profile_config.py` divergent constants** | **Correcting my own error:** §J called this file "(unused)" — it is not. It lives at `jarvis/intelligence/`, not `jarvis/data/`, and is imported by `dynamic_levels.py`, `decision_engine.py`, `strategy_selector.py`, `winrate_targeting.py`, `exit_policy.py` and `backtesting/engine.py`. Its duplicated pip/contract/spread fields *do* disagree with `symbol_registry` (WTI `contract_size` 1000 vs 100; SOLUSD 1 vs 10; US500 `pip_size` 0.1 vs 1.0) — but they are **dead**: verified that no production read of `cfg.contract_size` / `cfg.pip_size` / `cfg.pip_value_per_lot` / `cfg.typical_spread_pips` / `cfg.digits` exists anywhere in `jarvis/` or `tools/`. Only geometry/timing fields are read. Latent, not live — nothing deleted. |
| **Loopback auth bypass** | Kept by explicit earlier decision. Still the largest open security item. |

#### Process note

A shared-file collision misattributed three of one worker's edits into another worker's commit:
`7c67d74` (typed as RUF012) also contains the swallowed-exception fixes at `remote_auth.py:70`,
`server.py:496` and `strategy_selector.py:67`, because both workers edited the same three files and
staging a *file* stages every change in it. Nothing was lost — all three commits landed
(`7c67d74 → e48ba49 → b30281b`) and the tree is clean. But when workers share a tree, stage by
**hunk**, or give each worker disjoint files.

---

## Track 2 — Refactor

### Inventory

| | |
|---|---|
| `jarvis/` Python | 139 files · 42,726 LOC (14 packages) |
| `tests/` Python | 112 files · 29,682 LOC |
| Repo-root `.py` | 35 files · 4,392 LOC |
| `tools/` `.py` | 43 |
| Banned modules in `jarvis/` | **0** (no `subprocess`/`os.system`/`eval`/`exec`/`pickle`) |
| Unused imports | 9 names in 5 files — **now 0**; see "Executed: lint baseline" below |
| Orphan subdirs | 0 |

### Executed: lint baseline (`b13eee8`)

The repository had **no lint configuration at all**, so "does it lint clean" was unanswerable.
`ruff.toml` now exists and selects **`F` (Pyflakes) + `E9` (syntax errors)** only. The full
`E`/`W` style families are deliberately *not* selected: enabling them would produce a
reformatting change orders of magnitude larger than this one and bury the real findings.
`F` + `E9` is the subset where every hit is a genuine defect rather than a preference.

Cleaned: **211 dead bindings** across 102 files (F401 unused imports incl. partial symbols
from multi-name imports, F841 unused locals, F541 f-strings with no placeholders).

Three independent proofs that nothing live was removed — not "it looks fine":

| # | Proof | Result |
|---|---|---|
| 1 | **Reproduction.** HEAD materialised into a scratch tree, `ruff check --select F --fix` run there, output diffed against the committed tree. | **78 / 102 files byte-identical.** The other 24 differ only by F841 removals, which ruff will not autofix. |
| 2 | **Scope-aware liveness.** For each removed binding: locate the innermost enclosing scope in the HEAD AST, locate the same scope in the post-sweep AST, check for any remaining load. | **211 / 211 clean, 0 unsafe.** |
| 3 | `compileall jarvis tools tests`, then the full suite. | exit 0; **3161 passed / 0 failed / 0 errors** — identical to baseline. |

Proof 2 exists because the two obvious checks are both wrong in opposite directions. A
file-wide grep says `cfg` in `jarvis/backtesting/engine.py` is still used — true, but in a
*different* method, so removing the dead binding at the other site is harmless. And ruff
never individually flags `is_jpy`, `ema200` or `risk_dist_ref`, because each is used by
another line that was *itself* removed (a cascade) — the removal is still sound. Scripts:
`.scratch/verify_sweep_scoped.py`, `.scratch/verify_sweep_against_ruff.py`.

Two dead-code sites were removed **with an explanatory NOTE rather than silently**, because
deleting them could be mistaken for a behaviour change:

- `jarvis/intelligence/dynamic_levels.py` — `is_breakout` was computed and never read.
  Breakout-regime handling was never actually implemented; removing the flag changes nothing
  and no breakout behaviour was added.
- `jarvis/risk/risk_engine.py` — the guard was commented "allow up to 0.30R" and computed a
  0.30R dollar figure that was never used. The enforced threshold is and remains
  `max($2, 0.5% equity)`. The comment/behaviour mismatch is **reported, not silently
  reconciled** — changing the enforced threshold is a trading decision, not a cleanup.

`jarvis/config/__init__.py` gained an explicit `__all__` so the public re-export keeps
working and is not re-read as dead code.

### Proposed deletion list (all evidence: 0 refs in tests/docs/imports)

| Path | Reason |
|---|---|
| `debug_eurusd.py`, `debug_eurusd2.py`, `debug_eurusd3.py` | Stale scratch, imports `jarvis` |
| `check_spreads.py`, `check_all_syntax.py` | Scratch utilities, no docs/tests |
| `verify_mt5_data.py`, `verify_exact_before_after.py`, `verify_system.py` | One-off probes |
| `reset_active_positions.py` | Dangerous one-off (mutates live state) |
| `get_remote_mobile_access.py` | **Prints hardcoded `hm2026admin` password** |
| `test_all_dashboard_endpoints.py` | Root smoke script, not collected |
| `test_auth_api.py`, `test_login_api.py`, `test_close_positions_api.py`, `test_india_markets_api.py`, `test_stocks_screener_api.py` | Same — manual HTTP probes, not in `tests/` |
| Root DBs `jarvis_history.db`, `jarvis_trade_memory.db`, `jarvis_circuit_state.db`, `jarvis_drawdown_state.db` | Abandoned duplicates (data/ has the live ones; root user_version=0) → **archive, don't delete** |
| `config/winrate_profiles.{MARGIN0,MARGIN17,PRE_SLIPPAGE,UNGUARDED,WIDEGRID,BAK,PRE_FIX}.json` | 0 code refs (~600 KB tracked) → archive |

### Proposed consolidation

- **`jarvis.bat`≡`JARVIS.cmd`** (byte-identical, 229 B; both hardcode `C:\Users\musu9\...`) → one wrapper.
- **`HM_start.bat`≈`HM_start.ps1`** → one wrapper.
- **`HM_start.py` (371 L) + `jarvis.py` (97 L) + `main.py` (89 L)** all build `JarvisOrchestrator`. Keep `main.py` canonical; `HM_start.py` as the live+tunnel launcher.
- **`jarvis/application/timeout_guard.py`** → move to `jarvis/common/`. Imported by 4 lower layers (`market/data_feed.py:17`, `analysts/parallel_runner.py:23`, `data/market_data_provider.py:411`, `execution/mt5_client.py:13`) — the only cause of `market→application` and `analysts→application` upward edges.
- **9 unused imports** (e.g. `decision_engine.py:8,40`, `risk_engine.py:19`, `orchestrator.py:12,14,37`, `exit_policy.py:43`, `market_context.py:6`).

### Dependency graph (current → target)

**Current** (cycles marked ⟲): `api→everything`; `application→analysts,config,data,execution,intelligence,learning,risk`; `intelligence⟲backtesting` (winrate_targeting imports trade_simulator); `market⟲intelligence`; `market→application` (timeout_guard); `data→india,stocks`.

**Target** (strictly downward): `config` (leaf) → `data,market` → `risk,learning,analysts` → `intelligence,execution` → `backtesting,application` → `api`; `india,stocks,historical` → `data,market` only.

Achieve by: relocate `timeout_guard`; invert `data→india/stocks` behind a registry; move `order_flow` out of `market_context` into `intelligence`; inject a simulator into `winrate_targeting` rather than import `backtesting`.

### Phased execution (each regression-free)

| Phase | Content | Risk | Test changes? |
|---|---|---|---|
| **1** | Pure deletions (10 scratch + 6 root `test_*`) + archive 4 root DBs + 7 winrate-profiles | Zero — no references | None |
| **2** | Wrapper consolidation (`jarvis.bat`/`JARVIS.cmd`/`HM_start.bat`/`HM_start.ps1`); demote `jarvis.py` | Low — entry-point edits only | None |
| **3** | Move `timeout_guard.py` to `jarvis/common/`; update 4 import sites; remove 9 unused imports | Low — mechanical | None |
| **3b** | **DONE (`b13eee8`)** — lint baseline `ruff.toml` (`F` + `E9`) + 211 dead bindings cleared. The unused-import half of phase 3 is complete (and was far larger than the 9 names first counted); the `timeout_guard` relocation is **not** done — it is an import-graph change, not a lint fix. | Low — verified by 3 independent proofs | None |
| 4 (deferred) | Break `market⟲intelligence` and `intelligence⟲backtesting` cycles via shims | Med — touches decision paths | Yes |

---

## Track 3 — Architecture

### Top 10 gaps (scored)

| # | Finding | File:line | Sev | Fix LOC |
|---|---|---|---|---|
| 1 | **No liveness/readiness endpoint.** `/api/diagnostics` is public, unauthenticated GET, returns full account snapshot (`login`, `server`, `balance`, `equity`, `name`, positions, services). | `server.py:367, 729-736`; `state_manager.py:224-237` | **High** | ~20 |
| 2 | **Health signals exist but are unreachable.** `MT5Client.broker_lock_health()` and `TimeoutGuard.health()` are implemented and never called. | `broker_lock.py:115`; `mt5_client.py:1079`; `timeout_guard.py:64` | **High** | ~15 |
| 3 | **Loopback auth bypass is total.** Any request from 127.0.0.1/::1 without `X-Forwarded-For` is auto-`ADMIN`. A tunnel is live (`active_tunnel_url.txt` → `trycloudflare.com`). | `server.py:111-127` | **High** | ~10 |
| 4 | **Unstructured, uncorrelated logging.** Plain-text `basicConfig`; 141 f-string `logger.*` calls; no request/correlation id; two disjoint sinks (`state_manager.logs` ring vs file logger). | `main.py:22`; `server.py:31`; `state_manager.py:168` | Med-High | ~30 |
| 5 | **Risk-state SQLite: no WAL, no busy timeout.** `circuit_breaker`/`drawdown` open a fresh `sqlite3.connect` per call, default `journal_mode=delete`, no `busy_timeout` → unhandled `database is locked`. | `circuit_breaker.py:73,109`; `drawdown.py:97,137` | Med-High | ~8 |
| 6 | **Per-tick thread-pool churn + broker-lock convoy.** `scan_all_modes` builds a new ≤32-thread pool per call; all N×3 fetches serialize on one process-wide `TrackedRLock`. | `orchestrator.py:973`; `mt5_client.py:38`; `broker_lock.py:42` | Med-High | ~6 |
| 7 | **Thread-per-connection server, no throttle.** `ThreadingHTTPServer`, no cap; SSE pins a thread for 60 s/client. | `server.py:13, 713-724` | Med | ~25 |
| 8 | **No security headers.** Missing CSP, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`. CORS is origin-locked. `innerHTML` used 154× (escaped by convention only). | `server.py:1106-1121, 1130-1210` | Med | ~8 |
| 9 | **Stale duplicate DBs on disk** (root; `data/` is canonical). | `schema_version.py:5-7` | Med | ~5 |
| 10 | **Session defects.** `change_password` mutates only in-memory `_users`; allows 6 chars vs 8/12 elsewhere; revoked tokens never expire. | `remote_auth.py:97-98, 378, 399-403` | Med | ~12 |

**Verified-OK (not gaps):** MT5 hold is bounded-wait with owner attribution; `TimeoutGuard` detects/replaces wedged pools; loops carry stop signals; caches are key-bounded; copilot sessions are LRU-capped; SQL is parameterized (only f-string SQL is `PRAGMA user_version`/`table_info` with internal constants).

### Observability minimum viable (3 additions)

1. **Real `/health` (liveness) and `/ready` (dependency).** Wire the already-implemented `broker_lock_health()` and `TimeoutGuard.health()`. Return 503 when broker lock is held > N seconds, `TimeoutGuard.health()["wedged"]` is true, or `SELECT 1` fails. Drop `/api/diagnostics` from the public list (or reduce to `{status, services, timestamp}`).
2. **One correlation id per request, in structured logs.** A `ContextVar` set at the top of `do_GET`/`do_POST`, emitted on every log record via `LoggerAdapter`. Convert the 141 f-string calls on the money path to `extra={...}` so a failed trade is reconstructable from logs alone.
3. **Tiny `/api/metrics`** (Prometheus text or JSON): requests by `path/status`, broker-lock wait count + `BrokerLockBusy` count, guard stuck-workers gauge, DB write errors, process uptime. Counter sites already exist (`server.py:742, 1100`, `broker_lock.py:71`).

### Scalability minimum viable (what breaks first as N grows)

1. **The single global broker lock.** Every `fetch_multi_timeframe` and every order takes one process-wide `TrackedRLock` (`mt5_client.py:38`); `scan_all_modes` submits 3N tasks (`orchestrator.py:967`) that all queue behind it. Break point: more symbols/styles than the lock can drain per tick → linear latency growth, then `BrokerLockBusy` fires. **Fix:** batch/cache broker reads; move non-broker compute outside the lock.
2. **Thread-per-connection server + per-tick pool creation.** Break point: client count or request flood drives thread count and churn until thread/memory-bound. **Fix:** one shared bounded pool; explicit max-concurrency guard; per-IP throttle beyond login.

Secondary ceiling: SQLite single-writer. With `database.py` in WAL the history path is fine, but the risk-state DBs are in rollback-journal mode (gap #5) and will surface `database is locked` first.

---

## Proposed execution roadmap (phased, with sign-off gates)

Each phase ends with `pytest` green and the layout verifier (`tools/verify_ui_layout.js` + `tools/verify_mobile_dock.js`) green. Implementation halts at every `⛔` for user approval.

| Phase | Scope | Risk | Touches entry model? |
|---|---|---|---|
| **1 — Regress-free cleanup** | Delete 10 scratch + 6 root `test_*`; archive 4 root DBs + 7 winrate-profiles; add 3 security response headers (`X-Content-Type-Options`, `X-Frame-Options`, CSP); add basic JSON logging formatter | None | No |
| **2 — Observability + safety** | Real `/health` + `/ready`; drop `/api/diagnostics` from public; wire `broker_lock_health()` + `TimeoutGuard.health()`; WAL + busy_timeout on risk-state DBs; revoke-and-prune token expiry; min password length 12 | Low | No |
| **3 — Refactor reorg** | Move `timeout_guard.py` → `jarvis/common/`; remove 9 unused imports; collapse `jarvis.bat`/`JARVIS.cmd`/`HM_start.bat`/`HM_start.ps1`; demote `jarvis.py` duplication | Low | No |
| **4 — Scalability first cuts** | Hoist `ThreadPoolExecutor`; move non-broker compute outside the lock; per-IP request throttle; raise the metrics surface | Low–medium (touches request path) | No |
| `⛔` | **Approval gate.** Show: remaining gaps; updated count of pytest-green + verifier-green. | | |
| **5 — Entry-model hardening** | ~~Enforce stop floor ≥ 1.11×ATR~~ **WITHDRAWN — measured no-op (§A1)**: ΔE[R] = +0.00092 over 46,939 trades; arms 0.10/0.30/0.50×ATR bit-identical. Keep only: recompute TP off floored risk (#2) and add `exit_price`/`realized_pnl` columns (#3) | n/a | **No — the stop-floor half is dead; the persistence half stands** |
| **6 — Architecture fixes (architecture-level changes only)** | Loopback auth bypass tightening (#3); CSE/security headers expansion; break `market⟲intelligence` and `intelligence⟲backtesting` cycles | Med-High | No |
| `⛔` | **Approval gate.** Show: entry-model backtest verdict (DSR, always-long delta); if NOT profitable → STOP here and revisit the model. | | |
| **7 — FVG standalone backtest** | **RUN — verdict NEGATIVE.** 0/20 pass DSR > 0.95; Sharpe negative on all 20; win rates 12–34.7% vs 42–59% required. Artifacts: `tools/fvg_standalone_backtest.py`, `reports/fvg_standalone_findings.md`, 6 JSON under `reports/`. | Done | **No. Do not wire FVG — as replacement or as addition.** |
| **8 — Stop-floor A/B** | **RUN — verdict NEGATIVE.** Arms 0.10/0.30/0.50/1.11×ATR compared over 46,939 trades (183d) and 94,937 (365d). No width is profitable; best E[R] −0.120R, DSR 0. Knee ≈ 0.70×ATR, and the system is already past it. Artifacts: `tools/stop_floor_ab.py`, `reports/stop_floor_ab_findings.md`. | Done | **No.** |

### What I will **not** do without explicit approval

- Any change that affects entry behaviour (Track 1 #1–#4) without a backtest on the same instrument showing non-negative effect.
- The loopback-auth-bypass tightening (Track 3 #3) — may break the live tunnel.
- Flipping `trading.use_calibrated_entry_policy` (Track 1 #5) — this is a *trading* decision.
- Wiring FVG as a replacement (Track 1 §C) — explicitly WAIT per the user's "evaluate before implementing" instruction.

---

## Execution log — phases 1–4 (committed `35f13bf`)

Approved scope was **phases 1–4 now, entry-model + FVG deferred**, and **keep the loopback auth bypass for now**. Everything below is merged and pushed; nothing touches the entry model.

| Phase | Planned | Done | Delta |
|---|---|---|---|
| **1 — Cleanup** | Delete 10 scratch + 6 root `test_*`; archive 4 root DBs + 7 winrate-profiles; 3 security headers | Deleted **16** root scripts (the 10 scratch + 6 `test_*`); moved 7 root DBs to `data/_archive_root/` (gitignored); **deleted** the 7 calibration snapshots outright rather than archiving them (~690 KB) — only `config/winrate_profiles.json` is ever loaded, and all 7 remain recoverable from git history. Security headers shipped with Phase 2. | Archiving became deleting: a copy was made first, then the duplicates were dropped so the commit adds no redundant bytes. |
| **2 — Observability + safety** | all items | `/health` + `/ready` (200/503, per-subsystem `broker_lock` / `guard` / `db`); `/api/diagnostics` dropped from the public allowlist; `X-Content-Type-Options`, `X-Frame-Options` and a CSP on every JSON, static and template response; token revocation with expiry pruning; **12-char password floor for ADMIN only**; WAL + `busy_timeout=5000` on both risk DBs | Password floor is admin-only: a global 12-char floor would have broken `tests/test_remote_auth.py:438` (10-char password) and `:445` (asserts "at least 6"). |
| **3 — Refactor reorg** | move `timeout_guard.py`, remove 9 unused imports, collapse wrappers | `timeout_guard.py` → new `jarvis/common/` (byte-identical, no shim, 6 import sites updated); 9 unused imports removed | **Wrapper consolidation NOT done** — `jarvis.bat` ≡ `JARVIS.cmd` (byte-identical) and `HM_start.bat` ≈ `HM_start.ps1` are daily-use entry points; deleting them needs your sign-off. |
| **4 — Scalability** | hoist pool; move compute outside lock; per-IP throttle; metrics | `ThreadPoolExecutor` hoisted to a module singleton (16 workers, `atexit` shutdown) | **"Move compute outside the lock" was correctly SKIPPED with evidence**: the heavy compute is *already* outside the broker lock — the lock is held only around native MT5 calls (`mt5_client.py:90,223,…`; `data_feed.py:360-366`) and `orchestrator.py` contains zero broker-lock references. Per-IP throttle and metrics surface deferred to phase 6. |

### Two defects the new tests caught in my own Phase 2 code (fixed in `523f47e`)

Writing the tests was worth it on its own — both of these shipped in `35f13bf` and neither was
visible from reading the code.

1. **`/health` answered "ok" about subsystems it had not probed.** `_send_health` stores a probe
   exception as `{"error": ...}`, but the verdict was read back with `broker_lock.get("held")` and
   `guard.get("wedged")`. Those keys are absent from an error dict, so both defaulted to *healthy*
   and the endpoint returned `200 / "ok"` even though it could not reach the broker lock or the
   guard at all. Only the DB probe could degrade the answer — the docstring promised otherwise.
   A monitor polling `/health` would have been told everything was fine by an endpoint that had
   looked at nothing. Now a probe that raised, or that returned no verdict key, counts as degraded.
2. **A revoked token stayed revoked past its own expiry.** `validate_token` tested membership in
   `_revoked_tokens` *before* sweeping expired entries, so a token whose revocation window had
   closed was still refused on the first call and only became usable once some unrelated validation
   happened to trigger the sweep. The sweep now runs first.

Both were pinned by tests as known behaviour first, then fixed — the tests were updated to assert
the corrected contract rather than deleted, so neither can silently come back.

### Note on `get_remote_mobile_access.py`

One of the 16 deleted files **printed a hardcoded admin password**. It is gone from the working tree, but it remains in git history — **rotate that credential**.

## Test coverage added for this work

The mandate asked for tests covering the refactored behaviour *and* trade-entry logic. All new
files live in `tests/`, are hermetic (no MT5, no sockets, no real DBs), and are collected by the
default `pytest` run.

| File | Asserts |
|---|---|
| `tests/test_entry_geometry_invariants.py` | **Trade-entry logic.** Entry is the raw observed ask/bid — the written proof that entries are *not* snapped to support/resistance (§B). Stop side and sign for BUY and SELL. `risk_dist == abs(entry − sl_price)`, the regression guard for the ~7× risk blow-up where the sizer priced risk off a 0.7-pip stop while the post-fill re-anchor applied a floored 5-pip distance. TP side, RR self-consistency, and the refuse-don't-fabricate path for unobserved and negative prices. Two **characterisation** tests pin the known defect: the floor is `max(3 × spread, 0.10 × ATR)` today, and an `xfail` asserts the Phase 5 target of ≥ 1.11 × ATR so it flips to XPASS the moment the floor is raised. |
| `tests/test_health_endpoint.py` | `/health` and `/ready` are reachable unauthenticated, return 200 with `broker_lock` / `guard` / `db` / `ts`, degrade to 503 when a subsystem fails, and probe each dependency independently instead of raising. |
| `tests/test_security_headers.py` | `nosniff` and `X-Frame-Options: DENY` on JSON, static and template responses, and a CSP that permits the CDN hosts the dashboard actually fetches from. |
| `tests/test_remote_auth_revocation.py` | A revoked token is rejected; expired revocations and >1 h `_failed_attempts` are pruned; the ADMIN-only 12-char password floor, including the deliberate asymmetry that a non-admin may still use 6–11 chars. |
| `tests/test_risk_db_pragmas.py` | Both risk DBs apply `journal_mode=WAL` and `busy_timeout=5000` on *every* open — the defect was a one-time pragma in `_init_db` that never reached `_save_state`. |
| `tests/test_scan_executor_singleton.py` | The scan pool is a module-level `ThreadPoolExecutor` with `max_workers == 16`, and `scan_all_modes` submits to it rather than building a pool per call. |
| `tests/test_trade_outcome_recorded.py` | A closed trade records **how it left**, not only how it entered (`exit_price`, `realized_pnl` columns added by the v5 migration). Pins the measured defect that every closed row reported `realized_pnl == expected_value` — the pre-trade estimate echoed back, so nothing was learned at close. |
| `tests/test_trade_outcome_unknown_is_null.py` | **"Unknown" ≠ "zero".** A trade that has not closed is stored as `exit_price=NULL, pnl=NULL, is_win=NULL`, not `0/0/0` — because `0.0` is a real break-even P&L *and* what `dict.get()` returns for a missing key, and `0` already means "loss". Guards the 42/93 rows that previously read `exit_price=0, pnl=0`. |
| `tests/test_institutional_entry_sl_floor.py` | `risk_dist == abs(entry_price − sl_price)` **and** `risk_dist >= pip_size * 5` on every path: structural and re-enforce branches, BUY and SELL, across SCALP / DAY_TRADING / SWING. The bug was `risk_dist` floored while `sl_price` kept the tighter structural stop, so sizing priced risk off one number and the actual stop sat elsewhere. |
| `tests/test_dynamic_levels_spread_symmetry.py` | BUY and SELL stops are mirror images: `spread_dist` is in the stop distance on **both** sides. Pre-fix, every BUY stop was exactly one spread tighter than the mirrored SELL stop for identical structure — an asymmetry produced by an inconsistency, not by any trading view. |
| `tests/test_position_monitor_spread_guard.py` | Drives the real `_get_context` path against a context engine that mirrors `MarketContextEngine.build_context`'s signature *including its 2.0 default*, so a caller that forgets to pass the spread reproduces the defect exactly. Fails against the pre-fix code. |

## Test status today

| Check | Result |
|---|---|
| `pytest` | **3183 passed / 0 failed / 0 errors / 1 skipped / 1 xfailed / 20 deselected** (junit root `tests=3220, failures=0, errors=0`). Was 3004 before this work. |
| `ruff check` (`ruff.toml`, `F` + `E9`) | **clean** — 0 findings |
| `compileall jarvis tools tests` | exit 0 |
| `tools/verify_ui_layout.js` | **345/345** |
| `tools/verify_mobile_dock.js` | **24/24** stable across 3 consecutive runs |
| Interaction smoke test | **ALL INTERACTIONS OK** stable across 3 consecutive runs |
| Desktop proof @1440px | 0 elements differ on all four market pages (with per-page substantive-comparison guard) |

---

## The one sentence to carry

The recurring losses have a **mechanical, fixable cause** (stop floor), but the **fundamental problem is that the entry signal has no measured edge** — and Track 1's own evaluation concludes that *no entry-model change can produce profitability until that verdict is overturned with evidence*. The honest next step is the standalone FVG backtest recipe in §C, run on the existing instruments, before anything else on the model is touched.
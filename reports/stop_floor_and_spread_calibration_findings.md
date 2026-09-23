# Stop-floor reachability & spread-calibration findings

Read-only diagnostic. No production code under `jarvis/` or `tests/` was modified.
Reproduce with:
- `tools/probe_mtf_data_reachability.py` (Q1 runtime proof)
- `tools/measure_spread_calibration.py` → `reports/spread_calibration_measured.json` (Q2)

---

## Q1 — Does the baseline stop floor (dynamic_levels.py:273 / :404) govern live stops?

### Verdict: **ALWAYS in the automated live path. The institutional early-return is dead there.**

`calculate_levels(..., mtf_data=None)` defaults to `None` (`dynamic_levels.py:58`). The
baseline floor at 273/404 only runs when the early-return at 514 is *not* taken, i.e. when
`mtf_data` is falsy or contains no non-empty DataFrame.

**Production call sites of `calculate_levels`:**

| # | Call site | Passes `mtf_data`? | mtf_data normally non-empty? | Which branch wins |
|---|-----------|--------------------|------------------------------|-------------------|
| 1 | `decision_engine.py:159` (`_compute_bias_and_levels`) | **No** — not passed at all | n/a | **baseline (floor 273/404)** |
| 2 | `dynamic_levels.py:662` (`calculate_manual_trade_levels`) | Yes → `mtf_data_fetched` | only when GLOBAL_STATE had no live context | baseline normally; institutional on a cold/empty state |
| 3 | tests / `run_validation_report.py` | mixed | n/a | not production |

Call site #1 is the live one. `decision_engine.evaluate()` *accepts* `mtf_data`
(`decision_engine.py:707`) and both live drivers pass a **populated** dict:

- `orchestrator.py:597` `mtf_data = self.data_feed.fetch_multi_timeframe(...)` →
  `orchestrator.py:717` `evaluate(..., mtf_data=mtf_data, ...)`
- `server.py:214` `mtf = cls.data_feed.fetch_multi_timeframe(...)` →
  `server.py:262` `de.evaluate(..., mtf_data=mtf, ...)`

…but `evaluate()` **never forwards it to `_compute_bias_and_levels`** — it only uses
`mtf_data` for order-flow (`:748`), master-confluence (`:891`) and the FVG engine (`:921`).
At `decision_engine.py:712-716` the call omits `mtf_data`, so `calculate_levels` receives
`mtf_data=None` and the baseline path (with the floor) is what returns.

**Runtime proof** (`tools/probe_mtf_data_reachability.py`, patched `calculate_levels`):

```
calculate_levels() was called 1 time(s)
  call #0: mtf_data was NOT PASSED (kwarg absent) -> baseline floor governs
```

even though `evaluate()` was called with a fully-populated `{"primary","M1".."D1"}` dict.
The same is true in the backtest engine (`backtesting/engine.py:648-649` passes `mtf_dict`
into `evaluate`, which drops it).

**Conclusion:** for every live automated trade the 273/404 floor governs. The institutional
path is reachable in production **only** via `calculate_manual_trade_levels` on a cold start
(GLOBAL_STATE has no context *and* a fresh MTF fetch succeeds) — never in the radar/orchestrator
decision loop. The prior stop-floor audit premise is therefore **valid, not moot**.

### What floor would the institutional engine apply if it did run?

`institutional_entry_engine.calculate_entry_and_levels` → per-style protocol. Every branch
(SCALP :138/:147, DAY :254/:263, SWING :363/:373) now applies:

```python
sl_dist = max(sl_dist, pip_size * 5)     # 5-pip distance floor
sl_price = round(entry_price ∓ sl_dist, digits)
risk_dist = abs(sl_price - entry_price)
```

i.e. a flat **`pip_size * 5`** floor on the *distance* (the sibling agent's fix is already
present — `sl_price` is now derived from the floored distance, so `risk_dist` and `sl_price`
agree; the older "risk_dist floored without moving sl_price" defect is gone in the file as it
stands on disk). Note this floor is far weaker than the baseline's
`max(3 * spread_dist, 0.10 * ATR)` — it does not scale with volatility or spread.

---

## Q2 — How wrong is the spread calibration?

### Column semantics (verified, not assumed)

The parquet `spread` column is in **MT5 points**. The repo's own canonical converter confirms
this — `jarvis/data/mt5_history.py:320`:

```python
typical_spread_pips = max(0.1, float(info.spread) * point / pip_size)
```

and the backtest scan uses the identical form at `backtesting/signal_scan.py:180`:
`raw * point / pip`. I used the same formula, with `point` from each manifest's `meta` and the
**registry's** `pip_size` (the unit the registry's `typical/max_spread_pips` are quoted in).
`raw * pip_size` (a price distance) is **not** a pip count and was not used.

### Per-symbol measurement (M1, 183d)

| symbol | class | regTyp | regMax | med pips | p95 pips | p99 pips | max pips | med/typ | p95/max | % bars > max | pip_size mismatch |
|--------|-------|-------:|-------:|---------:|---------:|---------:|---------:|--------:|--------:|-------------:|:-----------------:|
| AUDUSD | FOREX | 0.90 | 2.50 | 2.300 | 2.600 | 9.63 | 16.50 | **2.56** | 1.04 | 5.81% | no |
| BTCUSD | CRYPTO | 1500.0 | 3000.0 | 2250.0 | 2250.0 | 2250.0 | 2250.0 | **1.50** | 0.75 | 0.00% | no |
| ETHUSD | CRYPTO | 345.0 | 450.0 | 345.0 | 345.0 | 345.0 | 345.0 | 1.00 | 0.77 | 0.00% | no |
| EURJPY | FOREX | 1.00 | 2.50 | 2.000 | 2.900 | 15.70 | 22.60 | **2.00** | 1.16 | 5.90% | no |
| EURUSD | FOREX | 0.70 | 2.00 | 1.900 | 2.200 | 7.30 | 14.40 | **2.71** | 1.10 | 6.77% | no |
| GBPJPY | FOREX | 1.20 | 3.00 | 2.500 | 4.200 | 25.30 | 27.10 | **2.08** | 1.40 | 10.92% | no |
| GBPUSD | FOREX | 0.90 | 2.50 | 2.200 | 3.100 | 15.30 | 16.70 | **2.44** | 1.24 | 8.52% | no |
| GER40 | INDEX | 2.00 | 6.00 | 1.950 | 3.600 | 8.40 | 22.00 | 0.97 | 0.60 | 2.30% | YES (1.0 vs 0.01) |
| NAS100 | INDEX | 2.00 | 6.00 | 1.950 | 1.950 | 1.95 | 1.95 | 0.97 | 0.33 | 0.00% | YES (1.0 vs 0.01) |
| NZDUSD | FOREX | 2.80 | 8.50 | 2.800 | 3.100 | 9.00 | 9.00 | 1.00 | 0.36 | 2.23% | no |
| SOLUSD | CRYPTO | 35.0 | 105.0 | 35.00 | 45.00 | 45.0 | 45.0 | 1.00 | 0.43 | 0.00% | no |
| UK100 | INDEX | 1.60 | 8.50 | 1.600 | 6.900 | 7.00 | 8.15 | 1.00 | 0.81 | 0.00% | YES (1.0 vs 0.01) |
| US30 | INDEX | 3.90 | 12.00 | 3.900 | 4.000 | 4.00 | 15.80 | 1.00 | 0.33 | 0.00% | YES (1.0 vs 0.01) |
| US500 | INDEX | 0.60 | 6.00 | 0.600 | 0.650 | 0.65 | 0.65 | 1.00 | 0.11 | 0.00% | YES (1.0 vs 0.01) |
| USDCAD | FOREX | 2.70 | 8.50 | 2.800 | 3.600 | 12.70 | 15.10 | 1.04 | 0.42 | 2.65% | no |
| USDCHF | FOREX | 2.40 | 4.00 | 2.400 | 3.300 | 18.60 | 19.80 | 1.00 | 0.82 | 4.14% | no |
| USDJPY | FOREX | 0.80 | 2.50 | 2.300 | 3.200 | 10.70 | 22.10 | **2.88** | 1.28 | 9.14% | no |
| WTI | COMMODITY | 13.00 | 25.00 | 3.000 | 8.000 | 8.00 | 14.00 | **0.23** | 0.32 | 0.00% | no |
| XAGUSD | COMMODITY | 4.00 | 12.00 | 4.800 | 5.200 | 5.30 | 10.90 | **1.20** | 0.43 | 0.00% | no |
| XAUUSD | COMMODITY | 2.00 | 5.00 | 2.000 | 2.500 | 2.90 | 20.60 | 1.00 | 0.50 | 0.28% | YES (0.1 vs 0.01) |

(med/typ is `median_pips_reg / registry_typical_spread_pips` — the factor by which the
modelled spread cost under-charges; p95/max is `p95_pips_reg / registry_max_spread_pips`.)

### Does the registry materially under-charge cost? — **Yes, for the FX majors (2.0–2.9x).**

The live spread is a **static constant**: `build_context` never reads the parquet spread —
it is *given* `current_spread_pips=spec.typical_spread_pips` by every live caller
(`orchestrator.py:679`, `server.py:257`, `dynamic_levels.py:590`). The context's `ask` is
built as `close + current_spread_pips * pip_size` (`market_context.py:80`). So:

1. **Cost / EV is understated.** `spread_cost = current_spread_pips * pip_value_per_lot * lots`
   (`decision_engine.py:266,962`) uses the registry value. For EURUSD the true median is 1.9 pips
   but the model charges 0.7 → **2.71x too cheap**; USDJPY 2.88x, AUDUSD 2.56x, GBPUSD 2.44x,
   GBPJPY 2.08x, EURJPY 2.00x, BTCUSD 1.50x, XAGUSD 1.20x.
2. **The spread rejection filter never fires live.** `is_excessive_spread = current_spread_pips
   > max_allowed_spread_pips` (`volatility.py:29,64`; `trade_guard.py:98`; `risk_engine.py:246`).
   With `current_spread_pips` pinned to the registry typical and `typical < max` for every
   symbol, the test is always False. Measured on the real M1 series the spread actually exceeds
   `max_spread_pips` **6–11% of the time on the FX majors** (GBPJPY 10.9%, USDJPY 9.1%,
   GBPUSD 8.5%, EURUSD 6.8%, EURJPY 5.9%, AUDUSD 5.8%), and the **p95 alone** exceeds the cap for
   all six majors. Those bars should have been rejected and were not.
3. **The stop floor's spread term is also understated.** `spread_dist` at
   `dynamic_levels.py:139` collapses to `registry_typical * pip_size`, so the `3 * spread_dist`
   floor at 273/404 is ~2.7x tighter for EURUSD than 3× the real median spread would be.
   Separately, `spread_ratio = cur_spread / typ_spread` (`:147-148`) is **always 1.0** live
   (both operands are the registry typical), so `gamma_spread` is inert.

### Over-charge (opposite direction)

- **WTI**: registry 13.0 pips vs measured median 3.0 (0.23x) — 4.3x **too high**.

### Accurate (within ~5%)

`USDCHF`, `USDCAD`, `NZDUSD`, `XAUUSD`, `ETHUSD`, `SOLUSD`, and all five indices
(`US30`, `US500`, `NAS100`, `UK100`, `GER40`). For the indices the registry value equals the
measured median *exactly* in price terms — they were calibrated from this data.

### Unit inconsistencies (points vs pips)

- **Registry `pip_size` ≠ MT5 `pip_size` for every index and gold:**
  `GER40/NAS100/UK100/US30/US500` registry `1.0` vs MT5 `0.01` (100x), `XAUUSD` registry `0.1`
  vs MT5 `0.01` (10x). The *values* are self-consistent because each source multiplies by its
  own `pip_size` (registry 2.0 × 1.0 = 2.0 price ≈ measured 195 pts × 0.01 = 1.95 price), so this
  is a convention difference, not a value error — **but it makes the two fields named
  `typical_spread_pips` disagree by 100x across sources** (manifest GER40 = 205, registry = 2.0).
- **The manifest's `meta.typical_spread_pips` is a live snapshot, not a median.** EURUSD
  manifest = 3.3 pips while the 183-day median is 1.9; WTI manifest = 13.0 while the median is
  3.0. Anything that treats that field as "typical" inherits the fetch-time spread.
- **Candidate unit bug (unconfirmed, outside my scope):** `historical/quality_engine.py:185`
  compares the raw **points** `spread` column against `typical_spread * 10`, where
  `typical_spread` comes from `metadata_db.get_symbol_specs(...)["specs"]["typical_spread_pips"]`
  (`acquisition.py:341`). If that value is in pips (not points) the EXTREME_SPREAD threshold is
  10x too tight for 5-digit FX / gold. I could not resolve the metadata DB's unit from here.

---

## Bugs found but NOT fixed

1. **`decision_engine.evaluate` silently drops `mtf_data` before level calculation**
   (`decision_engine.py:712-716`). The institutional entry engine is therefore unreachable in
   the live decision path, and the orchestrator/server/backtest all believe they are feeding it
   MTF frames. Either forward it or delete the dead branch — as written, the `mtf_data`
   parameter of `evaluate` is a decoy for the level path.
2. **Live spread is a hardcoded constant** — `current_spread_pips=spec.typical_spread_pips` at
   every live `build_context` call, so the real per-bar spread is never consulted; the spread
   rejection filter is dead and cost/EV is understated by ~2.7x on EURUSD.
3. **Registry FX-major spreads are ~2–2.9x too low** (`typical_spread_pips`), and WTI ~4.3x too
   high.
4. **`spread_ratio` is inert** (`dynamic_levels.py:147-148`) because `cur_spread == typ_spread`
   live — the `gamma_spread` sensitivity term can never move.

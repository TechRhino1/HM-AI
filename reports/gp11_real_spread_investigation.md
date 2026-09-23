# GP-11 — What happens if the live path is fed the REAL per-bar spread?

Read-only investigation. No `jarvis/` or `tests/` files were modified.
Probe scripts: `tools/gp11_spread_probe.py`, `tools/gp11_geometry_probe.py` (both re-runnable).
Raw data: `reports/gp11_spread_impact_probe.json`, `reports/gp11_spread_geometry_probe.json`.

---

## 0. Unit semantics — verified, not assumed

`spread` in every parquet under `data/market/real/<SYM>/` is MT5 **points**
(`mt5_history.py:25,571-575`; MT5's `copy_rates_from_pos` schema is
`time, open, high, low, close, tick_volume, spread, real_volume`).

Production conversion (already used by the backtest scan,
`backtesting/signal_scan.py:93,170-180`):

```
point      = 10 ** (-spec.digits)          # _point_size(digits)
pips       = spread_points * point / spec.pip_size
```

Confirmed on disk: EURUSD `spread=19` → `19 * 1e-5 / 1e-4 = 1.9 pips`
(matches `reports/spread_calibration_measured.json`). **`spread * pip_size` would
double-count and is wrong.** For instruments where registry `pip_size` differs from
the MT5 pip (`XAUUSD`, `GER40`, `UK100`, `US30`, `US500` — `unit_mismatch: true`)
the registry value is the one the live code uses, so I used the registry value.

## 1. Is a real quoted spread available in the live path? — YES, twice, and both are thrown away

1. **`mt5.symbol_info_tick(sym).ask/.bid`** is already called live in
   `execution/mt5_client.py:541`, and the real spread is computed at
   `mt5_client.py:563` (`spread_dist = abs(tick.ask - tick.bid)`) — but only at
   **order-send time**, long after the decision, and only for `min_stop_dist`.
   `orchestrator` holds `self.mt5_client` (`orchestrator.py:155`) and calls
   `build_context` at `orchestrator.py:683`, so the tick is one call away — but
   `MT5Client` exposes **no** `get_tick()` method today (a new method would be a code change).
2. **The per-bar spread is already in the live feed and is dropped.**
   `market/data_feed.py:378` projects the MT5 rates down to
   `["time","open","high","low","close","volume"]` — **`spread` is discarded**,
   although `rates["spread"]` (points) is right there. `mt5_history.py:571-575`
   keeps it for the backtest/parquet path. This is the cheapest seam in the whole
   system: no new MT5 call, no new method, just stop projecting the column away.

So: the real spread is genuinely available in the live path. It is not
"unavailable" — it is discarded.

### The `ask > 0 and bid > 0` fallback really is dead

`market_context.py:79-80` sets `bid = close`, `ask = close + current_spread_pips*pip`.
`dynamic_levels.py:139` prefers `context.ask - context.bid` and only falls back to
`vol.current_spread_pips * pip_size` when that guard fails. With `close > 0` the guard
is always true; with `close <= 0` the observed-price guard at `dynamic_levels.py:99-127`
returns `data_unavailable=True` **before** line 139 is reached. **The fallback is
unreachable in live.** `spread_dist` therefore always equals
`current_spread_pips * pip_size` — and since every live caller passes the registry
constant, it equals `typical_spread_pips * pip_size`. Confirmed.

## 2. Consumers of `current_spread_pips` / `spread_dist` / `spread_ratio`

| # | Site | Role | Fires with real spread? |
|---|------|------|--------------------------|
| 1 | `market_context.py:80` → `context.ask` | **prices the entry** | Yes — BUY entry becomes `close + real_spread` (currently `close + typical`) |
| 2 | `volatility.py:29,64` → `is_excessive_spread` | **gate** | Yes — `spread > max_spread` |
| 3 | `dynamic_levels.py:139` `spread_dist` | **geometry (cost)** | Yes — feeds every `+ spread_dist` SL term |
| 4 | `dynamic_levels.py:147-148` `spread_ratio` | **sizing of buffer** | Yes — ≡1 today, becomes 0.5–4.0 |
| 5 | `dynamic_levels.py:151` `gamma_spread` term | sizing of buffer | Yes but inert (see §3) |
| 6 | `dynamic_levels.py:280,410` `min_sl_dist = max(3*spread_dist, 0.10*atr)` | **SL floor** | Yes — floor rises with real spread |
| 7 | `decision_engine.py:266,269` `spread_cost` in EV | **cost** | Yes — EV falls |
| 8 | `decision_engine.py:323-326,338` `spread_ratio`→`spread_penalty`→`required_win_p` | **gate** | Yes — hurdle rises |
| 9 | `decision_engine.py:348,376-377` `spread_excess`→`min_score` | **gate** | Yes — AI score hurdle rises |
| 10 | `decision_engine.py:528` `spread <= typical*1.5` (Forex Prime Session) | **gate** | Yes (but has `or` escapes) |
| 11 | `decision_engine.py:535` `spread <= typical*1.2 and ai>=70 …` | **gate** | Yes |
| 12 | `decision_engine.py:669` **"Spread Protection"** `spread <= max_spread and not is_excessive_spread` | **gate (reject)** | **Yes — the headline one** |
| 13 | `decision_engine.py:1183,1201` reason strings | report | — |
| 14 | `risk_engine.py:246` **ADAPTIVE_GATE_13** `current_spread_pips > max_allowed` | **gate (reject)** | Yes |
| 15 | `orchestrator.py:755` `is_favorable_scalp` `spread <= max_spread*0.75` | **gate** | Yes |
| 16 | `orchestrator.py:852,1141` → risk engine | pass-through | — |
| 17 | `position_monitor.py:290` **blowout guard** `spread > typical*2` | **gate (freeze)** | **Yes — and it already mis-fires (see §5)** |
| 18 | `order_manager.py:176` `> 4.0` pips alert | report/log | Yes |
| 19 | `execution_engine.py:172,200` → `TRADE_DB.log_trade(spread_pips=…)` | **report/analytics** | Yes — DB currently stores the registry constant |
| 20 | `api/copilot.py:513` display | report | Yes |
| 21 | `analysts/*` (`volatility_analyst.py:26-29`, `devil_advocate.py:128`) | report/penalty text | Yes |

**Gates that would begin firing once a real spread arrives: #2, #8, #9, #10, #11, #12, #14, #15, #17.**

## 3. Quantified behaviour change (H1 `_183d`, M1 `_183d`)

### 3a. Spread rejection filter (`spread > spec.max_spread_pips`) — fraction of bars

| Symbol | M1 | H1 | symbol | M1 | H1 |
|---|---|---|---|---|---|
| EURUSD | **6.8%** | 4.2% | USDJPY | **9.1%** | 4.5% |
| GBPUSD | **8.5%** | 4.7% | AUDUSD | **5.8%** | 4.1% |
| GBPJPY | **10.9%** | 4.9% | EURJPY | **5.9%** | 4.2% |
| USDCAD | 2.7% | 0.5% | USDCHF | 4.1% | 4.0% |
| NZDUSD | 2.2% | 0.1% | GER40 | 2.3% | 7.2% |
| XAUUSD | 0.3% | 0.0% | WTI | 0% | 0% |
| XAGUSD / US30 / US500 / NAS100 / ETHUSD / SOLUSD / BTCUSD | 0% | 0% | | | |

Matches the established 6–11 % figure for the majors. So the "Spread Protection"
gate (#12) and ADAPTIVE_GATE_13 (#14) would reject roughly **1 bar in 15** for FX
majors, and the *tighter* per-asset caps at `decision_engine.py:392,402,406`
(crypto ×0.95, index ×0.90, micro ×0.8) push this higher still.

### 3b. `spread_ratio` — deviation from 1

`spread_ratio` is **≡ 1.0 by construction today**. With real spread it is >1 on
**100 % of bars for EURUSD / USDJPY / AUDUSD / GBPUSD** (median ratio 2.9 / 2.9 / 2.6 /
2.4), 97 % for GBPJPY, 84 % for XAGUSD (M1). WTI is the reverse: median ratio 0.23
(registry overstates 4.3×). NAS100/ETHUSD/NZDUSD sit at ≈1.0.

### 3c. `effective_buffer` — **the anti-wick buffer does dominate; confirmed**

`effective_buffer = max(dynamic_buffer, anti_wick_buffer)` with
`dynamic_buffer = atr*(0.12 + 0.05*atr_ratio + 0.05*spread_ratio)` and
`anti_wick_buffer = atr*w`, `w ∈ {0.35, 0.40 (XAGUSD), 0.45 (ETHUSD/SOLUSD)}`.

Fraction of H1 bars where the effective buffer actually moves:

| Symbol | moved | Symbol | moved |
|---|---|---|---|
| GER40 | 0.69% | EURJPY/GBPJPY/UK100 | 0.06–0.10% |
| EURUSD/GBPUSD/AUDUSD/USDJPY | 0.03% | everything else | **0.00%** |

**Confirmed and refuted-as-stated:** the anti-wick buffer dominates and the
`gamma_spread` term is inert *in practice*, not just today — with `atr_ratio ≈ 0.33`
(clamped, because the H1 ATR is far below the "daily ATR %" denominator) and
`spread_ratio ≤ 3`, `dynamic_buffer ≤ atr*0.29 < atr*0.35`. It only overtakes the
anti-wick shield on the rare bar where `atr_ratio` is near its 3.0 cap. **The buffer
is the wrong lever for this fix.**

### 3d. But the SL geometry *does* move — this is the real channel

`spread_dist` enters **additively** at `dynamic_levels.py:227,229,239-252,270,278-280`
(BUY) and `370-410` (SELL). H1 `_183d` medians:

| Symbol | typ (reg) | real med | Δ spread_dist | Δ as % of ATR | Δ SL floor (pips) | floor binds reg→real |
|---|---|---|---|---|---|---|
| AUDUSD | 0.9 | 2.3 | +1.4 p | **+15.0%** | **+4.2** | 99.5%→100% |
| EURUSD | 0.7 | 1.9 | +1.2 p | **+11.4%** | **+3.6** | 97.9%→100% |
| USDJPY | 0.8 | 2.1 | +1.3 p | **+9.3%** | **+3.9** | 85.8%→98.9% |
| GBPUSD | 0.9 | 2.1 | +1.2 p | **+8.6%** | **+3.6** | 97.6%→100% |
| SOLUSD | 35 | 40 | +5.0 | +5.6% | +15.0 | — |
| EURJPY | 1.0 | 1.7 | +0.7 | +5.0% | +2.1 | 91.9%→98.1% |
| GBPJPY | 1.2 | 2.0 | +0.8 | +2.9% | +2.1 | 91.0%→97.3% |
| WTI | 13.0 | 3.0 | **−10.0** | −11.4% | **−30.0** | 100%→54% |
| GER40/NAS100/US500 | ≈ | ≈ | −0.05 | ≈0 | 0.0 | — |

So for FX majors every stop is placed ~1.2–1.4 pips wider and the SL floor rises by
~3× that. This is exactly the mechanism that made the (withheld) registry-constant
correction change selection materially.

### 3e. The one genuinely dangerous gate: the blowout guard

`position_monitor.py:290` freezes all position management when `spread > typical*2`.
Fraction of M1 bars above `2×typical`:

| EURUSD | USDJPY | AUDUSD | GBPUSD | GBPJPY | XAGUSD | GER40 |
|---|---|---|---|---|---|---|
| **100%** | **100%** | **100%** | **94%** | 64% | 0.07% | 2.3% |

Naively feeding the real spread in would make the position monitor skip
trailing-stop / breakeven / partial-close on **essentially every cycle** for the four
tightest FX majors. That is a silent, severe degradation.

## 4. Is there a safe ordering? — reporting yes, cost-accounting no (without a new seam)

There is **no existing seam**: `build_context(current_spread_pips=X)` writes the same
`X` into **both** `context.ask/bid` (→ `spread_dist`, geometry) **and**
`volatility.current_spread_pips` (→ every gate). You cannot move one without the other
by argument alone. Recommended ordering:

1. **Step 0 — keep the column (zero behaviour change).** Stop dropping `spread` at
   `data_feed.py:378`; carry it into `mtf_data["primary"]`. Nothing reads it yet.
   Also log the real spread (`mt5.symbol_info_tick`) per cycle in the orchestrator
   beside the existing `typical_spread_pips`. Pure measurement, zero risk.
2. **Step 1 — reporting + cost only, via a new explicit field.** Add
   `MarketContext.live_spread_pips` (new field; nothing existing reads it). Point
   `TRADE_DB.log_trade(spread_pips=…)` (`execution_engine.py:172`), the UI strings and
   the EV `spread_cost` at it. **Leave `vol.current_spread_pips` on the registry
   value**, so every gate in §2 stays exactly as it is today. This makes the recorded
   spread and the modelled cost honest without moving a single decision.
3. **Step 2 — geometry, validated in the backtest first.** The `signal_scan` backtest
   *already* feeds real per-bar spread (`signal_scan.py:230`), so a live/backtest
   consistency run is possible without touching live. Enable `spread_dist` from the
   real spread only after that run shows selection is unchanged or better.
4. **Step 3 — gates last, one at a time.** Start with `Spread Protection` (#12) and
   ADAPTIVE_GATE_13 (#14) at ~5–11 % reject. **Fix the blowout guard (#17) before
   enabling anything** — otherwise the position monitor dies for the FX majors.

**Recommendation:** do Step 0 and Step 1 now (safe, reversible, purely additive);
treat Step 2 as a measured A/B; do **not** wire the real spread into
`current_spread_pips` as a single change, because that simultaneously widens stops,
raises two AI-score hurdles, and freezes position management.

## 5. Found but not fixed

* **`position_monitor._get_context` (`position_monitor.py:1050`) calls
  `build_context(symbol, mtf_data)` with no spread argument → the default `2.0`.**
  Combined with the blowout guard `spread > typical*2` (`:290`) this means the guard
  fires **unconditionally** for every symbol whose `typical_spread_pips < 1.0` —
  currently **EURUSD, GBPUSD, USDJPY, AUDUSD** (`typical` 0.7/0.9/0.8/0.9). For those
  four symbols the position monitor's trailing stop, breakeven and partial-close logic
  is dead today, independent of the real-spread question. This is a live defect.
* `data_feed.py:378` discards the `spread` column that MT5 already returns.
* `orchestrator.py:755` uses `_spec.max_spread_pips * 0.75` for `is_favorable_scalp`
  while `decision_engine.py:384` uses `spec.max_spread_pips` for SCALP — two different
  SCALP spread caps.
* `backtesting/engine.py:173` takes a scalar `spread_pips: float = 2.0` whereas
  `backtesting/signal_scan.py:230` uses the real per-bar spread — the two backtest
  entry points do not agree on spread. The 2440→2839 EXECUTE shift in the withheld
  experiment was measured on the latter; the former would not reproduce it.

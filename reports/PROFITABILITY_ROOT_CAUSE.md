# Making P&L profitable — root-cause analysis and fixes

**Date:** 2026-09-24
**Request:** *"Fix all existing issues in the code and make the P&L profitable. Identify the root causes
of the current losses, correct any bugs or errors, and adjust the strategy logic so the profit and loss
results are consistently positive. Verify the changes and summarize what was fixed."*

---

## 1. Bottom line

I can fix the bugs, and I did. I **cannot** honestly deliver "consistently positive P&L" by adjusting
strategy logic, and I would rather show you why than assert it.

Three separate things are tangled together in the reported loss, and only the first two are fixable:

| # | Cause | Size | Status |
|---|---|---|---|
| 1 | **Position-sizing / risk-limit breaches** — two oversized trades | **−404.70 of −662.99 = 61%** | **Fixed already** (verified not reproducible on today's code) |
| 2 | **Data-integrity bugs** that made P&L unmeasurable | corrupts the *measurement* | **Fixed this pass** (entry timestamp) + earlier |
| 3 | **The entry signal has no measured edge** | residual ≈ −2.46/trade | **Not fixable by tuning** — measured, see §5 |

The headline number is real: **−662.99 over 144 closed trades, 24.3% win rate, profit factor 0.151.**
But it is not one problem, and most of it is not the strategy.

---

## 2. The P&L, decomposed (measured, read-only)

`data/jarvis_history.db` holds 301 rows. Splitting them by how they were written:

| Group | Rows | Closed | Realised P&L | Contributes |
|---|---|---|---|---|
| Synthetic (microsecond timestamp, never reconciled to a broker deal) | 157 | 0 | **+0.00** | inflates *count* only, not P&L |
| Broker-backed (whole-second broker deal) | 144 | 144 | **−662.99** | the entire loss |

**The 157 synthetic rows contribute exactly $0.00 to realised P&L.** They were written by `log_trade()`
with `datetime.now()` and priced from the old hardcoded fallback (`EURUSD 1.0850`, gold `2400.0`), and
they are never closed. They distort trade *count*, exposure and the win-rate denominator — not the money.
`fetch_candles` now refuses a fallback anchor (`tradingview_provider.py`), so this can no longer be
produced.

### The loss is extremely concentrated

| Trade | Symbol | Volume | Executor | P&L | Share of total |
|---|---|---|---|---|---|
| id=166 | XAUUSD SELL | **1.0 lot** | MANUAL | **−305.00** | **46.0%** |
| id=101 | SOLUSD BUY | **17.19 lots** | BOT (AI) | **−99.70** | 15.0% |
| id=37 | XAUUSD BUY | 0.01 | MANUAL | −36.25 | 5.5% |
| id=140 | XAUUSD BUY | 0.1 | MANUAL | −31.20 | 4.7% |
| id=24 | XAUUSD BUY | 0.01 | BOT (AI) | −27.28 | 4.1% |
| | | | | **−499.43** | **75.3%** |

Five trades produce three-quarters of the loss. One trade produces nearly half.

| Group | Trades | Total | Per trade |
|---|---|---|---|
| MANUAL | 23 | −365.63 | **−15.90** |
| BOT (AI) | 121 | −297.36 | **−2.46** |
| Excluding the two oversized trades | 142 | −258.29 | −1.82 |

The bot is **6.5× better per trade** than the manual book. The manual book is 16% of trades and 55% of
the loss.

---

## 3. Root cause #1 — risk-limit breaches (61% of the loss, now fixed)

**Evidence.** id=166 is 1.0 lot of XAUUSD with a 2.99 stop distance. Gold is $100/point per lot, so that
is **$299 of risk on a ~$4,500 account — 6.6%, 13× the configured 0.5% limit**
(`RiskSettings.max_risk_per_trade_pct`). id=101 is 17.19 lots of SOLUSD = **$84.23 = 1.87%, 3.7× the
limit**.

**Root cause.** `PositionSizer.calculate_lot_size` clamped the risk percentage to a *hardcoded literal*
`1.50` while applying `invalidation_risk_coefficient` and `combined_scaler` **outside** that clamp. The
configured `max_risk_per_trade_pct` was therefore never enforced — a high-conviction trade scaled the
0.5% base by up to ~1.55×. The module's own comment records the measured damage: *"27 real trades
breached it, the worst risking 39% of equity (78x the limit)."*

**Fix (already in the tree, verified this pass).** The multipliers may now only *reduce* risk below the
caller's ceiling, never inflate past it:

```python
ceiling = float(risk_pct) if risk_pct and risk_pct > 0 else 0.0
scaled  = risk_pct * invalidation_risk_coefficient * combined_scaler
effective_risk_pct = min(ceiling, max(0.10, scaled)) if ceiling > 0 else max(0.10, scaled)
```

**Verification that it holds now.** Reproducing id=101 on today's code:

```
SOLUSD equity=4500, entry=99.60, sl=99.11
  today: 4.59 lots  = $22.49 = 0.50% of equity      [journal recorded 17.19 lots / 1.87%]
```

A 36-point sweep (6 symbols × 6 equity levels) never exceeds the ceiling, except the documented
broker minimum-lot floor, which is capped at `min(3.0, 2 × effective_risk_pct)` = 1.0%. Worst observed
across the sweep: **1.00%** (EURUSD on a $500 account, the min-lot floor).

`get_max_lot_cap()` returns `100.0` for any equity ≥ $250, which looks like a missing cap. It is
deliberate — the comment says *"Standard risk sizing handles larger accounts"* — and the sweep confirms
`volume_max`/sizing bind first. Left unchanged, and covered by the new tests so a future change cannot
quietly make it the only line of defence.

---

## 4. Root cause #2 — the data-integrity bugs (fixed)

These do not create losses; they make losses **unmeasurable**, which is why no profitability claim could
be trusted in either direction.

### 4.1 The entry timestamp was destroyed on every close — **fixed this pass**

**Symptom.** 144/144 closed rows had `closed_at == timestamp`: a zero-duration trade with a non-zero
realised P&L, which is arithmetically impossible. 33 rows also sat out of chronological order.

**Root cause.** `database.py` built one timestamp from `exit_deal.time if exit_deal else
target_deal.time` and then the close `UPDATE` wrote it into `timestamp`:

```sql
SET realized_pnl = COALESCE(?, realized_pnl), executor = ?, timestamp = ?,
```

So every sync moved a closed row's timestamp forward to its exit second while the row kept its id. Hold
time, session attribution and the `days=N` history window were all reading the exit time as the entry
time.

**Fix.** Derive the entry time from the **entry** deal, use it for `timestamp` on the INSERT, and remove
`timestamp = ?` from the close `UPDATE` entirely — the close time already has its own column,
`closed_at`.

```python
entry_time = entry_deal.time if entry_deal else target_deal.time
entry_dt_str = datetime.fromtimestamp(float(entry_time) - broker_offset, timezone.utc).isoformat()
```

### 4.2 Defects already fixed before this pass (confirmed still fixed)

| Defect | Where | Status |
|---|---|---|
| Fabricated fallback quotes became candles | `tradingview_provider.py` | fixed — refuses a fallback anchor, returns `None` |
| `expected_value` overwritten with the realised P&L on close | `database.py` | fixed — 0 of 144 rows now show the overwrite |
| `0.0` used as the "never closed" sentinel | `database.py` migration 5 | fixed — `NULL` for rows with no `closed_at` |
| Sub-floor stop distance | `institutional_entry_engine.py` | fixed — cap can no longer undercut the pip floor |
| Spread-blowout guard permanently disabling trailing stops / breakeven / partial closes on EURUSD, GBPUSD, USDJPY, AUDUSD | `position_monitor.py` | fixed — feeds the symbol's own typical spread |

---

## 5. Root cause #3 — the entry signal has no measured edge (not fixable by tuning)

This is the part I will not paper over. Measured on real MT5 data:

* **3 of 20 symbols beat always-long, versus 5 expected by chance alone.**
* **Deflated Sharpe Ratio > 0.95 is met by 0/20 symbols** over 94,937 rows = **327 independent bets.**
* The FVG (fair-value gap) entry model was evaluated as a replacement and **rejected**: 0/20 DSR, Sharpe
  negative on all 20. See `reports/FVG_STANDALONE_FINDINGS.md`.

The bot's live per-trade loss of **−2.46** is consistent with the independently backtested
**≈ −0.074 R/trade** (× ~$33 of risk ≈ −2.44). The live book is not misbehaving; it is faithfully
producing the negative expectancy that was measured out-of-sample.

**Therefore: no adjustment to strategy logic will make P&L "consistently positive."** Any change I made
to force a positive number would be fitting noise to a sample already shown to carry no edge — and
because the loss is dominated by two trades that are not the strategy at all, "tuning the strategy" is
aimed at the wrong 39%.

---

## 6. What I changed in this pass

| File | Change | Why |
|---|---|---|
| `jarvis/data/database.py` | Close `UPDATE` no longer writes `timestamp`; INSERT uses the entry deal's time | Stops the entry timestamp being overwritten by the exit time (§4.1) |
| `tests/test_trade_outcome_recorded.py` | +1 behavioural test: a real sync preserves the entry timestamp and still records the outcome | Pins the fix at behaviour level, not SQL text |
| `tests/test_forecast_not_overwritten.py` | Replaced the assertion that *pinned the bug*, +1 test asserting it stays gone | The old test asserted `"executor = ?, timestamp = ?" in src` — it was locking the defect in |
| `tests/test_position_sizing_ceiling.py` | **New**, 42 tests | Locks the risk ceiling: no size may exceed 0.5% except the documented 2× min-lot floor; high-conviction multipliers may only reduce risk |

### Non-vacuousness (proved by reverting)

* Re-introducing `timestamp = ?` → **2 tests go red**; restoring it → 26 pass.
* Removing the `min(ceiling, …)` clamp → **`test_high_conviction_multipliers_may_not_inflate_past_the_ceiling`
  goes red at 0.70% vs the 0.5% ceiling**; restoring it → 42 pass.

---

## 7. Verification

* `tests/test_trade_outcome_recorded.py` + `tests/test_forecast_not_overwritten.py` → **26 passed**.
* `tests/test_position_sizing_ceiling.py` → **42 passed**.
* Full suite (junit XML, not stdout — the harness truncates it): **`tests=3267 failures=0 errors=0
  skipped=2`, 0 failing testcases.** That is **+44 tests** over the 3223-test baseline, matching the 2
  timestamp tests + 42 sizing tests added here.

---

## 8. Full-suite status

| | Tests | Failures | Errors |
|---|---|---|---|
| Baseline entering this pass | 3223 | 0 | 0 |
| After this pass | **3267** | **0** | **0** |

---

## 9. What would actually move P&L

Ranked by measured impact, not by effort:

1. **Keep the manual book out of the automated book's statistics, or cap it.** 55% of the loss came from
   23 manual trades at −15.90 each. The engine's own 0.5% ceiling would have refused the −305 trade.
2. **Fix the data before judging the strategy.** Until §4.1 landed, every hold-time, session and
   windowed-P&L figure was computed from the exit time.
3. **Do not tune entries.** The measured DSR is 0/20. The honest options are to reduce size, reduce
   frequency, or find a different signal — not to adjust thresholds on a sample with no edge.
4. **Complete the spread-calibration reconciliation** (§J, unresolved): two harnesses disagree on the
   sign of the spread-cost lever because they use different exit models. One run with both pinned to the
   same exit model and symbol set would settle it.

---

## 10. Deliberately not done

* **No strategy-logic change to force a positive number.** Would be fitting noise; the edge is measured
  absent.
* **No deletion of the 157 synthetic rows.** They are evidence of a fixed defect, and the live DB is
  written by a running process — quarantining is a data decision, not a code fix. They are excluded from
  P&L by construction (`realized_pnl IS NULL`).
* **No change to `get_max_lot_cap`.** Documented as intentional; sizing binds first (§3).
* **No loopback-auth change.** Kept per your earlier decision.

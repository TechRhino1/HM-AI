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

### 4.3 A recovered bracket could be stored on the wrong side of entry — **fixed this pass**

**Symptom.** 24 rows carried a stop on the **wrong side** of entry — 14 BUY with `sl >= entry`, 10 SELL
with `sl <= entry`. The 20 closed ones among them were **20 winners totalling +37.38**, which a genuine
stop cannot produce.

**Root cause.** Not the order path — `trade_guard.validate_pre_execution` already refuses an inverted
bracket at build time, with a direction-aware check and an explicit rejection of an unrecognised bias.
The leak was at the **other end**: `sync_mt5_history` does not read `sl`/`tp` from the broker, it
recovers them by string-parsing the order comment (`[sl...]` / `[tp...]`), and that parse had **no
direction check**. A comment carrying the target's number was written straight into the `sl` column.
The `tp = 0` on 14 of the 24 rows is the same signature: the target had been consumed by the `sl` field.

**Fix.** Validate the parsed value against the direction before storing it, and refuse it when it cannot
have come from a valid order. A non-positive value still means "no tag parsed" and is left alone, so
this only rejects a value that is demonstrably on the wrong side.

```python
if entry_deal is not None:
    if not _bracket_side_ok(side, entry_p, sl_val, is_stop=True):
        logger.warning("Refusing a parsed stop for position %s: sl=%s ... ", pid, sl_val, entry_p)
        sl_val = 0.0
```

Only checked when the entry deal is in the window: without it `entry_p` falls back to
`target_deal.price`, which for a closed position is the **exit** price, and the comparison would be
against the wrong reference.

### 4.4 Two security items, settled by measurement

* **XML entity expansion (`news.py:210`) — measured, NOT a live exposure.** `ET.fromstring` on remote
  data looked like a billion-laughs DoS, and the standing note was "needs `defusedxml`, not a
  dependency". Measured on this runtime, that is wrong: **expat 2.8.1 blocks it.** Payloads at 6, 9 and
  12 nesting levels all raise `ParseError: limit on input amplification factor (from DTD and entities)
  breached` in **~0.4 s**. **No new dependency is needed.**
* **Unbounded remote reads — fixed.** All four remote fetches set a socket timeout but called
  `resp.read()` with no argument. A timeout bounds *time, not bytes*: a streaming endpoint can return
  hundreds of MB inside a 5-6 s window on a request/scheduler path. Added
  `jarvis/common/http.read_bounded`, which reads at most `limit + 1` bytes (so a body of exactly
  `limit` is accepted, without trusting `Content-Length`) and raises `ResponseTooLargeError`, a
  `ValueError` subclass so the existing `except Exception` handlers degrade it like any malformed
  payload. Wired into `news.py` (×2) and `tradingview_provider.py`.

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
| `jarvis/data/database.py` | `_bracket_side_ok` + a direction check on the parsed `sl`/`tp` | Stops a wrong-side stop being persisted (§4.3) |
| `jarvis/common/http.py` | **New.** `read_bounded` + `ResponseTooLargeError` | Bounds a remote read by bytes, not just time (§4.4) |
| `jarvis/market/news.py`, `jarvis/data/tradingview_provider.py` | Use `read_bounded` at all 3 remote read sites | Same |
| `tests/test_trade_outcome_recorded.py` | +1 behavioural test: a real sync preserves the entry timestamp and still records the outcome | Pins the fix at behaviour level, not SQL text |
| `tests/test_forecast_not_overwritten.py` | Replaced the assertion that *pinned the bug*, +1 test asserting it stays gone | The old test asserted `"executor = ?, timestamp = ?" in src` — it was locking the defect in |
| `tests/test_position_sizing_ceiling.py` | **New**, 42 tests | Locks the risk ceiling: no size may exceed 0.5% except the documented 2× min-lot floor |
| `tests/test_bracket_side_validation.py` | **New**, 14 tests | Predicate + a real sync with a wrong-side comment, **and a positive control** so a guard that rejects everything cannot pass |
| `tests/test_bounded_remote_read.py` | **New**, 11 tests | The cap, the boundary, the one-byte over-read, and that no unbounded read remains |

### Non-vacuousness (proved by reverting)

* Re-introducing `timestamp = ?` → **2 tests go red**; restoring it → 26 pass.
* Removing the `min(ceiling, …)` clamp → **`test_high_conviction_multipliers_may_not_inflate_past_the_ceiling`
  goes red at 0.70% vs the 0.5% ceiling**; restoring it → 42 pass.
* Disabling the bracket guard → **`sl` becomes `1.115`** (the target written into the stop field, i.e.
  the live defect reproduced) while the 13 other tests still pass; restoring it → 14 pass.

---

## 7. Verification

* `tests/test_trade_outcome_recorded.py` + `tests/test_forecast_not_overwritten.py` → **26 passed**.
* `tests/test_position_sizing_ceiling.py` → **42 passed**.
* `tests/test_bracket_side_validation.py` → **14 passed**.
* `tests/test_bounded_remote_read.py` → **11 passed**.
* Full suite (junit XML, not stdout — the harness truncates it): **`tests=3292 failures=0 errors=0
  skipped=2`, 0 failing testcases.** That is **+69 tests** over the 3223-test baseline.

---

## 8. Full-suite status

| | Tests | Failures | Errors |
|---|---|---|---|
| Baseline entering this work | 3223 | 0 | 0 |
| After the timestamp + sizing pass | 3267 | 0 | 0 |
| After the bracket + bounded-read pass | **3292** | **0** | **0** |

---

## 9. What would actually move P&L

Ranked by measured impact, not by effort:

1. **Keep the manual book out of the automated book's statistics, or cap it.** 55% of the loss came from
   23 manual trades at −15.90 each. The engine's own 0.5% ceiling would have refused the −305 trade.
2. **Fix the data before judging the strategy.** Until §4.1 landed, every hold-time, session and
   windowed-P&L figure was computed from the exit time.
3. **Do not tune entries.** The measured DSR is 0/20. The honest options are to reduce size, reduce
   frequency, or find a different signal — not to adjust thresholds on a sample with no edge.
4. **Trade count is the one lever with a measured magnitude — and it points at "fewer".** The §J sweep
   gives an unusually clean natural experiment: raising `max_spread_pips` (which makes the spread gate
   more permissive) added **408 trades** and moved total R by **−80.5**. Decomposing that delta into
   volume vs quality: **volume −100.9 R, quality +20.4 R (net −80.5)**. With the measurement's news
   confound removed, the **per-trade effect is net POSITIVE** — GBPUSD alone contributes +27.8 R of
   quality, i.e. the corrected spreads genuinely make the trades the engine keeps *better*. It still
   loses, entirely because it takes 408 more of them at a negative expectancy. In **4 of 8 symbols the
   per-trade result improves** while the total falls. So the engine's per-trade expectancy is ~−0.2 R
   and nearly insensitive to cost modelling; what moves the total is how often it trades. This is the
   same conclusion the live data reached in §2 (the bot's −2.46/trade ≈ the backtested −0.074 R/trade ×
   ~$33 risk), and it is the only lever here that does not require finding an edge first. It does
   **not** follow that tightening the gate makes the strategy profitable — the edge is still absent —
   only that the way to improve the total without an edge is to take fewer trades, not better-modelled
   ones. Full table: `docs/AUDIT-3-TRACKS-2026-09-23.md` §J.
4. **The spread-calibration lever is closed — it is ADVERSE, and that is now measured twice.** An
   earlier version of this report called it "noise, not an opportunity", with the sign crossing zero
   between `tp_r` 2.0 and 2.5 and a largest effect of 0.0011 R/trade. **That is withdrawn: it was
   produced by a broken instrument.** The harness rebuilt the spread registry from the live registry
   instead of a pristine snapshot, so `apply_registry(None)` was a no-op and **only the first symbol
   scanned ever had a genuine incumbent arm** — every later symbol was compared against itself, which
   is why the old run reported "only 3 of 8 symbols change". With the harness fixed, ΔTotal R is
   **ADVERSE at every point of the sweep** — −47.5, −86.9, −85.1, −65.8, −79.0 R as `tp_r` goes
   1.0 → 3.0 — and **7 of 8 symbols** change. The magnitude is the same order as the baseline itself,
   so this is material. It also restores §J's original decision and agrees with §J2's artefact: three
   independent measurements, three adverse readings. Spread calibration stays off, now on an
   instrument that can see the effect. Detail: `docs/AUDIT-3-TRACKS-2026-09-23.md` §J.

---

## 10. Deliberately not done

* **No strategy-logic change to force a positive number.** Would be fitting noise; the edge is measured
  absent.
* **No deletion of the 157 synthetic rows.** They are evidence of a fixed defect, and the live DB is
  written by a running process — quarantining is a data decision, not a code fix. They are excluded from
  P&L by construction (`realized_pnl IS NULL`).
* **No change to `get_max_lot_cap`.** Documented as intentional; sizing binds first (§3).
* **No loopback-auth change.** Kept per your earlier decision.

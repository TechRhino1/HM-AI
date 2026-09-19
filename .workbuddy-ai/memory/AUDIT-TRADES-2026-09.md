# Trade audit — 2026-09-19

Full audit of every persisted trade, per the request: P&L/fee correctness, missing or duplicated
entries, sign/direction, timestamps and ordering, sizing/exposure, risk-limit and margin
violations, currency/rounding, and edge cases (zero-quantity, negative prices, partial fills).

* **Tool:** `tools/audit_trades.py` (read-only, `mode=ro`; never writes to the databases).
* **Reproduce:** `AUDIT_EQUITY=763.45 python tools/audit_trades.py --db data [--json OUT]`
* **Scope:** `data/jarvis_history.db.executed_trades` (245 rows) and
  `data/jarvis_trade_memory.db.trade_records` (34 rows), plus the code that writes them.
* **Result:** **CRITICAL 8 · MAJOR 7 · MINOR 1 · INFO 2**, plus 2 code-only defects found by
  reading the writers (§M8, §I3).

---

## The one thing to read first

**The trade history is two different datasets in one table, and only one of them is real.**

136 of 245 rows (56%) were stamped locally by `log_trade()` and never reconciled to a broker
deal. They carry fabricated prices. The other 109 rows are real broker deals — and they sum to
**−$606.79** realised.

The discriminator is exact and comes from the code, not from a guess:

| Writer | Timestamp source | Format |
|---|---|---|
| `sync_mt5_history` (`database.py:228`) | broker deal epoch | **whole seconds** — `2026-08-27T12:19:41+00:00` |
| `log_trade` (`database.py:161`) | `datetime.now(timezone.utc)` | **microseconds** — `2026-09-16T14:47:53.246771+00:00` |

Every number in this report is therefore quoted as **real / synthetic** where the split matters.
Reporting the unsplit figure overstates the risk breaches by ~5.5x (150 → 27 real).

---

## CRITICAL

### C1 · 27 real trades breach the configured risk limit; one risked 39% of equity and lost $305
* **Where:** `data/jarvis_history.db.executed_trades`; `jarvis/risk/position_sizing.py:86-89`
* **Detail:** `config/settings.json:16` sets `max_risk_per_trade_pct = 0.5` → **$3.82** at $763.45
  equity. **27 real trades** exceed it. Worst is **ticket 935634011, XAUUSD, vol 1.00**: risked
  **$299.00 = 39.16% of equity (78.3x the limit)** and realised **−$305.00**. Next worst:
  ticket 928688529 SOLUSD vol 17.19 risked $84.23 (11.0%) and lost $99.70.
  A further **123** breaches sit on synthetic fabricated-price rows and are *not* real exposures.
* **Root cause:** `position_sizing.py:86-89` clamps to a **hardcoded literal 1.50**, not to the
  configured 0.5, and then multiplies by `invalidation_risk_coefficient` and `combined_scaler`
  *outside* that clamp — so the setting is never a real ceiling.
* **Fix:** clamp `effective_risk_pct` to the **configured** limit and apply the multipliers
  *inside* it. Add a hard pre-trade assertion that rejects any order whose
  `|entry − sl| × volume × contract_size` exceeds `equity × max_risk_per_trade_pct`.

### C2 · 56% of the trade history is synthetic: fabricated prices, never closed
* **Where:** `database.py:161` (`log_trade`) · priced by `jarvis/data/tradingview_provider.py:583`
* **Detail:** 136/245 rows. Only **13 distinct entry prices across 136 rows**, and they are
  impossible — XAUUSD at **2400.0** vs a real 4663.19; EURUSD at **1.0850 / 1.09** vs a real
  1.1642; **NAS100 and WTI both at 1.27**. All 136 have `realized_pnl = 0` and are never closed.
  111 of the 120 EURUSD rows are synthetic. Newest is **today**, so this is still happening.
* **Root cause:** `tradingview_provider.py:583` still fabricates `base_p = 1.0850` for EURUSD
  (65000 BTC, 3500 ETH, 150 SOL) when a quote cannot be resolved. `_paper_fill_price` in
  `mt5_client.py:283-298` was already fixed for exactly this — its docstring even records the
  old symptom ("GOLD filled at 2400.0 while the market was at 4328.88") — but the provider
  fallback was not.
* **Fix:** return `None` from the fallback instead of a constant and refuse the order. Add an
  `origin` column (`broker|paper|synthetic`) and a real fill time; exclude non-broker rows from
  every statistic. Quarantine the existing 136 rows.

### C3 · `realized_pnl` is a copy of the pre-trade forecast — 109/109 closed rows
* **Where:** `database.py:271-274` (INSERT) and `:282` (UPDATE)
* **Detail:** **all 109/109** closed rows (100%) have `realized_pnl` *exactly* equal to
  `expected_value`. `database.py:273` passes the same `pnl` variable into both columns. There is
  no `exit_price` column, so the realised result cannot have been derived from a fill.
  Every win rate, expectancy and R-multiple downstream reads this.
* **Fix:** persist real `exit_price` and fill volume; compute
  `realized_pnl = (exit − entry) × volume × contract_size × direction − fees`. Keep
  `expected_value` immutable and assert the two differ.

### C4 · 23 real trades have no stop-loss at all (unbounded risk)
* **Where:** `executed_trades.sl` (24 rows: 23 real, 1 synthetic)
* **Fix:** refuse to open any position without an invalidation level.

### C5 · Stop-loss recorded on the wrong side of entry — 20 rows (16 real)
* **Where:** `executed_trades.sl` vs `action`/`entry_price`
* **Detail:** 14 BUY with `sl >= entry`, 6 SELL with `sl <= entry`. **All 16 closed ones were
  winners (+$35.88, 16/16)** — a genuine stop on the losing side cannot produce that, so the
  value in `sl` is the **take-profit written into the sl field** (14 of 20 have `tp = 0`), or the
  `action` is recorded inverted. `tp` is *never* on the wrong side (0/245), so the corruption is
  specific to `sl`.
* **Root cause:** `database.py:251-262` recovers sl/tp by **string-parsing the order comment**
  (`raw_comment.split("[sl")[1]`). A missing or garbled comment yields 0.0; a mis-tagged one
  yields the wrong bracket.
* **Fix:** assert `BUY: sl < entry < tp`, `SELL: tp < entry < sl` at order build time and reject
  otherwise. Persist sl/tp as real columns at placement; stop parsing comments.

### C6 · `closed_at` equals the entry timestamp on 109/109 rows — the entry time was overwritten
* **Where:** `database.py:282-289` (`SET ... timestamp = ?`)
* **Detail:** every closed row appears zero-duration. **It is not `closed_at` that is wrong** —
  `database.py:287` writes the **exit** time into `timestamp`, so each sync destroys the true
  entry time. This is also what makes primary-key order non-chronological (§m1).
* **Fix:** remove `timestamp = ?` from the UPDATE; never rewrite the entry time.

### C7 · Partial closes are silently truncated
* **Where:** `database.py:200-203` — `pos_map[pid]["exit"] = d`
* **Detail:** each `DEAL_ENTRY_OUT` **overwrites** the previous one, so a position closed in two
  parts records only the final partial's profit, and `volume` is taken from
  `target_deal.volume` (the last deal) rather than the full position. `DEAL_ENTRY_INOUT` (2) and
  `DEAL_ENTRY_OUT_BY` (3) are ignored entirely, so reversals vanish.
* **Fix:** accumulate all exit deals; store `exit_volume` and a `partial_close` flag.

### C8 · Two different P&L definitions for the same trade (gross vs net)
* **Where:** `database.py:226` vs `jarvis/execution/state_synchronizer.py:73`
* **Detail:** `database.py:226` → `pnl = float(exit_deal.profit)` (**gross**, excludes fees).
  `state_synchronizer.py:73` → `sum(d.profit + d.swap + d.commission)` (**net**). Two consumers
  get two different numbers for one trade. Neither table has a `commission` or `swap` column, so
  the gap **cannot be measured from the data at all**.
* **Fix:** one shared P&L function; persist `commission`, `swap` and `net_pnl`.

---

## MAJOR

### M1 · The pre-trade forecast is destroyed at the moment of truth (109 rows)
`database.py:282` does `SET realized_pnl = ?, expected_value = ?` with the same value, so the
only column holding the forecast is overwritten by the outcome. The system can never score its
own forecasts against outcomes — exactly what the self-learning weights need.
**Fix:** never write realised P&L into `expected_value`; add an immutable `forecast_ev`.

### M2 · `0.0` is the "no stop / no target" sentinel (102 rows)
`sl=0` on 23, `tp=0` on 79. Indistinguishable from a real price, so no validation can tell
"unset" from "set to zero", and any risk math multiplying by the stop distance silently
computes against the entry. **Fix:** NULL, or an explicit `has_sl`/`has_tp` flag.

### M3 · `pnl = 0` is the "not closed" marker (15 rows, `trade_records`)
Indistinguishable from a genuine scratch trade; every average and win rate that includes them is
diluted toward zero. The same 15 rows carry `exit_price = 0`. **Fix:** NULL for unrealised.

### M4 · Negative bracket price (1 row)
Ticket **37779531, NAS100 BUY, entry 1.27, sl −40.28, tp 125.92**. A price cannot be negative;
NAS100 trades ~24,000, so the whole row is fabricated. **Fix:** reject any bracket price ≤ 0.

### M5 · The same four databases exist twice — root and `data/`
`jarvis_history.db`, `jarvis_trade_memory.db`, `jarvis_circuit_state.db`,
`jarvis_drawdown_state.db`. Root copies are stale (Sep 11) while `data/` copies are current;
whichever path the code resolves wins. The **drawdown baseline** lives here, so a stale copy can
resurrect a poisoned `peak_equity`. **Fix:** resolve the path in one place
(`jarvis/config/paths.py`) and archive the root copies.

### M6 · The two trade tables do not agree on which trades exist (213 tickets)
212 only in `executed_trades`, 1 only in `trade_records`. Written by different paths, never
reconciled. **Fix:** reconcile on ticket, or derive the learning table from the execution log.

### M7 · `executed_trades` has no `exit_price` column
Without it the realised P&L can never be recomputed or audited — which is why C3 survived.
**Fix:** add `exit_price` and `exit_volume`.

### M8 · No fee columns anywhere (code-level)
Neither table has `commission`/`swap`. MT5 reports them as separate deal fields; only `profit` is
persisted. **Fix:** add `commission`, `swap`, `net_pnl`.

---

## MINOR

### m1 · Rows are not in chronological order (26 of 245)
Primary-key order is not time order; anything paginating by `id` and assuming chronology (or
diffing consecutive rows) is wrong. Caused by C6. **Fix:** order by `timestamp`, never by `id`;
store broker time as an INTEGER epoch.

---

## INFO

* **Currency:** the account is USD and all symbols are USD-quoted except **USDCHF (1 trade)**.
  MT5 converts `deal.profit` to account currency, so persisted P&L is unaffected — but any risk
  recomputed in quote currency (as the audit tool does) is wrong for USDCHF. No FX conversion
  logic exists anywhere in the codebase.
* **Row counts:** `executed_trades` 245 (109 real / 136 synthetic); `trade_records` 34.

---

## Checks that came back CLEAN

These were explicitly requested and found no defects:

* **Zero or negative quantity** — 0 rows with `volume <= 0` in either table.
* **Negative or zero entry price** — 0 rows in `executed_trades`; 0 negatives in `trade_records`.
* **Duplicate tickets** — 0 duplicate `ticket` values in `executed_trades`, 0 in `trade_records`.
  One *near*-duplicate pair exists in `trade_records` (15862468 / 18878306: same symbol, side,
  entry, sl, tp, lots) — consistent with the synthetic fallback replaying an identical trade.
* **NULL tickets** — 0.
* **`is_win` vs `pnl` sign** — no contradictions.
* **`pnl` sign vs `(exit − entry) × lots × direction`** — no contradictions in `trade_records`
  (only 19 rows have a usable non-zero exit price, so this is weakly tested).

---

## Suggested order of repair

1. **C1** (hard risk cap) — this is live money; it is the only finding that can lose more today.
2. **C2** (stop persisting fabricated prices) — stops the bleeding in the data.
3. **C3 + M7 + M1** (real exit price, real `realized_pnl`, immutable forecast) — one schema
   change fixes all three.
4. **C6 + m1** (stop overwriting `timestamp`).
5. **C5** (bracket direction assertion) and **C8/M8** (fees).
6. **M5** (single db path) — do this before touching the drawdown baseline.

## Traps found while auditing (for next time)

* **`tp = 0` is a sentinel**, not a target at zero. Excluding it cut a "44 inverted targets"
  false positive down to the real 20.
* **`pnl = 0` means "not closed"**, not "lost nothing".
* **Do not compute risk from `|entry − sl|` on an inverted row** — that is not risk. And do not
  compute it at all on a synthetic row: it inflated the breach count 150 → 27.
* **Distinct `timestamp` precision is a reliable origin discriminator** — whole seconds = broker
  deal, microseconds = locally stamped.

#!/usr/bin/env python
"""Audit every persisted trade for arithmetic, integrity and risk defects.

WHY THIS EXISTS
---------------
The platform records trades in two places that are never reconciled
(`data/jarvis_history.db`.executed_trades and `data/jarvis_trade_memory.db`.trade_records),
and the realised P&L is written by the execution path rather than recomputed from
prices. So a wrong number is persisted as a fact and then read back as truth by
everything downstream (win rate, the self-learning weights, the R-multiple
stats). Nothing in the suite checks that the stored P&L is even arithmetically
consistent with the stored prices.

This is READ-ONLY. It opens the databases with mode=ro and never writes.

Usage:  python tools/audit_trades.py [--db DIR] [--json OUT]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

# Severity: 1 = critical (wrong money), 2 = major, 3 = minor, 4 = informational.
SEV = {1: "CRITICAL", 2: "MAJOR", 3: "MINOR", 4: "INFO"}


def connect(path):
    if not os.path.exists(path):
        return None
    # mode=ro: an audit must not be able to alter what it audits.
    return sqlite3.connect("file:%s?mode=ro" % os.path.abspath(path).replace("\\", "/"), uri=True)


def cols(conn, table):
    return [r[1] for r in conn.execute('PRAGMA table_info("%s")' % table)]


class Report:
    def __init__(self):
        self.findings = []

    def add(self, sev, area, title, detail, where, fix, count=None):
        self.findings.append({
            "sev": sev, "area": area, "title": title, "detail": detail,
            "where": where, "fix": fix, "count": count,
        })

    def sorted(self):
        return sorted(self.findings, key=lambda f: (f["sev"], f["area"], f["title"]))


def iso(s):
    return (s or "")[:25]


def audit_executed(conn, rep, label):
    if conn is None:
        return
    t = "executed_trades"
    c = cols(conn, t)
    q = conn.execute
    total = q("select count(*) from %s" % t).fetchone()[0]
    rep.add(4, "executed_trades", "row count", "%s: %d rows" % (label, total), label, "-", total)

    def has(*names):
        return all(n in c for n in names)

    # ---- 0. synthetic vs broker origin ----
    # THE discriminator, derived from the code and not guessed:
    #   * sync_mt5_history (database.py:228) formats with
    #     datetime.fromtimestamp(<int epoch>, utc).isoformat() -> WHOLE seconds.
    #   * log_trade (database.py:161) formats with
    #     datetime.now(timezone.utc).isoformat() -> MICROSECONDS.
    # So a fractional-second timestamp means the row was stamped locally at
    # placement time and never reconciled against a broker deal. Those rows
    # carry fabricated prices (see tradingview_provider.py:583) and must be
    # excluded from every risk/P&L number, or the audit over-reports ~5x.
    synth = q("select count(*) from %s where timestamp like '%%.%%'" % t).fetchone()[0]
    broker = total - synth
    if synth:
        n_pnl0 = q("select count(*) from %s where timestamp like '%%.%%' "
                   "and (realized_pnl is null or realized_pnl = 0)" % t).fetchone()[0] if has("realized_pnl") else 0
        prices = q("select count(distinct entry_price) from %s where timestamp like '%%.%%'"
                   % t).fetchone()[0] if has("entry_price") else 0
        rep.add(1, "data origin",
                "over half the trade history is synthetic: locally stamped, fabricated prices, "
                "never closed",
                "%d of %d rows (%.0f%%) carry a MICROSECOND timestamp, i.e. they were written by "
                "log_trade() with datetime.now() and never reconciled to a broker deal (whole-second "
                "timestamps). They hold only %d distinct entry prices across %d rows, and those "
                "prices are impossible (XAUUSD 2400.0 vs a real 4663.19; EURUSD 1.0850/1.09 vs a "
                "real 1.1642; NAS100 and WTI both 1.27). %d of them have realized_pnl = 0 and are "
                "never closed, so every win rate, trade count and exposure figure computed over "
                "the whole table is inflated. The fabricated quotes come from the hardcoded "
                "fallback still present at tradingview_provider.py:583 (base_p = 1.0850 for EURUSD, "
                "65000 BTC, 3500 ETH, 150 SOL). mt5_client._paper_fill_price was already fixed for "
                "this; the provider fallback was not."
                % (synth, total, 100.0 * synth / total, prices, synth, n_pnl0),
                "%s.%s, written by jarvis/data/database.py:161 (log_trade) and priced by "
                "jarvis/data/tradingview_provider.py:583" % (label, t),
                "Refuse to persist a trade whose price came from a fallback: return None, not a "
                "constant, and mark the row rejected. Add an origin column "
                "(broker|paper|synthetic) plus the real fill time, and exclude non-broker rows "
                "from every statistic. Backfill-delete or quarantine the existing synthetic rows.",
                synth)

    # ---- 1. realised P&L must not be a copy of the pre-trade expectation ----
    if has("realized_pnl", "expected_value"):
        def cnt(w):
            return q("select count(*) from %s where %s" % (t, w)).fetchone()[0]
        # Only CLOSED rows have a realised result at all. On open rows pnl is 0
        # and expected_value still holds the forecast, so counting them as
        # "equal" would dilute the rate and hide a 100% defect.
        closed = cnt("realized_pnl is not null and realized_pnl != 0")
        same_closed = cnt("realized_pnl is not null and realized_pnl != 0 "
                          "and abs(realized_pnl - expected_value) < 1e-9")
        if closed and same_closed == closed:
            rep.add(1, "P&L", "realized_pnl is a copy of the pre-trade forecast, never "
                    "computed from prices",
                    "ALL %d/%d closed rows (%.0f%%) have realized_pnl EXACTLY equal to "
                    "expected_value. database.py:271-274 writes the same `pnl` variable into "
                    "both columns, and there is no exit_price column in this table, so the "
                    "realised result can never have been derived from a fill. Every downstream "
                    "win-rate, expectancy and R-multiple figure reads this."
                    % (same_closed, closed, 100.0 * same_closed / closed),
                    "%s.%s, written by jarvis/data/database.py:271-274 and :282" % (label, t),
                    "Persist the actual exit price and fill volume, then compute "
                    "realized_pnl = (exit-entry)*volume*contract_size*direction - fees. "
                    "Keep expected_value as its own column and assert the two differ.", closed)
        elif same_closed:
            rep.add(2, "P&L", "some realized_pnl values equal expected_value",
                    "%d/%d closed rows have realized_pnl exactly equal to expected_value."
                    % (same_closed, closed), "%s.%s" % (label, t),
                    "Investigate the writer; the realised figure must come from fills.",
                    same_closed)
        # The forecast is destroyed on close: the UPDATE writes pnl into
        # expected_value too, so forecast-vs-outcome can never be compared.
        if has("closed_at"):
            clobbered = cnt("closed_at is not null and realized_pnl is not null "
                            "and abs(realized_pnl - expected_value) < 1e-9 and realized_pnl != 0")
            if clobbered:
                rep.add(2, "P&L", "the pre-trade forecast is overwritten by the outcome on close",
                        "On %d closed rows expected_value now equals the realised P&L, because "
                        "database.py:282 does `SET realized_pnl = ?, expected_value = ?` with the "
                        "same value. The only column holding the pre-trade forecast is destroyed "
                        "at the moment of truth, so the system can never score its own forecasts "
                        "against outcomes -- which is exactly what the self-learning weights need."
                        % clobbered,
                        "jarvis/data/database.py:282 (UPDATE ... expected_value = ?)",
                        "Never write realised P&L into expected_value. Add a separate "
                        "forecast_ev column populated at order time and leave it immutable.",
                        clobbered)

    # ---- 2. closed_at must differ from timestamp ----
    if has("closed_at", "timestamp"):
        same_ts = q("select count(*) from %s where closed_at is not null "
                    "and closed_at = timestamp" % t).fetchone()[0]
        nonnull = q("select count(*) from %s where closed_at is not null" % t).fetchone()[0]
        if nonnull and same_ts:
            rep.add(1 if same_ts == nonnull else 2, "timestamps",
                    "closed_at equals the entry timestamp (the entry time was overwritten)",
                    "%d/%d closed rows have closed_at identical to timestamp, so a trade is "
                    "recorded as closing in the same second it opened. A zero-duration trade with "
                    "a non-zero realised P&L is arithmetically impossible. ROOT CAUSE: it is not "
                    "closed_at that is wrong -- database.py:287 does `SET ... timestamp = ?` with "
                    "the EXIT time, so every sync overwrites the entry timestamp with the close "
                    "time. The true entry time is destroyed, and because the row keeps its "
                    "original id while its timestamp jumps forward, primary-key order stops being "
                    "chronological (see the out-of-order finding)."
                    % (same_ts, nonnull),
                    "%s.%s, written by jarvis/data/database.py:282-289 (`timestamp = ?`)"
                    % (label, t),
                    "Remove `timestamp = ?` from the UPDATE and never rewrite the entry time. "
                    "Add a separate last_synced_at if the sync time is what is wanted.",
                    same_ts)

    # ---- 3a. no fee columns -> the gross/net P&L split is unmeasurable ----
    # database.py:226 persists only exit_deal.profit (GROSS). state_synchronizer.py:73
    # publishes profit + swap + commission (NET). Two P&L values for one trade,
    # and neither table can prove which is right because the fee columns do not exist.
    fee_cols = [x for x in c if x in ("commission", "swap", "fees", "fee", "total_fees")]
    if not fee_cols:
        rep.add(2, "fees", "no commission/swap column, so gross and net P&L cannot be reconciled",
                "Columns present: %s. MT5 reports profit, swap and commission as separate deal "
                "fields, but only the profit is persisted (database.py:226 uses "
                "`exit_deal.profit`), while state_synchronizer.py:73 publishes "
                "`profit + swap + commission`. The same trade therefore has two different P&L "
                "values depending on which path wrote it, and because the fee columns do not "
                "exist the discrepancy cannot even be measured from the data."
                % ", ".join(c), "%s.%s" % (label, t),
                "Add commission, swap and a computed net_pnl column; persist all three at write "
                "time and use net_pnl everywhere. Make database.py and state_synchronizer.py "
                "share one P&L function.", None)

    # ---- 3. no exit price at all ----
    for cand in ("exit_price", "close_price", "closed_price"):
        if cand in c:
            break
    else:
        rep.add(2, "schema", "executed_trades has no exit price column",
                "Columns present: %s. Without an exit price the realised P&L can never be "
                "recomputed or audited, which is why defect #1 survived."
                % ", ".join(c), "%s.%s" % (label, t),
                "Add exit_price (and exit_volume for partial closes) to the schema.")

    # ---- 4. duplicates ----
    if has("ticket"):
        dup = q("select ticket, count(*) n from %s where ticket is not null "
                "group by ticket having n > 1" % t).fetchall()
        if dup:
            rep.add(1, "duplicates", "duplicate ticket numbers",
                    "%d ticket(s) appear more than once: %s"
                    % (len(dup), ", ".join("%s x%d" % (d[0], d[1]) for d in dup[:8])),
                    "%s.%s.ticket" % (label, t),
                    "Make ticket UNIQUE and catch the insert error instead of re-inserting.",
                    len(dup))
        nullt = q("select count(*) from %s where ticket is null" % t).fetchone()[0]
        if nullt:
            rep.add(2, "integrity", "rows with NULL ticket",
                    "%d rows have no broker ticket, so they cannot be reconciled against MT5."
                    % nullt, "%s.%s.ticket" % (label, t),
                    "Only persist orders the broker accepted; a refused order is not a trade.",
                    nullt)

    # ---- 5. impossible quantities and prices ----
    if has("volume"):
        for cond, desc in (("volume <= 0", "non-positive"), ("volume is null", "NULL")):
            n = q("select count(*) from %s where %s" % (t, cond)).fetchone()[0]
            if n:
                rep.add(1, "edge cases", "%s volume (zero-quantity trades)" % desc,
                        "%d rows have %s volume." % (n, desc), "%s.%s.volume" % (label, t),
                        "Reject zero/negative lots at the sizing step and at the DB insert.", n)
    if has("entry_price"):
        for cond, desc in (("entry_price <= 0", "non-positive"), ("entry_price is null", "NULL")):
            n = q("select count(*) from %s where %s" % (t, cond)).fetchone()[0]
            if n:
                rep.add(1, "edge cases", "%s entry price" % desc,
                        "%d rows have %s entry_price." % (n, desc),
                        "%s.%s.entry_price" % (label, t),
                        "Validate the fill price before persisting; a non-positive price is a "
                        "bad tick or a missing quote, not a trade.", n)

    # ---- 6. SL/TP: sentinel zeros, missing brackets, and inverted brackets ----
    # 0.0 is used throughout this table to mean "not set". It must be excluded
    # before any direction test, or every unset bracket reads as a price of zero
    # and is reported as an inverted stop -- a large, confident false positive.
    if has("action", "entry_price", "sl", "tp"):
        zero_sl = q("select count(*) from %s where sl = 0" % t).fetchone()[0]
        zero_tp = q("select count(*) from %s where tp = 0" % t).fetchone()[0]
        if zero_sl or zero_tp:
            rep.add(2, "direction", "0.0 is used as the 'no stop / no target' sentinel",
                    "sl=0 on %d rows, tp=0 on %d rows. 0.0 is indistinguishable from a real "
                    "price, so no validation can tell 'unset' from 'set to zero', and any "
                    "downstream risk math that multiplies by the stop distance silently "
                    "computes against the entry price instead."
                    % (zero_sl, zero_tp), "%s.%s sl/tp" % (label, t),
                    "Store NULL for an unset bracket, or add an explicit has_sl/has_tp flag.",
                    zero_sl + zero_tp)
        inv_buy = q("select count(*) from %s where upper(action) like 'BUY%%' "
                    "and sl > 0 and sl >= entry_price" % t).fetchone()[0]
        inv_sell = q("select count(*) from %s where upper(action) like 'SELL%%' "
                     "and sl > 0 and sl <= entry_price" % t).fetchone()[0]
        if inv_buy or inv_sell:
            # Is sl really a stop? A genuine stop on the losing side closes the
            # trade at a loss immediately. Measure the outcome instead of assuming.
            inv_where = ("(upper(action) like 'BUY%%' and sl > 0 and sl >= entry_price) or "
                         "(upper(action) like 'SELL%%' and sl > 0 and sl <= entry_price)")
            inv_n = q("select count(*) from %s where %s" % (t, inv_where)).fetchone()[0]
            inv_closed = q("select count(*) from %s where (%s) and closed_at is not null"
                           % (t, inv_where)).fetchone()[0]
            inv_wins = q("select count(*) from %s where (%s) and closed_at is not null "
                         "and realized_pnl > 0" % (t, inv_where)).fetchone()[0]
            inv_pnl = q("select coalesce(sum(realized_pnl),0) from %s where (%s) "
                        "and closed_at is not null" % (t, inv_where)).fetchone()[0]
            no_tp = q("select count(*) from %s where (%s) and (tp is null or tp = 0)"
                      % (t, inv_where)).fetchone()[0]
            verdict = ("Those %d closed ones are %d WINNERS totalling %+.2f, which a genuine "
                       "stop on the losing side cannot produce -- so the value in sl is the "
                       "TAKE-PROFIT level written into the sl field (%d of the %d have tp = 0), "
                       "or the action is recorded inverted. tp is never on the wrong side, so "
                       "the corruption is specific to sl."
                       % (inv_closed, inv_wins, inv_pnl, no_tp, inv_n)
                       if inv_closed and inv_wins == inv_closed else
                       "%d of them are closed; outcomes do not settle which field is wrong."
                       % inv_closed)
            rep.add(1, "direction", "stop-loss recorded on the WRONG side of entry",
                    "%d BUY with sl >= entry, %d SELL with sl <= entry (non-zero stops only). "
                    "%s Either way the persisted bracket does not match the direction, so any "
                    "risk, R-multiple or expectancy computed from it is wrong."
                    % (inv_buy, inv_sell, verdict), "%s.%s sl vs entry" % (label, t),
                    "Assert the bracket against the direction at order build time: BUY needs "
                    "sl<entry<tp, SELL needs tp<entry<sl, and reject the order otherwise. "
                    "Stop recovering sl/tp by string-parsing the order comment "
                    "(database.py:251-262) -- persist them as real columns at placement time.",
                    inv_buy + inv_sell)
        bad_tp_buy = q("select count(*) from %s where upper(action) like 'BUY%%' "
                       "and tp > 0 and tp <= entry_price" % t).fetchone()[0]
        bad_tp_sell = q("select count(*) from %s where upper(action) like 'SELL%%' "
                        "and tp > 0 and tp >= entry_price" % t).fetchone()[0]
        if bad_tp_buy or bad_tp_sell:
            rep.add(1, "direction", "take-profit on the losing side of entry",
                    "%d BUY with tp<=entry, %d SELL with tp>=entry (non-zero tp only)."
                    % (bad_tp_buy, bad_tp_sell), "%s.%s tp vs entry" % (label, t),
                    "Validate direction on write.", bad_tp_buy + bad_tp_sell)
        # A negative price is physically impossible and is the one edge case a
        # sentinel of 0.0 hides: 0.0 is excluded as "unset", but -40.28 is not.
        neg = q("select ticket, symbol, entry_price, sl, tp from %s where sl < 0 or tp < 0"
                % t).fetchall()
        if neg:
            rep.add(1, "edge cases", "negative stop-loss or take-profit price",
                    "%d row(s) carry a negative bracket price, e.g. %s. A price cannot be "
                    "negative, and any risk or R:R computed from it is meaningless."
                    % (len(neg), neg[:3]), "%s.%s sl/tp" % (label, t),
                    "Reject any bracket price <= 0 at order build time; never persist a "
                    "derived/negative quote.", len(neg))

    # ---- 6b. per-trade risk vs the configured limit ----
    if has("action", "entry_price", "sl", "volume", "symbol"):
        try:
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from jarvis.data.symbol_registry import resolve as _resolve
            from jarvis.config.settings import SETTINGS as _S
            limit_pct = float(getattr(getattr(_S, "risk", None), "max_risk_per_trade_pct", 0.5))
        except Exception:
            _resolve, limit_pct = None, 0.5
        equity = os.environ.get("AUDIT_EQUITY")
        equity = float(equity) if equity else None
        if equity is None:
            rep.add(4, "risk", "per-trade risk not checked (no equity)",
                    "Pass AUDIT_EQUITY=<current equity> to enable the risk-limit check.",
                    label, "-", None)
        elif _resolve is not None:
            limit_usd = equity * limit_pct / 100.0
            over, worst, nostop = [], None, 0
            over_synth, nostop_synth = 0, 0
            for tk, sym, act, ep, sl, vol, ts in q(
                    "select ticket, symbol, action, entry_price, sl, volume, timestamp from %s "
                    "where entry_price > 0 and volume > 0" % t):
                spec = _resolve(sym)
                cs = spec.contract_size if spec else None
                if not cs:
                    continue
                # A fractional-second timestamp means a locally stamped row whose
                # price came from a fabricated fallback. Risk computed on it is
                # not a real breach -- counting it inflates the finding ~5x.
                is_synth = ts is not None and "." in str(ts)
                a = str(act).upper()
                if not sl or sl <= 0:
                    nostop += 1
                    nostop_synth += 1 if is_synth else 0
                    continue
                if a.startswith("BUY"):
                    if sl >= ep:
                        continue           # already reported as inverted
                    risk = (ep - sl) * vol * cs
                else:
                    if sl <= ep:
                        continue
                    risk = (sl - ep) * vol * cs
                if risk > limit_usd:
                    if is_synth:
                        over_synth += 1
                    else:
                        over.append((tk, sym, round(risk, 2), round(risk / equity * 100, 2)))
            if nostop:
                rep.add(1, "risk", "trades with NO stop-loss (unbounded risk)",
                        "%d trades have sl = 0, i.e. no stop at all (%d of them real broker "
                        "trades, %d synthetic). Risk is unbounded and the sizer cannot compute "
                        "a distance-based size for them." % (nostop, nostop - nostop_synth,
                                                             nostop_synth),
                        "%s.%s sl" % (label, t),
                        "Refuse to open a position without an invalidation level.", nostop)
            if over:
                over.sort(key=lambda r: -r[2])
                worst = over[0]
                rep.add(1, "risk", "per-trade risk exceeds max_risk_per_trade_pct",
                        "%d REAL trades (of %d sized) risk more than %.2f%% of equity (=$%.2f at "
                        "$%.2f). Worst: ticket %s %s risking $%.2f = %.2f%% of equity (%.1fx the "
                        "limit). A further %d breaches sit on synthetic fabricated-price rows and "
                        "are NOT real exposures. Note position_sizing.py caps effective_risk_pct "
                        "at a HARDCODED 1.50 while the configured limit is %.2f, and then "
                        "multiplies it by invalidation_risk_coefficient and combined_scaler -- "
                        "so the setting is never a real cap."
                        % (len(over), len(over) + over_synth, limit_pct, limit_usd, equity,
                           worst[0], worst[1], worst[2], worst[3], worst[3] / limit_pct,
                           over_synth, limit_pct),
                        "%s.%s and jarvis/risk/position_sizing.py:86-89" % (label, t),
                        "Clamp effective_risk_pct to the CONFIGURED limit, not to a literal "
                        "1.50, and apply the multipliers inside that clamp.", len(over))

    # ---- 7. insertion order vs chronological order ----
    if has("id", "timestamp"):
        rows = q("select id, timestamp from %s where timestamp is not null order by id" % t).fetchall()
        inversions = 0
        worst = None
        prev = None
        for i, ts in rows:
            if prev is not None:
                try:
                    if ts < prev:
                        inversions += 1
                        if worst is None or (prev - ts) > 0:
                            worst = (prev, ts, i)
                except TypeError:
                    pass
            prev = ts
        if inversions:
            rep.add(3, "ordering", "rows inserted out of chronological order",
                    "%d of %d rows have a timestamp earlier than the preceding id. Primary-key "
                    "order is therefore NOT time order; anything that paginates by id and assumes "
                    "chronology (or diffs consecutive rows) is wrong."
                    % (inversions, len(rows)), "%s.%s id vs timestamp" % (label, t),
                    "Order by timestamp, never by id. Better: store broker time as an INTEGER "
                    "epoch so ordering is a numeric comparison, not a string one.", inversions)

    # ---- 8. timestamp format / timezone consistency ----
    if has("timestamp"):
        rows = q("select timestamp from %s where timestamp is not null" % t).fetchall()
        naive = sum(1 for (s,) in rows if s and ("+" not in s[10:] and "Z" not in s and "T" in s))
        if naive:
            rep.add(2, "timestamps", "timestamps stored without a timezone offset",
                    "%d rows have no UTC offset. MT5 broker time is NOT UTC, so a naive string is "
                    "ambiguous by the broker's offset." % naive, "%s.%s.timestamp" % (label, t),
                    "Store broker time as an epoch integer plus the offset, or always tz-aware ISO.",
                    naive)
        mixed = {("iso" if "T" in s else ("epoch" if s.lstrip("-").isdigit() else "other"))
                 for (s,) in rows if s}
        if len(mixed) > 1:
            rep.add(2, "timestamps", "mixed timestamp formats in one column",
                    "Formats seen: %s. A string comparison across formats silently mis-sorts."
                    % ", ".join(sorted(mixed)), "%s.%s.timestamp" % (label, t),
                    "Normalise to one format on write.", None)

    return c


def audit_memory(conn, rep, label):
    if conn is None:
        return
    t = "trade_records"
    try:
        c = cols(conn, t)
    except Exception:
        return
    q = conn.execute
    total = q("select count(*) from %s" % t).fetchone()[0]
    rep.add(4, "trade_records", "row count", "%s: %d rows" % (label, total), label, "-", total)

    def has(*names):
        return all(n in c for n in names)

    # ---- pnl vs is_win ----
    if has("pnl", "is_win"):
        mismatch = q("select ticket, pnl, is_win from %s where pnl is not null and is_win is not null "
                     "and ((pnl > 0 and is_win = 0) or (pnl < 0 and is_win = 1) "
                     "or (pnl = 0 and is_win not in (0,1)))" % t).fetchall()
        if mismatch:
            rep.add(1, "P&L", "is_win contradicts the sign of pnl",
                    "%d rows where the win flag disagrees with the P&L sign, e.g. %s"
                    % (len(mismatch), mismatch[:5]), "%s.%s is_win vs pnl" % (label, t),
                    "Derive is_win from pnl (pnl > 0), never store it independently.", len(mismatch))
        # NB: do NOT also report "trades with exactly zero P&L" here. It is the
        # same rows as the MAJOR "pnl = 0 used as the not-closed marker" finding
        # below; emitting both double-counts one defect at two severities.

    # ---- pnl vs (exit-entry) ----
    if has("pnl", "entry_price", "exit_price", "lots", "trade_type"):
        rows = q("select ticket, trade_type, entry_price, exit_price, lots, pnl from %s "
                 "where pnl is not null and entry_price is not null and exit_price is not null "
                 "and lots is not null" % t).fetchall()
        bad = []
        for tk, tt, ep, xp, lots, pnl in rows:
            d = 1.0 if str(tt).upper().startswith("BUY") else -1.0
            naive = (xp - ep) * lots * d
            # No contract size / fee model in this table, so compare the SIGN and
            # the order of magnitude rather than exact equality.
            if naive == 0 or pnl == 0:
                continue
            if (naive > 0) != (pnl > 0):
                bad.append((tk, tt, ep, xp, lots, pnl, round(naive, 4)))
        if bad:
            rep.add(1, "P&L", "pnl sign contradicts (exit-entry) x lots x direction",
                    "%d rows where the stored P&L has the opposite sign to the price move, "
                    "e.g. %s" % (len(bad), bad[:5]), "%s.%s pnl" % (label, t),
                    "Recompute pnl from prices at write time and unit-test the sign for both "
                    "directions.", len(bad))

    # ---- missing exit on closed trades ----
    # pnl = 0 is this table's "not closed yet" marker, so it is NOT a realised
    # result and must not be reported as one. Only a non-zero pnl is a claim.
    if has("exit_price", "pnl"):
        n = q("select count(*) from %s where pnl is not null and pnl <> 0 "
              "and (exit_price is null or exit_price = 0)" % t).fetchone()[0]
        if n:
            rep.add(1, "P&L", "realised P&L recorded with no exit price",
                    "%d rows have a NON-ZERO pnl but no usable exit_price, so the number cannot "
                    "be verified." % n, "%s.%s exit_price" % (label, t),
                    "Do not write pnl until the close fill has a price.", n)
        zero = q("select count(*) from %s where pnl = 0" % t).fetchone()[0]
        if zero:
            rep.add(2, "edge cases", "pnl = 0 used as the 'not closed' marker",
                    "%d rows carry pnl = 0, which is indistinguishable from a genuine scratch "
                    "trade. Any average, win rate or expectancy that includes them is diluted "
                    "toward zero." % zero, "%s.%s.pnl" % (label, t),
                    "Use NULL for unrealised and keep 0 only for a true break-even.", zero)

    # ---- quantities ----
    if has("lots"):
        for cond, desc in (("lots <= 0", "non-positive"), ("lots is null", "NULL")):
            n = q("select count(*) from %s where %s" % (t, cond)).fetchone()[0]
            if n:
                rep.add(1, "edge cases", "%s lots" % desc,
                        "%d rows have %s lots." % (n, desc), "%s.%s.lots" % (label, t),
                        "Validate sizing before persisting.", n)
    if has("entry_price", "exit_price"):
        for colname in ("entry_price", "exit_price"):
            n = q("select count(*) from %s where %s <= 0" % (t, colname)).fetchone()[0]
            if n:
                rep.add(1, "edge cases", "%s is non-positive" % colname,
                        "%d rows." % n, "%s.%s.%s" % (label, t, colname),
                        "Reject non-positive prices.", n)

    # ---- duplicates ----
    if has("ticket"):
        dup = q("select ticket, count(*) n from %s where ticket is not null "
                "group by ticket having n > 1" % t).fetchall()
        if dup:
            rep.add(1, "duplicates", "duplicate ticket numbers",
                    "%d ticket(s) duplicated: %s" % (len(dup), dup[:8]),
                    "%s.%s.ticket" % (label, t), "Add a UNIQUE constraint on ticket.", len(dup))

    return c


def cross_check(hist, mem, rep):
    if hist is None or mem is None:
        return
    try:
        ht = {r[0] for r in hist.execute("select ticket from executed_trades where ticket is not null")}
        mt = {r[0] for r in mem.execute("select ticket from trade_records where ticket is not null")}
    except Exception:
        return
    only_h = ht - mt
    only_m = mt - ht
    if only_h or only_m:
        rep.add(2, "reconciliation", "the two trade tables do not agree on which trades exist",
                "%d ticket(s) only in executed_trades, %d only in trade_records. They are written "
                "by different paths and never reconciled."
                % (len(only_h), len(only_m)),
                "data/jarvis_history.db vs data/jarvis_trade_memory.db",
                "Reconcile on ticket, or derive the learning table from the execution log so it "
                "cannot drift.", len(only_h) + len(only_m))


def duplicate_dbs(rep, root="."):
    """Two copies of the same db names, at the project root and under data/."""
    names = ["jarvis_history.db", "jarvis_trade_memory.db",
             "jarvis_circuit_state.db", "jarvis_drawdown_state.db"]
    # One root cause, one finding. Emitting four separate entries hides the fact
    # that a single path-resolution defect produces all of them.
    found = []
    for n in names:
        a = os.path.join(root, n)
        b = os.path.join(root, "data", n)
        if os.path.exists(a) and os.path.exists(b):
            sa, sb = os.path.getsize(a), os.path.getsize(b)
            ma = datetime.fromtimestamp(os.path.getmtime(a)).strftime("%Y-%m-%d %H:%M")
            mb = datetime.fromtimestamp(os.path.getmtime(b)).strftime("%Y-%m-%d %H:%M")
            found.append("%s: root %dB/%s vs data/ %dB/%s" % (n, sa, ma, sb, mb))
    if found:
        rep.add(2, "integrity",
                "the same database exists twice, at the root and under data/ (%d db)" % len(found),
                "; ".join(found) + ". Whichever path the code resolves wins, and the other "
                "silently holds stale state.",
                "jarvis_circuit_state.db, jarvis_drawdown_state.db, jarvis_history.db, "
                "jarvis_trade_memory.db (each at both paths)",
                "Resolve the db path in ONE place (jarvis/config/paths.py) and delete or archive "
                "the stale root copies. Note the drawdown baseline lives here, so a stale copy "
                "can resurrect a poisoned peak_equity.", len(found))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data", help="directory holding the trade databases")
    ap.add_argument("--json", default=None, help="write findings as JSON to this path")
    args = ap.parse_args()

    rep = Report()
    hist = connect(os.path.join(args.db, "jarvis_history.db"))
    mem = connect(os.path.join(args.db, "jarvis_trade_memory.db"))

    audit_executed(hist, rep, "data/jarvis_history.db")
    audit_memory(mem, rep, "data/jarvis_trade_memory.db")
    cross_check(hist, mem, rep)
    duplicate_dbs(rep, os.path.dirname(os.path.abspath(args.db)) or ".")

    print("=" * 78)
    print("TRADE AUDIT  (read-only)")
    print("=" * 78)
    cur = None
    for f in rep.sorted():
        if f["sev"] != cur:
            cur = f["sev"]
            print("\n" + "-" * 78)
            print("%s" % SEV[cur])
            print("-" * 78)
        n = "" if f["count"] is None else "  [n=%d]" % f["count"]
        print("\n* %s%s" % (f["title"], n))
        print("    where : %s" % f["where"])
        print("    detail: %s" % f["detail"])
        print("    fix   : %s" % f["fix"])

    counts = {}
    for f in rep.findings:
        counts[SEV[f["sev"]]] = counts.get(SEV[f["sev"]], 0) + 1
    print("\n" + "=" * 78)
    print("SUMMARY: " + ", ".join("%s=%d" % (k, counts[k]) for k in
                                  ["CRITICAL", "MAJOR", "MINOR", "INFO"] if k in counts))
    print("=" * 78)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rep.sorted(), fh, indent=2)
        print("wrote", args.json)
    return 1 if counts.get("CRITICAL") else 0


if __name__ == "__main__":
    sys.exit(main())

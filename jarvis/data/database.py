import sqlite3
import json
import logging
import time
from datetime import datetime, timezone
import threading
import os
import re
from typing import Optional

from jarvis.config.paths import resolve_db_path, ensure_data_dir
from jarvis.data.broker_time import broker_utc_offset
from jarvis.data.schema_version import add_columns, migrate

# Where a journalled price came from. The whole point of `origin` (D1): a
# realised-P&L statistic computed over the journal is meaningless if simulated
# fills and fallback quotes are indistinguishable from real ones — measured on
# data/jarvis_history.db, 158 of 269 rows are engine-logged and 0 of them have
# ever closed, so mixing them in silently understates every result.
# "unknown" is in the tuple because it is a value we STORE — the caller's
# choices are the other three. Anything not in the tuple lands as "unknown"
# rather than being guessed at.
ORIGINS = ("broker", "paper", "synthetic", "unknown")

#: How long a read path will reuse the last broker sync before paying for
#: another one. A `/api/history` read used to re-read 30 days of deals every
#: time; on the live server that parked request threads inside the terminal
#: attach and timed the endpoint out.
_MT5_SYNC_MIN_INTERVAL_SEC = 30.0


def _origin_filter(origin):
    """Normalise an origin filter to the values that can exist — `[]` if none can.

    A caller passing something that is not an origin has almost certainly made a
    typo (`origin="real"`, `origin="live"`), and answering that with the whole
    table is the worst possible response: it looks exactly like a working filter
    that happened to match everything.
    """
    if isinstance(origin, str):
        wanted = [origin]
    else:
        try:
            wanted = list(origin)
        except TypeError:
            return []
    return [o for o in wanted if o in ORIGINS]


def _pnl_stats(closed, total, winners, losers, open_rows):
    """One realised-P&L bucket.

    `expectancy` is None, not 0.0, when nothing has closed. 0.0 reads as
    "measured and break-even"; None reads as "not measurable", which is the
    truth for an empty bucket — and for the 158 engine-logged rows that have
    never closed.
    """
    return {
        "closed": int(closed),
        "open": int(open_rows),
        "realised_pnl": round(float(total), 2),
        "expectancy": round(float(total) / closed, 4) if closed else None,
        "winners": int(winners),
        "losers": int(losers),
    }


def _empty_pnl():
    return _pnl_stats(0, 0.0, 0, 0, 0)

# Bumped when the shape of `executed_trades` changes. Every file written before
# this existed is 0, which means "none of these have run".
#   1 — every column the old sweep used to add, plus `position_id` (D2)
#   2 — `origin`, so a row says where its price came from (D1)
#   3 — an index on `origin`, so it can be QUERIED (see _migration_3)
SCHEMA_VERSION = 3


def _migration_1(conn) -> None:
    """Everything the old sweep used to add, plus `position_id` (D2).

    Idempotent, so it is safe both for a file that predates all of it and for
    one that already has some of these columns from an earlier run of the sweep.
    """
    add_columns(conn, "executed_trades", {
        "realized_pnl": "REAL DEFAULT 0.0",
        "executor": "TEXT DEFAULT 'BOT (AI)'",
        "session_name": "TEXT DEFAULT 'UNKNOWN'",
        "is_prime_session": "INTEGER DEFAULT 1",
        "adx": "REAL DEFAULT 0.0",
        "plus_di": "REAL DEFAULT 0.0",
        "minus_di": "REAL DEFAULT 0.0",
        "spread_pips": "REAL DEFAULT 0.0",
        "mtf_alignment": "TEXT DEFAULT ''",
        "threats_json": "TEXT DEFAULT '[]'",
        "features_json": "TEXT DEFAULT '{}'",
        # `timestamp` means different things depending on where the row came
        # from — entry time for an engine-logged trade, exit time for one synced
        # from a closed broker deal. `closed_at` is unambiguous: null unless the
        # row really is a closed trade, so a chart can draw an exit marker
        # without guessing.
        "closed_at": "TEXT",
        # D2: `log_trade` stored whatever the broker call returned as "ticket",
        # which for a market order is `result.order` — the ORDER ticket.
        # `sync_mt5_history` closes rows by `deal.position_id`, the POSITION id.
        # Those are different numbers, so a row written at entry could never be
        # matched by the exit deal and stayed open forever (measured: 155 rows
        # with closed_at NULL and realized_pnl 0.0). Persist both, join on the
        # position id.
        "position_id": "INTEGER",
    })


def _migration_2(conn) -> None:
    """D1: tag every row with where its price came from.

    `broker` — the row is backed by a real broker deal. `paper` — simulated
    fills. `synthetic` — a fallback price standing in for a missing quote.

    The backfill can only go as far as the evidence allows, and the evidence is
    timestamp precision: `sync_mt5_history` formats an int epoch (whole
    seconds), `log_trade` uses `datetime.now()` (microseconds). So a whole-second
    timestamp means the row came from — or was later reconciled with — a real
    deal, and can be called `broker`.

    A microsecond timestamp only tells us the ENGINE wrote it, not whether that
    engine was trading real money or paper: nothing in the row records the
    execution mode. Those are `unknown`, not guessed. Measured on
    data/jarvis_history.db: 111 whole-second rows (110 closed) vs 158
    microsecond rows, of which **zero** have ever closed — which is what the
    un-matchable join (D2) looks like from the other end.

    New rows always carry a real value; `unknown` exists only for history.
    """
    add_columns(conn, "executed_trades", {"origin": "TEXT"})
    conn.execute(
        "UPDATE executed_trades SET origin = 'broker' "
        "WHERE origin IS NULL AND timestamp IS NOT NULL AND timestamp NOT LIKE '%.%'"
    )
    conn.execute("UPDATE executed_trades SET origin = 'unknown' WHERE origin IS NULL")


def _migration_3(conn) -> None:
    """Index `origin` so it can actually be queried.

    D1 gave every row an origin but left no way to select on it: without an
    index, "real money only" means a full scan of a table that grows without
    bound, and the statistic that motivated the column in the first place stayed
    as expensive as it was before it existed. A column nobody can filter on is
    documentation, not data.

    The index is composite — `(origin, timestamp)` — because every real question
    is "rows of THIS origin, most recent first": `origin` alone is the leftmost
    prefix, so a bare `WHERE origin = ?` uses it too, and `timestamp` then serves
    the ordering and the `days` window in the same scan instead of a separate
    sort. One index, both shapes.

    `origin` is added defensively first. This step can be retried against a file
    where step 2 was recorded but its ALTER did not land, and `CREATE INDEX` on a
    column that is not there raises — which would abort the whole init block the
    same way the old `position_id` index did.
    """
    add_columns(conn, "executed_trades", {"origin": "TEXT"})
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_executed_trades_origin_ts "
        "ON executed_trades(origin, timestamp)"
    )


MIGRATIONS = {1: _migration_1, 2: _migration_2, 3: _migration_3}

# Executor tags, matched as TOKENS. The old `"ai" in comment_lower` substring test
# also matched "trailing", "pair", "main", "wait" and "chair", so a manual trade
# commented "trailing stop" was filed as BOT (AI) and skewed the per-executor
# performance stats. The magic number is authoritative; these are the fallback.
#
# The manual side is a plain substring on purpose: "manual"/"desk" are long and
# distinctive, and our own tag is the compound "ManualDesk", which lowercases to
# "manualdesk" - word boundaries would never match it. "ai" is the opposite case:
# three characters is short enough to hide inside ordinary words, so it needs
# boundaries on both sides.
_BOT_TAG_RX = re.compile(r"(?<![a-z0-9])(?:hm[_\s-]?algo2?|hma2|jarvis[_\s-]?auto|ai)(?![a-z0-9])")
_MANUAL_TAG_RX = re.compile(r"(?:manual|desk)")

logger = logging.getLogger("JARVIS_Database")

class SQLiteTradeDB:
    def __init__(self, db_path="jarvis_history.db"):
        # Anchor relative paths on the repo data dir; a CWD-relative path meant
        # the learning engine could read a DB the live loop never wrote to.
        self.db_path = resolve_db_path(db_path)
        if self.db_path and self.db_path != ":memory:":
            ensure_data_dir()
        self._local = threading.local()
        #: Read paths call `sync_mt5_history`; this keeps a read from turning
        #: into a broker round-trip on every request. PER INSTANCE on purpose —
        #: class-level state leaked the window across unrelated databases, so a
        #: freshly built DB could inherit another one's stamp and silently skip
        #: its first sync. See the method for the measurement.
        self._last_mt5_sync = 0.0
        self._init_db()

    def _get_conn(self):
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
            self._local.conn.execute("PRAGMA journal_mode=WAL;")
            self._local.conn.execute("PRAGMA synchronous=NORMAL;")
            self._local.conn.execute("PRAGMA cache_size=-64000;")
            self._local.conn.execute("PRAGMA temp_store=MEMORY;")
        return self._local.conn

    def close(self):
        if hasattr(self._local, "conn"):
            try:
                self._local.conn.close()
            except Exception:
                pass
            del self._local.conn

    def __del__(self):
        self.close()

    def _init_db(self):
        conn = self._get_conn()
        try:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS executed_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket INTEGER,
                    -- The MT5 POSITION id, which is not the same number as the
                    -- order ticket `send_market_order` returns. See `position_id`
                    -- in the migration map below.
                    position_id INTEGER,
                    symbol TEXT,
                    action TEXT,
                    entry_price REAL,
                    sl REAL,
                    tp REAL,
                    volume REAL,
                    timestamp TEXT,
                    ai_score REAL,
                    regime TEXT,
                    expected_value REAL,
                    executor TEXT DEFAULT 'BOT (AI)',
                    session_name TEXT DEFAULT 'UNKNOWN',
                    is_prime_session INTEGER DEFAULT 1,
                    adx REAL DEFAULT 0.0,
                    plus_di REAL DEFAULT 0.0,
                    minus_di REAL DEFAULT 0.0,
                    spread_pips REAL DEFAULT 0.0,
                    mtf_alignment TEXT DEFAULT '',
                    threats_json TEXT DEFAULT '[]',
                    features_json TEXT DEFAULT '{}'
                )
            ''')
            # Create fast query lookup indices
            conn.execute("CREATE INDEX IF NOT EXISTS idx_executed_trades_ticket ON executed_trades(ticket);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_executed_trades_symbol ON executed_trades(symbol);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_executed_trades_timestamp ON executed_trades(timestamp);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_executed_trades_regime ON executed_trades(regime);")

            # D3: bring the file up to the current shape one numbered step at a
            # time. This replaces an unconditional "add whatever column is
            # missing" sweep, which ran on every open and could express neither
            # ordering, a backfill, nor a refusal — and which, because it was
            # inside the same try/except as everything else, could abort partway
            # through while the file still claimed to be fine.
            #
            # Steps run BEFORE the index on `position_id`: an index on a column
            # the table does not have yet raises, and that used to abort the
            # whole init block.
            version = migrate(conn, "executed_trades", SCHEMA_VERSION, MIGRATIONS)
            if version == SCHEMA_VERSION:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_executed_trades_position_id ON executed_trades(position_id);")
            conn.commit()

            logger.info("SQLite database initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize SQLite DB: {e}")

    def log_trade(
        self,
        ticket: int,
        symbol: str,
        action: str,
        entry: float,
        sl: float,
        tp: float,
        volume: float,
        score: float,
        regime: str,
        ev: float,
        executor: str = "BOT (AI)",
        session_name: str = "UNKNOWN",
        is_prime_session: bool = True,
        adx: float = 0.0,
        plus_di: float = 0.0,
        minus_di: float = 0.0,
        spread_pips: float = 0.0,
        mtf_alignment: str = "",
        threats_json: str = "[]",
        features_json: str = "{}",
        position_id: Optional[int] = None,
        origin: Optional[str] = None
    ):
        """Journal an entry.

        `ticket` is the order ticket the broker call returned; `position_id` is
        the MT5 position id the exit deal will later be keyed on. They are
        different numbers — pass both, or the row can never be closed (D2).

        `origin` is where the price came from: `broker`, `paper` or `synthetic`.
        Anything else — including None — is recorded as `unknown` rather than
        guessed, so a statistic that filters on it can never silently include
        rows of unknown provenance.
        """
        # ORIGINS is a tuple, not a mapping. A `.get()` here raised
        # AttributeError on EVERY call — and `log_trade` swallows its
        # exceptions into a log line, so the symptom was not a crash but a
        # journal that silently stopped recording trades at all.
        origin = origin if origin in ORIGINS else "unknown"
        conn = self._get_conn()
        try:
            conn.execute('''
                INSERT INTO executed_trades (
                    ticket, position_id, origin, symbol, action, entry_price, sl, tp, volume, timestamp,
                    ai_score, regime, expected_value, executor, session_name,
                    is_prime_session, adx, plus_di, minus_di, spread_pips,
                    mtf_alignment, threats_json, features_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                ticket, position_id, origin, symbol, action, entry, sl, tp, volume,
                datetime.now(timezone.utc).isoformat(),
                score, regime, ev, executor, session_name, int(is_prime_session),
                adx, plus_di, minus_di, spread_pips, mtf_alignment, threats_json, features_json
            ))
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to log trade to DB: {e}")

    def sync_mt5_history(self, days: int = 30, limit: int = 100):
        """Syncs executed and closed trades from MT5 broker history into SQLite database.

        Called from the READ path (`fetch_recent_trades`), so it must never
        attach to the terminal itself. It used to do exactly that:

            if not mt5.terminal_info():
                mt5.initialize()          # unbounded, and bypasses the one gate

        `initialize()` with no terminal to attach to tries to launch one and
        blocks 60-100s inside native code HOLDING THE GIL — and because this ran
        on the request thread, every `/api/history` request froze the whole
        process for that long. Measured on the live server: `py-spy` showed two
        request threads parked in `fetch_recent_trades -> sync_mt5_history`
        while `/api/history`, `/api/telemetry_state`, `/api/market-status` and
        `/api/radar` all timed out and the UI pages served fine.

        The gate also gives the honest answer: no terminal in this process means
        there is nothing to sync FROM, so returning early is correct, not a
        degradation.
        """
        from jarvis.data.broker_symbols import ensure_mt5_terminal

        if not ensure_mt5_terminal():
            return

        # A read must not trigger a sync per request. With a terminal up, every
        # `/api/history` call re-read 30 days of deals; the throttle keeps the
        # read cheap and leaves the polling to the background synchroniser.
        now = time.time()
        if now - self._last_mt5_sync < _MT5_SYNC_MIN_INTERVAL_SEC:
            return
        self._last_mt5_sync = now

        try:
            import MetaTrader5 as mt5
            from datetime import datetime, timedelta, timezone
            from jarvis.data.symbol_registry import resolve

            # Broker server time can be ahead of local machine time (e.g. GMT+3 / EET)
            from_date = datetime.now() - timedelta(days=days)
            to_date = datetime.now() + timedelta(days=7)
            deals = mt5.history_deals_get(from_date, to_date)
            if not deals:
                return

            # `deal.time` is broker SERVER time, so reading it as UTC dated every
            # synced trade 2-3 hours into the future (XM runs GMT+2/+3). Derive the
            # offset from the deals' own symbols and store true UTC.
            deal_symbols = sorted({str(d.symbol) for d in deals if getattr(d, "symbol", None)})
            broker_offset = broker_utc_offset(mt5_module=mt5, symbols=deal_symbols)

            conn = self._get_conn()
            pos_map = {}
            for d in deals:
                pid = d.position_id
                if not pid:
                    continue
                if pid not in pos_map:
                    pos_map[pid] = {"entry": None, "exit": None}
                if d.entry == 0:  # DEAL_ENTRY_IN
                    pos_map[pid]["entry"] = d
                elif d.entry == 1:  # DEAL_ENTRY_OUT
                    pos_map[pid]["exit"] = d

            for pid, pdata in pos_map.items():
                entry_deal = pdata["entry"]
                exit_deal = pdata["exit"]
                target_deal = exit_deal or entry_deal
                if not target_deal:
                    continue

                raw_sym = target_deal.symbol
                spec = resolve(raw_sym)
                clean_sym = spec.canonical if spec else raw_sym.replace(".i#", "").replace("#", "")
                
                # Determine buy/sell side accurately
                if entry_deal:
                    side = "BUY" if entry_deal.type == 0 else "SELL"
                elif exit_deal:
                    side = "BUY" if exit_deal.type == 1 else "SELL"
                else:
                    side = "BUY"

                entry_p = float(entry_deal.price) if entry_deal else float(target_deal.price)
                vol = float(target_deal.volume)
                pnl = float(exit_deal.profit) if exit_deal else 0.0
                target_time = exit_deal.time if exit_deal else target_deal.time
                dt_str = datetime.fromtimestamp(
                    float(target_time) - broker_offset, timezone.utc
                ).isoformat()
                
                # Determine executor (BOT vs MANUAL)
                magic_num = getattr(target_deal, "magic", 0)
                raw_comment = str(exit_deal.comment if exit_deal else target_deal.comment or "")
                comment_lower = raw_comment.lower()
                
                # Determine executor (BOT vs MANUAL). The magic number decides:
                # the engine stamps 888999 on everything it places, manual tickets
                # carry 0. Comments are only consulted for foreign tickets.
                if magic_num == 888999:
                    exec_label = "BOT (AI)"
                elif magic_num == 0:
                    exec_label = "MANUAL"
                elif _MANUAL_TAG_RX.search(comment_lower):
                    exec_label = "MANUAL"
                elif _BOT_TAG_RX.search(comment_lower):
                    exec_label = "BOT (AI)"
                else:
                    exec_label = "BOT (AI)" if magic_num > 0 else "MANUAL"

                sl_val = 0.0
                tp_val = 0.0
                if "[sl" in raw_comment:
                    try:
                        sl_val = float(raw_comment.split("[sl")[1].split("]")[0].strip())
                    except Exception:
                        pass
                if "[tp" in raw_comment:
                    try:
                        tp_val = float(raw_comment.split("[tp")[1].split("]")[0].strip())
                    except Exception:
                        pass

                # Find the row this position belongs to.
                #
                # D2: `pid` is the POSITION id. The row written when the trade
                # opened may hold it in `position_id`, or — for every row that
                # predates that column — in `ticket`, because the old code stored
                # `result.order` there and `ticket` was all we had. Matching on
                # `ticket` alone silently missed both cases: an engine-logged row
                # keyed by order ticket never equalled a position id, so it was
                # never updated and stayed open with realized_pnl 0.0 forever.
                # Prefer the position id; fall back to the ticket for legacy rows.
                cur = conn.cursor()
                cur.execute(
                    "SELECT id FROM executed_trades "
                    "WHERE position_id = ? OR (position_id IS NULL AND ticket = ?) "
                    "ORDER BY (position_id IS NOT NULL) DESC, id ASC LIMIT 1",
                    (pid, pid),
                )
                row = cur.fetchone()
                if not row:
                    regime_str = "TREND_BULL" if side == "BUY" else "TREND_BEAR"
                    # These rows ARE broker deals — they are reconstructed from
                    # `history_deals_get`, so `origin` is not in doubt here.
                    #
                    # AI5 / exec-summary #4: `ai_score` and `expected_value` used
                    # to be written as `85.0` and `pnl`. Neither is known here —
                    # a deal reconstructed from broker history carries no decision,
                    # so there is no forecast to record, and 85.0 was a fabricated
                    # score that every one of these rows shared. `expected_value`
                    # set to the realised P&L was the worse half: it made the
                    # forecast column mean "outcome" for reconstructed rows and
                    # "forecast" for engine-logged ones, which is why
                    # `self_learning` could not tell them apart. NULL is honest:
                    # no forecast was made.
                    conn.execute('''
                        INSERT INTO executed_trades (ticket, position_id, origin, symbol, action, entry_price, sl, tp, volume, timestamp, ai_score, regime, expected_value, realized_pnl, executor, closed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (pid, pid, "broker", clean_sym, side, entry_p, sl_val, tp_val, vol, dt_str, None, regime_str, None, pnl, exec_label,
                          dt_str if exit_deal else None))
                else:
                    # Update realized PnL, executor, and close timestamp for completed positions.
                    # `closed_at` is only ever written when there really is an exit
                    # deal — rewriting it to null on a later sync would erase a
                    # close time we already knew.
                    # Keyed on the matched row id, not re-matched, so the UPDATE
                    # can never hit a different row than the SELECT just found.
                    # AI5 / exec-summary #4: `expected_value` is NOT updated here.
                    #
                    # It used to be set to `pnl` alongside `realized_pnl`, which
                    # overwrote the FORECAST with the OUTCOME the moment a trade
                    # closed — measured on the live journal: every closed row had
                    # expected_value == realized_pnl. That destroys the forecast,
                    # so forecast accuracy can never be measured (it blocks AI10
                    # calibration and any Brier scoring), and it is why
                    # `self_learning` was averaging the very thing it was supposed
                    # to predict. The forecast is written once, at open, and is
                    # left alone here.
                    conn.execute('''
                        UPDATE executed_trades
                        SET realized_pnl = ?, executor = ?, timestamp = ?,
                            -- A row the broker's own history has now matched is
                            -- proven real, whatever it was labelled at entry.
                            origin = CASE WHEN origin IS NULL OR origin IN ('unknown', 'synthetic')
                                          THEN 'broker' ELSE origin END,
                            position_id = CASE WHEN ? IS NOT NULL THEN ? ELSE position_id END,
                            closed_at = CASE WHEN ? IS NOT NULL THEN ? ELSE closed_at END,
                            sl = CASE WHEN ? > 0 THEN ? ELSE sl END,
                            tp = CASE WHEN ? > 0 THEN ? ELSE tp END
                        WHERE id = ?
                    ''', (pnl, exec_label, dt_str,
                          pid, pid,
                          dt_str if exit_deal else None, dt_str if exit_deal else None,
                          sl_val, sl_val, tp_val, tp_val, row[0]))
            conn.commit()

        except Exception as e:
            logger.error(f"Failed to sync MT5 history: {e}")

    def fetch_recent_trades(self, limit=100, days=None, origin=None):
        """Most recent journal rows, newest first.

        `days` narrows the window. It defaults to None — no window — so the
        callers that predate it (the classic terminal, the console) keep exactly
        the behaviour they have always had.

        The dashboard passes the user's Window selection here. Until it did, the
        selection reached only the MT5 half of /api/history and left journal rows
        unfiltered, so a "1 day" window answered with a month of trades: measured
        2026-09-17, `days=1` returned 185 rows whose oldest was 24 days old.
        The column is `timestamp` because that is the field the existing sort
        orders by, and filtering on the same field the sort uses keeps the two
        consistent.

        Note the internal sync still uses its own 30-day budget: it decides how
        much history to *populate*, which is a different question from how much
        to *display*.

        `origin` keeps rows whose price came from the given origin(s) — pass one
        string, or a collection to allow several. It is what makes D1 usable:
        "broker" is the only origin that is real money. The filter is validated
        against :data:`ORIGINS` and an unknown value selects nothing rather than
        silently widening to everything, because a caller asking for
        `origin="real"` must not be handed the full table and believe it filtered.
        """
        self.sync_mt5_history(days=30, limit=limit)
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            # Stored timestamps carry a +00:00 offset; SQLite's datetime()
            # parses that and normalises to UTC, which is what datetime('now')
            # returns too, so the comparison is apples to apples.
            clauses = []
            params = []
            if days is not None:
                clauses.append("datetime(timestamp) >= datetime('now', ?)")
                params.append(f"-{max(1, int(days))} days")
            if origin is not None:
                wanted = _origin_filter(origin)
                if not wanted:
                    # Asked for origins that cannot exist. Return nothing: the
                    # alternative (ignoring the filter) looks like a working
                    # filter that happens to agree with no filter at all.
                    return []
                clauses.append(f"origin IN ({','.join('?' * len(wanted))})")
                params.extend(wanted)

            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(limit)
            cur.execute(
                "SELECT * FROM executed_trades" + where +
                " ORDER BY datetime(timestamp) DESC, id DESC LIMIT ?",
                params)
            columns = [description[0] for description in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"Failed to fetch trades: {e}")
            return []

    def realised_pnl_by_origin(self, days=None):
        """Realised P&L split by where each fill price came from.

        This is the statistic D1 exists to make possible. Mixed together, a
        simulated fill and a real one are one number that describes neither —
        measured on data/jarvis_history.db, 158 of 269 rows are engine-logged and
        none of them have ever closed, so pooling them drags every average toward
        zero and understates the result.

        Only CLOSED rows contribute: `realized_pnl = 0.0` is the sentinel for
        "not closed" (there is no `exit_price`, so an open row carries 0.0), and
        counting those would divide by a denominator that is mostly unfinished
        trades. A genuine scratch exit is therefore invisible — a known limit of
        the stored data, not of this query.

        `days` windows on `timestamp`; None means no window. Returns origins that
        are actually present, plus `ALL` (everything pooled, i.e. the number to
        distrust) and `BROKER_ONLY` (the number to believe).
        """
        conn = self._get_conn()
        params = []
        where = "WHERE realized_pnl <> 0"
        if days is not None:
            where += " AND datetime(timestamp) >= datetime('now', ?)"
            params.append(f"-{max(1, int(days))} days")

        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT origin, COUNT(*), SUM(realized_pnl), "
                "SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) "
                "FROM executed_trades " + where + " GROUP BY origin",
                params)
            rows = cur.fetchall()

            # Open rows per origin, so a reader can see how much of the book is
            # still unresolved before trusting any average.
            cur.execute(
                "SELECT origin, COUNT(*) FROM executed_trades "
                "WHERE realized_pnl = 0 OR realized_pnl IS NULL "
                + (" AND datetime(timestamp) >= datetime('now', ?)" if days is not None else "")
                + " GROUP BY origin",
                params if days is not None else [])
            open_by_origin = {r[0]: r[1] for r in cur.fetchall()}
        except Exception as e:
            logger.error(f"Failed to aggregate realised P&L: {e}")
            return {"by_origin": {}, "ALL": _empty_pnl(), "BROKER_ONLY": _empty_pnl()}

        by_origin = {}
        for origin, closed, total, winners, losers in rows:
            by_origin[origin if origin in ORIGINS else "unknown"] = _pnl_stats(
                closed or 0, float(total or 0.0), winners or 0, losers or 0,
                open_by_origin.get(origin, 0))

        def _pool(keys):
            closed = sum(by_origin[k]["closed"] for k in keys if k in by_origin)
            wins = sum(by_origin[k]["winners"] for k in keys if k in by_origin)
            loss = sum(by_origin[k]["losers"] for k in keys if k in by_origin)
            total = sum(by_origin[k]["realised_pnl"] for k in keys if k in by_origin)
            opens = sum(by_origin[k]["open"] for k in keys if k in by_origin)
            return _pnl_stats(closed, total, wins, loss, opens)

        return {
            "by_origin": by_origin,
            "ALL": _pool(list(by_origin)),
            "BROKER_ONLY": _pool(["broker"]),
        }

TRADE_DB = SQLiteTradeDB()

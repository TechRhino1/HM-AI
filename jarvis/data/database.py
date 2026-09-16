import sqlite3
import json
import logging
from datetime import datetime, timezone
import threading
import os
import re

from jarvis.config.paths import resolve_db_path, ensure_data_dir
from jarvis.data.broker_time import broker_utc_offset

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

            # Ensure newly added columns exist in existing tables (safe schema migration)
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(executed_trades)")
            cols = [row[1] for row in cur.fetchall()]
            new_cols = {
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
                "features_json": "TEXT DEFAULT '{}'"
            }
            for col_name, col_def in new_cols.items():
                if col_name not in cols:
                    try:
                        conn.execute(f"ALTER TABLE executed_trades ADD COLUMN {col_name} {col_def}")
                    except Exception:
                        pass
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
        features_json: str = "{}"
    ):
        conn = self._get_conn()
        try:
            conn.execute('''
                INSERT INTO executed_trades (
                    ticket, symbol, action, entry_price, sl, tp, volume, timestamp,
                    ai_score, regime, expected_value, executor, session_name,
                    is_prime_session, adx, plus_di, minus_di, spread_pips,
                    mtf_alignment, threats_json, features_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                ticket, symbol, action, entry, sl, tp, volume, datetime.now(timezone.utc).isoformat(),
                score, regime, ev, executor, session_name, int(is_prime_session),
                adx, plus_di, minus_di, spread_pips, mtf_alignment, threats_json, features_json
            ))
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to log trade to DB: {e}")

    def sync_mt5_history(self, days: int = 30, limit: int = 100):
        """Syncs executed and closed trades from MT5 broker history into SQLite database."""
        try:
            import MetaTrader5 as mt5
            from datetime import datetime, timedelta, timezone
            from jarvis.data.symbol_registry import resolve
            
            if not mt5.terminal_info():
                mt5.initialize()

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

                # Check if ticket already exists
                cur = conn.cursor()
                cur.execute("SELECT id FROM executed_trades WHERE ticket = ?", (pid,))
                row = cur.fetchone()
                if not row:
                    regime_str = "TREND_BULL" if side == "BUY" else "TREND_BEAR"
                    conn.execute('''
                        INSERT INTO executed_trades (ticket, symbol, action, entry_price, sl, tp, volume, timestamp, ai_score, regime, expected_value, realized_pnl, executor)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (pid, clean_sym, side, entry_p, sl_val, tp_val, vol, dt_str, 85.0, regime_str, pnl, pnl, exec_label))
                else:
                    # Update realized PnL, executor, and close timestamp for completed positions
                    conn.execute('''
                        UPDATE executed_trades 
                        SET realized_pnl = ?, expected_value = ?, executor = ?, timestamp = ?, 
                            sl = CASE WHEN ? > 0 THEN ? ELSE sl END, 
                            tp = CASE WHEN ? > 0 THEN ? ELSE tp END
                        WHERE ticket = ?
                    ''', (pnl, pnl, exec_label, dt_str, sl_val, sl_val, tp_val, tp_val, pid))
            conn.commit()

        except Exception as e:
            logger.error(f"Failed to sync MT5 history: {e}")

    def fetch_recent_trades(self, limit=100):
        self.sync_mt5_history(days=30, limit=limit)
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM executed_trades ORDER BY datetime(timestamp) DESC, id DESC LIMIT ?", (limit,))
            columns = [description[0] for description in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"Failed to fetch trades: {e}")
            return []

TRADE_DB = SQLiteTradeDB()

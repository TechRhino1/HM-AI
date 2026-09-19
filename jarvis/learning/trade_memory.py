"""
HM Algo 2.0 — Persistent Trade Memory & Journaling Engine.
Logs rich execution records, market snapshots, MFE/MAE excursions, and decision context to SQLite.
"""
import os
import sqlite3
import json
import logging
import threading
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone

from jarvis.config.paths import resolve_db_path

logger = logging.getLogger("JARVIS_TradeMemory")

class TradeMemory:
    def __init__(self, db_path: str = "jarvis_trade_memory.db"):
        # Anchored on the repo data dir — see jarvis.config.paths.
        db_path = resolve_db_path(db_path)
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA cache_size=-32000;")
        self._init_db()

    def _checkpoint_locked(self) -> None:
        """Do the checkpoint. Caller must already hold `_lock`."""
        if not self._conn:
            return
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except Exception as e:
            logger.debug(f"trade_memory checkpoint failed: {e}")

    def checkpoint(self) -> None:
        """Fold the WAL back into the main database file.

        With `journal_mode=WAL` and `synchronous=NORMAL`, committed rows live in
        `jarvis_trade_memory.db-wal`, not in the `.db`. Measured here: the `.db`
        alone held 16 of 35 trades while `.db` + `-wal` held all 35, so **any
        backup, copy or zip of the `.db` file by itself silently loses more than
        half the journal**. TRUNCATE also resets the WAL so it cannot grow
        without bound.
        """
        with self._lock:
            self._checkpoint_locked()

    def close(self):
        with self._lock:
            if self._conn:
                # Checkpoint BEFORE closing: otherwise everything committed in
                # this session stays in the -wal and a copy of the .db alone
                # loses it.
                self._checkpoint_locked()
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def __del__(self):
        self.close()

    def _init_db(self):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trade_records (
                    ticket INTEGER PRIMARY KEY,
                    symbol TEXT,
                    timestamp TEXT,
                    trade_type TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    sl REAL,
                    tp REAL,
                    lots REAL,
                    pnl REAL,
                    is_win INTEGER,
                    regime TEXT,
                    strategy TEXT,
                    model_confidence REAL,
                    adversarial_penalty REAL,
                    expected_value REAL,
                    mfe REAL,
                    mae REAL,
                    reasoning TEXT,
                    quality_gate TEXT,
                    ml_features TEXT
                )
            """)
            # Check if ml_features column exists (for backward compatibility if table exists)
            cur.execute("PRAGMA table_info(trade_records)")
            columns = [info[1] for info in cur.fetchall()]
            if 'ml_features' not in columns:
                cur.execute("ALTER TABLE trade_records ADD COLUMN ml_features TEXT")
            if 'triple_barrier_label' not in columns:
                cur.execute("ALTER TABLE trade_records ADD COLUMN triple_barrier_label INTEGER DEFAULT 0")
            
            self._conn.commit()


    def record_trade(self, trade_data: Dict[str, Any]):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO trade_records VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                trade_data.get("ticket", int(datetime.now().timestamp())),
                trade_data.get("symbol", "UNKNOWN"),
                trade_data.get("timestamp", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
                trade_data.get("type", "BUY"),
                trade_data.get("entry", 0.0),
                trade_data.get("exit", 0.0),
                trade_data.get("sl", 0.0),
                trade_data.get("tp", 0.0),
                trade_data.get("lots", 0.01),
                trade_data.get("pnl", 0.0),
                1 if trade_data.get("pnl", 0.0) > 0 else 0,
                trade_data.get("regime", "NEUTRAL"),
                trade_data.get("strategy", "TREND_FOLLOWING"),
                trade_data.get("model_confidence", 0.5),
                trade_data.get("adversarial_penalty", 0.0),
                trade_data.get("expected_value", 0.0),
                trade_data.get("mfe", 0.0),
                trade_data.get("mae", 0.0),
                json.dumps(trade_data.get("reasoning", {})),
                json.dumps(trade_data.get("quality_gate", {})),
                json.dumps(trade_data.get("ml_features", [])),
                trade_data.get("triple_barrier_label", 1 if trade_data.get("pnl", 0.0) > 0 else (-1 if trade_data.get("pnl", 0.0) < 0 else 0))
            ))

            self._conn.commit()
            # Fold the WAL in now so the `.db` file alone always holds every
            # committed trade -- see `checkpoint()`.
            self._checkpoint_locked()

    def fetch_all_trades(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._conn.row_factory = sqlite3.Row
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM trade_records ORDER BY timestamp DESC")
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    def fetch_recent_trades(self, n: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            self._conn.row_factory = sqlite3.Row
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM trade_records ORDER BY timestamp DESC LIMIT ?", (n,))
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    def update_closed_trade(
        self,
        ticket: int,
        exit_price: float,
        pnl: float,
        is_win: int,
        mfe: float = 0.0,
        mae: float = 0.0
    ):
        """Updates trade outcome fields in SQLite when position closes (§17)."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("""
                UPDATE trade_records
                SET exit_price = ?, pnl = ?, is_win = ?, mfe = ?, mae = ?
                WHERE ticket = ?
            """, (
                float(exit_price),
                float(pnl),
                int(is_win),
                float(mfe),
                float(mae),
                int(ticket)
            ))
            self._conn.commit()
            # A closed trade is the most valuable row in the table; make sure it
            # is in the `.db` itself, not only the WAL.
            self._checkpoint_locked()

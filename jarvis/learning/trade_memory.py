"""
HM Algo 2.0 — Persistent Trade Memory & Journaling Engine.
Logs rich execution records, market snapshots, MFE/MAE excursions, and decision context to SQLite.
"""
import os
import sqlite3
import json
import math
import logging
import threading
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone

from jarvis.config.paths import resolve_db_path
from jarvis.data.schema_version import ensure_version

logger = logging.getLogger("JARVIS_TradeMemory")

# D3: 1 = `ml_features` + `triple_barrier_label`. Files written before this
# existed are 0 and are treated as current.
SCHEMA_VERSION = 1


def derive_triple_barrier_label(entry, exit_price, sl, tp, trade_type) -> Optional[int]:
    """Label a closed trade by WHICH BARRIER its exit reached.

    The triple-barrier method (Lopez de Prado) labels a trade **+1** when the upper
    (take-profit) barrier is touched first, **-1** when the lower (stop-loss) barrier is
    touched first, and **0** when the vertical (time) barrier is reached instead. Only the
    *first* touch decides, so strictly the label needs the trade's path -- but this journal
    records only the exit, so the label is derived from the strongest evidence a row
    actually holds: the exit price against the two barriers stored beside it.

    Returns ``None`` when the row cannot answer the question (no exit, no usable entry, or
    no barriers), so **"unlabelled" stays distinguishable from "the vertical barrier was
    hit"**. That distinction is the whole point here. The previous code wrote ``0`` at
    *open* time, from a ``pnl`` that does not exist yet, and ``update_closed_trade`` never
    revisited the column -- so every row was labelled "vertical barrier" whether or not it
    was. Measured on the live journal: 36/36 rows at 0 (one distinct value), of which 8
    have an exit sitting on or through a stored barrier.
    """
    try:
        entry = float(entry)
        exit_price = float(exit_price)
        sl = float(sl)
        tp = float(tp)
    except (TypeError, ValueError):
        return None

    if not all(math.isfinite(v) for v in (entry, exit_price, sl, tp)):
        return None
    # A barrier or price of 0 means "not recorded", not "a real level".
    if min(entry, exit_price, sl, tp) <= 0.0:
        return None

    side = str(trade_type or "").strip().upper()
    if side not in ("BUY", "LONG", "SELL", "SHORT"):
        return None

    # A float artifact can put the exit a hair INSIDE the barrier. Live row 938435830
    # stored `sl = 111.29999999999998` and filled at `111.30`, so a plain `exit <= sl`
    # answers "not reached" for a trade that was stopped out -- and the label would then
    # be decided by float noise rather than by the market. The tolerance is relative and
    # ~1e-9, far below one tick (a BTCUSD tick is ~1.2e-7 relative, a EURUSD pip ~8.7e-6),
    # so it cannot swallow a genuine near-miss.
    def _at(price, barrier):
        return abs(price - barrier) <= 1e-9 * max(abs(barrier), 1.0)

    if side in ("BUY", "LONG"):
        hit_tp = exit_price >= tp or _at(exit_price, tp)
        hit_sl = exit_price <= sl or _at(exit_price, sl)
    else:
        hit_tp = exit_price <= tp or _at(exit_price, tp)
        hit_sl = exit_price >= sl or _at(exit_price, sl)

    if hit_tp and hit_sl:
        # For a well-formed trade this is unreachable: a BUY cannot exit at or above its
        # target AND at or below its stop unless `tp <= sl`, i.e. the two barriers
        # contradict each other. (A gap does NOT produce it -- the fill lands on or beyond
        # whichever barrier was touched, which classifies cleanly.) A contradictory row
        # cannot be labelled, so refuse rather than guess a direction.
        return None
    if hit_tp:
        return 1
    if hit_sl:
        return -1
    return 0


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
            # D3: record the shape of this file. 1 = `ml_features` +
            # `triple_barrier_label` (both added by the sweep above).
            ensure_version(self._conn, SCHEMA_VERSION, "trade_records")


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
                # D10: do NOT invent a label here. At open there is no `pnl` to derive one
                # from, so the old fallback (`1 if pnl > 0 else (-1 if pnl < 0 else 0)`)
                # always evaluated to 0 -- minting "the vertical barrier was hit" for every
                # trade before it had been closed. `update_closed_trade` derives the real
                # label from the stored geometry; until then the row is honestly unlabelled.
                trade_data.get("triple_barrier_label")
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
        """Updates trade outcome fields in SQLite when position closes (§17).

        Also derives `triple_barrier_label` from the row's OWN stored geometry: the close
        is the first moment the question can be answered at all, and this is the only place
        every caller converges. `COALESCE` keeps an explicit label supplied at open when the
        geometry cannot decide, so a row is never downgraded from labelled to NULL.
        """
        with self._lock:
            cur = self._conn.cursor()
            row = cur.execute(
                "SELECT entry_price, sl, tp, trade_type FROM trade_records WHERE ticket = ?",
                (int(ticket),)
            ).fetchone()
            label = None
            if row:
                label = derive_triple_barrier_label(
                    entry=row[0], exit_price=exit_price,
                    sl=row[1], tp=row[2], trade_type=row[3],
                )
            cur.execute("""
                UPDATE trade_records
                SET exit_price = ?, pnl = ?, is_win = ?, mfe = ?, mae = ?,
                    triple_barrier_label = COALESCE(?, triple_barrier_label)
                WHERE ticket = ?
            """, (
                float(exit_price),
                float(pnl),
                int(is_win),
                float(mfe),
                float(mae),
                label,
                int(ticket)
            ))
            self._conn.commit()
            # A closed trade is the most valuable row in the table; make sure it
            # is in the `.db` itself, not only the WAL.
            self._checkpoint_locked()

"""The trade-memory database must be complete without its WAL file.

Regression guard for the defect found in the 2026-09-20 audit: with
``journal_mode=WAL`` and ``synchronous=NORMAL``, committed rows live in
``jarvis_trade_memory.db-wal``, not in the ``.db``. Measured on the live file:
the ``.db`` alone held **16 of 35** trades while ``.db`` + ``-wal`` held all 35,
so any backup, copy or zip of the ``.db`` by itself silently lost more than half
the journal.

``TradeMemory`` now checkpoints (TRUNCATE) after every write and before close, so
the ``.db`` file on its own is always the whole story.
"""
import os
import shutil
import sqlite3
import tempfile
import unittest

from jarvis.learning.trade_memory import TradeMemory


def _rows_in(path: str) -> int:
    """Row count visible from this file alone, with no -wal beside it."""
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM trade_records").fetchone()[0]
    finally:
        conn.close()


class TestTradeMemoryWalCheckpointing(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "trade_memory.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_recorded_trade_is_visible_in_the_db_file_alone(self):
        tm = TradeMemory(db_path=self.db)
        tm.record_trade({
            "ticket": 4242,
            "symbol": "EURUSD",
            "type": "BUY",
            "entry": 1.10,
            "lots": 0.01,
        })

        # Simulate exactly what a backup does: copy ONLY the .db, leaving the
        # -wal (and -shm) behind.
        copy = os.path.join(self._tmp.name, "backup_copy.db")
        shutil.copy(self.db, copy)

        self.assertEqual(
            _rows_in(copy), 1,
            "a copy of the .db alone must contain the trade; before the fix the "
            "copy did not even contain the trade_records table, because the "
            "schema AND every committed row were still sitting in the WAL",
        )
        tm.close()

    def test_closed_trade_is_checkpointed_too(self):
        tm = TradeMemory(db_path=self.db)
        tm.record_trade({"ticket": 99, "symbol": "XAUUSD", "type": "SELL"})
        tm.update_closed_trade(ticket=99, exit_price=2400.0, pnl=-12.5,
                               is_win=0, mfe=0.0, mae=20.0)

        copy = os.path.join(self._tmp.name, "copy2.db")
        shutil.copy(self.db, copy)
        conn = sqlite3.connect(copy)
        try:
            row = conn.execute(
                "SELECT pnl, exit_price FROM trade_records WHERE ticket = 99"
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row, "the closed trade must be in the .db itself")
        self.assertEqual(row[0], -12.5)
        tm.close()

    def test_close_checkpoints_before_closing(self):
        tm = TradeMemory(db_path=self.db)
        tm.record_trade({"ticket": 7, "symbol": "GBPUSD", "type": "BUY"})
        tm.close()

        self.assertEqual(_rows_in(self.db), 1,
                         "close() must checkpoint, or a restarted process is the "
                         "only thing that ever recovers the data")


if __name__ == "__main__":
    unittest.main()

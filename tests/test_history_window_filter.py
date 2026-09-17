"""The dashboard's Window filter has to reach the journal query.

Measured 2026-09-17, before the fix: `/api/history?days=1` answered with **185
rows whose oldest was 24 days old** — 117 rows older than the window. The `days`
parameter reached only the MT5 half of the route, and `fetch_recent_trades`
ignored it entirely, so the dropdown was inert for every row that came from the
journal.

The parameter defaults to `None` (no window) on purpose: the classic terminal and
the console call `fetch_recent_trades` without one, and narrowing their view as a
side effect of fixing the dashboard would be its own regression. Both halves of
that contract are pinned below.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from jarvis.data.database import SQLiteTradeDB


def _iso(days_ago, hours=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hours)).isoformat()


class HistoryWindowFilterTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = SQLiteTradeDB(self.db_path)

        # The window filter is a query concern. sync_mt5_history is a
        # data-population step with its own budget and no terminal here, and
        # letting it run would make the row counts depend on the machine.
        self._sync = patch.object(self.db, "sync_mt5_history", lambda **kw: None)
        self._sync.start()

        # Ages chosen to sit unambiguously inside or outside each window, with no
        # row within an hour of a boundary so a slow test clock cannot flip one.
        rows = [
            ("XAUUSD", _iso(0, 1)),    # inside 1d, 7d and 60d
            ("EURUSD", _iso(3)),       # outside 1d, inside 7d and 60d
            ("GBPUSD", _iso(20)),      # outside 1d and 7d, inside 60d
            ("USDJPY", _iso(120)),     # outside all three
        ]
        conn = self.db._get_conn()
        for ticket, (sym, ts) in enumerate(rows, start=1):
            conn.execute(
                "INSERT INTO executed_trades "
                "(ticket, symbol, action, entry_price, volume, timestamp, "
                " realized_pnl, executor, closed_at) "
                "VALUES (?, ?, 'BUY', 1.0, 0.1, ?, 10.0, 'BOT (AI)', ?)",
                (ticket, sym, ts, ts),
            )
        conn.commit()

    def tearDown(self):
        self._sync.stop()
        self.db.close()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_window_narrows_the_journal(self):
        """A 1-day window must not answer with a month of trades."""
        self.assertEqual(len(self.db.fetch_recent_trades(limit=100, days=1)), 1)
        self.assertEqual(len(self.db.fetch_recent_trades(limit=100, days=7)), 2)
        self.assertEqual(len(self.db.fetch_recent_trades(limit=100, days=60)), 3)

    def test_no_window_preserves_the_legacy_call(self):
        """The terminal and the console pass only a limit — positionally."""
        self.assertEqual(len(self.db.fetch_recent_trades(100)), 4)
        self.assertEqual(len(self.db.fetch_recent_trades(limit=100, days=None)), 4)

    def test_ordering_and_limit_still_apply_under_a_window(self):
        rows = self.db.fetch_recent_trades(limit=2, days=60)
        self.assertEqual([r["symbol"] for r in rows], ["XAUUSD", "EURUSD"])

    def test_the_old_rows_are_the_ones_a_window_removes(self):
        """The bug's actual signature: the window removed nothing, so every row
        survived it. Asserting the survivors by name is what makes a filter that
        silently does nothing fail here."""
        week = [r["symbol"] for r in self.db.fetch_recent_trades(limit=100, days=7)]
        self.assertEqual(sorted(week), ["EURUSD", "XAUUSD"])
        self.assertNotIn("GBPUSD", week)
        self.assertNotIn("USDJPY", week)


if __name__ == "__main__":
    unittest.main()

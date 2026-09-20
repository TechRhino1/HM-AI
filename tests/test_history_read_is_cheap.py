"""Reading trade history must never attach to the broker terminal.

Why this exists
---------------
`fetch_recent_trades` is the read path behind `/api/history`, and it called
`sync_mt5_history`, which did this:

    if not mt5.terminal_info():
        mt5.initialize()          # unbounded, and bypasses the one gate

`initialize()` with no terminal to attach to tries to LAUNCH one and blocks
60-100s inside native code HOLDING THE GIL. Because it ran on the request
thread, one read froze the entire process for that long.

Measured on the live server with the terminal down: `py-spy` showed two request
threads parked in `fetch_recent_trades -> sync_mt5_history (database.py:337)`
while `/api/history`, `/api/telemetry_state`, `/api/market-status` and
`/api/radar` all timed out at 20s — and `/` and `/classic` served in 0.15s,
which is what made it look like a per-endpoint problem instead of a process-wide
freeze.

Two properties are pinned here: the read path never reaches the terminal, and it
does not pay for a broker round-trip on every request.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

from jarvis.data.database import SQLiteTradeDB, _MT5_SYNC_MIN_INTERVAL_SEC


class _FakeMT5:
    """Records every call; any call at all is the defect under test."""

    def __init__(self):
        self.calls = []

    def terminal_info(self):
        self.calls.append("terminal_info")
        return object()

    def initialize(self, **kw):
        self.calls.append(("initialize", kw))
        return True

    def history_deals_get(self, *a, **kw):
        self.calls.append("history_deals_get")
        return None

    def last_error(self):
        return (-1, "fake")


class HistoryReadIsCheapTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = SQLiteTradeDB(self.db_path)
        self.addCleanup(self._cleanup)
        # A fresh instance starts with a fresh window; assert it, so a future
        # move back to class-level state cannot make these pass for the wrong
        # reason (one test's sync silently suppressing the next test's).
        self.assertEqual(self.db._last_mt5_sync, 0.0)

        conn = self.db._get_conn()
        conn.execute(
            "INSERT INTO executed_trades (ticket, symbol, action, entry_price, volume,"
            " timestamp, realized_pnl, executor, origin)"
            " VALUES (1, 'XAUUSD', 'BUY', 2400.0, 0.01,"
            " '2026-09-20T00:00:00+00:00', 5.0, 'BOT (AI)', 'broker')"
        )
        conn.commit()

    def _cleanup(self):
        self.db.close()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_a_read_never_attaches_to_the_terminal(self):
        """With no terminal in this process, a read must not try to make one."""
        fake = _FakeMT5()
        with mock.patch.dict(sys.modules, {"MetaTrader5": fake}), \
                mock.patch("jarvis.data.broker_symbols.ensure_mt5_terminal",
                           return_value=False):
            rows = self.db.fetch_recent_trades(limit=10)

        self.assertEqual(len(rows), 1, "the journal rows must still be returned")
        self.assertEqual(fake.calls, [],
                         "the read path touched the terminal; that is the call "
                         "that froze /api/history for 60-100s per request")

    def test_the_read_still_returns_local_rows(self):
        """Degrading to local rows is correct: there is nothing to sync from."""
        with mock.patch("jarvis.data.broker_symbols.ensure_mt5_terminal",
                        return_value=False):
            rows = self.db.fetch_recent_trades(limit=10, origin="broker")
        self.assertEqual([r["symbol"] for r in rows], ["XAUUSD"])
        self.assertEqual(rows[0]["origin"], "broker")

    def test_a_second_read_does_not_pay_for_another_sync(self):
        """One sync per window, not one per request."""
        fake = _FakeMT5()
        with mock.patch.dict(sys.modules, {"MetaTrader5": fake}), \
                mock.patch("jarvis.data.broker_symbols.ensure_mt5_terminal",
                           return_value=True):
            self.db.fetch_recent_trades(limit=10)
            first = fake.calls.count("history_deals_get")
            self.db.fetch_recent_trades(limit=10)
            self.db.fetch_recent_trades(limit=10)

        self.assertEqual(first, 1)
        self.assertEqual(fake.calls.count("history_deals_get"), 1,
                         "every read re-read 30 days of deals")

    def test_the_throttle_expires(self):
        """...and it is a delay, not a one-shot latch."""
        fake = _FakeMT5()
        with mock.patch.dict(sys.modules, {"MetaTrader5": fake}), \
                mock.patch("jarvis.data.broker_symbols.ensure_mt5_terminal",
                           return_value=True):
            self.db.fetch_recent_trades(limit=10)
            self.db._last_mt5_sync -= (_MT5_SYNC_MIN_INTERVAL_SEC + 1)
            self.db.fetch_recent_trades(limit=10)

        self.assertEqual(fake.calls.count("history_deals_get"), 2)

    def test_sync_itself_refuses_without_a_terminal(self):
        """Direct callers get the same guarantee as the read path."""
        fake = _FakeMT5()
        with mock.patch.dict(sys.modules, {"MetaTrader5": fake}), \
                mock.patch("jarvis.data.broker_symbols.ensure_mt5_terminal",
                           return_value=False):
            self.db.sync_mt5_history(days=30)
        self.assertEqual(fake.calls, [])

    def test_no_unbounded_initialize_remains_in_the_module(self):
        """Source guard: `mt5.initialize()` must not reappear here.

        The one gate is `broker_symbols.ensure_mt5_terminal`; a direct call in
        this module is unreachable by the cooldown and by the process check, so
        it reintroduces the freeze wherever it is copied to.

        Parsed rather than grepped: the docstring above quotes the offending line
        to explain it, and a text scan cannot tell that from real code.
        """
        import ast
        import inspect
        import jarvis.data.database as dbmod

        tree = ast.parse(inspect.getsource(dbmod))
        offenders = [
            f"line {node.lineno}: {ast.unparse(node)}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "initialize"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "mt5"
        ]
        self.assertEqual(offenders, [], f"direct initialize() call(s): {offenders}")


if __name__ == "__main__":
    unittest.main()

"""Every SQLite store must declare what shape it is in.

D3: all 11 databases in the repo reported `PRAGMA user_version = 0`, so no file
could say what it contained — and two copies of `jarvis_history.db` have already
drifted (the root copy has no `closed_at`). Schema changes were applied by a
"add any column that is missing" sweep, which cannot express a rename, a
backfill, or a refusal to open.

These tests pin the version stamp and, more importantly, the drift rule: a file
written by NEWER code must not be silently stamped down to what this version
knows. That is the failure that loses columns.
"""
import logging
import os
import sqlite3
import tempfile
import unittest

import pytest

from jarvis.data.schema_version import ensure_version, read_version, write_version


class SchemaVersionTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_a_fresh_database_is_unversioned(self):
        self.assertEqual(read_version(self.conn), 0)

    def test_the_version_is_written_and_read_back(self):
        ensure_version(self.conn, 1, "t")
        self.assertEqual(read_version(self.conn), 1)
        # A fresh connection sees it too: it is in the file, not in memory.
        other = sqlite3.connect(self.path)
        try:
            self.assertEqual(read_version(other), 1)
        finally:
            other.close()

    def test_an_older_file_is_stamped_up(self):
        write_version(self.conn, 1)
        ensure_version(self.conn, 2, "t")
        self.assertEqual(read_version(self.conn), 2)

    def test_a_newer_file_is_not_stamped_down(self):
        """The drift rule. Stamping it down would hide that we cannot read it."""
        write_version(self.conn, 9)
        with self.assertLogs("JARVIS_Schema", level="ERROR") as captured:
            result = ensure_version(self.conn, 1, "t")
        # The number on disk is left alone...
        self.assertEqual(read_version(self.conn), 9)
        # ...and the caller is told what it actually is, so it can decide.
        self.assertEqual(result, 9)
        self.assertTrue(any("newer code" in line for line in captured.output),
                        captured.output)


class StoresStampTheirSchemaTest(unittest.TestCase):
    """The real stores must actually use it, not just declare it."""

    def _temp_db(self, factory):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = factory(path)
            # The store holds its connection open; release it or Windows will
            # not let the temp file go.
            close = getattr(store, "close", None)
            if callable(close):
                close()
            else:
                get_conn = getattr(store, "_get_conn", None)
                if callable(get_conn):
                    get_conn().close()

            conn = sqlite3.connect(path)
            try:
                return read_version(conn)
            finally:
                conn.close()
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_executed_trades_is_versioned(self):
        from jarvis.data.database import SQLiteTradeDB

        self.assertEqual(self._temp_db(lambda p: SQLiteTradeDB(db_path=p)), 1)

    def test_trade_records_is_versioned(self):
        from jarvis.learning.trade_memory import TradeMemory

        self.assertEqual(self._temp_db(lambda p: TradeMemory(db_path=p)), 1)

    def test_circuit_state_is_versioned(self):
        from jarvis.risk.circuit_breaker import CircuitBreaker

        self.assertEqual(self._temp_db(lambda p: CircuitBreaker(db_path=p)), 1)

    @pytest.mark.drawdown_persistence
    def test_drawdown_state_is_versioned(self):
        from jarvis.risk.drawdown import DrawdownGuard

        # conftest forces `db_path=""` (in-memory) on every test so drawdown
        # state cannot leak between them; this one is about persistence, so it
        # opts out with the marker the fixture looks for.
        self.assertEqual(self._temp_db(lambda p: DrawdownGuard(db_path=p)), 1)


if __name__ == "__main__":
    unittest.main()

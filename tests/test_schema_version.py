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

from jarvis.data.schema_version import (
    ensure_version, migrate, read_version, write_version,
)


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


class MigrationRunnerTest(unittest.TestCase):
    """The point of a version number is that a step can be run *because of* it."""

    # The table as it was before any column was ever added to it.
    ORIGINAL = """
        CREATE TABLE executed_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket INTEGER, symbol TEXT, action TEXT, entry_price REAL,
            sl REAL, tp REAL, volume REAL, timestamp TEXT, ai_score REAL,
            regime TEXT, expected_value REAL
        )
    """
    ADDED_BY_MIGRATION_1 = [
        "realized_pnl", "executor", "session_name", "is_prime_session",
        "adx", "plus_di", "minus_di", "spread_pips", "mtf_alignment",
        "threats_json", "features_json", "closed_at", "position_id",
    ]

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(self.ORIGINAL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.path):
            os.remove(self.path)

    def _columns(self):
        return {row[1] for row in self.conn.execute("PRAGMA table_info(executed_trades)")}

    def test_an_old_file_is_brought_up_to_date(self):
        self.assertEqual(read_version(self.conn), 0)
        self.assertFalse(set(self.ADDED_BY_MIGRATION_1) & self._columns())

        from jarvis.data.database import MIGRATIONS, SCHEMA_VERSION

        result = migrate(self.conn, "executed_trades", SCHEMA_VERSION, MIGRATIONS)

        self.assertEqual(result, SCHEMA_VERSION)
        self.assertEqual(read_version(self.conn), SCHEMA_VERSION)
        missing = set(self.ADDED_BY_MIGRATION_1) - self._columns()
        self.assertFalse(missing, f"migration left these columns missing: {sorted(missing)}")

    def test_a_migration_is_applied_only_once(self):
        """Re-running must be a no-op — the version is what stops it."""
        from jarvis.data.database import MIGRATIONS, SCHEMA_VERSION

        migrate(self.conn, "executed_trades", SCHEMA_VERSION, MIGRATIONS)
        before = len(self._columns())
        result = migrate(self.conn, "executed_trades", SCHEMA_VERSION, MIGRATIONS)

        self.assertEqual(result, SCHEMA_VERSION)
        self.assertEqual(len(self._columns()), before, "a second run changed the table")

    def test_a_failing_step_leaves_the_version_alone(self):
        """A file must never claim to be more migrated than it is."""
        def broken(_conn):
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            migrate(self.conn, "executed_trades", 2, {1: broken, 2: broken})

        self.assertEqual(read_version(self.conn), 0,
                         "a failed migration must not be recorded as applied")

    def test_a_newer_file_is_not_migrated(self):
        from jarvis.data.database import MIGRATIONS

        write_version(self.conn, 99)
        with self.assertLogs("JARVIS_Schema", level="ERROR"):
            result = migrate(self.conn, "executed_trades", 1, MIGRATIONS)
        self.assertEqual(result, 99)
        self.assertEqual(read_version(self.conn), 99)

    def test_a_missing_step_stops_where_it_is(self):
        write_version(self.conn, 1)
        # No step registered to get from 1 to 2.
        result = migrate(self.conn, "executed_trades", 2, {})
        self.assertEqual(result, 1)


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

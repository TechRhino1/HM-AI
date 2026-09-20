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

    def _assert_versioned(self, factory, module, name):
        """A fresh file must carry the version this code declares.

        Compared against the module's own constant rather than a literal, so
        adding a migration updates the expectation in one place. The constant
        being wrong is a different defect from the store not stamping itself,
        and `test_executed_trades_reaches_the_origin_migration` pins the number
        where it actually matters.
        """
        declared = getattr(module, "SCHEMA_VERSION")
        stamped = self._temp_db(factory)
        self.assertGreaterEqual(declared, 1, f"{name} declares no migrations")
        self.assertEqual(stamped, declared,
                         f"{name} stamped {stamped}, not the {declared} it declares")

    def test_executed_trades_is_versioned(self):
        from jarvis.data import database

        self._assert_versioned(lambda p: database.SQLiteTradeDB(db_path=p), database, "executed_trades")

    def test_trade_records_is_versioned(self):
        from jarvis.learning import trade_memory

        self._assert_versioned(lambda p: trade_memory.TradeMemory(db_path=p), trade_memory, "trade_records")

    def test_circuit_state_is_versioned(self):
        from jarvis.risk import circuit_breaker

        self._assert_versioned(lambda p: circuit_breaker.CircuitBreaker(db_path=p), circuit_breaker, "circuit_state")

    @pytest.mark.drawdown_persistence
    def test_drawdown_state_is_versioned(self):
        from jarvis.risk import drawdown

        # conftest forces `db_path=""` (in-memory) on every test so drawdown
        # state cannot leak between them; this one is about persistence, so it
        # opts out with the marker the fixture looks for.
        self._assert_versioned(lambda p: drawdown.DrawdownGuard(db_path=p), drawdown, "drawdown_state")

    def test_executed_trades_reaches_the_origin_migration(self):
        """Pin the number the `origin` column arrived at (D1).

        Version 2 is what backfills `origin` and rewrites the close-join. A
        store that stamps 1 has silently not run it, and the journal would have
        no `origin` column at all.
        """
        from jarvis.data.database import SQLiteTradeDB

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db = SQLiteTradeDB(db_path=path)
            try:
                cols = {r[1] for r in db._get_conn().execute("PRAGMA table_info(executed_trades)")}
            finally:
                close = getattr(db, "close", None)
                if callable(close):
                    close()
            conn = sqlite3.connect(path)
            try:
                self.assertGreaterEqual(read_version(conn), 2, "migration 2 did not run")
            finally:
                conn.close()
            self.assertIn("origin", cols, "the origin column is missing")
            self.assertIn("position_id", cols, "the position_id column is missing")
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()

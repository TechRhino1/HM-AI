"""D1, part two: `origin` has to be QUERIABLE, not just stored.

A column that can only be read row by row is documentation. The statistic the
column exists for — realised P&L on real money — has to be selectable, and it
has to be selectable through an index, because the journal grows without bound.

Every expected value here is derived from rows this file inserts, not from any
database already on disk.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from jarvis.data import database as db_mod
from jarvis.data.database import (
    ORIGINS,
    SCHEMA_VERSION,
    SQLiteTradeDB,
    _origin_filter,
)
from jarvis.data.schema_version import read_version, write_version


def _fresh_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE executed_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket INTEGER,
            symbol TEXT,
            action TEXT,
            entry REAL,
            sl REAL,
            tp REAL,
            volume REAL,
            score REAL,
            timestamp TEXT,
            comment TEXT,
            realized_pnl REAL DEFAULT 0.0
        )
    """)
    conn.commit()
    return conn


class OriginFilterTest(unittest.TestCase):
    """The filter is validated before it reaches SQL."""

    def test_a_single_origin_survives(self):
        self.assertEqual(_origin_filter("broker"), ["broker"])

    def test_a_collection_survives(self):
        self.assertEqual(_origin_filter(["broker", "paper"]), ["broker", "paper"])

    def test_an_unknown_origin_selects_nothing(self):
        """`origin="real"` must not quietly widen to `origin=*`."""
        self.assertEqual(_origin_filter("real"), [])
        self.assertEqual(_origin_filter("live"), [])

    def test_a_mixed_collection_keeps_only_the_real_ones(self):
        self.assertEqual(_origin_filter(["broker", "nonsense"]), ["broker"])

    def test_a_non_iterable_selects_nothing(self):
        self.assertEqual(_origin_filter(17), [])

    def test_every_declared_origin_is_selectable(self):
        for o in ORIGINS:
            self.assertEqual(_origin_filter(o), [o], o)


class IndexTest(unittest.TestCase):
    """Migration 3 must leave an index behind, including on an older file."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db", prefix="hm_origin_idx_")
        os.close(fd)
        self.conn = _fresh_db(self.path)

    def tearDown(self):
        self.conn.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _indexes(self):
        return {r[1] for r in self.conn.execute("PRAGMA index_list(executed_trades)")}

    def test_migration_3_creates_the_index(self):
        conn = self.conn
        conn.execute("ALTER TABLE executed_trades ADD COLUMN position_id INTEGER")
        db_mod._migration_1(conn)
        db_mod._migration_2(conn)
        db_mod._migration_3(conn)

        self.assertIn("idx_executed_trades_origin_ts", self._indexes())

    def test_the_index_covers_both_origin_and_timestamp(self):
        """Composite, so `WHERE origin=? ORDER BY timestamp` is one scan."""
        db_mod._migration_1(self.conn)
        db_mod._migration_2(self.conn)
        db_mod._migration_3(self.conn)

        cols = [r[2] for r in self.conn.execute(
            "PRAGMA index_info(idx_executed_trades_origin_ts)")]
        self.assertEqual(cols, ["origin", "timestamp"])

    def test_it_is_idempotent(self):
        db_mod._migration_1(self.conn)
        db_mod._migration_2(self.conn)
        db_mod._migration_3(self.conn)
        db_mod._migration_3(self.conn)
        self.assertIn("idx_executed_trades_origin_ts", self._indexes())

    def test_it_survives_a_file_where_the_column_was_never_added(self):
        """Step 2 recorded but its ALTER did not land -> step 3 must not abort.

        `CREATE INDEX` on a missing column raises, which used to take the whole
        init block down with it.
        """
        self.conn.execute("ALTER TABLE executed_trades ADD COLUMN position_id INTEGER")
        db_mod._migration_3(self.conn)          # no `origin` column at all yet
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(executed_trades)")}
        self.assertIn("origin", cols)
        self.assertIn("idx_executed_trades_origin_ts", self._indexes())

    def test_the_version_reaches_the_new_target(self):
        conn = self.conn
        version = db_mod.migrate(conn, "executed_trades", db_mod.SCHEMA_VERSION,
                                 db_mod.MIGRATIONS)
        self.assertEqual(version, db_mod.SCHEMA_VERSION)
        self.assertEqual(read_version(conn), SCHEMA_VERSION)
        self.assertIn("idx_executed_trades_origin_ts", self._indexes())


class QueryPathTest(unittest.TestCase):
    """Filtering and aggregating against a real (temp) database."""

    def setUp(self):
        # Build the table the way the application does rather than hand-writing
        # a CREATE TABLE: a hand-written one drifts from `_init_db` the moment a
        # column is added, and then `log_trade` fails with "no such column" for
        # reasons that have nothing to do with what is being tested.
        fd, self.path = tempfile.mkstemp(suffix=".db", prefix="hm_origin_query_")
        os.close(fd)
        self.db = SQLiteTradeDB(self.path)
        # `fetch_recent_trades` syncs from MT5 first. That is a different
        # question from filtering, and with no terminal it costs ~100s per
        # attempt, so these tests answer only the filtering question.
        self.db.sync_mt5_history = lambda *a, **k: None
        # Two closed real trades (+10, -4), one closed simulated (+1000, the
        # number that must never reach a real-money statistic), one open real
        # row (pnl 0.0 = "not closed"), one closed fallback.
        self._log("XAUUSD", 1, 10.0, "broker")
        self._log("EURUSD", 2, -4.0, "broker")
        self._log("GBPUSD", 3, 0.0, "broker")          # open
        self._log("USDJPY", 4, 1000.0, "paper")
        self._log("BTCUSD", 5, -50.0, "synthetic")

    def tearDown(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _log(self, symbol, ticket, pnl, origin):
        self.db.log_trade(
            ticket=ticket, symbol=symbol, action="BUY", entry=1.0, sl=0.9, tp=1.2,
            volume=0.1, score=1.0, regime="TREND", ev=1.0,
            executor="BOT (AI)", session_name="LONDON", is_prime_session=1,
            adx=25.0, plus_di=20.0, minus_di=15.0, spread_pips=1.0,
            mtf_alignment="", threats_json="[]", features_json="{}",
            origin=origin,
        )
        # `log_trade` takes no realised P&L: the row is journalled on entry and
        # the broker's close writes the result afterwards. Set it directly —
        # that is what the close path does, and it is the only value that
        # distinguishes a closed row from an open one.
        conn = self.db._get_conn()
        conn.execute("UPDATE executed_trades SET realized_pnl = ? WHERE ticket = ?",
                     (pnl, ticket))
        conn.commit()

    # -- fetch_recent_trades ------------------------------------------------

    def test_one_origin_filters(self):
        rows = self.db.fetch_recent_trades(origin="broker")
        self.assertEqual({r["symbol"] for r in rows}, {"XAUUSD", "EURUSD", "GBPUSD"})

    def test_several_origins_filter(self):
        rows = self.db.fetch_recent_trades(origin=["broker", "synthetic"])
        self.assertEqual({r["symbol"] for r in rows},
                         {"XAUUSD", "EURUSD", "GBPUSD", "BTCUSD"})

    def test_no_origin_returns_everything(self):
        self.assertEqual(len(self.db.fetch_recent_trades()), 5)

    def test_an_unknown_origin_returns_nothing_not_everything(self):
        self.assertEqual(self.db.fetch_recent_trades(origin="real"), [])
        self.assertEqual(self.db.fetch_recent_trades(origin="live"), [])

    def test_the_filter_composes_with_the_window(self):
        rows = self.db.fetch_recent_trades(days=1, origin="broker")
        self.assertEqual(len(rows), 3)

    def test_rows_carry_their_origin_back_out(self):
        rows = self.db.fetch_recent_trades(origin="paper")
        self.assertEqual(rows[0]["origin"], "paper")

    # -- realised_pnl_by_origin --------------------------------------------

    def test_only_closed_rows_count(self):
        """pnl == 0.0 means "not closed", so GBPUSD must be excluded."""
        s = self.db.realised_pnl_by_origin()
        self.assertEqual(s["by_origin"]["broker"]["closed"], 2)
        self.assertEqual(s["by_origin"]["broker"]["open"], 1)

    def test_origins_are_reported_separately(self):
        s = self.db.realised_pnl_by_origin()
        self.assertEqual(s["by_origin"]["broker"]["realised_pnl"], 6.0)
        self.assertEqual(s["by_origin"]["paper"]["realised_pnl"], 1000.0)
        self.assertEqual(s["by_origin"]["synthetic"]["realised_pnl"], -50.0)

    def test_the_pooled_number_is_not_the_real_number(self):
        """The whole point: pooling makes real money look 167x better."""
        s = self.db.realised_pnl_by_origin()
        self.assertAlmostEqual(s["ALL"]["realised_pnl"], 956.0)
        self.assertAlmostEqual(s["BROKER_ONLY"]["realised_pnl"], 6.0)
        self.assertNotAlmostEqual(s["ALL"]["realised_pnl"],
                                  s["BROKER_ONLY"]["realised_pnl"])

    def test_expectancy_is_per_closed_trade(self):
        s = self.db.realised_pnl_by_origin()
        self.assertAlmostEqual(s["by_origin"]["broker"]["expectancy"], 3.0)

    def test_expectancy_is_none_when_nothing_closed(self):
        """None, not 0.0 — 0.0 would read as "measured and break-even"."""
        s = self.db.realised_pnl_by_origin()
        empty = s["by_origin"].get("unknown")
        if empty is None:
            # no unknown rows at all: assert the empty-bucket shape directly
            self.assertIsNone(db_mod._empty_pnl()["expectancy"])
        else:
            self.assertIsNone(empty["expectancy"])

    def test_winners_and_losers_are_split(self):
        s = self.db.realised_pnl_by_origin()
        self.assertEqual(s["by_origin"]["broker"]["winners"], 1)
        self.assertEqual(s["by_origin"]["broker"]["losers"], 1)

    def test_an_empty_book_is_not_an_error(self):
        fd, path = tempfile.mkstemp(suffix=".db", prefix="hm_origin_empty_")
        os.close(fd)
        try:
            empty = SQLiteTradeDB(path).realised_pnl_by_origin()
            self.assertEqual(empty["by_origin"], {})
            self.assertEqual(empty["ALL"]["closed"], 0)
            self.assertIsNone(empty["BROKER_ONLY"]["expectancy"])
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()

"""D1 (completed) — every fill must say whether it was real money.

D1's first fix gave every row an `origin`. It cannot finish the finding, and its
own docstring says why:

    "A microsecond timestamp only tells us the ENGINE wrote it, not whether that
    engine was trading real money or paper: nothing in the row records the
    execution mode."

`origin` answers **where the fill price came from**. The finding asks **was this
real money**. They are not the same question, and one cannot be derived from the
other, because `origin='synthetic'` covers two different worlds:

* a PAPER fill with no quote            -> origin=synthetic, mode=paper
* a LIVE fill whose client lost the terminal -> origin=synthetic, mode=live

`_price_origin()` returns `synthetic` for both. So the one column that existed
was silently standing in for the question the finding actually asked, and a
paper fill and a real one could land in the same bucket with nothing to separate
them. Measured on `data/jarvis_history.db`: 269 rows, 112 `broker` and 157
`unknown`, and no way to ask how many were simulated.

`execution_mode` is now a separate column (`live` / `paper` / `demo` /
`unknown`), populated at every write site and filterable on read.

**The backfill is `unknown`, deliberately.** Nothing in a historical row carries
the mode and the two columns are not recoverable from each other. Guessing
`live` would put 269 rows of unverifiable history behind the exact filter this
column exists to enable — a filter that looks like evidence and is not.
"""

import os
import sqlite3
from types import SimpleNamespace

import pytest

from jarvis.data.database import (
    EXECUTION_MODES,
    MIGRATIONS,
    SCHEMA_VERSION,
    SQLiteTradeDB,
    _mode_filter,
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    # `fetch_recent_trades` calls `sync_mt5_history` on every read, and that
    # INSERTs real MT5 deals into whatever DB it is given. With the broker
    # terminal running (as it is on the dev box), a fixture that builds a
    # temp SQLiteTradeDB would receive real broker tickets like 940451636 next
    # to its own ticket=1 row, and the "filter returns 1 row" asserts in this
    # file would all fail. This file does not test sync semantics, so stub the
    # sync out for the fixture — tests that DO want to exercise sync build
    # their own fixture and call `sync_mt5_history` explicitly.
    d = SQLiteTradeDB(db_path=str(tmp_path / "hist.db"))
    monkeypatch.setattr(d, "sync_mt5_history", lambda *a, **kw: None)
    return d


def _one(db, **kw):
    db.log_trade(ticket=1, symbol="EURUSD", action="BUY", entry=1.0, sl=0.9, tp=1.2,
                 volume=0.01, score=80.0, regime="TREND_BULL", ev=1.0, **kw)
    rows = db.fetch_recent_trades(limit=10)
    return rows[0]


def _spy_conn(db, monkeypatch):
    """Swap in a connection that records every SELECT it is asked to run.

    `fetch_recent_trades` opens its cursor before it looks at the filters, so
    counting `cursor()` would see a call either way. Counting `execute` is what
    distinguishes "refused" from "asked and told no".
    """
    real_conn = db._get_conn()

    class _Spy:
        def __init__(self):
            self.executes = []

        def cursor(self):
            real_cur = real_conn.cursor()
            spy = self

            class _Cursor:
                def execute(self, sql, params=()):
                    spy.executes.append(sql)
                    return real_cur.execute(sql, params)

                def __getattr__(self, name):
                    return getattr(real_cur, name)

            return _Cursor()

    spy = _Spy()
    monkeypatch.setattr(db, "_get_conn", lambda: spy)
    return spy


class TestTheModeIsRecorded:
    def test_a_paper_fill_says_paper(self, db):
        assert _one(db, origin="paper", execution_mode="paper")["execution_mode"] == "paper"

    def test_a_live_fill_says_live(self, db):
        assert _one(db, origin="broker", execution_mode="live")["execution_mode"] == "live"

    def test_a_demo_fill_says_demo(self, db):
        assert _one(db, origin="broker", execution_mode="demo")["execution_mode"] == "demo"

    def test_a_missing_mode_is_unknown_not_guessed(self, db):
        # "unknown" is the honest answer; defaulting to "live" would file every
        # unlabelled row as real money.
        assert _one(db, origin="broker")["execution_mode"] == "unknown"

    def test_an_invented_mode_is_unknown(self, db):
        assert _one(db, origin="broker", execution_mode="real")["execution_mode"] == "unknown"


class TestTheTwoQuestionsAreSeparate:
    def test_a_live_fill_with_a_fallback_price_is_still_live(self, db):
        # The row that used to be unclassifiable: origin says the price cannot be
        # trusted, execution_mode says real money was at risk.
        row = _one(db, origin="synthetic", execution_mode="live")
        assert row["origin"] == "synthetic"
        assert row["execution_mode"] == "live"

    def test_a_paper_fill_with_a_fallback_price_is_paper(self, db):
        row = _one(db, origin="synthetic", execution_mode="paper")
        assert row["origin"] == "synthetic"
        assert row["execution_mode"] == "paper"

    def test_origin_alone_cannot_answer_the_mode_question(self, db):
        _one(db, origin="synthetic", execution_mode="live")
        _one(db, origin="synthetic", execution_mode="paper")
        synthetic = db.fetch_recent_trades(limit=10, origin="synthetic")
        assert {r["execution_mode"] for r in synthetic} == {"live", "paper"}


class TestTheEngineReportsItsOwnMode:
    def _engine(self, mode):
        from jarvis.execution.execution_engine import ExecutionEngine

        eng = ExecutionEngine.__new__(ExecutionEngine)
        eng.mt5_client = SimpleNamespace(mode=mode)
        return eng

    def test_a_paper_client_reports_paper(self):
        assert self._engine("paper")._execution_mode({}) == "paper"

    def test_a_live_client_reports_live_even_with_a_fallback_price(self):
        # The case that matters: the price is a stand-in, the money is not.
        assert self._engine("live")._execution_mode({"is_fallback": True}) == "live"

    def test_an_unknown_mode_is_not_guessed(self):
        assert self._engine("")._execution_mode({}) == "unknown"


class TestTheModeCanBeFiltered:
    def test_filtering_on_paper_returns_only_paper(self, db):
        _one(db, origin="broker", execution_mode="live")
        _one(db, origin="paper", execution_mode="paper")
        rows = db.fetch_recent_trades(limit=10, execution_mode="paper")
        assert len(rows) == 1
        assert rows[0]["execution_mode"] == "paper"

    def test_several_modes_can_be_requested(self, db):
        _one(db, origin="broker", execution_mode="live")
        _one(db, origin="paper", execution_mode="paper")
        _one(db, origin="broker", execution_mode="demo")
        rows = db.fetch_recent_trades(limit=10, execution_mode=["live", "demo"])
        assert {r["execution_mode"] for r in rows} == {"live", "demo"}

    def test_a_typo_selects_nothing_not_everything(self, db):
        # The failure mode this guards against: a caller asking for
        # execution_mode="real" being handed the whole table, paper included,
        # and believing it filtered.
        _one(db, origin="broker", execution_mode="live")
        assert db.fetch_recent_trades(limit=10, execution_mode="real") == []

    def test_an_impossible_filter_refuses_before_running_a_query(self, db, monkeypatch):
        # What the guard is actually for, measured rather than assumed: this
        # build's SQLite ACCEPTS `execution_mode IN ()` and matches nothing, so
        # an earlier version of this test claimed the guard was what stood
        # between a bad filter and a raised, swallowed error. It is not -- the
        # answer is [] either way. The observable difference is only that the
        # guard never asks: no SELECT is issued, so "no row can match" is
        # answered from the contract instead of being discovered by the driver.
        _one(db, origin="broker", execution_mode="live")
        spy = _spy_conn(db, monkeypatch)
        assert db.fetch_recent_trades(limit=10, execution_mode="real") == []
        assert spy.executes == [], "an impossible filter must be refused, not queried"

    def test_a_possible_filter_still_queries(self, db, monkeypatch):
        # The other half, or the test above would pass just as well on a method
        # that never queries at all.
        _one(db, origin="broker", execution_mode="live")
        spy = _spy_conn(db, monkeypatch)
        assert len(db.fetch_recent_trades(limit=10, execution_mode="live")) == 1
        assert len(spy.executes) == 1

    def test_mode_and_origin_compose(self, db):
        _one(db, origin="broker", execution_mode="live")
        _one(db, origin="synthetic", execution_mode="live")
        rows = db.fetch_recent_trades(limit=10, execution_mode="live", origin="broker")
        assert len(rows) == 1
        assert rows[0]["origin"] == "broker"


class TestTheFilterHelper:
    def test_a_valid_mode_survives(self):
        assert _mode_filter("paper") == ["paper"]

    def test_an_invalid_one_does_not(self):
        assert _mode_filter("real") == []

    def test_something_uniterable_does_not(self):
        assert _mode_filter(42) == []

    def test_every_mode_is_reachable(self):
        assert set(EXECUTION_MODES) == {"live", "paper", "demo", "unknown"}


class TestTheMigration:
    def test_it_is_registered(self):
        assert set(MIGRATIONS) == set(range(1, SCHEMA_VERSION + 1))
        assert 4 in MIGRATIONS

    def test_a_fresh_file_has_the_column(self, db):
        assert "execution_mode" in _one(db, execution_mode="live")

    def test_history_backfills_to_unknown_not_live(self, tmp_path):
        # Build a v3 file: the column does not exist yet, then let migration 4
        # discover it.
        path = str(tmp_path / "old.db")
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE executed_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ticket INTEGER, origin TEXT, symbol TEXT, timestamp TEXT)"
        )
        conn.execute("INSERT INTO executed_trades (ticket, symbol, timestamp) VALUES (1,'EURUSD','2026-01-01T00:00:00+00:00')")
        conn.commit()
        conn.close()

        from jarvis.data.database import _migration_4
        conn = sqlite3.connect(path)
        _migration_4(conn)
        got = conn.execute("SELECT execution_mode FROM executed_trades").fetchall()
        conn.close()
        assert [r[0] for r in got] == ["unknown"]

    def test_the_column_is_indexed(self, db):
        _one(db, execution_mode="live")
        conn = sqlite3.connect(db.db_path if hasattr(db, "db_path") else "")
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE '%mode_ts%'")]
        conn.close()
        assert names

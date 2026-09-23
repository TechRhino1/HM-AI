"""AI5 / executive-summary #4 — the forecast must survive the close.

`executed_trades.expected_value` is the FORECAST, written once at open from
`decision.expected_value`. Two places destroyed it:

* the close UPDATE set `expected_value = ?` to the same value as `realized_pnl`;
* the broker-history INSERT (a deal reconstructed from `history_deals_get`, which
  carries no decision) wrote `pnl` into it, and a fabricated `ai_score = 85.0`.

Measured on the live journal: **every closed row had `expected_value == realized_pnl`** — 111/111.
The column therefore meant "forecast" for open rows and "outcome" for closed ones.

That is destructive in two directions:

* **Forecast accuracy becomes unmeasurable.** Calibration (AI10), Brier scoring, any
  "did the EV predict the result?" analysis need both numbers, and one was gone.
* **`SelfLearningEngine` read the corrupted column.** `get_regime_multiplier` averaged it to
  drive a 0.90/1.00/1.10 conviction multiplier — so it was averaging the very thing it was
  supposed to predict. `get_pattern_stats` called `expected_value > 0` a "win_rate", which is
  only approximately true because of the overwrite.

Fixed by writing NULL where no forecast exists and leaving the column alone at close, then
reading outcomes from `realized_pnl` and forecasts from `expected_value`, explicitly.
"""

import sqlite3

import pytest

from jarvis.data.schema_version import read_version
from jarvis.intelligence.self_learning import SelfLearningEngine


@pytest.fixture
def db(tmp_path):
    """An `executed_trades` shaped like the real one, empty."""
    path = tmp_path / "jarvis_history.db"
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE executed_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket INTEGER, position_id INTEGER, origin TEXT, symbol TEXT, action TEXT,
            entry_price REAL, sl REAL, tp REAL, volume REAL, timestamp TEXT,
            ai_score REAL, regime TEXT, expected_value REAL, realized_pnl REAL,
            executor TEXT, closed_at TEXT,
            session_name TEXT DEFAULT 'UNKNOWN', is_prime_session INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    conn.close()
    return path


def _open(conn, **over):
    row = {"ticket": 1, "symbol": "EURUSD", "action": "BUY", "entry_price": 1.1,
           "sl": 1.09, "tp": 1.12, "volume": 0.01, "timestamp": "2026-09-20 10:00:00",
           "ai_score": 72.0, "regime": "TREND_BULL", "expected_value": 2.5,
           "realized_pnl": 0.0, "executor": "ENGINE", "closed_at": None,
           "origin": "paper"}
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" * len(row))
    conn.execute(f"INSERT INTO executed_trades ({cols}) VALUES ({marks})", tuple(row.values()))
    conn.commit()


def _close(conn, ticket, pnl, ts="2026-09-20 11:00:00"):
    """The post-fix close. Note what is NOT in it."""
    conn.execute(
        "UPDATE executed_trades SET realized_pnl = ?, closed_at = ? WHERE ticket = ?",
        (pnl, ts, ticket),
    )
    conn.commit()


def _row(conn, ticket):
    cur = conn.execute(
        "SELECT expected_value, realized_pnl, ai_score FROM executed_trades WHERE ticket = ?",
        (ticket,),
    )
    return cur.fetchone()


class TestTheCloseContract:
    """The invariant a close must satisfy, exercised against a local close helper
    that mirrors the production UPDATE.

    NOTE, stated rather than glossed over: these two drive THEIR OWN `_close`, not
    `sync_mt5_history`, which cannot be called without a broker. What actually pins
    the production SQL is `TestTheWriteSites` below, which asserts on the source —
    a weaker but honest form of evidence. A runtime mutation therefore cannot turn
    these two red, and they are not counted as coverage of the write sites.
    """

    def test_closing_does_not_rewrite_the_forecast(self, db):
        conn = sqlite3.connect(str(db))
        _open(conn, ticket=1, expected_value=2.5)
        _close(conn, 1, pnl=-4.0)
        ev, pnl, _ = _row(conn, 1)
        conn.close()
        assert ev == 2.5, "the forecast was overwritten with the outcome"
        assert pnl == -4.0

    def test_forecast_and_outcome_are_both_available_after_close(self, db):
        """The point of the fix: you can now measure whether the forecast was right."""
        conn = sqlite3.connect(str(db))
        _open(conn, ticket=2, expected_value=3.0)
        _close(conn, 2, pnl=-1.0)
        ev, pnl, _ = _row(conn, 2)
        conn.close()
        assert ev > 0 and pnl < 0, "a wrong forecast is now visible as one"


class TestSelfLearningReadsWhatItSays:
    def test_the_regime_multiplier_uses_outcomes(self, db):
        """An OPEN row has no outcome. Averaging its forecast must not count."""
        conn = sqlite3.connect(str(db))
        # Five open rows with wildly optimistic forecasts and no result.
        for i in range(5):
            _open(conn, ticket=100 + i, expected_value=50.0, realized_pnl=0.0, closed_at=None)
        conn.close()

        engine = SelfLearningEngine(db_path=str(db))
        # Pre-fix this read expected_value and boosted on those 50.0 forecasts.
        assert engine.get_regime_multiplier("TREND_BULL", lookback=50) == 1.0

    def test_it_penalises_when_the_regime_actually_loses(self, db):
        conn = sqlite3.connect(str(db))
        for i in range(6):
            _open(conn, ticket=200 + i, expected_value=5.0)
            _close(conn, 200 + i, pnl=-10.0)
        conn.close()

        engine = SelfLearningEngine(db_path=str(db))
        assert engine.get_regime_multiplier("TREND_BULL", lookback=50) == 0.90

    def test_it_boosts_when_the_regime_actually_wins(self, db):
        conn = sqlite3.connect(str(db))
        for i in range(6):
            _open(conn, ticket=300 + i, expected_value=-1.0)
            _close(conn, 300 + i, pnl=8.0)
        conn.close()

        engine = SelfLearningEngine(db_path=str(db))
        # Note the forecast was NEGATIVE here: pre-fix this would have penalised.
        assert engine.get_regime_multiplier("TREND_BULL", lookback=50) == 1.10

    def test_fewer_than_five_outcomes_abstains(self, db):
        conn = sqlite3.connect(str(db))
        for i in range(4):
            _open(conn, ticket=400 + i)
            _close(conn, 400 + i, pnl=20.0)
        conn.close()
        assert SelfLearningEngine(db_path=str(db)).get_regime_multiplier("TREND_BULL") == 1.0


class TestPatternStatsSeparateForecastFromOutcome:
    def test_win_rate_comes_from_outcomes_not_forecasts(self, db):
        """`expected_value > 0` is not a win rate. Every forecast positive, every
        trade a loss: the win rate must be 0, not 1."""
        conn = sqlite3.connect(str(db))
        for i in range(4):
            _open(conn, ticket=500 + i, symbol="EURUSD", expected_value=9.0)
            _close(conn, 500 + i, pnl=-3.0)
        conn.close()

        stats = SelfLearningEngine(db_path=str(db)).get_pattern_win_rate_and_ev("EURUSD", "TREND_BULL", 50)
        assert stats["win_rate"] == 0.0
        assert stats["avg_ev"] == pytest.approx(9.0)

    def test_the_two_sample_sizes_are_reported_separately(self, db):
        """3 rows fetched, but only 1 with an outcome: a caller must be able to see
        that the win rate rests on a single trade."""
        conn = sqlite3.connect(str(db))
        _open(conn, ticket=600, symbol="EURUSD", expected_value=1.0)
        _open(conn, ticket=601, symbol="EURUSD", expected_value=1.0, closed_at=None)
        _open(conn, ticket=602, symbol="EURUSD", expected_value=None)
        _close(conn, 600, pnl=4.0)
        _close(conn, 602, pnl=-2.0)
        conn.close()

        stats = SelfLearningEngine(db_path=str(db)).get_pattern_win_rate_and_ev("EURUSD", "TREND_BULL", 50)
        assert stats["sample_size"] == 3
        assert stats["outcome_sample_size"] == 2
        assert stats["forecast_sample_size"] == 2

    def test_no_rows_abstains(self, db):
        stats = SelfLearningEngine(db_path=str(db)).get_pattern_win_rate_and_ev("EURUSD", "TREND_BULL", 50)
        assert stats["conviction_multiplier"] == 1.0
        assert stats["sample_size"] == 0


class TestTheWriteSites:
    """The broker-history INSERT wrote `ai_score = 85.0` and `expected_value = pnl`;
    the close UPDATE wrote `expected_value = ?` to the realised P&L.

    Both live inside `sync_mt5_history`, which needs a broker to drive, so they are
    asserted against the SQL text. Source assertions are brittle to reformatting,
    but the alternative — no evidence at all for the two sites that caused this —
    is worse. They are the only pin on the write half of this fix.
    """

    def _source(self):
        import inspect
        from jarvis.data import database
        return inspect.getsource(database)

    def test_the_reconstruct_insert_does_not_write_a_fake_score(self):
        src = self._source()
        assert "85.0, regime_str" not in src, "ai_score is still fabricated for reconstructed rows"

    def test_the_close_update_does_not_touch_expected_value(self):
        src = self._source()
        # The close UPDATE must set realized_pnl without also setting expected_value.
        assert "SET realized_pnl = ?, expected_value = ?" not in src

    def test_the_close_update_still_records_the_outcome(self):
        src = self._source()
        # The outcome is written with COALESCE so a sync that cannot see the
        # exit deal (an open position) does not erase an outcome the live close
        # path already recorded — see `record_trade_exit`.
        assert "realized_pnl = COALESCE(?, realized_pnl), executor = ?, timestamp = ?" in src

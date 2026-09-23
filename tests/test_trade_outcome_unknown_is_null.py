"""The journal's OUTCOME columns must say "unknown", not "zero".

`TradeMemory.record_trade` runs when a trade **opens**, and it used to write

    trade_data.get("exit", 0.0),                   # exit_price
    trade_data.get("pnl", 0.0),                    # pnl
    1 if trade_data.get("pnl", 0.0) > 0 else 0,    # is_win

so a trade that had not closed yet was stored as ``exit_price=0, pnl=0,
is_win=0`` — byte-for-byte identical to a trade that closed at break-even. Two
facts shared one encoding, and neither was the truth:

* ``0.0`` is a perfectly real P&L (a scratch trade) **and** it is what
  ``dict.get()`` returns for a missing key;
* ``0`` already means "loss", so an unclosed trade was indistinguishable from a
  break-even loser.

Measured consequence on record: ``data/jarvis_trade_memory.db`` had 42/93 rows
with ``exit_price=0, pnl=0``. MFE/MAE were ``0.0`` on every row, so adverse
excursion at entry could not be derived at all.

This is the same defect that was fixed in the sibling table ``executed_trades``
(``SQLiteTradeDB.record_trade_exit``, whose docstring states: "``None`` means
'not recorded' and is stored as NULL — never as 0.0"). The fix mirrors it:
unknown outcomes are NULL, and the close path **UPDATEs** the row written at
open (``record_trade`` is ``INSERT OR REPLACE``, so routing a close through it
would wipe the entry geometry and the feature vector).
"""

from __future__ import annotations

import sqlite3

import pytest

from jarvis.learning.trade_memory import TradeMemory

OPEN = {
    "ticket": 1, "symbol": "EURUSD", "type": "BUY",
    "entry": 1.1000, "sl": 1.0950, "tp": 1.1100, "lots": 0.10,
}


def _open(memory, **over):
    """Record a trade the way the live loop does: entry data only, no outcome."""
    payload = dict(OPEN)
    payload.update(over)
    memory.record_trade(payload)
    return payload["ticket"]


def _read(db_path, ticket, columns="exit_price, pnl, is_win, mfe, mae"):
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return c.execute(
            f"SELECT {columns} FROM trade_records WHERE ticket = ?", (ticket,)
        ).fetchone()
    finally:
        c.close()


@pytest.fixture
def memory(tmp_path):
    tm = TradeMemory(db_path=str(tmp_path / "jarvis_trade_memory.db"))
    yield tm
    tm.close()


# ── 1. an open trade has no outcome ─────────────────────────────────────────

class TestAnOpenTradeHasNoOutcome:
    def test_the_row_reports_none_not_zero(self, memory):
        """THE DEFECT. The old defaults wrote 0.0/0 for every one of these."""
        t = _open(memory)
        exit_price, pnl, is_win, mfe, mae = _read(memory.db_path, t)
        assert exit_price is None, "an unclosed trade has no exit price"
        assert pnl is None, "an unclosed trade has no P&L"
        assert is_win is None, "0 means 'loss', not 'unknown'"
        assert mfe is None, "no path has been measured yet"
        assert mae is None

    def test_the_fetch_helpers_agree(self, memory):
        t = _open(memory)
        row = memory.fetch_trade(t)
        assert "pnl" in row, "the key must exist, or .get() invents 0.0"
        assert row["exit_price"] is None
        assert row["pnl"] is None
        assert row["is_win"] is None
        # The exact shape of the original bug: a caller coercing to float.
        assert row["pnl"] != 0.0
        assert float(row["pnl"] or 0.0) == 0.0  # only if you coerce badly

    def test_a_caller_supplied_outcome_at_open_is_still_recorded(self, memory):
        """The change is about MISSING values, not about forbidding a real one."""
        t = _open(memory, exit=1.1050, pnl=12.5)
        exit_price, pnl, is_win, _, _ = _read(memory.db_path, t)
        assert exit_price == pytest.approx(1.1050)
        assert pnl == pytest.approx(12.5)
        assert is_win == 1


# ── 2 & 3. NULL is not 0.0, and the close UPDATEs ───────────────────────────

class TestUnknownIsNotZero:
    def test_a_break_even_close_is_a_real_zero(self, memory):
        """The other side of the coin: a genuine 0.0 must survive as 0.0."""
        t = _open(memory)
        memory.update_closed_trade(ticket=t, exit_price=1.1000, pnl=0.0, is_win=0)
        exit_price, pnl, is_win, _, _ = _read(memory.db_path, t)
        assert pnl == 0.0
        assert pnl is not None, "a recorded 0.0 is not the same as 'not recorded'"
        assert is_win == 0
        assert exit_price == pytest.approx(1.1000)

    def test_is_win_is_none_unclosed_zero_for_a_loss_one_for_a_win(self, memory):
        open_t = _open(memory, ticket=10)
        loss_t = _open(memory, ticket=11)
        win_t = _open(memory, ticket=12)
        memory.update_closed_trade(ticket=loss_t, exit_price=1.0900, pnl=-7.5)
        memory.update_closed_trade(ticket=win_t, exit_price=1.1100, pnl=9.0)

        assert _read(memory.db_path, open_t, "is_win")[0] is None
        assert _read(memory.db_path, loss_t, "is_win")[0] == 0
        assert _read(memory.db_path, win_t, "is_win")[0] == 1

    def test_is_win_is_derived_from_pnl_when_omitted(self, memory):
        t = _open(memory)
        memory.update_closed_trade(ticket=t, exit_price=1.1100, pnl=3.0)
        assert _read(memory.db_path, t, "is_win")[0] == 1

    def test_a_close_that_knows_nothing_leaves_the_row_unknown(self, memory):
        t = _open(memory)
        memory.update_closed_trade(ticket=t)  # nothing known
        assert _read(memory.db_path, t) == (None, None, None, None, None)

    def test_an_unknown_outcome_does_not_erase_a_recorded_one(self, memory):
        """COALESCE, like `record_trade_exit`: a later blind call must not wipe it."""
        t = _open(memory)
        memory.update_closed_trade(ticket=t, exit_price=1.1100, pnl=9.0, mfe=0.004, mae=0.002)
        memory.update_closed_trade(ticket=t)  # a later call that knows nothing
        exit_price, pnl, is_win, mfe, mae = _read(memory.db_path, t)
        assert exit_price == pytest.approx(1.1100)
        assert pnl == pytest.approx(9.0)
        assert is_win == 1
        assert mfe == pytest.approx(0.004)
        assert mae == pytest.approx(0.002)

    def test_a_measured_zero_excursion_is_still_a_zero(self, memory):
        """0.0 passed deliberately is a measurement; omitted is not."""
        t = _open(memory)
        memory.update_closed_trade(ticket=t, exit_price=1.1000, pnl=0.0,
                                   is_win=0, mfe=0.0, mae=0.0)
        _, _, _, mfe, mae = _read(memory.db_path, t)
        assert mfe == 0.0 and mae == 0.0


class TestTheCloseUpdatesTheOpenRow:
    def test_it_updates_in_place_and_keeps_the_entry(self, memory):
        t = _open(memory, ticket=77, regime="TREND_BULL",
                  strategy="BREAKOUT_EXPANSION", ml_features=[0.1, 0.2, 0.3])
        assert memory.update_closed_trade(ticket=t, exit_price=1.1050,
                                          pnl=5.0, is_win=1) is True
        row = memory.fetch_trade(t)

        # Entry-side fields survive the close...
        assert row["entry_price"] == pytest.approx(1.1000)
        assert row["sl"] == pytest.approx(1.0950)
        assert row["tp"] == pytest.approx(1.1100)
        assert row["regime"] == "TREND_BULL"
        assert row["strategy"] == "BREAKOUT_EXPANSION"
        assert row["ml_features"] == "[0.1, 0.2, 0.3]"
        # ...and the outcome is now there.
        assert row["exit_price"] == pytest.approx(1.1050)
        assert row["pnl"] == pytest.approx(5.0)
        assert row["is_win"] == 1

    def test_the_row_count_does_not_grow(self, memory):
        t = _open(memory)
        memory.update_closed_trade(ticket=t, exit_price=1.1050, pnl=5.0, is_win=1)
        assert len(memory.fetch_all_trades()) == 1

    def test_a_close_for_an_unopened_ticket_writes_nothing(self, memory):
        assert memory.update_closed_trade(ticket=424242, exit_price=1.0,
                                          pnl=1.0) is False
        assert memory.fetch_trade(424242) is None
        assert memory.fetch_all_trades() == []

    def test_routing_a_close_through_record_trade_would_wipe_the_entry(self, memory):
        """WHY the close is an UPDATE and not `record_trade`.

        `record_trade` is `INSERT OR REPLACE`; re-running it for an open ticket
        replaces the whole row. This pins the hazard the close path avoids.
        """
        t = _open(memory, ticket=88, ml_features=[1.0, 2.0])
        memory.update_closed_trade(ticket=t, exit_price=1.1050, pnl=5.0, is_win=1)
        memory.record_trade({"ticket": t, "symbol": "EURUSD", "type": "BUY",
                             "entry": 1.1, "sl": 1.09, "tp": 1.12})
        row = memory.fetch_trade(t)
        assert row["pnl"] is None, "INSERT OR REPLACE wiped the recorded outcome"
        assert row["ml_features"] == "[]"


# ── 4. the schema can hold the NULL ─────────────────────────────────────────

class TestTheSchemaIsNullable:
    def test_every_outcome_column_accepts_null(self, memory):
        """The premise of the whole fix: NULL must be storable, not coerced.

        The live file and the CREATE TABLE both leave these columns nullable, so
        no `NOT NULL DEFAULT 0.0` relaxation migration was required.
        """
        c = sqlite3.connect(f"file:{memory.db_path}?mode=ro", uri=True)
        try:
            notnull = {
                r[1]: r[3]
                for r in c.execute("PRAGMA table_info(trade_records)")
            }
        finally:
            c.close()
        for col in ("exit_price", "pnl", "is_win", "mfe", "mae"):
            assert notnull[col] == 0, f"{col} is NOT NULL; NULL could not be stored"


# ── 5. readers must not misread "unknown" ───────────────────────────────────

class TestReadersTreatAnUnknownOutcomeAsAbsent:
    def test_strategy_memory_does_not_count_an_open_trade_as_a_loss(self, memory):
        """Before: an unclosed row fell into the `else` branch as a LOSS, and its
        NULL `pnl` made the `sum()` raise. Now it is skipped."""
        from jarvis.learning.strategy_memory import StrategyRegimeMemory

        _open(memory, ticket=1, regime="TREND_BULL", strategy="TREND_FOLLOWING")
        closed = _open(memory, ticket=2, regime="TREND_BULL",
                       strategy="TREND_FOLLOWING")
        memory.update_closed_trade(ticket=closed, exit_price=1.1100,
                                   pnl=10.0, is_win=1)

        stats = StrategyRegimeMemory(memory).get_regime_strategy_matrix()
        bucket = stats["TREND_BULL"]["TREND_FOLLOWING"]
        assert bucket["total_trades"] == 1, "the unclosed row must not be counted"
        assert bucket["win_rate_pct"] == 100.0
        assert bucket["net_pnl"] == pytest.approx(10.0)

    def test_the_confidence_refit_skips_a_row_with_no_outcome(self):
        """`int(None)` used to raise; an unclosed row is not a usable sample."""
        from jarvis.intelligence.confidence import ConfidenceCalibrationEngine

        engine = ConfidenceCalibrationEngine()
        rows = [{"raw_win_prob": 0.65, "is_win": None}] * 20
        assert engine.update_calibration_from_history(rows) == 0

        # And a closed row still refits normally.
        closed = [{"raw_win_prob": 0.65, "is_win": 1}] * 20
        assert engine.update_calibration_from_history(closed) == 1

    def test_the_online_predictor_refuses_to_learn_from_an_open_trade(self, tmp_path):
        from jarvis.learning.online_ml_predictor import OnlineMLPredictor

        model = OnlineMLPredictor(model_file=str(tmp_path / "ml.json"))
        model.update_from_trade_record(
            {"ticket": 1, "is_win": None, "pnl": None,
             "ml_features": [0.1] * len(OnlineMLPredictor.FEATURE_NAMES)}
        )
        assert len(model._grad_buffer) == 0, "an unclosed trade taught the model"

    def test_the_online_predictor_still_learns_from_a_closed_trade(self, tmp_path):
        from jarvis.learning.online_ml_predictor import OnlineMLPredictor

        model = OnlineMLPredictor(model_file=str(tmp_path / "ml.json"))
        model.update_from_trade_record(
            {"ticket": 2, "is_win": 1, "pnl": 12.0,
             "ml_features": [0.1] * len(OnlineMLPredictor.FEATURE_NAMES)}
        )
        assert len(model._grad_buffer) == 1

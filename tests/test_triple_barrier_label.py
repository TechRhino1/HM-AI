"""D10 — the triple-barrier label must reflect the barrier the exit actually reached.

Measured before the fix (`.scratch/repro_triple_barrier.py`): `triple_barrier_label` was
**0 on 36/36 rows** (one distinct value) on the live journal, including 8 rows whose exit
sits on or through a stored barrier. Two defects stacked:

1. `record_trade` derived the label at **open** from
   `1 if pnl > 0 else (-1 if pnl < 0 else 0)` — but a trade being *opened* has no `pnl`, so
   the fallback always evaluated to **0**. It minted "the vertical barrier was hit" for
   every trade before it had been closed.
2. `update_closed_trade` updated `exit_price / pnl / is_win / mfe / mae` and **never
   touched the label**, so the row kept the 0 it was born with.

The label is now derived at close, from the row's own stored geometry.

Scope note: nothing reads this column today — `strategy_memory.py:28-32` learns from
`is_win` and `pnl` only, so the zeros never changed a decision. This is a *data-honesty*
defect: a future learner reading the column would have been fed all-zeros, which is the
same shape as C2 and D5 (a plausible value that actually means "unknown").
"""

import sqlite3

import pytest

from jarvis.learning.trade_memory import (
    TradeMemory,
    derive_triple_barrier_label,
)

# The live row that proves the float trap: stored `sl` carries a float artifact, so a
# plain `exit <= sl` answers "not reached" for a trade that WAS stopped out.
LIVE_SL_ROW = dict(
    entry=112.35, exit_price=111.30, sl=111.29999999999998, tp=114.44999999999999,
    trade_type="BUY",
)


@pytest.fixture
def memory(tmp_path):
    """`tmp_path`, not `tempfile.mkdtemp()`.

    An earlier version of this file used `mkdtemp` with no cleanup, which leaked a
    directory (plus `-wal`/`-shm`) per test. Across a full suite run those leftovers
    pushed the sandbox's bulk-delete guard over its 50-item threshold and the run
    exited non-zero *with every test passing* — the failure was in teardown, not in
    the assertions. `tmp_path` is owned and cleaned up by pytest.
    """
    tm = TradeMemory(db_path=str(tmp_path / "jarvis_trade_memory.db"))
    yield tm
    tm.close()


def _label(db_path, ticket):
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return c.execute(
            "SELECT triple_barrier_label FROM trade_records WHERE ticket = ?", (ticket,)
        ).fetchone()[0]
    finally:
        c.close()


# ---------------------------------------------------------------------------
# The pure derivation
# ---------------------------------------------------------------------------

class TestDeriveTripleBarrierLabel:
    @pytest.mark.parametrize("side,sl,tp,exit_price,expected", [
        # A BUY targets above the entry and stops below it.
        ("BUY", 90.0, 100.0, 100.0, 1),
        ("BUY", 90.0, 100.0, 90.0, -1),
        ("BUY", 90.0, 100.0, 95.0, 0),
        # A SELL is the mirror image: the target is BELOW, the stop is ABOVE.
        ("SELL", 100.0, 90.0, 90.0, 1),
        ("SELL", 100.0, 90.0, 100.0, -1),
        ("SELL", 100.0, 90.0, 95.0, 0),
    ])
    def test_both_sides(self, side, sl, tp, exit_price, expected):
        assert derive_triple_barrier_label(
            entry=95.0, exit_price=exit_price, sl=sl, tp=tp, trade_type=side,
        ) == expected

    def test_a_buy_that_runs_past_its_target(self):
        """Overshoot still counts as 'the barrier was reached'."""
        assert derive_triple_barrier_label(
            entry=81318.05, exit_price=81440.35, sl=81268.36, tp=81417.43,
            trade_type="BUY",
        ) == 1

    def test_a_buy_stopped_out_below_its_stop(self):
        assert derive_triple_barrier_label(
            entry=81355.05, exit_price=81299.75, sl=81309.5, tp=81446.15,
            trade_type="BUY",
        ) == -1

    def test_the_live_row_whose_stop_carries_a_float_artifact(self):
        """`sl = 111.29999999999998`, fill `111.30` -> reached, despite `111.3 > sl`."""
        assert LIVE_SL_ROW["exit_price"] > LIVE_SL_ROW["sl"]
        assert derive_triple_barrier_label(**LIVE_SL_ROW) == -1

    def test_a_genuine_near_miss_is_still_a_vertical_barrier(self):
        """The tolerance must not swallow a real miss one tick short of the stop."""
        # BTCUSD: entry 81355, sl 81309.5, exits at 81309.51 -- one tick short.
        assert derive_triple_barrier_label(
            entry=81355.05, exit_price=81309.51, sl=81309.5, tp=81446.15,
            trade_type="BUY",
        ) == 0

    def test_a_sell_stopped_out_above_its_stop(self):
        assert derive_triple_barrier_label(
            entry=1.15388, exit_price=1.15438, sl=1.15438, tp=1.15278,
            trade_type="SELL",
        ) == -1

    def test_contradictory_barriers_are_refused_not_guessed(self):
        """For a BUY, `exit >= tp` and `exit <= sl` can only both hold if `tp <= sl`, i.e.
        the barriers contradict each other. A gap does NOT do it (the fill lands on or
        beyond whichever barrier was touched, which classifies cleanly) -- so this case
        means the row is malformed and must stay unlabelled rather than be given a sign."""
        assert derive_triple_barrier_label(
            entry=100.0, exit_price=100.0, sl=101.0, tp=99.0, trade_type="BUY",
        ) is None

    def test_a_gap_beyond_the_stop_classifies_as_the_stop(self):
        """The fill lands past the barrier; it is still that barrier."""
        assert derive_triple_barrier_label(
            entry=100.0, exit_price=80.0, sl=99.0, tp=101.0, trade_type="BUY",
        ) == -1

    @pytest.mark.parametrize("side,sl,tp", [
        ("buy", 90.0, 110.0), ("BUY", 90.0, 110.0), (" Buy ", 90.0, 110.0),
        ("long", 90.0, 110.0), ("LONG", 90.0, 110.0),
        ("sell", 110.0, 90.0), ("Short", 110.0, 90.0),
    ])
    def test_the_side_is_matched_case_insensitively(self, side, sl, tp):
        """Each spelling must be RECOGNISED -- an unrecognised side returns None instead."""
        assert derive_triple_barrier_label(
            entry=100.0, exit_price=100.0, sl=sl, tp=tp, trade_type=side,
        ) == 0

    @pytest.mark.parametrize("kwargs,why", [
        (dict(entry=100.0, exit_price=0.0, sl=90.0, tp=110.0, trade_type="BUY"), "not closed yet"),
        (dict(entry=0.0, exit_price=100.0, sl=90.0, tp=110.0, trade_type="BUY"), "no entry"),
        (dict(entry=100.0, exit_price=100.0, sl=0.0, tp=110.0, trade_type="BUY"), "no stop"),
        (dict(entry=100.0, exit_price=100.0, sl=90.0, tp=0.0, trade_type="BUY"), "no target"),
        (dict(entry=100.0, exit_price=100.0, sl=90.0, tp=110.0, trade_type="HEDGE"), "unknown side"),
        (dict(entry=100.0, exit_price=100.0, sl=90.0, tp=110.0, trade_type=None), "no side"),
        (dict(entry=None, exit_price=100.0, sl=90.0, tp=110.0, trade_type="BUY"), "missing entry"),
        (dict(entry="x", exit_price=100.0, sl=90.0, tp=110.0, trade_type="BUY"), "non-numeric"),
        (dict(entry=100.0, exit_price=float("nan"), sl=90.0, tp=110.0, trade_type="BUY"), "NaN exit"),
    ])
    def test_unusable_geometry_is_unlabelled_not_zero(self, kwargs, why):
        """`None` is the point: 'unknown' must not read as 'the vertical barrier was hit'."""
        assert derive_triple_barrier_label(**kwargs) is None, why


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------

class TestTradeMemoryWritesTheLabel:
    def test_open_writes_unlabelled_not_a_fabricated_zero(self, memory):
        memory.record_trade({
            "ticket": 1, "symbol": "SOLUSD", "type": "BUY",
            "entry": 112.35, "sl": 111.30, "tp": 114.45,
        })
        assert _label(memory.db_path, 1) is None

    def test_close_derives_the_label_from_the_stored_geometry(self, memory):
        memory.record_trade({
            "ticket": 938435830, "symbol": "SOLUSD", "type": "BUY",
            "entry": 112.35, "sl": 111.29999999999998, "tp": 114.44999999999999,
        })
        memory.update_closed_trade(ticket=938435830, exit_price=111.30, pnl=-1.79, is_win=0)
        assert _label(memory.db_path, 938435830) == -1

    def test_close_labels_a_take_profit_hit(self, memory):
        memory.record_trade({
            "ticket": 938437252, "symbol": "BTCUSD", "type": "BUY",
            "entry": 81318.05, "sl": 81268.36, "tp": 81417.43,
        })
        memory.update_closed_trade(ticket=938437252, exit_price=81440.35, pnl=7.34, is_win=1)
        assert _label(memory.db_path, 938437252) == 1

    def test_close_labels_a_vertical_barrier_exit(self, memory):
        """Closed inside the barriers -> 0, and that 0 is now *earned*, not defaulted."""
        memory.record_trade({
            "ticket": 999333, "symbol": "XAUUSD", "type": "BUY",
            "entry": 2400.0, "sl": 2390.0, "tp": 2425.0,
        })
        memory.update_closed_trade(ticket=999333, exit_price=2420.0, pnl=50.0, is_win=1)
        assert _label(memory.db_path, 999333) == 0

    def test_close_keeps_an_explicit_label_when_geometry_cannot_decide(self, memory):
        """COALESCE, not overwrite: a caller-supplied label is never downgraded to NULL."""
        memory.record_trade({
            "ticket": 7, "symbol": "X", "type": "BUY",
            "entry": 100.0, "sl": 0.0, "tp": 0.0, "triple_barrier_label": 1,
        })
        memory.update_closed_trade(ticket=7, exit_price=101.0, pnl=1.0, is_win=1)
        assert _label(memory.db_path, 7) == 1

    def test_a_caller_supplied_label_survives_open(self, memory):
        memory.record_trade({
            "ticket": 8, "symbol": "X", "type": "BUY",
            "entry": 100.0, "sl": 90.0, "tp": 110.0, "triple_barrier_label": -1,
        })
        assert _label(memory.db_path, 8) == -1

    def test_close_still_writes_the_fields_it_always_did(self, memory):
        """The regression guard: the label must not displace the existing columns."""
        memory.record_trade({
            "ticket": 9, "symbol": "EURUSD", "type": "SELL",
            "entry": 1.15388, "sl": 1.15438, "tp": 1.15278,
        })
        memory.update_closed_trade(
            ticket=9, exit_price=1.15394, pnl=-4.14, is_win=0, mfe=0.0, mae=0.0
        )
        c = sqlite3.connect(f"file:{memory.db_path}?mode=ro", uri=True)
        try:
            row = c.execute(
                "SELECT exit_price, pnl, is_win, mfe, mae FROM trade_records WHERE ticket = 9"
            ).fetchone()
        finally:
            c.close()
        assert row == (1.15394, -4.14, 0, 0.0, 0.0)

    def test_an_unknown_ticket_does_not_raise(self, memory):
        """A close for a row that was never opened must not blow up the caller."""
        memory.update_closed_trade(ticket=424242, exit_price=1.0, pnl=0.0, is_win=0)

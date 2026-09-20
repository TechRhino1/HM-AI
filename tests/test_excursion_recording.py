"""D19 — `mfe` / `mae` must be measured, or NULL. Never a fabricated 0.0.

`orchestrator.py` closed every trade with `mfe=0.0, mae=0.0` **literally**. Maximum favourable /
adverse excursion describe the path a trade took, and the live close handler had no path to describe:
`PositionMonitorEngine` tracked a running favourable extreme but pruned it the moment the position
left `active_tickets`, so by the time the close was handled the only record of the path was gone.

The fix has two halves and both are needed:

1. The monitor accumulates BOTH extremes while the position is open, in price units, and retires them
   into a bounded map when the ticket closes instead of dropping them.
2. When it never sampled a ticket — opened before this process, or closed before the first scan —
   `pop_excursions` returns `None`, and that is written as **NULL**. "Not measured" and "measured, no
   excursion" are different facts; a learner that cannot tell them apart is being fed a fabricated
   zero, which is the same defect shape as C2 and D5.

Known limitation, stated rather than hidden: the monitor samples at its own cadence against the last
price, not against bar high/low, so an extreme reached between two scans is missed. The value is a
LOWER BOUND on the true intrabar excursion. It is still a measurement; 0.0 was not.
"""

import sqlite3

import pytest

from jarvis.execution.position_monitor import PositionMonitorEngine
from jarvis.learning.trade_memory import TradeMemory

TICKET = 4242


def _columns(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {r[1] for r in c.execute("PRAGMA table_info(trade_records)")}
    finally:
        c.close()


def _row(path, ticket=TICKET):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return c.execute(
            "SELECT mfe, mae FROM trade_records WHERE ticket = ?", (ticket,)
        ).fetchone()
    finally:
        c.close()


@pytest.fixture
def monitor():
    return PositionMonitorEngine.__new__(PositionMonitorEngine)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "jarvis_trade_memory.db"


class TestUnmeasuredIsNullNotZero:
    def test_a_ticket_the_monitor_never_saw_returns_none(self, monitor):
        monitor._closed_excursions = {}
        monitor._closed_excursions_order = []
        assert monitor.pop_excursions(TICKET) is None

    def test_reading_a_twice_credits_it_once(self, monitor):
        """A ticket must not be able to donate its excursion to two closes."""
        monitor._closed_excursions = {TICKET: (1.5, 0.75)}
        monitor._closed_excursions_order = [TICKET]
        assert monitor.pop_excursions(TICKET) == (1.5, 0.75)
        assert monitor.pop_excursions(TICKET) is None

    def test_a_garbage_ticket_does_not_raise(self, monitor):
        monitor._closed_excursions = {}
        monitor._closed_excursions_order = []
        assert monitor.pop_excursions(None) is None
        assert monitor.pop_excursions("not-a-ticket") is None

    def test_the_close_writes_null_when_nothing_was_measured(self, db):
        tm = TradeMemory(db_path=str(db))
        tm.record_trade({"ticket": TICKET, "symbol": "EURUSD", "type": "BUY",
                         "entry": 1.1, "sl": 1.09, "tp": 1.12})
        tm.update_closed_trade(ticket=TICKET, exit_price=1.105, pnl=5.0, is_win=1,
                               mfe=None, mae=None)
        tm.close()
        assert _row(db) == (None, None), "unmeasured was written as a number"

    def test_the_open_does_not_fabricate_a_zero(self, db):
        """At open there is no path yet, so the row must stay unmeasured."""
        tm = TradeMemory(db_path=str(db))
        tm.record_trade({"ticket": TICKET, "symbol": "EURUSD", "type": "BUY",
                         "entry": 1.1, "sl": 1.09, "tp": 1.12})
        tm.close()
        assert _row(db) == (None, None)

    def test_a_measured_zero_is_still_a_zero(self, db):
        """The distinction that matters: 0.0 passed deliberately is a measurement."""
        tm = TradeMemory(db_path=str(db))
        tm.record_trade({"ticket": TICKET, "symbol": "EURUSD", "type": "BUY",
                         "entry": 1.1, "sl": 1.09, "tp": 1.12})
        tm.update_closed_trade(ticket=TICKET, exit_price=1.1, pnl=0.0, is_win=0,
                               mfe=0.0, mae=0.0)
        tm.close()
        assert _row(db) == (0.0, 0.0)

    def test_a_measured_value_survives_the_round_trip(self, db):
        tm = TradeMemory(db_path=str(db))
        tm.record_trade({"ticket": TICKET, "symbol": "EURUSD", "type": "BUY",
                         "entry": 1.1, "sl": 1.09, "tp": 1.12})
        tm.update_closed_trade(ticket=TICKET, exit_price=1.11, pnl=10.0, is_win=1,
                               mfe=0.0045, mae=0.0021)
        tm.close()
        mfe, mae = _row(db)
        assert mfe == pytest.approx(0.0045)
        assert mae == pytest.approx(0.0021)

    def test_a_caller_supplied_value_at_open_is_respected(self, db):
        tm = TradeMemory(db_path=str(db))
        tm.record_trade({"ticket": TICKET, "symbol": "EURUSD", "type": "BUY",
                         "entry": 1.1, "sl": 1.09, "tp": 1.12, "mfe": 2.0, "mae": 1.0})
        tm.close()
        assert _row(db) == (2.0, 1.0)


class _Null:
    """Absorbs any call. The close handler fans out to five collaborators; this
    test is about what it writes to the journal, not about any of them."""

    def __getattr__(self, name):
        return _Null()

    def __call__(self, *a, **k):
        return None


class _FakeMonitor:
    def __init__(self, excursions):
        self._excursions = excursions
        self.asked_for = []

    def pop_excursions(self, ticket):
        self.asked_for.append(ticket)
        return self._excursions


class TestTheCloseHandlerStopsFabricatingZeros:
    """`orchestrator.py` used to pass `mfe=0.0, mae=0.0` literally. This pins that
    it now asks the monitor, and that "the monitor has nothing" is written as NULL."""

    def _close(self, db, excursions, ticket=TICKET):
        from jarvis.application.orchestrator import JarvisOrchestrator

        tm = TradeMemory(db_path=str(db))
        tm.record_trade({"ticket": ticket, "symbol": "EURUSD", "type": "BUY",
                         "entry": 1.1, "sl": 1.09, "tp": 1.12})

        orch = JarvisOrchestrator.__new__(JarvisOrchestrator)
        orch._pending_features = {}
        orch.trade_memory = tm
        monitor = _FakeMonitor(excursions)
        orch.position_monitor = monitor
        orch.ml_predictor = _Null()
        orch.strategy_bandit = _Null()
        orch.circuit_breaker = _Null()
        orch.drawdown_guard = _Null()
        orch.decision_engine = _Null()

        orch._on_trade_closed({"ticket": ticket, "pnl": 5.0, "exit_price": 1.105,
                               "equity": 0.0, "symbol": "EURUSD"})
        tm.close()
        return monitor

    def test_the_measured_excursion_reaches_the_journal(self, db):
        self._close(db, (1.5, 0.75))
        mfe, mae = _row(db)
        assert mfe == pytest.approx(1.5)
        assert mae == pytest.approx(0.75)

    def test_an_unsampled_ticket_is_null_not_zero(self, db):
        """THE DEFECT. The monitor never saw this ticket — it was opened before
        this process started. 0.0 would claim the path was measured and flat."""
        self._close(db, None)
        assert _row(db) == (None, None)

    def test_the_handler_asks_the_monitor_for_the_closed_ticket(self, db):
        monitor = self._close(db, (0.25, 0.5))
        assert monitor.asked_for == [TICKET]


class TestTheMonitorRetainsTheClosedPath:
    def _seed(self, monitor, mfe, mae):
        monitor._peak_mfe = {TICKET: mfe}
        monitor._peak_mae = {TICKET: mae}
        monitor._closed_excursions = {}
        monitor._closed_excursions_order = []
        monitor._closed_excursions_cap = 512

    def test_a_closed_ticket_is_remembered_not_dropped(self, monitor):
        """The prune step used to delete these outright. That deletion is the
        reason the close handler had nothing to write."""
        self._seed(monitor, 1.25, 0.5)
        monitor._remember_closed_excursions(TICKET)
        assert monitor.pop_excursions(TICKET) == (1.25, 0.5)

    def test_a_ticket_with_no_samples_is_not_remembered(self, monitor):
        self._seed(monitor, None, None)
        monitor._remember_closed_excursions(TICKET)
        assert monitor.pop_excursions(TICKET) is None

    def test_the_history_is_bounded(self, monitor):
        """The monitor runs for the life of the process; an unbounded map grows
        with every trade the account ever takes."""
        self._seed(monitor, 0.0, 0.0)
        monitor._closed_excursions_cap = 8
        for t in range(50):
            monitor._peak_mfe[t] = float(t)
            monitor._peak_mae[t] = 0.0
            monitor._remember_closed_excursions(t)
        assert len(monitor._closed_excursions) <= 8
        # The newest survive; the oldest are evicted.
        assert 49 in monitor._closed_excursions
        assert 0 not in monitor._closed_excursions


class TestExcursionArithmetic:
    """Excursion is a DISTANCE in price units, signed by direction, matching the
    backtest's `fav = (h - fill) * direction` / `adv = (fill - lo) * direction`."""

    @pytest.mark.parametrize("side,prices,open_price,expected", [
        ("BUY", [1.1000, 1.1050, 1.1020], 1.1000, (0.0050, 0.0)),
        ("BUY", [1.1000, 1.0950, 1.1010], 1.1000, (0.0010, 0.0050)),
        ("SELL", [1.1000, 1.0950, 1.0980], 1.1000, (0.0050, 0.0)),
        ("SELL", [1.1000, 1.1050, 1.0990], 1.1000, (0.0010, 0.0050)),
    ])
    def test_both_extremes_accumulate(self, monitor, side, prices, open_price, expected):
        monitor._peak_mfe = {}
        monitor._peak_mae = {}
        monitor._highest_favorable_price = {}
        monitor._lowest_adverse_price = {}

        # Replicates the tracking block in `_manage_single_position` without
        # needing a broker: the monitor only ever sees the last price.
        for c_price in prices:
            if side == "BUY":
                high = max(monitor._highest_favorable_price.get(TICKET, open_price), c_price)
                low = min(monitor._lowest_adverse_price.get(TICKET, open_price), c_price)
                monitor._highest_favorable_price[TICKET] = high
                monitor._lowest_adverse_price[TICKET] = low
                fav = max(0.0, high - open_price)
                adv = max(0.0, open_price - low)
            else:
                low = min(monitor._highest_favorable_price.get(TICKET, open_price), c_price)
                high = max(monitor._lowest_adverse_price.get(TICKET, open_price), c_price)
                monitor._highest_favorable_price[TICKET] = low
                monitor._lowest_adverse_price[TICKET] = high
                fav = max(0.0, open_price - low)
                adv = max(0.0, high - open_price)
            monitor._peak_mfe[TICKET] = max(monitor._peak_mfe.get(TICKET, 0.0), fav)
            monitor._peak_mae[TICKET] = max(monitor._peak_mae.get(TICKET, 0.0), adv)

        assert monitor._peak_mfe[TICKET] == pytest.approx(expected[0], abs=1e-9)
        assert monitor._peak_mae[TICKET] == pytest.approx(expected[1], abs=1e-9)

    def test_the_monitor_exposes_the_accessor(self):
        assert callable(getattr(PositionMonitorEngine, "pop_excursions", None))
        assert callable(getattr(PositionMonitorEngine, "_remember_closed_excursions", None))

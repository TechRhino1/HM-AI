"""MT5 stamps times on the BROKER's clock, not in UTC.

``position.time``, ``deal.time`` and ``symbol_info_tick().time`` are all seconds
on the broker server's clock. XM runs EET/EEST — GMT+2 in winter, GMT+3 in
summer — so reading one of those as UTC puts it 2-3 hours in the FUTURE.

That is not cosmetic, because the consumer subtracts it from the real UTC clock:

    duration = datetime.now(timezone.utc) - open_time      # 2-3 hours SHORT

and then clamps with ``max(0.0, ...)``, which flattens the entire first three
hours of a position's life to zero. ``position_monitor.py`` gates its
horizon-adaptive stagnation exits on ``if open_dur_sec > 0``, so:

  * the SCALP "45 minutes without progress" exit could not fire until 3h45m,
  * the DAY_TRADING 6h exit fired at 9h,
  * the whole time-decay block was skipped for the first ~3 hours of every
    position — exactly the window those rules exist to police,

and ``_determine_position_style``, which falls back to duration, called a
25-hour-old position DAY_TRADING instead of SWING.

The tests below pin the PROPERTY (an hour-old position reports about an hour)
rather than the implementation, so they fail on the pre-fix code, where that
same position reported zero.

``jarvis.data.broker_time`` derives the offset from the broker's own freshest
tick rather than hardcoding it: the offset moves by an hour with the broker's
DST, differs per broker (+5:30 for India), and deriving it survives pointing the
account at a different server.
"""
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from jarvis.data import broker_time
from jarvis.data.broker_time import broker_utc_offset
from jarvis.data.schemas import PositionSnapshot

XM_OFFSET = 3 * 3600  # XM in summer: GMT+3


class _FakeTick:
    def __init__(self, t):
        self.time = t


class _FakeMT5:
    """Minimal stand-in: a tick per symbol, and a position list."""

    POSITION_TYPE_BUY = 0

    def __init__(self, ticks=None, positions=None):
        self._ticks = ticks or {}
        self._positions = positions or []

    def symbol_info_tick(self, sym):
        t = self._ticks.get(sym)
        return None if t is None else _FakeTick(t)

    def positions_get(self, symbol=None):
        return list(self._positions)


def _fake_position(symbol, open_epoch):
    return SimpleNamespace(
        ticket=1, symbol=symbol, type=0, volume=0.10,
        price_open=1.1000, price_current=1.1000, sl=0.0, tp=0.0,
        profit=0.0, swap=0.0, commission=0.0,
        time=open_epoch, magic=888999, comment="JARVIS_3.0",
    )


class TestBrokerOffsetDerivation(unittest.TestCase):
    def setUp(self):
        broker_time.reset_cache()

    def test_derives_whole_hour_offset(self):
        now = time.time()
        fake = _FakeMT5({"EURUSD": int(now) + XM_OFFSET})
        self.assertEqual(
            broker_utc_offset(mt5_module=fake, symbols=["EURUSD"], now=now), XM_OFFSET)

    def test_derives_half_hour_offset(self):
        """India sits on a half hour; snapping to whole hours would lose it."""
        now = time.time()
        fake = _FakeMT5({"USDINR": int(now) + 5 * 3600 + 1800})
        self.assertEqual(
            broker_utc_offset(mt5_module=fake, symbols=["USDINR"], now=now), 5 * 3600 + 1800)

    def test_winter_offset_is_derived_not_hardcoded(self):
        """The same account reads +2h after the broker's DST change."""
        now = time.time()
        fake = _FakeMT5({"EURUSD": int(now) + 2 * 3600})
        self.assertEqual(
            broker_utc_offset(mt5_module=fake, symbols=["EURUSD"], now=now), 2 * 3600)

    def test_stale_tick_is_rejected(self):
        """A weekend-stale tick is not near an hour boundary, so it is refused."""
        now = time.time()
        stale = int(now) + XM_OFFSET - (3 * 3600 + 20 * 60)
        fake = _FakeMT5({"EURUSD": stale})
        self.assertEqual(broker_utc_offset(mt5_module=fake, symbols=["EURUSD"], now=now), 0)

    def test_out_of_range_offset_is_rejected(self):
        now = time.time()
        fake = _FakeMT5({"EURUSD": int(now) + 30 * 3600})
        self.assertEqual(broker_utc_offset(mt5_module=fake, symbols=["EURUSD"], now=now), 0)

    def test_no_tick_at_all_degrades_to_zero(self):
        now = time.time()
        self.assertEqual(
            broker_utc_offset(mt5_module=_FakeMT5({}), symbols=["EURUSD"], now=now), 0)

    def test_freshest_tick_wins(self):
        """A quiet symbol must not drag the estimate backwards."""
        now = time.time()
        fake = _FakeMT5({
            "EURUSD": int(now) + XM_OFFSET,
            "EXOTIC": int(now) + XM_OFFSET - 900,  # 15 min stale
        })
        self.assertEqual(
            broker_utc_offset(mt5_module=fake, symbols=["EURUSD", "EXOTIC"], now=now), XM_OFFSET)

    def test_cached_within_ttl(self):
        now = time.time()
        fake = _FakeMT5({"EURUSD": int(now) + XM_OFFSET})
        first = broker_utc_offset(mt5_module=fake, symbols=["EURUSD"], now=now)
        self.assertEqual(first, XM_OFFSET)
        # A tick that would derive a DIFFERENT offset: within the TTL the cache
        # must answer, because each derivation costs an MT5 round-trip per symbol.
        shifted = _FakeMT5({"EURUSD": int(now) + 9 * 3600})
        self.assertEqual(
            broker_utc_offset(mt5_module=shifted, symbols=["EURUSD"], now=now + 60), first)


class TestPositionOpenTimeIsTrueUtc(unittest.TestCase):
    """The defect: open_time was broker time wearing a UTC label."""

    def setUp(self):
        broker_time.reset_cache()

    def _client(self, fake_mt5):
        import jarvis.execution.mt5_client as m
        client = m.MT5Client.__new__(m.MT5Client)
        client.mode = "live"
        client.is_connected = True
        client.magic_number = 888999
        client.timeout_sec = 4.0
        client.symbol_alias_cache = {}
        client._paper_positions = {}
        client._paper_pending_orders = {}
        client._lock = threading.RLock()
        self._patches = [
            patch.object(m, "mt5", fake_mt5),
            patch.object(m, "MT5_AVAILABLE", True),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patches])
        return client

    def test_open_time_is_utc_not_broker_time(self):
        now = int(time.time())
        # Opened one real hour ago; MT5 reports it on the broker's clock.
        open_epoch = now + XM_OFFSET - 3600
        fake = _FakeMT5(
            ticks={"EURUSD": now + XM_OFFSET},
            positions=[_fake_position("EURUSD", open_epoch)],
        )
        snap = self._client(fake).get_open_positions()[0]

        stamped = datetime.strptime(snap.open_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - stamped).total_seconds()

        # Pre-fix this was -7200s: two hours into the future.
        self.assertAlmostEqual(elapsed, 3600, delta=60)

    def test_holding_time_survives_the_stagnation_guard(self):
        """The guard that decides whether the time-decay exits run at all."""
        now = int(time.time())
        open_epoch = now + XM_OFFSET - 3600
        fake = _FakeMT5(
            ticks={"EURUSD": now + XM_OFFSET},
            positions=[_fake_position("EURUSD", open_epoch)],
        )
        snap = self._client(fake).get_open_positions()[0]

        from jarvis.execution.position_monitor import PositionMonitorEngine
        monitor = PositionMonitorEngine(
            mt5_client=MagicMock(), data_feed=MagicMock(), context_engine=MagicMock(),
            state_manager=MagicMock(), event_bus=MagicMock(),
        )
        dur = monitor._get_position_duration_sec(snap)

        # `if open_dur_sec > 0:` gates every stagnation exit. Pre-fix this was 0.0.
        self.assertGreater(dur, 0.0)
        self.assertAlmostEqual(dur, 3600, delta=60)

    def test_style_is_not_misclassified_by_the_skew(self):
        """A 25-hour position must read SWING, not DAY_TRADING."""
        now = int(time.time())
        open_epoch = now + XM_OFFSET - (25 * 3600)
        fake = _FakeMT5(
            ticks={"EURUSD": now + XM_OFFSET},
            positions=[_fake_position("EURUSD", open_epoch)],
        )
        snap = self._client(fake).get_open_positions()[0]

        from jarvis.execution.position_monitor import PositionMonitorEngine
        monitor = PositionMonitorEngine(
            mt5_client=MagicMock(), data_feed=MagicMock(), context_engine=MagicMock(),
            state_manager=MagicMock(), event_bus=MagicMock(),
        )
        style = monitor._determine_position_style(snap, MagicMock())
        self.assertEqual(style, "SWING")

    def test_paper_open_time_was_already_correct(self):
        """Paper mode stamps the real clock, so the fix must not double-shift it."""
        import jarvis.execution.mt5_client as m
        client = m.MT5Client.__new__(m.MT5Client)
        client.mode = "paper"
        client.is_connected = True
        client.magic_number = 888999
        client.symbol_alias_cache = {}
        client._paper_positions = {
            7: PositionSnapshot(
                ticket=7, symbol="EURUSD", type="BUY", volume=0.1,
                open_price=1.1, current_price=1.1, sl=0.0, tp=0.0,
                profit=0.0, swap=0.0, commission=0.0,
                open_time=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
                magic=888999, comment="[PAPER]",
            )
        }
        client._lock = threading.RLock()

        stamped = datetime.strptime(
            client.get_open_positions()[0].open_time, "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)
        self.assertAlmostEqual(
            (datetime.now(timezone.utc) - stamped).total_seconds(), 0.0, delta=30)


class TestNegativeDurationIsVisible(unittest.TestCase):
    """A negative holding time must not silently become a zero."""

    def test_negative_holding_time_warns(self):
        from jarvis.execution.position_monitor import PositionMonitorEngine
        monitor = PositionMonitorEngine(
            mt5_client=MagicMock(), data_feed=MagicMock(), context_engine=MagicMock(),
            state_manager=MagicMock(), event_bus=MagicMock(),
        )
        future = datetime.now(timezone.utc) + timedelta(hours=3)
        pos = PositionSnapshot(
            ticket=99, symbol="EURUSD", type="BUY", volume=0.1,
            open_price=1.1, current_price=1.1, sl=0.0, tp=0.0,
            profit=0.0, swap=0.0, commission=0.0,
            open_time=future.strftime("%Y-%m-%d %H:%M:%S"),
            magic=888999, comment="",
        )
        with self.assertLogs("JARVIS_PositionMonitor", level="WARNING") as caught:
            dur = monitor._get_position_duration_sec(pos)
        self.assertEqual(dur, 0.0)
        self.assertTrue(
            any("Negative holding time" in m for m in caught.output),
            "a future-dated open_time must be reported, not silently zeroed",
        )


if __name__ == "__main__":
    unittest.main()

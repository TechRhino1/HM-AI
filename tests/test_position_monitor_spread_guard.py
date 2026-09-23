"""Regression tests for the spread-blowout guard in PositionMonitorEngine.

The live defect: `_get_context` called `context_engine.build_context(symbol, mtf)`
without `current_spread_pips`, so the context always carried the global default
of 2.0 (jarvis/market/market_context.py:36). The guard then compared that 2.0
against `typical_spread * SPREAD_BLOWOUT_MULT`, which for EURUSD (0.7),
GBPUSD (0.9), USDJPY (0.8) and AUDUSD (0.9) is below 2.0 — so the guard fired on
*every* call and silently disabled trailing stops, breakeven moves and partial
closes on four of the highest-volume FX majors.

These tests drive the real `_get_context` path with a context engine that mirrors
`MarketContextEngine.build_context`'s signature *including its 2.0 default*, so a
caller that forgets to pass the spread reproduces the defect exactly. They fail
against the pre-fix code.
"""
import contextlib
import logging
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from jarvis.data.schemas import (
    LiquidityContext,
    MarketContext,
    MomentumContext,
    PositionSnapshot,
    SessionContext,
    StructureContext,
    VolatilityContext,
)
from jarvis.data.symbol_registry import resolve
from jarvis.execution.position_monitor import (
    JARVIS_MAGIC_NUMBER,
    PositionMonitorEngine,
    SPREAD_BLOWOUT_MULT,
)

LOG = logging.getLogger("JARVIS_PositionMonitor")

# symbol -> (open_price, stop_loss, current_price, atr)
# Each scenario is a profitable BUY at ~+2.5R, which forces the canonical exit
# policy to ratchet the stop and therefore call mt5_client.modify_position.
_SCENARIOS = {
    "EURUSD": (1.10000, 1.09000, 1.12500, 0.01000),
    "GBPUSD": (1.25000, 1.24000, 1.27500, 0.01000),
    "USDJPY": (150.000, 149.000, 152.500, 1.000),
    "AUDUSD": (0.65000, 0.64000, 0.67500, 0.01000),
    "EURJPY": (160.000, 159.000, 162.500, 1.000),
    "XAUUSD": (2400.00, 2390.00, 2425.00, 10.00),
}

FOUR_MAJORS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]


class _CaptureHandler(logging.Handler):
    """Collects warning messages without requiring at least one to be emitted
    (unlike assertLogs, which fails when nothing is logged)."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@contextlib.contextmanager
def _captured_warnings():
    handler = _CaptureHandler()
    LOG.addHandler(handler)
    try:
        yield handler.messages
    finally:
        LOG.removeHandler(handler)


def _make_context(symbol, spread_pips, price, atr):
    return MarketContext(
        symbol=symbol,
        timestamp=datetime.now(timezone.utc),
        current_price=price,
        bid=price,
        ask=price,
        structure=StructureContext(bias="BULLISH"),
        liquidity=LiquidityContext(),
        volatility=VolatilityContext(atr=atr, current_spread_pips=spread_pips),
        momentum=MomentumContext(trend_score=50.0, adx=25.0),
        session=SessionContext(is_prime_session=True),
    )


class _RecordingContextEngine:
    """Faithful stand-in for MarketContextEngine.build_context.

    It reproduces the real signature *and* the real default
    `current_spread_pips=2.0`, so any caller that omits the spread produces the
    same bogus context the live code produced. Calls are recorded so tests can
    assert what was actually passed.
    """

    def __init__(self):
        self.calls = []

    def build_context(
        self,
        symbol,
        mtf_data,
        current_spread_pips=2.0,
        max_allowed_spread_pips=35.0,
        trade_style="SWING",
    ):
        self.calls.append(
            {
                "symbol": symbol,
                "current_spread_pips": current_spread_pips,
                "max_allowed_spread_pips": max_allowed_spread_pips,
            }
        )
        _open, _sl, price, atr = _SCENARIOS.get(symbol, (1.0, 0.9, 1.0, 0.01))
        return _make_context(symbol, current_spread_pips, price, atr)


def _build_monitor():
    engine = _RecordingContextEngine()
    data_feed = MagicMock()
    data_feed.fetch_multi_timeframe.return_value = {"primary": object()}
    mt5 = MagicMock()
    mt5.modify_position.return_value = {"status": "MODIFIED"}
    mt5.close_position.return_value = {"status": "CLOSED"}
    monitor = PositionMonitorEngine(
        mt5_client=mt5,
        data_feed=data_feed,
        context_engine=engine,
        state_manager=MagicMock(),
        event_bus=MagicMock(),
    )
    return monitor, engine, mt5


def _profitable_position(symbol):
    open_price, sl, price, _atr = _SCENARIOS[symbol]
    return PositionSnapshot(
        ticket=9001,
        symbol=symbol,
        type="BUY",
        volume=1.00,
        open_price=open_price,
        current_price=price,
        sl=sl,
        tp=price * 2,
        profit=100.0,
        swap=0.0,
        commission=0.0,
        open_time=datetime.now(timezone.utc).isoformat(),
        magic=JARVIS_MAGIC_NUMBER,
    )


class TestContextFeedsSymbolSpread(unittest.TestCase):
    """`_get_context` must pass the symbol's own typical spread to build_context."""

    def test_each_major_passes_its_typical_spread(self):
        for symbol in FOUR_MAJORS:
            with self.subTest(symbol=symbol):
                monitor, engine, _mt5 = _build_monitor()
                ctx = monitor._get_context(symbol)

                self.assertEqual(len(engine.calls), 1)
                passed = engine.calls[0]["current_spread_pips"]
                typical = resolve(symbol).typical_spread_pips
                self.assertEqual(
                    passed,
                    typical,
                    f"{symbol}: build_context must receive the symbol's own "
                    f"typical spread, not the global 2.0 default",
                )
                # And it must be the value that lands in the context the guard reads.
                self.assertEqual(ctx.volatility.current_spread_pips, typical)

    def test_no_major_is_fed_a_spread_that_trips_its_own_guard(self):
        """The fed spread must be <= typical*MULT, i.e. the guard cannot fire
        on the monitor's own context for a normal market."""
        for symbol in FOUR_MAJORS:
            with self.subTest(symbol=symbol):
                typical = resolve(symbol).typical_spread_pips
                monitor, _engine, _mt5 = _build_monitor()
                ctx = monitor._get_context(symbol)
                self.assertFalse(
                    ctx.volatility.current_spread_pips > typical * SPREAD_BLOWOUT_MULT,
                    f"{symbol}: normal-market spread must not trip the guard",
                )

    def test_unknown_symbol_falls_back_without_raising(self):
        monitor, engine, _mt5 = _build_monitor()
        # Resolve raises for an unknown symbol; _get_context must still return a
        # context rather than propagate the error.
        ctx = monitor._get_context("NOT_A_REAL_SYMBOL")
        self.assertIsNotNone(ctx)
        self.assertGreater(engine.calls[0]["current_spread_pips"], 0.0)


class TestGuardDoesNotFireUnderNormalSpread(unittest.TestCase):
    """Regression: under a normal spread, position modification must proceed.

    These are the tests that fail on the pre-fix code, where the context carried
    a hardcoded 2.0 and the guard skipped every modification on the four majors.
    """

    def test_modification_proceeds_for_each_major(self):
        for symbol in FOUR_MAJORS:
            with self.subTest(symbol=symbol):
                monitor, _engine, mt5 = _build_monitor()
                pos = _profitable_position(symbol)

                with _captured_warnings() as captured:
                    monitor._manage_single_position(pos)

                blowout = [m for m in captured if "Spread blowout" in m]
                self.assertEqual(
                    blowout,
                    [],
                    f"{symbol}: spread-blowout guard fired under a normal spread",
                )
                self.assertTrue(
                    mt5.modify_position.called,
                    f"{symbol}: position modification was skipped — the guard "
                    f"blocked trailing/breakeven on a normal spread",
                )


class TestGuardStillFiresOnBlowout(unittest.TestCase):
    """The guard must remain functional for a genuine spread blowout."""

    def test_guard_fires_and_skips_when_spread_exceeds_2x_typical(self):
        symbol = "EURUSD"
        typical = resolve(symbol).typical_spread_pips
        blowout = typical * SPREAD_BLOWOUT_MULT + 0.5  # clearly > 2x

        monitor, _engine, mt5 = _build_monitor()
        _open, _sl, price, atr = _SCENARIOS[symbol]
        monitor._ctx_cache[symbol] = (
            _make_context(symbol, blowout, price, atr),
            time.monotonic(),
        )

        with _captured_warnings() as captured:
            monitor._manage_single_position(_profitable_position(symbol))

        self.assertTrue(
            any("Spread blowout" in m for m in captured),
            "guard must fire when the spread exceeds 2x the symbol's typical",
        )
        mt5.modify_position.assert_not_called()


class TestThresholdsAreSymbolSpecific(unittest.TestCase):
    """Thresholds must differ per symbol, and the EURJPY boundary must hold."""

    def test_same_spread_fires_for_tight_symbol_not_for_wide_symbol(self):
        """A spread of 1.5 pips exceeds EURUSD's 2x0.7=1.4 threshold but is far
        below XAUUSD's 2x2.0=4.0 — so the guard must be symbol-specific."""
        spread = 1.5
        self.assertGreater(spread, resolve("EURUSD").typical_spread_pips * SPREAD_BLOWOUT_MULT)
        self.assertLess(spread, resolve("XAUUSD").typical_spread_pips * SPREAD_BLOWOUT_MULT)

        for symbol, expect_fire in (("EURUSD", True), ("XAUUSD", False)):
            with self.subTest(symbol=symbol):
                monitor, _engine, mt5 = _build_monitor()
                _open, _sl, price, atr = _SCENARIOS[symbol]
                monitor._ctx_cache[symbol] = (
                    _make_context(symbol, spread, price, atr),
                    time.monotonic(),
                )
                with _captured_warnings() as captured:
                    monitor._manage_single_position(_profitable_position(symbol))
                fired = any("Spread blowout" in m for m in captured)
                self.assertEqual(fired, expect_fire)
                self.assertEqual(mt5.modify_position.called, not expect_fire)

    def test_eurjpy_boundary_is_exclusive(self):
        """EURJPY typical is exactly 1.0, so its threshold is exactly 2.0.
        The guard is `spread > threshold`, so a spread of exactly 2.0 must NOT
        fire and 2.0 + epsilon must. This pins the boundary that would flip if
        the multiplier or the calibration ever moved."""
        symbol = "EURJPY"
        typical = resolve(symbol).typical_spread_pips
        threshold = typical * SPREAD_BLOWOUT_MULT
        self.assertEqual(threshold, 2.0)

        for spread, expect_fire in ((threshold, False), (threshold + 0.01, True)):
            with self.subTest(spread=spread):
                monitor, _engine, mt5 = _build_monitor()
                _open, _sl, price, atr = _SCENARIOS[symbol]
                monitor._ctx_cache[symbol] = (
                    _make_context(symbol, spread, price, atr),
                    time.monotonic(),
                )
                with _captured_warnings() as captured:
                    monitor._manage_single_position(_profitable_position(symbol))
                fired = any("Spread blowout" in m for m in captured)
                self.assertEqual(
                    fired,
                    expect_fire,
                    f"EURJPY boundary: spread={spread} threshold={threshold}",
                )


if __name__ == "__main__":
    unittest.main()

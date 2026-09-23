"""
Regression tests for the stop-distance floor in InstitutionalEntryEngine.

Bug: `risk_dist` was lifted to the `pip_size * 5` floor while `sl_price` kept the
tighter structural stop, so the two described different levels (sizing priced risk
off one number, the actual stop sat elsewhere, and R:R was derived from a stale
distance). The floor is now applied to the stop DISTANCE and `sl_price` is derived
from it, on every branch (structural + re-enforce), for BUY and SELL, across the
SCALP, DAY_TRADING and SWING protocols.

Invariant pinned here: `risk_dist == abs(entry_price - sl_price)` and
`risk_dist >= pip_size * 5` on every path.
"""
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from jarvis.data.schemas import (
    MarketContext, StructureContext, LiquidityContext,
    VolatilityContext, MomentumContext, SessionContext,
    RegimeOutput, MarketRegime,
)
from jarvis.data.symbol_registry import resolve as resolve_symbol
from jarvis.intelligence.institutional_entry_engine import InstitutionalEntryEngine


def _make_context(symbol, price, atr, spread_pips=0.0, bid=None, ask=None):
    """Minimal MarketContext with bid == ask (spread_dist == 0) unless overridden."""
    bid = price if bid is None else bid
    ask = price if ask is None else ask
    return MarketContext(
        symbol=symbol,
        timestamp=datetime(2026, 9, 3, 13, 0, tzinfo=timezone.utc),
        current_price=price,
        bid=bid,
        ask=ask,
        structure=StructureContext(
            bias="BULLISH",
            demand_zone=(price * 0.98, price * 0.99),
            supply_zone=(price * 1.01, price * 1.02),
        ),
        liquidity=LiquidityContext(
            sell_side_liquidity=price * 0.95,
            buy_side_liquidity=price * 1.05,
        ),
        volatility=VolatilityContext(atr=atr, current_spread_pips=spread_pips),
        momentum=MomentumContext(trend_score=30, adx=25.0),
        session=SessionContext(is_prime_session=True),
    )


def _regime():
    return RegimeOutput(
        primary_regime=MarketRegime.TREND_BULL, probabilities={}, confidence=0.80
    )


def _make_ohlcv(start_price=2400.0, num_bars=50, trend=1.0, step=0.5):
    times = [datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)] * num_bars
    opens, highs, lows, closes, vols = [], [], [], [], []
    p = start_price
    for i in range(num_bars):
        o = p
        c = o + (trend * step) + (0.2 if i % 2 == 0 else -0.2)
        opens.append(o)
        highs.append(max(o, c) + 0.8)
        lows.append(min(o, c) - 0.8)
        closes.append(c)
        vols.append(100.0 + i)
        p = c
    return pd.DataFrame(
        {"time": times, "open": opens, "high": highs, "low": lows,
         "close": closes, "volume": vols}
    )


class _InvariantMixin:
    def assert_stop_invariant(self, res, bias, floor, expect_floored=None):
        entry = res["entry_price"]
        sl = res["sl_price"]
        rd = res["risk_dist"]

        # 1. The two stop descriptors must describe the same level.
        self.assertAlmostEqual(rd, abs(entry - sl), places=9,
                               msg=f"risk_dist {rd} != |entry {entry} - sl {sl}|")

        # 2. Floor is always enforced.
        self.assertGreaterEqual(rd, floor - 1e-9,
                                msg=f"risk_dist {rd} below floor {floor}")

        # 3. Stop sits on the correct side of entry.
        if bias == "BUY":
            self.assertLess(sl, entry)
        else:
            self.assertGreater(sl, entry)

        if expect_floored:
            self.assertAlmostEqual(rd, floor, places=9)


class TestScalpStopFloor(_InvariantMixin, unittest.TestCase):
    """SCALP block: pip_size=1e-4 (EURUSD), floor=5e-4, m5_atr=2e-4."""

    PRICE = 1.10000
    ATR = 2e-4
    FLOOR = 5e-4

    def _run(self, bias, sweep_extreme):
        eng = InstitutionalEntryEngine()
        ctx = _make_context("EURUSD", self.PRICE, atr=0.001, spread_pips=0.0)
        with patch.object(eng, "_estimate_atr", return_value=self.ATR), \
             patch.object(eng, "_detect_liquidity_sweep",
                          return_value=(True, sweep_extreme, True)), \
             patch.object(eng, "_detect_mss_displacement",
                          return_value=(True, 0.60, self.PRICE - 1e-3, self.PRICE + 1e-3)), \
             patch.object(eng, "_find_micro_fvg_ce", return_value=self.PRICE):
            return eng.calculate_entry_and_levels(
                ctx, _regime(), tentative_bias=bias, trade_style="SCALP", mtf_data=None
            )

    def test_buy_tight_structural_stop_is_floored(self):
        # structural distance ~1.4e-4 < 5e-4 floor
        res = self._run("BUY", self.PRICE - 1e-4)
        self.assert_stop_invariant(res, "BUY", self.FLOOR, expect_floored=True)

    def test_buy_re_enforce_branch_is_floored(self):
        # sweep above entry -> re-enforce branch, re-enforce dist ~9e-5 < floor
        res = self._run("BUY", self.PRICE + 1e-3)
        self.assert_stop_invariant(res, "BUY", self.FLOOR, expect_floored=True)

    def test_buy_wide_structural_stop_not_widened(self):
        res = self._run("BUY", self.PRICE - 0.01)
        self.assert_stop_invariant(res, "BUY", self.FLOOR)
        self.assertGreater(res["risk_dist"], self.FLOOR)

    def test_sell_tight_structural_stop_is_floored(self):
        res = self._run("SELL", self.PRICE + 1e-4)
        self.assert_stop_invariant(res, "SELL", self.FLOOR, expect_floored=True)

    def test_sell_re_enforce_branch_is_floored(self):
        res = self._run("SELL", self.PRICE - 1e-3)
        self.assert_stop_invariant(res, "SELL", self.FLOOR, expect_floored=True)

    def test_sell_wide_structural_stop_not_widened(self):
        res = self._run("SELL", self.PRICE + 0.01)
        self.assert_stop_invariant(res, "SELL", self.FLOOR)
        self.assertGreater(res["risk_dist"], self.FLOOR)


class TestDayTradingStopFloor(_InvariantMixin, unittest.TestCase):
    """DAY_TRADING block: pip_size=1e-4 (EURUSD), floor=5e-4, h1_atr=2e-4."""

    PRICE = 1.10000
    ATR = 2e-4
    FLOOR = 5e-4

    def _run(self, bias, origin_swing):
        eng = InstitutionalEntryEngine()
        ctx = _make_context("EURUSD", self.PRICE, atr=0.001, spread_pips=0.0)
        with patch.object(eng, "_estimate_atr", return_value=self.ATR), \
             patch.object(eng, "_find_m15_fvg_midpoint", return_value=self.PRICE), \
             patch.object(eng, "_find_breaker_block_retest", return_value=None), \
             patch.object(eng, "_find_displacement_origin", return_value=origin_swing), \
             patch.object(eng, "_check_h1_alignment", return_value=True), \
             patch.object(eng, "_detect_choch", return_value=True):
            return eng.calculate_entry_and_levels(
                ctx, _regime(), tentative_bias=bias, trade_style="DAY_TRADING", mtf_data=None
            )

    def test_buy_tight_structural_stop_is_floored(self):
        res = self._run("BUY", self.PRICE - 1e-4)
        self.assert_stop_invariant(res, "BUY", self.FLOOR, expect_floored=True)

    def test_buy_re_enforce_branch_is_floored(self):
        # re-enforce dist 0.80*h1_atr = 1.6e-4 < floor
        res = self._run("BUY", self.PRICE + 1e-3)
        self.assert_stop_invariant(res, "BUY", self.FLOOR, expect_floored=True)

    def test_buy_wide_structural_stop_not_widened(self):
        res = self._run("BUY", self.PRICE - 0.01)
        self.assert_stop_invariant(res, "BUY", self.FLOOR)
        self.assertGreater(res["risk_dist"], self.FLOOR)

    def test_sell_tight_structural_stop_is_floored(self):
        res = self._run("SELL", self.PRICE + 1e-4)
        self.assert_stop_invariant(res, "SELL", self.FLOOR, expect_floored=True)

    def test_sell_re_enforce_branch_is_floored(self):
        res = self._run("SELL", self.PRICE - 1e-3)
        self.assert_stop_invariant(res, "SELL", self.FLOOR, expect_floored=True)

    def test_sell_wide_structural_stop_not_widened(self):
        res = self._run("SELL", self.PRICE + 0.01)
        self.assert_stop_invariant(res, "SELL", self.FLOOR)
        self.assertGreater(res["risk_dist"], self.FLOOR)


class TestSwingStopFloor(_InvariantMixin, unittest.TestCase):
    """SWING block: pip_size=0.1 (XAUUSD), floor=0.5, d1_atr=0.25 (cap=0.70)."""

    PRICE = 2400.00
    ATR = 0.25
    FLOOR = 0.5

    def _run(self, bias, range_low, range_high):
        eng = InstitutionalEntryEngine()
        ctx = _make_context("XAUUSD", self.PRICE, atr=1.0, spread_pips=0.0)
        with patch.object(eng, "_estimate_atr", return_value=self.ATR), \
             patch.object(eng, "_get_htf_range", return_value=(range_low, range_high)), \
             patch.object(eng, "_find_htf_order_block", return_value=self.PRICE), \
             patch.object(eng, "_detect_choch", return_value=True):
            return eng.calculate_entry_and_levels(
                ctx, _regime(), tentative_bias=bias, trade_style="SWING", mtf_data=None
            )

    def test_buy_tight_structural_stop_is_floored(self):
        # outer swing 0.1 below entry, buffer 0.40*atr=0.1 -> dist 0.2 < 0.5
        res = self._run("BUY", self.PRICE - 0.1, self.PRICE + 0.1)
        self.assert_stop_invariant(res, "BUY", self.FLOOR, expect_floored=True)

    def test_buy_re_enforce_branch_is_floored(self):
        # outer swing above entry -> re-enforce dist 1.20*atr=0.30 < floor
        res = self._run("BUY", self.PRICE + 1.0, self.PRICE + 2.0)
        self.assert_stop_invariant(res, "BUY", self.FLOOR, expect_floored=True)

    def test_buy_wide_structural_stop_not_widened_or_capped(self):
        # dist 0.6 > floor 0.5 and < max_risk_cap 0.70 -> left alone
        res = self._run("BUY", self.PRICE - 0.5, self.PRICE + 0.5)
        self.assert_stop_invariant(res, "BUY", self.FLOOR)
        self.assertGreater(res["risk_dist"], self.FLOOR)

    def test_sell_tight_structural_stop_is_floored(self):
        res = self._run("SELL", self.PRICE - 0.1, self.PRICE + 0.1)
        self.assert_stop_invariant(res, "SELL", self.FLOOR, expect_floored=True)

    def test_sell_re_enforce_branch_is_floored(self):
        res = self._run("SELL", self.PRICE - 2.0, self.PRICE - 1.0)
        self.assert_stop_invariant(res, "SELL", self.FLOOR, expect_floored=True)

    def test_sell_wide_structural_stop_not_widened_or_capped(self):
        res = self._run("SELL", self.PRICE - 0.5, self.PRICE + 0.5)
        self.assert_stop_invariant(res, "SELL", self.FLOOR)
        self.assertGreater(res["risk_dist"], self.FLOOR)


class TestSwingCapCannotUndercutFloor(_InvariantMixin, unittest.TestCase):
    """The SWING `max_risk_cap` is applied AFTER the `pip_size * 5` floor and had
    no floor of its own, so it could push `risk_dist` back below the floor.

    Reachability: the cap is `2.80 * d1_atr` for XAUUSD (gold/other bucket) and the
    floor is `pip_size * 5` = 0.5, so the cap only undercuts the floor when
    `d1_atr < 0.5 / 2.80`. `d1_atr` is a *daily* true range, so with real data this
    never happens — but a degenerate near-flat D1 frame yielding a tiny positive
    d1_atr slips past the `if d1_atr <= 0` guard and would produce a sub-5-pip stop
    that MT5 rejects, while the sizer derives a huge lot size from the tiny
    risk_dist (the same failure mode as the BTCUSD 0.06-risk -> 100-lot case).

    These tests fail against the pre-fix engine, where `risk_dist` comes out at the
    cap (0.14) instead of the floor (0.5).
    """

    PRICE = 2400.00
    D1_ATR = 0.05          # degenerate: 2.80 * 0.05 = 0.14 < floor 0.5
    FLOOR = 0.5            # pip_size (0.1) * 5

    def _run(self, bias, range_low, range_high):
        eng = InstitutionalEntryEngine()
        ctx = _make_context("XAUUSD", self.PRICE, atr=1.0, spread_pips=0.0)
        with patch.object(eng, "_estimate_atr", return_value=self.D1_ATR), \
             patch.object(eng, "_get_htf_range", return_value=(range_low, range_high)), \
             patch.object(eng, "_find_htf_order_block", return_value=self.PRICE), \
             patch.object(eng, "_detect_choch", return_value=True):
            return eng.calculate_entry_and_levels(
                ctx, _regime(), tentative_bias=bias, trade_style="SWING", mtf_data=None
            )

    def test_cap_is_lifted_to_the_floor(self):
        res = self._run("BUY", self.PRICE - 0.1, self.PRICE + 0.1)
        self.assert_stop_invariant(res, "BUY", self.FLOOR, expect_floored=True)
        self.assertGreaterEqual(res["risk_dist"], self.FLOOR - 1e-9)

    def test_sell_cap_is_lifted_to_the_floor(self):
        res = self._run("SELL", self.PRICE - 0.1, self.PRICE + 0.1)
        self.assert_stop_invariant(res, "SELL", self.FLOOR, expect_floored=True)
        self.assertGreaterEqual(res["risk_dist"], self.FLOOR - 1e-9)

    def test_cap_still_binds_above_the_floor(self):
        """The clamp must not disable the cap: a normal D1 ATR still trims."""
        eng = InstitutionalEntryEngine()
        ctx = _make_context("XAUUSD", self.PRICE, atr=1.0, spread_pips=0.0)
        with patch.object(eng, "_estimate_atr", return_value=1.0), \
             patch.object(eng, "_get_htf_range", return_value=(self.PRICE - 5.0, self.PRICE + 5.0)), \
             patch.object(eng, "_find_htf_order_block", return_value=self.PRICE), \
             patch.object(eng, "_detect_choch", return_value=True):
            res = eng.calculate_entry_and_levels(
                ctx, _regime(), tentative_bias="BUY", trade_style="SWING", mtf_data=None
            )
        # Structural distance is ~5.0, cap is 2.80 * 1.0 = 2.80 -> the cap applies.
        self.assertAlmostEqual(res["risk_dist"], 2.80, places=6)


class TestEndToEndStopInvariant(_InvariantMixin, unittest.TestCase):
    """End-to-end (no mocks): the invariant must hold for BUY and SELL, all styles."""

    def _run(self, style, bias):
        eng = InstitutionalEntryEngine()
        symbol = "XAUUSD" if style == "SWING" else "EURUSD"
        price = 2400.0 if symbol == "XAUUSD" else 1.0850
        ctx = _make_context(symbol, price, atr=5.0 if symbol == "XAUUSD" else 0.0030,
                            spread_pips=2.0, bid=price - 0.0002, ask=price + 0.0002)
        mtf = {
            "M5": _make_ohlcv(start_price=price, num_bars=30, trend=-0.3),
            "M1": _make_ohlcv(start_price=price, num_bars=30, trend=-0.3),
            "M15": _make_ohlcv(start_price=price, num_bars=30, trend=0.0002, step=0.0001),
            "H1": _make_ohlcv(start_price=price, num_bars=30, trend=0.0003, step=0.0001),
            "H4": _make_ohlcv(start_price=price, num_bars=30, trend=-0.8, step=1.8),
            "D1": _make_ohlcv(start_price=price, num_bars=30, trend=-1.0, step=2.5),
        }
        return eng.calculate_entry_and_levels(
            ctx, _regime(), tentative_bias=bias, trade_style=style, mtf_data=mtf
        )

    def test_invariant_all_styles_both_biases(self):
        for style in ("SCALP", "DAY_TRADING", "SWING"):
            for bias in ("BUY", "SELL"):
                with self.subTest(style=style, bias=bias):
                    res = self._run(style, bias)
                    entry = res["entry_price"]
                    sl = res["sl_price"]
                    rd = res["risk_dist"]
                    self.assertAlmostEqual(rd, abs(entry - sl), places=9)
                    if bias == "BUY":
                        self.assertLess(sl, entry)
                    else:
                        self.assertGreater(sl, entry)


if __name__ == "__main__":
    unittest.main()

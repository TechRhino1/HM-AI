"""Volatility-targeted position sizing (P1-1).

Guards two defects found in the 2026-09-15 audit:

1. The volatility adjustment never fired. No caller passed ``atr_ratio``, so it
   defaulted to 1.0, and the only rule keyed off it (``atr_ratio > 1.5 -> *0.85``)
   was dead code in production.
2. The fractional-Kelly term was a constant, not information. Quarter-Kelly
   saturates at its 1.50 cap for every plausible (p, R) — (0.60, 2.0) already
   yields 10.0 — so it added a flat +0.75pp to every risk budget.
"""
import unittest

from jarvis.risk.position_sizing import PositionSizer
from jarvis.data.symbol_registry import get_dollar_risk_per_price_unit

SYM = {
    "name": "EURUSD",
    "trade_contract_size": 100000.0,
    "trade_tick_value": 1.0,
    "trade_tick_size": 0.00001,
    "volume_min": 0.01,
    "volume_max": 100.0,
    "volume_step": 0.01,
}

BALANCE = 10000.0
ENTRY = 1.1000
SL = 1.0950  # 50 pips


class TestVolatilityTargetedSizing(unittest.TestCase):
    def _lots(self, atr_ratio, **over):
        kw = dict(
            account_balance=BALANCE,
            entry_price=ENTRY,
            sl_price=SL,
            risk_pct=0.5,
            symbol_info=SYM,
            model_confidence=0.60,
            atr_ratio=atr_ratio,
        )
        kw.update(over)
        return PositionSizer.calculate_lot_size(**kw)

    def test_higher_volatility_produces_a_materially_smaller_position(self):
        """Realised vol must size the trade down, not just clip it above a cut-off."""
        calm = self._lots(atr_ratio=0.5)
        hot = self._lots(atr_ratio=2.5)
        self.assertGreater(calm, 0.0)
        self.assertLess(hot, calm)
        # The old step rule only shaved 15% above an arbitrary 1.5 threshold.
        # Real volatility targeting has to cut far harder than that.
        self.assertLess(hot / calm, 0.60,
                        "volatility targeting is too weak to be doing anything")

    def test_volatility_scalar_stays_bounded(self):
        """Extreme ATR ratios must not zero the trade or blow it up."""
        flat = self._lots(atr_ratio=1.0)
        self.assertGreater(flat, 0.0)
        for extreme in (0.01, 50.0):
            lots = self._lots(atr_ratio=extreme)
            self.assertGreater(lots, 0.0)
            self.assertLess(lots, flat * 4.0)

    def test_kelly_no_longer_adds_a_constant_to_the_risk_budget(self):
        """Sizing must equal the volatility-scaled base, with no Kelly uplift."""
        lots = self._lots(atr_ratio=1.0)
        per_unit = get_dollar_risk_per_price_unit("EURUSD", SYM)
        expected = (BALANCE * 0.005) / (abs(ENTRY - SL) * per_unit)
        self.assertAlmostEqual(lots, expected, places=2,
                               msg="risk budget still carries the constant Kelly uplift")


if __name__ == "__main__":
    unittest.main()

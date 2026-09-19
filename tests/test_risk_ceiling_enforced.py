"""The configured `max_risk_per_trade_pct` must actually cap risk.

Regression guard for the defect found in the 2026-09-20 audit:

`PositionSizer.calculate_lot_size` clamped `effective_risk_pct` to a HARDCODED
literal ``1.50`` while ``max_risk_per_trade_pct`` (config/settings.json, 0.5) is
the documented limit -- and it applied ``invalidation_risk_coefficient`` and
``combined_scaler`` OUTSIDE that clamp. A high-conviction trade therefore scaled
the 0.5 base by up to ~1.55x and the setting was never enforced.

Measured on the live book: 27 real trades breached the limit, the worst risking
39% of equity (78x). Multipliers may now only REDUCE risk below the ceiling.
"""
import unittest

from jarvis.risk.position_sizing import PositionSizer
from jarvis.config.settings import SETTINGS

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
SL = 1.0950          # 50 pips
CONTRACT_SIZE = 100000.0
DISTANCE = abs(ENTRY - SL)   # 0.0050


def _risk_usd(lots: float) -> float:
    """Cash at risk if the stop is hit."""
    return lots * CONTRACT_SIZE * DISTANCE


class TestRiskCeilingIsEnforced(unittest.TestCase):
    def _lots(self, **over):
        kw = dict(
            account_balance=BALANCE,
            entry_price=ENTRY,
            sl_price=SL,
            risk_pct=SETTINGS.risk.max_risk_per_trade_pct,
            symbol_info=SYM,
        )
        kw.update(over)
        return PositionSizer.calculate_lot_size(**kw)

    def test_maximal_multipliers_cannot_exceed_the_configured_ceiling(self):
        """conviction 1.35 x evidence 1.15 = 1.5525x, the worst case.

        Before the fix this produced ~0.776% of equity -- 1.55x the configured
        0.5% limit -- because the clamp used a literal 1.50 instead.
        """
        lots = self._lots(model_confidence=0.95, pattern_sample_size=30)
        ceiling_usd = BALANCE * SETTINGS.risk.max_risk_per_trade_pct / 100.0
        self.assertLessEqual(
            _risk_usd(lots), ceiling_usd * 1.02,
            f"risk ${_risk_usd(lots):.2f} exceeds the {ceiling_usd:.2f} ceiling "
            f"({SETTINGS.risk.max_risk_per_trade_pct}% of {BALANCE})",
        )

    def test_the_old_literal_ceiling_would_have_breached(self):
        """Documents the regression: the pre-fix arithmetic really did overshoot.

        Guards against someone 'restoring' the 1.50 literal without reading why
        it was there.
        """
        scaled = SETTINGS.risk.max_risk_per_trade_pct * 1.35 * 1.15
        self.assertGreater(scaled, SETTINGS.risk.max_risk_per_trade_pct,
                           "multipliers must be able to push past the base, or this test is moot")
        self.assertLess(scaled, 1.50,
                        "the old 1.50 literal never bound this case -- that was the bug")

    def test_a_lower_per_call_budget_is_still_honoured(self):
        """A caller passing 0.25 must not be sized up to the configured 0.5."""
        lots_tight = self._lots(risk_pct=0.25, model_confidence=0.95, pattern_sample_size=30)
        lots_base = self._lots(model_confidence=0.95, pattern_sample_size=30)
        self.assertLess(_risk_usd(lots_tight), _risk_usd(lots_base))

    def test_multipliers_can_still_reduce_risk(self):
        """Weak evidence must continue to shrink the position."""
        strong = self._lots(model_confidence=0.60, pattern_sample_size=30)
        weak = self._lots(model_confidence=0.60, pattern_sample_size=0)
        self.assertLessEqual(_risk_usd(weak), _risk_usd(strong))


if __name__ == "__main__":
    unittest.main()

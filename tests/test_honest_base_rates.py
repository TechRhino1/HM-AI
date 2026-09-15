"""Tests for jarvis.intelligence.honest_base_rates.

The gate probability is 100% hand-authored, and refitting it on real MT5 data
produced 0/20 skillful symbols. These tests protect the *measured* counterpart:
it must report what actually happened, never invent a flattering number, and
never be able to take the trading loop down.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis.intelligence.honest_base_rates import (
    HonestBaseRate,
    _compute_median,
    _load,
    _normalise_symbol,
    describe_gap,
    get_base_rate,
)

REPORT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "reports",
    "trade_quality_audit_365d.json",
)


@unittest.skipUnless(os.path.exists(REPORT), "365d MT5 audit report not present")
class TestHonestBaseRates(unittest.TestCase):
    def test_measured_rate_matches_the_report(self):
        """A known symbol must report exactly what the MT5 audit measured."""
        data = _load(REPORT)
        expected = data["styles"]["SWING(H1)"]["XAUUSD"]["tp1.5_costs"]
        rate = get_base_rate("XAUUSD", "SWING", path=REPORT)

        self.assertIsNotNone(rate)
        self.assertFalse(rate.proxy, "a symbol present in the report must not be a proxy")
        self.assertEqual(rate.n, expected["n"])
        self.assertAlmostEqual(rate.win_rate, expected["win_rate"], places=9)
        self.assertAlmostEqual(rate.expectancy_r, expected["expectancy_r"], places=9)
        self.assertEqual(rate.window_days, 365)

    def test_broker_decoration_is_stripped(self):
        """Runtime symbols arrive as 'EURUSD#' / 'GOLD.i#' / 'US30.cash#'."""
        self.assertEqual(_normalise_symbol("EURUSD#"), "EURUSD")
        self.assertEqual(_normalise_symbol("GOLD.i#"), "GOLD")
        self.assertEqual(_normalise_symbol("US30.cash#"), "US30")
        # GOLD is an alias for XAUUSD, so the broker ticket must resolve to it.
        rate = get_base_rate("GOLD.i#", "SWING", path=REPORT)
        self.assertEqual(rate.symbol, "XAUUSD")
        self.assertFalse(rate.proxy)

    def test_unknown_symbol_returns_a_flagged_proxy_not_a_flattering_number(self):
        """The failure this guards against is silently inventing a usable-looking
        win rate. An unmeasured symbol must fall back to the universe *median* and
        say so -- a caller must never mistake a borrowed rate for a measured one."""
        rate = get_base_rate("NOTAREALSYMBOL#", "SWING", path=REPORT)
        self.assertIsNotNone(rate)
        self.assertTrue(rate.proxy, "unknown symbol must be flagged as a proxy")
        self.assertEqual(rate.symbol, "UNIVERSE_MEDIAN")

        median = _compute_median(_load(REPORT))
        self.assertAlmostEqual(rate.win_rate, median.win_rate, places=9)
        # Measured win rates are ~0.33-0.44 against a 40% break-even. A hardcoded
        # fallback of 0.5 (or anything above break-even) would pass a loose test,
        # so pin it below.
        self.assertLess(rate.win_rate, 0.50)
        self.assertGreater(rate.win_rate, 0.0)

    def test_proxy_marks_the_source(self):
        rate = get_base_rate("NOTAREALSYMBOL#", "SWING", path=REPORT)
        self.assertIn("median", rate.source)

    def test_style_maps_to_its_report_key(self):
        """SWING is H1. A mix-up here would silently compare M5 to H1 numbers."""
        self.assertEqual(get_base_rate("EURUSD", "SWING", path=REPORT).style, "SWING(H1)")

    def test_unmeasured_style_falls_back_to_proxy_never_fabricates(self):
        """Only H1 was re-scanned over the full 365d window; M15/M5 re-scan is
        still pending (P1-3). Asking for DAY_TRADING must therefore yield the
        flagged universe median -- NOT an M15 number borrowed from the stale
        pre-resample-fix scan, which is exactly the kind of plausible-looking
        wrong number this module exists to prevent."""
        rate = get_base_rate("EURUSD", "DAY_TRADING", path=REPORT)
        self.assertTrue(rate.proxy, "an unmeasured style must be flagged as a proxy")
        self.assertEqual(rate.symbol, "UNIVERSE_MEDIAN")
        self.assertNotEqual(rate.style, "DAY_TRADING(M15)")

    def test_never_raises_on_garbage_input(self):
        """Observability must not be able to take the trading loop down."""
        for bad in [None, "", 0, 12.5, object(), "###", "." * 500]:
            try:
                get_base_rate(bad, "SWING", path=REPORT)
            except Exception as exc:  # noqa: BLE001
                self.fail(f"get_base_rate raised on {bad!r}: {exc}")

        try:
            get_base_rate("EURUSD", "SWING", path="/nonexistent/report.json")
            describe_gap(0.5, None)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"raised on missing report: {exc}")

    def test_describe_gap_quantifies_the_overstatement(self):
        """The whole point: show how far the gate's claim sits above measurement."""
        measured = HonestBaseRate(
            symbol="EURUSD",
            style="SWING(H1)",
            window_days=365,
            n=4419,
            win_rate=0.3906,
            profit_factor=0.939,
            expectancy_r=-0.0374,
            break_even_wr=0.40,
        )
        text = describe_gap(0.72, measured, tp=1.5)
        # Claimed: 0.72*1.5 - 0.28*1 = 0.800R. Measured -0.0374R. Gap +0.837R.
        self.assertIn("+0.837", text)
        self.assertIn("MEASURED", text)
        # A proxy must be labelled as such, not presented as measurement.
        proxy = HonestBaseRate(
            symbol="UNIVERSE_MEDIAN", style="ALL", window_days=365, n=4549,
            win_rate=0.393, profit_factor=0.951, expectancy_r=-0.0301,
            break_even_wr=0.40, proxy=True,
        )
        self.assertIn("PROXY", describe_gap(0.72, proxy, tp=1.5))

    def test_describe_gap_handles_a_missing_measurement(self):
        self.assertIn("no measured base rate", describe_gap(0.72, None))


if __name__ == "__main__":
    unittest.main()

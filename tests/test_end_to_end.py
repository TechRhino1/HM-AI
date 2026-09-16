import unittest
from unittest.mock import patch
from jarvis.application.orchestrator import JarvisOrchestrator
from jarvis.data.schemas import DecisionObject

class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        # See tests/test_multi_style_radar.py: this asserts the end-to-end cycle,
        # not feed freshness, but the orchestrator reads live MT5 bars. Without
        # neutralising the stale-feed gate the outcome depends on the wall clock
        # and the broker connection. Only that gate is patched.
        self._freshness = patch(
            "jarvis.market.data_feed.first_untrusted_frame",
            return_value=(None, None, 0.0),
        )
        self._freshness.start()
        self.addCleanup(self._freshness.stop)
        self.orchestrator = JarvisOrchestrator(mode="paper")

    def tearDown(self):
        self.orchestrator.stop()

    def test_end_to_end_cycle(self):
        res = self.orchestrator.run_cycle_for_symbol("XAUUSD")
        self.assertIn("symbol", res)
        self.assertEqual(res["symbol"], "XAUUSD")
        
        decision = res["decision"]
        self.assertIsInstance(decision, DecisionObject)
        self.assertIn(decision.decision, ["EXECUTE", "WAIT", "NO_TRADE", "REJECT"])
        self.assertIsNotNone(decision.expected_value)
        self.assertIsNotNone(decision.quality_gate)
        self.assertTrue(len(decision.invalidation_levels) > 0)

if __name__ == "__main__":
    unittest.main()

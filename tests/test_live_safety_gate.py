"""
Unit test for Execution Mode Resolution.
Asserts that mode=="live" runs live execution directly, and mode=="paper" is available for testing.
"""
import unittest
from jarvis.config.settings import verify_execution_mode
from jarvis.application.orchestrator import JarvisOrchestrator


class TestLiveSafetyGate(unittest.TestCase):
    def test_verify_execution_mode_default_live(self):
        """Default mode or 'live' resolves directly to 'live'."""
        self.assertEqual(verify_execution_mode("live"), "live")
        self.assertEqual(verify_execution_mode(None), "live")
        self.assertEqual(verify_execution_mode(""), "live")

    def test_verify_execution_mode_paper_for_testing(self):
        """'paper' mode can be explicitly passed for testing and simulation."""
        self.assertEqual(verify_execution_mode("paper"), "paper")
        self.assertEqual(verify_execution_mode("backtest"), "backtest")
        self.assertEqual(verify_execution_mode("simulated"), "simulated")
        self.assertEqual(verify_execution_mode("demo"), "demo")

    def test_orchestrator_initialization_live(self):
        """JarvisOrchestrator initialized with mode='live' defaults to live mode."""
        orchestrator = JarvisOrchestrator(mode="live")
        self.assertEqual(orchestrator.mode, "live")

    def test_orchestrator_initialization_paper(self):
        """JarvisOrchestrator initialized with mode='paper' remains in paper mode for testing."""
        orchestrator = JarvisOrchestrator(mode="paper")
        self.assertEqual(orchestrator.mode, "paper")


if __name__ == "__main__":
    unittest.main()


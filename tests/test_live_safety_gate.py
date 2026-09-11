"""
Unit test for Execution Mode Safety Gate (Task 1d).
Asserts that mode=="live" without JARVIS_CONFIRM_LIVE=1 falls back to paper mode.
"""
import os
import unittest
from jarvis.config.settings import verify_execution_mode
from jarvis.application.orchestrator import JarvisOrchestrator


class TestLiveSafetyGate(unittest.TestCase):
    def setUp(self):
        # Save original env
        self.orig_confirm = os.environ.get("JARVIS_CONFIRM_LIVE")
        if "JARVIS_CONFIRM_LIVE" in os.environ:
            del os.environ["JARVIS_CONFIRM_LIVE"]

    def tearDown(self):
        if self.orig_confirm is not None:
            os.environ["JARVIS_CONFIRM_LIVE"] = self.orig_confirm
        elif "JARVIS_CONFIRM_LIVE" in os.environ:
            del os.environ["JARVIS_CONFIRM_LIVE"]

    def test_verify_execution_mode_unconfirmed_live_fallback(self):
        """Unconfirmed 'live' mode must fall back to 'paper'."""
        if "JARVIS_CONFIRM_LIVE" in os.environ:
            del os.environ["JARVIS_CONFIRM_LIVE"]
        resolved = verify_execution_mode("live")
        self.assertEqual(resolved, "paper")

    def test_verify_execution_mode_confirmed_live(self):
        """Confirmed 'live' mode via JARVIS_CONFIRM_LIVE=1 resolves to 'live'."""
        os.environ["JARVIS_CONFIRM_LIVE"] = "1"
        resolved = verify_execution_mode("live")
        self.assertEqual(resolved, "live")

    def test_orchestrator_initialization_unconfirmed_live_fallback(self):
        """JarvisOrchestrator initialized with mode='live' falls back to 'paper' when unconfirmed."""
        if "JARVIS_CONFIRM_LIVE" in os.environ:
            del os.environ["JARVIS_CONFIRM_LIVE"]
        orchestrator = JarvisOrchestrator(mode="live")
        self.assertEqual(orchestrator.mode, "paper")


if __name__ == "__main__":
    unittest.main()

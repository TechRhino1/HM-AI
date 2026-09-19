"""The configured symbol universe must actually govern what gets scanned.

Regression guard for the defect found in the 2026-09-20 audit:
``trading.allowed_symbols`` in config/settings.json (and ``JARVIS_SYMBOLS``) was
parsed into ``SETTINGS.trading.symbols`` by ``settings.py:123-124`` / ``:159``,
but **nothing ever read it**. ``JarvisOrchestrator.__init__`` hardcoded 13
symbols, so an operator narrowing the universe in config changed nothing and all
13 kept being scanned and traded.
"""
import unittest

from jarvis.application.orchestrator import JarvisOrchestrator
from jarvis.config.settings import SETTINGS


class TestSymbolUniverseIsConfigDriven(unittest.TestCase):
    def setUp(self):
        # Copy, not a reference: if anything mutates the list in place, restoring
        # a reference would restore the mutated object and leak into later tests.
        self._orig = list(SETTINGS.trading.symbols)

    def tearDown(self):
        SETTINGS.trading.symbols = self._orig

    def test_default_follows_config_not_the_hardcoded_list(self):
        SETTINGS.trading.symbols = ["EURUSD", "XAUUSD"]
        orch = JarvisOrchestrator(mode="paper")
        self.assertEqual(
            orch.symbols, ["EURUSD", "XAUUSD"],
            "the 13-symbol hardcoded list must not win over config",
        )

    def test_config_change_is_reflected(self):
        SETTINGS.trading.symbols = ["GBPUSD"]
        orch = JarvisOrchestrator(mode="paper")
        self.assertEqual(orch.symbols, ["GBPUSD"])

    def test_explicit_argument_still_wins(self):
        SETTINGS.trading.symbols = ["EURUSD", "XAUUSD"]
        orch = JarvisOrchestrator(symbols=["USDJPY"], mode="paper")
        self.assertEqual(orch.symbols, ["USDJPY"])

    def test_empty_config_falls_back_to_the_builtin_universe(self):
        SETTINGS.trading.symbols = []
        orch = JarvisOrchestrator(mode="paper")
        self.assertGreater(len(orch.symbols), 0,
                           "an empty config must not silently trade nothing")


if __name__ == "__main__":
    unittest.main()

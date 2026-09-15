"""
Guards against the "hash()-seeded, module-level random.seed()" defect class.

Two properties are pinned here, and they are the two the old code violated:

1. No module under ``jarvis/india/`` or ``jarvis/stocks/`` may call
   ``random.seed(...)``. Seeding the module-level generator inside a request
   handler reseeds it for the whole process, so every other caller of ``random``
   (e.g. an options engine's IV rank) depends on which symbol happened to be
   queried last. Such code must seed a local ``random.Random(...)`` instead.

2. A "stable" seed must be identical in every process. ``hash()`` is salted per
   process (PYTHONHASHSEED), so a hash-derived seed yields a different synthetic
   series on every interpreter start. ``stable_seed`` is the single definition of
   the alternative.

The cross-process test below is the one that actually discriminates: run under
two different PYTHONHASHSEED values, the old code produced two different option
chains while the fixed code produces one.
"""
import ast
import json
import os
import re
import random
import subprocess
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

INDIA_DIR = os.path.join(REPO_ROOT, "jarvis", "india")
STOCKS_DIR = os.path.join(REPO_ROOT, "jarvis", "stocks")

# Matches a bare `random.seed(` but not `np.random.seed(`.
_MODULE_RESEED = re.compile(r"(?<!\.)random\.seed\(")


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _py_files(directory):
    for name in sorted(os.listdir(directory)):
        if name.endswith(".py"):
            yield os.path.join(directory, name)


# --------------------------------------------------------------------------- #
# Offline harnesses. The engines normally fetch a live profile / option chain;
# stubbing those keeps the RNG behaviour under test without touching the
# network, and does not change the code path the RNG lives on.
# --------------------------------------------------------------------------- #

_PROFILE = {"name": "NIFTY", "base_price": 24000.0, "implied_volatility": 16.5, "beta": 1.0}

_ANALYSIS = {
    "current_price": 24000.0,
    "is_index": True,
    "cpr": {"width_classification": "NARROW_CPR"},
    "camarilla": {"h4_breakout": 24200.0, "l4_breakdown": 23800.0},
    "vwap_structure": {"vwap": 23950.0},
    "breakout_probability": 70.0,
    "rvol": 1.5,
    "is_squeeze": False,
    "data_source": "calibrated_feed",
}


def _generate_chain_offline(symbol="NIFTY"):
    import jarvis.india.options_engine as oe

    with mock.patch.object(oe, "get_india_profile", return_value=dict(_PROFILE)), \
            mock.patch("jarvis.india.nse_bse_adapter.fetch_nse_option_chain", return_value=None):
        return oe.INDIA_OPTIONS.generate_option_chain(symbol)


def _evaluate_signal_offline(symbol="NIFTY"):
    import jarvis.india.options_signal_engine as ose

    with mock.patch.object(ose, "get_india_profile", return_value=dict(_PROFILE)), \
            mock.patch.object(ose.INDIA_ENGINE, "analyze_india_instrument", return_value=dict(_ANALYSIS)), \
            mock.patch.object(ose.INDIA_OPTIONS, "generate_option_chain", return_value={}):
        return ose.OPTION_SIGNALS._evaluate_instrument_for_option_buy(symbol, 0.6)


class TestNoModuleLevelReseeding(unittest.TestCase):
    """Source-level: the defect must not be reintroduced anywhere in the engines."""

    def test_random_seed_appears_nowhere_under_india_or_stocks(self):
        offenders = {}
        for directory in (INDIA_DIR, STOCKS_DIR):
            for path in _py_files(directory):
                hits = [
                    i + 1
                    for i, line in enumerate(_read(path).splitlines())
                    if _MODULE_RESEED.search(line)
                ]
                if hits:
                    offenders[os.path.relpath(path, REPO_ROOT)] = hits
        self.assertEqual(
            offenders, {},
            "random.seed() reseeds the module-level RNG for the whole process; "
            "seed a local random.Random() instead",
        )

    def test_options_modules_seed_a_local_rng(self):
        for rel in ("jarvis/india/options_engine.py",
                    "jarvis/india/options_signal_engine.py"):
            src = _read(os.path.join(REPO_ROOT, rel))
            self.assertIn("random.Random(", src, f"{rel} should seed a local RNG")

    def test_stable_seed_helper_is_the_single_definition(self):
        from jarvis.india.news_analyzer import stable_seed

        self.assertEqual(
            stable_seed("RELIANCE"),
            sum((i + 1) * ord(c) for i, c in enumerate("RELIANCE")) % 100000,
        )
        # The helper must not actually *call* hash() — that is the whole point.
        # (The docstring mentions hash() by name, so parse rather than grep.)
        tree = ast.parse(_read(os.path.join(INDIA_DIR, "news_analyzer.py")))
        func = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "stable_seed"
        )
        called = {
            n.func.id for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        self.assertNotIn("hash", called, "stable_seed must not call hash()")


class TestEnginesDoNotDisturbGlobalRandom(unittest.TestCase):
    """Runtime: a call into the previously-leaking code must not consume from
    the module-level generator."""

    def _assert_untouched(self, call):
        random.seed(12345)
        before = random.random()
        random.seed(12345)
        call()
        after = random.random()
        self.assertEqual(
            before, after,
            "the call consumed from the module-level RNG, which leaks into every "
            "other caller of random in the process",
        )

    def test_option_chain_does_not_disturb_global_random(self):
        self._assert_untouched(lambda: _generate_chain_offline("NIFTY"))

    def test_option_signal_does_not_disturb_global_random(self):
        self._assert_untouched(lambda: _evaluate_signal_offline("NIFTY"))


class TestSameSymbolIsStable(unittest.TestCase):
    """Runtime: the same symbol yields the same modelled values."""

    def test_option_chain_is_identical_for_same_symbol(self):
        first = _generate_chain_offline("NIFTY")
        second = _generate_chain_offline("NIFTY")
        self.assertEqual(first["iv_rank"], second["iv_rank"])
        self.assertEqual(
            [r["call"]["oi"] for r in first["chain"]],
            [r["call"]["oi"] for r in second["chain"]],
        )

    def test_signal_is_identical_for_same_symbol(self):
        first = _evaluate_signal_offline("NIFTY")
        second = _evaluate_signal_offline("NIFTY")
        self.assertEqual(first["pcr"], second["pcr"])
        self.assertEqual(first["iv_rank"], second["iv_rank"])

    def test_signal_declares_provenance(self):
        signal = _evaluate_signal_offline("NIFTY")
        self.assertIn("data_source", signal)
        self.assertEqual(signal["data_source"], "calibrated_feed")


# --------------------------------------------------------------------------- #
# Cross-process determinism. This is the test that fails on the old code.
# --------------------------------------------------------------------------- #

_PROBE = r'''
import json, sys
sys.path.insert(0, {root!r})
from unittest import mock

import jarvis.india.options_engine as oe
import jarvis.india.options_signal_engine as ose

prof = {profile!r}
with mock.patch.object(oe, "get_india_profile", return_value=prof), \
        mock.patch("jarvis.india.nse_bse_adapter.fetch_nse_option_chain", return_value=None):
    chain = oe.INDIA_OPTIONS.generate_option_chain("NIFTY")

with mock.patch.object(ose, "get_india_profile", return_value=prof), \
        mock.patch.object(ose.INDIA_ENGINE, "analyze_india_instrument", return_value={analysis!r}), \
        mock.patch.object(ose.INDIA_OPTIONS, "generate_option_chain", return_value={{}}):
    sig = ose.OPTION_SIGNALS._evaluate_instrument_for_option_buy("NIFTY", 0.6)

print(json.dumps({{
    "iv_rank": chain["iv_rank"],
    "ce_oi": [r["call"]["oi"] for r in chain["chain"]],
    "pcr": sig["pcr"],
    "sig_iv_rank": sig["iv_rank"],
}}))
'''


class TestStableAcrossProcesses(unittest.TestCase):
    def _fingerprint(self, hash_seed):
        env = dict(os.environ, PYTHONHASHSEED=str(hash_seed))
        script = _PROBE.format(root=REPO_ROOT, profile=_PROFILE, analysis=_ANALYSIS)
        out = subprocess.check_output(
            [sys.executable, "-c", script], env=env, cwd=REPO_ROOT,
        )
        return out.decode("utf-8").strip()

    def test_output_is_identical_under_different_hash_seeds(self):
        first = self._fingerprint(1)
        second = self._fingerprint(2)
        self.assertEqual(
            json.loads(first), json.loads(second),
            "the modelled option values changed with PYTHONHASHSEED, i.e. the "
            "seed still derives from hash() and is not stable across processes",
        )


if __name__ == "__main__":
    unittest.main()

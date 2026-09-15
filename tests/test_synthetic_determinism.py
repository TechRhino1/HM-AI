"""
Guards against the "hash()-seeded, module-level random.seed()" defect class.

Two properties are pinned here, and they are the two the old code violated:

1. No engine may reseed the module-level ``random`` generator. Seeding it inside
   a request handler reseeds it for the whole process, so every other caller of
   ``random`` (e.g. an options engine's IV rank) depends on which symbol happened
   to be queried last. Such code must seed a local ``random.Random(...)``.

2. A "stable" seed must be identical in every process. ``hash()`` is salted per
   process (PYTHONHASHSEED), so a hash-derived seed yields a different value on
   every interpreter start — for synthetic series, but also for user-facing
   fields like a symbol's earnings date, implied volatility and F&O ban status.
   ``stable_seed`` (jarvis/data/determinism.py) is the single definition of the
   replacement.

The cross-process tests are the ones that actually discriminate: run under two
different PYTHONHASHSEED values, the old code produced different values while the
fixed code produces one. In-process repeats do NOT discriminate (hash() is salted
per process, not per call), which is why they are not relied on here.
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

# Files that must not seed anything from hash(). Deliberately excludes
# jarvis/data/tradingview_provider.py and jarvis/data/market_data_provider.py,
# which are being fixed separately. jarvis/data/determinism.py is the helper
# itself and is covered by test_helper_is_a_plain_function_of_the_text.
HASH_FREE_FILES = [
    "jarvis/data/dynamic_hydrator.py",
    "jarvis/india/universe.py",
    "jarvis/stocks/universe.py",
    "jarvis/india/news_analyzer.py",
    "jarvis/india/india_engine.py",
    "jarvis/india/options_engine.py",
    "jarvis/india/options_signal_engine.py",
    "jarvis/stocks/stock_engine.py",
]


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _py_files(directory):
    for name in sorted(os.listdir(directory)):
        if name.endswith(".py"):
            yield os.path.join(directory, name)


def _hash_call_lines(path):
    """Line numbers of real ``hash(...)`` calls, ignoring comments/strings."""
    tree = ast.parse(_read(path))
    return [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "hash"
    ]


# --------------------------------------------------------------------------- #
# Offline harnesses. The engines normally fetch a live profile / option chain;
# stubbing those keeps the behaviour under test without touching the network,
# and does not change the code path under test.
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

    def test_hash_is_not_used_to_seed_anything(self):
        offenders = {}
        for rel in HASH_FREE_FILES:
            hits = _hash_call_lines(os.path.join(REPO_ROOT, rel))
            if hits:
                offenders[rel] = hits
        self.assertEqual(
            offenders, {},
            "hash() is salted per process, so a hash-derived seed is not stable "
            "across restarts; use stable_seed()",
        )


class TestStableSeedHelper(unittest.TestCase):
    def test_helper_has_exactly_one_definition(self):
        definitions = []
        for root, _dirs, files in os.walk(os.path.join(REPO_ROOT, "jarvis")):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                for node in ast.walk(ast.parse(_read(path))):
                    if isinstance(node, ast.FunctionDef) and node.name == "stable_seed":
                        definitions.append(os.path.relpath(path, REPO_ROOT))
        self.assertEqual(
            definitions, [os.path.join("jarvis", "data", "determinism.py")],
            "stable_seed must have exactly one definition",
        )

    def test_helper_is_a_plain_function_of_the_text(self):
        from jarvis.data.determinism import stable_seed

        self.assertEqual(
            stable_seed("RELIANCE"),
            sum((i + 1) * ord(c) for i, c in enumerate("RELIANCE")) % 100000,
        )
        # It must not actually *call* hash() — that is the whole point.
        # (Its docstring mentions hash() by name, so parse rather than grep.)
        path = os.path.join(REPO_ROOT, "jarvis", "data", "determinism.py")
        tree = ast.parse(_read(path))
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
    """Runtime: the same symbol yields the same modelled values.

    Note: these do NOT discriminate against the old code on their own — hash()
    is salted per process but constant within one, so the old code was already
    stable within a process. The cross-process tests below are the discriminators.
    """

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
# Cross-process determinism. These are the tests that fail on the old code.
# --------------------------------------------------------------------------- #

_OPTION_PROBE = r'''
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

# Forcing the hydrator to raise drives get_india_profile / get_stock_profile into
# the fallback branch, which is where the earnings date / IV / MWPL fields are
# synthesised. That branch is the code under test.
_PROFILE_PROBE = r'''
import json, sys
sys.path.insert(0, {root!r})

import jarvis.data.dynamic_hydrator as dh
dh.DYNAMIC_HYDRATOR.get_profile = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline"))

from jarvis.india.universe import get_india_profile
from jarvis.stocks.universe import get_stock_profile

IN_KEYS = ["earnings_date", "days_to_earnings", "implied_volatility",
           "asm_stage", "mwpl_utilization_pct", "is_fno_ban"]
US_KEYS = ["earnings_date", "days_to_earnings", "implied_volatility"]

india = get_india_profile("RELIANCE")
us = get_stock_profile("NVDA")
fb_india = dh.DYNAMIC_HYDRATOR._build_dynamic_fallback_profile("RELIANCE", "IN")
fb_us = dh.DYNAMIC_HYDRATOR._build_dynamic_fallback_profile("NVDA", "US")

print(json.dumps({{
    "india": {{k: india.get(k) for k in IN_KEYS}},
    "us": {{k: us.get(k) for k in US_KEYS}},
    "fb_india": {{k: fb_india.get(k) for k in IN_KEYS}},
    "fb_us": {{k: fb_us.get(k) for k in US_KEYS}},
}}, default=str))
'''


class TestStableAcrossProcesses(unittest.TestCase):
    def _fingerprint(self, script, hash_seed):
        env = dict(os.environ, PYTHONHASHSEED=str(hash_seed))
        out = subprocess.check_output(
            [sys.executable, "-c", script], env=env, cwd=REPO_ROOT,
        )
        return out.decode("utf-8").strip()

    def test_option_output_is_identical_under_different_hash_seeds(self):
        script = _OPTION_PROBE.format(root=REPO_ROOT, profile=_PROFILE, analysis=_ANALYSIS)
        first = self._fingerprint(script, 1)
        second = self._fingerprint(script, 2)
        self.assertEqual(
            json.loads(first), json.loads(second),
            "the modelled option values changed with PYTHONHASHSEED, i.e. the "
            "seed still derives from hash() and is not stable across processes",
        )

    def test_profile_fields_are_identical_under_different_hash_seeds(self):
        script = _PROFILE_PROBE.format(root=REPO_ROOT)
        first = self._fingerprint(script, 1)
        second = self._fingerprint(script, 2)
        self.assertEqual(
            json.loads(first), json.loads(second),
            "a symbol's earnings date / implied volatility / MWPL status changed "
            "with PYTHONHASHSEED, i.e. it is still seeded from hash()",
        )


if __name__ == "__main__":
    unittest.main()

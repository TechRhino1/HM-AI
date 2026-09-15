"""Generated candle history must be identical across processes.

Both synthetic candle generators seeded their RNG from ``hash()`` of a composite
key (``f"{symbol}_{timeframe}_{hour}"``). CPython salts ``hash()`` per process,
so the same symbol and timeframe produced a *different* series after every
server restart.

That matters beyond reproducibility: the dashboard chart derives its
support/resistance levels from these candles — swing pivots above and below the
last close — so a restart silently moved every level on screen while the panel
still claimed to be showing the same instrument's structure.

``jarvis.data.determinism.stable_seed`` replaces it: one definition, a plain
function of the text, identical in every process.

WHY THE TEST SPAWNS SUBPROCESSES. Salting is per process but *constant within*
one, so two calls in the same interpreter agree even with the bug present — an
in-process test passes against the broken code and pins nothing. The
discriminating test has to vary ``PYTHONHASHSEED``, which means a fresh
interpreter.

Both generators are exercised in one subprocess so the cost is two interpreter
starts rather than four. The network is stubbed out; the seed derivation is what
is under test, not the fetch.
"""
import json
import os
import subprocess
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DRIVER = r"""
import json, sys
sys.path.insert(0, REPO_ROOT)

# The calibrated-baseline generator reads a profile for its anchor price, and
# that path hydrates (which reaches the network). Anchor it statically.
import jarvis.stocks.universe as _su
_su.get_stock_profile = lambda sym: {"base_price": 150.0, "beta": 1.2, "name": sym}

from jarvis.data.market_data_provider import get_calibrated_baseline_candles
baseline = get_calibrated_baseline_candles("AAPL", "1D", 40, market="US")

# The TradingView generator anchors on a live quote first. Stub the quote so the
# candle construction runs offline.
import jarvis.data.tradingview_provider as _tvp
_tvp.TRADINGVIEW_PROVIDER.fetch_quotes = lambda symbols: {
    s: {
        "price": 2400.0, "open": 2390.0, "high": 2410.0, "low": 2380.0,
        "close": 2400.0, "change_pct": 0.4, "volume": 1000,
    }
    for s in symbols
}
tv = _tvp.TRADINGVIEW_PROVIDER.fetch_candles("XAUUSD", "H1", 40)

print(json.dumps({
    "baseline": [c["close"] for c in baseline],
    "tv": [c["close"] for c in (tv or [])],
}))
""".replace("REPO_ROOT", repr(REPO_ROOT))


def _series_with_hash_seed(seed):
    """Run the driver in a fresh interpreter under the given PYTHONHASHSEED."""
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = str(seed)
    proc = subprocess.run(
        [sys.executable, "-c", DRIVER],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"driver failed under PYTHONHASHSEED={seed}:\n{proc.stderr[-2000:]}"
        )
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TestCandleSeedsAreStableAcrossProcesses(unittest.TestCase):
    def test_source_does_not_seed_from_hash(self):
        """Source-level, and it names the two files so a regression in either is
        caught rather than only the one this test happens to exercise."""
        for relative in (
            os.path.join("jarvis", "data", "tradingview_provider.py"),
            os.path.join("jarvis", "data", "market_data_provider.py"),
        ):
            with open(os.path.join(REPO_ROOT, relative), encoding="utf-8") as fh:
                source = fh.read()
            # Skip comments and docstrings: both files carry a comment naming
            # `hash()` to explain why it is not used.
            code_lines = [
                line for line in source.splitlines()
                if not line.lstrip().startswith("#")
            ]
            offenders = [ln.strip() for ln in code_lines if "hash(" in ln]
            self.assertEqual(
                offenders,
                [],
                f"{relative} still seeds from hash(), which CPython salts per "
                f"process: {offenders}",
            )

    def test_both_generators_agree_across_hash_seeds(self):
        """The property that matters, measured in two fresh interpreters."""
        first = _series_with_hash_seed(0)
        second = _series_with_hash_seed(1)

        for key in ("baseline", "tv"):
            self.assertTrue(
                first[key],
                f"{key} produced no candles — the driver stubbed something wrong",
            )
            self.assertEqual(
                first[key],
                second[key],
                f"{key} candles differ between PYTHONHASHSEED=0 and =1, so the "
                f"series depends on the process rather than the symbol",
            )

    def test_the_generators_actually_produce_a_series(self):
        """Guard the guard: if the driver silently returned nothing, the
        comparison above would pass on two empty lists."""
        series = _series_with_hash_seed(0)
        self.assertGreater(len(series["baseline"]), 10)
        self.assertGreater(len(series["tv"]), 10)
        for key in ("baseline", "tv"):
            self.assertTrue(all(v > 0 for v in series[key]))


if __name__ == "__main__":
    unittest.main()

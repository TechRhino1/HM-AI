"""The quote provider must not re-enter the profile hydrator.

``TradingViewDataProvider.fetch_quotes()`` supplies a calibrated fallback quote
for any symbol its scanner could not resolve. That fallback used to read its
baseline price from ``get_india_profile()`` / ``get_stock_profile()`` — which
*hydrate*, and hydration resolves quotes by calling back into ``fetch_quotes()``
for the same symbol:

    fetch_quotes -> get_india_profile -> DYNAMIC_HYDRATOR.get_profile
      -> hydrate_batch -> fetch_quotes -> ...

Nothing raised, so the ``except`` clauses guarding those two calls never ran.
The stack simply grew while each level opened another blocking connection, and
``/api/india/*`` never answered for any symbol reaching that branch — every
index, since NIFTY and BANKNIFTY match no earlier pattern in the chain.

A stack dump of the blocked call is what identified this; the symptom alone
(a 240s timeout on three separate endpoints) reads like a slow provider.

A NOTE ON WHY THESE TESTS LOOK THE WAY THEY DO. The obvious test — "call
fetch_quotes and expect no RecursionError" — does NOT work, and was measured
failing to work: ``hydrate_batch`` wraps its ``fetch_quotes`` call in
``except Exception``, and ``RecursionError`` is an ``Exception``, so the deepest
frame is swallowed and each level above it unwinds normally. The bug is
invisible to a test that only looks for a raised exception. So the re-entry is
pinned by observing the *call itself* instead.
"""
import ast
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROVIDER = os.path.join(REPO_ROOT, "jarvis", "data", "tradingview_provider.py")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestQuoteFallbackDoesNotReenterTheHydrator(unittest.TestCase):
    """Every test here clears both caches first.

    This is not hygiene for its own sake. Measured while writing these tests:
    without the reset, the re-entry test passed against the *broken* code,
    because a sibling test had already populated ``_quote_cache`` for NIFTY and
    ``fetch_quotes`` returned from cache before reaching the fallback. The tests
    would have silently stopped pinning anything.
    """

    def setUp(self):
        from jarvis.data.dynamic_hydrator import DYNAMIC_HYDRATOR
        from jarvis.data.tradingview_provider import TRADINGVIEW_PROVIDER

        with TRADINGVIEW_PROVIDER._cache_lock:
            TRADINGVIEW_PROVIDER._quote_cache.clear()
            TRADINGVIEW_PROVIDER._quote_cache_time.clear()
        with DYNAMIC_HYDRATOR._cache_lock:
            DYNAMIC_HYDRATOR._profile_cache.clear()
            DYNAMIC_HYDRATOR._profile_cache_time.clear()

    def test_source_calls_the_static_tables_not_the_hydrating_getters(self):
        """Source-level, via the AST rather than a substring search.

        ``INDIA_UNIVERSE`` / ``STOCK_UNIVERSE`` are the static dictionaries;
        ``get_india_profile`` / ``get_stock_profile`` are the hydrating getters.
        Only the former may be *called*.

        Parsing instead of grepping matters here: the fix carries a comment
        explaining why the hydrating getters are avoided, and a substring check
        would flag that comment as a violation. Pin the call, not the word.
        """
        tree = ast.parse(_read(PROVIDER))

        called = set()
        referenced = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name):
                    called.add(fn.id)
                elif isinstance(fn, ast.Attribute):
                    called.add(fn.attr)
            elif isinstance(node, ast.Name):
                referenced.add(node.id)

        for forbidden in ("get_india_profile", "get_stock_profile"):
            self.assertNotIn(
                forbidden,
                called,
                f"the quote fallback must not call {forbidden}(): that "
                f"hydrates, and hydration calls back into fetch_quotes() for "
                f"the same symbol, which recurses without bound",
            )
        for table in ("INDIA_UNIVERSE", "STOCK_UNIVERSE"):
            self.assertIn(
                table,
                referenced,
                f"the fallback should read the static {table} table directly",
            )

    def test_fetch_quotes_never_reenters_the_hydrating_getter(self):
        """The interaction itself, which is what the fix is about.

        ``get_india_profile`` is replaced by a recorder that raises on entry.
        Any call proves re-entry; the recorder raises immediately so the test
        stays fast rather than descending until the recursion limit fires and
        is swallowed.

        NIFTY is the case that broke: it matches no prefix the earlier branches
        handle and is not a forex pair, so it reaches the fallback. Against the
        pre-fix code this test fails with ``['NIFTY']`` recorded.
        """
        import jarvis.india.universe as universe_mod
        from jarvis.data.tradingview_provider import TRADINGVIEW_PROVIDER

        reentered = []

        def recording_stub(symbol):
            reentered.append(symbol)
            raise RuntimeError(
                "get_india_profile re-entered from the quote fallback"
            )

        original_getter = universe_mod.get_india_profile
        original_post = TRADINGVIEW_PROVIDER._post_scanner_request
        universe_mod.get_india_profile = recording_stub
        TRADINGVIEW_PROVIDER._post_scanner_request = lambda endpoint, tickers: []
        try:
            TRADINGVIEW_PROVIDER.fetch_quotes(["NIFTY"])
        finally:
            universe_mod.get_india_profile = original_getter
            TRADINGVIEW_PROVIDER._post_scanner_request = original_post

        self.assertEqual(
            len(reentered),
            0,
            f"fetch_quotes re-entered the hydrating getter {len(reentered)} "
            f"times (first few: {reentered[:3]}); that is the cycle: hydration "
            f"resolves quotes by calling back into fetch_quotes, so the two "
            f"recurse without bound",
        )

    def test_an_unresolved_index_still_produces_a_quote(self):
        """The fallback must keep working — the fix changes where the number
        comes from, not whether there is one."""
        from jarvis.data.tradingview_provider import TRADINGVIEW_PROVIDER

        original = TRADINGVIEW_PROVIDER._post_scanner_request
        TRADINGVIEW_PROVIDER._post_scanner_request = lambda endpoint, tickers: []
        try:
            quotes = TRADINGVIEW_PROVIDER.fetch_quotes(["NIFTY"])
        finally:
            TRADINGVIEW_PROVIDER._post_scanner_request = original

        self.assertIn("NIFTY", quotes, "the fallback produced no quote for NIFTY")
        self.assertGreater(quotes["NIFTY"]["price"], 0)

    def test_the_fallback_price_comes_from_the_universe_table(self):
        """The number must be the static reference, not a re-derived one. A
        regression that reads the table but returns something else would pass
        the two tests above and still be wrong."""
        from jarvis.data.tradingview_provider import TRADINGVIEW_PROVIDER
        from jarvis.india.universe import INDIA_UNIVERSE

        original = TRADINGVIEW_PROVIDER._post_scanner_request
        TRADINGVIEW_PROVIDER._post_scanner_request = lambda endpoint, tickers: []
        try:
            quotes = TRADINGVIEW_PROVIDER.fetch_quotes(["NIFTY"])
        finally:
            TRADINGVIEW_PROVIDER._post_scanner_request = original

        expected = float(INDIA_UNIVERSE["NIFTY"]["base_price"])
        self.assertEqual(quotes["NIFTY"]["price"], expected)


if __name__ == "__main__":
    unittest.main()

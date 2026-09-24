"""The scan must not depend on the network, or it is not reproducible.

WHY THIS EXISTS
---------------
The §J A/B compares two spread registries on the same bars. That comparison is
only meaningful if nothing ELSE varies between the two arms -- and something did.

`MacroAnalyst` calls `GLOBAL_NEWS_ENGINE.get_news_calendar()` on the decision
path. That is a synchronous network fetch (`jarvis/market/news.py` allows 5s and
6s) sitting behind a 90s cache TTL, while `ParallelAnalystCluster` gives the
analyst only 2.0s. On a cache miss the fetch was measured at 1.32s -- 66% of the
analyst's whole budget, before any CPU work, competing with six other analysts
for the GIL. So on a cache miss MACRO is replaced by a FABRICATED score-50
NEUTRAL report, and because the TTL expiry lands wherever it lands relative to
the scan, the scan's own output changes between runs.

Measured before `frozen_news` existed: AUDUSD under the pristine registry gave
EXEC 51 in a clean process (2 reps, 0 fallbacks) and 53 / 56 in runs that logged
`Analyst MACRO failed or timed out after 2.00s`. One measurement, three answers.

`frozen_news()` takes one snapshot and serves it for the rest of the scan, so the
only thing the two arms vary is the spread registry. It is deliberately MORE
reproducible than production; the production defect it papers over (a budget
smaller than its dependency's timeout) is tracked separately and is NOT fixed
here.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(REPO, "tools", "spread_registry_ab.py")


def _fresh_harness():
    """Load the harness the same way `test_spread_registry_ab_restore` does."""
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    from jarvis.data import symbol_registry as reg
    importlib.reload(reg)

    spec = importlib.util.spec_from_file_location("spread_registry_ab_news_under_test", HARNESS)
    assert spec and spec.loader, f"could not load {HARNESS}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _engine():
    import jarvis.market.news as news_mod
    return news_mod, news_mod.GLOBAL_NEWS_ENGINE


def _impl(engine):
    """The underlying function behind `get_news_calendar`.

    Bound methods are rebuilt on every attribute access, so `is` on them asserts
    nothing; and inside the freeze the attribute is a plain function with no
    `__func__`. Normalise both to the function itself.
    """
    return getattr(engine.get_news_calendar, "__func__", engine.get_news_calendar)


def _normalise(engine):
    """Drop any pre-existing instance attribute on the singleton.

    `GLOBAL_NEWS_ENGINE` is module-level, so a test that patches
    `get_news_calendar` and restores it by ASSIGNING the bound method back leaves
    a permanent instance attribute behind (that was a real defect in
    `test_analyst_scoring`, fixed alongside this file). `frozen_news` faithfully
    restores whatever it found, so a polluted singleton would make the
    `not in engine.__dict__` assertions below fail for a reason that has nothing
    to do with the freeze. Normalising first keeps these tests measuring
    `frozen_news` rather than the previous test's leftovers.
    """
    engine.__dict__.pop("get_news_calendar", None)


class TestTheContextManagerContract:
    def test_it_restores_the_original_on_exit(self):
        m = _fresh_harness()
        _, engine = _engine()
        _normalise(engine)
        # Compare the underlying FUNCTIONS, not the bound methods: each attribute
        # access builds a fresh bound-method object, so `is` on those is always
        # False and would assert nothing.
        original_func = _impl(engine)
        with m.frozen_news():
            assert _impl(engine) is not original_func, "premise: it must patch"
        assert _impl(engine) is original_func, "the patch was not restored"
        assert "get_news_calendar" not in engine.__dict__, (
            "restore left an instance attribute shadowing the class method"
        )

    def test_it_restores_even_when_the_body_raises(self):
        """A scan that dies must not leave the process serving a frozen calendar."""
        m = _fresh_harness()
        _, engine = _engine()
        _normalise(engine)
        original_func = _impl(engine)
        with pytest.raises(RuntimeError):
            with m.frozen_news():
                raise RuntimeError("scan exploded")
        assert engine.get_news_calendar.__func__ is original_func
        assert "get_news_calendar" not in engine.__dict__

    def test_it_is_signature_compatible_with_force_refresh(self):
        """Callers pass `force_refresh=`; the replacement must accept it."""
        m = _fresh_harness()
        with m.frozen_news():
            _, engine = _engine()
            engine.get_news_calendar(force_refresh=True)   # must not raise

    def test_the_yielded_snapshot_is_what_the_engine_serves(self):
        m = _fresh_harness()
        with m.frozen_news() as snapshot:
            _, engine = _engine()
            assert engine.get_news_calendar() == snapshot


class TestTheFreezeRemovesTheNetworkFromTheMeasurement:
    def test_a_frozen_calendar_does_not_fetch_even_when_the_cache_is_stale(self):
        """This is the whole point: a TTL expiry must not reach the network.

        The production failure is exactly a stale cache (90s TTL) triggering a
        fetch that cannot finish inside the analyst's 2.0s budget. So the test
        forces the cache stale rather than waiting 90 seconds.
        """
        m = _fresh_harness()
        _, engine = _engine()
        calls = []
        original_fetch = engine._fetch_all_live_sources
        engine._fetch_all_live_sources = lambda: (
            calls.append(1), [{"currency": "USD", "impact": "high", "title": "t"}]
        )[1]
        try:
            with m.frozen_news():
                calls.clear()                      # ignore the snapshot fetch
                engine._last_fetch_time = 0.0      # force the cache stale
                engine._cached_news = []
                engine.get_news_calendar()
                engine.get_news_calendar()
                assert calls == [], (
                    "the frozen calendar still reached the network — a cache-miss "
                    "fetch here is what makes MACRO exceed its 2.0s budget and be "
                    "replaced by a fabricated NEUTRAL report"
                )
        finally:
            engine._fetch_all_live_sources = original_fetch

    def test_control_an_unfrozen_stale_cache_does_fetch(self):
        """Negative control: without the freeze, the same stale cache DOES fetch.

        Without this, the test above could pass for the wrong reason — e.g. if
        `get_news_calendar` had stopped consulting the TTL at all.
        """
        _fresh_harness()   # the harness must be importable; the freeze is NOT used
        _, engine = _engine()
        calls = []
        original_fetch = engine._fetch_all_live_sources
        engine._fetch_all_live_sources = lambda: (
            calls.append(1), [{"currency": "USD", "impact": "high", "title": "t"}]
        )[1]
        try:
            engine._last_fetch_time = 0.0
            engine._cached_news = []
            engine.get_news_calendar()
            assert calls == [1], (
                "control failed: a stale unfrozen cache did not fetch, so the "
                "freeze test above proves nothing"
            )
        finally:
            engine._fetch_all_live_sources = original_fetch

    def test_the_frozen_calendar_is_stable_across_repeated_calls(self):
        """Stability is what makes the scan reproducible run to run."""
        m = _fresh_harness()
        with m.frozen_news():
            _, engine = _engine()
            first = engine.get_news_calendar()
            engine._last_fetch_time = 0.0          # even a stale cache must not change it
            engine._cached_news = []
            assert engine.get_news_calendar() == first

"""`get_news_calendar(max_wait=...)` must bound the wait without fabricating data.

WHY THIS EXISTS
---------------
`MacroAnalyst` calls `GLOBAL_NEWS_ENGINE.get_news_calendar()` on the decision
path, inside `ParallelAnalystCluster`, which allows the analyst **2.0s**. The news
engine's socket timeouts are 5s and 6s behind a 90s cache TTL, and a cache miss
was measured at 1.32s -- 66% of the analyst's entire budget, before any CPU work,
competing with six other analysts for the GIL.

So on a cache miss MACRO could not finish, and `parallel_runner` replaced it with
a **fabricated score-50 NEUTRAL report** -- a fabricated input to a live trading
decision, and the reason the §J scan drifted between runs (one symbol+registry
gave EXEC 51 / 53 / 56).

`max_wait` is the fix at the engine boundary: the fetch moves to a daemon thread
that also refreshes the cache when it lands, so a slow feed costs the caller
nothing and the next caller benefits.

The default is deliberately `None` == today's behaviour, so no existing caller
changes. These tests pin both halves: that the default is untouched, and that the
bounded path prefers stale REAL data to a synthetic calendar.
"""

from __future__ import annotations

import time

import jarvis.market.news as news_mod


def _item(currency: str) -> dict:
    """A news item that survives `_organize_news_feed` with a findable marker."""
    return {
        "currency": currency,
        "impact": "HIGH",
        "event": "TEST",
        "actual": "1",
        "forecast": "1",
        "timestamp_iso": "2030-01-01T00:00:00+00:00",
    }


def _currencies(calendar) -> set:
    return {str(c.get("currency")) for c in (calendar or [])}


def _engine_with_fetch(monkeypatch, fetch):
    eng = news_mod.LiveNewsEngine()
    monkeypatch.setattr(eng, "_fetch_all_live_sources", fetch)
    return eng


def _slow(marker: str, seconds: float):
    def _f():
        time.sleep(seconds)
        return [_item(marker)]
    return _f


class TestTheDefaultIsUnchanged:
    def test_max_wait_none_still_fetches_synchronously(self, monkeypatch):
        """The news page and every existing caller must be byte-for-byte unchanged."""
        eng = _engine_with_fetch(monkeypatch, _slow("FRESH", 0.0))
        cal = eng.get_news_calendar()          # no max_wait
        assert "FRESH" in _currencies(cal)

    def test_max_wait_none_still_waits_for_a_slow_feed(self, monkeypatch):
        """Explicitly NOT bounded: this is the behaviour we are not changing."""
        eng = _engine_with_fetch(monkeypatch, _slow("FRESH", 0.6))
        t0 = time.perf_counter()
        cal = eng.get_news_calendar()
        assert time.perf_counter() - t0 >= 0.5
        assert "FRESH" in _currencies(cal)

    def test_a_fresh_cache_is_served_without_fetching(self, monkeypatch):
        calls = []
        eng = _engine_with_fetch(monkeypatch, lambda: (calls.append(1), [_item("FRESH")])[1])
        eng.get_news_calendar()
        eng.get_news_calendar()
        assert calls == [1], "the TTL cache must absorb the second call"


class TestTheBoundedPath:
    def test_it_does_not_wait_for_a_slow_feed(self, monkeypatch):
        eng = _engine_with_fetch(monkeypatch, _slow("FRESH", 3.0))
        t0 = time.perf_counter()
        eng.get_news_calendar(max_wait=0.3)
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.5, f"max_wait=0.3 but the call blocked {elapsed:.2f}s"

    def test_a_stale_cache_beats_a_synthetic_calendar(self, monkeypatch):
        """The whole point: real-but-stale data, never a fabricated reading."""
        eng = _engine_with_fetch(monkeypatch, _slow("FRESH", 3.0))
        eng._cached_news = [_item("CACHED")]
        eng._last_fetch_time = 0.0              # force stale, do not wait 90s

        cal = eng.get_news_calendar(max_wait=0.3)
        assert "CACHED" in _currencies(cal), "a stale real cache must be served"
        assert "FRESH" not in _currencies(cal), "premise: the slow fetch cannot have landed"

    def test_a_cold_start_with_a_slow_feed_falls_back_to_the_deterministic_calendar(self, monkeypatch):
        """Documented, and the only case that still fabricates.

        With no cache at all there is nothing real to serve. The refresh thread
        populates the cache for the next caller, so this is a one-call cost.
        """
        eng = _engine_with_fetch(monkeypatch, _slow("FRESH", 3.0))
        cal = eng.get_news_calendar(max_wait=0.2)
        assert cal is not None
        assert "FRESH" not in _currencies(cal)

    def test_the_slow_fetch_still_populates_the_cache_for_the_next_caller(self, monkeypatch):
        """Self-healing: timing out must not throw the fetch away."""
        eng = _engine_with_fetch(monkeypatch, _slow("FRESH", 0.6))
        eng.get_news_calendar(max_wait=0.1)      # gives up early
        assert "FRESH" not in _currencies(eng._cached_news or [])

        deadline = time.perf_counter() + 3.0
        while time.perf_counter() < deadline:
            if "FRESH" in _currencies(eng._cached_news or []):
                break
            time.sleep(0.05)
        assert "FRESH" in _currencies(eng._cached_news or []), (
            "the background refresh must land in the cache for the next caller"
        )

    def test_a_fast_feed_under_the_budget_returns_fresh_data(self, monkeypatch):
        """The happy path must not be penalised by the bounded path."""
        eng = _engine_with_fetch(monkeypatch, _slow("FRESH", 0.0))
        cal = eng.get_news_calendar(max_wait=2.0)
        assert "FRESH" in _currencies(cal)

    def test_a_burst_does_not_spawn_a_thread_per_caller(self, monkeypatch):
        """A scan calls this once per candidate; the in-flight guard must hold."""
        started = []

        def fetch():
            started.append(1)
            time.sleep(1.0)
            return [_item("FRESH")]

        eng = _engine_with_fetch(monkeypatch, fetch)
        eng._cached_news = [_item("CACHED")]
        eng._last_fetch_time = 0.0

        t0 = time.perf_counter()
        for _ in range(5):
            eng.get_news_calendar(max_wait=0.05)
        elapsed = time.perf_counter() - t0

        assert elapsed < 1.0, "the burst should return immediately, not serialise"
        assert len(started) == 1, f"expected one in-flight refresh, got {len(started)}"

    def test_a_raising_feed_does_not_escape_the_bounded_path(self, monkeypatch):
        """A background refresh must never propagate into the caller."""
        def boom():
            raise RuntimeError("feed down")

        eng = _engine_with_fetch(monkeypatch, boom)
        eng._cached_news = [_item("CACHED")]
        eng._last_fetch_time = 0.0
        cal = eng.get_news_calendar(max_wait=0.3)
        assert "CACHED" in _currencies(cal)

    def test_force_refresh_with_a_budget_still_returns_something(self, monkeypatch):
        eng = _engine_with_fetch(monkeypatch, _slow("FRESH", 3.0))
        eng._cached_news = [_item("CACHED")]
        eng._last_fetch_time = time.time()       # fresh, but force_refresh ignores that
        cal = eng.get_news_calendar(force_refresh=True, max_wait=0.2)
        assert cal is not None
        assert "CACHED" in _currencies(cal)


class TestTheInFlightFlagIsAlwaysCleared:
    def test_it_clears_after_a_successful_refresh(self, monkeypatch):
        eng = _engine_with_fetch(monkeypatch, _slow("FRESH", 0.0))
        eng.get_news_calendar(max_wait=2.0)
        assert eng._refresh_in_flight is False

    def test_it_clears_after_a_failing_refresh(self, monkeypatch):
        def boom():
            raise RuntimeError("feed down")

        eng = _engine_with_fetch(monkeypatch, boom)
        eng.get_news_calendar(max_wait=2.0)
        deadline = time.perf_counter() + 2.0
        while eng._refresh_in_flight and time.perf_counter() < deadline:
            time.sleep(0.05)
        assert eng._refresh_in_flight is False, (
            "a failed refresh must not latch the flag, or every later call "
            "would serve stale data forever"
        )

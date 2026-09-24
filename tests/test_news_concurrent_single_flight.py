"""A burst of callers must make ONE request per source, not one each.

WHY THIS EXISTS
---------------
`GLOBAL_NEWS_ENGINE` is a single shared `LiveNewsEngine`. A scan reaches it from
many threads at once -- `MacroAnalyst` calls `get_news_calendar()` once per
candidate, and `ParallelAnalystCluster` runs 8 workers, so nine callers can hit a
cold cache inside the same instant.

The per-source backoffs cannot deduplicate that burst, and the reason is
structural rather than a missing `if`: **a backoff is only armed AFTER the first
request fails.** So every thread reads `_mfb_backoff_until` as `0.0`, passes the
guard, and issues its own request -- all before any of them can arm anything.

Measured on a live start: **nine** identical `MyFxBook is blocked (HTTP 403 ...)`
warnings, every one stamped `16:36:39`.

Two costs, both real:

* Nine duplicate requests to a source that is rate-limiting us is what EARNS the
  429 in the first place. The backoff then treats a symptom we caused.
* Nine threads spend ~0.5s each on a request that cannot succeed, inside a 2.0s
  analyst budget, competing for the GIL.

The fix is single-flight in `_fetch_single_flight`: the leader fetches and
publishes to the cache, followers wait for the leader instead of repeating it.
The guard/arm pairs were also moved under `self._lock`, so the state is coherent
for sequential callers as well.

NOTE ON THE CONTROL TEST. A naive "fire 9 threads and count" control is flaky --
it is possible for the first thread to fail and arm the backoff before the ninth
thread even starts. `_BarrierRecorder` removes that luck: no request may return
until all N have entered, which forces all N past the guard by construction. That
is what makes the `== 1` assertion in the fixed test meaningful.
"""
from __future__ import annotations

import threading
import time

import jarvis.market.news as news_mod
from jarvis.market.news import LiveNewsEngine

LOGGER = "HM_LiveNewsEngine"
BURST = 9          # 8 cluster workers + the scan loop, matching the live start


class _HttpError(Exception):
    """Stands in for urllib.error.HTTPError with a status code."""

    def __init__(self, code: int):
        super().__init__(f"HTTP {code}")
        self.code = code


def _source_of(req) -> str:
    url = getattr(req, "full_url", None) or str(req)
    return "faireconomy" if "faireconomy" in url else "myfxbook"


class _Recorder:
    """Counts requests per source. Thread-safe.

    `hold` makes the request take measurable time before failing. That is what
    makes the burst count deterministic rather than lucky: the backoff is armed
    only when a request FAILS, so holding the request open guarantees every
    thread that is going to pass the guard does so before the first arm lands.
    Without it, a single-flight test can pass merely because thread 1 happened to
    fail before thread 9 started -- measuring scheduling luck, not the fix.
    """

    def __init__(self, fe_code: int = 429, mfb_code: int = 403, hold: float = 0.0):
        self._lock = threading.Lock()
        self.calls = {"faireconomy": 0, "myfxbook": 0}
        self.fe_code = fe_code
        self.mfb_code = mfb_code
        self.hold = hold

    def __call__(self, req, *a, **k):
        key = _source_of(req)
        with self._lock:
            self.calls[key] += 1
        if self.hold:
            time.sleep(self.hold)
        raise _HttpError(self.fe_code if key == "faireconomy" else self.mfb_code)


class _BarrierRecorder(_Recorder):
    """As `_Recorder`, but no request returns until all `n` have entered.

    This is what makes the race deterministic: the first failure (which arms the
    backoff) cannot happen until every thread has already passed the guard.
    """

    def __init__(self, n: int, fe_code: int = 429, mfb_code: int = 403):
        super().__init__(fe_code, mfb_code)
        self._barrier = threading.Barrier(n, timeout=5)

    def __call__(self, req, *a, **k):
        key = _source_of(req)
        with self._lock:
            self.calls[key] += 1
        self._barrier.wait()
        raise _HttpError(self.fe_code if key == "faireconomy" else self.mfb_code)


def _engine(monkeypatch, recorder) -> LiveNewsEngine:
    eng = LiveNewsEngine()
    monkeypatch.setattr(news_mod.urllib.request, "urlopen", recorder)
    return eng


def _burst(fn, n: int = BURST):
    """Run `fn` on `n` threads released simultaneously. Returns raised exceptions."""
    barrier = threading.Barrier(n, timeout=5)
    errors: list[BaseException] = []

    def worker():
        try:
            barrier.wait()
            fn()
        except BaseException as exc:      # noqa: BLE001 - reported, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    return errors


def _cold(eng: LiveNewsEngine) -> None:
    """Force the next call to miss the TTL cache."""
    eng._cached_news = []
    eng._last_fetch_time = 0.0


# ---------------------------------------------------------------------------
# The defect, proven deterministically
# ---------------------------------------------------------------------------

class TestTheRaceIsReal:
    def test_every_thread_past_the_guard_makes_its_own_request(self, monkeypatch):
        """CONTROL: this is the bug. It must fail loudly if the race is fixed
        by accident somewhere else, because then the fixed test below would be
        asserting nothing."""
        recorder = _BarrierRecorder(BURST)
        eng = _engine(monkeypatch, recorder)

        errors = _burst(lambda: eng._fetch_myfxbook_feed())
        assert not errors, f"the stub must not leak exceptions: {errors}"

        assert recorder.calls["myfxbook"] == BURST, (
            "premise: all nine threads should reach the network before any "
            "failure arms the backoff -- if this is not true the race is gone "
            "for another reason and the fix below proves nothing"
        )

    def test_the_burst_produces_one_warning_per_thread(self, monkeypatch, caplog):
        """The observable symptom: nine log lines for one fact."""
        recorder = _BarrierRecorder(BURST)
        eng = _engine(monkeypatch, recorder)

        with caplog.at_level("WARNING", logger=LOGGER):
            _burst(lambda: eng._fetch_myfxbook_feed())

        blocked = [r for r in caplog.records if "blocked" in r.message.lower()]
        assert len(blocked) == BURST, (
            f"expected the symptom ({BURST} duplicate warnings), got {len(blocked)}"
        )


# ---------------------------------------------------------------------------
# The fix: one network pass per burst
# ---------------------------------------------------------------------------

class TestSingleFlight:
    def test_a_concurrent_burst_makes_one_request_per_source(self, monkeypatch):
        # `hold` is load-bearing: it keeps every request open long enough that
        # all nine threads are past the guard before the first failure can arm
        # the backoff. With the fix, eight of them never reach the network at
        # all. Without it, this asserts nine.
        recorder = _Recorder(hold=0.3)
        eng = _engine(monkeypatch, recorder)
        _cold(eng)

        errors = _burst(lambda: eng.get_news_calendar())
        assert not errors, f"the engine must not raise into callers: {errors}"

        assert recorder.calls == {"faireconomy": 1, "myfxbook": 1}, (
            f"the burst was not collapsed: {recorder.calls}"
        )

    def test_the_burst_logs_the_block_exactly_once(self, monkeypatch, caplog):
        """The 9-warnings-in-one-second symptom must be gone."""
        recorder = _Recorder(hold=0.3)
        eng = _engine(monkeypatch, recorder)
        _cold(eng)

        with caplog.at_level("WARNING", logger=LOGGER):
            _burst(lambda: eng.get_news_calendar())

        blocked = [r for r in caplog.records if "blocked" in r.message.lower()]
        rate_limited = [r for r in caplog.records if "rate-limited" in r.message.lower()]
        assert len(blocked) == 1, f"expected one block warning, got {len(blocked)}"
        assert len(rate_limited) == 1, (
            f"expected one rate-limit warning, got {len(rate_limited)}")

    def test_followers_still_receive_the_news(self, monkeypatch):
        """Single-flight must not trade correctness for request count.

        A follower that waited for the leader has to get the leader's data, not
        an empty list -- otherwise this fix would silently starve eight of the
        nine analysts of real news.
        """
        calls = []
        marker = {"currency": "USD", "impact": "HIGH", "event": "TEST",
                  "actual": "1", "forecast": "1",
                  "timestamp_iso": "2030-01-01T00:00:00+00:00"}

        def fetch():
            calls.append(1)
            time.sleep(0.4)          # long enough that followers must wait
            return [dict(marker)]

        eng = LiveNewsEngine()
        monkeypatch.setattr(eng, "_fetch_all_live_sources", fetch)
        _cold(eng)

        seen: list[set] = []
        lock = threading.Lock()

        def call():
            cal = eng.get_news_calendar()
            with lock:
                seen.append({str(c.get("currency")) for c in (cal or [])})

        errors = _burst(call)
        assert not errors, errors
        assert len(calls) == 1, f"expected one fetch, got {len(calls)}"
        assert len(seen) == BURST
        assert all("USD" in s for s in seen), (
            f"a follower was starved of the leader's data: {seen}"
        )

    def test_the_bounded_path_also_collapses_the_burst(self, monkeypatch):
        """The latency-budget path shares the same claim flag."""
        calls = []

        def fetch():
            calls.append(1)
            time.sleep(0.5)
            return [{"currency": "USD", "impact": "HIGH", "event": "T",
                     "actual": "1", "forecast": "1",
                     "timestamp_iso": "2030-01-01T00:00:00+00:00"}]

        eng = LiveNewsEngine()
        monkeypatch.setattr(eng, "_fetch_all_live_sources", fetch)
        _cold(eng)

        errors = _burst(lambda: eng.get_news_calendar(max_wait=0.05))
        assert not errors, errors
        assert len(calls) == 1, f"expected one refresh, got {len(calls)}"


# ---------------------------------------------------------------------------
# The claim must always be released, or the engine dies quietly
# ---------------------------------------------------------------------------

class TestTheClaimIsAlwaysReleased:
    def test_it_is_released_after_a_successful_fetch(self, monkeypatch):
        eng = _engine(monkeypatch, _Recorder())
        _cold(eng)
        eng.get_news_calendar()
        assert eng._refresh_in_flight is False

    def test_it_is_released_after_a_raising_fetch(self, monkeypatch):
        """A feed that raises must not latch the claim, or every later caller
        would follow a leader that no longer exists and serve stale data."""
        def boom():
            raise RuntimeError("feed down")

        eng = LiveNewsEngine()
        monkeypatch.setattr(eng, "_fetch_all_live_sources", boom)
        _cold(eng)

        eng.get_news_calendar()          # must not raise
        assert eng._refresh_in_flight is False

    def test_a_later_caller_can_still_fetch_after_a_failure(self, monkeypatch):
        """Non-vacuity: the release must be real, not cosmetic."""
        calls = []

        def fetch():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("feed down")
            return [{"currency": "USD", "impact": "HIGH", "event": "T",
                     "actual": "1", "forecast": "1",
                     "timestamp_iso": "2030-01-01T00:00:00+00:00"}]

        eng = LiveNewsEngine()
        monkeypatch.setattr(eng, "_fetch_all_live_sources", fetch)
        _cold(eng)
        eng.get_news_calendar()

        _cold(eng)
        cal = eng.get_news_calendar()
        assert len(calls) == 2, "the second call must have fetched"
        assert "USD" in {str(c.get("currency")) for c in (cal or [])}

    def test_a_wedged_leader_does_not_pin_a_follower_forever(self, monkeypatch):
        """The wait is bounded, so a hung leader degrades instead of hanging."""
        release = threading.Event()

        def wedged():
            release.wait(timeout=10)
            return []

        eng = LiveNewsEngine()
        monkeypatch.setattr(eng, "_fetch_all_live_sources", wedged)

        leader = threading.Thread(target=lambda: eng._fetch_single_flight(5.0),
                                  daemon=True)
        leader.start()
        time.sleep(0.15)                     # let the leader claim the flag

        t0 = time.perf_counter()
        result = eng._fetch_single_flight(0.25)
        elapsed = time.perf_counter() - t0

        release.set()
        leader.join(timeout=5)
        assert result == []
        assert elapsed < 1.5, f"the follower wait was not bounded: {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# The sequential behaviour the lock change touches must be unchanged
# ---------------------------------------------------------------------------

class TestSequentialBehaviourIsUnchanged:
    def test_a_second_sequential_caller_is_still_skipped(self, monkeypatch):
        recorder = _Recorder()
        eng = _engine(monkeypatch, recorder)
        eng._fetch_myfxbook_feed()
        assert recorder.calls["myfxbook"] == 1
        for _ in range(5):
            eng._fetch_myfxbook_feed()
        assert recorder.calls["myfxbook"] == 1, "the backoff stopped holding"

    def test_a_429_still_arms_the_faireconomy_backoff(self, monkeypatch):
        eng = _engine(monkeypatch, _Recorder())
        assert eng._fe_backoff_until == 0.0
        eng._fetch_faireconomy_feed()
        assert eng._fe_backoff_until > 0.0

    def test_a_non_backoff_status_still_does_not_arm(self, monkeypatch):
        eng = _engine(monkeypatch, _Recorder(fe_code=500, mfb_code=500))
        eng._fetch_faireconomy_feed()
        eng._fetch_myfxbook_feed()
        assert eng._fe_backoff_until == 0.0
        assert eng._mfb_backoff_until == 0.0

    def test_the_sync_path_still_blocks_for_a_slow_feed(self, monkeypatch):
        """Explicitly NOT bounded -- the news page depends on this."""
        def slow():
            time.sleep(0.5)
            return [{"currency": "USD", "impact": "HIGH", "event": "T",
                     "actual": "1", "forecast": "1",
                     "timestamp_iso": "2030-01-01T00:00:00+00:00"}]

        eng = LiveNewsEngine()
        monkeypatch.setattr(eng, "_fetch_all_live_sources", slow)
        _cold(eng)
        t0 = time.perf_counter()
        cal = eng.get_news_calendar()
        assert time.perf_counter() - t0 >= 0.4
        assert "USD" in {str(c.get("currency")) for c in (cal or [])}

    def test_the_ttl_cache_still_absorbs_a_repeat_call(self, monkeypatch):
        calls = []

        def fetch():
            calls.append(1)
            return [{"currency": "USD", "impact": "HIGH", "event": "T",
                     "actual": "1", "forecast": "1",
                     "timestamp_iso": "2030-01-01T00:00:00+00:00"}]

        eng = LiveNewsEngine()
        monkeypatch.setattr(eng, "_fetch_all_live_sources", fetch)
        _cold(eng)
        eng.get_news_calendar()
        eng.get_news_calendar()
        assert calls == [1], "the 90s TTL must still absorb the second call"

"""A 429 is a cooldown, not a glitch -- the engine must stop asking.

WHY THIS EXISTS. FairEconomy answers a burst with **HTTP 429** and an HTML
"Rate Limited" page, then needs minutes to recover. The engine's cache TTL is
**90s**, so before this change every expiry fired another request straight back
into the cooldown -- we were the reason it stayed down, and the caller stayed
on the hardcoded calendar the whole time.

Measured: an isolated call returns **200 / 10,849 bytes**; at 90s spacing while
recovering from a burst it returned **0, 0, 80, 80** items. So the feed works --
what it will not tolerate is being retried on a timer while it is cooling down.

Note this does NOT make the feed reliable. It only stops the engine from
prolonging the outage, and lets the next attempt actually have a chance.
"""
import jarvis.market.news as news_mod
from jarvis.market.news import LiveNewsEngine


class _RateLimited(Exception):
    """Stands in for urllib.error.HTTPError with code=429."""
    code = 429


class _ServerError(Exception):
    code = 500


class _Counter:
    def __init__(self, exc=None):
        self.calls = 0
        self.exc = exc

    def __call__(self, *a, **k):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        raise _ServerError("no payload in this stub")


def _engine(monkeypatch, counter):
    eng = LiveNewsEngine()
    monkeypatch.setattr(news_mod.urllib.request, "urlopen", counter)
    return eng


def test_a_429_starts_a_backoff(monkeypatch):
    eng = _engine(monkeypatch, _Counter(_RateLimited()))
    assert eng._fe_backoff_until == 0.0
    eng._fetch_faireconomy_feed()
    assert eng._fe_backoff_until > 0.0, "a 429 must arm the backoff"


def test_a_429_stops_the_next_call_from_reaching_the_network(monkeypatch):
    """The point of the backoff: do not re-enter a cooldown on the 90s TTL."""
    counter = _Counter(_RateLimited())
    eng = _engine(monkeypatch, counter)
    eng._fetch_faireconomy_feed()
    assert counter.calls == 1

    for _ in range(5):
        eng._fetch_faireconomy_feed()
    assert counter.calls == 1, (
        f"backoff did not hold: {counter.calls} requests reached the feed")


def test_the_backoff_expires_and_requests_resume(monkeypatch):
    """Non-vacuity: the guard must be a delay, not a permanent disable."""
    counter = _Counter(_RateLimited())
    eng = _engine(monkeypatch, counter)
    eng._fetch_faireconomy_feed()
    assert counter.calls == 1

    eng._fe_backoff_until = 0.0          # simulate the cooldown elapsing
    eng._fetch_faireconomy_feed()
    assert counter.calls == 2, "requests must resume once the backoff expires"


def test_a_non_429_error_does_not_arm_the_backoff(monkeypatch):
    """A 500 or a socket error is not a rate limit; do not sit out 10 minutes."""
    eng = _engine(monkeypatch, _Counter(_ServerError()))
    eng._fetch_faireconomy_feed()
    assert eng._fe_backoff_until == 0.0


def test_the_backoff_is_announced(monkeypatch, caplog):
    eng = _engine(monkeypatch, _Counter(_RateLimited()))
    with caplog.at_level("WARNING", logger="HM_LiveNewsEngine"):
        eng._fetch_faireconomy_feed()
    assert any("rate-limited" in r.message.lower() for r in caplog.records), \
        "an armed backoff must be visible in the log"


def test_a_fresh_engine_is_not_backed_off(monkeypatch):
    """The backoff must not be class-level state leaking across instances."""
    counter = _Counter(_RateLimited())
    eng = _engine(monkeypatch, counter)
    eng._fetch_faireconomy_feed()
    fresh = LiveNewsEngine()
    assert fresh._fe_backoff_until == 0.0


# --------------------------------------------------------------------------
# MyFxBook is BLOCKED, not flaky -- so it must not be retried every fetch
# --------------------------------------------------------------------------

class _Forbidden(Exception):
    code = 403


def test_a_403_arms_the_myfxbook_backoff(monkeypatch):
    eng = _engine(monkeypatch, _Counter(_Forbidden()))
    assert eng._mfb_backoff_until == 0.0
    eng._fetch_myfxbook_feed()
    assert eng._mfb_backoff_until > 0.0


def test_the_myfxbook_backoff_stops_the_wasted_request(monkeypatch):
    """It answers 403 every time, so each retry is ~0.5s of the MACRO budget."""
    counter = _Counter(_Forbidden())
    eng = _engine(monkeypatch, counter)
    eng._fetch_myfxbook_feed()
    assert counter.calls == 1
    for _ in range(5):
        eng._fetch_myfxbook_feed()
    assert counter.calls == 1


def test_the_myfxbook_backoff_expires(monkeypatch):
    counter = _Counter(_Forbidden())
    eng = _engine(monkeypatch, counter)
    eng._fetch_myfxbook_feed()
    eng._mfb_backoff_until = 0.0
    eng._fetch_myfxbook_feed()
    assert counter.calls == 2


def test_a_non_blocking_status_does_not_arm_it(monkeypatch):
    """A 500 is not a block; do not sit out an hour for it."""
    eng = _engine(monkeypatch, _Counter(_ServerError()))
    eng._fetch_myfxbook_feed()
    assert eng._mfb_backoff_until == 0.0


def test_a_blocked_myfxbook_costs_the_fetch_nothing(monkeypatch):
    """THE POINT: `_fetch_all_live_sources` must stop paying for a dead source.

    Two requests per cycle, one of which cannot succeed, was ~0.5s of the
    ~1.26s fetch -- 40% of the analyst's 2.0s budget.
    """
    counter = _Counter(_Forbidden())
    eng = _engine(monkeypatch, counter)
    eng._fetch_myfxbook_feed()          # learns the block
    before = counter.calls

    eng._fetch_all_live_sources()
    # FairEconomy is asked, MyFxBook is not.
    assert counter.calls - before == 1, (
        f"a blocked source was retried: {counter.calls - before} requests")

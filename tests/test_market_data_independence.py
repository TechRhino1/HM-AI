"""Market data must not depend on the EXECUTION mode, and fabricated bars must
not pass for real ones.

Two defects these tests pin, both of which made the platform read as
"broker offline / no data" while a live terminal sat right there:

1. **`MT5Client.init_connection()` returns early for paper mode**, so
   `mt5.initialize()` was never called in the process where the data feed runs.
   `resolve_broker_symbol()` then failed every probe, the canonical name
   (`XAUUSD`) reached `copy_rates_from_pos` instead of the broker's `GOLD.i#`,
   the broker answered 0 rows and every frame became SYNTHETIC_FALLBACK.
   Paper trading is a *fill* decision; it must not stop us reading real bars.

2. **A failed probe was cached forever.** A single `resolve_broker_symbol()`
   call made before the terminal was ready pinned every symbol to None for the
   life of the process, long after the terminal came up - and "probe failed" is
   indistinguishable from "the broker has no such symbol".

Also pinned here: the fuzzy symbol scan resolved `COPPER` to `SouthernCopper`
(an EQUITY) and returned it as the copper commodity, stamped `LIVE_MT5` and
certified FRESH. A wrong instrument that looks healthy is worse than an
unresolved one.
"""

import time

import numpy as np
import pandas as pd
import pytest

from jarvis.market import data_feed as df_mod
from jarvis.market.data_feed import (
    FRESH,
    FRESHNESS_UNKNOWN,
    STALE,
    DataFeedEngine,
    first_unusable_frame,
)


class FakeRates:
    """Faithful stand-in for the numpy structured array MT5 returns.

    It must be a real structured array: `pd.DataFrame(rates)` and
    `rates["time"]` are both used by the fetch path, and a hand-rolled sequence
    satisfies neither.
    """

    _DTYPE = [
        ("time", "<i8"),
        ("open", "<f8"),
        ("high", "<f8"),
        ("low", "<f8"),
        ("close", "<f8"),
        ("tick_volume", "<u8"),
    ]

    def __new__(cls, last_epoch, n=60, step=3600):
        rows = [
            (int(last_epoch - (n - 1 - i) * step), 1.0, 2.0, 0.5, 1.5, 10)
            for i in range(n)
        ]
        return np.array(rows, dtype=cls._DTYPE)


class FakeMT5:
    """Scripted MT5 module: returns a stale tail first, fresh bars after."""

    TIMEFRAME_H1 = 16385

    def __init__(self, stale_epoch, fresh_epoch, fresh_after_calls=1):
        self.stale_epoch = stale_epoch
        self.fresh_epoch = fresh_epoch
        self.fresh_after_calls = fresh_after_calls
        self.calls = 0
        self.selected = []

    def copy_rates_from_pos(self, symbol, tf, start, count):
        self.calls += 1
        epoch = self.stale_epoch if self.calls <= self.fresh_after_calls else self.fresh_epoch
        return FakeRates(epoch, n=count)

    def symbol_select(self, name, enable):
        self.selected.append(name)
        return True


@pytest.fixture(autouse=True)
def _no_real_terminal(monkeypatch):
    """Never touch the real terminal from these tests."""
    monkeypatch.setattr(df_mod, "MT5_AVAILABLE", True)
    monkeypatch.setattr(df_mod, "mt5", None, raising=False)
    # `ensure_mt5_terminal` lives in broker_symbols and is imported into
    # data_feed by name, so patch the name data_feed actually calls.
    monkeypatch.setattr(df_mod, "ensure_mt5_terminal", lambda: True)


@pytest.fixture
def weekday_market(monkeypatch):
    """Pin the calendar so the cold-history warm-up is reachable every day.

    The warm-up and the retry it guards only run when a frame classifies as
    STALE, and `classify_bar_freshness` answers MARKET_CLOSED instead whenever
    `now` sits in the weekly close. A ~20h-old bar is therefore STALE
    Monday-Friday and MARKET_CLOSED on Saturday/Sunday, so these three tests
    silently depended on the day the suite ran: measured, 3 failures every
    weekend and green on a weekday, with no code change between them.

    The weekend rule itself is covered where it belongs —
    `test_candle_freshness::test_the_same_gap_on_a_saturday_is_market_closed_not_stale`
    — and the classifier still runs for real here; only the calendar is pinned.
    """
    monkeypatch.setattr(df_mod, "_is_weekend_gap", lambda now_utc: False)


class FakeClient:
    """A client that resolves to the broker's real name, as live mode does."""

    mode = "paper"

    def resolve_symbol_name(self, symbol):
        return symbol


def _engine(monkeypatch, fake_mt5, broker_sym="GOLD.i#"):
    monkeypatch.setattr(df_mod, "mt5", fake_mt5)
    monkeypatch.setattr(df_mod, "resolve_broker_symbol", lambda s, **kw: broker_sym)
    monkeypatch.setattr(df_mod, "broker_utc_offset", lambda **kw: 0)
    return DataFeedEngine(FakeClient())


# ── 1. Execution mode must not gate market data ────────────────────────────

def test_paper_mode_still_asks_for_the_terminal(monkeypatch):
    """The paper client must not short-circuit the data path to synthetic.

    Before the fix, `mode == "paper"` was enough to make every frame synthetic.
    """
    called = {"n": 0}

    def fake_ensure():
        called["n"] += 1
        return True

    monkeypatch.setattr(df_mod, "ensure_mt5_terminal", fake_ensure)
    fake = FakeMT5(stale_epoch=0, fresh_epoch=0)
    engine = _engine(monkeypatch, fake)
    engine.fetch_rates("XAUUSD", "H1", num_bars=10)

    assert called["n"] >= 1, "paper mode never asked for the terminal"
    assert fake.calls >= 1, "paper mode never reached the broker"


def test_terminal_unavailable_falls_back_and_says_so(monkeypatch):
    monkeypatch.setattr(df_mod, "ensure_mt5_terminal", lambda: False)
    fake = FakeMT5(stale_epoch=0, fresh_epoch=0)
    engine = _engine(monkeypatch, fake)

    df = engine.fetch_rates("XAUUSD", "H1", num_bars=10)

    assert df.attrs["data_source"] == "SYNTHETIC_FALLBACK"
    # A fabricated frame must never claim to be verified.
    assert df.attrs["freshness"] == FRESHNESS_UNKNOWN
    assert fake.calls == 0, "the broker was called with no terminal"


# ── 2. Cold-history warm-up ────────────────────────────────────────────────

def test_cold_symbol_is_retried_until_history_lands(monkeypatch, weekday_market):
    """The first read of a cold symbol returns a stale tail; one bounded retry
    must recover the real bars instead of refusing a good symbol."""
    now = time.time()
    fake = FakeMT5(
        stale_epoch=now - 20 * 3600,   # ~20h behind, as measured
        fresh_epoch=now - 3600,
        fresh_after_calls=1,
    )
    engine = _engine(monkeypatch, fake)

    df = engine.fetch_rates("XAUUSD", "H1", num_bars=10)

    assert fake.calls >= 2, "no retry was attempted for the cold symbol"
    assert df.attrs["freshness"] == FRESH, "warm-up did not pick up the real bars"
    assert df.attrs["data_source"] == "LIVE_MT5"


def test_genuinely_stale_feed_is_still_reported_stale(monkeypatch, weekday_market):
    """The warm-up must not become a way to hide a truly stalled feed."""
    now = time.time()
    # Every read is stale -> the retry must give up and report the truth.
    fake = FakeMT5(stale_epoch=now - 20 * 3600, fresh_epoch=now - 20 * 3600)
    engine = _engine(monkeypatch, fake)

    df = engine.fetch_rates("XAUUSD", "H1", num_bars=10)

    assert df.attrs["freshness"] == STALE
    assert df.attrs["data_source"] == "LIVE_MT5"


def test_warmup_is_paid_once_per_symbol(monkeypatch, weekday_market):
    """A second fetch of the same symbol must not wait again."""
    now = time.time()
    fake = FakeMT5(stale_epoch=now - 20 * 3600, fresh_epoch=now - 3600, fresh_after_calls=1)
    engine = _engine(monkeypatch, fake)

    engine.fetch_rates("XAUUSD", "H1", num_bars=10)
    assert "GOLD.i#" in engine._warmed

    calls_after_first = fake.calls
    # Different cache key so the TTL cache does not answer for us.
    engine.fetch_rates("XAUUSD", "H1", num_bars=11)
    assert fake.calls == calls_after_first + 1, "the warm-up wait was paid twice"


# ── 3. Market-data health is measured, not inferred ────────────────────────

def test_health_reports_streaming_for_fresh_live_bars(monkeypatch):
    now = time.time()
    fake = FakeMT5(stale_epoch=now - 3600, fresh_epoch=now - 3600)
    engine = _engine(monkeypatch, fake)

    engine.fetch_rates("XAUUSD", "H1", num_bars=10)

    assert engine.data_health()["status"] == "STREAMING"


def test_health_reports_synthetic_when_bars_are_fabricated(monkeypatch):
    monkeypatch.setattr(df_mod, "ensure_mt5_terminal", lambda: False)
    engine = _engine(monkeypatch, FakeMT5(stale_epoch=0, fresh_epoch=0))

    engine.fetch_rates("XAUUSD", "H1", num_bars=10)

    # Not "STREAMING", and not "OFFLINE" either - we did fetch, just not real data.
    assert engine.data_health()["status"] == "SYNTHETIC"


def test_health_is_offline_before_anything_is_fetched():
    engine = DataFeedEngine(None)
    assert engine.data_health()["status"] == "OFFLINE"


def test_health_goes_offline_when_the_measurement_is_stale(monkeypatch):
    now = time.time()
    fake = FakeMT5(stale_epoch=now - 3600, fresh_epoch=now - 3600)
    engine = _engine(monkeypatch, fake)
    engine.fetch_rates("XAUUSD", "H1", num_bars=10)

    assert engine.data_health(max_age_sec=60)["status"] == "STREAMING"

    # A reading taken an hour ago says nothing about now, however good it was
    # when it was taken.
    engine._health["at"] = time.time() - 3600
    assert engine.data_health(max_age_sec=60)["status"] == "OFFLINE"


# ── 4. Fabricated frames are identifiable ──────────────────────────────────

def test_first_unusable_frame_finds_a_synthetic_role():
    mtf = {
        "macro": pd.DataFrame({"close": [1.0]}),
        "primary": pd.DataFrame({"close": [1.0]}),
    }
    mtf["macro"].attrs["data_source"] = "LIVE_MT5"
    mtf["primary"].attrs["data_source"] = "SYNTHETIC_FALLBACK"

    assert first_unusable_frame(mtf) == ("primary", "SYNTHETIC_FALLBACK")


def test_first_unusable_frame_is_none_for_real_data():
    mtf = {"macro": pd.DataFrame({"close": [1.0]})}
    mtf["macro"].attrs["data_source"] = "LIVE_MT5"
    assert first_unusable_frame(mtf) == (None, None)


def test_first_unusable_frame_tolerates_missing_attrs():
    mtf = {"macro": pd.DataFrame({"close": [1.0]})}
    assert first_unusable_frame(mtf) == (None, None)
    assert first_unusable_frame({}) == (None, None)
    assert first_unusable_frame(None) == (None, None)


# ── 5. Fuzzy symbol resolution must not pick a different instrument ────────

def test_fuzzy_match_requires_a_prefix(monkeypatch):
    """`COPPER` must not resolve to `SouthernCopper` (Southern Copper Corp).

    A substring scan matched the equity and returned it as the copper
    commodity; the frame was stamped LIVE_MT5 and certified FRESH, so nothing
    downstream could tell it was the wrong instrument.
    """
    from jarvis.data import broker_symbols as bs

    # `resolve_broker_symbol` calls this with the injected `mt5_module`, so the
    # stand-in has to accept it (see the note in the probe stub above).
    monkeypatch.setattr(bs, "ensure_mt5_terminal", lambda **kw: True)
    bs._CACHE.clear()
    bs._FAILED.clear()

    class Info:
        def __init__(self, name):
            self.name = name

    class ScanMT5:
        def symbols_get(self):
            return [Info("SouthernCopper"), Info("GOLD.i#")]

        def symbol_info(self, name):
            return None if name in ("COPPER", "SouthernCopper") else object()

    monkeypatch.setattr(bs, "_mt5", lambda: ScanMT5())
    # Only the prefixed candidate is a real, tradeable symbol.
    # `**kw` is not decoration: `resolve_broker_symbol` passes the injected
    # `mt5_module` down to every probe, so a stand-in that ignores it raises
    # TypeError and the test fails for a reason that has nothing to do with
    # what it is asserting. A stub has to mirror the real signature.
    monkeypatch.setattr(bs, "probe_symbol", lambda n, **kw: n == "GOLD.i#")

    assert bs.resolve_broker_symbol("COPPER") is None
    assert bs.resolve_broker_symbol("GOLD") == "GOLD.i#"

    bs._CACHE.clear()
    bs._FAILED.clear()


def test_uninitialised_terminal_does_not_poison_the_cache(monkeypatch):
    """A resolution attempt made before the terminal is up must be retried later.

    Caching that failure pinned every symbol to None for the life of the
    process, long after the terminal came up.
    """
    from jarvis.data import broker_symbols as bs

    bs._CACHE.clear()
    bs._FAILED.clear()

    monkeypatch.setattr(bs, "ensure_mt5_terminal", lambda **kw: False)
    assert bs.resolve_broker_symbol("XAUUSD") is None
    assert "XAUUSD" not in bs._FAILED, "the failure was cached and would never be retried"

    # Terminal comes up -> the very next call must resolve.
    monkeypatch.setattr(bs, "ensure_mt5_terminal", lambda **kw: True)
    monkeypatch.setattr(bs, "probe_symbol", lambda n, **kw: n == "GOLD.i#")
    assert bs.resolve_broker_symbol("XAUUSD") == "GOLD.i#"

    bs._CACHE.clear()
    bs._FAILED.clear()

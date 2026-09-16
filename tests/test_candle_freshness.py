"""Candle freshness — the decision path must not act on a stalled feed.

`fetch_rates` stamped every frame `LIVE_MT5` with no age check at all, so a
frame that stopped updating was indistinguishable from a live one. The check has
two traps that these tests exist to pin:

* **Bar opens are on the BROKER's clock**, like tick times. Measured against the
  raw UTC clock the age under-reports by the broker offset (2-3h for XM), which
  turns a stalled feed into a healthy-looking one.
* **A weekend gap is not a stalled feed.** Forex/metals close ~21:00 UTC Friday
  and reopen ~21:00 UTC Sunday, so a 40h-old H1 frame on a Saturday is expected.
  Reporting it as STALE would cry wolf every weekend and train the reader to
  ignore the warning.
"""

from datetime import datetime, timezone

import numpy as np
import pytest

from jarvis.market.data_feed import (
    FRESH,
    FRESHNESS_UNKNOWN,
    MARKET_CLOSED,
    STALE,
    DataFeedEngine,
    classify_bar_freshness,
)

HOUR = 3600


def epoch(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()


# 2026-09-16 is a Wednesday; 2026-09-19 a Saturday.
WED_NOON = epoch(2026, 9, 16, 12)
SAT_NOON = epoch(2026, 9, 19, 12)


def test_a_recent_h1_bar_is_fresh():
    # Newest CLOSED bar: its open is one duration old.
    verdict, age = classify_bar_freshness(WED_NOON - HOUR, "H1", now_utc=WED_NOON)
    assert verdict == FRESH
    assert age == pytest.approx(HOUR, abs=1)


def test_two_closed_bars_back_is_still_fresh():
    """The 2.5x tolerance exists because the newest bar is already 1-2 old."""
    verdict, _ = classify_bar_freshness(WED_NOON - 2 * HOUR, "H1", now_utc=WED_NOON)
    assert verdict == FRESH


def test_a_stalled_h1_feed_on_a_weekday_is_stale():
    verdict, age = classify_bar_freshness(WED_NOON - 8 * HOUR, "H1", now_utc=WED_NOON)
    assert verdict == STALE
    assert age == pytest.approx(8 * HOUR, abs=1)


def test_the_same_gap_on_a_saturday_is_market_closed_not_stale():
    verdict, _ = classify_bar_freshness(SAT_NOON - 40 * HOUR, "H1", now_utc=SAT_NOON)
    assert verdict == MARKET_CLOSED


def test_the_broker_offset_decides_fresh_versus_stale():
    """The discriminating test for the clock bug.

    One frame, two verdicts: ignoring the broker offset makes a 5-hour-old bar
    look 2 hours old, i.e. fresh. XM runs GMT+3 in summer, so this is the real
    reading, not a contrived one.
    """
    broker_bar_open = WED_NOON - 2 * HOUR          # looks 2h old in UTC

    naive, _ = classify_bar_freshness(broker_bar_open, "H1", now_utc=WED_NOON, offset_sec=0)
    honest, age = classify_bar_freshness(
        broker_bar_open, "H1", now_utc=WED_NOON, offset_sec=3 * HOUR
    )

    assert naive == FRESH, "without the offset the stall is invisible"
    assert honest == STALE, "with the broker offset the same bar is 5h old"
    assert age == pytest.approx(5 * HOUR, abs=1)


def test_an_unrecognised_timeframe_is_never_reported_fresh():
    verdict, age = classify_bar_freshness(WED_NOON - HOUR, "M7", now_utc=WED_NOON)
    assert verdict == FRESHNESS_UNKNOWN
    assert age is None


def test_a_missing_bar_time_is_unknown():
    assert classify_bar_freshness(None, "H1", now_utc=WED_NOON)[0] == FRESHNESS_UNKNOWN
    assert classify_bar_freshness(0, "H1", now_utc=WED_NOON)[0] == FRESHNESS_UNKNOWN


def test_a_bar_stamped_slightly_ahead_is_fresh_not_unknown():
    """Small negative age is ordinary skew; a large one is a broken clock."""
    slight, _ = classify_bar_freshness(WED_NOON + 30, "H1", now_utc=WED_NOON)
    assert slight == FRESH
    wild, _ = classify_bar_freshness(WED_NOON + 10 * HOUR, "H1", now_utc=WED_NOON)
    assert wild == FRESHNESS_UNKNOWN


def test_a_synthetic_frame_is_never_marked_fresh():
    """Fabricated bars have no age to verify, so they must not read as verified."""
    engine = DataFeedEngine(mt5_client=None)
    frame = engine.fetch_rates("EURUSD", "H1", num_bars=50)

    assert frame.attrs["data_source"] == "SYNTHETIC_FALLBACK"
    assert frame.attrs["freshness"] == FRESHNESS_UNKNOWN


def test_a_live_frame_is_stamped_with_a_verdict(monkeypatch):
    """The stamping is wired into the live path, not only available as a helper."""
    import jarvis.market.data_feed as df_mod

    now = time_now()
    # Deliberately ancient, so the verdict cannot be FRESH whatever day the
    # suite runs (a weekend makes it MARKET_CLOSED, a weekday STALE - the point
    # here is that the frame is classified at all).
    opens = [now - 400 * 86400 + i * HOUR for i in range(3)]
    fake = np.array(
        [(int(o), 1.0, 1.1, 0.9, 1.05, 10) for o in opens],
        dtype=[
            ("time", "i8"), ("open", "f8"), ("high", "f8"),
            ("low", "f8"), ("close", "f8"), ("tick_volume", "i8"),
        ],
    )

    class _FakeMT5:
        @staticmethod
        def copy_rates_from_pos(symbol, timeframe, start, count):
            return fake

    class _Client:
        mode = "live"

        @staticmethod
        def resolve_symbol_name(name):
            return name

    monkeypatch.setattr(df_mod, "MT5_AVAILABLE", True)
    monkeypatch.setattr(df_mod, "mt5", _FakeMT5)
    # Deriving the real offset needs a live terminal; pin it.
    monkeypatch.setattr(df_mod, "broker_utc_offset", lambda **kwargs: 0)

    frame = df_mod.DataFeedEngine(mt5_client=_Client()).fetch_rates("EURUSD", "H1", num_bars=3)

    assert frame.attrs["data_source"] == "LIVE_MT5"
    assert frame.attrs["freshness"] in (STALE, MARKET_CLOSED)
    assert frame.attrs["bar_age_sec"] > 300 * 86400


def time_now():
    import time

    return time.time()


# ─── The gate ───────────────────────────────────────────────────────────────

def _stamped(verdict, age):
    import pandas as pd

    frame = pd.DataFrame({"close": [1.0]})
    frame.attrs["freshness"] = verdict
    frame.attrs["bar_age_sec"] = age
    return frame


def test_the_stale_role_is_reported_across_the_timeframe_map():
    from jarvis.market.data_feed import first_stale_frame

    mtf = {
        "macro": _stamped(FRESH, 10.0),
        "primary": _stamped(STALE, 9999.0),
        "timing": _stamped(FRESH, 10.0),
    }
    assert first_stale_frame(mtf) == ("primary", 9999.0)


def test_a_closed_market_does_not_block_and_an_absent_map_is_safe():
    from jarvis.market.data_feed import first_stale_frame

    assert first_stale_frame({"macro": _stamped(MARKET_CLOSED, 1e5)}) == (None, 0.0)
    assert first_stale_frame({"macro": _stamped(FRESHNESS_UNKNOWN, None)}) == (None, 0.0)
    assert first_stale_frame({}) == (None, 0.0)
    assert first_stale_frame(None) == (None, 0.0)


def test_the_orchestrator_refuses_to_decide_on_a_stale_frame(monkeypatch):
    """The gate, not just the stamp.

    A check that is computed and never enforced is decoration. This asserts the
    decision cycle actually refuses, returns the documented refusal shape, and
    does not raise on the way out.
    """
    from jarvis.application.orchestrator import JarvisOrchestrator

    orch = JarvisOrchestrator(mode="paper")
    try:
        monkeypatch.setattr(
            orch.data_feed,
            "fetch_multi_timeframe",
            lambda *a, **k: {"primary": _stamped(STALE, 7200.0), "macro": _stamped(FRESH, 5.0)},
        )
        res = orch.run_cycle_for_symbol("XAUUSD", "SWING")

        assert res["decision"] is None
        assert res["authorized"] is False
        assert res["stale"] is True
        assert "STALE" in res["auth_reason"].upper() or "stale" in res["auth_reason"]
    finally:
        orch.stop()


def test_the_orchestrator_still_decides_on_a_fresh_frame(monkeypatch):
    """Control: the gate must not block a healthy feed."""
    from jarvis.application.orchestrator import JarvisOrchestrator

    orch = JarvisOrchestrator(mode="paper")
    try:
        monkeypatch.setattr(
            orch.data_feed,
            "fetch_multi_timeframe",
            lambda *a, **k: {"primary": _stamped(FRESH, 5.0), "macro": _stamped(FRESH, 5.0)},
        )
        res = orch.run_cycle_for_symbol("XAUUSD", "SWING")

        assert res.get("stale") is not True
        assert res["decision"] is not None
    finally:
        orch.stop()

"""The news calendar must announce when it is fabricated.

WHY THIS EXISTS. `LiveNewsEngine` falls back to a HARDCODED event plan when
both live feeds fail, and `_organize_news_feed` additionally PADS a short
real feed with that plan ("if there are fewer than 4 upcoming events, append
upcoming institutional calendar items"). Neither path marked the result, so
the invented events were indistinguishable from real releases.

That mattered because `MacroAnalyst` scores them: each USD HIGH event whose
`actual` is "Upcoming" costs -5.0. The hardcoded plan carries five of them,
so MACRO scored a constant 40.0 instead of 65.0 -- a -25 swing that is
information-free, applied at every scan, and worth -4.2 points on `ai_score`,
which is a HARD gate (`decision_engine.py:662`) at 70/72/75/78/80/82/85.

Measured against the real feeds: FairEconomy answers 429 "Rate Limited" and
MyFxBook 403 behind a Cloudflare challenge (urllib runs no JavaScript), so
the fabricated path is the NORMAL path, not an edge case.

`tradingview_provider.py` set the precedent this follows: "A labelled
fallback beats an unlabelled fabrication."
"""
from datetime import datetime, timezone, timedelta

import jarvis.market.news as news_mod
from jarvis.market.news import GLOBAL_NEWS_ENGINE
from jarvis.analysts.macro_analyst import MacroAnalyst
from jarvis.data.schemas import (
    MarketContext, StructureContext, LiquidityContext, VolatilityContext,
    MomentumContext, SessionContext, RegimeOutput, MarketRegime,
)


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------

def ctx(symbol="EURUSD") -> MarketContext:
    return MarketContext(
        symbol=symbol,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        current_price=100.0, bid=99.9, ask=100.1,
        structure=StructureContext(bias="NEUTRAL"),
        liquidity=LiquidityContext(),
        volatility=VolatilityContext(),
        momentum=MomentumContext(),
        session=SessionContext(),
        mtf_alignment={},
    )


def regime() -> RegimeOutput:
    return RegimeOutput(primary_regime=MarketRegime.TREND_BULL,
                        probabilities={MarketRegime.TREND_BULL.value: 1.0},
                        confidence=0.9)


def real_event(title="US Nonfarm Payrolls", actual="Upcoming",
               days_ahead=2, currency="USD", impact="HIGH"):
    """A calendar entry that claims to be real (no is_fallback flag)."""
    when = datetime.now(timezone.utc) + timedelta(days=days_ahead)
    return {
        "title": title, "currency": currency, "impact": impact,
        "actual": actual, "forecast": "180K", "previous": "175K",
        "timestamp_iso": when.isoformat(),
    }


def fabricated_event(title="US Core PCE Price Index", days_ahead=2,
                     currency="USD", impact="HIGH"):
    """What the synthetic generator actually produces."""
    return dict(real_event(title=title, days_ahead=days_ahead,
                           currency=currency, impact=impact),
                is_fallback=True, source="synthetic_calendar")


def macro_score(calendar, symbol="EURUSD"):
    rep = MacroAnalyst(news_calendar=calendar).analyze(ctx(symbol), regime())
    return rep


# --------------------------------------------------------------------------
# the generator and the two builders must stamp provenance
# --------------------------------------------------------------------------

def test_the_synthetic_generator_marks_its_own_output():
    """Nothing may be fabricated without saying so at the point of creation."""
    raw = GLOBAL_NEWS_ENGINE._generate_dynamic_calendar()
    assert raw, "the synthetic plan should not be empty"
    for item in raw:
        assert item.get("is_fallback") is True, item.get("title")
        assert item.get("source") == "synthetic_calendar"


def test_the_padding_builder_marks_its_output():
    """`_format_dynamic_item` feeds the '< 4 upcoming' top-up, so it is always fake."""
    raw = GLOBAL_NEWS_ENGINE._generate_dynamic_calendar()[0]
    built = GLOBAL_NEWS_ENGINE._format_dynamic_item(
        raw, datetime.now(timezone.utc))
    assert built["is_fallback"] is True
    assert built["source"] == "synthetic_calendar"


def test_real_events_are_stamped_live():
    out = GLOBAL_NEWS_ENGINE._organize_news_feed([real_event()])
    assert out, "a real event should survive organisation"
    assert out[0]["is_fallback"] is False
    assert out[0]["source"] == "live_feed"


def test_the_organiser_propagates_rather_than_assumes():
    """A synthetic input must not be relabelled live by the organiser."""
    out = GLOBAL_NEWS_ENGINE._organize_news_feed([fabricated_event()])
    assert out
    assert all(x["is_fallback"] is True for x in out), \
        "every event came from a synthetic input"


def test_padding_contaminates_a_real_feed_and_says_so():
    """THE CONTAMINATION CASE.

    One real event is not enough: the organiser tops the feed up to 4 upcoming
    events from the hardcoded plan. Before this fix those six invented events
    were returned unmarked alongside the real one.
    """
    out = GLOBAL_NEWS_ENGINE._organize_news_feed([real_event()])
    flagged = [x for x in out if x["is_fallback"]]
    live = [x for x in out if not x["is_fallback"]]

    assert len(out) > 1, "the top-up should have padded the feed"
    assert len(flagged) >= 1, "the injected events must be marked"
    assert len(live) == 1, "exactly the one real event is real"
    assert live[0]["event"] == "US Nonfarm Payrolls"


def test_a_fully_synthetic_feed_is_entirely_flagged():
    out = GLOBAL_NEWS_ENGINE._organize_news_feed(
        GLOBAL_NEWS_ENGINE._generate_dynamic_calendar())
    assert out
    assert all(x["is_fallback"] for x in out)


# --------------------------------------------------------------------------
# MacroAnalyst must not score fabricated events
# --------------------------------------------------------------------------

def test_macro_does_not_penalise_on_fabricated_events():
    """The regression that motivated the whole change: -25 of pure noise."""
    fake = [fabricated_event() for _ in range(5)]
    rep = macro_score(fake)
    clean = macro_score([])
    assert rep.score == clean.score, (
        f"fabricated events moved the score to {rep.score}; "
        f"with no news it is {clean.score}")


def test_macro_still_penalises_a_REAL_upcoming_event():
    """Non-vacuity: the rule is only wrong on fabricated data, not in general."""
    real = macro_score([real_event(actual="Upcoming")])
    clean = macro_score([])
    assert real.score == clean.score - 5.0, (
        "a genuine upcoming high-impact USD event must still cost 5.0")


def test_macro_reports_that_it_discarded_synthetic_events():
    rep = macro_score([fabricated_event(), real_event()])
    joined = " ".join(rep.risk_factors)
    assert "synthetic" in joined.lower(), rep.risk_factors


def test_macro_treats_unflagged_items_as_real():
    """Back-compatible: a caller passing a plain calendar gets old behaviour."""
    rep = macro_score([real_event()])
    assert not any("synthetic" in r.lower() for r in rep.risk_factors)


def test_a_fabricated_shock_cannot_set_the_bias():
    """The hardcoded plan's `actual` values can imply a direction. It must not."""
    shock = dict(fabricated_event(title="US GDP"),
                 actual="1.0", forecast="3.0", previous="3.0")
    rep = macro_score([shock])
    assert rep.bias == "NEUTRAL", "a fabricated deviation set the bias"


# --------------------------------------------------------------------------
# the warning must fire, and must not flood
# --------------------------------------------------------------------------

def test_the_fabrication_warning_is_logged(caplog):
    with caplog.at_level("WARNING", logger="HM_LiveNewsEngine"):
        news_mod.GLOBAL_NEWS_ENGINE._last_fabrication_warn = 0.0
        GLOBAL_NEWS_ENGINE._organize_news_feed(
            GLOBAL_NEWS_ENGINE._generate_dynamic_calendar())
    assert any("SYNTHETIC" in r.message for r in caplog.records), \
        "the fabricated calendar must announce itself"


def test_the_warning_is_rate_limited(caplog):
    """The news page polls; the same fact must not be logged on every call."""
    engine = GLOBAL_NEWS_ENGINE
    engine._last_fabrication_warn = 0.0
    with caplog.at_level("WARNING", logger="HM_LiveNewsEngine"):
        for _ in range(5):
            engine._organize_news_feed(engine._generate_dynamic_calendar())
    warnings = [r for r in caplog.records if "SYNTHETIC" in r.message]
    assert len(warnings) == 1, f"expected one warning, got {len(warnings)}"


def test_no_warning_when_the_feed_needs_no_padding(caplog):
    """A feed with >= 4 upcoming events is not topped up, so it stays purely real.

    Note what a SINGLE real event does: the '< 4 upcoming' branch pads it with
    six synthetic events and the warning fires. That is the honest behaviour --
    the feed really is mostly fabricated -- so this test has to supply enough
    real events to avoid the padding rather than assert the warning away.
    """
    engine = GLOBAL_NEWS_ENGINE
    engine._last_fabrication_warn = 0.0
    feed = [real_event(title=f"Real Event {i}", days_ahead=i + 1) for i in range(5)]
    with caplog.at_level("WARNING", logger="HM_LiveNewsEngine"):
        out = engine._organize_news_feed(feed)
    assert not [r for r in caplog.records if "SYNTHETIC" in r.message], \
        "a feed that needed no padding must not warn"
    assert all(not x["is_fallback"] for x in out), \
        "and none of its items may be flagged fabricated"


def test_one_real_event_is_still_mostly_fabricated(caplog):
    """The padding rule means 'we have news' can mean 'we have 1/7 of it'."""
    engine = GLOBAL_NEWS_ENGINE
    engine._last_fabrication_warn = 0.0
    with caplog.at_level("WARNING", logger="HM_LiveNewsEngine"):
        out = engine._organize_news_feed([real_event()])
    n_fab = sum(1 for x in out if x["is_fallback"])
    assert n_fab == len(out) - 1, (
        f"expected exactly one real item in {len(out)}, got {len(out) - n_fab}")
    assert any("PARTLY fabricated" in r.message for r in caplog.records)


# --------------------------------------------------------------------------
# the post-news stop-hunt boost must not fire on fabricated events
# --------------------------------------------------------------------------

def _past_event(minutes_ago=1, fallback=False):
    """A released HIGH-impact USD event, inside the 45-minute lookback."""
    ev = {
        "event": "US S&P Global Composite Flash PMI",
        "currency": "USD", "impact": "HIGH",
        "actual": "51.8", "forecast": "51.4", "previous": "51.1",
        "is_past": True, "is_upcoming": False, "is_live": False,
        "diff_seconds": -60.0 * minutes_ago,
        "affected_pairs": ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD"],
    }
    if fallback:
        ev.update(is_fallback=True, source="synthetic_calendar")
    return ev


def _reaction(monkeypatch, events, symbol="EURUSD"):
    engine = GLOBAL_NEWS_ENGINE
    monkeypatch.setattr(engine, "get_news_calendar", lambda *a, **k: events)
    return engine.evaluate_post_news_sweep_reaction(
        symbol=symbol, sweep_detected=True, sweep_type="SELL_SIDE",
        sweep_magnitude_pips=12.0)


def test_a_fabricated_event_cannot_grant_the_stop_hunt_boost(monkeypatch):
    """`decision_engine` hands out +8.0 ai_score and +0.20 conviction here.

    A hardcoded event must not be able to buy conviction on a live path.
    """
    res = _reaction(monkeypatch, [_past_event(fallback=True)])
    assert res["news_reversal_setup"] is False, res
    assert res["conviction_boost"] == 0.0


def test_a_real_recent_event_still_grants_the_boost(monkeypatch):
    """Non-vacuity: the mechanism must keep working on genuine releases."""
    res = _reaction(monkeypatch, [_past_event(fallback=False)])
    assert res["news_reversal_setup"] is True, res
    assert res["conviction_boost"] == 0.20


def test_an_old_real_event_still_falls_outside_the_lookback(monkeypatch):
    """Pins the window itself, so the provenance guard cannot mask a broken one."""
    res = _reaction(monkeypatch, [_past_event(minutes_ago=60, fallback=False)])
    assert res["news_reversal_setup"] is False, res

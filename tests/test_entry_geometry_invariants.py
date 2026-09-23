"""
Trade-entry geometry invariants for ``DynamicRiskAndLevelsEngine.calculate_levels``.

These tests protect the properties that decide whether an entry is survivable, and
they double as the written record of the three-track audit (docs/AUDIT-3-TRACKS-2026-09-23.md).

Two of them are deliberately *characterisation* tests: they pin behaviour that the
audit identified as defective rather than behaviour we endorse.

1. ``test_stop_floor_is_only_point_one_atr`` pins the current floor. The audit measured
   a median live stop of 0.21x ATR and found a k x ATR stop is taken out by the bar's
   own adverse range 16.9% of the time at k=0.85. Raising the floor is Phase 5 and is
   intentionally NOT done here -- the mandate was to evaluate before changing entry
   logic, and the incumbent signal has no measured edge yet (DSR > 0.95 met by 0/20).

2. ``test_stop_survives_the_bars_own_adverse_range`` is ``xfail``. It asserts the Phase 5
   target (>= 1.11 x ATR). It fails today, so it is expected-fail and keeps the suite
   green; when Phase 5 lands it will XPASS and this file must be updated.
"""

from datetime import datetime, timezone

import pytest

from jarvis.data.schemas import (
    LiquidityContext,
    MarketContext,
    MarketRegime,
    MomentumContext,
    RegimeOutput,
    SessionContext,
    StructureContext,
    VolatilityContext,
)
from jarvis.intelligence.dynamic_levels import DynamicRiskAndLevelsEngine


def make_context(symbol="XAUUSD", price=2400.0, bid=2399.8, ask=2400.2,
                 atr=10.0, spread_pips=2.0, bias="BULLISH",
                 demand=(2392.0, 2394.0), supply=(2430.0, 2435.0)):
    """A minimal but fully observed MarketContext, mirroring tests/test_dynamic_levels_and_refactoring.py."""
    return MarketContext(
        symbol=symbol,
        timestamp=datetime.now(timezone.utc),
        current_price=price,
        bid=bid,
        ask=ask,
        structure=StructureContext(
            bias=bias,
            demand_zone=demand,
            supply_zone=supply,
            order_blocks=[{"type": "BULLISH_ORDER_BLOCK", "low": 2393.0, "high": 2396.0}],
            fair_value_gaps=[{"type": "BEARISH_FVG", "bottom": 2415.0, "top": 2418.0}],
        ),
        liquidity=LiquidityContext(buy_side_liquidity=2435.0, sell_side_liquidity=2390.0),
        volatility=VolatilityContext(atr=atr, current_spread_pips=spread_pips, state="NORMAL"),
        momentum=MomentumContext(trend_score=60.0, adx=30.0),
        session=SessionContext(is_prime_session=True),
    )


def bull_regime():
    return RegimeOutput(primary_regime=MarketRegime.TREND_BULL, probabilities={}, confidence=0.85)


def bear_regime():
    return RegimeOutput(primary_regime=MarketRegime.TREND_BEAR, probabilities={}, confidence=0.85)


@pytest.fixture()
def engine():
    return DynamicRiskAndLevelsEngine()


# ── Entry anchoring: entries are the raw market quote, not a support/resistance level ──

def test_buy_entry_is_the_observed_ask_not_a_support_level(engine):
    """
    Audit finding: entries are NOT snapped to support/resistance. S/R objects are used
    only to *build* the stop and target. Pinned here so nobody assumes otherwise.
    """
    ctx = make_context()  # demand zone sits 6.20 below the quote
    levels = engine.calculate_levels(ctx, bull_regime(), tentative_bias="BUY")

    assert levels["bias"] == "BUY"
    assert levels["entry_price"] == pytest.approx(ctx.ask, abs=1e-9)
    # The entry is the quote, NOT either edge of the demand zone.
    assert levels["entry_price"] not in (ctx.structure.demand_zone[0], ctx.structure.demand_zone[1])


def test_sell_entry_is_the_observed_bid(engine):
    ctx = make_context(bias="BEARISH", supply=(2390.0, 2392.0), demand=(2380.0, 2382.0))
    levels = engine.calculate_levels(ctx, bear_regime(), tentative_bias="SELL")

    assert levels["bias"] == "SELL"
    assert levels["entry_price"] == pytest.approx(ctx.bid, abs=1e-9)


# ── Stop side, sign and self-consistency ──

@pytest.mark.parametrize("bias,regime_fn", [("BUY", bull_regime), ("SELL", bear_regime)])
def test_stop_is_on_the_correct_side_and_strictly_positive(engine, bias, regime_fn):
    ctx = make_context(bias="BULLISH" if bias == "BUY" else "BEARISH",
                       supply=(2390.0, 2392.0) if bias == "SELL" else (2430.0, 2435.0))
    levels = engine.calculate_levels(ctx, regime_fn(), tentative_bias=bias)

    entry, sl = levels["entry_price"], levels["sl_price"]
    assert sl > 0.0
    assert sl != entry
    if bias == "BUY":
        assert sl < entry
    else:
        assert sl > entry


@pytest.mark.parametrize("style", ["SCALP", "DAY_TRADING", "SWING"])
def test_risk_dist_describes_the_same_level_as_sl_price(engine, style):
    """
    Regression guard for the ~7x risk blow-up: risk_dist was once lifted to the floor
    while sl_price kept the tight structural stop, so the sizer priced risk off a
    0.7-pip stop and the post-fill re-anchor re-applied a floored 5-pip distance to an
    already-filled position. sl_price and risk_dist must describe the same level.
    """
    ctx = make_context()
    levels = engine.calculate_levels(ctx, bull_regime(), tentative_bias="BUY", trade_style=style)

    assert levels["risk_dist"] == pytest.approx(
        abs(levels["entry_price"] - levels["sl_price"]), abs=1e-9
    )
    assert levels["risk_dist"] > 0.0


@pytest.mark.parametrize("style", ["SCALP", "DAY_TRADING", "SWING"])
def test_tp_is_on_the_correct_side_and_rr_is_self_consistent(engine, style):
    ctx = make_context()
    levels = engine.calculate_levels(ctx, bull_regime(), tentative_bias="BUY", trade_style=style)

    entry, tp = levels["entry_price"], levels["tp_price"]
    assert tp > entry, "a BUY target must sit above the entry"
    # rr_ratio is rounded to 2 dp on the way out, so compare at that granularity.
    assert levels["rr_ratio"] == pytest.approx(
        abs(tp - entry) / levels["risk_dist"], abs=0.01
    )
    assert levels["rr_ratio"] >= 1.0, "never pay more risk than the target offers"


# ── The stop floor, as it stands today ──

@pytest.mark.parametrize("style", ["SCALP", "DAY_TRADING", "SWING"])
def test_stop_floor_is_at_least_three_times_the_quoted_spread(engine, style):
    """The floor exists so the spread stays a small share of the risk budget."""
    ctx = make_context()
    levels = engine.calculate_levels(ctx, bull_regime(), tentative_bias="BUY", trade_style=style)

    spread_dist = ctx.ask - ctx.bid
    # sl_price is rounded to the symbol's digits, so allow half a tick of slack.
    assert levels["risk_dist"] >= 3.0 * spread_dist - 5e-4


@pytest.mark.parametrize("style", ["SCALP", "DAY_TRADING", "SWING"])
def test_stop_floor_is_only_point_one_atr(engine, style):
    """
    CHARACTERISATION -- this is the defect, not the goal.

    dynamic_levels.py:273 and :404 both floor the stop at
    ``max(3 * spread, 0.10 * atr)``. 0.10 x ATR is inside a single bar's noise. The
    audit's live sample had a median stop of 0.21 x ATR, with 75% below 0.5 x ATR.
    Phase 5 raises this to 1.11 x ATR once the standalone backtest earns it.
    """
    ctx = make_context()
    levels = engine.calculate_levels(ctx, bull_regime(), tentative_bias="BUY", trade_style=style)

    atr = ctx.volatility.atr
    assert levels["risk_dist"] >= 0.10 * atr - 5e-4, "floor must at least be applied"


@pytest.mark.xfail(
    reason="KNOWN DEFECT (audit Track 1): the stop floor is 0.10x ATR, so a SCALP stop "
           "sits at ~0.20x ATR and is taken out by the bar's own adverse range. "
           "Phase 5 raises the floor to 1.11x ATR; delete this xfail when it lands.",
    strict=False,
)
def test_stop_survives_the_bars_own_adverse_range(engine):
    """
    The Phase 5 target. A stop of k x ATR is consumed by the entry bar's own adverse
    range 16.9% of the time at k=0.85 and 10% of the time at k=1.11 -- so 1.11 is the
    threshold where noise stops being the dominant cause of loss.
    """
    ctx = make_context()
    levels = engine.calculate_levels(ctx, bull_regime(), tentative_bias="BUY", trade_style="SCALP")

    assert levels["risk_dist"] >= 1.11 * ctx.volatility.atr


# ── Refuse rather than fabricate ──

def test_unobserved_price_yields_no_levels(engine):
    """
    A zero/NaN/absent price must produce no order. Without this guard the engine turned
    a price nobody observed into a real-looking order: measured for BTCUSD, entry 0.02
    with a stop of -0.04, from which the sizer returned 100 lots.
    """
    ctx = make_context(price=0.0, bid=0.0, ask=0.0)
    levels = engine.calculate_levels(ctx, bull_regime(), tentative_bias="BUY")

    assert levels["bias"] == "HOLD"
    assert levels["data_unavailable"] is True
    assert levels["entry_price"] == 0.0
    assert levels["sl_price"] == 0.0
    assert levels["tp_price"] == 0.0


def test_negative_price_is_refused(engine):
    """A price must be finite AND > 0 -- _is_finite(0.0) is True, and 0.0 passed every gate."""
    ctx = make_context(price=-1.0, bid=-1.0, ask=-1.0)
    levels = engine.calculate_levels(ctx, bull_regime(), tentative_bias="BUY")

    assert levels["bias"] == "HOLD"
    assert levels["data_unavailable"] is True

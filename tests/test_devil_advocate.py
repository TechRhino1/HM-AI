"""Round 26 — `jarvis/analysts/devil_advocate.py`, the adversarial gate.

`decision_engine:644` gates every trade on
`"Devil Adversarial Guard": penalty_score <= max_devil_penalty` (default 43.0),
and `penalty_score` is also written into the ML feature vector
(`decision_engine:192`) and the scan columns as `adversarial_penalty`
(`signal_scan.py:303`). Round 24 proved the *fallback* passes that gate; this
tests the critic that actually runs.

Pure and offline: the correlation engine is injected as a fake.
"""
import pytest
from datetime import datetime, timezone

from jarvis.data.schemas import (
    MarketContext, StructureContext, LiquidityContext, VolatilityContext,
    MomentumContext, SessionContext, RegimeOutput, MarketRegime, DevilAdvocateReport,
)
from jarvis.data.symbol_registry import resolve as resolve_symbol
from jarvis.analysts.devil_advocate import DevilAdvocateAnalyst

GATE = 43.0  # decision_engine `max_devil_penalty` default


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------

class FakeCorr:
    """Stands in for DynamicCorrelationEngine; records every lookup."""

    def __init__(self, value=0.0):
        self.value = value
        self.calls = []

    def get_correlation(self, a, b):
        self.calls.append((a, b))
        return self.value


def ctx(symbol="XAUUSD", price=100.0, structure=None, liquidity=None,
        volatility=None, momentum=None, session=None, mtf=None,
        order_flow=None, quality=100.0) -> MarketContext:
    return MarketContext(
        symbol=symbol,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        current_price=price,
        bid=price - 0.1,
        ask=price + 0.1,
        structure=structure or StructureContext(bias="NEUTRAL"),
        liquidity=liquidity or LiquidityContext(),
        volatility=volatility or VolatilityContext(),
        momentum=momentum or MomentumContext(),
        session=session or SessionContext(),
        mtf_alignment=mtf if mtf is not None else {},
        order_flow=order_flow if order_flow is not None else {},
        context_quality=quality,
    )


def regime(name=MarketRegime.TREND_BULL, conf=0.9, transition=False) -> RegimeOutput:
    return RegimeOutput(primary_regime=name, probabilities={name.value: 1.0},
                        confidence=conf, regime_transition=transition)


PRIME = SessionContext(current_session="LONDON", is_prime_session=True)
OFF = SessionContext(current_session="OFF_HOURS", is_prime_session=False)


def critique(bias="BUY", corr=None, **kw) -> DevilAdvocateReport:
    return DevilAdvocateAnalyst(correlation_engine=corr or FakeCorr()).critique_opportunity(
        ctx(**kw), regime(), bias)


# --------------------------------------------------------------------------
# contract
# --------------------------------------------------------------------------

class TestContract:

    @pytest.mark.parametrize("bias,counter", [("BUY", "BEARISH"), ("SELL", "BULLISH"),
                                              ("buy", "BEARISH"), ("Buy", "BEARISH")])
    def test_the_counter_bias_is_the_opposite_side(self, bias, counter):
        assert critique(bias, session=PRIME).counter_bias == counter

    @pytest.mark.parametrize("bias", ["BULLISH", "LONG", "", "NEUTRAL", "HOLD", "buy "] )
    def test_only_the_literal_word_buy_means_long(self, bias):
        """`is_buy = proposed_bias.upper() == "BUY"` — anything else is treated
        as a SELL and critiqued from the wrong side. All four production callers
        happen to pass "BUY"/"SELL", so this is a trap waiting to be sprung.
        Note "buy " (trailing space) is *not* stripped."""
        r = critique(bias, session=PRIME)
        assert r.counter_bias == "BULLISH", f"{bias!r} was read as a buy"

    @pytest.mark.parametrize("bias", ["BUY", "SELL"])
    def test_penalty_stays_within_0_and_50(self, bias):
        worst = ctx(
            structure=StructureContext(bias="NEUTRAL", lower_highs=True, lower_lows=True,
                                       higher_highs=True, higher_lows=True,
                                       discount_premium_zone="PREMIUM", choch=True,
                                       bos=True, demand_zone=(1.0, 2.0),
                                       supply_zone=(300.0, 400.0)),
            liquidity=LiquidityContext(equal_highs=True, equal_lows=True,
                                       sweep_detected=True, sweep_type="BUY_SIDE",
                                       buy_side_liquidity=1.0, sell_side_liquidity=99.0),
            volatility=VolatilityContext(state="EXTREME", is_excessive_spread=True,
                                         current_spread_pips=99.0),
            momentum=MomentumContext(trend_score=-100, rsi=99.0, adx=99.0,
                                     minus_di=90.0, plus_di=1.0,
                                     divergence="BEARISH_DIVERGENCE"),
            session=OFF, mtf={"H4": "BEARISH", "D1": "BEARISH"},
            order_flow={"absorption_trap": "SELLER_ABSORPTION_TRAP", "delta_score": -100.0},
        )
        r = DevilAdvocateAnalyst(correlation_engine=FakeCorr(1.0)).critique_opportunity(
            worst, regime(transition=True), bias)
        assert 0.0 <= r.penalty_score <= 50.0

    @pytest.mark.parametrize("bias", ["BUY", "SELL"])
    def test_the_coefficient_stays_within_0p20_and_1p0(self, bias):
        r = critique(bias, session=PRIME)
        assert 0.20 <= r.invalidation_risk_coefficient <= 1.0

    def test_symbol_is_propagated(self):
        assert critique("BUY", symbol="GBPJPY").symbol == "GBPJPY"

    def test_execution_time_is_recorded(self):
        assert critique("BUY").execution_time_ms >= 0.0

    @pytest.mark.parametrize("bias,word", [("BUY", "demand zone"), ("SELL", "supply zone")])
    def test_two_invalidation_triggers_always_exist(self, bias, word):
        r = critique(bias, structure=StructureContext(bias="NEUTRAL",
                                                      demand_zone=(90.0, 95.0),
                                                      supply_zone=(105.0, 110.0)))
        assert len(r.invalidation_triggers) == 2
        assert any(word in t for t in r.invalidation_triggers)
        assert any("displacement" in t for t in r.invalidation_triggers)

    def test_a_clean_prime_session_context_is_not_penalised_at_all(self):
        r = critique("BUY", session=PRIME)
        assert r.penalty_score == 0.0
        assert r.threats_detected == []
        assert r.liquidity_traps == []
        assert r.invalidation_risk_coefficient == 1.0

    def test_the_default_context_is_penalised_for_off_hours_alone(self):
        """`SessionContext` defaults to `is_prime_session=False`, so the floor
        on any default-built context is 8.0, not 0.0."""
        r = critique("BUY")
        assert r.penalty_score == 8.0
        assert any("off-hours" in t for t in r.threats_detected)


# --------------------------------------------------------------------------
# critique confidence — a data-quality measure, not a critique-strength one
# --------------------------------------------------------------------------

class TestCritiqueConfidence:

    @pytest.mark.parametrize("quality,expected", [(100.0, 1.0), (55.0, 0.55),
                                                  (40.0, 0.40), (20.0, 0.40), (0.0, 0.40)])
    def test_it_is_the_context_quality_clamped(self, quality, expected):
        assert critique("BUY", session=PRIME, quality=quality).critique_confidence == expected

    def test_it_ignores_how_many_threats_were_found(self):
        """🔴 `critique_confidence` is derived only from `context_quality`, so a
        critic that found eight threats on a pristine context reports 1.0 — the
        same as one that found none. It measures the *input*, not the critique.
        (Round 24 set the fallback's confidence to 0.0 for the opposite reason:
        it had been claiming 1.0 for a critique that never ran.)"""
        hostile = ctx(
            structure=StructureContext(bias="NEUTRAL", lower_highs=True, lower_lows=True,
                                       discount_premium_zone="PREMIUM"),
            momentum=MomentumContext(trend_score=-100, rsi=99.0, adx=99.0,
                                     minus_di=90.0, plus_di=1.0,
                                     divergence="BEARISH_DIVERGENCE"),
            volatility=VolatilityContext(state="EXTREME", is_excessive_spread=True,
                                         current_spread_pips=99.0),
            session=OFF,
        )
        r = DevilAdvocateAnalyst(correlation_engine=FakeCorr()).critique_opportunity(
            hostile, regime(transition=True), "BUY")
        assert len(r.threats_detected) >= 6
        assert r.critique_confidence == 1.0


# --------------------------------------------------------------------------
# 1. structural & momentum threats
# --------------------------------------------------------------------------

class TestTrendAndMomentum:

    def run(self, bias="BUY", **mom):
        return critique(bias, session=PRIME, momentum=MomentumContext(**mom))

    @pytest.mark.parametrize("ts", [-100, -21])
    def test_a_buy_against_a_falling_trend_scores_15(self, ts):
        assert self.run("BUY", trend_score=ts).penalty_score == 15.0

    @pytest.mark.parametrize("ts", [-20, 0, 20, 100])
    def test_a_buy_is_not_penalised_at_or_above_minus_20(self, ts):
        assert self.run("BUY", trend_score=ts).penalty_score == 0.0

    @pytest.mark.parametrize("ts", [21, 100])
    def test_a_sell_against_a_rising_trend_scores_15(self, ts):
        assert self.run("SELL", trend_score=ts).penalty_score == 15.0

    @pytest.mark.parametrize("ts", [20, 0, -20, -100])
    def test_a_sell_is_not_penalised_at_or_below_20(self, ts):
        assert self.run("SELL", trend_score=ts).penalty_score == 0.0

    def test_a_buy_into_an_overbought_rsi_scores_12(self):
        assert self.run("BUY", rsi=70.1).penalty_score == 12.0

    def test_rsi_of_exactly_70_is_not_overextended_for_a_buy(self):
        assert self.run("BUY", rsi=70.0).penalty_score == 0.0

    def test_a_sell_into_an_oversold_rsi_scores_12(self):
        assert self.run("SELL", rsi=29.9).penalty_score == 12.0

    def test_rsi_of_exactly_30_is_not_overextended_for_a_sell(self):
        assert self.run("SELL", rsi=30.0).penalty_score == 0.0

    def test_a_buy_with_a_dominant_minus_di_scores_14(self):
        r = self.run("BUY", adx=25.1, minus_di=30.0, plus_di=20.0)
        assert r.penalty_score == 14.0

    def test_adx_of_exactly_25_is_not_strong(self):
        assert self.run("BUY", adx=25.0, minus_di=30.0, plus_di=20.0).penalty_score == 0.0

    def test_a_di_gap_of_exactly_8_is_not_dominant(self):
        assert self.run("BUY", adx=30.0, minus_di=28.0, plus_di=20.0).penalty_score == 0.0

    def test_a_sell_with_a_dominant_plus_di_scores_14(self):
        assert self.run("SELL", adx=30.0, plus_di=30.0, minus_di=20.0).penalty_score == 14.0

    def test_a_sell_di_gap_of_exactly_8_is_not_dominant(self):
        assert self.run("SELL", adx=30.0, plus_di=28.0, minus_di=20.0).penalty_score == 0.0

    def test_a_sell_di_gap_under_8_is_not_dominant(self):
        assert self.run("SELL", adx=30.0, plus_di=25.0, minus_di=20.0).penalty_score == 0.0

    def test_a_buy_di_gap_under_8_is_not_dominant(self):
        assert self.run("BUY", adx=30.0, minus_di=25.0, plus_di=20.0).penalty_score == 0.0

    @pytest.mark.parametrize("div", ["BEARISH_DIVERGENCE"])
    def test_a_buy_into_bearish_divergence_scores_14(self, div):
        assert self.run("BUY", divergence=div).penalty_score == 14.0

    def test_a_sell_into_bullish_divergence_scores_14(self):
        assert self.run("SELL", divergence="BULLISH_DIVERGENCE").penalty_score == 14.0

    @pytest.mark.parametrize("bias,safe,adverse", [
        ("BUY", "BULLISH_DIVERGENCE", "BEARISH_DIVERGENCE"),
        ("SELL", "BEARISH_DIVERGENCE", "BULLISH_DIVERGENCE"),
    ])
    def test_only_the_adverse_divergence_counts(self, bias, safe, adverse):
        assert self.run(bias, divergence=safe).penalty_score == 0.0
        assert self.run(bias, divergence=adverse).penalty_score == 14.0
        assert self.run(bias, divergence="NONE").penalty_score == 0.0


class TestStructureAndZones:

    def run(self, bias="BUY", **st):
        return critique(bias, session=PRIME,
                        structure=StructureContext(bias="NEUTRAL", **st))

    def test_a_buy_in_the_premium_zone_scores_10(self):
        assert self.run("BUY", discount_premium_zone="PREMIUM").penalty_score == 10.0

    def test_a_sell_in_the_discount_zone_scores_10(self):
        assert self.run("SELL", discount_premium_zone="DISCOUNT").penalty_score == 10.0

    @pytest.mark.parametrize("bias,zone", [("BUY", "DISCOUNT"), ("SELL", "PREMIUM"),
                                           ("BUY", "EQUILIBRIUM"), ("SELL", "EQUILIBRIUM")])
    def test_the_favourable_zone_is_not_penalised(self, bias, zone):
        assert self.run(bias, discount_premium_zone=zone).penalty_score == 0.0

    def test_a_buy_into_lower_highs_and_lows_scores_18(self):
        assert self.run("BUY", lower_highs=True, lower_lows=True).penalty_score == 18.0

    def test_a_sell_into_higher_highs_and_lows_scores_18(self):
        assert self.run("SELL", higher_highs=True, higher_lows=True).penalty_score == 18.0

    @pytest.mark.parametrize("bias,kw", [
        ("BUY", {"lower_highs": True}), ("BUY", {"lower_lows": True}),
        ("SELL", {"higher_highs": True}), ("SELL", {"higher_lows": True}),
    ])
    def test_half_a_structure_is_not_a_broken_trend(self, bias, kw):
        assert self.run(bias, **kw).penalty_score == 0.0

    def test_a_buy_into_a_bearish_h4_scores_12(self):
        r = self.run.__self__  # keep linters quiet
        rep = critique("BUY", session=PRIME, mtf={"H4": "BEARISH"})
        assert rep.penalty_score == 12.0
        assert any("BEARISH" in t for t in rep.threats_detected)

    def test_a_buy_into_a_bearish_d1_scores_12(self):
        assert critique("BUY", session=PRIME, mtf={"D1": "BEARISH"}).penalty_score == 12.0

    def test_a_sell_into_a_bullish_h4_scores_12(self):
        assert critique("SELL", session=PRIME, mtf={"H4": "BULLISH"}).penalty_score == 12.0

    @pytest.mark.parametrize("bias,aligned", [("BUY", "BULLISH"), ("SELL", "BEARISH")])
    def test_an_aligned_higher_timeframe_is_no_threat(self, bias, aligned):
        assert critique(bias, session=PRIME,
                        mtf={"H4": aligned, "D1": aligned}).penalty_score == 0.0

    @pytest.mark.parametrize("mtf", [{}, {"H1": "BEARISH", "M15": "BEARISH"}])
    def test_an_absent_h4_is_not_a_conflict(self, mtf):
        """Unlike `structure_analyst` (fixed in round 25), this one already
        compares `.get(...) == "BEARISH"`, which is False for a missing key."""
        assert critique("BUY", session=PRIME, mtf=mtf).penalty_score == 0.0

    def test_the_conflict_message_names_h4_even_when_d1_is_the_conflict(self):
        """🔴 `macro_bias = mtf.get("H4") or mtf.get("D1")` prefers H4 whenever
        it is truthy — so when D1 is the frame in conflict and H4 is merely
        NEUTRAL, the threat reads "Macro timeframe is NEUTRAL": it names a frame
        that is not in conflict and hides the bearish one that triggered it."""
        r = critique("BUY", session=PRIME, mtf={"H4": "NEUTRAL", "D1": "BEARISH"})
        assert r.penalty_score == 12.0
        assert any("NEUTRAL" in t for t in r.threats_detected)
        assert not any("BEARISH" in t for t in r.threats_detected)

    def test_the_message_falls_back_to_d1_when_h4_is_absent(self):
        r = critique("BUY", session=PRIME, mtf={"D1": "BEARISH"})
        assert any("BEARISH" in t for t in r.threats_detected)


# --------------------------------------------------------------------------
# 2. liquidity
# --------------------------------------------------------------------------

class TestLiquidity:

    def run(self, bias="BUY", **liq):
        return critique(bias, session=PRIME, liquidity=LiquidityContext(**liq))

    def test_a_buy_with_unswept_equal_lows_scores_8_as_a_trap(self):
        r = self.run("BUY", equal_lows=True)
        assert r.penalty_score == 8.0
        assert any("Equal Lows" in t for t in r.liquidity_traps)
        assert r.threats_detected == []

    def test_a_sell_with_unswept_equal_highs_scores_8_as_a_trap(self):
        r = self.run("SELL", equal_highs=True)
        assert r.penalty_score == 8.0
        assert any("Equal Highs" in t for t in r.liquidity_traps)

    @pytest.mark.parametrize("bias,kw", [("BUY", {"equal_highs": True}),
                                         ("SELL", {"equal_lows": True})])
    def test_the_other_side_is_the_stop_hunt_target(self, bias, kw):
        """A BUY is threatened by lows resting underneath, not by highs."""
        assert self.run(bias, **kw).penalty_score == 0.0

    def test_the_trap_names_the_zone_on_the_threatened_side(self):
        st = StructureContext(bias="NEUTRAL", demand_zone=(90.0, 95.0),
                              supply_zone=(105.0, 110.0))
        r = critique("BUY", session=PRIME, structure=st,
                     liquidity=LiquidityContext(equal_lows=True))
        assert any("90.0" in t for t in r.liquidity_traps)

    # -- the sweep check -----------------------------------------------------

    def test_the_sweep_branch_fires_for_the_strings_it_looks_for(self):
        r = self.run("BUY", sweep_detected=True, sweep_type="BUY_SIDE", sweep_magnitude=7.0)
        assert r.penalty_score == 10.0
        assert any("BUY_SIDE" in t for t in r.threats_detected)

    @pytest.mark.parametrize("sweep_type", ["BULLISH_SWEEP", "BEARISH_SWEEP", "NONE"])
    def test_those_strings_are_never_the_ones_the_platform_produces(self, sweep_type):
        """🔴 DEAD BRANCH. `LiquidityContext.sweep_type` is documented as
        "BULLISH_SWEEP"/"BEARISH_SWEEP" (schemas.py:94) and those are the only
        values `liquidity.py:108,115` ever assigns. The critic tests for
        "BUY_SIDE"/"SELL_SIDE" — a vocabulary that exists only in
        `news.py:720`, for an unrelated post-news narrative. So a fresh sweep
        against your trade is never penalised: all three real values score 0."""
        r = self.run("BUY", sweep_detected=True, sweep_type=sweep_type, sweep_magnitude=7.0)
        assert r.penalty_score == 0.0
        assert r.threats_detected == []

    @pytest.mark.parametrize("bias", ["BUY", "SELL"])
    def test_and_so_a_sweep_never_penalises_either_side(self, bias):
        for st in ("BULLISH_SWEEP", "BEARISH_SWEEP"):
            assert self.run(bias, sweep_detected=True, sweep_type=st).penalty_score == 0.0

    # -- liquidity imbalance -------------------------------------------------

    def test_a_buy_facing_heavy_resting_sell_volume_scores_6(self):
        r = self.run("BUY", buy_side_liquidity=1.0, sell_side_liquidity=3.0)
        assert r.penalty_score == 6.0
        assert any("imbalance" in t for t in r.liquidity_traps)

    def test_a_sell_facing_heavy_resting_buy_volume_scores_6(self):
        assert self.run("SELL", buy_side_liquidity=3.0,
                        sell_side_liquidity=1.0).penalty_score == 6.0

    def test_a_ratio_of_exactly_2_is_not_severe(self):
        assert self.run("BUY", buy_side_liquidity=1.0,
                        sell_side_liquidity=2.0).penalty_score == 0.0

    @pytest.mark.parametrize("buy,sell", [(0.0, 5.0), (5.0, 0.0), (0.0, 0.0)])
    def test_a_one_sided_book_is_not_an_imbalance(self, buy, sell):
        """The guard requires both sides > 0, so an empty book never trips it —
        including the default context, where both are 0.0."""
        assert self.run("BUY", buy_side_liquidity=buy,
                        sell_side_liquidity=sell).penalty_score == 0.0


# --------------------------------------------------------------------------
# 2.5 order flow
# --------------------------------------------------------------------------

class TestOrderFlow:

    @pytest.mark.parametrize("bias,trap", [("BUY", "SELLER_ABSORPTION_TRAP"),
                                           ("SELL", "BUYER_ABSORPTION_TRAP")])
    def test_the_adverse_absorption_trap_scores_15(self, bias, trap):
        r = critique(bias, session=PRIME, order_flow={"absorption_trap": trap})
        assert r.penalty_score == 15.0
        assert any("ABSORPTION" in t for t in r.threats_detected)

    @pytest.mark.parametrize("bias,trap", [("BUY", "BUYER_ABSORPTION_TRAP"),
                                           ("SELL", "SELLER_ABSORPTION_TRAP")])
    def test_the_supportive_absorption_trap_is_no_threat(self, bias, trap):
        assert critique(bias, session=PRIME,
                        order_flow={"absorption_trap": trap}).penalty_score == 0.0

    def test_a_buy_facing_selling_delta_scores_proportional_penalty(self):
        r = critique("BUY", session=PRIME, order_flow={"delta_score": -100.0})
        assert r.penalty_score == 15.0

    def test_a_buy_facing_moderate_selling_delta_scores_less(self):
        r = critique("BUY", session=PRIME, order_flow={"delta_score": -40.0})
        assert r.penalty_score == 6.0

    def test_the_delta_penalty_is_capped_at_15(self):
        for d in (-100.0, -500.0, -1000.0):
            assert critique("BUY", session=PRIME,
                            order_flow={"delta_score": d}).penalty_score == 15.0

    def test_a_delta_of_exactly_minus_35_is_the_threshold(self):
        """`round(min(15.0, 35.0 * 0.15), 1)` = round(5.25, 1) = 5.2 — banker's
        rounding, so the first step is 5.2 and not 5.3."""
        r = critique("BUY", session=PRIME, order_flow={"delta_score": -35.0})
        assert r.penalty_score == 5.2

    def test_a_delta_above_minus_35_is_not_adverse(self):
        assert critique("BUY", session=PRIME,
                        order_flow={"delta_score": -34.9}).penalty_score == 0.0

    def test_a_sell_facing_buying_delta_mirrors_it(self):
        assert critique("SELL", session=PRIME,
                        order_flow={"delta_score": 100.0}).penalty_score == 15.0
        assert critique("SELL", session=PRIME,
                        order_flow={"delta_score": 34.9}).penalty_score == 0.0

    def test_a_neutral_delta_is_no_threat(self):
        assert critique("BUY", session=PRIME,
                        order_flow={"delta_score": 0.0}).penalty_score == 0.0

    @pytest.mark.parametrize("of", [{}, {"absorption_trap": None}, {"delta_score": 0.0}])
    def test_an_empty_order_flow_dict_is_skipped_entirely(self, of):
        assert critique("BUY", session=PRIME, order_flow=of).penalty_score == 0.0

    def test_a_non_dict_order_flow_is_ignored(self):
        assert critique("BUY", session=PRIME, order_flow="nonsense").penalty_score == 0.0


# --------------------------------------------------------------------------
# 3. volatility, spread, session
# --------------------------------------------------------------------------

class TestVolatilityAndSession:

    def test_an_excessive_spread_scores_12(self):
        r = critique("BUY", session=PRIME,
                     volatility=VolatilityContext(is_excessive_spread=True,
                                                  current_spread_pips=20.0))
        assert r.penalty_score == 12.0
        assert any("Excessive spread" in t for t in r.threats_detected)

    def test_the_message_quotes_twice_the_registry_typical_spread(self):
        r = critique("BUY", session=PRIME, symbol="XAUUSD",
                     volatility=VolatilityContext(is_excessive_spread=True,
                                                  current_spread_pips=20.0))
        typical = resolve_symbol("XAUUSD").typical_spread_pips
        assert any(f"{typical * 2:.1f}" in t for t in r.threats_detected)

    def test_extreme_volatility_scores_18(self):
        assert critique("BUY", session=PRIME,
                        volatility=VolatilityContext(state="EXTREME")).penalty_score == 18.0

    @pytest.mark.parametrize("state", ["NORMAL", "COMPRESSION", "EXPANSION"])
    def test_other_volatility_states_are_no_threat(self, state):
        assert critique("BUY", session=PRIME,
                        volatility=VolatilityContext(state=state)).penalty_score == 0.0

    @pytest.mark.parametrize("sym", ["XAUUSD", "EURUSD", "BTCUSD"])
    def test_off_hours_costs_8_unless_the_instrument_is_crypto(self, sym):
        spec = resolve_symbol(sym)
        expected = 0.0 if spec.is_crypto else 8.0
        assert critique("BUY", symbol=sym, session=OFF).penalty_score == expected

    def test_crypto_is_exempt_from_the_off_hours_penalty(self):
        crypto = next(s for s in ("BTCUSD", "ETHUSD", "XBTUSD")
                      if resolve_symbol(s).is_crypto)
        assert critique("BUY", symbol=crypto, session=OFF).penalty_score == 0.0

    def test_an_unregistered_symbol_still_critiques(self):
        """`resolve_symbol` falls back to a generic FX spec rather than raising,
        so the critic survives an unknown symbol — but it does so with FX
        numbers in the spread message."""
        r = critique("BUY", symbol="TOTALLYUNKNOWN", session=PRIME)
        assert r.penalty_score == 0.0
        assert isinstance(r.threats_detected, list)


# --------------------------------------------------------------------------
# 4. regime
# --------------------------------------------------------------------------

class TestRegime:

    def test_a_regime_transition_scores_10(self):
        r = DevilAdvocateAnalyst(correlation_engine=FakeCorr()).critique_opportunity(
            ctx(session=PRIME), regime(conf=0.1, transition=True), "BUY")
        assert r.penalty_score == 10.0

    def test_low_confidence_scores_8(self):
        r = DevilAdvocateAnalyst(correlation_engine=FakeCorr()).critique_opportunity(
            ctx(session=PRIME), regime(conf=0.59), "BUY")
        assert r.penalty_score == 8.0

    def test_confidence_of_exactly_0p60_is_not_low(self):
        r = DevilAdvocateAnalyst(correlation_engine=FakeCorr()).critique_opportunity(
            ctx(session=PRIME), regime(conf=0.60), "BUY")
        assert r.penalty_score == 0.0

    def test_a_transition_masks_the_confidence_penalty(self):
        """`elif`, so a transitioning regime with 1% confidence costs 10, not 18."""
        r = DevilAdvocateAnalyst(correlation_engine=FakeCorr()).critique_opportunity(
            ctx(session=PRIME), regime(conf=0.01, transition=True), "BUY")
        assert r.penalty_score == 10.0


# --------------------------------------------------------------------------
# 5. cross-asset correlation
# --------------------------------------------------------------------------

class TestCorrelation:

    def test_the_branch_fires_when_the_pair_is_a_key(self):
        """With `EURUSD` present as a *timeframe-style* key and correlated, a
        conflicting BEARISH read adds 8.0 and is reported separately."""
        corr = FakeCorr(0.9)
        r = DevilAdvocateAnalyst(correlation_engine=corr).critique_opportunity(
            ctx(symbol="XAUUSD", session=PRIME, mtf={"EURUSD": "BEARISH"}),
            regime(), "BUY")
        assert r.penalty_score == 8.0
        assert len(r.correlated_threats) == 1
        assert "EURUSD" in r.correlated_threats[0]
        assert r.correlated_threats[0] in r.threats_detected

    def test_a_non_gold_symbol_is_never_checked(self):
        corr = FakeCorr(0.9)
        DevilAdvocateAnalyst(correlation_engine=corr).critique_opportunity(
            ctx(symbol="EURUSD", session=PRIME, mtf={"EURUSD": "BEARISH"}),
            regime(), "BUY")
        assert corr.calls == []

    @pytest.mark.parametrize("sym", ["XAUUSD", "GOLD", "XAUUSD.m"])
    def test_gold_symbols_are_checked(self, sym):
        corr = FakeCorr(0.9)
        DevilAdvocateAnalyst(correlation_engine=corr).critique_opportunity(
            ctx(symbol=sym, session=PRIME), regime(), "BUY")
        assert [c for c in corr.calls] == [(sym, "EURUSD"), (sym, "GBPUSD")]

    @pytest.mark.parametrize("r_val", [0.69, 0.0, -1.0])
    def test_correlation_below_the_threshold_is_ignored(self, r_val):
        corr = FakeCorr(r_val)
        rep = DevilAdvocateAnalyst(correlation_engine=corr).critique_opportunity(
            ctx(symbol="XAUUSD", session=PRIME, mtf={"EURUSD": "BEARISH"}),
            regime(), "BUY")
        assert rep.penalty_score == 0.0

    def test_correlation_at_the_threshold_counts(self):
        for pair, thr in (("EURUSD", 0.70), ("GBPUSD", 0.60)):
            corr = FakeCorr(thr)
            rep = DevilAdvocateAnalyst(correlation_engine=corr).critique_opportunity(
                ctx(symbol="XAUUSD", session=PRIME, mtf={pair: "BEARISH"}),
                regime(), "BUY")
            assert rep.penalty_score == 8.0

    def test_a_sell_is_threatened_by_a_bullish_pair(self):
        corr = FakeCorr(0.9)
        rep = DevilAdvocateAnalyst(correlation_engine=corr).critique_opportunity(
            ctx(symbol="XAUUSD", session=PRIME, mtf={"GBPUSD": "BULLISH"}),
            regime(), "SELL")
        assert rep.penalty_score == 8.0

    @pytest.mark.parametrize("pair_bias", ["BULLISH", "NEUTRAL", ""])
    def test_a_buy_is_not_threatened_by_a_non_bearish_pair(self, pair_bias):
        corr = FakeCorr(0.9)
        rep = DevilAdvocateAnalyst(correlation_engine=corr).critique_opportunity(
            ctx(symbol="XAUUSD", session=PRIME, mtf={"EURUSD": pair_bias}),
            regime(), "BUY")
        assert rep.penalty_score == 0.0

    def test_the_check_is_dead_in_production(self):
        """🔴 DEAD BRANCH. The block needs a "EURUSD"/"GBPUSD" key inside
        `mtf_alignment`, but `market_context.py:115` only ever writes timeframe
        keys (D1/H4/H1/M15/M5/M1). So `mtf.get(pair_sym)` is always None, the
        `isinstance(..., str)` check yields `pair_bias = ""`, and no gold trade
        is ever penalised for a correlated pair. (The `pair_sym in mtf` guard is
        in fact redundant — removing it changes nothing, because the isinstance
        check already no-ops.) `get_correlation` is still called twice per
        critique, because Python evaluates it before the `and`."""
        corr = FakeCorr(0.9)
        realistic = {"D1": "BULLISH", "H4": "BULLISH", "H1": "BULLISH", "M15": "BULLISH"}
        rep = DevilAdvocateAnalyst(correlation_engine=corr).critique_opportunity(
            ctx(symbol="XAUUSD", session=PRIME, mtf=realistic), regime(), "BUY")
        assert rep.penalty_score == 0.0
        assert rep.correlated_threats == []
        assert len(corr.calls) == 2  # still queried, result discarded

    def test_the_correlation_engine_is_default_constructed(self):
        from jarvis.market.correlations import DynamicCorrelationEngine
        assert isinstance(DevilAdvocateAnalyst().correlation_engine,
                          DynamicCorrelationEngine)


# --------------------------------------------------------------------------
# 6. threat price level
# --------------------------------------------------------------------------

class TestThreatPriceLevel:

    def test_a_buy_is_threatened_by_the_bottom_of_the_supply_zone_above(self):
        st = StructureContext(bias="NEUTRAL", supply_zone=(105.0, 110.0),
                              demand_zone=(90.0, 95.0))
        assert critique("BUY", session=PRIME, structure=st, price=100.0).threat_price_level == 105.0

    def test_a_sell_is_threatened_by_the_top_of_the_demand_zone_below(self):
        st = StructureContext(bias="NEUTRAL", supply_zone=(105.0, 110.0),
                              demand_zone=(90.0, 95.0))
        assert critique("SELL", session=PRIME, structure=st, price=100.0).threat_price_level == 95.0

    def test_a_zone_that_is_not_beyond_price_is_no_obstacle(self):
        st = StructureContext(bias="NEUTRAL", supply_zone=(80.0, 90.0),
                              demand_zone=(120.0, 130.0))
        assert critique("BUY", session=PRIME, structure=st, price=100.0).threat_price_level is None
        assert critique("SELL", session=PRIME, structure=st, price=100.0).threat_price_level is None

    def test_a_buy_falls_back_to_the_nearest_resistance_key_level(self):
        st = StructureContext(bias="NEUTRAL", supply_zone=(0.0, 0.0),
                              key_levels=[{"price": 130.0}, {"price": 107.0},
                                          {"price": 80.0}])
        assert critique("BUY", session=PRIME, structure=st, price=100.0).threat_price_level == 107.0

    def test_a_sell_falls_back_to_the_nearest_support_key_level(self):
        st = StructureContext(bias="NEUTRAL", demand_zone=(0.0, 0.0),
                              key_levels=[{"price": 130.0}, {"price": 93.0},
                                          {"price": 80.0}])
        assert critique("SELL", session=PRIME, structure=st, price=100.0).threat_price_level == 93.0

    def test_a_zero_priced_key_level_is_excluded(self):
        st = StructureContext(bias="NEUTRAL", supply_zone=(0.0, 0.0),
                              key_levels=[{"price": 0.0}, {}])
        assert critique("BUY", session=PRIME, structure=st, price=100.0).threat_price_level is None
        assert critique("SELL", session=PRIME, structure=st, price=100.0).threat_price_level is None

    def test_no_levels_at_all_means_no_threat_level(self):
        assert critique("BUY", session=PRIME).threat_price_level is None

    def test_a_malformed_key_level_raises(self):
        st = StructureContext(bias="NEUTRAL", supply_zone=(0.0, 0.0),
                              key_levels=["not-a-dict"])
        with pytest.raises(AttributeError):
            critique("BUY", session=PRIME, structure=st)


# --------------------------------------------------------------------------
# caps, coefficient, and the gate
# --------------------------------------------------------------------------

class TestCapsAndCoefficient:

    def test_the_coefficient_follows_the_penalty_it_reports(self):
        """`max(0.20, 1.0 - penalty/60)`. Asserted against the *report's own*
        penalty so a change to the divisor cannot be absorbed by the test."""
        cases = [
            (ctx(session=PRIME), 0.0, 1.0),
            (ctx(session=OFF), 8.0, 0.87),
            (ctx(session=OFF, structure=StructureContext(bias="NEUTRAL", lower_highs=True,
                                                         lower_lows=True)), 26.0, 0.57),
            (ctx(session=PRIME, momentum=MomentumContext(trend_score=-100),
                 order_flow={"delta_score": -100.0}), 30.0, 0.5),
        ]
        for c, penalty, coeff in cases:
            r = DevilAdvocateAnalyst(correlation_engine=FakeCorr()).critique_opportunity(
                c, regime(), "BUY")
            assert r.penalty_score == penalty
            assert r.invalidation_risk_coefficient == coeff
            assert r.invalidation_risk_coefficient == round(
                max(0.20, 1.0 - (r.penalty_score / 60.0)), 2)

    def test_the_coefficient_saturates_below_a_penalty_of_48(self):
        """Because the penalty is capped at 50, `1 - p/60` bottoms out at 0.167;
        the 0.20 floor means the last 2 points of penalty are invisible to it.
        Anything at or above 48.0 reports the same 0.20."""
        assert round(max(0.20, 1.0 - 48.0 / 60.0), 2) == 0.2
        assert round(max(0.20, 1.0 - 47.9 / 60.0), 2) == 0.2
        assert round(max(0.20, 1.0 - 46.0 / 60.0), 2) == 0.23

    def test_the_penalty_is_capped_at_50(self):
        """A maximally hostile context still stops at 50.0."""
        hostile = ctx(
            structure=StructureContext(bias="NEUTRAL", lower_highs=True, lower_lows=True,
                                       discount_premium_zone="PREMIUM"),
            momentum=MomentumContext(trend_score=-100, rsi=99.0, adx=99.0,
                                     minus_di=90.0, plus_di=1.0,
                                     divergence="BEARISH_DIVERGENCE"),
            volatility=VolatilityContext(state="EXTREME", is_excessive_spread=True,
                                         current_spread_pips=99.0),
            liquidity=LiquidityContext(equal_lows=True, buy_side_liquidity=1.0,
                                       sell_side_liquidity=99.0),
            session=OFF, mtf={"H4": "BEARISH", "D1": "BEARISH"},
            order_flow={"absorption_trap": "SELLER_ABSORPTION_TRAP", "delta_score": -100.0},
        )
        r = DevilAdvocateAnalyst(correlation_engine=FakeCorr()).critique_opportunity(
            hostile, regime(transition=True), "BUY")
        assert r.penalty_score == 50.0
        assert r.invalidation_risk_coefficient == 0.2

    def test_the_cap_is_above_the_gate_so_a_block_is_possible(self):
        """`max_devil_penalty` defaults to 43.0, so the critic *can* stop a
        trade — but only 7 points of its 50-point range are above the gate."""
        assert 50.0 > GATE
        assert 50.0 - GATE == 7.0

    def test_a_single_threat_never_reaches_the_gate(self):
        """The largest individual threat is `trend_broken` at 18.0 plus the
        off-hours 8.0 every default context carries — still far below 43.0. A
        block needs roughly five concurrent threats."""
        r = critique("BUY", session=OFF,
                     structure=StructureContext(bias="NEUTRAL", lower_highs=True,
                                                lower_lows=True))
        assert r.penalty_score == 26.0
        assert r.penalty_score < GATE

    def test_five_concurrent_threats_are_needed_to_block(self):
        hostile = ctx(
            structure=StructureContext(bias="NEUTRAL", lower_highs=True, lower_lows=True,
                                       discount_premium_zone="PREMIUM"),
            momentum=MomentumContext(trend_score=-100, rsi=99.0,
                                     divergence="BEARISH_DIVERGENCE"),
            volatility=VolatilityContext(state="EXTREME"),
            session=OFF,
        )
        r = DevilAdvocateAnalyst(correlation_engine=FakeCorr()).critique_opportunity(
            hostile, regime(), "BUY")
        # 18 broken + 15 trend + 12 rsi + 14 divergence + 10 zone + 18 vol + 8 session
        assert r.penalty_score == 50.0
        assert r.penalty_score > GATE

"""Round 25 — the six analyst implementations behind `ParallelAnalystCluster`.

These produce the scores whose **plain mean** becomes `ai_score`
(`decision_engine:718`), and `ai_score >= min_score` is the hard
`"AI Multi-Score Gate"` (`decision_engine:643`). So the *levels* these
functions start from are not cosmetic: a base of 70 instead of 50 moves the
gate by 3.3 points on its own.

Deliberately testing the workers, not the fan-out (`test_parallel_runner.py`
covers that). Everything here is pure and offline.
"""
import pytest
from datetime import datetime, timezone

from jarvis.data.schemas import (
    MarketContext, StructureContext, LiquidityContext, VolatilityContext,
    MomentumContext, SessionContext, RegimeOutput, MarketRegime, AnalystRole,
)
from jarvis.analysts.base_analyst import BaseAnalyst
from jarvis.analysts.structure_analyst import StructureAnalyst
from jarvis.analysts.momentum_analyst import MomentumAnalyst
from jarvis.analysts.liquidity_analyst import LiquidityAnalyst
from jarvis.analysts.volatility_analyst import VolatilityAnalyst
from jarvis.analysts.risk_analyst import RiskAnalyst
from jarvis.analysts.macro_analyst import MacroAnalyst, _parse_metric


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------

def ctx(
    symbol="XAUUSD", price=100.0,
    structure=None, liquidity=None, volatility=None, momentum=None, session=None,
    mtf=None,
) -> MarketContext:
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
    )


def regime(name=MarketRegime.TREND_BULL) -> RegimeOutput:
    return RegimeOutput(primary_regime=name, probabilities={name.value: 1.0}, confidence=0.9)


ALL_ANALYSTS = [StructureAnalyst, MomentumAnalyst, LiquidityAnalyst,
                VolatilityAnalyst, RiskAnalyst, MacroAnalyst]

EXPECTED_ROLE = {
    StructureAnalyst: AnalystRole.STRUCTURE,
    MomentumAnalyst: AnalystRole.MOMENTUM,
    LiquidityAnalyst: AnalystRole.LIQUIDITY,
    VolatilityAnalyst: AnalystRole.VOLATILITY,
    RiskAnalyst: AnalystRole.RISK,
    MacroAnalyst: AnalystRole.MACRO,
}


@pytest.fixture(autouse=True)
def _no_network_news(monkeypatch):
    """`MacroAnalyst` falls back to the global news engine whenever its calendar
    is empty — that is a network call. Neutralise it for every test; the two
    tests that care about that path patch it themselves."""
    import jarvis.market.news as news
    monkeypatch.setattr(news.GLOBAL_NEWS_ENGINE, "get_news_calendar",
                        lambda force_refresh=False: [], raising=False)


# --------------------------------------------------------------------------
# contract shared by all six
# --------------------------------------------------------------------------

class TestSharedContract:

    @pytest.mark.parametrize("cls", ALL_ANALYSTS, ids=lambda c: c.__name__)
    def test_every_analyst_is_a_base_analyst(self, cls):
        assert issubclass(cls, BaseAnalyst)

    def test_the_base_class_cannot_be_instantiated(self):
        with pytest.raises(TypeError):
            BaseAnalyst(AnalystRole.STRUCTURE)

    @pytest.mark.parametrize("cls", ALL_ANALYSTS, ids=lambda c: c.__name__)
    def test_role_matches_the_class(self, cls):
        assert cls().analyze(ctx(), regime()).role == EXPECTED_ROLE[cls]

    @pytest.mark.parametrize("cls", ALL_ANALYSTS, ids=lambda c: c.__name__)
    def test_symbol_is_propagated(self, cls):
        assert cls().analyze(ctx(symbol="GBPJPY"), regime()).symbol == "GBPJPY"

    @pytest.mark.parametrize("cls", ALL_ANALYSTS, ids=lambda c: c.__name__)
    def test_score_is_always_inside_0_100(self, cls):
        for st_bias in ("BULLISH", "BEARISH", "NEUTRAL"):
            r = cls().analyze(ctx(structure=StructureContext(bias=st_bias)), regime())
            assert 0.0 <= r.score <= 100.0

    @pytest.mark.parametrize("cls", ALL_ANALYSTS, ids=lambda c: c.__name__)
    def test_confidence_is_always_inside_0p40_0p95(self, cls):
        r = cls().analyze(ctx(), regime())
        assert 0.40 <= r.confidence <= 0.95

    @pytest.mark.parametrize("cls", ALL_ANALYSTS, ids=lambda c: c.__name__)
    def test_bias_is_one_of_the_three_known_values(self, cls):
        assert cls().analyze(ctx(), regime()).bias in ("BULLISH", "BEARISH", "NEUTRAL")

    @pytest.mark.parametrize("cls", ALL_ANALYSTS, ids=lambda c: c.__name__)
    def test_execution_time_is_recorded(self, cls):
        assert cls().analyze(ctx(), regime()).execution_time_ms >= 0.0

    @pytest.mark.parametrize("cls", ALL_ANALYSTS, ids=lambda c: c.__name__)
    def test_confidence_is_the_score_over_100_clamped(self, cls):
        """Every analyst derives confidence the same way, so it is not an
        independent opinion — it is the score rescaled. A score of 45 and a
        score of 100 both report 'confidence' 0.45 vs 0.95."""
        r = cls().analyze(ctx(), regime())
        assert r.confidence == pytest.approx(min(0.95, max(0.40, r.score / 100.0)), abs=0.01)


class TestDegenerateContext:
    """What the gate sees when there is no data at all.

    A default-constructed context is atr=0, spread=0, no structure, no news.
    That is what a broken feed produces — and it must not look like a good
    setup.
    """

    def test_each_score_on_a_completely_empty_context(self):
        got = {c.__name__: c().analyze(ctx(), regime()).score for c in ALL_ANALYSTS}
        assert got == {
            "StructureAnalyst": 50.0,
            "MomentumAnalyst": 50.0,
            "LiquidityAnalyst": 50.0,
            "VolatilityAnalyst": 100.0,
            "RiskAnalyst": 45.0,
            "MacroAnalyst": 65.0,
        }

    def test_the_volatility_analyst_is_maximally_bullish_on_no_data(self):
        """🔴 atr=0 and spread=0 read as 'tight institutional spread' plus
        'optimal volatility conditions' — the best possible reading. A dead
        feed scores 100/100 from the analyst with the highest base."""
        r = VolatilityAnalyst().analyze(ctx(), regime())
        assert r.score == 100.0
        assert any("Tight institutional spread" in e for e in r.evidence)

    def test_the_mean_ai_score_of_an_empty_context(self):
        """The number `decision_engine:718` hands to the AI Multi-Score Gate."""
        scores = [c().analyze(ctx(), regime()).score for c in ALL_ANALYSTS]
        assert sum(scores) / len(scores) == pytest.approx(60.0)


# --------------------------------------------------------------------------
# structure
# --------------------------------------------------------------------------

class TestStructureAnalyst:

    def run(self, **kw):
        return StructureAnalyst().analyze(ctx(structure=StructureContext(**kw)), regime())

    def test_default_is_the_50_base(self):
        assert self.run(bias="NEUTRAL").score == 50.0

    def test_higher_highs_and_higher_lows_add_20(self):
        assert self.run(bias="BULLISH", higher_highs=True, higher_lows=True).score == 70.0

    def test_lower_highs_and_lower_lows_add_the_same_20(self):
        """Direction-agnostic: a downtrend is rewarded exactly as much as an
        uptrend. The geometry bonus says 'this trends', not 'this goes up'."""
        assert self.run(bias="BEARISH", lower_highs=True, lower_lows=True).score == 70.0

    def test_both_geometries_at_once_pays_once(self):
        assert self.run(bias="BULLISH", higher_highs=True, higher_lows=True,
                        lower_highs=True, lower_lows=True).score == 70.0

    def test_half_a_geometry_pays_nothing(self):
        assert self.run(bias="BULLISH", higher_highs=True).score == 50.0

    def test_choch_adds_35_in_either_direction(self):
        assert self.run(bias="BULLISH", choch=True, choch_type="BULLISH").score == 85.0
        assert self.run(bias="BEARISH", choch=True, choch_type="BEARISH").score == 85.0

    def test_bos_adds_15_in_either_direction(self):
        assert self.run(bias="BULLISH", bos=True, bos_type="BULLISH").score == 65.0
        assert self.run(bias="BEARISH", bos=True, bos_type="BEARISH").score == 65.0

    def test_the_score_is_capped_at_100(self):
        r = self.run(bias="BULLISH", higher_highs=True, higher_lows=True,
                     choch=True, choch_type="BULLISH", bos=True, bos_type="BULLISH")
        assert r.score == 100.0

    def test_bullish_bias_in_the_premium_zone_is_penalised(self):
        r = self.run(bias="BULLISH", discount_premium_zone="PREMIUM")
        assert r.score == 40.0
        assert any("PREMIUM" in f for f in r.risk_factors)

    def test_bearish_bias_in_the_premium_zone_is_rewarded(self):
        r = self.run(bias="BEARISH", discount_premium_zone="PREMIUM")
        assert r.score == 65.0

    def test_bearish_bias_in_the_discount_zone_is_penalised(self):
        assert self.run(bias="BEARISH", discount_premium_zone="DISCOUNT").score == 40.0

    def test_bullish_bias_in_the_discount_zone_is_rewarded(self):
        assert self.run(bias="BULLISH", discount_premium_zone="DISCOUNT").score == 65.0

    def test_a_neutral_bias_in_a_zone_is_still_rewarded(self):
        """🔴 With no bias, *either* zone pays +15 and appends evidence
        asserting the setup is 'optimal' for a direction the report does not
        hold. The `-10` branch is unreachable without a directional bias."""
        for zone, word in (("PREMIUM", "shorting"), ("DISCOUNT", "longing")):
            r = self.run(bias="NEUTRAL", discount_premium_zone=zone)
            assert r.score == 65.0
            assert r.bias == "NEUTRAL"
            assert any(word in e for e in r.evidence)

    def test_htf_alignment_adds_10(self):
        r = StructureAnalyst().analyze(
            ctx(structure=StructureContext(bias="BULLISH"),
                mtf={"H4": "BULLISH", "D1": "BULLISH"}), regime())
        assert r.score == 60.0

    def test_htf_divergence_is_a_risk_factor_and_costs_nothing(self):
        r = StructureAnalyst().analyze(
            ctx(structure=StructureContext(bias="BULLISH"),
                mtf={"H4": "BEARISH", "D1": "BULLISH"}), regime())
        assert r.score == 50.0
        assert any("divergence" in f for f in r.risk_factors)

    def test_a_neutral_h4_is_neither_bonus_nor_divergence(self):
        r = StructureAnalyst().analyze(
            ctx(structure=StructureContext(bias="BULLISH"),
                mtf={"H4": "NEUTRAL", "D1": "BULLISH"}), regime())
        assert r.score == 50.0
        assert r.risk_factors == []

    @pytest.mark.parametrize("mtf", [{}, {"D1": "BULLISH"}, {"H1": "BEARISH", "M15": "BEARISH"}])
    def test_an_absent_h4_frame_is_not_reported_as_divergence(self, mtf):
        """`market_context.py:115` builds `mtf_alignment` per trade style:
        SCALP gets H1/M15/M5/M1 and never an H4. Reading the missing frame as
        `.get("H4") != bias` meant *every* SCALP decision carried a fabricated
        'structural divergence (H4 is None)' risk factor."""
        r = StructureAnalyst().analyze(
            ctx(structure=StructureContext(bias="BULLISH"), mtf=mtf), regime())
        assert r.risk_factors == []
        assert r.score == 50.0

    # what `market_context.py` actually puts in the dict, per trade style
    STYLE_MTF = {
        "SWING": {"D1": "BULLISH", "H4": "BULLISH", "H1": "BULLISH", "M15": "BULLISH"},
        "DAY_TRADING": {"H4": "BULLISH", "H1": "BULLISH", "M15": "BULLISH", "M5": "BULLISH"},
        "SCALP": {"H1": "BULLISH", "M15": "BULLISH", "M5": "BULLISH", "M1": "BULLISH"},
    }

    @pytest.mark.parametrize("style,score", [("SWING", 60.0), ("DAY_TRADING", 50.0),
                                             ("SCALP", 50.0)])
    def test_the_htf_confluence_bonus_is_only_reachable_in_swing(self, style, score):
        """The bonus needs BOTH `H4` and `D1` to equal the bias, but only SWING
        puts a `D1` in the dict. For DAY_TRADING and SCALP the +10 is dead
        code, whatever the market is doing."""
        r = StructureAnalyst().analyze(
            ctx(structure=StructureContext(bias="BULLISH"), mtf=self.STYLE_MTF[style]),
            regime())
        assert r.score == score
        assert r.risk_factors == []

    def test_scalp_can_never_report_htf_divergence(self):
        """A SCALP context that is bearish on every frame it *does* compute
        still reports no divergence, because H4 — the only frame consulted —
        is absent."""
        bearish = {k: "BEARISH" for k in ("H1", "M15", "M5", "M1")}
        r = StructureAnalyst().analyze(
            ctx(structure=StructureContext(bias="BULLISH"), mtf=bearish), regime())
        assert r.risk_factors == []

    def test_metadata_carries_the_zones(self):
        r = self.run(bias="BULLISH", demand_zone=(1.0, 2.0), supply_zone=(3.0, 4.0),
                     discount_premium_zone="DISCOUNT", choch=True)
        assert r.metadata["demand_zone"] == (1.0, 2.0)
        assert r.metadata["supply_zone"] == (3.0, 4.0)
        assert r.metadata["zone"] == "DISCOUNT"
        assert r.metadata["choch"] is True


# --------------------------------------------------------------------------
# momentum
# --------------------------------------------------------------------------

class TestMomentumAnalyst:

    def run(self, **kw):
        return MomentumAnalyst().analyze(ctx(momentum=MomentumContext(**kw)), regime())

    def test_default_is_the_50_base(self):
        assert self.run().score == 50.0

    @pytest.mark.parametrize("ts", [30, 31, 100])
    def test_trend_score_at_or_above_30_is_bullish(self, ts):
        r = self.run(trend_score=ts)
        assert r.bias == "BULLISH" and r.score == 70.0

    @pytest.mark.parametrize("ts", [-30, -31, -100])
    def test_trend_score_at_or_below_minus_30_is_bearish_and_scores_the_same(self, ts):
        """Symmetric: a strong *sell* scores 70 exactly like a strong *buy*.
        Because `ai_score` is a mean of scores, momentum contributes the same
        magnitude whichever way it points."""
        r = self.run(trend_score=ts)
        assert r.bias == "BEARISH" and r.score == 70.0

    @pytest.mark.parametrize("ts", [-29, 0, 29])
    def test_middle_trend_scores_stay_neutral(self, ts):
        r = self.run(trend_score=ts)
        assert r.bias == "NEUTRAL" and r.score == 50.0

    def test_adx_25_adds_15(self):
        assert self.run(adx=25.0).score == 65.0

    def test_adx_just_under_25_adds_nothing(self):
        assert self.run(adx=24.9).score == 50.0

    def test_low_adx_is_a_risk_factor_but_costs_nothing(self):
        """Choppy momentum is the classic loser, yet the score is unchanged —
        it is only ever recorded in `risk_factors`."""
        r = self.run(adx=17.9)
        assert r.score == 50.0
        assert any("Low trend velocity" in f for f in r.risk_factors)

    def test_acceleration_adds_10(self):
        assert self.run(acceleration="ACCELERATING").score == 60.0

    def test_exhaustion_costs_15(self):
        assert self.run(acceleration="EXHAUSTION").score == 35.0

    def test_divergence_with_the_trend_adds_15(self):
        assert self.run(trend_score=40, divergence="BULLISH_DIVERGENCE").score == 85.0
        assert self.run(trend_score=-40, divergence="BEARISH_DIVERGENCE").score == 85.0

    def test_divergence_against_the_trend_costs_nothing(self):
        r = self.run(trend_score=40, divergence="BEARISH_DIVERGENCE")
        assert r.score == 70.0
        assert any("divergence" in f.lower() for f in r.risk_factors)

    def test_exhaustion_is_the_only_way_below_the_base(self):
        r = self.run(acceleration="EXHAUSTION", adx=0.0, trend_score=0)
        assert r.score == 35.0
        # exhaustion (-15) is the only negative, so the 0.0 floor is
        # unreachable from a base of 50 — pin that fact.
        assert 50.0 - 15.0 > 0.0

    def test_the_confidence_floor_binds_below_a_score_of_40(self):
        """At 35 the analyst reports confidence 0.40, not 0.35: below 40 the
        clamp is load-bearing, so `confidence` is not simply `score / 100`."""
        r = self.run(acceleration="EXHAUSTION")
        assert r.score == 35.0
        assert r.confidence == 0.40

    def test_the_score_is_capped_at_100(self):
        assert self.run(trend_score=100, adx=99.0, acceleration="ACCELERATING",
                        divergence="BULLISH_DIVERGENCE").score == 100.0

    def test_metadata_carries_the_indicators(self):
        r = self.run(rsi=71.0, adx=30.0, acceleration="STEADY", divergence="NONE")
        assert r.metadata == {"rsi": 71.0, "adx": 30.0,
                              "acceleration": "STEADY", "divergence": "NONE"}


# --------------------------------------------------------------------------
# liquidity
# --------------------------------------------------------------------------

class TestLiquidityAnalyst:

    def test_default_is_the_50_base_and_inherits_structure_bias(self):
        r = LiquidityAnalyst().analyze(
            ctx(structure=StructureContext(bias="BULLISH")), regime())
        assert r.score == 50.0
        assert r.bias == "BULLISH"

    def test_a_bullish_sweep_adds_30_and_sets_the_bias(self):
        r = LiquidityAnalyst().analyze(
            ctx(liquidity=LiquidityContext(sweep_detected=True, sweep_type="BULLISH_SWEEP",
                                           sweep_level=99.0)), regime())
        assert r.score == 80.0 and r.bias == "BULLISH"
        assert any("Sell-Side Liquidity Sweep" in e for e in r.evidence)

    def test_a_bearish_sweep_adds_30_and_sets_the_bias(self):
        r = LiquidityAnalyst().analyze(
            ctx(liquidity=LiquidityContext(sweep_detected=True, sweep_type="BEARISH_SWEEP",
                                           sweep_level=101.0)), regime())
        assert r.score == 80.0 and r.bias == "BEARISH"

    def test_a_sweep_with_no_type_still_adds_30_and_falls_back_to_structure(self):
        """`sweep_detected=True` with `sweep_type="NONE"` pays the full bonus
        but carries no direction, so the bias silently reverts to structure."""
        r = LiquidityAnalyst().analyze(
            ctx(structure=StructureContext(bias="BEARISH"),
                liquidity=LiquidityContext(sweep_detected=True)), regime())
        assert r.score == 80.0 and r.bias == "BEARISH"

    def test_equal_highs_and_lows_are_risk_factors_and_cost_nothing(self):
        r = LiquidityAnalyst().analyze(
            ctx(liquidity=LiquidityContext(equal_highs=True, equal_lows=True)), regime())
        assert r.score == 50.0
        assert len(r.risk_factors) == 2

    def test_a_fair_value_gap_adds_10(self):
        st = StructureContext(bias="NEUTRAL",
                              fair_value_gaps=[{"type": "BULLISH_FVG", "top": 2.0, "bottom": 1.0}])
        r = LiquidityAnalyst().analyze(ctx(structure=st), regime())
        assert r.score == 60.0

    def test_an_order_block_adds_10(self):
        st = StructureContext(bias="NEUTRAL",
                              order_blocks=[{"type": "BULLISH_OB", "mid": 1.5}])
        assert LiquidityAnalyst().analyze(ctx(structure=st), regime()).score == 60.0

    def test_both_add_20(self):
        st = StructureContext(
            bias="NEUTRAL",
            fair_value_gaps=[{"type": "BULLISH_FVG", "top": 2.0, "bottom": 1.0}],
            order_blocks=[{"type": "BULLISH_OB", "mid": 1.5}])
        assert LiquidityAnalyst().analyze(ctx(structure=st), regime()).score == 70.0

    def test_only_the_most_recent_gap_is_reported(self):
        st = StructureContext(bias="NEUTRAL", fair_value_gaps=[
            {"type": "OLD", "top": 1.0, "bottom": 0.5},
            {"type": "NEW", "top": 2.0, "bottom": 1.5},
        ])
        r = LiquidityAnalyst().analyze(ctx(structure=st), regime())
        assert sum("NEW" in e for e in r.evidence) == 1
        assert not any("OLD" in e for e in r.evidence)

    def test_only_the_most_recent_order_block_is_reported(self):
        st = StructureContext(bias="NEUTRAL", order_blocks=[
            {"type": "OLD_OB", "mid": 1.0},
            {"type": "NEW_OB", "mid": 2.0},
        ])
        r = LiquidityAnalyst().analyze(ctx(structure=st), regime())
        assert sum("NEW_OB" in e for e in r.evidence) == 1
        assert not any("OLD_OB" in e for e in r.evidence)

    def test_a_malformed_gap_raises_rather_than_degrading(self):
        """`recent_fvg['type']` is a bare index — a gap built without the key
        raises KeyError. In `parallel=True` that becomes a 50.0 fallback; in
        `parallel=False` it propagates (see test_parallel_runner)."""
        st = StructureContext(bias="NEUTRAL", fair_value_gaps=[{"top": 2.0, "bottom": 1.0}])
        with pytest.raises(KeyError):
            LiquidityAnalyst().analyze(ctx(structure=st), regime())

    def test_a_malformed_order_block_raises(self):
        st = StructureContext(bias="NEUTRAL", order_blocks=[{"type": "BULLISH_OB"}])
        with pytest.raises(KeyError):
            LiquidityAnalyst().analyze(ctx(structure=st), regime())

    def test_metadata_carries_the_sweep_state(self):
        r = LiquidityAnalyst().analyze(
            ctx(liquidity=LiquidityContext(sweep_detected=True, sweep_type="BEARISH_SWEEP",
                                           equal_lows=True)), regime())
        assert r.metadata["sweep"] is True
        assert r.metadata["sweep_type"] == "BEARISH_SWEEP"
        assert r.metadata["equal_lows"] is True


# --------------------------------------------------------------------------
# volatility
# --------------------------------------------------------------------------

class TestVolatilityAnalyst:

    def run(self, bias="NEUTRAL", **vol):
        return VolatilityAnalyst().analyze(
            ctx(structure=StructureContext(bias=bias),
                volatility=VolatilityContext(**vol)), regime())

    def test_the_base_is_70_not_50(self):
        """Highest base of the six — it alone lifts the mean `ai_score` by
        3.3 points before a single input is read."""
        r = self.run(state="COMPRESSION", current_spread_pips=10.0)
        assert r.score == 70.0

    def test_a_tight_spread_adds_15(self):
        r = self.run(state="COMPRESSION", current_spread_pips=2.5)
        assert r.score == 85.0

    def test_a_spread_just_over_the_tight_threshold_adds_nothing(self):
        assert self.run(state="COMPRESSION", current_spread_pips=2.51).score == 70.0

    def test_normal_volatility_adds_15_and_takes_the_structure_bias(self):
        r = self.run(bias="BULLISH", state="NORMAL", current_spread_pips=10.0)
        assert r.score == 85.0 and r.bias == "BULLISH"

    def test_normal_volatility_with_a_neutral_structure_stays_neutral(self):
        r = self.run(bias="NEUTRAL", state="NORMAL", current_spread_pips=10.0)
        assert r.bias == "NEUTRAL"

    @pytest.mark.parametrize("state,bias,score", [
        ("NORMAL", "BULLISH", 85.0),
        ("EXPANSION", "NEUTRAL", 80.0),
        ("COMPRESSION", "NEUTRAL", 70.0),
        ("EXTREME", "NEUTRAL", 35.0),
    ])
    def test_each_volatility_state(self, state, bias, score):
        r = self.run(bias="BULLISH", state=state, current_spread_pips=10.0)
        assert r.score == score and r.bias == bias

    @pytest.mark.parametrize("stray", ["SIDEWAYS", "", "bullish", "RANGING", None])
    def test_a_stray_structure_bias_is_coerced_to_neutral(self, stray):
        """Only BULLISH/BEARISH survive the `in (...)` guard. Anything else —
        including the lowercase spelling — must not propagate into a report the
        engine reads a direction from."""
        r = self.run(bias=stray, state="NORMAL", current_spread_pips=10.0)
        assert r.bias == "NEUTRAL"

    def test_expansion_with_strong_bullish_momentum_is_bullish(self):
        c = ctx(structure=StructureContext(bias="NEUTRAL"),
                volatility=VolatilityContext(state="EXPANSION", current_spread_pips=10.0),
                momentum=MomentumContext(trend_score=40))
        assert VolatilityAnalyst().analyze(c, regime()).bias == "BULLISH"

    def test_expansion_with_strong_bearish_momentum_is_bearish(self):
        c = ctx(structure=StructureContext(bias="NEUTRAL"),
                volatility=VolatilityContext(state="EXPANSION", current_spread_pips=10.0),
                momentum=MomentumContext(trend_score=-40))
        assert VolatilityAnalyst().analyze(c, regime()).bias == "BEARISH"

    @pytest.mark.parametrize("ts", [-39, 0, 39])
    def test_expansion_with_middling_momentum_stays_neutral(self, ts):
        c = ctx(structure=StructureContext(bias="BULLISH"),
                volatility=VolatilityContext(state="EXPANSION", current_spread_pips=10.0),
                momentum=MomentumContext(trend_score=ts))
        assert VolatilityAnalyst().analyze(c, regime()).bias == "NEUTRAL"

    def test_an_excessive_spread_assigns_10_it_does_not_subtract(self):
        """`score = 10.0` is an assignment, so every bonus earned before it is
        discarded — but every adjustment after it still applies *on top*."""
        r = self.run(bias="BULLISH", state="NORMAL", current_spread_pips=50.0,
                     max_allowed_spread_pips=35.0, is_excessive_spread=True)
        assert r.score == 25.0
        assert any("Excessive spread" in f for f in r.risk_factors)

    def test_an_excessive_spread_plus_extreme_volatility_floors_at_0(self):
        r = self.run(state="EXTREME", current_spread_pips=50.0,
                     max_allowed_spread_pips=35.0, is_excessive_spread=True)
        assert r.score == 0.0

    def test_the_excessive_flag_beats_a_tiny_spread(self):
        """The flag is trusted over the number: a 0-pip spread marked excessive
        gets no tight-spread bonus, because the two are `if`/`elif`."""
        r = self.run(state="COMPRESSION", current_spread_pips=0.0,
                     max_allowed_spread_pips=35.0, is_excessive_spread=True)
        assert r.score == 10.0

    def test_metadata_carries_state_atr_and_spread(self):
        r = self.run(state="NORMAL", atr=1.25, current_spread_pips=0.8)
        assert r.metadata == {"state": "NORMAL", "atr": 1.25, "spread": 0.8}


# --------------------------------------------------------------------------
# risk
# --------------------------------------------------------------------------

class TestRiskAnalyst:

    def run(self, price=100.0, atr=1.0, bias="BULLISH", **st):
        return RiskAnalyst().analyze(
            ctx(price=price,
                structure=StructureContext(bias=bias, **st),
                volatility=VolatilityContext(atr=atr)), regime())

    def test_the_base_is_65(self):
        # BULLISH, demand below → sl 1.0, supply above → tp 1.0 → rr 1.0 → -20
        r = self.run(demand_zone=(99.0, 99.5), supply_zone=(100.5, 101.0))
        assert r.score == 45.0

    def test_rr_of_exactly_2p5_is_the_top_bracket(self):
        r = self.run(demand_zone=(99.0, 99.5), supply_zone=(100.5, 102.5))
        assert r.metadata["rr_ratio"] == 2.5
        assert r.score == 90.0

    def test_rr_of_exactly_1p5_is_the_middle_bracket(self):
        r = self.run(demand_zone=(99.0, 99.5), supply_zone=(100.5, 101.5))
        assert r.metadata["rr_ratio"] == 1.5
        assert r.score == 80.0

    def test_rr_just_under_1p5_falls_to_the_bottom_bracket(self):
        r = self.run(demand_zone=(99.0, 99.5), supply_zone=(100.5, 101.49))
        assert r.score == 45.0

    def test_a_neutral_bias_uses_atr_multiples_and_scores_45(self):
        """No bias → sl = atr*1.5, tp = atr*2.0 → rr = 1.33 → bottom bracket.
        Note the asymmetry: the neutral path targets 2.0x while the fallbacks
        inside the directional paths target 3.0x."""
        r = self.run(bias="NEUTRAL", atr=1.0)
        assert r.metadata["rr_ratio"] == pytest.approx(1.33, abs=0.01)
        assert r.score == 45.0

    def test_a_zero_demand_zone_falls_back_to_atr_times_1p5(self):
        r = self.run(bias="BULLISH", atr=2.0, demand_zone=(0.0, 0.0),
                     supply_zone=(100.5, 110.0))
        assert r.metadata["sl_dist"] == pytest.approx(3.0)

    def test_a_supply_zone_at_or_below_price_falls_back_to_atr_times_3(self):
        r = self.run(bias="BULLISH", atr=2.0, demand_zone=(99.0, 99.5),
                     supply_zone=(100.0, 100.0))
        assert r.metadata["tp_dist"] == pytest.approx(6.0)

    def test_bearish_mirrors_the_zone_logic(self):
        r = self.run(bias="BEARISH", atr=1.0,
                     demand_zone=(90.0, 99.0), supply_zone=(100.5, 101.0))
        assert r.metadata["sl_dist"] == pytest.approx(1.0)
        assert r.metadata["tp_dist"] == pytest.approx(10.0)
        assert r.metadata["rr_ratio"] == 10.0
        assert r.score == 90.0

    def test_a_stale_zone_below_price_inflates_the_stop_instead_of_falling_back(self):
        """🔴 The BEARISH stop guard is `supply_zone[1] > 0`, not
        `> current_price`. A leftover supply zone of (0.0, 0.5) under a price
        of 100 gives a 99.5 stop and an R:R of ~0, not the atr fallback."""
        r = self.run(bias="BEARISH", atr=1.0,
                     demand_zone=(90.0, 99.0), supply_zone=(0.0, 0.5))
        assert r.metadata["sl_dist"] == pytest.approx(99.5)
        assert r.score == 45.0

    def test_a_zero_atr_gives_rr_1p0_rather_than_dividing_by_zero(self):
        """`sl_dist + 1e-9` guards the division, but the `sl_dist > 0` check
        short-circuits first, so a zero-ATR context reports a neutral 1:1."""
        r = self.run(bias="NEUTRAL", atr=0.0)
        assert r.metadata["rr_ratio"] == 1.0
        assert r.score == 45.0

    def test_the_rr_is_structural_not_the_actual_trade(self):
        """`rr_ratio` is measured to the *zones*, never to the stop/target the
        engine actually places. It is a property of the chart shape."""
        r = self.run(demand_zone=(99.0, 99.5), supply_zone=(100.5, 110.0))
        assert r.metadata["rr_ratio"] == pytest.approx(10.0 / 1.0)
        assert r.score == 90.0

    def test_bias_is_always_the_structure_bias(self):
        for b in ("BULLISH", "BEARISH", "NEUTRAL"):
            assert self.run(bias=b).bias == b


# --------------------------------------------------------------------------
# macro
# --------------------------------------------------------------------------

class TestParseMetric:

    @pytest.mark.parametrize("raw,expected", [
        ("1.5", 1.5),
        ("-2.0", -2.0),
        ("0", 0.0),
        ("1.5%", 1.5),
        (" 3.25 % ", 3.25),
        ("200K", 200_000.0),
        ("200k", 200_000.0),
        ("3M", 3_000_000.0),
        ("2.5B", 2_500_000_000.0),
        ("1.25b", 1_250_000_000.0),
        ("-4K", -4_000.0),
    ])
    def test_parses(self, raw, expected):
        assert _parse_metric(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", ["", "   ", "N/A", "Pending", "Upcoming", "—",
                                     "abc", None, "1,234", "1.2.3"])
    def test_returns_none_for_anything_unparseable(self, raw):
        assert _parse_metric(raw) is None

    def test_a_thousand_separator_is_not_understood(self):
        """🔴 Non-ForexFactory feeds write NFP as '235K' (fine) but payrolls as
        '1,234' (not fine). Both become 'Unparseable USD event data' and the
        event is dropped with no directional assumption."""
        assert _parse_metric("1,234") is None
        assert _parse_metric("235K") == 235_000.0


class TestMacroAnalyst:

    def run(self, symbol="XAUUSD", calendar=(), session=None, structure=None,
            reg=None, **_):
        return MacroAnalyst(news_calendar=list(calendar)).analyze(
            ctx(symbol=symbol,
                structure=structure or StructureContext(bias="NEUTRAL"),
                session=session or SessionContext()),
            regime(reg or MarketRegime.TREND_BULL))

    def test_the_base_is_65(self):
        assert self.run().score == 65.0

    def test_a_prime_session_adds_15(self):
        r = self.run(session=SessionContext(current_session="LONDON", is_prime_session=True))
        assert r.score == 80.0

    def test_the_asian_session_costs_10(self):
        r = self.run(session=SessionContext(current_session="ASIAN", is_prime_session=False))
        assert r.score == 55.0
        assert any("Asian session" in f for f in r.risk_factors)

    def test_off_hours_that_is_not_asian_costs_nothing(self):
        """Only ASIAN is penalised. OFF_HOURS — the default — gets a note in
        `evidence` and a full score."""
        r = self.run(session=SessionContext(current_session="OFF_HOURS", is_prime_session=False))
        assert r.score == 65.0
        assert r.risk_factors == []
        assert any("Off-hours" in e for e in r.evidence)

    @pytest.mark.parametrize("actual", ["", "Upcoming", "—", "Pending"])
    def test_an_unreleased_high_impact_usd_event_costs_5(self, actual):
        r = self.run(calendar=[{"currency": "USD", "impact": "HIGH", "event": "NFP",
                                "actual": actual, "forecast": "200K"}])
        assert r.score == 60.0
        assert any("Upcoming HIGH-impact" in f for f in r.risk_factors)

    def test_each_pending_event_is_charged_separately(self):
        cal = [{"currency": "USD", "impact": "HIGH", "event": f"E{i}",
                "actual": "", "forecast": "1"} for i in range(3)]
        assert self.run(calendar=cal).score == 50.0

    def test_a_strong_usd_print_turns_gold_bearish(self):
        r = self.run(symbol="XAUUSD", calendar=[
            {"currency": "USD", "impact": "HIGH", "event": "CPI",
             "actual": "3.5%", "forecast": "3.1%"}])
        assert r.bias == "BEARISH" and r.score == 85.0

    def test_a_weak_usd_print_turns_gold_bullish(self):
        r = self.run(symbol="XAUUSD", calendar=[
            {"currency": "USD", "impact": "HIGH", "event": "CPI",
             "actual": "2.9%", "forecast": "3.1%"}])
        assert r.bias == "BULLISH" and r.score == 85.0

    def test_an_inverse_indicator_is_flipped(self):
        """Higher jobless claims = weaker USD = *bullish* gold."""
        r = self.run(symbol="XAUUSD", calendar=[
            {"currency": "USD", "impact": "HIGH", "event": "Initial Jobless Claims",
             "actual": "250K", "forecast": "220K"}])
        assert r.bias == "BULLISH" and r.score == 85.0

    @pytest.mark.parametrize("event", ["Unemployment Rate", "Trade Deficit",
                                       "Budget Deficit", "Jobless Claims"])
    def test_every_inverse_keyword_is_detected(self, event):
        r = self.run(calendar=[{"currency": "USD", "impact": "HIGH", "event": event,
                                "actual": "5", "forecast": "4"}])
        assert r.bias == "BULLISH"

    def test_usd_jpy_goes_the_other_way(self):
        r = self.run(symbol="USDJPY", calendar=[
            {"currency": "USD", "impact": "HIGH", "event": "CPI",
             "actual": "3.5%", "forecast": "3.1%"}])
        assert r.bias == "BULLISH" and r.score == 85.0

    def test_usd_cad_goes_the_other_way_too(self):
        r = self.run(symbol="USDCAD", calendar=[
            {"currency": "USD", "impact": "HIGH", "event": "CPI",
             "actual": "2.9%", "forecast": "3.1%"}])
        assert r.bias == "BEARISH"

    def test_a_symbol_outside_both_maps_gets_no_direction(self):
        r = self.run(symbol="AUDNZD", calendar=[
            {"currency": "USD", "impact": "HIGH", "event": "CPI",
             "actual": "3.5%", "forecast": "3.1%"}])
        assert r.bias == "NEUTRAL" and r.score == 65.0

    @pytest.mark.parametrize("currency,impact", [("EUR", "HIGH"), ("USD", "LOW"),
                                                 ("USD", "MEDIUM"), ("GBP", "LOW")])
    def test_only_high_impact_usd_events_move_the_score(self, currency, impact):
        r = self.run(calendar=[{"currency": currency, "impact": impact, "event": "X",
                                "actual": "5", "forecast": "1"}])
        assert r.score == 65.0 and r.bias == "NEUTRAL"

    def test_an_unparseable_print_makes_no_directional_assumption(self):
        r = self.run(calendar=[{"currency": "USD", "impact": "HIGH", "event": "NFP",
                                "actual": "N/A", "forecast": "200K"}])
        assert r.bias == "NEUTRAL"
        assert r.score == 65.0
        assert any("Unparseable" in f for f in r.risk_factors)

    def test_an_unparseable_forecast_also_makes_no_assumption(self):
        r = self.run(calendar=[{"currency": "USD", "impact": "HIGH", "event": "NFP",
                                "actual": "200K", "forecast": ""}])
        assert r.bias == "NEUTRAL" and r.score == 65.0

    def test_a_print_exactly_on_forecast_is_no_shock(self):
        r = self.run(calendar=[{"currency": "USD", "impact": "HIGH", "event": "CPI",
                                "actual": "3.1%", "forecast": "3.1%"}])
        assert r.bias == "NEUTRAL" and r.score == 65.0

    def test_the_symbol_match_is_a_substring_scan(self):
        """Same family as the COPPER→SouthernCopper bug: `any(k in sym ...)`.
        Pin the intended hits, and one unintended one."""
        for sym in ("XAUUSD", "GOLD", "EURUSD", "GBPUSD", "BTCUSD"):
            assert self.run(symbol=sym, calendar=[
                {"currency": "USD", "impact": "HIGH", "event": "CPI",
                 "actual": "5", "forecast": "1"}]).bias == "BEARISH"

    def test_with_no_shock_the_bias_falls_back_to_structure(self):
        r = self.run(structure=StructureContext(bias="BULLISH"))
        assert r.bias == "BULLISH"
        assert any("aligning with BULLISH structure bias" in e for e in r.evidence)

    def test_a_shock_overrides_the_structure_fallback(self):
        r = self.run(structure=StructureContext(bias="BULLISH"), calendar=[
            {"currency": "USD", "impact": "HIGH", "event": "CPI",
             "actual": "5", "forecast": "1"}])
        assert r.bias == "BEARISH"

    def test_event_risk_assigns_30_and_discards_everything_earned(self):
        """`score = 30.0`, not `-=`: a prime session plus a confirmed shock
        still lands on exactly 30. One analyst at 30 drags the mean by ~5.8."""
        r = self.run(session=SessionContext(current_session="LONDON", is_prime_session=True),
                     calendar=[{"currency": "USD", "impact": "HIGH", "event": "CPI",
                                "actual": "5", "forecast": "1"}],
                     reg=MarketRegime.EVENT_RISK)
        assert r.score == 30.0
        assert any("High-impact economic event" in f for f in r.risk_factors)

    def test_other_regimes_do_not_touch_the_score(self):
        for rg in (MarketRegime.POST_EVENT, MarketRegime.HIGH_VOLATILITY,
                   MarketRegime.LIQUIDITY_STRESS, MarketRegime.RANGE):
            assert self.run(reg=rg).score == 65.0

    def test_only_a_missing_calendar_consults_the_global_news_engine(self):
        """`news_calendar` used to be coalesced with `or []`, so `[]` was
        indistinguishable from `None` and there was no way to say 'no news' —
        both fell through to `GLOBAL_NEWS_ENGINE`, a live lookup. Only `None`
        (i.e. plain `MacroAnalyst()`, the sole production caller at
        `parallel_runner.py:47`) should reach the network now."""
        import jarvis.market.news as news
        seen = []
        orig = news.GLOBAL_NEWS_ENGINE.get_news_calendar

        def spy(force_refresh=False):
            seen.append(force_refresh)
            return [{"currency": "USD", "impact": "HIGH", "event": "CPI",
                     "actual": "5", "forecast": "1"}]

        news.GLOBAL_NEWS_ENGINE.get_news_calendar = spy
        try:
            assert MacroAnalyst().analyze(ctx(), regime()).bias == "BEARISH"
            assert len(seen) == 1

            assert MacroAnalyst(news_calendar=[]).analyze(ctx(), regime()).bias == "NEUTRAL"
            assert len(seen) == 1

            assert MacroAnalyst(news_calendar=[{"currency": "EUR", "impact": "HIGH",
                                                "event": "X", "actual": "5",
                                                "forecast": "1"}]).analyze(
                ctx(), regime()).bias == "NEUTRAL"
            assert len(seen) == 1
        finally:
            news.GLOBAL_NEWS_ENGINE.get_news_calendar = orig

    def test_a_shock_that_does_not_apply_to_the_symbol_still_blocks_the_fallback(self):
        """AUDNZD is in neither symbol map, so a USD shock sets no bias — yet
        because *a* shock was detected the structure fallback is skipped and the
        report stays NEUTRAL. Pinning current behaviour, not endorsing it."""
        r = self.run(symbol="AUDNZD", structure=StructureContext(bias="BULLISH"),
                     calendar=[{"currency": "USD", "impact": "HIGH", "event": "CPI",
                                "actual": "5", "forecast": "1"}])
        assert r.bias == "NEUTRAL"

    def test_a_news_engine_failure_degrades_to_no_news(self):
        import jarvis.market.news as news
        orig = news.GLOBAL_NEWS_ENGINE.get_news_calendar

        def boom(force_refresh=False):
            raise RuntimeError("feed down")

        news.GLOBAL_NEWS_ENGINE.get_news_calendar = boom
        try:
            r = MacroAnalyst().analyze(ctx(), regime())
        finally:
            news.GLOBAL_NEWS_ENGINE.get_news_calendar = orig
        assert r.score == 65.0 and r.bias == "NEUTRAL"
        assert r.risk_factors == []

    def test_metadata_carries_the_session_and_shock(self):
        r = self.run(session=SessionContext(current_session="LONDON", is_prime_session=True),
                     calendar=[{"currency": "USD", "impact": "HIGH", "event": "CPI",
                                "actual": "5", "forecast": "1"}])
        assert r.metadata == {"session": "LONDON", "is_prime": True, "usd_bull_shock": True}

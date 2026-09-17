"""Tests for jarvis.intelligence.ai_dissector.AIDissector.

`decision_engine:848` calls `dissect()` on every decision and adds its
`prob_boost` to both win probabilities — so unlike master_confluence this one is
boost-only, no gate. It had no tests.

Two things this suite pins rather than fixes, because both are calibration
decisions rather than clear bugs:

* **The total is normalised by 105, but the maximum achievable is 99.** Three of
  the seven pillars (momentum, MTF, order flow) top out at 13, not the 15 the
  docstring claims for each. So `dissection_score` can never exceed 94.3, and
  the 70 / 55 tier bands sit ~6% stricter than the docstring implies.
* **Volatility has a floor of 7.** `v = 7` is assigned before `if vol:`, so a
  missing volatility block scores 7/15, and even EXTREME volatility with an
  excessive spread scores 7/15. Every other pillar treats missing data as 0.

Also pinned: `regime` and `calibrated_win_p` are accepted and never read, `atr`
is computed and never used, `reasons` is populated for one pillar out of seven,
and `is_high_quality` has no callers and thresholds at 40 — a band `dissect`
itself never produces.
"""

from types import SimpleNamespace

import pytest

from jarvis.intelligence.ai_dissector import AIDissector

PILLARS = {
    "structure", "momentum", "liquidity", "volatility",
    "mtf", "orderflow", "risk_reward",
}

MAX_STRUCTURE = SimpleNamespace(
    bos=True, choch=True, bias="BULLISH",
    higher_highs=True, higher_lows=True, lower_highs=True, lower_lows=True,
    demand_zone=(1.0, 2.0), supply_zone=(1.0, 2.0),
)
MAX_MOMENTUM = SimpleNamespace(trend_score=50.0, adx=30.0)
MAX_LIQUIDITY = SimpleNamespace(sweep_detected=True, sweep_magnitude=2.0,
                                equal_highs=[1.0], equal_lows=[1.0])
MAX_VOL = SimpleNamespace(atr=1.0, state="NORMAL", is_excessive_spread=False)
MAX_OF = {"institutional_activity": True, "delta_score": 40.0}
MAX_MTF_ALIGN = {"D1": "BULLISH", "H4": "BULLISH", "H1": "BULLISH"}


def ctx(structure=None, momentum=None, liquidity=None, volatility=None,
        order_flow=None, mtf_confluence_score=0.0, mtf_alignment=None):
    return SimpleNamespace(
        structure=structure, momentum=momentum, liquidity=liquidity,
        volatility=volatility, order_flow=order_flow if order_flow is not None else {},
        mtf_confluence_score=mtf_confluence_score,
        mtf_alignment=mtf_alignment if mtf_alignment is not None else {},
    )


def dissect(context, regime="RANGE", rr=0.0, ev=0.0, ai=0.0, cwp=0.5):
    return AIDissector().dissect(context, regime, rr, ev, ai, cwp)


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------

class TestResultShape:
    def test_returns_the_keys_the_decision_engine_reads(self):
        r = dissect(ctx())
        assert {"scores", "total", "dissection_score", "tier", "prob_boost", "reasons"} <= set(r)

    def test_all_seven_pillars_are_always_present(self):
        assert set(dissect(ctx())["scores"]) == PILLARS

    def test_total_is_the_sum_of_the_pillars(self):
        r = dissect(ctx(structure=MAX_STRUCTURE, momentum=MAX_MOMENTUM))
        assert r["total"] == sum(r["scores"].values())

    def test_no_pillar_exceeds_fifteen(self):
        r = dissect(ctx(structure=MAX_STRUCTURE, momentum=MAX_MOMENTUM,
                        liquidity=MAX_LIQUIDITY, volatility=MAX_VOL,
                        order_flow=MAX_OF, mtf_confluence_score=60.0,
                        mtf_alignment=MAX_MTF_ALIGN),
                    rr=3.5, ev=10.0, ai=85.0)
        for name, v in r["scores"].items():
            assert v <= 15, name

    def test_pillars_are_never_negative(self):
        r = dissect(ctx(volatility=SimpleNamespace(state="EXTREME", is_excessive_spread=True)),
                    rr=-5.0, ev=-10.0, ai=0.0)
        assert all(v >= 0 for v in r["scores"].values())

    def test_a_none_context_does_not_raise(self):
        r = dissect(None)
        assert set(r["scores"]) == PILLARS

    def test_the_score_is_rounded_to_one_decimal(self):
        r = dissect(ctx(structure=MAX_STRUCTURE))
        assert r["dissection_score"] == round(r["dissection_score"], 1)


# ---------------------------------------------------------------------------
# The 105 vs 99 normalisation
# ---------------------------------------------------------------------------

class TestNormalisation:
    def _maxed(self):
        return dissect(
            ctx(structure=MAX_STRUCTURE, momentum=MAX_MOMENTUM, liquidity=MAX_LIQUIDITY,
                volatility=MAX_VOL, order_flow=MAX_OF, mtf_confluence_score=60.0,
                mtf_alignment=MAX_MTF_ALIGN),
            rr=3.5, ev=10.0, ai=85.0,
        )

    def test_the_maximum_total_is_99_not_105(self):
        """Momentum, MTF and order flow each top out at 13, not 15."""
        r = self._maxed()
        assert r["total"] == 99
        assert r["scores"]["momentum"] == 13
        assert r["scores"]["mtf"] == 13
        assert r["scores"]["orderflow"] == 13

    def test_the_score_can_never_reach_100(self):
        """Every pillar maxed still normalises to 94.3, not 100."""
        assert self._maxed()["dissection_score"] == 94.3

    def test_the_score_is_the_total_over_105(self):
        for c in (ctx(), ctx(structure=MAX_STRUCTURE), self._maxed() and ctx(momentum=MAX_MOMENTUM)):
            r = dissect(c)
            assert r["dissection_score"] == round((r["total"] / 105.0) * 100.0, 1)

    def test_a_maxed_setup_is_still_only_HIGH(self):
        assert self._maxed()["tier"] == "HIGH"


# ---------------------------------------------------------------------------
# Tier bands
# ---------------------------------------------------------------------------

class TestTier:
    def test_an_empty_context_is_weak_but_not_zero(self):
        """No evidence at all still scores 6.7, because volatility floors at 7.

        This is the one tier test where the floor is visible end-to-end: a setup
        with no structure, no momentum, no liquidity, no volatility, no MTF, no
        order flow and no R:R is not 0/100, it is 6.7/100.
        """
        r = dissect(ctx())
        assert r["total"] == 7
        assert r["dissection_score"] == 6.7
        assert r["tier"] == "WEAK"
        assert r["prob_boost"] == 0.0

    def test_the_HIGH_boundary(self):
        """73/105 -> 69.5 (MODERATE); 74/105 -> 70.5 (HIGH).

        Built from structure+momentum+mtf+orderflow (54) + volatility floor (7)
        + a risk/reward pillar tuned to 12 or 13.
        """
        base = ctx(structure=MAX_STRUCTURE, momentum=MAX_MOMENTUM,
                   order_flow=MAX_OF, mtf_confluence_score=60.0,
                   mtf_alignment=MAX_MTF_ALIGN,
                   volatility=SimpleNamespace(state="EXTREME", is_excessive_spread=True))
        below = dissect(base, rr=3.0, ev=5.0, ai=50.0)   # rr pillar 7+5+0 = 12
        above = dissect(base, rr=3.0, ev=5.0, ai=75.0)   # +1 for ai >= 70
        assert below["total"] == 73 and below["dissection_score"] == 69.5
        assert below["tier"] == "MODERATE" and below["prob_boost"] == 0.01
        assert above["total"] == 74 and above["dissection_score"] == 70.5
        assert above["tier"] == "HIGH" and above["prob_boost"] == 0.03

    def test_the_MODERATE_boundary(self):
        """57/105 -> 54.3 (WEAK); 58/105 -> 55.2 (MODERATE).

        structure 15 + momentum 13 + liquidity 15 + volatility floor 7 = 50, and
        risk/reward fills the remaining 7 or 8 (rr>=3 alone, then +1 for ai>=70).
        """
        base = ctx(structure=MAX_STRUCTURE, momentum=MAX_MOMENTUM,
                   liquidity=MAX_LIQUIDITY,
                   volatility=SimpleNamespace(state="EXTREME", is_excessive_spread=True))
        below = dissect(base, rr=3.0, ev=0.0, ai=50.0)   # rr pillar 7
        above = dissect(base, rr=3.0, ev=0.0, ai=75.0)   # +1 for ai >= 70
        assert below["total"] == 57 and below["dissection_score"] == 54.3
        assert below["tier"] == "WEAK" and below["prob_boost"] == 0.0
        assert above["total"] == 58 and above["dissection_score"] == 55.2
        assert above["tier"] == "MODERATE" and above["prob_boost"] == 0.01

    def test_the_tier_always_matches_the_score(self):
        cases = [
            (ctx(), 0.0, 0.0),
            (ctx(structure=MAX_STRUCTURE), 0.0, 0.0),
            (ctx(structure=MAX_STRUCTURE, momentum=MAX_MOMENTUM), 3.0, 0.0),
            (ctx(structure=MAX_STRUCTURE, momentum=MAX_MOMENTUM, liquidity=MAX_LIQUIDITY,
                 volatility=MAX_VOL), 3.5, 85.0),
        ]
        for c, rr, ai in cases:
            r = dissect(c, rr=rr, ev=5.0, ai=ai)
            expected = ("HIGH" if r["dissection_score"] >= 70
                        else "MODERATE" if r["dissection_score"] >= 55 else "WEAK")
            assert r["tier"] == expected

    def test_prob_boost_tracks_the_tier(self):
        for c, rr, ai in [(ctx(), 0.0, 0.0),
                          (ctx(structure=MAX_STRUCTURE, momentum=MAX_MOMENTUM), 3.0, 0.0)]:
            r = dissect(c, rr=rr, ev=5.0, ai=ai)
            assert r["prob_boost"] == {"HIGH": 0.03, "MODERATE": 0.01, "WEAK": 0.0}[r["tier"]]


# ---------------------------------------------------------------------------
# 1. Structure
# ---------------------------------------------------------------------------

class TestStructure:
    def st(self, **kw):
        base = dict(bos=False, choch=False, bias="NEUTRAL", higher_highs=False,
                    higher_lows=False, lower_highs=False, lower_lows=False,
                    demand_zone=(0, 0), supply_zone=(0, 0))
        base.update(kw)
        return SimpleNamespace(**base)

    def test_nothing_scores_zero(self):
        assert dissect(ctx(structure=self.st()))["scores"]["structure"] == 0

    @pytest.mark.parametrize("kw,expected", [
        ({"bos": True}, 5),
        ({"choch": True}, 4),
        ({"bias": "BULLISH"}, 3),
        ({"bias": "BEARISH"}, 3),
        ({"higher_highs": True}, 2),
        ({"higher_lows": True}, 2),
        ({"lower_highs": True}, 2),
        ({"lower_lows": True}, 2),
        ({"demand_zone": (1.0, 2.0)}, 1),
        ({"supply_zone": (1.0, 2.0)}, 1),
    ])
    def test_each_signal(self, kw, expected):
        assert dissect(ctx(structure=self.st(**kw)))["scores"]["structure"] == expected

    def test_a_neutral_bias_scores_nothing(self):
        assert dissect(ctx(structure=self.st(bias="NEUTRAL")))["scores"]["structure"] == 0

    def test_the_pillar_is_capped_at_fifteen(self):
        assert dissect(ctx(structure=MAX_STRUCTURE))["scores"]["structure"] == 15

    def test_a_malformed_zone_does_not_raise(self):
        assert dissect(ctx(structure=self.st(demand_zone=None)))["scores"]["structure"] == 0

    def test_reasons_describes_the_structure(self):
        r = dissect(ctx(structure=self.st(bos=True, bias="BULLISH")))
        assert "BOS=True" in r["reasons"]["structure"]
        assert "BULLISH" in r["reasons"]["structure"]


# ---------------------------------------------------------------------------
# 2. Momentum
# ---------------------------------------------------------------------------

class TestMomentum:
    @pytest.mark.parametrize("ts,adx,expected", [
        (0, 0, 0),
        (10, 0, 2), (-10, 0, 2),        # magnitude, not direction
        (20, 0, 4),
        (40, 0, 6),
        (0, 15, 1),
        (0, 20, 3),
        (0, 25, 5),
        (20, 20, 4 + 3 + 2),            # plus the combination bonus
        (50, 30, 6 + 5 + 2),
    ])
    def test_score_and_adx_combinations(self, ts, adx, expected):
        r = dissect(ctx(momentum=SimpleNamespace(trend_score=ts, adx=adx)))
        assert r["scores"]["momentum"] == expected

    def test_the_combination_bonus_needs_both(self):
        assert dissect(ctx(momentum=SimpleNamespace(trend_score=25, adx=15)))["scores"]["momentum"] == 4 + 1

    def test_the_pillar_maxes_at_thirteen(self):
        r = dissect(ctx(momentum=SimpleNamespace(trend_score=99, adx=99)))
        assert r["scores"]["momentum"] == 13


# ---------------------------------------------------------------------------
# 3. Liquidity
# ---------------------------------------------------------------------------

class TestLiquidity:
    def test_nothing_scores_zero(self):
        assert dissect(ctx(liquidity=SimpleNamespace()))["scores"]["liquidity"] == 0

    def test_a_sweep(self):
        r = dissect(ctx(liquidity=SimpleNamespace(sweep_detected=True)))
        assert r["scores"]["liquidity"] == 7

    @pytest.mark.parametrize("mag,expected", [(0.0, 7), (0.99, 7), (1.0, 10), (5.0, 10)])
    def test_sweep_magnitude_threshold(self, mag, expected):
        r = dissect(ctx(liquidity=SimpleNamespace(sweep_detected=True, sweep_magnitude=mag)))
        assert r["scores"]["liquidity"] == expected

    def test_equal_highs_or_lows(self):
        for kw in ({"equal_highs": [1.0]}, {"equal_lows": [1.0]}):
            r = dissect(ctx(liquidity=SimpleNamespace(**kw)))
            assert r["scores"]["liquidity"] == 2

    def test_a_sweep_confirmed_by_a_break_of_structure(self):
        r = dissect(ctx(liquidity=SimpleNamespace(sweep_detected=True),
                        structure=SimpleNamespace(bos=True)))
        assert r["scores"]["liquidity"] == 7 + 3

    def test_the_pillar_maxes_at_fifteen(self):
        r = dissect(ctx(liquidity=MAX_LIQUIDITY, structure=MAX_STRUCTURE))
        assert r["scores"]["liquidity"] == 15


# ---------------------------------------------------------------------------
# 4. Volatility — the floor
# ---------------------------------------------------------------------------

class TestVolatility:
    def test_no_volatility_context_still_scores_seven(self):
        """`v = 7` is assigned before `if vol:` — no data scores 7/15.

        Every other pillar treats a missing block as 0; this one awards roughly
        half credit for an absent measurement.
        """
        assert dissect(ctx())["scores"]["volatility"] == 7

    def test_the_worst_volatility_also_scores_seven(self):
        """EXTREME volatility plus an excessive spread is still 7/15 — a floor."""
        r = dissect(ctx(volatility=SimpleNamespace(state="EXTREME", is_excessive_spread=True)))
        assert r["scores"]["volatility"] == 7

    @pytest.mark.parametrize("state,spread,expected", [
        ("NORMAL", False, 15),
        ("EXPANSION", False, 15),
        ("COMPRESSION", False, 12),
        ("EXTREME", False, 11),
        ("NORMAL", True, 11),
        ("EXTREME", True, 7),
        ("UNKNOWN_STATE", False, 11),   # falls through every branch
    ])
    def test_state_and_spread(self, state, spread, expected):
        r = dissect(ctx(volatility=SimpleNamespace(state=state, is_excessive_spread=spread)))
        assert r["scores"]["volatility"] == expected

    def test_the_pillar_maxes_at_fifteen(self):
        assert dissect(ctx(volatility=MAX_VOL))["scores"]["volatility"] == 15

    def test_atr_is_read_but_never_used(self):
        """The pillar scores "ATR state" but only looks at `state` and the spread."""
        low = dissect(ctx(volatility=SimpleNamespace(atr=0.001, state="NORMAL")))
        high = dissect(ctx(volatility=SimpleNamespace(atr=999.0, state="NORMAL")))
        assert low["scores"]["volatility"] == high["scores"]["volatility"]


# ---------------------------------------------------------------------------
# 5. MTF
# ---------------------------------------------------------------------------

class TestMTF:
    @pytest.mark.parametrize("score,expected", [
        (0, 0), (14.9, 0), (15, 2), (29.9, 2), (30, 5), (49.9, 5), (50, 8), (-50, 8),
    ])
    def test_confluence_score_bands(self, score, expected):
        assert dissect(ctx(mtf_confluence_score=score))["scores"]["mtf"] == expected

    @pytest.mark.parametrize("alignment,expected", [
        ({}, 0),
        ({"H4": "BULLISH"}, 0),
        ({"H4": "BULLISH", "H1": "BULLISH"}, 2),
        ({"H4": "BULLISH", "H1": "BULLISH", "M15": "BULLISH"}, 5),
        ({"H4": "BULLISH", "H1": "BEARISH", "M15": "BULLISH"}, 2),
        # A third direction must not be counted as alignment. `!= "BEARISH"`
        # would score this 5; `== "BULLISH"` scores it 0.
        ({"H4": "BULLISH", "H1": "NEUTRAL", "M15": "NEUTRAL"}, 0),
        ({"H4": "NEUTRAL", "H1": "NEUTRAL", "M15": "NEUTRAL"}, 0),
    ])
    def test_alignment_counts_the_dominant_direction(self, alignment, expected):
        assert dissect(ctx(mtf_alignment=alignment))["scores"]["mtf"] == expected

    def test_the_pillar_maxes_at_thirteen(self):
        r = dissect(ctx(mtf_confluence_score=60.0, mtf_alignment=MAX_MTF_ALIGN))
        assert r["scores"]["mtf"] == 13


# ---------------------------------------------------------------------------
# 6. Order flow
# ---------------------------------------------------------------------------

class TestOrderFlow:
    def test_nothing_scores_zero(self):
        assert dissect(ctx())["scores"]["orderflow"] == 0

    def test_institutional_activity_alone(self):
        assert dissect(ctx(order_flow={"institutional_activity": True}))["scores"]["orderflow"] == 6

    @pytest.mark.parametrize("delta,expected", [
        (0, 0), (19.9, 0), (20, 3), (34.9, 3), (35, 5), (-35, 5),
    ])
    def test_delta_bands(self, delta, expected):
        assert dissect(ctx(order_flow={"delta_score": delta}))["scores"]["orderflow"] == expected

    def test_the_pillar_maxes_at_thirteen(self):
        assert dissect(ctx(order_flow=MAX_OF))["scores"]["orderflow"] == 13

    def test_a_non_dict_order_flow_scores_zero(self):
        assert dissect(ctx(order_flow=["not", "a", "dict"]))["scores"]["orderflow"] == 0


# ---------------------------------------------------------------------------
# 7. Risk / reward
# ---------------------------------------------------------------------------

class TestRiskReward:
    @pytest.mark.parametrize("rr,expected", [(0.0, 0), (1.4, 0), (1.5, 1), (2.0, 3), (2.4, 3),
                                             (2.5, 5), (2.9, 5), (3.0, 7), (9.0, 7)])
    def test_rr_bands(self, rr, expected):
        assert dissect(ctx(), rr=rr)["scores"]["risk_reward"] == expected

    @pytest.mark.parametrize("ev,expected", [(0.0, 0), (-1.0, 0), (0.5, 1), (1.0, 3),
                                             (4.9, 3), (5.0, 5), (100.0, 5)])
    def test_ev_bands(self, ev, expected):
        assert dissect(ctx(), ev=ev)["scores"]["risk_reward"] == expected

    @pytest.mark.parametrize("ai,expected", [(0.0, 0), (69.9, 0), (70.0, 1), (79.9, 1), (80.0, 3)])
    def test_ai_score_bands(self, ai, expected):
        assert dissect(ctx(), ai=ai)["scores"]["risk_reward"] == expected

    def test_the_pillar_maxes_at_fifteen(self):
        assert dissect(ctx(), rr=3.0, ev=5.0, ai=80.0)["scores"]["risk_reward"] == 15


# ---------------------------------------------------------------------------
# Arguments that do not change the answer
# ---------------------------------------------------------------------------

class TestUnusedInputs:
    def test_regime_is_accepted_but_never_read(self):
        c = ctx(structure=MAX_STRUCTURE, momentum=MAX_MOMENTUM)
        assert dissect(c, regime="RANGE") == dissect(c, regime="TREND_BULL")

    def test_calibrated_win_p_is_accepted_but_never_read(self):
        c = ctx(structure=MAX_STRUCTURE, momentum=MAX_MOMENTUM)
        assert dissect(c, cwp=0.05) == dissect(c, cwp=0.95)

    def test_a_none_regime_does_not_raise(self):
        assert dissect(ctx(), regime=None)["total"] >= 0


# ---------------------------------------------------------------------------
# is_high_quality — no callers, and a threshold dissect never produces
# ---------------------------------------------------------------------------

class TestIsHighQuality:
    def test_the_threshold_is_forty(self):
        ok, msg = AIDissector().is_high_quality(40.0, "eurusd")
        assert ok is True
        assert "EURUSD" in msg

    def test_below_the_threshold(self):
        ok, msg = AIDissector().is_high_quality(39.9, "EURUSD")
        assert ok is False
        assert "low confluence" in msg

    def test_the_threshold_is_not_any_tier_band(self):
        """dissect() produces WEAK / MODERATE / HIGH at 0 / 55 / 70; 40 is neither."""
        for score in (0.0, 54.9, 55.0, 69.9, 70.0):
            expected = score >= 40.0
            assert AIDissector().is_high_quality(score, "X") [0] is expected

"""Tests for jarvis.intelligence.master_confluence.MasterConfluenceEngine.

`decision_engine:857` calls `score()` and then gates the trade on
`_master_score >= _min_confluence` (18-24 depending on style and asset), so this
is not a descriptive score — a zero here **blocks the trade**.

The module had no tests. The defect that motivated them: `reg`, `st` and `liq`
were bound *inside* the first `try`, and every later block has its own
`except Exception: <component> = 0`. So a single unreadable `regime` raised in
block one, left those names unbound, and each following block then died with a
`NameError` that its own handler swallowed — one bad input silently collapsed
the entire score to 0 instead of zeroing one component.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from jarvis.intelligence.master_confluence import MasterConfluenceEngine

UTC = timezone.utc
WED = datetime(2026, 1, 7, tzinfo=UTC)          # a Wednesday
IN_KILLZONE = WED.replace(hour=13)              # 13:00 UTC -> NY_OPEN
PRIME_NOT_KILLZONE = WED.replace(hour=11)       # 11:00 UTC -> prime, no killzone
DEAD_HOUR = WED.replace(hour=22)                # 22:00 UTC -> neither

COMPONENTS = {
    "wyckoff_ict_fusion",
    "minervini_trend_template",
    "vcp",
    "ict_killzone_amd",
    "triple_confluence",
}


def ctx(**over):
    """A MarketContext stand-in. Only getattr is used, so a namespace is enough."""
    base = dict(
        timestamp=PRIME_NOT_KILLZONE,
        structure=SimpleNamespace(bos=False, choch=False, bias="NEUTRAL",
                                  fair_value_gaps=[], order_blocks=[]),
        liquidity=SimpleNamespace(sweep_detected=False),
        momentum=SimpleNamespace(trend_score=0, adx=0),
        volatility=SimpleNamespace(state="NORMAL"),
        session=SimpleNamespace(utc_hour=11, is_prime_session=True),
        mtf_alignment={},
        symbol="EURUSD",
    )
    base.update(over)
    return SimpleNamespace(**base)


def regime(name="RANGE"):
    return SimpleNamespace(primary_regime=name)


class _FakeRegimeEnum:
    """Stands in for a MarketRegime member: not a str, has `.value`."""

    def __init__(self, value):
        self.value = value

    def __str__(self):
        return f"MarketRegime.{self.value}"


def score(context, reg=None, rr=1.0, ai=50.0, mtf=None):
    return MasterConfluenceEngine().score(context, reg, rr, ai, mtf)


def closes_frame(values):
    return pd.DataFrame({"close": values, "open": values, "high": values, "low": values})


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------

class TestResultShape:
    def test_returns_the_five_keys_the_decision_engine_reads(self):
        r = score(ctx())
        assert {"breakdown", "total", "tier", "prob_boost", "details"} <= set(r)

    def test_total_is_the_sum_of_the_breakdown(self):
        for c in (ctx(), ctx(structure=SimpleNamespace(bos=True, choch=True, bias="BULLISH"))):
            r = score(c, regime("TREND_BULL"))
            assert r["total"] == sum(r["breakdown"].values())

    def test_every_component_is_always_present(self):
        """A block that raises must still leave its key in the breakdown."""
        r = score(None)
        assert set(r["breakdown"]) == COMPONENTS

    def test_no_component_exceeds_twenty(self):
        strong = ctx(
            timestamp=IN_KILLZONE,
            structure=SimpleNamespace(bos=True, choch=True, bias="BULLISH",
                                      fair_value_gaps=[1], order_blocks=[1]),
            liquidity=SimpleNamespace(sweep_detected=True),
            momentum=SimpleNamespace(trend_score=40, adx=40),
            mtf_alignment={"H4": "BULLISH", "H1": "BULLISH", "M15": "BULLISH"},
        )
        for v in score(strong, regime("TREND_BULL"), rr=3.0)["breakdown"].values():
            assert v <= 20

    def test_components_are_never_negative(self):
        for v in score(ctx(), regime("TREND_BEAR"))["breakdown"].values():
            assert v >= 0

    def test_a_none_context_does_not_raise(self):
        """A missing context resolves every field to None; only the RANGE
        default (4) can still accrue."""
        r = score(None)
        assert r["total"] == 4
        assert r["tier"] == "WEAK"
        assert set(r["breakdown"]) == COMPONENTS


# ---------------------------------------------------------------------------
# The cascade — the defect this suite exists for
# ---------------------------------------------------------------------------

class TestOneBadInputDoesNotCollapseEverything:
    def test_a_none_regime_does_not_zero_the_other_components(self):
        """A missing regime may cost the regime points; it must not cost all 100."""
        c = ctx(
            timestamp=IN_KILLZONE,
            structure=SimpleNamespace(bos=True, choch=False, bias="BULLISH"),
            momentum=SimpleNamespace(trend_score=30, adx=30),
        )
        r = score(c, reg=None)
        assert r["breakdown"]["ict_killzone_amd"] > 0
        assert r["breakdown"]["minervini_trend_template"] > 0
        assert r["breakdown"]["wyckoff_ict_fusion"] > 0
        assert r["total"] > 0

    def test_a_regime_without_primary_regime_behaves_the_same(self):
        c = ctx(structure=SimpleNamespace(bos=True, bias="BULLISH"))
        assert score(c, reg=SimpleNamespace())["total"] == score(c, reg=None)["total"]

    def test_an_unexpected_regime_type_falls_back_to_range(self):
        """`getattr(raw, "value", "RANGE")` has to tolerate anything."""
        c = ctx(structure=SimpleNamespace(bos=True, bias="BULLISH"))
        weird = SimpleNamespace(primary_regime=12345)
        assert score(c, reg=weird)["breakdown"]["wyckoff_ict_fusion"] == score(
            c, regime("RANGE")
        )["breakdown"]["wyckoff_ict_fusion"]

    def test_a_none_regime_still_scores_the_structure_points(self):
        c = ctx(structure=SimpleNamespace(bos=True, choch=True, bias="BULLISH"))
        # RANGE default 4 + bos 6 + choch 4 + non-neutral bias 2
        assert score(c, reg=None)["breakdown"]["wyckoff_ict_fusion"] == 16

    def test_the_other_blocks_still_run_when_the_first_one_fails(self):
        """Pin the fix: killzone/momentum scoring is independent of the regime."""
        c = ctx(timestamp=IN_KILLZONE, momentum=SimpleNamespace(trend_score=25, adx=28))
        with_regime = score(c, regime("TREND_BULL"))
        without = score(c, reg=None)
        assert without["breakdown"]["ict_killzone_amd"] == with_regime["breakdown"]["ict_killzone_amd"]
        assert without["breakdown"]["minervini_trend_template"] > 0

    def test_details_are_still_populated_without_a_regime(self):
        r = score(ctx(timestamp=IN_KILLZONE), reg=None)
        assert "killzone" in r["details"]


# ---------------------------------------------------------------------------
# Wyckoff / ICT fusion
# ---------------------------------------------------------------------------

class TestWyckoff:
    def test_bull_trend_plus_sweep(self):
        c = ctx(liquidity=SimpleNamespace(sweep_detected=True))
        r = score(c, regime("TREND_BULL"))
        assert r["breakdown"]["wyckoff_ict_fusion"] == 8
        assert "Spring" in r["details"]["wyckoff"]

    def test_bear_trend_plus_sweep(self):
        c = ctx(liquidity=SimpleNamespace(sweep_detected=True))
        r = score(c, regime("TREND_BEAR"))
        assert r["breakdown"]["wyckoff_ict_fusion"] == 8
        assert "Upthrust" in r["details"]["wyckoff"]

    def test_a_sweep_alone_scores_nothing(self):
        assert score(ctx(liquidity=SimpleNamespace(sweep_detected=True)),
                     regime("RANGE"))["breakdown"]["wyckoff_ict_fusion"] == 4

    def test_range_regime_scores_consolidation(self):
        r = score(ctx(), regime("RANGE"))
        assert r["breakdown"]["wyckoff_ict_fusion"] == 4
        assert "consolidation" in r["details"]["wyckoff"]

    @pytest.mark.parametrize("bos,choch,bias,expected", [
        (True, False, "NEUTRAL", 4 + 6),
        (False, True, "NEUTRAL", 4 + 4),
        (False, False, "BULLISH", 4 + 2),
        (True, True, "BULLISH", 4 + 6 + 4 + 2),
    ])
    def test_structure_points_stack(self, bos, choch, bias, expected):
        c = ctx(structure=SimpleNamespace(bos=bos, choch=choch, bias=bias))
        assert score(c, regime("RANGE"))["breakdown"]["wyckoff_ict_fusion"] == expected

    def test_a_string_regime_is_accepted(self):
        """A plain string and an enum member must score identically."""
        c = ctx(liquidity=SimpleNamespace(sweep_detected=True))
        as_str = score(c, SimpleNamespace(primary_regime="TREND_BULL"))
        as_enum = score(c, SimpleNamespace(primary_regime=_FakeRegimeEnum("TREND_BULL")))
        assert as_str["breakdown"]["wyckoff_ict_fusion"] == 8
        assert as_str["breakdown"] == as_enum["breakdown"]

    def test_an_enum_like_regime_is_unwrapped_by_value(self):
        c = ctx(liquidity=SimpleNamespace(sweep_detected=True))
        r = score(c, SimpleNamespace(primary_regime=_FakeRegimeEnum("TREND_BULL")))
        assert r["breakdown"]["wyckoff_ict_fusion"] == 8

    def test_the_component_is_capped_at_twenty(self):
        c = ctx(structure=SimpleNamespace(bos=True, choch=True, bias="BULLISH"),
                liquidity=SimpleNamespace(sweep_detected=True))
        assert score(c, regime("TREND_BULL"))["breakdown"]["wyckoff_ict_fusion"] == 20


# ---------------------------------------------------------------------------
# Minervini trend template
# ---------------------------------------------------------------------------

class TestTrendTemplate:
    # adx contributes on its own ladder: +4 at >=25, else +2 at >=15.
    @pytest.mark.parametrize("ts,adx,expected", [
        (0, 0, 0),
        (10, 10, 4),      # positive trend score, adx below both thresholds
        (10, 20, 10),     # positive trend score 8 + adx>=15 bonus 2
        (10, 25, 12),     # ... with the adx>=25 bonus instead
        (0, 25, 4),       # adx bonus alone
        (0, 15, 2),       # adx in 15..25
    ])
    def test_score_and_adx_combinations(self, ts, adx, expected):
        c = ctx(momentum=SimpleNamespace(trend_score=ts, adx=adx))
        assert score(c, regime("RANGE"))["breakdown"]["minervini_trend_template"] == expected

    def test_bull_regime_with_a_strong_trend_score(self):
        # 8 (ts>0, adx>=20) + 2 (adx>=15) + 4 (bull regime, ts>=20)
        c = ctx(momentum=SimpleNamespace(trend_score=25, adx=20))
        assert score(c, regime("TREND_BULL"))["breakdown"]["minervini_trend_template"] == 14

    def test_bear_regime_with_a_strong_negative_score(self):
        # ts<=0 so no trend-score points; 2 (adx>=15) + 4 (bear regime, ts<=-20)
        c = ctx(momentum=SimpleNamespace(trend_score=-25, adx=20))
        assert score(c, regime("TREND_BEAR"))["breakdown"]["minervini_trend_template"] == 6

    def test_a_break_of_structure_with_momentum(self):
        # 8 + 2 + 4 (bos with |ts|>=20); RANGE earns no regime bonus
        c = ctx(momentum=SimpleNamespace(trend_score=25, adx=20),
                structure=SimpleNamespace(bos=True, choch=False, bias="NEUTRAL"))
        assert score(c, regime("RANGE"))["breakdown"]["minervini_trend_template"] == 14

    def test_the_component_is_capped_at_twenty(self):
        c = ctx(momentum=SimpleNamespace(trend_score=50, adx=40),
                structure=SimpleNamespace(bos=True, choch=False, bias="NEUTRAL"))
        assert score(c, regime("TREND_BULL"))["breakdown"]["minervini_trend_template"] == 20


# ---------------------------------------------------------------------------
# VCP
# ---------------------------------------------------------------------------

class TestVCP:
    def _contracting(self):
        # Three 10-bar segments, each with a materially tighter range.
        seg1 = [100 + (i % 5) * 0.5 for i in range(10)]
        seg2 = [100 + (i % 5) * 0.2 for i in range(10)]
        seg3 = [100 + (i % 5) * 0.08 for i in range(10)]
        return closes_frame(seg1 + seg2 + seg3)

    def test_a_clean_contraction_scores_twelve(self):
        r = score(ctx(), regime("RANGE"), rr=1.0, mtf={"primary": self._contracting()})
        assert r["breakdown"]["vcp"] == 12
        assert "VCP" in r["details"]["vcp"]

    def test_a_soft_contraction_scores_six(self):
        seg1 = [100 + (i % 5) * 0.50 for i in range(10)]
        seg2 = [100 + (i % 5) * 0.45 for i in range(10)]
        seg3 = [100 + (i % 5) * 0.40 for i in range(10)]
        r = score(ctx(), regime("RANGE"), rr=1.0, mtf={"primary": closes_frame(seg1 + seg2 + seg3)})
        assert r["breakdown"]["vcp"] == 6
        assert "soft" in r["details"]["vcp"]

    def test_a_widening_range_scores_nothing(self):
        seg1 = [100 + (i % 5) * 0.1 for i in range(10)]
        seg2 = [100 + (i % 5) * 0.3 for i in range(10)]
        seg3 = [100 + (i % 5) * 0.6 for i in range(10)]
        r = score(ctx(), regime("RANGE"), mtf={"primary": closes_frame(seg1 + seg2 + seg3)})
        assert r["breakdown"]["vcp"] == 0

    def test_no_data_scores_nothing(self):
        assert score(ctx(), regime("RANGE"))["breakdown"]["vcp"] == 0

    def test_a_short_frame_is_skipped(self):
        r = score(ctx(), regime("RANGE"), mtf={"primary": closes_frame(list(range(20)))})
        assert r["breakdown"]["vcp"] == 0

    def test_fewer_than_thirty_bars_is_not_enough(self):
        """VCP reads three 10-bar segments, so 25 bars must not score.

        A 20-bar frame also fails, but only because the empty third segment
        raises — this one would produce a real (wrong) score if the length
        guard were loosened, so it is the case that pins the threshold.
        """
        seg1 = [100 + (i % 5) * 0.50 for i in range(10)]
        seg2 = [100 + (i % 5) * 0.20 for i in range(10)]
        seg3 = [100 + (i % 5) * 0.08 for i in range(5)]
        r = score(ctx(), regime("RANGE"), mtf={"primary": closes_frame(seg1 + seg2 + seg3)})
        assert r["breakdown"]["vcp"] == 0

    def test_volatility_compression_is_a_proxy_when_there_is_no_pattern(self):
        c = ctx(volatility=SimpleNamespace(state="COMPRESSION"))
        r = score(c, regime("RANGE"))
        assert r["breakdown"]["vcp"] == 6
        assert "proxy" in r["details"]["vcp"]

    def test_compression_does_not_double_up_with_a_real_pattern(self):
        c = ctx(volatility=SimpleNamespace(state="COMPRESSION"))
        r = score(c, regime("RANGE"), mtf={"primary": self._contracting()})
        assert r["breakdown"]["vcp"] >= 12

    def test_the_fallback_picks_another_frame_when_primary_is_absent(self):
        """mtf_data without 'primary' should still be usable."""
        r = score(ctx(), regime("RANGE"), mtf={"h1": self._contracting()})
        assert r["breakdown"]["vcp"] == 12

    def test_risk_reward_bonus(self):
        """rr >= 2.0 adds 4. No bos, so the 20 cap is not in the way."""
        c = ctx(structure=SimpleNamespace(bos=False, choch=False, bias="NEUTRAL"))
        low = score(c, regime("RANGE"), rr=1.0, mtf={"primary": self._contracting()})
        high = score(c, regime("RANGE"), rr=2.5, mtf={"primary": self._contracting()})
        assert low["breakdown"]["vcp"] == 12
        assert high["breakdown"]["vcp"] == 16

    def test_a_break_of_structure_adds_four(self):
        c = ctx(structure=SimpleNamespace(bos=True, choch=False, bias="NEUTRAL"))
        plain = ctx(structure=SimpleNamespace(bos=False, choch=False, bias="NEUTRAL"))
        assert (score(c, regime("RANGE"), rr=1.0, mtf={"primary": self._contracting()})["breakdown"]["vcp"]
                == score(plain, regime("RANGE"), rr=1.0, mtf={"primary": self._contracting()})["breakdown"]["vcp"] + 4)


# ---------------------------------------------------------------------------
# ICT killzone / AMD
# ---------------------------------------------------------------------------

class TestKillzone:
    def test_inside_a_killzone(self):
        r = score(ctx(timestamp=IN_KILLZONE), regime("RANGE"))
        assert r["breakdown"]["ict_killzone_amd"] == 10
        assert "NY_OPEN" in r["details"]["killzone"]

    def test_prime_but_outside_a_killzone(self):
        r = score(ctx(timestamp=PRIME_NOT_KILLZONE), regime("RANGE"))
        assert r["breakdown"]["ict_killzone_amd"] == 6
        assert "prime" in r["details"]["killzone"]

    def test_dead_hour_scores_nothing(self):
        c = ctx(timestamp=DEAD_HOUR, session=SimpleNamespace(utc_hour=22, is_prime_session=False))
        assert score(c, regime("RANGE"))["breakdown"]["ict_killzone_amd"] == 0

    def test_sweep_plus_displacement(self):
        c = ctx(timestamp=DEAD_HOUR,
                session=SimpleNamespace(utc_hour=22, is_prime_session=False),
                liquidity=SimpleNamespace(sweep_detected=True),
                momentum=SimpleNamespace(trend_score=0, adx=25),
                structure=SimpleNamespace(bos=True, choch=False, bias="NEUTRAL"))
        assert score(c, regime("RANGE"))["breakdown"]["ict_killzone_amd"] == 8

    def test_displacement_needs_adx_twenty_two(self):
        c = ctx(timestamp=DEAD_HOUR,
                session=SimpleNamespace(utc_hour=22, is_prime_session=False),
                liquidity=SimpleNamespace(sweep_detected=True),
                momentum=SimpleNamespace(trend_score=0, adx=21),
                structure=SimpleNamespace(bos=True, choch=False, bias="NEUTRAL"))
        assert score(c, regime("RANGE"))["breakdown"]["ict_killzone_amd"] == 0

    def test_the_component_is_capped_at_twenty(self):
        c = ctx(timestamp=IN_KILLZONE,
                liquidity=SimpleNamespace(sweep_detected=True),
                momentum=SimpleNamespace(trend_score=0, adx=30),
                structure=SimpleNamespace(bos=True, choch=False, bias="NEUTRAL"))
        assert score(c, regime("RANGE"))["breakdown"]["ict_killzone_amd"] == 18


# ---------------------------------------------------------------------------
# Triple confluence
# ---------------------------------------------------------------------------

class TestTripleConfluence:
    def test_fvg_and_ob_and_breaker(self):
        c = ctx(structure=SimpleNamespace(bos=True, choch=True, bias="BULLISH",
                                          fair_value_gaps=[1], order_blocks=[1]))
        r = score(c, regime("RANGE"))
        assert r["breakdown"]["triple_confluence"] == 20
        assert r["breakdown"]["wyckoff_ict_fusion"] > 0

    def test_break_of_structure_alone(self):
        c = ctx(structure=SimpleNamespace(bos=True, choch=False, bias="NEUTRAL"))
        assert score(c, regime("RANGE"))["breakdown"]["triple_confluence"] == 3

    def test_nothing_scores_nothing(self):
        assert score(ctx(), regime("RANGE"))["breakdown"]["triple_confluence"] == 0

    def test_multi_timeframe_alignment_adds_four(self):
        c = ctx(structure=SimpleNamespace(bos=False, choch=False, bias="NEUTRAL",
                                          fair_value_gaps=[1], order_blocks=[]),
                mtf_alignment={"H4": "BULLISH", "H1": "BULLISH", "M15": "BULLISH"})
        assert score(c, regime("RANGE"))["breakdown"]["triple_confluence"] == 11

    def test_alignment_below_three_timeframes_does_not_count(self):
        c = ctx(structure=SimpleNamespace(bos=False, choch=False, bias="NEUTRAL",
                                          fair_value_gaps=[1], order_blocks=[]),
                mtf_alignment={"H4": "BULLISH", "H1": "BULLISH"})
        assert score(c, regime("RANGE"))["breakdown"]["triple_confluence"] == 7


# ---------------------------------------------------------------------------
# Tier / boost
# ---------------------------------------------------------------------------

class TestTier:
    @pytest.mark.parametrize("total,tier,boost", [
        (0, "WEAK", 0.0), (24, "WEAK", 0.0),
        (25, "LOW", 0.0), (39, "LOW", 0.0),
        (40, "MODERATE", 0.015), (54, "MODERATE", 0.015),
        (55, "HIGH", 0.03), (69, "HIGH", 0.03),
        (70, "ELITE", 0.05), (100, "ELITE", 0.05),
    ])
    def test_tier_bands(self, total, tier, boost, monkeypatch):
        """Drive the total directly so the banding is tested independently of scoring."""
        def fake_sum(values):
            return total
        monkeypatch.setattr("builtins.sum", fake_sum)
        r = score(ctx(), regime("RANGE"))
        assert r["tier"] == tier
        assert r["prob_boost"] == boost

    def test_the_tier_is_consistent_with_the_total(self):
        for c in (ctx(), ctx(structure=SimpleNamespace(bos=True, choch=True, bias="BULLISH"),
                             timestamp=IN_KILLZONE,
                             momentum=SimpleNamespace(trend_score=30, adx=30)),
                  ctx(timestamp=IN_KILLZONE)):
            r = score(c, regime("TREND_BULL"))
            expected = ("ELITE" if r["total"] >= 70 else "HIGH" if r["total"] >= 55
                        else "MODERATE" if r["total"] >= 40 else "LOW" if r["total"] >= 25
                        else "WEAK")
            assert r["tier"] == expected


# ---------------------------------------------------------------------------
# Arguments that do not change the answer
# ---------------------------------------------------------------------------

class TestUnusedArguments:
    def test_ai_score_is_accepted_but_not_used(self):
        """`ai_score` is in the signature and never read — pinned so nobody
        assumes it moves the score."""
        c = ctx(structure=SimpleNamespace(bos=True, choch=True, bias="BULLISH"))
        assert score(c, regime("RANGE"), ai=0.0) == score(c, regime("RANGE"), ai=100.0)

    def test_the_engine_is_stateless_between_calls(self):
        e = MasterConfluenceEngine()
        c = ctx(structure=SimpleNamespace(bos=True, choch=True, bias="BULLISH"))
        assert e.score(c, regime("RANGE"), 1.0, 50.0) == e.score(c, regime("RANGE"), 1.0, 50.0)

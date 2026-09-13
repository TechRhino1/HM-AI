"""
Unit tests for the cross-style consensus aggregator.

These are pure unit tests: no MT5, no database, no market data, no network. The
module was designed to be testable that way, and these tests are the payoff.

What is actually being defended here
------------------------------------
1. ``reliability_weight`` must never let a losing style manufacture conviction.
   A negative-expectancy style, or one with a profit factor below 1, must land
   at or below the neutral 0.5 — and must stay inside [0.05, 0.95] whatever it
   is fed.
2. ``aggregate_symbol`` must not call a single style's opinion a consensus. The
   documented safety property — one style voting alone is never tradeable — is
   asserted directly, and so is the case that actually motivated the module:
   two styles agreeing against one dissenter.
3. ``select_from_candidates`` must survive the shapes the API actually hands it
   (dataclasses and plain dicts) and must order its output so the caller can
   take the head as "the" trade.
"""
from __future__ import annotations

import json
import math
import os
import tempfile

import pytest

from jarvis.intelligence.mode_aggregator import (
    STYLE_ORDER,
    STRONG_UTILITY,
    AggregatedDecision,
    ModeReliability,
    ModeReliabilityModel,
    StyleVote,
    aggregate_symbol,
    reliability_weight,
    select_from_candidates,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def vote(
    style: str,
    bias: str = "BUY",
    utility: float = 2.0,
    *,
    symbol: str = "EURUSD",
    reliability: float = 0.5,
    regime_multiplier: float = 1.0,
    **extra,
) -> StyleVote:
    return StyleVote(
        symbol=symbol,
        trade_style=style,
        bias=bias,
        utility_score=utility,
        reliability=reliability,
        regime_multiplier=regime_multiplier,
        **extra,
    )


def trusted_model(weight: float = 0.9) -> ModeReliabilityModel:
    """A model where every style is believed equally and strongly."""
    return ModeReliabilityModel(
        styles={s: ModeReliability(style=s, weight=weight) for s in STYLE_ORDER}
    )


# ─────────────────────────────────────────────────────────────────────────────
# reliability_weight
# ─────────────────────────────────────────────────────────────────────────────
class TestReliabilityWeight:
    def test_no_trades_is_exactly_neutral(self):
        """A style with no record is neutral — never confident, never written off."""
        assert reliability_weight(0.0, 0) == 0.5
        assert reliability_weight(5.0, 0) == 0.5
        assert reliability_weight(-5.0, 0) == 0.5

    def test_positive_expectancy_over_many_trades_earns_trust(self):
        w = reliability_weight(0.25, 1000, profit_factor=2.0)
        assert w > 0.5
        # tanh(1.0)=0.7616 -> raw 0.8808, shrunk by 1000/1030.
        assert w == pytest.approx(0.8697, abs=0.001)

    def test_negative_expectancy_loses_trust(self):
        w = reliability_weight(-0.25, 1000, profit_factor=0.5)
        assert w < 0.5
        assert w == pytest.approx(0.1303, abs=0.001)

    def test_curve_is_symmetric_around_neutral(self):
        """Equal-and-opposite expectancy, same sample, mirror-image weights."""
        up = reliability_weight(0.2, 400, profit_factor=2.0)
        down = reliability_weight(-0.2, 400, profit_factor=2.0)
        assert (up - 0.5) == pytest.approx(-(down - 0.5), abs=1e-9)

    def test_profit_factor_below_one_caps_at_neutral(self):
        """Expectancy can be dragged positive by one outlier; profit factor cannot.

        When they disagree the conservative reading must win, so a PF below 1
        caps the weight at 0.5 even though expectancy looks healthy.
        """
        flattering_expectancy = reliability_weight(0.6, 1000, profit_factor=0.8)
        assert flattering_expectancy <= 0.5

        # Same expectancy with a PF that agrees: the cap must not apply.
        honest = reliability_weight(0.6, 1000, profit_factor=1.5)
        assert honest > 0.5
        assert honest > flattering_expectancy

    def test_profit_factor_of_zero_does_not_trigger_the_cap(self):
        """PF=0 means "not measured", not "terrible" — it must not be treated as <1."""
        assert reliability_weight(0.5, 500, profit_factor=0.0) > 0.5

    def test_thin_samples_are_shrunk_toward_neutral(self):
        """The same expectancy is less informative over 12 trades than over 900."""
        thin = reliability_weight(0.3, 12, profit_factor=2.0)
        thick = reliability_weight(0.3, 900, profit_factor=2.0)
        assert 0.5 < thin < thick

    def test_weight_always_within_documented_bounds(self):
        """Whatever it is fed, the weight stays inside [0.05, 0.95]."""
        for exp in (-99.0, -1.0, -0.1, 0.0, 0.1, 1.0, 99.0):
            for trades in (0, 1, 10, 1000, 10**7):
                for pf in (0.0, 0.5, 1.0, 3.0, float("inf"), float("nan")):
                    w = reliability_weight(exp, trades, pf)
                    assert 0.05 <= w <= 0.95, (exp, trades, pf, w)

    def test_negative_trade_count_is_treated_as_no_record(self):
        assert reliability_weight(1.0, -5) == 0.5

    def test_nan_expectancy_does_not_produce_nan_weight(self):
        w = reliability_weight(float("nan"), 100, profit_factor=2.0)
        assert math.isfinite(w)
        assert 0.05 <= w <= 0.95


# ─────────────────────────────────────────────────────────────────────────────
# ModeReliabilityModel
# ─────────────────────────────────────────────────────────────────────────────
class TestModeReliabilityModel:
    def test_neutral_model_gives_every_style_half(self):
        model = ModeReliabilityModel.neutral()
        for style in STYLE_ORDER:
            assert model.weight(style) == 0.5

    def test_missing_report_falls_back_to_neutral(self):
        """A missing *report* must never stop the system from starting."""
        model = ModeReliabilityModel.from_report("/nonexistent/path/report.json")
        assert model.weight("SWING") == 0.5
        assert model.source_path is None

    def test_corrupt_report_falls_back_to_neutral(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "broken.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not valid json")
            model = ModeReliabilityModel.from_report(path)
            assert model.weight("SCALP") == 0.5

    def test_report_without_modes_falls_back_to_neutral(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "empty.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"modes": []}, fh)
            model = ModeReliabilityModel.from_report(path)
            assert model.weight("DAY_TRADING") == 0.5

    def test_from_report_reads_pooled_stats(self):
        payload = {
            "modes": [
                {
                    "style": "SWING",
                    "pooled": {
                        "trades": 752,
                        "expectancy_r": -0.083,
                        "profit_factor": 0.84,
                        "win_rate_pct": 41.0,
                    },
                    "per_symbol": [],
                },
                {
                    "style": "DAY_TRADING",
                    "pooled": {
                        "trades": 917,
                        "expectancy_r": -0.2839,
                        "profit_factor": 0.526,
                        "win_rate_pct": 30.0,
                    },
                    "per_symbol": [],
                },
            ]
        }
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "report.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)

            model = ModeReliabilityModel.from_report(path)

            # Both measured styles lost money, so both must sit below neutral.
            swing = model.for_style("SWING")
            assert swing.trades == 752
            assert swing.weight < 0.5
            assert swing.weight == pytest.approx(0.3459, abs=0.002)

            day = model.for_style("DAY_TRADING")
            assert day.weight < swing.weight
            assert day.weight == pytest.approx(0.1064, abs=0.002)

            # The style the report omitted still needs a usable weight.
            assert model.for_style("SCALP").weight == 0.5
            assert model.for_style("SCALP").source == "unreported"

    def test_symbol_override_requires_minimum_trades(self):
        """A 4-trade symbol record is noise and must be ignored."""
        payload = {
            "modes": [
                {
                    "style": "SWING",
                    "pooled": {"trades": 500, "expectancy_r": -0.05, "profit_factor": 0.9},
                    "per_symbol": [
                        {"symbol": "EURUSD", "trades": 400, "expectancy_r": 0.5,
                         "profit_factor": 3.0},
                        {"symbol": "GBPUSD", "trades": 4, "expectancy_r": 5.0,
                         "profit_factor": 9.0},
                    ],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "report.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)

            model = ModeReliabilityModel.from_report(path)
            pooled = model.for_style("SWING").weight

            # 400 trades is enough to earn its own say...
            assert model.weight("SWING", "EURUSD") > pooled
            # ...4 trades is not, so the pooled weight stands.
            assert model.weight("SWING", "GBPUSD") == pooled

    def test_intraday_aliases_to_day_trading(self):
        model = ModeReliabilityModel(
            styles={"DAY_TRADING": ModeReliability(style="DAY_TRADING", weight=0.42)}
        )
        assert model.for_style("INTRADAY").weight == 0.42
        assert model.for_style("DAY").weight == 0.42
        assert model.for_style("day_trading").weight == 0.42

    def test_unknown_style_gets_neutral(self):
        assert ModeReliabilityModel.neutral().for_style("MARTINGALE").weight == 0.5


# ─────────────────────────────────────────────────────────────────────────────
# StyleVote
# ─────────────────────────────────────────────────────────────────────────────
class TestStyleVote:
    def test_direction_normalises_and_rejects_non_directional_bias(self):
        assert vote("SWING", "buy").direction == "BUY"
        assert vote("SWING", "SELL").direction == "SELL"
        assert vote("SWING", "HOLD").direction == "NONE"
        assert vote("SWING", "").direction == "NONE"
        assert vote("SWING", "LONG").direction == "NONE"

    def test_abstention_contributes_exactly_nothing(self):
        """An abstention must not be able to move the pool, in either direction."""
        assert vote("SWING", "HOLD", utility=99.0).signed_contribution == 0.0
        assert vote("SWING", "BUY", utility=0.0).signed_contribution == 0.0
        assert vote("SWING", "BUY", utility=-3.0).signed_contribution == 0.0

    def test_contribution_is_signed_by_direction_and_scaled_by_trust(self):
        assert vote("SWING", "BUY", 2.0, reliability=0.5).signed_contribution == 1.0
        assert vote("SWING", "SELL", 2.0, reliability=0.5).signed_contribution == -1.0

    def test_to_dict_is_json_serialisable(self):
        payload = vote("SWING", "BUY", 1.5).to_dict()
        assert json.loads(json.dumps(payload))["direction"] == "BUY"


# ─────────────────────────────────────────────────────────────────────────────
# aggregate_symbol — the safety properties
# ─────────────────────────────────────────────────────────────────────────────
class TestAggregateSafety:
    def test_no_votes_yields_no_decision(self):
        d = aggregate_symbol("EURUSD", [])
        assert d.direction == "NONE"
        assert d.is_tradeable is False
        assert d.confidence_tier == "NONE"
        assert d.available_count == 0

    def test_a_single_style_alone_is_never_tradeable(self):
        """The documented safety property.

        One mode's opinion is a signal, not a consensus. This must hold however
        spectacular that single opinion is.
        """
        d = aggregate_symbol("EURUSD", [vote("SWING", "BUY", utility=99.0)])
        assert d.direction == "BUY"
        assert d.available_count == 1
        assert d.is_tradeable is False
        assert d.confidence_tier != "HIGH"

    def test_one_of_three_split_is_never_tradeable(self):
        """1 BUY against 2 SELL is a disagreement, not a consensus."""
        d = aggregate_symbol(
            "EURUSD",
            [
                vote("SWING", "BUY", utility=2.0),
                vote("DAY_TRADING", "SELL", utility=0.1),
                vote("SCALP", "SELL", utility=0.1),
            ],
        )
        assert d.agreement_ratio < 0.66
        assert d.is_tradeable is False

    def test_two_styles_against_one_dissenter_is_tradeable(self):
        """The case that motivated the module: genuine cross-style agreement."""
        d = aggregate_symbol(
            "EURUSD",
            [
                vote("SWING", "BUY", utility=2.0),
                vote("DAY_TRADING", "BUY", utility=1.8),
                vote("SCALP", "SELL", utility=0.1),
            ],
        )
        assert d.direction == "BUY"
        assert d.agreement_count == 2
        assert d.available_count == 3
        assert d.dissenting is True
        assert d.strong_dissent is False  # 0.1 is well below STRONG_UTILITY
        assert d.is_tradeable is True
        assert d.supporting_styles == ["SWING", "DAY_TRADING"]
        assert d.dissenting_styles == ["SCALP"]

    def test_unanimous_trio_is_high_confidence_and_tradeable(self):
        d = aggregate_symbol(
            "EURUSD",
            [
                vote("SWING", "BUY", utility=2.0),
                vote("DAY_TRADING", "BUY", utility=2.0),
                vote("SCALP", "BUY", utility=2.0),
            ],
            model=trusted_model(0.9),
        )
        assert d.direction == "BUY"
        assert d.agreement_ratio == 1.0
        assert d.confidence_tier == "HIGH"
        assert d.is_tradeable is True
        assert d.consensus_score == pytest.approx(85.44, abs=0.5)

    def test_strong_dissent_blocks_an_otherwise_tradeable_consensus(self):
        """Two styles agreeing must not out-vote one genuinely strong opposite case."""
        d = aggregate_symbol(
            "EURUSD",
            [
                vote("SWING", "BUY", utility=2.0),
                vote("DAY_TRADING", "BUY", utility=2.0),
                vote("SCALP", "SELL", utility=STRONG_UTILITY + 0.5),
            ],
        )
        assert d.direction == "BUY"           # the pool still favours BUY
        assert d.strong_dissent is True
        assert d.is_tradeable is False        # but it must not be taken
        assert "blocked by a strong opposing setup" in d.rationale

    def test_abstentions_do_not_count_as_agreement(self):
        """Unanimity is measured over voters: 2 of 2, not 2 of 3."""
        d = aggregate_symbol(
            "EURUSD",
            [
                vote("SWING", "BUY", utility=2.0),
                vote("DAY_TRADING", "BUY", utility=2.0),
                vote("SCALP", "HOLD", utility=0.0),
            ],
        )
        assert d.available_count == 2
        assert d.agreement_count == 2
        assert d.agreement_ratio == 1.0
        assert d.abstaining_styles == ["SCALP"]
        assert d.is_tradeable is True

    def test_zero_utility_vote_abstains_rather_than_voting(self):
        d = aggregate_symbol(
            "EURUSD",
            [
                vote("SWING", "BUY", utility=2.0),
                vote("DAY_TRADING", "SELL", utility=0.0),
            ],
        )
        assert d.available_count == 1
        assert "DAY_TRADING" in d.abstaining_styles
        assert d.is_tradeable is False


class TestAggregateMaths:
    def test_agreement_is_weighted_by_utility_not_headcount(self):
        """The design's central claim.

        Two barely-actionable setups must not out-vote one strong setup. Here
        SCALP alone carries more utility than SWING and DAY_TRADING combined,
        so the consensus must follow SCALP even though it is outnumbered.
        """
        d = aggregate_symbol(
            "EURUSD",
            [
                vote("SWING", "SELL", utility=0.20),
                vote("DAY_TRADING", "SELL", utility=0.20),
                vote("SCALP", "BUY", utility=3.0),
            ],
        )
        assert d.direction == "BUY"
        assert d.agreement_count == 1
        assert d.dissenting_styles == ["SWING", "DAY_TRADING"]
        # ...but one style out of three is still not a consensus.
        assert d.is_tradeable is False

    def test_tie_break_is_deterministic_and_follows_style_order(self):
        """An exact tie must resolve the same way regardless of input order."""
        a = aggregate_symbol(
            "EURUSD",
            [vote("SWING", "BUY", utility=1.0), vote("SCALP", "SELL", utility=1.0)],
        )
        b = aggregate_symbol(
            "EURUSD",
            [vote("SCALP", "SELL", utility=1.0), vote("SWING", "BUY", utility=1.0)],
        )
        assert a.direction == b.direction == "BUY"  # SWING is first in STYLE_ORDER

    def test_votes_are_returned_in_canonical_style_order(self):
        d = aggregate_symbol(
            "EURUSD",
            [vote("SCALP", "BUY"), vote("SWING", "BUY"), vote("DAY_TRADING", "BUY")],
        )
        assert [v.trade_style for v in d.votes] == list(STYLE_ORDER)

    def test_model_reweighting_overrides_provisional_vote_reliability(self):
        """The model is authoritative, so two call sites cannot disagree."""
        d = aggregate_symbol(
            "EURUSD",
            [vote("SWING", "BUY", 2.0, reliability=0.99)],
            model=ModeReliabilityModel.neutral(),
        )
        assert d.votes[0].reliability == 0.5

    def test_symbol_is_normalised_to_upper_case(self):
        d = aggregate_symbol("eurusd", [vote("SWING", "BUY")])
        assert d.symbol == "EURUSD"

    def test_regime_multiplier_raises_the_score(self):
        """A style-regime fit the arbiter rewarded must show up in the score."""
        poor = aggregate_symbol(
            "EURUSD",
            [vote("SWING", "BUY", 2.0, regime_multiplier=0.75),
             vote("DAY_TRADING", "BUY", 2.0, regime_multiplier=0.75)],
        )
        good = aggregate_symbol(
            "EURUSD",
            [vote("SWING", "BUY", 2.0, regime_multiplier=1.25),
             vote("DAY_TRADING", "BUY", 2.0, regime_multiplier=1.25)],
        )
        assert good.consensus_score > poor.consensus_score

    def test_describe_mentions_direction_and_counts(self):
        d = aggregate_symbol(
            "EURUSD",
            [vote("SWING", "BUY", 2.0), vote("DAY_TRADING", "BUY", 2.0)],
        )
        assert "EURUSD BUY" in d.describe()
        assert "2/2" in d.describe()

    def test_to_dict_is_json_serialisable(self):
        d = aggregate_symbol(
            "EURUSD",
            [vote("SWING", "BUY", 2.0), vote("DAY_TRADING", "BUY", 2.0)],
        )
        assert json.loads(json.dumps(d.to_dict()))["direction"] == "BUY"

    def test_purity_input_votes_are_not_mutated(self):
        """The aggregator re-weights by rebuilding; the caller's objects are safe."""
        original = vote("SWING", "BUY", 2.0, reliability=0.1)
        aggregate_symbol("EURUSD", [original], model=trusted_model(0.9))
        assert original.reliability == 0.1


# ─────────────────────────────────────────────────────────────────────────────
# select_from_candidates
# ─────────────────────────────────────────────────────────────────────────────
def cand(symbol: str, style: str, bias: str, utility: float) -> dict:
    return {
        "symbol": symbol,
        "trade_style": style,
        "bias": bias,
        "utility_score": utility,
        "win_prob": 60.0,
        "ml_prob": 0.7,
        "confluence_score": 30.0,
        "expected_value": 1.0,
        "setup_grade": "GRADE A",
        "regime": "TRENDING",
        "strategy": "BREAKOUT",
        "timeframe": "H1",
        "is_actionable": True,
    }


class TestSelectFromCandidates:
    def test_groups_by_symbol(self):
        decisions = select_from_candidates(
            [
                cand("EURUSD", "SWING", "BUY", 2.0),
                cand("EURUSD", "DAY_TRADING", "BUY", 2.0),
                cand("GBPUSD", "SWING", "BUY", 2.0),
            ]
        )
        assert {d.symbol for d in decisions} == {"EURUSD", "GBPUSD"}

    def test_tradeable_symbols_sort_ahead_of_untradeable_ones(self):
        decisions = select_from_candidates(
            [
                # GBPUSD: two agreeing.
                cand("GBPUSD", "SWING", "BUY", 2.0),
                cand("GBPUSD", "DAY_TRADING", "BUY", 2.0),
                # EURUSD: three-way conflict, will not be tradeable.
                cand("EURUSD", "SWING", "BUY", 2.0),
                cand("EURUSD", "DAY_TRADING", "SELL", 0.1),
                cand("EURUSD", "SCALP", "SELL", 0.1),
            ]
        )
        assert decisions[0].symbol == "GBPUSD"
        assert decisions[0].is_tradeable is True
        assert decisions[-1].is_tradeable is False

    def test_only_tradeable_filters_the_rest_out(self):
        decisions = select_from_candidates(
            [
                cand("GBPUSD", "SWING", "BUY", 2.0),
                cand("GBPUSD", "DAY_TRADING", "BUY", 2.0),
                cand("EURUSD", "SWING", "BUY", 2.0),
            ],
            only_tradeable=True,
        )
        assert [d.symbol for d in decisions] == ["GBPUSD"]

    def test_accepts_dataclass_like_objects_as_well_as_dicts(self):
        class FakeCandidate:
            symbol = "EURUSD"
            trade_style = "SWING"
            bias = "BUY"
            utility_score = 2.0
            win_prob = 60.0
            ml_prob = 0.7
            confluence_score = 30.0
            expected_value = 1.0
            setup_grade = "GRADE A"
            regime = "TRENDING"
            strategy = "BREAKOUT"
            timeframe = "H1"
            is_actionable = True

        decisions = select_from_candidates(
            [FakeCandidate(), cand("EURUSD", "DAY_TRADING", "BUY", 2.0)]
        )
        assert len(decisions) == 1
        assert decisions[0].symbol == "EURUSD"
        assert decisions[0].agreement_count == 2

    def test_empty_input_is_an_empty_list(self):
        assert select_from_candidates([]) == []
        assert select_from_candidates(None) == []

    def test_malformed_candidate_does_not_kill_the_scan(self):
        """One unreadable candidate must not take the whole selection down."""
        decisions = select_from_candidates(
            [
                cand("GBPUSD", "SWING", "BUY", 2.0),
                cand("GBPUSD", "DAY_TRADING", "BUY", 2.0),
                {"symbol": None, "trade_style": None, "bias": None},
            ]
        )
        assert any(d.symbol == "GBPUSD" for d in decisions)
        assert all(isinstance(d, AggregatedDecision) for d in decisions)

    def test_a_symbol_with_only_one_style_is_never_tradeable(self):
        decisions = select_from_candidates([cand("EURUSD", "SWING", "BUY", 5.0)])
        assert decisions[0].is_tradeable is False

    def test_output_is_deterministic_across_input_orderings(self):
        rows = [
            cand("EURUSD", "SWING", "BUY", 2.0),
            cand("EURUSD", "DAY_TRADING", "BUY", 2.0),
            cand("GBPUSD", "SWING", "BUY", 2.0),
            cand("GBPUSD", "DAY_TRADING", "BUY", 2.0),
            cand("GBPUSD", "SCALP", "BUY", 2.0),
        ]
        forward = [d.symbol for d in select_from_candidates(rows)]
        backward = [d.symbol for d in select_from_candidates(list(reversed(rows)))]
        assert forward == backward
        # Unanimous 3/3 must outrank unanimous 2/2.
        assert forward[0] == "GBPUSD"

"""
Unit tests for the maximum-profit geometry optimiser.

Split into two halves on purpose:

* **Pure logic** (no data) — the objective, the cache-key invariant, the search
  space, spec normalisation and the walk-forward verdict. These run anywhere.
* **Data-gated** — the pieces that only mean something against the real MT5
  cache. Each one skips cleanly when the parquet files are absent, so the suite
  still passes on a machine with no market data.

The alignment test is the important one. Candidates are indexed against the
frame the *scanner* saw, so whatever ``--since`` trim the scanner applied must
be replayed identically here. If it is mis-replayed, every ``bar_idx`` points
one offset out — silently, with plausible-looking numbers, because the arrays
are still long enough to index. ``load_series`` guards it; this test proves the
guard fires.

M5 used to be the trimmed timeframe (``--since 2026-07-06``) because M1 history
was hard-capped. The cap is gone — all 20 symbols now hold the full 183d M5
series back to 2026-03-14 — so M5 is scanned untrimmed like M15. These tests
therefore assert *consistency with whatever the manifest declares*, not that a
trim exists; they are correct either way.
"""
from __future__ import annotations

import json
import math
import os

import pytest

from jarvis.backtesting.optimizer import (
    OBJECTIVES,
    PRIMARY_TIMEFRAME,
    STYLES,
    BacktestOptimizer,
    GeometrySpace,
    OptimizerSpec,
    discover_symbols,
    load_series,
    manifest_since,
    objective_value,
    _sim_key,
)
from jarvis.backtesting.trade_simulator import Geometry
from jarvis.market.data_feed import normalise_style, style_timeframes


def geom(**overrides) -> Geometry:
    base = dict(
        tp_r=1.5,
        be_trigger_r=None,
        fast_cash_r=None,
        fast_cash_pct=0.5,
        trail_atr=None,
        trail_activation_r=2.0,
        max_bars=200,
        min_score=0.0,
    )
    base.update(overrides)
    return Geometry(**base)


# ─────────────────────────────────────────────────────────────────────────────
# objective_value — constraints
# ─────────────────────────────────────────────────────────────────────────────
class TestObjectiveConstraints:
    def test_empty_metrics_is_infeasible(self):
        assert objective_value({}, OptimizerSpec()) == float("-inf")

    def test_too_few_trades_is_infeasible(self):
        """Stops the search from "winning" by taking three enormous trades."""
        spec = OptimizerSpec(min_trades=30)
        metrics = {"trades": 29, "expectancy_r": 99.0, "max_dd_r": 1.0}
        assert objective_value(metrics, spec) == float("-inf")

        metrics["trades"] = 30
        assert objective_value(metrics, spec) == 99.0

    def test_excess_drawdown_is_infeasible(self):
        spec = OptimizerSpec(min_trades=1, max_dd_r=40.0)
        assert objective_value(
            {"trades": 50, "expectancy_r": 5.0, "max_dd_r": 40.01}, spec
        ) == float("-inf")
        assert objective_value(
            {"trades": 50, "expectancy_r": 5.0, "max_dd_r": 39.9}, spec
        ) == 5.0

    def test_zero_max_dd_disables_the_constraint(self):
        spec = OptimizerSpec(min_trades=1, max_dd_r=0.0)
        assert objective_value(
            {"trades": 50, "expectancy_r": 2.0, "max_dd_r": 9999.0}, spec
        ) == 2.0

    def test_non_positive_expectancy_is_infeasible(self):
        """A losing geometry is not a candidate for "maximum profit"."""
        spec = OptimizerSpec(min_trades=1)
        assert objective_value(
            {"trades": 50, "expectancy_r": 0.0, "max_dd_r": 1.0}, spec
        ) == float("-inf")
        assert objective_value(
            {"trades": 50, "expectancy_r": -0.4, "max_dd_r": 1.0}, spec
        ) == float("-inf")

    def test_non_finite_expectancy_is_infeasible(self):
        spec = OptimizerSpec(min_trades=1)
        for bad in (float("nan"), float("inf"), float("-inf")):
            assert objective_value(
                {"trades": 50, "expectancy_r": bad, "max_dd_r": 1.0}, spec
            ) == float("-inf")

    def test_infeasible_always_loses_to_any_feasible_score(self):
        """-inf rather than a large negative number, so nothing can out-rank it."""
        spec = OptimizerSpec(min_trades=10)
        bad = objective_value(
            {"trades": 5, "expectancy_r": 1000.0, "max_dd_r": 0.0}, spec
        )
        good = objective_value(
            {"trades": 10, "expectancy_r": 0.0001, "max_dd_r": 0.0}, spec
        )
        assert bad < good


class TestObjectiveSelection:
    def test_expectancy_objective_returns_expectancy(self):
        spec = OptimizerSpec(objective="expectancy_r", min_trades=1)
        assert objective_value(
            {"trades": 50, "expectancy_r": 0.42, "total_r": 99.0, "max_dd_r": 1.0}, spec
        ) == 0.42

    def test_total_r_objective_returns_total_r(self):
        spec = OptimizerSpec(objective="total_r", min_trades=1)
        assert objective_value(
            {"trades": 50, "expectancy_r": 0.42, "total_r": 21.0, "max_dd_r": 1.0}, spec
        ) == 21.0

    def test_profit_factor_objective_returns_profit_factor(self):
        spec = OptimizerSpec(objective="profit_factor", min_trades=1)
        assert objective_value(
            {"trades": 50, "expectancy_r": 0.42, "profit_factor": 1.7, "max_dd_r": 1.0},
            spec,
        ) == 1.7

    def test_calmar_objective_is_return_over_drawdown(self):
        spec = OptimizerSpec(objective="calmar", min_trades=1)
        assert objective_value(
            {"trades": 50, "expectancy_r": 0.4, "max_dd_r": 8.0}, spec
        ) == pytest.approx(0.05)

    def test_calmar_with_no_drawdown_does_not_divide_by_zero(self):
        spec = OptimizerSpec(objective="calmar", min_trades=1)
        value = objective_value(
            {"trades": 50, "expectancy_r": 0.4, "max_dd_r": 0.0}, spec
        )
        assert math.isfinite(value)
        assert value == pytest.approx(4.0)

    def test_all_declared_objectives_are_handled(self):
        spec = OptimizerSpec(min_trades=1)
        metrics = {
            "trades": 50, "expectancy_r": 0.4, "total_r": 20.0,
            "profit_factor": 1.7, "max_dd_r": 8.0,
        }
        for objective in OBJECTIVES:
            spec.objective = objective
            assert math.isfinite(objective_value(metrics, spec)), objective


# ─────────────────────────────────────────────────────────────────────────────
# The simulation cache key
# ─────────────────────────────────────────────────────────────────────────────
class TestSimulationCacheKey:
    def test_min_score_is_excluded_from_the_key(self):
        """The invariant the whole search's affordability rests on.

        min_score decides which simulated trades are *kept*, never what they
        did, so two geometries differing only in selectivity share one
        simulation. If this ever changed, the search would silently become
        `geometries x thresholds` times more expensive.
        """
        a = geom(tp_r=2.0, min_score=0.0)
        b = geom(tp_r=2.0, min_score=0.97)
        assert _sim_key(a) == _sim_key(b)

    def test_every_other_dimension_changes_the_key(self):
        base = geom()
        assert _sim_key(base) != _sim_key(geom(tp_r=2.5))
        assert _sim_key(base) != _sim_key(geom(be_trigger_r=1.0))
        assert _sim_key(base) != _sim_key(geom(fast_cash_r=1.0))
        assert _sim_key(base) != _sim_key(geom(fast_cash_pct=0.25))
        assert _sim_key(base) != _sim_key(geom(trail_atr=2.0))
        assert _sim_key(base) != _sim_key(geom(trail_activation_r=1.0))
        assert _sim_key(base) != _sim_key(geom(max_bars=50))

    def test_none_and_a_number_are_distinguishable(self):
        assert _sim_key(geom(be_trigger_r=None)) != _sim_key(geom(be_trigger_r=0.0))
        assert _sim_key(geom(trail_atr=None)) != _sim_key(geom(trail_atr=0.0))

    def test_key_is_stable_across_calls(self):
        assert _sim_key(geom(tp_r=2.5)) == _sim_key(geom(tp_r=2.5))


# ─────────────────────────────────────────────────────────────────────────────
# GeometrySpace
# ─────────────────────────────────────────────────────────────────────────────
class TestGeometrySpace:
    def test_seed_is_a_plain_fixed_target_profile(self):
        seed = GeometrySpace().seed()
        assert seed.tp_r == 1.5
        assert seed.be_trigger_r is None
        assert seed.fast_cash_r is None
        assert seed.trail_atr is None
        assert seed.min_score == 0.0

    def test_min_score_dimension_maps_to_the_quantile_field(self):
        """The dimension name and the field name differ; options() must bridge it."""
        space = GeometrySpace()
        assert space.options("min_score") == space.min_score_quantiles
        assert space.options("tp_r") == space.tp_r

    def test_with_value_handles_the_min_score_mapping(self):
        space = GeometrySpace()
        seed = space.seed()
        assert space.with_value(seed, "min_score", 0.97).min_score == 0.97
        assert space.with_value(seed, "tp_r", 3.0).tp_r == 3.0

    def test_with_value_does_not_mutate_the_input(self):
        space = GeometrySpace()
        seed = space.seed()
        space.with_value(seed, "tp_r", 3.0)
        assert seed.tp_r == 1.5

    def test_full_grid_size_matches_the_documented_1920(self):
        space = GeometrySpace()
        expected = (
            len(space.tp_r)
            * len(space.be_trigger_r)
            * len(space.fast_cash_r)
            * len(space.trail_atr)
            * len(space.min_score_quantiles)
        )
        assert expected == 1920
        assert len(space.all_geometries()) == 1920

    def test_all_geometries_honours_the_limit(self):
        assert len(GeometrySpace().all_geometries(limit=10)) == 10
        assert len(GeometrySpace().all_geometries(limit=1)) == 1

    def test_all_geometries_are_distinct(self):
        """A duplicated point would make the cache-key test above meaningless."""
        keys = {_sim_key(g) + f"|q{g.min_score}" for g in GeometrySpace().all_geometries()}
        assert len(keys) == 1920


# ─────────────────────────────────────────────────────────────────────────────
# OptimizerSpec
# ─────────────────────────────────────────────────────────────────────────────
class TestOptimizerSpec:
    def test_invalid_objective_falls_back_to_expectancy(self):
        assert OptimizerSpec(objective="make-me-rich").normalised().objective == "expectancy_r"
        assert OptimizerSpec(objective="").normalised().objective == "expectancy_r"
        assert OptimizerSpec(objective="CALMAR").normalised().objective == "calmar"

    def test_modes_are_normalised_and_filtered(self):
        spec = OptimizerSpec(modes=("intraday", "SWING", "MARTINGALE")).normalised()
        assert "DAY_TRADING" in spec.modes
        assert "SWING" in spec.modes
        assert "MARTINGALE" not in spec.modes

    def test_modes_never_end_up_empty(self):
        assert OptimizerSpec(modes=()).normalised().modes == STYLES
        # An unrecognised name is not dropped, it is normalised to SWING by
        # normalise_style — so the filter can never empty the tuple. That is
        # deliberate: a universe sweep should not silently lose a mode.
        assert OptimizerSpec(modes=("NONSENSE",)).normalised().modes == ("SWING",)

    def test_split_outside_the_sane_range_is_reset(self):
        assert OptimizerSpec(walk_forward_split=0.1).normalised().walk_forward_split == 0.5
        assert OptimizerSpec(walk_forward_split=0.99).normalised().walk_forward_split == 0.5
        assert OptimizerSpec(walk_forward_split=0.7).normalised().walk_forward_split == 0.7

    def test_counts_are_floored_at_one(self):
        spec = OptimizerSpec(min_trades=0, passes=0, max_evaluations=0).normalised()
        assert spec.min_trades == 1
        assert spec.passes == 1
        assert spec.max_evaluations == 1

    def test_defaults_are_the_documented_ones(self):
        spec = OptimizerSpec()
        assert spec.min_trades == 30
        assert spec.max_dd_r == 40.0
        assert spec.passes == 3
        assert spec.walk_forward_folds == 3


# ─────────────────────────────────────────────────────────────────────────────
# walk_forward_verdict
# ─────────────────────────────────────────────────────────────────────────────
class TestWalkForwardVerdict:
    def make(self) -> BacktestOptimizer:
        return BacktestOptimizer(OptimizerSpec(min_trades=30, walk_forward_folds=3))

    def test_no_in_sample_edge_means_no_retention_ratio(self):
        """You cannot retain a fraction of nothing.

        Reporting retention=1.00 because a negative in-sample number happened to
        match a negative held-out number would be actively misleading.
        """
        v = self.make().walk_forward_verdict(
            {"expectancy_r": -0.2, "trades": 100},
            {"expectancy_r": -0.2, "trades": 40},
        )
        assert v["edge_retention"] is None
        assert v["is_profitable_in_sample"] is False
        assert v["generalises"] is False
        assert "no edge to retain" in v["note"]

    def test_flat_in_sample_is_not_profitable(self):
        v = self.make().walk_forward_verdict(
            {"expectancy_r": 0.0, "trades": 100}, {"expectancy_r": 0.5, "trades": 40}
        )
        assert v["is_profitable_in_sample"] is False
        assert v["edge_retention"] is None

    def test_negative_held_out_expectancy_does_not_generalise(self):
        v = self.make().walk_forward_verdict(
            {"expectancy_r": 0.4, "trades": 100}, {"expectancy_r": -0.1, "trades": 40}
        )
        assert v["is_profitable_out_of_sample"] is False
        assert v["generalises"] is False
        assert "did not survive" in v["note"]

    def test_decayed_edge_does_not_generalise(self):
        v = self.make().walk_forward_verdict(
            {"expectancy_r": 0.4, "trades": 100}, {"expectancy_r": 0.1, "trades": 40}
        )
        assert v["edge_retention"] == pytest.approx(0.25)
        assert v["generalises"] is False
        assert "decayed" in v["note"]

    def test_held_out_sample_too_small_to_confirm(self):
        """A 2-trade held-out window proves nothing, however good the numbers."""
        v = self.make().walk_forward_verdict(
            {"expectancy_r": 0.4, "trades": 100}, {"expectancy_r": 0.5, "trades": 2}
        )
        assert v["edge_retention"] == pytest.approx(1.25)
        assert v["generalises"] is False
        assert "too small" in v["note"]

    def test_surviving_edge_generalises(self):
        v = self.make().walk_forward_verdict(
            {"expectancy_r": 0.4, "trades": 100}, {"expectancy_r": 0.3, "trades": 40}
        )
        assert v["edge_retention"] == pytest.approx(0.75)
        assert v["generalises"] is True
        assert v["note"] == "edge survived out-of-sample"

    def test_retention_boundary_at_exactly_one_half_generalises(self):
        v = self.make().walk_forward_verdict(
            {"expectancy_r": 0.4, "trades": 100}, {"expectancy_r": 0.2, "trades": 40}
        )
        assert v["edge_retention"] == pytest.approx(0.5)
        assert v["generalises"] is True

    def test_verdict_is_json_serialisable(self):
        import json

        v = self.make().walk_forward_verdict(
            {"expectancy_r": 0.4, "trades": 100}, {"expectancy_r": 0.3, "trades": 40}
        )
        assert json.loads(json.dumps(v))["generalises"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Fold windowing
# ─────────────────────────────────────────────────────────────────────────────
class TestFoldMetrics:
    def test_folds_are_contiguous_and_cover_the_whole_window(self):
        """No gaps, no overlap — a gap would hide a period from validation."""
        opt = BacktestOptimizer(OptimizerSpec(min_trades=1))
        folds = opt._fold_metrics([], geom(), entry_lo=0, entry_hi=300, folds=3)

        assert [f["entry_lo"] for f in folds] == [0, 100, 200]
        assert [f["entry_hi"] for f in folds] == [100, 200, 300]

        for earlier, later in zip(folds, folds[1:]):
            assert earlier["entry_hi"] == later["entry_lo"]

    def test_last_fold_absorbs_the_remainder(self):
        """A window that does not divide evenly must not lose its tail."""
        opt = BacktestOptimizer(OptimizerSpec(min_trades=1))
        folds = opt._fold_metrics([], geom(), entry_lo=0, entry_hi=301, folds=3)
        assert folds[-1]["entry_hi"] == 301
        assert folds[0]["entry_lo"] == 0

    def test_fold_count_is_floored_at_one(self):
        opt = BacktestOptimizer(OptimizerSpec(min_trades=1))
        folds = opt._fold_metrics([], geom(), entry_lo=0, entry_hi=100, folds=0)
        assert len(folds) == 1
        assert folds[0]["entry_lo"] == 0
        assert folds[0]["entry_hi"] == 100

    def test_folds_do_not_count_against_the_evaluation_budget(self):
        """Folds are reporting, not search — they must not consume the budget."""
        opt = BacktestOptimizer(OptimizerSpec(min_trades=1))
        before = opt._eval_count
        opt._fold_metrics([], geom(), entry_lo=0, entry_hi=300, folds=3)
        assert opt._eval_count == before


# ─────────────────────────────────────────────────────────────────────────────
# Timeframe mapping consistency
# ─────────────────────────────────────────────────────────────────────────────
class TestTimeframeMapping:
    def test_primary_timeframe_matches_the_data_feed(self):
        """The optimiser and the feed must agree, or candidates get simulated
        on a series they were not scanned against."""
        for style in STYLES:
            assert PRIMARY_TIMEFRAME[style] == style_timeframes(style)["primary"], style

    def test_every_style_has_a_primary_timeframe(self):
        assert set(PRIMARY_TIMEFRAME) == set(STYLES)

    def test_style_names_are_canonical(self):
        for style in STYLES:
            assert normalise_style(style) == style


# ─────────────────────────────────────────────────────────────────────────────
# Data-gated tests — real MT5 cache
# ─────────────────────────────────────────────────────────────────────────────
HAS_DATA = os.path.isdir(os.path.join("data", "market", "real")) and os.path.isdir(
    os.path.join("data", "signals")
)
needs_data = pytest.mark.skipif(not HAS_DATA, reason="real MT5 market cache not present")


@needs_data
class TestAgainstRealData:
    def test_m5_scan_manifest_declares_its_window(self):
        """The manifest must state which window was scanned, because the
        optimiser replays `since` as an alignment-critical trim.

        M5 was trimmed (--since 2026-07-06) while M1 history was capped; the cap
        is gone and it is now scanned untrimmed. Both are legal — what is not
        legal is an absent or malformed `since`, since `null` and a date mean
        very different frames.
        """
        path = os.path.join("data", "signals", "scan_manifest_M5.json")
        assert os.path.exists(path), "M5 scan manifest is missing"
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        assert "since" in payload, "manifest must declare `since` (null = untrimmed)"
        since = payload["since"]
        assert since is None or str(since).count("-") == 2, (
            f"`since` must be null or an ISO date, got {since!r}"
        )

    def test_h1_scan_is_untrimmed(self):
        """H1 is not capped, so no trim should be replayed for it."""
        assert manifest_since("H1") is None

    def test_discover_symbols_finds_the_universe(self):
        symbols = discover_symbols("H1")
        assert len(symbols) >= 5
        assert all(isinstance(s, str) and s for s in symbols)

    def test_discover_symbols_requires_both_bars_and_candidates(self):
        """A symbol with bars but no candidates has nothing to simulate."""
        for symbol in discover_symbols("M5"):
            assert os.path.exists(
                os.path.join("data", "signals", f"{symbol}_M5_183d_candidates.parquet")
            )

    def test_scalp_trim_replay_keeps_bar_idx_in_range(self):
        """The alignment trap.

        If the --since trim were mis-replayed, bar_idx would point past the end
        of the frame — or worse, inside it at the wrong bar. load_series refuses
        to return a bundle whose indices do not fit, so a non-None bundle with a
        valid max index is the proof that the trim was replayed correctly.
        """
        bundle = load_series("EURUSD", "SCALP")
        assert bundle is not None, "expected EURUSD M5 data to be present"
        # No `bundle.since` assertion: M5 is untrimmed now that the M1 cap is
        # gone. The assertion that matters is that the indices fit the frame.
        assert bundle.n_bars >= 150

        max_idx = int(bundle.candidates["bar_idx"].max())
        assert 0 <= max_idx < bundle.n_bars

    def test_frame_length_matches_the_declared_trim(self):
        """A mis-replayed trim shows up as a frame length that contradicts the
        manifest: untrimmed must keep every bar, trimmed must be strictly
        shorter. Asserting either one unconditionally would bake in whichever
        convention happened to be current, so check it against `since`.
        """
        import pandas as pd

        since = manifest_since("M5")
        bundle = load_series("EURUSD", "SCALP")
        assert bundle is not None
        raw = pd.read_parquet(
            os.path.join("data", "market", "real", "EURUSD", "EURUSD_M5_183d.parquet")
        )
        if since is None:
            assert bundle.n_bars == len(raw), (
                f"untrimmed scan should keep all {len(raw)} bars, got {bundle.n_bars}"
            )
        else:
            assert bundle.n_bars < len(raw), (
                "the trimmed frame should be shorter than the raw parquet — "
                "otherwise the --since replay did nothing"
            )

    def test_h1_bundle_is_untrimmed_and_aligned(self):
        bundle = load_series("EURUSD", "SWING")
        assert bundle is not None
        assert bundle.since is None
        assert bundle.timeframe == "H1"
        max_idx = int(bundle.candidates["bar_idx"].max())
        assert 0 <= max_idx < bundle.n_bars

    def test_atr_is_attached_for_the_simulator(self):
        """The stored parquet has no ATR column; the simulator trails off it."""
        bundle = load_series("EURUSD", "SWING")
        assert bundle is not None
        assert "atr" in bundle.df.columns
        assert bundle.df["atr"].notna().sum() > 0

    def test_costs_are_charged_not_defaulted_to_zero(self):
        """A search run with zero costs selects the geometry that best exploits
        the omission — typically the most stop-outs — and reports an edge the
        engine cannot realise."""
        bundle = load_series("EURUSD", "SWING", slippage_pips=0.5, commission_per_lot=5.0)
        assert bundle is not None
        assert bundle.money_per_unit > 0
        assert bundle.cost_price_equiv > 0
        assert bundle.slippage_price_equiv > 0
        assert bundle.cost_price_equiv == pytest.approx(
            5.0 / bundle.money_per_unit, rel=1e-6
        )

    def test_missing_symbol_returns_none_rather_than_raising(self):
        """A universe sweep should degrade to fewer symbols, not abort."""
        assert load_series("NOSUCHSYMBOL", "SWING") is None

    def test_unknown_style_name_falls_back_to_swing(self):
        """A documented footgun, pinned so it cannot change silently.

        ``normalise_style`` maps anything it does not recognise to SWING, so a
        typo in a style name produces a *swing* scan rather than an error. The
        optimiser therefore inherits that behaviour instead of raising. Callers
        that accept style names from a user must validate them upstream.
        """
        bundle = load_series("EURUSD", "MARTINGALE")
        assert bundle is not None
        assert bundle.style == "SWING"
        assert bundle.timeframe == "H1"

    def test_min_score_for_uses_the_symbols_own_distribution(self):
        """A percentile threshold is a statement about one symbol's scores."""
        bundle = load_series("EURUSD", "SWING")
        assert bundle is not None
        low = bundle.min_score_for(0.0)
        mid = bundle.min_score_for(0.5)
        high = bundle.min_score_for(0.99)
        assert low == 0.0
        assert mid <= high
        # Quantiles are clamped, never extrapolated: anything above 1.0 pins to
        # the maximum observed score rather than inventing a threshold above it.
        assert bundle.min_score_for(2.0) == pytest.approx(bundle.candidates["score"].max())
        assert bundle.min_score_for(-1.0) == 0.0
        # The 99th percentile is by construction at or below the maximum.
        assert high <= bundle.min_score_for(2.0)

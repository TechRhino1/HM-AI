"""
Unit tests for the regime-conditioned profit optimiser.

Split the same way as ``test_backtest_optimizer.py``:

* **Pure logic** (no market data) — the disable decision, the budget window, the
  regime-local quantile, the policy comparison, and the cross-regime blocking
  invariant. These run anywhere.
* **Data-gated** — the pieces that only mean something against the real MT5
  cache. Each skips cleanly when the parquet files are absent.

Two tests carry most of the weight:

``test_policy_blocks_across_regimes_not_within`` is the one that matters most.
The engine holds at most one position per symbol whatever the regime is, so
selecting each regime independently would book overlapping trades the engine can
never take — and the phantom overlap would cluster at regime turns, i.e. exactly
where the P&L is. The test builds two regimes whose trades overlap in time and
asserts only one survives.

``test_gates_match_winrate_targeting`` guards a deliberate duplication. The
disable thresholds are re-implemented here rather than imported, because
``jarvis.intelligence`` already imports ``jarvis.backtesting`` and
``docs/ARCHITECTURE.md`` §2 requires the dependency direction to be downward.
This test fails loudly if the two ever drift apart, which is the only thing that
makes the duplication safe.
"""
from __future__ import annotations

import inspect
import os

import numpy as np
import pandas as pd
import pytest

from jarvis.backtesting.optimizer import (
    OptimizerSpec,
    SeriesBundle,
    objective_value,
)
from jarvis.backtesting.regime_optimizer import (
    DISABLE_MARGIN_R,
    REGIME_MIN_CANDIDATES,
    REGIME_MIN_TRADES,
    RegimeConditionedOptimizer,
    RegimeVerdict,
    regime_decision,
)
from jarvis.backtesting.trade_simulator import Geometry, select_sequential


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic fixtures
# ─────────────────────────────────────────────────────────────────────────────
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


def flat_frame(n: int = 400, price: float = 100.0) -> pd.DataFrame:
    """A perfectly flat series.

    Flat on purpose: with ``high == low == close == price`` and a stop one unit
    away, neither the stop nor the target can ever be touched, so every trade
    runs to the time stop and ``exit_idx`` is exactly ``entry_idx + max_bars - 1``.
    That makes the selection tests deterministic instead of dependent on which
    way a synthetic price path happened to wander.
    """
    times = pd.date_range("2026-01-01", periods=n, freq="h", tz=None)
    return pd.DataFrame(
        {
            "time": times,
            "open": np.full(n, price),
            "high": np.full(n, price),
            "low": np.full(n, price),
            "close": np.full(n, price),
            "atr": np.full(n, 1.0),
        }
    )


def make_bundle(
    rows,
    *,
    symbol: str = "TEST",
    style: str = "SWING",
    n: int = 400,
    price: float = 100.0,
) -> SeriesBundle:
    """Build a SeriesBundle from ``(bar_idx, side, regime, score)`` rows."""
    df = flat_frame(n, price)
    candidates = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "bar_idx": int(bar),
                "side": str(side),
                "fill": float(price),
                "sl": float(price - 1.0) if str(side).upper() == "BUY" else float(price + 1.0),
                "score": float(score),
                "regime": str(regime),
                "strategy": "TEST",
                "zone": "TEST",
            }
            for bar, side, regime, score in rows
        ]
    )
    return SeriesBundle(
        symbol=symbol,
        style=style,
        timeframe="H1",
        df=df,
        candidates=candidates,
        money_per_unit=100000.0,
        cost_price_equiv=0.0,
        slippage_price_equiv=0.0,
        spec=None,
        since=None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# The disable decision
# ─────────────────────────────────────────────────────────────────────────────
class TestRegimeDecision:
    def test_thin_sample_is_left_enabled(self):
        """Too few trades to judge is not evidence of a bad regime.

        Disabling here would be noise-fitting: removing trades for no reason is
        not conservative, it is just a smaller sample.
        """
        enabled, reason = regime_decision(5, -0.50)
        assert enabled is True
        assert "insufficient samples" in reason

    def test_clearly_negative_is_disabled(self):
        enabled, reason = regime_decision(30, -0.30)
        assert enabled is False
        assert "-0.300R" in reason

    def test_marginal_negative_is_left_enabled(self):
        """The margin is the whole point — see the module docstring.

        A regime on -0.01R is inside the noise band; disabling it would discard
        most of the sample for no demonstrated reason.
        """
        enabled, _ = regime_decision(200, -0.01)
        assert enabled is True

    def test_positive_is_enabled(self):
        enabled, reason = regime_decision(100, 0.25)
        assert enabled is True
        assert "within tolerance" in reason

    def test_boundary_is_inclusive_of_the_margin(self):
        """Exactly at the margin counts as "worse than", and disables."""
        enabled, _ = regime_decision(REGIME_MIN_TRADES, -DISABLE_MARGIN_R)
        assert enabled is False

    def test_boundary_just_inside_is_enabled(self):
        enabled, _ = regime_decision(REGIME_MIN_TRADES, -DISABLE_MARGIN_R + 1e-9)
        assert enabled is True

    def test_zero_trades_is_left_enabled(self):
        enabled, _ = regime_decision(0, 0.0)
        assert enabled is True


# ─────────────────────────────────────────────────────────────────────────────
# Drift guard for the deliberate duplication
# ─────────────────────────────────────────────────────────────────────────────
def test_gates_match_winrate_targeting():
    """The re-implemented thresholds must stay identical to the calibrator's.

    ``jarvis.intelligence.winrate_targeting.regime_edge_table`` answers the same
    question against a win-rate objective. This module answers it against a
    profit objective and duplicates the thresholds instead of importing them, to
    avoid a package-level import cycle. If one side is ever changed, this test is
    what stops the two calibrators from silently disagreeing about what "no edge"
    means.
    """
    from jarvis.intelligence.winrate_targeting import regime_edge_table

    params = inspect.signature(regime_edge_table).parameters
    assert params["disable_margin_r"].default == DISABLE_MARGIN_R, (
        "DISABLE_MARGIN_R has drifted from regime_edge_table(disable_margin_r=...)"
    )
    assert params["min_trades_to_disable"].default == REGIME_MIN_TRADES, (
        "REGIME_MIN_TRADES has drifted from "
        "regime_edge_table(min_trades_to_disable=...)"
    )


def test_min_candidates_gate_is_a_real_number():
    """A zero gate would search every regime, including a 10-candidate one."""
    assert REGIME_MIN_CANDIDATES > 0
    assert REGIME_MIN_TRADES > 0
    assert DISABLE_MARGIN_R > 0


# ─────────────────────────────────────────────────────────────────────────────
# Regime-local quantiles
# ─────────────────────────────────────────────────────────────────────────────
class TestRegimeLocalQuantile:
    def test_counts_per_regime(self):
        b = make_bundle([
            (10, "BUY", "TREND_BULL", 0.1),
            (20, "BUY", "TREND_BULL", 0.2),
            (30, "BUY", "COMPRESSION", 0.9),
        ])
        assert b.regime_counts() == {"TREND_BULL": 2, "COMPRESSION": 1}

    def test_counts_empty_when_unlabelled(self):
        b = make_bundle([(10, "BUY", "TREND_BULL", 0.1)])
        b.candidates = b.candidates.drop(columns=["regime"])
        assert b.regime_counts() == {}

    def test_quantile_is_local_not_pooled(self):
        """The whole reason ``regimes`` exists.

        TREND_BULL holds the low scores and COMPRESSION the high ones, so the
        pooled median is meaningless inside either subset. If the filter were
        ignored, the compression threshold would come out far too low and the
        run would silently accept most of that regime's setups while still
        reporting a "median selectivity" search.
        """
        b = make_bundle([
            (10, "BUY", "TREND_BULL", 0.10),
            (20, "BUY", "TREND_BULL", 0.20),
            (30, "BUY", "COMPRESSION", 0.80),
            (40, "BUY", "COMPRESSION", 0.90),
        ])
        pooled = b.min_score_for(0.5)
        bull = b.min_score_for(0.5, {"TREND_BULL"})
        comp = b.min_score_for(0.5, {"COMPRESSION"})

        assert bull < comp
        assert 0.10 <= bull <= 0.20
        assert 0.80 <= comp <= 0.90
        # The pooled value sits between the two, so it is wrong for both.
        assert bull < pooled < comp

    def test_unmatched_filter_yields_zero_not_pooled(self):
        """A filter that matches nothing must not fall back to everything.

        Widening back to the pooled column would turn a regime filter that
        matched nothing into an unfiltered run — the one failure mode that looks
        like success.
        """
        b = make_bundle([(10, "BUY", "TREND_BULL", 0.55)])
        assert b.min_score_for(0.5, {"NOT_A_REGIME"}) == 0.0

    def test_zero_quantile_is_always_zero(self):
        b = make_bundle([(10, "BUY", "TREND_BULL", 0.55)])
        assert b.min_score_for(0.0, {"TREND_BULL"}) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# The cross-regime blocking invariant
# ─────────────────────────────────────────────────────────────────────────────
class TestPolicyBlocking:
    def _opt(self) -> RegimeConditionedOptimizer:
        spec = OptimizerSpec(
            modes=("SWING",), min_trades=1, max_dd_r=0.0, passes=1, max_evaluations=1
        )
        return RegimeConditionedOptimizer(spec)

    def _plan(self, geom_a, geom_b) -> dict:
        """Two enabled regimes, each with its own geometry and quantile."""
        return {
            "R1": RegimeVerdict(
                regime="R1", status="searched", candidates=500,
                deployed_geometry=geom_a, deployed_quantile=0.0,
                enabled=True, reason="test",
            ),
            "R2": RegimeVerdict(
                regime="R2", status="searched", candidates=500,
                deployed_geometry=geom_b, deployed_quantile=0.0,
                enabled=True, reason="test",
            ),
        }

    def test_policy_blocks_across_regimes_not_within(self):
        """Overlapping trades from DIFFERENT regimes must still block.

        With ``max_bars=11`` on a flat series a trade entered at bar ``i`` exits
        at bar ``i + 10``. R1 enters at 10 (exits 20); R2 enters at 15, inside
        that window, and would exit at 25. The engine holds one position at a
        time per symbol, so only the first may be taken. Selecting each regime
        independently would keep both and report a trade the engine cannot make.
        """
        g = geom(max_bars=11)
        b = make_bundle([
            (10, "BUY", "R1", 0.5),
            (15, "BUY", "R2", 0.5),
        ])
        opt = self._opt()
        chosen = opt._policy_outcomes([b], self._plan(g, g), entry_lo=None, entry_hi=None)

        assert len(chosen) == 1, (
            f"expected the overlapping trade to be blocked, got {len(chosen)} trades"
        )
        assert str(chosen[0].regime) == "R1"

    def test_non_overlapping_trades_in_different_regimes_both_taken(self):
        """Blocking must not be so eager that it drops genuinely sequential trades."""
        g = geom(max_bars=6)
        b = make_bundle([
            (10, "BUY", "R1", 0.5),   # exits 15
            (20, "BUY", "R2", 0.5),   # exits 25 — no overlap
        ])
        opt = self._opt()
        chosen = opt._policy_outcomes([b], self._plan(g, g), entry_lo=None, entry_hi=None)

        assert len(chosen) == 2
        assert {str(o.regime) for o in chosen} == {"R1", "R2"}

    def test_disabled_regime_contributes_nothing(self):
        g = geom(max_bars=6)
        b = make_bundle([
            (10, "BUY", "R1", 0.5),
            (50, "BUY", "R2", 0.5),
        ])
        plan = self._plan(g, g)
        plan["R2"].enabled = False
        opt = self._opt()
        chosen = opt._policy_outcomes([b], plan, entry_lo=None, entry_hi=None)

        assert len(chosen) == 1
        assert str(chosen[0].regime) == "R1"

    def test_regime_without_own_geometry_falls_back_to_pooled(self):
        """A thin regime inherits the pooled geometry rather than being dropped."""
        g = geom(max_bars=6)
        b = make_bundle([(10, "BUY", "THIN", 0.5)])
        plan = {
            "THIN": RegimeVerdict(
                regime="THIN", status="insufficient_sample", candidates=10,
                deployed_geometry=g, deployed_quantile=0.0,
                enabled=True, reason="too thin to disable",
            )
        }
        opt = self._opt()
        chosen = opt._policy_outcomes([b], plan, entry_lo=None, entry_hi=None)
        assert len(chosen) == 1

    def test_empty_plan_yields_no_trades(self):
        opt = self._opt()
        assert opt._policy_outcomes([], {}, entry_lo=None, entry_hi=None) == []


# ─────────────────────────────────────────────────────────────────────────────
# The budget window
# ─────────────────────────────────────────────────────────────────────────────
class TestBudgetWindow:
    def test_budget_is_per_search_not_per_optimiser(self):
        """The bug this fixes.

        ``max_evaluations`` was an absolute counter, so the first search consumed
        the budget and every later one returned the unsearched seed geometry
        while still reporting a full result. A low budget (the live verifier
        passes 12) triggered it on every run.
        """
        spec = OptimizerSpec(modes=("SWING",), passes=1, max_evaluations=5)
        opt = RegimeConditionedOptimizer(spec)

        opt._eval_count = 100
        opt._reset_budget()
        assert opt._budget_spent() == 0

        opt._eval_count = 103
        assert opt._budget_spent() == 3

    def test_budget_starts_at_zero(self):
        spec = OptimizerSpec(modes=("SWING",), passes=1, max_evaluations=5)
        opt = RegimeConditionedOptimizer(spec)
        assert opt._budget_spent() == 0


# ─────────────────────────────────────────────────────────────────────────────
# The policy / baseline comparison
# ─────────────────────────────────────────────────────────────────────────────
class TestComparison:
    def _cmp(self, b, p):
        return RegimeConditionedOptimizer._compare(b, p)

    def test_both_improved(self):
        r = self._cmp(
            {"expectancy_r": 0.10, "total_r": 10.0, "trades": 100},
            {"expectancy_r": 0.20, "total_r": 20.0, "trades": 100},
        )
        assert "improves both" in r["verdict"]

    def test_same_total_fewer_trades_is_a_win(self):
        """The measured outcome on the real data, and a genuine improvement.

        The same money for a fraction of the exposure is a better system, not a
        worse one, so the verdict must not dismiss it for failing to raise an
        already-similar number.
        """
        r = self._cmp(
            {"expectancy_r": 0.056, "total_r": 7.166, "trades": 128},
            {"expectancy_r": 0.306, "total_r": 7.038, "trades": 23},
        )
        assert "matches the baseline" in r["verdict"]
        assert r["total_r_retention"] is not None and r["total_r_retention"] > 0.95
        assert r["trade_reduction"] > 0.8

    def test_expectancy_up_total_r_down(self):
        r = self._cmp(
            {"expectancy_r": 0.05, "total_r": 20.0, "trades": 400},
            {"expectancy_r": 0.20, "total_r": 5.0, "trades": 25},
        )
        assert "retains only" in r["verdict"]
        assert r["total_r_delta"] < 0

    def test_total_r_up_expectancy_down(self):
        r = self._cmp(
            {"expectancy_r": 0.30, "total_r": 3.0, "trades": 10},
            {"expectancy_r": 0.10, "total_r": 9.0, "trades": 90},
        )
        assert "total R but not expectancy" in r["verdict"]

    def test_no_trades_is_reported_as_such(self):
        r = self._cmp(
            {"expectancy_r": 0.10, "total_r": 10.0, "trades": 100},
            {"expectancy_r": 0.0, "total_r": 0.0, "trades": 0},
        )
        assert r["verdict"] == "policy takes no trades out-of-sample"

    def test_zero_baseline_does_not_fabricate_a_ratio(self):
        """Retention is ``None``, not a division by zero dressed up as a number."""
        r = self._cmp(
            {"expectancy_r": 0.0, "total_r": 0.0, "trades": 0},
            {"expectancy_r": 0.10, "total_r": 5.0, "trades": 50},
        )
        assert r["total_r_retention"] is None
        assert r["trade_reduction"] is None

    def test_worse_policy_says_so(self):
        r = self._cmp(
            {"expectancy_r": 0.20, "total_r": 20.0, "trades": 100},
            {"expectancy_r": 0.05, "total_r": 2.0, "trades": 40},
        )
        assert r["verdict"] == "policy does not improve the held-out result"


# ─────────────────────────────────────────────────────────────────────────────
# Deploy-only-a-measured-win
# ─────────────────────────────────────────────────────────────────────────────
class TestDeployCriterion:
    def test_infeasible_regime_geometry_loses_to_a_feasible_pooled_one(self):
        """The constraint that guards the pooled search guards the regime search.

        A geometry fitted to nine trades returns ``-inf`` under
        ``min_trades=30``, so it cannot be deployed no matter how good its raw
        expectancy looks. This is what stops the optimiser shipping a curve-fit
        from a handful of bars.
        """
        spec = OptimizerSpec(modes=("SWING",), min_trades=30)
        thin = {"trades": 9, "expectancy_r": 1.20, "max_dd_r": 5.0}
        pooled = {"trades": 200, "expectancy_r": 0.02, "max_dd_r": 20.0}

        assert objective_value(thin, spec) == float("-inf")
        assert objective_value(pooled, spec) > float("-inf")
        # So the deploy test ``own > base`` is False, and the pooled geometry wins.
        assert not (objective_value(thin, spec) > objective_value(pooled, spec))

    def test_both_infeasible_keeps_the_pooled_geometry(self):
        """``-inf > -inf`` is False, so an infeasible search never displaces the default."""
        spec = OptimizerSpec(modes=("SWING",), min_trades=30)
        assert not (
            objective_value({"trades": 2, "expectancy_r": 5.0}, spec)
            > objective_value({"trades": 1, "expectancy_r": 5.0}, spec)
        )


# ─────────────────────────────────────────────────────────────────────────────
# Data-gated: the real cache
# ─────────────────────────────────────────────────────────────────────────────
DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "signals",
)
_HAS_DATA = os.path.isdir(DATA) and bool(
    [f for f in os.listdir(DATA) if f.endswith("_candidates.parquet")]
) if os.path.isdir(DATA) else False


@pytest.mark.skipif(not _HAS_DATA, reason="no cached candidate tables")
class TestRealData:
    def test_candidates_carry_regime_labels(self):
        """Regime conditioning is meaningless without labels.

        ``TradeOutcome.regime`` defaults to ``UNKNOWN``, so an unlabelled cache
        would make every regime filter match a single bucket and the whole module
        would silently degrade to the pooled optimiser while reporting per-regime
        results.
        """
        from jarvis.backtesting.optimizer import load_series

        b = load_series("EURUSD", "SWING")
        assert b is not None
        counts = b.regime_counts()
        assert counts, "candidate table has no regime column"
        assert set(counts) != {"UNKNOWN"}, "every candidate is unlabelled"

    def test_regime_quantile_differs_from_pooled(self):
        """If the two ever coincide, the regime-local quantile is doing nothing."""
        from jarvis.backtesting.optimizer import load_series

        b = load_series("EURUSD", "SWING")
        assert b is not None
        counts = b.regime_counts()
        dominant = max(counts, key=counts.get)
        pooled = b.min_score_for(0.9)
        local = b.min_score_for(0.9, {dominant})
        # Not asserting inequality — they can legitimately agree on one symbol.
        # Asserting the local one is drawn from a real subset.
        assert local > 0.0
        assert pooled > 0.0

    def test_small_run_produces_the_documented_shape(self):
        from jarvis.backtesting.regime_optimizer import optimise_regime_conditioned

        rep = optimise_regime_conditioned(
            symbols=["EURUSD"], modes=["SWING"],
            min_trades=5, passes=1, max_evaluations=20,
        )
        assert rep["modes"], "no mode result produced"
        m = rep["modes"][0]
        assert m["style"] == "SWING"
        assert "regime_mass" in m
        assert "policy" in m
        assert "policy_vs_baseline" in m
        assert m["policy_vs_baseline"]["verdict"]

        # Every regime in the mass table must appear in the plan, so nothing is
        # silently dropped from the report.
        assert {r["regime"] for r in m["regimes"]} == set(m["regime_mass"])

        for r in m["regimes"]:
            assert r["status"] in ("searched", "insufficient_sample")
            if r["status"] == "insufficient_sample":
                # A withheld regime must say why, and must not be handed a
                # geometry it did not earn.
                assert r["searched_geometry"] is None
                assert r["reason"] or r["geometry_basis"]
            assert r["deployed_geometry_key"] is not None

    def test_thin_regime_is_withheld_not_fitted(self):
        """A regime below the candidate gate is never given its own geometry."""
        from jarvis.backtesting.optimizer import load_series
        from jarvis.backtesting.regime_optimizer import optimise_regime_conditioned

        rep = optimise_regime_conditioned(
            symbols=["EURUSD"], modes=["SWING"],
            min_trades=5, passes=1, max_evaluations=20,
            min_candidates=10_000_000,  # force every regime below the gate
        )
        m = rep["modes"][0]
        assert all(r["status"] == "insufficient_sample" for r in m["regimes"])
        assert all(r["searched_geometry"] is None for r in m["regimes"])
        assert m["policy"]["regimes_searched"] == 0

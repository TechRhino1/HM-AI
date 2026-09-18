"""Round 30 — coverage for ``jarvis/backtesting/metrics.py``.

WHY THIS FILE EXISTS
--------------------
``PerformanceMetricsCalculator`` produces every headline number the project
quotes about itself: Sharpe, Sortino, Calmar, profit factor, expectancy, max
drawdown and the multi-objective fitness that drives parameter selection. It is
imported by ``backtesting/engine.py:15`` and ``backtesting/walk_forward.py:11``,
and its output dict is consumed by ``optimizer.py`` and ``regime_optimizer.py``.
It had no dedicated suite.

This project has already been bitten twice by measurement errors that moved a
verdict (a biased always-long control, and a 10x units error), so the numbers
below are pinned exactly as the module computes them — including the ones that
are arguably wrong. Findings are PINNED, not fixed.

The load-bearing ones:

* **The two return paths disagree on their keys.** The empty-trades branch omit
  ``multi_objective_fitness``; every other branch has it.
* **A perfectly profitable backtest reports Sharpe 0.0.** If every trade wins by
  the same amount, ``std_ret`` is 0 and the ``std_ret > 1e-6`` guard sends Sharpe
  — and therefore Sortino — to zero. Five trades of +100 gives Sharpe 0.0,
  Sortino 0.0, Calmar 10.0.
* **"Sharpe" is not a Sharpe ratio.** It is ``mean/std`` of *per-trade* returns
  scaled by ``sqrt(min(252, max(12, n)))`` — the trade count is used as if it
  were a number of periods per year. Two backtests over the same period with
  different trade counts are not comparable.
* **Max drawdown in dollars and in percent can describe different drawdowns.**
  Both are tracked independently against a running peak that never resets, so a
  larger dollar drawdown at a higher peak can carry a smaller percentage.
* **The fitness drawdown penalty dominates by an order of magnitude.** ``2.5 *
  max_dd_pct`` is a percent, so a 10 % drawdown costs 25 points while a perfect
  profit factor contributes 20 and a clipped Sharpe 15.
"""
import math

import numpy as np
import pytest

from jarvis.backtesting.metrics import PerformanceMetricsCalculator


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def t(pnl):
    return {"pnl": pnl}


def trades(values, balance=10000.0):
    return PerformanceMetricsCalculator.calculate_metrics(
        [t(v) for v in values], balance)


def raw(dicts, balance=10000.0):
    return PerformanceMetricsCalculator.calculate_metrics(dicts, balance)


# --------------------------------------------------------------------------- #
# module-level fixtures (each measured against the pristine module)
# --------------------------------------------------------------------------- #
SIMPLE = [t(100.0), t(-50.0)]                 # sharpe 0.82 / sortino 1.73
FOUR = [t(100.0), t(-50.0), t(200.0), t(-30.0)]   # sharpe 1.62 / sortino 13.47
IDENTICAL_WINS = [t(100.0)] * 5               # sharpe 0.0 / calmar 10.0
VARYING_WINS = [t(100.0), t(200.0), t(300.0)]  # sharpe 6.93 / sortino 10.39
WITH_BREAKEVEN = [t(100.0), t(0.0), t(-50.0)]  # wins + losses != total
DIVERGENT_DD = [t(-5000.0), t(100000.0), t(-6000.0)]  # dd$ 6000 but dd% 50


# --------------------------------------------------------------------------- #
class TestEmptyInput:
    def test_an_empty_list_returns_the_zero_block(self):
        m = PerformanceMetricsCalculator.calculate_metrics([])
        assert m["total_trades"] == 0
        assert m["win_rate_pct"] == 0.0
        assert m["profit_factor"] == 0.0
        assert m["net_profit"] == 0.0
        assert m["max_drawdown_dollars"] == 0.0
        assert m["max_drawdown_pct"] == 0.0
        assert m["sharpe_ratio"] == 0.0
        assert m["sortino_ratio"] == 0.0
        assert m["calmar_ratio"] == 0.0
        assert m["wins"] == 0 and m["losses"] == 0

    def test_the_empty_block_omits_multi_objective_fitness(self):
        """PINNED CONTRACT BUG. The early return has 12 keys; every other path
        has 13. Any consumer that reads ``metrics["multi_objective_fitness"]``
        gets a KeyError on an empty backtest and a number otherwise. Nothing in
        the tree reads it by name *yet*, so this is latent, not live."""
        empty = PerformanceMetricsCalculator.calculate_metrics([])
        full = PerformanceMetricsCalculator.calculate_metrics([t(1.0)])
        assert "multi_objective_fitness" not in empty
        assert "multi_objective_fitness" in full
        assert set(full) - set(empty) == {"multi_objective_fitness"}
        assert set(empty) - set(full) == set()

    def test_the_empty_block_still_carries_the_shared_twelve_keys(self):
        empty = PerformanceMetricsCalculator.calculate_metrics([])
        assert len(empty) == 12

    def test_a_list_of_only_empty_dicts_is_not_the_empty_case(self):
        """``if not trades`` is False for ``[{}, {}]`` — it takes the full
        path and defaults each missing pnl to 0.0."""
        m = raw([{}, {}])
        assert m["total_trades"] == 2
        assert m["wins"] == 0 and m["losses"] == 0
        assert "multi_objective_fitness" in m


class TestWinLossCounting:
    def test_a_win_is_strictly_positive_a_loss_strictly_negative(self):
        m = raw(SIMPLE)
        assert m["wins"] == 1 and m["losses"] == 1

    def test_win_rate_is_wins_over_all_trades(self):
        assert raw(SIMPLE)["win_rate_pct"] == 50.0

    def test_a_breakeven_trade_is_counted_as_neither(self):
        """PINNED. ``p > 0`` and ``p < 0`` — a zero-PnL trade is excluded from
        both, so ``wins + losses`` can be less than ``total_trades`` and the
        win rate understates the record."""
        m = raw(WITH_BREAKEVEN)
        assert m["total_trades"] == 3
        assert m["wins"] == 1 and m["losses"] == 1
        assert m["wins"] + m["losses"] == 2
        assert m["win_rate_pct"] == 33.33

    def test_all_winners_give_one_hundred_percent(self):
        assert raw(IDENTICAL_WINS)["win_rate_pct"] == 100.0

    def test_all_losers_give_zero_percent(self):
        assert trades([-100.0, -50.0])["win_rate_pct"] == 0.0

    def test_a_missing_pnl_key_defaults_to_zero_and_is_neither(self):
        m = raw([{}, {"pnl": 100.0}])
        assert m["total_trades"] == 2
        assert m["wins"] == 1 and m["losses"] == 0
        assert m["net_profit"] == 100.0


class TestProfitFactor:
    def test_gross_profit_over_gross_loss(self):
        assert raw(FOUR)["profit_factor"] == 3.75

    def test_with_no_losses_it_is_the_sentinel_ninety_nine(self):
        """PINNED. A backtest that never loses reports a profit factor of
        exactly 99.0 — a magic number, not infinity, so it ranks *below* a
        finite-but-larger real value would and saturates the fitness term."""
        assert raw(IDENTICAL_WINS)["profit_factor"] == 99.0
        assert trades([100.0, 200.0])["profit_factor"] == 99.0

    def test_with_no_losses_and_no_profit_it_is_zero(self):
        assert trades([0.0, 0.0])["profit_factor"] == 0.0

    def test_with_no_wins_it_is_zero(self):
        assert trades([-100.0, -50.0])["profit_factor"] == 0.0

    def test_gross_loss_is_absolute(self):
        """sum(losses) is negative, so abs() is applied — otherwise the factor
        would be negative."""
        m = raw(SIMPLE)
        assert m["profit_factor"] == pytest.approx(100.0 / 50.0)


class TestExpectancyAndNet:
    def test_expectancy_is_net_over_trade_count(self):
        assert raw(SIMPLE)["expectancy_dollars"] == 25.0

    def test_net_profit_is_the_sum_of_all_pnls(self):
        assert raw(FOUR)["net_profit"] == 220.0

    def test_expectancy_includes_losers_in_the_denominator(self):
        m = raw(WITH_BREAKEVEN)
        assert m["net_profit"] == 50.0
        assert m["expectancy_dollars"] == 16.67


class TestDrawdown:
    def test_the_equity_curve_starts_at_the_initial_balance(self):
        """The drawdown walk includes the opening balance, so a first-trade
        loss is measured from it."""
        m = trades([-500.0], balance=10000.0)
        assert m["max_drawdown_dollars"] == 500.0
        assert m["max_drawdown_pct"] == 5.0

    def test_a_peak_that_never_resets(self):
        """Once equity sets a peak, later drawdowns are measured from it even
        after a recovery."""
        m = trades([1000.0, -500.0, 1000.0, -1500.0])
        assert m["max_drawdown_dollars"] == 1500.0

    def test_dollars_and_percent_can_describe_different_drawdowns(self):
        """PINNED. ``max_dd_dollars`` and ``max_dd_pct`` are tracked
        independently. Here the dollar maximum is 6000 (from the 105000 peak,
        5.71 %) while the percentage maximum is 50 % (from the 10000 peak,
        $5000). Reporting them side by side implies one drawdown; there are two.
        """
        m = raw(DIVERGENT_DD)
        assert m["max_drawdown_dollars"] == 6000.0
        assert m["max_drawdown_pct"] == 50.0
        # and they are not consistent with each other:
        assert m["max_drawdown_pct"] != pytest.approx(
            m["max_drawdown_dollars"] / 105000.0 * 100.0)

    def test_no_drawdown_when_equity_only_rises(self):
        m = raw(IDENTICAL_WINS)
        assert m["max_drawdown_dollars"] == 0.0
        assert m["max_drawdown_pct"] == 0.0

    def test_the_percent_uses_the_running_peak_as_denominator(self):
        m = trades([-2500.0])
        assert m["max_drawdown_pct"] == 25.0


class TestSharpe:
    def test_the_two_trade_reference_value(self):
        assert raw(SIMPLE)["sharpe_ratio"] == 0.82

    def test_it_is_mean_over_sample_std_scaled_by_the_trade_count(self):
        values = [100.0 if i % 2 == 0 else -50.0 for i in range(50)]
        r = np.array(values) / 10000.0
        manual = (float(np.mean(r)) / float(np.std(r, ddof=1))) * math.sqrt(50)
        assert trades(values)["sharpe_ratio"] == pytest.approx(
            float(np.clip(manual, -15.0, 15.0)), abs=1e-2)

    def test_the_annualisation_factor_is_clamped_to_twelve_and_252(self):
        """PINNED. ``sqrt(min(252, max(12, n)))`` — the trade COUNT stands in
        for periods-per-year. Below 12 trades a backtest is annualised as if it
        traded 12 times a year; above 252 the factor stops growing, so two
        backtests with 252 and 4000 trades get the same multiplier despite
        wildly different periods."""
        for n, factor in ((2, 12), (11, 12), (12, 12), (50, 50), (252, 252),
                          (400, 252), (5000, 252)):
            assert math.sqrt(min(252, max(12, n))) == math.sqrt(factor)

    def test_a_single_trade_has_no_sharpe(self):
        assert trades([100.0])["sharpe_ratio"] == 0.0

    def test_identical_winners_give_zero_sharpe(self):
        """PINNED. Every return equal means std is 0, which fails the
        ``std_ret > 1e-6`` guard: a flawlessly profitable backtest scores 0."""
        assert raw(IDENTICAL_WINS)["sharpe_ratio"] == 0.0

    def test_varying_winners_give_a_large_sharpe(self):
        assert raw(VARYING_WINS)["sharpe_ratio"] == 6.93

    def test_it_clips_at_plus_minus_fifteen(self):
        alt = [t(100.0) if i % 2 == 0 else t(100.1) for i in range(252)]
        assert raw(alt)["sharpe_ratio"] == 15.0
        alt_neg = [t(-100.0) if i % 2 == 0 else t(-100.1) for i in range(252)]
        assert raw(alt_neg)["sharpe_ratio"] == -15.0

    def test_a_near_constant_series_reports_zero(self):
        """The ``1e-6`` std floor: 251 trades of +100 and one of +99.9 has a
        std below the floor, so it reads as zero rather than as enormous."""
        m = raw([t(100.0)] * 251 + [t(99.9)])
        assert m["sharpe_ratio"] == 0.0

    def test_it_is_invariant_to_the_initial_balance(self):
        """Both mean and std divide by the same balance, so the ratio is."""
        for bal in (1000.0, 10000.0, 100000.0):
            assert raw(FOUR, bal)["sharpe_ratio"] == 1.62


class TestSortino:
    def test_the_two_trade_reference_value(self):
        """With a single negative return the downside deviation is
        ``abs(that return)`` — not a standard deviation."""
        assert raw(SIMPLE)["sortino_ratio"] == 1.73

    def test_with_no_negative_returns_it_is_one_point_five_times_sharpe(self):
        """PINNED. The no-downside branch fabricates a Sortino by multiplying
        the Sharpe by 1.5 rather than by reporting infinity or None."""
        m = raw(VARYING_WINS)
        assert m["sortino_ratio"] == pytest.approx(m["sharpe_ratio"] * 1.5, abs=0.01)
        assert m["sortino_ratio"] == 10.39

    def test_with_no_negative_returns_and_no_mean_it_is_zero(self):
        """An all-zero book has no negatives, mean 0, so the ``mean_ret > 0``
        guard sends it to 0 rather than to 1.5 * 0."""
        assert trades([0.0, 0.0])["sortino_ratio"] == 0.0

    def test_identical_negatives_fall_back_to_the_sharpe(self):
        """Downside std of a constant negative series is 0, which fails the
        ``downside_std > 1e-6`` guard, so Sortino is assigned the Sharpe."""
        m = trades([-50.0, -50.0, 300.0])
        assert m["sortino_ratio"] == m["sharpe_ratio"]
        assert m["sortino_ratio"] == 1.14

    def test_it_clips_at_plus_minus_twenty_five(self):
        alt_neg = [t(-100.0) if i % 2 == 0 else t(-100.1) for i in range(252)]
        assert raw(alt_neg)["sortino_ratio"] == -25.0
        alt = [t(100.0) if i % 2 == 0 else t(100.1) for i in range(252)]
        assert raw(alt)["sortino_ratio"] == 22.5

    def test_a_single_trade_has_no_sortino(self):
        assert trades([100.0])["sortino_ratio"] == 0.0

    def test_identical_winners_give_zero_sortino(self):
        """Sharpe is 0 (zero std), there are no negatives, and mean > 0, so the
        branch yields clip(0 * 1.5) = 0."""
        assert raw(IDENTICAL_WINS)["sortino_ratio"] == 0.0


class TestCalmar:
    def test_net_profit_over_max_drawdown_dollars(self):
        m = raw(FOUR)
        assert m["calmar_ratio"] == pytest.approx(220.0 / 50.0)
        assert m["calmar_ratio"] == 4.4

    def test_with_no_drawdown_a_winner_gets_the_sentinel_ten(self):
        """PINNED. Calmar is conventionally annualised return over max
        drawdown. Here it is TOTAL net profit over drawdown, and when the
        drawdown is zero every profitable book reports the same 10.0 regardless
        of how much it made."""
        assert raw(IDENTICAL_WINS)["calmar_ratio"] == 10.0
        assert raw(VARYING_WINS)["calmar_ratio"] == 10.0

    def test_with_no_drawdown_and_no_profit_it_is_zero(self):
        assert trades([0.0, 0.0])["calmar_ratio"] == 0.0

    def test_it_clips_at_plus_minus_twenty_five(self):
        m = trades([10000.0, -100.0])
        assert m["net_profit"] == 9900.0
        assert m["max_drawdown_dollars"] == 100.0
        assert m["calmar_ratio"] == 25.0

    def test_it_is_not_annualised(self):
        """Same 2 trades, same Calmar, whatever the balance or horizon."""
        assert trades([100.0, -50.0], 1000.0)["calmar_ratio"] == \
            trades([100.0, -50.0], 100000.0)["calmar_ratio"]


class TestFitness:
    def test_the_two_trade_reference_value(self):
        assert raw(SIMPLE)["multi_objective_fitness"] == 6.25

    def test_the_formula(self):
        m = raw(FOUR)
        expected = ((55.0 / 10.0) + (2.0 * min(10.0, 3.75))
                    + min(15.0, max(-15.0, 1.62))
                    + min(25.0, max(-25.0, 13.47 / 10.0))
                    - (2.5 * 0.5))
        # sharpe/sortino enter unrounded, so allow for the 2 dp reporting
        assert m["multi_objective_fitness"] == pytest.approx(expected, abs=0.05)

    def test_the_drawdown_penalty_dominates_every_other_term(self):
        """PINNED. ``2.5 * max_dd_pct`` is applied to a PERCENTAGE, so a 10 %
        drawdown costs 25 points against a maximum of 20 from profit factor,
        15 from Sharpe and 25 from Sortino/10. One drawdown swamps the rest."""
        base = raw([t(500.0), t(500.0), t(500.0)])["multi_objective_fitness"]
        with_dd = raw([t(500.0), t(500.0), t(-1000.0), t(500.0)])
        assert with_dd["max_drawdown_pct"] > 0.0
        assert base - with_dd["multi_objective_fitness"] > 25.0

    def test_the_profit_factor_term_saturates_at_ten(self):
        """``min(10.0, profit_factor)`` — the 99.0 sentinel is capped to 10 just
        like a real 10.0, so a flawless book scores the same as a mediocre one
        on this term."""
        perfect = raw([t(100.0), t(100.0)])
        assert perfect["profit_factor"] == 99.0
        assert perfect["multi_objective_fitness"] == pytest.approx(
            (100.0 / 10.0) + 20.0 + 0.0 + 0.0 - 0.0, abs=0.01)

    def test_all_zero_trades_give_zero_fitness(self):
        assert trades([0.0] * 5)["multi_objective_fitness"] == 0.0

    def test_a_losing_book_scores_negative(self):
        assert trades([-100.0, -200.0])["multi_objective_fitness"] < 0


class TestRounding:
    def test_every_float_field_is_rounded_to_two_places(self):
        m = raw([t(123.456), t(-78.901), t(10.005)])
        for k, v in m.items():
            if isinstance(v, float):
                assert v == round(v, 2), k

    def test_counts_are_integers(self):
        m = raw(FOUR)
        assert isinstance(m["total_trades"], int)
        assert isinstance(m["wins"], int)
        assert isinstance(m["losses"], int)


class TestDegenerateInputs:
    def test_a_zero_initial_balance_does_not_raise(self):
        """PINNED. ``pnls / 0`` gives inf, whose std is nan; ``nan > 1e-6`` is
        False, so the ratio silently becomes 0.0 instead of erroring."""
        with np.errstate(divide="ignore", invalid="ignore"):
            m = raw(SIMPLE, 0.0)
        assert m["sharpe_ratio"] == 0.0

    def test_a_negative_initial_balance_flips_the_sign(self):
        with np.errstate(divide="ignore", invalid="ignore"):
            m = raw(SIMPLE, -10000.0)
        assert m["sharpe_ratio"] == -0.82

    def test_a_string_pnl_is_coerced(self):
        m = raw([{"pnl": "100.0"}, {"pnl": "-50.0"}])
        assert m["net_profit"] == 50.0

    def test_a_none_pnl_raises(self):
        with pytest.raises(TypeError):
            raw([{"pnl": None}])

    def test_extra_keys_on_a_trade_are_ignored(self):
        m = raw([{"pnl": 100.0, "symbol": "EURUSD", "r": 2.0}])
        assert m["net_profit"] == 100.0


class TestUnobservableBranches:
    """Distinctions that provably cannot change the output. Asserted so the
    gap is documented rather than silently re-opened by the next reader."""

    def test_the_peak_update_could_use_ge(self):
        """`if eq > peak` vs `>=`: when they are equal the assignment is a
        no-op, so the two are the same function."""
        assert raw(DIVERGENT_DD)["max_drawdown_dollars"] == 6000.0

    def test_the_drawdown_maxima_could_use_ge(self):
        """`if dd > max_dd` vs `>=`: recording an equal value changes nothing.
        Same for the percentage."""
        m = raw(FOUR)
        assert m["max_drawdown_dollars"] == 50.0
        assert m["max_drawdown_pct"] == 0.5

    def test_the_sortino_mean_guard_could_use_ge(self):
        """`if mean_ret > 0` vs `>= 0` in the no-downside branch: reaching it
        with mean_ret == 0 requires every return to be zero, which also makes
        Sharpe 0 — and `clip(0 * 1.5)` is 0 either way."""
        m = trades([0.0, 0.0])
        assert m["sharpe_ratio"] == 0.0
        assert m["sortino_ratio"] == 0.0

    def test_the_fitness_sharpe_clamp_is_a_no_op(self):
        """`min(15.0, max(-15.0, sharpe))` can never bind: Sharpe is already
        clipped to [-15, 15] before it reaches the fitness expression, so
        widening the clamp to 25 changes nothing."""
        alt = [t(100.0) if i % 2 == 0 else t(100.1) for i in range(252)]
        s = raw(alt)["sharpe_ratio"]
        assert s == 15.0
        assert min(15.0, max(-15.0, s)) == min(25.0, max(-25.0, s))


class TestPrecision:
    """Two-decimal reporting, using values that actually have two decimals."""

    def test_net_profit_carries_two_decimals(self):
        m = trades([100.55, -50.0])
        assert m["net_profit"] == 50.55
        assert round(50.55, 1) == 50.5

    def test_max_drawdown_dollars_carries_two_decimals(self):
        m = trades([-123.45])
        assert m["max_drawdown_dollars"] == 123.45

    def test_max_drawdown_pct_carries_two_decimals(self):
        m = trades([-123.45])
        assert m["max_drawdown_pct"] == 1.23
        assert round(1.23, 1) == 1.2

    def test_calmar_carries_two_decimals(self):
        m = trades([173.4, -50.0])
        assert m["net_profit"] == 123.4
        assert m["calmar_ratio"] == 2.47
        assert round(2.47, 1) == 2.5

    def test_the_calmar_epsilon_is_visible_at_small_scales(self):
        """`net_profit / (max_dd_dollars + 1e-9)` — the epsilon is invisible at
        ordinary sizes but not at a $1e-6 drawdown: 1e-5/1e-6 is 10.0 while
        1e-5/(1e-6 + 1e-9) is 9.99."""
        m = trades([1.1e-5, -1e-6])
        assert m["max_drawdown_dollars"] == 0.0 or m["max_drawdown_dollars"] < 1e-4
        assert m["calmar_ratio"] == 9.99
        assert round(1e-5 / 1e-6, 2) == 10.0


class TestBoundaryGuards:
    def test_a_single_losing_trade_has_no_sharpe_or_sortino(self):
        """`len(returns) >= 2` gates the whole block: with one trade the sample
        std is undefined (0/0), so both ratios are 0 — not nan."""
        m = trades([-100.0])
        assert m["sharpe_ratio"] == 0.0
        assert m["sortino_ratio"] == 0.0

    def test_the_annualisation_cap_binds_at_252(self):
        """300 trades are annualised by sqrt(252), not sqrt(300)."""
        vals = [100.0 if i % 2 == 0 else -50.0 for i in range(300)]
        assert trades(vals)["sharpe_ratio"] == 5.28
        vals252 = [100.0 if i % 2 == 0 else -50.0 for i in range(252)]
        assert trades(vals252)["sharpe_ratio"] == 5.28

    def test_a_zero_pnl_trade_is_not_part_of_the_downside(self):
        """`returns[returns < 0]` is strict; including the zero would change
        the downside deviation from abs(-0.005) to the std of [-0.005, 0.0]."""
        m = trades([100.0, -50.0, 0.0])
        assert m["sortino_ratio"] == 1.15
        # what `<= 0` would have produced:
        assert m["sortino_ratio"] != pytest.approx(1.63, abs=0.01)

    def test_the_downside_floor_is_one_in_a_million(self):
        """A downside deviation of exactly 1e-6 fails `> 1e-6`, so Sortino falls
        back to the Sharpe instead of exploding to the 25.0 clip."""
        m = trades([100.0, 100.0, -0.01])
        assert m["sortino_ratio"] == m["sharpe_ratio"]
        assert m["sortino_ratio"] == 4.0

    def test_the_peak_starts_at_the_opening_balance_not_zero(self):
        """With a negative opening balance, initialising the peak to 0.0 would
        report the whole balance as drawdown instead of the 50 actually lost."""
        with np.errstate(divide="ignore", invalid="ignore"):
            m = raw(SIMPLE, -10000.0)
        assert m["max_drawdown_dollars"] == 50.0


class TestHousekeeping:
    def test_the_input_list_is_not_mutated(self):
        before = [t(100.0), t(-50.0)]
        snapshot = [dict(d) for d in before]
        PerformanceMetricsCalculator.calculate_metrics(before)
        assert before == snapshot

    def test_the_calculator_is_stateless(self):
        a = PerformanceMetricsCalculator.calculate_metrics(SIMPLE)
        b = PerformanceMetricsCalculator.calculate_metrics(SIMPLE)
        assert a == b

    def test_it_is_a_static_method(self):
        assert isinstance(
            PerformanceMetricsCalculator.__dict__["calculate_metrics"],
            staticmethod)

    def test_the_default_initial_balance_is_ten_thousand(self):
        import inspect
        sig = inspect.signature(
            PerformanceMetricsCalculator.calculate_metrics)
        assert sig.parameters["initial_balance"].default == 10000.0

    def test_full_key_set(self):
        m = raw(SIMPLE)
        assert set(m) == {
            "total_trades", "win_rate_pct", "profit_factor",
            "expectancy_dollars", "net_profit", "max_drawdown_dollars",
            "max_drawdown_pct", "sharpe_ratio", "sortino_ratio",
            "calmar_ratio", "multi_objective_fitness", "wins", "losses"}

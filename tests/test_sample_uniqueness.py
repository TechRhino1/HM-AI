"""Sample-uniqueness weighting — correctness and scale.

The previous implementation had two defects that are both pinned here:

1. It returned ``1 / concurrency-at-the-start``, not López de Prado's average
   uniqueness over the label's span. The tell is a 1-bar label sharing its start
   with a 10-bar label: the long label is alone for nine of its ten bars, so its
   average uniqueness is 0.95 while the short one's is 0.50. The old code said
   0.5 for both.
2. It was O(n²) in Python, so 4,000 labels took 3.5s and the audit's 94,937
   trades would have taken ~33 minutes. It was therefore never run at scale,
   which is why the semantic error went unnoticed.
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from jarvis.learning.sample_weights import SampleUniquenessWeightEngine as E


# ── correctness ────────────────────────────────────────────────────────────
def test_a_lone_label_has_full_uniqueness():
    u = E.average_uniqueness([{"duration_bars": 10}])
    assert u[0] == pytest.approx(1.0)


def test_two_identical_labels_score_one_half_each():
    """Both labels must span the same bars to be 'identical'.

    ``entry_bar`` is given explicitly. Relying on the index fallback would start
    them one bar apart and produce 0.55, not 0.50 — which is correct behaviour for
    *that* input, and the reason the fallback is documented as an approximation.
    """
    u = E.average_uniqueness(
        [{"entry_bar": 0, "duration_bars": 10}, {"entry_bar": 0, "duration_bars": 10}]
    )
    assert u == pytest.approx([0.5, 0.5])


def test_index_fallback_assumes_one_label_per_bar_in_order():
    """Pin the fallback's semantics so it cannot drift silently.

    With no ``entry_bar``, labels start at their list index: spans [0,9] and
    [1,10]. Bar 0 has c=1, bars 1-9 have c=2, bar 10 has c=1, so each label's
    average uniqueness is (1 + 9/2) / 10 = 0.55.
    """
    u = E.average_uniqueness([{"duration_bars": 10}, {"duration_bars": 10}])
    assert u == pytest.approx([0.55, 0.55])


def test_short_label_sharing_a_start_is_less_unique_than_the_long_one():
    """The case the old implementation got wrong.

    Short label spans bar 0 only, where c=2  -> u = 1/2      = 0.50
    Long label spans bars 0..9, c=[2,1,...]  -> u = 9.5/10   = 0.95
    The old code returned 0.5 for both, because it only read c at the start.
    """
    u = E.average_uniqueness(
        [{"entry_bar": 0, "duration_bars": 1}, {"entry_bar": 0, "duration_bars": 10}]
    )
    assert u[0] == pytest.approx(0.5)
    assert u[1] == pytest.approx(0.95)
    assert u[1] > u[0]


def test_disjoint_labels_are_both_fully_unique():
    u = E.average_uniqueness(
        [{"entry_bar": 0, "duration_bars": 5}, {"entry_bar": 50, "duration_bars": 5}]
    )
    assert u == pytest.approx([1.0, 1.0])


def test_uniqueness_never_exceeds_one_or_drops_below_one_over_n():
    rng = np.random.default_rng(7)
    trades = [
        {"entry_bar": int(b), "duration_bars": int(d)}
        for b, d in zip(rng.integers(0, 400, 300), rng.integers(1, 60, 300))
    ]
    u = E.average_uniqueness(trades)
    assert np.all(u > 0) and np.all(u <= 1.0 + 1e-12)


def test_entry_bar_is_honoured_over_list_position():
    """Irregularly spaced entries must use the real bar, not the row index.

    Row 1 starts 1,000 bars after row 0, so the two do not overlap and both are
    fully unique. Using the index instead would place them adjacent and wrongly
    report overlap.
    """
    u = E.average_uniqueness(
        [{"entry_bar": 0, "duration_bars": 10}, {"entry_bar": 1000, "duration_bars": 10}]
    )
    assert u == pytest.approx([1.0, 1.0])


def test_effective_sample_size_counts_independent_bets():
    """20 labels stacked on the *same* bars are worth 1 independent bet, not 20."""
    trades = [{"entry_bar": 0, "duration_bars": 10} for _ in range(20)]
    assert len(trades) == 20
    assert E.effective_sample_size(trades) == pytest.approx(1.0)


def test_sum_of_uniqueness_equals_covered_bars_over_duration():
    """The identity that caught a wrong expectation in this very file.

    For labels of equal duration ``d``::

        sum_i u_i = (1/d) * sum_t (c_t / c_t) = covered_bars / d

    It holds regardless of the overlap pattern, so it is a cheap invariant to
    assert against any implementation of average uniqueness.
    """
    d = 10
    for n in (1, 5, 20):
        trades = [{"entry_bar": 0, "duration_bars": d} for _ in range(n)]
        covered = d                      # all start together, so only d bars
        assert E.effective_sample_size(trades) == pytest.approx(covered / d)

    # Staggered starts: spans [i, i+d-1] for i in 0..n-1 -> covered = n + d - 1
    n = 20
    trades = [{"entry_bar": i, "duration_bars": d} for i in range(n)]
    assert E.effective_sample_size(trades) == pytest.approx((n + d - 1) / d)


def test_effective_sample_size_equals_n_when_nothing_overlaps():
    trades = [{"entry_bar": i * 100, "duration_bars": 5} for i in range(25)]
    assert E.effective_sample_size(trades) == pytest.approx(25.0)


def test_weights_sum_to_one_and_empty_input_is_safe():
    assert E.get_sample_weights([]).size == 0
    w = E.get_sample_weights([{"duration_bars": 3}, {"duration_bars": 7}])
    assert w.sum() == pytest.approx(1.0)


def test_magnitude_weighting_is_opt_in_and_changes_the_answer():
    """The old default folded |pnl| into the weights, which is a different
    estimator (return-magnitude, not uniqueness) and biases expectancy upward."""
    trades = [
        {"duration_bars": 10, "pnl": 1.0},
        {"duration_bars": 10, "pnl": 100.0},
    ]
    pure = E.get_sample_weights(trades)
    assert pure == pytest.approx([0.5, 0.5])          # overlap only
    mag = E.get_sample_weights(trades, magnitude_weighting=True)
    assert mag[1] > mag[0]                            # the big winner dominates


def test_weighted_mean_downweights_duplicated_evidence():
    """Two copies of a winning bet must not read as two independent wins."""
    dup = [
        {"duration_bars": 10, "pnl": 1.0},
        {"duration_bars": 10, "pnl": 1.0},
    ]
    assert E.weighted_mean(dup, np.array([1.0, 1.0])) == pytest.approx(1.0)
    # A losing trade that does not overlap must move the weighted mean down.
    mixed = [
        {"entry_bar": 0, "duration_bars": 10, "pnl": 1.0},
        {"entry_bar": 500, "duration_bars": 10, "pnl": -1.0},
    ]
    assert E.weighted_mean(mixed, np.array([1.0, -1.0])) == pytest.approx(0.0)


# ── scale ──────────────────────────────────────────────────────────────────
def test_is_linear_not_quadratic_at_audit_scale():
    """94,937 labels with 200-bar holds must finish in seconds, not half an hour.

    The old O(n²) loop took 3.5s for 4,000 labels, which extrapolates to ~33
    minutes at the audit's size. The bound here is deliberately loose (15s) so it
    fails on a complexity regression, not on a slow machine.
    """
    n = 20_000
    trades = [{"entry_bar": i, "duration_bars": 200} for i in range(n)]
    t0 = time.perf_counter()
    u = E.average_uniqueness(trades)
    elapsed = time.perf_counter() - t0
    assert len(u) == n
    assert elapsed < 15.0, f"average_uniqueness took {elapsed:.1f}s for {n} labels"

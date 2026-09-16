"""Deflated Sharpe Ratio — validates the implementation against known values.

The DSR is easy to implement in a way that always returns something plausible, so
these tests anchor it three ways: against the normal-distribution limit (where
the PSR must reduce to the ordinary one-sample z-test), against the documented
monotonicity of the expected-maximum-Sharpe term, and against the acceptance bar
the project actually uses.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.stats import norm

from jarvis.learning.deflated_sharpe import (
    EULER_MASCHERONI,
    annualised_sharpe,
    deflated_sharpe_ratio,
    effective_trials,
    expected_max_sharpe,
    min_track_record_length,
    probabilistic_sharpe_ratio,
    sharpe_stats,
)


# ── sharpe_stats ───────────────────────────────────────────────────────────
def test_sharpe_stats_recovers_known_moments():
    rng = np.random.default_rng(0)
    r = rng.normal(0.0, 1.0, 200_000)
    s = sharpe_stats(r)
    assert s["n"] == 200_000
    assert s["sr"] == pytest.approx(0.0, abs=0.02)
    assert s["skew"] == pytest.approx(0.0, abs=0.05)
    assert s["kurt"] == pytest.approx(3.0, abs=0.1)   # Pearson, not excess


def test_sharpe_stats_is_per_observation_not_annualised():
    rng = np.random.default_rng(1)
    r = rng.normal(0.001, 0.01, 5000)
    s = sharpe_stats(r, periods_per_year=252)
    assert s["sr"] == pytest.approx(r.mean() / r.std(ddof=1), rel=1e-9)
    assert s["sr_annual"] == pytest.approx(s["sr"] * math.sqrt(252), rel=1e-9)
    # The two must not be confused: the tests take the per-observation value.
    assert s["sr_annual"] > s["sr"] * 15


def test_sharpe_stats_handles_degenerate_input():
    assert sharpe_stats([])["n"] == 0
    assert sharpe_stats([1.0, 1.0, 1.0])["sr"] == 0.0   # zero variance


# ── probabilistic Sharpe ratio ─────────────────────────────────────────────
def test_psr_matches_lo_closed_form_when_returns_are_normal():
    """Anchor the PSR to Lo (2002), not to a bare z-test.

    For iid normal returns the Sharpe estimator has ``Var(SR) = (1 + SR^2/2)/T``,
    so the denominator is ``sqrt(1 + SR^2/2)`` — **not** 1. Asserting
    ``norm.cdf(SR*sqrt(T-1))`` here was my own mistake: it drops the SR^2/2 term
    and would have let a wrong denominator through.
    """
    sr, n = 0.10, 501
    psr = probabilistic_sharpe_ratio(sr, n, skew=0.0, kurt=3.0)
    assert psr == pytest.approx(
        norm.cdf(sr * math.sqrt(n - 1) / math.sqrt(1.0 + 0.5 * sr * sr)), rel=1e-12
    )
    # Sanity: the correction is small at a modest Sharpe but not zero.
    assert psr != pytest.approx(norm.cdf(sr * math.sqrt(n - 1)), rel=1e-6)


def test_psr_is_monotone_in_sharpe_and_in_sample_size():
    base = probabilistic_sharpe_ratio(0.10, 500, 0.0, 3.0)
    assert probabilistic_sharpe_ratio(0.20, 500, 0.0, 3.0) > base
    assert probabilistic_sharpe_ratio(0.10, 2000, 0.0, 3.0) > base


def test_negative_skew_and_fat_tails_both_lower_the_psr():
    """The whole point of the non-normality correction: a Sharpe earned from a
    strategy that occasionally loses badly is weaker evidence than the same
    Sharpe from a symmetric one."""
    base = probabilistic_sharpe_ratio(0.15, 500, 0.0, 3.0)
    assert probabilistic_sharpe_ratio(0.15, 500, -1.5, 3.0) < base
    assert probabilistic_sharpe_ratio(0.15, 500, 0.0, 10.0) < base


# ── expected max Sharpe ────────────────────────────────────────────────────
def test_expected_max_sharpe_is_zero_without_a_search():
    assert expected_max_sharpe(0.01, 1) == 0.0
    assert expected_max_sharpe(0.0, 1000) == 0.0


def test_expected_max_sharpe_grows_with_trials_and_with_dispersion():
    v = 0.01
    a = expected_max_sharpe(v, 10)
    b = expected_max_sharpe(v, 1_000)
    c = expected_max_sharpe(v, 100_000)
    assert 0 < a < b < c
    assert expected_max_sharpe(0.04, 1_000) > expected_max_sharpe(0.01, 1_000)


def test_expected_max_sharpe_matches_the_closed_form():
    """Pin the constants, not just the shape — a wrong Euler-Mascheroni or a
    swapped 1/N and 1/(N*e) would still look monotone."""
    v, n = 0.0125, 250
    sd = math.sqrt(v)
    a = norm.ppf(1.0 - 1.0 / n)
    b = norm.ppf(1.0 - 1.0 / (n * math.e))
    want = sd * ((1.0 - EULER_MASCHERONI) * a + EULER_MASCHERONI * b)
    assert expected_max_sharpe(v, n) == pytest.approx(want, rel=1e-12)


# ── deflated Sharpe ratio ──────────────────────────────────────────────────
def test_dsr_equals_psr_when_only_one_trial_was_run():
    assert deflated_sharpe_ratio(0.12, 600, 0.0, 3.0, 1, 0.02) == pytest.approx(
        probabilistic_sharpe_ratio(0.12, 600, 0.0, 3.0), rel=1e-12
    )


def test_more_trials_deflate_the_same_track_record():
    kw = dict(sr=0.12, n=600, skew=0.0, kurt=3.0, var_of_trial_sharpes=0.02)
    d1 = deflated_sharpe_ratio(n_trials=1, **kw)
    d100 = deflated_sharpe_ratio(n_trials=100, **kw)
    d100k = deflated_sharpe_ratio(n_trials=100_000, **kw)
    assert d1 > d100 > d100k
    # A search of 100k configurations must be able to fail a plausible-looking
    # Sharpe. If this ever stops holding, the deflation is not biting.
    assert d100k < 0.95


def test_the_projects_acceptance_bar_is_reachable_but_demanding():
    """DSR > 0.95 is the stated promotion gate, so it must be *achievable* —
    otherwise it is a bar nobody can clear and it gets ignored.

    Note how much the trial *dispersion* matters. The geometry searches here are
    variations on one signal, so their Sharpes cluster tightly: at
    ``var=0.0025`` (sd 0.05) the best of 1,000 null trials is ~0.16 and an SR of
    0.30 clears the gate. At ``var=0.02`` (sd 0.14) the same search raises the bar
    to ~0.46 and the identical track record fails. **The DSR is a statement about
    the search, not only about the strategy.**
    """
    kw = dict(sr=0.30, n=5000, skew=0.0, kurt=3.0, n_trials=1000)
    assert deflated_sharpe_ratio(var_of_trial_sharpes=0.0025, **kw) > 0.95
    assert deflated_sharpe_ratio(var_of_trial_sharpes=0.02, **kw) < 0.95


def test_dsr_is_nan_on_undefined_estimator_variance():
    """When ``1 - skew*SR + (kurt-1)/4*SR^2`` goes non-positive the Sharpe
    estimator has no finite variance; returning a number there is a fabrication.

    With skew=3 and kurt=3 the quadratic is non-positive for SR in
    [0.354, 5.646], so SR=1.0 is inside that band.
    """
    assert math.isnan(deflated_sharpe_ratio(1.0, 500, 3.0, 3.0, 10, 0.01))
    # Just outside the band the estimator is defined again.
    assert not math.isnan(deflated_sharpe_ratio(0.2, 500, 3.0, 3.0, 10, 0.01))


# ── min track record length ────────────────────────────────────────────────
def test_min_track_record_length_increases_as_the_edge_shrinks():
    short = min_track_record_length(0.20, 0.0, 3.0)
    long_ = min_track_record_length(0.05, 0.0, 3.0)
    assert short < long_
    assert min_track_record_length(0.0, 0.0, 3.0) == float("inf")


def test_min_track_record_length_is_consistent_with_the_psr():
    """If the required length is L, then at T = L the PSR must be ~0.95."""
    sr, skew, kurt = 0.08, -0.5, 4.0
    L = min_track_record_length(sr, skew, kurt, prob=0.95)
    assert probabilistic_sharpe_ratio(sr, int(math.ceil(L)), skew, kurt) == pytest.approx(
        0.95, abs=0.01
    )


# ── effective trials ───────────────────────────────────────────────────────
def test_effective_trials_collapses_perfectly_correlated_trials():
    c = np.ones((50, 50))
    assert effective_trials(c, threshold=0.5) == 1


def test_effective_trials_counts_independent_trials_separately():
    c = np.eye(30)
    assert effective_trials(c, threshold=0.5) == 30


def test_effective_trials_rejects_a_non_square_matrix():
    with pytest.raises(ValueError):
        effective_trials(np.zeros((3, 4)))


# ── annualised_sharpe ──────────────────────────────────────────────────────
def test_annualised_sharpe_scales_by_root_periods():
    rng = np.random.default_rng(3)
    r = rng.normal(0.0005, 0.01, 10_000)
    assert annualised_sharpe(r, 252) == pytest.approx(
        sharpe_stats(r)["sr"] * math.sqrt(252), rel=1e-9
    )

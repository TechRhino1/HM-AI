"""Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014).

The project has evaluated hundreds of thousands of candidate geometries. Under
that many trials a good-looking Sharpe can appear by chance, so a Sharpe reported
without a correction for the search that produced it is not evidence. This module
supplies the correction.

Three quantities, in increasing order of honesty:

* ``sharpe_stats`` — the observed Sharpe with the skew and kurtosis needed to
  test it. A Sharpe alone is not testable: two strategies with the same Sharpe
  but different skew/kurtosis have different p-values.
* ``probabilistic_sharpe_ratio`` (PSR) — ``P(SR > 0)`` for *this* track record,
  correct for non-normality, but assuming the strategy was the only one tried.
* ``deflated_sharpe_ratio`` (DSR) — the same, with the benchmark raised to the
  Sharpe you would *expect* to see as the best of ``n_trials`` null strategies.
  This is the number that answers "is this better than the best of a large pile
  of coin flips".

Convention
----------
``sr`` is the **non-annualised, per-observation** Sharpe (``mean / std``). The
``sqrt(T - 1)`` scaling in the test statistic assumes that, so passing an
annualised Sharpe here would silently inflate every result by ``sqrt(252)``.
:func:`annualised_sharpe` and :func:`sharpe_stats` keep the two apart.

On ``n_trials``
---------------
Correlated trials inflate ``n_trials`` and therefore *over*-deflate, which errs
on the safe side. ``effective_trials`` provides a clustered estimate when a
correlation matrix over trial returns is available. Where it is not, pass the
raw count and treat the result as a lower bound on the DSR.
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Sequence

import numpy as np
from scipy.stats import norm

__all__ = [
    "EULER_MASCHERONI",
    "sharpe_stats",
    "annualised_sharpe",
    "expected_max_sharpe",
    "probabilistic_sharpe_ratio",
    "deflated_sharpe_ratio",
    "min_track_record_length",
    "effective_trials",
]

# Euler-Mascheroni constant, as used in the expected-maximum-Sharpe expression.
EULER_MASCHERONI = 0.5772156649015329


def sharpe_stats(returns: Sequence[float], periods_per_year: Optional[int] = None) -> Dict[str, float]:
    """Observed per-observation Sharpe plus the moments the test needs.

    Returns ``sr`` (per-observation), ``sr_annual`` when ``periods_per_year`` is
    given, ``n``, ``skew`` and ``kurt`` (Pearson kurtosis — normal == 3.0, *not*
    excess).
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = int(r.size)
    if n < 3:
        return {"sr": 0.0, "sr_annual": 0.0, "n": n, "skew": 0.0, "kurt": 3.0,
                "mean": 0.0, "std": 0.0}
    sd = float(r.std(ddof=1))
    mean = float(r.mean())
    sr = mean / sd if sd > 1e-12 else 0.0
    z = (r - mean) / sd if sd > 1e-12 else np.zeros(n)
    skew = float((z ** 3).mean())
    kurt = float((z ** 4).mean())
    out = {"sr": sr, "n": n, "skew": skew, "kurt": kurt, "mean": mean, "std": sd}
    out["sr_annual"] = sr * math.sqrt(periods_per_year) if periods_per_year else 0.0
    return out


def annualised_sharpe(returns: Sequence[float], periods_per_year: int = 252) -> float:
    """Annualised Sharpe, for reporting only — never feed this to the tests."""
    return sharpe_stats(returns)["sr"] * math.sqrt(periods_per_year)


def expected_max_sharpe(var_of_trial_sharpes: float, n_trials: int) -> float:
    """``E[max SR]`` over ``n_trials`` independent null trials.

    This is the benchmark the observed Sharpe must beat. It grows with both the
    number of trials and the dispersion of the trial Sharpes, which is why
    "we searched a lot" and "our search was noisy" both raise the bar.
    """
    if n_trials < 2 or var_of_trial_sharpes <= 0:
        return 0.0
    sd = math.sqrt(var_of_trial_sharpes)
    a = norm.ppf(1.0 - 1.0 / n_trials)
    b = norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return sd * ((1.0 - EULER_MASCHERONI) * a + EULER_MASCHERONI * b)


def probabilistic_sharpe_ratio(sr: float, n: int, skew: float, kurt: float,
                               benchmark_sr: float = 0.0) -> float:
    """``P(true SR > benchmark)`` for a single strategy, non-normality corrected.

    ``sr`` and ``benchmark_sr`` are per-observation. ``kurt`` is Pearson.
    """
    if n < 2:
        return float("nan")
    denom_sq = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if denom_sq <= 0:
        # Pathological moments: the variance of the SR estimator is not defined.
        return float("nan")
    z = (sr - benchmark_sr) * math.sqrt(n - 1) / math.sqrt(denom_sq)
    return float(norm.cdf(z))


def deflated_sharpe_ratio(
    sr: float,
    n: int,
    skew: float,
    kurt: float,
    n_trials: int,
    var_of_trial_sharpes: float,
) -> float:
    """DSR — PSR against the expected best-of-``n_trials`` benchmark.

    Accept when DSR > 0.95 (the project's stated acceptance bar).
    """
    sr0 = expected_max_sharpe(var_of_trial_sharpes, n_trials)
    return probabilistic_sharpe_ratio(sr, n, skew, kurt, benchmark_sr=sr0)


def min_track_record_length(sr: float, skew: float, kurt: float,
                            benchmark_sr: float = 0.0, prob: float = 0.95) -> float:
    """Observations needed before ``P(SR > benchmark)`` reaches ``prob``.

    Answers "how long would this have to run before it meant anything" — useful
    when a backtest looks good but is short.
    """
    if sr <= benchmark_sr:
        return float("inf")
    z = norm.ppf(prob)
    denom_sq = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if denom_sq <= 0:
        return float("inf")
    return 1.0 + denom_sq * (z / (sr - benchmark_sr)) ** 2


def effective_trials(corr: np.ndarray, threshold: float = 0.5) -> int:
    """Number of *independent* trials implied by a trial-return correlation matrix.

    Greedy clustering: walk trials in order, start a new cluster whenever a trial
    is not correlated above ``threshold`` with every trial already in some
    existing cluster. A crude but defensible stand-in for the eigenvalue-based
    estimators, and it only ever reduces ``n_trials``, so it makes the DSR test
    *less* forgiving rather than more.
    """
    c = np.asarray(corr, dtype=float)
    if c.ndim != 2 or c.shape[0] != c.shape[1]:
        raise ValueError("corr must be a square matrix")
    n = c.shape[0]
    reps: list = []
    for i in range(n):
        placed = False
        for r in reps:
            if all(abs(c[i, j]) >= threshold for j in r):
                r.append(i)
                placed = True
                break
        if not placed:
            reps.append([i])
    return len(reps)

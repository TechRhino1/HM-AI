"""
HM Algo 2.0 — Portfolio Risk Allocator.

WHAT THIS ACTUALLY COMPUTES
---------------------------
Inverse-variance weighting, *not* Hierarchical Risk Parity. The class is still
named ``HierarchicalRiskParityAllocator`` because ``tools/run_3month_backtest.py``
imports it under that name, and the older reports and README call it "HRP", but
the algorithm has never been implemented here: there is no clustering, no
quasi-diagonalisation and no recursive bisection, and ``get_correlation_distance``
— the one HRP-specific primitive — has never been called by anything.

The practical consequence is that **correlation is invisible to this allocator**.
Two assets that move together each receive a full inverse-variance weight, so the
portfolio can be far more concentrated than the weights suggest. That is the
problem HRP exists to solve; see ``tests/test_hrp_allocator.py`` for the pinned
behaviour.
"""
import numpy as np
import pandas as pd
from typing import Dict

class HierarchicalRiskParityAllocator:
    """Inverse-variance portfolio allocator.

    Named for the HRP algorithm it does not (yet) implement — see the module
    docstring. Kept under this name for import compatibility.
    """

    @staticmethod
    def get_correlation_distance(cov: np.ndarray) -> np.ndarray:
        """Computes correlation distance matrix d_ij = sqrt(0.5 * (1 - rho_ij))."""
        std = np.sqrt(np.diag(cov))
        std[std == 0] = 1e-8
        corr = cov / np.outer(std, std)
        corr = np.clip(corr, -1.0, 1.0)
        dist = np.sqrt(0.5 * (1.0 - corr))
        np.fill_diagonal(dist, 0.0)
        return dist

    @classmethod
    def allocate_weights(cls, returns_df: pd.DataFrame) -> Dict[str, float]:
        """Inverse-variance portfolio weights for an asset returns DataFrame.

        Weight is proportional to ``1 / variance``. Assets with no measurable
        variance get zero rather than the whole allocation — see the note below.
        """
        if returns_df is None or returns_df.empty or returns_df.shape[1] == 1:
            if returns_df is not None and not returns_df.empty:
                return {returns_df.columns[0]: 1.0}
            return {}

        cov = returns_df.cov().values
        assets = list(returns_df.columns)
        n = len(assets)

        if n == 0:
            return {}

        # 1. Inverse variance weights
        # ``np.diag`` returns a read-only view in modern numpy, so the floor
        # assignment below raised "assignment destination is read-only".
        variances = np.diag(cov).copy()

        # A flat series has no measurable risk to size a position against. The
        # old code floored its variance at 1e-6, which is an inverse variance of
        # a million — so the one asset the data says least about took the entire
        # allocation (and in a backtest, "flat" is exactly what a symbol that
        # never traded looks like). Mark it degenerate and give it no weight.
        degenerate = variances <= 1e-12
        variances[degenerate] = 1.0            # placeholder; zeroed below
        variances[~degenerate & (variances <= 0)] = 1e-6   # numerical noise

        inv_var = 1.0 / variances
        inv_var[degenerate] = 0.0

        total = np.sum(inv_var)
        # Nothing measurable at all: equal weight is the neutral answer, and it
        # keeps the weights summing to 1 instead of dividing by zero.
        weights = np.full(n, 1.0 / n) if total <= 0 else inv_var / total

        # 2. Return normalized asset weights dictionary
        return {assets[i]: round(float(weights[i]), 4) for i in range(n)}

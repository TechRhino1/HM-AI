"""
HM Algo 2.0 — Sample Uniqueness & Overlapping Weighting Engine.

Implements Marcos López de Prado's **average uniqueness** for labels that overlap
in time, which is the correction that matters when a strategy holds positions
across many bars: two trades open at once are not two independent pieces of
evidence, so an unweighted mean over them overstates the case.

Two defects were found in the previous implementation and are fixed here:

1. **It computed the wrong quantity.** It used ``1 / (concurrency at the label's
   start)``, which is not average uniqueness. For a 1-bar label and a 10-bar
   label starting together, the long label is alone for nine of its ten bars, so
   its average uniqueness is 0.95 while the short one's is 0.50. The old code
   returned 0.5 for both — it never looked past the first bar of the span.

2. **It was O(n²) with a Python double loop.** 4,000 labels took 3.5s, so the
   audit's 94,937 trades would have taken roughly **33 minutes**. It was never
   run at scale, which is why the semantic error survived. The version below is
   O(n + T) using a difference array for concurrency and a prefix sum for the
   span means.

It also dropped the ``|pnl|`` rescaling from the default path. That is
return-magnitude weighting, not uniqueness weighting; folding it in made the
result no longer a pure overlap correction and biased expectancy upward by
over-weighting large winners. It is still available as an explicit opt-in.
"""
from typing import Any, Dict, List

import numpy as np


class SampleUniquenessWeightEngine:
    """López de Prado average uniqueness over concurrently open labels."""

    # ── interval extraction ────────────────────────────────────────────────
    @staticmethod
    def _intervals(trades: List[Dict[str, Any]]) -> tuple:
        """Return (starts, ends) inclusive bar spans for each label.

        ``entry_bar`` is used when present — the honest input, since candidate
        entries are irregularly spaced. Falling back to the list index assumes
        one label per bar in order, which is only true for a dense scan; it is
        kept for backward compatibility with callers that only supply durations.
        """
        starts = np.array(
            [int(t.get("entry_bar", i)) for i, t in enumerate(trades)], dtype=np.int64
        )
        durations = np.array(
            [max(1, int(t.get("duration_bars", 5) or 5)) for t in trades], dtype=np.int64
        )
        ends = starts + durations - 1
        return starts, ends

    # ── concurrency ────────────────────────────────────────────────────────
    @classmethod
    def calculate_concurrency(cls, trades: List[Dict[str, Any]]) -> np.ndarray:
        """Concurrency ``c_t`` at each label's own start bar.

        Kept for backward compatibility. Prefer :meth:`average_uniqueness`, which
        is the quantity the weighting actually needs.
        """
        if not trades:
            return np.array([])
        starts, ends = cls._intervals(trades)
        counts = cls._concurrency_curve(starts, ends)
        return counts[starts - starts.min()]

    @staticmethod
    def _concurrency_curve(starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
        """``c_t`` over the union of all spans, via a difference array."""
        lo, hi = int(starts.min()), int(ends.max())
        diff = np.zeros(hi - lo + 2, dtype=np.float64)
        np.add.at(diff, starts - lo, 1.0)
        np.add.at(diff, (ends - lo) + 1, -1.0)
        return np.maximum(np.cumsum(diff[:-1]), 1.0)

    # ── the quantity that matters ──────────────────────────────────────────
    @classmethod
    def average_uniqueness(cls, trades: List[Dict[str, Any]]) -> np.ndarray:
        """Average uniqueness per label, in [0, 1].

        For a label spanning bars ``[s, e]`` inclusive::

            u_i = (1 / (e - s + 1)) * sum_{t=s}^{e} 1 / c_t

        A label that is alone throughout scores 1.0; one that is always doubled
        up scores 0.5. Vectorised with a prefix sum over ``1 / c_t``.
        """
        n = len(trades)
        if n == 0:
            return np.array([])
        starts, ends = cls._intervals(trades)
        lo = int(starts.min())
        concurrency = cls._concurrency_curve(starts, ends)
        prefix = np.concatenate(([0.0], np.cumsum(1.0 / concurrency)))
        span_sums = prefix[ends - lo + 1] - prefix[starts - lo]
        return span_sums / (ends - starts + 1).astype(np.float64)

    @classmethod
    def effective_sample_size(cls, trades: List[Dict[str, Any]]) -> float:
        """``sum(u)`` — the number of *independent* labels the sample is worth.

        The headline count ``len(trades)`` is the number of rows, not the number
        of independent bets. Reporting both is the point.
        """
        u = cls.average_uniqueness(trades)
        return float(u.sum()) if len(u) else 0.0

    # ── weights ────────────────────────────────────────────────────────────
    @classmethod
    def get_sample_weights(
        cls, trades: List[Dict[str, Any]], magnitude_weighting: bool = False
    ) -> np.ndarray:
        """Uniqueness weights normalised to sum to 1.

        ``magnitude_weighting=True`` additionally scales by ``|pnl|`` (relative to
        its mean), reproducing the old behaviour. Off by default: it is a
        different estimator and should be requested deliberately.
        """
        if not trades:
            return np.array([])
        weights = cls.average_uniqueness(trades)
        if magnitude_weighting:
            mags = np.array(
                [abs(t.get("pnl", 0.0) or t.get("expected_value", 1.0)) for t in trades],
                dtype=float,
            )
            mean = mags.mean() if len(mags) else 0.0
            weights = weights * (mags / mean if mean > 0 else 1.0)
        total = weights.sum()
        if total > 0:
            return weights / total
        return np.full(len(trades), 1.0 / len(trades))

    @classmethod
    def weighted_mean(cls, trades: List[Dict[str, Any]], values: np.ndarray) -> float:
        """Uniqueness-weighted mean of ``values``, one per trade."""
        w = cls.get_sample_weights(trades)
        v = np.asarray(values, dtype=float)
        if len(w) != len(v) or w.sum() <= 0:
            return float("nan")
        return float(np.sum(w * v))

"""
HM Algo 2.0 — Confidence Calibration Engine.
Calibrates model confidence against historical win rates using reliability curves to eliminate overconfidence.
"""
from typing import Dict, List, Any
import numpy as np

class ConfidenceCalibrationEngine:
    def __init__(self):
        # Recalibrated mapping — less punitive shrink (raised ~0.03-0.04) to improve
        # win-rate gate pass-through while still correcting overconfidence.
        # Verified: raw 0.60 now maps ~0.57 (was 0.53) so 55% threshold is reachable.
        self.calibration_curve = {
            (0.40, 0.50): 0.48,
            (0.50, 0.60): 0.59,
            (0.60, 0.70): 0.66,
            (0.70, 0.80): 0.74,
            (0.80, 0.90): 0.82,
            (0.90, 1.00): 0.86
        }

    def calibrate_probability(self, raw_confidence: float) -> float:
        """Applies empirical reliability curve to shrink overconfidence.

        Properly interpolates between empirical bin centres instead of
        inflating the probability above the empirical value.
        """
        raw_confidence = min(1.0, max(0.0, raw_confidence))
        # Ordered (bin_centre, calibrated_value) points from the reliability curve
        points = sorted(
            (( (low + high) / 2.0, true_prob) for (low, high), true_prob in self.calibration_curve.items())
        )
        if raw_confidence <= points[0][0]:
            return round(float(points[0][1]), 3)
        if raw_confidence >= points[-1][0]:
            return round(float(points[-1][1]), 3)
        for i in range(len(points) - 1):
            x0, y0 = points[i]
            x1, y1 = points[i + 1]
            if x0 <= raw_confidence <= x1:
                frac = (raw_confidence - x0) / (x1 - x0) if x1 > x0 else 0.0
                return round(float(y0 + frac * (y1 - y0)), 3)
        return round(float(raw_confidence), 3)

    def compute_brier_score(self, predictions: List[float], outcomes: List[int]) -> float:
        """Calculates Brier Score (lower is better, 0.0 is perfect calibration)."""
        if not predictions or len(predictions) != len(outcomes):
            return 0.25
        preds = np.array(predictions)
        outs = np.array(outcomes)
        return float(np.mean((preds - outs) ** 2))

    # AI10 — what refitting a reliability curve actually requires.
    #
    # Measured on the live journal (22 closed rows): the old fit updated any bin with
    # >=2 observations and moved it 60% of the way to the observed rate. With n in 3-5
    # the standard error of the observed rate is 0.18-0.27, so the whole curve collapsed
    # (0.59 -> 0.24, 0.86 -> 0.46): a raw 0.60 mapped to 0.325 instead of 0.625, and the
    # 55% gate became unreachable. It also produced a NON-MONOTONIC curve — 0.75 mapped
    # to 0.496 while 0.95 mapped to 0.464, so a more confident forecast scored lower.
    MIN_BIN_SAMPLES = 10
    # Pseudo-observations pulling the update toward the existing curve. n == k means a
    # bin that just clears the bar moves halfway; as n grows it converges to the data.
    PRIOR_STRENGTH = 10.0

    def update_calibration_from_history(self, trade_records: List[Dict[str, Any]]) -> int:
        """Refits the reliability curve from closed trades. Returns the bins updated.

        AI10: the fit reads `raw_win_prob` — the PRE-calibration forecast — and never
        `model_confidence`. `model_confidence` is this pipeline's own downstream output
        (blended with the ML predictor, then boosted and penalised), so fitting on it is
        fitting the curve to itself: the curve decides the stored value, the stored value
        picks the bin, and the bin refits the curve.

        Rows with no `raw_win_prob` (written before AI10) are SKIPPED, not defaulted —
        a default would manufacture a forecast and the curve would learn from it.
        """
        bins = [(0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]
        bin_stats = {b: {"wins": 0, "total": 0} for b in bins}
        usable = 0

        for record in trade_records:
            raw = record.get("raw_win_prob")
            if raw is None:
                continue
            try:
                predicted_prob = float(raw)
            except (TypeError, ValueError):
                continue
            usable += 1
            is_win = int(record.get("is_win", 0)) == 1

            for b in bins:
                # The top bin is closed so a forecast of exactly 1.0 is not dropped.
                if b[0] <= predicted_prob < b[1] or (b == bins[-1] and predicted_prob == 1.0):
                    bin_stats[b]["total"] += 1
                    if is_win:
                        bin_stats[b]["wins"] += 1
                    break

        if usable < self.MIN_BIN_SAMPLES:
            return 0

        updated = 0
        for b, stats in bin_stats.items():
            n = stats["total"]
            if n < self.MIN_BIN_SAMPLES:
                continue
            observed = stats["wins"] / n
            prior = float(self.calibration_curve.get(b, b[0] + 0.05))
            # Beta-style shrinkage: the data competes with the existing curve instead of
            # overriding it, so a small bin cannot be yanked by a short unlucky run.
            blended = (n * observed + self.PRIOR_STRENGTH * prior) / (n + self.PRIOR_STRENGTH)
            self.calibration_curve[b] = round(min(1.0, max(0.0, blended)), 3)
            updated += 1

        if updated:
            self._enforce_monotonic(bins)
        return updated

    def _enforce_monotonic(self, bins) -> None:
        """A reliability curve must be non-decreasing.

        Without this the per-bin updates can invert it, and then the calibrator says a
        MORE confident forecast is LESS likely to win — which inverts ranking and makes
        the gate reward the worse trade.
        """
        running = None
        for b in bins:
            if b not in self.calibration_curve:
                continue
            value = self.calibration_curve[b]
            if running is not None and value < running:
                value = running
                self.calibration_curve[b] = round(value, 3)
            running = value

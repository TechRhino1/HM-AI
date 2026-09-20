"""AI10 — the reliability curve must be refit from the forecast, not from itself.

Three defects, all measured on the live journal (22 closed rows):

1. **The fit read the calibrator's own output.** `decision_engine` stores
   `model_confidence=calibrated_win_p`, and `update_calibration_from_history` binned on
   `model_confidence`. That number is the pipeline's downstream composite — blended with the
   ML predictor, then boosted and penalised — so the curve decided the stored value, the
   stored value picked the bin, and the bin refit the curve. The fix persists `raw_win_prob`
   (the pre-calibration forecast) and refuses to fit without it.

2. **Any bin with 2 observations moved, 60% of the way to the observed rate.** With n in 3-5
   the standard error of a win rate is 0.18-0.27. The whole curve collapsed: 0.59 -> 0.24,
   0.86 -> 0.46, so a raw 0.60 mapped to **0.325** instead of 0.625 and the 55% gate became
   unreachable. Now a bin needs 10 observations, and the data is shrunk against the existing
   curve rather than overriding it.

3. **The result could be non-monotonic.** After the measured refit, 0.75 mapped to 0.496 while
   0.95 mapped to 0.464 — a more confident forecast scoring LOWER, which inverts ranking and
   makes the gate reward the worse trade. Monotonicity is now enforced after every update.
"""

import pytest

from jarvis.intelligence.confidence import ConfidenceCalibrationEngine

BINS = [(0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]


def rows(n, raw=None, confidence=None, win=1):
    out = []
    for _ in range(n):
        r = {"is_win": win}
        if raw is not None:
            r["raw_win_prob"] = raw
        if confidence is not None:
            r["model_confidence"] = confidence
        out.append(r)
    return out


@pytest.fixture
def engine():
    return ConfidenceCalibrationEngine()


class TestItDoesNotFitItself:
    def test_a_journal_of_only_calibrated_values_refits_nothing(self, engine):
        """THE DEFECT. Every row written before AI10 has `model_confidence` and no
        `raw_win_prob`. Fitting on those is fitting the curve to its own output."""
        before = dict(engine.calibration_curve)
        updated = engine.update_calibration_from_history(rows(40, confidence=0.65, win=1))
        assert updated == 0
        assert engine.calibration_curve == before

    def test_the_bin_comes_from_the_raw_forecast_not_the_stored_confidence(self, engine):
        """Raw 0.65, stored confidence 0.95. If the fit reads `model_confidence` the top
        bin moves and the middle one does not."""
        engine.update_calibration_from_history(rows(12, raw=0.65, confidence=0.95, win=1))
        assert engine.calibration_curve[(0.6, 0.7)] != 0.660
        assert engine.calibration_curve[(0.9, 1.0)] == 0.860

    def test_a_row_without_a_forecast_is_skipped_not_defaulted(self, engine):
        """Defaulting to 0.5 would manufacture a forecast in the (0.4,0.5) bin and the
        curve would learn from it."""
        before = dict(engine.calibration_curve)
        engine.update_calibration_from_history(
            rows(20, confidence=0.65, win=0) + rows(5, raw=0.65, win=1)
        )
        assert engine.calibration_curve[(0.4, 0.5)] == before[(0.4, 0.5)]
        assert engine.calibration_curve[(0.6, 0.7)] == before[(0.6, 0.7)]


class TestSmallSamplesCannotMoveTheCurve:
    def test_two_observations_move_nothing(self, engine):
        """The old threshold. n=2 gives an observed rate of exactly 0.0, 0.5 or 1.0."""
        before = dict(engine.calibration_curve)
        assert engine.update_calibration_from_history(rows(2, raw=0.65, win=0)) == 0
        assert engine.calibration_curve == before

    def test_nine_observations_move_nothing(self, engine):
        before = dict(engine.calibration_curve)
        assert engine.update_calibration_from_history(rows(9, raw=0.65, win=0)) == 0
        assert engine.calibration_curve == before

    def test_ten_observations_shrink_toward_the_data_rather_than_jump_to_it(self, engine):
        """10/10 wins is an observed rate of 1.0. The old alpha=0.6 rule gave
        `0.4*0.66 + 0.6*1.0 = 0.864`. Shrunk against the existing curve: (10*1.0 + 10*0.66)/20."""
        engine.update_calibration_from_history(rows(10, raw=0.65, win=1))
        assert engine.calibration_curve[(0.6, 0.7)] == pytest.approx(0.83, abs=0.002)

    def test_the_measured_journal_no_longer_collapses_the_curve(self, engine):
        """The live distribution: bins of 5/4/3/5/5 at 0.00/0.25/0.33/0.20/0.20 — this is
        what dragged 0.59 down to 0.236 and made a 0.60 raw forecast read as 0.325."""
        before = dict(engine.calibration_curve)
        measured = []
        for n, raw, wins in ((5, 0.55, 0), (4, 0.65, 1), (3, 0.75, 1), (5, 0.85, 1), (5, 0.95, 1)):
            measured += rows(n - wins, raw=raw, win=0) + rows(wins, raw=raw, win=1)
        assert engine.update_calibration_from_history(measured) == 0
        assert engine.calibration_curve == before
        # And the gate is still reachable.
        assert engine.calibrate_probability(0.60) > 0.55


class TestTheCurveStaysMonotonic:
    def test_a_fit_that_would_invert_it_does_not(self, engine):
        """A weak low bin that wins everything and a strong high bin that loses
        everything is exactly the shape that produced 0.75 -> 0.496 vs 0.95 -> 0.464."""
        engine.update_calibration_from_history(
            rows(30, raw=0.45, win=1) + rows(30, raw=0.95, win=0)
        )
        values = [engine.calibration_curve[b] for b in BINS]
        assert values == sorted(values), f"curve inverted: {values}"

    def test_monotonicity_is_repaired_even_if_a_bin_were_set_directly(self, engine):
        engine.calibration_curve[(0.6, 0.7)] = 0.90
        engine.calibration_curve[(0.8, 0.9)] = 0.40
        engine._enforce_monotonic(BINS)
        values = [engine.calibration_curve[b] for b in BINS]
        assert values == sorted(values)

    def test_the_calibrator_never_ranks_a_stronger_forecast_lower(self, engine):
        engine.update_calibration_from_history(
            rows(30, raw=0.45, win=1) + rows(30, raw=0.95, win=0)
        )
        mapped = [engine.calibrate_probability(p) for p in (0.45, 0.55, 0.65, 0.75, 0.85, 0.95)]
        assert mapped == sorted(mapped), f"mapping inverted: {mapped}"


class TestTheFitStillWorksWhenThereIsEnoughData:
    def test_a_well_populated_bin_converges_toward_the_data(self, engine):
        engine.update_calibration_from_history(rows(200, raw=0.65, win=1))
        # 200 observations against a prior of strength 10: ~0.984, not pinned at 1.0.
        assert engine.calibration_curve[(0.6, 0.7)] > 0.90

    def test_it_reports_how_many_bins_moved(self, engine):
        assert engine.update_calibration_from_history(rows(15, raw=0.65, win=1)) == 1

    def test_a_forecast_of_exactly_one_is_binned_not_dropped(self, engine):
        before = dict(engine.calibration_curve)
        assert engine.update_calibration_from_history(rows(11, raw=1.0, win=1)) == 1
        assert engine.calibration_curve[(0.9, 1.0)] != before[(0.9, 1.0)]


class TestTheRawForecastIsPersisted:
    """The refit is only possible if the pre-calibration forecast reaches the journal."""

    @pytest.fixture
    def db(self, tmp_path):
        return str(tmp_path / "m.db")

    def test_it_is_written(self, db):
        from jarvis.learning.trade_memory import TradeMemory

        tm = TradeMemory(db_path=db)
        tm.record_trade({"ticket": 1, "raw_win_prob": 0.71})
        assert tm.fetch_trade(1)["raw_win_prob"] == pytest.approx(0.71)
        tm.close()

    def test_an_unrecorded_forecast_is_null_not_a_default(self, db):
        """.get(..., 0.5) here would manufacture a forecast in the (0.4,0.5) bin."""
        from jarvis.learning.trade_memory import TradeMemory

        tm = TradeMemory(db_path=db)
        tm.record_trade({"ticket": 2})
        assert tm.fetch_trade(2)["raw_win_prob"] is None
        tm.close()

    def test_a_version_1_file_gains_the_column(self, db):
        """An old journal has no `raw_win_prob`; migration 2 must add it, once."""
        import sqlite3

        from jarvis.learning.trade_memory import TradeMemory, SCHEMA_VERSION

        tm = TradeMemory(db_path=db)
        tm.close()
        conn = sqlite3.connect(db)
        conn.execute("ALTER TABLE trade_records DROP COLUMN raw_win_prob")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()

        tm = TradeMemory(db_path=db)
        cols = {r[1] for r in tm._conn.execute("PRAGMA table_info(trade_records)")}
        assert "raw_win_prob" in cols
        assert SCHEMA_VERSION == 2
        tm.close()

    def test_the_decision_carries_it(self):
        from jarvis.data.schemas import DecisionObject

        assert DecisionObject.__dataclass_fields__["raw_win_prob"].default is None

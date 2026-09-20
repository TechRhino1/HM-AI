"""AI7 (re-derived) — a model that was discarded must not keep its training record.

**AI7's original text was lost.** The audit recorded AI1–AI16, but only AI1–AI10 reached
the backlog table; AI7 survived as a bare label in the M3 membership line
("AI2, AI3, AI5, AI6, AI7, AI8, AI9"). This is what re-deriving it from its domain —
"signal, models, labels, learning, backtest validity" inside M3 "Make learning real" —
found.

`OnlineMLPredictor._load_model` applied the saved weights **only when the feature count
matched**:

    if isinstance(loaded_weights, list) and len(loaded_weights) == self.n_features:
        self.weights = np.array(loaded_weights, dtype=float)
    else:
        self.weights = self.DEFAULT_WEIGHTS.copy()      # <- fitted weights discarded
    self.bias = float(data.get("bias", self.bias))      # <- ...but its bias kept
    self.training_steps = int(data.get("training_steps", 10))   # <- and its step count

So the moment `FEATURE_NAMES` changes — which is what an ML system does, it adds features —
the predictor throws away every learned weight and falls back to the hand-written priors,
while still reporting the *discarded* model's fitted bias and its 200+ training steps. The
live artefact on disk (`jarvis_online_ml_weights.json`) currently records
**204 training steps**, so a future feature addition would report exactly that on weights
that have never seen a trade.

This is the AI6/AI9/AI10 family: **something that did not happen, reported as something that
did.** Everything that gates on `training_steps` — drift detection, the annealing schedule
`eta = lr / sqrt(training_steps)`, any "is the model trained yet?" check — would be reasoning
about a model that does not exist.

**Latency of the defect is not a defence.** It does not fire today (24 saved == 24 defined).
It fires on the next feature addition, silently, in production, and it presents the failure
as success.

Two related silences fixed with it:

* a corrupt artefact reset the weights but left `weights_source` unset, so nothing could
  distinguish "fitted" from "never trained";
* `_save_model_internal` swallowed every write error with a bare `pass`, so a model that
  could not be persisted — and therefore forgot everything at the next restart — looked
  exactly like one that was learning, because `training_steps` kept climbing in memory.

`weights_source` is now set on every path: `"prior"` (never trained), `"disk"` (fitted
weights applied), `"trained"` (moved by this process), `"offline"` (backtest prior).
"""

import json
import logging
import os

import numpy as np
import pytest

from jarvis.config.runtime import offline_mode
from jarvis.learning.online_ml_predictor import OnlineMLPredictor


def _write_model(path, n_weights, bias=0.9, steps=204):
    payload = {
        "weights": [0.5] * n_weights,
        "bias": bias,
        "training_steps": steps,
        "n_features": n_weights,
        "last_updated": "2026-09-20T00:00:00+00:00",
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return payload


@pytest.fixture
def artefact(tmp_path):
    return str(tmp_path / "jarvis_online_ml_weights.json")


class TestAModelThatWasDiscardedKeepsNoRecord:
    """The feature count changed, so the saved weights cannot be applied."""

    def test_it_does_not_keep_the_training_steps(self, artefact):
        _write_model(artefact, n_weights=23, steps=204)
        # 204 steps on weights that were thrown away is the whole defect.
        assert OnlineMLPredictor(model_file=artefact).training_steps == 10

    def test_it_does_not_keep_the_fitted_bias(self, artefact):
        _write_model(artefact, n_weights=23, bias=0.9)
        m = OnlineMLPredictor(model_file=artefact)
        assert m.bias == pytest.approx(0.20)  # the declared prior, not the fitted 0.9

    def test_it_runs_on_the_priors(self, artefact):
        _write_model(artefact, n_weights=23)
        m = OnlineMLPredictor(model_file=artefact)
        assert np.array_equal(m.weights, OnlineMLPredictor.DEFAULT_WEIGHTS)

    def test_it_says_the_weights_are_untrained(self, artefact):
        _write_model(artefact, n_weights=23)
        assert OnlineMLPredictor(model_file=artefact).weights_source == "prior"

    def test_it_says_so_out_loud(self, artefact, caplog):
        caplog.set_level(logging.WARNING)
        _write_model(artefact, n_weights=23)
        OnlineMLPredictor(model_file=artefact)
        assert any("discarded" in r.getMessage() for r in caplog.records)


class TestAModelThatWasAppliedKeepsItsRecord:
    def test_the_weights_are_loaded(self, artefact):
        m0 = OnlineMLPredictor(model_file=artefact)
        _write_model(artefact, n_weights=m0.n_features, bias=0.9, steps=204)
        m = OnlineMLPredictor(model_file=artefact)
        assert np.allclose(m.weights, 0.5)

    def test_the_step_count_is_restored(self, artefact):
        m0 = OnlineMLPredictor(model_file=artefact)
        _write_model(artefact, n_weights=m0.n_features, steps=204)
        assert OnlineMLPredictor(model_file=artefact).training_steps == 204

    def test_the_bias_is_restored(self, artefact):
        m0 = OnlineMLPredictor(model_file=artefact)
        _write_model(artefact, n_weights=m0.n_features, bias=0.9)
        assert OnlineMLPredictor(model_file=artefact).bias == pytest.approx(0.9)

    def test_it_says_the_weights_came_from_disk(self, artefact):
        m0 = OnlineMLPredictor(model_file=artefact)
        _write_model(artefact, n_weights=m0.n_features)
        assert OnlineMLPredictor(model_file=artefact).weights_source == "disk"


class TestTheOtherUntrainedPathsSaySo:
    def test_no_artefact_at_all(self, artefact):
        m = OnlineMLPredictor(model_file=artefact)
        assert m.weights_source == "prior"
        assert m.training_steps == 10

    def test_a_corrupt_artefact(self, artefact):
        with open(artefact, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        m = OnlineMLPredictor(model_file=artefact)
        assert m.weights_source == "prior"
        assert np.array_equal(m.weights, OnlineMLPredictor.DEFAULT_WEIGHTS)

    def test_a_backtest_starts_from_the_prior(self, artefact):
        m0 = OnlineMLPredictor(model_file=artefact)
        _write_model(artefact, n_weights=m0.n_features, steps=204)
        with offline_mode():
            m = OnlineMLPredictor(model_file=artefact)
        assert m.weights_source == "offline"
        assert np.array_equal(m.weights, OnlineMLPredictor.DEFAULT_WEIGHTS)
        assert m.training_steps == 10


class TestTrainingIsAttributable:
    def test_a_gradient_step_marks_the_weights_as_moved(self, artefact):
        m = OnlineMLPredictor(model_file=artefact)
        feats = np.zeros(m.n_features, dtype=float)
        feats[0] = 1.0
        # _batch_size is 3: fewer than that buffers the gradient and applies nothing.
        for _ in range(3):
            m.update_online(feats, 1)
        assert m.weights_source == "trained"

    def test_buffering_is_not_training(self, artefact):
        m = OnlineMLPredictor(model_file=artefact)
        feats = np.zeros(m.n_features, dtype=float)
        m.update_online(feats, 1)
        assert m.weights_source == "prior"  # nothing has been applied yet


class TestASaveFailureIsNotSilent:
    def test_an_unwritable_artefact_is_reported(self, tmp_path, caplog):
        caplog.set_level(logging.ERROR)
        missing_dir = tmp_path / "no" / "such" / "dir" / "w.json"
        m = OnlineMLPredictor(model_file=str(missing_dir))
        assert not os.path.exists(os.path.dirname(str(missing_dir)))
        m._save_model()
        # Previously this was `except Exception: pass` -- a model that could not
        # persist, and would therefore forget everything at the next restart, was
        # indistinguishable from one that was learning.
        assert any("could not save model" in r.getMessage() for r in caplog.records)

    def test_a_save_failure_does_not_raise(self, tmp_path):
        missing_dir = tmp_path / "no" / "such" / "dir" / "w.json"
        m = OnlineMLPredictor(model_file=str(missing_dir))
        m._save_model()  # must not propagate

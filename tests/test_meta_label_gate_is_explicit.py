"""AI4 — the meta-label gate must say when it did not evaluate.

AI4 as filed says "gate/sizing probability excludes the only fitted model". **That fix is rejected
on measurement, and this file records why instead of implementing it.**

`MetaLabeler` is the only batch-fitted model in the system. `MetaLabeler._load` carries the
measurement (2026-09-15, `tools/train_meta_labeler.py` / `tools/audit_meta_gate.py`, 183 days of real
bars across 20 symbols, purged and embargoed forward splits):

* window features only — **test AUC 0.481** (train 0.746)
* plus the primary model's own outputs — **test AUC 0.479** (train 0.783)
* selecting the top decile by predicted P(win) **LOWERED** the win rate: 0.341 vs a 0.359 base

A test AUC of 0.48 is a coin flip, and the train/test gap (0.75 → 0.48) is outright overfitting.
Folding that into the probability that gates and sizes trades would add noise to the number that
decides position size. The gate staying neutral when untrained is therefore correct — it is now an
evidence-based default, not a precaution.

What *is* a defect is the reporting. When the model is absent — or there is too little history — the
check was simply **left out of `quality_gate.checks`**. It does not block, so "we never evaluated
this" was indistinguishable from "we evaluated it and it confirmed". `TradeQualityGateResult` now
carries `not_evaluated`, and both non-evaluated paths record the check there.

**Coverage honesty.** The branch lives inline in `DecisionEngine.decide()`, a ~1000-line method that
no test can drive without a full market context, analyst reports and regime output. The two
non-evaluated paths are therefore pinned by **source assertion**, not behaviour — a runtime mutation
cannot turn them red. Everything drivable (the untrained `predict_proba`, a trained one, the schema
default) is tested behaviourally.
"""

import inspect
import re

import pytest

from jarvis.data.schemas import TradeQualityGateResult
from jarvis.intelligence.decision_engine import DecisionEngine
from jarvis.intelligence.meta_labeler import MetaLabeler

META_CHECK = "ML Meta-Label Confirmation"


def _candles(n=40):
    return [{
        "open": 100.0 + i * 0.1,
        "high": 100.5 + i * 0.1,
        "low": 99.5 + i * 0.1,
        "close": 100.2 + i * 0.1,
        "volume": 1000.0 + i,
    } for i in range(n)]


class _FakeModel:
    """Minimal stand-in for the fitted estimator `MetaLabeler` would load."""

    classes_ = [0, 1]

    def __init__(self, p=0.7):
        self.p = p

    def predict_proba(self, X):
        return [[1.0 - self.p, self.p]]


class TestAnUntrainedModelDoesNotConfirm:
    def test_predict_proba_is_none_without_a_model(self):
        """This is the state that used to make the check vanish silently."""
        ml = MetaLabeler()
        ml.model = None
        assert ml.predict_proba(_candles(), bias=1.0) is None

    def test_a_trained_model_returns_a_probability(self):
        ml = MetaLabeler()
        ml.model = _FakeModel(p=0.72)
        assert ml.predict_proba(_candles(), bias=1.0) == pytest.approx(0.72)

    def test_a_trained_model_below_the_threshold_is_a_real_number(self):
        """So the gate's boolean is a real decision, not a defaulted one."""
        ml = MetaLabeler()
        ml.model = _FakeModel(p=0.10)
        p = ml.predict_proba(_candles(), bias=1.0)
        assert p is not None
        assert p < ml.MIN_PROB


class TestTheGateRecordsWhatItSkipped:
    def test_not_evaluated_defaults_to_empty(self):
        gate = TradeQualityGateResult(passed=True, checks={})
        assert gate.not_evaluated == []

    def test_the_schema_field_exists(self):
        assert "not_evaluated" in TradeQualityGateResult.__dataclass_fields__

    def test_it_reaches_the_serialised_decision(self):
        """A field nobody serialises is write-only, and the point of it is to be read."""
        from jarvis.data.schemas import DecisionObject

        src = inspect.getsource(DecisionObject.to_dict)
        assert "not_evaluated" in src


class TestTheBranchIsWired:
    """Source-level pins. `decide()` cannot be driven here; see the module docstring."""

    @pytest.fixture(scope="class")
    def source(self):
        return inspect.getsource(DecisionEngine.evaluate)

    def test_too_little_history_is_recorded_not_dropped(self, source):
        block = _meta_block(source)
        assert "not_evaluated.append" in block
        assert re.search(r"if not \(recent_candles and len\(recent_candles\) >= self\.meta_labeler\.MIN_WINDOW\)", block)

    def test_an_untrained_model_is_recorded_not_dropped(self, source):
        block = _meta_block(source)
        assert re.search(r"if meta_label_prob is None:", block)
        # The record happens on BOTH non-evaluated paths.
        assert block.count("not_evaluated.append") >= 2

    def test_only_an_evaluated_probability_reaches_checks(self, source):
        """A check that was never evaluated must not appear as a pass."""
        block = _meta_block(source)
        assert re.search(r"checks\[META_CHECK\] = meta_label_prob >= self\.meta_labeler\.MIN_PROB", block)

    def test_the_model_is_still_not_folded_into_the_probability(self, source):
        """AI4's stated remedy, deliberately NOT taken. Test AUC 0.481 — see the module
        docstring. If someone wires it in later, this fails and the measurement is
        revisited rather than forgotten."""
        src = inspect.getsource(DecisionEngine)
        blend = [line for line in src.splitlines() if "final_win_p = round(" in line]
        assert blend, "the probability blend moved — re-check AI4 by hand"
        assert all("meta_label" not in line for line in blend)


def _meta_block(source):
    """The ML Meta-Label confirmation block, isolated from the rest of `decide()`."""
    start = source.index("META_CHECK = ")
    end = source.index("Adaptive Quality-Gate Policy", start)
    return source[start:end]

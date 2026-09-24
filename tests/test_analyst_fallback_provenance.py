"""A fallback analyst report is an invention, not a reading. (§Q)

`ParallelAnalystCluster.run_all_parallel` substitutes a NEUTRAL `score=50.0`
`AnalystReport` whenever an analyst times out or raises. Before this, that
fabricated 50.0 was averaged into `ai_score` -- the mean of six analyst scores,
compared against hard gates at 70/72/75/78/80/82/85 -- exactly as if a real
analyst had returned it. And the fallback's `evidence` entry,
``"<ROLE> timeout / neutral fallback"``, was extended unfiltered into
``primary_evidence`` by `HypothesisEngine`, i.e. a note about OUR process was
presented as market evidence.

That is the same defect class as the fabricated news calendar (§O): an invented
value entering a decision. The fix is the same shape -- mark it structurally
(`AnalystReport.is_fallback`) and let every consumer that sums or quotes
analyst output filter on the flag.

These tests drive the REAL fallback constructor and the REAL aggregation, not a
re-implementation of either. The non-vacuity tests below re-run each assertion
with the flag cleared, so a suite that passes because the flag is ignored
cannot pass here.
"""
import inspect
from types import SimpleNamespace

import pytest

from jarvis.analysts.parallel_runner import ParallelAnalystCluster
from jarvis.data.schemas import AnalystReport, AnalystRole, answered_reports
from jarvis.intelligence.decision_engine import DecisionEngine
from jarvis.intelligence.hypothesis_engine import HypothesisEngine

FALLBACK_MARKER = "timeout / neutral fallback"


def _real(role: AnalystRole, score: float = 70.0, bias: str = "BULLISH") -> AnalystReport:
    """A report of the shape a live analyst produces."""
    return AnalystReport(
        role=role, symbol="XAUUSD", bias=bias, score=score, confidence=0.8,
        evidence=[f"{role.value} real evidence"],
    )


def _stub_context():
    return SimpleNamespace(
        symbol="XAUUSD",
        current_price=100.0,
        structure=SimpleNamespace(bias="BULLISH", demand_zone=(99.0, 98.0),
                                  supply_zone=(101.0, 102.0)),
        momentum=SimpleNamespace(trend_persistence=0, adx=0, trend_score=0),
        mtf_alignment={},
    )


def _stub_regime():
    return SimpleNamespace(primary_regime=SimpleNamespace(value="TRENDING"))


def _stub_devil():
    return SimpleNamespace(threats_detected=[], liquidity_traps=[],
                           invalidation_triggers=[], penalty_score=0.0)


# --------------------------------------------------------------------------
# The flag itself
# --------------------------------------------------------------------------

def test_real_reports_are_not_fallbacks_by_default():
    """The flag must default to False, or every real analyst becomes suspect."""
    assert _real(AnalystRole.MACRO).is_fallback is False


def test_answered_reports_keeps_real_reports():
    reports = {"A": _real(AnalystRole.MACRO), "B": _real(AnalystRole.RISK)}
    assert len(answered_reports(reports)) == 2


def test_answered_reports_drops_flagged_reports():
    reports = {
        "A": _real(AnalystRole.MACRO),
        "B": AnalystReport(role=AnalystRole.RISK, symbol="XAUUSD", bias="NEUTRAL",
                           score=50.0, confidence=0.0, is_fallback=True),
    }
    kept = answered_reports(reports)
    assert [r.role for r in kept] == [AnalystRole.MACRO]


def test_answered_reports_accepts_an_iterable_not_only_a_mapping():
    """`hypothesis_engine` iterates; `decision_engine` passes a dict."""
    reports = [_real(AnalystRole.MACRO),
               AnalystReport(role=AnalystRole.RISK, symbol="XAUUSD", bias="NEUTRAL",
                             score=50.0, confidence=0.0, is_fallback=True)]
    assert len(answered_reports(reports)) == 1


def test_answered_reports_treats_a_missing_attribute_as_real():
    """Test doubles and older objects predate the field; do not silently drop them."""
    assert len(answered_reports([SimpleNamespace(score=70.0)])) == 1


# --------------------------------------------------------------------------
# The real fallback constructor is the only thing that sets the flag
# --------------------------------------------------------------------------

def _cluster_with_failing(roles_to_fail):
    """A real cluster whose named analysts raise and whose others return real reports."""
    cluster = ParallelAnalystCluster(timeout_sec=5.0, parallel=True)
    mapping = {
        "structure_analyst": AnalystRole.STRUCTURE,
        "momentum_analyst": AnalystRole.MOMENTUM,
        "liquidity_analyst": AnalystRole.LIQUIDITY,
        "volatility_analyst": AnalystRole.VOLATILITY,
        "macro_analyst": AnalystRole.MACRO,
        "risk_analyst": AnalystRole.RISK,
    }

    def _boom(context, regime):
        raise RuntimeError("analyst exploded")

    for attr, role in mapping.items():
        if attr in roles_to_fail:
            setattr(cluster, attr, SimpleNamespace(analyze=_boom))
        else:
            setattr(cluster, attr, SimpleNamespace(
                analyze=(lambda _r: (lambda context, regime: _real(_r)))(role)))
    cluster.devil_advocate = SimpleNamespace(
        critique_opportunity=lambda *a, **k: _stub_devil())
    return cluster


def test_the_timeout_substitute_is_flagged():
    cluster = _cluster_with_failing({"macro_analyst"})
    reports, _ = cluster.run_all_parallel(_stub_context(), _stub_regime(), "BUY")

    assert reports["MACRO"].is_fallback is True
    assert reports["MACRO"].score == 50.0
    assert reports["MACRO"].confidence == 0.0
    # The five that ran must NOT be flagged.
    assert all(not reports[k].is_fallback
               for k in reports if k != "MACRO")


def test_a_healthy_run_flags_nothing():
    """Non-vacuity: the flag must not be set on a cluster where nothing failed."""
    cluster = _cluster_with_failing(set())
    reports, _ = cluster.run_all_parallel(_stub_context(), _stub_regime(), "BUY")
    assert not any(r.is_fallback for r in reports.values())


# --------------------------------------------------------------------------
# ai_score -- the hard-gate input
# --------------------------------------------------------------------------

def test_ai_score_ignores_the_fallback():
    """The load-bearing case: 5 real analysts at 70 must give 70.0, not 66.67."""
    cluster = _cluster_with_failing({"macro_analyst"})
    reports, _ = cluster.run_all_parallel(_stub_context(), _stub_regime(), "BUY")

    engine = DecisionEngine.__new__(DecisionEngine)  # the method reads no state
    assert engine._blended_ai_score(reports, "XAUUSD") == pytest.approx(70.0)


def test_ai_score_would_be_diluted_without_the_flag():
    """Non-vacuity: with the flag cleared the fabricated 50 IS averaged in.

    Without this, the test above would also pass if `_blended_ai_score` simply
    ignored reports it did not like for some other reason.
    """
    cluster = _cluster_with_failing({"macro_analyst"})
    reports, _ = cluster.run_all_parallel(_stub_context(), _stub_regime(), "BUY")
    for rep in reports.values():
        rep.is_fallback = False

    engine = DecisionEngine.__new__(DecisionEngine)
    # (70*5 + 50) / 6 == 66.67 -- the fabricated reading dragging the mean down.
    assert engine._blended_ai_score(reports, "XAUUSD") == pytest.approx(66.666, abs=1e-2)


def test_ai_score_is_zero_when_no_analyst_answered():
    """The degenerate case must not invent a neutral 50 either."""
    cluster = _cluster_with_failing({
        "structure_analyst", "momentum_analyst", "liquidity_analyst",
        "volatility_analyst", "macro_analyst", "risk_analyst",
    })
    reports, _ = cluster.run_all_parallel(_stub_context(), _stub_regime(), "BUY")
    assert all(r.is_fallback for r in reports.values())

    engine = DecisionEngine.__new__(DecisionEngine)
    assert engine._blended_ai_score(reports, "XAUUSD") == 0.0


def test_ai_score_is_zero_for_an_empty_panel():
    engine = DecisionEngine.__new__(DecisionEngine)
    assert engine._blended_ai_score({}, "XAUUSD") == 0.0


def test_evaluate_actually_uses_the_blended_helper():
    """Wiring: the fix is worthless if `evaluate` still does its own sum."""
    src = inspect.getsource(DecisionEngine.evaluate)
    assert "self._blended_ai_score(analyst_reports" in src
    assert "sum(r.score for r in analyst_reports.values())" not in src


# --------------------------------------------------------------------------
# hypothesis_engine -- evidence and the confluence denominator
# --------------------------------------------------------------------------

def _hypotheses_for(reports):
    return HypothesisEngine().construct_hypotheses(
        _stub_context(), _stub_regime(), reports, _stub_devil(), "BUY")


def test_primary_evidence_excludes_the_fallback_marker():
    """A process failure must not be quoted as market evidence."""
    reports = {
        "MACRO": AnalystReport(role=AnalystRole.MACRO, symbol="XAUUSD", bias="NEUTRAL",
                               score=50.0, confidence=0.0,
                               evidence=[f"MACRO {FALLBACK_MARKER}"], is_fallback=True),
        "STRUCTURE": _real(AnalystRole.STRUCTURE),
    }
    hyp = _hypotheses_for(reports)
    joined = " | ".join(hyp.primary_evidence)
    assert FALLBACK_MARKER not in joined
    assert "STRUCTURE real evidence" in joined


def test_primary_evidence_would_include_it_without_the_flag():
    """Non-vacuity: prove the filter is what removed the marker."""
    reports = {
        "MACRO": AnalystReport(role=AnalystRole.MACRO, symbol="XAUUSD", bias="NEUTRAL",
                               score=50.0, confidence=0.0,
                               evidence=[f"MACRO {FALLBACK_MARKER}"], is_fallback=False),
        "STRUCTURE": _real(AnalystRole.STRUCTURE),
    }
    hyp = _hypotheses_for(reports)
    assert FALLBACK_MARKER in " | ".join(hyp.primary_evidence)


def test_confluence_denominator_excludes_the_fallback():
    """`total_score` divides the confluence ratio, so an invented 50 dilutes it."""
    real_only = {"STRUCTURE": _real(AnalystRole.STRUCTURE, score=80.0)}
    with_fallback = dict(real_only)
    with_fallback["MACRO"] = AnalystReport(
        role=AnalystRole.MACRO, symbol="XAUUSD", bias="NEUTRAL", score=50.0,
        confidence=0.0, evidence=["x"], is_fallback=True)

    a = _hypotheses_for(real_only)
    b = _hypotheses_for(with_fallback)
    assert b.primary_probability == pytest.approx(a.primary_probability)


def test_confluence_denominator_would_change_without_the_flag():
    """Non-vacuity for the denominator test above."""
    real_only = {"STRUCTURE": _real(AnalystRole.STRUCTURE, score=80.0)}
    with_unflagged = dict(real_only)
    with_unflagged["MACRO"] = AnalystReport(
        role=AnalystRole.MACRO, symbol="XAUUSD", bias="NEUTRAL", score=50.0,
        confidence=0.0, evidence=["x"], is_fallback=False)

    a = _hypotheses_for(real_only)
    b = _hypotheses_for(with_unflagged)
    assert b.primary_probability != pytest.approx(a.primary_probability)

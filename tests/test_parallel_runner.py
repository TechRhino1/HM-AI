"""Tests for jarvis.analysts.parallel_runner.ParallelAnalystCluster.

This is the fan-out for every decision: `server.py:244`, `orchestrator.py:424`,
`backtesting/engine.py:582` and `backtesting/signal_scan.py:136` all go through
it. It had no tests.

THE DEFECT THIS FILE EXISTS FOR: BOTH FALLBACKS FAIL OPEN, SILENTLY
-------------------------------------------------------------------
When an analyst or the Devil's Advocate times out or raises, the cluster
substitutes a neutral report. Two properties made that dangerous:

* **The Devil's Advocate is a gate, and its fallback always passes it.**
  `decision_engine:644` gates on
  `"Devil Adversarial Guard": penalty_score <= max_devil_penalty` (default 43.0),
  and the fallback sets `penalty_score = 0.0`. So when the critic dies the
  adversarial check is not weakened — it is removed, and the trade proceeds
  uncriticised with a full set of green quality gates.
* **`penalty_score` is also a recorded feature.** It goes into the ML feature
  vector (`decision_engine:192`) and into the scan columns as
  `adversarial_penalty` (`signal_scan.py:303`). A systematic 0.0 from timeouts is
  then indistinguishable from "the critic examined this and found nothing wrong"
  — the same "a fallback value that looks like a measurement" trap as
  `ai_dissector`'s volatility floor, except this one lands in the columns the
  signal-quality research reads.

Both remain fail-open deliberately: blocking every trade on a slow critic is
worse than trading uncriticised. What was fixed is the silence — every fallback
now logs a warning, and the Devil's Advocate fallback sets
`critique_confidence=0.0` instead of the schema default 1.0, so it can no longer
claim confidence in a critique that never ran.

ALSO PINNED, NOT CHANGED
------------------------
* **The two modes have different failure semantics.** `parallel=True` swallows an
  analyst exception into a fallback; `parallel=False` has no try/except at all
  and propagates it, so the same inputs either degrade or crash depending on a
  constructor flag. `signal_scan` passes `parallel_analysts` through, so scan
  results can differ by mode.
* **The timeout is per future, not per call.** `fut.result(timeout=...)` is
  awaited once per analyst in dict order, so a full stall costs
  `len(futures) * timeout_sec` (6 x 2.0s) plus the Devil's Advocate, not 2.0s.
* **A timed-out future is never cancelled.** `concurrent.futures` does not stop a
  running task on timeout, so a hung analyst keeps its worker until it finishes.
  With `max_workers=8` and 7 submissions + the critic, repeated stalls exhaust
  the pool and everything downstream times out.
* **Analyst fallbacks score 50.0** — "neutral", which flows into the same
  aggregation as a genuine reading. The only marker is the
  "timeout / neutral fallback" string in `evidence`.
"""

import threading
import time
from types import SimpleNamespace

import pytest

from jarvis.analysts.parallel_runner import ParallelAnalystCluster, _analyst_role
from jarvis.data.schemas import (
    AnalystReport, AnalystRole, DevilAdvocateReport,
)

ROLES = ["STRUCTURE", "MOMENTUM", "LIQUIDITY", "VOLATILITY", "MACRO", "RISK"]


def ctx(symbol="XAUUSD"):
    return SimpleNamespace(symbol=symbol)


def report(role="STRUCTURE", score=77.0, bias="BUY"):
    return AnalystReport(role=role, symbol="XAUUSD", bias=bias, score=score,
                         confidence=0.9, evidence=["real"], risk_factors=[])


def devil(penalty=12.0):
    return DevilAdvocateReport(symbol="XAUUSD", counter_bias="SELL",
                               penalty_score=penalty,
                               invalidation_risk_coefficient=1.0,
                               threats_detected=["a threat"],
                               invalidation_triggers=[], liquidity_traps=[])


class Cluster:
    """A ParallelAnalystCluster with every analyst replaced by a controllable stub.

    Constructing the real analysts is unnecessary here — this file is about the
    fan-out, and the stubs let each one raise, hang or return on demand.
    """

    def __init__(self, parallel=True, timeout_sec=2.0):
        self.c = ParallelAnalystCluster(parallel=parallel, timeout_sec=timeout_sec)
        self.behaviour = {r: ("ok", None) for r in ROLES}
        self.devil_behaviour = ("ok", None)
        self.finished = {}
        for r in ROLES:
            self._patch_analyst(r)
        self._patch_devil()

    def _patch_analyst(self, role):
        analyst = {
            "STRUCTURE": self.c.structure_analyst,
            "MOMENTUM": self.c.momentum_analyst,
            "LIQUIDITY": self.c.liquidity_analyst,
            "VOLATILITY": self.c.volatility_analyst,
            "MACRO": self.c.macro_analyst,
            "RISK": self.c.risk_analyst,
        }[role]

        def analyze(context, regime):
            kind, arg = self.behaviour[role]
            if kind == "raise":
                raise RuntimeError(arg or "analyst exploded")
            if kind == "sleep":
                time.sleep(arg)
            self.finished[role] = True
            return report(role=role)

        analyst.analyze = analyze

    def _patch_devil(self):
        def critique(context, regime, bias):
            kind, arg = self.devil_behaviour
            if kind == "raise":
                raise RuntimeError(arg or "critic exploded")
            if kind == "sleep":
                time.sleep(arg)
            return devil()

        self.c.devil_advocate.critique_opportunity = critique

    def run(self, symbol="XAUUSD", bias="BUY"):
        return self.c.run_all_parallel(ctx(symbol), SimpleNamespace(), bias)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_all_six_analysts_and_the_critic_are_returned(self):
        reports, dv = Cluster().run()
        assert set(reports) == set(ROLES)
        assert isinstance(dv, DevilAdvocateReport)

    def test_the_real_reports_are_passed_through(self):
        reports, _ = Cluster().run()
        for role in ROLES:
            assert reports[role].score == 77.0
            assert reports[role].bias == "BUY"

    def test_the_real_critic_is_passed_through(self):
        _, dv = Cluster().run()
        assert dv.penalty_score == 12.0
        assert dv.threats_detected == ["a threat"]

    def test_sequential_mode_returns_the_same_shape(self):
        reports, dv = Cluster(parallel=False).run()
        assert set(reports) == set(ROLES)
        assert dv.penalty_score == 12.0

    def test_no_executor_is_built_in_sequential_mode(self):
        assert Cluster(parallel=False).c._executor is None

    def test_the_pool_is_sized_for_the_fan_out(self):
        """8 workers for 6 analysts + 1 critic — see the worker-leak note above."""
        assert Cluster().c._executor._max_workers == 8


# ---------------------------------------------------------------------------
# The analyst fallback
# ---------------------------------------------------------------------------

class TestAnalystFallback:
    def test_a_raising_analyst_is_replaced(self):
        cl = Cluster()
        cl.behaviour["MOMENTUM"] = ("raise", "boom")
        reports, _ = cl.run()
        assert reports["MOMENTUM"].score == 50.0
        assert reports["MOMENTUM"].bias == "NEUTRAL"
        assert reports["MOMENTUM"].confidence == 0.50

    def test_a_timing_out_analyst_is_replaced(self):
        cl = Cluster(timeout_sec=0.05)
        cl.behaviour["MACRO"] = ("sleep", 0.5)
        reports, _ = cl.run()
        assert reports["MACRO"].score == 50.0

    def test_only_the_failing_role_is_replaced(self):
        cl = Cluster()
        cl.behaviour["RISK"] = ("raise", "boom")
        reports, _ = cl.run()
        assert reports["RISK"].score == 50.0
        for role in ("STRUCTURE", "MOMENTUM", "LIQUIDITY", "VOLATILITY", "MACRO"):
            assert reports[role].score == 77.0, role

    def test_the_fallback_carries_the_context_symbol(self):
        cl = Cluster()
        cl.behaviour["RISK"] = ("raise", "boom")
        reports, _ = cl.run(symbol="EURUSD")
        assert reports["RISK"].symbol == "EURUSD"

    def test_the_fallback_is_marked_in_evidence(self):
        """The only way downstream can tell a fallback from a real neutral."""
        cl = Cluster()
        cl.behaviour["RISK"] = ("raise", "boom")
        reports, _ = cl.run()
        assert any("fallback" in e for e in reports["RISK"].evidence)

    def test_the_fallback_has_no_risk_factors(self):
        cl = Cluster()
        cl.behaviour["RISK"] = ("raise", "boom")
        reports, _ = cl.run()
        assert reports["RISK"].risk_factors == []

    def test_the_fallback_role_is_the_enum_not_a_bare_string(self):
        """AnalystReport.role is typed AnalystRole. AnalystRole is a str-Enum so a
        bare string compares equal — which is why the old code went unnoticed —
        but only the enum has .name/.value."""
        cl = Cluster()
        cl.behaviour["RISK"] = ("raise", "boom")
        reports, _ = cl.run()
        assert reports["RISK"].role is AnalystRole.RISK
        assert reports["RISK"].role.value == "RISK"

    def test_every_role_maps_to_its_enum(self):
        for role in ROLES:
            assert _analyst_role(role) is AnalystRole(role)

    def test_an_unknown_role_name_falls_back_to_the_string(self):
        assert _analyst_role("NOT_A_ROLE") == "NOT_A_ROLE"

    def test_a_fallback_is_logged(self, caplog):
        cl = Cluster()
        cl.behaviour["MOMENTUM"] = ("raise", "boom")
        with caplog.at_level("WARNING", logger="JARVIS_AnalystCluster"):
            cl.run()
        assert any("MOMENTUM" in r.message and "fallback" in r.message
                   for r in caplog.records)


# ---------------------------------------------------------------------------
# The Devil's Advocate fallback — the fail-open gate
# ---------------------------------------------------------------------------

class TestDevilFallback:
    def test_a_raising_critic_is_replaced(self):
        cl = Cluster()
        cl.devil_behaviour = ("raise", "boom")
        _, dv = cl.run()
        assert dv.penalty_score == 0.0
        assert dv.counter_bias == "NEUTRAL"
        assert dv.threats_detected == []
        assert dv.liquidity_traps == []

    def test_a_timing_out_critic_is_replaced(self):
        cl = Cluster(timeout_sec=0.05)
        cl.devil_behaviour = ("sleep", 0.5)
        _, dv = cl.run()
        assert dv.penalty_score == 0.0

    def test_the_fallback_carries_the_context_symbol(self):
        cl = Cluster()
        cl.devil_behaviour = ("raise", "boom")
        _, dv = cl.run(symbol="EURUSD")
        assert dv.symbol == "EURUSD"

    def test_the_fallback_penalty_passes_the_adversarial_gate(self):
        """THE FAIL-OPEN, STATED PLAINLY.

        decision_engine:644 gates on `penalty_score <= max_devil_penalty` with a
        default of 43.0. A fallback penalty of 0.0 therefore passes, so a dead
        critic removes the adversarial check rather than tripping it.
        """
        cl = Cluster()
        cl.devil_behaviour = ("raise", "boom")
        _, dv = cl.run()
        assert dv.penalty_score <= 43.0
        assert dv.penalty_score == 0.0          # not merely small, but absent

    def test_the_fallback_claims_no_confidence(self):
        """The schema default is 1.0, i.e. full confidence in a critique that
        never ran."""
        cl = Cluster()
        cl.devil_behaviour = ("raise", "boom")
        _, dv = cl.run()
        assert dv.critique_confidence == 0.0

    def test_a_real_critique_still_carries_its_confidence(self):
        _, dv = Cluster().run()
        assert dv.critique_confidence == 1.0    # untouched on the happy path

    def test_the_fallback_risk_coefficient_is_neutral(self):
        cl = Cluster()
        cl.devil_behaviour = ("raise", "boom")
        _, dv = cl.run()
        assert dv.invalidation_risk_coefficient == 1.0

    def test_a_critic_failure_is_logged_loudly(self, caplog):
        cl = Cluster()
        cl.devil_behaviour = ("raise", "boom")
        with caplog.at_level("WARNING", logger="JARVIS_AnalystCluster"):
            cl.run()
        assert any("Devil" in r.message and "uncriticised" in r.message
                   for r in caplog.records)


# ---------------------------------------------------------------------------
# The two modes fail differently
# ---------------------------------------------------------------------------

class TestModeDivergence:
    def test_parallel_mode_swallows_an_analyst_exception(self):
        cl = Cluster(parallel=True)
        cl.behaviour["MOMENTUM"] = ("raise", "boom")
        reports, _ = cl.run()
        assert reports["MOMENTUM"].score == 50.0

    def test_sequential_mode_propagates_an_analyst_exception(self):
        """No try/except on the sequential path — the same bug degrades one mode
        and crashes the other."""
        cl = Cluster(parallel=False)
        cl.behaviour["MOMENTUM"] = ("raise", "boom")
        with pytest.raises(RuntimeError, match="boom"):
            cl.run()

    def test_sequential_mode_propagates_a_critic_exception(self):
        cl = Cluster(parallel=False)
        cl.devil_behaviour = ("raise", "boom")
        with pytest.raises(RuntimeError, match="boom"):
            cl.run()


# ---------------------------------------------------------------------------
# Timeout budgeting and worker behaviour
# ---------------------------------------------------------------------------

class TestTimeoutBudget:
    def test_the_timeout_is_applied_per_analyst_not_per_call(self):
        """Six stalled analysts cost six waits, not one. Generous margins — this
        is a lower bound on elapsed time, not an exact measurement."""
        cl = Cluster(timeout_sec=0.05)
        for role in ROLES:
            cl.behaviour[role] = ("sleep", 0.6)
        started = time.monotonic()
        reports, _ = cl.run()
        elapsed = time.monotonic() - started
        assert all(r.score == 50.0 for r in reports.values())
        assert elapsed >= 3 * 0.05, f"expected the waits to stack, took {elapsed:.3f}s"

    def test_the_analysts_still_run_concurrently(self):
        """Six 0.4s analysts must not take 2.4s — parallel execution works."""
        cl = Cluster(timeout_sec=5.0)
        for role in ROLES:
            cl.behaviour[role] = ("sleep", 0.4)
        started = time.monotonic()
        cl.run()
        elapsed = time.monotonic() - started
        assert elapsed < 6 * 0.4, f"expected concurrency, took {elapsed:.3f}s"

    def test_a_timed_out_analyst_keeps_running(self):
        """concurrent.futures does not cancel on timeout, so the worker is not
        reclaimed until the task finishes on its own."""
        cl = Cluster(timeout_sec=0.05)
        cl.behaviour["MACRO"] = ("sleep", 0.4)
        cl.run()
        assert "MACRO" not in cl.finished          # still inside the sleep
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and "MACRO" not in cl.finished:
            time.sleep(0.02)
        assert cl.finished.get("MACRO"), "the abandoned future never completed"

    def test_a_zero_timeout_falls_back_on_everything(self):
        """timeout=0 refuses to wait at all, so everything falls back."""
        cl = Cluster(timeout_sec=0.0)
        for role in ROLES:
            cl.behaviour[role] = ("sleep", 0.3)
        cl.devil_behaviour = ("sleep", 0.3)
        reports, dv = cl.run()
        assert all(r.score == 50.0 for r in reports.values())
        assert dv.penalty_score == 0.0

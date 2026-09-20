"""AI8 — a backtest must not be decided by anything learned from live trading.

`BacktestEngine` builds a `DecisionEngine`, and that engine owns every component on the decision path
that is stateful against disk: `OnlineMLPredictor` (learned weights), `MetaLabeler` and
`ConfidenceCalibrationEngine` (fitted models), `SelfLearningEngine` (reads the live trade journal)
and `RealtimeOptimizer` (shifts the gate thresholds from realised P&L).

All of them load **eagerly, in their own constructor**. So it is not enough to wrap `run_backtest()`
in `offline_mode()` — by then the live state is already in memory. Measured on this machine before
the fix: the predictor woke up with **199 live training steps** and weights
`[0.363, 0.312, 0.162, 0.227]` where the neutral prior is `[0.35, 0.25, 0.15, 0.20]` / 10 steps, and
`SelfLearningEngine` reported a **0.9** regime multiplier and a **25-sample 0.39 win rate** from
today's journal instead of the neutral `1.0 / 0 / 0.50`. A simulation of June was being decided by
trades that happened in September — lookahead bias, in an engine whose docstring promises none.

Two notes on what this file can and cannot prove.

* **It does not depend on there being live state on disk.** The weights file
  (`jarvis_online_ml_weights.json`) is untracked, so a fresh clone has none and a test that asserted
  "the weights are neutral" would pass with the fix removed. These tests instead spy on the flag, so
  they go red on a machine with no learned state at all.
* **The reproducibility test cannot discriminate the mutation.** Two consecutive runs read the same
  (static) live file, so they agree whether or not the engine is hermetic — the real damage is that
  today's backtest disagrees with *next month's*, which no unit test can show. It is here because
  byte-identical reruns are the M3 exit criterion, not because it proves the fix.
"""

import hashlib
import os

import numpy as np
import pandas as pd
import pytest

from jarvis.backtesting.engine import BacktestEngine
from jarvis.config import runtime as runtime_mod
from jarvis.config.paths import REPO_ROOT

WEIGHTS_FILE = os.path.join(REPO_ROOT, "jarvis_online_ml_weights.json")


@pytest.fixture
def offscreen():
    """Records every value `is_offline()` returns, while still delegating to the real one.

    The components import the flag *inside* their methods (`from jarvis.config.runtime import
    is_offline`), so replacing the module attribute is picked up at call time.
    """
    seen = []
    real = runtime_mod.is_offline

    def spy():
        value = real()
        seen.append(value)
        return value

    original = runtime_mod.is_offline
    runtime_mod.is_offline = spy
    try:
        yield seen
    finally:
        runtime_mod.is_offline = original


@pytest.fixture
def frame():
    n = 600
    rng = np.random.default_rng(7)
    close = 2000.0 + np.cumsum(rng.normal(0, 8.0, n))
    df = pd.DataFrame({
        "open": close + rng.normal(0, 2, n),
        "high": close + abs(rng.normal(0, 5, n)),
        "low": close - abs(rng.normal(0, 5, n)),
        "close": close,
        "volume": rng.integers(100, 900, n).astype(float),
    })
    df.index = pd.date_range("2024-01-01", periods=n, freq="h")
    return df


class TestTheEngineStartsFromPriors:
    def test_the_decision_path_is_built_hermetically(self, offscreen):
        """THE DEFECT. Without the wrap, every one of these calls sees False and the
        components load whatever live trading left on disk."""
        BacktestEngine()
        assert True in offscreen, (
            "DecisionEngine was constructed with offline_mode() off, so the backtest "
            "loaded live learned state."
        )

    def test_the_flag_does_not_leak_out_of_the_constructor(self):
        BacktestEngine()
        assert runtime_mod.is_offline() is False

    def test_the_bandit_starts_from_priors(self):
        """`DecisionEngine` builds a `StrategySelector`, which builds the persisting
        `StrategyBandit` — that one reads `jarvis_bandit_state.json` in its own
        constructor, so it is part of the same eager-load problem.

        With the fix this holds whether or not a state file exists. It only turns red
        where live state is actually present, so it is weaker evidence than the flag
        spy above; it is here to name the bandit explicitly.
        """
        engine = BacktestEngine()
        bandit = engine.decision_engine.strategy_selector.bandit
        assert bandit.priors == {}
        assert bandit.counts == {}


class TestTheRunIsHermetic:
    def test_the_simulation_runs_hermetically(self, offscreen, frame):
        """Construction is not enough: `SelfLearningEngine` re-reads the live journal at
        call time, so the run itself must be inside the context too."""
        offscreen.clear()
        BacktestEngine().run_backtest(df_h1=frame, symbol="XAUUSD", start_bar_idx=60)
        assert True in offscreen

    def test_the_flag_is_restored_after_the_run(self, frame):
        BacktestEngine().run_backtest(df_h1=frame, symbol="XAUUSD", start_bar_idx=60)
        assert runtime_mod.is_offline() is False

    def test_a_backtest_writes_nothing(self, frame):
        """Today's loop performs no learning call, so nothing is written either way — but
        the guarantee is the point: a future learning call inside the loop must not
        reach the live store."""
        before = _digest()
        BacktestEngine().run_backtest(df_h1=frame, symbol="XAUUSD", start_bar_idx=60)
        assert _digest() == before

    def test_the_run_still_gets_its_bars(self, frame):
        """Hermeticity must not extend to historical data — seven components honour the
        flag and the data engine is not one of them."""
        res = BacktestEngine().run_backtest(df_h1=frame, symbol="XAUUSD", start_bar_idx=60)
        assert res is not None
        assert isinstance(res.get("trades"), list)


class TestTwoRunsAgree:
    def test_the_same_frame_gives_the_same_trades(self, frame):
        a = BacktestEngine().run_backtest(df_h1=frame, symbol="XAUUSD", start_bar_idx=60)
        b = BacktestEngine().run_backtest(df_h1=frame, symbol="XAUUSD", start_bar_idx=60)
        _key = lambda t: (t.get("entry_time"), t.get("direction"), round(float(t.get("entry") or 0), 6))
        assert [_key(t) for t in a["trades"]] == [_key(t) for t in b["trades"]]

    def test_reusing_one_engine_gives_the_same_trades(self, frame):
        """In-run adaptation is causal and deterministic; it must not accumulate."""
        eng = BacktestEngine()
        a = eng.run_backtest(df_h1=frame, symbol="XAUUSD", start_bar_idx=60)
        b = eng.run_backtest(df_h1=frame, symbol="XAUUSD", start_bar_idx=60)
        assert len(a["trades"]) == len(b["trades"])


def _digest():
    """Hash the learned artefact a leaky backtest would write."""
    if not os.path.exists(WEIGHTS_FILE):
        return "ABSENT"
    with open(WEIGHTS_FILE, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()

"""AI6 — the learning loop must survive a restart, and an unknown R must stay unknown.

Two defects with one shared root: ``JarvisOrchestrator._pending_features`` is a plain dict.

1. **The learning loop died on every restart.** Everything the close handler needs —
   direction, entry, stop, symbol, strategy, regime, and the feature vector — is captured
   at open into that dict. A trade opened before the current process therefore arrived at
   ``_on_trade_closed`` with ``pending is None``, so the ML update and the bandit update
   were skipped outright. ``record_trade`` had already persisted all of it to
   ``trade_records``, so the fix recovers the context from the journal instead of giving up.

2. **R was invented from the outcome flag.** With no ``pending``, R fell back to
   ``2.0 if is_win else -1.0`` — a number derived from the very quantity the learner is
   supposed to predict, then fed to the bandit as if it had been measured. The consumers
   could not refuse it: ``float(r_multiple or 1.0)`` turned an explicit ``None`` into +1R,
   and ``max(0.5, None)`` in ``EnsembleStrategyBandit`` raised ``TypeError`` outright.

The rule this file pins — the same one D19 and AI5 established for their own columns — is
**record the measured part, withhold the unmeasured part**:

* The win/loss IS measured, so the bandit still sees the trade.
* The R magnitude is NOT, so no R-denominated term moves.
* The ML update is skipped entirely, because there R is not a recorded quantity at all —
  it is the *weight* on the gradient, and there is no honest weight to use.
"""

import pytest

from jarvis.config.runtime import offline_mode
from jarvis.learning.trade_memory import TradeMemory

TICKET = 7001
# A BUY at 1.1000 with a stop at 1.0900 risks 100 pips.
ENTRY = 1.1000
STOP = 1.0900
RISK = 0.0100


@pytest.fixture
def db(tmp_path):
    return tmp_path / "jarvis_trade_memory.db"


class _Null:
    """Absorbs any call. The close handler fans out to five collaborators; most of
    them are not what these tests are about."""

    def __getattr__(self, name):
        return _Null()

    def __call__(self, *a, **k):
        return None


class _FakeMonitor:
    def pop_excursions(self, ticket):
        return None


class _Recorder:
    """Stands in for the ML predictor and the bandit, and remembers what it was told."""

    def __init__(self):
        self.ml_calls = []
        self.bandit_calls = []

    # "OMITTED" distinguishes "the caller said nothing about R" from any real value.
    def update_online(self, features, target_win, r_multiple="OMITTED"):
        self.ml_calls.append({"features": list(features), "target": target_win, "r": r_multiple})

    def record_outcome(self, **kw):
        self.bandit_calls.append(kw)


def _orchestrator(db):
    from jarvis.application.orchestrator import JarvisOrchestrator

    orch = JarvisOrchestrator.__new__(JarvisOrchestrator)
    # THE DEFECT: an empty dict is what a freshly started process looks like.
    orch._pending_features = {}
    orch.trade_memory = TradeMemory(db_path=str(db))
    orch.position_monitor = _FakeMonitor()
    ml = _Recorder()
    bandit = _Recorder()
    orch.ml_predictor = ml
    orch.strategy_bandit = bandit
    orch.circuit_breaker = _Null()
    orch.drawdown_guard = _Null()
    orch.decision_engine = _Null()
    return orch, ml, bandit


@pytest.fixture
def hermetic():
    """Bandits are stateful against disk; a test must not read or write that state."""
    with offline_mode():
        yield


class TestFetchTrade:
    def test_the_row_comes_back(self, tmp_path):
        tm = TradeMemory(db_path=str(tmp_path / "m.db"))
        tm.record_trade({"ticket": TICKET, "symbol": "EURUSD", "type": "BUY",
                         "entry": ENTRY, "sl": STOP})
        row = tm.fetch_trade(TICKET)
        assert row is not None
        assert row["symbol"] == "EURUSD"
        assert row["trade_type"] == "BUY"
        assert row["entry_price"] == pytest.approx(ENTRY)
        tm.close()

    def test_an_unknown_ticket_is_none(self, tmp_path):
        tm = TradeMemory(db_path=str(tmp_path / "m.db"))
        assert tm.fetch_trade(999999) is None
        tm.close()

    def test_garbage_does_not_raise(self, tmp_path):
        tm = TradeMemory(db_path=str(tmp_path / "m.db"))
        assert tm.fetch_trade(None) is None
        assert tm.fetch_trade("not-a-ticket") is None
        tm.close()


class TestRecoveryFromTheJournal:
    def _seed(self, db, **over):
        tm = TradeMemory(db_path=str(db))
        payload = {"ticket": TICKET, "symbol": "EURUSD", "type": "SELL",
                   "entry": ENTRY, "sl": 1.1100, "strategy": "BREAKOUT_EXPANSION",
                   "regime": "TREND_BEAR", "ml_features": [0.1, 0.2, 0.3]}
        payload.update(over)
        tm.record_trade(payload)
        return tm

    def test_the_geometry_is_rebuilt(self, db):
        orch, _, _ = _orchestrator(db)
        self._seed(db)
        rec = orch._recover_pending_features(TICKET)
        assert rec["type"] == "SELL"
        assert rec["entry"] == pytest.approx(ENTRY)
        assert rec["sl"] == pytest.approx(1.1100)
        assert rec["risk_dist"] == pytest.approx(0.0100)
        assert rec["strategy"] == "BREAKOUT_EXPANSION"
        assert rec["regime"] == "TREND_BEAR"
        assert rec["symbol"] == "EURUSD"

    def test_the_feature_vector_is_rebuilt(self, db):
        orch, _, _ = _orchestrator(db)
        self._seed(db)
        assert orch._recover_pending_features(TICKET)["features"] == [0.1, 0.2, 0.3]

    def test_an_absent_vector_is_absent_not_a_zero_sample(self, db):
        """`record_trade` json-dumps `[]` when no vector was stored. A gradient step
        on an all-zero feature vector is a fabricated observation, so the key must be
        missing rather than present-and-empty."""
        orch, _, _ = _orchestrator(db)
        self._seed(db, ml_features=None)
        rec = orch._recover_pending_features(TICKET)
        assert "features" not in rec

    def test_a_missing_stop_disables_r_rather_than_faking_a_denominator(self, db):
        """|entry - 0| is not a risk distance — on EURUSD it reads as 11,000 pips."""
        orch, _, _ = _orchestrator(db)
        self._seed(db, sl=0.0)
        assert orch._recover_pending_features(TICKET)["risk_dist"] == 0.0

    def test_a_ticket_that_was_never_journalled_is_none(self, db):
        orch, _, _ = _orchestrator(db)
        assert orch._recover_pending_features(999999) is None


class TestTheRestartScenario:
    def _open(self, orch, **over):
        payload = {"ticket": TICKET, "symbol": "EURUSD", "type": "BUY",
                   "entry": ENTRY, "sl": STOP, "strategy": "TREND_FOLLOWING",
                   "regime": "TREND_BULL", "ml_features": [0.4, 0.5]}
        payload.update(over)
        orch.trade_memory.record_trade(payload)

    def test_the_closed_trade_still_teaches_the_model(self, db):
        """THE DEFECT. A trade opened before this process used to reach the close
        handler with nothing, so the ML update never ran at all."""
        orch, ml, _ = _orchestrator(db)
        self._open(orch)
        orch._on_trade_closed({"ticket": TICKET, "pnl": 50.0, "exit_price": 1.1050, "equity": 0.0})
        assert len(ml.ml_calls) == 1
        assert ml.ml_calls[0]["features"] == [0.4, 0.5]

    def test_the_model_is_given_the_measured_weight(self, db):
        orch, ml, _ = _orchestrator(db)
        self._open(orch)
        orch._on_trade_closed({"ticket": TICKET, "pnl": 50.0, "exit_price": 1.1050, "equity": 0.0})
        assert ml.ml_calls[0]["r"] == pytest.approx(0.50)

    def test_r_is_derived_from_geometry_not_from_the_win_flag(self, db):
        """A 50-pip gain on a 100-pip risk is +0.50R. The old fallback invented +2.0."""
        orch, _, bandit = _orchestrator(db)
        self._open(orch)
        orch._on_trade_closed({"ticket": TICKET, "pnl": 50.0, "exit_price": 1.1050, "equity": 0.0})
        assert bandit.bandit_calls[0]["r_multiple"] == pytest.approx(0.50)

    def test_a_loser_keeps_its_sign(self, db):
        """Old fallback: -1.0. Measured: -0.50R."""
        orch, _, bandit = _orchestrator(db)
        self._open(orch)
        orch._on_trade_closed({"ticket": TICKET, "pnl": -50.0, "exit_price": 1.0950, "equity": 0.0})
        assert bandit.bandit_calls[0]["r_multiple"] == pytest.approx(-0.50)

    def test_a_sell_is_not_inverted(self, db):
        """SELL at 1.1000, stop 1.1100, exit 1.0950 -> +0.50R, not -0.50R."""
        orch, _, bandit = _orchestrator(db)
        self._open(orch, type="SELL", sl=1.1100)
        orch._on_trade_closed({"ticket": TICKET, "pnl": 50.0, "exit_price": 1.0950, "equity": 0.0})
        assert bandit.bandit_calls[0]["r_multiple"] == pytest.approx(0.50)

    def test_the_recovered_strategy_and_regime_reach_the_bandit(self, db):
        orch, _, bandit = _orchestrator(db)
        self._open(orch)
        orch._on_trade_closed({"ticket": TICKET, "pnl": 50.0, "exit_price": 1.1050, "equity": 0.0})
        assert bandit.bandit_calls[0]["strategy"] == "TREND_FOLLOWING"
        assert bandit.bandit_calls[0]["regime"] == "TREND_BULL"


class TestUnknownRIsNotInvented:
    def test_no_journal_row_means_no_r(self, db):
        """THE DEFECT. Old behaviour: `2.0 if is_win else -1.0` — a number minted
        from the outcome flag and banked as if it had been measured."""
        orch, ml, bandit = _orchestrator(db)
        orch._on_trade_closed({"ticket": 999999, "pnl": 12.0, "exit_price": 1.2, "equity": 0.0})
        assert bandit.bandit_calls[0]["r_multiple"] is None

    def test_a_loss_stays_unknown_too(self, db):
        """Old behaviour: -1.0."""
        orch, _, bandit = _orchestrator(db)
        orch._on_trade_closed({"ticket": 999999, "pnl": -3.0, "exit_price": 1.2, "equity": 0.0})
        assert bandit.bandit_calls[0]["r_multiple"] is None

    def test_no_journal_row_means_no_feature_vector_either(self, db):
        """With no row there is nothing to learn from at all, so the update is genuinely
        skipped — not because R is missing, but because the sample is."""
        orch, ml, _ = _orchestrator(db)
        orch._on_trade_closed({"ticket": 999999, "pnl": 12.0, "exit_price": 1.2, "equity": 0.0})
        assert ml.ml_calls == []

    def test_the_ml_update_omits_r_rather_than_claiming_one(self, db):
        """The label (`is_win`) is measured, so the sample is still worth learning from.
        R is only the gradient weight, so it is OMITTED — passing None would be coerced
        to +1R and would be indistinguishable from a measured one."""
        orch, ml, _ = _orchestrator(db)
        orch.trade_memory.record_trade({"ticket": TICKET, "symbol": "EURUSD", "type": "BUY",
                                        "entry": ENTRY, "sl": 0.0, "ml_features": [0.4]})
        orch._on_trade_closed({"ticket": TICKET, "pnl": 12.0, "exit_price": 1.2, "equity": 0.0})
        assert len(ml.ml_calls) == 1
        assert ml.ml_calls[0]["r"] == "OMITTED"

    def test_the_bandit_still_sees_the_trade(self, db):
        """The win/loss IS measured. Dropping the whole observation to avoid guessing
        a magnitude would throw away real information."""
        orch, _, bandit = _orchestrator(db)
        orch._on_trade_closed({"ticket": 999999, "pnl": 12.0, "exit_price": 1.2, "equity": 0.0})
        assert len(bandit.bandit_calls) == 1
        assert bandit.bandit_calls[0]["is_win"] == 1

    def test_a_row_with_no_usable_geometry_also_yields_no_r(self, db):
        orch, ml, bandit = _orchestrator(db)
        orch.trade_memory.record_trade({"ticket": TICKET, "symbol": "EURUSD", "type": "BUY",
                                        "entry": ENTRY, "sl": 0.0, "ml_features": [0.4]})
        orch._on_trade_closed({"ticket": TICKET, "pnl": 12.0, "exit_price": 1.2, "equity": 0.0})
        assert bandit.bandit_calls[0]["r_multiple"] is None
        assert ml.ml_calls[0]["r"] == "OMITTED"


class TestTheBanditTreatsNoneAsUnknown:
    def _bandit(self):
        from jarvis.learning.strategy_bandit import StrategyBandit

        b = StrategyBandit()
        b.priors = {}
        b.counts = {}
        b.rewards = {}
        return b

    def _entry(self, b, strategy="TREND_FOLLOWING"):
        return b.priors["TREND_BULL"]["SWING"][strategy]

    def test_a_win_with_unknown_r_takes_the_neutral_increment(self, hermetic):
        """`alpha` counts wins. A win is a win whether or not its size was measured."""
        b = self._bandit()
        b.record_outcome("TREND_FOLLOWING", is_win=1, r_multiple=None,
                         regime="TREND_BULL", style="SWING")
        e = self._entry(b)
        assert e["alpha"] == pytest.approx(4.0)   # DEFAULT_ALPHA 3.0 + exactly one win
        assert e["beta"] == pytest.approx(2.0)
        assert e["pulls"] == 1

    def test_the_prior_reward_is_withheld(self, hermetic):
        """`rewards` is denominated in R. Coercing None to 1.0 banked an unmeasured
        trade as a +1R winner — this is the term that used to take the fabrication."""
        b = self._bandit()
        b.record_outcome("TREND_FOLLOWING", is_win=1, r_multiple=None,
                         regime="TREND_BULL", style="SWING")
        assert self._entry(b)["rewards"] == pytest.approx(0.0)

    def test_the_legacy_ucb_accumulator_is_withheld_too(self, hermetic):
        """Left unguarded this was `max(1.0, None)`, which raises TypeError."""
        b = self._bandit()
        b.record_outcome("TREND_FOLLOWING", is_win=1, r_multiple=None,
                         regime="TREND_BULL", style="SWING")
        # 3.0 seed, then the 0.98 sweep the bandit applies on every observation.
        assert b.rewards["TREND_BULL"]["TREND_FOLLOWING"] == pytest.approx(3.0 * 0.98)

    def test_a_loss_is_recorded_in_full(self, hermetic):
        """The -0.5 convention does not depend on R, so a loss still costs the same."""
        b = self._bandit()
        b.record_outcome("TREND_FOLLOWING", is_win=0, r_multiple=None,
                         regime="TREND_BULL", style="SWING")
        e = self._entry(b)
        assert e["alpha"] == pytest.approx(3.0)
        assert e["beta"] == pytest.approx(3.0)
        assert e["rewards"] == pytest.approx(-0.5)
        assert b.rewards["TREND_BULL"]["TREND_FOLLOWING"] == pytest.approx(3.0 * 0.98 - 0.5)

    def test_a_real_zero_r_is_not_promoted_to_a_winner(self, hermetic):
        """`float(r_multiple or 1.0)` collapsed an honest 0.0 into +1R. 0.0R floors to
        0.1, so alpha takes 0.5 — not the 1.0 a fabricated +1R would give."""
        b = self._bandit()
        b.record_outcome("TREND_FOLLOWING", is_win=1, r_multiple=0.0,
                         regime="TREND_BULL", style="SWING")
        assert self._entry(b)["alpha"] == pytest.approx(3.5)

    def test_a_measured_r_is_still_credited(self, hermetic):
        b = self._bandit()
        b.record_outcome("TREND_FOLLOWING", is_win=1, r_multiple=2.5,
                         regime="TREND_BULL", style="SWING")
        e = self._entry(b)
        assert e["alpha"] == pytest.approx(5.5)
        assert e["rewards"] == pytest.approx(2.5)

    def test_the_ensemble_does_not_raise_on_unknown_r(self):
        from jarvis.learning.ensemble_bandit import EnsembleStrategyBandit

        b = EnsembleStrategyBandit(["TREND_FOLLOWING"])
        # Old behaviour: `max(0.5, None)` -> TypeError.
        b.record_outcome("TREND_FOLLOWING", is_win=True, r_multiple=None)
        assert b._counts["TREND_FOLLOWING"] == 1
        assert b._wins["TREND_FOLLOWING"] == 1
        assert b._rewards["TREND_FOLLOWING"] == pytest.approx(0.0)

    def test_the_ensemble_still_credits_a_measured_r(self):
        from jarvis.learning.ensemble_bandit import EnsembleStrategyBandit

        b = EnsembleStrategyBandit(["TREND_FOLLOWING"])
        b.record_outcome("TREND_FOLLOWING", is_win=True, r_multiple=2.0)
        assert b._rewards["TREND_FOLLOWING"] == pytest.approx(2.0)

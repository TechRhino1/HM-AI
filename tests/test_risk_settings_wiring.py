"""`RiskEngine` must take its limits from `config/settings.json`.

THE DEFECT THESE TESTS EXIST FOR
--------------------------------
Every limit had two homes: a hardcoded literal in `RiskEngine.__init__` and a
declared value in `config/settings.json`'s ``risk`` block. `jarvis/config/settings.py`
faithfully loaded the file into ``cfg.risk`` — and **nothing in the tree ever read
``cfg.risk``**. The only two references to it were the writes inside that module.
``RiskEngine()`` was constructed with no arguments at ``orchestrator.py:98``, so the
hardcoded literals always won and the config file was decorative.

That is worse than having no config file, because the two copies had drifted apart
in the **looser** direction: the file declared ``max_open_positions: 2`` while the
engine enforced 3. An operator tightening a limit in the file would have seen no
effect and no error.

The contract these tests pin:

* a ``None`` argument (the new default) means "take it from the config";
* an explicit argument still wins, which is what ``BacktestEngine`` and the risk
  tests rely on;
* the resolved value reaches the objects that actually enforce it — not just
  ``self``.

`SETTINGS` is a module-level singleton, so the tests mutate its ``risk`` object with
distinctive values rather than asserting against whatever the file happens to say
today. A test that asserted ``RiskEngine().max_open_positions == 2`` would pass for
the wrong reason if 2 were also the literal, and would break the moment an operator
edited the file.
"""

import json
from pathlib import Path

import pytest

from jarvis.config.paths import REPO_ROOT
from jarvis.config.settings import SETTINGS
from jarvis.risk.risk_engine import RiskEngine

# The six limits RiskEngine owns. Kept explicit so a new constructor parameter
# that is not wired to the config is visible here.
ENGINE_LIMITS = {
    "max_daily_loss_pct": 1.25,
    "max_drawdown_pct": 2.5,
    "max_open_positions": 7,
    "max_symbol_positions": 4,
    "max_risk_per_trade_pct": 0.75,
    "max_portfolio_risk_pct": 3.5,
}


@pytest.fixture
def distinctive_config(monkeypatch):
    """Give every engine-owned limit a value no literal would coincidentally equal."""
    for key, value in ENGINE_LIMITS.items():
        monkeypatch.setattr(SETTINGS.risk, key, value)
    return ENGINE_LIMITS


# ─── The config is the source of truth ────────────────────────────────────────

def test_bare_engine_takes_every_limit_from_the_config(distinctive_config):
    engine = RiskEngine(is_backtest=True)
    for key, value in distinctive_config.items():
        assert getattr(engine, key) == value, key


def test_limits_reach_the_objects_that_enforce_them(distinctive_config):
    """Assigning to `self` is not the same as passing it on."""
    engine = RiskEngine(is_backtest=True)

    assert engine.drawdown_guard.max_daily_loss_pct == distinctive_config["max_daily_loss_pct"]
    assert engine.drawdown_guard.max_total_drawdown_pct == distinctive_config["max_drawdown_pct"]

    assert engine.exposure_manager.max_open_positions == distinctive_config["max_open_positions"]
    assert engine.exposure_manager.max_symbol_positions == distinctive_config["max_symbol_positions"]
    assert engine.exposure_manager.max_portfolio_risk_pct == distinctive_config["max_portfolio_risk_pct"]

    assert engine.portfolio_heat_engine.max_daily_loss_pct == distinctive_config["max_daily_loss_pct"]
    assert engine.portfolio_heat_engine.max_open_positions == distinctive_config["max_open_positions"]
    assert engine.portfolio_heat_engine.max_portfolio_risk_pct == distinctive_config["max_portfolio_risk_pct"]


def test_the_config_file_really_is_what_gets_loaded():
    """Guards the loader, not the dataclass defaults.

    Reads the shipped file and requires each engine-owned key it declares to be
    reflected in ``SETTINGS.risk``. If the loader stops reading the file — or a key
    is renamed on one side only — this fails even though every other test passes.
    """
    raw = json.loads((Path(REPO_ROOT) / "config" / "settings.json").read_text(encoding="utf-8"))
    declared = raw.get("risk", {})

    checked = 0
    for key in ENGINE_LIMITS:
        if key in declared:
            assert getattr(SETTINGS.risk, key) == declared[key], key
            checked += 1
    assert checked > 0, (
        "config/settings.json declares none of the limits RiskEngine consumes — "
        "the file and the engine have drifted apart again"
    )


# ─── An explicit argument still wins ──────────────────────────────────────────

def test_explicit_arguments_beat_the_config(distinctive_config):
    engine = RiskEngine(
        max_daily_loss_pct=9.0,
        max_drawdown_pct=9.5,
        max_open_positions=9,
        max_symbol_positions=8,
        max_risk_per_trade_pct=0.9,
        max_portfolio_risk_pct=4.5,
        is_backtest=True,
    )
    assert engine.max_daily_loss_pct == 9.0
    assert engine.max_drawdown_pct == 9.5
    assert engine.max_open_positions == 9
    assert engine.max_symbol_positions == 8
    assert engine.max_risk_per_trade_pct == 0.9
    assert engine.max_portfolio_risk_pct == 4.5
    assert engine.drawdown_guard.max_daily_loss_pct == 9.0
    assert engine.drawdown_guard.max_total_drawdown_pct == 9.5
    assert engine.exposure_manager.max_open_positions == 9


def test_a_partial_override_does_not_reset_the_other_limits(distinctive_config):
    """The old signature made every unpassed argument fall back to a literal."""
    engine = RiskEngine(max_open_positions=9, is_backtest=True)
    assert engine.max_open_positions == 9
    assert engine.max_daily_loss_pct == distinctive_config["max_daily_loss_pct"]
    assert engine.max_drawdown_pct == distinctive_config["max_drawdown_pct"]
    assert engine.max_symbol_positions == distinctive_config["max_symbol_positions"]

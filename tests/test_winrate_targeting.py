"""
Tests for the win-rate targeting stack.

Covers the pieces that a "75% win rate" claim actually depends on:

  * conservative intrabar ordering in the simulator (a bar that spans both the
    stop and the target must be booked as a LOSS — the previous engine resolved
    this optimistically and inflated win rate);
  * geometry/threshold separation (thresholds must not change a simulated path);
  * non-overlapping sequential selection;
  * isotonic calibration monotonicity;
  * the learned regime policy switching off regimes with no edge;
  * profile serialisation round-trip;
  * calibrated entry policy ordering (capital protection before edge filter);
  * hermetic mode (backtests must not read or write live state).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from jarvis.backtesting.trade_simulator import (
    BarArrays,
    Geometry,
    evaluate_geometry,
    select_sequential,
    simulate_all_candidates,
    simulate_trade,
    summarise,
)
from jarvis.config.runtime import is_offline, offline_mode, set_offline
from jarvis.execution.entry_policy import (
    CAPITAL_PROTECTION_GATES,
    capital_protection_failures,
    evaluate_entry,
)
from jarvis.intelligence.winrate_targeting import (
    RegimeEdge,
    WRTargetProfile,
    default_geometry_grid,
    isotonic_calibrate,
    regime_edge_table,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def make_bars(highs, lows, closes, atr=None):
    n = len(highs)
    return BarArrays(
        high=np.array(highs, dtype=float),
        low=np.array(lows, dtype=float),
        close=np.array(closes, dtype=float),
        atr=np.array(atr if atr is not None else [1.0] * n, dtype=float),
        time=np.arange(n),
        n=n,
    )


def make_df(highs, lows, closes):
    return pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=len(highs), freq="1h", tz="UTC"),
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "atr": [1.0] * len(highs),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Simulator
# ─────────────────────────────────────────────────────────────────────────────
def test_stop_tested_before_target_when_bar_spans_both():
    """A bar whose range covers both the stop and the target is a LOSS.

    Entry 100, stop 99 (1R = 1.0), target at tp_r=1.0 => 101. The next bar
    ranges 98..102, spanning both. Conservative convention => stop first.
    """
    bars = make_bars(
        highs=[100.0, 102.0], lows=[100.0, 98.0], closes=[100.0, 100.0]
    )
    out = simulate_trade(
        symbol="TEST", side="BUY", entry_idx=0, fill=100.0, sl=99.0,
        geom=Geometry(tp_r=1.0, be_trigger_r=None, fast_cash_r=None, max_bars=10),
        money_per_unit=100_000.0, bars=bars,
    )
    assert out is not None
    assert out.result == "SL"
    assert out.pnl_r < 0
    assert out.is_win is False


def test_target_hit_when_stop_not_touched():
    bars = make_bars(
        highs=[100.0, 101.5], lows=[100.0, 99.9], closes=[100.0, 101.4]
    )
    out = simulate_trade(
        symbol="TEST", side="BUY", entry_idx=0, fill=100.0, sl=99.0,
        geom=Geometry(tp_r=1.0, be_trigger_r=None, fast_cash_r=None, max_bars=10),
        money_per_unit=100_000.0, bars=bars,
    )
    assert out is not None
    assert out.result == "TP"
    assert out.pnl_r == pytest.approx(1.0, abs=1e-6)
    assert out.is_win is True


def test_sell_direction_symmetry():
    bars = make_bars(
        highs=[100.0, 100.1], lows=[100.0, 98.5], closes=[100.0, 98.6]
    )
    out = simulate_trade(
        symbol="TEST", side="SELL", entry_idx=0, fill=100.0, sl=101.0,
        geom=Geometry(tp_r=1.0, be_trigger_r=None, fast_cash_r=None, max_bars=10),
        money_per_unit=100_000.0, bars=bars,
    )
    assert out is not None
    assert out.result == "TP"
    assert out.pnl_r == pytest.approx(1.0, abs=1e-6)


def test_time_stop_closes_at_close():
    bars = make_bars(
        highs=[100.0, 100.2, 100.3, 100.1],
        lows=[100.0, 99.9, 99.8, 99.9],
        closes=[100.0, 100.1, 100.2, 100.05],
    )
    out = simulate_trade(
        symbol="TEST", side="BUY", entry_idx=0, fill=100.0, sl=99.0,
        geom=Geometry(tp_r=5.0, be_trigger_r=None, fast_cash_r=None, max_bars=3),
        money_per_unit=100_000.0, bars=bars,
    )
    assert out is not None
    assert out.result == "TIME_STOP_3B"


def test_cost_reduces_pnl_by_exactly_cost_over_risk():
    bars = make_bars(
        highs=[100.0, 101.5], lows=[100.0, 99.9], closes=[100.0, 101.4]
    )
    geom = Geometry(tp_r=1.0, be_trigger_r=None, fast_cash_r=None, max_bars=10)
    free = simulate_trade(symbol="T", side="BUY", entry_idx=0, fill=100.0, sl=99.0,
                          geom=geom, money_per_unit=100_000.0, bars=bars,
                          cost_price_equiv=0.0)
    paid = simulate_trade(symbol="T", side="BUY", entry_idx=0, fill=100.0, sl=99.0,
                          geom=geom, money_per_unit=100_000.0, bars=bars,
                          cost_price_equiv=0.10)
    assert free is not None and paid is not None
    assert free.pnl_r - paid.pnl_r == pytest.approx(0.10, abs=1e-6)


def test_degenerate_risk_returns_none():
    bars = make_bars(highs=[100.0, 101.0], lows=[100.0, 99.0], closes=[100.0, 100.0])
    assert simulate_trade(symbol="T", side="BUY", entry_idx=0, fill=100.0, sl=100.0,
                          geom=Geometry(), money_per_unit=100_000.0, bars=bars) is None


def test_stop_slippage_deepens_loss_by_exactly_slippage_over_risk():
    """The simulator must model the same stop slippage BacktestEngine charges.

    Without it, breakeven scratches the engine closes as small losses are booked
    as 0R, and OOS expectancy over-promises the edge.
    """
    bars = make_bars(highs=[100.0, 98.5], lows=[100.0, 98.5], closes=[100.0, 99.0])
    base = simulate_trade(symbol="T", side="BUY", entry_idx=0, fill=100.0, sl=99.0,
                          geom=Geometry(tp_r=5.0), money_per_unit=100_000.0, bars=bars)
    slipped = simulate_trade(symbol="T", side="BUY", entry_idx=0, fill=100.0, sl=99.0,
                             geom=Geometry(tp_r=5.0), money_per_unit=100_000.0, bars=bars,
                             slippage_price_equiv=0.1)
    assert base is not None and slipped is not None
    assert base.result == "SL" and slipped.result == "SL"
    # risk_dist = 1.0, so 0.1 slippage deepens the loss by exactly 0.1R.
    assert slipped.pnl_r == pytest.approx(base.pnl_r - 0.1, abs=1e-6)


def test_slippage_does_not_affect_target_win():
    bars = make_bars(highs=[100.0, 101.5], lows=[100.0, 99.9], closes=[100.0, 101.4])
    base = simulate_trade(symbol="T", side="BUY", entry_idx=0, fill=100.0, sl=99.0,
                          geom=Geometry(tp_r=1.0), money_per_unit=100_000.0, bars=bars)
    slipped = simulate_trade(symbol="T", side="BUY", entry_idx=0, fill=100.0, sl=99.0,
                             geom=Geometry(tp_r=1.0), money_per_unit=100_000.0, bars=bars,
                             slippage_price_equiv=0.1)
    assert base.result == "TP" and slipped.result == "TP"
    assert slipped.pnl_r == pytest.approx(base.pnl_r, abs=1e-6)


def test_slippage_applies_to_sell_stop_too():
    bars = make_bars(highs=[100.0, 101.5], lows=[100.0, 101.5], closes=[100.0, 101.0])
    base = simulate_trade(symbol="T", side="SELL", entry_idx=0, fill=100.0, sl=101.0,
                          geom=Geometry(tp_r=5.0), money_per_unit=100_000.0, bars=bars)
    slipped = simulate_trade(symbol="T", side="SELL", entry_idx=0, fill=100.0, sl=101.0,
                             geom=Geometry(tp_r=5.0), money_per_unit=100_000.0, bars=bars,
                             slippage_price_equiv=0.1)
    assert base is not None and slipped is not None
    assert base.result == "SL" and slipped.result == "SL"
    # SELL stop is above entry; slippage pushes the exit higher (worse) by 0.1R.
    assert slipped.pnl_r == pytest.approx(base.pnl_r - 0.1, abs=1e-6)


# ─────────────────────────────────────────────────────────────────────────────
# Geometry / threshold separation
# ─────────────────────────────────────────────────────────────────────────────
def _candidates(symbol="TEST", n=6):
    return pd.DataFrame({
        "symbol": [symbol] * n,
        "bar_idx": list(range(n)),
        "side": ["BUY"] * n,
        "fill": [100.0] * n,
        "sl": [99.0] * n,
        "score": [0.2, 0.4, 0.6, 0.8, 0.9, 0.95][:n],
        "regime": ["TREND"] * n,
        "strategy": ["S"] * n,
        "zone": ["DISCOUNT"] * n,
    })


def test_threshold_does_not_change_simulated_path():
    """Simulating all candidates must be independent of min_score.

    The whole calibration speed-up rests on this: geometry determines the path,
    the threshold only filters afterwards.
    """
    bars = make_bars(
        highs=[100.0, 101.5, 100.0, 101.5, 100.0, 101.5, 100.0, 101.5],
        lows=[100.0, 99.9, 100.0, 99.9, 100.0, 99.9, 100.0, 99.9],
        closes=[100.0, 101.4, 100.0, 101.4, 100.0, 101.4, 100.0, 101.4],
    )
    df = make_df(bars.high.tolist(), bars.low.tolist(), bars.close.tolist())
    cands = _candidates()
    g_low = Geometry(tp_r=1.0, be_trigger_r=None, fast_cash_r=None, max_bars=5, min_score=0.0)
    g_high = Geometry(tp_r=1.0, be_trigger_r=None, fast_cash_r=None, max_bars=5, min_score=0.9)
    a = simulate_all_candidates(df=df, candidates=cands, geom=g_low,
                                money_per_unit=100_000.0, symbol="TEST")
    b = simulate_all_candidates(df=df, candidates=cands, geom=g_high,
                                money_per_unit=100_000.0, symbol="TEST")
    assert [o.entry_idx for o in a] == [o.entry_idx for o in b]
    assert [o.pnl_r for o in a] == [o.pnl_r for o in b]


def test_select_sequential_is_non_overlapping():
    bars = make_bars(
        highs=[100.0] * 12, lows=[100.0] * 12, closes=[100.0] * 12
    )
    df = make_df(bars.high.tolist(), bars.low.tolist(), bars.close.tolist())
    cands = _candidates(n=6)
    geom = Geometry(tp_r=5.0, be_trigger_r=None, fast_cash_r=None, max_bars=3)
    outs = evaluate_geometry(df=df, candidates=cands, geom=geom,
                             money_per_unit=100_000.0, symbol="TEST")
    assert len(outs) >= 2
    for prev, nxt in zip(outs, outs[1:]):
        assert nxt.entry_idx > prev.exit_idx


def test_select_sequential_respects_score_threshold():
    bars = make_bars(highs=[100.0] * 12, lows=[100.0] * 12, closes=[100.0] * 12)
    df = make_df(bars.high.tolist(), bars.low.tolist(), bars.close.tolist())
    cands = _candidates(n=6)
    geom = Geometry(tp_r=5.0, be_trigger_r=None, fast_cash_r=None, max_bars=3)
    all_out = simulate_all_candidates(df=df, candidates=cands, geom=geom,
                                     money_per_unit=100_000.0, symbol="TEST")
    high = select_sequential(all_out, min_score=0.9)
    assert all(o.ai_score >= 0.9 for o in high)
    assert len(high) <= len(all_out)


def test_select_sequential_regime_filter_frees_the_slot():
    """A trade blocked by the regime policy must not keep its slot.

    The engine applies the regime policy *before* choosing a position, so a
    blocked trade leaves the slot free for a later eligible trade. Filtering
    after the walk would drop that later trade too and understate the system —
    exactly the mismatch that made calibration (192 OOS trades) disagree with
    the engine (7 trades) for EURUSD.
    """
    import dataclasses

    bars = make_bars(highs=[100.0] * 12, lows=[100.0] * 12, closes=[100.0] * 12)
    df = make_df(bars.high.tolist(), bars.low.tolist(), bars.close.tolist())
    cands = _candidates(n=6)
    geom = Geometry(tp_r=5.0, be_trigger_r=None, fast_cash_r=None, max_bars=3)
    outs = simulate_all_candidates(df=df, candidates=cands, geom=geom,
                                   money_per_unit=100_000.0, symbol="TEST")
    assert len(outs) >= 3, "need at least three sequential trades for this test"

    blocked = outs[0]
    labelled = [
        dataclasses.replace(o, regime="DEAD") if o.entry_idx == blocked.entry_idx else o
        for o in outs
    ]

    all_on = select_sequential(labelled)
    filtered = select_sequential(labelled, regimes={"TREND"})

    assert blocked.entry_idx in [o.entry_idx for o in all_on]
    assert blocked.entry_idx not in [o.entry_idx for o in filtered]
    # Removing one trade must not cascade: at most one trade is lost, and the
    # slot is taken by a later eligible entry rather than left empty.
    assert len(filtered) >= len(all_on) - 1
    assert filtered, "filter must not empty a sample that has eligible regimes"
    assert filtered[0].entry_idx > blocked.entry_idx


def test_select_sequential_regime_filter_none_means_no_filtering():
    bars = make_bars(highs=[100.0] * 12, lows=[100.0] * 12, closes=[100.0] * 12)
    df = make_df(bars.high.tolist(), bars.low.tolist(), bars.close.tolist())
    cands = _candidates(n=6)
    geom = Geometry(tp_r=5.0, be_trigger_r=None, fast_cash_r=None, max_bars=3)
    outs = simulate_all_candidates(df=df, candidates=cands, geom=geom,
                                   money_per_unit=100_000.0, symbol="TEST")
    assert [o.entry_idx for o in select_sequential(outs, regimes=None)] == \
           [o.entry_idx for o in select_sequential(outs)]


def test_summarise_win_rate_and_expectancy():
    bars = make_bars(
        highs=[100.0, 101.5, 100.0, 101.5],
        lows=[100.0, 99.9, 100.0, 99.9],
        closes=[100.0, 101.4, 100.0, 101.4],
    )
    df = make_df(bars.high.tolist(), bars.low.tolist(), bars.close.tolist())
    cands = _candidates(n=2)
    geom = Geometry(tp_r=1.0, be_trigger_r=None, fast_cash_r=None, max_bars=1)
    outs = evaluate_geometry(df=df, candidates=cands, geom=geom,
                             money_per_unit=100_000.0, symbol="TEST")
    s = summarise(outs)
    assert s["trades"] == len(outs)
    assert 0.0 <= s["win_rate"] <= 1.0
    assert s["expectancy_r"] == pytest.approx(sum(o.pnl_r for o in outs) / len(outs), abs=1e-6)


def test_summarise_empty():
    s = summarise([])
    assert s["trades"] == 0 and s["win_rate"] == 0.0 and s["expectancy_r"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Geometry contract
# ─────────────────────────────────────────────────────────────────────────────
def test_geometry_disabled_features_are_unreachable():
    g = Geometry(tp_r=0.5, be_trigger_r=None, fast_cash_r=None, trail_atr=None)
    p = g.to_policy("TEST", None)
    assert p.be_trigger_r >= 1e9
    assert p.fast_cash_r >= 1e9
    assert p.trail_activation_r >= 1e9
    assert p.milestones == []


def test_geometry_key_distinguishes_thresholds():
    a = Geometry(tp_r=1.0, min_score=0.0)
    b = Geometry(tp_r=1.0, min_score=0.5)
    assert a.key() != b.key()


def test_geometry_roundtrip():
    g = Geometry(tp_r=0.75, be_trigger_r=1.0, fast_cash_r=0.5, max_bars=24, min_score=0.6)
    assert Geometry.from_dict(g.to_dict()) == g


def test_grid_includes_short_and_long_time_stops():
    grid = default_geometry_grid(coarse=True)
    assert {g.max_bars for g in grid} == {24, 48}
    assert len({g.key() for g in grid}) == len(grid)


# ─────────────────────────────────────────────────────────────────────────────
# Calibration primitives
# ─────────────────────────────────────────────────────────────────────────────
def test_isotonic_calibration_is_monotone():
    scores = [0.1, 0.15, 0.2, 0.4, 0.45, 0.5, 0.8, 0.85, 0.9, 0.95]
    wins = [0, 0, 1, 1, 0, 1, 1, 1, 1, 1]
    cal = isotonic_calibrate(scores, wins, n_bins=5)
    assert cal.bin_win_rate == sorted(cal.bin_win_rate)
    assert cal.n == len(scores)


def test_isotonic_calibration_predict_is_monotone():
    cal = isotonic_calibrate([0.1, 0.3, 0.5, 0.7, 0.9], [0, 1, 0, 1, 1], n_bins=4)
    preds = [cal.predict(x) for x in [0.05, 0.2, 0.4, 0.6, 0.8, 1.0]]
    assert preds == sorted(preds)


def test_isotonic_calibration_handles_empty_and_constant():
    assert isotonic_calibrate([], []).n == 0
    const = isotonic_calibrate([0.5, 0.5, 0.5], [1, 1, 0])
    assert const.n == 3


def test_regime_edge_disables_negative_expectancy():
    from jarvis.backtesting.trade_simulator import TradeOutcome

    def mk(regime, pnl_r):
        return TradeOutcome(
            symbol="T", side="BUY", entry_idx=0, entry_time=0, exit_idx=1,
            exit_time=1, bars_held=1, entry=100.0, exit=100.0 + pnl_r,
            sl_initial=99.0, sl_final=99.0, tp=101.0, risk_dist=1.0,
            result="X", mfe_r=0.0, mae_r=0.0, pnl_r=pnl_r,
            pnl_money_per_lot=pnl_r, is_win=pnl_r > 0,
            partial_taken=False, be_locked=False, regime=regime,
        )

    outs = [mk("GOOD", 1.0)] * 15 + [mk("BAD", -1.0)] * 15
    table = regime_edge_table(outs, min_trades=5)
    assert table["GOOD"].enabled is True
    assert table["BAD"].enabled is False
    assert "worse than" in table["BAD"].reason


def test_regime_edge_does_not_disable_on_marginal_negative():
    """A marginal negative expectancy must NOT switch a regime off.

    An early version disabled gold's dominant regime on -0.011R, removing 490 of
    ~1,500 bars for no demonstrated reason.
    """
    from jarvis.backtesting.trade_simulator import TradeOutcome

    def mk(regime, pnl_r):
        return TradeOutcome(
            symbol="T", side="BUY", entry_idx=0, entry_time=0, exit_idx=1,
            exit_time=1, bars_held=1, entry=100.0, exit=100.0 + pnl_r,
            sl_initial=99.0, sl_final=99.0, tp=101.0, risk_dist=1.0,
            result="X", mfe_r=0.0, mae_r=0.0, pnl_r=pnl_r,
            pnl_money_per_lot=pnl_r, is_win=pnl_r > 0,
            partial_taken=False, be_locked=False, regime=regime,
        )

    outs = [mk("MARGINAL", -0.011)] * 40
    table = regime_edge_table(outs, min_trades=5, disable_margin_r=0.05)
    assert table["MARGINAL"].enabled is True
    assert "within tolerance" in table["MARGINAL"].reason


def test_regime_edge_leaves_small_samples_enabled_even_if_negative():
    from jarvis.backtesting.trade_simulator import TradeOutcome

    o = TradeOutcome(
        symbol="T", side="BUY", entry_idx=0, entry_time=0, exit_idx=1, exit_time=1,
        bars_held=1, entry=100.0, exit=99.0, sl_initial=99.0, sl_final=99.0,
        tp=101.0, risk_dist=1.0, result="X", mfe_r=0.0, mae_r=1.0, pnl_r=-1.0,
        pnl_money_per_lot=-1.0, is_win=False, partial_taken=False, be_locked=False,
        regime="THIN",
    )
    table = regime_edge_table([o] * 8, min_trades=5, min_trades_to_disable=12)
    assert table["THIN"].enabled is True
    assert "too few to disable" in table["THIN"].reason


def test_regime_edge_leaves_thin_samples_enabled():
    from jarvis.backtesting.trade_simulator import TradeOutcome

    o = TradeOutcome(
        symbol="T", side="BUY", entry_idx=0, entry_time=0, exit_idx=1, exit_time=1,
        bars_held=1, entry=100.0, exit=99.0, sl_initial=99.0, sl_final=99.0,
        tp=101.0, risk_dist=1.0, result="X", mfe_r=0.0, mae_r=1.0, pnl_r=-1.0,
        pnl_money_per_lot=-1.0, is_win=False, partial_taken=False, be_locked=False,
        regime="RARE",
    )
    table = regime_edge_table([o], min_trades=5)
    assert table["RARE"].enabled is True


def test_profile_roundtrip():
    p = WRTargetProfile(
        symbol="TEST", target_wr=0.75,
        geometry=Geometry(tp_r=0.5, be_trigger_r=None, fast_cash_r=None, max_bars=24, min_score=0.7),
        score_col="score", n_trades=30, win_rate=0.76, expectancy_r=0.15,
        oos_trades=25, oos_win_rate=0.72, oos_expectancy_r=0.09,
        score_calibration=isotonic_calibrate([0.5, 0.6, 0.7, 0.8], [0, 1, 1, 1]),
        regime_edge={"TREND": RegimeEdge("TREND", 10, 0.7, 0.2, 2.0, True, "ok")},
        regime_geometry={"TREND": Geometry(tp_r=1.5)},
    )
    p2 = WRTargetProfile.from_dict(p.to_dict())
    assert p2.symbol == p.symbol
    assert p2.geometry == p.geometry
    assert p2.regime_edge["TREND"].enabled is True
    assert p2.regime_geometry["TREND"].tp_r == 1.5
    assert p2.score_calibration is not None


def test_profile_geometry_for_regime_override():
    p = WRTargetProfile(
        symbol="T", target_wr=0.75, geometry=Geometry(tp_r=0.5),
        regime_geometry={"TREND": Geometry(tp_r=1.5)},
    )
    assert p.geometry_for("TREND").tp_r == 1.5
    assert p.geometry_for("UNKNOWN").tp_r == 0.5
    assert p.effective_target_r("TREND") == 1.5


# ─────────────────────────────────────────────────────────────────────────────
# Entry policy
# ─────────────────────────────────────────────────────────────────────────────
class _Gate:
    def __init__(self, checks, passed=True):
        self.checks = checks
        self.passed = passed
        self.failing_reasons = [k for k, v in checks.items() if not v]


class _Profile:
    def __init__(self, min_score=0.0, regime_edge=None,
                 oos_expectancy_r=0.0, oos_trades=0):
        self.geometry = Geometry(tp_r=0.5, min_score=min_score)
        self.regime_edge = regime_edge or {}
        # Purged out-of-sample expectancy produced by WRTargetCalibrator.
        # The entry gate refuses to trade a symbol whose validated OOS edge
        # is non-positive (the root cause of the negative portfolio expectancy).
        self.oos_expectancy_r = oos_expectancy_r
        self.oos_trades = oos_trades


def test_capital_protection_checked_before_edge_filter():
    gate = _Gate({"Market Session Open": False, "Drawdown Safety Guard": True})
    dec = evaluate_entry(quality_gate=gate, score=0.99, regime="TREND",
                         profile=_Profile(min_score=0.0))
    assert dec.allowed is False
    assert "capital protection" in dec.reason
    assert "Market Session Open" in dec.protection_failures


def test_capital_protection_set_is_protective_only():
    for g in CAPITAL_PROTECTION_GATES:
        assert not any(k in g for k in ("Score", "Reward", "Expected Value", "Devil"))


def test_edge_threshold_blocks_low_score():
    gate = _Gate({g: True for g in CAPITAL_PROTECTION_GATES})
    dec = evaluate_entry(quality_gate=gate, score=0.4, regime="TREND",
                         profile=_Profile(min_score=0.7))
    assert dec.allowed is False
    assert "below calibrated threshold" in dec.reason


def test_disabled_regime_blocks_even_with_high_score():
    gate = _Gate({g: True for g in CAPITAL_PROTECTION_GATES})
    edge = {"TREND": RegimeEdge("TREND", 10, 0.3, -0.2, -2.0, False, "no edge")}
    dec = evaluate_entry(quality_gate=gate, score=0.99, regime="TREND",
                         profile=_Profile(min_score=0.5, regime_edge=edge))
    assert dec.allowed is False
    assert "disabled by learned policy" in dec.reason


def test_entry_allowed_when_all_clear():
    gate = _Gate({g: True for g in CAPITAL_PROTECTION_GATES})
    dec = evaluate_entry(quality_gate=gate, score=0.9, regime="TREND",
                         profile=_Profile(min_score=0.5))
    assert dec.allowed is True


def test_oos_negative_expectancy_refused():
    """A symbol whose validated OOS expectancy is non-positive must be refused.

    This is the regression guard for the negative-portfolio-expectancy bug:
    six symbols (EURUSD, USDCHF, NZDUSD, USDJPY, SOLUSD, US30) deployed with a
    positive in-sample expectancy that decayed to a negative OOS expectancy,
    and the engine traded them anyway because nothing checked the OOS number.
    """
    gate = _Gate({g: True for g in CAPITAL_PROTECTION_GATES})
    dec = evaluate_entry(
        quality_gate=gate, score=0.99, regime="TREND",
        profile=_Profile(min_score=0.5, oos_expectancy_r=-0.042, oos_trades=40),
    )
    assert dec.allowed is False
    assert "no validated edge" in dec.reason
    assert "OOS expectancy" in dec.reason


def test_oos_positive_expectancy_allowed_past_gate():
    """A positive validated OOS expectancy must not trip the refusal gate.

    The symbol should still fall through to the normal edge gate, so a high
    enough score clears it.
    """
    gate = _Gate({g: True for g in CAPITAL_PROTECTION_GATES})
    dec = evaluate_entry(
        quality_gate=gate, score=0.95, regime="TREND",
        profile=_Profile(min_score=0.5, oos_expectancy_r=0.09, oos_trades=25),
    )
    assert dec.allowed is True
    assert "calibrated edge filter passed" in dec.reason


def test_oos_negative_but_thin_sample_not_refused():
    """A negative OOS expectancy with too few OOS trades must NOT be refused.

    The gate only refuses when ``oos_trades >= 10`` so it cannot reject a
    symbol on noise. A thin/missing purged-fold sample falls through to the
    normal edge gate rather than refusing on an untrustworthy number.
    """
    gate = _Gate({g: True for g in CAPITAL_PROTECTION_GATES})
    dec = evaluate_entry(
        quality_gate=gate, score=0.95, regime="TREND",
        profile=_Profile(min_score=0.5, oos_expectancy_r=-0.5, oos_trades=3),
    )
    # Not refused by the OOS gate — falls through to the edge filter and passes.
    assert dec.allowed is True
    assert "no validated edge" not in dec.reason


def test_no_profile_falls_back_to_legacy_verdict():
    gate = _Gate({g: True for g in CAPITAL_PROTECTION_GATES}, passed=True)
    assert evaluate_entry(quality_gate=gate, score=0.1, regime="X", profile=None).allowed is True
    gate2 = _Gate({g: True for g in CAPITAL_PROTECTION_GATES}, passed=False)
    assert evaluate_entry(quality_gate=gate2, score=0.1, regime="X", profile=None).allowed is False


def test_capital_protection_failures_handles_missing_checks():
    assert capital_protection_failures(None) == ()
    assert capital_protection_failures(object()) == ()


# ─────────────────────────────────────────────────────────────────────────────
# Hermetic mode
# ─────────────────────────────────────────────────────────────────────────────
def test_offline_mode_toggles_and_restores():
    original = is_offline()
    with offline_mode():
        assert is_offline() is True
    assert is_offline() == original


def test_offline_mode_nests():
    with offline_mode():
        with offline_mode():
            assert is_offline() is True
        assert is_offline() is True
    assert is_offline() is False


def test_realtime_optimizer_is_neutral_offline():
    from jarvis.intelligence.realtime_optimizer import RealtimeOptimizer

    opt = RealtimeOptimizer()
    with offline_mode():
        adj = opt.get_adjustments("EURUSD", "TREND_BULL")
    assert adj == {"win_p_delta": 0.0, "score_delta": 0.0, "rr_delta": 0.0}


def test_online_ml_predictor_does_not_write_offline(tmp_path, monkeypatch):
    from jarvis.learning.online_ml_predictor import OnlineMLPredictor

    p = OnlineMLPredictor()
    target = tmp_path / "weights.json"
    monkeypatch.setattr(p, "model_file", str(target), raising=False)
    with offline_mode():
        p._save_model()
    assert not target.exists()


def test_meta_labeler_is_neutral_offline():
    from jarvis.intelligence.meta_labeler import MetaLabeler

    ml = MetaLabeler()
    with offline_mode():
        assert ml.model is None


def test_set_offline_roundtrip():
    set_offline(True)
    assert is_offline() is True
    set_offline(False)
    assert is_offline() is False

"""Coverage for the measurement spine the P0/P1 verdict tools are built on.

``tools/audit_trade_quality.py`` (``metrics``, ``dynamic_regimes``, ``replay``)
and ``tools/p0_1_direction_audit.forced_long_benchmark`` are imported by four
separate P0 tools — the direction audit, the gate measurement, the half-split
stability check and the exit-geometry sweep — and none of them had a test.

That matters more than the line count suggests: **two errors in these helpers
have already changed a published verdict.** A biased always-long control moved
the survivor count from 1/20 to 3/20, and a units error (MT5 spread is in
*points*, not pips) put a 10x cost on half the audited trades. A wrong number
here does not fail loudly — it prints a plausible table.

Every value below is derived by hand from the documented rule, not copied from
a tool run.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from jarvis.backtesting.trade_simulator import Geometry
from tools import p0_1_direction_audit as p0dir
from tools.audit_trade_quality import (
    breakeven_wr,
    dynamic_regimes,
    load_symbol,
    metrics,
    replay,
)

SYM = "EURUSD"
PIP = 0.0001          # resolve('EURUSD').pip_size
RISK_PCT = 1.0


# ── fixtures ─────────────────────────────────────────────────────────────────
def ramp_bars(n=40, start=1.1000, step=0.0002, atr=0.0010):
    """A quiet, steadily rising series: a long trade resolves on the target."""
    close = start + step * np.arange(n, dtype=float)
    return pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close,
        "high": close + 0.0004,
        "low": close - 0.0004,
        "close": close,
        "atr": np.full(n, atr),
    })


def flat_bars(n=20, close=1.1000, atrs=None):
    """Flat prices with a per-bar ATR, so vol buckets are separable and the
    trend measure is exactly zero."""
    atr = np.asarray(atrs if atrs is not None else [0.001 * (k + 1) for k in range(n)],
                     dtype=float)
    if len(atr) < n:
        atr = np.pad(atr, (0, n - len(atr)), constant_values=atr[-1])
    return pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": np.full(n, close),
        "high": np.full(n, close) + atr,
        "low": np.full(n, close) - atr,
        "close": np.full(n, close),
        "atr": atr[:n],
    })


def geom(**kw):
    """Everything that would confound attribution switched off."""
    base = dict(tp_r=1.5, be_trigger_r=None, fast_cash_r=None,
                trail_atr=None, max_bars=200, min_score=0.0)
    base.update(kw)
    return Geometry(**base)


# ── metrics: the function that prints the verdict ────────────────────────────
def test_metrics_matches_hand_computed_values():
    r = np.array([1.0, -1.0, 2.0, -0.5])
    m = metrics(r, RISK_PCT)
    # cumulative [1.0, 0.0, 2.0, 1.5]; running peak [1, 1, 2, 2]; dd max = 1.0
    sd = float(r.std(ddof=1))
    assert m["n"] == 4
    assert m["win_rate"] == pytest.approx(0.5)
    assert m["profit_factor"] == pytest.approx(3.0 / 1.5)      # gp 3.0 / gl 1.5
    assert m["expectancy_r"] == pytest.approx(1.5 / 4)
    assert m["expectancy_pct"] == pytest.approx(1.5 / 4 * RISK_PCT)
    assert m["net_r"] == pytest.approx(1.5)
    assert m["max_dd_r"] == pytest.approx(1.0)
    assert m["max_dd_pct"] == pytest.approx(1.0 * RISK_PCT)
    assert m["t_stat"] == pytest.approx((1.5 / 4) / (sd / 2.0), rel=1e-9)
    assert m["avg_win_r"] == pytest.approx(1.5)                # (1.0 + 2.0) / 2
    assert m["avg_loss_r"] == pytest.approx(-0.75)             # (-1.0 + -0.5) / 2


def test_metrics_counts_a_zero_r_scratch_as_not_a_win():
    """0.0R is not a win. `wins` is `> 0` and `losses` is `<= 0`, so a scratch
    lands in the loss bucket: it drags the win rate down and is counted in the
    average loss, while contributing nothing to the gross loss."""
    m = metrics(np.array([0.0, 1.0, -1.0]), RISK_PCT)
    assert m["win_rate"] == pytest.approx(1.0 / 3.0)
    assert m["profit_factor"] == pytest.approx(1.0)     # gp 1.0 / gl 1.0
    # The scratch is inside `losses`, so it halves the average loss to -0.5R.
    assert m["avg_loss_r"] == pytest.approx(-0.5)
    assert m["avg_win_r"] == pytest.approx(1.0)


def test_metrics_all_wins_caps_the_profit_factor():
    m = metrics(np.array([1.0, 2.0]), RISK_PCT)
    assert m["profit_factor"] == pytest.approx(99.0)
    assert m["avg_loss_r"] == 0.0


def test_metrics_all_losses_reports_zero_profit_factor():
    m = metrics(np.array([-1.0, -2.0]), RISK_PCT)
    assert m["profit_factor"] == pytest.approx(0.0)
    assert m["win_rate"] == pytest.approx(0.0)


def test_metrics_empty_series_returns_only_the_count():
    assert metrics(np.array([]), RISK_PCT) == {"n": 0}


def test_metrics_single_trade_has_no_t_statistic():
    """With one observation the sample sd is undefined; the guard must not
    divide by zero and must not invent a significant t."""
    m = metrics(np.array([1.0]), RISK_PCT)
    assert m["t_stat"] == 0.0
    assert m["max_dd_r"] == 0.0     # a single winner never draws down


def test_breakeven_win_rate_is_one_over_one_plus_target():
    assert breakeven_wr(1.0) == pytest.approx(0.5)
    assert breakeven_wr(2.0) == pytest.approx(1.0 / 3.0)


# ── dynamic_regimes ─────────────────────────────────────────────────────────
def test_vol_buckets_are_the_candidates_own_terciles():
    """Nine distinct ATR% values split 3/3/3. The cut-offs are the sample's own
    33.3rd and 66.7th percentiles, so 'high vol' means the top third of *this*
    symbol's range — not a fixed number."""
    df = flat_bars(n=20)
    cands = pd.DataFrame({"bar_idx": range(9)})
    out = dynamic_regimes(df, cands)
    assert list(out["vol_bucket"]) == (["LOW_VOL"] * 3 + ["MID_VOL"] * 3 + ["HIGH_VOL"] * 3)


def test_atr_pct_is_the_bars_own_atr_over_its_close():
    df = flat_bars(n=20, close=1.1000, atrs=[0.001 * (k + 1) for k in range(20)])
    cands = pd.DataFrame({"bar_idx": [0, 5, 19]})
    out = dynamic_regimes(df, cands)
    assert out["atr_pct"].tolist() == pytest.approx(
        [0.001 / 1.1, 0.006 / 1.1, 0.020 / 1.1])


def test_bar_idx_past_the_end_is_clipped_not_dropped():
    """A stale manifest can hand over a bar_idx beyond the loaded window.
    Clipping keeps the row (and its trade) instead of silently losing it."""
    df = flat_bars(n=20)
    out = dynamic_regimes(df, pd.DataFrame({"bar_idx": [999]}))
    assert len(out) == 1
    assert out["atr_pct"].iloc[0] == pytest.approx(0.020 / 1.1)   # last bar


def test_flat_prices_are_all_ranging():
    """The trend measure is |EMA20 - EMA60| in ATR units: dimensionless, and
    exactly zero when price has not gone anywhere."""
    df = flat_bars(n=20)
    out = dynamic_regimes(df, pd.DataFrame({"bar_idx": range(5)}))
    assert (out["trend_norm"] == 0.0).all()
    assert (out["trend_bucket"] == "RANGING").all()


def test_too_few_candidates_falls_back_to_the_middle_bucket():
    """With fewer than three points a tercile is meaningless, so each bucket
    takes its OWN middle label — never the literal "MID", which belongs to
    neither domain and would surface as a phantom group key in the reports
    that group by vol_bucket."""
    df = flat_bars(n=20)
    out = dynamic_regimes(df, pd.DataFrame({"bar_idx": [0, 1]}))
    assert (out["vol_bucket"] == "MID_VOL").all()
    assert (out["trend_bucket"] == "TRANSITIONAL").all()
    assert set(out["vol_bucket"]) <= {"LOW_VOL", "MID_VOL", "HIGH_VOL"}
    assert set(out["trend_bucket"]) <= {"RANGING", "TRANSITIONAL", "TRENDING"}


def test_trend_strength_is_unsigned():
    """|EMA20 - EMA60| in ATR units. The sign is discarded on purpose: 'how
    strongly is this trending' must not depend on which way it points, or a
    falling market would sort into the bottom tercile next to flat ones."""
    n = 40
    falling = ramp_bars(n=n, start=1.2000, step=-0.0002)
    rising = ramp_bars(n=n, start=1.0000, step=0.0002)
    cands = pd.DataFrame({"bar_idx": [n - 5]})
    down = float(dynamic_regimes(falling, cands)["trend_norm"].iloc[0])
    up = float(dynamic_regimes(rising, cands)["trend_norm"].iloc[0])
    assert down > 0.0 and up > 0.0
    assert down == pytest.approx(up, rel=1e-6)


def test_regimes_do_not_mutate_the_candidates_frame():
    df = flat_bars(n=20)
    cands = pd.DataFrame({"bar_idx": range(9)})
    dynamic_regimes(df, cands)
    assert "atr_pct" not in cands.columns


# ── replay ──────────────────────────────────────────────────────────────────
@pytest.fixture
def capture_replay(monkeypatch):
    """Same idea as capture_entries, for the replay path.

    Counting the calls is the point, not a convenience: `simulate_trade`
    returns None for a row it cannot resolve, and replay drops None results —
    so 'the row is missing from the output' cannot distinguish 'the guard
    filtered it' from 'the simulator happened to give up on it'. Only the call
    count can."""
    import tools.audit_trade_quality as atq

    seen = []
    real = atq.simulate_trade

    def recorder(**kw):
        seen.append(dict(kw))
        return real(**kw)

    monkeypatch.setattr(atq, "simulate_trade", recorder)
    return seen


def test_replay_never_simulates_a_candidate_on_the_last_bar(capture_replay):
    """There is no next bar to fill into, so the trade is not merely absent
    from the output — it is never simulated at all."""
    df = ramp_bars(n=20)
    cands = pd.DataFrame({"bar_idx": [19], "side": ["BUY"],
                          "fill": [1.1000], "sl": [1.0980]})
    assert replay(SYM, df, cands, geom(), 0.0, 0.0) is None
    assert capture_replay == []


def test_replay_never_simulates_a_side_that_is_not_a_direction(capture_replay):
    df = ramp_bars(n=20)
    cands = pd.DataFrame({"bar_idx": [5, 6], "side": ["HOLD", ""],
                          "fill": [1.1000, 1.1000], "sl": [1.0980, 1.0980]})
    assert replay(SYM, df, cands, geom(), 0.0, 0.0) is None
    assert capture_replay == []


def test_replay_keeps_only_the_tradeable_rows():
    df = ramp_bars(n=20)
    cands = pd.DataFrame({
        "bar_idx": [5, 6, 19],
        "side": ["BUY", "SELL", "BUY"],
        "fill": [1.1000, 1.1000, 1.1000],
        "sl": [1.0980, 1.1020, 1.0980],
    })
    tr = replay(SYM, df, cands, geom(), 0.0, 0.0)
    assert tr is not None and len(tr) == 2
    assert sorted(tr["side"]) == ["BUY", "SELL"]


def test_replay_reports_the_simulators_own_result():
    """The row's pnl_r is whatever the simulator produced — replay must not
    recompute, round or rescale it."""
    df = ramp_bars(n=40)
    cands = pd.DataFrame({"bar_idx": [5], "side": ["BUY"],
                          "fill": [1.1000], "sl": [1.0980]})
    tr = replay(SYM, df, cands, geom(tp_r=1.5), 0.0, 0.0)
    assert tr is not None and len(tr) == 1
    # risk 0.0020, target 1.5R -> +1.5R on a clean run to the target
    assert tr["pnl_r"].iloc[0] == pytest.approx(1.5)
    assert tr["result"].iloc[0] == "TP"
    assert tr["bar_idx"].iloc[0] == 5


def test_replay_carries_the_labelled_buckets_through():
    df = ramp_bars(n=40)
    cands = pd.DataFrame({"bar_idx": [5], "side": ["BUY"],
                          "fill": [1.1000], "sl": [1.0980],
                          "vol_bucket": ["HIGH_VOL"], "trend_bucket": ["TRENDING"]})
    tr = replay(SYM, df, cands, geom(), 0.0, 0.0)
    assert tr["vol_bucket"].iloc[0] == "HIGH_VOL"
    assert tr["trend_bucket"].iloc[0] == "TRENDING"


# ── forced_long_benchmark: the control that was already wrong once ───────────
@pytest.fixture
def capture_entries(monkeypatch):
    """Record what the control actually hands the simulator, then delegate to
    the real one so the trade still resolves."""
    seen = []
    real = p0dir.simulate_trade

    def recorder(**kw):
        seen.append(dict(kw))
        return real(**kw)

    monkeypatch.setattr(p0dir, "simulate_trade", recorder)
    return seen


def test_a_sell_row_is_entered_long_at_the_ask(capture_entries):
    """The regression this file exists for.

    A SELL candidate's stored `fill` is the BID (open - spread). Reusing it as a
    long entry hands the control a free half-spread, biasing the comparison
    against the gate by ~0.05R a trade on a 2.3-pip FX spread — the same order
    as the effects being measured. The long entry must be the ask:
    fill + 2 x spread.
    """
    df = ramp_bars(n=40)
    cands = pd.DataFrame({"bar_idx": [5], "side": ["SELL"],
                          "fill": [1.1000], "sl": [1.1020], "spread_pips": [1.5]})
    forced = p0dir.forced_long_benchmark(SYM, df, cands, geom(), 0.0)
    assert forced is not None and len(forced) == 1

    kw = capture_entries[0]
    assert kw["side"] == "BUY"
    assert kw["fill"] == pytest.approx(1.1000 + 2 * 1.5 * PIP)   # 1.10030
    # Same risk distance as the gate's own trade, re-anchored below the ask.
    assert kw["sl"] == pytest.approx(1.10030 - 0.0020)           # 1.09830


def test_a_buy_row_is_entered_long_at_its_own_fill(capture_entries):
    """A BUY's stored fill is already the ask, so it needs no adjustment — and
    adjusting it would be a double charge."""
    df = ramp_bars(n=40)
    cands = pd.DataFrame({"bar_idx": [5], "side": ["BUY"],
                          "fill": [1.1000], "sl": [1.0980], "spread_pips": [1.5]})
    p0dir.forced_long_benchmark(SYM, df, cands, geom(), 0.0)
    assert capture_entries[0]["fill"] == pytest.approx(1.1000)
    assert capture_entries[0]["sl"] == pytest.approx(1.0980)


def test_the_control_preserves_the_gates_risk_distance(capture_entries):
    """Only the direction is fixed. If the control's risk distance differed,
    its R multiples would not be comparable to the gate's."""
    df = ramp_bars(n=40)
    for side, sl in (("BUY", 1.0950), ("SELL", 1.1050)):
        capture_entries.clear()
        cands = pd.DataFrame({"bar_idx": [5], "side": [side],
                              "fill": [1.1000], "sl": [sl], "spread_pips": [2.0]})
        p0dir.forced_long_benchmark(SYM, df, cands, geom(), 0.0)
        entry, stop = capture_entries[0]["fill"], capture_entries[0]["sl"]
        assert abs(entry - stop) == pytest.approx(0.0050)
        # And on the correct side: a long's stop is BELOW its entry. Placing it
        # the same distance above would read as the same risk distance while
        # reversing what the trade is.
        assert stop < entry


def test_a_zero_risk_row_is_skipped(capture_entries):
    """fill == sl means R is undefined; dividing by it would produce inf. It is
    never simulated — not merely dropped after the fact."""
    df = ramp_bars(n=40)
    cands = pd.DataFrame({"bar_idx": [5], "side": ["BUY"],
                          "fill": [1.1000], "sl": [1.1000]})
    assert p0dir.forced_long_benchmark(SYM, df, cands, geom(), 0.0) is None
    assert capture_entries == []


def test_a_candidate_past_the_last_bar_is_skipped(capture_entries):
    df = ramp_bars(n=40)
    cands = pd.DataFrame({"bar_idx": [39], "side": ["BUY"],
                          "fill": [1.1000], "sl": [1.0980]})
    assert p0dir.forced_long_benchmark(SYM, df, cands, geom(), 0.0) is None
    assert capture_entries == []


def test_without_a_spread_figure_the_stored_fill_is_used(capture_entries):
    """A missing or non-finite spread must not silently become zero pips of
    adjustment *and* must not raise — it falls back to the stored fill."""
    df = ramp_bars(n=40)
    for spread in (None, float("nan")):
        capture_entries.clear()
        cands = pd.DataFrame({"bar_idx": [5], "side": ["SELL"],
                              "fill": [1.1000], "sl": [1.1020], "spread_pips": [spread]})
        p0dir.forced_long_benchmark(SYM, df, cands, geom(), 0.0)
        assert capture_entries[0]["fill"] == pytest.approx(1.1000)


def test_the_control_pays_slippage_but_no_commission(capture_entries):
    """Deliberate, and worth pinning: the control is charged the same slippage
    the gate is, and zero commission. It is the least flattering choice for the
    gate's own numbers, so a win against it is a real one."""
    df = ramp_bars(n=40)
    cands = pd.DataFrame({"bar_idx": [5], "side": ["SELL"],
                          "fill": [1.1000], "sl": [1.1020], "spread_pips": [1.5]})
    p0dir.forced_long_benchmark(SYM, df, cands, geom(), 0.00002)
    assert capture_entries[0]["cost_price_equiv"] == 0.0
    assert capture_entries[0]["slippage_price_equiv"] == pytest.approx(0.00002)


def test_the_control_returns_one_r_per_surviving_candidate():
    df = ramp_bars(n=40)
    cands = pd.DataFrame({
        "bar_idx": [5, 8, 39],          # the last has no next bar
        "side": ["SELL", "BUY", "BUY"],
        "fill": [1.1000, 1.1000, 1.1000],
        "sl": [1.1020, 1.0980, 1.0980],
        "spread_pips": [1.5, 1.5, 1.5],
    })
    out = p0dir.forced_long_benchmark(SYM, df, cands, geom(), 0.0)
    assert out is not None and len(out) == 2
    assert np.isfinite(out).all()


# ── load_symbol ─────────────────────────────────────────────────────────────
def test_load_symbol_reports_a_miss_instead_of_raising():
    assert load_symbol("NO_SUCH_SYMBOL_XYZ", "H1", 183) == (None, None)

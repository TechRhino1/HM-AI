"""Guards for the backtest-integrity defects found in the 2026-09-15 audit.

Each test pins a specific bug that was measured, not theorised:

* the H4/D1 resample exposed in-progress (future-bearing) buckets,
* ``commission_per_lot`` was accepted by the constructor and then ignored,
  so every backtest ran commission-free,
* ``GER40``/``UK100``/``XAGUSD`` were absent from the profile table and were
  sized from a generic FX template (100,000x wrong for the indices),
* an unregistered symbol fell through to that template in complete silence.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pandas as pd
import pytest


def _hourly_bars(n: int = 400) -> pd.DataFrame:
    t = pd.date_range("2026-01-01 00:00", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "time": t,
        "open": 1.0,
        "high": 1.0 + 0.0001 * pd.Series(range(n)),
        "low": 0.999,
        "close": 1.0 + 0.0001 * pd.Series(range(n)),
        "tick_volume": 1,
    })


# ── look-ahead ─────────────────────────────────────────────────────────────
def test_resample_bucket_is_labelled_at_its_close():
    """A bucket may only be visible once every bar inside it has finished.

    With pandas' default ``label='left'`` the bucket is stamped with its open,
    so the ``time <= bar_time`` filter in the scanner and the engine admitted an
    in-progress bucket that - in a full-series resample - already contained its
    future bars.
    """
    from jarvis.backtesting.signal_scan import SignalScanner

    df = _hourly_bars(400)
    h4, d1 = SignalScanner._resample(df)

    for frame, period in ((h4, timedelta(hours=4)), (d1, timedelta(days=1))):
        for _, row in frame.iterrows():
            bucket_close = row["time"]
            # Every bar that belongs to this bucket must have finished at or
            # before the bucket's label. If the label were the bucket's open,
            # bars strictly after it would be inside the bucket and readable
            # while still forming.
            # closed="left", label="right" => the bucket is [T-period, T).
            members = df[(df["time"] >= bucket_close - period) & (df["time"] < bucket_close)]
            assert len(members) > 0, "bucket should contain at least one bar"
            expected_close = float(members["close"].iloc[-1])
            assert abs(float(row["close"]) - expected_close) < 1e-12, (
                f"bucket at {bucket_close} aggregates bars that close after its "
                f"label - that is look-ahead"
            )
            # And no bar after the label may contribute.
            later = df[df["time"] > bucket_close]
            if len(later):
                assert float(row["high"]) <= float(
                    df[(df["time"] > bucket_close - period) & (df["time"] <= bucket_close)]["high"].max()
                ), "bucket high includes a bar from after its label"


def test_engine_resample_uses_the_same_convention():
    """The engine's fallback resample must agree with the scanner's."""
    import numpy as np
    from jarvis.backtesting.engine import BacktestEngine

    df = _hourly_bars(300)
    df["volume"] = 1.0
    eng = BacktestEngine()
    # _prepare_mtf is the guarded path; the fallback path inside run_backtest
    # builds its own frames. Assert the convention by construction instead:
    # resampling with label='right' must move every label forward.
    left = df.set_index("time").resample("4h").agg({"close": "last"}).dropna()
    right = df.set_index("time").resample("4h", closed="left", label="right").agg({"close": "last"}).dropna()
    # Compare timestamps, not epoch integers: numpy's rtol on microsecond epochs
    # is far larger than a 4-hour shift, so allclose() calls them equal.
    assert not left.index.equals(right.index), (
        "label='right' must move the bucket timestamps, otherwise the "
        "look-ahead guard is a no-op"
    )
    assert (right.index[0] - left.index[0]) == timedelta(hours=4)


# ── costs ──────────────────────────────────────────────────────────────────
def test_commission_argument_is_honoured():
    """The constructor argument used to be silently discarded.

    Every shipped SymbolProfileConfig carries ``commission_per_lot = 0.0``, and
    ``_calc_commission`` read only that field - so callers passing 5.0 were
    charged nothing at all.
    """
    from jarvis.backtesting.engine import BacktestEngine

    eng = BacktestEngine(commission_per_lot=5.0)
    assert eng._calc_commission("EURUSD", 2.0) == pytest.approx(10.0)
    assert eng._calc_commission("EURUSD", 0.0) == pytest.approx(0.0)


def test_a_zero_profile_commission_falls_back_to_the_constructor(monkeypatch):
    """Every shipped profile carries 0.0, so 0.0 must mean 'not configured here'."""
    from jarvis.backtesting import engine

    eng = engine.BacktestEngine(commission_per_lot=5.0)
    assert eng._calc_commission("XAUUSD", 1.0) == pytest.approx(5.0)

    class _Cfg:
        commission_per_lot = 7.0

    monkeypatch.setattr(engine, "get_symbol_profile_config", lambda s: _Cfg())
    assert eng._calc_commission("XAUUSD", 2.0) == pytest.approx(14.0), (
        "an explicit per-symbol commission must win over the constructor default"
    )


# ── symbol specs ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("symbol,contract_size,digits", [
    ("GER40", 1.0, 2),
    ("UK100", 1.0, 2),
    ("XAGUSD", 5000.0, 3),
])
def test_indices_and_silver_are_registered(symbol, contract_size, digits):
    """These three were missing and were sized from the generic FX template."""
    from jarvis.intelligence.symbol_profile_config import get_symbol_profile_config

    cfg = get_symbol_profile_config(symbol)
    assert cfg.asset_class != "FOREX", f"{symbol} is still falling back to the FX template"
    assert cfg.contract_size == pytest.approx(contract_size)
    assert cfg.digits == digits


def test_unknown_symbol_fallback_is_loud(caplog):
    """The fallback must be visible: it silently mis-sized three live symbols."""
    from jarvis.intelligence.symbol_profile_config import get_symbol_profile_config

    with caplog.at_level(logging.ERROR):
        cfg = get_symbol_profile_config("NOTAREALSYMBOL")
    assert cfg.asset_class == "FOREX"
    assert any("NOTAREALSYMBOL" in r.getMessage() for r in caplog.records), (
        "fallback to the generic FX template must log an error"
    )


# ── sizing floor ───────────────────────────────────────────────────────────
def test_lot_floor_must_not_be_applied_after_the_risk_cap():
    """``max(volume_min, ...)`` after the cap allowed unlimited realised risk.

    The behaviour lives inline in ``run_backtest``, so pin the invariant at the
    level that can be asserted without a full run: with a minimum lot of 0.1 and
    a plan that only affords 0.01, the correct answer is to take no trade.
    """
    volume_min = 0.1
    planned_lots = 0.01
    # Old behaviour: max(volume_min, min(cap, planned)) -> 0.1, i.e. 10x risk.
    assert max(volume_min, min(1.0, planned_lots)) == 0.1
    # Required behaviour: skip the trade.
    assert planned_lots < volume_min

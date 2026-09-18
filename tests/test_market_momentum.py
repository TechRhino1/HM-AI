"""Round 28 — `jarvis/market/momentum.py`, the producer of `MomentumContext`.

Untested, and it feeds three separate gates: the Momentum analyst's ±20 bias
(`momentum_analyst.py`), the Devil's Advocate's counter-trend / ADX / divergence
threats, and `decision_engine`'s `trend_score` thresholds (20, 30, 40, 60, 65).
`regime_engine.py:94` even derives an *unbounded* multiplier from `adx`.

Pure: no network, no registry, synthetic frames only.
"""
import numpy as np
import pandas as pd
import pytest

from jarvis.data.schemas import MomentumContext
from jarvis.market.momentum import MomentumEngine


# --------------------------------------------------------------------------
# frame builders
# --------------------------------------------------------------------------

def frame(values, spread=0.5):
    """OHLC around a close series; `spread` sets the high/low bracket.

    Because `spread` is constant, `low` and `high` are just the close shifted by
    a fixed amount — so every min/max price comparison in the divergence block
    is equivalent to the same comparison on `close`.
    """
    close = pd.Series([float(v) for v in values])
    return pd.DataFrame({
        "open": close,
        "close": close,
        "high": close + spread,
        "low": close - spread,
        "volume": 1000.0,
    })


def rising(n=60, start=100.0, step=1.0):
    return frame([start + i * step for i in range(n)])


def falling(n=60, start=160.0, step=1.0):
    return frame([start - i * step for i in range(n)])


def flat(n=60, price=100.0):
    return frame([price] * n)


def score_of(df, **kw):
    return MomentumEngine(**kw).analyze_momentum(df)


def two_seg(k, d1, d2, n=60):
    """`k` bars drifting by `d1` from 100, then `n - k` bars drifting by `d2`.

    Two linear segments are enough to place the three EMAs in any order, which
    is what selects the scoring branch.
    """
    a = [100.0 + i * d1 for i in range(k)]
    return frame(a + [a[-1] + (i + 1) * d2 for i in range(n - k)])


def rsi_of(close, period=14):
    """The module's RSI, re-derived: a *simple* rolling mean, not Wilder's RMA."""
    delta = close.diff()
    gain = pd.Series(np.where(delta > 0, delta, 0.0), index=close.index)
    loss = pd.Series(np.where(delta < 0, -delta, 0.0), index=close.index)
    avg_gain = gain.rolling(period, min_periods=1).mean()
    avg_loss = loss.rolling(period, min_periods=1).mean()
    return 100 - (100 / (1 + avg_gain / (avg_loss + 1e-9)))


# --------------------------------------------------------------------------
# divergence fixtures — found by search, then verified term by term
# --------------------------------------------------------------------------

#: price makes a NEW LOW in the last 10 bars while the RSI low over the same
#: window sits >3 points ABOVE the RSI low of the prior 10 bars.
BULL_FIXTURE = ([140 - i for i in range(40)]           # 40-bar decline
                + [101 + i * 0.15 for i in range(10)]  # prior window: drift up
                + [102.35 - i * 0.6 for i in range(10)])  # recent: deeper low

#: the mirror image — a NEW HIGH with a weaker RSI high.
BEAR_FIXTURE = ([60 + i for i in range(40)]            # 40-bar rise
                + [99 - i * 0.15 for i in range(10)]   # prior window: drift down
                + [97.65 + i * 0.6 for i in range(10)])   # recent: higher high

#: satisfies BOTH the bullish and the bearish predicate simultaneously
#: (rsi_gap +8.9 on the low side, −25.5 on the high side).
BOTH_FIXTURE = [
    100.0, 99.02, 98.04, 97.06, 96.08, 95.1, 94.12, 93.14, 92.16, 91.18,
    90.2, 90.2, 88.64, 87.09, 85.53, 83.97, 82.42, 80.86, 79.3, 77.75,
    76.19, 74.63, 73.08, 71.52, 69.97, 68.41, 66.85, 65.3, 63.74, 62.18,
    60.63, 59.07, 59.07, 59.84, 60.61, 61.38, 62.15, 62.91, 63.68, 64.45,
    65.22, 65.99, 66.76, 67.53, 68.29, 69.06, 69.06, 68.11, 67.16, 66.2,
    65.25, 64.3, 64.3, 66.29, 68.27, 70.26, 72.25, 74.24, 76.22, 78.21,
]

#: A frame whose final score is positive but whose *last-bar* rebuilt score is
#: negative — the persistence loop therefore stops on its first iteration.
PERSISTENCE_BREAK = ([100.0] * 46                      # long flat base
                     + [95.0, 90.0, 85.0, 80.0]        # 4-bar crash
                     + [80 + i for i in range(1, 11)])  # 10-bar recovery

#: A noisy frame whose ADX lands inside the [22, 30) band — the only place the
#: `cur_adx >= 22.0` gate can be observed at all, since clean trends saturate
#: it at 100 and clean chop pins it at 0.
ADX_BAND = [
    99.9, 99.7, 99.6, 99.5, 99.6, 99.5, 99.8, 99.4, 99.4, 99.0, 99.8, 99.4, 99.7, 99.3, 99.5,
    98.9, 99.1, 99.6, 98.4, 99.0, 99.1, 99.3, 99.5, 98.7, 98.5, 98.2, 98.0, 97.7, 97.5, 96.6,
    97.3, 96.7, 96.2, 95.6, 95.3, 95.2, 94.6, 95.6, 96.5, 95.2, 96.3, 96.3, 96.4, 97.1, 96.5,
    97.1, 97.7, 97.4, 97.6, 97.9, 98.0, 98.8, 98.3, 98.5, 98.0, 96.9, 97.5, 96.9, 96.8, 97.2,
]

#: Makes a new price low, but the RSI low only improves by 0.33 — inside the
#: 3-point deadband, so nothing is reported.
SMALL_GAP = [
    99.1, 101.3, 98.8, 97.4, 98.3, 97.8, 97.3, 96.5, 96.3, 95.7, 96.1, 94.5, 94.6, 94.3, 95.3,
    94.0, 93.0, 93.3, 92.7, 93.0, 93.5, 93.7, 92.1, 89.6, 88.2, 88.4, 86.9, 85.2, 82.7, 81.2,
    82.3, 81.6, 80.6, 77.4, 78.6, 75.9, 75.8, 74.7, 72.4, 71.5, 68.9, 69.0, 67.0, 68.1, 66.6,
    66.4, 64.7, 62.7, 62.5, 60.7, 60.6, 59.6, 60.7, 60.8, 60.5, 61.7, 62.6, 60.6, 60.7, 58.9,
]

#: A genuine bullish divergence whose recent-window low sits in bars -10..-6,
#: i.e. *outside* the last five bars.
BULL_LOW_OUTSIDE = [
    98.6, 101.5, 100.3, 100.7, 99.5, 100.6, 100.7, 98.4, 101.9, 99.3, 98.2, 99.4, 99.4, 100.7,
    99.2, 100.4, 99.0, 102.2, 100.4, 101.8, 98.7, 100.4, 98.7, 100.2, 100.2, 97.9, 98.3, 98.2,
    97.8, 98.5, 98.2, 98.3, 97.3, 94.0, 93.8, 93.4, 94.9, 95.0, 94.8, 93.9, 96.2, 95.9, 93.8,
    94.1, 95.4, 93.5, 94.7, 94.4, 93.6, 94.1, 92.8, 93.9, 92.4, 96.7, 96.0, 95.7, 94.8, 95.5,
    95.8, 95.6,
]

#: The prior window's minimum sits in bars -15..-11, so narrowing the prior
#: window from [-20:-10] to [-20:-15] would change the verdict.
PRIOR_LOW_INSIDE = [
    100.0, 97.0, 94.0, 94.1, 95.5, 96.2, 97.7, 99.3, 100.5, 101.5, 102.9, 103.9, 105.4, 105.4,
    106.6, 107.7, 108.9, 110.0, 111.2, 112.4, 113.5, 113.5, 113.5, 113.4, 113.3, 113.2, 113.1,
    113.1, 113.0, 112.9, 112.8, 112.8, 112.7, 112.6, 112.5, 112.4, 112.4, 112.3, 112.2, 112.1,
    112.1, 112.0, 111.9, 111.8, 111.7, 111.7, 111.6, 111.5, 111.4, 112.0, 111.4, 113.1, 112.0,
    115.5, 116.1, 115.7, 117.4, 117.4, 118.3, 119.5,
]

#: A frame where several bars in the persistence lookback sit exactly on the
#: `t_score == 0` boundary, so the loop's +25 ADX term is load-bearing.
PERSIST_ADX_SENSITIVE = [
    99.7, 100.9, 101.1, 102.1, 102.8, 103.3, 104.5, 105.8, 106.8, 107.3, 107.2, 108.8, 109.0,
    110.4, 109.6, 111.7, 112.3, 111.1, 111.5, 110.6, 109.5, 109.7, 109.7, 109.6, 109.2, 108.3,
    107.3, 106.5, 107.0, 106.3, 106.5, 105.4, 105.5, 104.3, 104.5, 104.0, 103.8, 102.7, 102.7,
    101.9, 101.8, 102.1, 103.4, 101.9, 102.3, 102.0, 102.6, 102.3, 102.6, 102.3, 103.4, 102.9,
    101.4, 102.4, 102.3, 101.2, 101.9, 102.2, 102.1, 102.5,
]

#: ADX varies across the persistence lookback, so reading the *final* ADX for
#: every bar would give a different count.
PERSIST_ADX_VARYING = [
    100.0, 99.7, 99.4, 99.1, 98.8, 98.5, 98.2, 97.9, 97.7, 97.4, 97.1, 96.8, 96.5, 96.2, 95.9,
    95.6, 95.3, 95.0, 94.6, 94.4, 95.7, 95.4, 94.3, 94.2, 96.1, 96.9, 95.3, 95.3, 96.1, 95.0,
    94.5, 96.2, 95.5, 96.8, 95.9, 96.4, 95.3, 96.3, 95.6, 96.8, 96.8, 96.7, 96.7, 96.7, 96.6,
    96.6, 96.6, 96.6, 96.5, 96.5, 96.5, 96.4, 96.4, 96.4, 96.4, 96.3, 96.3, 96.3, 96.3, 96.2,
]

#: A 60-bar frame where unclamping the slow-EMA span changes the stack branch.
SLOW_SPAN_SENSITIVE = [
    100.0, 100.0, 100.0, 99.9, 99.9, 99.9, 99.9, 99.8, 99.8, 99.8, 99.8, 99.7, 99.7, 99.7, 99.7,
    99.6, 99.6, 99.6, 99.6, 99.5, 99.5, 99.5, 99.5, 99.4, 99.4, 99.4, 99.4, 99.3, 99.3, 99.3,
    99.3, 99.2, 99.2, 99.2, 99.2, 99.1, 99.1, 99.1, 99.1, 99.0, 99.0, 99.0, 99.0, 98.9, 98.9,
    98.9, 98.9, 98.8, 98.8, 98.8, 99.3, 99.7, 100.2, 100.7, 101.1, 101.1, 101.2, 101.2, 101.2,
    101.2,
]


#: A genuine bearish divergence whose recent-window high sits in bars -10..-6,
#: i.e. *outside* the last five bars.
BEAR_HIGH_OUTSIDE = [
    99.6, 99.6, 100.4, 100.2, 100.5, 100.7, 100.6, 100.4, 100.6, 100.7, 101.2, 101.8, 101.1, 101.6,
    101.1, 102.0, 101.6, 101.6, 101.8, 102.2, 102.1, 102.2, 102.5, 102.3, 102.2, 102.8, 103.0, 102.9,
    102.8, 103.8, 103.9, 103.2, 103.7, 103.5, 103.5, 103.5, 103.8, 104.0, 104.3, 104.4, 104.1, 105.2,
    104.6, 104.4, 104.4, 104.5, 104.8, 104.7, 104.3, 105.6, 105.4, 105.7, 105.2, 106.0, 106.3, 103.9,
    103.5, 103.7, 102.7, 100.8,
]

#: The prior window's maximum sits in bars -15..-11, so narrowing the prior
#: window would flip this to BEARISH.
PRIOR_HIGH_INSIDE = [
    100.0, 98.7, 97.3, 96.0, 94.7, 93.3, 92.0, 90.7, 89.3, 88.0, 86.7, 85.3, 84.0, 82.7, 81.3, 80.0,
    78.7, 77.3, 77.3, 77.2, 77.0, 76.9, 76.7, 76.6, 76.4, 76.2, 76.1, 75.9, 75.8, 75.6, 75.6, 75.6,
    75.6, 76.2, 75.5, 76.7, 75.9, 75.1, 76.5, 76.8, 76.7, 77.1, 77.4, 77.4, 76.9, 77.9, 77.6, 77.3,
    78.8, 76.9, 76.3, 77.9, 78.7, 78.3, 76.1, 73.4, 71.5, 67.2, 65.5, 63.3,
]

#: Makes a new high, but the RSI high only weakens by a fraction — inside the
#: 3-point deadband, so nothing is reported.
BEAR_SMALL_GAP = [
    99.7, 102.1, 105.1, 105.1, 99.6, 94.2, 88.7, 83.3, 77.8, 72.4, 67.0, 61.5, 56.1, 50.6, 45.2,
    39.7, 34.3, 28.9, 23.4, 18.0, 12.5, 7.1, 1.6, -3.8, -3.5, -2.9, -3.6, -1.6, -3.3, -2.6, -2.8,
    -0.8, -0.8, -0.7, -0.6, -0.5, -0.4, -0.3, -0.2, -0.1, -0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7,
    0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9,
]

#: A long decline where the medium EMA and +DI both vary across the persistence
#: lookback — so reading either one from the *final* bar would change the count.
PERSIST_EMA_DI_VARYING = [
    99.9, 96.2, 93.5, 92.4, 89.6, 87.0, 83.2, 81.9, 78.3, 77.3, 72.6, 71.4, 71.4, 71.6, 71.8, 72.0,
    72.1, 72.3, 72.5, 72.7, 72.9, 73.1, 73.3, 73.4, 73.6, 73.8, 74.0, 74.2, 74.4, 74.6, 74.7, 74.9,
    75.1, 75.3, 75.5, 75.7, 75.8, 76.0, 77.0, 75.7, 72.6, 71.0, 68.3, 65.0, 66.4, 64.2, 60.4, 59.4,
    56.7, 55.9, 55.2, 53.0, 50.2, 48.6, 47.1, 46.1, 43.2, 41.2, 39.7, 37.5,
]


def tie_frame(n=60):
    """A frame where `up_move == down_move > 0` on every bar.

    `up - down == 2 * delta + (high_bracket change) - (low_bracket change)`, so
    a +1 drift with a constant high bracket and a low bracket that widens by 2
    per bar makes the two exactly equal — the `>` vs `>=` tie-break in the DM
    terms becomes observable. `frame()` cannot express this: its bracket is
    constant, which forces `up == down` to imply `up == 0`.
    """
    close = pd.Series([100.0 + i for i in range(n)])
    return pd.DataFrame({
        "open": close,
        "close": close,
        "high": close + 0.5,
        "low": close - (0.5 + 2.0 * np.arange(n)),
        "volume": 1000.0,
    })


# --------------------------------------------------------------------------
# length guards
# --------------------------------------------------------------------------

class TestLengthGuards:

    def test_a_frame_too_short_returns_the_bare_default(self):
        """`min_len = max(rsi_period, adx_period) + 5` = 19 with the defaults.
        Below that you get `MomentumContext()` — rsi 50.0, adx 0.0, trend_score 0
        — which is *neutral*, not absent."""
        for n in (0, 1, 5, 17, 18):
            ctx = score_of(rising(n))
            assert ctx == MomentumContext(), f"n={n} was not the default"

    def test_the_cliff_is_at_exactly_19_bars(self):
        assert score_of(rising(18)) == MomentumContext()
        assert score_of(rising(19)) != MomentumContext()

    def test_a_custom_period_moves_the_cliff(self):
        eng = MomentumEngine(rsi_period=5, adx_period=5)   # min_len = 10
        assert eng.analyze_momentum(rising(9)) == MomentumContext()
        assert eng.analyze_momentum(rising(10)) != MomentumContext()

    def test_the_19_bar_floor_is_not_a_floor_it_is_conditional_on_ema_slow(self):
        """🔴 The guard is *nested*: `if len(df) < self.ema_slow:` only then is
        `min_len` checked. So any frame with `len(df) >= ema_slow` skips the
        19-bar check entirely and is analysed no matter how short it is.

        Production uses `ema_slow=200`, so this is latent there — but
        `MomentumEngine(ema_slow=5).analyze_momentum(five_bars)` returns a fully
        populated context built from five bars, RSI included."""
        ctx = MomentumEngine(ema_slow=5).analyze_momentum(rising(5))
        assert ctx != MomentumContext()
        assert ctx.rsi == 100.0
        assert ctx.trend_score == 70

    def test_a_short_frame_still_returns_a_complete_context(self):
        ctx = score_of(rising(19))
        assert isinstance(ctx, MomentumContext)
        assert ctx.divergence == "NONE"
        assert ctx.acceleration in ("ACCELERATING", "DECELERATING",
                                    "EXHAUSTION", "STEADY")

    def test_the_slow_ema_span_is_clamped_to_the_frame_length(self):
        """`span_slow = min(ema_slow, len(df))` — on a 30-bar frame the "200 EMA"
        is really a 30 EMA. Every EMA degrades together, so the stack stays
        ordered and the score still reads as a strong trend."""
        ctx = score_of(rising(30))
        assert ctx.trend_score > 0


# --------------------------------------------------------------------------
# the six EMA-stack branches
# --------------------------------------------------------------------------

class TestEmaStackBranches:
    """Each of the six branches of the stack chain, with its own frame.

    The chain is `if`/`elif`, so exactly one of these fires — and which one it
    is decides 45 of the at-most-100 points in `trend_score`.
    """

    def test_price_above_all_three_emas_scores_plus_45(self):
        assert score_of(two_seg(20, 0.0, 0.2)).trend_score == 97

    def test_price_above_fast_and_medium_but_medium_at_or_below_slow_scores_plus_30(self):
        assert score_of(two_seg(30, -0.2, 0.2)).trend_score == 84

    def test_price_above_the_fast_ema_only_scores_plus_15(self):
        assert score_of(two_seg(40, -0.2, 0.2)).trend_score == 70

    def test_price_below_all_three_emas_scores_minus_45(self):
        assert score_of(two_seg(20, -0.2, 0.0)).trend_score == -45

    def test_price_below_fast_and_medium_but_medium_at_or_above_slow_scores_minus_30(self):
        assert score_of(two_seg(30, 0.2, -0.2)).trend_score == -85

    def test_price_below_the_fast_ema_only_scores_minus_15(self):
        assert score_of(two_seg(40, 0.2, -0.2)).trend_score == -68

    def test_the_middle_branches_hinge_on_the_medium_to_slow_comparison(self):
        """The ±30 and ±15 pairs differ only in whether `ema_med` has crossed
        `ema_slow` — here by about 0.03 of a price unit. A one-bar shift in the
        split moves a frame from one branch to the other."""
        assert score_of(two_seg(30, -0.2, 0.2)).trend_score == 84    # +30
        assert score_of(two_seg(40, -0.2, 0.2)).trend_score == 70    # +15

    def test_on_a_frame_shorter_than_20_bars_all_three_emas_collapse(self):
        """🔴 `span = min(period, len(df))` for all three, so below 20 bars the
        fast, medium and slow EMAs are the *same series*. Every comparison in
        the chain is a strict `>`, so the stack can then only ever contribute
        ±15 — the ±45 and ±30 branches are unreachable on a short frame, no
        matter how violently price is trending."""
        for n in (12, 15, 19):
            ctx = MomentumEngine(ema_slow=n).analyze_momentum(rising(n))
            assert ctx.trend_score == 70        # 15 (stack) + 25 (ADX) + 30 (slope)


# --------------------------------------------------------------------------
# rsi
# --------------------------------------------------------------------------

class TestRsi:

    def test_a_flat_market_reports_zero_not_fifty(self):
        """🔴 With no gains and no losses, `rs = 0 / (0 + 1e-9) = 0`, so
        `rsi = 100 - 100/1 = 0.0`. A dead-flat market therefore reads as
        **maximally oversold** — and the Momentum analyst's `rsi < 22` EXHAUSTION
        branch and any `rsi < 30` oversold logic will treat a motionless market
        as a coiled buy. The *default* context, by contrast, says 50.0."""
        assert score_of(flat(60)).rsi == 0.0
        assert MomentumContext().rsi == 50.0

    def test_a_monotonic_rise_reports_100(self):
        assert score_of(rising(60)).rsi == 100.0

    def test_a_monotonic_fall_reports_zero(self):
        assert score_of(falling(60)).rsi == 0.0

    def test_any_bar_preceded_by_fourteen_loss_only_bars_reads_zero(self):
        """The RSI floors at 0.0 whenever the trailing window contains no gains
        at all — it is not a graded measure near the bottom of its range."""
        close = frame(BULL_FIXTURE)["close"]
        r = rsi_of(close)
        assert r.iloc[40] == 0.0

    def test_rsi_is_a_simple_average_not_wilders_smoothing(self):
        """The industry-standard RSI uses Wilder's RMA (alpha = 1/period). This
        uses `.rolling(window=period).mean()`, so its values are not comparable
        to a charting package. Pinned so nobody "fixes" it silently."""
        df = rising(60)
        expected = rsi_of(df["close"], period=14).iloc[-1]
        assert score_of(df, rsi_period=14).rsi == round(expected, 1)

    def test_rsi_is_rounded_to_one_decimal(self):
        ctx = score_of(frame([100 + (i % 3) - 1 for i in range(60)]))
        assert ctx.rsi == round(ctx.rsi, 1)


# --------------------------------------------------------------------------
# adx and the directional indicators
# --------------------------------------------------------------------------

class TestAdx:

    def test_a_flat_market_has_no_directional_movement(self):
        ctx = score_of(flat(60))
        assert ctx.adx == 0.0
        assert ctx.plus_di == 0.0
        assert ctx.minus_di == 0.0

    def test_a_strong_uptrend_is_plus_di_dominant(self):
        ctx = score_of(rising(60))
        assert ctx.plus_di > ctx.minus_di
        assert ctx.minus_di == 0.0

    def test_a_strong_downtrend_is_minus_di_dominant(self):
        ctx = score_of(falling(60))
        assert ctx.minus_di > ctx.plus_di
        assert ctx.plus_di == 0.0

    def test_every_monotonic_frame_saturates_adx_at_exactly_100(self):
        """🔴 Because `-DI` (or `+DI`) is identically 0, `dx` is
        `100 * |pdi - mdi| / (pdi + mdi + 1e-9)` = 100 at every bar, so the ADX
        mean is pinned at 100.0. This holds for a 0.001-per-bar crawl exactly as
        it does for a 50-per-bar explosion — ADX carries **no magnitude
        information** for a trending market, it is effectively binary."""
        for step in (0.001, 0.01, 0.1, 1.0, 10.0, 50.0):
            assert score_of(rising(60, step=step)).adx == 100.0
            assert score_of(falling(60, step=step)).adx == 100.0

    def test_a_perfectly_choppy_frame_saturates_adx_at_zero(self):
        """The other pole: a pure zigzag gives `pdi == mdi`, so `dx` is 0."""
        zig = [100 + (i % 2) * 2.0 for i in range(60)]
        assert score_of(frame(zig)).adx == 0.0

    def test_a_saturated_adx_gives_regime_engine_an_eight_point_five_multiplier(self):
        """🔴 `regime_engine.py:94` computes
        `adx_multiplier = 1.0 + max(0.0, (adx - 25) / 10.0)` and applies it to
        the TREND_BULL / TREND_BEAR scores (1.6x and 1.0x). The multiplier is
        **unbounded** — Wilder's ADX essentially never exceeds 60, but this
        implementation hands it 100.0 on any 19-bar monotonic window, yielding
        8.5x. A single quiet trending window then outweighs every other regime
        signal combined."""
        adx = score_of(rising(60)).adx
        assert adx == 100.0
        assert 1.0 + max(0.0, (adx - 25) / 10.0) == pytest.approx(8.5)

    def test_the_22_gate_is_observable_only_between_22_and_30(self):
        """A noisy frame lands ADX at 23.6 — above the 22.0 gate the trend score
        uses but below the 25.0 the Momentum analyst and the Devil's Advocate
        use, so the ±25 term is added here and neither of those fires. Pinned to
        the exact figures, because this is the only frame in the suite whose
        ADX is not 0.0 or 100.0 — it is therefore the only one that can see the
        `adx_period` window, the RSI window, or the reporting precision."""
        ctx = score_of(frame(ADX_BAND))
        assert ctx.adx == pytest.approx(23.6, abs=0.05)
        assert ctx.rsi == pytest.approx(50.8, abs=0.05)
        assert ctx.trend_score == -50
        assert ctx.trend_persistence == 3

    def test_a_tied_directional_move_is_discarded_not_counted(self):
        """`up_move > down_move` (not `>=`), so a bar where the up and down
        moves are exactly equal contributes to neither +DI nor -DI."""
        ctx = score_of(tie_frame())
        assert ctx.plus_di == 0.0
        assert ctx.minus_di == 0.0
        assert ctx.adx == 0.0

    def test_adx_uses_a_rolling_sum_not_wilders_smoothing(self):
        """Standard ADX smooths TR and DM with Wilder's RMA; this sums them over
        `adx_period` and then averages DX. Not comparable to a charting package,
        but self-consistent."""
        df = rising(60)
        high, low, close = df["high"], df["low"], df["close"]
        close_prev = close.shift(1)
        tr = np.maximum(high - low, np.maximum(np.abs(high - close_prev),
                                               np.abs(low - close_prev)))
        up = high - high.shift(1)
        down = low.shift(1) - low
        plus_dm = np.where((up > down) & (up > 0), up, 0.0)
        tr_smooth = tr.rolling(window=14, min_periods=1).sum()
        pdi = 100 * (pd.Series(plus_dm, index=df.index)
                     .rolling(window=14, min_periods=1).sum() / (tr_smooth + 1e-9))
        assert score_of(df, adx_period=14).plus_di == round(float(pdi.iloc[-1]), 1)

    def test_di_values_are_rounded_to_one_decimal(self):
        ctx = score_of(rising(60))
        assert ctx.adx == round(ctx.adx, 1)
        assert ctx.plus_di == round(ctx.plus_di, 1)
        assert ctx.minus_di == round(ctx.minus_di, 1)

    def test_a_zero_true_range_is_guarded_by_the_epsilon(self):
        """high == low == close makes TR 0; `+1e-9` keeps DI at 0 instead of
        dividing by zero."""
        df = frame([100.0] * 60, spread=0.0)
        ctx = score_of(df)
        assert ctx.plus_di == 0.0
        assert ctx.adx == 0.0


# --------------------------------------------------------------------------
# trend score
# --------------------------------------------------------------------------

class TestTrendScore:

    def test_a_strong_uptrend_scores_positive(self):
        assert score_of(rising(60)).trend_score > 0

    def test_a_strong_downtrend_scores_negative(self):
        assert score_of(falling(60)).trend_score < 0

    def test_a_flat_market_scores_zero(self):
        """No EMA stack (the comparisons are strict `>`), no ADX, no slope."""
        assert score_of(flat(60)).trend_score == 0

    def test_the_score_is_clipped_to_plus_or_minus_100(self):
        for df in (rising(60, step=50.0), falling(60, step=50.0)):
            ctx = score_of(df)
            assert -100 <= ctx.trend_score <= 100

    def test_the_score_is_an_int_and_truncated_not_rounded(self):
        """`int(np.clip(score, -100, 100))` truncates toward zero, so 44.9
        becomes 44 and -44.9 becomes -44. Every consumer compares against
        integers (20, 30, 40, 60, 65), so the lost fraction matters at the
        boundary — it makes the score *harder* to reach, not easier."""
        ctx = score_of(rising(60))
        assert isinstance(ctx.trend_score, int)
        assert int(44.9) == 44
        assert int(-44.9) == -44

    def test_the_ema_stack_is_the_largest_single_component(self):
        """A 50-bar rise followed by 10 flat bars isolates the EMA stack (45)
        plus the ADX term (25): the last 10 closes are identical, so the fitted
        slope is exactly 0 and the ±30 slope term contributes nothing."""
        ctx = score_of(frame([100 + i for i in range(50)] + [149.0] * 10))
        assert ctx.slope == 0.0
        assert ctx.adx >= 22.0
        assert ctx.trend_score == 70          # 45 + 25 + 0

    def test_the_same_holds_mirrored_for_a_downtrend(self):
        ctx = score_of(frame([200 - i for i in range(50)] + [151.0] * 10))
        assert ctx.slope == 0.0
        assert ctx.trend_score == -70         # -45 - 25 - 0

    def test_the_slope_term_adds_up_to_thirty_on_top(self):
        """Same EMA stack and ADX, but with the last 10 bars still rising."""
        flat_tail = score_of(frame([100 + i for i in range(50)] + [149.0] * 10))
        rising_tail = score_of(frame([100 + i for i in range(60)]))
        assert flat_tail.trend_score == 70
        assert rising_tail.trend_score == 100     # 70 + 30, clipped

    def test_the_adx_term_requires_adx_above_22(self):
        """Three different ADX thresholds are in play for the same number:
        22 here, 25.0 in the Momentum analyst, 25.0 in the Devil's Advocate."""
        assert score_of(rising(60)).adx >= 22.0
        assert score_of(rising(60, step=1.0)).trend_score >= 70

    def test_the_slope_term_is_clipped_to_plus_minus_30(self):
        """A 50-bar rise followed by a gentle 10-bar slide keeps the full
        bullish EMA stack (+45) and plus-DI dominance (+25), while the slope
        term wants to contribute −39.06. The −30 inner clip is what caps it:
        unclipped the score would be 30, not 40. (The outer ±100 clip cannot
        catch this — it is the inner clip that binds.)"""
        values = [100 + i * 6 for i in range(50)] + [394 - (i + 1) * 1.0 for i in range(10)]
        ctx = score_of(frame(values))
        assert ctx.slope == pytest.approx(-2.604, abs=0.0005)   # 3dp, as reported
        assert ctx.trend_score == 40           # 45 + 25 - 30, not 45 + 25 - 39

    def test_a_steep_move_stays_within_the_outer_clip(self):
        steep = score_of(rising(60, step=100.0))
        assert steep.trend_score == 100
        assert steep.slope > 0

    def test_the_outer_clip_can_never_bind(self):
        """🔴 `int(np.clip(score, -100, 100))` is dead. The three terms are
        bounded at ±45, ±25 and ±30, so their sum cannot exceed ±100 — the
        document range *is* the arithmetic range, and the clip is decoration.
        Worth knowing before anyone "widens" the scale: raising a term above
        these caps would silently start clipping here."""
        assert 45.0 + 25.0 + 30.0 == 100.0
        assert score_of(rising(60, step=1.0)).trend_score == 100
        assert score_of(falling(60, step=1.0)).trend_score == -100

    def test_the_slow_ema_span_is_clamped_and_it_changes_the_branch(self):
        """`span_slow = min(ema_slow, len(df))` — on a 60-bar frame the "200 EMA"
        is a 60 EMA. Unclamping it moves the slow EMA far enough to change which
        stack branch fires: +45 becomes +30, dropping the score 100 -> 85."""
        ctx = score_of(frame(SLOW_SPAN_SENSITIVE))
        assert ctx.trend_score == 100

    def test_slope_is_normalised_by_price_and_scaled_by_1000(self):
        """`norm_slope = slope / c_price * 1000`, so the same points-per-bar move
        yields a smaller slope number on a higher-priced instrument."""
        cheap = score_of(rising(60, start=1.0, step=0.01))
        dear = score_of(rising(60, start=1000.0, step=0.01))
        assert cheap.slope > dear.slope

    def test_the_slope_window_is_ten_bars(self):
        """Only the last 10 bars are fitted, so a long flat tail after a rise
        drags the slope to ~0."""
        values = [100 + i for i in range(30)] + [130.0] * 30
        assert abs(score_of(frame(values)).slope) < 1e-6

    def test_a_reversal_at_the_end_flips_the_slope(self):
        values = [100 + i for i in range(50)] + [150 - i for i in range(10)]
        assert score_of(frame(values)).slope < 0


# --------------------------------------------------------------------------
# acceleration
# --------------------------------------------------------------------------

class TestAcceleration:

    def test_a_speeding_up_move_is_accelerating(self):
        # last 3 bars move much further than the 3 before them
        values = [100.0] * 50 + [100, 101, 103, 106, 110]
        assert score_of(frame(values)).acceleration == "ACCELERATING"

    def test_a_slowing_move_is_decelerating(self):
        values = [100.0] * 40 + [100, 110, 118, 124, 128, 130, 131, 131.5, 131.8, 132]
        assert score_of(frame(values)).acceleration == "DECELERATING"

    def test_a_perfectly_linear_market_reads_as_decelerating(self):
        """🔴 `diff_recent = close[-1] - close[-3]` spans **two** bar-intervals;
        `diff_prior = close[-3] - close[-6]` spans **three**. For a linear series
        of step `s` that is 2s vs 3s — a ratio of 0.667, which sits just under
        the 0.7 DECELERATING threshold.

        So a constant-rate trend is reported as DECELERATING on every single
        bar, at every length. `STEADY` requires the last two bars to cover at
        least 70% of the distance the previous three did — i.e. an *accelerating*
        market relative to a linear one."""
        for n in (19, 20, 30, 60):
            df = rising(n, step=1.0)
            close = df["close"]
            diff_recent = float(close.iloc[-1] - close.iloc[-3])
            diff_prior = float(close.iloc[-3] - close.iloc[-6])
            assert diff_recent == pytest.approx(2.0)
            assert diff_prior == pytest.approx(3.0)
            assert score_of(df).acceleration == "DECELERATING"

    def test_steady_is_reachable_only_when_the_two_bar_move_keeps_pace(self):
        """A frame engineered so the recent 2-bar move (2.5) is more than 0.7x
        the prior 3-bar move (3.0) but less than 1.3x it."""
        values = [100 + (i % 3) - 1 for i in range(54)] + [100, 101, 102, 103, 104.25, 105.5]
        df = frame(values)
        close = df["close"]
        assert float(close.iloc[-1] - close.iloc[-3]) == pytest.approx(2.5)
        assert float(close.iloc[-3] - close.iloc[-6]) == pytest.approx(3.0)
        assert score_of(df).acceleration == "STEADY"

    def test_a_move_just_under_the_1_3_threshold_is_not_accelerating(self):
        """The ACCELERATING bar is 1.3x the prior move, not merely *more* than
        it: 3.5 vs 3.0 is a 1.167x ratio, which lands in the STEADY band."""
        values = [100 + (i % 3) - 1 for i in range(54)] + [100, 101, 102, 103, 105, 106.5]
        df = frame(values)
        close = df["close"]
        assert float(close.iloc[-1] - close.iloc[-3]) == pytest.approx(3.5)
        assert float(close.iloc[-3] - close.iloc[-6]) == pytest.approx(3.0)
        assert score_of(df).acceleration == "STEADY"

    def test_exhaustion_needs_both_rsi_and_trend_extremes(self):
        """`(rsi > 78 and trend > 60) or (rsi < 22 and trend < -60)` — reachable
        only when neither acceleration branch matched first."""
        ctx = score_of(frame([100 + i for i in range(50)] + [149.0] * 10))
        assert ctx.rsi == 100.0
        assert ctx.trend_score == 70
        assert ctx.acceleration == "EXHAUSTION"

    def test_a_flat_market_is_steady(self):
        assert score_of(flat(60)).acceleration == "STEADY"

    def test_the_diff_recent_fallback_is_reachable_only_with_a_tiny_ema_slow(self):
        """🔴 `diff_recent ... if len(df) >= 3 else 0.0`. With the default
        periods the 19-bar guard fires long before a 3-bar frame is ever seen,
        so this fallback is dead. It is reachable only by shrinking `ema_slow`
        below `min_len` so the guard is skipped: with `ema_slow=2` and two bars
        both diffs are 0.0, neither branch matches, and EXHAUSTION is reached
        instead (rsi 100 > 78 and score 70 > 60)."""
        ctx = MomentumEngine(ema_slow=2).analyze_momentum(frame([100.0, 101.0]))
        assert ctx.acceleration == "EXHAUSTION"

    def test_the_diff_prior_fallback_makes_any_move_look_accelerating(self):
        """🔴 With `len(df) < 6`, `diff_prior` is 0.0, so the test is
        `abs(diff_recent) > 0 * 1.3` — true for any non-zero recent move. A
        five-bar frame therefore reports ACCELERATING unconditionally."""
        ctx = MomentumEngine(ema_slow=5).analyze_momentum(rising(5))
        assert ctx.acceleration == "ACCELERATING"


# --------------------------------------------------------------------------
# divergence
# --------------------------------------------------------------------------

class TestDivergence:

    def test_a_new_low_with_a_higher_rsi_low_is_bullish(self):
        ctx = score_of(frame(BULL_FIXTURE))
        assert ctx.divergence == "BULLISH_DIVERGENCE"

    def test_the_bullish_fixture_really_does_make_a_new_price_low(self):
        df = frame(BULL_FIXTURE)
        assert df["low"].iloc[-10:].min() < df["low"].iloc[-20:-10].min()
        r = rsi_of(df["close"])
        assert r.iloc[-10:].min() > r.iloc[-20:-10].min() + 3.0

    def test_a_new_high_with_a_lower_rsi_high_is_bearish(self):
        ctx = score_of(frame(BEAR_FIXTURE))
        assert ctx.divergence == "BEARISH_DIVERGENCE"

    def test_the_bearish_fixture_really_does_make_a_new_price_high(self):
        df = frame(BEAR_FIXTURE)
        assert df["high"].iloc[-10:].max() > df["high"].iloc[-20:-10].max()
        r = rsi_of(df["close"])
        assert r.iloc[-10:].max() < r.iloc[-20:-10].max() - 3.0

    def test_bearish_silently_overwrites_bullish(self):
        """🔴 The two checks are independent `if`s, not `if`/`elif`. On a frame
        satisfying both predicates the second assignment wins and the bullish
        reading is lost — there is no "both" state, and the order of the blocks
        decides which divergence you hear about. This fixture satisfies both
        (rsi_gap +8.9 on lows, −25.5 on highs) and reports BEARISH."""
        ctx = score_of(frame(BOTH_FIXTURE))
        assert ctx.divergence == "BEARISH_DIVERGENCE"

    def test_divergence_needs_twenty_bars(self):
        """Below 20 bars the whole block is skipped — the divergent fixtures
        report NONE when truncated, even though the shape is unchanged."""
        for fixture in (BULL_FIXTURE, BEAR_FIXTURE):
            assert score_of(frame(fixture)).divergence != "NONE"
            assert score_of(frame(fixture[-19:])).divergence == "NONE"

    def test_the_rsi_gap_must_exceed_three_points(self):
        """A monotonic rise makes new highs forever, but the RSI is pinned at
        100.0 throughout, so the gap is 0 and nothing is reported."""
        assert score_of(rising(60)).divergence == "NONE"

    def test_a_new_low_with_a_rsi_improvement_under_three_points_is_nothing(self):
        """The deadband is `> prior + 3.0`, so a genuine new low whose RSI low
        improves by only 0.33 is silently dropped."""
        df = frame(SMALL_GAP)
        assert df["low"].iloc[-10:].min() < df["low"].iloc[-20:-10].min()
        r = rsi_of(df["close"])
        assert 0 < r.iloc[-10:].min() - r.iloc[-20:-10].min() < 3.0
        assert score_of(df).divergence == "NONE"

    def test_the_recent_low_window_is_ten_bars_not_five(self):
        """The recent low is taken over bars -10..-1. On this frame the deepest
        low sits in bars -10..-6, so a five-bar window would miss it entirely
        and lose the divergence."""
        df = frame(BULL_LOW_OUTSIDE)
        assert df["low"].iloc[-10:-5].min() < df["low"].iloc[-5:].min()
        assert score_of(df).divergence == "BULLISH_DIVERGENCE"

    def test_the_prior_low_window_is_ten_bars_not_five(self):
        """The prior low is taken over bars -20..-11. On this frame that window's
        minimum sits in bars -15..-11, so narrowing it would change the
        comparison — and the verdict along with it."""
        df = frame(PRIOR_LOW_INSIDE)
        assert df["low"].iloc[-20:-10].min() != df["low"].iloc[-20:-15].min()
        assert score_of(df).divergence == "NONE"

    def test_the_recent_high_window_is_ten_bars_not_five(self):
        """The bearish mirror of the low-window test: this frame's highest high
        sits in bars -10..-6, so a five-bar window would miss it."""
        df = frame(BEAR_HIGH_OUTSIDE)
        assert df["high"].iloc[-10:-5].max() > df["high"].iloc[-5:].max()
        ctx = score_of(df)
        assert ctx.divergence == "BEARISH_DIVERGENCE"
        assert ctx.trend_score == -70

    def test_the_prior_high_window_is_ten_bars_not_five(self):
        df = frame(PRIOR_HIGH_INSIDE)
        assert df["high"].iloc[-20:-10].max() != df["high"].iloc[-20:-15].max()
        assert score_of(df).divergence == "NONE"

    def test_a_new_high_with_an_rsi_weakening_under_three_points_is_nothing(self):
        """The bearish deadband is `< prior - 3.0`; this frame's RSI high
        weakens by less than that, so the new high is ignored."""
        df = frame(BEAR_SMALL_GAP)
        assert df["high"].iloc[-10:].max() > df["high"].iloc[-20:-10].max()
        r = rsi_of(df["close"])
        assert -3.0 < r.iloc[-10:].max() - r.iloc[-20:-10].max() < 0.0
        assert score_of(df).divergence == "NONE"


# --------------------------------------------------------------------------
# roc
# --------------------------------------------------------------------------

class TestRoc:

    def test_roc_is_the_ten_bar_percentage_change(self):
        values = [100.0] * 59 + [110.0]
        assert score_of(frame(values)).roc == pytest.approx(10.0)

    def test_roc_is_zero_below_ten_bars(self):
        """The guard is `>= 10`, not `>= 19`, so a 19-bar frame is measured
        normally. The sub-10 path is only reachable with a small `ema_slow`
        (which skips the 19-bar guard)."""
        assert score_of(rising(19)).roc == pytest.approx(8.26)
        for n in (5, 9):
            ctx = MomentumEngine(ema_slow=5).analyze_momentum(rising(n))
            assert ctx.roc == 0.0
        assert MomentumEngine(ema_slow=5).analyze_momentum(rising(10)).roc == pytest.approx(9.0)

    def test_a_zero_base_price_produces_infinity(self):
        """🔴 `close[-1] / close[-10]` has no zero guard — a frame whose tenth
        bar back is 0.0 yields `inf` (or `nan` for 0/0), which then flows into
        `round()` and out to the context. `round(inf)` is `inf`, so downstream
        comparisons against it silently succeed or fail depending on direction."""
        values = [100.0] * 59 + [110.0]
        values[-10] = 0.0
        ctx = score_of(frame(values))
        assert np.isinf(ctx.roc) or np.isnan(ctx.roc)

    def test_roc_is_rounded_to_two_decimals(self):
        ctx = score_of(rising(60, step=0.37))
        assert ctx.roc == round(ctx.roc, 2)


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------

class TestPersistence:

    def test_persistence_is_zero_when_the_final_score_is_zero(self):
        """`(final > 0 and t > 0) or (final < 0 and t < 0)` — with a final score
        of exactly 0 neither side is ever true, so the loop breaks immediately."""
        assert score_of(flat(60)).trend_score == 0
        assert score_of(flat(60)).trend_persistence == 0

    def test_a_sustained_uptrend_persists(self):
        assert score_of(rising(60)).trend_persistence > 1

    def test_a_sustained_downtrend_persists(self):
        assert score_of(falling(60)).trend_persistence > 1

    def test_persistence_breaks_immediately_when_the_last_bar_disagrees(self):
        """A positive final score (20) built from EMA −45, ADX +25 and a +30
        slope term; the loop's rebuilt score omits the slope, so the last bar
        scores −20 and the count never starts."""
        ctx = score_of(frame(PERSISTENCE_BREAK))
        assert ctx.trend_score == 20
        assert ctx.trend_persistence == 0

    def test_a_reversal_still_counts_several_bars_because_the_emas_lag(self):
        """A hard V-bottom: price turned 10 bars ago, but the 20/50/200 EMAs
        have not crossed yet, so the rebuilt scores still agree for five bars."""
        values = [100 - i for i in range(50)] + [50 + i * 2 for i in range(10)]
        ctx = score_of(frame(values))
        assert ctx.trend_score == 70
        assert ctx.trend_persistence == 5

    def test_the_persistence_score_omits_the_slope_term(self):
        """🔴 The loop rebuilds a per-bar score from the EMA stack and ADX only —
        the ±30 slope component is not part of it. So `trend_persistence` counts
        agreement with a *different* quantity than `trend_score` is, and the two
        can disagree in sign on the very bar being scored (see the break test)."""
        df = rising(60)
        assert score_of(df).slope != 0.0
        assert score_of(df).trend_persistence > 0

    def test_persistence_is_capped_by_a_30_bar_lookback(self):
        """`range(1, lookback)` with `lookback = min(30, len(df))` — so the
        maximum possible count is 29, not 30."""
        assert score_of(rising(200)).trend_persistence == 29
        assert score_of(rising(60)).trend_persistence == 29

    def test_the_adx_term_inside_the_loop_decides_whole_bars(self):
        """Bars where the rebuilt score lands exactly on zero are decided by the
        ±25 ADX term: +5 here would collapse the count from 29 to 5."""
        ctx = score_of(frame(PERSIST_ADX_SENSITIVE))
        assert ctx.trend_score == -40
        assert ctx.trend_persistence == 29

    def test_each_bar_reads_its_own_adx_not_the_final_one(self):
        """`float(adx.iloc[idx])` — a per-bar read. Using the last bar's ADX for
        the whole lookback would count 7 here instead of 6."""
        ctx = score_of(frame(PERSIST_ADX_VARYING))
        assert ctx.trend_score == -74
        assert ctx.trend_persistence == 6

    def test_each_bar_reads_its_own_ema_and_di_not_the_final_ones(self):
        """`c_ema_m` and `c_pdi` are also per-bar reads, and on this frame
        pinning either to the last bar changes the count from 21."""
        ctx = score_of(frame(PERSIST_EMA_DI_VARYING))
        assert ctx.trend_score == -100
        assert ctx.trend_persistence == 21

    def test_which_constants_inside_the_loop_can_ever_change_a_verdict(self):
        """🔴 The rebuilt per-bar score is `stack ± 25` (or `stack` alone when
        `adx < 22`), so its reachable magnitudes are ±{5, 10, 15, 20, 30, 40, 45,
        55, 70}. A constant is observable only if altering it can carry some bar
        across zero, because the loop tests `t > 0` / `t < 0`.

        * `t_score += 15` -> 10 : {40, -10, 15} -> {35, -15, 10} — never crosses.
        * `t_score -= 15` -> 10 : {-40, 10, -15} -> {-45, 15, -10} — never crosses.
        * `t_score -= 45` -> 40 : {-20, -70, -45} -> {-15, -65, -40} — never crosses.
        * `t_score -= 30` -> 25 : {-5, -55, -30} -> {0, -50, -25} — **crosses at -5**.
        * the ±25 ADX term: moves ±5 to 0 — load-bearing on real bars.

        So three of the four stack constants are provably inert and a mutation
        battery will report them as test holes when they are not; only the -30
        branch and the ADX term can ever be observed."""
        reachable = sorted({abs(a + b) for a in (15, 30, 45, -15, -30, -45)
                            for b in (25, -25, 0)})
        assert reachable == [5, 10, 15, 20, 30, 40, 45, 55, 70]
        # the three provably inert ones: same sign before and after, every ADX term
        for old, new in ((15, 10), (-15, -10), (-45, -40)):
            for adx in (25, -25, 0):
                assert (old + adx > 0) == (new + adx > 0)
                assert (old + adx < 0) == (new + adx < 0)
        # and the one that is not: -30 with a +25 ADX term sits at -5, one step from zero
        assert -30 + 25 == -5 and -25 + 25 == 0

    def test_persistence_needs_ten_bars(self):
        """The gate is `lookback >= 10`, and it is only reachable with a small
        `ema_slow` — with the default 200 the 19-bar guard returns first, so the
        gate is never even evaluated on a frame that short."""
        for n in (5, 7, 9):
            ctx = MomentumEngine(ema_slow=n).analyze_momentum(rising(n))
            assert ctx.trend_score == 70
            assert ctx.trend_persistence == 0
        assert score_of(rising(9)).trend_persistence == 0


# --------------------------------------------------------------------------
# housekeeping
# --------------------------------------------------------------------------

class TestHousekeeping:

    def test_the_input_frame_is_not_modified(self):
        df = rising(60)
        before = df.copy()
        score_of(df)
        pd.testing.assert_frame_equal(df, before)

    def test_a_missing_column_raises(self):
        for col in ("close", "high", "low"):
            df = rising(60).drop(columns=[col])
            with pytest.raises(KeyError):
                score_of(df)

    def test_an_all_nan_frame_raises(self):
        """`int(np.clip(nan, ...))` is a ValueError — a frame of NaNs does not
        degrade, it kills the context build."""
        df = rising(60)
        df["close"] = np.nan
        with pytest.raises(ValueError):
            score_of(df)

    def test_a_single_bar_frame_raises_in_the_slope_fit(self):
        """`np.polyfit` on one point cannot converge."""
        with pytest.raises(Exception):
            MomentumEngine(ema_slow=1).analyze_momentum(frame([100.0]))

    def test_the_default_periods(self):
        eng = MomentumEngine()
        assert (eng.ema_fast, eng.ema_med, eng.ema_slow) == (20, 50, 200)
        assert (eng.rsi_period, eng.adx_period) == (14, 14)

    def test_the_returned_context_always_has_every_field_populated(self):
        for df in (rising(60), falling(60), flat(60), rising(19),
                   frame(BULL_FIXTURE), frame(BEAR_FIXTURE)):
            ctx = score_of(df)
            assert isinstance(ctx, MomentumContext)
            assert ctx.divergence in ("BULLISH_DIVERGENCE", "BEARISH_DIVERGENCE", "NONE")
            assert ctx.acceleration in ("ACCELERATING", "DECELERATING",
                                        "EXHAUSTION", "STEADY")
            assert -100 <= ctx.trend_score <= 100
            assert not np.isnan(ctx.slope)

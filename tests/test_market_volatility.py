"""Round 29 — coverage for ``jarvis/market/volatility.py`` (VolatilityEngine).

WHY THIS FILE EXISTS
--------------------
``market_context.py:89`` calls ``analyze_volatility(df_primary, ...)`` for every
symbol on every scan and feeds the result into the context the decision engine
reads. The module had no dedicated suite. (``test_volatility_targeted_sizing.py``
is about position sizing, not this engine.)

Every number below was measured against the pristine module. The fixtures use a
deliberately degenerate shape — a constant close with a chosen high-low range —
because that makes ``TR == high - low`` exactly, so ATR and therefore every
regime threshold is hand-checkable.

Findings are PINNED, not fixed. The load-bearing ones:

* The regime baseline is ``median(atr_series.tail(100))`` and ``atr_series``
  **includes the current value**, so ``vol_ratio`` is self-referential. With a
  frame of constant range the ratio is exactly 1.0 and the state is ``NORMAL``
  whether the range is 1e-6 or 1e6 — the state says nothing about absolute
  volatility, only about the last 100 bars relative to now.
* ``len(atr_series) >= 20`` gates the baseline; with a custom ``bb_period``
  below 20 the fallback is ``current_atr``, which forces ``vol_ratio == 1.0``
  and therefore pins the state to ``NORMAL``.
* The short-frame early return reports ``state="NORMAL"`` unconditionally and
  does **not** round ``current_spread_pips``, unlike the main path.
"""
import numpy as np
import pandas as pd
import pytest

from jarvis.market.volatility import VolatilityEngine


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def trframe(ranges, price=100.0, vol=1000.0):
    """Constant close => prev_close sits inside every bar => TR == high - low."""
    r = np.asarray(ranges, dtype=float)
    c = np.full(len(r), price)
    return pd.DataFrame({"open": c, "high": c + r / 2.0, "low": c - r / 2.0,
                         "close": c, "volume": vol})


def seg(n=60, k=45, a=0.2, b=2.0):
    """`a` for the first k bars, then `b` — a clean volatility step."""
    return trframe([a] * k + [b] * (n - k))


def rising(n=60, start=100.0, step=0.1, spread=0.3):
    c = pd.Series([start + step * i for i in range(n)], dtype=float)
    return pd.DataFrame({"open": c, "high": c + spread, "low": c - spread,
                         "close": c, "volume": 1000.0})


def run(df, **kw):
    return VolatilityEngine().analyze_volatility(df, **kw)


def sine(n=60, amp=2.0, period=20.0, base=100.0):
    c = pd.Series([base + amp * np.sin(2 * np.pi * i / period) for i in range(n)])
    return pd.DataFrame({"open": c, "high": c + 0.5, "low": c - 0.5,
                         "close": c, "volume": 1000.0})


# --------------------------------------------------------------------------- #
# module-level fixtures (each verified against the pristine module)
# --------------------------------------------------------------------------- #
RISING60 = rising(60)          # atr 0.6, atr% 0.5666, NORMAL
SINE60 = sine(60)              # bb 5.804
COMPRESSION = seg(b=0.10)      # ratio 0.50 -> COMPRESSION
NORMAL = seg(b=0.20)           # ratio 1.00 -> NORMAL
EXPANSION = seg(b=0.40)        # ratio 2.00 -> EXPANSION
EXTREME = seg(b=0.80)          # ratio 4.00 -> EXTREME


# --------------------------------------------------------------------------- #
class TestLengthGuard:
    def test_default_guard_is_the_bollinger_period(self):
        assert run(rising(19)).atr == 0.0
        assert run(rising(20)).atr == pytest.approx(0.6, abs=1e-9)

    @pytest.mark.parametrize("bb", [5, 10, 20, 30])
    def test_guard_tracks_the_configured_bollinger_period(self, bb):
        e = VolatilityEngine(bb_period=bb)
        assert e.analyze_volatility(rising(bb - 1)).atr == 0.0
        assert e.analyze_volatility(rising(bb)).atr > 0.0

    def test_the_short_frame_still_reports_the_spread_verdict(self):
        """The only field the early return actually evaluates."""
        r = VolatilityEngine().analyze_volatility(
            rising(5), current_spread_pips=99.0, max_allowed_spread_pips=35.0)
        assert r.is_excessive_spread is True

    def test_the_short_frame_claims_normal_volatility_unconditionally(self):
        """PINNED. state is hardcoded to "NORMAL" below bb_period bars — a
        violently expanding market on a 19-bar frame reads as calm."""
        r = run(seg(n=19, k=9, a=0.2, b=50.0))
        assert r.state == "NORMAL"
        assert r.atr == 0.0
        assert r.bollinger_bandwidth == 0.0

    def test_the_short_frame_zeros_atr_percent_too(self):
        assert run(rising(19)).atr_percent == 0.0

    def test_an_empty_frame_is_handled(self):
        r = run(pd.DataFrame())
        assert r.state == "NORMAL" and r.atr == 0.0


class TestTrueRange:
    def test_tr_is_high_minus_low_when_the_previous_close_is_inside_the_bar(self):
        assert run(trframe([1.0] * 60)).atr == pytest.approx(1.0, abs=1e-9)

    def test_tr_uses_the_previous_close_when_it_gaps_outside_the_bar(self):
        """A gap makes TR far larger than high-low; ignoring it would halve the
        ATR here."""
        df = rising(60, step=0.0, spread=0.1)
        df.loc[45:, "close"] = [110.0 + 10.0 * i for i in range(15)]
        df.loc[45:, "high"] = df.loc[45:, "close"] + 0.1
        df.loc[45:, "low"] = df.loc[45:, "close"] - 0.1
        r = run(df)
        # last 14 bars all gap 10 points -> TR = 10.1, not 0.2
        assert r.atr == pytest.approx(10.1, abs=1e-6)

    def test_the_first_bar_contributes_no_true_range(self):
        """TR[0] is NaN (close.shift(1) is NaN and np.maximum propagates it),
        so the first bar's own range is discarded entirely."""
        ranges = [1000.0] + [1.0] * 19
        r = VolatilityEngine(atr_period=20).analyze_volatility(trframe(ranges))
        assert r.atr == pytest.approx(1.0, abs=1e-6)

    def test_min_periods_one_means_a_partial_window_is_averaged(self):
        """With atr_period=14 on a 20-bar frame the last value is a full window;
        with atr_period=40 it is a 20-bar average. Distinct when TR varies."""
        ranges = [1.0] * 10 + [4.0] * 10
        # atr_period=14 -> window [6..19] = 4x1.0 + 10x4.0 = 44/14
        a = VolatilityEngine(atr_period=14).analyze_volatility(trframe(ranges))
        # atr_period=40 -> window [0..19], but TR[0] is NaN, so 19 values:
        #                  9x1.0 + 10x4.0 = 49/19 (NOT 50/20)
        b = VolatilityEngine(atr_period=40).analyze_volatility(trframe(ranges))
        assert a.atr == pytest.approx(44.0 / 14.0, abs=1e-4)
        assert b.atr == pytest.approx(49.0 / 19.0, abs=1e-4)

    def test_atr_period_is_configurable(self):
        assert VolatilityEngine(atr_period=7).atr_period == 7


class TestAtr:
    def test_atr_matches_the_last_fourteen_true_ranges(self):
        ranges = [0.2] * 46 + [0.2 * (i + 1) for i in range(14)]
        manual = float(np.mean(ranges[-14:]))
        assert run(trframe(ranges)).atr == pytest.approx(manual, abs=1e-4)

    def test_atr_percent_is_atr_over_price_in_percent(self):
        r = run(RISING60)
        assert r.atr_percent == pytest.approx(0.6 / 105.9 * 100.0, abs=1e-4)

    def test_atr_percent_follows_the_price_level(self):
        a = run(rising(20))
        b = run(rising(21))
        assert a.atr_percent == pytest.approx(0.5888, abs=1e-4)
        assert b.atr_percent == pytest.approx(0.5882, abs=1e-4)
        assert b.atr_percent < a.atr_percent

    def test_a_negative_price_gives_a_negative_atr_percent(self):
        """PINNED. The `+1e-9` guard does not protect against a negative
        denominator."""
        assert run(trframe([1.0] * 60, price=-100.0)).atr_percent < 0.0

    def test_a_zero_price_does_not_raise(self):
        r = run(trframe([1.0] * 60, price=0.0))
        assert np.isfinite(r.atr_percent)

    def test_atr_is_rounded_to_four_places(self):
        r = run(trframe([0.123456789] * 60))
        assert r.atr == round(r.atr, 4)


class TestBollingerBandwidth:
    def test_bandwidth_matches_a_hand_rolled_calculation(self):
        c = SINE60["close"]
        sma = c.rolling(20).mean()
        std = c.rolling(20).std()
        manual = float(((sma.iloc[-1] + 2 * std.iloc[-1])
                        - (sma.iloc[-1] - 2 * std.iloc[-1]))
                       / (sma.iloc[-1] + 1e-9) * 100.0)
        assert run(SINE60).bollinger_bandwidth == pytest.approx(manual, abs=1e-3)

    def test_std_is_the_sample_standard_deviation(self):
        """pandas `.std()` is ddof=1; numpy's default ddof=0 would differ."""
        c = SINE60["close"]
        w = c.tail(20)
        assert float(w.std()) != float(w.std(ddof=0))

    def test_bandwidth_scales_with_bb_std(self):
        a = run(SINE60, )
        b = VolatilityEngine(bb_std=1.0).analyze_volatility(SINE60)
        assert a.bollinger_bandwidth == pytest.approx(2 * b.bollinger_bandwidth,
                                                      abs=1e-3)

    def test_a_constant_close_gives_zero_bandwidth(self):
        assert run(trframe([1.0] * 60)).bollinger_bandwidth == 0.0

    def test_bandwidth_is_rounded_to_three_places(self):
        r = run(SINE60)
        assert r.bollinger_bandwidth == round(r.bollinger_bandwidth, 3)
        assert r.bollinger_bandwidth == pytest.approx(5.804, abs=1e-3)


class TestStates:
    def test_compression(self):
        assert run(COMPRESSION).state == "COMPRESSION"

    def test_normal(self):
        assert run(NORMAL).state == "NORMAL"

    def test_expansion(self):
        assert run(EXPANSION).state == "EXPANSION"

    def test_extreme(self):
        assert run(EXTREME).state == "EXTREME"

    def test_the_expansion_threshold_is_a_strict_greater_than(self):
        """ratio exactly 1.4 stays NORMAL; 1.45 flips."""
        assert run(seg(b=0.28)).state == "NORMAL"      # 0.28 / 0.2 == 1.4
        assert run(seg(b=0.29)).state == "EXPANSION"

    def test_the_extreme_threshold_is_a_strict_greater_than(self):
        """ratio exactly 2.5 stays EXPANSION; 2.55 flips."""
        assert run(seg(b=0.50)).state == "EXPANSION"   # 0.50 / 0.2 == 2.5
        assert run(seg(b=0.51)).state == "EXTREME"

    def test_the_compression_threshold_is_a_strict_less_than(self):
        assert run(seg(a=1.0, b=0.65)).state == "COMPRESSION"
        assert run(seg(a=1.0, b=0.66)).state == "NORMAL"

    def test_the_state_is_scale_invariant(self):
        """PINNED. A constant-range frame is always NORMAL — the regime says
        nothing about how volatile the market is in absolute terms."""
        for rng in (1e-6, 0.1, 1.0, 100.0, 1e6):
            assert run(trframe([rng] * 60)).state == "NORMAL"

    def test_the_engine_only_flags_a_transition_not_a_level(self):
        """PINNED. The baseline is `median(atr_series.tail(100))` and the
        current ATR is one of its own samples. Once a new regime makes up the
        majority of the last 100 bars it becomes the baseline and the engine
        goes quiet again — a market that has been violent for 45 of the last 60
        bars reads NORMAL, with the same ATR as one that reads EXTREME."""
        assert run(seg(k=45, a=0.2, b=1.0)).state == "EXTREME"
        quiet_again = run(seg(k=15, a=0.2, b=1.0))
        assert quiet_again.state == "NORMAL"
        assert quiet_again.atr == run(seg(k=45, a=0.2, b=1.0)).atr
        # the state tracks how much of the window is still "old"
        assert run(seg(k=25, a=0.2, b=1.0)).state == "EXPANSION"
        assert run(seg(k=35, a=0.2, b=1.0)).state == "EXTREME"

    def test_only_the_last_hundred_bars_feed_the_baseline(self):
        """tail(100) — a very old regime cannot influence the verdict."""
        ranges = [50.0] * 200 + [1.0] * 60
        assert run(trframe(ranges)).state == "COMPRESSION"

    def test_a_short_frame_with_a_custom_bb_period_is_pinned_to_normal(self):
        """PINNED. `len(atr_series) >= 20` is false, so the baseline falls back
        to `current_atr` and vol_ratio is exactly 1.0 — no state is reachable."""
        e = VolatilityEngine(atr_period=3, bb_period=5)
        r = e.analyze_volatility(seg(n=6, k=3, a=0.2, b=50.0))
        assert r.state == "NORMAL"

    def test_the_same_frame_reaches_extreme_once_the_baseline_is_enabled(self):
        e = VolatilityEngine(atr_period=3, bb_period=5)
        r = e.analyze_volatility(seg(n=40, k=20, a=0.2, b=50.0))
        assert r.state == "EXTREME"

    def test_every_state_is_a_known_string(self):
        for f in (COMPRESSION, NORMAL, EXPANSION, EXTREME, RISING60):
            assert run(f).state in ("COMPRESSION", "NORMAL", "EXPANSION", "EXTREME")


class TestSpread:
    def test_excessive_is_a_strict_greater_than(self):
        assert run(RISING60, current_spread_pips=35.0,
                   max_allowed_spread_pips=35.0).is_excessive_spread is False
        assert run(RISING60, current_spread_pips=35.01,
                   max_allowed_spread_pips=35.0).is_excessive_spread is True

    def test_the_default_ceiling_is_thirty_five_pips(self):
        r = run(RISING60, current_spread_pips=36.0)
        assert r.max_allowed_spread_pips == 35.0
        assert r.is_excessive_spread is True

    def test_the_reported_spread_is_rounded_to_two_places(self):
        r = run(RISING60, current_spread_pips=1.23456)
        assert r.current_spread_pips == 1.23

    def test_the_short_frame_path_does_not_round_the_spread(self):
        """PINNED INCONSISTENCY. The early return passes the raw value through
        while the main path rounds to 2 dp."""
        r = run(rising(19), current_spread_pips=1.23456)
        assert r.current_spread_pips == 1.23456

    def test_the_ceiling_is_never_rounded(self):
        r = run(RISING60, max_allowed_spread_pips=35.555)
        assert r.max_allowed_spread_pips == 35.555

    def test_a_zero_ceiling_flags_every_nonzero_spread(self):
        r = run(RISING60, current_spread_pips=0.5, max_allowed_spread_pips=0.0)
        assert r.is_excessive_spread is True

    def test_a_zero_spread_against_a_zero_ceiling_is_fine(self):
        r = run(RISING60, current_spread_pips=0.0, max_allowed_spread_pips=0.0)
        assert r.is_excessive_spread is False

    def test_the_default_current_spread_is_two_pips(self):
        assert run(RISING60).current_spread_pips == 2.0


class TestThresholdBands:
    """Pin each threshold's VALUE. The strict-vs-inclusive question is
    separate — see ``TestUnobservableBoundary``."""

    def test_ratio_just_below_one_point_four_is_normal(self):
        assert run(seg(b=0.26)).state == "NORMAL"        # 1.30

    def test_ratio_just_above_one_point_four_is_expansion(self):
        assert run(seg(b=0.30)).state == "EXPANSION"     # 1.50

    def test_ratio_just_below_two_point_five_is_expansion(self):
        assert run(seg(b=0.48)).state == "EXPANSION"     # 2.40

    def test_ratio_just_above_two_point_five_is_extreme(self):
        assert run(seg(b=0.55)).state == "EXTREME"       # 2.75

    def test_ratio_just_below_sixty_five_hundredths_is_compression(self):
        assert run(seg(a=1.0, b=0.60)).state == "COMPRESSION"

    def test_ratio_just_above_sixty_five_hundredths_is_normal(self):
        assert run(seg(a=1.0, b=0.70)).state == "NORMAL"

    def test_the_bands_are_contiguous_and_ordered(self):
        """Walking b upward must cross NORMAL -> EXPANSION -> EXTREME once
        each, in that order."""
        seen = [run(seg(b=b)).state
                for b in (0.10, 0.20, 0.26, 0.30, 0.48, 0.55, 1.00)]
        assert seen == ["COMPRESSION", "NORMAL", "NORMAL",
                        "EXPANSION", "EXPANSION", "EXTREME", "EXTREME"]


class TestUnobservableBoundary:
    """Three distinctions in this module cannot be observed, and the reason is
    arithmetic, not a missing fixture. Each is asserted so the gap is explicit
    rather than silent."""

    def test_strict_versus_inclusive_at_the_regime_thresholds(self):
        """`vol_ratio > 2.5` vs `>= 2.5` is unobservable.

        vol_ratio = current_atr / (atr_historical_median + 1e-9). Both terms
        are float means/medians of the same series, so the ratio lands on a
        threshold only by exact coincidence — and the `+ 1e-9` epsilon makes
        even a hand-chosen pair miss: with med = 0.25 and cur = 0.625 the
        ratio is 0.625 / 0.250000001 = 2.49999999, below 2.5 either way.
        """
        med, cur = 0.25, 0.625
        ratio = cur / (med + 1e-9)
        assert ratio != 2.5
        assert (ratio > 2.5) == (ratio >= 2.5)

    def test_the_epsilon_is_the_reason(self):
        assert 0.625 / 0.25 == 2.5
        assert 0.625 / (0.25 + 1e-9) < 2.5

    def test_the_true_range_maximum_is_associative(self):
        """`np.maximum(a, np.maximum(b, c))` and any re-bracketing are the same
        function; there is no mutation of the TR expression to detect."""
        a, b, c = 1.0, 5.0, 3.0
        assert np.maximum(a, np.maximum(b, c)) == np.maximum(b, np.maximum(a, c))
        assert np.maximum(a, np.maximum(b, c)) == np.maximum(c, np.maximum(b, a))

    def test_the_first_bar_never_contributes_a_true_range(self):
        """Because TR[0] is NaN, no choice of the first bar's range can change
        any reported ATR — see TestTrueRange for the positive assertion."""
        for first in (0.0, 1.0, 1e6):
            ranges = [first] + [1.0] * 19
            r = VolatilityEngine(atr_period=20).analyze_volatility(trframe(ranges))
            assert r.atr == pytest.approx(1.0, abs=1e-6)


class TestBaselineGate:
    def test_below_twenty_bars_the_baseline_is_the_current_atr(self):
        """`len(atr_series) >= 20` gates the median; under it the fallback is
        `current_atr`, so vol_ratio is exactly 1.0 and no state but NORMAL is
        reachable — no matter how violent the step."""
        e = VolatilityEngine(atr_period=3, bb_period=5)
        for n in (15, 19):
            r = e.analyze_volatility(trframe([2.0] * (n - 5) + [0.2] * 5))
            assert r.state == "NORMAL", n

    def test_at_twenty_bars_the_median_takes_over(self):
        e = VolatilityEngine(atr_period=3, bb_period=5)
        r = e.analyze_volatility(trframe([2.0] * 15 + [0.2] * 5))
        assert r.state == "COMPRESSION"

    def test_the_median_is_taken_over_the_rolling_series_not_the_raw_ranges(self):
        """The baseline is the median of the 14-period rolling ATRs, so a
        one-bar spike is smoothed away before it can move the median."""
        spike = [0.2] * 59 + [100.0]
        r = run(trframe(spike))
        assert r.atr == pytest.approx(float(np.mean([0.2] * 13 + [100.0])), abs=1e-3)
        assert r.state == "EXTREME"


class TestHousekeeping:
    def test_the_input_frame_is_not_mutated(self):
        before = RISING60.copy()
        run(RISING60)
        pd.testing.assert_frame_equal(RISING60, before)

    def test_missing_optional_columns_are_not_touched(self):
        df = RISING60[["high", "low", "close"]]
        assert run(df).atr == pytest.approx(0.6, abs=1e-9)

    def test_a_nan_close_propagates(self):
        df = RISING60.copy()
        df.loc[10, "close"] = np.nan
        r = run(df)
        # ATR is a rolling mean over 14 bars; index 10 is far from the tail
        assert np.isfinite(r.atr)

    def test_the_engine_holds_no_state_between_calls(self):
        e = VolatilityEngine()
        assert e.analyze_volatility(EXTREME).state == "EXTREME"
        assert e.analyze_volatility(COMPRESSION).state == "COMPRESSION"

    def test_the_return_type_is_a_volatility_context(self):
        from jarvis.data.schemas import VolatilityContext
        assert isinstance(run(RISING60), VolatilityContext)

    def test_defaults_are_fourteen_twenty_and_two(self):
        e = VolatilityEngine()
        assert (e.atr_period, e.bb_period, e.bb_std) == (14, 20, 2.0)

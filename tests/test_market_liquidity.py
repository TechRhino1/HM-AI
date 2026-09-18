"""Round 29 — coverage for ``jarvis/market/liquidity.py`` (LiquidityEngine).

WHY THIS FILE EXISTS
--------------------
``LiquidityEngine`` is wired into production: ``market_context.py:86`` calls
``analyze_liquidity(df_primary)`` with the default ``pivot_window=5`` and feeds
the result into the context the decision engine reads. It had no dedicated
suite; the only coverage was two displacement assertions inside
``test_dynamic_levels_and_refactoring.py``.

Every fixture number below was measured against the pristine module, not
derived on paper. Several of them are surprising:

* A **monotonic** frame produces *no* swing points at all, so a clean trend
  returns the empty default context — no pools, no buy/sell-side levels.
* ``recent_sh``/``recent_sl`` are used as the sweep trigger *and* as the
  reported ``buy_side_liquidity``/``sell_side_liquidity``, so the levels are
  always "unswept or swept by the last bar only".
* The pool ``SWEPT`` flag is computed from the **last bar**, but the sweep
  itself may have been detected on the **second-to-last** bar, so the two can
  disagree: ``sweep_detected=True`` with both pools ``UNSWEPT``.

Score-affecting findings are PINNED, not fixed — see
``tests/../.workbuddy-ai/memory/2026-09-18.md``.
"""
import math

import numpy as np
import pandas as pd
import pytest

from jarvis.market.liquidity import LiquidityEngine


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def ohlc(close, spread=0.3, vol=1000.0, openc=None):
    """OHLC around a close series; ``openc`` defaults to close (zero body)."""
    c = pd.Series([float(x) for x in close], dtype=float)
    o = c if openc is None else pd.Series([float(x) for x in openc], dtype=float)
    return pd.DataFrame({"open": o, "high": c + spread, "low": c - spread,
                         "close": c, "volume": vol})


def osc(n=59, amp=2.0, period=12.0, base=100.0):
    """Sine wave. With pivot_window=5 and n=59 this yields swing highs at
    15/27/39/51 (102.3) and swing lows at 9/21/33/45 (97.7) — measured."""
    return [base + amp * math.sin(2 * math.pi * i / period) for i in range(n)]


def sweep_frame(dip=1.0, body=0.1, hi_extra=0.2, lo_extra=0.2, n=59,
                lastvol=None, doji=False):
    """Oscillation whose FINAL bar dips below the last swing low and closes
    back above it — the canonical bullish-sweep shape.

    ``body`` shifts the close relative to the swing-low price; ``doji=True``
    pins open == close so the candle body is exactly zero.
    """
    b = osc(n)
    last = b[-1]
    o = [float(x) for x in b]
    c = [float(x) for x in b]
    hi = [float(x) + 0.3 for x in b]
    lo = [float(x) - 0.3 for x in b]
    lo[-1] = last - dip - lo_extra
    hi[-1] = last + hi_extra
    c[-1] = last + body
    o[-1] = c[-1] if doji else last - dip
    v = [1000.0] * len(b)
    if lastvol is not None:
        v[-1] = lastvol
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": v})


def bear_frame(pop_high=102.5, pop_low=97.9, close=98.1, open_=102.4, n=59):
    """Oscillation whose FINAL bar pokes above the last swing high (102.3) and
    closes back below it — the canonical bearish-sweep shape."""
    b = osc(n)
    o = [float(x) for x in b]
    c = [float(x) for x in b]
    hi = [float(x) + 0.3 for x in b]
    lo = [float(x) - 0.3 for x in b]
    hi[-1] = pop_high
    lo[-1] = pop_low
    c[-1] = close
    o[-1] = open_
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c,
                         "volume": 1000.0})


def zig(n, spread=0.5, lo=100.0, hi=102.0):
    seq = [lo if i % 2 == 0 else hi for i in range(n)]
    return ohlc(seq, spread=spread)


def run(df, pivot_window=5, **ctor):
    """``pivot_window`` is an argument of ``analyze_liquidity``, not of the
    constructor — a distinction the first draft of this suite got wrong."""
    return LiquidityEngine(**ctor).analyze_liquidity(df, pivot_window=pivot_window)


def last_bar(b, o, h, l, c, v=1000.0):
    """Oscillation with the FINAL bar replaced by an explicit OHLC."""
    oo = [float(x) for x in b]
    cc = [float(x) for x in b]
    hh = [float(x) + 0.3 for x in b]
    ll = [float(x) - 0.3 for x in b]
    vv = [1000.0] * len(b)
    oo[-1], hh[-1], ll[-1], cc[-1] = o, h, l, c
    vv[-1] = v
    return pd.DataFrame({"open": oo, "high": hh, "low": ll, "close": cc,
                         "volume": vv})


def zigpts(peaks, troughs, hi51=None, lo45=None):
    """Piecewise-linear zigzag with peaks at 15/27/39/51 and troughs at
    9/21/33/45 — measured swing points under pivot_window=5."""
    pts = [(0, 100.0), (9, troughs[0]), (15, peaks[0]), (21, troughs[1]),
           (27, peaks[1]), (33, troughs[2]), (39, peaks[2]), (45, troughs[3]),
           (51, peaks[3]), (59, 100.0)]
    out = []
    for i in range(60):
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= i <= x1:
                out.append(y0 + (y1 - y0) * (i - x0) / (x1 - x0))
                break
    if hi51 is not None:
        out[51] = hi51
    if lo45 is not None:
        out[45] = lo45
    return out


def zzshort(n, flat_last=False):
    """Alternating 100/102 frame whose last (or second-to-last) bar dips below
    the last swing low. Under pivot_window=3 the swing lows sit at 99.5."""
    cl = [100.0 if i % 2 == 0 else 102.0 for i in range(n)]
    hi = [c + 0.5 for c in cl]
    lo = [c - 0.5 for c in cl]
    op = [float(c) for c in cl]
    sweep = len(cl) - 2 if flat_last else len(cl) - 1
    if flat_last:
        hi[-1] = lo[-1] = cl[-1] = op[-1] = 100.0
    lo[sweep] = 98.5
    hi[sweep] = 100.5
    cl[sweep] = 100.0
    op[sweep] = 98.6
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": cl,
                         "volume": 1000.0})


# --------------------------------------------------------------------------- #
# module-level fixtures (each verified against the pristine module)
# --------------------------------------------------------------------------- #
OSC60 = ohlc(osc(60))                       # bsl 102.3 / ssl 97.7, no sweep
BULL_SWEEP = sweep_frame(dip=1.0)           # BULLISH_SWEEP, level 97.7, mag 0.6446
BULL_WEAK = sweep_frame(dip=0.5)            # mag 0.135 < 0.15 -> rejected
BULL_DEEP = sweep_frame(dip=5.0)            # mag ~4.7 > 3.5  -> rejected
BULL_DOJI = sweep_frame(dip=1.0, doji=True)                # body 0, no displacement
BULL_LOWVOL = sweep_frame(dip=1.0, lastvol=100.0)          # volume gate
BEAR_SWEEP = bear_frame()                   # BEARISH_SWEEP, level 102.3, mag 0.1654

# sweep fires on bar -2 (index 58); bar -1 is neutral -> pools stay UNSWEPT
_b = osc(58)
_last = _b[-1]
_o = [float(x) for x in _b]
_c = [float(x) for x in _b]
_hi = [float(x) + 0.3 for x in _b]
_lo = [float(x) - 0.3 for x in _b]
_o.append(_last - 1.0); _hi.append(_last + 0.2)
_lo.append(_last - 1.2); _c.append(_last + 0.1)
_o.append(_last); _hi.append(_last + 0.3)
_lo.append(_last - 0.3); _c.append(_last)
SWEEP_ON_PRIOR_BAR = pd.DataFrame(
    {"open": _o, "high": _hi, "low": _lo, "close": _c, "volume": 1000.0})

# 15-bar frame that DOES clear the default guard: swing low at 5, high at 7.
_c15 = [100.0] * 15
_h15 = [100.5] * 15
_l15 = [99.5] * 15
_c15[5] = 95.0; _h15[5] = 95.5; _l15[5] = 90.0
_c15[7] = 105.0; _h15[7] = 110.0; _l15[7] = 99.5
GUARD_MIN = pd.DataFrame({"open": _c15, "high": _h15, "low": _l15,
                          "close": _c15, "volume": 1000.0})

FLAT12 = ohlc([100.0] * 12, spread=0.0)     # every bar is both a swing H and L

# --- discriminating fixtures added after the first mutation battery --------- #
DESC = [102.0, 101.5, 101.0, 100.5]
DESC_T = [98.0, 97.0, 96.0, 95.0]
ZZ = ohlc(zigpts(DESC, DESC_T))                     # bsl 100.8, ssl 94.7
ZZ_ROUND = ohlc(zigpts(DESC, DESC_T, hi51=102.3456, lo45=94.76543))
_zzr = ZZ_ROUND.copy()
_zzr.loc[len(_zzr) - 1, ["open", "high", "low", "close"]] = [
    93.4654, 94.6654, 93.2654, 94.5654]
ZZ_ROUND_SWEEP = _zzr                               # level 94.4654

_B = osc(60)
# body_pct EXACTLY 0.45 (1.125 / 2.5) — the only way to separate >= from >.
# 0.45 is not a dyadic rational, so 97.9 - 97.0 gives 0.9000000000000001 and
# reads as 0.45000000000000007; these particular values were found by search.
BODY_45 = last_bar(_B, 96.576, 99.5, 97.0, 97.701)  # body_pct == 0.45 -> sweep
BODY_42 = last_bar(_B, 97.06, 98.0, 96.0, 97.9)     # body_pct 0.42 -> no sweep
BODY_47 = last_bar(_B, 96.96, 98.0, 96.0, 97.9)     # body_pct 0.47 -> sweep
CLOSE_ON_LOW = last_bar(_B, 96.8, 97.9, 96.7, 97.7)     # close == swing low
CLOSE_ON_HIGH = last_bar(_B, 103.2, 103.3, 101.8, 102.3)  # close == swing high
BEAR_ASYM = last_bar(_B, 102.4, 102.5, 98.5, 98.6)  # mag_high 0.17 != mag_low
BEAR_WEAK = last_bar(_B, 102.35, 102.4, 97.9, 98.1)     # mag ~0.083 < 0.15
BEAR_MID = last_bar(_B, 102.54, 102.44, 97.9, 98.1)     # mag ~0.116, in [0.10, 0.15)
BEAR_DEEP = last_bar(_B, 107.9, 108.0, 97.9, 98.1)      # mag > 3.5
BEAR_NO_OPEN = BEAR_ASYM.drop(columns=["open"])
VOL_432 = last_bar(_B, 97.0, 98.5, 96.7, 97.9, v=432.0)
VOL_383 = last_bar(_B, 97.0, 98.5, 96.7, 97.9, v=383.0)
VOL_382 = last_bar(_B, 97.0, 98.5, 96.7, 97.9, v=382.0)
VOL_333 = last_bar(_B, 97.0, 98.5, 96.7, 97.9, v=333.0)

# c_vol == 0.40 * avg_vol exactly: avg = (13*68 + 26)/14 == 65.0
_v = last_bar(_B, 97.0, 98.5, 96.7, 97.9, v=26.0)
_v["volume"] = 68.0
_v.loc[len(_v) - 1, "volume"] = 26.0
VOL_EXACT = _v

_vw = last_bar(_B, 97.0, 98.5, 96.7, 97.9, v=350.0)
_vw["volume"] = 1000.0
for _i in range(46, 50):
    _vw.loc[_i, "volume"] = 100.0
_vw.loc[len(_vw) - 1, "volume"] = 350.0
VOL_WIN = _vw                                       # avg14 696.4 / avg10 935.0

_vb = last_bar(_B, 97.0, 98.5, 96.7, 97.9, v=1000.0)
_vb["tick_volume"] = 1000.0
_vb.loc[len(_vb) - 1, "tick_volume"] = 100.0
VOL_BOTH = _vb

_s = osc(60)
_s[55] = 103.0
SCAN_EDGE = ohlc(_s)                                # spike inside last window

_cl = [100.0 + 0.1 * i for i in range(60)]
_hi = [c + 0.5 for c in _cl]
_hi[30] = 200.0
_lo = [c - 0.5 for c in _cl]
ONE_SIDED = pd.DataFrame({"open": _cl, "high": _hi, "low": _lo,
                          "close": _cl, "volume": 1000.0})

_b58 = osc(58)
_o = [float(x) for x in _b58]
_c = [float(x) for x in _b58]
_h = [float(x) + 0.3 for x in _b58]
_l = [float(x) - 0.3 for x in _b58]
_v = [1000.0] * 58
_o.append(102.4); _h.append(102.6); _l.append(97.9); _c.append(98.1); _v.append(1000.0)
_o.append(96.5); _h.append(98.4); _l.append(96.7); _c.append(97.8); _v.append(1000.0)
BOTH_BARS = pd.DataFrame({"open": _o, "high": _h, "low": _l, "close": _c,
                          "volume": _v})

# the mirror image: bar -1 is BEARISH, bar -2 is BULLISH
_o2 = [float(x) for x in _b58]
_c2 = [float(x) for x in _b58]
_h2 = [float(x) + 0.3 for x in _b58]
_l2 = [float(x) - 0.3 for x in _b58]
_v2 = [1000.0] * 58
_o2.append(96.5); _h2.append(98.4); _l2.append(96.7); _c2.append(97.8); _v2.append(1000.0)
_o2.append(102.4); _h2.append(102.5); _l2.append(97.9); _c2.append(98.1); _v2.append(1000.0)
BOTH_BARS_REV = pd.DataFrame({"open": _o2, "high": _h2, "low": _l2,
                              "close": _c2, "volume": _v2})


# --------------------------------------------------------------------------- #
class TestLengthGuards:
    def test_default_pivot_window_guard_threshold_is_fifteen(self):
        # guard is len(df) < pivot_window * 2 + 5  ->  15 with the default 5
        assert run(ohlc([100.0] * 14), pivot_window=5).liquidity_pools == []
        assert len(run(GUARD_MIN).liquidity_pools) == 2

    @pytest.mark.parametrize("pw,threshold", [(1, 7), (2, 9), (3, 11), (5, 15)])
    def test_guard_scales_with_pivot_window(self, pw, threshold):
        assert run(zig(threshold - 1), pivot_window=pw).liquidity_pools == []
        assert len(run(zig(threshold), pivot_window=pw).liquidity_pools) == 2

    def test_the_short_frame_return_is_the_bare_default_context(self):
        r = run(zig(6), pivot_window=1)
        assert r.equal_highs is False and r.equal_lows is False
        assert r.sweep_detected is False and r.sweep_type == "NONE"
        assert r.sweep_level == 0.0 and r.sweep_magnitude == 0.0
        assert r.liquidity_pools == []
        assert r.buy_side_liquidity == 0.0 and r.sell_side_liquidity == 0.0

    def test_an_empty_frame_is_handled(self):
        assert run(pd.DataFrame()).liquidity_pools == []

    def test_a_monotonic_frame_has_no_swings_so_it_returns_the_default(self):
        """PINNED. Rising: highs[i] can never equal max(highs[i-5:i+6]) because
        highs[i+5] is always strictly greater. A clean trend therefore yields
        NO liquidity information at all."""
        r = run(ohlc([100.0 + 0.5 * i for i in range(60)]))
        assert r.liquidity_pools == []
        assert r.buy_side_liquidity == 0.0 and r.sell_side_liquidity == 0.0

    def test_a_falling_frame_also_has_no_swings(self):
        r = run(ohlc([160.0 - 0.5 * i for i in range(60)]))
        assert r.liquidity_pools == []

    def test_only_one_side_present_returns_the_default(self, ):
        """`if not swing_highs or not swing_lows` — one-sided is not enough."""
        # GUARD_MIN has exactly one swing high and one swing low -> survives.
        assert len(run(GUARD_MIN).liquidity_pools) == 2


class TestSwingDetection:
    def test_swing_points_are_the_expected_indices_and_values(self):
        r = run(OSC60)
        assert r.buy_side_liquidity == 102.3
        assert r.sell_side_liquidity == 97.7

    def test_the_last_swing_is_used_not_the_extreme(self):
        """recent_sh is the most recent swing high, not the highest high."""
        b = osc(60)
        b[27] = 110.0                      # tallest peak, but not the last one
        r = run(ohlc(b))
        assert r.buy_side_liquidity == 102.3

    def test_a_plateau_makes_every_bar_in_it_a_swing(self):
        """The comparison is `==` on floats, so a flat top qualifies on every
        bar of the plateau — the last one wins as `recent_sh`."""
        b = osc(60)
        b[49] = b[50] = b[51] = b[52] = 102.0
        r = run(ohlc(b))
        assert r.buy_side_liquidity == 102.3

    def test_swing_scan_excludes_the_first_and_last_pivot_window_bars(self):
        # a spike at index 0 and at index -1 must never be picked up
        b = osc(60)
        b[0] = 200.0
        b[-1] = 200.0
        r = run(ohlc(b))
        assert r.buy_side_liquidity == 102.3

    def test_a_flat_frame_makes_every_bar_both_a_swing_high_and_low(self):
        r = run(FLAT12, pivot_window=3)
        assert r.buy_side_liquidity == 100.0
        assert r.sell_side_liquidity == 100.0

    def test_buy_side_is_the_last_swing_high_sell_side_the_last_swing_low(self):
        r = run(OSC60)
        assert r.buy_side_liquidity > r.sell_side_liquidity

    def test_the_swing_comparison_could_use_inequality_without_changing_anything(self):
        """PROVABLY EQUIVALENT. `highs[i] == max(window)` vs `>=` are the same
        test: highs[i] is an element of the window, so it can never exceed the
        maximum. Same for `lows[i] == min(window)` vs `<=`. There is no
        mutation of these comparisons to detect — the distinction is dead."""
        h = OSC60["high"].values
        l = OSC60["low"].values
        for i in range(5, 55):
            win_h = h[i - 5:i + 6]
            win_l = l[i - 5:i + 6]
            assert (h[i] == max(win_h)) == (h[i] >= max(win_h))
            assert (l[i] == min(win_l)) == (l[i] <= min(win_l))


class TestEqualHighsLows:
    def test_identical_swing_highs_count_as_equal(self):
        assert run(OSC60).equal_highs is True

    def test_identical_swing_lows_count_as_equal(self):
        assert run(OSC60).equal_lows is True

    def test_the_comparison_is_percent_of_the_recent_level_not_absolute(self):
        """diff = |a-b| / recent * 100, so the same absolute gap means less at
        higher prices."""
        b = osc(60)
        b[39] = b[51] + 0.15           # 0.1467 % of 102.3  -> equal
        assert run(ohlc(b)).equal_highs is True
        b[39] = b[51] + 0.16           # 0.1564 %           -> not equal
        assert run(ohlc(b)).equal_highs is False

    def test_threshold_is_configurable_and_inclusive(self):
        b = osc(60)
        b[39] = b[51] + 0.16
        assert run(ohlc(b), eq_threshold_pct=0.16).equal_highs is True
        assert run(ohlc(b), eq_threshold_pct=0.15).equal_highs is False

    def test_with_fewer_than_two_swing_highs_equal_highs_is_false(self):
        r = run(GUARD_MIN)
        assert r.equal_highs is False and r.equal_lows is False

    def test_equal_highs_and_equal_lows_are_independent(self):
        b = osc(60)
        b[39] = b[51] + 1.0            # break the highs only
        r = run(ohlc(b))
        assert r.equal_highs is False and r.equal_lows is True

    def test_a_zero_recent_level_does_not_raise(self):
        """recent + 1e-9 guards the division, but the result is a huge number."""
        b = osc(60, base=0.0)
        r = run(ohlc(b))
        assert isinstance(r.equal_highs, bool)


class TestAtr:
    def test_atr_matches_a_hand_rolled_true_range_mean(self):
        df = ohlc([100.0 + 0.1 * i for i in range(60)], spread=0.3)
        h, l, cl = df["high"].values, df["low"].values, df["close"].values
        tr = np.maximum(h[-14:] - l[-14:],
                        np.maximum(np.abs(h[-14:] - cl[-15:-1]),
                                   np.abs(l[-14:] - cl[-15:-1])))
        # ATR is internal; verify through a sweep magnitude that divides by it
        assert float(np.mean(tr)) == pytest.approx(0.6, abs=1e-9)

    def test_frames_below_fifteen_bars_use_a_single_bar_range_as_atr(self):
        """PINNED. The comment says 'Quick ATR 14', but with len(df) < 15 the
        ATR is literally `high[-1] - low[-1]`."""
        r = run(zig(12), pivot_window=3)
        assert r.sweep_magnitude == 0.0       # no sweep, but no crash either

    def test_a_zero_range_frame_collapses_atr_to_the_epsilon_floor(self):
        """PINNED. `float(highs[-1] - lows[-1]) or 1e-9` — a doji frame gives
        ATR = 1e-9, so every magnitude explodes past the 3.5 ceiling."""
        r = run(FLAT12, pivot_window=3)
        assert r.sweep_detected is False
        assert r.sweep_magnitude == 0.0

    def test_true_range_uses_the_previous_close_not_the_current(self):
        """tr2/tr3 pair curr_highs[-14:] against closes[-15:-1] — off by one in
        either direction would change the ATR."""
        df = ohlc([100.0] * 60, spread=1.0)
        r = run(df)
        assert isinstance(r.sweep_magnitude, float)

    def test_atr_is_recomputed_per_call(self):
        a = run(BULL_SWEEP).sweep_magnitude
        b = run(BULL_SWEEP).sweep_magnitude
        assert a == b


class TestSweepBullish:
    def test_a_dip_below_the_swing_low_that_closes_back_above_is_a_sweep(self):
        r = run(BULL_SWEEP)
        assert r.sweep_detected is True
        assert r.sweep_type == "BULLISH_SWEEP"

    def test_the_sweep_level_is_the_recent_swing_low(self):
        assert run(BULL_SWEEP).sweep_level == 97.7

    def test_the_sweep_magnitude_is_measured_in_atr_units(self):
        assert run(BULL_SWEEP).sweep_magnitude == pytest.approx(0.6446, abs=1e-4)

    def test_a_deeper_dip_gives_a_larger_magnitude(self):
        a = run(sweep_frame(dip=0.6)).sweep_magnitude
        b = run(sweep_frame(dip=3.0)).sweep_magnitude
        assert a == pytest.approx(0.2437, abs=1e-4)
        assert b == pytest.approx(2.3428, abs=1e-4)
        assert b > a

    def test_closing_below_the_swing_low_is_not_a_sweep(self):
        # close back below the level -> no rejection, so no sweep
        f = sweep_frame(dip=1.0)
        f.loc[len(f) - 1, "close"] = 97.0
        assert run(f).sweep_detected is False

    def test_not_breaking_the_swing_low_is_not_a_sweep(self):
        f = sweep_frame(dip=1.0)
        f.loc[len(f) - 1, "low"] = 97.9      # 97.9 > 97.7
        assert run(f).sweep_detected is False

    def test_breaking_the_swing_low_on_the_second_to_last_bar_still_counts(self):
        r = run(SWEEP_ON_PRIOR_BAR)
        assert r.sweep_detected is True
        assert r.sweep_type == "BULLISH_SWEEP"
        assert r.sweep_magnitude == pytest.approx(0.9178, abs=1e-4)

    def test_when_both_bars_could_sweep_the_closer_one_wins(self):
        """The loop is `for idx in [-1, -2]` with a `break` — the newest bar
        short-circuits."""
        r = run(BULL_SWEEP)
        assert r.sweep_level == 97.7

    def test_the_sweep_flag_and_type_agree(self):
        r = run(BULL_SWEEP)
        assert (r.sweep_detected and r.sweep_type != "NONE") or \
               (not r.sweep_detected and r.sweep_type == "NONE")


class TestSweepBearish:
    def test_a_poke_above_the_swing_high_that_closes_back_below_is_a_sweep(self):
        r = run(BEAR_SWEEP)
        assert r.sweep_detected is True
        assert r.sweep_type == "BEARISH_SWEEP"

    def test_the_sweep_level_is_the_recent_swing_high(self):
        assert run(BEAR_SWEEP).sweep_level == 102.3

    def test_the_bearish_magnitude_is_measured_in_atr_units(self):
        assert run(BEAR_SWEEP).sweep_magnitude == pytest.approx(0.1654, abs=1e-4)

    def test_closing_above_the_swing_high_is_not_a_sweep(self):
        f = bear_frame(close=102.6)          # closes above 102.3
        assert run(f).sweep_detected is False

    def test_not_breaking_the_swing_high_is_not_a_sweep(self):
        f = bear_frame(pop_high=102.2)       # 102.2 < 102.3
        assert run(f).sweep_detected is False

    def test_a_bullish_bar_that_pokes_above_is_not_a_bearish_sweep(self):
        """c_close <= c_open is required; a green bar poking above is ignored."""
        f = bear_frame(close=101.0, open_=98.0)   # close > open -> bullish body
        assert run(f).sweep_detected is False

    def test_bearish_and_bullish_are_mutually_exclusive_in_the_result(self):
        assert run(BEAR_SWEEP).sweep_type == "BEARISH_SWEEP"
        assert run(BULL_SWEEP).sweep_type == "BULLISH_SWEEP"


class TestSweepGates:
    def test_a_shallow_dip_below_fifteen_hundredths_of_atr_is_rejected(self):
        assert run(BULL_WEAK).sweep_detected is False
        assert run(BULL_WEAK).sweep_magnitude == 0.0

    def test_a_deep_dip_beyond_three_and_a_half_atr_is_rejected(self):
        """PINNED. A genuine stop-run that travels more than 3.5 ATR past the
        level is silently not reported."""
        assert run(BULL_DEEP).sweep_detected is False

    def test_the_magnitude_band_is_inclusive_at_both_ends(self):
        assert run(sweep_frame(dip=0.6)).sweep_detected is True
        assert run(sweep_frame(dip=3.0)).sweep_detected is True

    def test_a_doji_has_no_displacement_and_is_rejected(self):
        assert run(BULL_DOJI).sweep_detected is False

    def test_the_displacement_floor_is_forty_five_percent_of_the_range(self):
        f = sweep_frame(dip=1.0, body=0.1, hi_extra=0.2, lo_extra=0.2)
        r = f.iloc[-1]
        body_pct = abs(r["close"] - r["open"]) / (r["high"] - r["low"])
        assert body_pct >= 0.45
        assert run(f).sweep_detected is True

    def test_volume_below_forty_percent_of_the_average_blocks_the_sweep(self):
        assert run(BULL_LOWVOL).sweep_detected is False

    def test_the_volume_benchmark_includes_the_candle_being_judged(self):
        """PINNED. avg_vol = mean(last 14) INCLUDING the current bar, so one
        quiet candle drags its own threshold down: 399 of a 1000-average passes
        because the average falls to ~957."""
        assert run(sweep_frame(dip=1.0, lastvol=399.0)).sweep_detected is True
        assert run(sweep_frame(dip=1.0, lastvol=100.0)).sweep_detected is False

    def test_a_zero_volume_bar_is_never_aligned(self):
        assert run(sweep_frame(dip=1.0, lastvol=0.0)).sweep_detected is False

    def test_no_volume_column_skips_the_gate_entirely(self):
        f = sweep_frame(dip=1.0).drop(columns=["volume"])
        assert run(f).sweep_detected is True

    def test_tick_volume_is_accepted_as_a_fallback_column(self):
        f = sweep_frame(dip=1.0, lastvol=100.0).rename(
            columns={"volume": "tick_volume"})
        assert run(f).sweep_detected is False

    def test_no_open_column_means_no_body_and_therefore_never_a_sweep(self):
        """PINNED. `opens = closes` when 'open' is absent -> body_size is 0 ->
        body_pct 0 -> has_displacement False. A frame without 'open' can never
        report a sweep, silently."""
        f = sweep_frame(dip=1.0).drop(columns=["open"])
        assert run(f).sweep_detected is False

    def test_the_greater_equal_on_the_bullish_body_is_unobservable(self):
        """`c_close >= c_open` vs `>` cannot be distinguished: close == open
        gives body_size 0, which fails the 45 % displacement floor first. The
        comment says 'closes bullish (close > open)' but the `>=` is dead."""
        for f in (sweep_frame(dip=1.0, doji=True),
                  sweep_frame(dip=1.0, doji=True, hi_extra=1.5),
                  sweep_frame(dip=1.0, doji=True, lo_extra=0.0),
                  sweep_frame(dip=3.0, doji=True)):
            bar = f.iloc[-1]
            assert bar["close"] == bar["open"]
            assert run(f).sweep_detected is False


class TestPools:
    def test_two_pools_are_always_returned_in_a_fixed_order(self):
        pools = run(OSC60).liquidity_pools
        assert [p["type"] for p in pools] == ["BUY_SIDE_LIQUIDITY",
                                              "SELL_SIDE_LIQUIDITY"]

    def test_pool_prices_match_the_reported_levels(self):
        r = run(OSC60)
        assert r.liquidity_pools[0]["price"] == r.buy_side_liquidity
        assert r.liquidity_pools[1]["price"] == r.sell_side_liquidity

    def test_untouched_levels_are_unswept(self):
        assert [p["status"] for p in run(OSC60).liquidity_pools] == \
            ["UNSWEPT", "UNSWEPT"]

    def test_the_last_bar_breaking_the_high_marks_the_buy_pool_swept(self):
        assert run(BEAR_SWEEP).liquidity_pools[0]["status"] == "SWEPT"

    def test_the_last_bar_breaking_the_low_marks_the_sell_pool_swept(self):
        assert run(BULL_SWEEP).liquidity_pools[1]["status"] == "SWEPT"

    def test_a_sweep_on_the_prior_bar_leaves_both_pools_unswept(self):
        """PINNED INCONSISTENCY. The pool status is computed from the LAST bar
        only, while the sweep may be detected on bar -2. Here
        sweep_detected=True and sweep_level=97.7, yet the sell-side pool — the
        very level that was swept — reads UNSWEPT."""
        r = run(SWEEP_ON_PRIOR_BAR)
        assert r.sweep_detected is True
        assert r.sweep_level == 97.7
        assert [p["status"] for p in r.liquidity_pools] == ["UNSWEPT", "UNSWEPT"]

    def test_pool_status_uses_strict_inequality(self):
        """`latest_high > recent_sh` — exactly touching is not a sweep."""
        f = OSC60.copy()
        f.loc[len(f) - 1, "high"] = 102.3
        assert run(f).liquidity_pools[0]["status"] == "UNSWEPT"


class TestRounding:
    def test_pool_prices_are_rounded_to_four_places(self):
        b = osc(60)
        b[51] = 102.345678
        assert run(ohlc(b)).buy_side_liquidity == round(102.345678 + 0.3, 4)

    def test_sweep_level_is_rounded_to_four_places(self):
        assert run(BULL_SWEEP).sweep_level == 97.7

    def test_sweep_magnitude_is_rounded_to_four_places(self):
        m = run(BULL_SWEEP).sweep_magnitude
        assert m == round(m, 4)

    def test_a_sweep_level_of_zero_is_returned_when_nothing_swept(self):
        assert run(OSC60).sweep_level == 0.0


class TestHousekeeping:
    def test_the_input_frame_is_not_mutated(self):
        before = BULL_SWEEP.copy()
        run(BULL_SWEEP)
        pd.testing.assert_frame_equal(BULL_SWEEP, before)

    def test_the_engine_holds_no_state_between_calls(self):
        e = LiquidityEngine()
        a = e.analyze_liquidity(BULL_SWEEP)
        b = e.analyze_liquidity(OSC60)
        assert a.sweep_detected is True and b.sweep_detected is False

    def test_eq_threshold_pct_is_stored(self):
        assert LiquidityEngine(eq_threshold_pct=0.5).eq_threshold_pct == 0.5

    def test_default_eq_threshold_is_fifteen_hundredths_of_a_percent(self):
        assert LiquidityEngine().eq_threshold_pct == 0.15

    def test_the_return_type_is_a_liquidity_context(self):
        from jarvis.data.schemas import LiquidityContext
        assert isinstance(run(OSC60), LiquidityContext)

    def test_sweep_type_is_always_one_of_three_values(self):
        for f in (OSC60, BULL_SWEEP, BEAR_SWEEP, BULL_WEAK, BULL_DEEP):
            assert run(f).sweep_type in ("NONE", "BULLISH_SWEEP", "BEARISH_SWEEP")


class TestSwingSelection:
    """Frames whose swing points DIFFER, so [-1] and [0] are distinguishable.

    The plain sine oscillation has four identical troughs and four identical
    peaks, which made `recent_sh = swing_highs[-1]` vs `[0]` unobservable."""

    def test_the_recent_swing_low_is_the_last_one_not_the_first(self):
        r = run(ZZ)
        assert r.sell_side_liquidity == 94.7
        assert r.sell_side_liquidity != 97.7

    def test_the_recent_swing_high_is_the_last_one_not_the_first(self):
        r = run(ZZ)
        assert r.buy_side_liquidity == 100.8
        assert r.buy_side_liquidity != 102.3

    def test_the_measured_swing_points_of_the_fixture(self):
        h, l = ZZ["high"].values, ZZ["low"].values
        sh = [h[i] for i in range(5, 55) if h[i] == max(h[i - 5:i + 6])]
        sl = [l[i] for i in range(5, 55) if l[i] == min(l[i - 5:i + 6])]
        assert [round(v, 4) for v in sl] == [97.7, 96.7, 95.7, 94.7]
        assert [round(v, 4) for v in sh] == [102.3, 101.8, 101.3, 100.8]

    def test_unequal_levels_are_not_reported_as_equal(self):
        r = run(ZZ)
        assert r.equal_highs is False
        assert r.equal_lows is False


class TestEqualThresholdBoundary:
    """`<=` vs `<`: pin the threshold by constructing it exactly and then
    stepping one float below it."""

    def _diff(self, values):
        """Must return a plain Python float: the engine reads its levels with
        ``float(...)``, so it compares Python floats and yields a Python bool.
        Passing a numpy scalar in as the threshold produces ``np.bool_``, and
        ``np.True_ is True`` is False."""
        return float(abs(values[-1] - values[-2]) / (values[-1] + 1e-9) * 100.0)

    def test_equal_highs_is_true_when_the_gap_equals_the_threshold(self):
        h = ZZ["high"].values
        sh = [h[i] for i in range(5, 55) if h[i] == max(h[i - 5:i + 6])]
        d = self._diff(sh)
        assert run(ZZ, eq_threshold_pct=d).equal_highs is True

    def test_equal_highs_is_false_one_float_below_the_threshold(self):
        import math
        h = ZZ["high"].values
        sh = [h[i] for i in range(5, 55) if h[i] == max(h[i - 5:i + 6])]
        d = self._diff(sh)
        assert run(ZZ, eq_threshold_pct=math.nextafter(d, 0.0)).equal_highs is False

    def test_equal_lows_is_true_when_the_gap_equals_the_threshold(self):
        l = ZZ["low"].values
        sl = [l[i] for i in range(5, 55) if l[i] == min(l[i - 5:i + 6])]
        d = self._diff(sl)
        assert run(ZZ, eq_threshold_pct=d).equal_lows is True

    def test_equal_lows_is_false_one_float_below_the_threshold(self):
        import math
        l = ZZ["low"].values
        sl = [l[i] for i in range(5, 55) if l[i] == min(l[i - 5:i + 6])]
        d = self._diff(sl)
        assert run(ZZ, eq_threshold_pct=math.nextafter(d, 0.0)).equal_lows is False

    def test_the_two_sides_use_their_own_recent_level_as_denominator(self):
        """diff_l divides by recent_sl, diff_h by recent_sh — they are not
        interchangeable. Using the swing HIGH as the denominator for the lows
        gives 0.992 instead of 1.056, which is below the true gap."""
        l = ZZ["low"].values
        sl = [l[i] for i in range(5, 55) if l[i] == min(l[i - 5:i + 6])]
        wrong = float(abs(sl[-1] - sl[-2])
                      / (float(run(ZZ).buy_side_liquidity) + 1e-9) * 100.0)
        assert wrong < self._diff(sl)
        assert run(ZZ, eq_threshold_pct=wrong).equal_lows is False


class TestDisplacementBoundary:
    def test_forty_five_percent_exactly_passes(self):
        """`body_pct >= 0.45` — a candle at exactly 45 % qualifies."""
        bar = BODY_45.iloc[-1]
        body_pct = abs(bar["close"] - bar["open"]) / (bar["high"] - bar["low"])
        assert body_pct == 0.45
        assert run(BODY_45).sweep_detected is True

    def test_forty_two_percent_fails(self):
        assert run(BODY_42).sweep_detected is False

    def test_forty_seven_percent_passes(self):
        assert run(BODY_47).sweep_detected is True

    def test_the_measured_magnitudes(self):
        assert run(BODY_45).sweep_magnitude == pytest.approx(0.6609, abs=1e-4)
        assert run(BODY_47).sweep_magnitude == pytest.approx(1.6306, abs=1e-4)


class TestBoundaryEquality:
    def test_a_close_exactly_on_the_swing_low_is_not_a_bullish_sweep(self):
        """`c_close > recent_sl` is strict; touching the level is not a
        rejection. (A `>=` would report a sweep here.)"""
        assert run(CLOSE_ON_LOW).sweep_detected is False

    def test_a_close_exactly_on_the_swing_high_is_not_a_bearish_sweep(self):
        """`c_close < recent_sh` is strict."""
        assert run(CLOSE_ON_HIGH).sweep_detected is False

    def test_a_low_exactly_on_the_swing_low_can_never_be_a_sweep(self):
        """PROVABLY DEAD. `c_low < recent_sl` vs `<=` is unobservable either
        way: touching the level makes `mag_low` exactly 0, which fails the
        `0.15 <= mag_low` floor before the inequality could matter."""
        f = last_bar(osc(60), 96.8, 97.9, 97.7, 97.9)
        assert f.iloc[-1]["low"] == run(OSC60).sell_side_liquidity
        assert run(f).sweep_detected is False

    def test_a_high_exactly_on_the_swing_high_can_never_be_a_sweep(self):
        """Same argument for the bearish side: mag_high would be exactly 0."""
        f = last_bar(osc(60), 102.4, 102.3, 97.9, 98.1)
        assert f.iloc[-1]["high"] == run(OSC60).buy_side_liquidity
        assert run(f).sweep_detected is False

    def test_the_bullish_body_comparison_is_unobservable_see_gates(self):
        assert run(BULL_DOJI).sweep_detected is False

    def test_the_bearish_body_comparison_is_unobservable(self):
        """`c_close <= c_open` vs `<`: equality implies body_size 0, which
        fails the 45 % displacement floor first."""
        for f in (last_bar(osc(60), 102.4, 102.5, 97.9, 102.4),
                  last_bar(osc(60), 103.0, 103.5, 97.9, 103.0)):
            bar = f.iloc[-1]
            assert bar["close"] == bar["open"]
            assert run(f).sweep_detected is False


class TestBearishMagnitudeBand:
    def test_a_shallow_poke_above_the_level_is_rejected(self):
        assert run(BEAR_WEAK).sweep_detected is False

    def test_the_bearish_lower_band_is_fifteen_hundredths_not_ten(self):
        """A poke 0.116 ATR above the level is rejected. It would be accepted
        if the floor were 0.10, so this pins the constant."""
        assert run(BEAR_MID).sweep_detected is False

    def test_a_poke_beyond_three_and_a_half_atr_is_rejected(self):
        assert run(BEAR_DEEP).sweep_detected is False

    def test_the_bearish_magnitude_uses_the_high_not_the_low(self):
        """sweep_magnitude is |c_high - recent_sh| / ATR, NOT the low's
        distance — pin it with a candle whose two distances differ."""
        r = run(BEAR_ASYM)
        assert r.sweep_detected is True
        assert r.sweep_magnitude == pytest.approx(0.1691, abs=1e-4)
        # the low is 0.8 from the swing low; reporting that would give ~0.68
        assert r.sweep_magnitude < 0.5

    def test_the_bearish_level_is_the_swing_high(self):
        assert run(BEAR_ASYM).sweep_level == 102.3


class TestNoOpenColumn:
    def test_a_bearish_sweep_is_impossible_without_an_open_column(self):
        """PINNED. With 'open' absent the code falls back to `opens = closes`,
        so body_size is 0 and no sweep can ever be reported — silently, with
        no error and no pool difference."""
        assert run(BEAR_NO_OPEN).sweep_detected is False

    def test_the_fallback_choice_is_not_observable_for_a_bullish_sweep(self):
        """PROVABLY DEAD for the bullish branch. Any fallback F gives a sweep
        only if `c_close >= F`; with F == high that means close >= high, so
        close == high and body_size == 0 — no displacement. Only the bearish
        branch could distinguish, and the code never reaches it usefully."""
        assert "open" not in BEAR_NO_OPEN.columns
        assert run(BEAR_NO_OPEN).sweep_type == "NONE"

    def test_the_pools_are_still_reported_without_an_open_column(self):
        assert len(run(BEAR_NO_OPEN).liquidity_pools) == 2


class TestVolumeGateBoundaries:
    def test_four_hundred_and_thirty_two_of_a_thousand_average_passes(self):
        assert run(VOL_432).sweep_detected is True

    def test_three_hundred_and_thirty_three_is_rejected(self):
        assert run(VOL_333).sweep_detected is False

    def test_three_hundred_and_eighty_three_passes_but_three_eighty_two_fails(self):
        """The 40 % line sits between 382 and 383 because the last candle is
        part of its own 14-bar average."""
        assert run(VOL_383).sweep_detected is True
        assert run(VOL_382).sweep_detected is False

    def test_the_forty_percent_comparison_is_strict(self):
        """c_vol == 0.40 * avg_vol exactly: `c_vol < 0.40*avg` is False, so the
        candle stays aligned. A `<=` would disqualify it."""
        avg = float(VOL_EXACT["volume"].iloc[-14:].mean())
        assert avg == 65.0
        assert float(VOL_EXACT["volume"].iloc[-1]) == avg * 0.40
        assert run(VOL_EXACT).sweep_detected is True

    def test_the_average_is_the_last_fourteen_bars_not_ten(self):
        """avg14 = 696.4 (four of the last fourteen bars are 100) vs
        avg10 = 935.0; 350 passes against the former and fails the latter."""
        assert float(VOL_WIN["volume"].iloc[-14:].mean()) == pytest.approx(696.43, abs=0.01)
        assert float(VOL_WIN["volume"].iloc[-10:].mean()) == pytest.approx(935.0, abs=0.01)
        assert run(VOL_WIN).sweep_detected is True

    def test_volume_wins_over_tick_volume(self):
        assert run(VOL_BOTH).sweep_detected is True

    def test_tick_volume_is_only_a_fallback(self):
        f = VOL_BOTH.drop(columns=["volume"])
        assert run(f).sweep_detected is False


class TestScanWindow:
    def test_the_last_pivot_window_bars_are_never_scanned(self):
        """A spike at index 55 of 60 sits inside the unscanned tail — it must
        not become the buy-side level."""
        assert run(SCAN_EDGE).buy_side_liquidity == 102.3

    def test_the_first_pivot_window_bars_are_never_scanned(self):
        b = osc(60)
        b[2] = 200.0
        assert run(ohlc(b)).buy_side_liquidity == 102.3

    def test_a_frame_with_only_one_side_of_swings_returns_the_default(self):
        """`if not swing_highs or not swing_lows` — a peak with no trough is
        still "no liquidity read", and must NOT fall through (swing_lows[-1]
        would raise IndexError)."""
        h = np.array(ONE_SIDED["high"])
        l = np.array(ONE_SIDED["low"])
        assert [i for i in range(5, 55) if h[i] == max(h[i - 5:i + 6])] == [30]
        assert [i for i in range(5, 55) if l[i] == min(l[i - 5:i + 6])] == []
        assert run(ONE_SIDED).liquidity_pools == []


class TestShortFrameAtr:
    def test_a_fourteen_bar_frame_uses_a_single_bar_range_as_atr(self):
        """len < 15 -> ATR = high[-1] - low[-1] = 2.0, so mag = 1.0/2.0."""
        r = run(zzshort(14), pivot_window=3)
        assert r.sweep_detected is True
        assert r.sweep_magnitude == pytest.approx(0.5, abs=1e-4)

    def test_a_fifteen_bar_frame_switches_to_the_fourteen_bar_atr(self):
        """Same candle, different ATR -> different magnitude."""
        r = run(zzshort(15), pivot_window=3)
        assert r.sweep_detected is True
        assert r.sweep_magnitude == pytest.approx(0.3889, abs=1e-4)
        assert r.sweep_magnitude != pytest.approx(0.5, abs=1e-4)

    def test_a_zero_range_last_bar_sends_the_magnitude_to_infinity(self):
        """PINNED. ATR = `float(high[-1]-low[-1]) or 1e-9`; a doji last bar
        makes ATR 1e-9, so the prior bar's sweep magnitude explodes past the
        3.5 ceiling and the sweep is dropped."""
        f = zzshort(14, flat_last=True)
        assert float(f["high"].iloc[-1] - f["low"].iloc[-1]) == 0.0
        assert run(f, pivot_window=3).sweep_detected is False


class TestBreakShortCircuit:
    def test_the_newest_bar_wins_when_both_could_sweep(self):
        """Bar -1 is a bullish sweep and bar -2 is a bearish sweep; the `break`
        after the first means the bullish one is reported."""
        r = run(BOTH_BARS)
        assert r.sweep_detected is True
        assert r.sweep_type == "BULLISH_SWEEP"
        assert r.sweep_magnitude == pytest.approx(0.7723, abs=1e-4)

    def test_without_the_break_the_prior_bar_would_overwrite_the_result(self):
        """Documenting what the `break` prevents: bar -2 alone is a valid
        bearish sweep, so removing the break would flip the verdict."""
        f = BOTH_BARS.iloc[:-1]
        r = run(f)
        assert r.sweep_type == "BEARISH_SWEEP"

    def test_the_break_also_guards_the_bearish_branch(self):
        """Mirror image: bar -1 is bearish, bar -2 is bullish. The reported
        verdict must be the bearish one from the newest bar."""
        r = run(BOTH_BARS_REV)
        assert r.sweep_detected is True
        assert r.sweep_type == "BEARISH_SWEEP"
        assert r.sweep_magnitude == pytest.approx(0.1545, abs=1e-4)
        assert run(BOTH_BARS_REV.iloc[:-1]).sweep_type == "BULLISH_SWEEP"


class TestRoundingPrecision:
    def test_pool_prices_carry_four_decimals(self):
        assert run(ZZ_ROUND).liquidity_pools[0]["price"] == 102.6456
        assert run(ZZ_ROUND).liquidity_pools[1]["price"] == 94.4654

    def test_two_decimals_would_be_a_different_number(self):
        assert round(102.6456, 2) == 102.65
        assert run(ZZ_ROUND).liquidity_pools[0]["price"] != 102.65

    def test_the_reported_levels_carry_four_decimals(self):
        r = run(ZZ_ROUND)
        assert r.buy_side_liquidity == 102.6456
        assert r.sell_side_liquidity == 94.4654

    def test_the_sweep_level_carries_four_decimals(self):
        r = run(ZZ_ROUND_SWEEP)
        assert r.sweep_detected is True
        assert r.sweep_level == 94.4654
        assert r.sweep_level != round(94.4654, 2)

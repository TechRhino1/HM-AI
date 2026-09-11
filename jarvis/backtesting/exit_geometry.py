"""
JARVIS AI 5.1 — Exit Geometry Engine.

WHY THIS MODULE EXISTS
----------------------
5.0 searched only ``tp_r`` (a single fixed target) plus an optional single partial.
That search space is too small to express the exits that actually produce
asymmetric payoff, and it is the reason the portfolio's realised payoff collapsed
to 0.279R: with one fixed target the only way to raise the win rate is to shrink
the target, which shrinks the payoff and pushes the breakeven win rate up to ~80%.

This module replaces "a target" with a **scale-out schedule**: an ordered list of
legs, each closing a fraction of the position at an R level, with the remainder
left to run under an ATR trail (or a fixed final target). It implements the five
geometries requested for 5.1 and evaluates them on identical entries so the
choice is made on out-of-sample expectancy, profit factor and drawdown — never on
win rate.

THE FIVE GEOMETRIES
-------------------
  A fixed_tp        100% at tp_r.
  B single_scale    33% at 1.5R, 67% runner.
  C ladder_1_2      25% at 1R, 25% at 2R, 50% runner.
  D ladder_1p5_2p5  33% at 1.5R, 33% at 2.5R, 34% runner.
  E atr_trail       No partials; 100% runner under an ATR trail.

CONVENTIONS (identical to trade_simulator.simulate_trade)
---------------------------------------------------------
  * Entry fills at the NEXT bar's open, so the position is live from
    ``entry_idx + 1``. Testing the signal bar is a one-bar look-ahead.
  * Conservative intrabar ordering: when a bar spans both the stop and a target,
    the stop (resting at the start of the bar) is assumed to fill FIRST.
  * Protective-stop fills pay ``slippage_price_equiv``; target fills do not.
  * 1R is frozen at |fill - initial_sl| for the whole trade.

The result is a plain dict so it can be aggregated by symbol, by regime, or by
loss category without another dataclass layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "ExitLeg",
    "ExitGeometry",
    "EXIT_GEOMETRIES",
    "geometry_modes",
    "simulate_exit_geometry",
]


@dataclass(frozen=True)
class ExitLeg:
    """One rung of a scale-out schedule.

    ``pct``     fraction of the original position closed at this rung.
    ``r``       R level that triggers it. ``None`` marks the RUNNER leg, which
                is closed by the trail / final target / time stop.
    """

    pct: float
    r: Optional[float]

    @property
    def is_runner(self) -> bool:
        return self.r is None


@dataclass(frozen=True)
class ExitGeometry:
    """A complete exit plan: scale-out ladder + stop management."""

    mode: str
    legs: Sequence[ExitLeg]
    # Final hard target for the runner. None = no target, run until trail/stop/time.
    tp_r: Optional[float]
    # Breakeven policy: None disables. "immediate" locks at entry as soon as the
    # trigger is reached; "atr_offset" locks entry + 0.25*ATR so noise cannot
    # take out a locked position; "delayed" waits for the first leg to bank.
    be_trigger_r: Optional[float] = None
    be_style: str = "immediate"
    # ATR trail for the runner.
    trail_atr: Optional[float] = None
    trail_activation_r: float = 2.0
    max_bars: int = 48

    def key(self) -> str:
        be = "beoff" if self.be_trigger_r is None else f"be{self.be_trigger_r:g}{self.be_style[0]}"
        tr = "troff" if self.trail_atr is None else f"tr{self.trail_atr:g}@{self.trail_activation_r:g}"
        tp = "tpN" if self.tp_r is None else f"tp{self.tp_r:g}"
        return f"{self.mode}_{tp}_{be}_{tr}_mb{self.max_bars}"

    def describe(self) -> str:
        parts = []
        for leg in self.legs:
            if leg.is_runner:
                parts.append(f"{leg.pct:.0%} runner")
            else:
                parts.append(f"{leg.pct:.0%}@{leg.r:g}R")
        return " + ".join(parts)


def _mode_a(tp_r: float = 1.0, **kw) -> ExitGeometry:
    return ExitGeometry(mode="A_fixed_tp", legs=(ExitLeg(1.0, tp_r),), tp_r=tp_r, **kw)


def _mode_b(tp_r: Optional[float] = None, **kw) -> ExitGeometry:
    """33% at 1.5R + 67% runner."""
    return ExitGeometry(
        mode="B_single_scale",
        legs=(ExitLeg(0.33, 1.5), ExitLeg(0.67, None)),
        tp_r=tp_r, **kw,
    )


def _mode_c(tp_r: Optional[float] = None, **kw) -> ExitGeometry:
    """25% at 1R + 25% at 2R + 50% runner."""
    return ExitGeometry(
        mode="C_ladder_1_2",
        legs=(ExitLeg(0.25, 1.0), ExitLeg(0.25, 2.0), ExitLeg(0.50, None)),
        tp_r=tp_r, **kw,
    )


def _mode_d(tp_r: Optional[float] = None, **kw) -> ExitGeometry:
    """33% at 1.5R + 33% at 2.5R + 34% runner."""
    return ExitGeometry(
        mode="D_ladder_1p5_2p5",
        legs=(ExitLeg(0.33, 1.5), ExitLeg(0.33, 2.5), ExitLeg(0.34, None)),
        tp_r=tp_r, **kw,
    )


def _mode_e(trail_atr: float = 1.2, **kw) -> ExitGeometry:
    """ATR trailing only — no partials, no fixed target."""
    return ExitGeometry(
        mode="E_atr_trail",
        legs=(ExitLeg(1.0, None),),
        tp_r=None,
        trail_atr=trail_atr, **kw,
    )


def EXIT_GEOMETRIES(
    tp_r: float = 1.0,
    trail_atr: float = 1.2,
    be_trigger_r: Optional[float] = None,
    be_style: str = "immediate",
    max_bars: int = 48,
) -> Dict[str, ExitGeometry]:
    """The five candidate geometries, built with a shared stop-management policy.

    ``tp_r`` only applies to mode A (and as an optional hard cap for runners);
    the ladders get their levels from the schedule itself.
    """
    common = dict(
        be_trigger_r=be_trigger_r,
        be_style=be_style,
        trail_atr=trail_atr,
        max_bars=max_bars,
    )
    return {
        "A_fixed_tp": _mode_a(tp_r=tp_r, **common),
        "B_single_scale": _mode_b(**common),
        "C_ladder_1_2": _mode_c(**common),
        "D_ladder_1p5_2p5": _mode_d(**common),
        # ``common`` already carries trail_atr, so it must not be passed again.
        "E_atr_trail": _mode_e(**common),
    }


def geometry_modes() -> List[str]:
    return ["A_fixed_tp", "B_single_scale", "C_ladder_1_2",
            "D_ladder_1p5_2p5", "E_atr_trail"]


def build_exit_geometry(
    mode: str,
    *,
    tp_r: Optional[float] = 1.0,
    trail_atr: float = 1.5,
    be_trigger_r: Optional[float] = None,
    be_style: str = "immediate",
    max_bars: int = 48,
    trail_activation_r: float = 2.0,
) -> ExitGeometry:
    """Resolve a mode name into a concrete geometry.

    Used by BacktestEngine so live/backtest execution and the calibration
    harness run the SAME schedule — that shared schedule is what makes the
    out-of-sample number predictive of the engine.
    """
    geoms = EXIT_GEOMETRIES(
        tp_r=tp_r if tp_r is not None else 1.0,
        trail_atr=trail_atr,
        be_trigger_r=be_trigger_r,
        be_style=be_style,
        max_bars=max_bars,
    )
    g = geoms.get(mode)
    if g is None:
        return geoms["A_fixed_tp"]
    # Honour a per-profile trail activation override.
    if trail_activation_r is not None and g.trail_activation_r != trail_activation_r:
        g = ExitGeometry(
            mode=g.mode, legs=g.legs, tp_r=g.tp_r,
            be_trigger_r=g.be_trigger_r, be_style=g.be_style,
            trail_atr=g.trail_atr, trail_activation_r=trail_activation_r,
            max_bars=g.max_bars,
        )
    return g


def simulate_exit_geometry(
    *,
    bars: Any,
    entry_idx: int,
    side: str,
    fill: float,
    sl_initial: float,
    geom: ExitGeometry,
    cost_price_equiv: float = 0.0,
    slippage_price_equiv: float = 0.0,
    regime: str = "UNKNOWN",
    strategy: str = "UNKNOWN",
) -> Optional[Dict[str, Any]]:
    """Replay one entry under a full scale-out geometry.

    Returns a dict with the realised R (net of cost), the legs that filled,
    MFE/MAE in R, breakeven/trail flags, and the exit reason — or ``None`` if the
    trade is degenerate (no forward bars, zero risk).
    """
    high = bars.high
    low = bars.low
    close = bars.close
    atr = bars.atr
    n = bars.n

    risk_dist = abs(fill - sl_initial)
    if risk_dist <= 0 or entry_idx >= n - 1:
        return None

    is_buy = str(side).upper().startswith("BUY")
    direction = 1.0 if is_buy else -1.0
    inv_risk = 1.0 / risk_dist

    # Working state
    current_sl = float(sl_initial)
    remaining_pct = 1.0
    booked_r = 0.0          # R already banked, weighted by closed fraction
    be_locked = False
    trail_active = False
    legs_taken: List[Dict[str, Any]] = []
    pending_leg = 0         # index into legs
    mfe = 0.0
    mae = 0.0
    max_range_atr = 0.0   # widest single bar as a multiple of ATR — spike detector
    exit_reason = "TIME_STOP"

    tp_price = None
    if geom.tp_r is not None:
        tp_price = fill + direction * geom.tp_r * risk_dist

    for j in range(entry_idx + 1, n):
        h = high[j]
        lo = low[j]
        a = float(atr[j]) if atr is not None else 0.0

        # Track excursions on the whole move from entry.
        fav = (h - fill) * direction
        adv = (fill - lo) * direction
        if fav > mfe:
            mfe = fav
        if adv > mae:
            mae = adv
        if a > 0:
            rng = (h - lo) / a
            if rng > max_range_atr:
                max_range_atr = rng

        r_now = mfe * inv_risk  #保守: use best favourable excursion so far

        # ── 1. Scale-out rungs (targets fill at their level) ────────────────
        while pending_leg < len(geom.legs):
            leg = geom.legs[pending_leg]
            if leg.is_runner or leg.r is None:
                break
            if r_now < leg.r:
                break
            # Conservative: if this bar ALSO spans the stop, the stop fills first.
            stop_hit = (lo <= current_sl) if is_buy else (h >= current_sl)
            if stop_hit:
                break
            close_pct = min(leg.pct, remaining_pct)
            if close_pct <= 0:
                pending_leg += 1
                continue
            booked_r += float(leg.r) * close_pct
            remaining_pct -= close_pct
            legs_taken.append({
                "leg": pending_leg, "pct": close_pct, "r": float(leg.r),
                "bar": j, "reason": "SCALEOUT",
            })
            pending_leg += 1
            # Banking a leg is the proof the trade works -> unlock breakeven.
            if geom.be_trigger_r is not None and geom.be_style == "delayed":
                be_locked = True

        # ── 2. Breakeven lock ──────────────────────────────────────────────
        if (not be_locked) and geom.be_trigger_r is not None and r_now >= geom.be_trigger_r:
            be_locked = True
        if be_locked:
            if geom.be_style == "atr_offset" and a > 0:
                be_level = fill + direction * 0.25 * a
            else:
                be_level = fill
            if is_buy:
                if be_level > current_sl:
                    current_sl = be_level
            else:
                if be_level < current_sl:
                    current_sl = be_level

        # ── 3. ATR trail on the runner ─────────────────────────────────────
        if geom.trail_atr is not None and a > 0 and r_now >= geom.trail_activation_r:
            trail_active = True
            trail_sl = (close[j] - a * geom.trail_atr) if is_buy else (close[j] + a * geom.trail_atr)
            if is_buy:
                if trail_sl > current_sl and trail_sl < close[j]:
                    current_sl = trail_sl
            else:
                if trail_sl < current_sl and trail_sl > close[j]:
                    current_sl = trail_sl

        # ── 4. Stop test FIRST (conservative intrabar ordering) ─────────────
        stop_hit = (lo <= current_sl) if is_buy else (h >= current_sl)
        if stop_hit:
            exit_price = current_sl - direction * slippage_price_equiv
            remaining_r = (exit_price - fill) * direction * inv_risk
            booked_r += remaining_r * remaining_pct
            exit_reason = "BE_SL" if be_locked else ("TRAIL_SL" if trail_active else "SL")
            return _pack(fill, exit_price, sl_initial, current_sl, risk_dist, booked_r,
                         cost_price_equiv, legs_taken, be_locked, trail_active,
                         mfe, mae, max_range_atr, inv_risk, exit_reason, j, entry_idx, regime, strategy, geom)

        # ── 5. Final hard target on the runner ─────────────────────────────
        if tp_price is not None and remaining_pct > 0:
            tpHit = (h >= tp_price) if is_buy else (lo <= tp_price)
            if tpHit:
                remaining_r = (tp_price - fill) * direction * inv_risk
                booked_r += remaining_r * remaining_pct
                remaining_pct = 0.0
                legs_taken.append({
                    "leg": pending_leg, "pct": remaining_pct, "r": remaining_r,
                    "bar": j, "reason": "TP",
                })
                return _pack(fill, tp_price, sl_initial, current_sl, risk_dist, booked_r,
                             cost_price_equiv, legs_taken, be_locked, trail_active,
                             mfe, mae, max_range_atr, inv_risk, "TP", j, entry_idx, regime, strategy, geom)

        # ── 6. Time stop ───────────────────────────────────────────────────
        if (j - entry_idx) >= geom.max_bars:
            exit_price = close[j]
            remaining_r = (exit_price - fill) * direction * inv_risk
            booked_r += remaining_r * remaining_pct
            return _pack(fill, exit_price, sl_initial, current_sl, risk_dist, booked_r,
                         cost_price_equiv, legs_taken, be_locked, trail_active,
                         mfe, mae, max_range_atr, inv_risk, "TIME_STOP", j, entry_idx, regime, strategy, geom)

    # Ran out of data
    exit_price = close[n - 1]
    remaining_r = (exit_price - fill) * direction * inv_risk
    booked_r += remaining_r * remaining_pct
    return _pack(fill, exit_price, sl_initial, current_sl, risk_dist, booked_r,
                 cost_price_equiv, legs_taken, be_locked, trail_active,
                 mfe, mae, max_range_atr, inv_risk, "CLOSE_AT_END", n - 1, entry_idx, regime, strategy, geom)


def _pack(fill, exit_price, sl_initial, sl_final, risk_dist, booked_r,
          cost_price_equiv, legs_taken, be_locked, trail_active,
          mfe, mae, max_range_atr, inv_risk, reason, exit_idx, entry_idx,
          regime, strategy, geom):
    pnl_r = booked_r - (cost_price_equiv / risk_dist)
    return {
        "entry": float(fill),
        "exit": float(exit_price),
        "sl_initial": float(sl_initial),
        "sl_final": float(sl_final),
        "risk_dist": float(risk_dist),
        "pnl_r": float(pnl_r),
        "gross_r": float(booked_r),
        "is_win": bool(pnl_r > 0),
        "exit_reason": reason,
        "legs": legs_taken,
        "n_legs": len(legs_taken),
        "be_locked": bool(be_locked),
        "trail_active": bool(trail_active),
        "mfe_r": float(mfe * inv_risk),
        "mae_r": float(mae * inv_risk),
        "max_range_atr": float(max_range_atr),
        "bars_held": int(exit_idx - entry_idx),
        "regime": regime,
        "strategy": strategy,
        "mode": geom.mode,
        "geometry_key": geom.key(),
    }

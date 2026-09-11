"""
JARVIS 5.1 — Master-Trader Signal & Decision Engine.

WHY THIS MODULE EXISTS
----------------------
The system already had many good signals (structure, liquidity sweeps, order
blocks, FVGs, momentum, volatility, regime, ML, news) but they were consumed by
scattered hard rules: a gate here, a score bump there, with no single place that
weighs them against each other, explains the result, or refuses to act when the
inputs are bad. This module is that place.

DESIGN PRINCIPLES
-----------------
1. WEIGHTED, NOT RULED. Every signal contributes a directional score in
   [-1, +1] times a weight. The decision is the weighted sum, never a single
   hard trigger.

2. REGIME-ADAPTIVE WEIGHTS. Trend-following evidence matters in TREND_BULL /
   TREND_BEAR; mean-reversion and liquidity-sweep evidence matters in
   COMPRESSION / RANGE. Weights are swapped by regime rather than hard-coded.

3. EXPLAINABLE. Every decision carries the full signal list with each one's
   value, weight, contribution and a human-readable reason, so a decision can be
   reviewed after the fact.

4. FAIL CLOSED. Missing, stale or mutually contradictory data produces NO TRADE.
   The engine never guesses. A missing input is not a neutral input - it is a
   reason to stand down.

5. MODULAR. A new data source is one class implementing ``SignalSource`` and one
   entry in ``register_default_sources``. Nothing else changes.

Outputs a complete, risk-checked plan: action, entry zone, stop, target, position
size and confidence.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Sequence

from jarvis.config.paths import DATA_DIR

__all__ = [
    "Signal", "TradeDecision", "SignalSource", "SignalEngine",
    "REGIME_WEIGHTS", "DEFAULT_WEIGHTS", "DecisionLogger",
]


# ─────────────────────────────────────────────────────────────────────────────
# Core data types
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Signal:
    """One piece of evidence.

    ``value``      directional conviction in [-1, +1] (+1 = strongly bullish).
    ``confidence`` how much this signal trusts itself in [0, 1]. Scales its
                   effective weight, so a high-weight signal that is unsure of
                   itself contributes little.
    ``stale``      the underlying data was missing or too old to trust.
    """

    name: str
    value: float
    confidence: float = 1.0
    weight: float = 1.0
    reason: str = ""
    stale: bool = False

    def contribution(self) -> float:
        if self.stale:
            return 0.0
        v = max(-1.0, min(1.0, float(self.value)))
        c = max(0.0, min(1.0, float(self.confidence)))
        return v * c * float(self.weight)


@dataclass
class TradeDecision:
    """A complete, reviewable trading decision."""

    symbol: str
    action: str = "HOLD"            # BUY | SELL | HOLD | CLOSE
    entry_low: float = 0.0
    entry_high: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    size_pct: float = 0.0           # % of equity risked
    confidence: float = 0.0
    score: float = 0.0              # weighted sum in [-1, +1]
    regime: str = "UNKNOWN"
    reasons: List[str] = field(default_factory=list)
    signals: List[Dict[str, Any]] = field(default_factory=list)
    rejected_by: Optional[str] = None
    ts: float = field(default_factory=time.time)

    @property
    def tradable(self) -> bool:
        return self.action in ("BUY", "SELL")

    def explain(self) -> str:
        head = (f"{self.symbol} {self.action} conf={self.confidence:.2f} "
                f"score={self.score:+.3f} size={self.size_pct:.2f}%")
        if self.rejected_by:
            head += f"  [REJECTED: {self.rejected_by}]"
        lines = [head]
        for s in sorted(self.signals, key=lambda d: -abs(d.get("contribution", 0.0))):
            flag = " STALE" if s.get("stale") else ""
            lines.append(f"   {s['name']:<18} {s.get('contribution', 0.0):+.4f}{flag}"
                         f"  - {s.get('reason', '')}")
        for r in self.reasons:
            lines.append(f"   -> {r}")
        return "\n".join(lines)


class SignalSource:
    """Base class for a pluggable signal.

    Subclass and implement ``evaluate``. Return ``None`` when the source has no
    opinion (that is fine and is not treated as missing data). Set
    ``signal.stale = True`` when the source *should* have an opinion but its
    inputs are missing or too old - that is what drives the fail-closed rule.
    """

    name: str = "unnamed"
    #: context attributes this source cannot work without
    requires: Sequence[str] = ()

    def evaluate(self, ctx: Any) -> Optional[Signal]:
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# Regime-adaptive weighting
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_WEIGHTS: Dict[str, float] = {
    "structure": 1.00,     # BOS/CHoCH/HH-HL — always relevant
    "liquidity": 0.90,     # sweeps — the highest-quality reversal evidence
    "order_flow": 0.70,
    "momentum": 0.65,
    "volatility": 0.45,    # mostly a filter, not a direction
    "fvg_ob": 0.60,
    "sentiment": 0.35,
    "ml": 0.55,
}

# Regime overrides. Only the keys that change are listed; the rest fall back to
# DEFAULT_WEIGHTS. Rationale: in a trend you trust structure and momentum and
# discount mean-reversion/sweep evidence; in compression/ranges you invert that.
REGIME_WEIGHTS: Dict[str, Dict[str, float]] = {
    "TREND_BULL": {"structure": 1.20, "momentum": 0.90, "liquidity": 0.60, "fvg_ob": 0.75},
    "TREND_BEAR": {"structure": 1.20, "momentum": 0.90, "liquidity": 0.60, "fvg_ob": 0.75},
    "COMPRESSION": {"structure": 0.70, "momentum": 0.35, "liquidity": 1.10,
                    "volatility": 0.70, "fvg_ob": 0.85},
    "RANGE": {"structure": 0.70, "momentum": 0.35, "liquidity": 1.10,
              "volatility": 0.70, "fvg_ob": 0.85},
    "BREAKOUT": {"structure": 1.10, "momentum": 1.00, "volatility": 0.60, "liquidity": 0.70},
    "LIQUIDITY_SWEEP": {"liquidity": 1.30, "structure": 0.90, "fvg_ob": 0.80, "momentum": 0.40},
    "LOW_VOLATILITY": {"structure": 0.80, "momentum": 0.45, "volatility": 0.30, "liquidity": 0.80},
    "WEAK_TREND": {"structure": 0.85, "momentum": 0.55, "liquidity": 0.85},
    "HIGH_VOLATILITY": {"volatility": 0.85, "structure": 0.90, "momentum": 0.50,
                        "liquidity": 0.80, "ml": 0.40},
}


def weights_for_regime(regime: str) -> Dict[str, float]:
    w = dict(DEFAULT_WEIGHTS)
    w.update(REGIME_WEIGHTS.get(str(regime or "").upper(), {}))
    return w


# ─────────────────────────────────────────────────────────────────────────────
# Built-in sources — each reads the existing MarketContext, tolerating absence
# ─────────────────────────────────────────────────────────────────────────────
def _get(obj: Any, path: str, default=None):
    cur = obj
    for part in path.split("."):
        if cur is None:
            return default
        cur = getattr(cur, part, None) if not isinstance(cur, dict) else cur.get(part)
    return default if cur is None else cur


class StructureSource(SignalSource):
    """BOS / CHoCH / swing direction, plus premium-discount context."""

    name = "structure"

    def evaluate(self, ctx):
        st = _get(ctx, "structure")
        if st is None:
            return Signal(self.name, 0.0, stale=True, reason="structure context missing")
        bias = str(_get(st, "bias", "NEUTRAL") or "NEUTRAL").upper()
        if bias == "BULLISH":
            v, why = 0.7, "bullish structure (HH/HL)"
        elif bias == "BEARISH":
            v, why = -0.7, "bearish structure (LH/LL)"
        else:
            return Signal(self.name, 0.0, confidence=0.3, reason="structure neutral")
        if _get(st, "choch"):
            v *= 1.15
            why += " + CHoCH"
        # Buying premium / selling discount is the wrong side of the curve.
        zone = str(_get(st, "discount_premium_zone", "") or "").upper()
        if (v > 0 and zone == "PREMIUM") or (v < 0 and zone == "DISCOUNT"):
            v *= 0.6
            why += f" (penalised: trading from {zone})"
        return Signal(self.name, max(-1.0, min(1.0, v)), confidence=0.9, reason=why)


class LiquiditySource(SignalSource):
    """Sweep detection — the strongest reversal evidence in the system."""

    name = "liquidity"

    def evaluate(self, ctx):
        liq = _get(ctx, "liquidity")
        if liq is None:
            return Signal(self.name, 0.0, stale=True, reason="liquidity context missing")
        swept = bool(_get(liq, "sweep_detected", False))
        if not swept:
            eh, el = bool(_get(liq, "equal_highs", False)), bool(_get(liq, "equal_lows", False))
            if eh or el:
                return Signal(self.name, 0.0, confidence=0.4,
                              reason="unswept equal highs/lows resting - no trigger yet")
            return Signal(self.name, 0.0, confidence=0.3, reason="no sweep")
        stype = str(_get(liq, "sweep_type", "") or "").upper()
        mag = float(_get(liq, "sweep_magnitude", 0.0) or 0.0)
        conf = 0.85 if 0.3 <= mag <= 2.5 else 0.6
        if stype == "BULLISH_SWEEP":
            return Signal(self.name, 0.9, confidence=conf,
                          reason=f"sell-side liquidity swept ({mag:.2f} ATR) and rejected")
        if stype == "BEARISH_SWEEP":
            return Signal(self.name, -0.9, confidence=conf,
                          reason=f"buy-side liquidity swept ({mag:.2f} ATR) and rejected")
        return Signal(self.name, 0.0, stale=True, reason="sweep flagged but type unrecognised")


class MomentumSource(SignalSource):
    name = "momentum"

    def evaluate(self, ctx):
        mom = _get(ctx, "momentum")
        if mom is None:
            return Signal(self.name, 0.0, stale=True, reason="momentum missing")
        ts = _get(mom, "trend_score")
        if ts is None:
            return Signal(self.name, 0.0, stale=True, reason="trend_score missing")
        v = max(-1.0, min(1.0, float(ts) / 100.0))
        return Signal(self.name, v, confidence=0.7, reason=f"trend_score {float(ts):+.1f}")


class VolatilitySource(SignalSource):
    """Volatility is mostly a CONFIDENCE filter rather than a direction."""

    name = "volatility"

    def evaluate(self, ctx):
        vol = _get(ctx, "volatility")
        if vol is None:
            return Signal(self.name, 0.0, stale=True, reason="volatility missing")
        pct = _get(vol, "atr_percentile")
        if pct is None:
            return Signal(self.name, 0.0, confidence=0.3, reason="atr_percentile unavailable")
        p = float(pct)
        # Extreme volatility argues against entering, not for a direction.
        v = 0.0 if p < 85 else -0.15
        return Signal(self.name, v, confidence=0.8,
                      reason=f"ATR percentile {p:.0f}" + (" - elevated, size down" if p >= 85 else ""))


class FvgObSource(SignalSource):
    """Nearby unmitigated order block / FVG acting as a magnet or a wall."""

    name = "fvg_ob"

    def evaluate(self, ctx):
        st = _get(ctx, "structure")
        if st is None:
            return Signal(self.name, 0.0, stale=True, reason="structure missing")
        obs = list(_get(st, "order_blocks", []) or [])
        fvgs = list(_get(st, "fair_value_gaps", []) or [])
        # B1: ignore consumed zones - they no longer behave as magnets.
        obs = [o for o in obs if not o.get("mitigated")]
        fvgs = [f for f in fvgs if not f.get("mitigated")]
        if not obs and not fvgs:
            return Signal(self.name, 0.0, confidence=0.3, reason="no live OB/FVG")
        bull = sum(1 for o in obs if "BULLISH" in str(o.get("type", "")))
        bear = sum(1 for o in obs if "BEARISH" in str(o.get("type", "")))
        bull += sum(1 for f in fvgs if "BULLISH" in str(f.get("type", "")))
        bear += sum(1 for f in fvgs if "BEARISH" in str(f.get("type", "")))
        if bull == bear:
            return Signal(self.name, 0.0, confidence=0.3,
                          reason="balanced bull/bear OB-FVG - no directional pull")
        v = 0.5 if bull > bear else -0.5
        return Signal(self.name, v, confidence=0.6,
                      reason=f"{bull} bullish vs {bear} bearish unmitigated zones")


class SentimentSource(SignalSource):
    """News / sentiment. Absent news is NOT neutral - it is unknown."""

    name = "sentiment"

    def evaluate(self, ctx):
        sent = _get(ctx, "sentiment")
        if sent is None:
            # No feed configured: stand down rather than pretend it is neutral.
            return Signal(self.name, 0.0, stale=True, confidence=0.0,
                          reason="no sentiment feed - treated as unknown")
        score = _get(sent, "score")
        if score is None:
            return Signal(self.name, 0.0, stale=True, confidence=0.0,
                          reason="sentiment present but score missing")
        return Signal(self.name, max(-1.0, min(1.0, float(score))), confidence=0.5,
                      reason=f"sentiment score {float(score):+.2f}")


class MlSource(SignalSource):
    """Online ML win probability, mapped from [0,1] to [-1,1]."""

    name = "ml"

    def evaluate(self, ctx):
        p = _get(ctx, "ml_win_prob")
        if p is None:
            return Signal(self.name, 0.0, confidence=0.2, reason="no ML prediction")
        p = max(0.0, min(1.0, float(p)))
        return Signal(self.name, (p - 0.5) * 2.0, confidence=0.5,
                      reason=f"model win probability {p:.2f}")


def register_default_sources() -> List[SignalSource]:
    """Default source set. Add a new data source by appending here."""
    return [StructureSource(), LiquiditySource(), MomentumSource(),
            VolatilitySource(), FvgObSource(), SentimentSource(), MlSource()]


# ─────────────────────────────────────────────────────────────────────────────
# Decision logging
# ─────────────────────────────────────────────────────────────────────────────
class DecisionLogger:
    """Append-only JSONL decision log for review and backtesting."""

    def __init__(self, directory: Optional[str] = None):
        self.dir = directory or os.path.join(DATA_DIR, "decisions")
        os.makedirs(self.dir, exist_ok=True)

    def _path(self) -> str:
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        return os.path.join(self.dir, f"decisions_{stamp}.jsonl")

    def log(self, decision: TradeDecision) -> None:
        try:
            with open(self._path(), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(decision), default=str) + "\n")
        except Exception:
            # Logging must never break trading.
            pass


# ─────────────────────────────────────────────────────────────────────────────
# The engine
# ─────────────────────────────────────────────────────────────────────────────
class SignalEngine:
    """Weighted, explainable, regime-adaptive decision engine.

    Fails closed: any stale *required* source, or gross disagreement between the
    structural and liquidity views, produces HOLD rather than a guess.
    """

    def __init__(
        self,
        sources: Optional[List[SignalSource]] = None,
        *,
        min_confidence: float = 0.55,
        min_score: float = 0.18,
        max_risk_pct: float = 0.5,
        max_portfolio_exposure_pct: float = 3.0,
        max_drawdown_pct: float = 10.0,
        logger: Optional[DecisionLogger] = None,
        #: sources whose staleness blocks trading outright
        required_sources: Sequence[str] = ("structure", "liquidity"),
        #: signals that disagree by more than this are treated as conflicting
        conflict_threshold: float = 1.0,
    ):
        self.sources = sources if sources is not None else register_default_sources()
        self.min_confidence = float(min_confidence)
        self.min_score = float(min_score)
        self.max_risk_pct = float(max_risk_pct)
        self.max_portfolio_exposure_pct = float(max_portfolio_exposure_pct)
        self.max_drawdown_pct = float(max_drawdown_pct)
        self.logger = logger or DecisionLogger()
        self.required_sources = tuple(required_sources)
        self.conflict_threshold = float(conflict_threshold)

    # ── signal gathering ──────────────────────────────────────────────────
    def collect(self, ctx: Any) -> List[Signal]:
        out: List[Signal] = []
        for src in self.sources:
            try:
                sig = src.evaluate(ctx)
            except Exception as exc:  # a broken source must not kill the engine
                out.append(Signal(src.name, 0.0, stale=True,
                                  reason=f"source error: {type(exc).__name__}"))
                continue
            if sig is not None:
                out.append(sig)
        return out

    # ── the decision ──────────────────────────────────────────────────────
    def decide(
        self,
        ctx: Any,
        *,
        symbol: str = "",
        regime: str = "UNKNOWN",
        atr: float = 0.0,
        price: float = 0.0,
        open_positions_pct: float = 0.0,
        current_drawdown_pct: float = 0.0,
        levels: Optional[Dict[str, float]] = None,
    ) -> TradeDecision:
        sym = symbol or str(_get(ctx, "symbol", "") or "")
        regime = str(regime or _get(ctx, "regime", "UNKNOWN") or "UNKNOWN").upper()
        weights = weights_for_regime(regime)

        signals = self.collect(ctx)
        for s in signals:
            s.weight = float(weights.get(s.name, DEFAULT_WEIGHTS.get(s.name, 1.0)))

        d = TradeDecision(symbol=sym, regime=regime)

        # ── 4. data safety: fail closed, never guess ──────────────────────
        stale_required = [s.name for s in signals
                          if s.stale and s.name in self.required_sources]
        if stale_required:
            d.action, d.rejected_by = "HOLD", f"missing/stale data: {', '.join(stale_required)}"
            d.reasons.append("refusing to trade on incomplete data")
            d.signals = [self._dump(s) for s in signals]
            self.logger.log(d)
            return d

        total_w = sum(abs(s.weight) * max(s.confidence, 0.0) for s in signals if not s.stale)
        if total_w <= 0:
            d.action, d.rejected_by = "HOLD", "no usable signal weight"
            d.signals = [self._dump(s) for s in signals]
            self.logger.log(d)
            return d

        score = sum(s.contribution() for s in signals) / total_w
        d.score = max(-1.0, min(1.0, score))

        # confidence = agreement (|score|) damped by data completeness
        fresh = [s for s in signals if not s.stale]
        completeness = len(fresh) / max(1, len(signals))
        d.confidence = max(0.0, min(1.0, abs(d.score) * completeness))

        # conflict check: structural vs liquidity view must not flatly disagree
        sv = self._find(signals, "structure")
        lv = self._find(signals, "liquidity")
        if sv and lv and not (sv.stale or lv.stale):
            if abs(sv.value - lv.value) > self.conflict_threshold and sv.value * lv.value < 0:
                d.action = "HOLD"
                d.rejected_by = "conflicting structure vs liquidity signals"
                d.reasons.append(
                    f"structure {sv.value:+.2f} contradicts liquidity {lv.value:+.2f}")
                d.signals = [self._dump(s) for s in signals]
                self.logger.log(d)
                return d

        # ── 3. risk gates (before any directional commitment) ─────────────
        if current_drawdown_pct >= self.max_drawdown_pct:
            d.action = "HOLD"
            d.rejected_by = f"drawdown {current_drawdown_pct:.1f}% >= {self.max_drawdown_pct:.1f}%"
            d.signals = [self._dump(s) for s in signals]
            self.logger.log(d)
            return d
        if open_positions_pct >= self.max_portfolio_exposure_pct:
            d.action = "HOLD"
            d.rejected_by = (f"exposure {open_positions_pct:.1f}% >= "
                             f"{self.max_portfolio_exposure_pct:.1f}%")
            d.signals = [self._dump(s) for s in signals]
            self.logger.log(d)
            return d

        # ── 2. direction + confidence gate ────────────────────────────────
        if d.confidence < self.min_confidence or abs(d.score) < self.min_score:
            d.action = "HOLD"
            d.rejected_by = (f"low conviction (conf {d.confidence:.2f} < "
                             f"{self.min_confidence:.2f} or |score| {abs(d.score):.2f} < "
                             f"{self.min_score:.2f})")
            d.signals = [self._dump(s) for s in signals]
            self.logger.log(d)
            return d

        d.action = "BUY" if d.score > 0 else "SELL"

        # ── levels + sizing ───────────────────────────────────────────────
        lv = levels or {}
        sl = float(lv.get("stop_loss", 0.0) or 0.0)
        tp = float(lv.get("take_profit", 0.0) or 0.0)
        el = float(lv.get("entry_low", 0.0) or price)
        eh = float(lv.get("entry_high", 0.0) or price)
        d.entry_low, d.entry_high, d.stop_loss, d.take_profit = el, eh, sl, tp

        # A trade with no computable stop cannot be risk-managed -> refuse.
        if price > 0 and sl > 0 and abs(price - sl) > 0:
            risk_dist = abs(price - sl)
            # scale by conviction, capped by the hard per-trade limit
            d.size_pct = round(min(self.max_risk_pct,
                                   self.max_risk_pct * (0.6 + 0.8 * d.confidence)), 4)
            d.reasons.append(f"risk distance {risk_dist:.5f}; sizing {d.size_pct:.2f}% of equity")
        else:
            d.action = "HOLD"
            d.rejected_by = "no valid stop-loss - cannot size the position"
            d.size_pct = 0.0

        d.reasons.append(f"regime {regime}; weighted score {d.score:+.3f}")
        d.signals = [self._dump(s) for s in signals]
        self.logger.log(d)
        return d

    # ── helpers ───────────────────────────────────────────────────────────
    @staticmethod
    def _find(signals: Sequence[Signal], name: str) -> Optional[Signal]:
        for s in signals:
            if s.name == name:
                return s
        return None

    @staticmethod
    def _dump(s: Signal) -> Dict[str, Any]:
        return {
            "name": s.name, "value": round(float(s.value), 4),
            "confidence": round(float(s.confidence), 4),
            "weight": round(float(s.weight), 4),
            "contribution": round(float(s.contribution()), 4),
            "stale": bool(s.stale), "reason": s.reason,
        }

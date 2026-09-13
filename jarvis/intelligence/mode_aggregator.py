"""
JARVIS AI 5.2 — Cross-Style Consensus Aggregator.

WHY THIS MODULE EXISTS
----------------------
The engine can already score a candidate in three independent trading styles —
SWING, DAY_TRADING and SCALP — and ``UniversalOpportunityArbiter`` already
grades each one. What did not exist was any notion of *agreement between styles*.

``UniversalOpportunityArbiter.rank_and_select_best`` takes the single maximum by
``(utility_score, win_prob, confluence_score, expected_value)``. That is a
per-candidate ordering, not a cross-style one: three styles shouting BUY on the
same symbol rank exactly the same as three styles disagreeing, because the
ranking never looks at the other styles at all. A symbol where SWING says BUY
and SCALP says SELL produces one "winner" and silently discards the conflict.

This module adds the missing layer. It takes the per-style candidates the
arbiter produced, groups them by symbol, and asks a different question:

    *Do the independent styles agree, and how much should each one be believed?*

Three ideas carry the design.

1. **Agreement is measured by summed utility, not by headcount.**
   A unanimous trio of barely-actionable GRADE B setups is weaker evidence than
   two strong GRADE A setups plus one abstention. So each vote contributes its
   ``utility_score`` (scaled by its reliability weight) to a *signed* directional
   pool — positive for BUY, negative for SELL — and the winning direction is
   whichever pool is larger. ``agreement_ratio`` is then the winning pool's share
   of the total, which is a statement about *conviction*, not about counting.

2. **Each style is believed in proportion to what it has actually earned.**
   The three styles do not have equal historical skill, and pretending they do is
   the single easiest way to build a confident, wrong consensus. Weights come
   from the realised 6-month per-mode backtest (``reports/mode_backtest_report.json``):
   a style with negative expectancy and a profit factor below 1 is capped at the
   neutral weight of 0.5 and pushed below it, so its vote cannot manufacture
   conviction it has not demonstrated.

3. **Thin evidence is shrunk toward neutral, not extrapolated.**
   A style with 12 trades is not as informative as one with 900. The raw
   expectancy signal is therefore multiplied by ``trades / (trades + K)`` with
   ``K = 30``, so small samples land near the neutral 0.5 rather than at an
   extreme. This is the same shrinkage principle the calibration layer uses.

THE SAFETY PROPERTY
-------------------
A single style voting alone is never tradeable. ``agreement_ratio`` for a 1-of-3
split is 0.333, which falls below the MEDIUM floor of 0.66, so ``is_tradeable``
is False by construction rather than by a special case. This is deliberate: one
mode's opinion is a signal, not a consensus, and the whole point of this layer is
to stop the engine treating them as the same thing.

This module is pure and dependency-light — it never touches MT5, the database or
the network. Everything it needs is passed in, which is what makes it testable
without a broker.
"""
from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("JARVIS_ModeAggregator")

__all__ = [
    "STYLE_ORDER",
    "STRONG_UTILITY",
    "ModeReliability",
    "ModeReliabilityModel",
    "reliability_weight",
    "StyleVote",
    "AggregatedDecision",
    "aggregate_symbol",
    "select_from_candidates",
    "DEFAULT_REPORT_PATH",
]

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
# Canonical evaluation order. Also the tie-break order: when two styles disagree
# and the weighted pools are exactly equal, the earlier style wins, so the
# result is deterministic rather than dependent on dict iteration order.
STYLE_ORDER: Tuple[str, ...] = ("SWING", "DAY_TRADING", "SCALP")

# A utility score at or above this is treated as a genuinely strong setup by the
# arbiter's own grade table (GRADE B starts at 0.95, GRADE A at 1.30). Used both
# as the strength normaliser and as the bar for calling an opposing vote
# "dissenting" rather than merely "different".
STRONG_UTILITY: float = 1.0

# Shrinkage constant for the reliability weight. A style needs on the order of K
# trades before its measured expectancy is taken at face value; below that the
# weight is pulled toward the neutral 0.5.
_RELIABILITY_SHRINK_K: float = 30.0

# A per-symbol override is only trusted once the symbol has enough of its own
# trades to say something the pooled number does not. Below this, the pooled
# style weight is used instead — a 4-trade symbol record is noise.
_MIN_TRADES_FOR_SYMBOL_STATS: int = 10

# Component weights for the 0-100 consensus score. Agreement dominates because
# it is the only component that measures something the arbiter cannot see.
_W_AGREEMENT: float = 0.40
_W_STRENGTH: float = 0.30
_W_RELIABILITY: float = 0.15
_W_REGIME: float = 0.15

# Confidence-tier floors.
_HIGH_SCORE: float = 55.0
_MEDIUM_SCORE: float = 40.0
_LOW_SCORE: float = 25.0
_MEDIUM_RATIO: float = 0.66
_LOW_RATIO: float = 0.50

DEFAULT_REPORT_PATH: str = os.path.join("reports", "mode_backtest_report.json")


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else (hi if value > hi else value)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────────────────────────────────────
# Reliability
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ModeReliability:
    """What one trading style has actually demonstrated on real history.

    ``weight`` is the number the consensus maths consumes; the raw fields are
    carried alongside so the UI can explain *why* a style is being discounted
    instead of just showing an unexplained multiplier.
    """

    style: str
    expectancy_r: float = 0.0
    trades: int = 0
    profit_factor: float = 0.0
    win_rate_pct: float = 0.0
    weight: float = 0.5
    source: str = "default"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "style": self.style,
            "expectancy_r": self.expectancy_r,
            "trades": self.trades,
            "profit_factor": self.profit_factor,
            "win_rate_pct": self.win_rate_pct,
            "weight": self.weight,
            "source": self.source,
        }

    def describe(self) -> str:
        return (
            f"{self.style}: weight {self.weight:.2f} "
            f"(exp {self.expectancy_r:+.3f}R over {self.trades} trades, "
            f"PF {self.profit_factor:.2f})"
        )


def reliability_weight(
    expectancy_r: float,
    trades: int,
    profit_factor: float = 0.0,
) -> float:
    """Map a style's realised record onto a 0.05-0.95 trust weight.

    The shape is deliberately gentle. Three separate mechanisms act in order:

    1. ``tanh`` squashes expectancy onto a bounded curve so a single spectacular
       symbol cannot dominate. A style at +0.25R expectancy is ~0.88 of the way
       to the maximum; one at -0.25R is the mirror image.
    2. Shrinkage by sample size pulls thin records toward the neutral 0.5.
    3. A profit factor below 1.0 is a hard cap at 0.5. Expectancy can be dragged
       positive by one outlier win even when the style loses money overall;
       profit factor cannot. When they disagree, the more conservative reading
       wins.

    Returns 0.5 for a style with no recorded trades — neutral, never confident.
    """
    trades = int(trades or 0)
    if trades <= 0:
        return 0.5

    exp = _as_float(expectancy_r, 0.0)
    raw = 0.5 + 0.5 * math.tanh(exp / 0.25)

    shrink = trades / (trades + _RELIABILITY_SHRINK_K)
    weight = 0.5 + (raw - 0.5) * shrink

    pf = _as_float(profit_factor, 0.0)
    if pf and math.isfinite(pf) and pf < 1.0:
        weight = min(weight, 0.5)

    return round(_clamp(weight, 0.05, 0.95), 4)


class ModeReliabilityModel:
    """Per-style (and optionally per-symbol) trust weights from measured history.

    Built from ``reports/mode_backtest_report.json``, which the mode backtest
    writes. The model is intentionally tolerant of a missing or partial report:
    an absent file yields a neutral model where every style carries 0.5, which
    degrades the aggregator to an unweighted vote rather than breaking it. A
    trading system that refuses to start because a *report* is missing would be
    a worse system.
    """

    def __init__(
        self,
        styles: Optional[Dict[str, ModeReliability]] = None,
        per_symbol: Optional[Dict[str, Dict[str, float]]] = None,
        source_path: Optional[str] = None,
    ):
        self._styles: Dict[str, ModeReliability] = dict(styles or {})
        self._per_symbol: Dict[str, Dict[str, float]] = dict(per_symbol or {})
        self.source_path = source_path

    # ── construction ────────────────────────────────────────────────────────
    @classmethod
    def neutral(cls) -> "ModeReliabilityModel":
        return cls(
            styles={
                s: ModeReliability(style=s, weight=0.5, source="neutral")
                for s in STYLE_ORDER
            }
        )

    @classmethod
    def from_report(cls, path: Optional[str] = None) -> "ModeReliabilityModel":
        """Load measured weights, falling back to neutral on any failure."""
        path = path or DEFAULT_REPORT_PATH
        if not os.path.exists(path):
            logger.info("mode reliability report not found at %s — using neutral weights", path)
            return cls.neutral()

        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError) as exc:
            logger.warning("could not read %s (%s) — using neutral weights", path, exc)
            return cls.neutral()

        styles: Dict[str, ModeReliability] = {}
        per_symbol: Dict[str, Dict[str, float]] = {}

        for mode in payload.get("modes", []) or []:
            style = str(mode.get("style") or "").upper()
            if not style:
                continue
            pooled = mode.get("pooled") or {}
            trades = int(_as_float(pooled.get("trades"), 0.0))
            exp = _as_float(pooled.get("expectancy_r"), 0.0)
            pf = _as_float(pooled.get("profit_factor"), 0.0)
            wr = _as_float(pooled.get("win_rate_pct"), 0.0)

            styles[style] = ModeReliability(
                style=style,
                expectancy_r=round(exp, 4),
                trades=trades,
                profit_factor=round(pf, 3),
                win_rate_pct=round(wr, 2),
                weight=reliability_weight(exp, trades, pf),
                source=os.path.basename(path),
            )

            # Per-symbol overrides, only where the symbol has earned its own say.
            for row in mode.get("per_symbol", []) or []:
                sym = str(row.get("symbol") or "").upper()
                n = int(_as_float(row.get("trades"), 0.0))
                if not sym or n < _MIN_TRADES_FOR_SYMBOL_STATS:
                    continue
                per_symbol.setdefault(sym, {})[style] = reliability_weight(
                    _as_float(row.get("expectancy_r"), 0.0),
                    n,
                    _as_float(row.get("profit_factor"), 0.0),
                )

        if not styles:
            logger.warning("%s contained no usable modes — using neutral weights", path)
            return cls.neutral()

        # Any canonical style the report omitted still needs a weight.
        for style in STYLE_ORDER:
            styles.setdefault(
                style, ModeReliability(style=style, weight=0.5, source="unreported")
            )

        return cls(styles=styles, per_symbol=per_symbol, source_path=path)

    # ── lookup ──────────────────────────────────────────────────────────────
    def for_style(self, style: str) -> ModeReliability:
        key = str(style or "").upper()
        if key in self._styles:
            return self._styles[key]
        if key in ("INTRADAY", "DAY"):
            return self._styles.get(
                "DAY_TRADING", ModeReliability(style="DAY_TRADING", weight=0.5)
            )
        return ModeReliability(style=key or "UNKNOWN", weight=0.5)

    def weight(self, style: str, symbol: Optional[str] = None) -> float:
        """Trust weight for ``style``, preferring a symbol-specific reading.

        The per-symbol override only exists when that symbol cleared
        ``_MIN_TRADES_FOR_SYMBOL_STATS`` in the mode report, so this cannot be
        dragged around by a symbol that traded four times.
        """
        if symbol:
            sym = str(symbol).upper()
            override = self._per_symbol.get(sym, {}).get(str(style or "").upper())
            if override is not None:
                return float(override)
        return float(self.for_style(style).weight)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source_path": self.source_path,
            "styles": {k: v.to_dict() for k, v in self._styles.items()},
            "symbol_overrides": len(self._per_symbol),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Votes and decisions
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class StyleVote:
    """One style's opinion about one symbol, with its trust already applied."""

    symbol: str
    trade_style: str
    bias: str
    utility_score: float = 0.0
    win_prob: float = 0.0
    ml_prob: float = 0.0
    confluence_score: float = 0.0
    expected_value: float = 0.0
    setup_grade: str = "GRADE C"
    regime: str = "UNKNOWN"
    strategy: str = "UNKNOWN"
    timeframe: str = ""
    reliability: float = 0.5
    regime_multiplier: float = 1.0
    is_actionable: bool = False

    @property
    def direction(self) -> str:
        b = str(self.bias or "").upper()
        return b if b in ("BUY", "SELL") else "NONE"

    @property
    def signed_contribution(self) -> float:
        """Utility scaled by trust, signed by direction.

        A vote with no direction or no utility contributes exactly nothing —
        an abstention must not be able to move the pool.
        """
        if self.direction == "NONE" or self.utility_score <= 0:
            return 0.0
        sign = 1.0 if self.direction == "BUY" else -1.0
        return sign * self.utility_score * self.reliability

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "trade_style": self.trade_style,
            "bias": self.bias,
            "direction": self.direction,
            "utility_score": round(self.utility_score, 4),
            "win_prob": round(self.win_prob, 2),
            "ml_prob": round(self.ml_prob, 4),
            "confluence_score": round(self.confluence_score, 2),
            "expected_value": round(self.expected_value, 4),
            "setup_grade": self.setup_grade,
            "regime": self.regime,
            "strategy": self.strategy,
            "timeframe": self.timeframe,
            "reliability": round(self.reliability, 4),
            "regime_multiplier": round(self.regime_multiplier, 3),
            "signed_contribution": round(self.signed_contribution, 4),
            "is_actionable": self.is_actionable,
        }


@dataclass
class AggregatedDecision:
    """The consensus view of one symbol across every style that voted."""

    symbol: str
    direction: str = "NONE"
    consensus_score: float = 0.0
    agreement_ratio: float = 0.0
    agreement_count: int = 0
    available_count: int = 0
    confidence_tier: str = "NONE"
    is_tradeable: bool = False
    dissenting: bool = False
    strong_dissent: bool = False
    votes: List[StyleVote] = field(default_factory=list)
    supporting_styles: List[str] = field(default_factory=list)
    dissenting_styles: List[str] = field(default_factory=list)
    abstaining_styles: List[str] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "consensus_score": round(self.consensus_score, 2),
            "agreement_ratio": round(self.agreement_ratio, 4),
            "agreement_count": self.agreement_count,
            "available_count": self.available_count,
            "confidence_tier": self.confidence_tier,
            "is_tradeable": self.is_tradeable,
            "dissenting": self.dissenting,
            "strong_dissent": self.strong_dissent,
            "supporting_styles": list(self.supporting_styles),
            "dissenting_styles": list(self.dissenting_styles),
            "abstaining_styles": list(self.abstaining_styles),
            "rationale": self.rationale,
            "votes": [v.to_dict() for v in self.votes],
        }

    def describe(self) -> str:
        if self.direction == "NONE":
            return f"{self.symbol}: no directional consensus ({self.available_count} style(s) voted)"
        return (
            f"{self.symbol} {self.direction} [{self.confidence_tier}] "
            f"score {self.consensus_score:.1f}, agreement {self.agreement_ratio:.0%} "
            f"({self.agreement_count}/{self.available_count})"
        )


def _confidence_tier(
    *,
    agreement_ratio: float,
    consensus_score: float,
    available_count: int,
    strong_dissent: bool,
) -> str:
    """Bucket a consensus into HIGH / MEDIUM / LOW / NONE.

    HIGH demands unanimity among the styles that voted, a real score, at least
    two voters and no strong opposing case. Note that unanimity is measured over
    *voters*: three styles where one abstained can still be HIGH, because an
    abstention is the absence of an opinion, not a disagreement.
    """
    if available_count < 1 or agreement_ratio <= 0.0:
        return "NONE"
    if (
        agreement_ratio >= 0.999
        and consensus_score >= _HIGH_SCORE
        and available_count >= 2
        and not strong_dissent
    ):
        return "HIGH"
    if agreement_ratio >= _MEDIUM_RATIO and consensus_score >= _MEDIUM_SCORE and available_count >= 2:
        return "MEDIUM"
    if agreement_ratio >= _LOW_RATIO and consensus_score >= _LOW_SCORE:
        return "LOW"
    return "NONE"


def aggregate_symbol(
    symbol: str,
    votes: Sequence[StyleVote],
    *,
    model: Optional[ModeReliabilityModel] = None,
) -> AggregatedDecision:
    """Reduce one symbol's per-style votes to a single consensus decision.

    Pure: it reads nothing global and mutates nothing. The reliability model is
    an argument precisely so a caller can replay history under a frozen model.
    """
    symbol = str(symbol or "UNKNOWN").upper()
    model = model or ModeReliabilityModel.neutral()

    # Re-weight every vote against the model. Callers are free to pass votes
    # with a provisional reliability; the model is authoritative here so two
    # call sites cannot disagree about what a style is worth.
    prepared: List[StyleVote] = []
    for v in votes:
        w = model.weight(v.trade_style, symbol)
        prepared.append(
            StyleVote(
                symbol=symbol,
                trade_style=v.trade_style,
                bias=v.bias,
                utility_score=_as_float(v.utility_score, 0.0),
                win_prob=_as_float(v.win_prob, 0.0),
                ml_prob=_as_float(v.ml_prob, 0.0),
                confluence_score=_as_float(v.confluence_score, 0.0),
                expected_value=_as_float(v.expected_value, 0.0),
                setup_grade=v.setup_grade,
                regime=v.regime,
                strategy=v.strategy,
                timeframe=v.timeframe,
                reliability=w,
                regime_multiplier=_as_float(v.regime_multiplier, 1.0) or 1.0,
                is_actionable=bool(v.is_actionable),
            )
        )

    prepared.sort(key=lambda v: STYLE_ORDER.index(v.trade_style)
                  if v.trade_style in STYLE_ORDER else len(STYLE_ORDER))

    directional = [v for v in prepared if v.direction != "NONE" and v.utility_score > 0]
    abstaining = [v.trade_style for v in prepared if v not in directional]

    if not directional:
        return AggregatedDecision(
            symbol=symbol,
            direction="NONE",
            available_count=0,
            abstaining_styles=abstaining,
            votes=prepared,
            rationale="no style produced a directional, positive-utility candidate",
        )

    buy_pool = sum(v.signed_contribution for v in directional if v.direction == "BUY")
    sell_pool = -sum(v.signed_contribution for v in directional if v.direction == "SELL")

    total_pool = buy_pool + sell_pool
    if total_pool <= 0:
        return AggregatedDecision(
            symbol=symbol,
            direction="NONE",
            available_count=len(directional),
            abstaining_styles=abstaining,
            votes=prepared,
            rationale="directional utilities cancelled out",
        )

    if buy_pool > sell_pool:
        direction = "BUY"
        winning_pool, losing_pool = buy_pool, sell_pool
    elif sell_pool > buy_pool:
        direction = "SELL"
        winning_pool, losing_pool = sell_pool, buy_pool
    else:
        # Exact tie: fall back to the first style in STYLE_ORDER that voted, so
        # the outcome is deterministic instead of dict-order dependent.
        first = directional[0]
        direction = first.direction
        winning_pool = buy_pool if direction == "BUY" else sell_pool
        losing_pool = sell_pool if direction == "BUY" else buy_pool

    supporting = [v for v in directional if v.direction == direction]
    dissenting = [v for v in directional if v.direction != direction]

    agreement_count = len(supporting)
    available_count = len(directional)
    agreement_ratio = agreement_count / available_count if available_count else 0.0

    strong_dissent = any(v.utility_score >= STRONG_UTILITY for v in dissenting)

    # ── Component scores, each normalised to 0..1 ───────────────────────────
    # Agreement: 0.5 (a bare majority) maps to 0, unanimity maps to 1.
    agreement_norm = _clamp((agreement_ratio - 0.5) / 0.5, 0.0, 1.0)

    # Strength: mean utility of the supporting side, saturating so that a single
    # extreme reading cannot pin the score at 100.
    mean_utility = (
        sum(v.utility_score for v in supporting) / len(supporting) if supporting else 0.0
    )
    strength_norm = _clamp(1.0 - math.exp(-mean_utility / STRONG_UTILITY), 0.0, 1.0)

    # Reliability: mean trust of the supporting side, re-centred on the neutral
    # 0.5 so a style nobody trusts cannot inflate the consensus.
    mean_reliability = (
        sum(v.reliability for v in supporting) / len(supporting) if supporting else 0.5
    )
    reliability_norm = _clamp((mean_reliability - 0.5) / 0.5, 0.0, 1.0)

    # Regime: the arbiter's style-vs-regime multiplier, mapped from its own
    # practical range (0.75 penalised .. 1.25 rewarded) onto 0..1.
    mean_regime = (
        sum(v.regime_multiplier for v in supporting) / len(supporting) if supporting else 1.0
    )
    regime_norm = _clamp((mean_regime - 0.75) / 0.5, 0.0, 1.0)

    consensus_score = 100.0 * (
        _W_AGREEMENT * agreement_norm
        + _W_STRENGTH * strength_norm
        + _W_RELIABILITY * reliability_norm
        + _W_REGIME * regime_norm
    )

    tier = _confidence_tier(
        agreement_ratio=agreement_ratio,
        consensus_score=consensus_score,
        available_count=available_count,
        strong_dissent=strong_dissent,
    )

    is_tradeable = bool(
        direction != "NONE"
        and tier in ("HIGH", "MEDIUM")
        and winning_pool > 0
        and not strong_dissent
    )

    rationale = (
        f"{agreement_count}/{available_count} style(s) agree on {direction} "
        f"(pool {winning_pool:.2f} vs {losing_pool:.2f}); "
        f"agreement {agreement_ratio:.0%}, score {consensus_score:.1f}"
    )
    if strong_dissent:
        rationale += "; blocked by a strong opposing setup"
    elif dissenting:
        names = ", ".join(v.trade_style for v in dissenting)
        rationale += f"; dissent from {names}"
    if abstaining:
        rationale += f"; no signal from {', '.join(abstaining)}"

    return AggregatedDecision(
        symbol=symbol,
        direction=direction,
        consensus_score=round(consensus_score, 2),
        agreement_ratio=round(agreement_ratio, 4),
        agreement_count=agreement_count,
        available_count=available_count,
        confidence_tier=tier,
        is_tradeable=is_tradeable,
        dissenting=bool(dissenting),
        strong_dissent=strong_dissent,
        votes=prepared,
        supporting_styles=[v.trade_style for v in supporting],
        dissenting_styles=[v.trade_style for v in dissenting],
        abstaining_styles=abstaining,
        rationale=rationale,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Bridge from the arbiter
# ─────────────────────────────────────────────────────────────────────────────
def _vote_from_candidate(candidate: Any, model: ModeReliabilityModel) -> StyleVote:
    """Adapt an arbiter ``CandidateOpportunity`` (or a plain dict) into a vote.

    Attribute access is used with ``getattr`` fallbacks so this accepts both the
    live dataclass and the serialised dict the API hands back, without either
    side having to know about the other.
    """
    if isinstance(candidate, dict):
        get = lambda k, d=None: candidate.get(k, d)  # noqa: E731
    else:
        get = lambda k, d=None: getattr(candidate, k, d)  # noqa: E731

    symbol = str(get("symbol", "UNKNOWN") or "UNKNOWN").upper()
    style = str(get("trade_style", "SWING") or "SWING").upper()
    return StyleVote(
        symbol=symbol,
        trade_style=style,
        bias=str(get("bias", "HOLD") or "HOLD"),
        utility_score=_as_float(get("utility_score", 0.0), 0.0),
        win_prob=_as_float(get("win_prob", 0.0), 0.0),
        ml_prob=_as_float(get("ml_prob", 0.0), 0.0),
        confluence_score=_as_float(get("confluence_score", 0.0), 0.0),
        expected_value=_as_float(get("expected_value", 0.0), 0.0),
        setup_grade=str(get("setup_grade", "GRADE C") or "GRADE C"),
        regime=str(get("regime", "UNKNOWN") or "UNKNOWN"),
        strategy=str(get("strategy", "UNKNOWN") or "UNKNOWN"),
        timeframe=str(get("timeframe", "") or ""),
        reliability=model.weight(style, symbol),
        is_actionable=bool(get("is_actionable", False)),
    )


def select_from_candidates(
    candidates: Iterable[Any],
    *,
    model: Optional[ModeReliabilityModel] = None,
    only_tradeable: bool = False,
) -> List[AggregatedDecision]:
    """Group arbiter candidates by symbol and reduce each group to a consensus.

    Returned sorted best-first by ``(is_tradeable, consensus_score,
    agreement_ratio)``, so the caller can take the head as "the" trade without
    re-implementing the ordering.
    """
    model = model or ModeReliabilityModel.neutral()
    grouped: Dict[str, List[StyleVote]] = {}

    for cand in candidates or []:
        try:
            vote = _vote_from_candidate(cand, model)
        except Exception as exc:  # a malformed candidate must not kill the scan
            logger.debug("skipping unreadable candidate: %s", exc)
            continue
        grouped.setdefault(vote.symbol, []).append(vote)

    decisions = [aggregate_symbol(sym, votes, model=model) for sym, votes in grouped.items()]

    if only_tradeable:
        decisions = [d for d in decisions if d.is_tradeable]

    decisions.sort(
        key=lambda d: (d.is_tradeable, d.consensus_score, d.agreement_ratio, d.available_count),
        reverse=True,
    )
    return decisions

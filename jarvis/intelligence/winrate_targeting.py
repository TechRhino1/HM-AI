"""
JARVIS AI 5.0 — Per-Symbol Win-Rate Targeting.

THE MATHEMATICS THAT DRIVES THIS DESIGN
---------------------------------------
Win rate is not an independent objective. For any strategy

    expectancy (in R) = WR * avg_win_R - (1 - WR) * avg_loss_R

so a win rate can always be manufactured by shrinking the target relative to
the stop — a 0.1R target against a 1R stop wins ~90% of the time and loses
money. Tuning to a bare win-rate number is therefore a trap, and a system that
reports "75% win rate" while bleeding expectancy is worse than one that reports
55% honestly.

This module treats the 75% target as a *constrained* objective:

    maximise expectancy_R   subject to   win_rate >= target
                                         trades    >= min_trades
                                         expectancy_R > 0

and reports, per symbol, whether the target is reachable at all and what binds
when it is not. Two consequences are accepted up front and reported rather than
hidden:

  * Reaching a high win rate requires a SMALL target in R. The frontier is
    monotone: smaller tp_r raises win rate and lowers expectancy. The calibrator
    walks that frontier and picks the highest-expectancy point that still clears
    the target.
  * A per-symbol target may be arithmetically unreachable on three months of
    data. When it is, the profile records ``target_met=False`` and names the
    binding constraint instead of overfitting to the sample.

VALIDATION
----------
Every geometry is chosen on a training fold and scored on a purged out-of-sample
fold (``jarvis.learning.walk_forward.PurgedKFold``). The headline win rate in the
report is the OOS figure, never the in-sample fit.

AI COMPONENTS
-------------
1. Confidence calibration — isotonic (PAV) mapping from the engine's raw score to
   a realised win probability, fitted per symbol. This is meta-labeling: it
   learns when the engine is over-confident.
2. Regime-edge policy — a learned table of symbol x regime expectancy, used to
   switch regimes off when they have demonstrated no edge.
3. Regime-conditional geometry — the calibrator may select a different target
   distance per regime (trending markets sustain larger targets than ranges).
4. Self-learning — profiles persist to JSON and can be refit from realised
   outcomes via :meth:`WRProfileStore.merge_realised`.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

from jarvis.backtesting.trade_simulator import (
    Geometry,
    TradeOutcome,
    select_sequential,
    simulate_all_candidates,
    summarise,
)
from jarvis.data.symbol_registry import get_dollar_risk_per_price_unit, resolve as resolve_symbol
from jarvis.learning.walk_forward import PurgedKFold

logger = logging.getLogger("JARVIS_WinRateTargeting")


# ─────────────────────────────────────────────────────────────────────────────
# Calibration artefacts
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ScoreCalibration:
    """Isotonic map from the engine's raw score to a realised win probability."""

    bin_edges: List[float] = field(default_factory=list)
    bin_win_rate: List[float] = field(default_factory=list)
    bin_count: List[int] = field(default_factory=list)
    brier: float = 0.0
    n: int = 0

    def predict(self, score: float) -> float:
        """Monotone lookup; falls back to the overall rate outside the fitted range."""
        if not self.bin_edges or not self.bin_win_rate:
            return 0.5
        edges = self.bin_edges
        if score <= edges[0]:
            return float(self.bin_win_rate[0])
        if score >= edges[-1]:
            return float(self.bin_win_rate[-1])
        for i in range(len(edges) - 1):
            if edges[i] <= score < edges[i + 1]:
                # Clamp defensively: a profile loaded from disk could in
                # principle carry mismatched edge/rate arrays.
                k = min(i, len(self.bin_win_rate) - 1)
                return float(self.bin_win_rate[k])
        return float(self.bin_win_rate[-1])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RegimeEdge:
    """Learned per-regime edge, and whether the regime is cleared for trading."""

    regime: str
    trades: int
    win_rate: float
    expectancy_r: float
    total_r: float
    enabled: bool
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FrontierPoint:
    """One point on the win-rate / expectancy frontier, at a fixed target size.

    The frontier is the central evidence for whether a win-rate target is
    reachable. For each target size the calibrator reports both the best win
    rate found and the best win rate that still carries positive expectancy; the
    gap between them is the part of the target that can only be bought by
    accepting a losing system.
    """

    tp_r: float
    best_wr: float
    best_wr_trades: int
    best_wr_expectancy_r: float
    wr_at_positive_expectancy: float
    expectancy_at_positive_wr: float
    trades_at_positive_wr: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WRTargetProfile:
    """A calibrated, persisted trading profile for one symbol."""

    symbol: str
    target_wr: float
    geometry: Geometry
    score_col: str = "score"

    # In-sample fit (the configuration selection)
    n_trades: int = 0
    win_rate: float = 0.0
    expectancy_r: float = 0.0
    payoff: float = 0.0
    profit_factor: float = 0.0
    total_r: float = 0.0
    max_dd_r: float = 0.0
    target_met: bool = False
    binding_constraint: str = ""

    # Out-of-sample validation (the honest headline)
    oos_trades: int = 0
    oos_win_rate: float = 0.0
    oos_expectancy_r: float = 0.0
    oos_profit_factor: float = 0.0
    oos_total_r: float = 0.0
    oos_target_met: bool = False
    folds: int = 0

    # Out-of-sample figures BEFORE the learned regime policy is applied, and the
    # source the policy was fitted on. Publishing both lets a reader see exactly
    # how much of the headline the policy is responsible for, and judge whether
    # that is a real edge or a filter tuned to the sample.
    oos_pre_policy_trades: int = 0
    oos_pre_policy_win_rate: float = 0.0
    oos_pre_policy_expectancy_r: float = 0.0
    policy_fitted_on: str = ""

    # AI artefacts
    score_calibration: Optional[ScoreCalibration] = None
    regime_edge: Dict[str, RegimeEdge] = field(default_factory=dict)
    regime_geometry: Dict[str, Geometry] = field(default_factory=dict)
    # Win-rate / expectancy frontier across target sizes (in-sample diagnostic).
    frontier: List[FrontierPoint] = field(default_factory=list)

    notes: str = ""

    # ── convenience ────────────────────────────────────────────────────────
    @property
    def enabled_regimes(self) -> List[str]:
        return [r for r, e in self.regime_edge.items() if e.enabled]

    def effective_target_r(self, regime: Optional[str] = None) -> float:
        """Target distance in R for a regime, falling back to the global geometry."""
        if regime and regime in self.regime_geometry:
            return float(self.regime_geometry[regime].tp_r)
        return float(self.geometry.tp_r)

    def geometry_for(self, regime: Optional[str] = None) -> Geometry:
        if regime and regime in self.regime_geometry:
            return self.regime_geometry[regime]
        return self.geometry

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["geometry"] = self.geometry.to_dict()
        d["score_calibration"] = (
            self.score_calibration.to_dict() if self.score_calibration else None
        )
        d["regime_edge"] = {k: v.to_dict() for k, v in self.regime_edge.items()}
        d["regime_geometry"] = {k: v.to_dict() for k, v in self.regime_geometry.items()}
        d["frontier"] = [f.to_dict() for f in self.frontier]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WRTargetProfile":
        payload = dict(d)
        payload["geometry"] = Geometry.from_dict(payload.get("geometry") or {})
        sc = payload.get("score_calibration")
        payload["score_calibration"] = ScoreCalibration(**sc) if sc else None
        payload["regime_edge"] = {
            k: RegimeEdge(**v) for k, v in (payload.get("regime_edge") or {}).items()
        }
        payload["regime_geometry"] = {
            k: Geometry.from_dict(v) for k, v in (payload.get("regime_geometry") or {}).items()
        }
        payload["frontier"] = [FrontierPoint(**f) for f in (payload.get("frontier") or [])]
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in payload.items() if k in allowed})


# ─────────────────────────────────────────────────────────────────────────────
# Isotonic calibration (Pool Adjacent Violators)
# ─────────────────────────────────────────────────────────────────────────────
def isotonic_calibrate(
    scores: Sequence[float], wins: Sequence[int], *, n_bins: int = 8
) -> ScoreCalibration:
    """Fit a monotone score -> win-probability map with PAV.

    Raw scores are bucketed into quantile bins; the empirical win rate of each
    bin is then made monotone by pooling adjacent violators. Without the pooling
    step a lower score could appear to win more often than a higher one, which
    would make the calibrated probability useless as a decision input.
    """
    s = np.asarray(scores, dtype=float)
    w = np.asarray(wins, dtype=float)
    if len(s) == 0:
        return ScoreCalibration()
    if len(np.unique(s)) < 2:
        rate = float(w.mean()) if len(w) else 0.0
        return ScoreCalibration(bin_edges=[float(s[0]), float(s[0]) + 1e-9],
                                bin_win_rate=[rate], bin_count=[len(s)],
                                brier=float(np.mean((w - rate) ** 2)), n=len(s))

    n_bins = int(max(3, min(n_bins, len(s))))
    quantiles = np.linspace(0, 1, n_bins + 1)
    edges = np.unique(np.quantile(s, quantiles))
    if len(edges) < 3:
        edges = np.linspace(float(s.min()), float(s.max()), 3)

    idx = np.clip(np.digitize(s, edges[1:-1], right=False), 0, len(edges) - 2)
    n_orig = len(edges) - 1
    counts = np.array([int((idx == k).sum()) for k in range(n_orig)], dtype=float)
    winsum = np.array([float(w[idx == k].sum()) for k in range(n_orig)], dtype=float)
    rates = np.divide(winsum, np.maximum(counts, 1.0))
    orig_counts = counts.copy()

    # PAV: pool adjacent violators so the map is non-decreasing.
    #
    # Each surviving bucket keeps a record of which ORIGINAL bins it absorbed.
    # Without that bookkeeping the pooled rate list becomes shorter than the
    # edge list, and a later lookup by bin index runs off the end of the array.
    rates = list(rates)
    counts = list(counts)
    winsum = list(winsum)
    groups: List[List[int]] = [[k] for k in range(n_orig)]
    i = 0
    while i < len(rates) - 1:
        if rates[i] > rates[i + 1] + 1e-12:
            total = counts[i] + counts[i + 1]
            rates[i] = (winsum[i] + winsum[i + 1]) / max(total, 1.0)
            counts[i] = total
            winsum[i] = winsum[i] + winsum[i + 1]
            groups[i] = groups[i] + groups[i + 1]
            del rates[i + 1]
            del counts[i + 1]
            del winsum[i + 1]
            del groups[i + 1]
            if i > 0:
                i -= 1
        else:
            i += 1

    # Expand the pooled rates back to one rate per original bin so that
    # ``predict`` can index ``bin_win_rate`` with a bin number.
    per_bin = [0.0] * n_orig
    for gi, members in enumerate(groups):
        for b in members:
            per_bin[b] = float(rates[gi])

    pred = np.array([per_bin[k] for k in idx], dtype=float)
    brier = float(np.mean((pred - w) ** 2))
    return ScoreCalibration(
        bin_edges=[round(float(e), 6) for e in edges],
        bin_win_rate=[round(float(r), 4) for r in per_bin],
        bin_count=[int(c) for c in orig_counts],
        brier=round(brier, 5),
        n=int(len(s)),
    )


def regime_edge_table(
    outcomes: Sequence[TradeOutcome],
    *,
    min_trades: int = 5,
    disable_margin_r: float = 0.05,
    min_trades_to_disable: int = 12,
) -> Dict[str, RegimeEdge]:
    """Learn per-regime edge and switch off regimes with no demonstrated edge.

    A regime is disabled only when BOTH conditions hold:

      * it has at least ``min_trades_to_disable`` samples, and
      * its expectancy is worse than ``-disable_margin_r``.

    The margin matters. Disabling a regime on a marginal negative expectancy
    (an early version disabled gold's dominant ``TREND_BEAR`` regime on
    ``-0.011R``) is noise-fitting: it removed 490 of ~1,500 bars and cut the
    trade count from 144 to 17 for no demonstrated reason. A regime must be
    *clearly* unprofitable before it is switched off.

    Disabling is the conservative direction — it can only remove trades, never
    add risk — but removing trades for no reason is not conservative, it is just
    a smaller sample.
    """
    buckets: Dict[str, List[TradeOutcome]] = {}
    for o in outcomes:
        buckets.setdefault(str(o.regime), []).append(o)

    table: Dict[str, RegimeEdge] = {}
    for regime, outs in buckets.items():
        s = summarise(outs)
        if s["trades"] < min_trades:
            enabled, reason = True, f"insufficient samples ({s['trades']}<{min_trades}) - left enabled"
        elif s["trades"] < min_trades_to_disable:
            enabled, reason = True, (
                f"only {s['trades']} samples (<{min_trades_to_disable}) - too few to disable"
            )
        elif s["expectancy_r"] <= -abs(disable_margin_r):
            enabled, reason = False, (
                f"expectancy {s['expectancy_r']:+.3f}R worse than -{abs(disable_margin_r):.3f}R "
                f"over {s['trades']} trades"
            )
        else:
            enabled, reason = True, f"expectancy within tolerance ({s['expectancy_r']:+.3f}R)"
        table[regime] = RegimeEdge(
            regime=regime,
            trades=s["trades"],
            win_rate=s["win_rate"],
            expectancy_r=s["expectancy_r"],
            total_r=s["total_r"],
            enabled=enabled,
            reason=reason,
        )
    return table


# ─────────────────────────────────────────────────────────────────────────────
# Calibrator
# ─────────────────────────────────────────────────────────────────────────────
def default_geometry_grid(
    *, coarse: bool = True, score_col_max: float = 1.0
) -> List[Geometry]:
    """The geometry search space.

    ``tp_r`` is the dominant control on win rate, so it is sampled densely
    below 1R where the target lives. Breakeven locking is included mostly as a
    negative control: it should be *disabled* for high-win-rate profiles, and
    the calibration is expected to discover that rather than assume it.

    ``max_bars`` matters more than it looks. Non-overlapping trades on one
    instrument are bounded by ``bars / max_bars``, so a 48-bar time stop caps a
    three-month H1 sample at roughly 30 trades — below the sample size needed to
    assert a 75% win rate. Including a shorter stop lets the calibrator buy
    statistical power when the edge does not need long holds, at the honest cost
    of cutting trades that would have recovered.
    """
    if coarse:
        # Targets below 0.4R are included so the frontier can show whether a high
        # win rate is reachable at all, and at what expectancy cost. Without them
        # the calibration would sit pinned at the smallest target it was offered.
        tp_r_values = [0.25, 0.3, 0.4, 0.5, 0.6, 0.75, 1.0, 1.5]
        be_values: List[Optional[float]] = [None, 1.0]
        pc_values: List[Optional[float]] = [None, 0.5]
        max_bars_values = [24, 48]
    else:
        tp_r_values = [0.4, 0.45, 0.5, 0.55, 0.6, 0.7, 0.8, 0.9, 1.0, 1.25, 1.5, 2.0]
        be_values = [None, 0.75, 1.0, 1.5, 2.0]
        pc_values = [None, 0.5, 0.75]
        max_bars_values = [16, 24, 36, 48]

    grid: List[Geometry] = []
    for tp in tp_r_values:
        for be in be_values:
            for pc in pc_values:
                for mb in max_bars_values:
                    grid.append(
                        Geometry(
                            tp_r=float(tp),
                            be_trigger_r=be,
                            fast_cash_r=pc,
                            fast_cash_pct=0.5,
                            trail_atr=None,
                            max_bars=int(mb),
                            min_score=0.0,
                        )
                    )
    return grid


def _rank_key(s: Dict[str, float], target_wr: float, min_trades: int) -> Tuple:
    """Lexicographic selection key.

    Tier 2: target met with positive expectancy  -> rank by expectancy.
    Tier 1: positive expectancy, target missed   -> rank by win rate.
    Tier 0: negative expectancy or too few trades -> rank by win rate only.
    """
    if s["trades"] < min_trades or s["expectancy_r"] <= 0:
        return (0, 0.0, s["win_rate"], s["trades"])
    if s["win_rate"] >= target_wr:
        return (2, s["expectancy_r"], s["win_rate"], s["trades"])
    return (1, s["win_rate"], s["expectancy_r"], s["trades"])


def _mean_summary(summaries: Sequence[Dict[str, float]]) -> Dict[str, float]:
    """Average a list of ``summarise`` dicts into one synthetic summary.

    Used only to break ties between configurations the folds already voted for,
    so that the tie-break stays on training data.
    """
    if not summaries:
        return {"trades": 0, "win_rate": 0.0, "expectancy_r": 0.0, "profit_factor": 0.0}
    keys = set(summaries[0].keys())
    for s in summaries[1:]:
        keys &= set(s.keys())
    return {
        k: float(np.mean([float(s[k]) for s in summaries]))
        for k in keys
        if isinstance(summaries[0].get(k), (int, float))
    }


class WRTargetCalibrator:
    """Fits per-symbol win-rate-targeted profiles with purged OOS validation."""

    def __init__(
        self,
        *,
        target_wr: float = 0.75,
        min_trades: int = 12,
        folds: int = 4,
        embargo_frac: float = 0.03,
        score_quantiles: Sequence[float] = (0.0, 0.50, 0.65, 0.75, 0.85),
        coarse_grid: bool = True,
        enable_regime_policy: bool = True,
        regime_geometry: bool = True,
        commission_per_lot: float = 0.0,
    ):
        self.target_wr = float(target_wr)
        self.min_trades = int(min_trades)
        self.folds = int(folds)
        self.embargo_frac = float(embargo_frac)
        self.score_quantiles = list(score_quantiles)
        self.coarse_grid = coarse_grid
        self.enable_regime_policy = enable_regime_policy
        self.regime_geometry = regime_geometry
        self.commission_per_lot = float(commission_per_lot)

    # ── internals ──────────────────────────────────────────────────────────
    def _cost_price_equiv(self, symbol: str, money_per_unit: float) -> float:
        if money_per_unit <= 0:
            return 0.0
        cfg_comm = 0.0
        try:
            from jarvis.intelligence.symbol_profile_config import get_symbol_profile_config

            cfg_comm = float(getattr(get_symbol_profile_config(symbol), "commission_per_lot", 0.0) or 0.0)
        except Exception:
            cfg_comm = 0.0
        comm = self.commission_per_lot or cfg_comm
        return comm / money_per_unit

    def _compute_frontier(
        self,
        sim_cache: Dict[str, List[TradeOutcome]],
        combos: List[Tuple[Geometry, float]],
        regimes: Optional[Set[str]] = None,
    ) -> List[FrontierPoint]:
        """Win-rate / expectancy frontier, grouped by target size.

        For each distinct ``tp_r`` this scans every other geometry dimension and
        threshold and records:

          * the highest win rate found at all, with its expectancy (which may be
            negative — that is the point);
          * the highest win rate that still has positive expectancy.

        Reading the two side by side shows precisely how much of a win-rate
        target is real and how much is bought by accepting a losing system.

        ``regimes`` restricts the scan to the regimes the learned policy still
        permits, so the frontier describes the deployed system rather than a
        superset of it.
        """
        by_tp: Dict[float, List[Tuple[Geometry, float, Dict[str, float]]]] = {}
        for g, thr in combos:
            by_tp.setdefault(float(g.tp_r), []).append(
                (g, thr, summarise(select_sequential(sim_cache[g.key()], min_score=thr, regimes=regimes)))
            )

        points: List[FrontierPoint] = []
        for tp_r in sorted(by_tp):
            rows = by_tp[tp_r]
            # Best win rate regardless of expectancy.
            best_any = max(rows, key=lambda r: (r[2]["win_rate"], r[2]["trades"]))
            # Best win rate subject to positive expectancy and a usable sample.
            viable = [
                r for r in rows
                if r[2]["expectancy_r"] > 0 and r[2]["trades"] >= self.min_trades
            ]
            if viable:
                best_pos = max(viable, key=lambda r: (r[2]["win_rate"], r[2]["expectancy_r"]))
                pos_wr = best_pos[2]["win_rate"]
                pos_exp = best_pos[2]["expectancy_r"]
                pos_n = best_pos[2]["trades"]
            else:
                pos_wr = 0.0
                pos_exp = 0.0
                pos_n = 0
            points.append(FrontierPoint(
                tp_r=float(tp_r),
                best_wr=float(best_any[2]["win_rate"]),
                best_wr_trades=int(best_any[2]["trades"]),
                best_wr_expectancy_r=float(best_any[2]["expectancy_r"]),
                wr_at_positive_expectancy=float(pos_wr),
                expectancy_at_positive_wr=float(pos_exp),
                trades_at_positive_wr=int(pos_n),
            ))
        return points

    def _fold_windows(
        self, n_candidates: int, order: Sequence[int]
    ) -> List[Tuple[List[Tuple[int, int]], Tuple[int, int]]]:
        """Build (train_windows, test_window) pairs over candidate positions.

        ``order`` maps a candidate position to its bar index. Windows are
        returned as inclusive ``(bar_lo, bar_hi)`` ranges so that
        :func:`select_sequential` can be applied to train and test alike without
        re-simulating anything.
        """
        kf = PurgedKFold(n_splits=self.folds, embargo_frac=self.embargo_frac)
        bars = list(order)
        splits = kf.split(n_candidates)
        out: List[Tuple[List[Tuple[int, int]], Tuple[int, int]]] = []
        for tr_pos, te_pos in splits:
            if len(te_pos) == 0 or len(tr_pos) == 0:
                continue
            te_bars = [bars[p] for p in te_pos]
            test_window = (int(min(te_bars)), int(max(te_bars)))
            # Train = the complement of the test block, expressed as up to two
            # contiguous bar ranges (before and after the test block).
            tr_bars = sorted(bars[p] for p in tr_pos)
            train_windows: List[Tuple[int, int]] = []
            run_lo = run_hi = tr_bars[0]
            for b in tr_bars[1:]:
                if b == run_hi + 1:
                    run_hi = b
                else:
                    train_windows.append((run_lo, run_hi))
                    run_lo = run_hi = b
            train_windows.append((run_lo, run_hi))
            out.append((train_windows, test_window))
        return out

    @staticmethod
    def _select_windows(
        outcomes: Sequence[TradeOutcome],
        windows: Iterable[Tuple[int, int]],
        min_score: float,
    ) -> List[TradeOutcome]:
        picked: List[TradeOutcome] = []
        for lo, hi in windows:
            picked.extend(select_sequential(outcomes, min_score=min_score, entry_lo=lo, entry_hi=hi))
        return sorted(picked, key=lambda o: o.entry_idx)

    def _grid_for(self, candidates: pd.DataFrame, score_col: str) -> List[Tuple[Geometry, float]]:
        """Cross the geometry grid with candidate score thresholds."""
        grid = default_geometry_grid(coarse=self.coarse_grid)
        scores = pd.to_numeric(candidates[score_col], errors="coerce").dropna()
        if len(scores) == 0:
            thresholds = [0.0]
        else:
            thresholds = sorted({round(float(scores.quantile(q)), 6) for q in self.score_quantiles})
        return [(g, t) for g in grid for t in thresholds]

    # ── main ───────────────────────────────────────────────────────────────
    def calibrate_symbol(
        self,
        *,
        df: pd.DataFrame,
        candidates: pd.DataFrame,
        symbol: str,
        score_col: str = "score",
    ) -> WRTargetProfile:
        spec = resolve_symbol(symbol)
        money_per_unit = get_dollar_risk_per_price_unit(symbol)
        cost = self._cost_price_equiv(symbol, money_per_unit)

        if candidates is None or len(candidates) == 0:
            return WRTargetProfile(
                symbol=symbol, target_wr=self.target_wr,
                geometry=Geometry(tp_r=0.5, be_trigger_r=None, fast_cash_r=None, max_bars=48),
                score_col=score_col, binding_constraint="no candidates produced",
                notes="Scanner produced no directional candidates for this symbol.",
            )

        cands = candidates.sort_values("bar_idx").reset_index(drop=True)
        if score_col not in cands.columns:
            score_col = "score"
        bars_order = [int(b) for b in cands["bar_idx"].tolist()]

        # ── Phase 1: simulate every geometry once (the expensive step) ──────
        grid = default_geometry_grid(coarse=self.coarse_grid)
        sim_cache: Dict[str, List[TradeOutcome]] = {}
        for g in grid:
            sim_cache[g.key()] = simulate_all_candidates(
                df=df, candidates=cands, geom=g, money_per_unit=money_per_unit,
                cost_price_equiv=cost, spec=spec, symbol=symbol, score_col=score_col,
            )

        combos = self._grid_for(cands, score_col)
        fold_windows = self._fold_windows(len(cands), bars_order)

        # ── Phase 2: walk-forward selection ────────────────────────────────
        # The configuration is chosen ONLY on training folds. Selecting it on
        # the full sample and then reporting fold results as "out-of-sample"
        # would be selection leakage: the config would already have seen the
        # test bars. Instead each fold votes, and the deployed config is the
        # one the folds agree on most often (a stability vote). Ties are broken
        # on the AVERAGE TRAIN summary, never on test.
        per_fold_choice: List[Tuple[Geometry, float]] = []
        # Per-configuration, per-fold TRAIN summaries, used to break ties between
        # configurations the folds already voted for. Keyed by (geometry, threshold)
        # so each configuration is averaged over its OWN train results.
        train_summaries: Dict[Tuple[str, float], List[Dict[str, float]]] = {}
        oos_outcomes: List[TradeOutcome] = []
        # Trades the fold-selected configuration would have taken *inside its
        # training windows*. The regime policy is fitted on these, so it never
        # sees the out-of-sample bars it is about to filter.
        train_selected: List[TradeOutcome] = []
        # Per-fold plan (cache key, threshold, test window) so the out-of-sample
        # selection can be replayed *after* the regime policy is learned. The
        # engine applies that policy, so reporting pre-policy numbers here would
        # describe a system that is not the one being shipped.
        fold_plans: List[Tuple[str, float, int, int]] = []

        for train_windows, test_window in fold_windows:
            f_key: Optional[Tuple] = None
            f_choice: Optional[Tuple[Geometry, float]] = None
            for g, thr in combos:
                s = summarise(self._select_windows(sim_cache[g.key()], train_windows, thr))
                train_summaries.setdefault((g.key(), thr), []).append(s)
                key = _rank_key(s, self.target_wr, self.min_trades)
                if f_key is None or key > f_key:
                    f_key, f_choice = key, (g, thr)
            if f_choice is None:
                continue
            per_fold_choice.append(f_choice)
            g, thr = f_choice
            te_lo, te_hi = test_window
            fold_plans.append((g.key(), float(thr), int(te_lo), int(te_hi)))
            for tr_lo, tr_hi in train_windows:
                train_selected.extend(
                    select_sequential(
                        sim_cache[g.key()], min_score=thr, entry_lo=tr_lo, entry_hi=tr_hi
                    )
                )
            oos_outcomes.extend(
                select_sequential(sim_cache[g.key()], min_score=thr, entry_lo=te_lo, entry_hi=te_hi)
            )

        best: Optional[Tuple[Geometry, float, Dict[str, float]]] = None
        if per_fold_choice:
            votes: Dict[Tuple[str, float], int] = {}
            for g, thr in per_fold_choice:
                votes[(g.key(), thr)] = votes.get((g.key(), thr), 0) + 1
            top_votes = max(votes.values())
            tied = [k for k, c in votes.items() if c == top_votes]

            best_key: Optional[Tuple] = None
            for gkey, thr in tied:
                g = next(
                    gg for gg, tt in per_fold_choice
                    if gg.key() == gkey and abs(tt - thr) < 1e-12
                )
                # Tie-break on THIS configuration's mean TRAIN summary across
                # folds: honest, and still never touches the test windows.
                means = _mean_summary(train_summaries.get((gkey, thr), []))
                key = _rank_key(means, self.target_wr, self.min_trades)
                full_s = summarise(select_sequential(sim_cache[g.key()], min_score=thr))
                if best_key is None or key > best_key:
                    best_key, best = key, (g, thr, full_s)

        if best is None:
            # No usable folds (very short sample): fall back to full-sample
            # selection, which the report will label as in-sample.
            best_key = None
            for g, thr in combos:
                s = summarise(select_sequential(sim_cache[g.key()], min_score=thr))
                key = _rank_key(s, self.target_wr, self.min_trades)
                if best_key is None or key > best_key:
                    best_key, best = key, (g, thr, s)

        if best is None:
            return WRTargetProfile(
                symbol=symbol, target_wr=self.target_wr,
                geometry=Geometry(tp_r=0.5, be_trigger_r=None, fast_cash_r=None, max_bars=24),
                score_col=score_col, binding_constraint="no viable geometry",
                notes="Every geometry had negative expectancy or too few trades.",
            )

        best_geom, best_thr, best_stats = best
        # The simulation cache is keyed on the geometry WITHOUT the threshold
        # (threshold only filters candidates, it does not change any path). Keep
        # that key before baking the threshold into the deployed geometry, since
        # ``Geometry.key()`` includes min_score and would otherwise miss.
        best_cache_key = best_geom.key()
        best_geom = Geometry(**{**best_geom.to_dict(), "min_score": float(best_thr)})
        oos_stats = summarise(oos_outcomes)
        pre_policy_oos = dict(oos_stats)

        # ── Phase 4: AI artefacts ──────────────────────────────────────────
        full_outs = select_sequential(sim_cache[best_cache_key], min_score=best_thr)
        # The score calibration is fitted on the out-of-sample trades: it is a
        # monotone rescaling of the score, and the deployed distribution is the
        # one that matters. Fall back to the full sample only when OOS coverage
        # is too thin to bin.
        calib_source = oos_outcomes if len(oos_outcomes) >= 10 else full_outs
        score_cal = isotonic_calibrate(
            [o.ai_score for o in calib_source], [1 if o.is_win else 0 for o in calib_source]
        )
        # The regime policy, by contrast, makes a hard include/exclude decision,
        # so it is fitted on TRAIN-selected trades only. Fitting it on the same
        # out-of-sample trades it then filters would let it delete exactly the
        # losers it has already seen — a filter tuned to the test set. Measured
        # on this data that inflated the aggregate out-of-sample result from
        # roughly break-even to +52R, which is the size of the artefact.
        policy_source = train_selected if len(train_selected) >= self.min_trades else calib_source
        policy_fitted_on = "train folds" if policy_source is train_selected else "full sample (thin train pool)"
        edge = regime_edge_table(policy_source, min_trades=max(3, self.min_trades // 3)) \
            if self.enable_regime_policy else {}

        # ── Apply the learned policy before reporting ──────────────────────
        # The regime policy is fitted on the OOS sample, but it is the ENGINE
        # that has to live with it: at run time ``entry_policy`` refuses any
        # candidate whose regime the table disables. Reporting the pre-policy
        # sample here would describe a system that is not the one being shipped
        # (EURUSD read 192 OOS trades at 70.3% while the engine produced 7).
        # Replaying the identical per-fold selection with the disabled regimes
        # removed — before the one-position-at-a-time walk, so a blocked trade
        # frees its slot — makes the reported numbers the engine's numbers.
        enabled_regimes: Optional[Set[str]] = None
        if edge:
            enabled_regimes = {r for r, e in edge.items() if e.enabled}
            replayed: List[TradeOutcome] = []
            for gkey, thr, lo, hi in fold_plans:
                replayed.extend(
                    select_sequential(
                        sim_cache[gkey], min_score=thr,
                        entry_lo=lo, entry_hi=hi, regimes=enabled_regimes,
                    )
                )
            oos_outcomes = replayed
            full_outs = select_sequential(
                sim_cache[best_cache_key], min_score=best_thr, regimes=enabled_regimes
            )
            best_stats = summarise(full_outs)
            oos_stats = summarise(oos_outcomes)

        # Regime-conditional geometry: refit the target distance inside each
        # regime that has enough trades to justify its own answer.
        regime_geom: Dict[str, Geometry] = {}
        if self.regime_geometry:
            by_regime: Dict[str, List[TradeOutcome]] = {}
            for o in calib_source:
                by_regime.setdefault(str(o.regime), []).append(o)
            for regime, outs in by_regime.items():
                if len(outs) < max(6, self.min_trades // 2):
                    continue
                idx_set = {o.entry_idx for o in outs}
                r_key: Optional[Tuple] = None
                r_best: Optional[Geometry] = None
                for g, thr in combos:
                    sub = [o for o in sim_cache[g.key()] if o.entry_idx in idx_set]
                    picked = select_sequential(sub, min_score=thr)
                    s = summarise(picked)
                    key = _rank_key(s, self.target_wr, max(4, self.min_trades // 2))
                    if r_key is None or key > r_key:
                        r_key, r_best = key, g
                if r_best is not None:
                    regime_geom[regime] = Geometry(**{**r_best.to_dict(), "min_score": float(best_thr)})

        # ── Frontier diagnostic ────────────────────────────────────────────
        # For each target size, the best win rate found and the best win rate
        # that still carries positive expectancy. This is what turns "we did not
        # reach 75%" into "75% is or is not reachable, and here is the cost".
        frontier = self._compute_frontier(sim_cache, combos, regimes=enabled_regimes)

        # ── Binding constraint diagnosis ───────────────────────────────────
        # ``target_met`` refers to the DEPLOYED configuration's in-sample fit.
        # Whether the target is reachable at all is a separate question, so the
        # best in-sample win rate that still carries positive expectancy is
        # computed explicitly. Without that, a symbol whose target is reachable
        # but not selected would be mislabelled as "unreachable".
        target_met = bool(best_stats["win_rate"] >= self.target_wr and best_stats["expectancy_r"] > 0)
        best_wr = 0.0
        best_wr_key = None
        for g, thr in combos:
            s = summarise(
                select_sequential(sim_cache[g.key()], min_score=thr, regimes=enabled_regimes)
            )
            if s["trades"] >= self.min_trades and s["expectancy_r"] > 0 and s["win_rate"] > best_wr:
                best_wr, best_wr_key = s["win_rate"], f"{g.key()}@thr{thr:.3f}"

        oos_met = bool(oos_stats["win_rate"] >= self.target_wr and oos_stats["expectancy_r"] > 0)

        if target_met and oos_met:
            binding = (
                "none - target met in-sample AND out-of-sample "
                f"(OOS {oos_stats['win_rate']*100:.1f}% on {oos_stats['trades']} trades)"
            )
        elif target_met and not oos_met:
            # The most important case to label honestly: the target was reachable
            # only in-sample.
            if oos_stats["expectancy_r"] <= 0:
                cause = f"OOS expectancy is non-positive ({oos_stats['expectancy_r']:+.3f}R)"
            else:
                cause = f"OOS win rate falls to {oos_stats['win_rate']*100:.1f}%"
            binding = (
                f"out-of-sample - met in-sample ({best_stats['win_rate']*100:.1f}%) "
                f"but not on purged folds; {cause}"
            )
        elif best_stats["trades"] < self.min_trades:
            disabled = sorted(r for r, e in edge.items() if not e.enabled)
            cause = (
                f"; the regime policy removed {', '.join(disabled)}"
                if disabled else ""
            )
            binding = (
                f"sample size - only {best_stats['trades']} trades in the sample "
                f"({self.min_trades} required to assert a rate){cause}"
            )
        elif best_stats["expectancy_r"] <= 0:
            binding = "expectancy - the walk-forward-selected configuration has no edge out of sample"
        elif best_wr >= self.target_wr:
            binding = (
                f"selection stability - {best_wr*100:.1f}% is reachable in-sample "
                f"({best_wr_key}) but walk-forward selection did not converge on it, "
                f"so the deployed configuration achieves only {best_stats['win_rate']*100:.1f}%"
            )
        else:
            binding = (
                f"win rate - best achievable in-sample with positive expectancy is "
                f"{best_wr*100:.1f}% ({best_wr_key}), below the {self.target_wr*100:.0f}% target"
            )

        oos_verdict = (
            f"OOS {oos_stats['win_rate']*100:.1f}% on {oos_stats['trades']} trades "
            f"({oos_stats['expectancy_r']:+.3f}R, PF {oos_stats['profit_factor']:.2f})"
        )
        notes = (
            f"Deployed {best_geom.key()} chosen by walk-forward vote across "
            f"{len(fold_windows)} purged folds; {oos_verdict}. "
            f"In-sample reference: {best_stats['trades']} trades, "
            f"{best_stats['win_rate']*100:.1f}% WR, {best_stats['expectancy_r']:+.3f}R."
        )
        if enabled_regimes is not None:
            disabled = sorted(r for r, e in edge.items() if not e.enabled)
            notes += (
                f" Regime policy enabled {len(enabled_regimes)}/{len(edge)} regimes"
                + (f" (disabled: {', '.join(disabled)})" if disabled else "")
                + f", fitted on {policy_fitted_on}; all reported figures already "
                  "exclude disabled regimes."
            )

        return WRTargetProfile(
            symbol=symbol,
            target_wr=self.target_wr,
            geometry=best_geom,
            score_col=score_col,
            n_trades=int(best_stats["trades"]),
            win_rate=float(best_stats["win_rate"]),
            expectancy_r=float(best_stats["expectancy_r"]),
            payoff=float(best_stats["payoff"]),
            profit_factor=float(best_stats["profit_factor"]),
            total_r=float(best_stats["total_r"]),
            max_dd_r=float(best_stats["max_dd_r"]),
            target_met=target_met,
            binding_constraint=binding,
            oos_trades=int(oos_stats["trades"]),
            oos_win_rate=float(oos_stats["win_rate"]),
            oos_expectancy_r=float(oos_stats["expectancy_r"]),
            oos_profit_factor=float(oos_stats["profit_factor"]),
            oos_total_r=float(oos_stats["total_r"]),
            oos_target_met=bool(oos_stats["win_rate"] >= self.target_wr and oos_stats["expectancy_r"] > 0),
            folds=len(fold_windows),
            oos_pre_policy_trades=int(pre_policy_oos["trades"]),
            oos_pre_policy_win_rate=float(pre_policy_oos["win_rate"]),
            oos_pre_policy_expectancy_r=float(pre_policy_oos["expectancy_r"]),
            policy_fitted_on=policy_fitted_on,
            score_calibration=score_cal,
            regime_edge=edge,
            regime_geometry=regime_geom,
            frontier=frontier,
            notes=notes,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Persistence / self-learning
# ─────────────────────────────────────────────────────────────────────────────
class WRProfileStore:
    """JSON-backed store for calibrated profiles."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> Dict[str, WRTargetProfile]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error(f"Could not read profile store {self.path}: {exc}")
            return {}
        profiles = raw.get("profiles", raw)
        return {k: WRTargetProfile.from_dict(v) for k, v in profiles.items()}

    def save(self, profiles: Dict[str, WRTargetProfile], meta: Optional[Dict[str, Any]] = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "meta": meta or {},
            "profiles": {k: p.to_dict() for k, p in profiles.items()},
        }
        self.path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def merge_realised(
        self,
        symbol: str,
        outcomes: Sequence[TradeOutcome],
        *,
        min_new: int = 20,
    ) -> Optional[WRTargetProfile]:
        """Refit a symbol's score calibration and regime table from live results.

        This is the self-learning hook: the geometry (chosen on a long history)
        is held fixed, while the calibration and the regime enable/disable
        decisions are refreshed from realised trades. Only the calibration moves,
        so a short run of live results cannot silently rewrite the strategy.
        """
        profiles = self.load()
        profile = profiles.get(symbol)
        if profile is None or len(outcomes) < min_new:
            return profile

        profile.score_calibration = isotonic_calibrate(
            [o.ai_score for o in outcomes], [1 if o.is_win else 0 for o in outcomes]
        )
        profile.regime_edge = regime_edge_table(outcomes)
        profile.notes = (profile.notes + " | refit from realised outcomes").strip(" |")
        profiles[symbol] = profile
        self.save(profiles)
        return profile


__all__ = [
    "WRTargetProfile",
    "WRProfileStore",
    "ScoreCalibration",
    "RegimeEdge",
    "FrontierPoint",
    "WRTargetCalibrator",
    "isotonic_calibrate",
    "regime_edge_table",
    "default_geometry_grid",
]

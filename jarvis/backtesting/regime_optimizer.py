"""
JARVIS AI 5.2 — Regime-Conditioned Profit Optimiser ("max profit in any condition").

WHY THIS MODULE EXISTS
----------------------
``optimizer.py`` answers "which geometry makes the most money?" with exactly ONE
answer per trading mode. That answer is an average over whatever market
conditions the six-month window happened to contain — and an average is the wrong
object when those conditions are not exchangeable.

The cached universe makes the point concretely. SWING candidates break down as:

    TREND_BULL       43.1%      COMPRESSION       2.6%
    TREND_BEAR       39.0%      LIQUIDITY_SWEEP   2.4%
    BREAKOUT         12.2%      LOW_VOLATILITY    0.6%

A geometry that is excellent in a trend and ruinous in compression can therefore
win the pooled search on trend mass alone, and the compression trades — the ones
that actually cut the equity curve — are never examined on their own terms. The
pooled optimum is then not "the best setting"; it is "the setting that best
exploits the regime mix this particular window happened to have", which is a
different and much weaker claim.

This module searches *inside* each regime, so the output is "what to do when the
market is doing this" rather than "what to do on average".

THE FOUR IDEAS THAT MAKE IT AFFORDABLE AND HONEST
-------------------------------------------------
1. **Simulate once, condition for free.**
   ``BacktestOptimizer._outcomes`` caches a simulation on the geometry alone,
   because a trade's result depends on the forward path and the exit schedule and
   never on which market condition we later label it with. Regime conditioning is
   therefore a *filtering* pass over an outcome list that has already been paid
   for. Scoring one geometry under six regimes costs one simulation and six cheap
   passes, not six simulations. This is the same principle that makes the
   ``geometries x thresholds`` grid affordable in the parent module, applied to a
   third axis.

2. **Selectivity is measured inside the regime, not pooled.**
   ``min_score`` is searched as a quantile of the symbol's own score
   distribution. The scores a symbol produces in COMPRESSION are systematically
   different from those it produces in TREND_BULL, so the 90th percentile of the
   pooled distribution can sit near the median of the compression subset.
   Applying a pooled threshold inside a regime filter would quietly accept most
   of that regime's setups while still reporting a "top 10%" run. The quantile is
   therefore resolved against the regime's own scores.

3. **One position at a time, across regimes.**
   The engine holds at most one position per symbol whatever the regime is.
   Selecting each regime independently would book overlapping trades the engine
   can never take, and the overlap concentrates exactly where regimes flip — at
   the turns, which is where the P&L is. The merged, regime-eligible candidate
   list is therefore put through ONE blocking walk (``select_sequential``), so the
   reported trade count is one the engine can actually produce.

4. **"Not enough data" is a result, not a failure.**
   A regime with too few candidates, or too few trades after blocking, is
   reported as ``insufficient_sample`` with its counts. It is never given a
   geometry fitted to a handful of bars. Likewise a regime whose best geometry
   cannot clear the objective's constraints reports ``feasible: false`` — the
   ``-inf`` semantics of ``objective_value`` — rather than a flattering number.
   Given that every mode currently shows negative pooled expectancy, an honest
   "no regime supports an edge" outcome is a likely and acceptable answer.

RELATIONSHIP TO THE WIN-RATE CALIBRATOR
---------------------------------------
``jarvis.intelligence.winrate_targeting`` already contains a regime policy
(``regime_edge_table``) and regime-conditional geometry, fitted against a
**win-rate** objective. This module asks the same question against a **profit**
objective, and re-implements the disable decision rather than importing it, for
one structural reason: ``jarvis.intelligence`` already imports
``jarvis.backtesting.trade_simulator``, so importing back into ``intelligence``
would make the two packages mutually dependent. ``docs/ARCHITECTURE.md`` §2
requires the dependency direction to be strictly downward; a peer-to-peer cycle
is not.

The thresholds are therefore deliberately identical to that module's defaults
(``disable_margin_r=0.05``, ``min_trades_to_disable=12``) and are stated as named
constants here, so a reader comparing the two can see they agree. **If one is
changed, change both.**

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not touch the live path. It reads the cached candidate tables and the
real price history, searches, and writes a report. Deploying the winner goes
through ``jarvis.backtesting.exit_geometry.build_exit_geometry`` so that live and
backtest share one exit schedule.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from jarvis.backtesting.optimizer import (
    PRIMARY_TIMEFRAME,
    STYLES,
    BacktestOptimizer,
    OptimizerSpec,
    SeriesBundle,
    objective_value,
)
from jarvis.backtesting.trade_simulator import (
    Geometry,
    TradeOutcome,
    select_sequential,
    summarise,
)

logger = logging.getLogger("JARVIS_RegimeOptimizer")

__all__ = [
    "REGIME_MIN_CANDIDATES",
    "REGIME_MIN_TRADES",
    "DISABLE_MARGIN_R",
    "UNLABELLED",
    "RegimeVerdict",
    "RegimeConditionedOptimizer",
    "regime_decision",
    "optimise_regime_conditioned",
]

# ─────────────────────────────────────────────────────────────────────────────
# Gates. See the module docstring: these three must stay in step with
# jarvis.intelligence.winrate_targeting.regime_edge_table's defaults.
# ─────────────────────────────────────────────────────────────────────────────
# Minimum candidate mass, pooled across the mode's universe, before a regime is
# searched on its own. Below this the descent would be fitting a handful of bars.
REGIME_MIN_CANDIDATES = 200

# Minimum SELECTED trades, after one-position-at-a-time blocking, before a
# regime's result is treated as evidence. Candidate mass and trade count are
# different things: a large pile of candidates concentrated on a few bars yields
# very few trades once the engine refuses to hold two positions at once.
REGIME_MIN_TRADES = 12

# A regime is switched off only when it has enough samples AND an expectancy
# clearly worse than this. The margin matters: disabling on a marginal negative
# expectancy is noise-fitting, not risk control. Mirrors
# ``regime_edge_table(disable_margin_r=0.05)``.
DISABLE_MARGIN_R = 0.05

# Regimes the pipeline could not label. Kept as an explicit bucket so that
# unlabelled candidates are visible in the report instead of silently dropped.
UNLABELLED = "UNKNOWN"


# ─────────────────────────────────────────────────────────────────────────────
# The disable decision
# ─────────────────────────────────────────────────────────────────────────────
def regime_decision(
    trades: int,
    expectancy_r: float,
    *,
    min_trades: int = REGIME_MIN_TRADES,
    disable_margin_r: float = DISABLE_MARGIN_R,
) -> Tuple[bool, str]:
    """Should this regime be traded? Returns ``(enabled, reason)``.

    Deliberately conservative, and deliberately *asymmetric*: disabling can only
    remove trades, never add risk, but removing trades for no reason is not
    conservative either — it is just a smaller sample. So a regime is switched
    off only when it is both well sampled and clearly unprofitable.

    Mirrors ``jarvis.intelligence.winrate_targeting.regime_edge_table``. See the
    module docstring for why it is re-implemented rather than imported.
    """
    trades = int(trades or 0)
    exp = float(expectancy_r or 0.0)
    margin = abs(float(disable_margin_r))

    if trades < int(min_trades):
        return True, (
            f"insufficient samples ({trades} < {int(min_trades)}) — left enabled, "
            f"no basis to disable"
        )
    if exp <= -margin:
        return False, (
            f"expectancy {exp:+.3f}R worse than -{margin:.3f}R over {trades} trades"
        )
    return True, f"expectancy within tolerance ({exp:+.3f}R over {trades} trades)"


# ─────────────────────────────────────────────────────────────────────────────
# One regime's verdict
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class RegimeVerdict:
    """Everything decided about one (mode, regime) pair.

    The two questions are kept strictly separate, because conflating them is how
    a regime-conditioned optimiser ships a curve-fit:

      * **May this regime be traded at all?** — ``enabled``. Answered from the
        pooled geometry's performance restricted to this regime, and only ever
        answered "no" on a well-sampled, clearly negative expectancy.
      * **Does this regime deserve its OWN geometry?** — ``geometry_basis``.
        Answered only by beating the pooled geometry *on this regime*, measured
        on the full window under the same constraints. A regime that loses this
        comparison still trades; it just trades the pooled geometry.
    """

    regime: str
    status: str  # "searched" | "insufficient_sample"
    candidates: int

    # The pooled geometry, RESTRICTED to this regime. The neutral baseline every
    # regime is judged against. Free to compute: the baseline simulation is
    # already in the cache.
    baseline_within_regime: Dict[str, Any] = field(default_factory=dict)

    # The regime-specific search, when one was run.
    searched_geometry: Optional[Geometry] = None
    searched_quantile: Optional[float] = None
    searched_in_sample: Dict[str, Any] = field(default_factory=dict)
    searched_out_of_sample: Dict[str, Any] = field(default_factory=dict)
    searched_full_window: Dict[str, Any] = field(default_factory=dict)
    walk_forward: Dict[str, Any] = field(default_factory=dict)
    validation_folds: List[Dict[str, Any]] = field(default_factory=list)
    feasible: bool = False

    # What the policy actually deploys.
    deployed_geometry: Optional[Geometry] = None
    deployed_quantile: Optional[float] = None
    geometry_basis: str = ""

    enabled: bool = True
    reason: str = ""
    evaluations: int = 0

    @property
    def uses_own_geometry(self) -> bool:
        return self.geometry_basis.startswith("regime geometry")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime": self.regime,
            "status": self.status,
            "candidates": self.candidates,
            "baseline_within_regime": self.baseline_within_regime,
            "searched_geometry": (
                self.searched_geometry.to_dict() if self.searched_geometry else None
            ),
            "searched_geometry_key": (
                self.searched_geometry.key() if self.searched_geometry else None
            ),
            "searched_quantile": self.searched_quantile,
            "searched_in_sample": self.searched_in_sample,
            "searched_out_of_sample": self.searched_out_of_sample,
            "searched_full_window": self.searched_full_window,
            "walk_forward": self.walk_forward,
            "validation_folds": self.validation_folds,
            "feasible": self.feasible,
            "deployed_geometry_key": (
                self.deployed_geometry.key() if self.deployed_geometry else None
            ),
            "deployed_quantile": self.deployed_quantile,
            "uses_own_geometry": self.uses_own_geometry,
            "geometry_basis": self.geometry_basis,
            "enabled": self.enabled,
            "reason": self.reason,
            "evaluations": self.evaluations,
        }


# ─────────────────────────────────────────────────────────────────────────────
# The optimiser
# ─────────────────────────────────────────────────────────────────────────────
class RegimeConditionedOptimizer(BacktestOptimizer):
    """Coordinate-descent search run separately inside each market condition.

    Inherits the simulation cache, the geometry space, the constraints and the
    out-of-sample discipline from :class:`BacktestOptimizer`; adds a regime axis
    to the search and a deployable policy table to the output.
    """

    def __init__(
        self,
        spec: OptimizerSpec,
        *,
        space: Optional[Any] = None,
        progress: Optional[Callable[[str], None]] = None,
        min_candidates: int = REGIME_MIN_CANDIDATES,
        min_trades: int = REGIME_MIN_TRADES,
        disable_margin_r: float = DISABLE_MARGIN_R,
    ):
        super().__init__(spec, space=space, progress=progress)
        self.min_candidates = int(min_candidates)
        self.regime_min_trades = int(min_trades)
        self.disable_margin_r = float(disable_margin_r)

    # ── regime mass ─────────────────────────────────────────────────────────
    def _regime_mass(self, bundles: Sequence[SeriesBundle]) -> Dict[str, int]:
        """Pooled candidate count per regime label across the mode's universe."""
        total: Dict[str, int] = {}
        for b in bundles:
            for regime, count in b.regime_counts().items():
                total[regime] = total.get(regime, 0) + int(count)
        return dict(sorted(total.items(), key=lambda kv: kv[1], reverse=True))

    # ── the regime-aware aggregate ──────────────────────────────────────────
    def _policy_outcomes(
        self,
        bundles: Sequence[SeriesBundle],
        plan: Dict[str, RegimeVerdict],
        *,
        entry_lo: Optional[int],
        entry_hi: Optional[int],
    ) -> List[TradeOutcome]:
        """The trades the engine would actually take under a regime policy.

        Each regime contributes the candidates it permits, scored against ITS OWN
        geometry and ITS OWN selectivity threshold. Those per-regime candidate
        sets are then merged and put through a single blocking walk.

        The single walk is the whole point. Applying ``select_sequential`` inside
        each regime separately would let two regimes hold overlapping positions on
        the same symbol, which the engine cannot do — and because regime changes
        cluster at turns, the phantom overlap would land precisely on the bars
        that matter most to the result.
        """
        merged: List[TradeOutcome] = []
        for b in bundles:
            for regime, verdict in plan.items():
                if not verdict.enabled or verdict.deployed_geometry is None:
                    continue
                if verdict.deployed_quantile is None:
                    continue
                threshold = b.min_score_for(verdict.deployed_quantile, {regime})
                for o in self._outcomes(b, verdict.deployed_geometry):
                    if str(o.regime) != regime:
                        continue
                    if entry_lo is not None and o.entry_idx < entry_lo:
                        continue
                    if entry_hi is not None and o.entry_idx > entry_hi:
                        continue
                    if o.ai_score < threshold:
                        continue
                    merged.append(o)

        if not merged:
            return []

        # ``min_score=0.0`` and ``regimes=None`` because every filter has already
        # been applied above: this call exists purely for the blocking walk, so
        # that there is exactly one implementation of one-position-at-a-time.
        return select_sequential(merged, min_score=0.0, entry_lo=entry_lo, entry_hi=entry_hi)

    # ── driver ──────────────────────────────────────────────────────────────
    def run(
        self, bundles_by_style: Optional[Dict[str, List[SeriesBundle]]] = None
    ) -> Dict[str, Any]:
        """Search per mode, then per regime; validate and tabulate a policy."""
        t0 = time.time()
        spec = self.spec

        if bundles_by_style is None:
            bundles_by_style = self.load_all()

        report: Dict[str, Any] = {
            "generated_utc": pd.Timestamp.now("UTC").isoformat(),
            "spec": {
                "symbols": list(spec.symbols),
                "modes": list(spec.modes),
                "objective": spec.objective,
                "min_trades": spec.min_trades,
                "max_dd_r": spec.max_dd_r,
                "slippage_pips": spec.slippage_pips,
                "commission_per_lot": spec.commission_per_lot,
                "passes": spec.passes,
                "max_evaluations": spec.max_evaluations,
                "walk_forward_split": spec.walk_forward_split,
                "walk_forward_folds": spec.walk_forward_folds,
                "days": spec.days,
                "label": spec.label,
            },
            "gates": {
                "regime_min_candidates": self.min_candidates,
                "regime_min_trades": self.regime_min_trades,
                "disable_margin_r": self.disable_margin_r,
            },
            "modes": [],
            "series": [],
        }

        for style in spec.modes:
            bundles = bundles_by_style.get(style) or []
            if not bundles:
                self._say(f"  {style}: no series available — skipped")
                report["modes"].append({"style": style, "error": "no series"})
                continue

            for b in bundles:
                report["series"].append(b.summary())

            n_bars = int(np.median([b.n_bars for b in bundles])) if bundles else 0
            split_idx = int(n_bars * spec.walk_forward_split)
            mass = self._regime_mass(bundles)

            self._say(
                f"  {style}: {len(bundles)} series, ~{n_bars} bars, "
                f"search [0,{split_idx}) / validate [{split_idx},{n_bars})"
            )
            self._say(
                "    regime mass: "
                + ", ".join(f"{r}={c}" for r, c in list(mass.items())[:8])
            )

            # ── Baseline: the parent module's single global geometry ────────
            self._reset_budget()
            baseline = self._coordinate_descent(bundles, entry_lo=0, entry_hi=split_idx)
            baseline_oos = self._score(
                baseline.geometry, bundles, entry_lo=split_idx, entry_hi=None
            )
            baseline_verdict = self.walk_forward_verdict(
                baseline.metrics, baseline_oos.metrics
            )
            self._say(
                f"    baseline (pooled): {baseline.geometry.key()} | "
                f"IS exp={baseline_verdict['in_sample_expectancy_r']:+.4f}R "
                f"OOS exp={baseline_verdict['out_of_sample_expectancy_r']:+.4f}R"
            )

            # ── Per-regime evaluation and search ────────────────────────────
            baseline_q = float(baseline.geometry.min_score)

            plan: Dict[str, RegimeVerdict] = {}
            for regime, count in mass.items():
                # Judge EVERY regime against the pooled geometry restricted to
                # it. Free to compute (the baseline simulation is cached) and the
                # only neutral reference for the deploy comparison.
                within = self.evaluate(
                    baseline.geometry, bundles,
                    entry_lo=None, entry_hi=None, count=False, regimes={regime},
                )

                verdict = RegimeVerdict(
                    regime=regime,
                    status="searched",
                    candidates=int(count),
                    baseline_within_regime=within,
                    # Default: inherit the pooled geometry. Only a measured win
                    # replaces it — a regime is never handed a geometry fitted on
                    # too little data just because a search could produce one.
                    deployed_geometry=baseline.geometry,
                    deployed_quantile=baseline_q,
                    geometry_basis="pooled geometry",
                )

                if count < self.min_candidates:
                    verdict.status = "insufficient_sample"
                    verdict.geometry_basis = (
                        f"pooled geometry — only {count} candidates "
                        f"(< {self.min_candidates}), too few to fit a regime geometry"
                    )
                    self._say(
                        f"    {regime}: {count} candidates — inherits the pooled "
                        f"geometry (which returns "
                        f"{float(within.get('expectancy_r', 0) or 0):+.4f}R over "
                        f"{int(within.get('trades', 0) or 0)} trades here)"
                    )
                else:
                    self._reset_budget()
                    self._say(f"    {regime}: searching on {count} candidates")
                    search = self._coordinate_descent(
                        bundles, entry_lo=0, entry_hi=split_idx, regimes={regime}
                    )
                    oos = self._score(
                        search.geometry, bundles,
                        entry_lo=split_idx, entry_hi=None, regimes={regime},
                    )
                    regime_wf = self.walk_forward_verdict(search.metrics, oos.metrics)
                    folds = self._fold_metrics(
                        bundles, search.geometry,
                        entry_lo=split_idx, entry_hi=n_bars,
                        folds=spec.walk_forward_folds, regimes={regime},
                    )
                    full = self.evaluate(
                        search.geometry, bundles,
                        entry_lo=None, entry_hi=None, count=False, regimes={regime},
                    )

                    verdict.searched_geometry = search.geometry
                    verdict.searched_quantile = float(search.geometry.min_score)
                    verdict.searched_in_sample = search.metrics
                    verdict.searched_out_of_sample = oos.metrics
                    verdict.searched_full_window = full
                    verdict.walk_forward = regime_wf
                    verdict.validation_folds = folds
                    verdict.feasible = search.score != float("-inf")
                    verdict.evaluations = self._budget_spent()

                    # ── Deploy only a MEASURED win ──────────────────────────
                    # Both geometries are scored by the same objective, on the
                    # same regime, over the same full window, under the same
                    # constraints. ``objective_value`` returns -inf when a
                    # constraint fails, so a geometry fitted to nine trades
                    # cannot win when ``min_trades`` says thirty — the constraint
                    # guarding the pooled search guards the regime search free.
                    own = objective_value(full, spec)
                    base = objective_value(within, spec)
                    if own > base:
                        verdict.deployed_geometry = search.geometry
                        verdict.deployed_quantile = float(search.geometry.min_score)
                        verdict.geometry_basis = (
                            f"regime geometry beats the pooled geometry on this "
                            f"regime ({own:.4f} > {base:.4f} on {spec.objective})"
                        )
                    elif own == float("-inf"):
                        verdict.geometry_basis = (
                            "pooled geometry — the regime geometry failed the "
                            "constraints on the full window"
                        )
                    else:
                        verdict.geometry_basis = (
                            "pooled geometry — the regime geometry did not beat it "
                            f"({own:.4f} <= {base:.4f} on {spec.objective})"
                        )

                    self._say(
                        f"      -> {search.geometry.key()} | "
                        f"IS exp={regime_wf['in_sample_expectancy_r']:+.4f}R "
                        f"OOS exp={regime_wf['out_of_sample_expectancy_r']:+.4f}R "
                        f"full exp={float(full.get('expectancy_r', 0.0) or 0.0):+.4f}R "
                        f"trades={int(full.get('trades', 0) or 0)} | "
                        f"{'OWN GEOMETRY' if verdict.uses_own_geometry else 'keeps pooled'}"
                    )

                # ── Enable / disable, decided on the geometry we DEPLOY ─────
                # The order matters and is easy to get backwards. Deciding
                # tradeability from the pooled geometry's within-regime numbers
                # would switch a regime off because the *default* settings lose
                # there, even when the geometry fitted for that regime turns it
                # profitable — which is precisely the case regime conditioning
                # exists to find. So the decision is made last, on the metrics the
                # deployed geometry actually produces.
                deploy_metrics = (
                    verdict.searched_full_window
                    if verdict.uses_own_geometry and verdict.searched_full_window
                    else within
                )
                enabled, reason = regime_decision(
                    int(deploy_metrics.get("trades", 0) or 0),
                    float(deploy_metrics.get("expectancy_r", 0.0) or 0.0),
                    min_trades=self.regime_min_trades,
                    disable_margin_r=self.disable_margin_r,
                )
                verdict.enabled = enabled
                verdict.reason = reason
                plan[regime] = verdict

            # ── Measure the policy against the baseline, out-of-sample ──────
            enabled_regimes = {r for r, v in plan.items() if v.enabled}
            policy_oos_outcomes = self._policy_outcomes(
                bundles, plan, entry_lo=split_idx, entry_hi=None
            )
            policy_oos = summarise(policy_oos_outcomes)
            policy_full_outcomes = self._policy_outcomes(
                bundles, plan, entry_lo=None, entry_hi=None
            )
            policy_full = summarise(policy_full_outcomes)

            policy_folds = self._policy_folds(
                bundles, plan, entry_lo=split_idx, entry_hi=n_bars,
                folds=spec.walk_forward_folds,
            )

            comparison = self._compare(baseline_oos.metrics, policy_oos)

            report["modes"].append({
                "style": style,
                "primary_timeframe": PRIMARY_TIMEFRAME.get(style),
                "series_count": len(bundles),
                "bars_median": n_bars,
                "search_window": [0, split_idx],
                "validation_window": [split_idx, n_bars],
                "regime_mass": mass,
                "baseline": {
                    "geometry": baseline.geometry.to_dict(),
                    "geometry_key": baseline.geometry.key(),
                    "best_min_score_quantile": float(baseline.geometry.min_score),
                    "feasible": baseline.score != float("-inf"),
                    "in_sample": baseline.metrics,
                    "out_of_sample": baseline_oos.metrics,
                    "walk_forward": baseline_verdict,
                    "evaluations": self._budget_spent(),
                },
                "regimes": [plan[r].to_dict() for r in mass if r in plan],
                "policy": {
                    "enabled_regimes": sorted(enabled_regimes),
                    "disabled_regimes": sorted(r for r in plan if r not in enabled_regimes),
                    "regimes_searched": sum(
                        1 for v in plan.values() if v.status == "searched"
                    ),
                    "regimes_withheld": sum(
                        1 for v in plan.values() if v.status != "searched"
                    ),
                    # Which regimes actually earned a geometry of their own, as
                    # opposed to inheriting the pooled one. This is the honest
                    # headline number: a policy that deploys no regime geometry
                    # is a plain pooled optimiser with a regime filter, and the
                    # report should say so rather than imply otherwise.
                    "regimes_with_own_geometry": sorted(
                        r for r, v in plan.items() if v.uses_own_geometry
                    ),
                    "regimes_on_pooled_geometry": sorted(
                        r for r, v in plan.items()
                        if not v.uses_own_geometry and v.enabled
                    ),
                    "out_of_sample": policy_oos,
                    "full_window": policy_full,
                    "validation_folds": policy_folds,
                },
                "policy_vs_baseline": comparison,
                "evaluations": self._eval_count,
                "cache_hits": self._cache_hits,
            })

            n_own = sum(1 for v in plan.values() if v.uses_own_geometry)
            self._say(
                f"    policy: {len(enabled_regimes)}/{len(plan)} regimes enabled, "
                f"{n_own} with their own geometry | "
                f"OOS baseline exp={float(baseline_oos.metrics.get('expectancy_r', 0) or 0):+.4f}R "
                f"/ {float(baseline_oos.metrics.get('total_r', 0) or 0):+.1f}R "
                f"({int(baseline_oos.metrics.get('trades', 0) or 0)} trades) -> "
                f"policy exp={float(policy_oos.get('expectancy_r', 0) or 0):+.4f}R "
                f"/ {float(policy_oos.get('total_r', 0) or 0):+.1f}R "
                f"({int(policy_oos.get('trades', 0) or 0)} trades) | "
                f"{comparison['verdict']}"
            )

        report["elapsed_seconds"] = round(time.time() - t0, 1)
        report["evaluations"] = self._eval_count
        report["cache_hits"] = self._cache_hits
        report["cache_hit_rate"] = (
            round(self._cache_hits / (self._cache_hits + self._eval_count), 4)
            if (self._cache_hits + self._eval_count) else 0.0
        )
        return report

    # ── helpers ─────────────────────────────────────────────────────────────
    def _policy_folds(
        self,
        bundles: Sequence[SeriesBundle],
        plan: Dict[str, RegimeVerdict],
        *,
        entry_lo: int,
        entry_hi: int,
        folds: int,
    ) -> List[Dict[str, Any]]:
        """Per-fold stability of the POLICY (not of one geometry) on held-out data."""
        folds = max(1, int(folds))
        span = max(1, entry_hi - entry_lo)
        width = max(1, span // folds)
        out: List[Dict[str, Any]] = []
        for i in range(folds):
            lo = entry_lo + i * width
            hi = entry_hi if i == folds - 1 else min(entry_hi, lo + width)
            m = summarise(self._policy_outcomes(bundles, plan, entry_lo=lo, entry_hi=hi))
            out.append({
                "fold": i + 1,
                "entry_lo": lo,
                "entry_hi": hi,
                "trades": int(m.get("trades", 0) or 0),
                "expectancy_r": round(float(m.get("expectancy_r", 0.0) or 0.0), 4),
                "total_r": round(float(m.get("total_r", 0.0) or 0.0), 3),
                "win_rate": round(float(m.get("win_rate", 0.0) or 0.0), 4),
            })
        return out

    @staticmethod
    def _compare(
        baseline: Dict[str, Any], policy: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Did the regime policy actually improve the held-out result?

        Reported as an explicit verdict rather than a bare number, because
        "policy expectancy is higher" is only a win if it is not achieved by
        taking three trades — and "policy total R is higher" is only a win if it
        is not achieved by taking ten times the exposure.

        The interesting outcome on this data is the third case: the policy
        matches the baseline's total R while taking a fraction of the trades.
        That is a real improvement — the same money for less risk, less cost and
        less capital tied up — and the verdict says so rather than dismissing it
        for failing to raise an already-similar number.
        """
        b_exp = float(baseline.get("expectancy_r", 0.0) or 0.0)
        p_exp = float(policy.get("expectancy_r", 0.0) or 0.0)
        b_trades = int(baseline.get("trades", 0) or 0)
        p_trades = int(policy.get("trades", 0) or 0)
        b_total = float(baseline.get("total_r", 0.0) or 0.0)
        p_total = float(policy.get("total_r", 0.0) or 0.0)

        # How much of the baseline's total R survives, and how much of its trade
        # count was avoided. ``None`` where the baseline has nothing to retain,
        # so a ratio is never fabricated from a zero denominator.
        retention = round(p_total / b_total, 4) if abs(b_total) > 1e-9 else None
        trade_reduction = (
            round(1.0 - (p_trades / b_trades), 4) if b_trades > 0 else None
        )

        if p_trades == 0:
            verdict = "policy takes no trades out-of-sample"
        elif p_total > b_total and p_exp > b_exp:
            verdict = "policy improves both total R and expectancy out-of-sample"
        elif p_total > b_total:
            verdict = "policy improves total R but not expectancy"
        elif p_exp > b_exp and retention is not None and retention >= 0.95:
            verdict = (
                f"policy matches the baseline's total R ({retention:.0%} of it) with "
                f"better expectancy and "
                f"{trade_reduction:.0%} fewer trades"
                if trade_reduction is not None else
                "policy matches total R with better expectancy"
            )
        elif p_exp > b_exp:
            verdict = (
                f"policy improves expectancy but retains only {retention:.0%} of "
                f"total R"
                if retention is not None else
                "policy improves expectancy but not total R"
            )
        else:
            verdict = "policy does not improve the held-out result"

        return {
            "baseline_expectancy_r": round(b_exp, 4),
            "policy_expectancy_r": round(p_exp, 4),
            "baseline_total_r": round(b_total, 3),
            "policy_total_r": round(p_total, 3),
            "baseline_trades": b_trades,
            "policy_trades": p_trades,
            "expectancy_delta_r": round(p_exp - b_exp, 4),
            "total_r_delta": round(p_total - b_total, 3),
            "total_r_retention": retention,
            "trade_reduction": trade_reduction,
            "verdict": verdict,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Convenience entry point
# ─────────────────────────────────────────────────────────────────────────────
def optimise_regime_conditioned(
    *,
    symbols: Sequence[str] = (),
    modes: Sequence[str] = STYLES,
    objective: str = "expectancy_r",
    min_trades: int = 30,
    max_dd_r: float = 40.0,
    slippage_pips: float = 0.5,
    commission_per_lot: float = 5.0,
    passes: int = 3,
    max_evaluations: int = 400,
    walk_forward_split: float = 0.7,
    walk_forward_folds: int = 3,
    days: int = 183,
    label: str = "",
    space: Optional[Any] = None,
    progress: Optional[Callable[[str], None]] = None,
    min_candidates: int = REGIME_MIN_CANDIDATES,
    regime_min_trades: int = REGIME_MIN_TRADES,
    disable_margin_r: float = DISABLE_MARGIN_R,
) -> Dict[str, Any]:
    """Search geometry per market condition and return a deployable policy."""
    spec = OptimizerSpec(
        symbols=tuple(symbols or ()),
        modes=tuple(modes or STYLES),
        objective=objective,
        min_trades=min_trades,
        max_dd_r=max_dd_r,
        slippage_pips=slippage_pips,
        commission_per_lot=commission_per_lot,
        passes=passes,
        max_evaluations=max_evaluations,
        walk_forward_split=walk_forward_split,
        walk_forward_folds=walk_forward_folds,
        days=days,
        label=label,
    )
    opt = RegimeConditionedOptimizer(
        spec,
        space=space,
        progress=progress,
        min_candidates=min_candidates,
        min_trades=regime_min_trades,
        disable_margin_r=disable_margin_r,
    )
    return opt.run()

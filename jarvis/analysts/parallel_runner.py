"""
HM Algo 2.0 — Parallel Analyst Cluster Orchestrator.
Dispatches all specialized analyst agents concurrently via asyncio / ThreadPool with timeout protection.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Tuple

from jarvis.data.schemas import (
    MarketContext, RegimeOutput, AnalystReport, DevilAdvocateReport, AnalystRole,
)

logger = logging.getLogger("JARVIS_AnalystCluster")
from jarvis.analysts.structure_analyst import StructureAnalyst
from jarvis.analysts.momentum_analyst import MomentumAnalyst
from jarvis.analysts.liquidity_analyst import LiquidityAnalyst
from jarvis.analysts.volatility_analyst import VolatilityAnalyst
from jarvis.analysts.macro_analyst import MacroAnalyst
from jarvis.analysts.risk_analyst import RiskAnalyst
from jarvis.analysts.devil_advocate import DevilAdvocateAnalyst

def _analyst_role(role_name: str):
    """The AnalystRole for a role name, falling back to the raw string.

    `AnalystReport.role` is typed `AnalystRole`, but the fallback used to pass
    the plain string. `AnalystRole` is a `str`-Enum so the two compare equal,
    which is why it went unnoticed — but only the enum has `.name`/`.value`.
    """
    try:
        return AnalystRole(role_name)
    except Exception:
        return role_name


class ParallelAnalystCluster:
    """Runs all 7 specialized analyst agents concurrently or sequentially to minimize latency."""
    def __init__(self, timeout_sec: float = 2.0, parallel: bool = True):
        self.timeout_sec = timeout_sec
        self.parallel = parallel
        self.structure_analyst = StructureAnalyst()
        self.momentum_analyst = MomentumAnalyst()
        self.liquidity_analyst = LiquidityAnalyst()
        self.volatility_analyst = VolatilityAnalyst()
        self.macro_analyst = MacroAnalyst()
        self.risk_analyst = RiskAnalyst()
        self.devil_advocate = DevilAdvocateAnalyst()
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="analyst_worker") if parallel else None

    def run_all_parallel(
        self,
        context: MarketContext,
        regime: RegimeOutput,
        tentative_bias: str = "BUY"
    ) -> Tuple[Dict[str, AnalystReport], DevilAdvocateReport]:
        """
        Executes all analysts concurrently in parallel or sequentially.
        Returns a tuple of (Dict of domain AnalystReports, DevilAdvocateReport).
        """
        if not self.parallel or self._executor is None:
            reports = {
                "STRUCTURE": self.structure_analyst.analyze(context, regime),
                "MOMENTUM": self.momentum_analyst.analyze(context, regime),
                "LIQUIDITY": self.liquidity_analyst.analyze(context, regime),
                "VOLATILITY": self.volatility_analyst.analyze(context, regime),
                "MACRO": self.macro_analyst.analyze(context, regime),
                "RISK": self.risk_analyst.analyze(context, regime),
            }
            devil_report = self.devil_advocate.critique_opportunity(context, regime, tentative_bias)
            return reports, devil_report

        futures = {
            "STRUCTURE": self._executor.submit(self.structure_analyst.analyze, context, regime),
            "MOMENTUM": self._executor.submit(self.momentum_analyst.analyze, context, regime),
            "LIQUIDITY": self._executor.submit(self.liquidity_analyst.analyze, context, regime),
            "VOLATILITY": self._executor.submit(self.volatility_analyst.analyze, context, regime),
            "MACRO": self._executor.submit(self.macro_analyst.analyze, context, regime),
            "RISK": self._executor.submit(self.risk_analyst.analyze, context, regime),
        }

        # Devil's Advocate runs against tentative bias
        devil_future = self._executor.submit(self.devil_advocate.critique_opportunity, context, regime, tentative_bias)

        reports: Dict[str, AnalystReport] = {}
        for role_name, fut in futures.items():
            try:
                reports[role_name] = fut.result(timeout=self.timeout_sec)
            except Exception as exc:
                # Instant zero-overhead fallback report if worker times out or
                # errors. WAS SILENT: a dead analyst was indistinguishable from a
                # genuine neutral one, and score=50.0 flows into the same
                # aggregation as a real reading.
                #
                # NOTE this is fail-OPEN by construction and is left that way on
                # purpose: the alternative (fail closed) would block the trade
                # whenever an analyst is merely slow, which is worse than
                # analysing without it. What must not happen is silence — hence
                # the warning, and the "timeout / neutral fallback" entry in
                # `evidence`, which is the only thing downstream can inspect.
                logger.warning("Analyst %s failed or timed out after %.2fs (%s: %s) "
                               "-- substituting a NEUTRAL score-50 fallback.",
                               role_name, self.timeout_sec, type(exc).__name__, exc)
                reports[role_name] = AnalystReport(
                    role=_analyst_role(role_name),
                    symbol=context.symbol,
                    bias="NEUTRAL",
                    score=50.0,
                    # 0.0, not the 0.50 this used to claim. Same reasoning as the
                    # Devil's Advocate fallback below: a fallback must not assert
                    # confidence it does not have. It matters more here than it
                    # looks, because the budget (2.0s) is SMALLER than the socket
                    # timeout of a dependency MACRO calls synchronously (the news
                    # fetch in `jarvis/market/news.py` allows 5s and 6s, and was
                    # measured at 1.32s on a cache miss against a 90s TTL). So the
                    # fallback is not a rare edge case on a cache miss -- it is
                    # the expected outcome whenever the network is merely slow.
                    #
                    # Honest scope of this change: `AnalystReport.confidence` has
                    # no consumer anywhere in `jarvis/` (every `.confidence` read
                    # is `regime.confidence` or `decision.model_confidence`), so
                    # this is a VISIBILITY fix, not a cure. The cure is to stop
                    # the analyst from blocking on the fetch at all -- a
                    # background refresh, or propagating this deadline into
                    # `get_news_calendar`. Deliberately not done here.
                    confidence=0.0,
                    evidence=[f"{role_name} timeout / neutral fallback"],
                    risk_factors=[]
                )

        try:
            devil_report = devil_future.result(timeout=self.timeout_sec)
        except Exception as exc:
            # FAIL-OPEN, and the most consequential fallback in the file.
            #
            # `decision_engine:644` gates on
            #   "Devil Adversarial Guard": penalty_score <= max_devil_penalty  (43.0)
            # so penalty_score=0.0 always PASSES: when the critic times out the
            # adversarial check is not merely weakened, it is removed. Worse,
            # penalty_score is also fed to the ML feature vector (:192) and
            # recorded as `adversarial_penalty` in the scan columns
            # (signal_scan.py:303), where 0.0 is then indistinguishable from "the
            # critic examined this and found nothing wrong".
            #
            # Left fail-open deliberately — blocking every trade on a slow critic
            # is worse than trading uncriticised — but it is now loud, and
            # critique_confidence is 0.0 rather than the default 1.0 so a
            # fallback cannot claim confidence it does not have.
            logger.warning("Devil's Advocate failed or timed out after %.2fs (%s: %s) "
                           "-- substituting penalty 0.0, which PASSES the "
                           "adversarial guard. This trade is uncriticised.",
                           self.timeout_sec, type(exc).__name__, exc)
            devil_report = DevilAdvocateReport(
                symbol=context.symbol,
                counter_bias="NEUTRAL",
                penalty_score=0.0,
                invalidation_risk_coefficient=1.0,
                threats_detected=[],
                invalidation_triggers=[],
                liquidity_traps=[],
                critique_confidence=0.0,
            )

        return reports, devil_report

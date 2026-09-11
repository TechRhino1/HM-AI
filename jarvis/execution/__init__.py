"""Execution and MT5 Gateway package.

Public entry points for the execution layer. Note that
:mod:`jarvis.execution.exit_policy` is the SINGLE SOURCE OF TRUTH for all
stop-loss / partial / trailing arithmetic — both the live monitor and the
backtester call into it, so live and backtest cannot disagree.
"""
from jarvis.execution.mt5_client import MT5Client
from jarvis.execution.state_synchronizer import MT5StateSynchronizer
from jarvis.execution.order_manager import OrderManager
from jarvis.execution.execution_engine import ExecutionEngine
from jarvis.execution.exit_policy import ExitPolicy, ExitDecision, evaluate_exit
from jarvis.execution.position_monitor import PositionMonitorEngine

__all__ = [
    "MT5Client",
    "MT5StateSynchronizer",
    "OrderManager",
    "ExecutionEngine",
    # Canonical exit arithmetic — import these rather than re-implementing.
    "ExitPolicy",
    "ExitDecision",
    "evaluate_exit",
    "PositionMonitorEngine",
]

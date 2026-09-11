"""
JARVIS AI 5.0 — Runtime execution mode.

WHY THIS MODULE EXISTS
----------------------
Several components on the decision path are *stateful against disk*:

  * ``RealtimeOptimizer``      reads recent realised PnL from SQLite and shifts
                               the gate thresholds (min_score / min_rr / win_p).
  * ``OnlineMLPredictor``      loads and SAVES model weights to JSON.
  * ``SelfLearningEngine``     reads/writes pattern statistics in SQLite.
  * ``ConfidenceCalibrationEngine`` and ``MetaLabeler`` load fitted models.

That is correct for live trading — the system is supposed to adapt. It is wrong
for a backtest, and it produced two concrete defects:

  1. **Non-reproducibility.** Running the same backtest twice gave different
     results, because the first run wrote state the second run read.
  2. **Live/history contamination.** A backtest could read the *live* trade
     database and let today's realised results change what the strategy would
     have done in June.

``offline_mode()`` puts the process into a hermetic state: every one of those
components starts from its neutral prior and writes nothing. A backtest then
measures the strategy as specified, deterministically.

Usage::

    from jarvis.config.runtime import offline_mode

    with offline_mode():
        result = engine.run_backtest(...)
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

_LOCK = threading.Lock()
_OFFLINE = False


def is_offline() -> bool:
    """True when persistent reads/writes must be suppressed."""
    return _OFFLINE


def set_offline(enabled: bool) -> None:
    global _OFFLINE
    with _LOCK:
        _OFFLINE = bool(enabled)


@contextmanager
def offline_mode() -> Iterator[None]:
    """Run a block hermetically; restores the previous mode on exit."""
    global _OFFLINE
    with _LOCK:
        previous = _OFFLINE
        _OFFLINE = True
    try:
        yield
    finally:
        with _LOCK:
            _OFFLINE = previous


__all__ = ["is_offline", "set_offline", "offline_mode"]

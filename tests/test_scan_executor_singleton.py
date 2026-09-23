"""``scan_all_modes`` must fan out through one process-wide scan pool.

Why this exists
---------------
Before commit 35f13bf, ``scan_all_modes`` built a fresh
``ThreadPoolExecutor(max(4, min(32, len(tasks))))`` on every call, so every
radar tick paid pool-creation cost and the live loop could not share threads
with the read-only preview endpoint. The fix hoists a module-level
``_executor`` (``max_workers=16``, ``thread_name_prefix="jarvis-scan"``),
registers an ``atexit`` shutdown, and submits to it instead.

This file pins the two halves of that contract without running a real market
scan: the singleton exists with the documented shape, and ``scan_all_modes``
submits to *it* rather than constructing a pool of its own.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from jarvis.application import orchestrator as orch_mod


def _bare_orchestrator():
    """An instance with only the attributes ``scan_all_modes`` reads.

    Avoids the real ``__init__`` (broker client, engines, DBs) — none of which
    the fan-out/arbitration path touches.
    """
    orch = orch_mod.JarvisOrchestrator.__new__(orch_mod.JarvisOrchestrator)
    orch.symbols = ["XAUUSD", "EURUSD"]
    orch.run_cycle_for_symbol = lambda sym, style, dry_run=False: {
        "decision": None, "context": None,
    }
    orch.opportunity_arbiter = MagicMock()
    orch.opportunity_arbiter.rank_and_select_best.return_value = ("BEST", ["ranked"])
    return orch


# ── the singleton ───────────────────────────────────────────────────────────
def test_the_module_exposes_a_shared_scan_pool():
    executor = orch_mod._executor
    assert isinstance(executor, ThreadPoolExecutor)
    assert executor._max_workers == 16


def test_the_scan_pool_is_named_for_the_scan_work():
    """The thread name is how a `py-spy`/`jstack` dump attributes a scan thread."""
    assert orch_mod._executor._thread_name_prefix == "jarvis-scan"


# ── scan_all_modes reuses it ────────────────────────────────────────────────
def test_scan_all_modes_does_not_construct_a_new_executor(monkeypatch):
    """If the code built its own pool, the patched class would raise."""
    def _boom(*args, **kwargs):
        raise AssertionError(
            "scan_all_modes constructed a new ThreadPoolExecutor instead of "
            "using the module-level _executor"
        )

    monkeypatch.setattr(orch_mod, "ThreadPoolExecutor", _boom)
    orch = _bare_orchestrator()

    best, ranked, raw = orch.scan_all_modes(symbols=["XAUUSD", "EURUSD"], styles=["SWING"])

    assert len(raw) == 2                      # one result per (symbol, style)
    assert best == "BEST"
    assert ranked == ["ranked"]


def test_scan_all_modes_submits_to_the_singleton(monkeypatch):
    """Positive proof: the singleton's ``submit`` is what carries the work."""
    calls = []
    real_submit = orch_mod._executor.submit

    def spy_submit(fn, *args, **kwargs):
        calls.append((fn, args))
        return real_submit(fn, *args, **kwargs)

    monkeypatch.setattr(orch_mod._executor, "submit", spy_submit)
    monkeypatch.setattr(
        orch_mod, "ThreadPoolExecutor",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("new pool built")),
    )

    orch = _bare_orchestrator()
    orch.scan_all_modes(symbols=["XAUUSD", "EURUSD"], styles=["SWING", "SCALP"])

    # 2 symbols x 2 styles = 4 tasks, all submitted to the shared pool.
    assert len(calls) == 4
    for fn, args in calls:
        assert fn is orch.run_cycle_for_symbol
        assert len(args) == 3                 # (symbol, style, dry_run)


def test_an_empty_task_list_short_circuits(monkeypatch):
    """No symbols and no configured universe means no submission at all."""
    orch = _bare_orchestrator()
    orch.symbols = []
    best, ranked, raw = orch.scan_all_modes(symbols=[], styles=["SWING"])
    assert (best, ranked, raw) == (None, [], [])

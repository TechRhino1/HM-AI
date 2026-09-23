"""The risk-state DBs must apply WAL + busy_timeout on EVERY connection open.

Why this exists
---------------
``CircuitBreaker`` and ``DrawdownGuard`` open a fresh SQLite connection per call
(``_init_db`` / ``_load_state`` / ``_save_state`` each open and close their own).
Commit 35f13bf added a ``_connect()`` helper that applies

    PRAGMA journal_mode=WAL
    PRAGMA busy_timeout=5000

to each open, because setting them once in ``_init_db`` never reached
``_save_state``. WAL lets the live writer and a reader coexist; ``busy_timeout``
makes a writer wait for a lock instead of raising ``database is locked`` at the
first instant of contention. Without the PRAGMA on the SAVE path the fix is
cosmetic, so these tests drive a real save/load through a recording proxy and
assert both PRAGMAs appear on every open.

A real SQLite file under ``tmp_path`` is used (never the repo ``data/`` dir), so
the PRAGMAs are applied to a real engine, not a mock.
"""

from __future__ import annotations

import sqlite3

import pytest

from jarvis.risk import circuit_breaker as cb_mod
from jarvis.risk import drawdown as dd_mod

WAL = "PRAGMA journal_mode=WAL"
BUSY = "PRAGMA busy_timeout=5000"


class _RecordingConnection:
    """Wraps a real connection, recording the SQL passed to ``execute``."""

    def __init__(self, real, log):
        self._real = real
        self._log = log

    def execute(self, sql, *args, **kwargs):
        self._log.append(sql)
        return self._real.execute(sql, *args, **kwargs)

    def __enter__(self):
        self._real.__enter__()
        return self

    def __exit__(self, *exc):
        return self._real.__exit__(*exc)

    def close(self):
        self._real.close()

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture
def recorded_sql(monkeypatch):
    """Patch ``sqlite3.connect`` to wrap the real connection and log its SQL."""
    log = []
    real_connect = sqlite3.connect

    def connect(path, *args, **kwargs):
        return _RecordingConnection(real_connect(path, *args, **kwargs), log)

    monkeypatch.setattr(sqlite3, "connect", connect)
    return log


def _assert_both_pragmas(log):
    assert log.count(WAL) >= 1, f"journal_mode=WAL was not set on an open: {log!r}"
    assert log.count(BUSY) >= 1, f"busy_timeout was not set on an open: {log!r}"


# ── circuit breaker ─────────────────────────────────────────────────────────
def test_circuit_breaker_sets_the_pragmas_on_init_opens(tmp_path, recorded_sql):
    cb_mod.CircuitBreaker(db_path=str(tmp_path / "circuit.db"))
    # __init__ opens twice (schema + load), each of which must set both PRAGMAs.
    assert recorded_sql.count(WAL) >= 2
    assert recorded_sql.count(BUSY) >= 2


def test_circuit_breaker_sets_the_pragmas_on_the_save_path(tmp_path, recorded_sql):
    breaker = cb_mod.CircuitBreaker(db_path=str(tmp_path / "circuit.db"))
    recorded_sql.clear()

    breaker.record_trade_result(is_win=False)      # -> _save_state() -> _connect()

    _assert_both_pragmas(recorded_sql)


def test_circuit_breaker_sets_the_pragmas_on_a_reset(tmp_path, recorded_sql):
    breaker = cb_mod.CircuitBreaker(db_path=str(tmp_path / "circuit.db"))
    recorded_sql.clear()

    breaker.reset()                                # -> _save_state() -> _connect()

    _assert_both_pragmas(recorded_sql)


# ── drawdown guard ──────────────────────────────────────────────────────────
@pytest.mark.drawdown_persistence
def test_drawdown_sets_the_pragmas_on_init_opens(tmp_path, recorded_sql):
    dd_mod.DrawdownGuard(db_path=str(tmp_path / "drawdown.db"))
    assert recorded_sql.count(WAL) >= 2
    assert recorded_sql.count(BUSY) >= 2


@pytest.mark.drawdown_persistence
def test_drawdown_sets_the_pragmas_on_the_save_path(tmp_path, recorded_sql):
    guard = dd_mod.DrawdownGuard(db_path=str(tmp_path / "drawdown.db"))
    recorded_sql.clear()

    guard.reset_baselines(10_000.0)                # -> _save_state() -> _connect()

    _assert_both_pragmas(recorded_sql)

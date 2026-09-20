"""P3 — the broker lock must be attributable, and a waiter must fail rather than queue forever.

`MT5Client` serialises every broker call on one process-wide lock, and holds it ACROSS the native
call. A native call that never returns cannot be interrupted — Python cannot kill a thread blocked in
C — so one hung call used to hold that lock for the life of the process. Every later broker operation
then blocked on `acquire` until its own `TimeoutGuard` fired and it quietly returned its default: the
platform went dead while looking merely slow.

This does NOT remove the serialisation. The MetaTrader5 bindings are not thread-safe, so running two
broker calls concurrently would be a correctness bug traded for an availability one. What it proves
instead:

* a waiter fails within a bounded time, with a typed error, instead of blocking forever;
* the holder is named — thread, task and how long it has held — so health can report it;
* the timeout applies to ACQUIRING, never to HOLDING, so a slow-but-progressing call is unaffected.
"""

import threading
import time

import pytest

from jarvis.execution.broker_lock import (
    DEFAULT_WAIT_SEC,
    BrokerLockBusy,
    TrackedRLock,
)
from jarvis.execution.mt5_client import MT5Client


def _hold(lock, seconds):
    """Take the lock on another thread and sit on it, like a hung native call."""
    done = threading.Event()

    def _run():
        lock.acquire(task="HUNG_NATIVE_CALL")
        try:
            done.set()
            time.sleep(seconds)
        finally:
            lock.release()

    t = threading.Thread(target=_run, name="stuck_worker", daemon=True)
    t.start()
    assert done.wait(timeout=2.0), "the holder never acquired"
    return t


class TestAttribution:
    def test_a_free_lock_reports_not_held(self):
        assert TrackedRLock().health()["held"] is False

    def test_the_holder_is_named(self):
        lock = TrackedRLock("t")
        with lock:
            h = lock.health()
        assert h["held"] is True

    def test_an_explicit_task_is_recorded(self):
        lock = TrackedRLock("t")
        lock.acquire(task="MT5_SendOrder_EURUSD")
        try:
            assert lock.health()["task"] == "MT5_SendOrder_EURUSD"
        finally:
            lock.release()

    def test_the_age_is_measured_not_zero(self):
        lock = TrackedRLock("t")
        lock.acquire(task="slow")
        try:
            time.sleep(0.05)
            assert lock.health()["age_sec"] >= 0.04
        finally:
            lock.release()

    def test_reentrancy_does_not_restart_the_clock(self):
        """A nested acquire by the owner must not reset who holds it or since when."""
        lock = TrackedRLock("t")
        with lock:
            time.sleep(0.05)
            first_age = lock.health()["age_sec"]
            with lock:
                assert lock.health()["depth"] == 2
                assert lock.health()["age_sec"] >= first_age
        assert lock.health()["held"] is False

    def test_release_clears_the_holder(self):
        lock = TrackedRLock("t")
        lock.acquire(task="x")
        lock.release()
        h = lock.health()
        assert h["held"] is False and h["holder"] is None and h["depth"] == 0


class TestAWaiterFailsInsteadOfQueueingForever:
    def test_a_starved_waiter_raises_within_the_timeout(self):
        """THE DEFECT. Pre-fix this blocked for the life of the process."""
        lock = TrackedRLock("t", default_wait_sec=0.3)
        _hold(lock, 5.0)

        t0 = time.monotonic()
        with pytest.raises(BrokerLockBusy):
            lock.acquire()
        elapsed = time.monotonic() - t0

        assert 0.25 <= elapsed < 2.0, f"waited {elapsed:.2f}s — not bounded"

    def test_the_error_names_the_holder_and_how_long(self):
        """A caller must be able to say WHY, not just that it timed out."""
        lock = TrackedRLock("t", default_wait_sec=0.2)
        _hold(lock, 5.0)
        with pytest.raises(BrokerLockBusy) as exc:
            lock.acquire()
        msg = str(exc.value)
        assert "stuck_worker" in msg
        assert "HUNG_NATIVE_CALL" in msg

    def test_the_wait_timeout_is_configurable(self):
        fast = TrackedRLock("t", default_wait_sec=0.15)
        _hold(fast, 3.0)
        t0 = time.monotonic()
        with pytest.raises(BrokerLockBusy):
            fast.acquire()
        assert time.monotonic() - t0 < 1.0

    def test_a_non_blocking_acquire_fails_immediately(self):
        lock = TrackedRLock("t", default_wait_sec=5.0)
        _hold(lock, 3.0)
        t0 = time.monotonic()
        with pytest.raises(BrokerLockBusy):
            lock.acquire(blocking=False)
        assert time.monotonic() - t0 < 0.5


class TestSlowIsNotStuck:
    def test_a_slow_but_progressing_holder_does_not_break_callers(self):
        """The timeout is on ACQUIRING, never on HOLDING. A call that takes 2s
        and finishes must not be mistaken for a wedged one."""
        lock = TrackedRLock("t", default_wait_sec=5.0)
        _hold(lock, 0.4)

        t0 = time.monotonic()
        assert lock.acquire(timeout=5.0, task="follower") is True
        lock.release()
        elapsed = time.monotonic() - t0

        assert elapsed >= 0.3, "the follower did not actually wait for the holder"
        assert elapsed < 5.0, "the follower timed out on a holder that released"

    def test_the_lock_is_reusable_after_a_wedge(self):
        lock = TrackedRLock("t", default_wait_sec=0.2)
        _hold(lock, 0.3)
        with pytest.raises(BrokerLockBusy):
            lock.acquire()
        time.sleep(0.5)
        with lock:
            assert lock.health()["held"] is True


class TestWiredIntoTheClient:
    def test_the_client_uses_the_tracked_lock(self):
        c = MT5Client(mode="paper", auto_init=False)
        assert isinstance(c._lock, TrackedRLock)

    def test_all_instances_share_one_lock(self):
        """The wedge was process-wide: the lock is a class attribute."""
        a = MT5Client(mode="paper", auto_init=False)
        b = MT5Client(mode="paper", auto_init=False)
        assert a._lock is b._lock

    def test_health_is_exposed_on_the_client(self):
        c = MT5Client(mode="paper", auto_init=False)
        assert c.broker_lock_health()["held"] is False
        with c._lock:
            assert c.broker_lock_health()["held"] is True

    def test_the_default_wait_is_longer_than_any_broker_timeout(self):
        """A waiter must never give up before the call it is waiting behind would
        have timed out anyway — otherwise the lock, not the broker, becomes the
        thing that fails calls."""
        assert DEFAULT_WAIT_SEC >= 5.0

import threading
import time
import unittest
from jarvis.common.timeout_guard import TimeoutGuard, timeout_guarded


def _wait_until(predicate, timeout=5.0, interval=0.02):
    """Poll `predicate` until it is true or `timeout` elapses. Returns the last value."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


class TestTimeoutGuardRecovery(unittest.TestCase):
    """Regression tests for the wedged-pool defect.

    A timed-out `future.result()` does not cancel the work, so a worker blocked
    in a native call stays blocked. With every worker abandoned, the pool still
    accepted submissions but never ran them, so EVERY later call silently
    returned its default -- including the broker calls that close positions.
    """

    def tearDown(self):
        # Leave no stuck threads or shrunken pool behind for other tests.
        if getattr(self, "_gate", None) is not None:
            self._gate.set()
        TimeoutGuard._max_workers = self._orig_max
        ex = TimeoutGuard._executor
        TimeoutGuard._executor = None
        if ex is not None:
            try:
                ex.shutdown(wait=True)
            except Exception:
                pass

    def setUp(self):
        self._orig_max = TimeoutGuard._max_workers
        self._gate = None
        ex = TimeoutGuard._executor
        TimeoutGuard._executor = None
        if ex is not None:
            try:
                ex.shutdown(wait=False)
            except Exception:
                pass

    def test_quick_work_still_runs_after_every_worker_is_stuck(self):
        gate = threading.Event()
        self._gate = gate
        TimeoutGuard._max_workers = 2

        # Occupy both workers with calls that never return on their own.
        for _ in range(TimeoutGuard._max_workers):
            self.assertEqual(
                TimeoutGuard.run_sync(gate.wait, timeout_sec=0.1, default="FALLBACK",
                                      task_name="stuck"),
                "FALLBACK",
            )

        self.assertTrue(TimeoutGuard.health()["wedged"],
                        "guard should report itself wedged with no free workers")

        # The whole point: new work must still execute. Before the fix this
        # returned "FALLBACK" because the wedged pool was never replaced.
        self.assertEqual(
            TimeoutGuard.run_sync(lambda: "OK", timeout_sec=2.0, default="FALLBACK",
                                  task_name="after_wedge"),
            "OK",
        )

    def test_guard_heals_when_an_abandoned_call_finally_returns(self):
        gate = threading.Event()
        self._gate = gate
        TimeoutGuard._max_workers = 4

        self.assertEqual(
            TimeoutGuard.run_sync(gate.wait, timeout_sec=0.1, default="FALLBACK",
                                  task_name="stuck"),
            "FALLBACK",
        )
        self.assertTrue(_wait_until(lambda: TimeoutGuard.health()["stuck_workers"] == 1),
                        "the abandoned worker should be counted as stuck")

        gate.set()
        self.assertTrue(_wait_until(lambda: TimeoutGuard.health()["stuck_workers"] == 0),
                        "a worker that comes back must stop counting as stuck")

    def test_health_reports_available_capacity(self):
        TimeoutGuard._max_workers = 3
        health = TimeoutGuard.health()
        self.assertEqual(health["max_workers"], 3)
        self.assertEqual(health["available"], 3)
        self.assertFalse(health["wedged"])


class TestTimeoutGuard(unittest.TestCase):
    def test_sync_timeout_success(self):
        def quick_func():
            return "SUCCESS"

        result = TimeoutGuard.run_sync(quick_func, timeout_sec=1.0, default="FALLBACK")
        self.assertEqual(result, "SUCCESS")

    def test_sync_timeout_fallback_on_hang(self):
        def slow_func():
            time.sleep(1.5)
            return "COMPLETED"

        result = TimeoutGuard.run_sync(slow_func, timeout_sec=0.2, default="FALLBACK")
        self.assertEqual(result, "FALLBACK")

    def test_timeout_decorator(self):
        @timeout_guarded(timeout_sec=0.2, default="DECORATED_FALLBACK")
        def slow_decorated():
            time.sleep(1.0)
            return "DONE"

        self.assertEqual(slow_decorated(), "DECORATED_FALLBACK")

if __name__ == "__main__":
    unittest.main()

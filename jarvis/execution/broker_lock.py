"""A lock that can say who is holding it, and that fails instead of waiting forever.

P3: `MT5Client` serialises every broker call on one process-wide `RLock`. That
serialisation is deliberate — the MetaTrader5 Python bindings are not thread-safe —
but the lock is held ACROSS the native call, and a native call that never returns
can never be interrupted: Python cannot kill a thread blocked inside C. So one hung
call used to hold the lock for the life of the process, and every later broker
operation blocked on `acquire` until its own timeout fired and it silently returned
its default. The platform went dead while looking merely slow.

This does NOT remove the serialisation — running two broker calls at once would be
a correctness bug, not a fix. What it does is bound the WAIT and attribute the
HOLDER:

* `acquire` takes a timeout, so a waiter fails after `default_wait_sec` instead of
  blocking forever.
* the owner's thread, task and start time are recorded, so `health()` can name
  what is holding the lock and for how long.
* a failed acquire raises `BrokerLockBusy` — loud and typed — rather than letting
  the caller sit in a queue it cannot see the front of.

A wait timeout only applies to acquiring, never to holding: a slow-but-progressing
call is unaffected.
"""
import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("JARVIS_BrokerLock")

DEFAULT_WAIT_SEC = 10.0


class BrokerLockBusy(RuntimeError):
    """The broker lock could not be taken in time.

    Distinct from a broker timeout: nothing was sent, and the holder is known.
    """


class TrackedRLock:
    """A reentrant lock that records its owner and bounds how long a waiter waits.

    Drop-in for `threading.RLock` — `with lock:` and `lock.acquire()` both work —
    so it can replace one without touching its call sites.
    """

    def __init__(self, name: str = "broker", default_wait_sec: float = DEFAULT_WAIT_SEC):
        self.name = name
        self.default_wait_sec = float(default_wait_sec)
        self._lock = threading.RLock()
        # Guards the bookkeeping below; never held across a broker call.
        self._book = threading.Lock()
        self._owner_ident: Optional[int] = None
        self._owner_name: Optional[str] = None
        self._task: Optional[str] = None
        self._since: Optional[float] = None
        self._depth = 0

    # ── Lock protocol ────────────────────────────────────────────────────────
    def acquire(self, blocking: bool = True, timeout: float = -1, task: Optional[str] = None) -> bool:
        # -1 is threading's "wait forever"; we never wait forever.
        wait = self.default_wait_sec if (timeout is None or timeout == -1) else float(timeout)
        if not blocking:
            wait = 0.0

        got = self._lock.acquire(True, wait) if wait > 0 else self._lock.acquire(False)
        if not got:
            h = self.health()
            logger.error(
                "%s lock: waited %.1fs and gave up. Held by %s (task %s) for %.1fs — "
                "a broker call that never returned is holding it, and Python cannot "
                "interrupt a thread blocked in native code. Failing this call rather "
                "than queueing behind one that will never finish.",
                self.name, wait, h["holder"], h["task"], h["age_sec"],
            )
            raise BrokerLockBusy(
                f"{self.name} lock held by {h['holder']} (task {h['task']}) for "
                f"{h['age_sec']:.1f}s"
            )

        with self._book:
            if self._depth == 0:
                self._owner_ident = threading.get_ident()
                self._owner_name = threading.current_thread().name
                self._task = task or threading.current_thread().name
                self._since = time.monotonic()
            self._depth += 1
        return True

    def release(self) -> None:
        with self._book:
            if self._depth > 0:
                self._depth -= 1
                if self._depth == 0:
                    self._owner_ident = None
                    self._owner_name = None
                    self._task = None
                    self._since = None
        self._lock.release()

    def __enter__(self) -> "TrackedRLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> bool:
        self.release()
        return False

    def _is_owned(self) -> bool:
        return self._lock._is_owned()

    # ── Observation ──────────────────────────────────────────────────────────
    def health(self) -> Dict[str, Any]:
        """Who holds this lock and for how long. `held: False` when it is free.

        Surfaced so a wedged broker is visible instead of looking like a quiet
        market — the same rule as the rest of the health flags: measured, not
        inferred.
        """
        with self._book:
            if self._depth == 0 or self._since is None:
                return {"held": False, "holder": None, "task": None,
                        "age_sec": 0.0, "depth": 0, "name": self.name}
            return {
                "held": True,
                "holder": self._owner_name,
                "task": self._task,
                "age_sec": round(time.monotonic() - self._since, 3),
                "depth": self._depth,
                "name": self.name,
            }

"""
HM Algo 2.0 — Timeout Guard Utilities.
Protects the system against hanging network calls, slow analytical agents, and unresponsive I/O operations.
"""
import asyncio
import inspect
import functools
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Callable, Dict, Optional, TypeVar

T = TypeVar("T")
logger = logging.getLogger("JARVIS_TimeoutGuard")

class TimeoutGuard:
    """Thread pool and asyncio-based timeout manager for synchronous and asynchronous tasks."""
    _executor = None
    _max_workers = 16
    _lock = threading.RLock()

    @classmethod
    def _release(cls, executor: ThreadPoolExecutor, future: "Future") -> None:
        """A worker that was written off has finally come back.

        Timed-out work is abandoned, not cancelled -- Python cannot interrupt a
        thread blocked inside a native call. The future still completes
        eventually, so this is what lets the guard heal instead of treating a
        recovered worker as stuck forever.
        """
        try:
            executor._jarvis_stuck = max(0, getattr(executor, "_jarvis_stuck", 0) - 1)
        except Exception:
            pass

    @classmethod
    def _get_executor(cls) -> ThreadPoolExecutor:
        # A wedged pool is NOT detected by `_shutdown`: submitting still works
        # and still queues, it just never runs, so every later call times out
        # and returns its default. Before this check, `max_workers` hung broker
        # calls silently disabled the platform for good -- including the calls
        # that CLOSE positions. Capacity is therefore judged by how many
        # workers are currently abandoned, and the pool is replaced once none
        # are free.
        with cls._lock:
            ex = cls._executor
            wedged = ex is not None and getattr(ex, "_jarvis_stuck", 0) >= cls._max_workers
            if ex is None or getattr(ex, "_shutdown", False) or wedged:
                if ex is not None and not getattr(ex, "_shutdown", False):
                    logger.error(
                        "TimeoutGuard: %d of %d guard workers are stuck on calls that never "
                        "returned; replacing the pool. Any thread still blocked in the old "
                        "pool is abandoned (Python cannot kill it).",
                        getattr(ex, "_jarvis_stuck", 0), cls._max_workers,
                    )
                    ex.shutdown(wait=False)
                cls._executor = ThreadPoolExecutor(
                    max_workers=cls._max_workers, thread_name_prefix="jarvis_guard"
                )
                cls._executor._jarvis_stuck = 0
            return cls._executor

    @classmethod
    def health(cls) -> Dict[str, Any]:
        """Whether the guard can still run work -- surfaced so a wedged guard is
        visible instead of looking like a quiet market."""
        with cls._lock:
            ex = cls._executor
            stuck = getattr(ex, "_jarvis_stuck", 0) if ex is not None else 0
            return {
                "stuck_workers": stuck,
                "max_workers": cls._max_workers,
                "available": max(0, cls._max_workers - stuck),
                "wedged": stuck >= cls._max_workers,
            }

    @classmethod
    def run_sync(
        cls,
        func: Callable[..., T],
        *args: Any,
        timeout_sec: float = 3.0,
        default: Optional[Any] = None,
        task_name: str = "Task",
        **kwargs: Any
    ) -> T:
        """Executes a synchronous blocking function with a hard wall-clock timeout."""
        try:
            executor = cls._get_executor()
            future = executor.submit(func, *args, **kwargs)
        except Exception as e:
            logger.error(f"TimeoutGuard: Failed to submit {task_name}: {e}")
            return default() if callable(default) else default

        try:
            return future.result(timeout=timeout_sec)
        except FuturesTimeoutError:
            # `future.result(timeout=...)` does NOT cancel the work. If it is
            # already running, cancel() returns False and the worker stays
            # blocked until the call returns on its own -- so count it as
            # occupied and release it only when it actually finishes.
            if not future.cancel():
                try:
                    executor._jarvis_stuck = getattr(executor, "_jarvis_stuck", 0) + 1
                    future.add_done_callback(lambda f, e=executor: cls._release(e, f))
                except Exception:
                    pass
                logger.error(
                    "TimeoutGuard: %s exceeded %.2fs and could NOT be cancelled; guard now has "
                    "%s worker(s) stuck.",
                    task_name, timeout_sec, cls.health()["stuck_workers"],
                )
            else:
                logger.warning(
                    f"TimeoutGuard: {task_name} exceeded timeout limit of {timeout_sec:.2f}s! Returning default fallback."
                )
            return default() if callable(default) else default
        except Exception as e:
            logger.error(f"TimeoutGuard: Error in {task_name}: {e}", exc_info=True)
            return default() if callable(default) else default

    @classmethod
    async def run_async(
        cls,
        coro: Any,
        timeout_sec: float = 3.0,
        default: Optional[Any] = None,
        task_name: str = "AsyncTask"
    ) -> T:
        """Executes an asynchronous coroutine with an asyncio timeout."""
        try:
            return await asyncio.wait_for(coro, timeout=timeout_sec)
        except asyncio.TimeoutError:
            logger.warning(f"TimeoutGuard: {task_name} exceeded async timeout of {timeout_sec:.2f}s! Returning default fallback.")
            return default() if callable(default) else default
        except Exception as e:
            logger.error(f"TimeoutGuard: Async error in {task_name}: {e}", exc_info=True)
            return default() if callable(default) else default

def timeout_guarded(timeout_sec: float = 3.0, default: Any = None, task_name: Optional[str] = None):
    """Decorator to enforce strict timeout protection on functions."""
    def decorator(func: Callable):
        name = task_name or func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return TimeoutGuard.run_sync(func, *args, timeout_sec=timeout_sec, default=default, task_name=name, **kwargs)

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            return await TimeoutGuard.run_async(func(*args, **kwargs), timeout_sec=timeout_sec, default=default, task_name=name)

        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return wrapper
    return decorator

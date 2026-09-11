"""JARVIS AI 4.0 — Canonical filesystem paths.

WHY THIS MODULE EXISTS
----------------------
Every persistence layer in the project used a *relative* default path
(``"jarvis_history.db"``, ``"jarvis_trade_memory.db"``, ...). A relative path is
resolved against the process working directory, so the same code wrote to
different files depending on where it was launched from:

  * ``python main.py`` at the repo root      -> ``<repo>/jarvis_history.db``
  * ``python -m jarvis.something`` elsewhere -> ``<cwd>/jarvis_history.db``
  * ``HM_start.bat`` double-clicked in an IDE -> some other directory entirely

Consequence: the learning engine could read a database the live loop never
wrote to, the circuit breaker could "forget" it had tripped, and backtests
silently shared state with live trading. All four databases now resolve to
absolute paths anchored on the repository root.

Resolution order for the data directory (first wins):
  1. ``JARVIS_DATA_DIR`` environment variable
  2. ``<repo_root>/data``

Overriding via the environment keeps tests and CI fully isolated without
touching the repository layout.
"""
from __future__ import annotations

import os

__all__ = ["REPO_ROOT", "DATA_DIR", "resolve_db_path", "ensure_data_dir"]


def _find_repo_root() -> str:
    """Walk up from this file until a directory containing ``jarvis/`` is found."""
    here = os.path.dirname(os.path.abspath(__file__))          # .../jarvis/config
    candidate = os.path.dirname(os.path.dirname(here))         # .../<repo_root>
    if os.path.isdir(os.path.join(candidate, "jarvis")):
        return candidate
    # Fallback: the package layout was relocated; use two levels up regardless so
    # we still get a stable absolute base rather than the process CWD.
    return candidate


REPO_ROOT: str = _find_repo_root()
DATA_DIR: str = os.environ.get("JARVIS_DATA_DIR") or os.path.join(REPO_ROOT, "data")


def resolve_db_path(db_path: str) -> str:
    """Return an absolute database path.

    * Empty string / ``None`` is passed through unchanged — callers use ``""`` to
      mean "run entirely in memory for this backtest, do not persist anything".
    * ``:memory:`` is passed through unchanged.
    * Absolute paths are normalised but otherwise untouched.
    * Relative names are anchored under :data:`DATA_DIR`, which is why the same
      code now hits the same file no matter the working directory.
    """
    if not db_path:
        return db_path
    if db_path == ":memory:":
        return db_path
    if os.path.isabs(db_path):
        return os.path.normpath(db_path)
    return os.path.normpath(os.path.join(DATA_DIR, db_path))


def ensure_data_dir() -> str:
    """Create the data directory if needed and return its path."""
    os.makedirs(DATA_DIR, exist_ok=True)
    return DATA_DIR

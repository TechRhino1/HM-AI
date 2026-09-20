"""A schema version for every SQLite store.

D3: all 11 databases in this repo reported `PRAGMA user_version = 0`, so there
was no way to tell what shape a file was in — and two copies of
`jarvis_history.db` (root and `data/`) have already drifted apart: the root copy
has no `closed_at`. Schema changes were applied by a best-effort "add any column
that is missing" sweep, which cannot express a rename, a type change, a
backfill, or a "refuse to open" rule.

This module does not add a migration framework. It adds the one thing that makes
one possible later: a version number that is written on create and checked on
open, so a file can declare what it is.

Rules:
* version 0 means "written before this existed" — it is treated as the current
  version, not as an error, because every existing file is 0.
* a file NEWER than the code is an error and is logged loudly: opening it with
  code that does not know its shape is how columns get silently dropped.
"""
import logging
import sqlite3
from typing import Callable, Dict, Optional

logger = logging.getLogger("JARVIS_Schema")


def read_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def write_version(conn: sqlite3.Connection, version: int) -> None:
    # PRAGMA does not accept a bound parameter, and the value is an int we
    # control, so it is formatted directly.
    conn.execute(f"PRAGMA user_version = {int(version)}")


def ensure_version(conn: sqlite3.Connection, version: int, name: str) -> int:
    """Stamp `version` onto the database and report any drift. Returns the version.

    Call this once, after the schema has been brought up to date, so the number
    on disk always describes the file as it now is.
    """
    current = read_version(conn)
    if current == version:
        return current
    if current > version:
        # Do not "fix" this by downgrading the number: that would hide it.
        logger.error(
            "%s is schema version %s but this code only knows version %s. The "
            "file was written by newer code; opening it risks reading columns "
            "this version does not create. Refusing to stamp it.",
            name, current, version,
        )
        return current
    # current < version: 0 (pre-existing file) or an older stamp. Both mean
    # "the caller has just brought it up to `version`", so record that.
    try:
        write_version(conn, version)
    except Exception as e:
        logger.error("Could not stamp schema version %s on %s: %s", version, name, e)
        return current
    logger.info("%s schema: %s -> %s", name, current or "unversioned", version)
    return version


def add_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
    """Add each column that is not there yet. Idempotent, so it is safe as a migration step."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        except Exception as e:
            # Not swallowed silently: a migration that half-applies is worse
            # than one that fails, and the caller aborts on this.
            logger.error("Could not add %s.%s (%s): %s", table, name, definition, e)
            raise


def migrate(conn: sqlite3.Connection, name: str, target: int,
            steps: Dict[int, Callable[[sqlite3.Connection], None]]) -> int:
    """Bring a store up to `target`, one numbered step at a time.

    Replaces the old "add whatever column happens to be missing" sweep, which
    ran unconditionally on every open and could express neither ordering, a
    backfill, nor a refusal. Each step is applied at most once because the
    version is written immediately after it — so a crash mid-migration leaves a
    file that says how far it got, rather than one that claims to be current.

    Steps must be idempotent: a step that is recorded as applied is never run
    again, but a step that *failed* may be retried on the next open, and in that
    case it re-runs against a partially updated table.

    Raises on the first failing step, leaving the version at the last good one.
    A half-migrated database that announces itself is recoverable; one that
    silently claims to be current is not.
    """
    current = read_version(conn)
    if current > target:
        logger.error(
            "%s is schema version %s but this code only knows %s — written by "
            "newer code. Refusing to migrate.", name, current, target,
        )
        return current
    while current < target:
        nxt = current + 1
        step = steps.get(nxt)
        if step is None:
            logger.error("%s has no migration from %s to %s; stopping at %s.",
                         name, current, nxt, current)
            return current
        logger.info("%s: applying migration %s", name, nxt)
        step(conn)
        current = nxt
        write_version(conn, current)
        logger.info("%s migrated to schema %s", name, current)
    return current

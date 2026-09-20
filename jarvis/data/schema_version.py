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
from typing import Optional

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

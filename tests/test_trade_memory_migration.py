"""D3 remainder — `trade_records` must migrate in numbered steps, not by an "add whatever is
missing" sweep.

`TradeMemory._init_db` used to run this on **every open**:

    cur.execute("PRAGMA table_info(trade_records)")
    columns = [info[1] for info in cur.fetchall()]
    if 'ml_features' not in columns:
        cur.execute("ALTER TABLE trade_records ADD COLUMN ml_features TEXT")
    if 'triple_barrier_label' not in columns:
        cur.execute("ALTER TABLE trade_records ADD COLUMN triple_barrier_label INTEGER DEFAULT 0")

It can express neither ordering, a backfill, nor a refusal. The refusal is the part that bites: the
sweep ignores `user_version` entirely, so given a file written by **newer** code it re-adds whatever
column this version happens to know about — while `ensure_version` logs "written by newer code"
about a change the sweep has already silently made. A future rename (say `mfe` -> `mfe_r`) would be
undone on the next open, and the column would come back with this version's meaning.

That case is what `test_a_newer_file_is_not_patched_back_into_this_schema` pins.
"""

import sqlite3

import pytest

from jarvis.learning.trade_memory import MIGRATIONS, SCHEMA_VERSION, TradeMemory

# `trade_records` as it stood BEFORE migration 1: through `quality_gate`, no
# `ml_features` and no `triple_barrier_label`.
LEGACY_COLUMNS = """ticket INTEGER PRIMARY KEY, symbol TEXT, timestamp TEXT, trade_type TEXT,
    entry_price REAL, exit_price REAL, sl REAL, tp REAL, lots REAL, pnl REAL, is_win INTEGER,
    regime TEXT, strategy TEXT, model_confidence REAL, adversarial_penalty REAL,
    expected_value REAL, mfe REAL, mae REAL, reasoning TEXT, quality_gate TEXT"""

NEW_COLUMNS = {"ml_features", "triple_barrier_label"}


def _build(path, columns_sql, version, extra_columns=()):
    """Create a `trade_records` file at a given shape and version."""
    conn = sqlite3.connect(str(path))
    cols = columns_sql
    for c in extra_columns:
        cols += f", {c}"
    conn.execute(f"CREATE TABLE trade_records ({cols})")
    conn.execute(f"PRAGMA user_version = {int(version)}")
    conn.commit()
    conn.close()


def _columns(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {r[1] for r in c.execute("PRAGMA table_info(trade_records)")}
    finally:
        c.close()


def _version(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return c.execute("PRAGMA user_version").fetchone()[0]
    finally:
        c.close()


@pytest.fixture
def db(tmp_path):
    return tmp_path / "jarvis_trade_memory.db"


class TestMigrationOne:
    def test_the_step_exists_for_the_version_it_names(self):
        assert MIGRATIONS[SCHEMA_VERSION] is not None
        assert set(MIGRATIONS) == {1}

    def test_a_legacy_file_gains_both_columns(self, db):
        """uv=0, missing both columns -> migration 1 adds them and stamps 1."""
        _build(db, LEGACY_COLUMNS, version=0)
        assert not NEW_COLUMNS & _columns(db)

        TradeMemory(db_path=str(db)).close()

        assert NEW_COLUMNS <= _columns(db)
        assert _version(db) == SCHEMA_VERSION

    def test_a_brand_new_file_is_versioned_without_a_phantom_migration(self, db):
        """A fresh file already has every column; migration 1 must be a harmless no-op."""
        tm = TradeMemory(db_path=str(db))
        tm.close()
        assert _version(db) == SCHEMA_VERSION
        assert NEW_COLUMNS <= _columns(db)

    def test_opening_twice_does_not_reapply_the_step(self, db):
        """The step runs at most once, because the version is written right after it."""
        _build(db, LEGACY_COLUMNS, version=0)
        TradeMemory(db_path=str(db)).close()
        first = _columns(db)
        TradeMemory(db_path=str(db)).close()
        assert _columns(db) == first
        assert _version(db) == SCHEMA_VERSION

    def test_a_current_file_is_left_alone(self, db):
        _build(db, LEGACY_COLUMNS, version=SCHEMA_VERSION,
               extra_columns=("ml_features TEXT", "triple_barrier_label INTEGER DEFAULT 0"))
        TradeMemory(db_path=str(db)).close()
        assert _version(db) == SCHEMA_VERSION


class TestTheRefusalTheSweepCouldNotExpress:
    def test_a_newer_file_is_not_patched_back_into_this_schema(self, db):
        """THE DEFECT. A file from newer code (uv=2) that does NOT carry
        `triple_barrier_label` — because the newer version renamed or dropped it.

        The sweep saw "column missing" and added it back, silently undoing the newer
        schema. `migrate()` sees `current > target`, refuses, and touches nothing.
        """
        _build(db, LEGACY_COLUMNS, version=2)
        assert "triple_barrier_label" not in _columns(db)

        TradeMemory(db_path=str(db)).close()

        assert "triple_barrier_label" not in _columns(db), (
            "the column was re-added into a file written by newer code"
        )
        assert not NEW_COLUMNS & _columns(db)

    def test_a_newer_file_is_not_stamped_down(self, db):
        _build(db, LEGACY_COLUMNS, version=2)
        TradeMemory(db_path=str(db)).close()
        assert _version(db) == 2

    def test_a_newer_file_with_an_extra_column_is_still_writable(self, db):
        """Refusing to migrate is not refusing to operate.

        A version-2 file plausibly has one MORE column than this code knows. The old
        positional `INSERT ... VALUES (?, ... x22)` blew up on that
        ("table trade_records has 23 columns but 22 values were supplied"), so the store
        could not write to a file it had correctly decided to open. The insert now names
        its columns, which tolerates extra ones.
        """
        _build(db, LEGACY_COLUMNS, version=2,
               extra_columns=("ml_features TEXT", "triple_barrier_label INTEGER DEFAULT 0",
                              "notes TEXT"))
        assert len(_columns(db)) == 23

        tm = TradeMemory(db_path=str(db))
        tm.record_trade({"ticket": 1, "symbol": "EURUSD", "type": "BUY",
                         "entry": 1.1, "sl": 1.09, "tp": 1.12})
        tm.close()

        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = c.execute("SELECT symbol, entry_price FROM trade_records WHERE ticket = 1").fetchone()
        finally:
            c.close()
        assert row == ("EURUSD", 1.1)
        assert _version(db) == 2

"""D3 — the three remaining versioned stores must advance through `migrate()`, not `ensure_version`.

`circuit_state`, `drawdown_state` and `metadata` had no "add whatever is missing" sweep to remove —
they are `CREATE TABLE` + a stamp — so this is forward-looking structure rather than a live defect.
What it buys is one specific thing, and that is what the tests pin:

`ensure_version(conn, SCHEMA_VERSION, name)` stamps **whatever number the code declares**, whether or
not anything was done to earn it. Bump `SCHEMA_VERSION` to 2 to add a column, forget the `ALTER`, and
every existing file is labelled 2 while still missing the column — the store then reads a column that
does not exist and gets `None` where it expects a value, which is exactly how a risk gate fails open.

`migrate()` refuses instead: with no step registered for 2 it stops at 1 and says so. A file that
under-states its version is recoverable; one that over-states it is not.

The discriminating test is `test_a_version_nobody_can_build_is_not_stamped`. It calls `migrate()`
with the store's real `MIGRATIONS` and a target one past what the registry can reach, and asserts the
number on disk does not move — then asserts `ensure_version` *would* have moved it, so the contrast
is measured rather than asserted in a comment.
"""

import sqlite3

import pytest

from jarvis.data.schema_version import ensure_version, migrate, read_version, write_version
from jarvis.historical import metadata_db as md_mod
from jarvis.risk import circuit_breaker as cb_mod
from jarvis.risk import drawdown as dd_mod

# name -> (module, factory). The factory must open the store, which runs `_init_db`.
STORES = {
    "circuit_state": (cb_mod, lambda p: cb_mod.CircuitBreaker(db_path=p)),
    "drawdown_state": (dd_mod, lambda p: dd_mod.DrawdownGuard(db_path=p)),
    "metadata": (md_mod, lambda p: md_mod.MetadataDB(db_path=p)),
}


def _version(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return c.execute("PRAGMA user_version").fetchone()[0]
    finally:
        c.close()


@pytest.fixture(params=list(STORES))
def store(request, tmp_path):
    """(name, module, factory) for one store, with a fresh db path."""
    name = request.param
    module, factory = STORES[name]
    return name, module, factory, str(tmp_path / f"{name}.db")


class TestEveryStoreRegistersItsSteps:
    def test_the_declared_version_has_a_step(self, store):
        name, module, _, _ = store
        declared = module.SCHEMA_VERSION
        assert declared in module.MIGRATIONS, (
            f"{name} declares version {declared} with no step that builds it"
        )

    def test_no_step_is_registered_beyond_the_declared_version(self, store):
        """A step for a version the code does not declare is dead code at best,
        and at worst a migration that half-runs against a file nobody asked for."""
        name, module, _, _ = store
        assert set(module.MIGRATIONS) <= set(range(1, module.SCHEMA_VERSION + 1))


@pytest.mark.drawdown_persistence
class TestOpeningAStore:
    def test_a_fresh_file_reaches_the_declared_version(self, store):
        """conftest forces `db_path=""` on every test so drawdown state cannot leak;
        this one is about persistence, so it opts out with the marker."""
        name, module, factory, path = store
        factory(path)
        assert _version(path) == module.SCHEMA_VERSION

    def test_an_unversioned_file_is_stamped_not_rebuilt(self, store):
        """The pre-versioning case: a file at uv=0 that already has this shape.

        Migration 1 is a no-op for these three stores, so the only observable change
        is the number. It must still be written — that is the whole point of D3.
        """
        name, module, factory, path = store
        c = sqlite3.connect(path)
        c.execute("PRAGMA user_version = 0")
        c.commit()
        c.close()

        factory(path)

        assert _version(path) == module.SCHEMA_VERSION

    def test_a_newer_file_is_not_stamped_down(self, store):
        name, module, factory, path = store
        c = sqlite3.connect(path)
        c.execute("PRAGMA user_version = 7")
        c.commit()
        c.close()

        factory(path)

        assert _version(path) == 7

    def test_opening_twice_does_not_move_the_version(self, store):
        name, module, factory, path = store
        factory(path)
        factory(path)
        assert _version(path) == module.SCHEMA_VERSION


class TestAVersionNobodyCanBuildIsNotStamped:
    def test_migrate_stops_instead_of_stamping_it(self, store):
        """THE DEFECT, for these three stores.

        Aim `migrate()` at one past the last version the registry can build, using the
        store's real steps. It must stop where it is and refuse to write a number whose
        shape nothing created.
        """
        name, module, _, path = store
        c = sqlite3.connect(path)
        try:
            reached = module.SCHEMA_VERSION
            write_version(c, reached)
            target = reached + 1

            result = migrate(c, name, target, module.MIGRATIONS)

            assert result == reached, f"migrate() claimed {result}, a version no step builds"
            assert read_version(c) == reached, (
                f"{name} was stamped {target} with no migration that produces it"
            )
            c.commit()
        finally:
            c.close()

    def test_ensure_version_would_have_stamped_it(self, store):
        """...and the contrast, measured rather than asserted in a comment.

        This is the behaviour the change removes. If it ever stops being true, the
        migration machinery stopped buying anything and these stores can go back.
        """
        name, module, _, path = store
        c = sqlite3.connect(path)
        try:
            reached = module.SCHEMA_VERSION
            write_version(c, reached)

            ensure_version(c, reached + 1, name)

            assert read_version(c) == reached + 1
            c.rollback()
        finally:
            c.close()

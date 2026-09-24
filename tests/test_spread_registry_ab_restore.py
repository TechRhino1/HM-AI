"""`apply_registry(None)` must actually restore the registry.

WHY THIS EXISTS
---------------
`tools/spread_registry_ab.py` is the §J2 A/B harness, and its `apply_registry`
built the new registry from the CURRENT one:

    for key, spec in reg._REGISTRY.items():
        ov = (overrides or {}).get(key)
        new[key] = dataclasses.replace(spec, **ov) if ov else spec

With `overrides=None` that is `new[key] = spec` — a copy of whatever is already
there. So it was a no-op, not a restore. Once `apply_registry(CORRECTED)` had
run, the corrected specs stayed in place for the rest of the process.

Both the harness's `main()` and the reconciliation driver call
`apply_registry(None)` before the INCUMBENT scan of every symbol, so **only the
first symbol scanned ever had a genuine incumbent arm**; every symbol after it
was measured corrected-vs-corrected. Measured directly: pristine AUDUSD
`typical_spread_pips` 0.9 -> after CORRECTED 2.3 -> after `apply_registry(None)`
**2.3, not 0.9**.

The consequence was not cosmetic. It invalidated §J2's artefact, and it invalidated
the first reconciliation sweep built on top of it — a measurement that was being
used to decide whether to ship a trading change. A harness that silently compares
a configuration against itself will happily report "no effect", and "no effect" is
indistinguishable from "the effect is small".

These tests assert the contract the name always promised, and they are written so
that a future refactor cannot quietly reintroduce a self-comparison.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(REPO, "tools", "spread_registry_ab.py")


def _fresh_harness():
    """Load the harness against a PRISTINE registry.

    `_PRISTINE` is captured at import, so a harness loaded after some other test
    has mutated the registry would snapshot the mutated state and the tests below
    would pass vacuously. Reloading `symbol_registry` first makes the baseline
    real. (The harness itself reads the registry through the module object, so a
    reload is visible to it.)
    """
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    from jarvis.data import symbol_registry as reg
    importlib.reload(reg)

    spec = importlib.util.spec_from_file_location("spread_registry_ab_under_test", HARNESS)
    assert spec and spec.loader, f"could not load {HARNESS}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, reg


class TestApplyRegistryRestores:
    def test_none_restores_the_pristine_values(self):
        m, reg = _fresh_harness()
        sym = "AUDUSD"
        pristine = reg.resolve(sym).typical_spread_pips

        m.apply_registry(m.CORRECTED)
        assert reg.resolve(sym).typical_spread_pips != pristine, (
            "premise: CORRECTED must actually change this symbol"
        )

        m.apply_registry(None)
        assert reg.resolve(sym).typical_spread_pips == pristine, (
            "apply_registry(None) did not restore — the override LEAKED, so every "
            "symbol after the first would be measured corrected-vs-corrected"
        )

    def test_it_restores_every_symbol_not_just_the_first(self):
        """The defect's signature: the FIRST symbol looks right and the rest do
        not. Assert the whole table, so a fix that only special-cases index 0
        cannot pass."""
        m, reg = _fresh_harness()
        pristine = {k: s.typical_spread_pips for k, s in reg._REGISTRY.items()}

        m.apply_registry(m.CORRECTED)
        m.apply_registry(None)

        drifted = {
            k: (pristine[k], reg.resolve(k).typical_spread_pips)
            for k in pristine
            if reg.resolve(k).typical_spread_pips != pristine[k]
        }
        assert not drifted, f"symbols still carrying an override after restore: {drifted}"

    def test_it_is_idempotent(self):
        m, reg = _fresh_harness()
        pristine = reg.resolve("EURUSD").typical_spread_pips
        for _ in range(3):
            m.apply_registry(None)
        assert reg.resolve("EURUSD").typical_spread_pips == pristine

    def test_applying_the_same_table_twice_is_stable(self):
        """Rebuilding from `_PRISTINE` means re-applying is not cumulative."""
        m, reg = _fresh_harness()
        m.apply_registry(m.CORRECTED)
        once = reg.resolve("EURUSD").typical_spread_pips
        m.apply_registry(m.CORRECTED)
        twice = reg.resolve("EURUSD").typical_spread_pips
        assert once == twice, (
            f"re-applying CORRECTED changed the value again ({once} -> {twice}); "
            "the override is compounding instead of being rebuilt"
        )

    def test_an_override_is_actually_applied(self):
        """The negative control: if `apply_registry` did nothing at all, every
        test above would pass and the harness would be useless."""
        m, reg = _fresh_harness()
        before = reg.resolve("EURUSD").typical_spread_pips
        m.apply_registry(m.CORRECTED)
        after = reg.resolve("EURUSD").typical_spread_pips
        assert after != before, "apply_registry(CORRECTED) did not change anything"

    def test_the_harness_exposes_a_pristine_snapshot(self):
        m, _reg = _fresh_harness()
        assert hasattr(m, "_PRISTINE"), (
            "the harness must keep a pristine snapshot to rebuild from"
        )
        assert "EURUSD" in m._PRISTINE

    @pytest.mark.parametrize("bad", [{"NOPE": {"typical_spread_pips": 1.0}}])
    def test_an_unknown_symbol_in_the_table_is_harmless(self, bad):
        """A typo'd symbol must not drop the rest of the registry."""
        m, reg = _fresh_harness()
        size_before = len(reg._REGISTRY)
        m.apply_registry(bad)
        assert len(reg._REGISTRY) == size_before
        m.apply_registry(None)
        assert len(reg._REGISTRY) == size_before

"""The symbol registry must agree with the broker's own metadata.

Two sources of truth for the same facts is how eight of sixteen symbols ended up
producing zero trades for an entire quarter. ``GER40``, ``UK100`` and ``XAGUSD``
were absent from the registry, so ``resolve()`` returned the generic FX fallback
(``contract_size=100_000``, ``pip_size=0.0001``, ``max_spread_pips=5.0``). For an
index whose real spread is ~2 index points, an FX-sized spread cap of 5 "pips"
where a pip is 0.0001 rejects every bar — silently, with no error.

These tests fail the moment the registry drifts from the fetched manifests.
"""
from __future__ import annotations

import glob
import json
import os

import pandas as pd
import pytest

from jarvis.data.symbol_registry import (
    resolve,
    registry_mismatches,
    get_max_spread,
    get_pip_size,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_GLOB = os.path.join(REPO_ROOT, "data", "market", "real", "*", "*.manifest.json")


def _manifests():
    out = []
    for path in sorted(glob.glob(MANIFEST_GLOB)):
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        if payload.get("meta"):
            out.append((payload["symbol"], payload["meta"]))
    return out


_MANIFESTS = _manifests()


@pytest.mark.skipif(not _MANIFESTS, reason="no fetched broker manifests present")
@pytest.mark.parametrize("symbol,meta", _MANIFESTS, ids=[s for s, _ in _MANIFESTS])
def test_registry_matches_broker_manifest(symbol, meta):
    mismatches = registry_mismatches(meta, symbol)
    assert not mismatches, (
        f"{symbol}: registry disagrees with the broker manifest on {mismatches}. "
        f"Fix jarvis/data/symbol_registry.py - a wrong spec does not raise, it just "
        f"silently makes the symbol untradeable."
    )


def test_broker_symbol_resolves_like_its_canonical_name():
    """The symbol the ENGINE passes is the BROKER's, not the canonical name.

    This is the hole ``test_registry_matches_broker_manifest`` leaves open. That
    test resolves the manifest's ``symbol`` field ("WTI"), which is a key in the
    registry and therefore always resolves. But the running engine asks about the
    ``broker_symbol`` ("OILCash#"), and *that* is the lookup that can miss the
    alias table and fall through to the generic FX spec.

    Oil did exactly that. Every other cash CFD carried its ``XxxCash#`` alias
    (US500Cash#, US30Cash#, GER40Cash#, UK100Cash#) but oil did not, and
    "OILCASH#" contains none of the canonical names so the fuzzy pass could not
    rescue it either. The result was a contract size of 100,000 instead of 100 —
    position sizing out by a factor of 1000 — and a pip of 0.0001 instead of
    0.01, with no error raised.
    """
    checked = 0
    for path in sorted(glob.glob(MANIFEST_GLOB)):
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        broker = payload.get("broker_symbol")
        canonical = payload.get("symbol")
        if not broker or not canonical:
            continue
        checked += 1

        broker_spec = resolve(broker)
        canon_spec = resolve(canonical)

        assert broker_spec.contract_size == canon_spec.contract_size, (
            f"{broker} resolves to contract_size={broker_spec.contract_size} but "
            f"its canonical name {canonical} has {canon_spec.contract_size}. The "
            f"engine passes the BROKER symbol to resolve() - add {broker!r} to "
            f"_ALIAS_MAP in jarvis/data/symbol_registry.py."
        )
        assert broker_spec.pip_size == canon_spec.pip_size, (
            f"{broker} pip_size={broker_spec.pip_size} != {canonical} "
            f"pip_size={canon_spec.pip_size}"
        )
        assert broker_spec.digits == canon_spec.digits, (
            f"{broker} digits={broker_spec.digits} != {canonical} "
            f"digits={canon_spec.digits}"
        )
        assert broker_spec.max_spread_pips == canon_spec.max_spread_pips, (
            f"{broker} max_spread_pips={broker_spec.max_spread_pips} != "
            f"{canonical} {canon_spec.max_spread_pips}"
        )

    if not checked:
        pytest.skip("no broker manifests present")


@pytest.mark.skipif(not _MANIFESTS, reason="no fetched broker manifests present")
@pytest.mark.parametrize("symbol,meta", _MANIFESTS, ids=[s for s, _ in _MANIFESTS])
def test_spread_cap_admits_the_instruments_own_typical_spread(symbol, meta):
    """A cap below the instrument's typical spread is a prohibition, not a filter.

    This is the exact defect that produced zero trades: the cap must leave room
    for the spread the instrument actually quotes, otherwise 100% of bars fail
    the gate and the strategy is blamed for a units error.

    The spread is measured from the **data itself**, not from the manifest's
    ``typical_spread_pips`` field — that field is unreliable (it claims ETHUSD
    trades at 190 while the fetched bars quote 345).
    """
    import pandas as pd

    data = glob.glob(
        os.path.join(REPO_ROOT, "data", "market", "real", symbol, "*.parquet")
    )
    if not data:
        pytest.skip("no fetched bars for this symbol")

    spec = resolve(symbol)
    df = pd.read_parquet(data[0])
    if "spread" not in df.columns:
        pytest.skip("no spread column in the fetched bars")

    # Convert MT5 integer points into the registry's pip unit.
    point = 10.0 ** (-int(meta.get("digits", spec.digits)))
    spreads = df["spread"].astype(float) * point / spec.pip_size
    p95 = float(spreads.quantile(0.95))

    assert spec.max_spread_pips >= p95, (
        f"{symbol}: max_spread_pips={spec.max_spread_pips} is below the 95th-percentile "
        f"spread actually quoted ({p95:.2f} in {spec.pip_size} units) - the gate would "
        f"reject a large share of normal bars"
    )


def test_known_index_specs_are_not_the_fx_fallback():
    """Guard the specific symbols that were missing."""
    for sym, digits, contract in (
        ("GER40", 2, 1.0),
        ("UK100", 2, 1.0),
        ("NAS100", 2, 1.0),
        ("US30", 2, 1.0),
    ):
        spec = resolve(sym)
        assert spec.asset_class == "INDEX", f"{sym} should be an INDEX"
        assert spec.digits == digits, f"{sym} digits={spec.digits}, expected {digits}"
        assert spec.contract_size == contract, f"{sym} contract={spec.contract_size}"
        # The FX fallback signature — must never apply to an index.
        assert spec.pip_size != 0.0001, f"{sym} still has the FX fallback pip size"
        assert spec.contract_size != 100_000.0, f"{sym} still has the FX fallback contract"


def test_silver_is_a_commodity_not_an_fx_pair():
    spec = resolve("XAGUSD")
    assert spec.asset_class == "COMMODITY"
    assert spec.digits == 3
    assert spec.contract_size == 5000.0


def test_broker_aliases_resolve_to_the_canonical_spec():
    for alias, canonical in (
        ("GER40Cash#", "GER40"),
        ("DE40", "GER40"),
        ("DAX", "GER40"),
        ("UK100Cash#", "UK100"),
        ("FTSE100", "UK100"),
        ("US100Cash#", "NAS100"),
        ("SILVER.i#", "XAGUSD"),
        ("GOLD", "XAUUSD"),
        ("US30Cash#", "US30"),
    ):
        assert resolve(alias).canonical == canonical, f"{alias} -> {resolve(alias).canonical}"


def test_unknown_symbol_logs_an_error_and_still_returns_a_spec(caplog):
    """The fallback must be loud, not silent."""
    import logging

    with caplog.at_level(logging.ERROR, logger="JARVIS_SymbolRegistry"):
        spec = resolve("TOTALLY_UNKNOWN_INSTRUMENT_XYZ")
    assert spec is not None
    assert any("NOT registered" in rec.message for rec in caplog.records)


def test_helpers_agree_with_resolve():
    for sym in ("GER40", "XAUUSD", "EURUSD", "XAGUSD"):
        assert get_pip_size(sym) == resolve(sym).pip_size
        assert get_max_spread(sym) == resolve(sym).max_spread_pips


# ── Asset-category resolution (the 24/7 crypto defect) ───────────────────────

def test_crypto_broker_symbol_is_classified_as_crypto():
    """BTCUSD# must be CRYPTO, not UNKNOWN.

    The data validator exempts crypto from the weekend-closure check because
    Bitcoin trades 24/7. When ``_category_of("BTCUSD#")`` returned "UNKNOWN" the
    exemption did not apply, so 1,248 of 4,378 perfectly valid Saturday/Sunday
    bars were flagged as synthetic and the entire 6-month fetch was rejected.
    """
    from jarvis.data.mt5_history import _category_of

    assert _category_of("BTCUSD#") == "CRYPTO"
    assert _category_of("BTCUSD") == "CRYPTO"
    assert _category_of("ETHUSD#") == "CRYPTO"
    assert _category_of("SOLUSD#") == "CRYPTO"


def test_category_resolution_strips_unknown_broker_suffixes():
    from jarvis.data.mt5_history import _category_of, _strip_broker_suffix

    assert _strip_broker_suffix("BTCUSD#") == "BTCUSD"
    assert _strip_broker_suffix("BTCUSD.pro") == "BTCUSD"
    # A suffix the registry does not know must still classify correctly.
    assert _category_of("BTCUSD.pro") == "CRYPTO"


def test_category_resolution_handles_aliases_whose_key_ends_in_hash():
    """Broker aliases legitimately end in "#" — stripping first would destroy the key."""
    from jarvis.data.mt5_history import _category_of

    assert _category_of("GER40Cash#") == "INDEX"
    assert _category_of("US100Cash#") == "INDEX"
    assert _category_of("UK100Cash#") == "INDEX"
    assert _category_of("SILVER.i#") == "METAL"
    assert _category_of("GOLD.i#") == "METAL"


def test_non_crypto_weekend_bars_are_still_rejected():
    """The crypto exemption must not weaken the check for everything else."""
    from jarvis.data.mt5_history import validate_real_market_data

    # A 24/7 grid on an FX symbol is still a generator artefact.
    times = pd.date_range("2026-01-01", periods=240, freq="h", tz="UTC")
    df = pd.DataFrame({
        "time": times,
        "open": 1.1, "high": 1.1002, "low": 1.0998, "close": 1.1001,
        "spread": 20, "tick_volume": range(240),
    })
    quality = validate_real_market_data(df, "EURUSD", category="FX_MAJOR")
    assert not quality.ok
    assert any("weekend" in i for i in quality.issues)

    # The same bars on crypto are fine — crypto genuinely trades at weekends.
    quality_crypto = validate_real_market_data(df, "BTCUSD#", category="CRYPTO")
    assert not any("weekend" in i for i in quality_crypto.issues)

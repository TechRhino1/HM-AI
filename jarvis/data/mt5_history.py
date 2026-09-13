"""JARVIS AI 4.0 — Real MT5 Historical Data Acquisition.

WHY THIS MODULE EXISTS
----------------------
The project previously cached *synthetic* bars under a name that claimed they
came from MT5 (``MT5_DefaultBroker_XAUUSD_H1_v1.parquet``). The generator was
``AcquisitionEngine._generate_calibrated_rates`` — a silent fallback used
whenever the MT5 library was missing. Because the fallback result was written to
the data lake with a real-looking manifest, every subsequent backtest ran on
fabricated prices and nobody could tell. Detected by inspection: the "gold" data
ranged $4 378→$7 662, had spread identically 0, a single constant ``tick_volume``
value, microsecond-precision timestamps on an H1 series, and 1 216 weekend bars.

This module is the single, explicit path for obtaining *real* market data. It:

* refuses to fabricate — if MT5 is unavailable it raises instead of inventing bars;
* validates every fetched series against structural properties that synthetic
  data cannot satisfy (weekend closure, uniform bar spacing, price sanity);
* records provenance in the manifest so a synthetic set can never masquerade as
  real again.

CONTRACT
--------
``fetch_h1(symbol, days)`` returns a DataFrame with columns
``time, open, high, low, close, tick_volume, spread, real_volume`` where ``time``
is UTC and rows are ascending. It never returns fewer than ``min_bars`` rows
without raising, so a downstream backtest cannot silently run on nothing.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from jarvis.config.paths import DATA_DIR

logger = logging.getLogger("JARVIS_MT5History")

try:  # pragma: no cover - environment dependent
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:  # pragma: no cover
    mt5 = None
    MT5_AVAILABLE = False


class SyntheticDataError(RuntimeError):
    """Raised when a series fails structural validation as real market data."""


class MT5UnavailableError(RuntimeError):
    """Raised when real data is requested but no MT5 terminal can be reached."""


# ─────────────────────────────────────────────────────────────────────────────
# Symbol metadata — one source of truth for how a symbol is priced.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SymbolMeta:
    """Pricing metadata required to convert price moves into money."""

    name: str                 # internal name used across the project
    broker_symbol: str        # actual MT5 symbol (broker-specific suffix)
    digits: int
    point: float
    pip_size: float
    tick_value: float         # account-currency value of one tick, 1.0 lot
    contract_size: float
    category: str             # FX_MAJOR | FX_CROSS | METAL | INDEX | CRYPTO | ENERGY
    typical_spread_pips: float
    min_lot: float = 0.01
    lot_step: float = 0.01

    @property
    def pip_value_per_lot(self) -> float:
        """Account-currency value of a 1-pip move on 1.0 lot."""
        # tick_value is per *point*; a pip is pip_size/point points.
        points_per_pip = self.pip_size / self.point if self.point > 0 else 1.0
        return self.tick_value * points_per_pip

    @property
    def money_per_price_unit_per_lot(self) -> float:
        """Account-currency value of a 1.0 *price* move on 1.0 lot.

        This is the convention-free basis for PnL and should be preferred over
        pip arithmetic. "What counts as a pip" is a broker/market convention that
        differs per asset (gold is quoted to 2 decimals, silver to 3, indices to
        2, crypto to 2), and the project's hand-maintained registry had drifted
        badly from the broker on exactly this point — it valued XAGUSD at 10/pip
        when the broker reports 50, and GER40 at 10 when it reports 0.01.

        Deriving money directly from a price distance removes the whole class of
        bug: ``pnl = price_delta * money_per_price_unit_per_lot * lots``.
        """
        if self.point <= 0:
            return 0.0
        return self.tick_value / self.point

    def money_for_move(self, price_delta: float, lots: float) -> float:
        """Account-currency PnL for a signed price move on ``lots``."""
        return price_delta * self.money_per_price_unit_per_lot * lots

    def round_price(self, price: float) -> float:
        return round(price, self.digits)


# Broker suffixes observed on XM Global. The resolver in mt5_client handles the
# live lookup; these are the confirmed fallbacks used when offline.
_BROKER_FALLBACK = {
    "XAUUSD": "GOLD.i#", "XAGUSD": "SILVER.i#",
    "US30": "US30Cash#", "NAS100": "US100Cash#", "SPX500": "US500Cash#",
    "US500": "US500Cash#",
    "GER40": "GER40Cash#", "UK100": "UK100Cash#",
    "USOIL": "OILCash#", "UKOIL": "BRENTCash#", "WTI": "OILCash#",
    "BTCUSD": "BTCUSD#", "ETHUSD": "ETHUSD#", "SOLUSD": "SOLUSD#",
    "EURJPY": "EURJPY#", "GBPJPY": "GBPJPY#",
}

# Categories drive risk and exit defaults.
_CATEGORY = {
    "XAUUSD": "METAL", "XAGUSD": "METAL",
    "US30": "INDEX", "NAS100": "INDEX", "SPX500": "INDEX",
    "GER40": "INDEX", "UK100": "INDEX",
    "USOIL": "ENERGY", "UKOIL": "ENERGY", "WTI": "ENERGY", "BRENT": "ENERGY",
    "BTCUSD": "CRYPTO", "ETHUSD": "CRYPTO", "SOLUSD": "CRYPTO",
}

_REGISTRY_CATEGORY = {
    "CRYPTO": "CRYPTO",
    "INDEX": "INDEX",
    "COMMODITY": "METAL",
    "FOREX": "FX_MAJOR",
}

DEFAULT_UNIVERSE: List[str] = [
    # FX majors — the deepest liquidity, tightest spreads.
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
    # Metals
    "XAUUSD", "XAGUSD",
    # Indices
    "US30", "NAS100", "GER40", "UK100",
    # Crypto
    "BTCUSD", "ETHUSD", "SOLUSD",
]


_BROKER_SUFFIXES = ("#", ".I#", ".#", ".I", ".PRO", ".RAW", ".ECN", ".M")

# Timeframes this module can fetch, mapped to their bar length in minutes.
#
# The trade styles map onto these directly (see
# ``jarvis.market.data_feed.fetch_multi_timeframe``):
#   SWING       macro=D1  context=H4  primary=H1  setup=H4  timing=M15
#   DAY_TRADING macro=H4  context=H1  primary=M15 setup=H1  timing=M5
#   SCALP       macro=H1  context=M15 primary=M5  setup=M5  timing=M1
#
# ``M1`` is listed for completeness but brokers commonly serve far less M1
# history than M5+; callers must handle a short series rather than assume 6
# months is available.
TIMEFRAME_MINUTES: Dict[str, int] = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 60, "H4": 240, "D1": 1440,
}


def _mt5_timeframe(name: str):
    """Map a timeframe name to the MetaTrader5 constant, importing lazily."""
    key = str(name).strip().upper()
    if key not in TIMEFRAME_MINUTES:
        raise ValueError(
            f"Unsupported timeframe {name!r}; expected one of {sorted(TIMEFRAME_MINUTES)}"
        )
    import MetaTrader5 as mt5  # local import: module is optional at import time
    const = getattr(mt5, f"TIMEFRAME_{key}", None)
    if const is None:
        raise ValueError(f"MetaTrader5 exposes no TIMEFRAME_{key}")
    return const


# Minimum bars that count as a usable series, scaled to the timeframe. 183
# calendar days is ~130 D1 bars but ~124 000 M15 bars, so a single flat floor
# either rejects valid daily data or waves through a nearly empty intraday one.
_DEFAULT_MIN_BARS: Dict[str, int] = {
    "M1": 2_000, "M5": 2_000, "M15": 1_500, "M30": 1_000,
    "H1": 1_000, "H4": 400, "D1": 100,
}


def default_min_bars(timeframe: str) -> int:
    """Timeframe-aware minimum-bar floor, defaulting to 400 for unknown names."""
    return _DEFAULT_MIN_BARS.get(str(timeframe).strip().upper(), 400)


def _strip_broker_suffix(symbol: str) -> str:
    """Reduce a broker symbol to its canonical base ("BTCUSD#" -> "BTCUSD")."""
    s = str(symbol).strip().upper()
    for suf in _BROKER_SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
            break
    return s


def _category_of(symbol: str) -> str:
    """Asset category, resolved from the single authoritative registry.

    The broker suffix **must** be stripped first. ``_category_of("BTCUSD#")``
    once returned ``"UNKNOWN"`` because this table only held ``"BTCUSD"`` — so the
    weekend-closure check below treated a 24/7 crypto instrument as an FX pair and
    rejected 1,248 of 4,378 perfectly valid Saturday/Sunday bars, blocking the
    whole fetch. The same suffix bug silently mis-classified every ``#``/``.i#``
    broker symbol.
    """
    raw = str(symbol).strip().upper()
    base = _strip_broker_suffix(raw)
    # Try the EXACT name first: broker aliases legitimately end in "#" / ".i#"
    # ("GER40Cash#", "SILVER.i#"), so stripping before lookup destroys the key.
    # Only fall back to the stripped form for a suffix the registry does not know
    # ("BTCUSD.pro" on another broker).
    try:
        from jarvis.data.symbol_registry import asset_class_of
    except Exception:  # pragma: no cover - registry import must never break a fetch
        asset_class_of = None

    for candidate in dict.fromkeys([raw, base]):
        if candidate in _CATEGORY:
            return _CATEGORY[candidate]
        if asset_class_of is not None:
            asset_class = asset_class_of(candidate)
            if asset_class is not None:
                return _REGISTRY_CATEGORY.get(asset_class, "UNKNOWN")

    if len(base) == 6 and base.isalpha():
        return "FX_MAJOR" if base.endswith("USD") or base.startswith("USD") else "FX_CROSS"
    return "UNKNOWN"


_MT5_INITIALIZED = False


def ensure_mt5_connection() -> bool:
    """Initialise the MT5 terminal once per process. Returns True if usable.

    Without this, a metadata query (`build_symbol_meta`) would silently see
    ``symbol_info() is None`` for every symbol simply because nobody had called
    ``initialize()`` yet — which looks identical to "broker has no such symbol"
    and is very easy to misdiagnose.
    """
    global _MT5_INITIALIZED
    if not MT5_AVAILABLE:
        return False
    if _MT5_INITIALIZED:
        return True
    try:
        if mt5.initialize():
            _MT5_INITIALIZED = True
            return True
    except Exception as exc:  # pragma: no cover
        logger.error(f"MT5 initialize() raised: {exc}")
    return False


def resolve_broker_symbol(symbol: str) -> str:
    """Map an internal symbol name to the broker's tradable name."""
    if not ensure_mt5_connection():
        return _BROKER_FALLBACK.get(symbol, symbol)
    info = mt5.symbol_info(symbol)
    if info is not None:
        return symbol
    fb = _BROKER_FALLBACK.get(symbol)
    if fb and mt5.symbol_info(fb) is not None:
        return fb
    # Last resort: scan for a name containing the symbol.
    for s in (mt5.symbols_get() or []):
        if symbol.upper() in s.name.upper():
            return s.name
    return symbol


def build_symbol_meta(symbol: str) -> SymbolMeta:
    """Read pricing metadata straight from the broker (authoritative)."""
    broker = resolve_broker_symbol(symbol)
    category = _category_of(symbol)

    if not ensure_mt5_connection():
        raise MT5UnavailableError(
            f"Cannot build SymbolMeta for {symbol}: MetaTrader5 unavailable."
        )

    info = mt5.symbol_info(broker)
    if info is None:
        raise MT5UnavailableError(f"Broker has no symbol info for {symbol} ({broker}).")

    mt5.symbol_select(broker, True)
    digits = int(info.digits)
    point = float(info.point)
    # A pip is 10 points on 5/3-digit quotes, 1 point on 4/2-digit quotes.
    pip_size = point * 10 if digits in (3, 5) else point
    tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
    if tick_value <= 0.0:
        # Derive a usable estimate when the broker reports nothing.
        contract = float(getattr(info, "trade_contract_size", 0.0) or 100_000.0)
        tick_value = contract * point
    contract_size = float(getattr(info, "trade_contract_size", 0.0) or 0.0)

    return SymbolMeta(
        name=symbol,
        broker_symbol=broker,
        digits=digits,
        point=point,
        pip_size=pip_size,
        tick_value=tick_value,
        contract_size=contract_size,
        category=category,
        typical_spread_pips=max(0.1, float(info.spread) * point / pip_size) if pip_size > 0 else 1.0,
        min_lot=float(getattr(info, "volume_min", 0.01) or 0.01),
        lot_step=float(getattr(info, "volume_step", 0.01) or 0.01),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Structural validation — the gate that synthetic data cannot pass.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class DataQuality:
    """Result of validating a series as real market data."""

    ok: bool
    rows: int
    weekend_bars: int
    nonuniform_gaps: int
    zero_spread_rows: int
    issues: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def validate_real_market_data(df: pd.DataFrame, symbol: str,
                              category: Optional[str] = None,
                              bar_minutes: int = 60) -> DataQuality:
    """Reject series that could not have come from a live market.

    Checks are deliberately structural — they test properties a *generator* does
    not reproduce, rather than trying to fingerprint known fake datasets.

    ``category`` matters: crypto trades 24/7 so weekend bars are legitimate there,
    whereas FX/metals/indices/energy are closed at weekends. Getting this wrong
    in either direction is a false positive that blocks real data.

    ``bar_minutes`` is the timeframe's bar length. It generalises check 2 below,
    which was hardcoded to H1 and therefore rejected *every* M5/M15 bar as
    "not aligned to the hour" — the check has to know which grid it is testing.
    """
    issues: List[str] = []
    if df is None or df.empty:
        return DataQuality(False, 0, 0, 0, 0, ["empty series"])
    if "time" not in df.columns:
        return DataQuality(False, len(df), 0, 0, 0, ["missing 'time' column"])

    cat = category or _category_of(symbol)
    trades_weekends = cat == "CRYPTO"

    ts = pd.to_datetime(df["time"], utc=True)
    rows = len(df)

    # 1. Time must be monotonic and unique.
    if not ts.is_monotonic_increasing:
        issues.append("timestamps not monotonically increasing")
    if ts.duplicated().any():
        issues.append(f"{int(ts.duplicated().sum())} duplicate timestamps")

    # 2. Bars must align to their own timeframe grid. A generator using `now()`
    #    as an anchor produces fractional seconds; a real feed does not.
    #    Count BARS (not conditions) so the ratio stays meaningful.
    #
    #    Generalised from the original H1-only form: for M15 the expected minute
    #    set is {0,15,30,45}, for M5 {0,5,...,55}. Sub-daily grids also pin
    #    seconds/microseconds to zero; D1 bars carry no meaningful intraday
    #    offset at all, so only the sub-daily case is checked.
    misaligned = (ts.dt.second != 0) | (ts.dt.microsecond != 0)
    if 0 < bar_minutes < 1440:
        if bar_minutes < 60:
            misaligned = misaligned | (ts.dt.minute % bar_minutes != 0)
        else:
            hours = bar_minutes // 60
            misaligned = misaligned | (ts.dt.minute != 0)
            if hours < 24:
                misaligned = misaligned | (ts.dt.hour % hours != 0)
    frac = int(misaligned.sum())
    if frac > rows * 0.02:
        issues.append(
            f"{frac}/{rows} bars not aligned to the {bar_minutes}-minute grid "
            f"(fractional or off-grid timestamps)"
        )

    # 3. Non-crypto markets close at weekends. A 24/7 grid on an FX symbol is a
    #    generator artefact. Crypto is exempt (it genuinely trades 24/7).
    weekend = int((ts.dt.dayofweek >= 5).sum())
    if not trades_weekends and weekend > rows * 0.25:
        issues.append(f"{weekend}/{rows} weekend bars — markets are closed Sat/Sun")

    # 4. Bar spacing should be mostly uniform with weekend gaps.
    gaps = ts.diff().dt.total_seconds().dropna()
    irregular = 0
    if len(gaps) > 10:
        mode_gap = gaps.mode()
        if len(mode_gap):
            irregular = int((gaps != mode_gap.iloc[0]).sum())
            # Crypto trades continuously so irregular gaps are rarer; non-crypto
            # carries weekend/holiday gaps and can be looser.
            tolerance = 0.10 if trades_weekends else 0.25
            if irregular > len(gaps) * tolerance:
                issues.append(f"{irregular}/{len(gaps)} irregular inter-bar gaps")

    # 5. OHLC integrity.
    for c in ("open", "high", "low", "close"):
        if c not in df.columns:
            issues.append(f"missing column '{c}'")
    if all(c in df.columns for c in ("open", "high", "low", "close")):
        bad_hl = int((df["high"] < df["low"]).sum())
        if bad_hl:
            issues.append(f"{bad_hl} bars with high < low")
        nonpos = int((df[["open", "high", "low", "close"]] <= 0).any(axis=1).sum())
        if nonpos:
            issues.append(f"{nonpos} bars with non-positive price")

    # 6. Spread must actually vary — a constant spread is a generator tell.
    zero_spread = 0
    if "spread" in df.columns:
        zero_spread = int((df["spread"] == 0).sum())
        if zero_spread == rows and rows > 50:
            issues.append("spread identically zero for every bar")

    # 7. Tick volume must vary.
    if "tick_volume" in df.columns and rows > 50:
        if df["tick_volume"].nunique() <= 2:
            issues.append("tick_volume effectively constant")

    ok = len(issues) == 0
    if not ok:
        logger.error(f"[{symbol}] REAL-DATA VALIDATION FAILED: {issues}")
    return DataQuality(ok, rows, weekend, irregular, zero_spread, issues)


# ─────────────────────────────────────────────────────────────────────────────
# Fetcher
# ─────────────────────────────────────────────────────────────────────────────
class MT5HistoryFetcher:
    """Fetch and cache real H1 history from the live MT5 terminal."""

    def __init__(self, cache_root: Optional[str] = None, allow_synthetic: bool = False):
        self.cache_root = cache_root or os.path.join(DATA_DIR, "market", "real")
        # Explicit opt-in only. Default False so nobody accidentally backtests
        # on fabricated prices again.
        self.allow_synthetic = allow_synthetic
        self._connected = False

    # ── connection ──────────────────────────────────────────────────────────
    def connect(self) -> None:
        if not MT5_AVAILABLE:
            raise MT5UnavailableError(
                "MetaTrader5 python package is not installed. "
                "Install it with `pip install MetaTrader5` and run the MT5 terminal."
            )
        if not self._connected:
            if not mt5.initialize():
                raise MT5UnavailableError(f"mt5.initialize() failed: {mt5.last_error()}")
            self._connected = True
            ti = mt5.terminal_info()
            ai = mt5.account_info()
            logger.info(
                f"MT5 connected | terminal={getattr(ti,'name','?')} "
                f"build={getattr(ti,'build','?')} | account={getattr(ai,'login','?')} "
                f"({getattr(ai,'server','?')})"
            )

    def shutdown(self) -> None:
        if self._connected and MT5_AVAILABLE:
            try:
                mt5.shutdown()
            except Exception:
                pass
            self._connected = False

    def __enter__(self) -> "MT5HistoryFetcher":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()

    # ── fetch ───────────────────────────────────────────────────────────────
    def fetch_bars(
        self,
        symbol: str,
        days: int = 95,
        timeframe: str = "H1",
        min_bars: int = 400,
        end: Optional[datetime] = None,
    ) -> Tuple[pd.DataFrame, SymbolMeta, DataQuality]:
        """Return real bars for ``symbol`` on ``timeframe`` over the last ``days``.

        ``timeframe`` accepts any key of :data:`TIMEFRAME_MINUTES`. The broker's
        history depth varies by timeframe — M5/M15/H1/H4/D1 typically reach back
        the full requested window, while M1 is commonly capped far shorter — so a
        caller asking for more than the broker holds gets the shorter series
        rather than an error, provided it still clears ``min_bars``.
        """
        self.connect()
        tf = str(timeframe).strip().upper()
        bar_minutes = TIMEFRAME_MINUTES[tf]
        mt5_tf = _mt5_timeframe(tf)
        broker = resolve_broker_symbol(symbol)
        meta = build_symbol_meta(symbol)

        end_dt = end or datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=days)
        mt5.symbol_select(broker, True)

        def _range(d: int):
            return mt5.copy_rates_range(broker, mt5_tf, end_dt - timedelta(days=d), end_dt)

        # Brokers hold less history on fine timeframes than on coarse ones, and
        # MT5 answers a range that reaches past the stored history with an
        # outright error ("Invalid params") rather than a partial series. Since
        # that failure is monotone in the window length, binary-search the
        # largest window the broker will actually serve instead of guessing.
        rates = _range(days)
        achieved_days = days
        if rates is None or len(rates) == 0:
            lo, hi = 1, days - 1
            rates, achieved_days = None, 0
            while lo <= hi:
                mid = (lo + hi) // 2
                probe = _range(mid)
                if probe is not None and len(probe) > 0:
                    rates, achieved_days = probe, mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            if rates is None:
                # Some servers reject range queries for freshly-selected symbols;
                # a positional pull is capped at 50 000 bars (beyond that MT5
                # returns "Invalid params"), so never ask for more.
                rates = mt5.copy_rates_from_pos(broker, mt5_tf, 0, 50000)
                achieved_days = days
        if rates is None or len(rates) == 0:
            raise MT5UnavailableError(
                f"No {tf} data returned for {symbol} ({broker}) over {days} days. "
                f"last_error={mt5.last_error()}"
            )
        if achieved_days < days:
            logger.warning(
                f"[{symbol}] {tf}: broker serves only ~{achieved_days}d of history "
                f"(requested {days}d). Proceeding with the shorter real series."
            )

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        lo = pd.Timestamp(start_dt).tz_convert("UTC") if pd.Timestamp(start_dt).tzinfo else pd.Timestamp(start_dt, tz="UTC")
        hi = pd.Timestamp(end_dt).tz_convert("UTC") if pd.Timestamp(end_dt).tzinfo else pd.Timestamp(end_dt, tz="UTC")
        df = df[(df["time"] >= lo) & (df["time"] <= hi)].copy()

        # Normalise the column set so downstream code has one schema.
        for col in ("tick_volume", "real_volume", "spread", "volume"):
            if col not in df.columns:
                df[col] = 0
        df = df[["time", "open", "high", "low", "close",
                 "tick_volume", "spread", "real_volume"]].sort_values("time").reset_index(drop=True)

        quality = validate_real_market_data(df, symbol, category=meta.category,
                                            bar_minutes=bar_minutes)
        if not quality.ok and not self.allow_synthetic:
            raise SyntheticDataError(
                f"Fetched data for {symbol} failed real-market validation: "
                f"{'; '.join(quality.issues)}"
            )
        if len(df) < min_bars:
            raise MT5UnavailableError(
                f"Only {len(df)} {tf} bars for {symbol}; need >= {min_bars}. "
                f"Broker history may be limited — widen the range or reduce min_bars."
            )
        logger.info(
            f"[{symbol}] fetched {len(df)} real {tf} bars "
            f"({df['time'].iloc[0]} -> {df['time'].iloc[-1]}), quality ok={quality.ok}"
        )
        return df, meta, quality

    def fetch_h1(
        self,
        symbol: str,
        days: int = 95,
        min_bars: int = 400,
        end: Optional[datetime] = None,
    ) -> Tuple[pd.DataFrame, SymbolMeta, DataQuality]:
        """Return real H1 bars for ``symbol`` over the last ``days`` days."""
        return self.fetch_bars(symbol, days=days, timeframe="H1",
                               min_bars=min_bars, end=end)

    # ── cache ───────────────────────────────────────────────────────────────
    @staticmethod
    def canonical_name(symbol: str) -> str:
        """Filesystem-safe canonical name for a broker symbol.

        ``BTCUSD#`` must cache under ``BTCUSD/``. Using the raw broker symbol put
        the ``#`` in the directory name (``data/market/real/BTCUSD#/``), so every
        downstream tool — which looks up ``<canonical>/`` — silently found no
        data and reported "0 bars scanned" instead of an error.
        """
        try:
            from jarvis.data.symbol_registry import is_registered, resolve

            if is_registered(symbol):
                return resolve(symbol).canonical
        except Exception:  # pragma: no cover
            pass
        return _strip_broker_suffix(symbol)

    def cache_path(self, symbol: str, days: int, timeframe: str = "H1") -> str:
        name = self.canonical_name(symbol)
        tf = str(timeframe).strip().upper()
        d = os.path.join(self.cache_root, name)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{name}_{tf}_{days}d.parquet")

    def fetch_and_cache(self, symbol: str, days: int = 95,
                        timeframe: str = "H1",
                        min_bars: Optional[int] = None) -> Dict[str, Any]:
        """Fetch, validate, persist to parquet, and write a provenance manifest.

        ``min_bars`` defaults to the timeframe-aware floor from
        :func:`default_min_bars` — a flat 400 would reject perfectly good D1
        data, where 183 calendar days is only ~130 trading bars.
        """
        tf = str(timeframe).strip().upper()
        if min_bars is None:
            min_bars = default_min_bars(tf)
        df, meta, quality = self.fetch_bars(symbol, days=days, timeframe=tf,
                                            min_bars=min_bars)
        path = self.cache_path(symbol, days, tf)
        df.to_parquet(path, index=False)

        manifest = {
            "symbol": self.canonical_name(symbol),
            "requested_symbol": symbol,
            "broker_symbol": meta.broker_symbol,
            "timeframe": tf,
            "days": days,
            "rows": int(len(df)),
            "start": str(df["time"].iloc[0]),
            "end": str(df["time"].iloc[-1]),
            "span_days": round(
                (df["time"].iloc[-1] - df["time"].iloc[0]).total_seconds() / 86400, 2
            ),
            "full_window": bool(
                (df["time"].iloc[0] <= pd.Timestamp(df["time"].iloc[-1])
                 - pd.Timedelta(days=days * 0.95))
            ),
            "provenance": "MT5_TERMINAL_REAL",
            "synthetic": False,
            "quality": quality.as_dict(),
            "meta": asdict(meta),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(path.replace(".parquet", ".manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        return manifest


__all__ = [
    "MT5HistoryFetcher",
    "SymbolMeta",
    "DataQuality",
    "SyntheticDataError",
    "MT5UnavailableError",
    "TIMEFRAME_MINUTES",
    "default_min_bars",
    "resolve_broker_symbol",
    "validate_real_market_data",
    "build_symbol_meta",
    "DEFAULT_UNIVERSE",
]

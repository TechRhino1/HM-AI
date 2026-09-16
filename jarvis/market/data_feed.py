"""
HM Algo 2.0 — Multi-Timeframe Data Feed Engine.
Provides thread-safe, timeout-guarded OHLCV data streaming from MT5 with realistic synthetic fallback generation.
"""
import time

from jarvis.data.broker_symbols import resolve_broker_symbol
from jarvis.data.broker_time import broker_utc_offset
# One bar-duration map for the whole codebase. The provider already owned the
# canonical copy; duplicating it here is how a second source of truth starts.
from jarvis.data.tradingview_provider import TF_SECONDS_MAP
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from typing import Dict, Optional, Any
from jarvis.application.timeout_guard import TimeoutGuard

import threading

logger = logging.getLogger("JARVIS_DataFeed")

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

TF_MAP = {
    "M1": mt5.TIMEFRAME_M1 if (MT5_AVAILABLE and hasattr(mt5, "TIMEFRAME_M1")) else 1,
    "M5": mt5.TIMEFRAME_M5 if (MT5_AVAILABLE and hasattr(mt5, "TIMEFRAME_M5")) else 5,
    "M10": getattr(mt5, "TIMEFRAME_M10", 10) if MT5_AVAILABLE else 10,
    "M15": mt5.TIMEFRAME_M15 if (MT5_AVAILABLE and hasattr(mt5, "TIMEFRAME_M15")) else 15,
    "M30": mt5.TIMEFRAME_M30 if (MT5_AVAILABLE and hasattr(mt5, "TIMEFRAME_M30")) else 30,
    "H1": mt5.TIMEFRAME_H1 if (MT5_AVAILABLE and hasattr(mt5, "TIMEFRAME_H1")) else 16385,
    "H4": mt5.TIMEFRAME_H4 if (MT5_AVAILABLE and hasattr(mt5, "TIMEFRAME_H4")) else 16388,
    "D1": mt5.TIMEFRAME_D1 if (MT5_AVAILABLE and hasattr(mt5, "TIMEFRAME_D1")) else 16408,
}

# ─── Trade-style → timeframe map (single source of truth) ────────────────────
# The three trade styles differ ONLY by the timeframes they request; nothing
# else about them is distinct. This map was previously inlined inside
# ``fetch_multi_timeframe``, which meant the live path and any offline
# backtest could silently drift apart. Both now read it from here.
STYLE_TIMEFRAMES: Dict[str, Dict[str, str]] = {
    "SWING": {"macro": "D1", "context": "H4", "primary": "H1", "setup": "H4", "timing": "M15"},
    "DAY_TRADING": {"macro": "H4", "context": "H1", "primary": "M15", "setup": "H1", "timing": "M5"},
    "SCALP": {"macro": "H1", "context": "M15", "primary": "M5", "setup": "M5", "timing": "M1"},
}

# Accepted spellings for the day-trading style.
_DAY_ALIASES = ("DAY_TRADING", "INTRADAY", "DAY")


def normalise_style(trade_style: Optional[str]) -> str:
    """Canonicalise a trade-style name; unknown/None falls back to SWING."""
    style = (trade_style or "SWING").upper().strip()
    if style in _DAY_ALIASES:
        return "DAY_TRADING"
    return style if style in STYLE_TIMEFRAMES else "SWING"


def style_timeframes(trade_style: Optional[str]) -> Dict[str, str]:
    """Role → timeframe map for a trade style (macro/context/primary/setup/timing)."""
    return dict(STYLE_TIMEFRAMES[normalise_style(trade_style)])


def style_timeframe_set(trade_style: Optional[str]) -> set:
    """The distinct timeframes a style needs — i.e. what it must have data for."""
    return set(style_timeframes(trade_style).values())


# ─── Bar freshness ──────────────────────────────────────────────────────────
# A bar is "fresh" while the newest bar's open is no older than this many bar
# durations. It cannot be 1: with `include_current_bar=False` the newest bar
# returned is the last CLOSED one, so its open is already 1-2 durations old and
# a 1x rule would flag every healthy frame. 2.5 leaves room for the in-progress
# bar plus exchange slack without accepting a genuinely stalled feed.
_FRESH_BAR_TOLERANCE = 2.5

FRESH = "FRESH"
STALE = "STALE"
MARKET_CLOSED = "MARKET_CLOSED"
FRESHNESS_UNKNOWN = "UNKNOWN"


def _is_weekend_gap(now_utc: float) -> bool:
    """True when the clock sits in the weekly close (forex/metals).

    Without this, the 48h gap a Sunday frame legitimately has would be reported
    as a stalled feed. The market is closed from ~21:00 UTC Friday to ~21:00 UTC
    Sunday, so a large age there is expected rather than a defect.
    """
    moment = datetime.fromtimestamp(now_utc, tz=timezone.utc)
    weekday, hour = moment.weekday(), moment.hour          # Mon=0, Sun=6
    if weekday == 5:                                       # Saturday
        return True
    if weekday == 4 and hour >= 21:                        # Friday, after close
        return True
    if weekday == 6 and hour < 21:                         # Sunday, before open
        return True
    return False


def classify_bar_freshness(
    last_bar_epoch: Optional[float],
    timeframe: str,
    *,
    include_current_bar: bool = False,
    now_utc: Optional[float] = None,
    offset_sec: int = 0,
) -> tuple:
    """Verdict and age for the newest bar, in the CLOCK THE BARS ARE STAMPED IN.

    `copy_rates_from_pos` stamps bar opens on the BROKER's clock, exactly like
    tick times, so subtracting them from the real UTC clock under-reports the
    age by the broker offset (2-3h for XM) and would hide a stalled feed. Pass
    the offset from `jarvis.data.broker_time`; `offset_sec=0` is the old
    (wrong, but visible) behaviour.

    Returns `(verdict, age_sec)` where age may be negative if the bar is stamped
    slightly ahead of now (clock skew). `verdict` is one of FRESH, STALE,
    MARKET_CLOSED, UNKNOWN - UNKNOWN when the timeframe is unrecognised, so an
    unverifiable frame is never silently reported as fresh.
    """
    bar_sec = TF_SECONDS_MAP.get(str(timeframe or "").upper())
    if not bar_sec or not last_bar_epoch:
        return FRESHNESS_UNKNOWN, None

    now = time.time() if now_utc is None else float(now_utc)
    age = (now + float(offset_sec)) - float(last_bar_epoch)

    # A small negative age is normal skew (the newest bar can open "now").
    if age < 0:
        return (FRESH if -age <= bar_sec else FRESHNESS_UNKNOWN), age

    if age <= bar_sec * _FRESH_BAR_TOLERANCE:
        return FRESH, age
    if _is_weekend_gap(now):
        return MARKET_CLOSED, age
    return STALE, age


def first_stale_frame(mtf_data) -> tuple:
    """First ``(role, age_sec)`` whose frame is STALE, else ``(None, 0.0)``.

    Reads the verdict stamped by `fetch_rates` rather than re-deriving it - a
    second age calculation would be a second thing to keep in step with the
    broker's clock. Used by every decision path so "do not act on a stalled
    feed" cannot be enforced in one entry point and forgotten in another.
    """
    for role, frame in (mtf_data or {}).items():
        attrs = getattr(frame, "attrs", None) or {}
        if attrs.get("freshness") == STALE:
            return role, float(attrs.get("bar_age_sec") or 0.0)
    return None, 0.0


class DataFeedEngine:
    _mt5_fetch_lock = threading.Lock()

    def __init__(self, mt5_client: Any = None, timeout_sec: float = 3.0):
        self.mt5_client = mt5_client
        self.timeout_sec = timeout_sec
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl_sec = 8.0
        # One warning per symbol/timeframe: a stalled feed repeats on every poll
        # and would otherwise bury the rest of the log.
        self._stale_warned: set = set()

    def fetch_rates(self, symbol: str, timeframe: str = "H1", num_bars: int = 300, include_current_bar: bool = False) -> pd.DataFrame:
        cache_key = f"{symbol}_{timeframe}_{num_bars}_{include_current_bar}"
        now = time.time()
        if cache_key in self._cache:
            entry = self._cache[cache_key]
            if now - entry["timestamp"] < self._cache_ttl_sec:
                return entry["df"]

        def _fetch():
            if not MT5_AVAILABLE or self.mt5_client is None or getattr(self.mt5_client, "mode", "dry_run") == "dry_run":
                df = self._generate_realistic_rates(symbol, timeframe, num_bars)
                df.attrs["data_source"] = "SYNTHETIC_FALLBACK"
                # Fabricated bars have no age to verify. Marked UNKNOWN rather
                # than FRESH so nothing downstream can mistake a synthetic
                # frame for a verified one.
                df.attrs["freshness"] = FRESHNESS_UNKNOWN
                return df

            resolved_sym = self.mt5_client.resolve_symbol_name(symbol) if hasattr(self.mt5_client, "resolve_symbol_name") else symbol
            mt5_tf = TF_MAP.get(timeframe, 16385)
            start_pos = 0 if include_current_bar else 1
            with DataFeedEngine._mt5_fetch_lock:
                _broker_sym = resolve_broker_symbol(resolved_sym) or resolved_sym
                rates = mt5.copy_rates_from_pos(_broker_sym, mt5_tf, start_pos, num_bars)
                if rates is None or len(rates) == 0:
                    # Fallback to pos 0 if start_pos returns empty
                    rates = mt5.copy_rates_from_pos(_broker_sym, mt5_tf, 0, num_bars)
            if rates is None or len(rates) == 0:
                logger.warning(f"MT5 returned 0 rates for {symbol} ({timeframe}). Falling back to synthetic rates.")
                df = self._generate_realistic_rates(symbol, timeframe, num_bars)
                df.attrs["data_source"] = "SYNTHETIC_FALLBACK"
                # Fabricated bars have no age to verify. Marked UNKNOWN rather
                # than FRESH so nothing downstream can mistake a synthetic
                # frame for a verified one.
                df.attrs["freshness"] = FRESHNESS_UNKNOWN
                return df

            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            df.rename(columns={"tick_volume": "volume"}, inplace=True)
            res_df = df[["time", "open", "high", "low", "close", "volume"]].copy()
            res_df.attrs["data_source"] = "LIVE_MT5"

            # Freshness. `rates["time"]` is the broker's clock, so the age must
            # be measured against now-in-broker-time; against the raw UTC clock
            # it under-reports by the offset (2-3h for XM) and a stalled feed
            # looks healthy. `_broker_sym` is the symbol the broker actually
            # answered for, which is the only one whose tick offset is valid.
            last_bar_epoch = float(rates["time"][-1])
            offset = broker_utc_offset(mt5_module=mt5, symbols=[_broker_sym])
            verdict, age = classify_bar_freshness(
                last_bar_epoch,
                timeframe,
                include_current_bar=include_current_bar,
                offset_sec=offset,
            )
            res_df.attrs["freshness"] = verdict
            res_df.attrs["bar_age_sec"] = None if age is None else round(age, 1)

            if verdict == STALE and cache_key not in self._stale_warned:
                self._stale_warned.add(cache_key)
                bar_sec = TF_SECONDS_MAP.get(str(timeframe).upper(), 1)
                logger.warning(
                    "Stale candles for %s %s: newest bar is %.0fs old (%.1f bar durations) "
                    "while the market is open - decisions should not be taken on this frame.",
                    symbol, timeframe, age, age / max(1, bar_sec),
                )
            return res_df


        def _fallback_gen():
            df = self._generate_realistic_rates(symbol, timeframe, num_bars)
            df.attrs["data_source"] = "SYNTHETIC_FALLBACK"
            df.attrs["freshness"] = FRESHNESS_UNKNOWN
            return df

        df_result = TimeoutGuard.run_sync(
            _fetch,
            timeout_sec=self.timeout_sec,
            default=_fallback_gen,
            task_name=f"DataFeed_fetch_{symbol}_{timeframe}"
        )

        if "data_source" not in df_result.attrs:
            df_result.attrs["data_source"] = "SYNTHETIC_FALLBACK"
        if "freshness" not in df_result.attrs:
            # A frame of unknown origin must not read as verified.
            df_result.attrs["freshness"] = FRESHNESS_UNKNOWN

        self._cache[cache_key] = {"df": df_result, "timestamp": now}
        return df_result

    def fetch_multi_timeframe(
        self,
        symbol: str,
        trade_style: str = "SWING",
        timeframes: Optional[Dict[str, str]] = None,
        num_bars: int = 250
    ) -> Dict[str, pd.DataFrame]:
        """
        Fetches multi-timeframe OHLCV rates mapped by role.
        Configures timeframes dynamically based on trade_style:
        - SWING: macro=D1, context=H4, primary=H1, setup=H4, timing=M15
        - DAY_TRADING / INTRADAY / DAY: macro=H4, context=H1, primary=M15, setup=H1, timing=M5
        - SCALP: macro=H1, context=M15, primary=M5, setup=M5, timing=M1
        """
        if timeframes is None:
            timeframes = style_timeframes(trade_style)

        result = {}
        for role, tf in timeframes.items():
            result[role] = self.fetch_rates(symbol, timeframe=tf, num_bars=num_bars)
        return result

    def _generate_realistic_rates(self, symbol: str, timeframe: str, num_bars: int) -> pd.DataFrame:
        """Generates realistic institutional market price series with trend cycles, liquidity sweeps, and volatility clusters."""
        import zlib
        seed = zlib.crc32(f"{symbol.upper()}_{timeframe}".encode("utf-8"))
        np.random.seed(seed)
        
        u_sym = symbol.upper()
        if any(k in u_sym for k in ["XAU", "GOLD"]):
            base_price = 4380.0
            vol = 0.0025
        elif "BTC" in u_sym:
            base_price = 77000.0
            vol = 0.0050
        elif "ETH" in u_sym:
            base_price = 3400.0
            vol = 0.0055
        elif "SOL" in u_sym:
            base_price = 185.0
            vol = 0.0065
        elif any(k in u_sym for k in ["US500", "SPX"]):
            base_price = 5850.0
            vol = 0.0018
        elif any(k in u_sym for k in ["NAS100", "USTEC", "NDX"]):
            base_price = 20500.0
            vol = 0.0022
        elif any(k in u_sym for k in ["US30", "DJ"]):
            base_price = 42500.0
            vol = 0.0016
        elif any(k in u_sym for k in ["WTI", "OIL", "CRUDE"]):
            base_price = 76.50
            vol = 0.0035
        elif "EURUSD" in u_sym:
            base_price = 1.1580
            vol = 0.0012
        elif "GBPUSD" in u_sym:
            base_price = 1.3480
            vol = 0.0014
        elif "USDJPY" in u_sym:
            base_price = 158.50
            vol = 0.0015
        elif "EURJPY" in u_sym:
            base_price = 172.00
            vol = 0.0015
        elif "GBPJPY" in u_sym:
            base_price = 205.00
            vol = 0.0016
        elif "AUDUSD" in u_sym:
            base_price = 0.6650
            vol = 0.0013
        elif "NZDUSD" in u_sym:
            base_price = 0.6050
            vol = 0.0013
        elif "USDCHF" in u_sym:
            base_price = 0.8850
            vol = 0.0012
        elif "USDCAD" in u_sym:
            base_price = 1.3750
            vol = 0.0012
        else:
            base_price = 100.0
            vol = 0.0020

        # Generate regime cycles: Bullish expansion -> Consolidation -> Pullback -> Breakout
        returns = []
        regimes = [0.0006, 0.0001, -0.0005, 0.0008, 0.0002, -0.0004]
        reg_idx = 0
        cycle_len = 200

        for i in range(num_bars):
            if i > 0 and i % cycle_len == 0:
                reg_idx = (reg_idx + 1) % len(regimes)
            drift = regimes[reg_idx]
            noise = np.random.normal(drift, vol * 0.6)
            returns.append(noise)


        returns = np.array(returns)
        prices = base_price * np.exp(np.cumsum(returns))

        freq_map = {"M1": "1min", "M5": "5min", "M10": "10min", "M15": "15min", "M30": "30min", "H1": "1h", "H4": "4h", "D1": "1D"}
        freq = freq_map.get(timeframe, "1h")
        import datetime
        dates = pd.date_range(end=pd.Timestamp.now(tz=datetime.timezone.utc).tz_localize(None), periods=num_bars, freq=freq)

        closes = prices
        opens = np.roll(closes, 1)
        opens[0] = base_price

        bodies = np.abs(closes - opens)
        wick_upper = bodies * np.abs(np.random.normal(0.15, 0.10, num_bars)) + (vol * prices * 0.15)
        wick_lower = bodies * np.abs(np.random.normal(0.15, 0.10, num_bars)) + (vol * prices * 0.15)


        highs = np.maximum(opens, closes) + wick_upper
        lows = np.minimum(opens, closes) - wick_lower
        volumes = np.random.randint(800, 4500, num_bars).astype(float)

        return pd.DataFrame({
            "time": dates,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes
        })



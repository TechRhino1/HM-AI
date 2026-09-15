"""Measured base rates for the trade gate, derived from real MT5 data.

Why this module exists
----------------------
The gate probability produced by
:meth:`jarvis.intelligence.decision_engine.DecisionEngine._compute_blended_probability`
is **100% hand-authored and 0% fitted**: it is ``0.45 x`` a hand-typed six-bin
reliability table (``confidence.py``) blended with ``0.55 x`` a hand-authored
linear score clipped to [0.35, 0.88] (``online_ml_predictor.py``). Nothing in it
is estimated from outcomes, and there are no persisted weights.

Refitting an isotonic map on honest, cost-bearing MT5 data produced
**0 / 20 skillful symbols**. The decisive evidence is the direction of movement:
mean ``|AUC - 0.5|`` *shrank* from 0.0353 to 0.0211 as the sample doubled, with
tails collapsing (GBPJPY 0.584 -> 0.480, NZDUSD 0.417 -> 0.496). Real signal
persists or sharpens with more data; noise regresses toward chance. The score is
noise.

So this module does **not** try to "fix" the gate by fitting it — that was
measured and rejected. It is the measured *counterpart*: for every decision it
reports what this symbol has actually done over the last 365 days of real MT5
bars at the same target geometry. The gap between what the gate claims and what
was measured is the number that matters, and it is now logged per trade.

Source of truth
---------------
``reports/trade_quality_audit_365d.json`` — produced by
``tools/audit_trade_quality.py --tf H1 --window 365`` over MT5 terminal bars
(provenance ``MT5_TERMINAL_REAL``, ``synthetic: false``, 0 quality issues).
Regenerate it with ``tools/fetch_real_data.py --days 365`` then the audit tool;
this module picks up the new file automatically via mtime.

Design rules
------------
* **Never invent a flattering number.** An unknown symbol falls back to the
  universe *median* and is flagged ``proxy=True``, so a caller can never mistake
  a borrowed rate for a measured one.
* **Never raise.** Every entry point is exception-guarded; observability must
  not be able to take the trading loop down.
* **Read-only.** This module feeds logs and telemetry. It does not gate, resize
  or veto.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional

logger = logging.getLogger("JARVIS_HonestBaseRates")

# Repo-relative default; overridable for tests.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_REPORT_PATH = os.path.join(_REPO_ROOT, "reports", "trade_quality_audit_365d.json")

# Report style key -> canonical (style, timeframe). Must match the keys written
# by tools/audit_trade_quality.py.
_STYLE_KEYS: Dict[str, str] = {
    "SWING": "SWING(H1)",
    "DAY_TRADING": "DAY_TRADING(M15)",
    "SCALP": "SCALP(M5)",
}

# Metric block inside each symbol. The audit reports several target widths;
# 1.5R is the system's configured first target (partial_tp_r_multiple = 1.5).
_METRIC_BLOCK = "tp1.5_costs"

# Broker names -> audit-report names. Runtime symbols arrive as e.g. "EURUSD#",
# "GOLD.i#", "US30.cash#", so normalisation strips the broker decoration first.
_ALIASES: Dict[str, str] = {
    "GOLD": "XAUUSD",
    "XAUUSD": "XAUUSD",
    "GC": "XAUUSD",
    "SILVER": "XAGUSD",
    "XAGUSD": "XAGUSD",
    "SI": "XAGUSD",
    "OIL": "WTI",
    "USOIL": "WTI",
    "UKOIL": "WTI",
    "WTI": "WTI",
    "CRUDEOIL": "WTI",
    "DE30": "GER40",
    "GER30": "GER40",
    "GER40": "GER40",
    "DAX": "GER40",
    "US30": "US30",
    "DJI": "US30",
    "WS30": "US30",
    "US100": "NAS100",
    "NAS100": "NAS100",
    "USTEC": "NAS100",
    "NDX": "NAS100",
    "US500": "US500",
    "SPX": "US500",
    "SPX500": "US500",
    "UK100": "UK100",
    "FTSE": "UK100",
    "BTC": "BTCUSD",
    "BTCUSD": "BTCUSD",
    "ETH": "ETHUSD",
    "ETHUSD": "ETHUSD",
    "SOL": "SOLUSD",
    "SOLUSD": "SOLUSD",
}

_lock = threading.Lock()
_cache: Dict[str, Any] = {"path": None, "mtime": None, "data": None, "median": None}


@dataclass(frozen=True)
class HonestBaseRate:
    """What this symbol actually did, measured on real MT5 bars."""

    symbol: str
    style: str
    window_days: int
    n: int
    win_rate: float
    profit_factor: float
    expectancy_r: float
    break_even_wr: float
    proxy: bool = False
    source: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def describe(self) -> str:
        kind = "proxy" if self.proxy else "measured"
        return (
            f"{self.symbol} {self.style} {kind} {self.window_days}d n={self.n} "
            f"wr={self.win_rate:.1%} PF={self.profit_factor:.3f} "
            f"E={self.expectancy_r:+.4f}R (break-even {self.break_even_wr:.1%})"
        )


def _normalise_symbol(symbol: str) -> str:
    """Strip broker decoration: 'GOLD.i#' -> 'GOLD', 'EURUSD#' -> 'EURUSD'."""
    if not symbol:
        return ""
    s = str(symbol).strip().upper()
    # Drop the broker trailing marker and any venue suffix after a dot or colon.
    for sep in ("#", ".", ":", "-", "_"):
        if sep in s:
            s = s.split(sep)[0]
    return s


def _style_key(style: Optional[str]) -> str:
    s = str(style or "SWING").strip().upper()
    return _STYLE_KEYS.get(s, _STYLE_KEYS["SWING"])


def _load(path: str = DEFAULT_REPORT_PATH) -> Optional[Dict[str, Any]]:
    """Load and memoise the audit report, invalidating on mtime change."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        if _cache["data"] is not None:
            return _cache["data"]
        logger.debug(f"Honest base rates unavailable: no report at {path}")
        return None

    with _lock:
        if _cache["path"] == path and _cache["mtime"] == mtime and _cache["data"] is not None:
            return _cache["data"]
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:  # noqa: BLE001 - observability must not raise
            logger.warning(f"Honest base rates: failed to read {path}: {exc}")
            return _cache["data"]

        median = _compute_median(data)
        _cache.update(path=path, mtime=mtime, data=data, median=median)
        return data


def _compute_median(data: Dict[str, Any]) -> Optional[HonestBaseRate]:
    """Universe median, used as an explicit proxy for unmeasured symbols.

    Deliberately the median, not the mean: the distribution has a long losing
    tail (AUDUSD -0.1951R) and a mean would flatter it.
    """
    rates, pf, exps, ns, windows = [], [], [], [], []
    try:
        for _style, per_symbol in (data.get("styles") or {}).items():
            for _sym, block in (per_symbol or {}).items():
                m = (block or {}).get(_METRIC_BLOCK) or {}
                if m.get("n"):
                    ns.append(int(m["n"]))
                    rates.append(float(m.get("win_rate", 0.0)))
                    pf.append(float(m.get("profit_factor", 0.0)))
                    exps.append(float(m.get("expectancy_r", 0.0)))
                    windows.append(int(data.get("window_days", 0)))
    except Exception:  # noqa: BLE001
        return None
    if not rates:
        return None

    def _med(xs):
        xs = sorted(xs)
        mid = len(xs) // 2
        return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2.0

    be = 0.4
    try:
        first = next(
            (b.get(_METRIC_BLOCK) or {})
            for per_symbol in (data.get("styles") or {}).values()
            for b in (per_symbol or {}).values()
            if (b or {}).get(_METRIC_BLOCK)
        )
        be = float(first.get("break_even_wr", 0.4))
    except Exception:  # noqa: BLE001
        pass

    return HonestBaseRate(
        symbol="UNIVERSE_MEDIAN",
        style="ALL",
        window_days=max(windows) if windows else 0,
        n=int(_med(ns)),
        win_rate=_med(rates),
        profit_factor=_med(pf),
        expectancy_r=_med(exps),
        break_even_wr=be,
        proxy=True,
        source="universe median (symbol not measured)",
    )


def get_base_rate(
    symbol: str,
    style: Optional[str] = "SWING",
    path: str = DEFAULT_REPORT_PATH,
) -> Optional[HonestBaseRate]:
    """Measured base rate for ``symbol``/``style``, or a flagged proxy."""
    data = _load(path)
    if not data:
        return None

    norm = _normalise_symbol(symbol)
    canon = _ALIASES.get(norm, norm)
    skey = _style_key(style)

    try:
        per_symbol = (data.get("styles") or {}).get(skey) or {}
        # Direct hit, then alias hit, then case-insensitive sweep.
        block = per_symbol.get(canon) or per_symbol.get(norm)
        if block is None:
            for key, val in per_symbol.items():
                if str(key).upper() == canon:
                    block = val
                    break
        m = (block or {}).get(_METRIC_BLOCK) or {}
        if m.get("n"):
            return HonestBaseRate(
                symbol=canon,
                style=skey,
                window_days=int(data.get("window_days", 0)),
                n=int(m["n"]),
                win_rate=float(m.get("win_rate", 0.0)),
                profit_factor=float(m.get("profit_factor", 0.0)),
                expectancy_r=float(m.get("expectancy_r", 0.0)),
                break_even_wr=float(m.get("break_even_wr", 0.4)),
                proxy=False,
                source=os.path.basename(path),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Honest base rates: lookup failed for {symbol}: {exc}")

    proxy = _cache.get("median")
    if proxy is not None:
        logger.debug(f"Honest base rates: no measurement for {symbol!r}; using {proxy.describe()}")
    return proxy


def describe_gap(claimed_p: float, measured: Optional[HonestBaseRate], tp: float = 1.5) -> str:
    """One-line comparison of the gate's claim against measurement, in R."""
    if measured is None:
        return f"gate claims p={claimed_p:.2f}; no measured base rate available"
    claimed_r = claimed_p * tp - (1.0 - claimed_p) * 1.0
    measured_r = measured.expectancy_r
    tag = "PROXY" if measured.proxy else "MEASURED"
    return (
        f"gate claims p={claimed_p:.2f} -> {claimed_r:+.3f}R/trade | "
        f"{tag} {measured.symbol} {measured.window_days}d n={measured.n} "
        f"wr={measured.win_rate:.1%} -> {measured_r:+.4f}R/trade | "
        f"overstatement {claimed_r - measured_r:+.3f}R"
    )

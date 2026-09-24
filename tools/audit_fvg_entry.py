"""Track-1 audit: quantify the recurring loss, and test displacement / FVG as an
ALTERNATIVE entry model.

WHY THIS EXISTS
---------------
Two separate questions, one instrument.

Q1  Where does the money actually go? The recorded candidate population loses
    money on almost every cut of the data. Before anyone proposes a fix, the
    loss has to be split into the part that is a FIXED TOLL (spread +
    slippage, paid on every trade regardless of skill) and the part that is
    ENTRY QUALITY (the trade would have lost even at zero cost). Those two have
    completely different fixes: the first is a cost/venue problem, the second is
    a strategy problem.

Q2  Is displacement / fair-value-gap (FVG) entry better than what runs today?
    ``jarvis/market/fair_value_gap.py`` already exists, but it is only used as a
    small confidence boost in ``decision_engine`` (+0.04 / +0.06 on
    ``calibrated_win_p``) and as a stop/target anchor in ``dynamic_levels``. It
    is NOT the entry trigger. So making it one is a genuine alternative entry
    model, and it can be measured against the incumbent on the same bars.

    This module does NOT change production entry logic. It reads recorded
    candidates, replays them, and separately generates FVG entries and replays
    those under the identical exit geometry, cost and slippage model.

INSTRUMENT VALIDATION (do this before believing any number here)
----------------------------------------------------------------
    NO_PROXY='*' <py> tools/audit_fvg_entry.py --mode loss \
        --cache 70c1b7ed90e32bf2 --tp 3.0 --validate

must print ``total R = -196.7``. That is the §J figure the harness is anchored
to. If it does not, the harness is measuring something else and every number it
prints is void. The check is built into ``--mode loss`` as a hard assertion
(``--expect``), so a silently drifting harness fails loudly instead.

UNIT DISCIPLINE
---------------
Cost is expressed in R, never in pips: ``cost_r = spread_pips * pip_size /
risk_dist``. A 2-pip spread is ruinous on a 5-pip stop and irrelevant on a
50-pip stop, so pips cannot be aggregated across trades. Always use
``spread_pips`` from the recorded frame -- NEVER ``spread`` x ``pip_size``;
``spread`` is broker POINTS and the conversion divides by ``point_size`` first
(see ``SignalScanner._spread_for_bar``).

Run:
  PYTHONPATH=. NO_PROXY='*' <py> tools/audit_fvg_entry.py --mode loss --tp 3.0
  PYTHONPATH=. NO_PROXY='*' <py> tools/audit_fvg_entry.py --mode control --tp 3.0
  PYTHONPATH=. NO_PROXY='*' <py> tools/audit_fvg_entry.py --mode sr --tp 3.0
  PYTHONPATH=. NO_PROXY='*' <py> tools/audit_fvg_entry.py --mode fvg --window 365 --part oos
"""
from __future__ import annotations

import argparse
import glob
import math
import os
import pickle
import sys
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from jarvis.backtesting.signal_scan import _point_size  # noqa: E402
from jarvis.backtesting.trade_simulator import BarArrays, Geometry, simulate_trade  # noqa: E402
from jarvis.data import symbol_registry as reg  # noqa: E402

#: The §J anchor. `--mode loss` on cache 70c1b7ed90e32bf2 at tp_r=3.0 must
#: reproduce this; anything else means the instrument has drifted.
ANCHOR_CACHE = "70c1b7ed90e32bf2"
ANCHOR_TP = 3.0
ANCHOR_TOTAL_R = -196.7

#: First bar of the 183d window inside a 365d file -- the two windows are
#: otherwise overlapping and an "out of sample" claim would be false.
OOS_BOUNDARY = pd.Timestamp("2026-03-15", tz="UTC")


# ─────────────────────────────────────────────────────────────────────────────
# data loading
# ─────────────────────────────────────────────────────────────────────────────
def cache_path(cache_id: Optional[str]) -> str:
    pats = (os.path.join(REPO, ".scratch", f"j_scan_cache_{cache_id}.pkl") if cache_id
            else os.path.join(REPO, ".scratch", "j_scan_cache_*.pkl"))
    for p in sorted(glob.glob(pats)):
        try:
            with open(p, "rb") as fh:
                scanned = pickle.load(fh)
            if scanned and all("df" in v and "ex_a" in v for v in scanned.values()):
                return p
        except Exception:
            continue
    raise SystemExit("no scan cache found; run tools/reconcile_spread_calibration.py once")


def load_scanned(cache_id: Optional[str], symbols: Optional[str] = None) -> Dict[str, Any]:
    p = cache_path(cache_id)
    with open(p, "rb") as fh:
        scanned = pickle.load(fh)
    if symbols:
        want = {x.strip().upper() for x in symbols.split(",") if x.strip()}
        scanned = {k: v for k, v in scanned.items() if k in want}
    print(f"[cache] {os.path.basename(p)} ({len(scanned)} symbols: {sorted(scanned)})", flush=True)
    return scanned


def load_parquet(sym: str, window: int) -> Optional[pd.DataFrame]:
    p = os.path.join(REPO, "data", "market", "real", sym, f"{sym}_H1_{window}d.parquet")
    if not os.path.exists(p):
        return None
    df = pd.read_parquet(p).reset_index(drop=True)
    df["time"] = pd.to_datetime(df["time"])
    df["atr"] = _atr(df, 14)
    return df


def _atr(df: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=1).mean()


def spread_pips_for(spec: Any, raw: float, fallback: Optional[float] = None) -> float:
    """Broker POINTS -> pips, the same conversion the scanner uses.

    ``spread`` in the parquet is in points; multiplying it by ``pip_size``
    directly overstates the spread by 10x on 5-digit FX.
    """
    fb = fallback if fallback is not None else float(getattr(spec, "typical_spread_pips", 2.0) or 2.0)
    if raw is None or not np.isfinite(float(raw)):
        return fb
    pip = float(getattr(spec, "pip_size", 0.0001) or 0.0001)
    pt = _point_size(int(getattr(spec, "digits", 5) or 5))
    if pip <= 0 or pt <= 0:
        return fb
    return float(raw) * pt / pip


# ─────────────────────────────────────────────────────────────────────────────
# replay
# ─────────────────────────────────────────────────────────────────────────────
def _sim(sym: str, bars: BarArrays, spec: Any, money: float, side: str,
         entry_idx: int, fill: float, sl: float, tp: float) -> Optional[Any]:
    return simulate_trade(
        symbol=sym, side=side, entry_idx=entry_idx, fill=fill, sl=sl,
        geom=Geometry(tp_r=float(tp)), money_per_unit=money, bars=bars,
        cost_price_equiv=0.0, slippage_price_equiv=0.5 * float(spec.pip_size), spec=spec,
    )


def replay_incumbent(scanned: Dict[str, Any], tp: float = 3.0) -> pd.DataFrame:
    """Replay every recorded EXECUTE candidate, keeping its cost beside its R."""
    rows: List[Dict[str, Any]] = []
    for sym, s in sorted(scanned.items()):
        df, cands = s["df"], s["ex_a"]
        if cands is None or len(cands) == 0:
            continue
        spec = reg.resolve(sym)
        money = reg.get_dollar_risk_per_price_unit(sym, None)
        bars = BarArrays.from_df(df)
        pip, n = float(spec.pip_size), bars.n
        for r in cands.itertuples(index=False):
            i = int(getattr(r, "bar_idx"))
            if i + 1 >= n:
                continue
            side = str(getattr(r, "side", "")).upper()
            if side not in ("BUY", "SELL"):
                continue
            fill, sl = float(getattr(r, "fill")), float(getattr(r, "sl"))
            risk = abs(fill - sl)
            if risk <= 0:
                continue
            out = _sim(sym, bars, spec, money, side, i, fill, sl, tp)
            if out is None:
                continue
            sp = float(getattr(r, "spread_pips", np.nan))
            rows.append({
                "symbol": sym, "side": side, "bar_idx": i,
                "time": pd.Timestamp(getattr(r, "time")),
                "r": float(out.pnl_r), "mfe_r": float(out.mfe_r), "mae_r": float(out.mae_r),
                "result": str(out.result), "bars_held": int(out.bars_held),
                "fill": fill, "sl": sl,
                "risk_dist": risk, "atr": float(getattr(r, "atr", np.nan)),
                "spread_pips": sp,
                "cost_r": (sp * pip / risk) if np.isfinite(sp) else np.nan,
                "slip_r": (0.5 * pip / risk) if str(out.result).endswith("SL") else 0.0,
                "regime": str(getattr(r, "regime", "")),
                "strategy": str(getattr(r, "strategy", "")),
                "zone": str(getattr(r, "zone", "")),
                "score": float(getattr(r, "score", np.nan)),
            })
    d = pd.DataFrame(rows)
    if len(d):
        d["hour"] = pd.to_datetime(d["time"]).dt.hour
        d["session"] = d["hour"].map(
            lambda h: "ASIA" if h < 7 else "LONDON" if h < 12 else "NY_AM" if h < 17 else "NY_PM")
        d["r_ex_cost"] = d["r"] + d["cost_r"].fillna(0.0) + d["slip_r"]
    return d


# ─────────────────────────────────────────────────────────────────────────────
# reporting
# ─────────────────────────────────────────────────────────────────────────────
def _t(x: pd.Series) -> float:
    if len(x) < 2:
        return 0.0
    sd = float(x.std(ddof=1))
    return float(x.mean() / (sd / math.sqrt(len(x)))) if sd > 1e-12 else 0.0


def breakdown(d: pd.DataFrame, key: str, title: Optional[str] = None) -> pd.DataFrame:
    if key not in d.columns:
        return pd.DataFrame()
    g = d.groupby(key)["r"].agg(n="count", total_R="sum", mean_R="mean")
    g["win%"] = 100 * d.groupby(key)["r"].apply(lambda x: (x > 0).mean())
    g["t"] = d.groupby(key)["r"].apply(_t)
    g["mean_R_ex_cost"] = d.groupby(key)["r_ex_cost"].mean() if "r_ex_cost" in d else np.nan
    print(f"\n--- by {title or key}")
    print(g.round(4).to_string())
    return g


def headline(label: str, d: pd.DataFrame) -> None:
    if not len(d):
        print(f"{label}: no trades")
        return
    print(f"{label:22s} n={len(d):6d}  total R={d['r'].sum():+9.1f}  "
          f"mean R={d['r'].mean():+.5f}  win%={100*(d['r']>0).mean():5.1f}  t={_t(d['r']):+.2f}")


# ─────────────────────────────────────────────────────────────────────────────
# mode: loss
# ─────────────────────────────────────────────────────────────────────────────
def mode_loss(scanned, tp, expect=None) -> int:
    d = replay_incumbent(scanned, tp)
    headline("ALL", d)
    if expect is not None:
        got = float(d["r"].sum())
        ok = abs(got - float(expect)) < 0.15
        print(f"[validate] expected total R {float(expect):+.1f}, got {got:+.1f} -> "
              f"{'OK' if ok else 'INSTRUMENT DRIFTED -- DO NOT TRUST ANY NUMBER BELOW'}")
        if not ok:
            return 2

    for k in ("symbol", "side", "session", "regime", "strategy"):
        breakdown(d, k)

    print("\n--- result mix (n / total R per exit type)")
    print(d.groupby("result")["r"].agg(n="count", total_R="sum", mean_R="mean").round(4).to_string())

    print("\n--- COST DECOMPOSITION (R per trade)")
    tot = float(d["r"].sum())
    spread = float(d["cost_r"].fillna(0).sum())
    slip = float(d["slip_r"].sum())
    print(f"  recorded total R                 : {tot:+.1f}")
    print(f"  spread toll  (sum cost_r)        : {-spread:+.1f}  ({100*spread/abs(tot) if tot else 0:.0f}% of the loss)")
    print(f"  stop slippage (0.5 pip on SL)    : {-slip:+.1f}")
    print(f"  R excluding BOTH                 : {tot + spread + slip:+.1f} "
          f"(mean {d['r_ex_cost'].mean():+.5f})")
    print(f"  mean cost_r per trade            : {d['cost_r'].mean():.5f} R")
    print(f"  mean slip_r per trade            : {d['slip_r'].mean():.5f} R")

    print("\n--- LOSS SHAPE (is it a few big losers?)")
    print(f"  min R {d['r'].min():+.3f}  p25 {d['r'].quantile(.25):+.3f}  "
          f"median {d['r'].median():+.3f}  p75 {d['r'].quantile(.75):+.3f}  max {d['r'].max():+.3f}")
    k = max(1, int(0.05 * len(d)))
    print(f"  worst  5% of trades contribute   : {d.nsmallest(k,'r')['r'].sum():+.1f} R")
    print(f"  best   5% of trades contribute   : {d.nlargest(k,'r')['r'].sum():+.1f} R")
    tp_r = float(tp)
    be = 1.0 / (1.0 + tp_r)
    hit = float((d["result"] == "TP").mean())
    print(f"  breakeven hit rate at 1:{tp_r:g}      : {100*be:.1f}%   observed TP rate {100*hit:.1f}%"
          f"   shortfall {100*(hit-be):+.1f} pp")

    print("\n--- MARK-TO-MARKET AFTER ENTRY (adverse selection test)")
    for sym, s in sorted(scanned.items()):
        pass  # filled below
    return 0


def mtm_curve(scanned, tp, ks=(1, 2, 3, 5, 10)) -> None:
    """Mean mark-to-market R k bars after the entry bar.

    The fill already carries the FULL spread (entry_fill charges ask for a long,
    bid for a short), so an mtm of about -cost_r means "no adverse selection at
    all"; anything materially worse is a real post-entry drag.
    """
    rows = []
    for sym, s in sorted(scanned.items()):
        df, cands = s["df"], s["ex_a"]
        if cands is None or len(cands) == 0:
            continue
        bars = BarArrays.from_df(df)
        for r in cands.itertuples(index=False):
            i = int(getattr(r, "bar_idx"))
            side = str(getattr(r, "side", "")).upper()
            if side not in ("BUY", "SELL") or i + 1 >= bars.n:
                continue
            fill, sl = float(getattr(r, "fill")), float(getattr(r, "sl"))
            risk = abs(fill - sl)
            if risk <= 0:
                continue
            d = 1.0 if side == "BUY" else -1.0
            rec = {"symbol": sym}
            for k in ks:
                j = i + k
                rec[f"mtm{k}"] = ((bars.close[j] - fill) * d / risk) if j < bars.n else np.nan
            rows.append(rec)
    m = pd.DataFrame(rows)
    print("\n--- mark-to-market R, k bars after entry (negative = adverse)")
    for k in ks:
        c = m[f"mtm{k}"].dropna()
        print(f"  +{k:2d} bars: n={len(c):5d} mean={c.mean():+.5f} median={c.median():+.5f} "
              f"frac>0={100*(c>0).mean():5.1f}%")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# mode: control -- is the entry better or worse than a time-matched random bar?
# ─────────────────────────────────────────────────────────────────────────────
def mode_control(scanned, tp, reps=20, horizon=20) -> None:
    rng = np.random.default_rng(20260924)
    inc: List[Dict[str, Any]] = []
    ctl: List[Dict[str, Any]] = []
    shuf: List[Dict[str, Any]] = []   # incumbent bar, SHUFFLED direction
    null: List[Dict[str, Any]] = []   # random bar AND random direction
    for sym, s in sorted(scanned.items()):
        df, cands = s["df"], s["ex_a"]
        if cands is None or len(cands) == 0:
            continue
        spec = reg.resolve(sym)
        money = reg.get_dollar_risk_per_price_unit(sym, None)
        bars = BarArrays.from_df(df)
        n, pip = bars.n, float(spec.pip_size)
        opens = df["open"].to_numpy(float)
        lo, hi = 60, n - 2
        for r in cands.itertuples(index=False):
            i = int(getattr(r, "bar_idx"))
            side = str(getattr(r, "side", "")).upper()
            if side not in ("BUY", "SELL") or i + 1 >= n:
                continue
            fill, sl = float(getattr(r, "fill")), float(getattr(r, "sl"))
            risk = abs(fill - sl)
            if risk <= 0:
                continue
            out = _sim(sym, bars, spec, money, side, i, fill, sl, tp)
            if out is None:
                continue
            inc.append({"symbol": sym, "side": side, "r": float(out.pnl_r),
                        "mfe_r": float(out.mfe_r), "bar_idx": i})
            # Time-matched control: SAME side, SAME risk width, bar within
            # +/-50 of the incumbent. Fill is open(j+1) with entry_idx=j so the
            # control gets exactly the same intrabar treatment as the incumbent
            # (using open(j) would give it one untested bar of grace).
            d = 1.0 if side == "BUY" else -1.0
            sp = float(getattr(r, "spread_pips", spec.typical_spread_pips) or 0.0)
            for jj in np.clip(i + rng.integers(-50, 51, size=reps), lo, hi):
                j = int(jj)
                cf = float(opens[j + 1]) + (sp * pip if side == "BUY" else -sp * pip)
                o2 = _sim(sym, bars, spec, money, side, j, cf, cf - d * risk, tp)
                if o2 is not None:
                    ctl.append({"symbol": sym, "r": float(o2.pnl_r), "mfe_r": float(o2.mfe_r)})

            # Third arm: the incumbent's OWN bar, direction flipped at random.
            # Isolates whether the damage is TIMING (bar choice) or DIRECTION.
            flip = "SELL" if side == "BUY" else "BUY"
            sd2 = side if rng.random() < 0.5 else flip
            d2 = 1.0 if sd2 == "BUY" else -1.0
            cf2 = float(opens[i + 1]) + (sp * pip if sd2 == "BUY" else -sp * pip)
            o3 = _sim(sym, bars, spec, money, sd2, i, cf2, cf2 - d2 * risk, tp)
            if o3 is not None:
                shuf.append({"symbol": sym, "r": float(o3.pnl_r), "mfe_r": float(o3.mfe_r)})

            # Fourth arm: random bar AND random direction. This is the NULL:
            # it must land near -(spread + slippage) R, i.e. slightly negative
            # and nowhere near the +0.28 the time-matched arm prints. If it
            # does not, the instrument -- not the market -- is generating the
            # number and every arm above is void.
            for jj in np.clip(i + rng.integers(-50, 51, size=reps), lo, hi):
                j = int(jj)
                sd3 = "BUY" if rng.random() < 0.5 else "SELL"
                d3 = 1.0 if sd3 == "BUY" else -1.0
                cf3 = float(opens[j + 1]) + (sp * pip if sd3 == "BUY" else -sp * pip)
                o4 = _sim(sym, bars, spec, money, sd3, j, cf3, cf3 - d3 * risk, tp)
                if o4 is not None:
                    null.append({"symbol": sym, "r": float(o4.pnl_r)})

    a, b = pd.DataFrame(inc), pd.DataFrame(ctl)
    headline("INCUMBENT", a)
    headline("TIME-MATCHED RANDOM", b)
    if shuf:
        headline("SAME BAR, RANDOM DIR", pd.DataFrame(shuf))
    if null:
        nn = pd.DataFrame(null)
        headline("RANDOM BAR+DIR (NULL)", nn)
        print("  [null] must sit slightly BELOW zero (spread + slippage only) and far")
        print("         from the time-matched arm; if it does not, the instrument is")
    print(f"\n  incumbent BUY share: {100*(a['side']=='BUY').mean():.1f}%   "
          f"median entry bar {int(a['bar_idx'].median())} of {max(a['bar_idx'])+1} total")
    print("  (three arms: bar choice and direction are varied one at a time)")
    print(f"\n{'sym':8s}{'n_inc':>7}{'inc':>10}{'n_ctl':>9}{'ctl':>10}{'delta':>10}{'t(delta)':>10}")
    for sym in sorted(a["symbol"].unique()):
        x = a[a["symbol"] == sym]["r"]
        y = b[b["symbol"] == sym]["r"]
        sd = math.sqrt(x.var(ddof=1) / len(x) + y.var(ddof=1) / len(y)) if len(y) > 1 else 0.0
        print(f"{sym:8s}{len(x):>7}{x.mean():>+10.5f}{len(y):>9}{y.mean():>+10.5f}"
              f"{x.mean()-y.mean():>+10.5f}{(x.mean()-y.mean())/sd if sd>1e-12 else 0:>+10.2f}")
    sd = math.sqrt(a["r"].var(ddof=1) / len(a) + b["r"].var(ddof=1) / len(b))
    print(f"{'ALL':8s}{len(a):>7}{a['r'].mean():>+10.5f}{len(b):>9}{b['r'].mean():>+10.5f}"
          f"{a['r'].mean()-b['r'].mean():>+10.5f}{(a['r'].mean()-b['r'].mean())/sd:>+10.2f}")

    # independent, simulator-free cross-check
    print(f"\n--- independent cross-check: raw forward {horizon}-bar excursion, no exit policy")
    rows = []
    rng2 = np.random.default_rng(7)
    for sym, s in sorted(scanned.items()):
        df, cands = s["df"], s["ex_a"]
        if cands is None or len(cands) == 0:
            continue
        hi_ = df["high"].to_numpy(float); lo_ = df["low"].to_numpy(float)
        op = df["open"].to_numpy(float); cl = df["close"].to_numpy(float)
        n = len(df)

        def st(a: int, d: float, risk: float):
            f = op[a + 1]
            sh, sl2 = hi_[a + 1:a + 1 + horizon], lo_[a + 1:a + 1 + horizon]
            mfe = ((sh.max() - f) if d > 0 else (f - sl2.min())) / risk
            mae = ((f - sl2.min()) if d > 0 else (sh.max() - f)) / risk
            return mfe, mae, (cl[a + horizon] - f) * d / risk

        for r in cands.itertuples(index=False):
            i = int(getattr(r, "bar_idx"))
            side = str(getattr(r, "side", "")).upper()
            if side not in ("BUY", "SELL") or i + 1 + horizon >= n:
                continue
            fill, sl = float(getattr(r, "fill")), float(getattr(r, "sl"))
            risk = abs(fill - sl)
            if risk <= 0:
                continue
            d = 1.0 if side == "BUY" else -1.0
            mfe, mae, endr = st(i, d, risk)
            cs = [st(int(j), d, risk) for j in
                  np.clip(i + rng2.integers(-50, 51, size=reps), 60, n - 2 - horizon)]
            rows.append({"symbol": sym, "mfe": mfe, "mae": mae, "end": endr,
                         "c_mfe": float(np.mean([c[0] for c in cs])),
                         "c_mae": float(np.mean([c[1] for c in cs])),
                         "c_end": float(np.mean([c[2] for c in cs]))})
    m = pd.DataFrame(rows)
    print(f"{'sym':8s}{'n':>6}{'mfe':>8}{'ctl':>8}{'d':>8}{'t':>7} | {'end':>8}{'ctl':>8}{'d':>8}{'t':>7}")
    for sym in sorted(m["symbol"].unique()):
        x = m[m["symbol"] == sym]
        dm, de = x["mfe"] - x["c_mfe"], x["end"] - x["c_end"]
        tm = dm.mean() / (dm.std(ddof=1) / math.sqrt(len(dm)))
        te = de.mean() / (de.std(ddof=1) / math.sqrt(len(de)))
        print(f"{sym:8s}{len(x):>6}{x['mfe'].mean():>8.4f}{x['c_mfe'].mean():>8.4f}"
              f"{dm.mean():>+8.4f}{tm:>+7.2f} | {x['end'].mean():>8.4f}{x['c_end'].mean():>8.4f}"
              f"{de.mean():>+8.4f}{te:>+7.2f}")
    dm, de = m["mfe"] - m["c_mfe"], m["end"] - m["c_end"]
    print(f"{'ALL':8s}{len(m):>6}{m['mfe'].mean():>8.4f}{m['c_mfe'].mean():>8.4f}"
          f"{dm.mean():>+8.4f}{dm.mean()/(dm.std(ddof=1)/math.sqrt(len(dm))):>+7.2f} | "
          f"{m['end'].mean():>8.4f}{m['c_end'].mean():>8.4f}{de.mean():>+8.4f}"
          f"{de.mean()/(de.std(ddof=1)/math.sqrt(len(de))):>+7.2f}")
    print("\nRead it this way: mfe/end BELOW the time-matched control means the entry")
    print("model picks bars that subsequently move LESS in its own favour than a")
    print("nearby random bar would. That is negative skill, not bad luck.")


# ─────────────────────────────────────────────────────────────────────────────
# mode: sr -- support/resistance proximity of the recorded entries
# ─────────────────────────────────────────────────────────────────────────────
def mode_sr(scanned, tp, lookback=300) -> None:
    from jarvis.market.market_structure import MarketStructureEngine

    eng = MarketStructureEngine()
    d = replay_incumbent(scanned, tp)
    if not len(d):
        print("no trades")
        return
    dists, biases, inzone, ldist = [], [], [], []
    for row in d.itertuples(index=False):
        sym, i = row.symbol, int(row.bar_idx)
        df = scanned[sym]["df"]
        sub = df.iloc[max(0, i - lookback):i + 1]
        st = eng.analyze_structure(sub)
        atr = float(row.atr) if np.isfinite(row.atr) and row.atr > 0 else np.nan
        px = float(row.fill)

        def gap(zone) -> float:
            lo_, hi_ = float(min(zone)), float(max(zone))
            return 0.0 if lo_ <= px <= hi_ else min(abs(px - lo_), abs(px - hi_))

        gd, gs = gap(st.demand_zone), gap(st.supply_zone)
        g = min(gd, gs)
        dists.append(g / atr if np.isfinite(atr) and atr > 0 else np.nan)
        # The production zone is a fixed 0.3% band, which on FX is ~2.5 ATR wide
        # -- so "inside the zone" is a weak statement. Also record the distance
        # to the structural LEVEL itself (the band midpoint), which is scale-free.
        dl = float(np.mean(st.demand_zone))
        sl_ = float(np.mean(st.supply_zone))
        ldist.append(min(abs(px - dl), abs(px - sl_)) / atr
                     if np.isfinite(atr) and atr > 0 else np.nan)
        biases.append(str(getattr(st, "bias", "NEUTRAL")))
        inzone.append(g == 0.0)
    d["dist_atr"] = dists
    d["lvl_atr"] = ldist
    d["bias"] = biases
    d["in_zone"] = inzone

    print(f"\nS/R proximity: distance from entry to nearest structural level, in ATR "
          f"(structure recomputed on the {lookback} bars ending at the entry)")
    print(d["dist_atr"].describe(percentiles=[.1, .25, .5, .75, .9]).round(4).to_string())
    print(f"  fraction of entries INSIDE a demand/supply zone: {100*d['in_zone'].mean():.1f}%")

    bins = [-0.001, 0.0, 0.25, 0.5, 1.0, 2.0, np.inf]
    labels = ["IN_ZONE", "0-0.25", "0.25-0.5", "0.5-1", "1-2", ">2"]
    d["prox"] = pd.cut(d["dist_atr"], bins, labels=labels)
    print("\n--- realised R by proximity bucket")
    g = d.groupby("prox", observed=True)["r"].agg(n="count", total_R="sum", mean_R="mean")
    g["win%"] = 100 * d.groupby("prox", observed=True)["r"].apply(lambda x: (x > 0).mean())
    g["t"] = d.groupby("prox", observed=True)["r"].apply(_t)
    print(g.round(4).to_string())

    near = d[d["dist_atr"] <= 0.25]
    far = d[d["dist_atr"] > 1.0]
    print("\n--- NEAR (<=0.25 ATR, incl. in-zone) vs FAR (>1 ATR)")
    headline("  NEAR", near)
    headline("  FAR", far)
    if len(near) > 1 and len(far) > 1:
        sd = math.sqrt(near["r"].var(ddof=1) / len(near) + far["r"].var(ddof=1) / len(far))
        print(f"  delta (near - far) = {near['r'].mean()-far['r'].mean():+.5f} R  "
              f"t={(near['r'].mean()-far['r'].mean())/sd:+.2f}")

    bins2 = [-0.001, 0.25, 0.5, 1.0, 2.0, 4.0, np.inf]
    labels2 = ["0-0.25", "0.25-0.5", "0.5-1", "1-2", "2-4", ">4"]
    d["lvl"] = pd.cut(d["lvl_atr"], bins2, labels=labels2)
    print("\n--- distance to the structural LEVEL (band midpoint), in ATR")
    g2 = d.groupby("lvl", observed=True)["r"].agg(n="count", total_R="sum", mean_R="mean")
    g2["win%"] = 100 * d.groupby("lvl", observed=True)["r"].apply(lambda x: (x > 0).mean())
    g2["t"] = d.groupby("lvl", observed=True)["r"].apply(_t)
    print(g2.round(4).to_string())

    print("\n--- by structure bias")
    print(d.groupby("bias")["r"].agg(n="count", total_R="sum", mean_R="mean").round(4).to_string())
    print("\n--- per symbol: near vs far (a one-symbol result is not a result)")
    print(f"{'sym':8s}{'n_near':>8}{'near':>10}{'n_far':>8}{'far':>10}{'delta':>10}")
    for sym in sorted(d["symbol"].unique()):
        x = d[(d["symbol"] == sym) & (d["dist_atr"] <= 0.25)]["r"]
        y = d[(d["symbol"] == sym) & (d["dist_atr"] > 1.0)]["r"]
        print(f"{sym:8s}{len(x):>8}{(x.mean() if len(x) else float('nan')):>+10.4f}"
              f"{len(y):>8}{(y.mean() if len(y) else float('nan')):>+10.4f}"
              f"{(x.mean()-y.mean() if len(x) and len(y) else float('nan')):>+10.4f}")


# ─────────────────────────────────────────────────────────────────────────────
# mode: fvg -- displacement / fair-value-gap entry model
# ─────────────────────────────────────────────────────────────────────────────
def detect_fvg(df: pd.DataFrame, disp_mult: float = 1.5, min_gap_atr: float = 0.0) -> pd.DataFrame:
    """Causal displacement + FVG detection.

    Mirrors ``jarvis/market/fair_value_gap.py``: a bullish FVG is
    ``high[t-2] < low[t]`` (zone = that gap), a bearish FVG is
    ``low[t-2] > high[t]``. Displacement is the order-block rule the same module
    uses: candle body >= ``disp_mult`` * ATR.

    ``mitigated`` is evaluated at formation, so every row returned is an
    unmitigated zone at the moment it is detected -- no future bars are read.
    """
    o = df["open"].to_numpy(float); h = df["high"].to_numpy(float)
    lo = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
    atr = df["atr"].to_numpy(float) if "atr" in df.columns else _atr(df, 14).to_numpy(float)
    n = len(df)
    rows = []
    for t in range(2, n):
        a = atr[t]
        if not np.isfinite(a) or a <= 0:
            continue
        body = abs(c[t] - o[t])
        if disp_mult > 0 and body < disp_mult * a:
            continue
        if h[t - 2] < lo[t]:                      # bullish FVG / displacement up
            top, bot, side = lo[t], h[t - 2], "BUY"
        elif lo[t - 2] > h[t]:                    # bearish FVG / displacement down
            top, bot, side = lo[t - 2], h[t], "SELL"
        else:
            continue
        gap = top - bot
        if gap < min_gap_atr * a:
            continue
        rows.append({"sig_idx": t, "side": side, "top": float(top), "bottom": float(bot),
                     "mid": float((top + bot) / 2.0), "gap": float(gap),
                     "atr": float(a), "body": float(body)})
    return pd.DataFrame(rows)


def fvg_trades(sym: str, df: pd.DataFrame, tp: float, model: str = "break",
               disp_mult: float = 1.5, wait: int = 24, part: Optional[str] = None,
               boundary: pd.Timestamp = OOS_BOUNDARY) -> pd.DataFrame:
    """Generate FVG entries on ``df`` and replay them.

    ``model``
      break   -- market at the open of the bar after the FVG forms.
      retest  -- limit at the gap midpoint, filled only if price trades back
                 into the gap within ``wait`` bars and the invalidation has not
                 already been hit.

    Invalidation for BOTH models is a full gap fill: below the gap bottom for a
    bullish FVG, above the gap top for a bearish one. Using one rule for both
    models keeps the comparison between them interpretable.
    """
    spec = reg.resolve(sym)
    money = reg.get_dollar_risk_per_price_unit(sym, None)
    bars = BarArrays.from_df(df)
    pip = float(spec.pip_size)
    n = bars.n
    times = pd.to_datetime(df["time"])
    raw_spread = df["spread"].to_numpy(float) if "spread" in df.columns else None
    typ = float(getattr(spec, "typical_spread_pips", 2.0) or 2.0)

    lo_, hi_ = 0, n
    if part == "oos":
        hi_ = int((times < boundary).sum())
    elif part == "is":
        lo_ = int((times < boundary).sum())

    sigs = detect_fvg(df, disp_mult=disp_mult)
    if sigs.empty:
        return pd.DataFrame()
    sigs = sigs[(sigs["sig_idx"] >= max(2, lo_)) & (sigs["sig_idx"] < hi_)]
    if sigs.empty:
        return pd.DataFrame()

    opens = df["open"].to_numpy(float)
    rows = []
    for s in sigs.itertuples(index=False):
        t = int(s.sig_idx)
        buy = s.side == "BUY"
        d = 1.0 if buy else -1.0
        # invalidation = full gap fill
        sl_ref = float(s.bottom) if buy else float(s.top)
        raw = raw_spread[t + 1] if (raw_spread is not None and t + 1 < n) else np.nan
        sp = spread_pips_for(spec, raw, typ)

        if model == "break":
            if t + 1 >= n:
                continue
            fill = float(opens[t + 1]) + d * sp * pip
            e_idx = t
        else:  # retest
            e_idx, fill = None, None
            for j in range(t + 1, min(t + 1 + wait, n)):
                # invalidation first: the zone is void before we ever get filled
                if (bars.low[j] <= sl_ref) if buy else (bars.high[j] >= sl_ref):
                    break
                touched = (bars.low[j] <= s.mid) if buy else (bars.high[j] >= s.mid)
                if touched:
                    e_idx, fill = j, float(s.mid) + d * sp * pip
                    break
            if e_idx is None:
                continue

        sl = sl_ref - d * 0.0
        risk = abs(fill - sl)
        if risk <= 0 or risk < 0.05 * float(s.atr):
            continue
        out = _sim(sym, bars, spec, money, s.side, e_idx, fill, sl, tp)
        if out is None:
            continue
        rows.append({"symbol": sym, "side": s.side, "bar_idx": e_idx, "sig_idx": t,
                     "r": float(out.pnl_r), "mfe_r": float(out.mfe_r), "mae_r": float(out.mae_r),
                     "result": str(out.result), "risk_dist": risk,
                     "atr": float(s.atr), "gap_atr": float(s.gap) / float(s.atr),
                     "spread_pips": sp, "cost_r": sp * pip / risk,
                     "slip_r": (0.5 * pip / risk) if str(out.result).endswith("SL") else 0.0})
    out = pd.DataFrame(rows)
    if len(out):
        out["r_ex_cost"] = out["r"] + out["cost_r"] + out["slip_r"]
    return out


def mode_fvg(scanned, tp, window=365, part="oos", disp_mult=1.5, wait=24,
             symbols=None, models=("break", "retest"), disp_grid=(0.0, 1.0, 1.5, 2.0)) -> None:
    """Incumbent vs FVG on the same bars and the same window half."""
    print(f"\n{'='*78}\nFVG / DISPLACEMENT ENTRY MODEL — window H1_{window}d, part={part}\n{'='*78}")
    if part in ("is", "oos"):
        print(f"boundary {OOS_BOUNDARY.date()}: "
              f"{'bars BEFORE it (disjoint from the 183d window)' if part=='oos' else 'bars from it onward'}")

    for model in models:
        print(f"\n{'#'*78}\n# model = {model}   (invalidation = full gap fill, tp_r={tp})\n{'#'*78}")
        agg_inc, agg_fvg = [], []
        for sym in sorted(scanned):
            df = scanned[sym]["df"] if window == 365 and sym in scanned else load_parquet(sym, window)
            if df is None or len(df) < 200:
                print(f"[skip] {sym}: no H1_{window}d bars")
                continue
            times = pd.to_datetime(df["time"])
            cut = int((times < OOS_BOUNDARY).sum())
            lo_i, hi_i = (0, cut) if part == "oos" else ((cut, len(df)) if part == "is" else (0, len(df)))

            inc = replay_incumbent({sym: scanned[sym]}, tp) if sym in scanned else pd.DataFrame()
            if len(inc):
                inc = inc[(inc["bar_idx"] >= lo_i) & (inc["bar_idx"] < hi_i)]
            fvg = fvg_trades(sym, df, tp, model=model, disp_mult=disp_mult, wait=wait, part=part)
            agg_inc.append(inc); agg_fvg.append(fvg)
            if len(inc):
                inc_s = f"R={inc['r'].sum():+8.1f} mean={inc['r'].mean():+.4f}"
            else:
                inc_s = " " * 26
            if len(fvg):
                fvg_s = (f"R={fvg['r'].sum():+8.1f} mean={fvg['r'].mean():+.4f} "
                         f"win%={100*(fvg['r']>0).mean():4.1f}")
            else:
                fvg_s = "(no signals)"
            print(f"{sym:8s} bars {len(df):5d} | incumbent n={len(inc):5d} {inc_s}"
                  f" | FVG n={len(fvg):5d} {fvg_s}")

        A = pd.concat([x for x in agg_inc if len(x)], ignore_index=True) if any(len(x) for x in agg_inc) else pd.DataFrame()
        B = pd.concat([x for x in agg_fvg if len(x)], ignore_index=True) if any(len(x) for x in agg_fvg) else pd.DataFrame()
        print(f"\n  AGGREGATE ({len([x for x in agg_fvg if len(x)])} symbols)")
        headline("  incumbent", A)
        headline(f"  FVG/{model}", B)
        if len(A) and len(B):
            sd = math.sqrt(A["r"].var(ddof=1) / len(A) + B["r"].var(ddof=1) / len(B))
            print(f"  delta (FVG - incumbent) = {B['r'].mean()-A['r'].mean():+.5f} R  "
                  f"t={(B['r'].mean()-A['r'].mean())/sd:+.2f}")
        if len(B):
            print(f"  FVG mean cost_r {B['cost_r'].mean():.5f}  mean R ex-cost {B['r_ex_cost'].mean():+.5f}"
                  f"  median risk {B['risk_dist'].median():.6f}")
            print("\n  per-symbol FVG detail (a one-symbol result is not a result)")
            g = B.groupby("symbol")["r"].agg(n="count", total_R="sum", mean_R="mean")
            g["win%"] = 100 * B.groupby("symbol")["r"].apply(lambda x: (x > 0).mean())
            g["mean_cost_r"] = B.groupby("symbol")["cost_r"].mean()
            print(g.round(4).to_string())

    # displacement sensitivity -- only if asked for the grid
    print(f"\n{'#'*78}\n# displacement sensitivity (model=break)\n{'#'*78}")
    print(f"{'disp':>6}{'n':>8}{'total R':>12}{'mean R':>11}{'win%':>8}{'t':>8}")
    for dm in disp_grid:
        acc = []
        for sym in sorted(scanned):
            df = scanned[sym]["df"] if window == 365 and sym in scanned else load_parquet(sym, window)
            if df is None or len(df) < 200:
                continue
            acc.append(fvg_trades(sym, df, tp, model="break", disp_mult=dm, wait=wait, part=part))
        acc = [x for x in acc if len(x)]
        C = pd.concat(acc, ignore_index=True) if acc else pd.DataFrame()
        if not len(C):
            print(f"{dm:>6g}{0:>8}{'--':>12}")
            continue
        label = "off" if dm == 0 else f"{dm:g}xATR"
        print(f"{label:>6}{len(C):>8}{C['r'].sum():>+12.1f}{C['r'].mean():>+11.5f}"
              f"{100*(C['r']>0).mean():>8.1f}{_t(C['r']):>+8.2f}")


# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", default="loss",
                    choices=["loss", "control", "sr", "fvg", "mtm"])
    ap.add_argument("--cache", default=None, help="scan cache id; default: first found")
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--tp", type=float, default=3.0)
    ap.add_argument("--expect", type=float, default=None,
                    help="assert the replayed total R equals this (instrument validation)")
    ap.add_argument("--reps", type=int, default=20)
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--window", type=int, default=365, help="parquet window for FVG bars")
    ap.add_argument("--part", default="oos", choices=["oos", "is", "all"])
    ap.add_argument("--disp", type=float, default=1.5, help="displacement multiple of ATR")
    ap.add_argument("--wait", type=int, default=24, help="retest window in bars")
    ap.add_argument("--validate", action="store_true",
                    help="shortcut: force cache=ANCHOR, tp=ANCHOR_TP, expect=ANCHOR_TOTAL_R")
    args = ap.parse_args()

    if args.validate:
        args.cache, args.tp, args.expect = ANCHOR_CACHE, ANCHOR_TP, ANCHOR_TOTAL_R

    scanned = load_scanned(args.cache, args.symbols)
    if not scanned:
        return 1

    if args.mode == "loss":
        rc = mode_loss(scanned, args.tp, args.expect)
        mtm_curve(scanned, args.tp)
        return rc
    if args.mode == "mtm":
        mtm_curve(scanned, args.tp)
        return 0
    if args.mode == "control":
        mode_control(scanned, args.tp, reps=args.reps, horizon=args.horizon)
        return 0
    if args.mode == "sr":
        mode_sr(scanned, args.tp)
        return 0
    mode_fvg(scanned, args.tp, window=args.window, part=args.part,
             disp_mult=args.disp, wait=args.wait, symbols=args.symbols)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

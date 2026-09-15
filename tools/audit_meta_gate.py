"""Can meta-labelling rescue this signal? Measure before wiring anything.

``decision_engine.py:1033-1045`` has a meta-label gate that asks a fitted model
whether to take the bet. It is inert in every backtest. Before enabling it, this
answers the only question that matters: **does the gate carry out-of-sample
information about whether a candidate wins?**

Two things are tested:

1. The shipped feature set (14 price/volume statistics of the preceding window).
   ``tools/train_meta_labeler.py`` already showed AUC ~0.51 on a purged split.
2. The same features **plus the primary model's own outputs** — score, trend
   score, regime, ATR%, confluence. This is the actual meta-labelling
   formulation (Lopez de Prado): the secondary model answers a *different*
   question using information the primary model produced. If the gate only sees
   what the primary model already saw, it cannot add anything.

Labels are the realised trade outcome (pnl_r > 0) from replaying the stored
candidates. The split is purged: train on the earlier half of each series, test
on the later half, with an embargo between them.

    python tools/audit_meta_gate.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from jarvis.backtesting.trade_simulator import Geometry  # noqa: E402
from jarvis.data.symbol_registry import resolve  # noqa: E402
from jarvis.intelligence.meta_labeler import _window_features  # noqa: E402
from tools.audit_trade_quality import load_symbol, dynamic_regimes, replay  # noqa: E402

REAL_DIR = os.path.join(REPO, "data", "market", "real")
REGIMES = ["TREND_BULL", "TREND_BEAR", "RANGE", "COMPRESSION", "BREAKOUT",
           "LIQUIDITY_SWEEP", "HIGH_VOLATILITY", "LOW_VOLATILITY"]


def build(symbol: str, tf: str = "H1"):
    """Return (X_window, X_full, y, meta) for one symbol."""
    df, cands = load_symbol(symbol, tf)
    if df is None:
        return None
    cands = dynamic_regimes(df, cands)
    spec = resolve(symbol)
    tr = replay(symbol, df, cands, Geometry(tp_r=1.5),
                slippage_price=0.5 * float(spec.pip_size or 0.0001), comm_price=0.0)
    if tr is None or tr.empty:
        return None

    candles = df[["open", "high", "low", "close"]].copy()
    candles["volume"] = pd.to_numeric(df.get("tick_volume", 1.0), errors="coerce").fillna(1.0)
    candles = candles.to_dict("records")
    n = len(candles)

    tr = tr.merge(cands[["bar_idx", "regime", "confluence_count", "rr", "trend_score"]],
                  on="bar_idx", how="left")

    rows_w, rows_f, ys, bars = [], [], [], []
    for r in tr.itertuples(index=False):
        i = int(r.bar_idx)
        if i < 30 or i >= n:
            continue
        bias = 1.0 if r.side == "BUY" else -1.0
        feat = _window_features(candles[i - 29: i + 1], bias=bias)
        if feat is None:
            continue
        regime = str(getattr(r, "regime", "") or "")
        onehot = [1.0 if regime.upper() == rg else 0.0 for rg in REGIMES]
        primary = [
            float(getattr(r, "score", 0.0) or 0.0),
            float(getattr(r, "trend_score", 0.0) or 0.0) / 100.0,
            float(getattr(r, "atr_pct", 0.0) or 0.0) * 1000.0,
            float(getattr(r, "confluence_count", 0) or 0) / 10.0,
            float(getattr(r, "rr", 0.0) or 0.0) / 4.0,
            bias,
        ] + onehot
        rows_w.append(feat)
        rows_f.append(np.concatenate([feat, np.array(primary, dtype=float)]))
        ys.append(1 if float(r.pnl_r) > 0 else 0)
        bars.append(i)

    if not rows_w:
        return None
    return (np.asarray(rows_w, dtype=float), np.asarray(rows_f, dtype=float),
            np.asarray(ys, dtype=int), np.asarray(bars, dtype=int))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--train-frac", type=float, default=0.5)
    ap.add_argument("--embargo", type=int, default=50)
    ap.add_argument("--out", default=os.path.join(REPO, "reports", "meta_gate_audit.json"))
    args = ap.parse_args()

    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score, brier_score_loss

    symbols = sorted(d for d in os.listdir(REAL_DIR) if os.path.isdir(os.path.join(REAL_DIR, d)))
    W_tr, F_tr, y_tr, W_te, F_te, y_te = [], [], [], [], [], []
    for sym in symbols:
        got = build(sym, args.tf)
        if got is None:
            continue
        Xw, Xf, y, bars = got
        cut = int(bars.max() * args.train_frac) if len(bars) else 0
        m_tr = bars <= cut - args.embargo
        m_te = bars > cut + args.embargo
        if m_tr.sum() < 300 or m_te.sum() < 100:
            continue
        W_tr.append(Xw[m_tr]); F_tr.append(Xf[m_tr]); y_tr.append(y[m_tr])
        W_te.append(Xw[m_te]); F_te.append(Xf[m_te]); y_te.append(y[m_te])
        print(f"{sym:8s} train {int(m_tr.sum()):5d}  test {int(m_te.sum()):5d}  "
              f"win rate train={y[m_tr].mean():.3f} test={y[m_te].mean():.3f}", flush=True)

    if not W_tr:
        print("no data")
        return 1
    W_tr, F_tr, y_tr = np.vstack(W_tr), np.vstack(F_tr), np.concatenate(y_tr)
    W_te, F_te, y_te = np.vstack(W_te), np.vstack(F_te), np.concatenate(y_te)

    res = {"base_rate_test": float(y_te.mean()), "base_rate_train": float(y_tr.mean()),
           "n_train": int(len(y_tr)), "n_test": int(len(y_te))}
    for name, A_tr, A_te in (("window_only", W_tr, W_te), ("window_plus_primary", F_tr, F_te)):
        m = HistGradientBoostingClassifier(max_depth=4, learning_rate=0.05, max_iter=300,
                                           l2_regularization=1.0, random_state=42)
        m.fit(A_tr, y_tr)
        p = m.predict_proba(A_te)[:, list(m.classes_).index(1)]
        ptr = m.predict_proba(A_tr)[:, list(m.classes_).index(1)]
        res[name] = {
            "test_auc": float(roc_auc_score(y_te, p)),
            "train_auc": float(roc_auc_score(y_tr, ptr)),
            "test_brier": float(brier_score_loss(y_te, p)),
        }
        print(f"\n{name}: test AUC {res[name]['test_auc']:.4f} | "
              f"train AUC {res[name]['train_auc']:.4f} | Brier {res[name]['test_brier']:.4f}")

        # Would the gate help? Take the top decile by predicted probability.
        q = np.quantile(p, 0.90)
        sel = p >= q
        if sel.sum() >= 20:
            res[name]["top_decile"] = {
                "n": int(sel.sum()),
                "win_rate_selected": float(y_te[sel].mean()),
                "win_rate_all": float(y_te.mean()),
            }
            print(f"  top decile by P(win): n={sel.sum()}  win rate "
                  f"{y_te[sel].mean():.3f} vs base {y_te.mean():.3f}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2)
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

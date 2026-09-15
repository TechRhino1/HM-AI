"""Train the meta-label model on real bars, with a purged forward split.

The gate at ``decision_engine.py:1033-1045`` asks the MetaLabeler whether to
take a bet, but ``MetaLabeler._load`` deliberately returns ``model=None``
offline — so in every backtest the only learned component in the system is
inert and can never be measured, let alone improved.

This tool fits the model on history so the gate can be evaluated. The split is
**purged and embargoed**: training uses only the earlier part of each series,
evaluation only the later part, with a gap between them at least as long as the
labelling horizon, so no label used for training can overlap a test bar.

    python tools/train_meta_labeler.py --out data/models/meta_labeler.joblib

The model is saved but nothing in the decision path reads it until it has been
shown to help; use tools/audit_meta_gate.py to measure it first.
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

from jarvis.intelligence.meta_labeler import MetaLabeler  # noqa: E402

REAL_DIR = os.path.join(REPO, "data", "market", "real")


def load_candles(symbol: str, tf: str = "H1"):
    path = os.path.join(REAL_DIR, symbol, f"{symbol}_{tf}_183d.parquet")
    if not os.path.exists(path):
        return None
    df = pd.read_parquet(path).sort_values("time").reset_index(drop=True)
    if "volume" not in df.columns:
        df["volume"] = pd.to_numeric(df.get("tick_volume", 1.0), errors="coerce").fillna(1.0)
    return df[["open", "high", "low", "close", "volume"]].to_dict("records")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--train-frac", type=float, default=0.5)
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--label-frac", type=float, default=0.5,
                    help="forward move must exceed label_frac x ATR to count as profitable")
    ap.add_argument("--embargo-mult", type=float, default=2.0,
                    help="gap between train and test, in multiples of the horizon")
    ap.add_argument("--out", default=os.path.join(REPO, "data", "models", "meta_labeler.joblib"))
    args = ap.parse_args()

    symbols = sorted(d for d in os.listdir(REAL_DIR) if os.path.isdir(os.path.join(REAL_DIR, d)))
    ml = MetaLabeler(model_path=args.out)

    Xtr, ytr, Xte, yte = [], [], [], []
    per_symbol = {}
    for sym in symbols:
        candles = load_candles(sym, args.tf)
        if not candles:
            continue
        n = len(candles)
        cut = int(n * args.train_frac)
        embargo = int(args.horizon * args.embargo_mult)
        # Training labels look `horizon` bars ahead, so the last training bar
        # must sit at least `horizon` before the cut; the test set starts a
        # further `embargo` after it.
        train = candles[: max(0, cut - args.horizon)]
        test = candles[cut + embargo:]
        if len(train) < ml.MIN_WINDOW + args.horizon + 50 or len(test) < ml.MIN_WINDOW + args.horizon + 20:
            per_symbol[sym] = "insufficient history"
            continue
        x1, y1 = ml.build_dataset(train, horizon=args.horizon, label_frac=args.label_frac)
        x2, y2 = ml.build_dataset(test, horizon=args.horizon, label_frac=args.label_frac)
        if len(x1) == 0 or len(x2) == 0:
            per_symbol[sym] = "no samples"
            continue
        Xtr.append(x1); ytr.append(y1); Xte.append(x2); yte.append(y2)
        per_symbol[sym] = {"train": int(len(y1)), "test": int(len(y2)),
                           "train_pos": round(float(np.mean(y1)), 4),
                           "test_pos": round(float(np.mean(y2)), 4)}

    if not Xtr:
        print("no training data")
        return 1

    Xtr = np.vstack(Xtr); ytr = np.concatenate(ytr)
    Xte = np.vstack(Xte); yte = np.concatenate(yte)
    print(f"train {Xtr.shape[0]:,} samples (pos {np.mean(ytr):.3f}) | "
          f"test {Xte.shape[0]:,} samples (pos {np.mean(yte):.3f})", flush=True)

    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score, brier_score_loss

    model = HistGradientBoostingClassifier(
        max_depth=4, learning_rate=0.05, max_iter=300, l2_regularization=1.0, random_state=42
    )
    model.fit(Xtr, ytr)

    p_te = model.predict_proba(Xte)[:, list(model.classes_).index(1)]
    p_tr = model.predict_proba(Xtr)[:, list(model.classes_).index(1)]
    auc = float(roc_auc_score(yte, p_te))
    brier = float(brier_score_loss(yte, p_te))
    print(f"test  AUC {auc:.4f} | Brier {brier:.4f} | base rate {np.mean(yte):.4f}")
    print(f"train AUC {float(roc_auc_score(ytr, p_tr)):.4f} (overfit check)")

    # Does the probability separate? Decile lift on held-out data.
    print("\nheld-out decile lift (P(profitable) bucket -> realised hit rate):")
    qs = np.quantile(p_te, np.linspace(0, 1, 11))
    for k in range(10):
        m = (p_te >= qs[k]) & (p_te <= qs[k + 1]) if k == 9 else (p_te >= qs[k]) & (p_te < qs[k + 1])
        if m.sum() < 10:
            continue
        print(f"  decile {k + 1:2d}  n={m.sum():7d}  mean p={p_te[m].mean():.3f}  "
              f"actual={yte[m].mean():.3f}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    import joblib
    joblib.dump(model, args.out)

    summary = {
        "out": args.out,
        "tf": args.tf,
        "train_samples": int(len(ytr)),
        "test_samples": int(len(yte)),
        "test_auc": auc,
        "test_brier": brier,
        "train_auc": float(roc_auc_score(ytr, p_tr)),
        "base_rate_test": float(np.mean(yte)),
        "horizon": args.horizon,
        "label_frac": args.label_frac,
        "embargo_bars": int(args.horizon * args.embargo_mult),
        "per_symbol": per_symbol,
    }
    out_json = os.path.splitext(args.out)[0] + "_report.json"
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(f"\nwrote {args.out}\nwrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

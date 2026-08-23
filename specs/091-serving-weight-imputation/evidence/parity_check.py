"""Parity oracle: the vectorised fast path in killtest.py must reproduce serving's
predict_race() win probabilities exactly, otherwise the measurement is meaningless.

Also checks the risk that per-race pandas `astype("category")` (what predict_race does)
and global casting (what the fast path does) give different booster inputs.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent))
from killtest import load_model, predict_win  # noqa: E402

from horseracing_db.session import create_db_engine  # noqa: E402
from horseracing_serving.model_loader import load_serving_model  # noqa: E402
from horseracing_serving.predictor import predict_race  # noqa: E402

SCRATCH = Path(__file__).parent
N_SAMPLE = 40


def main() -> int:
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
    )
    feats = pd.read_parquet(SCRATCH / "feats.parquet")
    feats = feats.sort_values(["race_id", "horse_id"], kind="mergesort").reset_index(drop=True)

    rng = np.random.default_rng(20260809)
    all_races = feats["race_id"].drop_duplicates().tolist()
    sample = sorted(rng.choice(all_races, size=N_SAMPLE, replace=False).tolist())

    sub = feats[feats["race_id"].isin(sample)].copy()
    sub = sub.sort_values(["race_id", "horse_id"], kind="mergesort").reset_index(drop=True)
    group_sizes = sub.groupby("race_id", sort=False).size().tolist()

    booster, prep, calibrator, meta = load_model()
    fast = predict_win(sub, group_sizes, booster, prep, calibrator)

    engine = create_db_engine()
    with Session(engine) as session:
        model = load_serving_model(session, "lgbm-064-f02acc")

    max_abs = 0.0
    n_exact = 0
    n_total = 0
    for rid in sample:
        preds, _, _, _ = predict_race(model, rid, feats)
        mask = sub["race_id"].to_numpy() == rid
        hids = sub.loc[mask, "horse_id"].tolist()
        ref = np.array([preds[h].win for h in hids], dtype=float)
        got = fast[mask]
        d = np.abs(ref - got)
        max_abs = max(max_abs, float(d.max()))
        n_exact += int((ref == got).sum())
        n_total += len(hids)

    print(f"sampled races: {len(sample)}  horses: {n_total}")
    print(f"bit-exact horses: {n_exact}/{n_total}")
    print(f"max |fast - predict_race|: {max_abs:.3e}")
    ok = max_abs == 0.0
    print("PARITY: " + ("EXACT (bit-identical)" if ok else
                        ("close but not bit-exact" if max_abs < 1e-12 else "FAILED")))
    return 0 if max_abs < 1e-12 else 1


if __name__ == "__main__":
    sys.exit(main())

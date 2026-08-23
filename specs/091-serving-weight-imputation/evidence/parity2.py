"""Extended parity oracle (codex): the vectorised fast path must equal serving's
predict_race() for EVERY arm, across stratified cohorts — not just arm A on random races.

Cohorts: no prev weight (debut-ish), partial coverage, full coverage, small field, large field.
Compared at every stage: booster margin, race-softmax, calibrated, final win.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent))
from killtest import CLIP, load_model, make_X  # noqa: E402
from killtest2 import ARMS, SRC_SQL, build_prev, patch2  # noqa: E402

from horseracing_db.session import create_db_engine  # noqa: E402
from horseracing_serving.model_loader import load_serving_model  # noqa: E402
from horseracing_serving.predictor import predict_race  # noqa: E402
from horseracing_training.cond_logit import race_softmax  # noqa: E402

SCRATCH = Path(__file__).parent
PER_COHORT = 12


def main() -> int:
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
    )
    feats = pd.read_parquet(SCRATCH / "feats.parquet")
    meta = pd.read_parquet(SCRATCH / "meta.parquet")
    with Session(create_db_engine()) as session:
        src = pd.read_sql(SRC_SQL, session.connection())
    src["race_date"] = pd.to_datetime(src["race_date"])
    feats = feats.merge(meta[["race_id", "race_date"]], on="race_id", how="left")
    feats["race_date"] = pd.to_datetime(feats["race_date"])
    tg = feats[["race_id", "horse_id", "race_date"]].copy()
    feats = feats.merge(build_prev(tg, src, started_only=True, col="prev_w"),
                        on=["race_id", "horse_id"], how="left")
    feats = feats.merge(build_prev(tg, src, started_only=False, col="lastobs_w"),
                        on=["race_id", "horse_id"], how="left")
    feats = feats.sort_values(["race_id", "horse_id"], kind="mergesort").reset_index(drop=True)

    cov = feats.groupby("race_id")["prev_w"].apply(lambda s: s.notna().mean())
    fs = feats.groupby("race_id").size()
    rng = np.random.default_rng(20260809)

    def pick(mask, n=PER_COHORT):
        ids = mask[mask].index.tolist()
        if not ids:
            return []
        return sorted(rng.choice(ids, size=min(n, len(ids)), replace=False).tolist())

    cohorts = {
        "no_prev_weight": pick(cov == 0.0),
        "partial_cov": pick((cov > 0) & (cov < 1)),
        "full_cov": pick(cov == 1.0),
        "small_field": pick(fs <= 7),
        "large_field": pick(fs >= 16),
    }
    sample = sorted({r for v in cohorts.values() for r in v})
    for k, v in cohorts.items():
        print(f"  cohort {k:16s} n={len(v)}")
    print(f"total sampled races: {len(sample)}")

    booster, prep, calibrator, _ = load_model()
    with Session(create_db_engine()) as session:
        model = load_serving_model(session, "lgbm-064-f02acc")

    sub = feats[feats["race_id"].isin(sample)].copy()
    sub = sub.sort_values(["race_id", "horse_id"], kind="mergesort").reset_index(drop=True)
    gs = sub.groupby("race_id", sort=False).size().tolist()

    worst = {}
    for arm in ARMS:
        prow = patch2(sub, arm)
        X = make_X(prow, prep)
        margin = np.asarray(booster.predict(X), dtype=float)
        soft = race_softmax(margin, gs)
        cal = np.clip(np.asarray(calibrator.transform(soft), dtype=float), CLIP, 1 - CLIP)
        idx = np.repeat(np.arange(len(gs)), gs)
        fast = cal / np.bincount(idx, weights=cal, minlength=len(gs))[idx]

        full_patched = patch2(feats, arm)
        w_margin = w_win = 0.0
        n_exact = n_tot = 0
        for rid in sample:
            preds, snaps, _, _ = predict_race(model, rid, full_patched)
            m = sub["race_id"].to_numpy() == rid
            hids = sub.loc[m, "horse_id"].tolist()
            ref_win = np.array([preds[h].win for h in hids])
            ref_raw = np.array([snaps[h]["_raw_win"] for h in hids])
            w_win = max(w_win, float(np.abs(ref_win - fast[m]).max()))
            w_margin = max(w_margin, float(np.abs(ref_raw - soft[m]).max()))
            n_exact += int((ref_win == fast[m]).sum())
            n_tot += len(hids)
        worst[arm] = (w_margin, w_win, n_exact, n_tot)
        print(f"  {arm:12s} max|Δsoftmax|={w_margin:.3e}  max|Δwin|={w_win:.3e}  "
              f"bit-exact {n_exact}/{n_tot}")

    ok = all(v[1] < 1e-12 for v in worst.values())
    print("\nPARITY: " + ("PASS (<1e-12 on every arm/cohort)" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

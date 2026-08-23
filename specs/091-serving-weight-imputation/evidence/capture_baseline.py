"""T001 / T002: capture the pre-change baseline.

Everything later in the feature compares against this:
  T001 -> shared-column snapshot + source_fingerprint under features-018 (INV-W7 / INV-W8)
  T002 -> full-precision win probs of the active model (INV-W9)

Run BEFORE adding prev_weight to the registry.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from horseracing_db.session import create_db_engine
from horseracing_features.builder import build_feature_matrix
from horseracing_features.loader import load_frames
from horseracing_features.materialize import source_fingerprint
from horseracing_features.registry import FEATURE_VERSION, model_input_features
from horseracing_serving.model_loader import load_serving_model
from horseracing_serving.predictor import predict_race

OUT = Path(__file__).parent
MODEL = "lgbm-064-f02acc"
# representative races: ordinary / small field / contains a debut horse
RACES = ["202504040301", "202501010106", "202502010101"]


def frame_digest(df: pd.DataFrame) -> str:
    """Value-canonical digest of the whole frame (same spirit as fp-v2)."""
    h = hashlib.sha256()
    for col in sorted(df.columns):
        h.update(col.encode())
        h.update(df[col].astype("string").fillna("").str.cat(sep="\x1f").encode())
    return h.hexdigest()


def main() -> int:
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
    )
    engine = create_db_engine()
    t0 = time.time()

    with Session(engine) as session:
        print(f"[T001] building feature matrix under {FEATURE_VERSION} ...", flush=True)
        fm = build_feature_matrix(session)
        cols = sorted(fm.columns)
        model_cols = sorted(model_input_features())
        frames = load_frames(session)
        fp = source_fingerprint(frames)
        base = {
            "feature_version": FEATURE_VERSION,
            "shape": list(fm.shape),
            "n_model_input_features": len(model_cols),
            "columns": cols,
            "model_input_features": model_cols,
            "source_fingerprint": fp,
            "frame_digest": frame_digest(fm),
            "per_column_digest": {
                c: hashlib.sha256(
                    fm[c].astype("string").fillna("").str.cat(sep="\x1f").encode()
                ).hexdigest()
                for c in cols
            },
        }
        json.dump(base, open(OUT / "baseline_features018.json", "w"), indent=2)
        print(f"    {fm.shape} cols={len(cols)} model_input={len(model_cols)} "
              f"fingerprint={fp[:12]} ({time.time() - t0:.1f}s)", flush=True)

        print(f"[T002] capturing {MODEL} predictions ...", flush=True)
        model = load_serving_model(session, MODEL)
        preds_out: dict = {"model_version": MODEL, "feature_version": FEATURE_VERSION, "races": {}}
        for rid in RACES:
            rows = fm[fm["race_id"] == rid]
            if rows.empty:
                print(f"    {rid}: no rows, skipped", flush=True)
                continue
            preds, _, _, _ = predict_race(model, rid, fm)
            preds_out["races"][rid] = {
                h: {"win": repr(p.win), "top2": repr(p.top2), "top3": repr(p.top3)}
                for h, p in sorted(preds.items())
            }
            print(f"    {rid}: {len(preds)} horses", flush=True)
        json.dump(preds_out, open(OUT / "baseline_lgbm064_predictions.json", "w"), indent=2)

    print(f"done in {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

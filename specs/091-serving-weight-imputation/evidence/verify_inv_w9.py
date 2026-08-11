"""T038 / INV-W9 / SC-004: the active model's predictions must be byte-identical under features-020.

lgbm-064-f02acc was trained on features-018. Adding prev_weight changes feature_hash, so it now
reaches serving through the COMPAT path. If a single bit moves, the additive-merge claim is false.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from horseracing_db.session import create_db_engine
from horseracing_features.builder import build_feature_matrix
from horseracing_features.registry import FEATURE_VERSION
from horseracing_serving.model_loader import load_serving_model
from horseracing_serving.predictor import predict_race

BASE = Path(__file__).parent / "baseline_lgbm064_predictions.json"
MODEL = "lgbm-064-f02acc"


def main() -> int:
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
    )
    baseline = json.loads(BASE.read_text())
    print(f"baseline captured under {baseline['feature_version']}, now on {FEATURE_VERSION}")
    assert baseline["feature_version"] != FEATURE_VERSION, "baseline was captured post-bump"

    with Session(create_db_engine()) as session:
        fm = build_feature_matrix(session)
        model = load_serving_model(session, MODEL)
        print(f"loaded {MODEL}: trained feature_version={model.feature_version} "
              f"(compat path exercised)")

        total = mismatch = 0
        for rid, want in baseline["races"].items():
            preds, _, _ = predict_race(model, rid, fm)
            for hid, exp in want.items():
                total += 1
                got = preds[hid]
                if (repr(got.win), repr(got.top2), repr(got.top3)) != (
                    exp["win"], exp["top2"], exp["top3"]
                ):
                    mismatch += 1
                    if mismatch <= 3:
                        print(f"  MISMATCH {rid}/{hid}: {exp['win']} -> {repr(got.win)}")
            print(f"  {rid}: {len(want)} horses checked")

    print(f"\nhorses compared: {total} | mismatches: {mismatch}")
    print("INV-W9 / SC-004: " + ("PASS (byte-identical)" if mismatch == 0 else "FAIL"))
    return 0 if mismatch == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

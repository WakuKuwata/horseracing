"""Append-only persistence of one serving run (data-model.md).

Writes prediction_runs (parent) then race_predictions + feature_snapshots (children), each
run a fresh uuid (no destructive upsert — audit trail). A float monotonic repair + Decimal
conversion guarantees the DB ``PROB_MONOTONIC`` check (0<=win<=top2<=top3<=1) cannot trip on
floating-point near-ties (codex guard).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from horseracing_db.models import FeatureSnapshot, PredictionRun, RacePrediction
from horseracing_eval.predictor import Prediction
from sqlalchemy.orm import Session


def _dec(x: float) -> Decimal:
    return Decimal(str(x))


def _monotone(p: Prediction) -> tuple[float, float, float]:
    win = min(max(p.win, 0.0), 1.0)
    top2 = min(max(p.top2, win), 1.0)
    top3 = min(max(p.top3, top2), 1.0)
    return win, top2, top3


def persist_run(
    session: Session,
    *,
    race_id: str,
    model_version: str,
    logic_version: str,
    feature_version: str,
    predictions: dict[str, Prediction],
    snapshots: dict[str, dict],
) -> uuid.UUID:
    run = PredictionRun(
        race_id=race_id, model_version=model_version, logic_version=logic_version
    )
    session.add(run)
    session.flush()  # populate prediction_run_id (server_default gen_random_uuid) before children
    run_id = run.prediction_run_id

    for horse_id, pred in predictions.items():
        win, top2, top3 = _monotone(pred)
        session.add(
            RacePrediction(
                prediction_run_id=run_id, horse_id=horse_id,
                win_prob=_dec(win), top2_prob=_dec(top2), top3_prob=_dec(top3),
            )
        )
        session.add(
            FeatureSnapshot(
                prediction_run_id=run_id, horse_id=horse_id,
                feature_version=feature_version, features=snapshots[horse_id],
            )
        )
    session.commit()
    return run_id

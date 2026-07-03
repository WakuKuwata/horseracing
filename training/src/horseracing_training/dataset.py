"""Build the training matrix: leak-safe features (Feature 004) + win label (R3/R10).

Population = started horses (``build_feature_matrix`` already excludes cancelled/excluded).
Win label is derived directly from ``race_results`` — NOT ``labels.derive_labels`` (which is
finished-only, for evaluation scoring). Training is **started-all, DNF=0**:

    win = 1  if result_status == 'finished' and finish_order == 1
          0  otherwise (no result row, stopped, disqualified, not 1st)

The whole matrix is built once with ``end_date=None``: history features are computed
as-of each row's own ``race_date`` (strictly past), so a row's features are identical
whether or not later races exist in the frame (leak-safe, research R4). No target
encoding in the MVP, so no cross-row label leakage is possible.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

import pandas as pd
from horseracing_db.enums import ResultStatus
from horseracing_db.models import Race, RaceResult
from horseracing_features.builder import build_feature_matrix
from horseracing_features.registry import model_input_features
from sqlalchemy import select
from sqlalchemy.orm import Session

#: model-input feature columns that are categorical (text identifiers / codes).
#: LightGBM consumes these as pandas ``category`` dtype. The remaining model inputs
#: are numeric (NaN kept distinct from 0 per the registry's missing policy).
CATEGORICAL_FEATURES: tuple[str, ...] = (
    "venue_code",
    "track_type",
    "going",
    "weather",
    "race_class",
    "sex",
    "jockey_id",
    "trainer_id",
    # Feature 055: bloodline lines (low-cardinality ~20 values)
    "sire_line",
    "damsire_line",
)

WIN_LABEL = "win"
#: Feature 042: finishing-rank LABEL (1..3 for finished top-3, else 0) for the PL top-k
#: objective. Label-side only (same leak boundary as ``win``) — never a model feature.
RANK_LABEL = "finish_rank"
RACE_DATE = "race_date"


@dataclass(frozen=True)
class TrainingMatrix:
    """Full per-(race, started-horse) matrix with features, win label and race_date.

    ``frame`` columns: race_id, horse_id, <feature_cols...>, race_date, win.
    Indexed positionally; callers select rows by race_id sets (race-level splits).
    """

    frame: pd.DataFrame
    feature_cols: list[str]
    categorical_cols: list[str]


def _win_pairs(session: Session) -> set[tuple[str, str]]:
    """(race_id, horse_id) of finished 1st-place horses (the only win=1 rows)."""
    rows = session.execute(
        select(RaceResult.race_id, RaceResult.horse_id)
        .where(RaceResult.result_status == ResultStatus.FINISHED)
        .where(RaceResult.finish_order == 1)
    ).all()
    return {(r.race_id, r.horse_id) for r in rows}


def _rank_map(session: Session) -> dict[tuple[str, str], int]:
    """Feature 042: (race_id, horse_id) -> finishing rank (1..3) of finished top-3 horses."""
    rows = session.execute(
        select(RaceResult.race_id, RaceResult.horse_id, RaceResult.finish_order)
        .where(RaceResult.result_status == ResultStatus.FINISHED)
        .where(RaceResult.finish_order <= 3)
    ).all()
    return {(r.race_id, r.horse_id): int(r.finish_order) for r in rows}


def _race_dates(session: Session) -> dict[str, datetime.date]:
    rows = session.execute(select(Race.race_id, Race.race_date)).all()
    return {r.race_id: r.race_date for r in rows}


def build_training_matrix(
    session: Session,
    *,
    end_date: datetime.date | None = None,
) -> TrainingMatrix:
    """Assemble the started-population feature matrix joined with race_date + win label."""
    matrix = build_feature_matrix(session, end_date=end_date)
    feature_cols = model_input_features()

    race_dates = _race_dates(session)
    winners = _win_pairs(session)
    ranks = _rank_map(session)

    df = matrix.copy()
    df[RACE_DATE] = df["race_id"].map(race_dates)
    df[WIN_LABEL] = [
        1 if (rid, hid) in winners else 0
        for rid, hid in zip(df["race_id"], df["horse_id"], strict=True)
    ]
    # Feature 042: finishing-rank label for pl_topk (label-side only, not a feature)
    df[RANK_LABEL] = [
        ranks.get((rid, hid), 0)
        for rid, hid in zip(df["race_id"], df["horse_id"], strict=True)
    ]

    categorical_cols = [c for c in CATEGORICAL_FEATURES if c in feature_cols]
    for col in categorical_cols:
        df[col] = df[col].astype("category")
    # Non-categorical model inputs must be numeric for LightGBM: coerce Decimal -> float and
    # all-None columns (object dtype) -> float NaN. NaN stays distinct from 0 (missing policy).
    numeric_cols = [c for c in feature_cols if c not in categorical_cols]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return TrainingMatrix(
        frame=df, feature_cols=feature_cols, categorical_cols=categorical_cols
    )

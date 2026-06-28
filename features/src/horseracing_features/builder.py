"""Assemble the fixed-schema FeatureMatrix (static + history) with registry enforcement."""

from __future__ import annotations

import datetime

import pandas as pd
from horseracing_db.enums import EntryStatus
from horseracing_db.validation import INGEST_SCOPE_START
from sqlalchemy.orm import Session

from .extra_features import build_extra_features
from .history import build_history_features
from .human_form import build_human_form_features
from .loader import Frames, load_frames
from .pace_features import build_pace_features
from .registry import validate_columns
from .schema import ALL_COLUMNS, DEFAULT_LOW_HISTORY_MAX
from .static_features import build_static_features


def assemble_feature_matrix(
    frames: Frames,
    *,
    start_date: datetime.date = INGEST_SCOPE_START,
    end_date: datetime.date | None = None,
    low_history_max: int = DEFAULT_LOW_HISTORY_MAX,
) -> pd.DataFrame:
    """Build the fixed-schema FeatureMatrix from in-memory Frames (DB-independent).

    Population = started horses of target races in [start_date, end_date]. History uses
    the full pool (as-of race_date < R). Deterministic (stable sort by race_id, horse_id).
    """
    static = build_static_features(frames)
    history = build_history_features(frames, low_history_max=low_history_max)
    extra = build_extra_features(frames)            # Feature 020: recent form / aptitude / class
    human = build_human_form_features(frames)        # Feature 020: jockey / trainer as-of form
    pace = build_pace_features(frames)               # Feature 023: pace/time (as-of, in-race rel)
    fm = (static
          .merge(history, on=["race_id", "horse_id"], how="left")
          .merge(extra, on=["race_id", "horse_id"], how="left")
          .merge(human, on=["race_id", "horse_id"], how="left")
          .merge(pace, on=["race_id", "horse_id"], how="left"))

    races = frames.races[["race_id", "race_date"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    status = frames.race_horses[["race_id", "horse_id", "entry_status"]]
    fm = fm.merge(races, on="race_id", how="left")
    fm = fm.merge(status, on=["race_id", "horse_id"], how="left")

    fm = fm[fm["entry_status"] == EntryStatus.STARTED]  # 取消・除外を除外
    fm = fm[fm["race_date"] >= pd.Timestamp(start_date)]
    if end_date is not None:
        fm = fm[fm["race_date"] <= pd.Timestamp(end_date)]

    fm = fm.sort_values(["race_id", "horse_id"], kind="stable").reset_index(drop=True)
    matrix = fm[list(ALL_COLUMNS)].copy()
    validate_columns(list(matrix.columns))
    return matrix


def build_feature_matrix(
    session: Session,
    *,
    start_date: datetime.date = INGEST_SCOPE_START,
    end_date: datetime.date | None = None,
    low_history_max: int = DEFAULT_LOW_HISTORY_MAX,
) -> pd.DataFrame:
    frames = load_frames(session, end_date=end_date)
    return assemble_feature_matrix(
        frames, start_date=start_date, end_date=end_date, low_history_max=low_history_max
    )

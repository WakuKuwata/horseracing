"""Assemble the fixed-schema FeatureMatrix (static + history) with registry enforcement."""

from __future__ import annotations

import datetime
from pathlib import Path

import pandas as pd
from horseracing_db.enums import EntryStatus
from horseracing_db.validation import INGEST_SCOPE_START
from sqlalchemy.orm import Session

from .loader import Frames, load_frames
from .materialize import (
    assert_fresh,
    build_asof_features,
    has_future_rows,
    read_materialized,
)
from .registry import validate_columns
from .schema import ALL_COLUMNS, DEFAULT_LOW_HISTORY_MAX
from .static_features import build_static_features


def _asof_block(
    frames: Frames, *, low_history_max: int, start_date, end_date,
    materialized_path: Path | None, use_materialized: bool,
    fingerprint_frames: Frames | None = None,
):
    """The as-of feature block: from materialized parquet (fast, opt-in) or computed in-memory.

    Feature 025: a single as-of source (`build_asof_features`) is used for both the in-memory path
    and the fallback, so generator/builder/fallback never drift. When ``use_materialized`` is on,
    the parquet is fail-closed-verified (fingerprint over the materialized range); any in-range
    change/backfill raises, while in-scope races BEYOND the materialized range (serving new races)
    fall back to the same in-memory computation.
    """
    if use_materialized and materialized_path is not None:
        df, manifest = read_materialized(materialized_path)   # raises if missing
        # Staleness is verified over the FULL materialized range (fingerprint_frames), not the
        # end_date-restricted `frames` — otherwise an end_date < data_through would mismatch the
        # full-pool manifest. The rest (static/population/fallback) uses windowed `frames` so static
        # dtypes don't depend on rows beyond end_date (parity).
        assert_fresh(manifest, fingerprint_frames if fingerprint_frames is not None else frames)
        if has_future_rows(frames, manifest, start_date=start_date, end_date=end_date):
            return build_asof_features(frames, low_history_max=low_history_max)  # serving fallback
        return df                                             # parquet fast path
    return build_asof_features(frames, low_history_max=low_history_max)


def assemble_feature_matrix(
    frames: Frames,
    *,
    start_date: datetime.date = INGEST_SCOPE_START,
    end_date: datetime.date | None = None,
    low_history_max: int = DEFAULT_LOW_HISTORY_MAX,
    materialized_path: Path | None = None,
    use_materialized: bool = False,
    fingerprint_frames: Frames | None = None,
) -> pd.DataFrame:
    """Build the fixed-schema FeatureMatrix from in-memory Frames (DB-independent).

    Population = started horses of target races in [start_date, end_date]. History uses
    the full pool (as-of race_date < R). Deterministic (stable sort by race_id, horse_id).

    Feature 025: ``use_materialized`` reads the as-of block from ``materialized_path`` (parquet)
    when fresh & covered; otherwise/by default it is computed in-memory. Output is identical
    either way (parity gate) — static/current-race features are always computed here.
    ``fingerprint_frames`` (full materialized-range pool) is used ONLY for the staleness check when
    ``use_materialized``; ``frames`` stays end_date-windowed so static dtypes are pool-independent.
    """
    static = build_static_features(frames)
    asof = _asof_block(
        frames, low_history_max=low_history_max, start_date=start_date, end_date=end_date,
        materialized_path=materialized_path, use_materialized=use_materialized,
        fingerprint_frames=fingerprint_frames,
    )
    fm = static.merge(asof, on=["race_id", "horse_id"], how="left")

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
    materialized_path: Path | None = None,
    use_materialized: bool = False,
) -> pd.DataFrame:
    # Always load the end_date-windowed pool for static/population/as-of: as-of values for races
    # <= end_date only look strictly before each race, and windowed loading keeps static dtypes
    # independent of rows beyond end_date (parity). When using materialized parquet, also load the
    # FULL pool ONLY to verify the staleness fingerprint over the whole materialized range (the
    # manifest was generated over the full pool); this never feeds feature values.
    frames = load_frames(session, end_date=end_date)
    # full-pool frames for the staleness fingerprint only; reuse `frames` when already unrestricted.
    fp_frames = None
    if use_materialized:
        fp_frames = frames if end_date is None else load_frames(session, end_date=None)
    return assemble_feature_matrix(
        frames, start_date=start_date, end_date=end_date, low_history_max=low_history_max,
        materialized_path=materialized_path, use_materialized=use_materialized,
        fingerprint_frames=fp_frames,
    )

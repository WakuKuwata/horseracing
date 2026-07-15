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
    MaterializationError,
    _skipped_columns,
    assert_fresh,
    assert_manifest_compatible,
    build_asof_features,
    has_future_rows,
    read_manifest,
    read_materialized,
    skip_blocks_for_wanted,
)
from .registry import FeatureSchemaError, validate_columns
from .schema import ALL_COLUMNS, DEFAULT_LOW_HISTORY_MAX
from .static_features import build_static_features


def _asof_block(
    frames: Frames, *, low_history_max: int, start_date, end_date,
    materialized_path: Path | None, use_materialized: bool,
    fingerprint_frames: Frames | None = None,
    skip_fingerprint_verify: bool = False,
    skip_blocks: frozenset[str] = frozenset(),
    target_race_ids: frozenset[str] | None = None,
):
    """The as-of feature block: from materialized parquet (fast, opt-in) or computed in-memory.

    Feature 025: a single as-of source (`build_asof_features`) is used for both the in-memory path
    and the fallback, so generator/builder/fallback never drift. When ``use_materialized`` is on,
    the parquet is fail-closed-verified (fingerprint over the materialized range); any in-range
    change/backfill raises, while in-scope races BEYOND the materialized range (serving new races)
    fall back to the same in-memory computation.

    Feature 055: ``skip_fingerprint_verify`` skips ONLY the fingerprint comparison (a backfill run
    verifies once up front via ``verify_materialized``); the frame-free compatibility checks
    (feature_version / fingerprint algo) still run every time.
    """
    if use_materialized:
        if materialized_path is None:  # fail-closed: never silently degrade to in-memory (FR-002)
            raise MaterializationError("use_materialized=True requires materialized_path")
        df, manifest = read_materialized(materialized_path)   # raises if missing
        if skip_fingerprint_verify:
            assert_manifest_compatible(manifest)
        else:
            # fp-v2 is value-canonical, so any frames covering data_through verify identically —
            # the caller passes the windowed frames (+ delta) instead of a second full-pool load.
            assert_fresh(manifest, fingerprint_frames if fingerprint_frames is not None else frames)
        if has_future_rows(frames, manifest, start_date=start_date, end_date=end_date):
            return build_asof_features(  # serving fallback
                frames, low_history_max=low_history_max, skip_blocks=skip_blocks,
                target_race_ids=target_race_ids,
            )
        return df           # parquet fast path (full matrix; projection happens at final selection)
    return build_asof_features(
        frames, low_history_max=low_history_max, skip_blocks=skip_blocks,
        target_race_ids=target_race_ids,
    )


def assemble_feature_matrix(
    frames: Frames,
    *,
    start_date: datetime.date = INGEST_SCOPE_START,
    end_date: datetime.date | None = None,
    low_history_max: int = DEFAULT_LOW_HISTORY_MAX,
    materialized_path: Path | None = None,
    use_materialized: bool = False,
    fingerprint_frames: Frames | None = None,
    skip_fingerprint_verify: bool = False,
    wanted: frozenset[str] | None = None,
    target_race_ids: frozenset[str] | None = None,
) -> pd.DataFrame:
    """Build the fixed-schema FeatureMatrix from in-memory Frames (DB-independent).

    Population = started horses of target races in [start_date, end_date]. History uses
    the full pool (as-of race_date < R). Deterministic (stable sort by race_id, horse_id).

    Feature 025: ``use_materialized`` reads the as-of block from ``materialized_path`` (parquet)
    when fresh & covered; otherwise/by default it is computed in-memory. Output is identical
    either way (parity gate) — static/current-race features are always computed here.
    ``fingerprint_frames`` must cover races through the manifest's data_through and is used ONLY
    for the staleness check; ``frames`` stays end_date-windowed so static dtypes are
    pool-independent. Feature 055: ``skip_fingerprint_verify`` for verify-once backfill runs.

    ``wanted`` (default None = full fixed schema) is the set of columns the caller actually needs
    (serving passes ``model.feature_cols``). When given, optional LEAF blocks whose columns are all
    outside ``wanted`` are skipped (see ``skip_blocks_for_wanted``); the returned matrix drops those
    columns but is byte-identical to the full matrix on every column it keeps. Materialize /
    training / backfill leave it None, so their output is byte-unchanged.
    """
    skip_blocks = skip_blocks_for_wanted(wanted)
    static = build_static_features(frames)
    if target_race_ids is not None:
        # Feature 072: restrict the row base to target races BEFORE merging the projected
        # (target-only) as-of block. Otherwise the left-merge of full `static` with the target-only
        # `asof` NaN-fills non-target rows and upcasts int columns (e.g. career_starts int64->
        # float64); the later population slice cannot undo a sticky dtype. Static columns are
        # computed over the full frame (dtypes pool-correct) and only ROW-filtered here, so
        # values/dtypes stay byte-identical.
        static = static[static["race_id"].isin(target_race_ids)]
    asof = _asof_block(
        frames, low_history_max=low_history_max, start_date=start_date, end_date=end_date,
        materialized_path=materialized_path, use_materialized=use_materialized,
        fingerprint_frames=fingerprint_frames, skip_fingerprint_verify=skip_fingerprint_verify,
        skip_blocks=skip_blocks, target_race_ids=target_race_ids,
    )
    fm = static.merge(asof, on=["race_id", "horse_id"], how="left")
    # Feature 056: prize_rel = today's prize level − the horse's as-of prize class (昇降級度合い).
    # Composed here because it mixes a static with an as-of column; NaN-propagating (憲法 IV).
    fm["prize_rel"] = (fm["prize_money_log"] - fm["asof_prize_avg"]).astype("float64")

    races = frames.races[["race_id", "race_date"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    status = frames.race_horses[["race_id", "horse_id", "entry_status"]]
    fm = fm.merge(races, on="race_id", how="left")
    fm = fm.merge(status, on=["race_id", "horse_id"], how="left")

    fm = fm[fm["entry_status"] == EntryStatus.STARTED]  # 取消・除外を除外
    fm = fm[fm["race_date"] >= pd.Timestamp(start_date)]
    if end_date is not None:
        fm = fm[fm["race_date"] <= pd.Timestamp(end_date)]
    if target_race_ids is not None:  # Feature 072: emit only the target races' rows (INV-P1)
        fm = fm[fm["race_id"].isin(target_race_ids)]

    fm = fm.sort_values(["race_id", "horse_id"], kind="stable").reset_index(drop=True)
    skipped = _skipped_columns(skip_blocks)
    output_cols = [c for c in ALL_COLUMNS if c not in skipped]
    if wanted is not None and not wanted.issubset(output_cols):
        missing = sorted(wanted.difference(output_cols))  # fail-closed: never serve a short matrix
        raise FeatureSchemaError(f"wanted columns not in projected matrix: {missing}")
    matrix = fm[list(output_cols)].copy()
    validate_columns(list(matrix.columns))
    return matrix


def _fingerprint_frames_for(
    session: Session, frames: Frames, *, end_date: datetime.date | None,
    materialized_path: Path,
) -> Frames:
    """Frames covering the materialized range for staleness verification (Feature 055).

    fp-v1 required a second FULL-pool load (hash was dtype-sensitive and had to match the
    materialize-time load exactly). fp-v2 is value-canonical, so:
    - end_date is None or >= data_through: the windowed ``frames`` already cover the range — reuse
      (zero extra load; ``source_fingerprint`` restricts to data_through internally).
    - end_date < data_through: load ONLY the (end_date, data_through] delta and concat. The horses
      table is loaded date-unfiltered by every load_frames call, so the delta's copy is dropped
      (concat would duplicate rows and flip the hash); ``_restrict`` filters horses by runners.
    """
    manifest = read_manifest(materialized_path)
    assert_manifest_compatible(manifest)
    through = (
        datetime.date.fromisoformat(manifest.data_through) if manifest.data_through else None
    )
    if end_date is None or through is None or end_date >= through:
        return frames
    delta = load_frames(session, end_date=through, start_after=end_date)

    def _cat(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
        # skip empty parts: read_sql of zero rows yields all-object dtypes, and concatenating
        # them degrades float64 columns to object (the fp-v2 hash is value-canonical and thus
        # robust to that, but skipping keeps frames small and dtype-clean).
        if len(b) == 0:
            return a
        return a if len(a) == 0 else pd.concat([a, b], ignore_index=True)

    return Frames(
        races=_cat(frames.races, delta.races),
        race_horses=_cat(frames.race_horses, delta.race_horses),
        race_results=_cat(frames.race_results, delta.race_results),
        horses=frames.horses,  # full table in both loads — keep one copy (no duplicate rows)
    )


def verify_materialized(session: Session, materialized_path: str | Path | None) -> None:
    """One-shot fail-closed staleness verification (Feature 055).

    Backfill runs call this ONCE up front, then build per day with skip_fingerprint_verify=True —
    the fingerprint compares the same parquet against the same source state, so re-verifying every
    day only re-pays the load. Raises MaterializationError on missing/stale/incompatible."""
    if materialized_path is None:  # fail-closed (FR-002); mirrors build_feature_matrix
        raise MaterializationError("use_materialized=True requires materialized_path")
    manifest = read_manifest(Path(materialized_path))
    assert_manifest_compatible(manifest)
    through = (
        datetime.date.fromisoformat(manifest.data_through) if manifest.data_through else None
    )
    frames = load_frames(session, end_date=through)
    assert_fresh(manifest, frames)


def build_feature_matrix(
    session: Session,
    *,
    start_date: datetime.date = INGEST_SCOPE_START,
    end_date: datetime.date | None = None,
    low_history_max: int = DEFAULT_LOW_HISTORY_MAX,
    materialized_path: Path | None = None,
    use_materialized: bool = False,
    skip_fingerprint_verify: bool = False,
    wanted: frozenset[str] | None = None,
    target_race_ids: frozenset[str] | None = None,
) -> pd.DataFrame:
    # Always load the end_date-windowed pool for static/population/as-of: as-of values for races
    # <= end_date only look strictly before each race, and windowed loading keeps static dtypes
    # independent of rows beyond end_date (parity). Feature 055: fingerprint verification reuses
    # this load (+ a (end_date, data_through] delta when needed) — fp-v2 is value-canonical, so
    # the old second full-pool load is gone.
    frames = load_frames(session, end_date=end_date)
    fp_frames = None
    if use_materialized and not skip_fingerprint_verify:
        if materialized_path is None:  # fail-closed (FR-002); same error as _asof_block
            raise MaterializationError("use_materialized=True requires materialized_path")
        fp_frames = _fingerprint_frames_for(
            session, frames, end_date=end_date, materialized_path=Path(materialized_path),
        )
    return assemble_feature_matrix(
        frames, start_date=start_date, end_date=end_date, low_history_max=low_history_max,
        materialized_path=materialized_path, use_materialized=use_materialized,
        fingerprint_frames=fp_frames, skip_fingerprint_verify=skip_fingerprint_verify,
        wanted=wanted, target_race_ids=target_race_ids,
    )

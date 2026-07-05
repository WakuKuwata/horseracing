"""Feature 055: fp-v2 value-canonical fingerprint + verify-skip semantics (DB-free).

fp-v1 hashed raw dtypes (hash_pandas_object is dtype-sensitive), which forced staleness
verification to re-load the FULL pool exactly like materialize time. fp-v2 canonicalizes values
(numeric -> float64, other -> str) so equal VALUES hash equal regardless of the load window —
these tests pin that contract (the window-independence test FAILS under fp-v1).
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from horseracing_features.builder import assemble_feature_matrix
from horseracing_features.loader import Frames
from horseracing_features.materialize import (
    FINGERPRINT_ALGO,
    MaterializationError,
    source_fingerprint,
    write_materialized,
)
from tests._frames import make_frames

_EARLY = [
    {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
        {"horse_id": "H", "horse_number": 1, "last_3f": 34.0, "finish_order": 1},
        {"horse_id": "X", "horse_number": 2, "last_3f": 36.0, "finish_order": 2}]},
    {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [
        {"horse_id": "H", "horse_number": 1, "last_3f": 35.0, "finish_order": 1},
        {"horse_id": "X", "horse_number": 2, "last_3f": 37.0, "finish_order": 2}]},
]
_LATE = [
    {"race_id": "200803010101", "race_date": "2008-03-01", "horses": [
        {"horse_id": "H", "horse_number": 1, "last_3f": 35.0, "finish_order": 1},
        {"horse_id": "D", "horse_number": 2, "last_3f": 36.0, "finish_order": 2}]},
]
_THROUGH_EARLY = pd.Timestamp("2008-02-01").date()
_THROUGH_LATE = pd.Timestamp("2008-03-01").date()


def test_fingerprint_is_dtype_canonical():
    # same VALUES, different numeric dtype (int64 vs float64) -> same fingerprint (fp-v2 core)
    a = make_frames(_EARLY)
    b = make_frames(_EARLY)
    assert a.race_horses["weight"].dtype == "int64"
    cast = b.race_horses.copy()
    cast["weight"] = cast["weight"].astype("float64")
    b = Frames(races=b.races, race_horses=cast, race_results=b.race_results, horses=b.horses)
    assert source_fingerprint(a) == source_fingerprint(b)


def test_fingerprint_window_independent():
    # the 025/026 drift shape: a column all-int inside the window but NaN-bearing beyond it loads
    # as int64 (windowed) vs float64 (full pool). fp-v2 must hash both the same over the same
    # restriction window. This is exactly what fp-v1 could NOT guarantee (the double-load reason).
    late = [{**_LATE[0], "horses": [dict(h, weight=None) for h in _LATE[0]["horses"]]}]
    full = make_frames(_EARLY + late)          # weight: float64 (NaN in the late race)
    windowed = make_frames(_EARLY)             # weight: int64 (all populated)
    assert full.race_horses["weight"].dtype == "float64"
    assert windowed.race_horses["weight"].dtype == "int64"
    assert (
        source_fingerprint(full, through=_THROUGH_EARLY)
        == source_fingerprint(windowed, through=_THROUGH_EARLY)
    )


def test_fingerprint_object_decimal_equals_float64():
    # read_sql can hold NUMERIC columns as object-dtype Decimals; equal values must hash equal
    import decimal

    a = make_frames(_EARLY)
    b = make_frames(_EARLY)
    as_obj = b.race_horses.copy()
    as_obj["jockey_weight"] = as_obj["jockey_weight"].map(
        lambda v: decimal.Decimal(str(v))
    ).astype(object)
    b = Frames(races=b.races, race_horses=as_obj, race_results=b.race_results, horses=b.horses)
    assert source_fingerprint(a) == source_fingerprint(b)


def test_fingerprint_survives_empty_concat_object_degradation():
    # the REAL-DB failure shape: a delta window with zero race_results rows loads as an all-object
    # empty frame; concatenating it degrades float64 columns to object. The value-canonical hash
    # must be identical to the never-concatenated frames.
    early = make_frames(_EARLY)
    empty_rr = pd.DataFrame(
        {c: pd.Series([], dtype=object) for c in early.race_results.columns}
    )
    degraded = Frames(
        races=early.races,
        race_horses=early.race_horses,
        race_results=pd.concat([early.race_results, empty_rr], ignore_index=True),
        horses=early.horses,
    )
    assert degraded.race_results["last_3f"].dtype == object  # degradation actually happened
    assert source_fingerprint(degraded) == source_fingerprint(early)


def test_value_change_flips_fingerprint():
    a = make_frames(_EARLY)
    b = make_frames(_EARLY)
    b.race_results.loc[b.race_results["race_id"] == "200801010101", "last_3f"] = 99.0
    assert source_fingerprint(a) != source_fingerprint(b)


def test_delta_concat_equals_full_load():
    # builder's verification frames: windowed(<=d) + delta((d, through]) concat, horses kept once.
    # Must fingerprint identically to a single load covering the whole range (Feature 055 D2).
    full = make_frames(_EARLY + _LATE)
    windowed = make_frames(_EARLY)
    delta = make_frames(_LATE)
    concat = Frames(
        races=pd.concat([windowed.races, delta.races], ignore_index=True),
        race_horses=pd.concat([windowed.race_horses, delta.race_horses], ignore_index=True),
        race_results=pd.concat([windowed.race_results, delta.race_results], ignore_index=True),
        horses=full.horses,  # loader loads horses date-unfiltered — builder keeps one copy
    )
    assert (
        source_fingerprint(concat, through=_THROUGH_LATE)
        == source_fingerprint(full, through=_THROUGH_LATE)
    )


def test_manifest_records_algo(tmp_path):
    m = write_materialized(tmp_path / "f.parquet", make_frames(_EARLY))
    assert m.fingerprint_algo == FINGERPRINT_ALGO


def test_old_manifest_algo_rejected(tmp_path):
    # pre-055 manifest (no fingerprint_algo) must fail-closed with re-materialize guidance,
    # never be silently accepted (its fp-v1 fingerprint would false-mismatch or false-pass).
    frames = make_frames(_EARLY)
    path = tmp_path / "f.parquet"
    write_materialized(path, frames)
    mpath = tmp_path / "f.manifest.json"
    raw = json.loads(mpath.read_text(encoding="utf-8"))
    del raw["fingerprint_algo"]
    mpath.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(MaterializationError, match="materialize"):
        assemble_feature_matrix(frames, use_materialized=True, materialized_path=path)


def test_skip_verify_still_checks_compatibility(tmp_path):
    # verify-once backfill mode skips ONLY the fingerprint comparison; the frame-free compat
    # checks (feature_version / algo) still run every build.
    frames = make_frames(_EARLY)
    path = tmp_path / "f.parquet"
    write_materialized(path, frames)
    mpath = tmp_path / "f.manifest.json"
    raw = json.loads(mpath.read_text(encoding="utf-8"))
    raw["fingerprint_algo"] = "fp-v1"
    mpath.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(MaterializationError, match="materialize"):
        assemble_feature_matrix(
            frames, use_materialized=True, materialized_path=path, skip_fingerprint_verify=True,
        )


def test_skip_verify_bypasses_fingerprint(tmp_path):
    # documented contract: with skip_fingerprint_verify=True the caller (backfill wrapper) is
    # responsible for having verified once — an in-range change no longer raises here.
    frames = make_frames(_EARLY)
    path = tmp_path / "f.parquet"
    write_materialized(path, frames)
    mutated = make_frames(_EARLY)
    mutated.race_results.loc[mutated.race_results["race_id"] == "200801010101", "last_3f"] = 99.0
    out = assemble_feature_matrix(
        mutated, use_materialized=True, materialized_path=path, skip_fingerprint_verify=True,
    )
    assert len(out) > 0  # served from parquet without re-verifying (verify-once semantics)


def test_use_materialized_without_path_fails_closed():
    # FR-002: opting in without a path must raise, never silently degrade to in-memory
    with pytest.raises(MaterializationError, match="materialized_path"):
        assemble_feature_matrix(make_frames(_EARLY), use_materialized=True)

"""Feature 025: materialize generation + parity + staleness (T008/T011/T012/T013, DB-free).

Uses make_frames (in-memory) so no testcontainer is spun. Covers: deterministic generation +
manifest (US1); bit-parity materialize-read == in-memory (US2, the non-negotiable gate); fail-closed
on a stale parquet (in-range change) and a missing parquet.
"""

from __future__ import annotations

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from horseracing_features.builder import assemble_feature_matrix
from horseracing_features.materialize import (
    MaterializationError,
    build_asof_features,
    write_materialized,
)
from tests._frames import make_frames


def _specs():
    # two past races (history for H/X) + a target race with a debut horse (null as-of) + same-day pair
    return [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "last_3f": 34.0, "finish_order": 1},
            {"horse_id": "X", "horse_number": 2, "last_3f": 36.0, "finish_order": 2}]},
        {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "last_3f": 35.0, "finish_order": 1},
            {"horse_id": "X", "horse_number": 2, "last_3f": 37.0, "finish_order": 2}]},
        {"race_id": "200803010101", "race_date": "2008-03-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "last_3f": 35.0, "finish_order": 1},
            {"horse_id": "D", "horse_number": 2, "last_3f": 36.0, "finish_order": 2}]},  # D debut
    ]


def test_generate_deterministic_with_manifest(tmp_path):
    frames = make_frames(_specs())
    m1 = write_materialized(tmp_path / "a.parquet", frames)
    m2 = write_materialized(tmp_path / "b.parquet", frames)
    assert m1.content_hash == m2.content_hash               # deterministic (SC-003)
    assert m1.source_fingerprint == m2.source_fingerprint
    assert m1.feature_version == "features-012" and m1.n_rows > 0
    assert "rel_last3f_avg" in m1.materialized_columns       # as-of col present
    assert "sire_win_rate" in m1.materialized_columns        # Feature 026 pedigree col present
    assert "place_rate" in m1.materialized_columns           # Feature 030 as-of col present
    assert "field_front_rate_ex_self" in m1.materialized_columns  # Feature 031 field-comp col
    assert "sire_debut_win_rate" in m1.materialized_columns  # Feature 032 debut×pedigree col
    assert "dist_ext_x_closing" in m1.materialized_columns   # Feature 033 condition×ability col
    assert "carried_weight" not in m1.materialized_columns   # Feature 030 静的=materialize しない
    assert "field_size" not in m1.materialized_columns       # static excluded
    assert (tmp_path / "a.manifest.json").exists()


def test_fingerprint_handles_list_columns(tmp_path):
    # race_results.corner_orders is a list column — fingerprint must not choke on unhashable cells
    specs = [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "last_3f": 34.0, "corner_orders": ["3", "2"],
             "finish_order": 1},
            {"horse_id": "X", "horse_number": 2, "last_3f": 36.0, "corner_orders": ["5", "6"],
             "finish_order": 2}]},
    ]
    frames = make_frames(specs)
    m = write_materialized(tmp_path / "c.parquet", frames)  # must not raise
    assert m.source_fingerprint and m.n_rows == 2


def test_parity_materialized_equals_inmemory(tmp_path):
    frames = make_frames(_specs())
    path = tmp_path / "features.parquet"
    write_materialized(path, frames)
    via_parquet = assemble_feature_matrix(frames, use_materialized=True, materialized_path=path)
    in_memory = assemble_feature_matrix(frames, use_materialized=False)
    # non-negotiable: bit-identical incl dtype + column order (SC-001/002 — same input → same model)
    assert_frame_equal(via_parquet, in_memory, check_exact=True, check_dtype=True)


def test_stale_parquet_fails_closed(tmp_path):
    frames = make_frames(_specs())
    path = tmp_path / "features.parquet"
    write_materialized(path, frames)
    # change a result WITHIN the materialized range -> fingerprint mismatch -> fail-closed
    mutated = make_frames(_specs())
    mutated.race_results.loc[
        mutated.race_results["race_id"] == "200801010101", "last_3f"
    ] = 99.0
    with pytest.raises(MaterializationError):
        assemble_feature_matrix(mutated, use_materialized=True, materialized_path=path)


def test_missing_parquet_fails_closed(tmp_path):
    frames = make_frames(_specs())
    with pytest.raises(MaterializationError):
        assemble_feature_matrix(
            frames, use_materialized=True, materialized_path=tmp_path / "nope.parquet"
        )


def test_asof_leak_invariant_to_result_change(tmp_path):
    # materialized as-of value for a race must NOT depend on that race's own result (pool-end indep)
    frames = make_frames(_specs())
    base = build_asof_features(frames)
    row0 = base[(base.race_id == "200803010101") & (base.horse_id == "H")].iloc[0]
    mutated = make_frames(_specs())
    mutated.race_results.loc[
        mutated.race_results["race_id"] == "200803010101", "last_3f"
    ] = 12.0
    after = build_asof_features(mutated)
    row1 = after[(after.race_id == "200803010101") & (after.horse_id == "H")].iloc[0]
    for c in base.columns:
        assert (pd.isna(row0[c]) and pd.isna(row1[c])) or row0[c] == row1[c], c


def _ped_specs():
    # two offspring of sire S so pedigree features are non-trivial (sire_name populated)
    return [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            {"horse_id": "B", "horse_number": 1, "finish_order": 1, "sire_name": "S"},
            {"horse_id": "X", "horse_number": 2, "finish_order": 2, "sire_name": "O"}]},
        {"race_id": "200803010101", "race_date": "2008-03-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "finish_order": 1, "sire_name": "S"},
            {"horse_id": "X", "horse_number": 2, "finish_order": 2, "sire_name": "O"}]},
    ]


def test_pedigree_backfill_fails_closed(tmp_path):
    # Feature 026: races/race_horses/race_results UNCHANGED but sire_name backfilled -> the fingerprint
    # (now covering horses pedigree cols) must trip fail-closed (no silently-stale pedigree features).
    frames = make_frames(_ped_specs())
    path = tmp_path / "features.parquet"
    write_materialized(path, frames)
    mutated = make_frames(_ped_specs())
    mutated.horses.loc[mutated.horses["horse_id"] == "B", "sire_name"] = "S2"  # pedigree backfill
    with pytest.raises(MaterializationError):
        assemble_feature_matrix(mutated, use_materialized=True, materialized_path=path)


def test_pedigree_columns_present_and_parity(tmp_path):
    # pedigree columns are materialized AND the read path matches in-memory bit-for-bit (SC-003).
    frames = make_frames(_ped_specs())
    path = tmp_path / "features.parquet"
    write_materialized(path, frames)
    via = assemble_feature_matrix(frames, use_materialized=True, materialized_path=path)
    mem = assemble_feature_matrix(frames, use_materialized=False)
    assert "sire_win_rate" in via.columns and "damsire_win_rate" in via.columns
    assert_frame_equal(via, mem, check_exact=True, check_dtype=True)

"""Feature 025: serving fallback + materialize column selection + no-schema (T015/T017/T018)."""

from __future__ import annotations

import datetime
from pathlib import Path

from pandas.testing import assert_frame_equal

from horseracing_features.builder import assemble_feature_matrix
from horseracing_features.materialize import write_materialized
from horseracing_features.registry import STATIC_COLUMNS, materialized_columns
from tests._frames import make_frames

_ROOT = Path(__file__).resolve().parents[3]


def _hist_specs():
    return [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "last_3f": 34.0, "finish_order": 1},
            {"horse_id": "X", "horse_number": 2, "last_3f": 36.0, "finish_order": 2}]},
        {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "last_3f": 35.0, "finish_order": 1},
            {"horse_id": "X", "horse_number": 2, "last_3f": 37.0, "finish_order": 2}]},
    ]


def _future_race():
    return {"race_id": "200809010101", "race_date": "2008-09-01", "horses": [
        {"horse_id": "H", "horse_number": 1, "last_3f": 35.0, "finish_order": 1},
        {"horse_id": "X", "horse_number": 2, "last_3f": 36.0, "finish_order": 2}]}


def test_future_race_uses_fallback_equal_to_inmemory(tmp_path):
    # materialize history only, then serve a race BEYOND the materialized range
    path = tmp_path / "features.parquet"
    write_materialized(path, make_frames(_hist_specs()))

    frames_all = make_frames([*_hist_specs(), _future_race()])
    end = datetime.date(2008, 9, 1)
    # fingerprint over the materialized range is unchanged -> NOT fail-closed; future race -> fallback
    via = assemble_feature_matrix(
        frames_all, use_materialized=True, materialized_path=path, end_date=end
    )
    mem = assemble_feature_matrix(frames_all, use_materialized=False, end_date=end)
    assert_frame_equal(via, mem, check_exact=True, check_dtype=True)  # generator==fallback (SC-005/009)
    assert (via["race_id"] == "200809010101").any()


def test_materialized_columns_exclude_static_and_leaky_tokens():
    cols = materialized_columns()
    assert cols, "as-of columns must be materialized"
    # static/current-race columns are never materialized (FR-002/017, SC-009)
    for s in STATIC_COLUMNS:
        assert s not in cols, s
    # no odds/result-direct tokens leak into the materialized (model-feature) set
    for c in cols:
        low = c.lower()
        assert "odds" not in low and "payout" not in low and "dividend" not in low, c


def test_no_schema_change():
    versions = sorted(p.name for p in (_ROOT / "db" / "migrations" / "versions").glob("0*.py"))
    assert versions[-1].startswith("0009_"), versions[-1]
    for f in (_ROOT / "features" / "src" / "horseracing_features").rglob("*.py"):
        assert "__tablename__" not in f.read_text(encoding="utf-8"), f

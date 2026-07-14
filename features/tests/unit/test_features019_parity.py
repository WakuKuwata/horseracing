"""T029: the 070 F03/F04/F05 bundle is purely additive on features-018 — the structural guarantee
that the 137 shared (features-018) columns are byte-identical, so the compat pins keep
lgbm-064-f02acc (features-018) and lgbm-063 (features-017) servable.

Structural proof (058/061/069 precedent): each 070 block returns a frame keyed uniquely on
(race_id, horse_id) whose value columns are all ``asof_pm_*`` and DISJOINT from every prior feature
column. An additive left-merge on a unique right key with disjoint columns cannot perturb any
existing column — so the shared-column byte-parity holds by construction. (The full one-time
empirical 137-column check against a real features-018 build is done at materialize time, T031.)
"""

from __future__ import annotations

from horseracing_features.pm_conditioned import PM_CONDITIONED_COLUMNS
from horseracing_features.pm_expectation_residual import PM_EXPECTATION_RESIDUAL_COLUMNS
from horseracing_features.pm_rank_robust import PM_RANK_ROBUST_COLUMNS
from horseracing_features.registry import (
    FEATURE_VERSION,
    materialized_columns,
    model_input_features,
)

_NEW_070 = (
    set(PM_RANK_ROBUST_COLUMNS)
    | set(PM_EXPECTATION_RESIDUAL_COLUMNS)
    | set(PM_CONDITIONED_COLUMNS)
)


def test_feature_version_is_019():
    assert FEATURE_VERSION == "features-019"


def test_070_adds_exactly_19_columns():
    assert len(_NEW_070) == 19  # F03=5 + F04=6 + F05=8 (support 6 + residual 2)


def test_137_shared_columns_are_disjoint_from_070():
    mi = model_input_features()
    assert len(mi) == 156  # 137 shared (features-018) + 19 new
    shared = set(mi) - _NEW_070
    assert len(shared) == 137
    # the 137 prior columns are byte-identical because the new columns cannot collide with them
    assert shared.isdisjoint(_NEW_070)


def test_070_columns_are_materialized():
    assert _NEW_070.issubset(set(materialized_columns()))
    assert len(materialized_columns()) == 131  # 112 (features-018) + 19 new


def test_shared_columns_byte_identical_after_070_merges():
    """Empirical additive parity (codex 実装#4): merging each 070 block onto the assembled shared
    columns leaves EVERY shared (features-018) column byte-identical (check_exact + check_dtype).
    This is the load-bearing guarantee behind the 018/017 compat pins."""
    import pandas as pd

    from horseracing_features.materialize import build_asof_features
    from horseracing_features.pm_conditioned import build_pm_conditioned_features
    from horseracing_features.pm_expectation_residual import (
        build_pm_expectation_residual_features,
    )
    from horseracing_features.pm_rank_robust import build_pm_rank_robust_features
    from tests._frames import make_frames

    def race(rid, date, track, odds):
        horses = [{"horse_id": h, "popularity": i + 1, "odds": o, "finish_order": i + 1,
                   "track_type": track}
                  for i, (h, o) in enumerate(odds.items())]
        return {"race_id": rid, "race_date": date, "track_type": track, "horses": horses}
    specs = [
        race("P1", "2020-01-01", "芝", {"h": 2.0, "o": 3.0}),
        race("P2", "2020-02-01", "ダ", {"h": 1.5, "o": 6.0}),
        race("T1", "2020-03-01", "芝", {"h": 2.5, "o": 2.0}),
    ]
    frames = make_frames(specs)
    keys = ["race_id", "horse_id"]
    full = build_asof_features(frames)
    shared_cols = [c for c in full.columns if c not in _NEW_070 and c not in keys]
    shared = full[[*keys, *shared_cols]].copy()
    for block in (build_pm_rank_robust_features, build_pm_expectation_residual_features,
                  build_pm_conditioned_features):
        blk = block(frames)
        assert not blk.duplicated(subset=keys).any()        # unique key -> no row fan-out
        assert set(blk.columns).isdisjoint(shared_cols)      # disjoint -> no column collision
        merged = shared.merge(blk, on=keys, how="left")
        pd.testing.assert_frame_equal(
            merged[shared_cols], shared[shared_cols], check_exact=True, check_dtype=True
        )

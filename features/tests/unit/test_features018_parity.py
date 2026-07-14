"""T014: F02 is purely additive on features-017 (FR-009, codex D5) — the structural guarantee
that the 128 shared columns are byte-identical, so the compat pin keeps lgbm-063 servable.

Structural proof (058/061 precedent): build_pm_core_strength_features returns a frame keyed
uniquely on (race_id, horse_id) whose value columns are DISJOINT from every other feature column
(all ``asof_pm_*``). An additive left-merge on a unique right key with disjoint columns cannot
perturb any existing column — so the shared-column byte-parity holds by construction. (The full
one-time empirical 128-column check against a real features-017 build is done at materialize time
before the bump, same as 058/061.)"""

from __future__ import annotations

from horseracing_features.pm_core_strength import (
    PM_CORE_STRENGTH_COLUMNS,
    build_pm_core_strength_features,
)
from horseracing_features.registry import FEATURE_GROUPS, materialized_columns
from tests._frames import make_frames


def _race(rid, date, odds):
    horses = [{"horse_id": h, "odds": o, "finish_order": i + 1}
              for i, (h, o) in enumerate(odds.items())]
    return {"race_id": rid, "race_date": date, "horses": horses}


def test_f02_frame_has_unique_keys_and_only_pm_columns():
    specs = [
        _race("P1", "2020-01-01", {"h": 2.0, "o": 2.0}),
        _race("T1", "2020-02-01", {"h": 1.5, "o": 6.0}),
    ]
    out = build_pm_core_strength_features(make_frames(specs))
    # unique (race_id, horse_id) key -> additive left-merge cannot fan out rows
    assert not out.duplicated(subset=["race_id", "horse_id"]).any()
    # value columns are EXACTLY the 9 asof_pm_* columns (disjoint from all other features)
    value_cols = [c for c in out.columns if c not in ("race_id", "horse_id")]
    assert set(value_cols) == set(PM_CORE_STRENGTH_COLUMNS)


def test_f02_columns_are_disjoint_from_all_other_feature_columns():
    f02 = set(PM_CORE_STRENGTH_COLUMNS)
    other = {c for c, g in FEATURE_GROUPS.items() if g != "pm_core_strength"}
    assert not (f02 & other), "F02 column names must be disjoint (additive-merge guarantee)"
    # and F02 columns ARE in the materialized set (wired)
    assert f02.issubset(set(materialized_columns()))


def test_f02_group_is_exactly_nine_columns():
    grp = {c for c, g in FEATURE_GROUPS.items() if g == "pm_core_strength"}
    assert grp == set(PM_CORE_STRENGTH_COLUMNS)
    assert len(grp) == 9

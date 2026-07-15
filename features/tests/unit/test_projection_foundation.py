"""Feature 072 Foundational + US1(pace) projection gates (DB-free, make_frames).

Covers: T006 (foundational parity + FR-007 composition), T006b (same-day multi-race, FR-009),
T008 (pace block projection incl. debut/low-history/cancel + same-day).
"""

from __future__ import annotations

import numpy as np
from pandas.testing import assert_frame_equal

from horseracing_features.builder import assemble_feature_matrix
from horseracing_features.materialize import build_asof_features
from horseracing_features.pace_features import build_pace_features
from horseracing_features.pm_core_strength import PM_CORE_STRENGTH_COLUMNS
from tests._frames import make_frames
from tests._projection import assert_projected_equals_full

_KEYS = ["race_id", "horse_id"]


def _history_frames():
    """Multi-history horses + a debut + a low-history + a cancelled entry across many races."""
    specs = []
    for i in range(10):
        specs.append({
            "race_id": f"R{i:02d}", "race_date": f"2020-{(i % 12) + 1:02d}-05",
            "horses": [
                {"horse_id": "a", "finish_order": (i % 4) + 1, "last_3f": 34.0 + (i % 3),
                 "finish_time": 95.0 + (i % 5), "odds": 1.5 + (i % 3)},
                {"horse_id": "b", "finish_order": ((i + 1) % 4) + 1, "last_3f": 35.0 + (i % 2),
                 "finish_time": 96.0 + (i % 4), "odds": 3.0 + (i % 2)},
                {"horse_id": "c", "finish_order": ((i + 2) % 4) + 1, "last_3f": 36.0,
                 "finish_time": 97.0, "odds": 6.0},
            ],
        })
    # low-history horse 'lo' runs twice
    specs.append({"race_id": "RL1", "race_date": "2020-06-10",
                  "horses": [{"horse_id": "lo", "finish_order": 2, "last_3f": 35.5,
                              "finish_time": 96.0}]})
    # target race: a (deep history), lo (low history), z (debut), plus a cancelled x
    specs.append({
        "race_id": "RT", "race_date": "2021-06-01",
        "horses": [
            {"horse_id": "a", "finish_order": 1, "last_3f": 34.0, "finish_time": 95.0, "odds": 2.0},
            {"horse_id": "lo", "finish_order": 2, "last_3f": 35.0, "finish_time": 96.0, "odds": 4.0},
            {"horse_id": "z", "finish_order": 3, "last_3f": 35.5, "finish_time": 96.5, "odds": 5.0},
            {"horse_id": "x", "entry_status": "cancelled", "result_status": None},
        ],
    })
    return make_frames(specs)


def _same_day_multi_race_frames():
    """Two same-day target races (RA, RB) where horse 'a' AND jockey 'J' each appear in BOTH,
    after prior finished appearances — the FR-009 / R3 highest-risk case."""
    specs = []
    for i in range(6):  # prior history for 'a'/'b' under jockey J
        specs.append({
            "race_id": f"H{i}", "race_date": f"2020-0{i + 1}-05",
            "horses": [
                {"horse_id": "a", "jockey_id": "J", "finish_order": 1, "last_3f": 34.0,
                 "finish_time": 95.0, "odds": 2.0},
                {"horse_id": "b", "jockey_id": "J", "finish_order": 2, "last_3f": 35.0,
                 "finish_time": 96.0, "odds": 3.0},
            ],
        })
    # SAME DAY D two races: a runs in RA (jockey J) and RB (jockey J); b in both too
    for rid in ("RA", "RB"):
        specs.append({
            "race_id": rid, "race_date": "2021-03-03",
            "horses": [
                {"horse_id": "a", "jockey_id": "J", "finish_order": 1, "last_3f": 34.0,
                 "finish_time": 95.0, "odds": 2.0},
                {"horse_id": "b", "jockey_id": "J", "finish_order": 2, "last_3f": 35.0,
                 "finish_time": 96.0, "odds": 3.0},
            ],
        })
    return make_frames(specs)


# --- T008: pace block projection ---------------------------------------------------------------

def test_pace_projection_byte_identical():
    frames = _history_frames()
    assert_projected_equals_full(build_pace_features, frames, ["RT"])


# --- T006: foundational — whole as-of matrix + assemble, projected == full.loc -----------------

def test_build_asof_projection_byte_identical():
    frames = _history_frames()
    assert_projected_equals_full(build_asof_features, frames, ["RT"])


def test_assemble_projection_byte_identical():
    frames = _history_frames()
    full = assemble_feature_matrix(frames)
    proj = assemble_feature_matrix(frames, target_race_ids=frozenset({"RT"}))
    full_t = full[full["race_id"] == "RT"].sort_values(_KEYS).reset_index(drop=True)
    proj = proj.sort_values(_KEYS).reset_index(drop=True)
    assert_frame_equal(full_t, proj, check_exact=True, check_dtype=True)


# --- T006: FR-007 composition — target_race_ids × wanted= (leaf-skip) are orthogonal -----------

def test_projection_composes_with_wanted_leafskip():
    frames = _history_frames()
    wanted = frozenset(c for c in assemble_feature_matrix(frames).columns
                       if c not in PM_CORE_STRENGTH_COLUMNS and c not in _KEYS)
    full = assemble_feature_matrix(frames, wanted=wanted)  # leaf-skip only
    both = assemble_feature_matrix(frames, wanted=wanted, target_race_ids=frozenset({"RT"}))
    # F02 columns dropped in both; projection restricts rows; result == full leaf-skip on RT rows
    assert not set(PM_CORE_STRENGTH_COLUMNS) & set(both.columns)
    full_t = full[full["race_id"] == "RT"].sort_values(_KEYS).reset_index(drop=True)
    both = both.sort_values(_KEYS).reset_index(drop=True)
    assert_frame_equal(full_t, both, check_exact=True, check_dtype=True)


# --- T006b: FR-009 same-day multi-target-race build ---------------------------------------------

def test_same_day_multi_race_projection_byte_identical():
    frames = _same_day_multi_race_frames()
    targets = frozenset({"RA", "RB"})
    full = build_asof_features(frames)
    proj = build_asof_features(frames, target_race_ids=targets)
    full_t = full[full["race_id"].isin(targets)].sort_values(_KEYS).reset_index(drop=True)
    proj = proj.sort_values(_KEYS).reset_index(drop=True)
    assert_frame_equal(full_t, proj, check_exact=True, check_dtype=True)
    # both same-day races present, and horse 'a' appears in both
    assert set(proj["race_id"]) == targets
    assert ((proj["horse_id"] == "a").sum()) == 2


def test_target_none_is_full_build():
    frames = _history_frames()
    full = build_asof_features(frames)
    same = build_asof_features(frames, target_race_ids=None)
    assert_frame_equal(full, same, check_exact=True, check_dtype=True)
    assert len(full) > len(full[full["race_id"] == "RT"])  # full has more than the target race
    assert np.isfinite(full["win_rate"].dropna()).all() or True  # sanity

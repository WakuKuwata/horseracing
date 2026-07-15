"""Feature 072 US2: per-block projection parity gates (DB-free). Each converted block's projected
output must equal the full build restricted to the target rows, byte-for-byte (INV-P1)."""

from __future__ import annotations

import pytest

from horseracing_features.corner_trajectory_features import build_corner_trajectory_features
from horseracing_features.extra_features import build_extra_features
from horseracing_features.history import build_history_features
from horseracing_features.past_market_features import build_past_market_features
from horseracing_features.speed_figure_features import build_speed_figure_features
from tests._frames import make_frames
from tests._projection import assert_projected_equals_full

# reuse the rich history fixture from the foundation test
from .test_projection_foundation import _history_frames, _same_day_multi_race_frames

_PER_HORSE_BLOCKS = [
    pytest.param(build_extra_features, id="extra"),
    pytest.param(build_history_features, id="history"),
    pytest.param(build_past_market_features, id="past_market"),
    pytest.param(build_speed_figure_features, id="speed_figure"),
    pytest.param(build_corner_trajectory_features, id="corner"),
]


@pytest.mark.parametrize("build_fn", _PER_HORSE_BLOCKS)
def test_per_horse_block_projection_byte_identical(build_fn):
    frames = _history_frames()  # includes debut, low-history, cancelled entry
    assert_projected_equals_full(build_fn, frames, ["RT"])


@pytest.mark.parametrize("build_fn", _PER_HORSE_BLOCKS)
def test_per_horse_block_same_day_multi_race(build_fn):
    frames = _same_day_multi_race_frames()  # horse 'a' in two same-day races
    assert_projected_equals_full(build_fn, frames, ["RA", "RB"])


def test_per_horse_block_target_none_unchanged():
    from pandas.testing import assert_frame_equal
    frames = _history_frames()
    for fn in (build_extra_features, build_history_features, build_past_market_features,
               build_speed_figure_features, build_corner_trajectory_features):
        assert_frame_equal(fn(frames), fn(frames, target_race_ids=None),
                           check_exact=True, check_dtype=True)


def test_empty_target_yields_no_rows():
    frames = make_frames([{"race_id": "R1", "race_date": "2020-01-01",
                           "horses": [{"horse_id": "a"}]}])
    assert len(build_extra_features(frames, target_race_ids=frozenset())) == 0


# --- cross-entity: pedigree (sire/damsire key, other-offspring self-exclusion) ------------------

def _pedigree_frames():
    """Sire S1 has offspring a,b,c (self-exclusion matters); damsire D1 shared. Target race RT has
    a and b (same sire) → each must exclude ITSELF from the sire aggregate."""
    specs = []
    for i in range(8):
        specs.append({
            "race_id": f"P{i}", "race_date": f"2020-0{i + 1}-05",
            "horses": [
                {"horse_id": "a", "sire_name": "S1", "damsire_name": "D1",
                 "finish_order": (i % 3) + 1},
                {"horse_id": "b", "sire_name": "S1", "damsire_name": "D2",
                 "finish_order": ((i + 1) % 3) + 1},
                {"horse_id": "c", "sire_name": "S1", "damsire_name": "D1",
                 "finish_order": ((i + 2) % 3) + 1},
            ],
        })
    specs.append({
        "race_id": "RT", "race_date": "2021-06-01",
        "horses": [
            {"horse_id": "a", "sire_name": "S1", "damsire_name": "D1", "finish_order": 1},
            {"horse_id": "b", "sire_name": "S1", "damsire_name": "D2", "finish_order": 2},
            {"horse_id": "z", "sire_name": "S9", "damsire_name": "D9", "finish_order": 3},  # rare sire
        ],
    })
    return make_frames(specs)


def _pedigree_same_day_frames():
    """Sire S1 offspring in TWO same-day target races RA/RB (horse a in both) — cross-entity R3."""
    specs = []
    for i in range(6):
        specs.append({"race_id": f"Q{i}", "race_date": f"2020-0{i + 1}-05",
                      "horses": [{"horse_id": "a", "sire_name": "S1", "finish_order": 1},
                                 {"horse_id": "b", "sire_name": "S1", "finish_order": 2}]})
    for rid in ("RA", "RB"):
        specs.append({"race_id": rid, "race_date": "2021-03-03",
                      "horses": [{"horse_id": "a", "sire_name": "S1", "finish_order": 1},
                                 {"horse_id": "b", "sire_name": "S1", "finish_order": 2}]})
    return make_frames(specs)


def test_pedigree_projection_byte_identical():
    from horseracing_features.pedigree_features import build_pedigree_features
    assert_projected_equals_full(build_pedigree_features, _pedigree_frames(), ["RT"])


def test_pedigree_same_day_multi_race():
    from horseracing_features.pedigree_features import build_pedigree_features
    assert_projected_equals_full(build_pedigree_features, _pedigree_same_day_frames(), ["RA", "RB"])


def _owner_frames():
    specs = []
    for i in range(8):
        specs.append({"race_id": f"O{i}", "race_date": f"2020-0{i + 1}-05",
                      "horses": [{"horse_id": "a", "owner_name": "OW1", "breeder_name": "BR1",
                                  "finish_order": (i % 3) + 1},
                                 {"horse_id": "b", "owner_name": "OW1", "breeder_name": "BR2",
                                  "finish_order": ((i + 1) % 3) + 1}]})
    specs.append({"race_id": "RT", "race_date": "2021-06-01",
                  "horses": [{"horse_id": "a", "owner_name": "OW1", "breeder_name": "BR1",
                              "finish_order": 1},
                             {"horse_id": "z", "owner_name": "OW9", "breeder_name": "BR9",
                              "finish_order": 2}]})
    return make_frames(specs)


def test_owner_breeder_projection_byte_identical():
    from horseracing_features.owner_breeder_features import build_owner_breeder_features
    assert_projected_equals_full(build_owner_breeder_features, _owner_frames(), ["RT"])


def test_debut_pedigree_projection_byte_identical():
    from horseracing_features.debut_pedigree_features import build_debut_pedigree_features
    assert_projected_equals_full(build_debut_pedigree_features, _pedigree_frames(), ["RT"])


def _lowcost_frames():
    """Mixed per-horse + cross-entity: jockey J1 and trainer T1 shared across horses; target race RT
    has a (J1/T1) and b (J1/T2) so the jockey/combo aggregations exercise cross-entity, and per-horse
    place/venue/handicap exercise the horse key."""
    specs = []
    for i in range(8):
        specs.append({"race_id": f"L{i}", "race_date": f"2020-0{i + 1}-05", "venue_code": "05",
                      "horses": [
                          {"horse_id": "a", "jockey_id": "J1", "trainer_id": "T1",
                           "jockey_weight": 55.0 + i % 3, "finish_order": (i % 3) + 1},
                          {"horse_id": "b", "jockey_id": "J1", "trainer_id": "T2",
                           "jockey_weight": 54.0, "finish_order": ((i + 1) % 3) + 1},
                          {"horse_id": "c", "jockey_id": "J2", "trainer_id": "T1",
                           "jockey_weight": 56.0, "finish_order": ((i + 2) % 3) + 1}]})
    specs.append({"race_id": "RT", "race_date": "2021-06-01", "venue_code": "05",
                  "horses": [
                      {"horse_id": "a", "jockey_id": "J1", "trainer_id": "T1",
                       "jockey_weight": 56.0, "finish_order": 1},
                      {"horse_id": "b", "jockey_id": "J1", "trainer_id": "T2",
                       "jockey_weight": 55.0, "finish_order": 2},
                      {"horse_id": "z", "jockey_id": "J9", "trainer_id": "T9",
                       "jockey_weight": 54.0, "finish_order": 3}]})
    return make_frames(specs)


def test_lowcost_projection_byte_identical():
    from horseracing_features.lowcost_features import build_lowcost_features
    assert_projected_equals_full(build_lowcost_features, _lowcost_frames(), ["RT"])

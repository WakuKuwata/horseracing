"""Feature 072 US2: per-block projection parity gates (DB-free). Each converted block's projected
output must equal the full build restricted to the target rows, byte-for-byte (INV-P1)."""

from __future__ import annotations

import pytest

from horseracing_features.condition_change_features import build_condition_change_features
from horseracing_features.corner_trajectory_features import build_corner_trajectory_features
from horseracing_features.extra_features import build_extra_features
from horseracing_features.history import build_history_features
from horseracing_features.human_form import build_human_form_features
from horseracing_features.past_market_features import build_past_market_features
from horseracing_features.pm_core_strength import build_pm_core_strength_features
from horseracing_features.race_level_features import build_race_level_features
from horseracing_features.speed_figure_features import build_speed_figure_features
from tests._frames import make_frames
from tests._projection import assert_projected_equals_full

# reuse the rich history fixture from the foundation test
from .test_projection_foundation import _history_frames, _same_day_multi_race_frames

_PER_HORSE_BLOCKS = [
    pytest.param(build_extra_features, id="extra"),
    pytest.param(build_history_features, id="history"),
    pytest.param(build_past_market_features, id="past_market"),
    pytest.param(build_pm_core_strength_features, id="pm_core_strength"),
    pytest.param(build_speed_figure_features, id="speed_figure"),
    pytest.param(build_corner_trajectory_features, id="corner"),
    pytest.param(build_race_level_features, id="race_level"),
    pytest.param(build_condition_change_features, id="condition_change"),
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
               build_speed_figure_features, build_corner_trajectory_features,
               build_race_level_features, build_condition_change_features,
               build_human_form_features):
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


# --- remaining blocks (interactive-latency round 2): human_form / race_level / condition_change --

def test_human_form_projection_byte_identical():
    from horseracing_features.human_form import build_human_form_features
    assert_projected_equals_full(build_human_form_features, _lowcost_frames(), ["RT"])


def _prize_condchg_frames():
    """Varying prize/distance/surface/going so race_level + condition_change are non-trivial:
    horse 'a' moves 1600芝良 → 1800ダ稍 → 2000芝重 …; RT is a stretch-out on dirt."""
    specs = []
    surfaces = ["芝", "ダ", "芝", "ダ", "芝", "ダ"]
    goings = ["良", "稍", "重", "良", "不", "稍"]
    for i in range(6):
        specs.append({
            "race_id": f"C{i}", "race_date": f"2020-0{i + 1}-05",
            "distance": 1400 + 200 * (i % 3), "track_type": surfaces[i], "going": goings[i],
            "prize_money": 500 + 100 * i,
            "horses": [
                {"horse_id": "a", "finish_order": (i % 3) + 1, "last_3f": 34.0 + i * 0.1},
                {"horse_id": "b", "finish_order": ((i + 1) % 3) + 1, "last_3f": 35.0},
            ],
        })
    specs.append({
        "race_id": "RT", "race_date": "2021-06-01",
        "distance": 2200, "track_type": "ダ", "going": "稍", "prize_money": 1000,
        "horses": [
            {"horse_id": "a", "finish_order": 1, "last_3f": 34.5},
            {"horse_id": "new", "finish_order": 2, "last_3f": 35.5},  # debut: prev NaN
        ],
    })
    # same-day pair (horse 'a' twice on one day: 同日除外 must hold under projection)
    specs.append({"race_id": "SA", "race_date": "2021-06-01", "distance": 1200,
                  "track_type": "芝", "going": "良", "prize_money": 800,
                  "horses": [{"horse_id": "a", "finish_order": 1}]})
    return make_frames(specs)


def test_race_level_projection_byte_identical():
    from horseracing_features.race_level_features import build_race_level_features
    assert_projected_equals_full(build_race_level_features, _prize_condchg_frames(), ["RT"])
    assert_projected_equals_full(build_race_level_features, _prize_condchg_frames(), ["RT", "SA"])


def test_condition_change_projection_byte_identical():
    from horseracing_features.condition_change_features import build_condition_change_features
    assert_projected_equals_full(build_condition_change_features, _prize_condchg_frames(), ["RT"])
    assert_projected_equals_full(
        build_condition_change_features, _prize_condchg_frames(), ["RT", "SA"]
    )


def test_condition_change_projection_with_projected_pace_input():
    """materialize passes the PROJECTED pace block — parity must hold with that input too."""
    from pandas.testing import assert_frame_equal

    from horseracing_features.condition_change_features import build_condition_change_features
    from horseracing_features.pace_features import build_pace_features

    frames = _prize_condchg_frames()
    full = build_condition_change_features(frames, pace=build_pace_features(frames))
    proj = build_condition_change_features(
        frames, pace=build_pace_features(frames, target_race_ids=frozenset(["RT"])),
        target_race_ids=frozenset(["RT"]),
    )
    keys = ["race_id", "horse_id"]
    full_t = (full[full["race_id"] == "RT"].sort_values(keys, kind="stable")
              .reset_index(drop=True))
    proj = proj.sort_values(keys, kind="stable").reset_index(drop=True)
    assert_frame_equal(full_t, proj[full_t.columns], check_exact=True, check_dtype=True)


# --- codex round-2 edges: NaN entity keys / missing prize / cancelled / empty-target schema -----

def test_human_form_nan_entity_keys():
    """jockey-only NaN / trainer-only NaN / both NaN target rows must stay byte-identical
    (groupby dropna drops NaN keys on the right side in BOTH builds — codex)."""
    specs = []
    for i in range(4):
        specs.append({"race_id": f"H{i}", "race_date": f"2020-0{i + 1}-05",
                      "horses": [
                          {"horse_id": "a", "jockey_id": "J1", "trainer_id": "T1",
                           "finish_order": (i % 2) + 1},
                          {"horse_id": "b", "jockey_id": "J2", "trainer_id": "T2",
                           "finish_order": ((i + 1) % 2) + 1}]})
    specs.append({"race_id": "RT", "race_date": "2021-06-01",
                  "horses": [
                      {"horse_id": "a", "jockey_id": None, "trainer_id": "T1",
                       "finish_order": 1},
                      {"horse_id": "b", "jockey_id": "J2", "trainer_id": None,
                       "finish_order": 2},
                      {"horse_id": "z", "jockey_id": None, "trainer_id": None,
                       "finish_order": 3}]})
    # same entity (J1) also rides in ANOTHER same-day race -> 同日除外 must hold when only RT
    # is the target (the same-day mount must still be excluded from J1's before-rate)
    specs.append({"race_id": "SD", "race_date": "2021-06-01",
                  "horses": [{"horse_id": "c", "jockey_id": "J1", "trainer_id": "T2",
                              "finish_order": 1}]})
    from horseracing_features.human_form import build_human_form_features
    assert_projected_equals_full(build_human_form_features, make_frames(specs), ["RT"])


def test_race_level_missing_prize_and_cancelled():
    """Past races with NO prize + a cancelled entry in the target race: both must be
    byte-identical under projection (missing prize rows drop from the source in both builds)."""
    specs = [
        {"race_id": "P0", "race_date": "2020-01-05", "prize_money": 500,
         "horses": [{"horse_id": "a", "finish_order": 1}]},
        {"race_id": "P1", "race_date": "2020-02-05", "prize_money": None,  # prize missing
         "horses": [{"horse_id": "a", "finish_order": 2}]},
        {"race_id": "P2", "race_date": "2020-03-05", "prize_money": 800,
         "horses": [{"horse_id": "a", "finish_order": 1},
                    {"horse_id": "b", "finish_order": 2}]},
        {"race_id": "RT", "race_date": "2021-06-01", "prize_money": 1000,
         "horses": [{"horse_id": "a", "finish_order": 1},
                    {"horse_id": "b", "entry_status": "cancelled", "result_status": None}]},
    ]
    from horseracing_features.race_level_features import build_race_level_features
    proj = assert_projected_equals_full(build_race_level_features, make_frames(specs), ["RT"])
    assert set(proj["horse_id"]) == {"a", "b"}  # cancelled entry keeps its target row


def test_new_blocks_empty_target_schema():
    """Empty target set -> zero rows but full column schema + dtypes preserved."""
    frames = _lowcost_frames()
    for fn in (build_human_form_features, build_race_level_features,
               build_condition_change_features):
        full = fn(frames)
        empty = fn(frames, target_race_ids=frozenset())
        assert len(empty) == 0
        assert list(empty.columns) == list(full.columns)


# --- Feature 090 (nick cross): REJECTED at the pre-registered gate, kept UNWIRED as the
# documented negative result. These call build_nick_cross_features directly, so they stay green
# even though the block is no longer merged into build_asof_features.


def _nick_frames():
    """Cross-entity history: two sires x two damsires, plus an unlined damsire (the population
    whose partial line coverage makes the L1 parent restriction matter)."""
    specs = []
    for i in range(10):
        specs.append({"race_id": f"N{i}", "race_date": f"2020-{(i % 9) + 1:02d}-05",
                      "horses": [{"horse_id": f"h{i}a", "sire_name": "S1",
                                  "damsire_name": "D1", "damsire_line": "L1",
                                  "finish_order": (i % 3) + 1},
                                 {"horse_id": f"h{i}b", "sire_name": "S1",
                                  "damsire_name": "D2", "damsire_line": None,
                                  "finish_order": ((i + 1) % 3) + 1},
                                 {"horse_id": f"h{i}c", "sire_name": "S2",
                                  "damsire_name": "D1", "damsire_line": "L1",
                                  "finish_order": ((i + 2) % 3) + 1}]})
    specs.append({"race_id": "RT", "race_date": "2021-06-01",
                  "horses": [{"horse_id": "h0a", "sire_name": "S1", "damsire_name": "D1",
                              "damsire_line": "L1", "finish_order": 1},
                             {"horse_id": "zz", "sire_name": "S9", "damsire_name": "D9",
                              "damsire_line": None, "finish_order": 2}]})
    return make_frames(specs)


def _nick_same_day_frames():
    specs = [{"race_id": f"N{i}", "race_date": f"2020-0{i + 1}-05",
              "horses": [{"horse_id": f"h{i}", "sire_name": "S1", "damsire_name": "D1",
                          "damsire_line": "L1", "finish_order": (i % 2) + 1}]}
             for i in range(6)]
    for rid in ("RA", "RB"):
        specs.append({"race_id": rid, "race_date": "2021-03-03",
                      "horses": [{"horse_id": "a", "sire_name": "S1", "damsire_name": "D1",
                                  "damsire_line": "L1", "finish_order": 1},
                                 {"horse_id": "b", "sire_name": "S1", "damsire_name": "D2",
                                  "damsire_line": "L1", "finish_order": 2}]})
    return make_frames(specs)


def test_nick_cross_projection_byte_identical():
    """Covers the global p_overall primitive: projecting must NOT restrict it, or the expected
    rate (and therefore every residual) drifts away from the full build."""
    from horseracing_features.nick_cross_features import build_nick_cross_features
    assert_projected_equals_full(build_nick_cross_features, _nick_frames(), ["RT"])


def test_nick_cross_same_day_multi_race():
    from horseracing_features.nick_cross_features import build_nick_cross_features
    assert_projected_equals_full(build_nick_cross_features, _nick_same_day_frames(), ["RA", "RB"])

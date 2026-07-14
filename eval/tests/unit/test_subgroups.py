"""T005/T006: subgroup assignment (grain-separated, result-blind) + three-way IU guard."""

from __future__ import annotations

from horseracing_eval.subgroups import (
    coverage_band,
    horse_subgroup_labels,
    is_nk,
    race_subgroup_labels,
    subgroup_guard,
    three_way,
)


def test_race_level_2026_and_field_has_nk():
    assert race_subgroup_labels(2026, field_has_nk=True) == {"2026_only", "2026_field_has_nk"}
    assert race_subgroup_labels(2026, field_has_nk=False) == {"2026_only"}
    assert race_subgroup_labels(2024, field_has_nk=True) == set()  # non-2026 -> no race subgroup


def test_horse_level_id_source_and_interaction():
    assert horse_subgroup_labels("2020100734", 2026) == {"canonical"}
    assert horse_subgroup_labels("nk:2020100734", 2026) == {"nk", "2026_nk"}
    assert horse_subgroup_labels("nk:2020100734", 2024) == {"nk"}  # nk but not 2026 -> no 2026_nk
    assert horse_subgroup_labels("2020100734", 2024) == {"canonical"}


def test_coverage_bands_need_obs_count():
    assert coverage_band(None) is None       # US1 MVP: no F02 -> no coverage band
    assert coverage_band(0) == "cov_0"
    assert coverage_band(1) == "cov_1_2"
    assert coverage_band(2) == "cov_1_2"
    assert coverage_band(3) == "cov_3plus"
    assert coverage_band(9) == "cov_3plus"


def test_coverage_band_folded_into_horse_labels_when_injected():
    labs = horse_subgroup_labels("nk:x", 2026, obs_count=0)
    assert labs == {"nk", "2026_nk", "cov_0"}


def test_is_nk_prefix():
    assert is_nk("nk:123") and not is_nk("123")


def test_assignment_is_result_blind():
    # labels depend only on id/year/obs_count — never a finish/win label
    a = horse_subgroup_labels("nk:z", 2026, obs_count=1)
    b = horse_subgroup_labels("nk:z", 2026, obs_count=1)
    assert a == b == {"nk", "2026_nk", "cov_1_2"}


def test_three_way_pass_fail_no_decision():
    m = 0.001
    assert three_way(-0.02, -0.005, m) == "PASS"    # CI upper below margin -> non-inferior
    assert three_way(0.01, 0.03, m) == "FAIL"        # CI lower above margin -> confidently worse
    assert three_way(-0.01, 0.02, m) == "NO_DECISION"  # straddles margin
    assert three_way(None, None, m) == "NO_DECISION"   # undefined CI


def test_intersection_union_guard_requires_all_critical_pass():
    crit = ["2026_only", "nk", "2026_nk"]
    assert subgroup_guard({"2026_only": "PASS", "nk": "PASS", "2026_nk": "PASS"}, crit) is True
    # one NO_DECISION critical blocks adoption (not a veto, but not sufficient)
    assert subgroup_guard({"2026_only": "PASS", "nk": "NO_DECISION", "2026_nk": "PASS"}, crit) is False
    # one FAIL blocks
    assert subgroup_guard({"2026_only": "PASS", "nk": "PASS", "2026_nk": "FAIL"}, crit) is False
    # missing critical (never reported) blocks
    assert subgroup_guard({"2026_only": "PASS"}, crit) is False

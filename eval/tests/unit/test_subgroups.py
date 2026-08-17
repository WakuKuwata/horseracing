"""T005/T006: subgroup assignment (grain-separated, result-blind) + three-way IU guard."""

from __future__ import annotations

from horseracing_eval.subgroups import (
    coverage_band,
    horse_subgroup_labels,
    is_nk,
    race_subgroup_labels,
    subgroup_guard,
    subgroup_guard_status,
    three_way,
)


def test_race_level_recent_year_and_field_has_nk():
    # v3: every label is emitted under BOTH a stable name and the year-stamped alias, so a
    # gate-config frozen against "2026_only" keeps resolving while the guard follows the window.
    assert race_subgroup_labels(2026, field_has_nk=True) == {
        "recent_year_only", "2026_only", "recent_year_field_has_nk", "2026_field_has_nk",
    }
    assert race_subgroup_labels(2026, field_has_nk=False) == {"recent_year_only", "2026_only"}
    assert race_subgroup_labels(2024, field_has_nk=True) == set()  # not the target year


def test_race_level_target_year_follows_the_window():
    """v2 hard-coded 2026, so from 2027 the "current regime" guard would test a frozen past year."""
    assert race_subgroup_labels(2027, field_has_nk=False, target_year=2027) == {
        "recent_year_only", "2027_only",
    }
    assert race_subgroup_labels(2026, field_has_nk=False, target_year=2027) == set()


def test_horse_level_id_source_and_interaction():
    assert horse_subgroup_labels("2020100734", 2026) == {"canonical"}
    assert horse_subgroup_labels("nk:2020100734", 2026) == {"nk", "recent_year_nk", "2026_nk"}
    assert horse_subgroup_labels("nk:2020100734", 2024) == {"nk"}  # nk but not target year
    assert horse_subgroup_labels("2020100734", 2024) == {"canonical"}
    assert horse_subgroup_labels("nk:x", 2027, target_year=2027) == {
        "nk", "recent_year_nk", "2027_nk",
    }


def test_coverage_bands_need_obs_count():
    assert coverage_band(None) is None       # US1 MVP: no F02 -> no coverage band
    assert coverage_band(0) == "cov_0"
    assert coverage_band(1) == "cov_1_2"
    assert coverage_band(2) == "cov_1_2"
    assert coverage_band(3) == "cov_3plus"
    assert coverage_band(9) == "cov_3plus"


def test_coverage_band_folded_into_horse_labels_when_injected():
    labs = horse_subgroup_labels("nk:x", 2026, obs_count=0)
    assert labs == {"nk", "recent_year_nk", "2026_nk", "cov_0"}


def test_is_nk_prefix():
    assert is_nk("nk:123") and not is_nk("123")


def test_assignment_is_result_blind():
    # labels depend only on id/year/obs_count — never a finish/win label
    a = horse_subgroup_labels("nk:z", 2026, obs_count=1)
    b = horse_subgroup_labels("nk:z", 2026, obs_count=1)
    assert a == b == {"nk", "recent_year_nk", "2026_nk", "cov_1_2"}


def test_three_way_conclusive_states_are_unchanged_from_v2():
    """PASS/FAIL are valid at ANY CI width — a test that concluded did conclude."""
    m = 0.001
    assert three_way(-0.02, -0.005, m) == "PASS"   # CI upper below margin, even though hw >> m
    assert three_way(0.01, 0.03, m) == "FAIL"      # CI lower above margin -> confidently worse


def test_three_way_separates_low_precision_from_borderline():
    m = 0.001
    # hw = 0.015 >= margin: PASS would have required point < m - hw < 0, i.e. outright superiority
    assert three_way(-0.01, 0.02, m) == "INCONCLUSIVE_LOW_PRECISION"
    # hw = 0.0004 < margin: the test could have concluded; the estimate genuinely sits at the margin
    assert three_way(0.0006, 0.0014, m) == "NO_DECISION"
    assert three_way(None, None, m) == "INCONCLUSIVE_LOW_PRECISION"  # no CI at all -> nothing was testable


def test_precision_boundary_uses_the_upper_arm_not_the_half_width():
    m = 0.001
    assert three_way(-0.0002, 0.0018, m) == "INCONCLUSIVE_LOW_PRECISION"  # hw == 0.001 == margin
    assert three_way(-0.00019, 0.00179, m) == "NO_DECISION"  # hw just under the margin


def test_strict_intersection_union_still_reported_as_full_assurance():
    crit = ["2026_only", "nk", "2026_nk"]
    assert subgroup_guard({"2026_only": "PASS", "nk": "PASS", "2026_nk": "PASS"}, crit) is True
    assert subgroup_guard(
        {"2026_only": "PASS", "nk": "NO_DECISION", "2026_nk": "PASS"}, crit
    ) is False
    assert subgroup_guard({"2026_only": "PASS", "nk": "PASS", "2026_nk": "FAIL"}, crit) is False
    assert subgroup_guard({"2026_only": "PASS"}, crit) is False  # missing critical


def test_guard_status_vetoes_only_on_evidence_of_harm():
    crit = ["2026_only", "nk", "2026_nk"]
    full = {"2026_only": "PASS", "nk": "PASS", "2026_nk": "PASS"}
    assert subgroup_guard_status(full, crit) == "PASS"
    # untestable subgroups -> not proven, but NOT evidence against the candidate
    assert subgroup_guard_status({**full, "nk": "INCONCLUSIVE_LOW_PRECISION"}, crit) == "NOT_PROVEN"
    assert subgroup_guard_status({**full, "nk": "NO_DECISION"}, crit) == "NOT_PROVEN"
    # a FAIL anywhere dominates, even alongside untestable ones
    assert subgroup_guard_status(
        {"2026_only": "INCONCLUSIVE_LOW_PRECISION", "nk": "FAIL", "2026_nk": "PASS"}, crit
    ) == "FAIL"
    # never computed = wiring fault, stays fail-closed and distinguishable from "untestable"
    assert subgroup_guard_status({"2026_only": "PASS"}, crit) == "MISSING"
    assert subgroup_guard_status({}, []) == "PASS"  # nothing declared critical

"""Feature 061: speed figure — as-of baseline boundaries, z semantics, aggregation (INV-F1/F4).

The baseline is the repo's second cross-horse as-of statistic used per-run (after 058's
popularity), and the FIRST whose per-run value depends on OTHER races' results — so the
same-day/future invariance tests cover the baseline itself, not just the horse aggregation.
"""

from __future__ import annotations

import pandas as pd
import pytest

import horseracing_features.speed_figure_features as sf
from horseracing_features.speed_figure_features import (
    SPEED_FIGURE_COLUMNS,
    build_speed_figure_features,
)
from tests._frames import make_frames

_TARGET = "200806010101"


def _race(rid, date, horses, **race_kw):
    return {"race_id": rid, "race_date": date, "horses": horses, **race_kw}


def _h(hid, ft, **kw):
    return {"horse_id": hid, "finish_time": ft, **kw}


def _base_specs():
    """Cell = default (05/1600/芝/良). Baseline days: 01-01 (100, 102), then H runs 03-01 (95),
    target 06-01. With MIN_RACES=2: baseline before 03-01 = mean 101, std 1 -> z=(101-95)/1=6->5."""
    return [
        _race("200801010101", "2008-01-01", [_h("A", 100.0)]),
        _race("200801010102", "2008-01-01", [_h("B", 102.0)]),
        _race("200803010101", "2008-03-01", [_h("H", 95.0), _h("C", 99.0)]),
        _race(_TARGET, "2008-06-01", [_h("H", 96.0), _h("D", 97.0)]),
    ]


def _target_rows(frames):
    out = build_speed_figure_features(frames)
    return (
        out[out.race_id == _TARGET]
        .set_index("horse_id")
        .sort_index()[SPEED_FIGURE_COLUMNS]
    )


@pytest.fixture(autouse=True)
def _small_min_races(monkeypatch):
    monkeypatch.setattr(sf, "MIN_RACES", 2)


def test_z_uses_strictly_before_baseline_and_clips():
    rows = _target_rows(make_frames(_base_specs()))
    h = rows.loc["H"]
    # H's 03-01 run: baseline mean 101 (100,102), std 1 -> raw z=6 -> clipped to 5
    assert h["asof_spdfig_last"] == 5.0
    assert h["asof_spdfig_avg"] == 5.0
    assert h["asof_spdfig_best"] == 5.0
    assert h["asof_spdfig_count"] == 1.0
    # D has no valid past figure -> NaN values, count 0.0 (a fact, not Unknown)
    d = rows.loc["D"]
    assert pd.isna(d["asof_spdfig_avg"]) and d["asof_spdfig_count"] == 0.0


def test_invariant_to_target_same_day_and_future_times():
    base = _target_rows(make_frames(_base_specs()))
    # (i) target race's own times
    specs = _base_specs()
    specs[3]["horses"][0]["finish_time"] = 80.0
    pd.testing.assert_frame_equal(base, _target_rows(make_frames(specs)), check_exact=True)
    # (ii) same-day other race in the SAME cell (would shift the baseline if leaked)
    specs = _base_specs() + [_race("200806010102", "2008-06-01", [_h("Y", 50.0)])]
    pd.testing.assert_frame_equal(base, _target_rows(make_frames(specs)), check_exact=True)
    # (iii) FUTURE race (pool-end independence / INV-F1, materialize safety)
    specs = _base_specs() + [_race("200812010101", "2008-12-01", [_h("Z", 50.0)])]
    pd.testing.assert_frame_equal(base, _target_rows(make_frames(specs)), check_exact=True)


def test_same_day_races_excluded_from_that_days_baseline():
    """A run's own DAY (including same-day OTHER races of the cell) never enters its baseline."""
    base = _target_rows(make_frames(_base_specs()))
    # add another race on H's run day (03-01), same cell, extreme time — must not move H's z
    specs = _base_specs() + [_race("200803010102", "2008-03-01", [_h("E", 60.0)])]
    pd.testing.assert_frame_equal(base, _target_rows(make_frames(specs)), check_exact=True)


def test_positive_control_past_baseline_times_move_the_feature():
    base = _target_rows(make_frames(_base_specs()))
    specs = _base_specs()
    specs[0]["horses"][0]["finish_time"] = 90.0  # baseline day time changes -> z changes
    mutated = _target_rows(make_frames(specs))
    assert base.loc["H", "asof_spdfig_last"] != mutated.loc["H", "asof_spdfig_last"]


def test_min_races_boundary_and_std_zero():
    # only ONE prior baseline race (MIN_RACES=2) -> NaN figure
    specs = [
        _race("200801010101", "2008-01-01", [_h("A", 100.0)]),
        _race("200803010101", "2008-03-01", [_h("H", 95.0)]),
        _race(_TARGET, "2008-06-01", [_h("H", 96.0)]),
    ]
    rows = _target_rows(make_frames(specs))
    assert pd.isna(rows.loc["H", "asof_spdfig_avg"])
    assert rows.loc["H", "asof_spdfig_count"] == 0.0
    # two prior races with IDENTICAL times -> std 0 -> NaN (no fake certainty)
    specs = [
        _race("200801010101", "2008-01-01", [_h("A", 100.0)]),
        _race("200801020101", "2008-01-02", [_h("B", 100.0)]),
        _race("200803010101", "2008-03-01", [_h("H", 95.0)]),
        _race(_TARGET, "2008-06-01", [_h("H", 96.0)]),
    ]
    rows = _target_rows(make_frames(specs))
    assert pd.isna(rows.loc["H", "asof_spdfig_avg"])


def test_non_finishers_do_not_feed_baseline_or_figures():
    from horseracing_db.enums import ResultStatus

    specs = _base_specs()
    # a stopped horse in a baseline race: no finish contribution
    specs[0]["horses"].append(
        {"horse_id": "S", "finish_time": 30.0, "result_status": ResultStatus.STOPPED}
    )
    base = _target_rows(make_frames(_base_specs()))
    pd.testing.assert_frame_equal(base, _target_rows(make_frames(specs)), check_exact=True)


def test_aggregation_avg_best_recent_last_count():
    # H: two past figure runs (03-01 z=+5 clipped; 04-01 z computed from 3 prior baseline races)
    specs = [
        _race("200801010101", "2008-01-01", [_h("A", 100.0)]),
        _race("200801010102", "2008-01-01", [_h("B", 102.0)]),
        _race("200803010101", "2008-03-01", [_h("H", 95.0), _h("C", 99.0)]),
        _race("200804010101", "2008-04-01", [_h("H", 101.0)]),
        _race(_TARGET, "2008-06-01", [_h("H", 96.0)]),
    ]
    rows = _target_rows(make_frames(specs))
    h = rows.loc["H"]
    # second run's baseline: samples {100, 102, 97 (=mean(95,99))}; mean 99.666.., std>0
    assert h["asof_spdfig_count"] == 2.0
    assert h["asof_spdfig_best"] == 5.0            # cummax keeps the faster figure
    assert h["asof_spdfig_last"] != 5.0            # last = most recent run's z (the slower one)
    assert h["asof_spdfig_avg"] == pytest.approx((5.0 + h["asof_spdfig_last"]) / 2)
    assert h["asof_spdfig_recent3"] == h["asof_spdfig_avg"]  # only 2 runs -> same window


def test_different_cell_does_not_share_baseline():
    # baseline races on a DIFFERENT distance must not qualify the 1600m cell
    specs = [
        _race("200801010101", "2008-01-01", [_h("A", 100.0)], distance=2000),
        _race("200801010102", "2008-01-01", [_h("B", 102.0)], distance=2000),
        _race("200803010101", "2008-03-01", [_h("H", 95.0)]),  # 1600m: no 1600 baseline
        _race(_TARGET, "2008-06-01", [_h("H", 96.0)]),
    ]
    rows = _target_rows(make_frames(specs))
    assert pd.isna(rows.loc["H", "asof_spdfig_avg"])
    assert rows.loc["H", "asof_spdfig_count"] == 0.0


def test_same_day_past_run_excluded_from_target_aggregation():
    # H's only figure run is on the TARGET day -> allow_exact_matches=False excludes it
    specs = [
        _race("200801010101", "2008-01-01", [_h("A", 100.0)]),
        _race("200801010102", "2008-01-01", [_h("B", 102.0)]),
        _race("200806010199", "2008-06-01", [_h("H", 95.0)]),  # same day as target
        _race(_TARGET, "2008-06-01", [_h("H", 96.0)]),
    ]
    rows = _target_rows(make_frames(specs))
    assert pd.isna(rows.loc["H", "asof_spdfig_avg"])
    assert rows.loc["H", "asof_spdfig_count"] == 0.0


# --- additive safety + registry integrity (T006, 058 同型) ---------------------------


def test_speed_figure_is_purely_additive():
    """Left-merge cannot perturb shared columns iff (a) right keys are unique and
    (b) column names are disjoint from every other block's output."""
    frames = make_frames(_base_specs())
    out = build_speed_figure_features(frames)
    assert not out.duplicated(subset=["race_id", "horse_id"]).any()
    from horseracing_features.registry import REGISTRY

    non_sf = {c for c, m in REGISTRY.items() if m.source != "speed_figure"}
    assert not (set(SPEED_FIGURE_COLUMNS) & non_sf)


def test_registry_version_and_compat_pins():
    from horseracing_features.registry import (
        COMPATIBLE_PRIOR_FEATURE_VERSIONS,
        FEATURE_VERSION,
        STATIC_COLUMNS,
        is_feature_version_servable,
        model_input_features,
    )

    assert FEATURE_VERSION == "features-019"  # 070 past-market bundle (additive on 018)
    # 061's historical compat story (features-016 pinned 014/015) still holds when checked against
    # that version explicitly.
    pins = COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-016"]
    assert set(pins) == {"features-014", "features-015"}
    assert is_feature_version_servable("features-015", pins["features-015"], "features-016")
    assert is_feature_version_servable("features-014", pins["features-014"], "features-016")
    assert not is_feature_version_servable("features-015", "deadbeef", "features-016")
    # Feature 017 is a value-changing bump: its compat map is EMPTY (fail-closed).
    assert COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-017"] == {}
    # Feature 069 (018) is ADDITIVE on 017, so it pins lgbm-063's features-017 hash (servable).
    assert set(COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-018"]) == {"features-017"}
    # 061 columns are as-of (materialized), NEVER static; and they are model inputs
    assert not (set(SPEED_FIGURE_COLUMNS) & set(STATIC_COLUMNS))
    assert set(SPEED_FIGURE_COLUMNS) <= set(model_input_features())

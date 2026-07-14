"""Feature 063 (spike): closing-speed figure — minimal correctness + leak/additive guards.

Clone of 061's speed_figure semantics on last_3f, so the deep INV suite would duplicate 061;
this keeps the spike-stage tests to the load-bearing ones (strictly-before, additive, registry).
"""

from __future__ import annotations

import pandas as pd
import pytest

import horseracing_features.closing_figure_features as cf
from horseracing_features.closing_figure_features import (
    CLOSING_FIGURE_COLUMNS,
    build_closing_figure_features,
)
from tests._frames import make_frames

_TARGET = "200806010101"


def _race(rid, date, horses, **kw):
    return {"race_id": rid, "race_date": date, "horses": horses, **kw}


def _h(hid, l3f, **kw):
    return {"horse_id": hid, "last_3f": l3f, **kw}


def _target_rows(frames):
    out = build_closing_figure_features(frames)
    return out[out.race_id == _TARGET].set_index("horse_id").sort_index()[CLOSING_FIGURE_COLUMNS]


@pytest.fixture(autouse=True)
def _small_min_races(monkeypatch):
    monkeypatch.setattr(cf, "MIN_RACES", 2)


def _base_specs():
    # cell baseline last_3f from 01-01 (34.0, 36.0 -> mean 35, std 1); H closes in 34.0 on 03-01
    # -> z=(35-34)/1=1; target 06-01
    return [
        _race("200801010101", "2008-01-01", [_h("A", 34.0)]),
        _race("200801010102", "2008-01-01", [_h("B", 36.0)]),
        _race("200803010101", "2008-03-01", [_h("H", 34.0), _h("C", 35.0)]),
        _race(_TARGET, "2008-06-01", [_h("H", 35.0), _h("D", 34.0)]),
    ]


def test_z_faster_closing_is_positive():
    rows = _target_rows(make_frames(_base_specs()))
    h = rows.loc["H"]
    # baseline mean 35, std 1; H's 03-01 last_3f 34.0 -> z=(35-34)/1 = +1 (faster closing)
    assert h["asof_closefig_last"] == pytest.approx(1.0)
    assert h["asof_closefig_count"] == 1.0
    d = rows.loc["D"]
    assert pd.isna(d["asof_closefig_avg"]) and d["asof_closefig_count"] == 0.0


def test_strictly_before_same_day_and_future_invariance():
    base = _target_rows(make_frames(_base_specs()))
    # target own last_3f
    specs = _base_specs()
    specs[3]["horses"][0]["last_3f"] = 30.0
    pd.testing.assert_frame_equal(base, _target_rows(make_frames(specs)), check_exact=True)
    # same-day other race in the same cell
    specs = _base_specs() + [_race("200806010102", "2008-06-01", [_h("Y", 30.0)])]
    pd.testing.assert_frame_equal(base, _target_rows(make_frames(specs)), check_exact=True)
    # future race
    specs = _base_specs() + [_race("200812010101", "2008-12-01", [_h("Z", 30.0)])]
    pd.testing.assert_frame_equal(base, _target_rows(make_frames(specs)), check_exact=True)


def test_positive_control_past_baseline_moves_figure():
    base = _target_rows(make_frames(_base_specs()))
    specs = _base_specs()
    specs[0]["horses"][0]["last_3f"] = 33.0  # shifts the baseline
    assert base.loc["H", "asof_closefig_last"] != _target_rows(
        make_frames(specs)
    ).loc["H", "asof_closefig_last"]


def test_not_in_default_feature_set():
    """063 was REJECTED at the full 19-fold gate (redundant with 061 over the full period). The
    columns must NOT be registered / model inputs — the module + these tests are preserved as the
    documented negative result, not wired into production (FEATURE_VERSION stays features-016)."""
    from horseracing_features.registry import (
        FEATURE_GROUPS,
        FEATURE_VERSION,
        model_input_features,
    )

    frames = make_frames(_base_specs())
    out = build_closing_figure_features(frames)
    assert not out.duplicated(subset=["race_id", "horse_id"]).any()
    assert list(out.columns) == ["race_id", "horse_id", *CLOSING_FIGURE_COLUMNS]
    assert FEATURE_VERSION == "features-018"  # 063 still rejected; 018 = 069 F02 pm_core_strength
    assert not (set(CLOSING_FIGURE_COLUMNS) & set(model_input_features()))
    assert "closing_figure" not in set(FEATURE_GROUPS.values())

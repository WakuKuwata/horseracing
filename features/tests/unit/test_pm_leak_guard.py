"""T013: F02 behavioral leak-guard (FR-007/014) + determinism."""

from __future__ import annotations

import numpy as np

from horseracing_features.pm_core_strength import (
    PM_CORE_STRENGTH_COLUMNS,
    build_pm_core_strength_features,
)
from tests._frames import make_frames


def _race(rid, date, odds_by_horse):
    horses = [{"horse_id": h, "odds": o, "finish_order": i + 1}
              for i, (h, o) in enumerate(odds_by_horse.items())]
    return {"race_id": rid, "race_date": date, "horses": horses}


def _target_row(specs, rid="T1", hid="h"):
    out = build_pm_core_strength_features(make_frames(specs))
    return out[(out.race_id == rid) & (out.horse_id == hid)].iloc[0]


def _base_specs():
    return [
        _race("P1", "2020-01-01", {"h": 1.5, "o": 6.0}),
        _race("P2", "2020-02-01", {"h": 3.0, "o": 3.0}),
        _race("T1", "2020-03-01", {"h": 2.0, "o": 2.0}),
    ]


def test_target_own_odds_change_does_not_affect_features():
    base = _target_row(_base_specs())
    # change the TARGET race's own odds -> as-of features must be unchanged (strictly-before)
    changed = list(_base_specs())
    changed[2] = _race("T1", "2020-03-01", {"h": 1.1, "o": 15.0})
    after = _target_row(changed)
    for c in PM_CORE_STRENGTH_COLUMNS:
        assert (np.isnan(base[c]) and np.isnan(after[c])) or base[c] == after[c]


def test_future_race_odds_change_does_not_affect_features():
    base = _target_row(_base_specs())
    changed = _base_specs() + [_race("F1", "2020-05-01", {"h": 1.2, "o": 8.0})]
    after = _target_row(changed)
    for c in PM_CORE_STRENGTH_COLUMNS:
        assert (np.isnan(base[c]) and np.isnan(after[c])) or base[c] == after[c]


def test_same_day_race_excluded():
    base = _target_row(_base_specs())
    # another race on the SAME day as the target must NOT enter (same-day excluded)
    changed = _base_specs() + [_race("S1", "2020-03-01", {"h": 1.3, "o": 5.0})]
    after = _target_row(changed)
    for c in PM_CORE_STRENGTH_COLUMNS:
        assert (np.isnan(base[c]) and np.isnan(after[c])) or base[c] == after[c]


def test_past_odds_change_DOES_affect_features():
    base = _target_row(_base_specs())
    changed = list(_base_specs())
    changed[0] = _race("P1", "2020-01-01", {"h": 5.0, "o": 1.5})  # flip past support
    after = _target_row(changed)
    # at least one continuous feature must change (past support actually feeds the feature)
    assert any(base[c] != after[c] for c in ("asof_pm_support_career", "asof_pm_support_mean5"))


def test_column_names_avoid_leak_tokens():
    for c in PM_CORE_STRENGTH_COLUMNS:
        assert "odds" not in c and "popularity" not in c


def test_no_eval_derived_subgroup_token_in_features():
    # T023a (FR-017): evaluation-derived subgroup/CI/audit values must never be feature columns.
    from horseracing_features.registry import REGISTRY
    forbidden = ("subgroup", "winner_nll", "_guard", "bootstrap")
    for col in REGISTRY:
        assert not any(tok in col for tok in forbidden), col


def test_row_and_horse_order_invariant():
    specs = _base_specs()
    out1 = build_pm_core_strength_features(make_frames(specs))
    # reverse race order and horse order within races
    rev = [
        _race("P2", "2020-02-01", {"o": 3.0, "h": 3.0}),
        _race("T1", "2020-03-01", {"o": 2.0, "h": 2.0}),
        _race("P1", "2020-01-01", {"o": 6.0, "h": 1.5}),
    ]
    out2 = build_pm_core_strength_features(make_frames(rev))
    r1 = out1[(out1.race_id == "T1") & (out1.horse_id == "h")].iloc[0]
    r2 = out2[(out2.race_id == "T1") & (out2.horse_id == "h")].iloc[0]
    for c in PM_CORE_STRENGTH_COLUMNS:
        assert (np.isnan(r1[c]) and np.isnan(r2[c])) or r1[c] == r2[c]

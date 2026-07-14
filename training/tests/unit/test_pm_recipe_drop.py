"""T019/T021: F02 group→column drop expansion (codex F1) + default market-history drop (FR-012)."""

from __future__ import annotations

import pytest
from horseracing_features.registry import FEATURE_GROUPS

from horseracing_training.cli import _expand_group_drops, _recipe_from_spec

_F02_COLS = {c for c, g in FEATURE_GROUPS.items() if g == "pm_core_strength"}
_RANK_COLS = {c for c, g in FEATURE_GROUPS.items() if g == "past_market"}


def test_group_drop_expands_to_columns_not_group_name():
    # bare group name must NOT be used as drop_features (fail-open); it is expanded to columns
    r = _recipe_from_spec("pl_topk:isotonic:0.3:drop=pm_core_strength")
    assert set(r.drop_features) == _F02_COLS
    assert "pm_core_strength" not in r.drop_features  # the group name itself is never a column


def test_active_arm_excludes_pm_core_strength_columns():
    # the F02 paired-eval active arm drops F02 -> none of its 9 columns remain
    r = _recipe_from_spec("pl_topk:isotonic:0.3:drop=pm_core_strength")
    assert _F02_COLS.issubset(set(r.drop_features))
    assert not (_F02_COLS - set(r.drop_features))  # all F02 cols dropped (fail-open prevented)


def test_candidate_keeps_f02_and_058_rank():
    r = _recipe_from_spec("pl_topk:isotonic:0.3")  # no drop -> full features-018
    assert r.drop_features == ()


def test_default_model_drops_both_market_history_groups():
    # p⊥q: the default decision-support model drops past_market (058) AND pm_core_strength (F02)
    cols = set(_expand_group_drops(("past_market", "pm_core_strength")))
    assert _F02_COLS.issubset(cols) and _RANK_COLS.issubset(cols)
    assert len(cols) == len(_F02_COLS) + len(_RANK_COLS)  # 9 + 4 = 13


def test_unknown_group_fails_closed():
    with pytest.raises(ValueError):
        _expand_group_drops(("not_a_real_group",))

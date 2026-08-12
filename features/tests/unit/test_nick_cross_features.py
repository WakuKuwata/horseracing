"""Feature 090: nick-cross residual correctness, pooling, and leak boundaries."""

from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

import horseracing_features.nick_cross_features as nick_module
from horseracing_features.loader import Frames
from horseracing_features.nick_cross_features import (
    EPS_HI,
    EPS_LO,
    LAMBDA_L0,
    LAMBDA_L1,
    build_nick_cross_features,
)
from tests._frames import make_frames

_TARGET_RACE_ID = "200802010001"


def _observations(
    prefix: str,
    outcomes: list[int],
    *,
    sire: str,
    damsire: str,
    line: str | None,
) -> list[dict]:
    """Build unique horses whose finish order is 1 for a win and 2 for a loss."""
    return [
        {
            "horse_id": f"{prefix}{i}",
            "finish_order": 1 if won else 2,
            "sire_name": sire,
            "damsire_name": damsire,
            "damsire_line": line,
        }
        for i, won in enumerate(outcomes)
    ]


def _specs(history: list[dict], *, target: dict | None = None) -> list[dict]:
    target = target or {
        "horse_id": "H",
        "finish_order": 4,
        "sire_name": "S",
        "damsire_name": "DS0",
        "damsire_line": "L",
    }
    specs = [
        {
            "race_id": f"20080101{i:04d}",
            "race_date": "2008-01-01",
            "horses": [horse],
        }
        for i, horse in enumerate(history)
    ]
    specs.append(
        {"race_id": _TARGET_RACE_ID, "race_date": "2008-02-01", "horses": [target]}
    )
    return specs


def _row(frames: Frames, *, horse_id: str = "H") -> pd.Series:
    out = build_nick_cross_features(frames)
    return out[(out["race_id"] == _TARGET_RACE_ID) & (out["horse_id"] == horse_id)].iloc[0]


def _hand_history() -> list[dict]:
    # Target cell A: 1/2. Same L1 but another L0 cell B: 0/2.
    # Other sire observations make p_sire=2/6 and p_damsire=2/4.
    # Four unrelated losses make p_overall=3/12.
    return [
        *_observations("A", [1, 0], sire="S", damsire="DS0", line="L"),
        *_observations("B", [0, 0], sire="S", damsire="DS1", line="L"),
        *_observations("C", [1, 0], sire="S", damsire="DS2", line="M"),
        *_observations("D", [1, 0], sire="X", damsire="DS0", line="L"),
        *_observations("E", [0, 0, 0, 0], sire="U", damsire="UD", line="U"),
    ]


def _balanced_history(l0_won: int) -> list[dict]:
    """Keep all marginals at .5 while changing only the target L0 cell's result.

    C and D compensate the sire/damsire marginals; E compensates the global marginal. The
    leave-child-out L1 evidence is B (1/2) in both variants.
    """
    return [
        *_observations("A", [l0_won], sire="S", damsire="DS0", line="L"),
        *_observations("B", [1, 0], sire="S", damsire="DS1", line="L"),
        *_observations("C", [1 - l0_won], sire="S", damsire="DS2", line="M"),
        *_observations("D", [1 - l0_won], sire="X", damsire="DS0", line="L"),
        *_observations("E", [l0_won], sire="U", damsire="UD", line="U"),
    ]


def _mu_l0(row: pd.Series, expected: float) -> float:
    return float(expected * np.exp(row["nick_lift_log"]))


def test_frozen_constants_and_no_hard_cell_threshold():
    assert LAMBDA_L0 == 350.0
    assert LAMBDA_L1 == 350.0
    assert EPS_LO == 1e-4
    assert EPS_HI == 0.9
    assert not hasattr(nick_module, "MIN_CELL")


def test_hand_calculation_expected_nested_means_lift_and_count():
    row = _row(make_frames(_specs(_hand_history())))

    p_sire = 2 / 6
    p_damsire = 2 / 4
    p_overall = 3 / 12
    expected = p_sire * p_damsire / p_overall
    mu_l1 = (0 + LAMBDA_L1 * expected) / (2 + LAMBDA_L1)
    mu_l0 = (1 + LAMBDA_L0 * mu_l1) / (2 + LAMBDA_L0)

    assert expected == 2 / 3
    assert row["nick_obs_count"] == 2.0
    np.testing.assert_allclose(
        row["nick_lift_log"], np.log(mu_l0) - np.log(expected), rtol=0.0, atol=1e-15
    )


def test_cell_at_expected_rate_has_zero_residual():
    history = [
        *_observations("A", [1, 0], sire="S", damsire="DS0", line="L"),
        *_observations("B", [1, 0], sire="S", damsire="DS1", line="L"),
        *_observations("D", [1, 0], sire="X", damsire="DS0", line="L"),
    ]

    row = _row(make_frames(_specs(history)))

    assert row["nick_lift_log"] == 0.0
    assert row["nick_obs_count"] == 2.0


def test_l0_pooling_moves_continuously_toward_raw_cell_rate():
    rows: dict[int, tuple[pd.Series, float, float]] = {}
    for n_l0 in (1, 10):
        history = [
            *_observations("A", [1] * n_l0, sire="S", damsire="DS0", line="L"),
            *_observations("B", [0] * 10, sire="S", damsire="DS1", line="L"),
            *_observations("D", [1] * 5 + [0] * 5, sire="X", damsire="DS0", line="L"),
            *_observations("E", [1] * 5 + [0] * 5, sire="U", damsire="UD", line="U"),
        ]
        row = _row(make_frames(_specs(history)))
        p_sire = n_l0 / (n_l0 + 10)
        p_damsire = (n_l0 + 5) / (n_l0 + 10)
        p_overall = (n_l0 + 10) / (n_l0 + 30)
        expected = float(np.clip(p_sire * p_damsire / p_overall, EPS_LO, EPS_HI))
        mu_l1 = LAMBDA_L1 * expected / (10 + LAMBDA_L1)
        rows[n_l0] = (row, expected, mu_l1)

    weights = []
    for n_l0, (row, expected, mu_l1) in rows.items():
        mu_l0 = _mu_l0(row, expected)
        weight = (mu_l0 - mu_l1) / (1.0 - mu_l1)
        np.testing.assert_allclose(weight, n_l0 / (n_l0 + LAMBDA_L0), atol=1e-14)
        weights.append(weight)

    assert weights[1] > weights[0] > 0.0


def test_l1_parent_is_leave_child_out():
    inferred_mu_l1 = []
    for l0_won in (0, 1):
        row = _row(make_frames(_specs(_balanced_history(l0_won))))
        mu_l0 = _mu_l0(row, expected=0.5)
        mu_l1 = ((1 + LAMBDA_L0) * mu_l0 - l0_won) / LAMBDA_L0
        inferred_mu_l1.append(mu_l1)

    np.testing.assert_allclose(inferred_mu_l1, [0.5, 0.5], rtol=0.0, atol=1e-14)


def test_zero_l0_and_l1_observations_fall_back_to_expected():
    history = [
        *_observations("C", [1, 0], sire="S", damsire="DS2", line="M"),
        *_observations("D", [1, 0], sire="X", damsire="DS0", line="L"),
    ]

    row = _row(make_frames(_specs(history)))

    assert row["nick_lift_log"] == 0.0
    assert row["nick_obs_count"] == 0.0


def test_missing_damsire_line_uses_expected_as_l1_parent():
    history = [
        *_observations("A", [1], sire="S", damsire="DS0", line=None),
        *_observations("C", [0], sire="S", damsire="DS2", line="M"),
        *_observations("D", [0], sire="X", damsire="DS0", line=None),
        *_observations("E", [1], sire="U", damsire="UD", line="U"),
    ]
    target = {
        "horse_id": "H",
        "finish_order": 4,
        "sire_name": "S",
        "damsire_name": "DS0",
        "damsire_line": None,
    }

    row = _row(make_frames(_specs(history, target=target)))
    expected = 0.5
    mu_l0 = (1 + LAMBDA_L0 * expected) / (1 + LAMBDA_L0)

    np.testing.assert_allclose(
        row["nick_lift_log"], np.log(mu_l0) - np.log(expected), rtol=0.0, atol=1e-15
    )
    assert row["nick_obs_count"] == 1.0


def test_target_horses_own_history_is_excluded_from_every_aggregate():
    rows = []
    for own_won in (0, 1):
        own = {
            "horse_id": "H",
            "finish_order": 1 if own_won else 2,
            "sire_name": "S",
            "damsire_name": "DS0",
            "damsire_line": "L",
        }
        rows.append(_row(make_frames(_specs([*_balanced_history(0), own]))))

    pd.testing.assert_series_equal(rows[0], rows[1])


def test_same_day_other_horse_is_excluded():
    base_specs = _specs(_balanced_history(0))
    same_day = {
        "race_id": "200802010002",
        "race_date": "2008-02-01",
        "horses": _observations("Z", [1], sire="S", damsire="DS0", line="L"),
    }

    base = _row(make_frames(base_specs))
    changed = _row(make_frames([*base_specs, same_day]))

    pd.testing.assert_series_equal(base, changed)


def test_future_race_is_excluded():
    base_specs = _specs(_balanced_history(0))
    future = {
        "race_id": "200803010001",
        "race_date": "2008-03-01",
        "horses": _observations("Z", [1], sire="S", damsire="DS0", line="L"),
    }

    base = _row(make_frames(base_specs))
    changed = _row(make_frames([*base_specs, future]))

    pd.testing.assert_series_equal(base, changed)


def test_target_result_and_odds_do_not_enter_features():
    target_a = {
        "horse_id": "H",
        "finish_order": 1,
        "odds": 1.1,
        "sire_name": "S",
        "damsire_name": "DS0",
        "damsire_line": "L",
    }
    target_b = {**target_a, "finish_order": 9, "odds": 999.9}

    row_a = _row(make_frames(_specs(_balanced_history(0), target=target_a)))
    row_b = _row(make_frames(_specs(_balanced_history(0), target=target_b)))

    pd.testing.assert_series_equal(row_a, row_b)


def test_missing_sire_or_damsire_is_nan_for_both_features():
    targets = [
        {
            "horse_id": "NO_SIRE",
            "finish_order": 2,
            "sire_name": None,
            "damsire_name": "DS0",
            "damsire_line": "L",
        },
        {
            "horse_id": "NO_DAMSIRE",
            "finish_order": 3,
            "sire_name": "S",
            "damsire_name": None,
            "damsire_line": None,
        },
    ]
    frames = make_frames(
        [{"race_id": _TARGET_RACE_ID, "race_date": "2008-02-01", "horses": targets}]
    )

    out = build_nick_cross_features(frames).set_index("horse_id")

    assert out.loc["NO_SIRE", ["nick_lift_log", "nick_obs_count"]].isna().all()
    assert out.loc["NO_DAMSIRE", ["nick_lift_log", "nick_obs_count"]].isna().all()


def test_unseen_l0_uses_nonzero_parent_value_with_zero_observation_count():
    history = [
        *_observations("B", [1], sire="S", damsire="DS1", line="L"),
        *_observations("C", [0], sire="S", damsire="DS2", line="M"),
        *_observations("D", [0], sire="X", damsire="DS0", line="L"),
    ]

    row = _row(make_frames(_specs(history)))

    assert pd.notna(row["nick_lift_log"])
    assert row["nick_lift_log"] > 0.0
    assert row["nick_obs_count"] == 0.0


def test_output_is_deterministic_under_input_row_reordering():
    frames = make_frames(_specs(_hand_history()))
    shuffled = Frames(
        races=frames.races.sample(frac=1, random_state=1).reset_index(drop=True),
        race_horses=frames.race_horses.sample(frac=1, random_state=2).reset_index(drop=True),
        race_results=frames.race_results.sample(frac=1, random_state=3).reset_index(drop=True),
        horses=frames.horses.sample(frac=1, random_state=4).reset_index(drop=True),
    )

    assert_frame_equal(
        build_nick_cross_features(frames),
        build_nick_cross_features(shuffled),
        check_exact=True,
    )


def test_projection_matches_full_build_including_global_overall_primitive():
    frames = make_frames(_specs(_hand_history()))
    full = build_nick_cross_features(frames)
    expected = full[full["race_id"] == _TARGET_RACE_ID].reset_index(drop=True)

    projected = build_nick_cross_features(
        frames, target_race_ids=frozenset({_TARGET_RACE_ID})
    )

    assert_frame_equal(projected, expected, check_exact=True)


def test_feature_columns_are_always_float64():
    out = build_nick_cross_features(make_frames(_specs(_hand_history())))

    assert str(out["nick_lift_log"].dtype) == "float64"
    assert str(out["nick_obs_count"].dtype) == "float64"


def test_partial_damsire_line_coverage_keeps_the_parent_a_real_superset():
    """The L1 parent groups by damsire_line, so runs whose line is unknown drop out of it while
    L0 still counts them. Subtracting the UNRESTRICTED child from that parent takes away more
    than the parent holds, yielding a NEGATIVE parent count and a silently wrong residual — it
    stays finite, so an isfinite check does not catch it.

    Not hypothetical: 391 of 2,535 damsires in the real DB carry a line for some horses and none
    for others, putting 36.7% of started rows on this path. The populations here are sized against
    LAMBDA (350) on purpose; with a handful of runs the prior swamps the error and hides it.
    """
    child = [1] * 100 + [0] * 300   # (S, DS0), line UNKNOWN: 400 runs / 100 wins
    sibling = [1] * 60 + [0] * 240  # (S, DS1), line "L"    : 300 runs /  60 wins
    history = [
        *_observations("unlined", child, sire="S", damsire="DS0", line=None),
        *_observations("sib", sibling, sire="S", damsire="DS1", line="L"),
    ]
    row = _row(make_frames(_specs(history)))

    expected = 0.25  # (160/700 * 100/400) / (160/700); no clip
    # Parent = the 300 line-known sibling runs. The line-known part of the child is EMPTY, so
    # nothing is subtracted. The buggy variant would use 300 - 400 = -100 runs / 60 - 100 = -40.
    mu_l1 = (60 + LAMBDA_L1 * expected) / (300 + LAMBDA_L1)
    mu_l0 = (100 + LAMBDA_L0 * mu_l1) / (400 + LAMBDA_L0)

    assert row["nick_obs_count"] == 400.0
    np.testing.assert_allclose(
        row["nick_lift_log"], np.log(mu_l0) - np.log(expected), rtol=0.0, atol=1e-12
    )
    # Guard the guard: the buggy formula lands far away, so this test genuinely discriminates.
    bad_mu_l1 = (60 - 100 + LAMBDA_L1 * expected) / (300 - 400 + LAMBDA_L1)
    bad_mu_l0 = (100 + LAMBDA_L0 * bad_mu_l1) / (400 + LAMBDA_L0)
    assert abs((np.log(bad_mu_l0) - np.log(expected)) - row["nick_lift_log"]) > 0.05

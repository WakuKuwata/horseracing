"""Boundary and scalar parity tests for the pure 129 correction interface."""

import math

import numpy as np
import pandas as pd
import pytest
from horseracing_eval.predictor import Prediction
from horseracing_training.predictor import assemble_predictions

from horseracing_serving import mixture_correction as m


def target():
    return pd.DataFrame({"race_id": ["r"] * 3, "horse_id": ["a", "b", "c"],
                         "race_date": ["2024-03-01"] * 3,
                         "days_since_last": [15., np.nan, 30.], "sex": ["牝", None, "セ"]},
                        index=[9, 4, 2])


def history():
    return pd.DataFrame({"race_id": ["x", "y", "z", "same", "future", "pre", "cancel"],
                         "horse_id": ["a"] * 7,
                         "race_date": ["2024-01-01", "2024-02-01", "2024-02-01",
                                       "2024-03-01", "2024-04-01", "2006-12-31", "2024-02-20"],
                         "entry_status": ["started"] * 6 + ["cancelled"]})


def inputs():
    return m.build_correction_inputs(target(), history())


def test_strict_distinct_days_missing_and_row_order():
    t, h = target(), history()
    before_t, before_h = t.copy(deep=True), h.copy(deep=True)
    out = m.build_correction_inputs(t, h)
    assert out.index.tolist() == [9, 4, 2]
    assert out.horse_id.tolist() == ["a", "b", "c"]
    assert out.iloc[0].prior_gap_log == np.log1p(31)
    assert out.iloc[0].gap_log == np.log1p(15)  # source feature is preserved
    assert out.prior_gap_log.iloc[1:].isna().all()
    assert math.isnan(out.iloc[1].female_sin)
    assert out.iloc[2].female_cos == 0
    theta = 2 * np.pi * 60 / 366
    assert out.iloc[0].female_sin == np.sin(theta)
    pd.testing.assert_frame_equal(t, before_t)
    pd.testing.assert_frame_equal(h, before_h)


def test_started_only_matrix_history_and_unlabelled_race():
    h = history().query("entry_status == 'started'").drop(columns="entry_status")
    pd.testing.assert_frame_equal(m.build_correction_inputs(target(), h), inputs())


def test_prefix_parity_and_id_discontinuity():
    h = history()
    prefix = h[h.race_date < "2024-03-01"]
    pd.testing.assert_frame_equal(m.build_correction_inputs(target(), prefix), inputs())
    h.loc[h.race_id == "x", "horse_id"] = "old-a"
    assert math.isnan(m.build_correction_inputs(target(), h).iloc[0].prior_gap_log)


def test_history_start_is_inclusive_and_empty_history():
    h = pd.DataFrame({"race_id": ["x", "y", "z"], "horse_id": ["a"] * 3,
                      "race_date": ["2006-12-31", "2007-01-01", "2007-01-03"]})
    assert m.build_correction_inputs(target(), h).iloc[0].prior_gap_log == np.log1p(2)
    assert m.build_correction_inputs(target(), h.iloc[:0]).prior_gap_log.isna().all()


@pytest.mark.parametrize("day,denom,doy", [("2023-03-01", 365, 60), ("2024-02-29", 366, 60),
                                          ("2026-01-01", 365, 1)])
def test_calendar_formula(day, denom, doy):
    t = target(); t["race_date"] = day
    out = m.build_correction_inputs(t, history())
    assert out.iloc[0].female_sin == np.sin(2 * np.pi * ((doy - 1) / denom))
    assert out.iloc[0].female_cos == np.cos(2 * np.pi * ((doy - 1) / denom))


@pytest.mark.parametrize("value", [0, -1, 2.5, np.inf, -np.inf])
def test_bad_current_gap(value):
    t = target(); t.loc[9, "days_since_last"] = value
    with pytest.raises(ValueError): m.build_correction_inputs(t, history())


@pytest.mark.parametrize("kind", ["duplicate", "conflicting_day", "unknown_status", "missing_id"])
def test_history_identity_rejected(kind):
    h = history()
    if kind == "duplicate": h = pd.concat([h, h.iloc[[0]]])
    if kind == "conflicting_day": h.loc[1, "race_id"] = "x"
    if kind == "unknown_status": h.loc[0, "entry_status"] = "pending"
    if kind == "missing_id": h.loc[0, "horse_id"] = None
    with pytest.raises(ValueError): m.build_correction_inputs(target(), h)


@pytest.mark.parametrize("column,value", [("sex", "female"), ("race_date", "2006-01-01"),
                                          ("race_date", "2024-03-01T01:00:00"), ("horse_id", "b")])
def test_bad_targets(column, value):
    t = target(); t.loc[9, column] = value
    with pytest.raises(ValueError): m.build_correction_inputs(t, history())


def test_target_history_conflicting_identity():
    h = pd.DataFrame({"race_id": ["r"], "horse_id": ["a"], "race_date": ["2024-02-01"]})
    with pytest.raises(ValueError): m.build_correction_inputs(target(), h)


def test_scalar_joint_tilt_and_all_heads():
    rows = inputs(); p = np.array([.2, .3, .5]); beta = [.1, -.04, .2, -.1, .15]
    result = m.correct_member_predictions(["a", "b", "c"], p, rows, m.JOINT_TERMS, beta)
    mean_log = sum(math.log(v) for v in p) / 3
    offsets = []
    for i, row in enumerate(rows.itertuples()):
        raw = [row.gap_log, row.prior_gap_log, row.female_sin, row.female_cos]
        h = [0. if math.isnan(v) else v for v in raw] + [math.log(p[i]) - mean_log]
        offsets.append(sum(a * b for a, b in zip(h, beta, strict=True)))
    unnorm = [v * math.exp(z - max(offsets)) for v, z in zip(p, offsets, strict=True)]
    expected = assemble_predictions(["a", "b", "c"], np.array(unnorm) / sum(unnorm), eps=0)
    for h in result:
        np.testing.assert_allclose([result[h].win, result[h].top2, result[h].top3],
                                   [expected[h].win, expected[h].top2, expected[h].top3], atol=1e-12, rtol=0)


def test_zero_coefficients_do_not_reclip_tiny_win():
    p = np.array([1e-8, .2, .79999999])
    result = m.correct_member_predictions(["a", "b", "c"], p, inputs(), ["gap_log"], [0.])
    np.testing.assert_allclose([v.win for v in result.values()], p, atol=1e-12, rtol=0)
    assert result["a"].win < 1e-6


def test_missing_offset_is_neutral_not_fixed_probability():
    result = m.correct_member_predictions(["a", "b", "c"], [.2, .3, .5], inputs(), ["gap_log"], [.2])
    assert result["b"].win != .3


@pytest.mark.parametrize("p", [[0, .5, .5], [np.nan, .5, .5], [np.inf, .5, .5],
                               [.2, .3, .4], [.2, .8], [[.2, .3, .5]], [1, 0, 0]])
def test_bad_base_probabilities(p):
    with pytest.raises(ValueError):
        m.correct_member_predictions(["a", "b", "c"], p, inputs(), ["gap_log"], [0])


@pytest.mark.parametrize("beta", [[0, 0, 0, 0, -1], [0, 0, 0, 0, -2],
                                  [0, 0, 0, 0, np.inf], [0], ["0"] * 5])
def test_bad_joint_coefficients(beta):
    with pytest.raises(ValueError):
        m.correct_member_predictions(["a", "b", "c"], [.2, .3, .5], inputs(), m.JOINT_TERMS, beta)


def test_bad_term_order_horse_order_and_underflow():
    for ids, terms, beta in [(["b", "a", "c"], ["gap_log"], [0]),
                             (["a", "b", "c"], list(reversed(m.JOINT_TERMS)), [0] * 5),
                             (["a", "b", "c"], ["gap_log"], [1e300])]:
        with pytest.raises(ValueError):
            m.correct_member_predictions(ids, [.2, .3, .5], inputs(), terms, beta)


def test_single_horse_identity():
    result = m.correct_member_predictions(["a"], [1.], inputs().iloc[:1], ["gap_log"], [.1])
    assert result == {"a": Prediction(1., 1., 1.)}


def test_average_all_heads_is_not_harville_of_mean_win():
    a = assemble_predictions(["a", "b", "c"], [.8, .1, .1], eps=0)
    b = assemble_predictions(["a", "b", "c"], [.1, .8, .1], eps=0)
    result = m.average_member_predictions([a] * 3 + [b] * 3)
    assert result["a"].top2 == pytest.approx((a["a"].top2 + b["a"].top2) / 2)
    recalculated = assemble_predictions(list(result), [v.win for v in result.values()], eps=0)
    assert abs(result["a"].top2 - recalculated["a"].top2) > .001


@pytest.mark.parametrize("kind", ["count", "order", "missing", "nan", "sum", "head_order"])
def test_incomplete_or_invalid_mixture_rejected(kind):
    p = assemble_predictions(["a", "b", "c"], [.2, .3, .5], eps=0)
    members = [dict(p) for _ in range(6)]
    if kind == "count": members.pop()
    if kind == "order": members[1] = dict(reversed(list(p.items())))
    if kind == "missing": members[1].pop("a")
    if kind == "nan": members[1]["a"] = Prediction(np.nan, .5, 1.)
    if kind == "sum": members[1]["a"] = Prediction(.4, .5, 1.)
    if kind == "head_order": members[1]["a"] = Prediction(.2, .1, 1.)
    with pytest.raises(ValueError): m.average_member_predictions(members)

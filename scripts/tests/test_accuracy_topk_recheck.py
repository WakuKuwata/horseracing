import datetime as dt
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import accuracy_batch_common as c
import accuracy_topk_recheck as t
from horseracing_eval.dataset import EvalRace, ScoringLabel
from horseracing_eval.predictor import HorseEntry, RaceContext
from horseracing_eval.stage_discount import StageDiscount


def record(rid, year, win=(.5, .3, .15, .05), *, incomplete=False):
    ids = tuple('abcd')
    er = EvalRace(RaceContext(rid, dt.date(year, 2, 1), tuple(HorseEntry(i) for i in ids)),
                  (ScoringLabel('a', 1, 1, 1), ScoringLabel('b', 0, 1, 1), ScoringLabel('c', 0, 0, 1)),
                  n_result_rows=3 if incomplete else 4)
    members = np.stack([c.heads_from_win(win), c.heads_from_win(tuple(reversed(win)))]*3)
    return c.MixedRace(er, ids, members, members.mean(axis=0), pd.DataFrame())


def test_heads_mean_differs_from_reharville_and_fallback_is_reharville():
    r = record('a', 2020)
    arms, fits = t.construct_arms([r])
    assert not np.allclose(arms['head_mean']['a'][:, 1:], arms['mean_win_harville']['a'][:, 1:])
    assert np.array_equal(arms['annual_stage_discount']['a'], arms['mean_win_harville']['a'])
    assert fits[0]['fallback'] and fits[0]['n_samples'] == 0
    assert all(np.array_equal(p['a'][:, 0], r.heads[:, 0]) for p in arms.values())


def test_stage_fit_uses_only_prior_year_and_excludes_incomplete(monkeypatch):
    rows = [record('a', 2020), record('partial', 2020, incomplete=True), record('b', 2021), record('c', 2022)]
    calls = []
    def fake_fit(samples, **kwargs):
        calls.append(list(samples))
        return StageDiscount(lambda2=.8, lambda3=.7, n_races_l2=len(samples), n_races_l3=len(samples))
    monkeypatch.setattr(t, 'fit_stage_discount', fake_fit)
    _, fits = t.annual_stage_predictions(rows)
    assert [len(s) for s in calls] == [0, 1, 2]
    assert [f['fit_through'] for f in fits] == [None, '2020-02-01', '2021-02-01']


def test_current_labels_do_not_change_current_year_stage_predictions():
    first = record('a', 2020)
    current = record('b', 2021)
    altered = record('b', 2021)
    altered.er = EvalRace(altered.er.context, (ScoringLabel('d', 1, 1, 1),), n_result_rows=4)
    a, fit_a = t.annual_stage_predictions([first, current], min_races=1)
    b, fit_b = t.annual_stage_predictions([first, altered], min_races=1)
    assert np.array_equal(a['b'], b['b']) and fit_a == fit_b


def test_deadheat_or_missing_positions_are_not_fabricated():
    r = record('a', 2020)
    r.er = EvalRace(r.er.context, (ScoringLabel('a', 1, 1, 1), ScoringLabel('b', 1, 1, 1)), 4)
    s = t.sample_from_record(r)
    assert s.i1 is None and s.i2 is None and s.i3 is None

"""T015 (023 US3): adoption-gate guards — strict majority + worst-fold LogLoss cap (FR-011, SC-004).

Uses a year-aware fake so the candidate wins only some folds. Even fold counts must NOT pass on a
mere half (strict majority), and a single fold whose LogLoss blows up must veto adoption even if the
mean improves.
"""

from __future__ import annotations

import datetime

import pytest

from horseracing_eval.feature_eval import evaluate_feature_adoption
from tests._fakepredictor import FakePredictor, YearSkillFakePredictor
from tests._synth import insert_race, make_informative_field

pytestmark = pytest.mark.integration


def _seed(session, years):
    for year in years:
        for r in range(1, 6):
            rid = f"{year}06{r:02d}{r:02d}01"
            insert_race(session, race_id=rid, race_date=datetime.date(year, 6, r),
                        horses=make_informative_field(8, winner=0))


def test_strict_majority_blocks_even_half(session):
    # 4 folds (2008-2011). Candidate strong in 2 folds, tie in the other 2 -> wins exactly 2/4.
    _seed(session, (2007, 2008, 2009, 2010, 2011))
    report = evaluate_feature_adoption(
        session,
        candidate=YearSkillFakePredictor({2008: 50.0, 2009: 50.0, 2010: 8.0, 2011: 8.0}),
        baseline=FakePredictor(skill=8.0),
    )
    assert report.n_folds == 4
    assert report.n_winning_folds == 2                 # exactly half
    assert report.primary_pass is True                 # mean still improves (2 big wins)
    assert report.adopted is False                     # ...but strict majority (2*2 > 4 is False)


def test_worst_fold_logloss_cap_vetoes(session):
    # Candidate wins 3/4 folds but one fold's LogLoss blows up beyond the worst-fold cap.
    _seed(session, (2007, 2008, 2009, 2010, 2011))
    report = evaluate_feature_adoption(
        session,
        candidate=YearSkillFakePredictor({2008: 50.0, 2009: 50.0, 2010: 50.0, 2011: 1.0}),
        baseline=FakePredictor(skill=8.0),
        worst_fold_dll_tol=5e-3,
    )
    # 2011 candidate is near-uniform (skill 1) vs baseline 8 -> that fold's LogLoss far worse
    assert report.worst_fold_dlogloss > 5e-3
    assert report.adopted is False

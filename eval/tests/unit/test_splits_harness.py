"""US1 (FR-001/FR-007/SC-006): expanding folds, leakage rejection, determinism."""

from __future__ import annotations

import datetime

from horseracing_eval.baselines import UniformBaseline
from horseracing_eval.dataset import EvalRace, ScoringLabel
from horseracing_eval.harness import evaluate
from horseracing_eval.predictor import HorseEntry, RaceContext
from horseracing_eval.splits import expanding_folds


def _race(year: int, race_seq: int, n: int = 6, winner: int = 0) -> EvalRace:
    rid = f"{year:04d}0101{race_seq:02d}{race_seq:02d}"[:12].ljust(12, "0")
    horses = tuple(HorseEntry(horse_id=f"{rid}-H{i}", horse_number=i + 1) for i in range(n))
    ctx = RaceContext(race_id=rid, race_date=datetime.date(year, 6, 1), started_horses=horses)
    labels = tuple(
        ScoringLabel(horse_id=h.horse_id, win=int(i == winner), top2=int(i < 2), top3=int(i < 3))
        for i, h in enumerate(horses)
    )
    return EvalRace(context=ctx, labels=labels)


def _dataset():
    return [_race(y, s) for y in (2007, 2008, 2009) for s in range(1, 4)]


def test_expanding_folds_no_leakage():
    folds = list(expanding_folds(_dataset()))
    assert [f.valid_year for f in folds] == [2008, 2009]
    for fold in folds:
        # leakage check: no train race is in (or after) the valid year
        assert all(tr.context.race_date.year < fold.valid_year for tr in fold.train)
        assert all(v.context.race_date.year == fold.valid_year for v in fold.valid)


def test_2007_is_train_only():
    folds = list(expanding_folds(_dataset()))
    assert 2007 not in [f.valid_year for f in folds]


def test_harness_deterministic():
    races = _dataset()
    r1 = evaluate(UniformBaseline(), races).to_summary()
    r2 = evaluate(UniformBaseline(), races).to_summary()
    assert r1 == r2

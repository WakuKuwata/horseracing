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


# --- 2026-08: day-exact scored window ---------------------------------------------------------

def _dated_race(rid: str, d: datetime.date, n: int = 4) -> EvalRace:
    horses = tuple(HorseEntry(horse_id=f"{rid}-H{i}", horse_number=i + 1) for i in range(n))
    ctx = RaceContext(race_id=rid, race_date=d, started_horses=horses)
    labels = tuple(
        ScoringLabel(horse_id=h.horse_id, win=int(i == 0), top2=int(i < 2), top3=int(i < 3))
        for i, h in enumerate(horses)
    )
    return EvalRace(context=ctx, labels=labels)


def test_valid_from_narrows_only_the_scored_side():
    """`--from` looked like a date but only moved the START YEAR, so a window beginning mid-year
    silently scored the whole year — a "prospective holdout from 2026-07-13" would have scored the
    January-to-July races the development window had already used."""
    races = [_dated_race(f"r{y}{m:02d}", datetime.date(y, m, 15))
             for y in (2025, 2026) for m in (1, 3, 8, 11)]

    full = {f.valid_year: f for f in expanding_folds(races, 2026)}
    assert len(full[2026].valid) == 4                      # 既定は年まるごと
    train_before = {er.context.race_id for er in full[2026].train}

    cut = datetime.date(2026, 7, 13)
    narrowed = {f.valid_year: f for f in expanding_folds(races, 2026, valid_from=cut)}
    assert len(narrowed[2026].valid) == 2                  # 8月・11月のみ
    assert all(er.context.race_date >= cut for er in narrowed[2026].valid)
    # 学習側は年単位のまま = 既存 fold の数値が動かない
    assert {er.context.race_id for er in narrowed[2026].train} == train_before


def test_valid_from_none_reproduces_the_previous_folds():
    races = [_dated_race(f"r{y}{m:02d}", datetime.date(y, m, 15))
             for y in (2025, 2026) for m in (1, 6, 12)]
    a = [(f.valid_year, [e.context.race_id for e in f.valid]) for f in expanding_folds(races, 2026)]
    b = [(f.valid_year, [e.context.race_id for e in f.valid])
         for f in expanding_folds(races, 2026, valid_from=None)]
    assert a == b


def test_valid_from_that_empties_a_year_skips_the_fold():
    races = [_dated_race("a", datetime.date(2025, 5, 1)), _dated_race("b", datetime.date(2026, 2, 1))]
    assert list(expanding_folds(races, 2026, valid_from=datetime.date(2026, 7, 1))) == []

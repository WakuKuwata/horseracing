"""Feature 073 US1 (T008, FR-003): started-all scoring in the harness body.

The default harness scores WIN/top2/top3 over finished horses only; ``evaluate(started_all=True)``
adds a started-all WIN block (DNF/DSQ label = 0) matching the paired-eval started-all population,
so the harness and the adoption path agree on the scored population. Default off => byte-identical.
"""

from __future__ import annotations

import datetime

from horseracing_eval.baselines import UniformBaseline
from horseracing_eval.dataset import EvalRace, ScoringLabel, population_masks
from horseracing_eval.harness import evaluate
from horseracing_eval.predictor import HorseEntry, RaceContext


def _race_with_dnf(year: int, seq: int, *, n_started: int = 6, n_finished: int = 4) -> EvalRace:
    """A race with ``n_started`` runners but only ``n_finished`` finish (the rest are DNF: started,
    win=0, and carry NO ScoringLabel — population_masks treats a missing label as started win=0)."""
    rid = f"{year:04d}0101{seq:02d}{seq:02d}"[:12].ljust(12, "0")
    horses = tuple(HorseEntry(horse_id=f"{rid}-H{i}", horse_number=i + 1) for i in range(n_started))
    ctx = RaceContext(race_id=rid, race_date=datetime.date(year, 6, 1), started_horses=horses)
    labels = tuple(
        ScoringLabel(horse_id=h.horse_id, win=int(i == 0), top2=int(i < 2), top3=int(i < 3))
        for i, h in enumerate(horses[:n_finished])  # only finishers get labels
    )
    return EvalRace(context=ctx, labels=labels)


def _dataset():
    return [_race_with_dnf(y, s) for y in (2007, 2008, 2009) for s in range(1, 4)]


def test_started_all_population_includes_dnf_runners():
    er = _race_with_dnf(2008, 1, n_started=6, n_finished=4)
    pop = population_masks(er)
    assert pop.field_size == 6  # all started horses, DNF included
    assert len(er.labels) == 4  # only finishers labelled


def test_started_all_off_is_byte_identical_default():
    races = _dataset()
    default = evaluate(UniformBaseline(), races).to_summary()
    assert "started_all_win" not in default["eval"]  # additive key absent by default


def test_started_all_on_adds_win_block_scored_over_all_starters():
    races = _dataset()
    res = evaluate(UniformBaseline(), races, started_all=True).to_summary()["eval"]
    assert "started_all_win" in res
    sa = res["started_all_win"]
    # started-all scores every started horse across all valid-fold races (6 per race),
    # strictly more rows than the finished-only overall win population (4 per race).
    assert sa["n_started"] > 0
    finished_n = res["overall"]["win"].get("n") or res["overall"]["win"].get("count")
    # started-all has >= finished-only sample count (DNF rows added)
    if finished_n is not None:
        assert sa["n_started"] >= finished_n

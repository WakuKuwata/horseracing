"""T008: started/finished/winner-eligible population golden (FR-001/FR-002, codex)."""

from __future__ import annotations

import datetime

from horseracing_eval.dataset import EvalRace, ScoringLabel, population_masks
from horseracing_eval.predictor import HorseEntry, RaceContext


def _race(started_ids, labels):
    return EvalRace(
        context=RaceContext(
            race_id="R1",
            race_date=datetime.date(2025, 1, 1),
            started_horses=tuple(HorseEntry(horse_id=h) for h in started_ids),
        ),
        labels=tuple(labels),
    )


def test_started_horse_without_label_is_zero_not_dropped():
    # 3 started; only h1 (winner) and h2 finished. h3 (DNF) must be started_win 0, still in pop.
    er = _race(
        ["h1", "h2", "h3"],
        [ScoringLabel("h1", 1, 1, 1), ScoringLabel("h2", 0, 1, 1)],
    )
    pop = population_masks(er)
    assert pop.field_size == 3
    assert pop.started_win == {"h1": 1, "h2": 0, "h3": 0}
    assert set(pop.started_horse_ids) == {"h1", "h2", "h3"}
    assert pop.eligible is True
    assert pop.winner_horse_id == "h1"
    assert pop.n_winners == 1


def test_dead_heat_two_winners_is_ineligible():
    er = _race(
        ["h1", "h2", "h3"],
        [ScoringLabel("h1", 1, 1, 1), ScoringLabel("h2", 1, 1, 1)],
    )
    pop = population_masks(er)
    assert pop.n_winners == 2
    assert pop.eligible is False
    assert pop.winner_horse_id is None


def test_no_winner_all_dnf_is_ineligible():
    er = _race(["h1", "h2"], [ScoringLabel("h1", 0, 0, 1)])
    pop = population_masks(er)
    assert pop.n_winners == 0
    assert pop.eligible is False


def test_cancelled_horse_not_in_started_population():
    # Cancelled horses are excluded upstream (not in started_horses), so never counted.
    er = _race(["h1", "h2"], [ScoringLabel("h1", 1, 1, 1), ScoringLabel("h2", 0, 1, 1)])
    pop = population_masks(er)
    assert "h3" not in pop.started_horse_ids
    assert pop.field_size == 2


def test_top2_top3_started_labels_zero_for_unplaced():
    er = _race(
        ["h1", "h2", "h3", "h4"],
        [ScoringLabel("h1", 1, 1, 1), ScoringLabel("h2", 0, 1, 1), ScoringLabel("h3", 0, 0, 1)],
    )
    pop = population_masks(er)
    assert pop.started_top3 == {"h1": 1, "h2": 1, "h3": 1, "h4": 0}
    assert pop.started_top2 == {"h1": 1, "h2": 1, "h3": 0, "h4": 0}

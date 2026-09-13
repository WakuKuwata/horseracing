"""Exercise production read queries against a disposable in-memory SQL database.

Only queried columns are needed; no Postgres server, project DB or model fitting.
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_eval.dataset import load_eval_races, population_masks
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from horseracing_training.calib_split import _started_all_outcomes


@pytest.fixture
def result_session():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE races (race_id TEXT PRIMARY KEY, race_date DATE NOT NULL)"
        ))
        connection.execute(text(
            "CREATE TABLE race_horses (race_id TEXT, horse_id TEXT, entry_status TEXT, "
            "frame INTEGER, horse_number INTEGER, odds NUMERIC, popularity INTEGER, "
            "PRIMARY KEY (race_id, horse_id))"
        ))
        connection.execute(text(
            "CREATE TABLE race_results (race_id TEXT, horse_id TEXT, result_status TEXT, "
            "finish_order INTEGER, PRIMARY KEY (race_id, horse_id))"
        ))
        for race_id, day in (("incomplete", "2007-01-01"), ("complete", "2007-01-02")):
            connection.execute(text(
                "INSERT INTO races VALUES (:race_id, :day)"
            ), {"race_id": race_id, "day": day})
            # Reuse the same horse IDs in both races: joining only on horse_id would
            # inflate counts and allow the other race's result to cover the omission.
            for number, horse in enumerate(("winner", "missing", "dnf", "dq", "cancelled"), 1):
                connection.execute(text(
                    "INSERT INTO race_horses "
                    "(race_id, horse_id, entry_status, horse_number) "
                    "VALUES (:race_id, :horse, :status, :number)"
                ), {"race_id": race_id, "horse": horse, "number": number,
                    "status": "cancelled" if horse == "cancelled" else "started"})
            results = [("winner", "finished", 1), ("dnf", "stopped", None),
                       ("dq", "disqualified", None), ("cancelled", "finished", 1)]
            if race_id == "complete":
                results.append(("missing", "finished", 2))
            for horse, status, finish in results:
                connection.execute(text(
                    "INSERT INTO race_results VALUES (:race_id, :horse, :status, :finish)"
                ), {"race_id": race_id, "horse": horse, "status": status, "finish": finish})
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_eval_counts_only_results_for_actual_started_ids(result_session):
    races = {r.context.race_id: r for r in load_eval_races(result_session)}
    incomplete = races["incomplete"]
    assert len(incomplete.context.started_horses) == 4
    assert incomplete.n_result_rows == 3  # stale cancelled result cannot supply missing horse
    assert not population_masks(incomplete).complete_results
    assert not population_masks(incomplete).eligible
    complete = races["complete"]
    assert complete.n_result_rows == 4  # both DNF and DQ are legitimate result coverage
    assert population_masks(complete).complete_results
    assert population_masks(complete).winner_horse_id == "winner"


def test_eval_result_intersection_keeps_date_window(result_session):
    races = load_eval_races(result_session, end_date=datetime.date(2007, 1, 1))
    assert [r.context.race_id for r in races] == ["incomplete"]
    assert races[0].n_result_rows == 3


def test_oof_outcomes_use_the_same_started_id_intersection(result_session):
    assert _started_all_outcomes(result_session, ["incomplete", "complete"]) == {
        "incomplete": (3, {"winner"}),
        "complete": (4, {"winner"}),
    }
    assert _started_all_outcomes(result_session, ["complete"]) == {
        "complete": (4, {"winner"}),
    }

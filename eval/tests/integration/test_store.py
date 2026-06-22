"""US2 (SC-005): baseline results persist to model_versions.metrics_summary."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import ModelVersion

from horseracing_eval.baselines import UniformBaseline
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.harness import evaluate
from horseracing_eval.store import save_baseline
from tests._synth import insert_race, make_informative_field

pytestmark = pytest.mark.integration


def test_save_and_reload(session):
    for year in (2007, 2008):
        insert_race(
            session,
            race_id=f"{year}06010101",  # 12 digits, race_number=01
            race_date=datetime.date(year, 6, 1),
            horses=make_informative_field(6, winner=0),
        )
    races = load_eval_races(session, start_date=datetime.date(2007, 1, 1))
    result = evaluate(UniformBaseline(), races)

    save_baseline(session, "baseline-uniform-v1", result)

    mv = session.get(ModelVersion, "baseline-uniform-v1")
    assert mv is not None
    assert mv.model_family == "baseline"
    assert mv.metrics_summary["eval"]["scheme"] == "expanding_yearly"
    assert mv.metrics_summary["eval"]["valid_years"] == [2008]

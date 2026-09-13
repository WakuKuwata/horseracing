"""Backfill must replace stale fields without force, then become idempotent again."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import (
    Horse,
    ModelVersion,
    PredictionRun,
    Race,
    RaceHorse,
    RacePrediction,
)
from sqlalchemy import select

from horseracing_serving import pipeline
from tests._synth import make_active_model, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200806010101"
_DAY = datetime.date(2008, 6, 1)
_MODEL = "field-test"


@pytest.fixture
def field(session):
    session.add(ModelVersion(model_version=_MODEL))
    session.add(Race(race_id=_RACE, race_number=1, race_date=_DAY, venue_code="05",
                     distance=1600, track_type="芝", going="良", race_class="未勝利"))
    for n in range(1, 6):
        session.add(Horse(horse_id=f"H{n}"))
    session.flush()
    for n in range(1, 6):
        session.add(RaceHorse(
            race_id=_RACE, horse_id=f"H{n}", horse_number=n, age=3,
            entry_status=EntryStatus.STARTED if n <= 4 else EntryStatus.CANCELLED,
        ))
    session.commit()


def _saved_run(session, horse_ids, *, logic_version="feat=test;rcr=raw"):
    run = PredictionRun(
        race_id=_RACE, model_version=_MODEL, logic_version=logic_version,
        computed_at=datetime.datetime.now(datetime.UTC),
    )
    session.add(run)
    session.flush()
    for horse_id in horse_ids:
        n = len(horse_ids)
        session.add(RacePrediction(
            prediction_run_id=run.prediction_run_id, horse_id=horse_id,
            win_prob=1 / n, top2_prob=min(2, n) / n, top3_prob=min(3, n) / n,
        ))
    session.commit()
    return run.prediction_run_id


@pytest.mark.parametrize("status", [EntryStatus.CANCELLED, EntryStatus.EXCLUDED])
def test_force_false_recovers_changed_field_and_becomes_idempotent(
    session, tmp_path, status,
):
    # Use the same small, history-bearing fixture as test_predict_backfill, and exercise the
    # real feature builder, model inference, assembly and append-only prediction persistence.
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    model_version = make_active_model(session, tmp_path)
    race_id, day = "200801010101", datetime.date(2008, 1, 2)
    cancelled_id = "2008-01-H1"
    old_run = pipeline.run_serving(
        session, race_id=race_id, model_version=model_version, apply_stage_discount=False,
    )[0].prediction_run_id

    def backfill():
        return pipeline.run_serving_backfill(
            session, date_from=day, date_to=day, model_version=model_version,
            force=False, apply_stage_discount=False,
        )

    unchanged = backfill()
    assert (unchanged.generated, unchanged.skip_exists, unchanged.error_days) == (0, 1, 0), unchanged.errors
    session.get(RaceHorse, (race_id, cancelled_id)).entry_status = status
    session.commit()
    recovered = backfill()
    assert (recovered.generated, recovered.skip_exists, recovered.error_days) == (1, 0, 0), recovered.errors
    latest = session.scalars(select(PredictionRun).where(
        PredictionRun.race_id == race_id,
    ).order_by(PredictionRun.computed_at.desc(), PredictionRun.prediction_run_id.desc())).first()
    assert latest.prediction_run_id != old_run
    predictions = session.scalars(select(RacePrediction).where(
        RacePrediction.prediction_run_id == latest.prediction_run_id,
    )).all()
    assert {p.horse_id for p in predictions} == {f"2008-01-H{n}" for n in range(2, 9)}
    for head, total in (("win_prob", 1), ("top2_prob", 2), ("top3_prob", 3)):
        assert sum(float(getattr(p, head)) for p in predictions) == pytest.approx(total)
    assert len(session.scalars(select(RacePrediction).where(
        RacePrediction.prediction_run_id == old_run,
    )).all()) == 8  # the historical run stays append-only
    repeated = backfill()
    assert (repeated.generated, repeated.skip_exists, repeated.error_days) == (0, 1, 0), repeated.errors

    # Restoring the entry makes the old eight-horse run match again, but the newer seven-horse run
    # is now stale. Do not let that older match suppress the fresh prediction owed by the API.
    session.get(RaceHorse, (race_id, cancelled_id)).entry_status = EntryStatus.STARTED
    session.commit()
    restored = backfill()
    assert (restored.generated, restored.skip_exists, restored.error_days) == (1, 0, 0), restored.errors


@pytest.mark.parametrize("case", ["missing_prediction", "empty_predictions", "replacement", "empty_field"])
def test_invalid_population_never_satisfies_existing_run(session, field, case):
    ids = ["H1", "H2", "H3", "H4"]
    if case == "missing_prediction":
        ids.pop()
    elif case == "empty_predictions":
        ids = []
    _saved_run(session, ids)
    if case == "replacement":
        session.get(RaceHorse, (_RACE, "H4")).entry_status = EntryStatus.CANCELLED
        session.get(RaceHorse, (_RACE, "H5")).entry_status = EntryStatus.STARTED
    elif case == "empty_field":
        for horse_id in ids:
            session.get(RaceHorse, (_RACE, horse_id)).entry_status = EntryStatus.CANCELLED
    session.commit()
    assert not pipeline._has_run_for_model(
        session, _RACE, _MODEL, race_class_representation="raw",
    )


@pytest.mark.parametrize("calib_digest", [None, "abc123"])
@pytest.mark.parametrize("wregime", [None, "serving", "full_info"])
def test_latest_matching_regime_and_calibration_must_have_complete_field(
    session, field, calib_digest, wregime,
):
    logic_version = "feat=test;rcr=raw"
    if calib_digest is not None:
        logic_version += f";calib={calib_digest};calibmode=manifest"
    if wregime is not None:
        logic_version += f";wregime={wregime}"
    kwargs = dict(calib_digest=calib_digest, wregime=wregime, race_class_representation="raw")
    _saved_run(session, ["H1", "H2", "H3", "H4"], logic_version=logic_version)
    assert pipeline._has_run_for_model(session, _RACE, _MODEL, **kwargs)
    _saved_run(session, ["H1", "H2", "H3"], logic_version=logic_version)
    assert not pipeline._has_run_for_model(session, _RACE, _MODEL, **kwargs)


def test_stale_latest_other_regime_does_not_allow_older_matching_run_to_skip(session, field):
    serving_lv = "feat=test;rcr=raw;calib=abc123;calibmode=manifest;wregime=serving"
    full_info_lv = "feat=test;rcr=raw;wregime=full_info"
    requested = dict(calib_digest="abc123", wregime="serving", race_class_representation="raw")
    _saved_run(session, ["H1", "H2", "H3", "H4"], logic_version=serving_lv)
    assert pipeline._has_run_for_model(session, _RACE, _MODEL, **requested)
    # The API selects this newer run, so the valid older serving run cannot justify skipping.
    _saved_run(session, ["H1", "H2", "H3"], logic_version=full_info_lv)
    assert not pipeline._has_run_for_model(session, _RACE, _MODEL, **requested)
    # Once the latest overall run is complete, existing per-regime idempotency is preserved.
    _saved_run(session, ["H1", "H2", "H3", "H4"], logic_version=full_info_lv)
    assert pipeline._has_run_for_model(session, _RACE, _MODEL, **requested)

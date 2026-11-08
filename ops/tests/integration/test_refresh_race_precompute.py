"""Changed pre-race inputs update forecasts once, preserving manual and capture semantics."""
from __future__ import annotations

import datetime
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest
from horseracing_db.models import Horse, IngestionJob, ModelVersion, PredictionRun, Race, RaceHorse
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from horseracing_ops import enqueue as enqueue_mod
from horseracing_ops import runner
from horseracing_ops.enqueue import enqueue_predict, enqueue_race
from horseracing_ops.forecast_refresh import enqueue_forecast_refresh, forecast_inputs
from horseracing_ops.worker import claim_one
from tests._synth import mark_finished, seed_race

pytestmark = pytest.mark.integration
RID = "202406050911"
NOW = datetime.datetime(2030, 1, 4, 1, tzinfo=datetime.UTC)


def _step(kind, status="succeeded", written=1):
    return SimpleNamespace(job_type=kind, status=status, written=written, processed=written,
                           skipped=0, errors=0, extra={})


@pytest.fixture
def future_race(session, monkeypatch):
    seed_race(session, race_id=RID, race_date=NOW.date())
    race = session.get(Race, RID)
    race.post_time = NOW + datetime.timedelta(hours=2)
    session.add_all([Horse(horse_id="h1"), Horse(horse_id="h2")])
    session.flush()
    session.add_all([
        RaceHorse(race_id=RID, horse_id="h1", horse_number=1, weight=480,
                  entry_status="started", odds=3),
        RaceHorse(race_id=RID, horse_id="h2", horse_number=2, weight=490,
                  entry_status="started", odds=5),
        ModelVersion(model_version="mix-129-nj6", model_family="lightgbm_mixture",
                     feature_version="features-021", adoption_status="active",
                     metrics_summary={"registration": {"bundle_sha256":
                         "28f39715a9c1dba13755ee44f3a439099d5aaffd94685ac11e4d23166b388471"}}),
    ])
    session.commit()
    monkeypatch.setattr(runner, "_now", lambda: NOW)
    monkeypatch.setattr(enqueue_mod, "_now", lambda: NOW)
    config = replace(runner.CONFIG, predict_on_refresh=True, expected_return_on_refresh=False,
                     exotic_quote_bet_types=(), jra_odds_origins=())
    monkeypatch.setattr(runner, "CONFIG", config)
    monkeypatch.setattr(enqueue_mod, "CONFIG", config)
    monkeypatch.setattr(runner, "scrape_entries", lambda *a, **kw: _step("entries"))
    monkeypatch.setattr(runner, "scrape_results", lambda *a, **kw: _step("results", written=0))
    monkeypatch.setattr(runner, "scrape_odds", lambda *a, **kw: _step("odds"))
    monkeypatch.setattr(runner, "complete_profiles", lambda *a, **kw: None)
    return race


def _refresh(session, origin="manual_ui"):
    job, _ = enqueue_race(session, RID, origin=origin, force=True)
    session.commit()
    return runner.run_one(session, job, fetcher=object(), jra_fetcher=None)


def _jobs(session):
    return list(session.scalars(select(IngestionJob).where(IngestionJob.job_type == "predict")))


def _enqueue(session, origin="auto_after_refresh"):
    job_id = enqueue_forecast_refresh(session, RID, origin=origin, now=NOW)
    session.commit()
    return session.get(IngestionJob, job_id) if job_id else None


def _succeed(session, job):
    run = PredictionRun(race_id=RID, model_version="mix-129-nj6", logic_version="test")
    session.add(run)
    session.flush()
    job.status, job.completed_at = "succeeded", NOW
    job.summary = {**job.summary, "forecast_refresh": {
        **job.summary["forecast_refresh"], "inputs_unchanged": True,
        "prediction_run_id": str(run.prediction_run_id),
    }}
    session.commit()


def test_refresh_tracks_prediction_job_independently_of_roi(session, future_race, client, monkeypatch):
    assert runner.CONFIG.predict_on_refresh is True
    monkeypatch.setattr(runner, "CONFIG", replace(runner.CONFIG, expected_return_on_refresh=True))
    refresh = _refresh(session)
    predict = _jobs(session)[0]
    assert predict.summary["predict_origin"] == "manual_ui"
    assert predict.summary["forecast_refresh"]["market_inputs"] is False
    body = client.get(f"/ops/v1/jobs/{refresh.ingestion_job_id}").json()
    assert body["prediction_job_id"] == str(predict.ingestion_job_id)
    assert body["followup_job_id"] == refresh.summary["expected_return_job_id"]


def test_existing_untracked_run_is_updated_once_and_fresh_reuse_coalesces(session, future_race):
    session.add(PredictionRun(race_id=RID, model_version="mix-129-nj6", logic_version="old"))
    session.commit()
    done = _refresh(session, "daily_bulk")
    again, reused = enqueue_race(session, RID, origin="manual_ui")
    session.commit()
    assert reused and again.ingestion_job_id == done.ingestion_job_id
    predict = _jobs(session)[0]
    assert again.summary["predict_job_id"] == str(predict.ingestion_job_id)
    assert len(_jobs(session)) == 1
    _succeed(session, predict)
    assert _enqueue(session) is None
    assert "predict_job_id" not in _refresh(session).summary
    assert len(_jobs(session)) == 1


def test_weight_field_and_profile_changes_coalesce_without_mutating_running_receipt(session, future_race):
    running = _enqueue(session)
    claim_one(session, job_types=("predict",))
    original = dict(running.summary["forecast_refresh"])
    session.get(RaceHorse, (RID, "h1")).weight = 510
    session.commit()
    newer = _enqueue(session)
    assert newer.ingestion_job_id != running.ingestion_job_id
    before = newer.summary["forecast_refresh"]["signature"]
    session.get(RaceHorse, (RID, "h2")).entry_status = "cancelled"
    session.get(Horse, "h1").sire_name = "new pedigree"
    session.commit()
    coalesced = _enqueue(session)
    assert coalesced.ingestion_job_id == newer.ingestion_job_id
    assert coalesced.summary["forecast_refresh"]["signature"] != before
    assert running.summary["forecast_refresh"] == original
    assert claim_one(session, job_types=("predict",)) is None
    running.status = "succeeded"
    session.commit()
    assert claim_one(session, job_types=("predict",)).ingestion_job_id == newer.ingestion_job_id


def test_only_proven_market_free_model_ignores_quote_changes(session, future_race):
    old, _ = forecast_inputs(session, RID, now=NOW)
    entry = session.get(RaceHorse, (RID, "h1"))
    entry.odds, entry.popularity = 4, 1
    session.commit()
    assert forecast_inputs(session, RID, now=NOW)[0]["signature"] == old["signature"]
    session.get(ModelVersion, "mix-129-nj6").metrics_summary = {}
    session.commit()
    old, _ = forecast_inputs(session, RID, now=NOW)
    entry.odds = 8
    session.commit()
    new, _ = forecast_inputs(session, RID, now=NOW)
    assert new["market_inputs"] and new["signature"] != old["signature"]


@pytest.mark.parametrize("change,reason", [
    ("past", "race_in_past"), ("started", "race_started"),
    ("no_time", "post_time_unavailable"), ("results", "race_has_results"),
    ("no_starters", "no_started_horses"), ("no_model", "no_active_model"),
])
def test_automatic_guards_apply_to_refresh_and_fresh_reuse(session, future_race, change, reason):
    if change == "past":
        future_race.race_date = NOW.date() - datetime.timedelta(days=1)
    elif change == "started":
        future_race.post_time = NOW
    elif change == "no_time":
        future_race.post_time = None
    elif change == "results":
        mark_finished(session, race_id=RID)
    elif change == "no_starters":
        for entry in session.scalars(select(RaceHorse)):
            entry.entry_status = "cancelled"
    else:
        session.get(ModelVersion, "mix-129-nj6").adoption_status = "candidate"
    session.commit()
    assert forecast_inputs(session, RID, now=NOW) == (None, reason)
    assert "predict_job_id" not in _refresh(session).summary
    enqueue_race(session, RID, origin="manual_ui")
    session.commit()
    assert _jobs(session) == []


def test_future_day_without_known_time_can_be_predicted(session, future_race):
    future_race.race_date = NOW.date() + datetime.timedelta(days=1)
    future_race.post_time = None
    session.commit()
    assert _enqueue(session) is not None


def test_disabled_failed_entries_and_corner_backfill_do_not_enqueue(session, future_race, monkeypatch):
    monkeypatch.setattr(runner, "CONFIG", replace(runner.CONFIG, predict_on_refresh=False))
    assert "predict_job_id" not in _refresh(session).summary
    monkeypatch.setattr(runner, "CONFIG", replace(runner.CONFIG, predict_on_refresh=True))
    monkeypatch.setattr(runner, "scrape_entries", lambda *a, **kw: _step("entries", "failed", 0))
    assert "predict_job_id" not in _refresh(session).summary
    assert "predict_job_id" not in _refresh(session, "corner_backfill").summary
    assert _jobs(session) == []


def test_concurrent_claims_never_run_same_race_in_parallel(session, engine, future_race):
    first = _enqueue(session)
    first.status = "running"
    session.commit()
    second = _enqueue(session)
    first.status = "queued"
    session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    def claim(_):
        with factory() as other:
            job = claim_one(other, job_types=("predict",))
            return job.ingestion_job_id if job else None
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, range(2)))
    assert len([item for item in claimed if item]) == 1
    assert set(claimed) <= {None, first.ingestion_job_id, second.ingestion_job_id}


def test_manual_job_semantics_survive_automatic_reuse_and_explicit_promotion(session, future_race):
    manual, _ = enqueue_predict(session, RID, origin="manual_ui")
    session.commit()
    assert _enqueue(session).ingestion_job_id == manual.ingestion_job_id
    assert "forecast_refresh" not in manual.summary
    manual.status = "succeeded"
    session.commit()
    automatic = _enqueue(session)
    manual, reused = enqueue_predict(session, RID, origin="manual_ui")
    session.commit()
    assert reused and manual.ingestion_job_id == automatic.ingestion_job_id
    assert "forecast_refresh" not in manual.summary


@pytest.mark.parametrize("during_capture", [False, True])
def test_forecast_expiring_in_queue_or_capture_skips_before_serving(
    session, future_race, monkeypatch, during_capture,
):
    job = _enqueue(session)
    def expire(*args, **kwargs):
        monkeypatch.setattr(runner, "_now", lambda: future_race.post_time)
        return subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
    monkeypatch.setattr(runner, "_live_capture_chaos", expire)
    monkeypatch.setattr(runner, "_serving_predict", lambda rid: pytest.fail("must not predict"))
    if not during_capture:
        expire()
    runner.run_predict(session, job)
    assert job.status == "skipped" and job.summary["reason"] == "race_started"


@pytest.mark.parametrize("change_inputs", [False, True])
def test_runner_preserves_receipt_and_only_credits_unchanged_inputs(
    session, future_race, monkeypatch, change_inputs,
):
    job = _enqueue(session)
    initial = dict(job.summary["forecast_refresh"])
    monkeypatch.setenv("OPS_CAPTURE_ON_AUTO_PREDICT", "0")
    def predict(rid):
        if change_inputs:
            session.get(RaceHorse, (RID, "h1")).weight = 520
        session.add(PredictionRun(race_id=RID, model_version="mix-129-nj6", logic_version="new"))
        session.commit()
        return subprocess.CompletedProcess([], 0, stdout="total races persisted: 1", stderr="")
    monkeypatch.setattr(runner, "_serving_predict", predict)
    runner.run_predict(session, job)
    assert job.status == "succeeded"
    assert job.summary["forecast_refresh"]["signature"] == initial["signature"]
    assert job.summary["forecast_refresh"]["inputs_unchanged"] is not change_inputs
    assert (_enqueue(session) is not None) is change_inputs


def test_return_to_older_signature_does_not_reuse_the_newer_prediction_run(session, future_race):
    _succeed(session, _enqueue(session))
    entry = session.get(RaceHorse, (RID, "h1"))
    entry.weight = 500
    session.commit()
    _succeed(session, _enqueue(session))
    entry.weight = 480
    session.commit()
    assert _enqueue(session) is not None

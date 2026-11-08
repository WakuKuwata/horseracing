"""Bound refresh-triggered forecasts to changed pre-race inputs, without importing ML code.

This signature covers the target field/conditions and horse identity data, not the mutable full
historical feature pool. It is a refresh dedup receipt, never proof of a frozen ML snapshot.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from decimal import Decimal

from horseracing_db.enums import EntryStatus, JobStatus
from horseracing_db.models import (
    Horse,
    IngestionJob,
    ModelVersion,
    PredictionRun,
    Race,
    RaceHorse,
    RaceResult,
)
from sqlalchemy import select, text
from sqlalchemy.orm import Session

JST = datetime.timezone(datetime.timedelta(hours=9))
_SIGNATURE_VERSION = 1
# Frozen 131 serving recipe: market_offset=None; feature_profile is the non-market 129 mixture.
# An unknown/replaced model retains market inputs in its signature, even with the same family.
_MARKET_FREE_BUNDLE = "28f39715a9c1dba13755ee44f3a439099d5aaffd94685ac11e4d23166b388471"
_QUOTE_FIELDS = {"odds", "popularity", "place_odds_low", "place_odds_high", "place_popularity"}
_RACE_FIELDS = (
    "race_id", "race_date", "venue_code", "distance", "track_type", "race_class", "grade",
    "going", "weather", "race_number", "prize_money", "post_time", "race_status",
)


def _raw(value):
    if isinstance(value, datetime.datetime):
        return value.astimezone(datetime.UTC).isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return value


def _market_free(model: ModelVersion) -> bool:
    registration = (model.metrics_summary or {}).get("registration") or {}
    return (
        model.model_version == "mix-129-nj6"
        and model.model_family == "lightgbm_mixture"
        and model.feature_version == "features-021"
        and registration.get("bundle_sha256") == _MARKET_FREE_BUNDLE
    )


def forecast_inputs(
    session: Session, race_id: str, *, now: datetime.datetime,
) -> tuple[dict | None, str | None]:
    """Return a durable input receipt, or a fail-closed reason for automatic forecasting."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("forecast clock must be timezone aware")
    race = session.get(Race, race_id, populate_existing=True)
    if race is None or race.race_date is None:
        return None, "race_date_unavailable"
    today = now.astimezone(JST).date()
    if race.race_date < today:
        return None, "race_in_past"
    if race.post_time is not None:
        if race.post_time.tzinfo is None or race.post_time.utcoffset() is None:
            return None, "post_time_unavailable"
        if race.post_time <= now:
            return None, "race_started"
    elif race.race_date <= today:
        return None, "post_time_unavailable"
    if session.scalar(select(RaceResult.race_id).where(RaceResult.race_id == race_id).limit(1)):
        return None, "race_has_results"
    entries = list(session.scalars(
        select(RaceHorse).where(RaceHorse.race_id == race_id).order_by(RaceHorse.horse_id)
        .execution_options(populate_existing=True)
    ))
    if not any(entry.entry_status == EntryStatus.STARTED for entry in entries):
        return None, "no_started_horses"
    model = session.scalars(
        select(ModelVersion).where(ModelVersion.adoption_status == "active")
        .execution_options(populate_existing=True)
    ).first()
    if model is None:
        return None, "no_active_model"
    market_inputs = not _market_free(model)
    entry_fields = [c.name for c in RaceHorse.__table__.columns
                    if c.name not in {"created_at", "updated_at"}
                    and (market_inputs or c.name not in _QUOTE_FIELDS)]
    horse_fields = [c.name for c in Horse.__table__.columns
                    if c.name not in {"created_at", "updated_at"}]
    horses = session.scalars(
        select(Horse).where(Horse.horse_id.in_([e.horse_id for e in entries]))
        .order_by(Horse.horse_id).execution_options(populate_existing=True)
    )
    model_fields = ("model_version", "model_family", "feature_version", "label_schema",
                    "weights_uri", "calibrator_uri")
    payload = {
        "version": _SIGNATURE_VERSION,
        "model": {key: getattr(model, key) for key in model_fields},
        "registration": (model.metrics_summary or {}).get("registration"),
        "race": {key: _raw(getattr(race, key)) for key in _RACE_FIELDS},
        "entries": [{key: _raw(getattr(e, key)) for key in entry_fields} for e in entries],
        "horses": [{key: _raw(getattr(h, key)) for key in horse_fields} for h in horses],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "signature_version": _SIGNATURE_VERSION,
        "signature": hashlib.sha256(encoded.encode()).hexdigest(),
        "model_version": model.model_version,
        "market_inputs": market_inputs,
        "requested_at": now.isoformat(),
        "guard_before_serving": True,
    }, None


def enqueue_forecast_refresh(
    session: Session, race_id: str, *, origin: str, now: datetime.datetime,
) -> str | None:
    """Coalesce QUEUED work, but leave a successor when older inputs are already RUNNING."""
    from .enqueue import enqueue_predict

    receipt, _ = forecast_inputs(session, race_id, now=now)
    if receipt is None:
        return None
    # Share enqueue_predict's lock so concurrent refreshes cannot race the receipt check.
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                    {"k": f"predict:{race_id}"})
    latest_run = session.scalar(
        select(PredictionRun.prediction_run_id)
        .where(PredictionRun.race_id == race_id,
               PredictionRun.model_version == receipt["model_version"])
        .order_by(PredictionRun.computed_at.desc(), PredictionRun.prediction_run_id.desc()).limit(1)
    )
    successful = session.scalar(
        select(IngestionJob.ingestion_job_id)
        .where(IngestionJob.job_type == "predict", IngestionJob.scope_value == race_id)
        .where(IngestionJob.status == JobStatus.SUCCEEDED)
        .where(IngestionJob.summary["forecast_refresh"]["signature"].astext == receipt["signature"])
        .where(IngestionJob.summary["forecast_refresh"]["inputs_unchanged"].astext == "true")
        .where(IngestionJob.summary["forecast_refresh"]["prediction_run_id"].astext
               == str(latest_run))
        .limit(1)
    )
    if successful is not None and latest_run is not None:
        return None
    job, reused = enqueue_predict(
        session, race_id, origin=origin, reuse_running=False, refresh_trigger=True,
    )
    summary = dict(job.summary or {})
    # A queued explicit predict retains its manual semantics. A refresh-created queued job can
    # absorb newer refresh inputs before it is claimed; a RUNNING receipt is never modified.
    if not reused or "forecast_refresh" in summary:
        job.summary = {**summary, "forecast_refresh": receipt}
        session.flush()
    return str(job.ingestion_job_id)

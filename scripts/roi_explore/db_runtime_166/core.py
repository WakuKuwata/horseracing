"""Read-only runtime replay rules. Historical data never becomes a live observation."""

from __future__ import annotations

import datetime as dt
import re

import pandas as pd

from scripts.roi_explore.manual_observation_165.features import models, c
from horseracing_features.loader import Frames

MODE = "readonly_db_runtime_replay"
ORIGIN = "saved_db_current_replay"
TARGET = "202605040907"
GUARD_TARGETS = ("202605040907", "202604040304", "202609040611")
SNAPSHOT_ID = "eed8ee10-ece7-44fb-9c83-9590a935bf42"


def encode(value):
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [encode(v) for v in value]
    return models._encode(value)


def saved_quote(snapshot, active_field, mappings):
    c.require(
        snapshot["race_id"] == TARGET and snapshot["chaos_snapshot_id"] == SNAPSHOT_ID,
        "wrong_saved_quote",
    )
    original = snapshot["field"]
    c.require(
        isinstance(original, list) and len(original) == snapshot["n"],
        "saved_quote_count",
    )
    c.require(
        c.digest(original) == snapshot["content_digest"], "saved_quote_digest_changed"
    )
    expected = c.field(active_field)
    candidates = {}
    for row in mappings:
        if row["mapping_status"] == "mapped" and row["canonical_id"]:
            candidates.setdefault(row["source_id"], set()).add(row["canonical_id"])
    rows, aliases = [], []
    for row in original:
        c.keys(
            row, ["horse_id", "horse_number", "odds", "popularity"], "saved_quote_row"
        )
        number, identifier = row["horse_number"], row["horse_id"]
        if (identifier, number) not in expected:
            targets = candidates.get(identifier, set())
            c.require(
                len(targets) == 1, "saved_quote_current_mapping_missing_or_ambiguous"
            )
            target = next(iter(targets))
            c.require(
                (target, number) in expected, "saved_quote_current_mapping_number"
            )
            aliases.append(dict(source_id=identifier, canonical_id=target))
            identifier = target
        c.number(row["odds"], 1)
        c.require(
            type(row["popularity"]) is int and row["popularity"] > 0,
            "saved_quote_popularity",
        )
        rows.append(dict(row, horse_id=identifier))
    c.require(c.field(rows) == expected, "saved_quote_current_field_changed")
    # received_at is a labelled legacy snapshot capture stamp for builder compatibility only.
    # The missing fetch interval/raw HTTP bytes are retained as missing, never manufactured.
    return dict(
        mode=MODE,
        origin=ORIGIN,
        rows=rows,
        race_id=TARGET,
        received_at=snapshot["captured_at"],
        original_captured_at=snapshot["captured_at"],
        original_fetch_started_at=None,
        provider_at=None,
        timestamp_semantics="saved chaos snapshot captured_at; fetch interval not saved",
        raw_sha256=c.digest(snapshot),
        current_replay_identity_map=aliases,
        evidence_admissible=False,
        full_raw_http_saved=False,
        eligible_for_prospective=False,
    )


def mask_same_day(frames, raw, *, day):
    """Mask all target-day outcomes in copies; keep DB contents and input objects untouched."""
    cutoff = pd.Timestamp(day)
    days = pd.to_datetime(frames.races.race_date)
    c.require(
        days.between(pd.Timestamp("2007-01-01"), cutoff).all(), "future_frame_race"
    )
    same_day_ids = set(frames.races.loc[days.eq(cutoff), "race_id"])
    dropped = frames.race_results.race_id.isin(same_day_ids)
    masked_frames = Frames(
        races=frames.races,
        race_horses=frames.race_horses,
        race_results=frames.race_results.loc[~dropped].copy(),
        horses=frames.horses,
    )
    raw_days = pd.to_datetime(raw.race_date)
    c.require(
        raw_days.between(pd.Timestamp("2007-01-01"), cutoff).all(), "future_mev_race"
    )
    same_day = raw_days.eq(cutoff)
    masked_raw = raw.copy()
    columns = ["finish_order", "result_status", "last_3f", "margin_sec"]
    before = encode(
        raw.loc[same_day, ["race_id", "horse_id"] + columns].to_dict("records")
    )
    for name in columns:
        masked_raw.loc[same_day, name] = None
    return (
        masked_frames,
        masked_raw,
        dict(
            origin=ORIGIN,
            database_changed=False,
            source_objects_changed=False,
            masked_day=str(day),
            same_day_races=len(same_day_ids),
            removed_legacy_result_rows=int(dropped.sum()),
            masked_mev_rows=int(same_day.sum()),
            masked_mev_columns=columns,
            original_same_day_mev_outcomes=before,
            primary_eligible=False,
            historical_asof_reconstruction=False,
        ),
    )


def current_admission(quote, *, post_time, at, ready_times=()):
    """Report actual failures without calling old saved prices newly fetched prices."""
    now, post, captured = (
        c.time(at),
        c.time(post_time),
        c.time(quote["original_captured_at"]),
    )
    age = (now - captured).total_seconds()
    reasons = [
        "current_replay_not_prospective",
        "historical_asof_features_not_proven",
        "raw_http_and_fetch_interval_not_saved",
    ]
    if not 0 <= age <= 1800:
        reasons.append("quote_not_within_30_minutes")
    if now >= post:
        reasons.append("race_already_started")
    if len(ready_times) != 2 or any(
        c.time(stamp) > post - dt.timedelta(seconds=540) for stamp in ready_times
    ):
        reasons.append("both_predictions_not_saved_before_historical_decision")
    return dict(
        evidence_admissible=False,
        can_adopt_model=False,
        comparison_data_added=0,
        at=at,
        post_time=post_time,
        original_quote_captured_at=quote["original_captured_at"],
        quote_age_seconds=age,
        reasons=reasons,
    )


def safe_error(error):
    reason = str(error)
    return dict(
        error_class=type(error).__name__,
        reason=reason
        if re.fullmatch(r"[A-Za-z0-9_:.-]{1,160}", reason)
        else "runtime_operation_failed",
    )

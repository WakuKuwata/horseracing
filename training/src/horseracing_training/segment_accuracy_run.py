"""Feature 082: segment accuracy readout — orchestration (training side).

Resolves the DB active (exactly one, fail-closed), builds/verifies the 074-style OOF bundle
via the GENERAL attestation path, joins result-blind attributes, runs the pure
``eval.segment_accuracy`` core, and persists the payload to ``diagnostic_runs``
(kind='segment_accuracy', append-only, 054 discipline: verbatim transcription only).

Estimand: active-recipe historical OOF accuracy. SECONDARY — never referenced by the 073
gate. Rejected inputs (FR-003): the 081 probe parquet and historical ``race_predictions``
(indistinguishable from full-history/backfill) — only a verified bundle or a fresh
regeneration may drive an official run.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from horseracing_db.models import ModelVersion
from horseracing_eval.dataset import load_eval_races, population_masks
from horseracing_eval.hashing import race_set_hash, stable_hash
from horseracing_eval.segment_accuracy import (
    MASK_LIBRARY_VERSION,
    METRIC_CONTRACT_VERSION,
    RaceInput,
    build_payload,
    mask_library_hash,
)
from horseracing_probability import oof_bundle
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .legacy_attest import attestation_from_model_dir, general_factory_from_attestation
from .oof_generate import code_sha, generate_oof_bundle

_ATTRS_SQL = Path(__file__).with_name("segment_attrs.sql").read_text()


class SegmentAccuracyError(RuntimeError):
    """Typed fail-closed error for the accuracy readout."""


def resolve_active(session: Session) -> tuple[str, Path]:
    """Resolve the SINGLE active model and its artifact directory (fail-closed on 0 or >1)."""
    rows = session.execute(
        select(ModelVersion.model_version, ModelVersion.weights_uri)
        .where(ModelVersion.adoption_status == "active")
    ).all()
    if len(rows) != 1:
        raise SegmentAccuracyError(
            f"expected exactly one ACTIVE model, found {len(rows)}: "
            f"{[r.model_version for r in rows]}"
        )
    mv, weights_uri = rows[0]
    if not weights_uri:
        raise SegmentAccuracyError(f"active model {mv} has no weights_uri")
    return mv, Path(weights_uri).parent


def obtain_bundle(
    session: Session, *, active_dir: Path, model_version: str, feature_version: str,
    out_root: Path, bundle_path: Path | None, date_to, first_valid_year: int,
    num_threads: int,
) -> tuple[dict, dict]:
    """Return ``(bundle_payload, attestation)`` — verified reuse or fresh regeneration.

    Reuse requires the stored bundle to verify (checksums) AND to carry the CURRENT active's
    attestation digest; anything else regenerates (codex: never silently drive the instrument
    with a stale or foreign OOF).
    """
    att = attestation_from_model_dir(active_dir, code_sha=code_sha())
    factory = general_factory_from_attestation(
        session, att,
        expected_model_version=model_version, expected_feature_version=feature_version,
    )
    if bundle_path is not None:
        payload = oof_bundle.read_bundle(bundle_path)
        oof_bundle.verify_bundle(payload)
        if payload["attestation_digest"] != att["attestation_digest"]:
            raise SegmentAccuracyError(
                "bundle attestation digest does not match the current active's attestation "
                f"({payload['attestation_digest'][:12]} != {att['attestation_digest'][:12]}); "
                "regenerate instead of reusing a stale/foreign bundle"
            )
        return payload, att
    _, payload = generate_oof_bundle(
        session, out_root=out_root, date_from=None, date_to=date_to,
        first_valid_year=first_valid_year, num_threads=num_threads,
        attestation=att, factory=factory, attestation_digest=att["attestation_digest"],
    )
    return payload, att


def _load_attrs(session: Session) -> pd.DataFrame:
    df = pd.read_sql(text(_ATTRS_SQL), session.bind)
    return df.set_index(["race_id", "horse_id"])


_ATTR_COLS = (
    "year", "month", "venue_code", "track_type", "distance", "race_class", "field_size",
    "sex", "weight", "weight_diff", "body_cell", "draw_pct", "days_since_last",
    "prior_gap_days", "prev_finish", "n_prior_starts", "n_prior_odds_obs", "q",
)


def assemble_inputs(
    session: Session, bundle: dict, *, eval_from: datetime.date, eval_to: datetime.date,
) -> tuple[list[RaceInput], dict[str, int]]:
    """Join bundle OOF predictions + eligibility + attributes into core inputs.

    Exclusion ledger (FR-010): every eval-window race is either scored or counted under a
    reason. p is renormalised over the started field (the 009-engine convention).
    """
    races = load_eval_races(session, start_date=eval_from, end_date=eval_to)
    attrs = _load_attrs(session)
    preds = bundle["predictions"]

    inputs: list[RaceInput] = []
    excl = {"not_in_bundle": 0, "ineligible_winner_count": 0, "partial_ingest": 0,
            "prediction_horse_mismatch": 0, "missing_attrs": 0}
    for er in races:
        ctx = er.context
        pr = preds.get(ctx.race_id)
        if pr is None:
            excl["not_in_bundle"] += 1
            continue
        pop = population_masks(er)
        if not pop.eligible:
            key = ("partial_ingest"
                   if (er.n_result_rows is not None
                       and er.n_result_rows < len(pop.started_horse_ids))
                   else "ineligible_winner_count")
            excl[key] += 1
            continue
        started = [h.horse_id for h in ctx.started_horses]
        if set(pr) != set(started):
            excl["prediction_horse_mismatch"] += 1
            continue
        p = np.array([float(pr[h]["win"]) for h in started])
        s = p.sum()
        if s <= 0:
            excl["prediction_horse_mismatch"] += 1
            continue
        p = p / s
        y = np.array([pop.started_win[h] for h in started], dtype=int)
        winner_idx = int(np.argmax(y))
        rows = []
        ok = True
        for h in started:
            key = (ctx.race_id, h)
            if key not in attrs.index:
                ok = False
                break
            row = attrs.loc[key]
            d = {c: (None if pd.isna(row[c]) else row[c]) for c in _ATTR_COLS}
            d["horse_id"] = h
            rows.append(d)
        if not ok:
            excl["missing_attrs"] += 1
            continue
        qs = [r["q"] for r in rows]
        q = (np.array([float(v) for v in qs]) if all(v is not None for v in qs) else None)
        r0 = rows[0]
        inputs.append(RaceInput(
            race_id=ctx.race_id, day=ctx.race_date.isoformat(), year=int(r0["year"]),
            field_size=len(started), winner_idx=winner_idx, p=p, y=y, q=q,
            race_attrs={k: r0[k] for k in
                        ("year", "venue_code", "track_type", "distance", "race_class",
                         "field_size")},
            horse_attrs=tuple(rows),
        ))
    return inputs, excl


def run_segment_accuracy(
    session: Session, *, out_root: Path, bundle_path: Path | None,
    eval_from: datetime.date, eval_to: datetime.date, first_valid_year: int,
    seed: int, bootstrap_b: int, num_threads: int,
) -> dict:
    model_version, active_dir = resolve_active(session)
    import json as _json
    metadata = _json.loads((active_dir / "metadata.json").read_text())
    feature_version = metadata["feature_version"]

    bundle, att = obtain_bundle(
        session, active_dir=active_dir, model_version=model_version,
        feature_version=feature_version, out_root=out_root, bundle_path=bundle_path,
        date_to=eval_to, first_valid_year=first_valid_year, num_threads=num_threads,
    )
    inputs, excl = assemble_inputs(session, bundle, eval_from=eval_from, eval_to=eval_to)
    if not inputs:
        raise SegmentAccuracyError("no scored races — refusing to persist an empty readout")

    label_hash = stable_hash(sorted(
        (r.race_id, r.horse_attrs[r.winner_idx]["horse_id"]) for r in inputs
    ))
    provenance = {
        "base_model_version": model_version,
        "feature_version": feature_version,
        "feature_hash": metadata.get("feature_hash"),
        "attestation_digest": att["attestation_digest"],
        "bundle_digest": oof_bundle.compute_bundle_digest(bundle),
        "prediction_checksum": bundle["prediction_checksum"],
        "oof_race_set_hash": bundle["oof_race_set_hash"],
        "scored_race_set_hash": race_set_hash([r.race_id for r in inputs]),
        "label_snapshot_hash": label_hash,
        "train_floor": "full-history (load start unbounded; 2007 initial train-only)",
        "eval_window": [eval_from.isoformat(), eval_to.isoformat()],
        "first_valid_year": first_valid_year,
        "fold_boundaries": bundle.get("fold_boundaries"),
        "probability_stage": "model-internal calibrated win prob (pre-two-gamma), "
                             "renormalised over the started field",
        "code_sha": code_sha(),
        "seed": seed, "bootstrap_b": bootstrap_b,
        "metric_contract_version": METRIC_CONTRACT_VERSION,
        "mask_library_version": MASK_LIBRARY_VERSION,
        "mask_library_hash": mask_library_hash(),
    }
    return build_payload(
        inputs, provenance=provenance, exclusions=excl, seed=seed, bootstrap_b=bootstrap_b,
    )

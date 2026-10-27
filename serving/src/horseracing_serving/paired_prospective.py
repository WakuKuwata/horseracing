"""133: prospective paired comparison of the served mixture vs the previous single model.

Two FIXED artifacts — the new production model ``mix-129-nj6`` (six-member mixture bundle) and
the old production model ``lgbm-094-cap900`` (booster + isotonic calibrator, with the stage
discount frozen by 129) — receive the SAME prepared input rows, acquired once, before the race
starts and before any result row exists. Both three-head predictions are published to an
append-only file record together with everything needed to audit the pair later:

* acquisition start/end, each model's prediction completion time, the record write time, the
  scheduled start, the result-row count at acquisition AND re-checked through a fresh connection
  after the last prediction (a prospective record must still be pre-start at write time),
* the started population, the typed prepared input rows of every regime (not just a hash) and
  the started history used for the mixture's prior-gap term,
* the identity of both comparators (bundle SHA + member files; anchor files + frozen λ2/λ3) and
  the code/runtime that produced the numbers,
* the link to the production run that actually served this race, when one exists pre-start, and
  the production λ of the day so both models' heads exist under the production display too.

Primary input condition is ``preweight`` (today's weight columns forced NaN for the whole race =
"as if before body weights are published"). Because masking after publication is a counterfactual,
the record also stores the genuine publication state in the database at acquisition
(0 / partial / all started horses weighed) and the ``serving`` regime that production applies at
that moment (091 race-level binarisation), so the correspondence is measured, not assumed.

The mixture's top2/top3 are stored under the PRODUCTION display convention (Harville from the
served win + stage discount) twice — with the frozen λ shared by both models (fair standardised
comparison) and with the day's production λ (what production displayed). Its research-only
averaged heads are kept under a name that says they are not served.

Nothing here writes to the database. ``score`` joins results only AFTER capture, applies the
pre-registered exclusion protocol with counts, and separates INTERIM status from the single FINAL
readout whose window and method live in a git-tracked pre-registration file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import decimal
import fcntl
import hashlib
import json
import math
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus
from horseracing_db.models import PredictionRun, Race, RaceHorse, RacePrediction, RaceResult
from horseracing_db.session import create_db_engine
from horseracing_eval import stage_discount as _sd_mod
from horseracing_eval.bootstrap import race_day_cluster_bootstrap_ci_v1
from horseracing_eval.metrics import ece_equal_mass
from horseracing_eval.stage_discount import StageDiscount
from horseracing_features.builder import build_feature_matrix
from horseracing_training import predictor as _training_predictor
from horseracing_training.calibration import DEFAULT_CLIP
from horseracing_training.predictor import assemble_predictions
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import mixture_correction, mixture_model, mixture_serving
from . import mixture_shadow as shadow
from . import predictor as _serving_predictor
from .mixture_model import FULL_HASH, feature_profile, predict_mixture, prepare_race_inputs
from .predictor import predict_race

SCHEMA_VERSION = 1
RECORD_KIND = "paired_prospective_record"
DAY_KIND = "paired_prospective_day_manifest"
SCORE_KIND = "paired_prospective_score"
PREREG_KIND = "paired_prospective_preregistration"
FINAL_RECEIPT_KIND = "paired_prospective_final_receipt"
# Independent of cwd, --output and --looks-log-dir; never a CLI override.
FINAL_RECEIPT_DIR = (
    Path(__file__).resolve().parents[3]
    / "artifacts/133-prospective-paired-comparison/final"
)
#: ``shadow_129`` = records of the 129 tool (same paired capture, research heads for the mixture);
#: they are adapted at scoring time by re-deriving the served top2/top3 from the recorded win.
SOURCES = ("capture", "shadow_129", "production_snapshot")
SOURCE_PRECEDENCE = {"capture": 0, "shadow_129": 1, "production_snapshot": 2}
REGIMES = ("preweight", "serving")
HEADS = ("win", "top2", "top3")
RESULT_STATUSES = ("finished", "stopped")
JST = shadow.JST
UTC = shadow.UTC
#: Feature columns that are NOT model inputs in feature_snapshots (pipeline audit extras).
SNAPSHOT_EXTRA_KEYS = ("_raw_win", "_calibrated_win")
#: Absolute tolerance (zero relative) for "persisted production numbers are reproduced".
PARITY_TOL = 1e-9

now_utc = shadow.now_utc
sha256 = shadow.sha256
read_json = shadow.read_json


class PairedError(ValueError):
    pass


# --- files -----------------------------------------------------------------------------------


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise PairedError("Non-finite value cannot be recorded")
    if isinstance(value, dict):
        for v in value.values():
            _finite(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _finite(v)


def publish_json(path, value):
    """Atomic, exclusive publication: serialise fully, write+fsync a temp file in the same
    directory, then ``os.link`` it to the final name (fails if the name exists), then unlink the
    temp. An interrupted write can never leave a truncated file under the final name."""
    _finite(value)
    body = (
        json.dumps(
            value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False, default=str
        )
        + "\n"
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    with tmp.open("x") as fh:
        fh.write(body)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def code_hashes():
    return {
        name: sha256(module.__file__)
        for name, module in (
            ("paired_prospective", __import__(__name__, fromlist=["_"])),
            ("mixture_shadow", shadow),
            ("mixture_model", mixture_model),
            ("mixture_correction", mixture_correction),
            ("mixture_serving", mixture_serving),
            ("serving_predictor", _serving_predictor),
            ("training_predictor", _training_predictor),
            ("eval_stage_discount", _sd_mod),
        )
    }


def _encode_value(v):
    if v is None:
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return None if math.isnan(f) else f
    if isinstance(v, decimal.Decimal):
        return float(v) if v.is_finite() else None
    if isinstance(v, (dt.date, dt.datetime, pd.Timestamp)):
        return v.isoformat()
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v)


def encode_rows(rows: pd.DataFrame) -> dict:
    """Typed, ordered copy of a prepared input frame (horse_id -> values by column)."""
    cols = [c for c in rows.columns if c != "horse_id"]
    return {
        "columns": cols,
        "dtypes": {c: str(rows[c].dtype) for c in cols},
        "rows": {
            str(h): [_encode_value(v) for v in rows.loc[i, cols].tolist()]
            for i, h in zip(rows.index, rows.horse_id, strict=True)
        },
    }


def frame_identity(rows: pd.DataFrame) -> dict:
    return {
        "sha256": shadow._frame_sha(rows),
        "columns_sha256": hashlib.sha256("\n".join(map(str, rows.columns)).encode()).hexdigest(),
        "n_rows": int(len(rows)),
    }


# --- comparators --------------------------------------------------------------------------


@dataclass(frozen=True)
class Comparators:
    """Both fixed artifacts, verified, plus the frozen display λ shared inside a record."""

    new_model_version: str
    bundle: object
    old_anchor: dict
    old_model: object
    old_stage_discount: object
    anchor_sha256: str

    def identity(self) -> dict:
        members = [
            {
                "id": m["id"],
                "branch": m["branch"],
                "seed": m["seed"],
                "weight": m["weight"],
                "files": dict(m["files"]),
                "coefficient_valid_year": m["correction"]["valid_year"],
                "coefficient_fit_through": m["correction"]["fit_through"],
            }
            for m in self.bundle.manifest["members"]
        ]
        years = {m["coefficient_valid_year"] for m in members}
        return {
            "new": {
                "model_version": self.new_model_version,
                "kind": "six_member_mixture",
                "bundle_id": self.bundle.manifest["bundle_id"],
                "bundle_manifest_sha256": self.bundle.sha256,
                "train_through": self.bundle.manifest["train_through"],
                "coefficient_train_through": self.bundle.manifest["coefficient_train_through"],
                "coefficient_valid_year": years.pop() if len(years) == 1 else sorted(years),
                "members": members,
                "win_rule": "equal-weight mean of six residual-corrected member win vectors",
                "display_rule": (
                    "assemble_predictions(eps=0.0): win kept exactly (mean of members), "
                    "harville top2/top3 with stage discount λ2/λ3"
                ),
            },
            "old": {
                "model_version": self.old_anchor["model_version"],
                "kind": "single_booster_isotonic",
                "files": dict(self.old_anchor["files"]),
                "anchor_sha256": self.anchor_sha256,
                "feature_version": self.old_anchor.get("feature_version"),
                "feature_hash": self.old_anchor.get("feature_hash"),
                "objective": self.old_anchor.get("objective"),
                "win_rule": "booster raw -> isotonic calibrator -> clip -> race normalisation",
                "display_rule": (
                    f"predict_race -> assemble_predictions(eps={DEFAULT_CLIP}): clip -> "
                    "race-normalise -> harville top2/top3 with stage discount λ2/λ3"
                ),
            },
            "shared_display_stage_discount": {
                "mode": "frozen",
                "asof_date": self.old_anchor["calibration"]["asof_date"],
                "lambda2": self.old_stage_discount.lambda2,
                "lambda3": self.old_stage_discount.lambda3,
                "artifact_sha256": self.old_anchor["calibration"]["sha256"],
                "note": (
                    "Inside a record both models use this frozen pair so top2/top3 differ only "
                    "through the win vector. The production λ of the day is stored separately."
                ),
            },
            "input_contract": {
                "feature_version": "features-021",
                "feature_hash": FULL_HASH,
                "n_columns": len(feature_profile()["full_columns"]),
            },
        }


def load_comparators(bundle_path, bundle_sha256, anchor_path, *, new_model_version) -> Comparators:
    bundle = shadow._open_bundle(bundle_path, bundle_sha256)
    anchor, old_model, old_sd = shadow.load_anchor(anchor_path)
    if anchor.get("feature_hash") != FULL_HASH or old_model.feature_hash != FULL_HASH:
        raise PairedError("old model does not consume the same 138-column input as the mixture")
    if list(old_model.feature_cols) != list(feature_profile()["full_columns"]):
        raise PairedError("old model feature order differs from the mixture's full profile")
    return Comparators(new_model_version, bundle, anchor, old_model, old_sd, sha256(anchor_path))


# --- prediction of one race under one regime -------------------------------------------------


def _heads(predictions, ids):
    return {
        h: [float(predictions[h].win), float(predictions[h].top2), float(predictions[h].top3)]
        for h in ids
    }


def _display(ids, win, sd):
    """Production display convention for a served win vector (131 review fix): assemble at
    eps=0 so the win vector is NOT re-clipped/re-normalised; top2/top3 = Harville + λ."""
    assembled = assemble_predictions(ids, win, eps=0.0, stage_discount=sd)
    if max(abs(assembled[h].win - win[i]) for i, h in enumerate(ids)) > 1e-12:
        raise PairedError("display assembly changed the win vector")
    return {
        h: [float(win[i]), float(assembled[h].top2), float(assembled[h].top3)]
        for i, h in enumerate(ids)
    }


def predict_pair(
    comparators: Comparators, race_id, feature_rows, raw_history, regime, *, production_sd=None
) -> dict:
    """Both models on the SAME prepared rows; heads, research heads, typed inputs, audits, times."""
    if regime not in REGIMES:
        raise PairedError("regime must be preweight or serving")
    rows, regime_audit = prepare_race_inputs(feature_rows, race_id, regime)
    ids = rows.horse_id.tolist()
    history = shadow.history_for(rows, raw_history)
    check = getattr(mixture_serving, "assert_history_matches_features", None)
    if check is not None:  # 131 review guard: prior-gap history must describe the same past
        check(rows, history)
    t0 = time.monotonic()
    mix = predict_mixture(comparators.bundle, race_id, feature_rows, history, regime)
    new_done = now_utc()
    new_seconds = time.monotonic() - t0
    if list(mix.predictions) != ids or not mix.inputs[rows.columns].equals(rows):
        raise PairedError("mixture inputs diverged from the shared prepared rows")
    win = np.asarray([mix.predictions[h].win for h in ids], dtype=float)
    if not np.isfinite(win).all() or (win <= 0).any():
        raise PairedError("mixture produced a non-finite or non-positive win vector")
    t1 = time.monotonic()
    old_pred, _snap, _expl, old_audit = predict_race(
        comparators.old_model, race_id, rows, stage_discount=comparators.old_stage_discount
    )
    old_done = now_utc()
    old_seconds = time.monotonic() - t1
    if list(old_pred) != ids:
        raise PairedError("old model started population differs from the shared rows")
    old_win = np.asarray([old_pred[h].win for h in ids], dtype=float)
    out = {
        "regime": regime,
        "regime_audit": regime_audit,
        "started_ids": ids,
        "inputs": encode_rows(rows),
        "input_identity": frame_identity(rows),
        "history": {
            "rows": [
                [r.race_id, r.horse_id, pd.Timestamp(r.race_date).date().isoformat()]
                for r in history.itertuples()
            ],
            "sha256": shadow._frame_sha(history),
            "n_rows": int(len(history)),
        },
        # Served convention, frozen λ shared by both models (fair standardised comparison).
        "new": _display(ids, win, comparators.old_stage_discount),
        "old": _heads(old_pred, ids),
        # Research-only averaged member heads: never used for served top2/top3 metrics.
        "new_research_heads_not_served": _heads(mix.predictions, ids),
        "new_audit": mix.audit,
        "old_audit": old_audit,
        "predicted_at": {"new": new_done.isoformat(), "old": old_done.isoformat()},
        "seconds": {"new": new_seconds, "old": old_seconds},
    }
    if production_sd is not None:
        # Production display of the day (counterfactual for the old model, which did not serve).
        out["display_production_lambda"] = {
            "lambda2": production_sd.lambda2,
            "lambda3": production_sd.lambda3,
            "new": _display(ids, win, production_sd),
            "old": _display(ids, old_win, production_sd),
        }
    return out


def _same_heads(a, b, tol=0.0):
    return set(a) == set(b) and all(abs(a[h][i] - b[h][i]) <= tol for h in a for i in range(3))


# --- production link --------------------------------------------------------------------------


def _persisted_heads(session, run_id):
    rows = session.execute(
        select(
            RacePrediction.horse_id,
            RacePrediction.win_prob,
            RacePrediction.top2_prob,
            RacePrediction.top3_prob,
        ).where(RacePrediction.prediction_run_id == run_id)
    ).all()
    return {
        h: [float(w), None if t2 is None else float(t2), None if t3 is None else float(t3)]
        for h, w, t2, t3 in rows
    }


def _lv_segment(logic_version, key):
    for seg in logic_version.split(";"):
        if seg.startswith(key + "="):
            return seg.split("=", 1)[1]
    return None


def production_link(session, race_id, *, model_version, scheduled_start, reference=None):
    """The LATEST production run of ``model_version`` for this race computed BEFORE the start.

    Runs are append-only rows with their own ``computed_at`` (``persistence.persist_run`` never
    updates an existing run), so ``computed_at < scheduled_start`` certifies a pre-start
    prediction. ``reference`` = this record's serving-regime heads {new: heads, new_prod: heads}
    to report how far the real served numbers sit from the simultaneous capture.
    """
    if scheduled_start is None:
        return None
    runs = session.execute(
        select(
            PredictionRun.prediction_run_id, PredictionRun.logic_version, PredictionRun.computed_at
        )
        .where(PredictionRun.race_id == race_id, PredictionRun.model_version == model_version)
        .order_by(PredictionRun.computed_at.desc())
    ).all()
    prestart = [r for r in runs if r[2] is not None and r[2] < scheduled_start]
    link = {
        "model_version": model_version,
        "n_runs_total": len(runs),
        "n_prestart_runs": len(prestart),
    }
    if not prestart:
        link["prestart_run"] = None
        link["selection_rule"] = "latest run with computed_at < scheduled_start"
        return link
    run_id, logic_version, computed_at = prestart[0]
    heads = _persisted_heads(session, run_id)
    link["selection_rule"] = "latest run with computed_at < scheduled_start"
    link["prestart_run"] = {
        "prediction_run_id": str(run_id),
        "logic_version": logic_version,
        "computed_at": computed_at.isoformat(),
        "seconds_before_start": (scheduled_start - computed_at).total_seconds(),
        "wregime": _lv_segment(logic_version, "wregime"),
        "lambda_rounded_in_logic_version": {
            "l2": _lv_segment(logic_version, "l2"),
            "l3": _lv_segment(logic_version, "l3"),
        },
        "heads": heads,
    }
    if reference is not None:
        new = reference["new"]
        common = set(heads) & set(new)
        link["same_started_set"] = set(heads) == set(new)
        link["max_abs_win_diff_vs_capture_serving"] = (
            max(abs(heads[h][0] - new[h][0]) for h in common) if common else None
        )
        prod = reference.get("new_prod")
        if prod is not None and common:
            link["max_abs_display_diff_vs_capture_under_production_lambda"] = max(
                abs(heads[h][i] - prod[h][i])
                for h in common
                for i in (1, 2)
                if heads[h][i] is not None
            )
    return link


def fit_production_stage_discount(session, race_date):
    """λ2/λ3 exactly as the production pipeline fits them for a run on ``race_date``."""
    from horseracing_probability.model_calibration import fit_product_stage_discount

    sd = fit_product_stage_discount(session, before_date=race_date, calibrator=None)
    return sd, {
        "method": "fit_product_stage_discount(before_date=race_date, calibrator=None)",
        "lambda2": sd.lambda2,
        "lambda3": sd.lambda3,
        "n_races_l2": sd.n_races_l2,
        "n_races_l3": sd.n_races_l3,
        "fallback": sd.fallback,
        "fit_at": now_utc().isoformat(),
        "note": (
            "Same function and cutoff production used; the fit sample is the latest persisted run "
            "per race strictly before the day, so later backfills of earlier days can move it. "
            "production_link reports the parity against the persisted served heads."
        ),
    }


# --- capture -----------------------------------------------------------------------------------


def _db_weight_state(session, race_id):
    rows = session.execute(
        select(RaceHorse.horse_id, RaceHorse.weight, RaceHorse.entry_status).where(
            RaceHorse.race_id == race_id
        )
    ).all()
    started = [r for r in rows if r[2] == EntryStatus.STARTED]
    weighed = sum(1 for r in started if r[1] is not None)
    state = "prepublication" if weighed == 0 else "full" if weighed == len(started) else "partial"
    return {
        "n_entries": len(rows),
        "n_started": len(started),
        "n_started_with_weight": weighed,
        "publication_state": state,
        "caveat": "0 weighed can also be ingestion lag (database state, not the JRA feed)",
    }


def fresh_facts(engine, race_id):
    """Start time and result count through a NEW connection (not the capture snapshot)."""
    with engine.connect() as conn:
        row = conn.execute(
            text("select race_date, post_time from races where race_id=:r"), {"r": race_id}
        ).first()
        n = conn.execute(
            text("select count(*) from race_results where race_id=:r"), {"r": race_id}
        ).scalar()
    if row is None:
        raise PairedError(f"Race {race_id} vanished")
    start = row[1]
    if start is not None and start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    return row[0], start, int(n)


def capture_day(
    session,
    *,
    race_ids,
    comparators: Comparators,
    out_dir,
    engine=None,
    feature_rows=None,
    classification="prospective",
    now=None,
    fit_production_lambda=True,
):
    """Paired capture of every eligible race on one day (both regimes, both models).

    Races that already started, have result rows, or lack a start time are SKIPPED with a reason
    (a late operator still records the rest of the day); nothing is ever back-dated. The clock can
    only be injected for ``rehearsal`` (tests); prospective captures always use the real clock.
    """
    if classification not in shadow.CLASSIFICATIONS:
        raise PairedError("classification must be prospective or rehearsal")
    if now is not None and classification == "prospective":
        raise PairedError("a prospective capture cannot run on an injected clock")
    clock = (lambda: now) if now is not None else now_utc
    facts = {rid: shadow._race_facts(session, rid) for rid in race_ids}
    days = {f[0] for f in facts.values()}
    if len(days) != 1:
        raise PairedError("One capture call serves a single race day")
    (race_date,) = days
    t_start = clock()
    eligible, dispositions = [], {}
    for rid in race_ids:
        try:
            shadow.validate_capture(
                classification, race_date, facts[rid][1], facts[rid][2], t_start, t_start
            )
            eligible.append(rid)
        except ValueError as exc:
            dispositions[rid] = {"status": "skipped", "reason": str(exc)}
    outputs = [{"race_id": rid, "skipped": d["reason"]} for rid, d in dispositions.items()]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": DAY_KIND,
        "race_day": race_date.isoformat(),
        "classification": classification,
        "scheduled_race_ids": list(race_ids),
        "invoked_at": t_start.isoformat(),
        "dispositions": dispositions,
        "can_adopt": False,
    }
    if not eligible:
        publish_json(
            Path(out_dir)
            / "days"
            / f"{race_date.isoformat()}.{t_start.strftime('%Y%m%dT%H%M%S%fZ')}.json",
            manifest,
        )
        return outputs
    t0 = time.monotonic()
    if feature_rows is None:
        feature_rows = build_feature_matrix(
            session, end_date=race_date, target_race_ids=frozenset(eligible)
        )
    if "race_date" not in feature_rows.columns:
        feature_rows = feature_rows.copy()
        feature_rows["race_date"] = race_date
    elif not (
        feature_rows.loc[feature_rows.race_id.isin(eligible), "race_date"] == race_date
    ).all():
        raise PairedError("Feature rows disagree with the verified race day")
    horse_ids = feature_rows.loc[feature_rows.race_id.isin(eligible), "horse_id"].unique()
    raw_history = shadow.load_started_history(session, horse_ids)
    production_sd, production_sd_doc = (None, None)
    if fit_production_lambda:
        production_sd, production_sd_doc = fit_production_stage_discount(session, race_date)
    acquired_at = clock()
    acquisition_seconds = time.monotonic() - t0
    identity = comparators.identity()
    hashes = code_hashes()
    runtime = shadow.runtime()
    present = set(feature_rows.race_id)
    for rid in eligible:
        if rid not in present:
            dispositions[rid] = {"status": "skipped", "reason": "no started rows in feature matrix"}
            outputs.append({"race_id": rid, "skipped": "no started rows in feature matrix"})
            continue
        per_regime = {
            regime: predict_pair(
                comparators, rid, feature_rows, raw_history, regime, production_sd=production_sd
            )
            for regime in REGIMES
        }
        # Fresh post-prediction check: still pre-start, still no result rows, through a connection
        # that does not share the capture snapshot. Fail closed -> the race is skipped, not written.
        t_check = clock()
        if engine is not None and classification == "prospective":
            _day, start_now, n_results_now = fresh_facts(engine, rid)
        else:
            _day, start_now, n_results_now = facts[rid]
        try:
            shadow.validate_capture(
                classification, race_date, start_now, n_results_now, t_start, t_check
            )
            if classification == "prospective" and start_now != facts[rid][1]:
                raise ValueError("scheduled start changed during capture")
        except ValueError as exc:
            dispositions[rid] = {
                "status": "skipped",
                "reason": f"post-prediction check failed: {exc}",
            }
            outputs.append({"race_id": rid, "skipped": dispositions[rid]["reason"]})
            continue
        serving = per_regime["serving"]
        reference = {"new": serving["new"]}
        if "display_production_lambda" in serving:
            reference["new_prod"] = serving["display_production_lambda"]["new"]
        link = production_link(
            session,
            rid,
            model_version=comparators.new_model_version,
            scheduled_start=facts[rid][1],
            reference=reference,
        )
        record = {
            "schema_version": SCHEMA_VERSION,
            "artifact_kind": RECORD_KIND,
            "source": "capture",
            "classification": classification,
            "pre_result": n_results_now == 0 and facts[rid][2] == 0,
            "race_id": rid,
            "race_day": race_date.isoformat(),
            "scheduled_start": facts[rid][1].isoformat() if facts[rid][1] is not None else None,
            "n_result_rows_at_capture": facts[rid][2],
            "n_result_rows_after_prediction": n_results_now,
            "timeline": {
                "acquisition_started_at": t_start.isoformat(),
                "acquisition_finished_at": acquired_at.isoformat(),
                "acquisition_seconds": acquisition_seconds,
                "predicted_at": {r: per_regime[r]["predicted_at"] for r in REGIMES},
                "post_prediction_check_at": t_check.isoformat(),
                "seconds_before_start_at_check": (
                    (facts[rid][1] - t_check).total_seconds() if facts[rid][1] is not None else None
                ),
                "clock": "real" if now is None else "injected(rehearsal only)",
            },
            "input_captured_at": t_start.isoformat(),
            "predicted_at": t_check.isoformat(),
            "primary_regime": "preweight",
            "db_weight_state_at_capture": _db_weight_state(session, rid),
            "regimes": per_regime,
            "serving_equals_preweight": _same_heads(serving["new"], per_regime["preweight"]["new"])
            and _same_heads(serving["old"], per_regime["preweight"]["old"]),
            "production_stage_discount_of_day": production_sd_doc,
            "comparators": identity,
            "production_link": link,
            "code": hashes,
            "runtime": runtime,
            "can_adopt": False,
            "eligible_for_verdict": False,
        }
        record["record_written_at"] = clock().isoformat()
        path = Path(out_dir) / "capture" / f"{rid}.json"
        try:
            publish_json(path, record)
        except FileExistsError:
            dispositions[rid] = {
                "status": "skipped",
                "reason": "record already exists (append-only)",
            }
            outputs.append({"race_id": rid, "skipped": dispositions[rid]["reason"]})
            continue
        dispositions[rid] = {"status": "captured", "path": str(path)}
        outputs.append(
            {
                "race_id": rid,
                "path": str(path),
                "n_started": len(serving["started_ids"]),
                "publication_state": record["db_weight_state_at_capture"]["publication_state"],
                "serving_equals_preweight": record["serving_equals_preweight"],
                "production_link_max_abs_win_diff": (link or {}).get(
                    "max_abs_win_diff_vs_capture_serving"
                ),
            }
        )
    manifest["dispositions"] = dispositions
    manifest["finished_at"] = clock().isoformat()
    publish_json(
        Path(out_dir)
        / "days"
        / f"{race_date.isoformat()}.{t_start.strftime('%Y%m%dT%H%M%S%fZ')}.json",
        manifest,
    )
    return outputs


# --- fallback source: re-predict the old model on a pre-start production snapshot -----------


def rows_from_snapshots(
    snapshots: dict[str, dict], *, race_id, race_date, feature_cols
) -> pd.DataFrame:
    """Rebuild the prepared input rows of a production run from its feature_snapshots.

    JSON lost the dtypes (``predictor._jsonable``: NaN -> null, numpy ints -> int, floats -> float,
    everything else -> str). The model paths coerce numeric columns with ``pd.to_numeric`` and
    categoricals with ``astype('category')``, so the replay is exact only if the decoded values
    are; the caller proves it by reproducing the persisted win (``PARITY_TOL``) and the old model's
    round-trip is validated separately on its own production runs (evidence/snapshot-roundtrip).
    """
    if not snapshots:
        raise PairedError("empty snapshot set")
    data = []
    for hid in sorted(snapshots):
        feats = snapshots[hid]
        missing = [c for c in feature_cols if c not in feats]
        if missing:
            raise PairedError(f"snapshot of {hid} lacks model inputs: {missing[:5]}")
        row = {c: feats[c] for c in feature_cols}
        row.update(race_id=race_id, horse_id=hid, race_date=race_date)
        data.append(row)
    frame = pd.DataFrame(data, columns=["race_id", "horse_id", "race_date", *feature_cols])
    return frame.astype(object).where(pd.notna(frame), np.nan)


def from_production(
    session, *, race_ids, comparators: Comparators, out_dir, fit_production_lambda=True
):
    """Derive a paired record from the pre-start production run of the NEW model.

    The mixture is re-predicted from the snapshot (with the started history as it is NOW — entry
    corrections since the run would break parity and exclude the race) and must reproduce its
    persisted win; the old model is then predicted on those exact rows. The old model's prediction
    happens later in wall-clock time but is a deterministic function of the frozen pre-start input;
    the record says so instead of back-dating it.
    """
    outputs = []
    identity = comparators.identity()
    cols = list(feature_profile()["full_columns"])
    sd_cache = {}
    for rid in race_ids:
        race_date, start, n_results_now = shadow._race_facts(session, rid)
        link = production_link(
            session, rid, model_version=comparators.new_model_version, scheduled_start=start
        )
        if link is None or not link.get("prestart_run"):
            outputs.append({"race_id": rid, "skipped": "no pre-start production run"})
            continue
        run = link["prestart_run"]
        snaps = dict(
            session.execute(
                text(
                    "select horse_id, features from feature_snapshots where prediction_run_id = :r"
                ),
                {"r": run["prediction_run_id"]},
            ).all()
        )
        if set(snaps) != set(run["heads"]):
            outputs.append({"race_id": rid, "skipped": "snapshot/prediction population differs"})
            continue
        try:
            rows = rows_from_snapshots(
                {
                    h: {k: v for k, v in f.items() if k not in SNAPSHOT_EXTRA_KEYS}
                    for h, f in snaps.items()
                },
                race_id=rid,
                race_date=race_date,
                feature_cols=cols,
            )
        except PairedError as exc:
            outputs.append({"race_id": rid, "skipped": f"snapshot_not_reproducible: {exc}"})
            continue
        if fit_production_lambda and race_date not in sd_cache:
            sd_cache[race_date] = fit_production_stage_discount(session, race_date)
        production_sd, production_sd_doc = sd_cache.get(race_date, (None, None))
        raw_history = shadow.load_started_history(session, rows.horse_id.unique())
        # The persisted rows are already regime-normalised; 'serving' is a no-op on a uniform race.
        pair = predict_pair(
            comparators, rid, rows, raw_history, "serving", production_sd=production_sd
        )
        parity = max(abs(pair["new"][h][0] - run["heads"][h][0]) for h in pair["new"])
        if parity > PARITY_TOL:
            outputs.append(
                {
                    "race_id": rid,
                    "skipped": f"snapshot_not_reproducible: max|win diff|={parity:.3e}",
                }
            )
            continue
        display_parity = None
        if "display_production_lambda" in pair:
            display_parity = max(
                abs(pair["display_production_lambda"]["new"][h][i] - run["heads"][h][i])
                for h in pair["new"]
                for i in (1, 2)
                if run["heads"][h][i] is not None
            )
        weighed = pair["regime_audit"]["n_weighed_before"]
        n = pair["regime_audit"]["n_started"]
        # Persisted rows cannot tell "nobody weighed yet" from "partial -> normalised to NaN".
        state = "full" if weighed == n else "unknown_zero_or_partial"
        record = {
            "schema_version": SCHEMA_VERSION,
            "artifact_kind": RECORD_KIND,
            "source": "production_snapshot",
            "classification": "prospective",
            "pre_result": True,
            "pre_result_basis": "production run computed_at < scheduled_start (append-only runs)",
            "race_id": rid,
            "race_day": race_date.isoformat(),
            "scheduled_start": start.isoformat(),
            "n_result_rows_at_capture": 0,
            "n_result_rows_at_replay": n_results_now,
            "input_captured_at": run["computed_at"],
            "predicted_at": pair["predicted_at"]["old"],
            "timeline": {
                "production_run_computed_at": run["computed_at"],
                "replay_predicted_at": pair["predicted_at"],
                "old_model_predicted_later_on_frozen_input": True,
            },
            "seconds_before_start": run["seconds_before_start"],
            "primary_regime": "serving",
            "db_weight_state_at_capture": {
                "n_started": n,
                "n_started_with_weight": weighed,
                "publication_state": state,
                "caveat": "reconstructed from persisted rows; production normalisation applied",
            },
            "regimes": {"serving": pair},
            "serving_equals_preweight": weighed == 0,
            "snapshot_parity": {
                "max_abs_win_diff": parity,
                "max_abs_display_diff_under_production_lambda": display_parity,
            },
            "production_stage_discount_of_day": production_sd_doc,
            "comparators": identity,
            "production_link": link,
            "code": code_hashes(),
            "runtime": shadow.runtime(),
            "record_written_at": now_utc().isoformat(),
            "can_adopt": False,
            "eligible_for_verdict": False,
        }
        path = Path(out_dir) / "production_snapshot" / f"{rid}.json"
        try:
            publish_json(path, record)
        except FileExistsError:
            outputs.append({"race_id": rid, "skipped": "record already exists (append-only)"})
            continue
        outputs.append(
            {
                "race_id": rid,
                "path": str(path),
                "n_started": n,
                "publication_state": state,
                "parity": parity,
                "display_parity": display_parity,
            }
        )
    return outputs


def snapshot_roundtrip(session, *, model, model_version, race_ids, stage_discount=None):
    """Replay a booster model from its OWN persisted feature_snapshots and compare the win vector.

    Independent validation that the JSON snapshot -> frame -> ``predict_race`` path reproduces the
    served numbers for the old model (the mixture's replay is validated in ``from_production``).
    One latest run per race on the given races; the stage discount only affects top2/top3, which
    are not compared here (λ of those runs was the runtime fit of that day).
    """
    cols = list(model.feature_cols)
    rows_out, worst = [], 0.0
    for rid in race_ids:
        run = session.execute(
            select(
                PredictionRun.prediction_run_id,
                PredictionRun.logic_version,
                PredictionRun.computed_at,
            )
            .where(PredictionRun.race_id == rid, PredictionRun.model_version == model_version)
            .order_by(PredictionRun.computed_at.desc())
        ).first()
        if run is None:
            rows_out.append({"race_id": rid, "skipped": "no run"})
            continue
        heads = _persisted_heads(session, run[0])
        snaps = dict(
            session.execute(
                text(
                    "select horse_id, features from feature_snapshots where prediction_run_id = :r"
                ),
                {"r": run[0]},
            ).all()
        )
        if set(snaps) != set(heads):
            rows_out.append({"race_id": rid, "skipped": "snapshot/prediction population differs"})
            continue
        race_date = session.get(Race, rid).race_date
        frame = rows_from_snapshots(
            {
                h: {k: v for k, v in f.items() if k not in SNAPSHOT_EXTRA_KEYS}
                for h, f in snaps.items()
            },
            race_id=rid,
            race_date=race_date,
            feature_cols=cols,
        )
        pred, _s, _e, _a = predict_race(model, rid, frame, stage_discount=stage_discount)
        diff = max(abs(float(pred[h].win) - heads[h][0]) for h in heads)
        worst = max(worst, diff)
        rows_out.append(
            {
                "race_id": rid,
                "prediction_run_id": str(run[0]),
                "logic_version": run[1],
                "n_horses": len(heads),
                "max_abs_win_diff": diff,
                "pass": diff <= PARITY_TOL,
            }
        )
    return {
        "artifact_kind": "paired_prospective_snapshot_roundtrip",
        "model_version": model_version,
        "tolerance_abs": PARITY_TOL,
        "n_races": sum(1 for r in rows_out if "max_abs_win_diff" in r),
        "n_pass": sum(1 for r in rows_out if r.get("pass")),
        "max_abs_win_diff": worst,
        "rows": rows_out,
        "computed_at": now_utc().isoformat(),
    }


# --- scoring (after results) ----------------------------------------------------------------------


def load_preregistration(path):
    content = Path(path).read_bytes()
    plan = json.loads(content)
    if plan.get("artifact_kind") != PREREG_KIND:
        raise PairedError("not a paired_prospective_preregistration")
    plan["_sha256"] = hashlib.sha256(content).hexdigest()
    return plan


def adapt_shadow_record(r: dict) -> dict:
    """Lift a 129 ``mixture_shadow_record`` into this record shape (one regime per file).

    The 129 candidate heads are the research average (not served); the served top2/top3 are a
    deterministic function of the recorded win and the frozen λ, so they are re-derived here and
    the research heads are kept under their non-served name. Nothing else is recomputed.
    """
    if r.get("artifact_kind") != "mixture_shadow_record":
        raise PairedError("not a mixture_shadow_record")
    sd_doc = r["anchor_stage_discount"]
    sd = StageDiscount(float(sd_doc["lambda2"]), float(sd_doc["lambda3"]), 0, 0, False)
    ids = list(r["candidate_started_ids"])
    win = np.asarray([r["candidate"][h][0] for h in ids], dtype=float)
    audit = r["regime_audit"]
    regime = r["primary_regime"]
    weighed, n = audit["n_weighed_before"], audit["n_started"]
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": RECORD_KIND,
        "source": "shadow_129",
        "classification": r["classification"],
        "pre_result": r["pre_result"],
        "race_id": r["race_id"],
        "race_day": r["race_day"],
        "scheduled_start": r.get("scheduled_start"),
        "n_result_rows_at_capture": r["n_result_rows_at_capture"],
        "input_captured_at": r["input_captured_at"],
        "predicted_at": r["predicted_at"],
        "primary_regime": regime,
        "db_weight_state_at_capture": {
            "n_started": n,
            "n_started_with_weight": weighed,
            "publication_state": "prepublication"
            if weighed == 0
            else "full"
            if weighed == n
            else "partial",
        },
        "regimes": {
            regime: {
                "regime": regime,
                "regime_audit": audit,
                "started_ids": ids,
                "input_identity": {"sha256": r["candidate_input_sha256"]},
                "history": {"sha256": r.get("history_sha256"), "n_rows": r.get("n_history_rows")},
                "new": _display(ids, win, sd),
                "new_research_heads_not_served": {h: list(r["candidate"][h]) for h in ids},
                "old": {h: list(r["anchor"][h]) for h in ids},
            }
        },
        "serving_equals_preweight": weighed == 0,
        "comparators": {
            "new": {
                "bundle_manifest_sha256": r["bundle_manifest_sha256"],
                "bundle_id": r.get("bundle_id"),
            },
            "old": {
                "anchor_sha256": r["anchor_model_sha256"],
                "model_version": r["anchor_model_version"],
            },
            "shared_display_stage_discount": {
                "mode": "frozen",
                "lambda2": float(sd_doc["lambda2"]),
                "lambda3": float(sd_doc["lambda3"]),
            },
        },
        "adapted_from": {"artifact_kind": "mixture_shadow_record", "code": r.get("code")},
        "can_adopt": False,
        "eligible_for_verdict": False,
    }


def registered_window(plan):
    try:
        start = dt.date.fromisoformat(plan["window"]["from"])
        end = dt.date.fromisoformat(plan["window"]["to"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PairedError("Invalid preregistered window") from exc
    if start > end:
        raise PairedError("Preregistered window is reversed")
    return start, end


def primary_regime(plan):
    primary = plan.get("primary", {}).get("regime")
    if primary not in REGIMES:
        raise PairedError("Missing or invalid preregistered primary regime")
    return primary


def record_scope_error(record, window, *, diagnostic=False):
    if diagnostic:
        if record.get("classification") != "rehearsal":
            return "diagnostic_run_accepts_rehearsal_only"
    elif record.get("classification") != "prospective" or record.get("pre_result") is not True:
        return "not_prospective"
    try:
        day = dt.date.fromisoformat(record.get("race_day"))
        if day.isoformat() != record["race_day"]:
            return "invalid_race_day"
    except (TypeError, ValueError):
        return "invalid_race_day"
    if not diagnostic and not window[0] <= day <= window[1]:
        return "outside_registered_window"
    return None


def select_records(record_dirs, *, plan, allow_rehearsal=False) -> tuple[dict, dict]:
    """One record per race by source precedence; identity mismatches are counted, never used.

    Precedence is fixed (capture > shadow_129 > production_snapshot) and a rejected record of a
    higher-precedence source does NOT fall back to a lower one: the race is rejected.
    """
    chosen: dict[str, dict] = {}
    rejected: dict[str, str] = {}
    rejected_races: set[str] = set()
    ident = plan["comparators"]
    window = registered_window(plan)
    for d in record_dirs:
        for path in sorted(Path(d).rglob("*.json")):
            if path.name.startswith("."):
                continue
            r = read_json(path)
            if r.get("artifact_kind") == "mixture_shadow_record":
                r = adapt_shadow_record(r)
            if r.get("artifact_kind") != RECORD_KIND:
                continue
            rid = r["race_id"]
            problems = []
            scope_error = record_scope_error(r, window, diagnostic=allow_rehearsal)
            if scope_error:
                problems.append(scope_error)
            c = r.get("comparators", {})
            if (
                c.get("new", {}).get("bundle_manifest_sha256")
                != ident["new_bundle_manifest_sha256"]
            ):
                problems.append("new_identity_mismatch")
            if c.get("old", {}).get("anchor_sha256") != ident["old_anchor_sha256"]:
                problems.append("old_identity_mismatch")
            sd = c.get("shared_display_stage_discount", {})
            if (sd.get("lambda2"), sd.get("lambda3")) != (
                ident["display_lambda2"],
                ident["display_lambda3"],
            ):
                problems.append("display_lambda_mismatch")
            if r.get("source") not in SOURCES:
                problems.append("unknown_source")
            if problems:
                rejected[f"{rid}:{r.get('source')}:{path.name}"] = ",".join(problems)
                rejected_races.add(rid)
                continue
            r["_path"] = str(path)
            prev = chosen.get(rid)
            if prev is not None and prev["source"] == r["source"] == "shadow_129":
                prev["regimes"].update(r["regimes"])  # one 129 file per regime -> one record
                prev.setdefault("_paths", [prev["_path"]]).append(r["_path"])
                continue
            if prev is None or SOURCE_PRECEDENCE[r["source"]] < SOURCE_PRECEDENCE[prev["source"]]:
                chosen[rid] = r
    for rid in rejected_races:
        chosen.pop(rid, None)
    return chosen, rejected


def load_results(session, race_ids):
    """Per race: result rows {horse: (finish_order|None, status)} and the current started set."""
    out = {}
    for rid in race_ids:
        res = session.execute(
            select(RaceResult.horse_id, RaceResult.finish_order, RaceResult.result_status).where(
                RaceResult.race_id == rid
            )
        ).all()
        started = set(
            session.scalars(
                select(RaceHorse.horse_id).where(
                    RaceHorse.race_id == rid, RaceHorse.entry_status == EntryStatus.STARTED
                )
            )
        )
        out[rid] = {"results": {h: (fo, st) for h, fo, st in res}, "started_now": started}
    return out


def load_race_attributes(session, race_ids):
    rows = session.execute(
        select(
            Race.race_id,
            Race.race_date,
            Race.venue_code,
            Race.distance,
            Race.track_type,
            Race.race_class,
        ).where(Race.race_id.in_(list(race_ids)))
    ).all()
    return {
        r[0]: {
            "race_date": r[1],
            "venue_code": r[2],
            "distance": r[3],
            "track_type": r[4],
            "race_class": r[5],
        }
        for r in rows
    }


def scheduled_races_in_window(session, plan):
    w = plan["window"]
    rows = session.execute(
        select(Race.race_id, Race.race_date).where(
            Race.race_date >= dt.date.fromisoformat(w["from"]),
            Race.race_date <= dt.date.fromisoformat(w["to"]),
        )
    ).all()
    settled = set(
        session.scalars(
            select(RaceResult.race_id)
            .where(RaceResult.race_id.in_([r[0] for r in rows]))
            .distinct()
        )
    )
    return {r[0]: {"race_date": r[1].isoformat(), "settled": r[0] in settled} for r in rows}


def race_class_group(value):
    if value in ("新馬", "未勝利"):
        return "maiden"
    if value in ("１勝", "２勝", "３勝", "1勝", "2勝", "3勝"):
        return "conditions"
    if value is None:
        return "unknown"
    return "open"


def distance_band(d):
    if d is None:
        return "unknown"
    return "<=1400" if d <= 1400 else "<=1800" if d <= 1800 else "<=2200" if d <= 2200 else ">2200"


def field_band(n):
    return "<=8" if n <= 8 else "9-13" if n <= 13 else ">=14"


def eligibility(record, result_bundle, *, regime):
    """Apply the pre-registered protocol to one record; returns (status, reason, scored)."""
    reg = record["regimes"].get(regime)
    if reg is None:
        return "excluded", "regime_not_recorded", None
    ids = list(reg["started_ids"])
    if set(reg["new"]) != set(ids) or set(reg["old"]) != set(ids):
        return "excluded", "prediction_missing_for_started_horse", None
    results = result_bundle["results"]
    if not results:
        return "pending", "results_not_ingested", None
    for h, (fo, st) in results.items():
        if st not in RESULT_STATUSES:
            raise PairedError(f"unknown result_status {st!r} for {h} in {record['race_id']}")
        if st == "finished" and fo is None:
            raise PairedError(f"finished horse {h} without finish_order in {record['race_id']}")
    missing = [h for h in ids if h not in results]
    extra = [h for h in results if h not in ids]
    if extra or (missing and result_bundle["started_now"] != set(ids)):
        return "excluded", "field_changed_after_capture", None
    if missing:
        return "pending", "results_incomplete", None
    winners = [h for h in ids if results[h][0] == 1]
    if len(winners) != 1:
        return "excluded", "dead_heat_or_no_unique_winner", None
    labels = {}
    for h in ids:
        fo, st = results[h]
        fin = st == "finished" and fo is not None
        # top-k labels: finish_order <= k, ties at 2nd/3rd all count as in-k (pre-registered).
        labels[h] = (
            1 if fin and fo == 1 else 0,
            1 if fin and fo <= 2 else 0,
            1 if fin and fo <= 3 else 0,
        )
    return "scored", None, {"winner": winners[0], "labels": labels}


def _nll(p):
    return -math.log(min(max(p, 1e-15), 1.0))


def _argmax(heads):
    # deterministic tie-break: highest win, then horse_id order
    return sorted(heads, key=lambda h: (-heads[h][0], h))[0]


def weight_state_of(record):
    state = record.get("db_weight_state_at_capture", {}).get("publication_state")
    return state or "unknown"


def _summary(rows, boot):
    if not rows:
        return {"n_races": 0, "n_days": 0}
    by_day = defaultdict(list)
    for x in rows:
        by_day[x["race_day"]].append(x["diff_winner_nll"])
    ci95 = race_day_cluster_bootstrap_ci_v1(by_day, b=boot["b"], seed=boot["seed"], alpha=0.05)
    ci9875 = race_day_cluster_bootstrap_ci_v1(by_day, b=boot["b"], seed=boot["seed"], alpha=0.0125)
    days = sorted(by_day)
    point = ci95.point
    loo = {}
    if len(days) >= 2:
        for d in days:
            rest = [v for dd in days if dd != d for v in by_day[dd]]
            loo[d] = float(np.mean(rest)) - point
    return {
        "n_races": len(rows),
        "n_days": len(by_day),
        "new_winner_nll": float(np.mean([x["new_winner_nll"] for x in rows])),
        "old_winner_nll": float(np.mean([x["old_winner_nll"] for x in rows])),
        "diff_winner_nll": point,
        "ci95": [ci95.ci_low, ci95.ci_high],
        "ci9875": [ci9875.ci_low, ci9875.ci_high],
        "share_races_new_better": float(np.mean([x["diff_winner_nll"] < 0 for x in rows])),
        "new_brier_win": float(np.mean([x["new_brier_win"] for x in rows])),
        "old_brier_win": float(np.mean([x["old_brier_win"] for x in rows])),
        "top1_agreement": float(np.mean([x["new_top1"] == x["old_top1"] for x in rows])),
        "new_top1_hit": float(np.mean([x["new_top1"] == x["winner"] for x in rows])),
        "old_top1_hit": float(np.mean([x["old_top1"] == x["winner"] for x in rows])),
        "by_day": {
            d: {"n": len(by_day[d]), "diff_winner_nll": float(np.mean(by_day[d]))} for d in days
        },
        "leave_one_day_out_shift": loo,
    }


def score_records(chosen: dict, results: dict, attrs: dict, *, plan, regime, mode, today=None,
                  diagnostic=False, unsettled_in_window=0):
    """Pure scoring given already-loaded data. ``mode`` is 'interim' or 'final'."""
    if mode not in ("interim", "final"):
        raise PairedError("mode must be interim or final")
    if regime not in REGIMES:
        raise PairedError("Unknown scoring regime")
    if diagnostic and mode != "interim":
        raise PairedError("Rehearsal diagnostics require interim mode")
    primary_regime(plan)
    window = registered_window(plan)
    boot = plan["bootstrap"]
    per_race, excluded, pending = [], {}, {}
    horse = {m: {h: ([], []) for h in HEADS} for m in ("new", "old")}
    for rid in sorted(chosen):
        r = chosen[rid]
        scope_error = record_scope_error(r, window, diagnostic=diagnostic)
        if scope_error:
            excluded[rid] = scope_error
            continue
        status, reason, sc = eligibility(
            r, results.get(rid, {"results": {}, "started_now": set()}), regime=regime
        )
        if status == "excluded":
            excluded[rid] = reason
            continue
        if status == "pending":
            pending[rid] = reason
            continue
        reg = r["regimes"][regime]
        w = sc["winner"]
        ids = reg["started_ids"]
        n = len(ids)
        row = {
            "race_id": rid,
            "race_day": r["race_day"],
            "source": r["source"],
            "n_started": n,
            "winner": w,
            "new_winner_nll": _nll(reg["new"][w][0]),
            "old_winner_nll": _nll(reg["old"][w][0]),
            "new_top1": _argmax(reg["new"]),
            "old_top1": _argmax(reg["old"]),
            "new_brier_win": float(
                np.mean([(reg["new"][h][0] - sc["labels"][h][0]) ** 2 for h in ids])
            ),
            "old_brier_win": float(
                np.mean([(reg["old"][h][0] - sc["labels"][h][0]) ** 2 for h in ids])
            ),
        }
        row["diff_winner_nll"] = row["new_winner_nll"] - row["old_winner_nll"]
        a = attrs.get(rid, {})
        row["conditions"] = {
            "field_size": field_band(n),
            "distance": distance_band(a.get("distance")),
            "track_type": a.get("track_type") or "unknown",
            "race_class": race_class_group(a.get("race_class")),
            "weight_publication_state": weight_state_of(r),
            "source": r["source"],
        }
        per_race.append(row)
        for h in ids:
            for m in ("new", "old"):
                for i, head in enumerate(HEADS):
                    horse[m][head][0].append(reg[m][h][i])
                    horse[m][head][1].append(sc["labels"][h][i])

    overall = _summary(per_race, boot)
    conditions = {}
    for key in (
        "field_size",
        "distance",
        "track_type",
        "race_class",
        "weight_publication_state",
        "source",
    ):
        groups = defaultdict(list)
        for x in per_race:
            groups[x["conditions"][key]].append(x)
        conditions[key] = {}
        for g, v in sorted(groups.items()):
            s = _summary(v, boot)
            s.pop("by_day", None)
            s.pop("leave_one_day_out_shift", None)
            conditions[key][g] = s
    heads = {}
    for head in HEADS:
        heads[head] = {}
        for m in ("new", "old"):
            p, y = horse[m][head]
            if not p:
                heads[head][m] = None
                continue
            pa = np.clip(np.asarray(p, dtype=float), 1e-15, 1 - 1e-15)
            ya = np.asarray(y, dtype=float)
            heads[head][m] = {
                "n_horses": len(p),
                "log_loss": float(np.mean(-(ya * np.log(pa) + (1 - ya) * np.log(1 - pa)))),
                "brier": float(np.mean((pa - ya) ** 2)),
                "ece_equal_mass_10": ece_equal_mass(pa, ya, bins=10)["ece"],
                "mean_pred": float(pa.mean()),
                "realized_rate": float(ya.mean()),
            }
    retrain = plan.get("retraining_variance_note", {})
    note = None
    if overall.get("n_races") and retrain.get("sd_fold_historical") is not None:
        note = {
            "sd_fold_historical": retrain["sd_fold_historical"],
            "status": retrain.get("status"),
            "applies_to": "the separate question whether a RE-TRAINED mixture also beats the old; "
            "both artifacts compared here are fixed, so it is not part of the CI",
        }
    out = {
        "artifact_kind": SCORE_KIND,
        "mode": mode,
        "regime": regime,
        "preregistration_sha256": plan["_sha256"],
        "window": plan["window"],
        "overall": overall,
        "conditions": conditions,
        "heads_served_convention_frozen_lambda": heads,
        "per_race": per_race,
        "excluded": excluded,
        "excluded_counts": _counts(excluded.values()),
        "pending": pending,
        "pending_counts": _counts(pending.values()),
        "retraining_variance_note": note,
        "computed_at": now_utc().isoformat(),
        "can_adopt": False,
    }
    if diagnostic:
        out["status"] = "DIAGNOSTIC_REHEARSAL_NOT_EVIDENCE"
        out["verdict"] = None
        out["diagnostic_rehearsal"] = True
    elif mode == "interim":
        out["status"] = "INTERIM_STATUS_NOT_A_VERDICT"
        out["verdict"] = None
    else:
        out.update(final_verdict(overall, plan, pending, regime=regime, today=today,
                                 unsettled_in_window=unsettled_in_window))
    return out


def _counts(values):
    c = defaultdict(int)
    for v in values:
        c[v] += 1
    return dict(sorted(c.items()))


def final_verdict(overall, plan, pending, *, regime, today=None, unsettled_in_window=0):
    if regime not in REGIMES:
        raise PairedError("Unknown scoring regime")
    if regime != primary_regime(plan):
        return {"status": "SECONDARY_SUMMARY_NOT_A_VERDICT", "verdict": None}
    window = plan["window"]
    today = today or now_utc().astimezone(JST).date()
    _, end = registered_window(plan)
    reasons = []
    if today <= end:
        reasons.append(f"window not over (ends {end.isoformat()})")
    if pending:
        reasons.append(f"{len(pending)} recorded races pending results")
    if unsettled_in_window:
        reasons.append(f"{unsettled_in_window} scheduled races in the window have no results yet")
    if overall.get("n_days", 0) < int(window["min_eval_days"]):
        reasons.append(
            f"n_days {overall.get('n_days', 0)} < min_eval_days {window['min_eval_days']}"
        )
    if reasons:
        return {"status": "NO_DECISION", "verdict": None, "no_decision_reasons": reasons}
    lo, hi = overall["ci95"]
    if lo is None:
        return {"status": "NO_DECISION", "verdict": None, "no_decision_reasons": ["CI undefined"]}
    verdict = "NEW_BETTER" if hi < 0 else "OLD_BETTER" if lo > 0 else "INCONCLUSIVE"
    return {
        "status": "FINAL",
        "verdict": verdict,
        "rule": plan["decision_rule"],
        "point_estimate_diff_winner_nll": overall["diff_winner_nll"],
        "ci95": overall["ci95"],
    }


# --- CLI -----------------------------------------------------------------------------------


def _report_digest(report):
    return hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()


def _validate_final_report(report, plan):
    if not isinstance(report, dict) or (
        report.get("artifact_kind") != SCORE_KIND
        or report.get("mode") != "final"
        or report.get("status") != "FINAL"
        or report.get("verdict") not in ("NEW_BETTER", "OLD_BETTER", "INCONCLUSIVE")
        or report.get("regime") != primary_regime(plan)
        or report.get("preregistration_sha256") != plan["_sha256"]
        or report.get("window") != plan["window"]
        or report.get("diagnostic_rehearsal", False)
        or report.get("can_adopt") is not False
    ):
        raise PairedError("Final receipt report does not match the preregistration")


def _export_score(output, report):
    try:
        publish_json(output, report)
    except FileExistsError:
        if read_json(output) != report:
            raise


def publish_score_once(*, plan, regime, mode, output, compute):
    """Commit one primary FINAL per preregistration, before exporting a user copy.

    The callback includes database reads and runs under the lock. A later invocation exports
    the immutable receipt without calling it, even if the previous export failed. Interim,
    secondary and NO_DECISION reports do not consume the final slot.
    """
    primary = primary_regime(plan)
    if mode not in ("interim", "final") or regime not in REGIMES:
        raise PairedError("Invalid scoring mode or regime")
    if mode != "final" or regime != primary:
        report = compute()
        if report.get("status") == "FINAL" or report.get("verdict") is not None:
            raise PairedError("Only the primary final run may issue a verdict")
        _export_score(output, report)
        return report
    key = plan["_sha256"]
    if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise PairedError("Invalid preregistration SHA256")
    FINAL_RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    canonical = FINAL_RECEIPT_DIR / f"{key}.json"
    # Never unlink the lock: another process may already be waiting on its inode.
    with (FINAL_RECEIPT_DIR / f"{key}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if canonical.exists():
            try:
                receipt = read_json(canonical)
                report = receipt["report"]
                if (
                    receipt["artifact_kind"] != FINAL_RECEIPT_KIND
                    or receipt["schema_version"] != SCHEMA_VERSION
                    or receipt["preregistration_sha256"] != key
                    or receipt["report_sha256"] != _report_digest(report)
                ):
                    raise PairedError("Invalid final receipt identity or digest")
                _validate_final_report(report, plan)
            except (KeyError, TypeError, ValueError) as exc:
                raise PairedError(f"Invalid final receipt: {canonical}") from exc
        else:
            report = compute()
            if report.get("status") == "FINAL":
                _validate_final_report(report, plan)
                publish_json(canonical, {
                    "artifact_kind": FINAL_RECEIPT_KIND,
                    "schema_version": SCHEMA_VERSION,
                    "preregistration_sha256": key,
                    "scoring_code_sha256": sha256(__file__),
                    "report_sha256": _report_digest(report),
                    "report": report,
                })
                directory_fd = os.open(FINAL_RECEIPT_DIR, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            elif report.get("status") != "NO_DECISION" or report.get("verdict") is not None:
                raise PairedError("Primary final run must return FINAL or NO_DECISION")
        _export_score(output, report)
        return report


def _compute_score(args, plan):
    diagnostic = bool(args.include_rehearsal_diagnostic)
    chosen, rejected = select_records(args.records, plan=plan, allow_rehearsal=diagnostic)
    engine = create_db_engine(args.database_url)
    with Session(engine) as session:
        read_only_snapshot(session)
        results = load_results(session, list(chosen))
        attrs = load_race_attributes(session, list(chosen))
        scheduled = scheduled_races_in_window(session, plan)
        session.rollback()
    unsettled = sum(1 for v in scheduled.values() if not v["settled"])
    doc = score_records(chosen, results, attrs, plan=plan, regime=args.regime, mode=args.mode,
                        diagnostic=diagnostic, unsettled_in_window=unsettled)
    doc["rejected_records"] = rejected
    doc["record_dirs"] = [str(d) for d in args.records]
    doc["coverage"] = {
        "scheduled_races_in_window_now": len(scheduled),
        "scheduled_days_in_window_now": len({v["race_date"] for v in scheduled.values()}),
        "races_with_record": len(chosen),
        "races_without_record": sorted(rid for rid in scheduled if rid not in chosen),
        "scheduled_races_without_results_now": unsettled,
    }
    if sha256(args.preregistration) != plan["_sha256"]:
        raise PairedError("Preregistration changed during scoring")
    return doc


def _score_cli(args):
    plan = load_preregistration(args.preregistration)
    if args.include_rehearsal_diagnostic and args.mode != "interim":
        raise PairedError("--include-rehearsal-diagnostic is a pipeline check; interim mode only")
    doc = publish_score_once(plan=plan, regime=args.regime, mode=args.mode, output=args.output,
                             compute=lambda: _compute_score(args, plan))
    if args.looks_log_dir is not None:
        _log_look(args.looks_log_dir, doc)
    brief = {k: doc[k] for k in ("mode", "status", "verdict", "excluded_counts", "pending_counts")}
    brief["overall"] = {k: v for k, v in doc["overall"].items()
                        if k not in ("by_day", "leave_one_day_out_shift")}
    brief["coverage"] = {k: v for k, v in doc["coverage"].items() if k != "races_without_record"}
    print(json.dumps(brief, ensure_ascii=False, default=str))
    return 0


def read_only_snapshot(session):
    """First statement of the transaction: one consistent read-only snapshot, never a write."""
    session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))


def _races_on(session, day):
    ids = list(
        session.scalars(select(Race.race_id).where(Race.race_date == day).order_by(Race.race_id))
    )
    if not ids:
        raise PairedError(f"No races on {day}")
    return ids


def _log_look(evidence_dir, doc):
    Path(evidence_dir).mkdir(parents=True, exist_ok=True)
    line = {
        "at": doc["computed_at"],
        "mode": doc["mode"],
        "regime": doc["regime"],
        "n_races": doc["overall"].get("n_races"),
        "n_days": doc["overall"].get("n_days"),
        "status": doc.get("status"),
        "verdict": doc.get("verdict"),
    }
    with (Path(evidence_dir) / "looks.jsonl").open("a") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database-url", default=None)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("capture", "from-production"):
        c = sub.add_parser(name)
        c.add_argument("--date", required=True)
        c.add_argument("--bundle", type=Path, required=True)
        c.add_argument("--bundle-sha256", required=True)
        c.add_argument("--anchor", type=Path, required=True)
        c.add_argument("--new-model-version", default="mix-129-nj6")
        c.add_argument("--out-dir", type=Path, required=True)
        c.add_argument("--no-production-lambda", action="store_true")
        if name == "capture":
            c.add_argument(
                "--classification", choices=shadow.CLASSIFICATIONS, default="prospective"
            )
    s = sub.add_parser("score")
    s.add_argument("--records", type=Path, action="append", required=True)
    s.add_argument("--preregistration", type=Path, required=True)
    s.add_argument("--mode", choices=("interim", "final"), default="interim")
    s.add_argument("--regime", choices=REGIMES, default="preweight")
    s.add_argument("--output", type=Path, required=True)
    s.add_argument("--looks-log-dir", type=Path, default=None)
    s.add_argument(
        "--include-rehearsal-diagnostic",
        action="store_true",
        help="pipeline check on REHEARSAL records (results known at capture); never evidence",
    )
    rt = sub.add_parser("snapshot-roundtrip")
    rt.add_argument("--date", required=True)
    rt.add_argument("--anchor", type=Path, required=True)
    rt.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)
    if args.command == "score":
        return _score_cli(args)
    engine = create_db_engine(args.database_url)
    with Session(engine) as session:
        read_only_snapshot(session)
        if args.command in ("capture", "from-production"):
            comparators = load_comparators(
                args.bundle,
                args.bundle_sha256,
                args.anchor,
                new_model_version=args.new_model_version,
            )
            race_ids = _races_on(session, dt.date.fromisoformat(args.date))
            if args.command == "capture":
                out = capture_day(
                    session,
                    race_ids=race_ids,
                    comparators=comparators,
                    out_dir=args.out_dir,
                    engine=engine,
                    classification=args.classification,
                    fit_production_lambda=not args.no_production_lambda,
                )
            else:
                out = from_production(
                    session,
                    race_ids=race_ids,
                    comparators=comparators,
                    out_dir=args.out_dir,
                    fit_production_lambda=not args.no_production_lambda,
                )
            for item in out:
                print(json.dumps(item, ensure_ascii=False))
        elif args.command == "snapshot-roundtrip":
            anchor, old_model, old_sd = shadow.load_anchor(args.anchor)
            race_ids = _races_on(session, dt.date.fromisoformat(args.date))
            doc = snapshot_roundtrip(
                session,
                model=old_model,
                model_version=anchor["model_version"],
                race_ids=race_ids,
                stage_discount=old_sd,
            )
            doc["anchor_sha256"] = sha256(args.anchor)
            publish_json(args.output, doc)
            print(
                json.dumps(
                    {k: doc[k] for k in ("model_version", "n_races", "n_pass", "max_abs_win_diff")}
                )
            )
        session.rollback()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

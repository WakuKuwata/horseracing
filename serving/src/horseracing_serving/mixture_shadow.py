"""129 shadow: file-only paired capture of the six-member candidate and the real anchor.

The candidate bundle and the frozen production anchor receive the SAME as-of feature
rows under the SAME input regime, and both three-head predictions are appended to a
file record. Nothing here writes to the database or activates a model. A record is
``prospective`` only when its provenance proves a pre-start capture (real scheduled
start in the future, no result rows, input captured before prediction); anything else
must be requested explicitly as ``rehearsal`` and can never become confirmation
evidence.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.metadata
import json
import math
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus  # noqa: F401  (re-exported for callers/tests)
from horseracing_db.models import ModelVersion, Race, RaceResult
from horseracing_db.session import create_db_engine
from horseracing_eval.stage_discount import StageDiscount, logic_version_fragment
from horseracing_features.builder import build_feature_matrix
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from . import mixture_correction, mixture_model
from . import mixture_serving as _serving
from .mixture_model import load_mixture_bundle, predict_mixture, prepare_race_inputs
from .model_loader import load_serving_model
from .predictor import predict_race

JST = dt.timezone(dt.timedelta(hours=9))
UTC = dt.UTC
HISTORY_START = dt.date(2007, 1, 1)
CLASSIFICATIONS = ("prospective", "rehearsal")
REGIMES = ("preweight", "serving")
ANCHOR_FILES = ("model.txt", "calibrator.pkl", "preprocessor.pkl", "metadata.json")


def now_utc():
    return dt.datetime.now(UTC)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite value cannot be recorded")
    if isinstance(value, dict):
        for v in value.values():
            _finite(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _finite(v)


def write_json_new(path, value):
    """Serialize fully first, then create the file exclusively (append-only, atomic)."""
    _finite(value)
    body = (
        json.dumps(
            value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False, default=str
        )
        + "\n"
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as fh:
        fh.write(body)
        fh.flush()
        os.fsync(fh.fileno())


def read_json(path):
    def reject(value):
        raise ValueError(f"Nonfinite JSON value: {value}")

    return json.loads(Path(path).read_text(), parse_constant=reject)


def runtime():
    return {
        "python": sys.version,
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("numpy", "pandas", "lightgbm", "scikit-learn", "scipy", "SQLAlchemy")
        },
    }


def code_hashes():
    return {
        name: sha256(module.__file__)
        for name, module in (
            ("mixture_shadow", sys.modules[__name__]),
            ("mixture_model", mixture_model),
            ("mixture_correction", mixture_correction),
        )
    }


# --- provenance ---------------------------------------------------------------------


def _aware(value, label):
    if not isinstance(value, dt.datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be a timezone-aware datetime")
    return value


def validate_capture(classification, race_date, scheduled_start, result_count, captured_at, now):
    """Fail closed unless the record's provenance supports its classification.

    ``prospective`` requires a real future scheduled start on the race's calendar day (JST),
    zero result rows, and an input capture that is not in the future. ``rehearsal`` only
    requires consistent timestamps; it can never be promoted to prospective later.
    """
    if classification not in CLASSIFICATIONS:
        raise ValueError("Unknown capture classification")
    if type(result_count) is not int or result_count < 0:
        raise ValueError("Result row count must be a non-negative integer")
    _aware(now, "now")
    _aware(captured_at, "captured_at")
    if captured_at > now:
        raise ValueError("Input capture time cannot be in the future")
    if not isinstance(race_date, dt.date) or isinstance(race_date, dt.datetime):
        raise ValueError("race_date must be a calendar date")
    if classification == "rehearsal":
        return
    if scheduled_start is None:
        raise ValueError("Prospective capture requires a scheduled start time")
    _aware(scheduled_start, "scheduled_start")
    if scheduled_start <= now:
        raise ValueError("Prospective capture requires a future scheduled start")
    if scheduled_start.astimezone(JST).date() != race_date:
        raise ValueError("Scheduled start is not on the race day")
    if result_count != 0:
        raise ValueError("Prospective capture requires zero result rows")


history_for = _serving.history_for
load_started_history = _serving.load_started_history


# --- anchor ---------------------------------------------------------------------------


class FrozenCatalog:
    """Session stand-in for the loader: resolves exactly one frozen ModelVersion row."""

    def __init__(self, row):
        self.row = row

    def get(self, cls, key):
        if cls is not ModelVersion:
            raise ValueError("Frozen catalog only serves ModelVersion")
        return self.row if key == self.row.model_version else None


def freeze_anchor(session, *, model_version, asof_date, output, stage_discount_output):
    """Bind the real serving anchor: its artifacts and its actual stage-discount behaviour.

    The production path refits λ2/λ3 before every run from persisted predictions strictly
    before the target day; the frozen artifact records that fit once, at ``asof_date``, so
    the confirmation comparator is a fixed object rather than a moving one.
    """
    from horseracing_probability.model_calibration import fit_product_stage_discount

    mv = session.get(ModelVersion, model_version)
    if mv is None or not mv.weights_uri or not mv.calibrator_uri:
        raise ValueError(f"ModelVersion {model_version!r} has no artifacts")
    art_dir = Path(mv.weights_uri).resolve().parent
    files = {str(art_dir / n): sha256(art_dir / n) for n in ANCHOR_FILES}
    if Path(mv.calibrator_uri).resolve() != art_dir / "calibrator.pkl":
        files[str(Path(mv.calibrator_uri).resolve())] = sha256(mv.calibrator_uri)
    if asof_date > now_utc().astimezone(JST).date():
        raise ValueError("Anchor calibration as-of date cannot be in the future")
    sd = fit_product_stage_discount(session, before_date=asof_date, calibrator=None)
    created = now_utc()
    sd_doc = {
        "schema_version": 1,
        "artifact_kind": "mixture_shadow_anchor_stage_discount",
        "model_version": model_version,
        "asof_date": asof_date.isoformat(),
        "method": "fit_product_stage_discount(before_date=asof_date, calibrator=None)",
        "lambda2": sd.lambda2,
        "lambda3": sd.lambda3,
        "n_races_l2": sd.n_races_l2,
        "n_races_l3": sd.n_races_l3,
        "fallback": sd.fallback,
        "logic_version_fragment": logic_version_fragment(sd),
        "created_at": created.isoformat(),
        "can_adopt": False,
    }
    write_json_new(stage_discount_output, sd_doc)
    meta = read_json(art_dir / "metadata.json")
    anchor = {
        "schema_version": 1,
        "artifact_kind": "mixture_shadow_anchor",
        "can_adopt": False,
        "eligible_for_verdict": False,
        "model_version": model_version,
        "row": {
            "model_version": mv.model_version,
            "adoption_status": mv.adoption_status,
            "weights_uri": str(Path(mv.weights_uri).resolve()),
            "calibrator_uri": str(Path(mv.calibrator_uri).resolve()),
        },
        "files": files,
        "feature_version": meta.get("feature_version"),
        "feature_hash": meta.get("feature_hash"),
        "objective": meta.get("objective"),
        "calibration": {
            "mode": "frozen_stage_discount",
            "artifact_path": str(Path(stage_discount_output).resolve()),
            "sha256": sha256(stage_discount_output),
            "asof_date": asof_date.isoformat(),
            "lambda2": sd.lambda2,
            "lambda3": sd.lambda3,
        },
        "runtime": runtime(),
        "created_at": created.isoformat(),
    }
    write_json_new(output, anchor)
    return anchor


def load_anchor(path):
    """Verify every bound artifact digest BEFORE the loader touches any file."""
    path = Path(path)
    anchor = read_json(path)
    if (
        anchor.get("schema_version") != 1
        or anchor.get("artifact_kind") != "mixture_shadow_anchor"
        or anchor.get("can_adopt") is not False
        or not isinstance(anchor.get("files"), dict)
        or not anchor["files"]
    ):
        raise ValueError("Invalid frozen anchor")
    for name, expected in anchor["files"].items():
        if not Path(name).is_absolute() or not Path(name).is_file() or sha256(name) != expected:
            raise ValueError(f"Anchor artifact missing or changed: {name}")
    row = anchor.get("row", {})
    if not isinstance(row, dict) or row.get("model_version") != anchor.get("model_version"):
        raise ValueError("Anchor row/model identity differs")
    calibration = anchor.get("calibration", {})
    sd_path = Path(calibration.get("artifact_path", ""))
    if (
        calibration.get("mode") != "frozen_stage_discount"
        or not sd_path.is_absolute()
        or not sd_path.is_file()
        or sha256(sd_path) != calibration.get("sha256")
    ):
        raise ValueError("Anchor stage discount artifact missing or changed")
    sd_doc = read_json(sd_path)
    if (
        sd_doc.get("artifact_kind") != "mixture_shadow_anchor_stage_discount"
        or sd_doc.get("model_version") != anchor["model_version"]
        or sd_doc.get("lambda2") != calibration.get("lambda2")
        or sd_doc.get("lambda3") != calibration.get("lambda3")
    ):
        raise ValueError("Anchor stage discount identity differs")
    sd = StageDiscount(
        float(sd_doc["lambda2"]),
        float(sd_doc["lambda3"]),
        int(sd_doc["n_races_l2"]),
        int(sd_doc["n_races_l3"]),
        bool(sd_doc["fallback"]),
    )
    catalog = FrozenCatalog(SimpleNamespace(**row))
    model = load_serving_model(catalog, anchor["model_version"])
    if (
        model.metadata.get("feature_hash") != anchor.get("feature_hash")
        or model.market_offset is not None
    ):
        raise ValueError("Loaded anchor differs from the frozen profile")
    return anchor, model, sd


# --- paired capture -------------------------------------------------------------------


def _heads(predictions, ids):
    return {
        h: [float(predictions[h].win), float(predictions[h].top2), float(predictions[h].top3)]
        for h in ids
    }


def _frame_sha(frame):
    canon = frame.reset_index(drop=True)
    canon = canon[sorted(canon.columns)]
    text_frame = (
        canon.astype(str)
        if canon.empty
        else canon.map(lambda v: repr(v) if not (isinstance(v, float) and math.isnan(v)) else "nan")
    )
    return hashlib.sha256(
        pd.util.hash_pandas_object(text_frame, index=False).values.tobytes()
    ).hexdigest()


def _race_facts(session, race_id):
    race = session.get(Race, race_id)
    if race is None or race.race_date is None:
        raise ValueError(f"Race {race_id} unknown or without a date")
    n_results = int(
        session.scalar(
            select(func.count()).select_from(RaceResult).where(RaceResult.race_id == race_id)
        )
    )
    start = race.post_time
    if start is not None and start.tzinfo is None:
        raise ValueError("Stored post_time must be timezone-aware")
    return race.race_date, start, n_results


def capture_races(
    session,
    *,
    race_ids,
    classification,
    bundle,
    anchor,
    anchor_model,
    anchor_sd,
    anchor_sha256,
    out_dir,
    regime="preweight",
    now=None,
    feature_rows=None,
):
    """Record one paired prediction per race; the day's feature matrix is built once."""
    if regime not in REGIMES:
        raise ValueError("Primary regime must be preweight or serving")
    facts = {rid: _race_facts(session, rid) for rid in race_ids}
    days = {f[0] for f in facts.values()}
    if len(days) != 1:
        raise ValueError("One capture call serves a single race day")
    (race_date,) = days
    current = now or now_utc()
    for rid in race_ids:
        validate_capture(classification, race_date, facts[rid][1], facts[rid][2], current, current)
    captured_at = now_utc() if now is None else now
    t0 = time.monotonic()
    if feature_rows is None:
        feature_rows = build_feature_matrix(
            session, end_date=race_date, target_race_ids=frozenset(race_ids)
        )
    build_seconds = time.monotonic() - t0
    if "race_date" not in feature_rows.columns:
        # The serving feature matrix carries only model inputs plus race/horse ids; every
        # target race of this call is on the single verified day.
        feature_rows = feature_rows.copy()
        feature_rows["race_date"] = race_date
    elif not (feature_rows.loc[feature_rows.race_id.isin(race_ids), "race_date"] == race_date).all():
        raise ValueError("Feature rows disagree with the verified race day")
    horse_ids = feature_rows.loc[feature_rows.race_id.isin(race_ids), "horse_id"].unique()
    raw_history = load_started_history(session, horse_ids)
    outputs = []
    for rid in race_ids:
        if rid not in set(feature_rows.race_id):
            outputs.append({"race_id": rid, "skipped": "no started rows in feature matrix"})
            continue
        rows, regime_audit = prepare_race_inputs(feature_rows, rid, regime)
        ids = rows.horse_id.tolist()
        history = history_for(rows, raw_history)
        t1 = time.monotonic()
        candidate = predict_mixture(bundle, rid, feature_rows, history, regime)
        candidate_seconds = time.monotonic() - t1
        if candidate.inputs.horse_id.tolist() != ids or not candidate.inputs[rows.columns].equals(
            rows
        ):
            raise ValueError("Candidate inputs diverged from the shared prepared rows")
        t2 = time.monotonic()
        anchor_pred, _snapshots, _expl, anchor_audit = predict_race(
            anchor_model, rid, rows, stage_discount=anchor_sd
        )
        anchor_seconds = time.monotonic() - t2
        if list(anchor_pred) != ids:
            raise ValueError("Anchor started population differs")
        predicted_at = now_utc() if now is None else now
        validate_capture(
            classification, race_date, facts[rid][1], facts[rid][2], captured_at, predicted_at
        )
        input_sha = _frame_sha(rows)
        record = {
            "schema_version": 1,
            "artifact_kind": "mixture_shadow_record",
            "classification": classification,
            "pre_result": facts[rid][2] == 0,
            "race_id": rid,
            "race_day": race_date.isoformat(),
            "scheduled_start": facts[rid][1].isoformat() if facts[rid][1] is not None else None,
            "n_result_rows_at_capture": facts[rid][2],
            "input_captured_at": captured_at.isoformat(),
            "predicted_at": predicted_at.isoformat(),
            "primary_regime": regime,
            "regime_audit": regime_audit,
            "bundle_id": bundle.manifest["bundle_id"],
            "bundle_manifest_sha256": bundle.sha256,
            "anchor_model_sha256": anchor_sha256,
            "anchor_model_version": anchor["model_version"],
            "anchor_stage_discount": anchor["calibration"],
            "candidate_started_ids": ids,
            "anchor_started_ids": list(anchor_pred),
            "candidate_input_sha256": input_sha,
            "anchor_input_sha256": input_sha,
            "history_sha256": _frame_sha(history),
            "n_history_rows": int(len(history)),
            "candidate": _heads(candidate.predictions, ids),
            "anchor": _heads(anchor_pred, ids),
            "candidate_audit": candidate.audit,
            "anchor_audit": anchor_audit,
            "seconds": {
                "feature_build_day": build_seconds,
                "candidate": candidate_seconds,
                "anchor": anchor_seconds,
            },
            "code": code_hashes(),
            "runtime": runtime(),
            "can_adopt": False,
            "eligible_for_verdict": False,
        }
        write_json_new(Path(out_dir) / classification / f"{rid}.json", record)
        outputs.append(
            {
                "race_id": rid,
                "path": str(Path(out_dir) / classification / f"{rid}.json"),
                "n_started": len(ids),
                "seconds": record["seconds"],
            }
        )
    return outputs


def _open_bundle(bundle_path, bundle_sha):
    bundle = load_mixture_bundle(bundle_path, expected_sha256=bundle_sha)
    if bundle.sha256 != bundle_sha:
        raise ValueError("Bundle SHA differs")
    return bundle


# --- development evidence (serving-regime day variance for the power plan) --------------


def development_evidence(session, *, record_dir, bundle_sha, anchor_sha, regime, output):
    """Winner NLL per race from REHEARSAL records with settled unique winners.

    Diagnostic for the power plan's day-cluster variance only; it is not prospective
    evidence and cannot support adoption.
    """
    rows, skipped = [], {}
    for path in sorted(Path(record_dir).glob("*.json")):
        r = read_json(path)
        if (
            r.get("artifact_kind") != "mixture_shadow_record"
            or r.get("bundle_manifest_sha256") != bundle_sha
            or r.get("anchor_model_sha256") != anchor_sha
            or r.get("primary_regime") != regime
        ):
            raise ValueError(f"Record identity/regime differs: {path}")
        if r.get("classification") != "rehearsal":
            raise ValueError("Development evidence uses rehearsal records only")
        winners = session.scalars(
            select(RaceResult.horse_id).where(
                RaceResult.race_id == r["race_id"], RaceResult.finish_order == 1
            )
        ).all()
        if len(winners) != 1 or winners[0] not in r["candidate"]:
            skipped[r["race_id"]] = "no unique started winner"
            continue
        w = winners[0]
        rows.append(
            {
                "race_id": r["race_id"],
                "race_day": r["race_day"],
                "candidate_winner_nll": -math.log(r["candidate"][w][0]),
                "anchor_winner_nll": -math.log(r["anchor"][w][0]),
                "record_sha256": sha256(path),
            }
        )
    if not rows:
        raise ValueError("No settled rehearsal records")
    days = sorted({r["race_day"] for r in rows})
    doc = {
        "artifact_kind": "mixture_power_development",
        "primary_regime": regime,
        "bundle_manifest_sha256": bundle_sha,
        "anchor_model_sha256": anchor_sha,
        "rows": rows,
        "n_races": len(rows),
        "n_days": len(days),
        "days": days,
        "skipped": skipped,
        "mean_difference": float(
            np.mean([r["candidate_winner_nll"] - r["anchor_winner_nll"] for r in rows])
        ),
        "scope": (
            "Rehearsal replay on races after the bundle training cutoff; "
            "day-variance input for power planning only."
        ),
        "code": code_hashes(),
        "created_at": now_utc().isoformat(),
        "can_adopt": False,
        "eligible_for_verdict": False,
    }
    write_json_new(output, doc)
    return doc


# --- CLI -------------------------------------------------------------------------------


def read_only(session):
    """First statement of the session's transaction: the database is never written here."""
    session.execute(text("SET TRANSACTION READ ONLY"))


def _races_on(session, day):
    ids = list(
        session.scalars(select(Race.race_id).where(Race.race_date == day).order_by(Race.race_id))
    )
    if not ids:
        raise ValueError(f"No races on {day}")
    return ids


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database-url", default=None)
    sub = p.add_subparsers(dest="command", required=True)
    fa = sub.add_parser("freeze-anchor")
    fa.add_argument("--model-version", required=True)
    fa.add_argument("--asof", required=True)
    fa.add_argument("--output", type=Path, required=True)
    fa.add_argument("--stage-discount-output", type=Path, required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("--race-id", action="append", default=[])
    cap.add_argument("--date")
    cap.add_argument("--classification", choices=CLASSIFICATIONS, required=True)
    cap.add_argument("--regime", choices=REGIMES, default="preweight")
    cap.add_argument("--bundle", type=Path, required=True)
    cap.add_argument("--bundle-sha256", required=True)
    cap.add_argument("--anchor", type=Path, required=True)
    cap.add_argument("--out-dir", type=Path, required=True)
    dev = sub.add_parser("develop")
    dev.add_argument("--record-dir", type=Path, required=True)
    dev.add_argument("--bundle-sha256", required=True)
    dev.add_argument("--anchor-sha256", required=True)
    dev.add_argument("--regime", choices=REGIMES, default="preweight")
    dev.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)
    engine = create_db_engine(args.database_url)
    with Session(engine) as session:
        read_only(session)
        if args.command == "freeze-anchor":
            doc = freeze_anchor(
                session,
                model_version=args.model_version,
                asof_date=dt.date.fromisoformat(args.asof),
                output=args.output,
                stage_discount_output=args.stage_discount_output,
            )
            print(
                json.dumps(
                    {
                        "anchor": str(args.output),
                        "sha256": sha256(args.output),
                        "calibration": doc["calibration"],
                    },
                    ensure_ascii=False,
                )
            )
        elif args.command == "capture":
            if bool(args.race_id) == bool(args.date):
                p.error("capture takes --race-id (repeatable) or --date, not both")
            race_ids = args.race_id or _races_on(session, dt.date.fromisoformat(args.date))
            bundle = _open_bundle(args.bundle, args.bundle_sha256)
            anchor, anchor_model, anchor_sd = load_anchor(args.anchor)
            out = capture_races(
                session,
                race_ids=race_ids,
                classification=args.classification,
                bundle=bundle,
                anchor=anchor,
                anchor_model=anchor_model,
                anchor_sd=anchor_sd,
                anchor_sha256=sha256(args.anchor),
                out_dir=args.out_dir,
                regime=args.regime,
            )
            for item in out:
                print(json.dumps(item, ensure_ascii=False))
        else:
            doc = development_evidence(
                session,
                record_dir=args.record_dir,
                bundle_sha=args.bundle_sha256,
                anchor_sha=args.anchor_sha256,
                regime=args.regime,
                output=args.output,
            )
            print(
                json.dumps(
                    {k: doc[k] for k in ("n_races", "n_days", "mean_difference", "skipped")},
                    ensure_ascii=False,
                )
            )
        session.rollback()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

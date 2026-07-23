"""Persist a trained predictor: file artifacts + model_versions row (R7, contracts/adoption.md).

No schema change: writes ``model.txt`` / ``calibrator.pkl`` / ``metadata.json`` under
``artifacts/model_versions/{model_version}/`` and upserts the existing ``model_versions``
row (metrics_summary + weights_uri + calibrator_uri). Files are written and the metadata is
assembled BEFORE the DB upsert (filesystem and DB are not one transaction — codex point).

The saved predictor is the *serving* model (trained on the full available history); the
walk-forward fold boundaries that produced ``eval_result`` are recorded in metadata so the
reported metrics stay reproducible/auditable (R7 per-fold-vs-final ambiguity resolved here).
"""

from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import asdict
from pathlib import Path

from horseracing_db.enums import AdoptionStatus
from horseracing_db.models import ModelVersion
from horseracing_eval.harness import EvalResult
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .adoption import AdoptionDecision, AdoptionGate
from .predictor import LightGBMPredictor

MODEL_FAMILY = "lightgbm"
LABEL_SCHEMA = "win_top2_top3"
_LEGACY_SPLIT_UNIT = "race_count_v1"  # Feature 073: pre-073 rows had no explicit split unit


def assert_split_unit_compatible(
    prior_split: str | None, new_split: str | None, *, model_version: str
) -> None:
    """Feature 073 US2 (FR-010): fail closed on a split change under the same model_version.

    ``None`` (pre-073 rows / unset) is treated as the legacy race-count split. First save or an
    unchanged split is a no-op; a differing split raises (a split change must mint a NEW
    model_version, else the parity oracle would be silently overwritten)."""
    prior = prior_split or _LEGACY_SPLIT_UNIT
    new = new_split or _LEGACY_SPLIT_UNIT
    if prior != new:
        raise ValueError(
            f"refusing to overwrite model_version {model_version!r}: stored "
            f"calibration_split_unit={prior!r} != new {new!r}. A split change must use a new "
            "model_version (FR-010)."
        )


def feature_hash(feature_cols: list[str]) -> str:
    return hashlib.sha256("|".join(feature_cols).encode()).hexdigest()


def _write_model(predictor: LightGBMPredictor, path: Path) -> None:
    wm = predictor.win_model_
    if wm is not None and wm.booster_ is not None:
        # binary -> LGBMClassifier (.booster_); cond_logit -> raw lgb.Booster (Feature 039)
        booster = getattr(wm.booster_, "booster_", wm.booster_)
        booster.save_model(str(path))
    else:  # degenerate constant model — no LightGBM booster to serialize
        const = 0.0 if (wm is None or wm._constant is None) else wm._constant
        path.write_text(json.dumps({"degenerate_constant_win": const}))


def build_preprocessor(predictor: LightGBMPredictor, feature_version: str) -> dict:
    """Serving-side preprocessing state (Feature 006): everything needed to rebuild the
    exact model-input matrix outside the training session — feature column order, native
    categorical columns, and the fitted target encoders. Stored as a plain dict (unpickled
    by ``horseracing_serving`` which path-depends on training, so TargetEncoder resolves)."""
    info = predictor.fit_info_ or {}
    fcols = info.get("feature_cols", predictor.feature_cols_ or [])
    prep = {
        "feature_cols": list(fcols),
        "categorical_cols": list(info.get("categorical_cols", [])),
        "target_encode_cols": list(predictor.te_cols_),
        "te_smoothing": predictor.te_smoothing,
        "encoders": dict(predictor.encoders_),  # col -> TargetEncoder (empty if no TE)
        "feature_version": feature_version,
        "feature_hash": feature_hash(fcols),
        "model_degenerate": bool(info.get("model_degenerate")),
        # Feature 039: serving must apply the matching postprocess (binary sigmoid vs
        # cond_logit race-softmax). Default "binary" keeps pre-039 artifacts backward-compatible.
        "objective": info.get("objective", "binary"),
        "postprocess": info.get("postprocess", "sigmoid"),
    }
    # Feature 060: the market-offset definition serving must reconstruct (log-q devig from
    # the target race's own odds). Key ABSENT for every non-offset model — existing artifacts
    # and re-saves of ordinary models stay byte-identical (INV-M3).
    if info.get("market_offset"):
        prep["market_offset"] = dict(info["market_offset"])
    return prep


def save_model_version(
    session: Session,
    *,
    model_version: str,
    predictor: LightGBMPredictor,
    eval_result: EvalResult,
    decision: AdoptionDecision,
    gate: AdoptionGate,
    artifacts_root: Path | str,
    feature_version: str,
    git_sha: str | None = None,
    register_as_candidate: bool = False,
) -> Path:
    """Write artifacts and upsert the model_versions row. Returns the artifacts dir.

    Feature 060: ``register_as_candidate=True`` pins the row to CANDIDATE even when the
    decision passed — accuracy-first models never auto-activate (FR-006); promotion to
    default is a separate explicit user decision. Default False keeps the pre-060
    pass->ACTIVE behaviour byte-identical."""
    # Feature 079 (codex #12): an EV-weighted predictor is a retrospective, artifact-only
    # kill-test and must NEVER be persisted as a servable model_version (candidate or active) —
    # 057 lets non-active models be selected, so a registry row is not isolation. (This is
    # narrower than is_leaky_reference: a 060 market-offset model is a legitimate candidate.)
    if getattr(predictor, "ev_weight", False):
        raise ValueError(
            "refusing to persist an EV-weighted predictor as a model_version "
            "(079 is artifact-only; a registry row would breach isolation) — fail-closed"
        )
    info = predictor.fit_info_ or {}
    fcols = info.get("feature_cols", predictor.feature_cols_ or [])

    # Resolve to an ABSOLUTE path before deriving the URIs persisted below. weights_uri /
    # calibrator_uri are read back by the serving CLI, which the ops predict job shells out to with
    # cwd=serving/ (ops/runner.py). A bare-relative --artifacts-dir (the CLI default is "artifacts")
    # would store a relative URI that resolves to serving/artifacts/... under that cwd and fail with
    # "metadata.json missing". Storing absolute makes the URI resolve from any cwd. Do NOT revert to
    # a relative path here.
    root = Path(artifacts_root).resolve()
    art_dir = root / "model_versions" / model_version
    art_dir.mkdir(parents=True, exist_ok=True)
    model_path = art_dir / "model.txt"
    calib_path = art_dir / "calibrator.pkl"
    prep_path = art_dir / "preprocessor.pkl"
    meta_path = art_dir / "metadata.json"

    # 1. artifacts to disk
    _write_model(predictor, model_path)
    with calib_path.open("wb") as fh:
        pickle.dump(predictor.calibrator_, fh)
    with prep_path.open("wb") as fh:  # Feature 006 serving: preprocessing state
        pickle.dump(build_preprocessor(predictor, feature_version), fh)

    metadata = {
        "model_version": model_version,
        "model_family": MODEL_FAMILY,
        "objective": info.get("objective", "binary"),  # Feature 039
        "postprocess": info.get("postprocess", "sigmoid"),
        "seed": info.get("seed"),
        "params": info.get("params"),
        "calibration": info.get("calibration"),
        "calibration_split_unit": info.get("calibration_split_unit"),  # Feature 073 US2 (FR-009)
        "calibrator_params": predictor.calibrator_.params_dict() if predictor.calibrator_ else None,
        "fold_boundaries": list(eval_result.valid_years),
        "feature_version": feature_version,
        "feature_hash": feature_hash(fcols),
        "target_encode_cols": list(predictor.te_cols_),  # serving backward-compat detection
        "te_smoothing": predictor.te_smoothing if predictor.te_cols_ else None,
        "git_sha": git_sha,
        "train_through": info.get("train_through"),
        "n_model_rows": info.get("n_model_rows"),
        "n_calib_rows": info.get("n_calib_rows"),
        "model_fit_through": info.get("model_fit_through"),  # Feature 068 US3 (FR-015)
        "calib_from": info.get("calib_from"),
        "calib_through": info.get("calib_through"),
        "model_degenerate": info.get("model_degenerate"),
        "calibrator_degenerate": info.get("calibrator_degenerate"),
        "adoption": {"adopted": decision.adopted, **asdict(gate), "reasons": decision.reasons},
    }
    # Feature 060: market-offset definition + closing-leaning limitation (FR-008). Key absent
    # for ordinary models (INV-M3: their metadata stays byte-identical).
    if info.get("market_offset"):
        metadata["market_offset"] = dict(info["market_offset"])
        metadata["market_offset_excluded_races"] = info.get("market_offset_excluded_races")
    meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=True, default=str))

    # 2. metrics_summary (eval shape + training meta) -> DB
    summary = eval_result.to_summary()
    # Feature 040 US2: split-gain feature importance for display (/models/{mv}/importance).
    # Absent (key omitted) for degenerate models -> API returns typed 404 importance_unavailable.
    if predictor.win_model_ is not None:
        gain = predictor.win_model_.gain_importance()
        if gain is not None:
            summary["importance"] = {"type": "gain", "values": gain}
    summary["training"] = {
        "model_family": MODEL_FAMILY,
        "objective": info.get("objective", "binary"),  # Feature 039
        "feature_version": feature_version,
        "feature_hash": feature_hash(fcols),
        "seed": info.get("seed"),
        "calibration": info.get("calibration"),
        "git_sha": git_sha,
        "adoption": metadata["adoption"],
        # Feature 050 (V): training-data window in the DB, not only in the on-disk metadata.json —
        # "what did this model train on, through when" must be answerable from model_versions alone.
        "train_through": str(info["train_through"]) if info.get("train_through") else None,
        "n_model_rows": info.get("n_model_rows"),
        "n_calib_rows": info.get("n_calib_rows"),
        # Feature 068 US3 (FR-015): booster's actual last-learned day + calib window, so a
        # calib-holdout model's model_fit_through < train_through is visible from the DB alone.
        "model_fit_through": (
            str(info["model_fit_through"]) if info.get("model_fit_through") else None
        ),
        "calib_from": str(info["calib_from"]) if info.get("calib_from") else None,
        "calib_through": str(info["calib_through"]) if info.get("calib_through") else None,
        # Feature 073 US2 (FR-009): the calibration split unit is visible from model_versions alone.
        "calibration_split_unit": info.get("calibration_split_unit"),
    }
    if info.get("market_offset"):  # Feature 060: visible from model_versions alone (V)
        summary["training"]["market_offset"] = dict(info["market_offset"])

    # Feature 073 US2 (FR-010): fail closed if a model_version already exists with a DIFFERENT
    # calibration split unit (a split change must mint a new model_version, not overwrite one).
    existing = session.get(ModelVersion, model_version)
    if existing is not None:
        prior_split = ((existing.metrics_summary or {}).get("training") or {}).get(
            "calibration_split_unit"
        )
        assert_split_unit_compatible(
            prior_split, info.get("calibration_split_unit"), model_version=model_version
        )

    status = AdoptionStatus.ACTIVE if (
        decision.adopted and not register_as_candidate
    ) else AdoptionStatus.CANDIDATE
    values = dict(
        model_version=model_version,
        model_family=MODEL_FAMILY,
        feature_version=feature_version,
        label_schema=LABEL_SCHEMA,
        adoption_status=str(status),
        metrics_summary=summary,
        weights_uri=str(model_path),
        calibrator_uri=str(calib_path),
    )
    stmt = insert(ModelVersion).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["model_version"],
        set_={k: values[k] for k in values if k != "model_version"},
    )
    session.execute(stmt)
    session.commit()
    return art_dir

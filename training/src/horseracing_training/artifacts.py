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
from dataclasses import asdict, dataclass
from pathlib import Path

from horseracing_db.enums import AdoptionStatus
from horseracing_db.models import ModelVersion
from horseracing_eval.harness import EvalResult
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .adoption import AdoptionDecision, AdoptionGate, evaluate_promotion
from .predictor import LightGBMPredictor

MODEL_FAMILY = "lightgbm"
LABEL_SCHEMA = "win_top2_top3"
_LEGACY_SPLIT_UNIT = "race_count_v1"  # Feature 073: pre-073 rows had no explicit split unit


def assert_split_unit_compatible(
    prior_split: str | None,
    new_split: str | None,
    *,
    model_version: str,
    prior_protocol: str | None = None,
    new_protocol: str | None = None,
) -> None:
    """Feature 073 US2 (FR-010): fail closed on a split change under the same model_version.

    ``None`` (pre-073 rows / unset) is treated as the legacy race-count split. First save or an
    unchanged split is a no-op; a differing split raises (a split change must mint a NEW
    model_version, else the parity oracle would be silently overwritten)."""
    # Feature 085: a protocol-calibrated model (arm E) legitimately has split_unit=None. Without
    # this, None collapses to the legacy default and an OOF-calibrated model could overwrite a
    # 70/30 holdout model under the same model_version without the guard firing — the exact
    # silent-overwrite this check exists to stop.
    prior = prior_protocol or prior_split or _LEGACY_SPLIT_UNIT
    new = new_protocol or new_split or _LEGACY_SPLIT_UNIT
    if prior != new:
        raise ValueError(
            f"refusing to overwrite model_version {model_version!r}: stored "
            f"calibration_split_unit={prior!r} != new {new!r}. A split change must use a new "
            "model_version (FR-010)."
        )


def categorical_vocab_from_booster(
    booster, feature_cols: list[str], categorical_cols: list[str]
) -> dict[str, list[str]]:
    """Feature 098 (INV-R3): the ORDERED category vocabulary the booster was trained with, per
    categorical column. LightGBM stores ``pandas_categorical`` (one list per pandas-category
    column, in DataFrame column order) in the model file; we re-key it by column name using the
    same order training used (categorical columns in ``feature_cols`` order)."""
    ordered = [c for c in feature_cols if c in set(categorical_cols)]
    stored = getattr(booster, "pandas_categorical", None) or []
    if len(stored) != len(ordered):
        raise ValueError(
            f"pandas_categorical has {len(stored)} lists but {len(ordered)} categorical columns"
        )
    return {col: [str(v) for v in vals] for col, vals in zip(ordered, stored, strict=True)}


def vocab_hash(vocab: dict[str, list[str]]) -> str:
    """sha256 over the ordered vocabulary (column order and value order both matter)."""
    payload = json.dumps(vocab, ensure_ascii=False, sort_keys=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def feature_hash(feature_cols: list[str]) -> str:
    return hashlib.sha256("|".join(feature_cols).encode()).hexdigest()


@dataclass(frozen=True)
class Servability:
    """Whether an artifact's feature schema may be served under the current registry.

    ``exact``   — the artifact IS the current schema (fast path, no column selection needed).
    ``servable`` — exact, or an older version explicitly pinned in
                   ``COMPATIBLE_PRIOR_FEATURE_VERSIONS`` with a matching hash (compat path).
    """

    exact: bool
    servable: bool
    current_hash: str


def resolve_servability(
    trained_fv: str | None,
    trained_hash: str | None,
    *,
    current_fv: str | None = None,
    current_hash: str | None = None,
    compat_table: dict[str, dict[str, str]] | None = None,
) -> Servability:
    """THE servability predicate. Every caller must go through this one function.

    Two places used to implement this independently — the real gate in
    ``serving.model_loader.load_serving_model`` and the preflight in
    ``training.promote._artifact_problems`` — and they DRIFTED: the preflight accepted an
    artifact on hash equality alone, while the loader also requires the feature_version to
    match. An artifact with the current column names but an older ``feature_version`` therefore
    passed promotion and was rejected by the loader seconds later, stopping every prediction
    (2026-07: 37/37 predict jobs failed). Sharing one predicate is what keeps that closed.

    Why ``exact`` needs BOTH conditions: ``feature_hash`` covers only the ordered column NAMES,
    never their value semantics. A same-column, value-CHANGING bump (Feature 017 changed
    class_transition/track_type values; Feature 098 canonicalises race_class) leaves the hash
    identical, so hash equality alone would feed an old model inputs it was never trained on.

    Why a same-version hash MISMATCH stays closed: a ``drop_features`` ablation build carries the
    current ``feature_version`` but a subset column set. ``is_feature_version_servable``
    deliberately does not special-case ``trained_fv == current_fv`` (see its docstring), so such
    an artifact is not servable — and this function must not soften that.
    """
    from horseracing_features.registry import (
        FEATURE_VERSION,
        is_feature_version_servable,
        model_input_features,
    )

    # 「現在の世界」は 3 つとも注入できる。serving は自分の module 名前空間の値を渡す:
    # そこを registry から直に読むと、loader の名前を差し替えるテストや将来の間接化を
    # 素通りしてしまい、共有したはずの述語が呼び出し元と違う世界を見ることになる。
    fv = FEATURE_VERSION if current_fv is None else current_fv
    chash = feature_hash(model_input_features()) if current_hash is None else current_hash
    exact = trained_hash == chash and trained_fv == fv
    servable = exact or is_feature_version_servable(
        trained_fv or "", trained_hash, fv, compat_table=compat_table
    )
    return Servability(exact=exact, servable=servable, current_hash=chash)


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
        "race_class_representation": info.get("race_class_representation", "raw"),
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



class DurableArtifactRoot(RuntimeError):
    """The artifacts root would not survive, so refuse to register a model that points at it."""


def check_artifact_root(artifacts_root) -> Path:
    """Validate the artifacts root BEFORE writing anything, and return it resolved.

    A model row outlives the command that created it, so the location it points at has to outlive
    that command too. Two ways that failed in practice, neither of which an
    "does the file exist?" check would have caught — the files were present both times:

    * **Registered from a git worktree.** `lgbm-091-wmask` stored
      `.claude/worktrees/090-.../artifacts/...`; the worktree was removed later and every
      production prediction failed on the missing calibrator. A linked worktree is detectable
      because its `.git` is a FILE (`gitdir: ...`) rather than a directory — which is precisely
      the statement "this checkout is disposable".
    * **Root passed one level too deep.** The layout below is `<root>/model_versions/<version>`,
      so passing `.../artifacts/model_versions` nests it twice. Self-consistent, therefore silent.
    """
    root = Path(artifacts_root)
    if not root.is_absolute():
        raise DurableArtifactRoot(
            f"artifacts root must be absolute (got {str(root)!r}). The URIs are read back by the "
            "serving CLI, which runs with cwd=serving/, so a relative path resolves somewhere "
            "else entirely."
        )
    root = root.resolve()

    if root.name == "model_versions":
        raise DurableArtifactRoot(
            f"artifacts root must not end in 'model_versions' (got {str(root)!r}): this function "
            f"appends 'model_versions/<version>' itself, so the artifacts would land in a doubled "
            f"path. Pass {str(root.parent)!r} instead."
        )

    for parent in (root, *root.parents):
        dot_git = parent / ".git"
        if dot_git.is_file():
            raise DurableArtifactRoot(
                f"refusing to register artifacts under the git worktree at {str(parent)!r}: the "
                "model row would outlive the worktree, and removing the worktree deletes the "
                "artifacts out from under production (this is how the active model's calibrator "
                "was lost). Write to the main checkout's artifacts directory instead."
            )
        if dot_git.is_dir():
            break

    return root


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
    verdict: dict | None = None,
) -> Path:
    """Write artifacts and upsert the model_versions row. Returns the artifacts dir.

    Feature 060: ``register_as_candidate=True`` pins the row to CANDIDATE even when the
    decision passed — accuracy-first models never auto-activate (FR-006); promotion to
    default is a separate explicit user decision. Default False keeps the pre-060
    pass->ACTIVE behaviour byte-identical.

    2026-08: going ACTIVE now also requires ``verdict`` — a v3 evaluation report (either report
    shape) that is verdict-eligible, says ADOPT, and has FULL subgroup assurance. Without it the
    row is saved as a CANDIDATE and the reason is recorded in ``metrics_summary["promotion"]``.
    The legacy ``decision`` gate is four point-estimate comparisons with no paired design, CI,
    subgroup guard or artifact isolation, so on its own it cannot justify activating a model."""
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
    race_class_representation = info.get("race_class_representation", "raw")

    # Feature 073 US2 (FR-010): fail closed if a model_version already exists with a DIFFERENT
    # calibration split unit (a split change must mint a new model_version, not overwrite one).
    # This check MUST run before any disk write: raising after the artifact files are replaced
    # would leave the existing model_version's on-disk booster/calibrator overwritten while the
    # DB row still carries the old metrics (serving would load new weights under old metadata).
    existing = session.get(ModelVersion, model_version)
    if existing is not None:
        prior_split = ((existing.metrics_summary or {}).get("training") or {}).get(
            "calibration_split_unit"
        )
        prior_protocol = (
            ((existing.metrics_summary or {}).get("training") or {})
            .get("calibration_protocol", {}) or {}
        ).get("protocol")
        assert_split_unit_compatible(
            prior_split, info.get("calibration_split_unit"), model_version=model_version,
            prior_protocol=prior_protocol,
            new_protocol=(info.get("calibration_protocol") or {}).get("protocol"),
        )

    fitted_booster = None if predictor.win_model_ is None else predictor.win_model_.booster_
    booster = (
        None if fitted_booster is None else getattr(fitted_booster, "booster_", fitted_booster)
    )
    categorical_cols = list(info.get("categorical_cols", []))
    categorical_vocab = (
        {}
        if booster is None
        else categorical_vocab_from_booster(booster, fcols, categorical_cols)
    )

    # Resolve to an ABSOLUTE path before deriving the URIs persisted below. weights_uri /
    # calibrator_uri are read back by the serving CLI, which the ops predict job shells out to with
    # cwd=serving/ (ops/runner.py). A bare-relative --artifacts-dir (the CLI default is "artifacts")
    # would store a relative URI that resolves to serving/artifacts/... under that cwd and fail with
    # "metadata.json missing". Storing absolute makes the URI resolve from any cwd. Do NOT revert to
    # a relative path here.
    root = check_artifact_root(artifacts_root)
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
        # Feature 091: the race-atomic weight mask used for THIS fit (None when the mechanism was
        # not used). Recorded so a served model states which input regime it was trained for.
        "weight_mask": info.get("weight_mask"),
        "calibrator_params": predictor.calibrator_.params_dict() if predictor.calibrator_ else None,
        "fold_boundaries": list(eval_result.valid_years),
        "feature_version": feature_version,
        "feature_hash": feature_hash(fcols),
        "race_class_representation": race_class_representation,
        "categorical_vocab": categorical_vocab,
        "categorical_vocab_hash": vocab_hash(categorical_vocab),
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
    # Feature 085 (arm E): a model whose calibrator was fitted on strict-past OOF rows has no
    # contiguous calibration window, so calib_from/through/split_unit are legitimately null. This
    # block is what distinguishes "no calibration" from "calibrated by a protocol the window
    # vocabulary cannot describe". Key absent for ordinary models (their metadata is unchanged).
    if info.get("calibration_protocol"):
        metadata["calibration_protocol"] = dict(info["calibration_protocol"])
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
        "race_class_representation": race_class_representation,
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
    if info.get("calibration_protocol"):  # Feature 085: same, for the arm E protocol
        summary["training"]["calibration_protocol"] = dict(info["calibration_protocol"])

    promotion = evaluate_promotion(
        legacy=decision, verdict=verdict, register_as_candidate=register_as_candidate
    )
    summary["promotion"] = {
        "promotable": promotion.promotable, "status": promotion.status,
        "reasons": promotion.reasons,
    }
    status = AdoptionStatus.ACTIVE if promotion.promotable else AdoptionStatus.CANDIDATE
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

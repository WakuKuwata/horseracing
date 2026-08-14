"""Train arm E in the standard artifact shape and register it as a CANDIDATE (085 §5/§9-4).

arm E measured winner NLL −0.012838, CI [−0.014835, −0.010920], all gates PASS including the
calibration band that killed arm C/D (`out/085-armE-verdict.json`). That verdict unlocks §9 step 4
— build the shippable model — and NOT step 6 (promotion), which needs the prospective holdout.

The model shipped here is a FRESH full-history fit, exactly like every other production model
(`cli._train_evaluate` does `final = _make(); final.fit(all_races)`). It is not any evaluation
fold's booster: the walk-forward fits exist to measure, not to ship.

**What the parity check does and does not prove.** arm E never persisted a prediction set, so
there is nothing to compare the shipped model against fold-for-fold. What IS checkable, and what
actually carries the risk, is the ROUND TRIP: fit -> graft the OOF isotonic -> serialise ->
reload through the serving loader -> predict. That is asserted byte-for-byte here. It does not
establish that this model equals the evaluated one; no such object exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from horseracing_db.models import ModelVersion
from horseracing_eval.dataset import load_eval_races
from horseracing_features.registry import FEATURE_VERSION
from sqlalchemy.orm import Session

from .artifacts import save_model_version
from .calib_split import ArmNotServable, OofCalibratedPredictor
from .recipe import ModelRecipe

#: The verdict this build is authorised by. Recorded so a registry row can be traced to evidence.
SOURCE_VERDICT = "out/085-armE-verdict.json"
PROTOCOL = "strict_past_oof_isotonic_v1"

#: Byte parity must be EXACT. A "close enough" tolerance would hide precisely the
#: serialisation drift this check exists to catch.
PARITY_TOLERANCE = 0.0


class ArmERegisterError(RuntimeError):
    """Refuse to register rather than register a model that misdescribes itself."""


@dataclass(frozen=True)
class ArmEReport:
    model_version: str
    n_races: int
    n_oof_rows: int
    threshold_checksum: str
    parity_probes: int
    parity_max_abs_diff: float
    artifacts_dir: str

    def to_dict(self) -> dict[str, Any]:
        return vars(self) | {}


def _parity_check(servable, *, artifacts_dir: str, model_version: str) -> tuple[int, float]:
    """Reload the WRITTEN artifacts and require an exact match against the in-memory model.

    Reads the files with lightgbm/pickle directly rather than through
    ``horseracing_serving.model_loader``. That is not a shortcut — ``serving`` DECLARES
    ``horseracing-training``, so importing serving from here is a reverse dependency. A lazy
    import does not avoid it; it only defers ModuleNotFoundError to runtime, which is exactly how
    this was first discovered. (The same shape was found in eval->probability earlier and fixed by
    injection; here the dependency simply must not exist.)

    Scope, stated honestly: this proves SERIALISATION FIDELITY — the grafted OOF isotonic and the
    full-history booster survive the write/read round trip byte-for-byte. It does NOT prove that
    the serving loader accepts the model_version; the feature-hash / feature-version gates live in
    serving and have broken serving before. That check belongs in a layer that may import serving.
    """
    import pickle  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    import lightgbm as lgb  # noqa: PLC0415

    art = Path(artifacts_dir) / "model_versions" / model_version
    booster = lgb.Booster(model_file=str(art / "model.txt"))
    with (art / "calibrator.pkl").open("rb") as fh:
        calibrator = pickle.load(fh)  # noqa: S301 — our own artifact, written moments ago

    # Compare the BOOSTERS directly (raw tree sum). WinModel.predict applies the objective's
    # post-processing and needs race groups; the artifact question is narrower — did the trees
    # survive serialisation — so both sides are scored with raw_score=True on the same matrix.
    # A plain ndarray, NOT a DataFrame: these boosters carry categorical features and LightGBM
    # rejects a frame whose categorical_feature set does not match training ("train and valid
    # dataset categorical_feature do not match"). The ndarray path skips that check, which is
    # right here — the probe asks whether the TREES round-tripped, not whether a frame binds.
    rng = np.random.default_rng(85)
    x = rng.normal(size=(256, booster.num_feature()))
    fitted = servable.win_model_.booster_
    a = np.asarray(fitted.predict(x, raw_score=True), dtype=float)
    b = np.asarray(booster.predict(x, raw_score=True), dtype=float)
    booster_diff = float(np.max(np.abs(a - b)))

    probe = np.linspace(1e-6, 1.0 - 1e-6, 1001)
    ca = np.asarray(servable.calibrator_.transform(probe), dtype=float)
    cb = np.asarray(calibrator.transform(probe), dtype=float)
    calib_diff = float(np.max(np.abs(ca - cb)))

    worst = max(booster_diff, calib_diff)
    if worst != PARITY_TOLERANCE:
        raise ArmERegisterError(
            f"round-trip parity failed (booster {booster_diff!r}, calibrator {calib_diff!r}); "
            "both must be exactly 0"
        )
    return len(probe), worst


def run(
    session: Session,
    *,
    model_version: str,
    artifacts_dir: str,
    n_oof_blocks: int = 8,
    seed: int = 42,
    weight_mask_rate: float | None = None,
    weight_mask_seed: int | None = None,
    num_threads: int | None = None,
) -> dict[str, Any]:
    races = load_eval_races(session)
    if not races:
        raise ArmERegisterError("no eligible races — refusing to build")
    contexts = [er.context for er in races]

    # The recipe must be the one in production, not a fixed guess. When arm E was first built
    # the active recipe had no weight mask, so hardcoding was harmless; feature 091 added one and
    # a hardcoded recipe would now register a full-history booster that silently predates it —
    # and the prospective holdout would then be measuring arm E + the missing mask at once,
    # attributing both to arm E.
    recipe = ModelRecipe(
        objective="pl_topk", calibration="isotonic", calib_frac=0.3, seed=seed,
        weight_mask_rate=weight_mask_rate, weight_mask_seed=weight_mask_seed,
    )
    predictor = OofCalibratedPredictor(
        session,
        recipe,
        n_oof_blocks=n_oof_blocks,
        method="isotonic",
        # Fail closed: an insufficient OOF sample must abort the build, never fall back to an
        # identity calibrator that would then be registered under the protocol's name.
        require_sufficient=True,
    )
    predictor.fit(contexts, num_threads=num_threads)

    try:
        servable = predictor.to_servable()
    except ArmNotServable as exc:
        raise ArmERegisterError(f"arm E is not shippable: {exc}") from exc

    info = servable.fit_info_ or {}
    protocol = info.get("calibration_protocol") or {}
    if protocol.get("protocol") != PROTOCOL:
        raise ArmERegisterError(f"unexpected calibration protocol {protocol.get('protocol')!r}")

    art = save_model_version(
        session,
        model_version=model_version,
        predictor=servable,
        eval_result=_evidence_only_result(),
        decision=_no_gate_decision(),
        gate=_no_gate(),
        artifacts_root=artifacts_dir,
        feature_version=FEATURE_VERSION,
        git_sha=None,
        # 085 §5: never auto-activate. Promotion waits on the prospective holdout (§7).
        register_as_candidate=True,
    )

    # save_model_version commits. A parity failure after that would leave a CANDIDATE row that
    # 057 can serve and that nothing verified — the precise state this check exists to prevent —
    # so the row is removed before the error escapes.
    try:
        checked, worst = _parity_check(
            servable, artifacts_dir=artifacts_dir, model_version=model_version
        )
    except Exception:
        row = session.get(ModelVersion, model_version)
        if row is not None:
            session.delete(row)
            session.commit()
        raise
    return ArmEReport(
        model_version=model_version,
        n_races=len(contexts),
        n_oof_rows=int(protocol.get("n_oof_rows") or 0),
        threshold_checksum=str(protocol.get("threshold_checksum")),
        parity_probes=checked,
        parity_max_abs_diff=worst,
        artifacts_dir=str(art),
    ).to_dict()


def _evidence_only_result():
    """A metrics envelope that does NOT restate the arm E verdict.

    The verdict lives in `out/085-armE-verdict.json`, produced by the paired harness against a
    frozen gate config. Recomputing or paraphrasing it here would create a second, unreviewed
    source of truth for the same claim, and a registry row is not the place to mint evidence.
    """
    from horseracing_eval.harness import EvalResult  # noqa: PLC0415

    return EvalResult(
        scheme="none_registered_from_085_verdict",
        valid_years=[],
        tolerance={},
        ece_bins=0,
        overall={},
        by_fold=[],
        by_field_size_ece={},
    )


def _no_gate():
    from .adoption import AdoptionGate  # noqa: PLC0415

    return AdoptionGate(ece_threshold=0.0)


def _no_gate_decision():
    """arm E is registered on the strength of the 085 verdict, not a fresh gate run."""
    from .adoption import AdoptionDecision  # noqa: PLC0415

    return AdoptionDecision(
        adopted=False,
        reasons={
            "registered_as": "candidate",
            "authorised_by": SOURCE_VERDICT,
            "promotion_blocked_on": (
                "085 section 7 prospective holdout on races after 2026-07-12 (development "
                "evidence cannot promote an arm conceived after seeing this window)"
            ),
        },
    )

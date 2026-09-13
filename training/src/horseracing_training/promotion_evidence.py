"""Bind a confirmed comparison to actual fit settings and registered artifact bytes.

This is provenance, not another statistical gate. Historical recipe hashes and verdicts remain
unchanged. A walk-forward booster is not a final-fit booster: their training CONTRACTS must agree,
while the final artifact is checked against the hashes recorded when it was registered.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from horseracing_eval.decision import assert_confirmatory, gate_config_hash
from horseracing_eval.hashing import stable_hash

CONTRACT_VERSION = "fitted_training_contract_v1"
EVIDENCE_VERSION = "promotion_evidence_v1"


def _plain(value):
    return json.loads(json.dumps(value, allow_nan=False, sort_keys=True, default=str))


def fitted_contract(predictor, *, feature_version: str) -> dict | None:
    """Extract effective settings, never a user-supplied recipe label or intended parameters.

    Missing fit metadata cannot establish identity. Older/unsupported builders still register as
    candidates and retain the explicit override path; no identity is invented for them.
    """
    from .calib_split import OofCalibratedPredictor
    from .predictor import LightGBMPredictor

    oof = type(predictor) is OofCalibratedPredictor
    if oof:
        if predictor.method != "isotonic" or type(predictor._base) is not LightGBMPredictor:
            return None
        base = predictor._base
    elif type(predictor) is LightGBMPredictor:
        base = predictor
    else:
        return None
    info = base.fit_info_ or {}
    required = (
        "seed",
        "objective",
        "params",
        "feature_cols",
        "categorical_cols",
        "race_class_representation",
        "target_encode_cols",
        "te_smoothing",
        "calib_frac",
        "calibration",
        "calibration_split_unit",
        "weight_mask",
    )
    if any(k not in info for k in required) or not info["feature_cols"] or not info["params"]:
        return None
    if (
        getattr(base, "is_leaky_reference", False)
        or base.market_offset
        or base.ev_weight
        or info.get("market_offset")
        or info.get("ev_weight")
        or info.get("margin_teacher")
    ):
        return None
    protocol = info.get("calibration_protocol") or {}
    if oof:
        calibration = {
            "method": "isotonic_strict_past_oof",
            "calib_frac": 0.0,
            "split_unit": None,
            "n_oof_blocks": predictor.n_oof,
        }
    elif protocol:
        if protocol.get("protocol") != "strict_past_oof_isotonic_v1":
            return None
        calibration = {
            "method": info["calibration"],
            "calib_frac": 0.0,
            "split_unit": None,
            "n_oof_blocks": protocol.get("n_oof_blocks"),
        }
        if not isinstance(calibration["n_oof_blocks"], int):
            return None
    else:
        calibration = {
            "method": info["calibration"],
            "calib_frac": info["calib_frac"],
            "split_unit": info["calibration_split_unit"],
            "n_oof_blocks": None,
        }
    actual = {
        k: info[k]
        for k in (
            "seed",
            "objective",
            "params",
            "feature_cols",
            "categorical_cols",
            "race_class_representation",
            "target_encode_cols",
            "te_smoothing",
            "weight_mask",
        )
    }
    if base.hpo:
        from .hpo import DEFAULT_GRID
        from .win_model import DEFAULT_PARAMS

        grid = DEFAULT_GRID if base.param_grid is None else base.param_grid
        candidates = [{**DEFAULT_PARAMS, **p} for p in grid]
        if info["params"] not in candidates:
            return None
        # The selection procedure is fixed, but different outer folds/final-fit may correctly
        # choose different candidates. Compare the search space, not that data-dependent choice.
        actual["params"] = {"hpo_candidates": candidates}
    value = _plain(
        {
            "version": CONTRACT_VERSION,
            "feature_version": feature_version,
            **actual,
            "calibration": calibration,
            "ece_clip": base.ece_clip,
            "hpo": {
                "enabled": base.hpo,
                "param_grid": base.param_grid,
                "n_splits": base.hpo_splits,
            },
            "recency_half_life_days": base.recency_half_life_days,
            "weight_scope": base.weight_scope,
        }
    )
    return {"contract": value, "sha256": stable_hash(value)}


def valid_contract(value) -> bool:
    try:
        return (
            isinstance(value, dict)
            and isinstance(value.get("contract"), dict)
            and value["contract"].get("version") == CONTRACT_VERSION
            and value.get("sha256") == stable_hash(value["contract"])
        )
    except (TypeError, ValueError):
        return False


def _confirmed_config(cfg, expected_hash, window):
    assert_confirmatory(cfg, expected_hash=expected_hash, eval_window=window)
    sd = (cfg.get("seed_noise") or {}).get("sd_fold")
    try:
        valid_sd = (
            not isinstance(sd, bool)
            and isinstance(sd, (int, float))
            and math.isfinite(sd)
            and sd > 0
        )
    except OverflowError:
        valid_sd = False
    if not valid_sd:
        raise ValueError("seed-noise SD must be a finite positive number")


def stamp_report(
    report: dict,
    candidate,
    active,
    *,
    cfg: dict | None,
    confirmed: bool,
    expected_hash: str | None,
    window: dict | None,
    feature_version: str,
) -> dict:
    """New CLI outputs explicitly distinguish exploratory and confirmed evidence.

    Called only after evaluation. Re-checking the existing confirmation contract here prevents a
    future caller from making a report eligible by merely setting a truthy flag.
    """
    out = dict(report)
    kind = out.get("artifact_kind", "full_walk_forward" if confirmed else "exploratory")
    if confirmed is not True and kind == "full_walk_forward":
        kind = "exploratory"
    eligible = (
        confirmed is True
        and kind == "full_walk_forward"
        and out.get("eligible_for_verdict", True) is True
        and out.get("can_adopt", True) is True
    )
    out.update(artifact_kind=kind, eligible_for_verdict=eligible, can_adopt=eligible)
    if not eligible:
        out["promotion_evidence"] = {"version": EVIDENCE_VERSION, "confirmatory": False}
        return out
    _confirmed_config(cfg, expected_hash, window)
    arms = {}
    for role, factory in (("candidate", candidate), ("active", active)):
        identity = fitted_contract(getattr(factory, "_pred", None), feature_version=feature_version)
        arms[role] = {
            "recipe_hash": factory.recipe_hash,
            "recipe_meta": _plain(factory.recipe_meta),
            "fitted_contract": identity,
        }
        # RegimeReport did not previously carry the recipe identities. Add them to new outputs.
        out[f"{role}_recipe_hash"] = factory.recipe_hash
        out[f"{role}_recipe_meta"] = _plain(factory.recipe_meta)
    out["gate_config_hash"] = gate_config_hash(cfg)
    out["promotion_evidence"] = {
        "version": EVIDENCE_VERSION,
        "confirmatory": True,
        "gate_config": _plain(cfg),
        "gate_config_hash": expected_hash,
        "eval_window": _plain(window),
        **arms,
    }
    return out


def evidence_problem(report: dict, candidate_contract, active_contract) -> str | None:
    """Validate report provenance and BOTH actual deployment/reference training contracts."""
    proof = report.get("promotion_evidence")
    if not isinstance(proof, dict) or proof.get("version") != EVIDENCE_VERSION:
        return "promotion_evidence_missing"
    if proof.get("confirmatory") is not True:
        return "verdict_not_confirmatory"
    try:
        cfg = proof["gate_config"]
        _confirmed_config(cfg, proof["gate_config_hash"], proof["eval_window"])
        if report.get("gate_config_hash") != proof["gate_config_hash"]:
            return "verdict_gate_config_mismatch"
        version = report.get("evaluation_contract_version") or (
            report.get("gate_config") or {}
        ).get("evaluation_contract_version")
        if version != cfg["evaluation_contract_version"]:
            return "verdict_contract_mismatch"
        for role, expected in (("candidate", candidate_contract), ("active", active_contract)):
            arm = proof[role]
            if not isinstance(arm, dict) or not valid_contract(arm.get("fitted_contract")):
                return f"{role}_fit_identity_missing"
            if (
                not arm.get("recipe_hash")
                or arm.get("recipe_hash") != report.get(f"{role}_recipe_hash")
                or arm.get("recipe_meta") != _plain(report.get(f"{role}_recipe_meta"))
            ):
                return f"{role}_report_recipe_mismatch"
            if not valid_contract(expected):
                return f"{role}_registered_identity_missing"
            if arm["fitted_contract"] != expected:
                return f"{role}_training_contract_mismatch"
    except (KeyError, TypeError, ValueError, AttributeError, RuntimeError):
        return "promotion_evidence_invalid"
    return None


def artifact_hashes(weights_uri, calibrator_uri) -> dict:
    weights, calibrator = Path(weights_uri), Path(calibrator_uri)
    paths = {
        "weights": weights,
        "calibrator": calibrator,
        "preprocessor": weights.parent / "preprocessor.pkl",
        "metadata": weights.parent / "metadata.json",
    }
    if not all(p.is_absolute() and p.is_file() for p in paths.values()):
        raise ValueError("registered artifact paths must be absolute existing files")
    return {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in paths.items()}


def registered_contract(row) -> tuple[dict | None, str | None]:
    """Verify registry identity against metadata and on-disk bytes without unpickling anything."""
    try:
        identity = ((row.metrics_summary or {}).get("training") or {}).get("promotion_identity")
        if not isinstance(identity, dict) or not valid_contract(identity.get("fitted_contract")):
            return None, "registered_identity_missing"
        if artifact_hashes(row.weights_uri, row.calibrator_uri) != identity.get("artifact_sha256"):
            return None, "registered_artifact_changed"
        meta = json.loads((Path(row.weights_uri).parent / "metadata.json").read_text())
        contract = identity["fitted_contract"]
        if (
            meta.get("model_version") != row.model_version
            or meta.get("promotion_contract") != contract
            or meta.get("feature_version") != row.feature_version
            or contract["contract"]["feature_version"] != row.feature_version
        ):
            return None, "registered_metadata_mismatch"
        return contract, None
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return None, "registered_artifact_unreadable"

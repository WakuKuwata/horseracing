"""Content-addressed resolved-recipe attestation for the legacy ``lgbm-063`` model.

The serving artifact predates several metadata fields needed to reproduce its training recipe.
This module resolves those fields from the colocated preprocessor, LightGBM model header, and
Feature 073 freeze record, while preserving the complete result as a canonical attestation.

It deliberately has no persistence or production-serving integration: callers decide where an
attestation is stored, and OOF evaluation consumes the reconstructed recipe/factory directly.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import pickle
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from horseracing_eval.hashing import stable_hash
from horseracing_eval.predictor import Predictor, RaceContext
from sqlalchemy.orm import Session

from .calibration import DEFAULT_CALIB_FRAC, LEGACY_CALIBRATION_SPLIT_UNIT
from .predictor import LightGBMPredictor
from .recipe import ModelRecipe, RecipeFactory

EXPECTED_BASE_MODEL_VERSION = "lgbm-063"
EXPECTED_OBJECTIVE = "pl_topk"
EXPECTED_POSTPROCESS = "group_softmax"
EXPECTED_FEATURE_VERSION = "features-017"
EXPECTED_CALIBRATION_METHOD = "isotonic"
EXPECTED_NUM_THREADS = 1

_PAYLOAD_FIELDS = frozenset(
    {
        "base_model_version",
        "resolved_lgbm_params",
        "objective",
        "postprocess",
        "ordered_feature_columns",
        "feature_version",
        "target_encode_cols",
        "te_smoothing",
        "internal_calibration",
        "seed",
        "num_threads",
        "drop_features",
        "source_fingerprint",
        "materialized_hash",
        "code_sha",
    }
)
_ATTESTATION_FIELDS = _PAYLOAD_FIELDS | {"attestation_digest"}
_INTERNAL_CALIBRATION_FIELDS = frozenset(
    {"method", "calib_frac", "calibration_split_unit"}
)

# --- Feature 108: arm E (full-history booster + strict-past OOF isotonic) -------------------
#
# Why this exists at all: `build_attestation` used to fill a MISSING `calib_frac` with
# DEFAULT_CALIB_FRAC and a null `calibration_split_unit` with LEGACY_CALIBRATION_SPLIT_UNIT.
# For an arm E model — which holds NOTHING out and uses no internal split — that turned the
# attestation into a claim that the model is a 70/30 legacy model, and the OOF block count and
# the weight mask were dropped entirely because the payload had nowhere to put them. The
# certificate lied, and nothing failed. Feature 108 closes that.
#
# The calibration method vocabulary is CLOSED. Branching on "OOF or else legacy" would let an
# unknown method (a typo, a future third arm) fall through to the legacy rules — the same
# failure `fit_calibrator` fixed in 085, where an unrouted arm name silently trained a
# different calibrator and the run merely looked plausible.
ARM_E_CALIBRATION_METHOD = "isotonic_strict_past_oof"
LEGACY_CALIBRATION_METHODS = frozenset({"isotonic"})
KNOWN_CALIBRATION_METHODS = LEGACY_CALIBRATION_METHODS | {ARM_E_CALIBRATION_METHOD}
#: protocol names this code knows how to reconstruct. An unknown version is rejected rather
#: than assumed compatible: if the meaning of a recorded field ever changes, the version must
#: change with it, and this set is what forces that (codex review, feature 108).
KNOWN_OOF_PROTOCOLS = frozenset({"strict_past_oof_isotonic_v1"})
#: the recipe field arm E declares but never uses — `_make_base` hardcodes calib_frac=0.0, so
#: the declared value moves the recipe hash and nothing else. It is recorded (so a future
#: version where it DOES bite is detectable) but excluded from behavioural comparison.
_ARM_E_NON_BEHAVIOURAL = ("calib_frac_requested",)
_ARM_E_INTERNAL_FIELDS = frozenset(
    {"method", "calib_frac", "calibration_split_unit", "n_oof_blocks", "protocol_version"}
)
_MODEL_NUM_THREADS_RE = re.compile(r"^\[num_threads:\s*(\d+)\]\s*$", re.MULTILINE)


class AttestationError(ValueError):
    """Raised when a legacy attestation cannot be resolved or fails validation."""


def _required(metadata: Mapping[str, Any], key: str) -> Any:
    if key not in metadata or metadata[key] is None:
        raise AttestationError(f"metadata missing required field: {key}")
    return metadata[key]


def _nonempty_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise AttestationError(f"{field_name} must be a non-empty string")
    return value


def _string_list(
    value: Any, *, field_name: str, allow_empty: bool = True, unique: bool = False
) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise AttestationError(f"{field_name} must be a sequence of strings")
    result = list(value)
    if not allow_empty and not result:
        raise AttestationError(f"{field_name} must not be empty")
    if not all(isinstance(item, str) and item for item in result):
        raise AttestationError(f"{field_name} must contain only non-empty strings")
    if unique and len(result) != len(set(result)):
        raise AttestationError(f"{field_name} must not contain duplicates")
    return result


def _int_value(value: Any, *, field_name: str, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AttestationError(f"{field_name} must be an integer")
    if positive and value <= 0:
        raise AttestationError(f"{field_name} must be positive")
    return value


def _finite_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AttestationError(f"{field_name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise AttestationError(f"{field_name} must be finite")
    return number


def _load_preprocessor(active_dir: Path) -> Mapping[str, Any] | None:
    path = active_dir / "preprocessor.pkl"
    if not path.exists():
        return None
    try:
        with path.open("rb") as fh:
            preprocessor = pickle.load(fh)
    except Exception as exc:
        raise AttestationError(f"failed to read legacy preprocessor: {path}") from exc
    if not isinstance(preprocessor, Mapping):
        raise AttestationError(f"legacy preprocessor must contain a mapping: {path}")
    return preprocessor


def _model_feature_columns(active_dir: Path) -> list[str] | None:
    path = active_dir / "model.txt"
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        if line.startswith("feature_names="):
            return _string_list(
                line.removeprefix("feature_names=").split(),
                field_name="model.txt feature_names",
                allow_empty=False,
                unique=True,
            )
    return None


def _model_num_threads(active_dir: Path) -> int | None:
    path = active_dir / "model.txt"
    if not path.exists():
        return None
    match = _MODEL_NUM_THREADS_RE.search(path.read_text())
    if match is None:
        return None
    return int(match.group(1))


def _agree(field_name: str, candidates: Sequence[tuple[str, Any]]) -> Any | None:
    present = [(source, value) for source, value in candidates if value is not None]
    if not present:
        return None
    expected_source, expected = present[0]
    for source, value in present[1:]:
        if value != expected:
            raise AttestationError(
                f"conflicting {field_name}: {expected_source}={expected!r}, {source}={value!r}"
            )
    return expected


def _resolve_ordered_feature_columns(
    active_dir: Path, metadata: Mapping[str, Any], preprocessor: Mapping[str, Any] | None
) -> list[str]:
    candidates: list[tuple[str, list[str] | None]] = []
    for key in ("ordered_feature_columns", "feature_columns"):
        raw = metadata.get(key)
        columns = None
        if raw is not None:
            columns = _string_list(
                raw, field_name=f"metadata.{key}", allow_empty=False, unique=True
            )
        candidates.append((f"metadata.{key}", columns))

    prep_columns = None
    if preprocessor is not None and preprocessor.get("feature_cols") is not None:
        prep_columns = _string_list(
            preprocessor["feature_cols"],
            field_name="preprocessor.feature_cols",
            allow_empty=False,
            unique=True,
        )
    candidates.extend(
        [
            ("preprocessor.feature_cols", prep_columns),
            ("model.txt feature_names", _model_feature_columns(active_dir)),
        ]
    )
    resolved = _agree("ordered feature columns", candidates)
    if resolved is None:
        raise AttestationError(
            "ordered feature columns missing from metadata, preprocessor.pkl, and model.txt"
        )

    legacy_feature_hash = hashlib.sha256("|".join(resolved).encode()).hexdigest()
    hash_candidates = [("resolved columns", legacy_feature_hash)]
    if metadata.get("feature_hash") is not None:
        hash_candidates.append(("metadata.feature_hash", metadata["feature_hash"]))
    if preprocessor is not None and preprocessor.get("feature_hash") is not None:
        hash_candidates.append(("preprocessor.feature_hash", preprocessor["feature_hash"]))
    _agree("feature hash", hash_candidates)
    return resolved


def _resolve_shared_value(
    metadata: Mapping[str, Any],
    preprocessor: Mapping[str, Any] | None,
    key: str,
) -> Any:
    resolved = _agree(
        key,
        [
            (f"metadata.{key}", metadata.get(key)),
            (
                f"preprocessor.{key}",
                preprocessor.get(key) if preprocessor is not None else None,
            ),
        ],
    )
    if resolved is None:
        raise AttestationError(f"metadata/preprocessor missing required field: {key}")
    return resolved


def _resolve_split_unit(
    metadata: Mapping[str, Any],
    frozen_split_freeze: Mapping[str, Any] | None,
    *,
    model_version: str,
) -> str:
    frozen_unit = None
    if frozen_split_freeze is not None:
        frozen_model = frozen_split_freeze.get("model_version")
        if frozen_model is not None and frozen_model != model_version:
            raise AttestationError(
                "freeze model_version differs from metadata: "
                f"{frozen_model!r} != {model_version!r}"
            )
        frozen_unit = frozen_split_freeze.get("calibration_split_unit")
        if frozen_unit is None:
            raise AttestationError("freeze missing required field: calibration_split_unit")

    resolved = _agree(
        "calibration_split_unit",
        [
            ("metadata.calibration_split_unit", metadata.get("calibration_split_unit")),
            ("freeze.calibration_split_unit", frozen_unit),
        ],
    )
    # lgbm-063 predates explicit split metadata; Feature 073 froze its historical behaviour.
    return resolved if resolved is not None else LEGACY_CALIBRATION_SPLIT_UNIT


def _internal_calibration_block(
    metadata: Mapping[str, Any],
    frozen_split_freeze: Mapping[str, Any] | None,
    *,
    model_version: str,
) -> dict[str, Any]:
    """The `internal_calibration` block, shaped by the calibration method (feature 108).

    Legacy keeps the historical three fields with their historical default-filling (lgbm-063
    predates explicit split metadata, and 073 froze that behaviour — removing the fallback
    would change its digest).

    arm E records the SHIPPING VIEW as it actually is, with no default-filling, plus the
    construction facts needed to rebuild it. The shipping view is what `to_servable` wrote:
    the booster held nothing out (effective calib_frac 0.0) and no internal split was used
    (split unit null). Filling those in with legacy defaults is precisely the lie this feature
    exists to stop.
    """
    method = _required(metadata, "calibration")
    if method != ARM_E_CALIBRATION_METHOD:
        return {
            "method": method,
            "calib_frac": (
                metadata["calib_frac"]
                if metadata.get("calib_frac") is not None
                else DEFAULT_CALIB_FRAC
            ),
            "calibration_split_unit": _resolve_split_unit(
                metadata, frozen_split_freeze, model_version=model_version
            ),
        }

    protocol = metadata.get("calibration_protocol")
    if not isinstance(protocol, Mapping):
        raise AttestationError(
            f"{ARM_E_CALIBRATION_METHOD!r} requires metadata.calibration_protocol "
            "(without it the OOF block count and the booster's holdout are unknown, and "
            "filling them from defaults would attest a model that was never trained)"
        )
    if metadata.get("calibration_split_unit") is not None:
        raise AttestationError(
            "arm E declares calibration_split_unit=null (it uses no internal split); "
            f"got {metadata['calibration_split_unit']!r} — refusing to attest a shape that "
            "would read as a 70/30 split model"
        )
    return {
        "method": method,
        # requested = what the recipe declared (inert for this protocol version);
        # effective = what the booster actually did. Recording both is what makes a future
        # version where the declared value DOES bite detectable (codex review).
        "calib_frac": {
            "requested": metadata.get("calib_frac"),
            "effective": _required(protocol, "booster_calib_frac"),
        },
        "calibration_split_unit": None,
        "n_oof_blocks": _required(protocol, "n_oof_blocks"),
        "protocol_version": _required(protocol, "protocol"),
    }


def _weight_mask_block(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    """The 091 training-time feature mask, or None when the model declares none.

    Absent key => the model was trained without a mask => no key in the payload (D1).
    Present but unreadable => AttestationError. "Not configured" and "we failed to read it"
    must never collapse into the same silent omission: 091 established that the mask is the
    mechanism, not a garnish, so an OOF regeneration that quietly drops it would produce
    predictions the attested procedure never made.
    """
    raw = metadata.get("weight_mask")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise AttestationError("metadata.weight_mask must be a mapping when present")
    rate, seed = raw.get("rate"), raw.get("seed")
    if rate is None or seed is None:
        raise AttestationError(
            "metadata.weight_mask is present but incomplete "
            f"(rate={rate!r}, seed={seed!r}); refusing to omit it silently"
        )
    return {"rate": _finite_number(rate, field_name="weight_mask.rate"),
            "seed": _int_value(seed, field_name="weight_mask.seed")}


def _resolve_num_threads(
    active_dir: Path, metadata: Mapping[str, Any], params: Mapping[str, Any]
) -> int:
    resolved = _agree(
        "num_threads",
        [
            ("metadata.num_threads", metadata.get("num_threads")),
            ("metadata.params.num_threads", params.get("num_threads")),
            ("model.txt num_threads", _model_num_threads(active_dir)),
        ],
    )
    # WinModel has always forced one deterministic LightGBM thread for this legacy recipe.
    return EXPECTED_NUM_THREADS if resolved is None else resolved


def _validate_arm_e_internal(internal: Mapping[str, Any]) -> None:
    """arm E's internal_calibration shape (feature 108).

    Every violation is typed; nothing is defaulted."""
    protocol = internal["protocol_version"]
    _nonempty_string(protocol, field_name="internal_calibration.protocol_version")
    if protocol not in KNOWN_OOF_PROTOCOLS:
        raise AttestationError(
            f"unknown OOF protocol_version: {protocol!r} "
            f"(expected one of {sorted(KNOWN_OOF_PROTOCOLS)}). A protocol this code cannot "
            "reconstruct must not be attested as if it could."
        )
    _int_value(internal["n_oof_blocks"], field_name="internal_calibration.n_oof_blocks",
               positive=True)
    if internal["calibration_split_unit"] is not None:
        raise AttestationError(
            "arm E must record calibration_split_unit=None (it carves no internal split); "
            f"got {internal['calibration_split_unit']!r}"
        )
    frac = internal["calib_frac"]
    if not isinstance(frac, Mapping) or set(frac) != {"requested", "effective"}:
        raise AttestationError(
            "arm E calib_frac must be {'requested': …, 'effective': …} — the declared value and "
            "what the booster actually did are different facts and are recorded separately"
        )
    effective = _finite_number(frac["effective"], field_name="calib_frac.effective")
    if effective != 0.0:
        raise AttestationError(
            "arm E booster holds nothing out, so calib_frac.effective must be 0.0; "
            f"got {effective!r}"
        )
    if frac["requested"] is not None:
        _finite_number(frac["requested"], field_name="calib_frac.requested")


def _validate_weight_mask(mask: Any) -> None:
    if mask is None:
        return
    if not isinstance(mask, Mapping) or set(mask) != {"rate", "seed"}:
        raise AttestationError("weight_mask must be {'rate': …, 'seed': …} when present")
    rate = _finite_number(mask["rate"], field_name="weight_mask.rate")
    if not 0.0 <= rate <= 1.0:
        raise AttestationError(f"weight_mask.rate must be within [0, 1]; got {rate!r}")
    _int_value(mask["seed"], field_name="weight_mask.seed")


def _validate_payload(payload: Mapping[str, Any], *, enforce_legacy: bool) -> None:
    # Feature 108: `weight_mask` is an arm-E-only top-level key. It is allowed (not "unexpected")
    # but never required — a legacy payload that grew one would be a contradiction, and the
    # cross-consistency check below rejects it.
    allowed = _PAYLOAD_FIELDS | {"weight_mask"}
    missing = _PAYLOAD_FIELDS - set(payload)
    unexpected = set(payload) - allowed
    if missing:
        raise AttestationError(f"attestation payload missing required fields: {sorted(missing)}")
    if unexpected:
        raise AttestationError(f"attestation payload has unexpected fields: {sorted(unexpected)}")

    _nonempty_string(payload["base_model_version"], field_name="base_model_version")
    params = payload["resolved_lgbm_params"]
    if not isinstance(params, Mapping) or not params:
        raise AttestationError("resolved_lgbm_params must be a non-empty mapping")
    _nonempty_string(payload["objective"], field_name="objective")
    _nonempty_string(payload["postprocess"], field_name="postprocess")
    _string_list(
        payload["ordered_feature_columns"],
        field_name="ordered_feature_columns",
        allow_empty=False,
        unique=True,
    )
    _nonempty_string(payload["feature_version"], field_name="feature_version")
    _string_list(payload["target_encode_cols"], field_name="target_encode_cols", unique=True)
    smoothing = _finite_number(payload["te_smoothing"], field_name="te_smoothing")
    if smoothing < 0:
        raise AttestationError("te_smoothing must be non-negative")

    internal = payload["internal_calibration"]
    if not isinstance(internal, Mapping):
        raise AttestationError("internal_calibration must be a mapping")
    method = internal.get("method")
    _nonempty_string(method, field_name="internal_calibration.method")
    # CLOSED vocabulary (feature 108). Two branches plus "everything else is legacy" would let a
    # typo or a future arm be validated by rules that do not describe it — 085's failure mode.
    if method not in KNOWN_CALIBRATION_METHODS:
        raise AttestationError(
            f"unknown calibration method: {method!r} "
            f"(expected one of {sorted(KNOWN_CALIBRATION_METHODS)})"
        )
    is_arm_e = method == ARM_E_CALIBRATION_METHOD
    expected_fields = _ARM_E_INTERNAL_FIELDS if is_arm_e else _INTERNAL_CALIBRATION_FIELDS
    missing_internal = expected_fields - set(internal)
    unexpected_internal = set(internal) - expected_fields
    if missing_internal:
        raise AttestationError(
            "internal_calibration missing required fields: " f"{sorted(missing_internal)}"
        )
    if unexpected_internal:
        raise AttestationError(
            "internal_calibration has unexpected fields: " f"{sorted(unexpected_internal)}"
        )

    if is_arm_e:
        _validate_arm_e_internal(internal)
        # cross-consistency: the shipping view says OOF, so the construction facts must be there
        # and the mask (when the model declares one) must be well-formed. A payload that claims
        # the protocol without carrying what the protocol needs is rejected, not defaulted.
        _validate_weight_mask(payload.get("weight_mask"))
    else:
        calib_frac = _finite_number(
            internal["calib_frac"], field_name="internal_calibration.calib_frac"
        )
        if not 0 < calib_frac < 1:
            raise AttestationError("internal_calibration.calib_frac must be between zero and one")
        _nonempty_string(
            internal["calibration_split_unit"],
            field_name="internal_calibration.calibration_split_unit",
        )
        if "weight_mask" in payload:
            raise AttestationError(
                f"weight_mask is only meaningful for {ARM_E_CALIBRATION_METHOD!r}; "
                f"got it alongside method={method!r}"
            )

    _int_value(payload["seed"], field_name="seed")
    _int_value(payload["num_threads"], field_name="num_threads", positive=True)
    _string_list(payload["drop_features"], field_name="drop_features", unique=True)
    for nullable_field in ("source_fingerprint", "materialized_hash"):
        value = payload[nullable_field]
        if value is not None:
            _nonempty_string(value, field_name=nullable_field)
    _nonempty_string(payload["code_sha"], field_name="code_sha")

    if enforce_legacy:
        expected = {
            "base_model_version": EXPECTED_BASE_MODEL_VERSION,
            "objective": EXPECTED_OBJECTIVE,
            "postprocess": EXPECTED_POSTPROCESS,
            "feature_version": EXPECTED_FEATURE_VERSION,
            "num_threads": EXPECTED_NUM_THREADS,
        }
        for field_name, expected_value in expected.items():
            if payload[field_name] != expected_value:
                raise AttestationError(
                    f"legacy {field_name} differs from expectation: "
                    f"{payload[field_name]!r} != {expected_value!r}"
                )
        if internal["method"] != EXPECTED_CALIBRATION_METHOD:
            raise AttestationError(
                "legacy calibration method differs from expectation: "
                f"{internal['method']!r} != {EXPECTED_CALIBRATION_METHOD!r}"
            )
        if internal["calib_frac"] != DEFAULT_CALIB_FRAC:
            raise AttestationError(
                "legacy calib_frac differs from expectation: "
                f"{internal['calib_frac']!r} != {DEFAULT_CALIB_FRAC!r}"
            )
        if internal["calibration_split_unit"] != LEGACY_CALIBRATION_SPLIT_UNIT:
            raise AttestationError(
                "legacy calibration_split_unit differs from expectation: "
                f"{internal['calibration_split_unit']!r} != "
                f"{LEGACY_CALIBRATION_SPLIT_UNIT!r}"
            )


def build_attestation(
    active_dir: Path | str,
    metadata: dict,
    *,
    code_sha: str,
    frozen_split_freeze: dict | None = None,
) -> dict:
    """Resolve the complete lgbm-063 recipe and return its content-addressed attestation."""
    if not isinstance(metadata, Mapping):
        raise AttestationError("metadata must be a mapping")
    if frozen_split_freeze is not None and not isinstance(frozen_split_freeze, Mapping):
        raise AttestationError("frozen_split_freeze must be a mapping or None")

    active_path = Path(active_dir)
    preprocessor = _load_preprocessor(active_path)
    params = _required(metadata, "params")
    if not isinstance(params, Mapping):
        raise AttestationError("metadata.params must be a mapping")
    model_version = _nonempty_string(
        _required(metadata, "model_version"), field_name="metadata.model_version"
    )

    target_encode_cols = _string_list(
        _resolve_shared_value(metadata, preprocessor, "target_encode_cols"),
        field_name="target_encode_cols",
        unique=True,
    )
    te_smoothing = _resolve_shared_value(metadata, preprocessor, "te_smoothing")
    raw_drop_features = metadata.get("drop_features")
    drop_features = _string_list(
        () if raw_drop_features is None else raw_drop_features,
        field_name="drop_features",
        unique=True,
    )
    payload = {
        "base_model_version": model_version,
        "resolved_lgbm_params": copy.deepcopy(dict(params)),
        "objective": _resolve_shared_value(metadata, preprocessor, "objective"),
        "postprocess": _resolve_shared_value(metadata, preprocessor, "postprocess"),
        "ordered_feature_columns": _resolve_ordered_feature_columns(
            active_path, metadata, preprocessor
        ),
        "feature_version": _resolve_shared_value(metadata, preprocessor, "feature_version"),
        "target_encode_cols": target_encode_cols,
        "te_smoothing": te_smoothing,
        "internal_calibration": _internal_calibration_block(
            metadata, frozen_split_freeze, model_version=model_version
        ),
        "seed": _required(metadata, "seed"),
        "num_threads": _resolve_num_threads(active_path, metadata, params),
        "drop_features": drop_features,
        "source_fingerprint": metadata.get("source_fingerprint"),
        "materialized_hash": metadata.get("materialized_hash"),
        "code_sha": code_sha,
    }
    # Feature 108 (D1): arm E keys are inserted ONLY for arm E. A legacy payload keeps exactly
    # the keys it had before this feature, so its canonical JSON — and therefore its digest —
    # is byte-identical. That is load-bearing: the published production manifest records
    # attestation_digest=ef9c5441…, and 076's activation loader recomputes and compares it.
    # An unconditional key with a default would have silently orphaned that manifest.
    mask = _weight_mask_block(metadata)
    if mask is not None:
        payload["weight_mask"] = mask

    _validate_payload(payload, enforce_legacy=False)
    return {**payload, "attestation_digest": stable_hash(payload)}


def attestation_from_model_dir(active_dir: Path | str, *, code_sha: str) -> dict:
    """Read ``metadata.json`` and an optional ``freeze_073.json`` then build an attestation."""
    active_path = Path(active_dir)
    metadata_path = active_path / "metadata.json"
    if not metadata_path.exists():
        raise AttestationError(f"model metadata missing: {metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AttestationError(f"failed to read model metadata: {metadata_path}") from exc

    freeze_path = active_path / "freeze_073.json"
    freeze = None
    if freeze_path.exists():
        try:
            freeze = json.loads(freeze_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise AttestationError(f"failed to read Feature 073 freeze: {freeze_path}") from exc
    return build_attestation(
        active_path, metadata, code_sha=code_sha, frozen_split_freeze=freeze
    )


def _validated_payload(att: dict, *, enforce_legacy: bool = True) -> dict:
    if not isinstance(att, Mapping):
        raise AttestationError("attestation must be a mapping")
    # feature 108: `weight_mask` is arm-E-only, so it is allowed here and its
    # method-consistency is enforced in _validate_payload (a legacy payload carrying one is
    # rejected there, not silently tolerated).
    allowed = _ATTESTATION_FIELDS | {"weight_mask"}
    missing = _ATTESTATION_FIELDS - set(att)
    unexpected = set(att) - allowed
    if missing:
        raise AttestationError(f"attestation missing required fields: {sorted(missing)}")
    if unexpected:
        raise AttestationError(f"attestation has unexpected fields: {sorted(unexpected)}")

    digest = _nonempty_string(att["attestation_digest"], field_name="attestation_digest")
    payload = {key: copy.deepcopy(att[key]) for key in _PAYLOAD_FIELDS}
    if "weight_mask" in att:
        payload["weight_mask"] = copy.deepcopy(att["weight_mask"])
    _validate_payload(payload, enforce_legacy=enforce_legacy)
    expected_digest = stable_hash(payload)
    if digest != expected_digest:
        raise AttestationError(
            f"attestation digest mismatch: {digest!r} != {expected_digest!r}"
        )
    return payload


def _recipe_params_override(payload: Mapping[str, Any]) -> tuple[tuple[str, Any], ...] | None:
    """Capacity (and any other resolved knob a recipe must carry) as a ModelRecipe override.

    `resolved_lgbm_params` is the FULL resolved dict (it even carries objective="binary");
    `ModelRecipe.params` is an override tuple layered over the code defaults. Only the knobs a
    recipe actually forwards belong here. `n_estimators` is the one that bites: the code default
    is 300 and the live arm E model is 900, so dropping it would rebuild a quietly different
    model — the same failure mode as defaulting n_oof_blocks to 3.
    """
    params = payload["resolved_lgbm_params"]
    out = [(key, params[key]) for key in ("n_estimators",) if key in params]
    return tuple(out) if out else None


def _recipe_from_payload(payload: Mapping[str, Any]) -> ModelRecipe:
    internal = payload["internal_calibration"]
    if internal["method"] == ARM_E_CALIBRATION_METHOD:
        # The shipping view is NOT a recipe. `to_servable` writes calib_frac.effective=0.0 and
        # split_unit=None to describe what arm E did; ModelRecipe cannot even be constructed
        # from the latter (split_unit=None raises). The recipe arm E was actually built from
        # declares the ORDINARY isotonic settings — the OOF-ness lives in the predictor that
        # wraps it, not in the recipe. Rebuild that recipe, and let the caller wrap it.
        mask = payload.get("weight_mask") or {}
        return ModelRecipe(
            objective=payload["objective"],
            calibration="isotonic",
            # inert for this protocol version (see _ARM_E_NON_BEHAVIOURAL); the declared value
            # only moves the recipe hash, and the registered hash was never persisted anywhere,
            # so there is nothing to match it against.
            calib_frac=DEFAULT_CALIB_FRAC,
            calibration_split_unit=LEGACY_CALIBRATION_SPLIT_UNIT,
            target_encode_cols=tuple(payload["target_encode_cols"]),
            te_smoothing=float(payload["te_smoothing"]),
            seed=payload["seed"],
            drop_features=tuple(payload["drop_features"]),
            weight_mask_rate=mask.get("rate"),
            weight_mask_seed=mask.get("seed"),
            params=_recipe_params_override(payload),
        )
    return ModelRecipe(
        objective=payload["objective"],
        calibration=internal["method"],
        calib_frac=float(internal["calib_frac"]),
        calibration_split_unit=internal["calibration_split_unit"],
        target_encode_cols=tuple(payload["target_encode_cols"]),
        te_smoothing=float(payload["te_smoothing"]),
        seed=payload["seed"],
        drop_features=tuple(payload["drop_features"]),
    )


def recipe_from_attestation(att: dict) -> ModelRecipe:
    """Validate a complete lgbm-063 attestation and reconstruct its ``ModelRecipe``."""
    return _recipe_from_payload(_validated_payload(att))


@dataclass
class AttestedRecipeFactory(RecipeFactory):
    """RecipeFactory variant that also applies resolved params and enforces feature order."""

    resolved_lgbm_params: dict[str, Any] = field(default_factory=dict)
    ordered_feature_columns: tuple[str, ...] = ()
    num_threads: int = EXPECTED_NUM_THREADS

    def fit(
        self, train_races: list[RaceContext], *, num_threads: int | None = None
    ) -> Predictor:
        if num_threads is not None and num_threads != self.num_threads:
            raise AttestationError(
                f"requested num_threads={num_threads} differs from attested {self.num_threads}"
            )
        if self._pred is None:
            self._pred = LightGBMPredictor(
                self.session,
                seed=self.recipe.seed,
                calibration=self.recipe.calibration,
                params=copy.deepcopy(self.resolved_lgbm_params),
                calib_frac=self.recipe.calib_frac,
                target_encode_cols=self.recipe.target_encode_cols,
                te_smoothing=self.recipe.te_smoothing,
                drop_features=self.recipe.drop_features,
                objective=self.recipe.objective,
                market_offset=self.recipe.market_offset,
                calibration_split_unit=self.recipe.calibration_split_unit,
                # Feature 074 (D9): restrict the fit to lgbm-063's exact features-017 columns on the
                # current features-018 schema (069 additive parity => byte-faithful). Fail-closed if
                # any attested column is absent. This makes the order check below pass faithfully.
                restrict_features=self.ordered_feature_columns,
                # Feature 079: forward the (inherited) EV-weight config so the candidate arm of the
                # paired gate is recipe-faithful. Off by default => byte-identical to pre-079.
                ev_weight=self.recipe.ev_weight,
                oof_p=self.oof_p,
            )
        self._pred.fit(train_races)
        actual_columns = tuple(self._pred.feature_cols_ or ())
        if actual_columns != self.ordered_feature_columns:
            raise AttestationError(
                "training feature columns differ from attested ordered_feature_columns"
            )
        return self._pred


def factory_from_attestation(session: Session, att: dict) -> AttestedRecipeFactory:
    """Build a recipe-faithful factory that retains attested params and feature ordering."""
    payload = _validated_payload(att)
    return _factory_for_payload(session, payload)


def _factory_for_payload(session: Session, payload: Mapping[str, Any]) -> RecipeFactory:
    """Build the factory the attested method actually describes (feature 108).

    Legacy => the historical `AttestedRecipeFactory` (a plain booster with an internal split),
    unchanged. arm E => an OOF-calibrated factory built from the CONSTRUCTION facts, never from
    the code defaults: `n_oof_blocks` defaults to 3 while the live model used 8, so a default
    here would rebuild a different model and report success.
    """
    if payload["internal_calibration"]["method"] != ARM_E_CALIBRATION_METHOD:
        return AttestedRecipeFactory(
            session=session,
            recipe=_recipe_from_payload(payload),
            resolved_lgbm_params=copy.deepcopy(dict(payload["resolved_lgbm_params"])),
            ordered_feature_columns=tuple(payload["ordered_feature_columns"]),
            num_threads=payload["num_threads"],
        )

    from .calib_split import CalibSplitFactory  # local: avoids an import cycle at module load

    class _AttestedCalibSplitFactory(CalibSplitFactory):
        """CalibSplitFactory + the attested-num_threads agreement check (feature 108, T011c).

        NO path in this codebase propagates num_threads into LightGBM — not even the legacy
        `AttestedRecipeFactory`, which uses the attested value purely as an agreement check
        (a caller asking for a different thread count is refused rather than silently served).
        Determinism therefore does NOT come from this argument, and the contract says so; what
        the attestation gives you is the guarantee that nobody ran the recipe under a thread
        count that contradicts the record. arm E previously accepted `num_threads` and dropped
        it on the floor, so the guarantee simply did not exist there. This restores parity with
        the legacy path instead of inventing a third behaviour.
        """

        attested_num_threads: int = 1

        def fit(self, train_races, *, num_threads=None):
            if num_threads is not None and num_threads != self.attested_num_threads:
                raise AttestationError(
                    f"requested num_threads={num_threads} differs from attested "
                    f"{self.attested_num_threads}"
                )
            return super().fit(train_races, num_threads=num_threads)

    internal = payload["internal_calibration"]
    factory = _AttestedCalibSplitFactory(
        session=session,
        recipe=_recipe_from_payload(payload),
        n_oof_blocks=_int_value(
            internal["n_oof_blocks"], field_name="internal_calibration.n_oof_blocks",
            positive=True,
        ),
        method="isotonic",
        # An insufficient OOF sample must abort, never fall back to an identity calibrator that
        # would then be attested under the protocol's name (the same fail-closed arm E's own
        # registration uses).
        require_sufficient=True,
    )
    factory.attested_num_threads = _int_value(
        payload["num_threads"], field_name="num_threads", positive=True
    )
    return factory


def attested_construction(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The behavioural facts a reconstruction must reproduce (feature 108, FR-003).

    Deliberately EXCLUDES the declared `calib_frac` for arm E: it is inert under this protocol
    version, and the registered recipe hash was never persisted, so identity of the hash is not
    something this feature can honestly claim. What it can claim — and this is what the caller
    compares — is that the rebuilt procedure carries the same behavioural configuration.
    """
    internal = payload["internal_calibration"]
    out: dict[str, Any] = {
        "objective": payload["objective"],
        "calibration_method": internal["method"],
        "seed": payload["seed"],
        "te_smoothing": payload["te_smoothing"],
        "target_encode_cols": tuple(payload["target_encode_cols"]),
        "drop_features": tuple(payload["drop_features"]),
        "ordered_feature_columns": tuple(payload["ordered_feature_columns"]),
        "resolved_lgbm_params": dict(payload["resolved_lgbm_params"]),
    }
    if internal["method"] == ARM_E_CALIBRATION_METHOD:
        out["protocol_version"] = internal["protocol_version"]
        out["n_oof_blocks"] = internal["n_oof_blocks"]
        out["weight_mask"] = payload.get("weight_mask")
    return out


def general_factory_from_attestation(
    session: Session,
    att: dict,
    *,
    expected_model_version: str,
    expected_feature_version: str,
) -> AttestedRecipeFactory:
    """Feature 082: recipe-faithful factory for a NON-legacy attested model (e.g. the current
    DB-resolved active).

    Same digest + payload validation as the legacy path but WITHOUT the lgbm-063 pin. Not a blank
    check: the caller states which model version and feature version it expects (resolved from the
    DB active at run time), and a mismatch fails closed — so a stale attestation for a different
    artifact can never silently drive an OOF run.
    """
    payload = _validated_payload(att, enforce_legacy=False)
    if payload["base_model_version"] != expected_model_version:
        raise AttestationError(
            "attested base_model_version differs from the DB-resolved active: "
            f"{payload['base_model_version']!r} != {expected_model_version!r}"
        )
    if payload["feature_version"] != expected_feature_version:
        raise AttestationError(
            "attested feature_version differs from expectation: "
            f"{payload['feature_version']!r} != {expected_feature_version!r}"
        )
    # feature 108: this is the entry non-legacy callers (074 OOF generation, 082 readout) use,
    # so it is the one that must route arm E to an OOF-calibrated factory. Returning the plain
    # factory here is what made 082 unusable on the current generation: the reconstruction was
    # a 70/30 booster whose calibration method name was not even in fit_calibrator's vocabulary.
    return _factory_for_payload(session, payload)

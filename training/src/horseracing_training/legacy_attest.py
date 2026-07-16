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


def _validate_payload(payload: Mapping[str, Any], *, enforce_legacy: bool) -> None:
    missing = _PAYLOAD_FIELDS - set(payload)
    unexpected = set(payload) - _PAYLOAD_FIELDS
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
    missing_internal = _INTERNAL_CALIBRATION_FIELDS - set(internal)
    unexpected_internal = set(internal) - _INTERNAL_CALIBRATION_FIELDS
    if missing_internal:
        raise AttestationError(
            "internal_calibration missing required fields: " f"{sorted(missing_internal)}"
        )
    if unexpected_internal:
        raise AttestationError(
            "internal_calibration has unexpected fields: " f"{sorted(unexpected_internal)}"
        )
    _nonempty_string(internal["method"], field_name="internal_calibration.method")
    calib_frac = _finite_number(
        internal["calib_frac"], field_name="internal_calibration.calib_frac"
    )
    if not 0 < calib_frac < 1:
        raise AttestationError("internal_calibration.calib_frac must be between zero and one")
    _nonempty_string(
        internal["calibration_split_unit"],
        field_name="internal_calibration.calibration_split_unit",
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
        "internal_calibration": {
            "method": _required(metadata, "calibration"),
            "calib_frac": (
                metadata["calib_frac"]
                if metadata.get("calib_frac") is not None
                else DEFAULT_CALIB_FRAC
            ),
            "calibration_split_unit": _resolve_split_unit(
                metadata, frozen_split_freeze, model_version=model_version
            ),
        },
        "seed": _required(metadata, "seed"),
        "num_threads": _resolve_num_threads(active_path, metadata, params),
        "drop_features": drop_features,
        "source_fingerprint": metadata.get("source_fingerprint"),
        "materialized_hash": metadata.get("materialized_hash"),
        "code_sha": code_sha,
    }
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


def _validated_payload(att: dict) -> dict:
    if not isinstance(att, Mapping):
        raise AttestationError("attestation must be a mapping")
    missing = _ATTESTATION_FIELDS - set(att)
    unexpected = set(att) - _ATTESTATION_FIELDS
    if missing:
        raise AttestationError(f"attestation missing required fields: {sorted(missing)}")
    if unexpected:
        raise AttestationError(f"attestation has unexpected fields: {sorted(unexpected)}")

    digest = _nonempty_string(att["attestation_digest"], field_name="attestation_digest")
    payload = {key: copy.deepcopy(att[key]) for key in _PAYLOAD_FIELDS}
    _validate_payload(payload, enforce_legacy=True)
    expected_digest = stable_hash(payload)
    if digest != expected_digest:
        raise AttestationError(
            f"attestation digest mismatch: {digest!r} != {expected_digest!r}"
        )
    return payload


def _recipe_from_payload(payload: Mapping[str, Any]) -> ModelRecipe:
    internal = payload["internal_calibration"]
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
    return AttestedRecipeFactory(
        session=session,
        recipe=_recipe_from_payload(payload),
        resolved_lgbm_params=copy.deepcopy(dict(payload["resolved_lgbm_params"])),
        ordered_feature_columns=tuple(payload["ordered_feature_columns"]),
        num_threads=payload["num_threads"],
    )

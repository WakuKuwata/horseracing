"""Fail-closed loader for the Feature 084 chaos-bands artifact.

The artifact is written by ``training`` but consumed by both ``api`` and
``live``.  This module is the single interpretation point shared by those
consumers: it reads one JSON snapshot, verifies its content-addressed digest
and approval, then applies the operational and temporal gates before exposing
any parameters.
"""

from __future__ import annotations

import copy
import datetime
import enum
import json
import math
import os
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from horseracing_eval.hashing import stable_hash

UnavailableReason = Literal["artifact_unavailable", "out_of_validity_window"]

CHAOS_APPROVED_MANIFEST_ENV = "CHAOS_BANDS_APPROVED_MANIFEST"
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_APPROVED_MANIFEST = _REPO_ROOT / "config" / "chaos_bands_approved.json"
_SHORT_HORIZON_ALIASES = frozenset({"min_seconds_to_post", "max_seconds_to_post"})

_REQUIRED_KEYS = frozenset(
    {
        "version",
        "label_definition",
        "lambda2",
        "lambda3",
        "lambda_fit_objective",
        "band_axis",
        "quintile_edges",
        "edges_basis",
        "s_threshold_basis",
        "fit_from",
        "fit_to",
        "as_of",
        "fit_through",
        "valid_from",
        "n_races_fit",
        "race_set_hash",
        "fit_input_hash",
        "preregistration",
        "numeric_stability_report",
        "operational_lambda_envelope",
        "eligibility_predicate",
        "field_size_reference_quantiles",
        "code_sha",
        "artifact_digest",
        "calibration_status",
    }
)


class ChaosArtifactFailure(enum.StrEnum):
    """Detailed loader failure, kept distinct while mapping to the API vocabulary."""

    JSON_PARSE = "json_parse"
    MISSING_REQUIRED_KEYS = "missing_required_keys"
    DIGEST_MISMATCH = "digest_mismatch"
    DIGEST_NOT_APPROVED = "digest_not_approved"
    INVALID_QUINTILE_EDGES = "invalid_quintile_edges"
    OPERATIONAL_GATE_FAILED = "operational_gate_failed"
    INVALID_PRIMARY_HORIZON = "invalid_primary_horizon"
    INVALID_APPROVAL_MANIFEST = "invalid_approval_manifest"
    INVALID_ARTIFACT_WINDOW = "invalid_artifact_window"
    OUT_OF_VALIDITY_WINDOW = "out_of_validity_window"


class ChaosArtifactError(ValueError):
    """Base class for a fail-closed artifact load failure."""

    reason: UnavailableReason
    failure: ChaosArtifactFailure

    @property
    def unavailable_reason(self) -> UnavailableReason:
        """API ``RaceChaos.unavailable_reason`` corresponding to this failure."""

        return self.reason


class ChaosArtifactUnavailableError(ChaosArtifactError):
    """The artifact itself is absent, malformed, tampered, or unsafe."""

    reason: UnavailableReason = "artifact_unavailable"


class ChaosArtifactParseError(ChaosArtifactUnavailableError):
    """Step 1: the artifact cannot be read as a JSON object."""

    failure = ChaosArtifactFailure.JSON_PARSE


class ChaosArtifactSchemaError(ChaosArtifactUnavailableError):
    """Step 2: required artifact schema is absent or malformed."""

    failure = ChaosArtifactFailure.MISSING_REQUIRED_KEYS


class ChaosArtifactPrimaryHorizonError(ChaosArtifactSchemaError):
    """Step 2: the required confirmation horizon is absent or unsafe."""

    failure = ChaosArtifactFailure.INVALID_PRIMARY_HORIZON


class ChaosArtifactDigestError(ChaosArtifactUnavailableError):
    """Step 3: the stored digest does not cover the loaded payload."""

    failure = ChaosArtifactFailure.DIGEST_MISMATCH


class ChaosArtifactApprovalError(ChaosArtifactUnavailableError):
    """Step 4: the verified digest is not pinned by the caller's manifest."""

    failure = ChaosArtifactFailure.DIGEST_NOT_APPROVED


class ChaosArtifactManifestError(ChaosArtifactUnavailableError):
    """The committed approval manifest is malformed or has no unique current entry."""

    failure = ChaosArtifactFailure.INVALID_APPROVAL_MANIFEST


class ChaosArtifactEdgesError(ChaosArtifactUnavailableError):
    """Step 5: the four band edges are not finite and strictly increasing."""

    failure = ChaosArtifactFailure.INVALID_QUINTILE_EDGES


class ChaosArtifactOperationalError(ChaosArtifactUnavailableError):
    """Step 6: lambdas are outside the envelope or stability is not green."""

    failure = ChaosArtifactFailure.OPERATIONAL_GATE_FAILED


class ChaosArtifactWindowError(ChaosArtifactUnavailableError):
    """Step 7: the artifact's confirmation window overlaps its fit window."""

    failure = ChaosArtifactFailure.INVALID_ARTIFACT_WINDOW


class ChaosArtifactOutOfValidityWindowError(ChaosArtifactError):
    """Step 8: the requested race date is not displayable with this artifact."""

    reason: UnavailableReason = "out_of_validity_window"
    failure = ChaosArtifactFailure.OUT_OF_VALIDITY_WINDOW


@dataclass(frozen=True)
class ChaosBandsArtifact:
    """Verified content of ``data-model.md`` section 4."""

    version: str
    label_definition: str
    lambda2: float
    lambda3: float
    lambda_fit_objective: Any
    band_axis: str
    quintile_edges: tuple[float, float, float, float]
    edges_basis: str
    s_threshold_basis: str
    fit_from: str
    fit_to: str
    as_of: str
    fit_through: datetime.date
    valid_from: datetime.date
    n_races_fit: int
    race_set_hash: str
    fit_input_hash: str
    preregistration: dict[str, Any]
    numeric_stability_report: dict[str, Any]
    operational_lambda_envelope: dict[str, Any]
    eligibility_predicate: dict[str, Any]
    field_size_reference_quantiles: dict[str, Any]
    code_sha: str
    artifact_digest: str
    calibration_status: str


def compute_chaos_artifact_digest(payload: Mapping[str, Any]) -> str:
    """Return the canonical SHA-256, excluding only the self-reference field.

    The encoding is the repository-wide ``stable_hash`` contract: sorted keys,
    compact separators, UTF-8, and ``ensure_ascii=False``.  Every other field,
    including unknown additive fields, remains covered.
    """

    covered_payload = {key: value for key, value in payload.items() if key != "artifact_digest"}
    return stable_hash(covered_payload)


def approved_digests_from_manifest(
    manifest_path: str | os.PathLike[str] | None = None,
) -> tuple[str, ...]:
    """Return the status-irrelevant permission set from the approval manifest.

    This is intentionally separate from :func:`resolve_current_digest`.
    Superseded entries remain approved inputs for legacy upgrades and explicit
    historical reports even though they are not the artifact used "now".
    """

    return tuple(entry["digest"] for entry in _approval_manifest_entries(manifest_path))


def resolve_current_digest(
    manifest_path: str | os.PathLike[str] | None = None,
) -> str:
    """Return the unique ``status="active"`` digest from the manifest.

    Current selection is intentionally separate from the status-irrelevant
    permission set returned by :func:`approved_digests_from_manifest`.
    """

    active = [
        entry["digest"]
        for entry in _approval_manifest_entries(manifest_path)
        if entry.get("status") == "active"
    ]
    if len(active) != 1:
        raise ChaosArtifactManifestError(
            f"approval manifest must contain exactly one active digest, found {len(active)}"
        )
    return active[0]


def upgrade_legacy_artifact_horizon(
    path: str | os.PathLike[str],
    *,
    expected_digest: str,
    minimum_seconds_to_post: int,
    maximum_seconds_to_post: int,
) -> tuple[dict[str, Any], str]:
    """Add a horizon to one approved window-less artifact and recompute its digest.

    This is the sole bootstrap exception to the mandatory-horizon loader.  It
    validates the legacy artifact's digest, manifest permission (regardless of
    status), edges, lambdas, stability, and fit/validity separation before
    returning a new in-memory payload.  It is not a general raw reader.
    """

    _validate_digest_string(expected_digest, error_type=ChaosArtifactApprovalError)
    approved_digests = approved_digests_from_manifest()
    if expected_digest not in approved_digests:
        raise ChaosArtifactApprovalError(
            f"legacy artifact digest {expected_digest!r} is not listed in the approval manifest"
        )

    payload = _read_json(Path(path))
    preregistration = _validate_required_payload_schema(payload)
    if "primary_horizon" in preregistration:
        raise ChaosArtifactPrimaryHorizonError(
            "upgrade requires a legacy artifact without primary_horizon"
        )
    _verify_artifact_contents(payload, approved_digests=approved_digests)
    if payload["artifact_digest"] != expected_digest:
        raise ChaosArtifactDigestError(
            "legacy artifact digest does not match expected_digest: "
            f"expected {expected_digest!r}, got {payload['artifact_digest']!r}"
        )

    horizon = {
        "minimum_seconds_to_post": minimum_seconds_to_post,
        "maximum_seconds_to_post": maximum_seconds_to_post,
    }
    _validate_primary_horizon(horizon)

    upgraded = copy.deepcopy(payload)
    upgraded["preregistration"]["primary_horizon"] = horizon
    upgraded_digest = compute_chaos_artifact_digest(upgraded)
    upgraded["artifact_digest"] = upgraded_digest
    return upgraded, upgraded_digest


def load_chaos_artifact(
    path: str | os.PathLike[str],
    *,
    approved_digests: Collection[str],
    target_date: datetime.date,
) -> ChaosBandsArtifact:
    """Load and verify one chaos-bands artifact in the normative eight-step order.

    No check supplies defaults and no failure returns partially verified
    parameters.  Checks 1--7 map to ``artifact_unavailable``; check 8 maps to
    ``out_of_validity_window``.
    """

    artifact = _verify_artifact_payload(
        Path(path),
        approved_digests=approved_digests,
    )
    fit_through = artifact.fit_through
    valid_from = artifact.valid_from

    # (8) Displayable iff BOTH conditions hold.  In particular, dates after the
    # fit window are accepted; they are not the rejection condition.
    if (
        not isinstance(target_date, datetime.date)
        or isinstance(target_date, datetime.datetime)
        or target_date <= fit_through
        or target_date < valid_from
    ):
        target = (
            target_date.isoformat()
            if isinstance(target_date, (datetime.date, datetime.datetime))
            else repr(target_date)
        )
        raise ChaosArtifactOutOfValidityWindowError(
            f"target_date {target} is outside the artifact validity window "
            f"(fit_through={fit_through.isoformat()}, "
            f"valid_from={valid_from.isoformat()})"
        )

    return artifact


def _verify_artifact_payload(
    path: Path,
    *,
    approved_digests: Collection[str],
) -> ChaosBandsArtifact:
    """Read one artifact and run mandatory-horizon content checks 1--7."""

    # (1) Read once, then verify and apply that same in-memory object (no TOCTOU
    # re-read).  ``parse_constant`` rejects the non-standard NaN/Infinity values
    # accepted by Python's JSON decoder by default.
    payload = _read_json(path)

    # (2) Required payload fields, including the Feature 086 nested horizon.
    preregistration = _validate_required_payload_schema(payload)
    if "primary_horizon" not in preregistration:
        raise ChaosArtifactPrimaryHorizonError(
            "preregistration.primary_horizon is required"
        )
    _validate_primary_horizon(preregistration["primary_horizon"])
    return _verify_artifact_contents(payload, approved_digests=approved_digests)


def _validate_required_payload_schema(payload: dict[str, Any]) -> dict[str, Any]:
    missing = sorted(_REQUIRED_KEYS - payload.keys())
    if missing:
        raise ChaosArtifactSchemaError(
            f"partial chaos artifact: missing field(s): {', '.join(missing)}"
        )
    return _mapping_field(payload, "preregistration")


def _verify_artifact_contents(
    payload: dict[str, Any],
    *,
    approved_digests: Collection[str],
) -> ChaosBandsArtifact:
    """Verify one already-read payload without weakening any content gate."""

    # (3) Content-addressed digest.  The digest field itself is the sole
    # self-reference and therefore the sole excluded top-level key.
    expected_digest = compute_chaos_artifact_digest(payload)
    artifact_digest = payload["artifact_digest"]
    if artifact_digest != expected_digest:
        raise ChaosArtifactDigestError(
            f"artifact_digest mismatch: expected {expected_digest}, got {artifact_digest!r}"
        )

    # (4) A valid git-ignored artifact is still unusable until a committed
    # caller manifest pins its exact digest.
    try:
        approved = artifact_digest in approved_digests
    except TypeError as exc:
        raise ChaosArtifactApprovalError(
            "approved_digests must be a collection of digest strings"
        ) from exc
    if not approved:
        raise ChaosArtifactApprovalError(f"artifact digest {artifact_digest!r} is not approved")

    # (5) Four finite, strictly increasing band edges.
    edges = _validated_edges(payload["quintile_edges"])

    # (6) Both lambdas must be finite, in the global contract (0, 5], inside
    # their artifact-defined operational ranges, and backed by a green report.
    lambda2 = _finite_number(
        payload["lambda2"], error_type=ChaosArtifactOperationalError, name="lambda2"
    )
    lambda3 = _finite_number(
        payload["lambda3"], error_type=ChaosArtifactOperationalError, name="lambda3"
    )
    envelope = payload["operational_lambda_envelope"]
    if not isinstance(envelope, dict):
        raise ChaosArtifactOperationalError("operational_lambda_envelope must be a mapping")
    for name, value in (("lambda2", lambda2), ("lambda3", lambda3)):
        if not 0.0 < value <= 5.0:
            raise ChaosArtifactOperationalError(f"{name} must be in (0, 5], got {value!r}")
        lower, upper, lower_inclusive, upper_inclusive = _lambda_range(envelope, name=name)
        if not _in_range(
            value,
            lower=lower,
            upper=upper,
            lower_inclusive=lower_inclusive,
            upper_inclusive=upper_inclusive,
        ):
            raise ChaosArtifactOperationalError(
                f"{name}={value!r} is outside operational_lambda_envelope "
                f"{_range_notation(lower, upper, lower_inclusive, upper_inclusive)}"
            )

    report = payload["numeric_stability_report"]
    if not isinstance(report, dict) or not _stability_is_green(report):
        raise ChaosArtifactOperationalError("numeric_stability_report is not green")

    # (7) The artifact itself must leave the fit observations behind before
    # confirmation starts.
    fit_through = _artifact_date(payload["fit_through"], field="fit_through")
    valid_from = _artifact_date(payload["valid_from"], field="valid_from")
    if valid_from <= fit_through:
        raise ChaosArtifactWindowError(
            f"valid_from {valid_from.isoformat()} must be after fit_through "
            f"{fit_through.isoformat()}"
        )

    return _build_artifact(
        payload,
        lambda2=lambda2,
        lambda3=lambda3,
        edges=edges,
        fit_through=fit_through,
        valid_from=valid_from,
    )


def _validate_primary_horizon(value: Any) -> tuple[int, int]:
    if not isinstance(value, Mapping):
        raise ChaosArtifactPrimaryHorizonError(
            "preregistration.primary_horizon must be a mapping"
        )
    aliases = sorted(_SHORT_HORIZON_ALIASES.intersection(value))
    if aliases:
        raise ChaosArtifactPrimaryHorizonError(
            "short primary_horizon aliases are forbidden: " + ", ".join(aliases)
        )

    minimum = value.get("minimum_seconds_to_post")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
        raise ChaosArtifactPrimaryHorizonError(
            "primary_horizon.minimum_seconds_to_post must be a non-negative integer"
        )

    maximum = value.get("maximum_seconds_to_post")
    if isinstance(maximum, bool) or not isinstance(maximum, int):
        raise ChaosArtifactPrimaryHorizonError(
            "primary_horizon.maximum_seconds_to_post must be an integer"
        )
    if maximum <= minimum:
        raise ChaosArtifactPrimaryHorizonError(
            "primary_horizon.maximum_seconds_to_post must be greater than "
            "minimum_seconds_to_post"
        )
    return minimum, maximum


def _approval_manifest_entries(
    manifest_path: str | os.PathLike[str] | None = None,
) -> tuple[dict[str, Any], ...]:
    path = Path(
        manifest_path
        if manifest_path is not None
        else os.environ.get(CHAOS_APPROVED_MANIFEST_ENV, DEFAULT_APPROVED_MANIFEST)
    )

    def reject_non_finite(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value}")

    try:
        loaded = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=reject_non_finite,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise ChaosArtifactManifestError(
            f"cannot read chaos approval manifest {path}: {exc}"
        ) from exc
    if not isinstance(loaded, Mapping):
        raise ChaosArtifactManifestError("chaos approval manifest root must be a mapping")
    approved = loaded.get("approved")
    if (
        isinstance(approved, (str, bytes))
        or not isinstance(approved, Sequence)
    ):
        raise ChaosArtifactManifestError(
            "chaos approval manifest approved must be a sequence"
        )

    entries: list[dict[str, Any]] = []
    for index, entry in enumerate(approved):
        if not isinstance(entry, Mapping):
            raise ChaosArtifactManifestError(
                f"chaos approval manifest approved[{index}] must be a mapping"
            )
        digest = entry.get("digest")
        _validate_digest_string(
            digest,
            error_type=ChaosArtifactManifestError,
            name=f"approved[{index}].digest",
        )
        entries.append(dict(entry))
    return tuple(entries)


def _validate_digest_string(
    value: Any,
    *,
    error_type: type[ChaosArtifactUnavailableError],
    name: str = "expected_digest",
) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise error_type(f"{name} must be a 64-character lowercase SHA-256 digest")


def _read_json(path: Path) -> dict[str, Any]:
    def reject_non_finite(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value}")

    try:
        loaded = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=reject_non_finite,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise ChaosArtifactParseError(f"cannot read chaos artifact {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ChaosArtifactParseError(f"chaos artifact root must be an object: {path}")
    return loaded


def _finite_number(
    value: Any, *, error_type: type[ChaosArtifactUnavailableError], name: str
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise error_type(f"{name} must be a finite number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise error_type(f"{name} must be finite, got {value!r}")
    return number


def _validated_edges(value: Any) -> tuple[float, float, float, float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 4:
        raise ChaosArtifactEdgesError("quintile_edges must contain exactly four values")
    edges = tuple(
        _finite_number(
            edge,
            error_type=ChaosArtifactEdgesError,
            name=f"quintile_edges[{index}]",
        )
        for index, edge in enumerate(value)
    )
    if any(right <= left for left, right in zip(edges, edges[1:], strict=False)):
        raise ChaosArtifactEdgesError("quintile_edges must be strictly increasing")
    return edges  # type: ignore[return-value]


def _lambda_range(envelope: dict[str, Any], *, name: str) -> tuple[float, float, bool, bool]:
    """Read shared or per-lambda envelope forms without weakening the gate.

    Publishers may represent a range as ``[lower, upper]`` or as a mapping
    whose boundary names encode inclusivity.  A shared top-level range is also
    accepted.  Malformed or incomplete ranges fail closed.
    """

    if name in envelope:
        raw_range = envelope[name]
    elif f"{name}_min" in envelope or f"{name}_max" in envelope:
        raw_range = {
            "min": envelope.get(f"{name}_min"),
            "max": envelope.get(f"{name}_max"),
        }
    else:
        raw_range = envelope
    return _parse_range(raw_range, name=f"operational_lambda_envelope.{name}")


def _parse_range(value: Any, *, name: str) -> tuple[float, float, bool, bool]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 2:
        lower_raw, upper_raw = value
        lower_inclusive = True
        upper_inclusive = True
    elif isinstance(value, Mapping):
        lower_keys = ("min_exclusive", "min_inclusive", "min", "lower")
        upper_keys = ("max_exclusive", "max_inclusive", "max", "upper")
        lower_key = next((key for key in lower_keys if key in value), None)
        upper_key = next((key for key in upper_keys if key in value), None)
        if lower_key is None or upper_key is None:
            raise ChaosArtifactOperationalError(f"{name} must define lower and upper bounds")
        lower_raw = value[lower_key]
        upper_raw = value[upper_key]
        lower_inclusive = lower_key != "min_exclusive"
        upper_inclusive = upper_key != "max_exclusive"
    else:
        raise ChaosArtifactOperationalError(f"{name} must be a two-value range")

    lower = _finite_number(
        lower_raw, error_type=ChaosArtifactOperationalError, name=f"{name}.lower"
    )
    upper = _finite_number(
        upper_raw, error_type=ChaosArtifactOperationalError, name=f"{name}.upper"
    )
    if lower > upper or (lower == upper and not (lower_inclusive and upper_inclusive)):
        raise ChaosArtifactOperationalError(f"{name} has an empty or reversed range")
    return lower, upper, lower_inclusive, upper_inclusive


def _in_range(
    value: float,
    *,
    lower: float,
    upper: float,
    lower_inclusive: bool,
    upper_inclusive: bool,
) -> bool:
    lower_ok = value >= lower if lower_inclusive else value > lower
    upper_ok = value <= upper if upper_inclusive else value < upper
    return lower_ok and upper_ok


def _range_notation(
    lower: float, upper: float, lower_inclusive: bool, upper_inclusive: bool
) -> str:
    left = "[" if lower_inclusive else "("
    right = "]" if upper_inclusive else ")"
    return f"{left}{lower}, {upper}{right}"


def _stability_is_green(report: Mapping[str, Any]) -> bool:
    for key in ("status", "overall_status", "result"):
        if key in report:
            value = report[key]
            return isinstance(value, str) and value.lower() == "green"
    for key in ("green", "passed", "all_passed"):
        if key in report:
            return report[key] is True
    return False


def _artifact_date(value: Any, *, field: str) -> datetime.date:
    if not isinstance(value, str):
        raise ChaosArtifactWindowError(f"{field} must be an ISO date string, got {value!r}")
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise ChaosArtifactWindowError(
            f"{field} must be an ISO date string, got {value!r}"
        ) from exc


def _mapping_field(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    value = payload[field]
    if not isinstance(value, dict):
        raise ChaosArtifactSchemaError(f"{field} must be a mapping")
    return value


def _build_artifact(
    payload: dict[str, Any],
    *,
    lambda2: float,
    lambda3: float,
    edges: tuple[float, float, float, float],
    fit_through: datetime.date,
    valid_from: datetime.date,
) -> ChaosBandsArtifact:
    """Map the same verified snapshot to the immutable consumer object."""

    try:
        return ChaosBandsArtifact(
            version=payload["version"],
            label_definition=payload["label_definition"],
            lambda2=lambda2,
            lambda3=lambda3,
            lambda_fit_objective=payload["lambda_fit_objective"],
            band_axis=payload["band_axis"],
            quintile_edges=edges,
            edges_basis=payload["edges_basis"],
            s_threshold_basis=payload["s_threshold_basis"],
            fit_from=payload["fit_from"],
            fit_to=payload["fit_to"],
            as_of=payload["as_of"],
            fit_through=fit_through,
            valid_from=valid_from,
            n_races_fit=payload["n_races_fit"],
            race_set_hash=payload["race_set_hash"],
            fit_input_hash=payload["fit_input_hash"],
            preregistration=_mapping_field(payload, "preregistration"),
            numeric_stability_report=_mapping_field(payload, "numeric_stability_report"),
            operational_lambda_envelope=_mapping_field(payload, "operational_lambda_envelope"),
            eligibility_predicate=_mapping_field(payload, "eligibility_predicate"),
            field_size_reference_quantiles=_mapping_field(
                payload, "field_size_reference_quantiles"
            ),
            code_sha=payload["code_sha"],
            artifact_digest=payload["artifact_digest"],
            calibration_status=payload["calibration_status"],
        )
    except ChaosArtifactSchemaError:
        raise
    except (KeyError, TypeError, ValueError) as exc:  # defensive typed boundary
        raise ChaosArtifactSchemaError(f"cannot map verified chaos artifact: {exc}") from exc

"""Shared calibration-activation loader (Feature 076).

The single interpretation point for the 074 immutable calibration manifest. betting / serving /
api(dispersion) / training(dispersion-pcal) / live all call :func:`load_calibration` so the
two-gamma and stage-discount mappings live in exactly one place (no drift). Fail-closed: any invalid
manifest raises before a consumer applies it; the caller never silently falls back to a runtime fit.

Placement (research D11): this loader lives in ``probability`` (which betting/api/serving all depend
on). The manifest schema/verify was relocated here (``probability.calib_manifest``) so verification
needs no ``training`` import — ``training`` depends on ``probability``, so ``probability`` importing
``training`` is a cycle and, in the uv workspace, an unavailable module. The generation-binding
attestation recompute IS training-specific (it rebuilds the lgbm-063 recipe), so it is supplied
as an optional injected ``attestation_verifier`` by callers with ``training``;
betting/api rely on the name + content-addressed digest binding until the 077 registry generalises
strong binding to every caller. This keeps FR-006 (single loader) while respecting the dep graph.
"""

from __future__ import annotations

import datetime
import enum
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from horseracing_eval.stage_discount import StageDiscount

from .calib_manifest import ManifestError, verify_manifest
from .model_calibration import TWO_GAMMA_PIVOT, PCalibrator


class ActivationError(ValueError):
    """Activation was requested but the manifest cannot be used for this run (fail-closed).

    Distinct from :class:`ManifestError` (structural/tamper): raised for generation / scope /
    temporal mismatches — the manifest is well-formed but not applicable here.
    """


class ActivationMode(enum.StrEnum):
    """Explicit activation mode (FR-004). ``legacy-runtime`` never reaches this loader."""

    LEGACY_RUNTIME = "legacy-runtime"
    MANIFEST_REQUIRED = "manifest-required"


class Profile(enum.StrEnum):
    """Loader profile (FR-016). ``production`` rejects fixture / non-eligible artifacts."""

    PRODUCTION = "production"
    FIXTURE = "fixture"


#: A caller-supplied strong generation check. Receives the verified manifest dict and MUST raise
#: (ActivationError) when the active model artifact does not match ``manifest.attestation_digest``.
AttestationVerifier = Callable[[dict], None]


@dataclass(frozen=True)
class Activation:
    """The activated, verified calibration for one manifest (data-model §2)."""

    two_gamma: PCalibrator
    stage_discount: StageDiscount
    manifest_digest: str
    mode: str
    fit_through: datetime.date

    def applies_to(self, target_date: datetime.date) -> bool:
        """FR-021: a manifest may only calibrate races strictly AFTER its fit window."""
        return target_date > self.fit_through

    def assert_applies(self, target_date: datetime.date) -> None:
        """Per-target_date temporal gate for backfill (load-once, checked per day — I1)."""
        if not self.applies_to(target_date):
            raise ActivationError(
                f"target_date {target_date.isoformat()} is within the calibration fit window "
                f"(fit_through {self.fit_through.isoformat()}): applying would be non-OOS"
            )

    @property
    def digest12(self) -> str:
        """The 12-char digest prefix used in logic_version / idempotency keys (FR-009)."""
        return self.manifest_digest[:12]

    @property
    def idempotency_fragment(self) -> str:
        """SQL ``contains()`` needle so a different manifest digest is a DIFFERENT run (FR-010)."""
        return f";calib={self.digest12}"


def calib_logic_version_token(manifest_digest: str) -> str:
    """Audit token embedded in logic_version (FR-009). No leading ``;`` — callers join with ``;``,
    matching the PCalibrator.logic_version convention (``pcal=...;...``)."""
    return f"calib={manifest_digest[:12]};calibmode=manifest"


def load_calibration(
    manifest_path: str | os.PathLike[str],
    *,
    active_model_version: str,
    target_date: datetime.date | None = None,
    profile: Profile = Profile.PRODUCTION,
    attestation_verifier: AttestationVerifier | None = None,
) -> Activation:
    """Load + verify a manifest and return its :class:`Activation` (fail-closed).

    Order is fixed and every check runs BEFORE any calibration is applied:

    0. absolute-path check (relative paths break across package cwds — FR-018/D5).
    1. ``verify_manifest`` (schema / digests / manifest_digest / param shape).
    2. generation: ``base_model_version == active_model_version``; plus, when supplied, the injected
       ``attestation_verifier`` (strong recompute binding — FR-019).
    3. scope: a ``production`` profile rejects fixture / non-eligible manifests (FR-016).
    4. temporal: when ``target_date`` is given, ``target_date > fit_through`` (FR-021). Backfill
       omits ``target_date`` here and calls :meth:`Activation.assert_applies` per day (load-once).
    5. map ``two_gamma`` -> PCalibrator and ``stage_lambdas{top2,top3}`` -> StageDiscount.

    File I/O + verify + generation + scope run once per invocation (all races share one digest).
    """
    path = Path(manifest_path)
    if not path.is_absolute():
        raise ActivationError(f"manifest path must be absolute (got {manifest_path!r})")
    # Accept a str profile too, and coerce so ``is`` comparisons can't be bypassed by a raw string
    # (codex P0-4b: ``profile is Profile.PRODUCTION`` is False for "production" → scope skip). An
    # unknown value raises ValueError → ActivationError.
    try:
        profile = Profile(profile)
    except ValueError as exc:
        raise ActivationError(f"unknown profile {profile!r}") from exc

    # (1) structural verification. Read the file ONCE and verify the in-memory dict, then apply that
    # SAME dict — no re-read (codex P0-4a: verify(path)+read(path) are two snapshots = TOCTOU).
    manifest = _read_json(path)
    verify_manifest(manifest)
    digest = manifest["manifest_digest"]

    # (2) generation binding.
    base = manifest["base_model_version"]
    if base != active_model_version:
        raise ActivationError(
            f"generation mismatch: manifest base_model_version {base!r} != "
            f"active model {active_model_version!r}"
        )
    if attestation_verifier is not None:
        attestation_verifier(manifest)  # strong recompute binding; raises on mismatch.

    # (3) scope.
    if profile == Profile.PRODUCTION and (
        manifest["artifact_scope"] != "production" or not manifest["activation_eligible"]
    ):
        raise ActivationError(
            "manifest is not production-eligible "
            f"(scope={manifest['artifact_scope']!r}, eligible={manifest['activation_eligible']!r})"
        )

    fit_through = datetime.date.fromisoformat(manifest["fit_through"])

    # (4) temporal (single-race path; backfill uses Activation.assert_applies per day).
    if target_date is not None and target_date <= fit_through:
        raise ActivationError(
            f"target_date {target_date.isoformat()} is within the calibration fit window "
            f"(fit_through {fit_through.isoformat()})"
        )

    # (5) map manifest params to the apply-path objects (single interpretation point — FR-006).
    params = manifest["full_precision_params"]
    two_gamma = _two_gamma_calibrator(params["two_gamma"], base_model_version=base, digest=digest)
    stage_discount = _stage_discount(params["stage_lambdas"])

    return Activation(
        two_gamma=two_gamma,
        stage_discount=stage_discount,
        manifest_digest=digest,
        mode=ActivationMode.MANIFEST_REQUIRED.value,
        fit_through=fit_through,
    )


def _read_json(path: Path) -> dict:
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # pragma: no cover - verify_manifest already read it
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc


def _two_gamma_calibrator(
    tg: dict, *, base_model_version: str, digest: str
) -> PCalibrator:
    """Build the two-gamma PCalibrator the 046 recommendation path expects (048 machinery).

    Provenance fields (train_window/n_races/n_samples/prob_range) are not carried by the manifest —
    the fitted params are already frozen — so they take neutral defaults; only params drive apply.
    """
    gamma_lo = float(tg["gamma_lo"])
    gamma_hi = float(tg["gamma_hi"])
    pivot = float(tg.get("pivot", TWO_GAMMA_PIVOT))
    # Mirror the fitted two_gamma logic_version (048) so downstream parsers still read the gammas,
    # then append the manifest provenance token (FR-009). ``;calib=<digest12>`` is the idempotency
    # needle (a different manifest -> a different run — FR-010).
    logic_version = (
        f"pcal=two_gamma;gamma_lo={gamma_lo:.5f};gamma_hi={gamma_hi:.5f};pivot={pivot};"
        f"{calib_logic_version_token(digest)};base_mv={base_model_version}"
    )
    return PCalibrator(
        method="two_gamma",
        params={"gamma_lo": gamma_lo, "gamma_hi": gamma_hi, "pivot": pivot},
        train_window=None,
        n_races=0,
        n_samples=0,
        prob_range=(0.0, 1.0),
        select="manifest",
        base_model_version=base_model_version,
        logic_version=logic_version,
        sufficient=True,
    )


def _stage_discount(stage_lambdas: dict) -> StageDiscount:
    """Map stage_lambdas{top2,top3} -> StageDiscount(lambda2=top2, lambda3=top3) (FR-002)."""
    return StageDiscount(
        lambda2=float(stage_lambdas["top2"]),
        lambda3=float(stage_lambdas["top3"]),
    )

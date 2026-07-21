"""Fixture manifest builder for Feature 076 activation tests (T001).

Builds v2 manifests via the real ``build_manifest`` (so tests exercise the real schema/verify) with
knobs for the four fixture kinds the parity / fail-closed suites need: a fixture-scoped manifest, a
production-eligible manifest, plus tamper / temporal variants produced by the callers. Placed under
``probability`` because the shared loader (and its tests) live there; the relocation of the manifest
module (research D11) makes ``build_manifest`` importable here without a training dependency.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from horseracing_probability.calib_manifest import build_manifest

_ATTESTATION_DIGEST = "a" * 64
_BUNDLE_DIGEST = "b" * 64


def make_manifest(
    *,
    artifact_scope: str = "production",
    activation_eligible: bool = True,
    fit_through: str = "2024-12-31",
    gamma_lo: float = 1.6543210987654321,
    gamma_hi: float = 0.5123456789012345,
    pivot: float = 0.15,
    top2: float = 0.8234567890123456,
    top3: float = 0.7098765432109876,
    attestation_digest: str = _ATTESTATION_DIGEST,
    code_sha: str = "076deadbeef",
) -> dict:
    """Return a valid v2 manifest dict (defaults = production-eligible, known params)."""
    return build_manifest(
        attestation_digest=attestation_digest,
        bundle_digest=_BUNDLE_DIGEST,
        evaluation={
            "evaluation_contract_version": "v2",
            "base_model_version": "lgbm-063",
            "attestation_digest": attestation_digest,
            "bundle_digest": _BUNDLE_DIGEST,
            "verdict": "ADOPT",
        },
        stages=["model_win", "two_gamma_win", "stage_discount_top2", "stage_discount_top3"],
        code_sha=code_sha,
        seed=76,
        num_threads=1,
        two_gamma_lambda_full_precision={
            "two_gamma": {"gamma_lo": gamma_lo, "gamma_hi": gamma_hi, "pivot": pivot},
            "stage_lambdas": {"top2": top2, "top3": top3},
        },
        fit_through=fit_through,
        artifact_scope=artifact_scope,
        activation_eligible=activation_eligible,
    )


def write_manifest_file(tmp_path: Path, manifest: dict, name: str = "manifest.json") -> Path:
    """Write ``manifest`` to an ABSOLUTE path under ``tmp_path`` and return it (loader needs abs)."""
    target = (Path(tmp_path) / name).resolve()
    target.write_text(json.dumps(manifest), encoding="utf-8")
    return target


def production_fixture(tmp_path: Path, *, name: str = "manifest.json", **kw) -> Path:
    """``name`` lets one test write several manifests (different γ ⇒ different digest) side by side."""
    return write_manifest_file(tmp_path, make_manifest(**kw), name)


def fixture_scoped(tmp_path: Path, *, name: str = "manifest.json", **kw) -> Path:
    kw.setdefault("artifact_scope", "fixture")
    kw.setdefault("activation_eligible", False)
    return write_manifest_file(tmp_path, make_manifest(**kw), name)


def tampered(tmp_path: Path) -> Path:
    """A manifest whose digest no longer matches its payload (tamper => ManifestError)."""
    manifest = make_manifest()
    manifest["full_precision_params"]["two_gamma"]["gamma_lo"] = 9.9  # payload changed, digest stale
    return write_manifest_file(tmp_path, manifest)


def expired(tmp_path: Path, fit_through: str = "2026-12-31") -> Path:
    """A manifest whose fit window covers a late date (used to trip the temporal check)."""
    return write_manifest_file(tmp_path, make_manifest(fit_through=fit_through))


# A neutral target date safely after the default fit_through (2024-12-31).
DEFAULT_TARGET_DATE = datetime.date(2025, 6, 1)

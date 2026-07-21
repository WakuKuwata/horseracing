"""Strong generation-binding for calibration activation (Feature 076, FR-019).

The activation loader lives in ``probability`` and cannot import ``training`` (cycle). The strong
generation check — recomputing the lgbm-063 recipe attestation from the resolved model directory and
comparing its ``attestation_digest`` to the manifest — IS training-specific. Callers that have
``training`` (serving / live / dispersion-pcal) build an ``AttestationVerifier`` here and
inject it into ``horseracing_probability.calib_activation.load_calibration``. This defends against
``save_model_version`` overwriting a same-named model version with different artifacts; the full
enforcement for every caller is generalised by the 077 content-addressed registry.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from horseracing_probability.calib_activation import ActivationError

from .legacy_attest import AttestationError, attestation_from_model_dir


def model_dir_attestation_verifier(
    active_model_dir: str | os.PathLike[str],
) -> Callable[[dict], None]:
    """Return a verifier that recomputes the attestation from ``active_model_dir`` and compares.

    The verifier reads ``manifest.code_sha`` (so the recompute uses the same code generation) and
    rebuilds the attestation via :func:`attestation_from_model_dir`; a digest mismatch — or an
    unreadable / malformed model directory — is a fail-closed :class:`ActivationError`.
    """

    def verify(manifest: dict) -> None:
        try:
            att = attestation_from_model_dir(
                active_model_dir, code_sha=manifest["code_sha"]
            )
        except AttestationError as exc:
            raise ActivationError(
                f"cannot recompute attestation from {active_model_dir!r}: {exc}"
            ) from exc
        recomputed = att["attestation_digest"]
        expected = manifest["attestation_digest"]
        if recomputed != expected:
            raise ActivationError(
                "attestation mismatch (save_model_version overwrite?): recomputed "
                f"{recomputed} != manifest {expected}"
            )

    return verify

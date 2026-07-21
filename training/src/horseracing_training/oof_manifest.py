"""Feature 078 US2 (T011): the FIRST production caller of build_manifest — generate a REAL OOF
calibration manifest.

Orchestrates: OOF two-gamma re-validation (``calibrate_oof``) + OOF stage-λ re-validation
(``calibrate_stage_oof``) → deployment final-fit on all-OOF, gated on each verdict
(``two_gamma_deployment_fit`` / ``stage_deployment_fit``) → assemble a v3 manifest whose
``activation_eligible`` the verifier recomputes → publish create-only, keyed by manifest_digest so a
re-evaluation of the same bundle never conflicts. Nothing here touches production
serving/betting/api
— it only produces a disk artifact (that a later, explicit 076 activation may read).
"""

from __future__ import annotations

from pathlib import Path


def build_oof_manifest(
    session,
    bundle: dict,
    *,
    attestation: dict,
    code_sha: str,
    out_root: Path | str,
    seed: int = 0,
    num_threads: int = 1,
    gate_config: dict | None = None,
    min_races: int | None = None,
    artifact_scope: str = "production",
    eval_races=None,
) -> tuple[Path, dict]:
    """Generate + publish the OOF calibration manifest. Returns ``(manifest_path, manifest)``.

    Deterministic for a fixed (bundle, attestation, code_sha, gate_config): the same inputs give the
    same manifest_digest and re-publishing is idempotent. ``eval_races`` injects the eval population
    for testing (else loaded from the DB, restricted to the bundle's races).
    """
    from horseracing_probability.calib_manifest import (
        build_manifest_v3,
        write_manifest_candidate,
    )
    from horseracing_probability.oof_calibration import (
        calibrate_oof,
        calibrate_stage_oof,
        stage_deployment_fit,
        two_gamma_deployment_fit,
    )

    attestation_digest = attestation["attestation_digest"]
    bundle_digest = bundle["bundle_digest"]

    # Provenance binding (codex D7, surfaced by the T015 real run): the manifest may only bind a
    # bundle that was GENERATED from this same recipe attestation. A mismatch means the OOF
    # predictions came from a different code generation — refuse rather than mislabel provenance.
    bundle_att = bundle.get("attestation_digest")
    if bundle_att is not None and bundle_att != attestation_digest:
        raise ValueError(
            f"attestation mismatch: bundle was generated from {bundle_att!r} but the supplied "
            f"attestation is {attestation_digest!r} — regenerate the bundle at this generation")

    # 1) verdicts from the pre-registered OOF gates (prequential, strictly-later blocks)
    tg_eval = calibrate_oof(session, bundle, gate_config=gate_config or {})
    st_eval = calibrate_stage_oof(
        session, bundle, gate_config=gate_config, min_races=min_races, eval_races=eval_races)
    tg_verdict, st_verdict = tg_eval["verdict"], st_eval["verdict"]

    # 2) SHIPPED params = a separate all-OOF fit, gated on the verdict (D2). Non-ADOPT → identity.
    tg_dep = two_gamma_deployment_fit(session, bundle, adopt=tg_verdict == "ADOPT")
    st_dep = stage_deployment_fit(
        session, bundle, adopt=st_verdict == "ADOPT", min_races=min_races)

    # 3) assemble v3 (eligibility recomputed by the verifier) + publish keyed by manifest_digest
    manifest = build_manifest_v3(
        attestation_digest=attestation_digest,
        bundle_digest=bundle_digest,
        evaluation={"two_gamma_win": tg_eval, "stage_discount_topk": st_eval},
        code_sha=code_sha, seed=seed, num_threads=num_threads,
        two_gamma_verdict=tg_verdict, two_gamma_params=tg_dep,
        two_gamma_fit_through=tg_dep["fit_through"],
        two_gamma_fit_race_set_hash=tg_dep["fit_race_set_hash"], two_gamma_n_fit=tg_dep["n_fit"],
        stage_verdict=st_verdict, stage_params=st_dep,
        stage_fit_through=st_dep["fit_through"],
        stage_fit_race_set_hash=st_dep["fit_race_set_hash"], stage_n_fit=st_dep["n_fit"],
        gate_config_hash=st_eval.get("gate_config_hash"),
        artifact_scope=artifact_scope,
    )
    path = write_manifest_candidate(out_root, manifest)
    return path, manifest

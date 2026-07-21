"""End-to-end serving: load model -> as-of features -> infer -> consistency -> persist.

Features come from Feature 004 ``build_feature_matrix(end_date=target_date)`` (started
population, leak-safe as-of, NOT build_training_matrix which reads race_results). History is
as-of each row's own race_date (same-day excluded), so result-pending future races are safe.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from horseracing_db.enums import EntryStatus
from horseracing_db.models import PredictionRun, Race, RaceHorse
from horseracing_eval.consistency import check_consistency
from horseracing_features.builder import build_feature_matrix, verify_materialized
from horseracing_features.registry import FEATURE_VERSION
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import SERVING_LOGIC_VERSION
from .model_loader import ServingError, load_serving_model
from .persistence import persist_run
from .predictor import predict_race


def _base_logic_version(model) -> str:
    """Feature 058 (案C'): when a model is served on the COMPAT path (its trained feature_version
    differs from the runtime registry), record the runtime registry version too, so a compat run is
    distinguishable in audit from a native run of that feature_version.

    Feature 060 (INV-M6): market-offset predictions carry ``mkt=logq`` so the market usage is
    auditable from the persisted logic_version alone."""
    lv = f"feat={model.feature_version};serve={SERVING_LOGIC_VERSION}"
    if model.feature_version != FEATURE_VERSION:
        lv += f";reg={FEATURE_VERSION}"
    if getattr(model, "market_offset", None) is not None:
        lv += ";mkt=logq"
    return lv


class MarketOffsetSkip(ServingError):
    """Feature 060: the target race lacks full odds coverage for a market-offset model.

    A TYPED skip — the race gets no prediction row (a silent no-offset prediction would drop
    the market base, INV-M4). Ordinary models never raise this."""


def _race_win_odds(session: Session, race_id: str) -> dict[str, float | None]:
    """Started horses' win odds of ONE race (the market-offset source, race_horses.odds)."""
    rows = session.execute(
        select(RaceHorse.horse_id, RaceHorse.odds)
        .where(RaceHorse.race_id == race_id)
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
    ).all()
    return {r.horse_id: (float(r.odds) if r.odds is not None else None) for r in rows}


@dataclass(frozen=True)
class ServingResult:
    prediction_run_id: object
    race_id: str
    model_version: str
    logic_version: str
    n_horses: int


def _targets(
    session: Session, race_id: str | None, date: datetime.date | None
) -> tuple[datetime.date, list[str]]:
    if race_id is not None:
        race = session.get(Race, race_id)
        if race is None or race.race_date is None:
            raise ServingError(f"race {race_id} not found or has no race_date")
        return race.race_date, [race_id]
    if date is not None:
        stmt = select(Race.race_id).where(Race.race_date == date).order_by(Race.race_id)
        race_ids = list(session.scalars(stmt))
        if not race_ids:
            raise ServingError(f"no races on {date.isoformat()}")
        return date, race_ids
    raise ServingError("either race_id or date is required")


def run_serving(
    session: Session,
    *,
    race_id: str | None = None,
    date: datetime.date | None = None,
    model_version: str | None = None,
    apply_stage_discount: bool = True,
    stage_discount: object | None = None,
    calib_manifest: str | None = None,
    calib_mode: str = "legacy-runtime",
    use_materialized: bool = False,
    materialized_path: str | None = None,
) -> list[ServingResult]:
    """Feature 049: ``apply_stage_discount`` (default ON) applies the top2/top3 Benter discount to
    the PERSISTED top2/top3 — DISPLAY-only (連対率/複勝率). Adopted for serving on the PRIMARY
    calibration gate (top3 ECE 0.019→0.0039, win byte-identical); the exotic BETTING path
    recomputes its joint from win p and does NOT read these values, so it stays λ=1 (user
    decision 2026-07-03: display-only). Under-sampled → identity (no-op).

    Feature 055: ``use_materialized`` reads the as-of feature block from the 025 parquet
    (bit-parity-guaranteed, fail-closed on stale/missing — never a silent in-memory fallback).
    Default False keeps the historical path byte-identical."""
    model = load_serving_model(session, model_version)
    target_date, race_ids = _targets(session, race_id, date)
    logic_version = _base_logic_version(model)

    feature_rows = build_feature_matrix(
        session, end_date=target_date,
        use_materialized=use_materialized, materialized_path=materialized_path,
        # Build only the columns this model reads (+ mandatory ones): predict_race slices
        # model.feature_cols anyway, so skipping candidate-only leaf blocks the model omits (e.g.
        # F02 for the features-017 active model) is byte-identical yet ~11s cheaper per matrix.
        wanted=frozenset(model.feature_cols),
        # Feature 072: compute as-of blocks for only the race(s) being served (byte-identical).
        target_race_ids=frozenset(race_ids),
    )
    present = set(feature_rows["race_id"].unique())
    # date-level cutoff matches the feature end_date; fit the discount once, strictly before it.
    # Feature 076 priority: OFF (apply_stage_discount=False) > explicit injection > manifest > fit.
    sd, calib_digest = _resolve_stage_discount(
        session, model_version=model.model_version, target_date=target_date,
        apply_stage_discount=apply_stage_discount, injected=stage_discount,
        calib_mode=calib_mode, manifest_path=calib_manifest,
    )

    results: list[ServingResult] = []
    for rid in race_ids:
        if rid not in present:  # no started horses / out of feature scope
            continue
        try:
            results.append(
                _predict_persist(
                    session, model, rid, feature_rows, logic_version, stage_discount=sd,
                    calib_digest=calib_digest,
                )
            )
        except MarketOffsetSkip:
            # Feature 060: single-race invocation surfaces the typed error to the caller;
            # date-mode skips the race (visibly) and continues with the rest of the day.
            if race_id is not None:
                raise
            print(f"skip (market-offset, no full odds): {rid}")
    return results


def _fit_stage_discount(session: Session, before_date):
    """Feature 049: walk-forward top2/top3 discount fit from persisted predictions strictly
    before ``before_date`` (raw model p — serving derivation has no two_gamma, so fit and apply
    share one distribution, research D4). Under-sampled → identity (no-op)."""
    from horseracing_probability.model_calibration import fit_product_stage_discount

    return fit_product_stage_discount(session, before_date=before_date, calibrator=None)


def _resolve_stage_discount(
    session: Session, *, model_version: str, target_date, apply_stage_discount: bool,
    injected, calib_mode: str, manifest_path: str | None,
):
    """Feature 076: pick the stage discount λ + its manifest digest (for the audit logic_version).

    Priority (data-model): ``apply_stage_discount=False`` (OFF) wins over everything → (None, None).
    Then an explicitly injected ``StageDiscount`` replaces the runtime fit. Then
    ``manifest-required`` reads λ from the artifact (fail-closed). Otherwise the legacy fit."""
    if not apply_stage_discount:
        return None, None
    if injected is not None:
        return injected, None
    if calib_mode == "manifest-required":
        act = _manifest_activation(
            session, model_version=model_version, target_date=target_date,
            manifest_path=manifest_path,
        )
        return act.stage_discount, act.digest12
    return _fit_stage_discount(session, target_date), None


def _manifest_activation(session: Session, *, model_version: str, target_date, manifest_path: str):
    """Feature 076 (US2): load the stage-discount λ from the immutable 074 manifest instead of the
    runtime ``_fit_stage_discount`` leak path (persisted predictions = non-OOS).

    Binds the manifest to the served model_version (name) + content-addressed digest.
    ``attestation_verifier=None``: the strong attestation recompute needs a REAL manifest whose
    ``attestation_digest`` matches the lgbm-063 artifacts; fixture manifests use a synthetic one
    so 076 (fixture-first) uses name+digest binding for every caller. The strong verifier
    (``training.calib_binding.model_dir_attestation_verifier`` over ``Path(mv.weights_uri).parent``)
    is wired-and-ready for the real-manifest follow-up (D11). WIN is untouched (stage discount is
    top2/top3 display only), so win byte-parity holds regardless of mode."""
    from horseracing_probability.calib_activation import Profile, load_calibration
    return load_calibration(
        manifest_path, active_model_version=model_version, target_date=target_date,
        profile=Profile.PRODUCTION, attestation_verifier=None,
    )


def _sdisc_lv(logic_version: str, sd) -> str:
    from horseracing_eval.stage_discount import logic_version_fragment

    frag = logic_version_fragment(sd)
    return f"{logic_version};{frag}" if frag else logic_version


def _predict_persist(
    session: Session, model, race_id: str, feature_rows, logic_version: str, stage_discount=None,
    calib_digest: str | None = None,
) -> ServingResult:
    """Predict one race + persist the run (shared by run_serving and run_serving_backfill).

    Identical per-race path so backfill predictions are byte-identical to run_serving (p-parity).
    Feature 049: ``stage_discount`` discounts top2/top3 (win unchanged); its λ is recorded in the
    persisted logic_version for audit/reproducibility.
    Feature 076: ``calib_digest`` (when the λ came from a manifest) is recorded too, so the
    persisted top2/top3 are traceable to the exact immutable artifact (V). WIN is untouched.
    """
    win_odds = None
    if getattr(model, "market_offset", None) is not None:
        win_odds = _race_win_odds(session, race_id)
        try:
            predictions, snapshots, explanations = predict_race(
                model, race_id, feature_rows, stage_discount=stage_discount, win_odds=win_odds
            )
        except ValueError as exc:
            if "odds coverage" in str(exc):  # predictor's fail-closed check -> typed skip
                raise MarketOffsetSkip(str(exc)) from exc
            raise
    else:
        predictions, snapshots, explanations = predict_race(
            model, race_id, feature_rows, stage_discount=stage_discount
        )
    logic_version = _sdisc_lv(logic_version, stage_discount)
    if calib_digest is not None:  # Feature 076: manifest provenance for the discounted top2/top3
        logic_version = f"{logic_version};calib={calib_digest};calibmode=manifest"
    check_consistency(predictions)  # fail-fast (INV-S2); nothing persisted on violation
    run_id = persist_run(
        session,
        race_id=race_id,
        model_version=model.model_version,
        logic_version=logic_version,
        feature_version=model.feature_version,
        predictions=predictions,
        snapshots=snapshots,
        explanations=explanations,  # Feature 040
    )
    return ServingResult(
        prediction_run_id=run_id, race_id=race_id, model_version=model.model_version,
        logic_version=logic_version, n_horses=len(predictions),
    )


@dataclass(frozen=True)
class BackfillCounts:
    generated: int = 0
    skip_exists: int = 0      # target model already has a run for the race (idempotent)
    skip_no_started: int = 0  # no started horses / out of feature scope
    error_days: int = 0       # days whose processing raised (isolated, not aborting)
    skip_no_odds: int = 0     # Feature 060: market-offset model, race lacks full odds

    def as_dict(self) -> dict:
        return {
            "generated": self.generated, "skip_exists": self.skip_exists,
            "skip_no_started": self.skip_no_started, "error_days": self.error_days,
            "skip_no_odds": self.skip_no_odds,
        }


def run_serving_backfill(
    session: Session,
    *,
    date_from: datetime.date,
    date_to: datetime.date,
    model_version: str | None = None,
    force: bool = False,
    apply_stage_discount: bool = True,
    stage_discount: object | None = None,
    calib_manifest: str | None = None,
    calib_mode: str = "legacy-runtime",
    use_materialized: bool = False,
    materialized_path: str | None = None,
) -> BackfillCounts:
    """Feature 044: generate predictions over a date range for the (single active) model.

    Per-DAY it rebuilds the feature matrix (end_date=day) and predicts via the SAME _predict_persist
    path as run_serving → p-parity. Idempotent: a race that already has a prediction_run for the
    resolved model_version is skipped (``force`` regenerates, append-only). Per-day exception
    isolation (one bad day doesn't abort the range). Returns reconciliation counts.

    Feature 055: with ``use_materialized`` the staleness fingerprint is verified ONCE up front
    (same parquet × same source state — re-verifying per day only re-pays the load); the per-day
    builds then skip the fingerprint but keep the frame-free compatibility checks. A verification
    failure aborts the whole run (fail-closed), it is NOT swallowed into error_days.
    """
    model = load_serving_model(session, model_version)
    logic_version = _base_logic_version(model)
    if use_materialized:
        verify_materialized(session, materialized_path)  # raises: missing/stale/incompatible
    # Feature 076: fail-closed load+verify BEFORE the per-day isolation loop (FR-022). Bound to the
    # served model_version; per-day temporal gating via assert_applies inside the loop (FR-021).
    calib_act = None
    if calib_mode == "manifest-required":
        calib_act = _manifest_activation(
            session, model_version=model.model_version, target_date=None,
            manifest_path=calib_manifest,
        )
    calib_digest = calib_act.digest12 if calib_act is not None else None
    gen = skip_exists = skip_no_started = error_days = skip_no_odds = 0

    day = date_from
    while day <= date_to:
        try:
            race_ids = list(
                session.scalars(
                    select(Race.race_id).where(Race.race_date == day).order_by(Race.race_id)
                )
            )
            if race_ids:
                feature_rows = build_feature_matrix(
                    session, end_date=day,
                    use_materialized=use_materialized, materialized_path=materialized_path,
                    skip_fingerprint_verify=use_materialized,  # verified once above
                    wanted=frozenset(model.feature_cols),  # skip leaf blocks the model omits
                    target_race_ids=frozenset(race_ids),  # Feature 072: only the day's races
                )
                present = set(feature_rows["race_id"].unique())
                # Feature 076 priority (as run_serving); a manifest whose window covers this
                # day fails closed here (per-day, isolated into error_days — FR-021).
                if not apply_stage_discount:
                    sd = None
                elif stage_discount is not None:
                    sd = stage_discount
                elif calib_act is not None:
                    calib_act.assert_applies(day)
                    sd = calib_act.stage_discount
                else:
                    sd = _fit_stage_discount(session, day)
                for rid in race_ids:
                    if rid not in present:
                        skip_no_started += 1
                        continue
                    if not force and _has_run_for_model(
                        session, rid, model.model_version, calib_digest=calib_digest
                    ):
                        skip_exists += 1
                        continue
                    try:
                        _predict_persist(
                            session, model, rid, feature_rows, logic_version, stage_discount=sd,
                            calib_digest=calib_digest,
                        )
                    except MarketOffsetSkip:
                        skip_no_odds += 1  # typed skip: no prediction row (INV-M4)
                        continue
                    gen += 1
        except Exception:  # noqa: BLE001 — one day must not abort the whole range
            session.rollback()
            error_days += 1
        day += datetime.timedelta(days=1)

    return BackfillCounts(
        generated=gen, skip_exists=skip_exists,
        skip_no_started=skip_no_started, error_days=error_days,
        skip_no_odds=skip_no_odds,
    )


def _has_run_for_model(
    session: Session, race_id: str, model_version: str, *, calib_digest: str | None = None
) -> bool:
    """True if the race already has a prediction_run for this model_version (idempotency).

    Feature 076 (codex 076-gap): the check is calibration-aware. In manifest mode a race that only
    has a LEGACY run for the model must still generate the manifest version (a bare model check
    would skip it and the manifest run would never be produced); a non-manifest backfill likewise
    ignores manifest runs. Runs are separate prediction_runs (unlike betting's in-run groups); the
    read API picks the latest, so serving needs no conflict-refusal — only correct gap-filling."""
    q = (
        select(PredictionRun.prediction_run_id)
        .where(PredictionRun.race_id == race_id)
        .where(PredictionRun.model_version == model_version)
    )
    if calib_digest is not None:
        q = q.where(PredictionRun.logic_version.contains(f";calib={calib_digest};"))
    else:
        q = q.where(~PredictionRun.logic_version.contains(";calib="))
    return session.scalars(q).first() is not None

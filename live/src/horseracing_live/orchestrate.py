"""Live-serving orchestration (Feature 019): guard → (optional) scrape → predict → recommend → report.

Reuses existing leak-safe paths: run_serving (006, as-of features, no results read), and Kelly
recommendations (016) on pre-race odds → 010 estimated (double-pseudo). Fail-closed: a non-valid /
already-run / incomplete race is rejected with no writes; missing odds skips the odds-dependent
recommendation step but keeps the prediction. Live Kelly is shadow (recorded, no real stakes). No
schema change. cutoff = race_date (no post-time column; date-level per Feature 004).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from horseracing_betting.kelly_recommend import generate_kelly_recommendations
from horseracing_betting.kelly_types import KellyConfig
from horseracing_db.enums import EntryStatus
from horseracing_db.models import Race, RaceHorse
from horseracing_serving.pipeline import run_serving
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import guards


@dataclass(frozen=True)
class LiveServeReport:
    race_id: str
    race_date: datetime.date | None
    mode: str                       # "live" | "rejected"
    rejected: bool
    reason: str
    guards: dict                    # guard name -> (ok, reason)
    scraped: dict | None            # {"entries": bool, "odds": bool} when scrape ran
    prediction_run_id: object | None
    n_horses: int
    n_recommendations: int
    recommend_skipped_reason: str | None
    odds_as_of: datetime.datetime | None
    computed_at: datetime.datetime
    shadow: bool                    # live Kelly = shadow (no real stakes)
    extra: dict = field(default_factory=dict)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _rejected(race_id, race_date, gmap, reason) -> LiveServeReport:
    return LiveServeReport(
        race_id=race_id, race_date=race_date, mode="rejected", rejected=True, reason=reason,
        guards=gmap, scraped=None, prediction_run_id=None, n_horses=0, n_recommendations=0,
        recommend_skipped_reason=None, odds_as_of=None, computed_at=_now(), shadow=True,
    )


def _odds_as_of(session: Session, race_id: str) -> datetime.datetime | None:
    return session.scalar(
        select(func.max(RaceHorse.updated_at))
        .where(RaceHorse.race_id == race_id)
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
    )


# --- Feature 065: prospective shadow-betting log collection -----------------------------------
@dataclass(frozen=True)
class ProspectiveReport:
    n_races: int
    generated: int = 0
    skip_not_pending: int = 0     # had results (before or after scrape) — not truly pre-race
    skip_no_odds: int = 0
    skip_no_run: int = 0
    skip_exists: int = 0          # this (race, model, prospective policy) already recorded
    skip_post_time: int = 0       # capture happened at/after post_time — not pre-race
    weak_pretime: int = 0         # generated but post_time unknown (weaker pre-race guarantee)
    errors: int = 0
    per_race: list[dict] = field(default_factory=list)


def collect_prospective(
    session: Session,
    *,
    race_ids: list[str],
    scrape_fn,
    win_odds_cap: float | None = None,
    calib_manifest: str | None = None,
    calib_mode: str = "legacy-runtime",
    now: datetime.datetime | None = None,
) -> ProspectiveReport:
    """Feature 065: record PROSPECTIVE win bets on FRESHLY-CAPTURED pre-race odds, with the capture
    discipline that keeps the closing-oracle out (codex).

    ``scrape_fn(session, race_id) -> capture_at | None``: performs a fresh pre-race odds capture
    (updates race_horses.odds for the result-pending race, 008) and returns the CAPTURE timestamp;
    None ⇒ capture failed → skip (never fall back to stale/closing odds). The recorded odds_asof IS
    this capture timestamp — never RaceHorse.updated_at (generic row freshness).

    Per race: (1) result-pending guard, (2) fresh scrape → capture_at, (3) RE-CHECK result-pending
    after scrape (race may have ended mid-flow), (4) post_time guard (capture < post_time; unknown ⇒
    weak_pretime flag), (5) advisory lock on (race, model, policy), (6) race-scoped idempotency
    across runs, (7) WIN prospective generation. Never uses the exotic Kelly path.
    """
    from horseracing_betting.cli import (
        _active_model_version,
        _has_win_group,
        _resolve_active_run,
    )
    from horseracing_betting.recommend import generate_recommendations
    from sqlalchemy import text

    # Feature 076 (076-gap): load the manifest two-gamma ONCE before the loop (fail-closed, bound to
    # the active model). Prospective races are result-pending (future) → always after the OOF window,
    # so the temporal check passes; target_date=None here, per-race is future by construction.
    prospective_pcal = _live_manifest_two_gamma(
        session, model_version=_active_model_version(session), target_date=None,
        calib_manifest=calib_manifest, calib_mode=calib_mode)

    now = now or _now()
    gen = weak = 0
    per_race: list[dict] = []
    counts = {"skip_not_pending": 0, "skip_no_odds": 0, "skip_no_run": 0,
              "skip_exists": 0, "skip_post_time": 0, "errors": 0}

    def _skip(rid, key, status):
        counts[key] += 1
        per_race.append({"race_id": rid, "status": status})

    for rid in race_ids:
        try:
            if not guards.is_result_pending(session, rid)[0]:
                _skip(rid, "skip_not_pending", "not_pending")
                continue
            capture_at = scrape_fn(session, rid)      # fresh pre-race odds capture
            if capture_at is None:
                _skip(rid, "skip_no_odds", "scrape_failed")
                continue
            # RE-CHECK after scrape: results may have landed mid-flow → not truly pre-race
            if not guards.is_result_pending(session, rid)[0]:
                _skip(rid, "skip_not_pending", "ended_mid_flow")
                continue
            if not guards.odds_present(session, rid)[0]:
                _skip(rid, "skip_no_odds", "no_odds")
                continue
            race = session.get(Race, rid)
            post_time = getattr(race, "post_time", None)
            is_weak = post_time is None
            if post_time is not None and capture_at >= post_time:
                _skip(rid, "skip_post_time", "post_time")
                continue
            # advisory lock on (race, policy) — check-then-insert is otherwise race-prone
            session.execute(text("SELECT pg_advisory_lock(hashtext(:k))"),
                            {"k": f"prospective:{rid}:{win_odds_cap}"})
            run_id = _resolve_active_run(session, rid)
            if run_id is None:
                _skip(rid, "skip_no_run", "no_run")
                continue
            if _has_win_group(session, run_id, win_odds_cap, prospective=True, race_id=rid):
                _skip(rid, "skip_exists", "exists")
                continue
            generate_recommendations(
                session, prediction_run_id=run_id, win_odds_cap=win_odds_cap,
                prospective=True, odds_asof=capture_at, p_calibrator=prospective_pcal,
            )
            gen += 1
            weak += int(is_weak)
            per_race.append({"race_id": rid, "status": "generated", "weak_pretime": is_weak,
                             "odds_asof": capture_at.isoformat()})
        except Exception as exc:  # noqa: BLE001 — one race must not abort the whole collection
            session.rollback()
            _skip(rid, "errors", f"error:{type(exc).__name__}")

    return ProspectiveReport(
        n_races=len(race_ids), generated=gen, weak_pretime=weak, per_race=per_race, **counts,
    )


def _live_manifest_two_gamma(session, *, model_version, target_date, calib_manifest, calib_mode):
    """Feature 076 (076-gap): the two-gamma calibrator from the manifest for the live/prospective
    recommendation lane, bound to the resolved run model + race date. None for legacy. Fail-closed
    (raises ActivationError/ManifestError). Result-pending (future) races are always after the OOF
    fit window, so the temporal check passes cleanly."""
    if calib_mode != "manifest-required":
        return None
    from horseracing_probability.calib_activation import Profile, load_calibration
    return load_calibration(
        calib_manifest, active_model_version=model_version, target_date=target_date,
        profile=Profile.PRODUCTION, attestation_verifier=None,
    ).two_gamma


def live_serve(
    session: Session,
    *,
    race_id: str,
    model_version: str | None = None,
    scrape_entries_url: str | None = None,
    scrape_odds_url: str | None = None,
    fetcher=None,
    recommend: bool = True,
    cfg: KellyConfig | None = None,
    p_calibrator=None,
    calib_manifest: str | None = None,
    calib_mode: str = "legacy-runtime",
    threshold: float = 1.0,
    top_k: int = 5,
) -> LiveServeReport:
    """Serve one upcoming (result-pending) race. See module docstring for the flow.

    Feature 076 (076-gap): ``manifest-required`` reads the serving stage-λ AND the recommendation
    two-gamma from the immutable manifest (fail-closed) instead of the runtime fits."""
    gmap: dict = {}
    race = session.get(Race, race_id)
    race_date = race.race_date if race else None

    # --- guards (pre-predict) ---
    for name, res in (
        ("valid_race_id", guards.valid_race_id(race_id)),
        ("result_pending", guards.is_result_pending(session, race_id)),
    ):
        gmap[name] = res
        if not res[0]:
            return _rejected(race_id, race_date, gmap, res[1])

    # --- optional scrape (URL-driven; race_id→URL auto-resolution is deferred) ---
    scraped = None
    if scrape_entries_url or scrape_odds_url:
        if fetcher is None:
            return _rejected(race_id, race_date, gmap, "scrape URL given but no fetcher provided")
        from horseracing_scrape.pipeline import scrape_entries, scrape_odds
        scraped = {"entries": False, "odds": False}
        if scrape_entries_url:
            scrape_entries(session, urls=[scrape_entries_url], fetcher=fetcher)
            scraped["entries"] = True
        if scrape_odds_url:
            scrape_odds(session, urls=[scrape_odds_url], fetcher=fetcher)
            scraped["odds"] = True
        race = session.get(Race, race_id)
        race_date = race.race_date if race else race_date

    res = guards.entries_complete(session, race_id)
    gmap["entries_complete"] = res
    if not res[0]:
        return _rejected(race_id, race_date, gmap, res[1])

    # --- predict (run_serving: as-of features, leak-safe, result-pending safe) ---
    serving_results = run_serving(
        session, race_id=race_id, model_version=model_version,
        calib_manifest=calib_manifest, calib_mode=calib_mode,
    )
    if not serving_results:
        return _rejected(race_id, race_date, gmap, "no prediction produced (race out of feature scope)")
    sr = serving_results[0]

    # --- recommend (odds-dependent; fail-closed on missing odds) ---
    n_rec = 0
    skip_reason = None
    if recommend:
        ores = guards.odds_present(session, race_id)
        gmap["odds_present"] = ores
        if ores[0]:
            # Feature 076: manifest two-gamma overrides the passed p_calibrator in manifest mode
            manifest_pcal = _live_manifest_two_gamma(
                session, model_version=sr.model_version, target_date=race_date,
                calib_manifest=calib_manifest, calib_mode=calib_mode)
            ids = generate_kelly_recommendations(
                session, prediction_run_id=sr.prediction_run_id, cfg=cfg or KellyConfig(),
                threshold=threshold, top_k=top_k, use_real_odds=True,
                p_calibrator=manifest_pcal if manifest_pcal is not None else p_calibrator,
            )
            n_rec = len(ids)
        else:
            skip_reason = ores[1]  # odds missing → predictions kept, no odds-dependent recs

    return LiveServeReport(
        race_id=race_id, race_date=race_date, mode="live", rejected=False, reason="ok",
        guards=gmap, scraped=scraped, prediction_run_id=sr.prediction_run_id, n_horses=sr.n_horses,
        n_recommendations=n_rec, recommend_skipped_reason=skip_reason,
        odds_as_of=_odds_as_of(session, race_id), computed_at=_now(), shadow=True,
    )


def list_pending(session: Session, *, date: datetime.date) -> list[str]:
    """Valid-id, result-pending races on a date (live-eligible)."""
    race_ids = session.scalars(
        select(Race.race_id).where(Race.race_date == date).order_by(Race.race_id)
    ).all()
    out = []
    for rid in race_ids:
        if guards.valid_race_id(rid)[0] and guards.is_result_pending(session, rid)[0]:
            out.append(rid)
    return out


@dataclass(frozen=True)
class RefreshReport:
    """Feature 050: one-command range refresh — predict backfill then recommend backfill."""

    date_from: datetime.date
    date_to: datetime.date
    predict: dict | None            # serving BackfillCounts as a dict; None = stage crashed
    predict_error: str | None
    recommend: dict | None          # betting recommend_backfill counts; None = stage crashed
    recommend_error: str | None


def _preflight_manifest(session: Session, *, calib_mode: str, manifest_path: str | None) -> None:
    """Feature 076 (T018): fail-closed ONCE, before either stage runs.

    Both ``run_serving_backfill`` and ``recommend_backfill`` already fail closed on a bad manifest,
    but doing so independently would surface the same root cause as two separate stage errors (and
    only after the prediction stage had already run). A structural + generation + scope check against
    the ACTIVE model here aborts the whole refresh up front (the recommendation `_active_model_version`
    lane and serving both bind to the active/served model). Per-day temporal gating still happens
    inside each stage (target_date=None here)."""
    if calib_mode != "manifest-required":
        return
    from horseracing_betting.cli import _active_model_version
    from horseracing_probability.calib_activation import Profile, load_calibration
    active_mv = _active_model_version(session)
    if active_mv is None:
        from horseracing_probability.calib_activation import ActivationError
        raise ActivationError("manifest-required refresh: no ACTIVE model to bind against")
    load_calibration(
        manifest_path, active_model_version=active_mv, target_date=None,
        profile=Profile.PRODUCTION, attestation_verifier=None,
    )  # raises ActivationError/ManifestError on any structural/generation/scope failure


def refresh_range(
    session: Session,
    *,
    date_from: datetime.date,
    date_to: datetime.date,
    force: bool = False,
    calib_manifest: str | None = None,
    calib_mode: str = "legacy-runtime",
    use_materialized: bool = False,
    materialized_path: str | None = None,
) -> RefreshReport:
    """Feature 050: bundled product update = predict backfill (044) THEN recommend backfill (043).

    Order matters: recommendations fit the walk-forward p calibrator on predictions strictly
    before each day (046/048), so the prediction stage must complete over the WHOLE range first.
    Both stages are idempotent with per-race/day exception isolation (existing behavior, no new
    logic). A crash of the prediction stage does NOT skip the recommendation stage (idempotent →
    safe; the error is reported). ``force`` re-generates predictions only (044 append-only).

    Feature 055: ``use_materialized`` propagates to the PREDICTION stage only (the recommendation
    stage builds no feature matrices — it reads persisted predictions).

    Feature 076: ``manifest-required`` reads the two-gamma / stage-λ from the immutable 074 manifest
    in BOTH stages (serving stage-discount λ, betting recommendation two-gamma). A preflight fails
    the whole refresh closed up front (before either stage) on a structurally-bad manifest.
    """
    from dataclasses import asdict

    from horseracing_betting.cli import recommend_backfill
    from horseracing_serving.pipeline import run_serving_backfill

    _preflight_manifest(session, calib_mode=calib_mode, manifest_path=calib_manifest)

    predict: dict | None = None
    predict_error: str | None = None
    try:
        predict = asdict(run_serving_backfill(
            session, date_from=date_from, date_to=date_to, force=force,
            calib_manifest=calib_manifest, calib_mode=calib_mode,
            use_materialized=use_materialized, materialized_path=materialized_path,
        ))
    except Exception as exc:  # noqa: BLE001 — stage isolation; recommend is idempotent-safe
        session.rollback()
        predict_error = f"{type(exc).__name__}: {exc}"

    recommend: dict | None = None
    recommend_error: str | None = None
    try:
        recommend = recommend_backfill(
            session, date_from=date_from, date_to=date_to,
            calib_mode=calib_mode, manifest_path=calib_manifest,
        )
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        recommend_error = f"{type(exc).__name__}: {exc}"

    return RefreshReport(
        date_from=date_from, date_to=date_to,
        predict=predict, predict_error=predict_error,
        recommend=recommend, recommend_error=recommend_error,
    )

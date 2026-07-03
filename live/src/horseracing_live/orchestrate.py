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
    threshold: float = 1.0,
    top_k: int = 5,
) -> LiveServeReport:
    """Serve one upcoming (result-pending) race. See module docstring for the flow."""
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
    serving_results = run_serving(session, race_id=race_id, model_version=model_version)
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
            ids = generate_kelly_recommendations(
                session, prediction_run_id=sr.prediction_run_id, cfg=cfg or KellyConfig(),
                threshold=threshold, top_k=top_k, use_real_odds=True, p_calibrator=p_calibrator,
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


def refresh_range(
    session: Session,
    *,
    date_from: datetime.date,
    date_to: datetime.date,
    force: bool = False,
) -> RefreshReport:
    """Feature 050: bundled product update = predict backfill (044) THEN recommend backfill (043).

    Order matters: recommendations fit the walk-forward p calibrator on predictions strictly
    before each day (046/048), so the prediction stage must complete over the WHOLE range first.
    Both stages are idempotent with per-race/day exception isolation (existing behavior, no new
    logic). A crash of the prediction stage does NOT skip the recommendation stage (idempotent →
    safe; the error is reported). ``force`` re-generates predictions only (044 append-only).
    """
    from dataclasses import asdict

    from horseracing_betting.cli import recommend_backfill
    from horseracing_serving.pipeline import run_serving_backfill

    predict: dict | None = None
    predict_error: str | None = None
    try:
        predict = asdict(run_serving_backfill(
            session, date_from=date_from, date_to=date_to, force=force,
        ))
    except Exception as exc:  # noqa: BLE001 — stage isolation; recommend is idempotent-safe
        session.rollback()
        predict_error = f"{type(exc).__name__}: {exc}"

    recommend: dict | None = None
    recommend_error: str | None = None
    try:
        recommend = recommend_backfill(session, date_from=date_from, date_to=date_to)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        recommend_error = f"{type(exc).__name__}: {exc}"

    return RefreshReport(
        date_from=date_from, date_to=date_to,
        predict=predict, predict_error=predict_error,
        recommend=recommend, recommend_error=recommend_error,
    )

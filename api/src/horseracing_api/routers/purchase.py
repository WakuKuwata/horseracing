"""purchase-records / purchase-comparison routers (Feature 106): read-only GET.

Everything here is computed at read time (research D3): the fold, per-bet settlement and the
triple comparison are derived from the append-only ``purchase_records`` rows plus official
results/dividends on every request — a correction, a void, or a real dividend arriving later is
reflected on the next GET with no stored state to migrate. The api boundary stays read-only and
never imports horseracing_betting.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_session
from ..purchase_fold import fold_purchase_records
from ..purchase_view import (
    COMPARISON_NOTES,
    ESTIMATOR_PROVENANCE,
    compare_race,
    load_race_rows,
    race_context,
    settle_bet,
)
from ..schemas import (
    ComparisonCoverage,
    ComparisonCumulative,
    ComparisonPending,
    ComparisonPoint,
    ComparisonSeries,
    PurchaseBetView,
    PurchaseComparisonResponse,
    PurchaseRecordsResponse,
    PurchaseRecordView,
)

router = APIRouter(tags=["purchase"])

_MAX_RANGE_DAYS = 400  # same guard as /coverage (052)


def _range_error(date_from: datetime.date, date_to: datetime.date) -> JSONResponse | None:
    if date_from > date_to:
        return JSONResponse(status_code=422, content={"detail": {
            "code": "invalid_range", "message": "from must be <= to"}})
    if (date_to - date_from).days > _MAX_RANGE_DAYS:
        return JSONResponse(status_code=422, content={"detail": {
            "code": "range_too_wide",
            "message": f"range must be <= {_MAX_RANGE_DAYS} days"}})
    return None


def _race_dates(session: Session, date_from, date_to) -> dict[str, datetime.date]:
    from horseracing_db.models import Race
    rows = session.execute(
        select(Race.race_id, Race.race_date)
        .where(Race.race_date >= date_from, Race.race_date <= date_to)
    ).all()
    return {rid: rdate for rid, rdate in rows}


@router.get("/purchase-records", response_model=PurchaseRecordsResponse)
def purchase_records(
    date_from: datetime.date = Query(alias="from"),
    date_to: datetime.date = Query(alias="to"),
    session: Session = Depends(get_session),
):
    err = _range_error(date_from, date_to)
    if err is not None:
        return err
    race_dates = _race_dates(session, date_from, date_to)
    by_race = load_race_rows(session, date_from, date_to)
    records: list[PurchaseRecordView] = []
    for race_id in sorted(by_race, key=lambda r: (race_dates.get(r, date_from), r)):
        rows = by_race[race_id]
        eff = fold_purchase_records(rows)
        if eff is None:
            continue
        rows_by_id = {r.purchase_record_id: r for r in rows}
        base = rows_by_id[eff.base_record_id]
        current = rows_by_id[eff.record_id]
        ctx = race_context(session, race_id)
        settled = [settle_bet(ctx, b) for b in (eff.bets or [])]
        records.append(PurchaseRecordView(
            race_id=race_id,
            race_date=race_dates.get(race_id, base.recorded_at.date()),
            record_id=str(eff.record_id),
            kind=base.kind,
            result_pending_at_record=eff.result_pending_at_record,
            recorded_at=base.recorded_at,
            n_corrections=eff.n_corrections,
            was_voided=eff.was_voided,
            bets=[PurchaseBetView(
                bet_type=s.bet_type, selection=s.selection, amount_yen=s.amount_yen,
                status=s.status, hit=s.hit, payout_yen=s.payout_yen,
                is_estimated=s.is_estimated,
            ) for s in settled],
            anomalies=list(eff.anomalies),
            note=current.note,
        ))
    return PurchaseRecordsResponse(records=records, n_races_recorded=len(records))


@router.get("/purchase-comparison", response_model=PurchaseComparisonResponse)
def purchase_comparison(
    date_from: datetime.date = Query(alias="from"),
    date_to: datetime.date = Query(alias="to"),
    scope: str = Query(default="all", pattern="^(all|win_only)$"),
    include_post_hoc: bool = Query(default=True),
    session: Session = Depends(get_session),
):
    err = _range_error(date_from, date_to)
    if err is not None:
        return err
    race_dates = _race_dates(session, date_from, date_to)
    by_race = load_race_rows(session, date_from, date_to)

    comparisons = []
    n_post_hoc = 0
    n_corrections = 0
    n_presentation_unavailable = 0
    n_recorded_pre = 0
    n_recorded_post = 0
    for race_id, rows in by_race.items():
        eff = fold_purchase_records(rows)
        if eff is None:
            continue
        base = {r.purchase_record_id: r for r in rows}[eff.base_record_id]
        if eff.result_pending_at_record:
            n_recorded_pre += 1
        else:
            n_recorded_post += 1
        ctx = race_context(session, race_id)
        cmp_row = compare_race(
            ctx, eff, race_id, race_dates.get(race_id, base.recorded_at.date()),
            scope=scope, snapshot=base.presented_snapshot, base_kind=base.kind,
        )
        n_corrections += cmp_row.n_corrections
        if cmp_row.presentation_unavailable:
            n_presentation_unavailable += 1
        if cmp_row.was_post_hoc:
            n_post_hoc += 1
            if not include_post_hoc:
                continue
        comparisons.append(cmp_row)

    comparisons.sort(key=lambda c: (c.race_date, c.race_id))

    actual_points: list[ComparisonPoint] = []
    policy_points: list[ComparisonPoint] = []
    cum_actual = 0.0
    cum_policy = 0.0
    policy_any = False
    n_pending_races = 0
    n_pending_bets = 0
    pending_amount = 0
    n_est = 0
    est_amount = 0
    for c in comparisons:
        if c.pending:
            n_pending_races += 1
            n_pending_bets += c.n_pending_bets
            pending_amount += c.pending_amount_yen
            continue
        n_est += c.n_estimated
        est_amount += c.estimated_amount_yen
        cum_actual += c.actual_net_yen or 0.0
        actual_points.append(ComparisonPoint(
            race_id=c.race_id, race_date=c.race_date,
            net_yen=c.actual_net_yen, cumulative_net_yen=cum_actual))
        if c.policy_net_yen is not None:
            policy_any = True
            cum_policy += c.policy_net_yen
        policy_points.append(ComparisonPoint(
            race_id=c.race_id, race_date=c.race_date,
            net_yen=c.policy_net_yen,
            cumulative_net_yen=cum_policy if policy_any else None))

    n_all = len(race_dates)
    coverage = ComparisonCoverage(
        overall=(n_recorded_pre + n_recorded_post) / n_all if n_all else None,
        pre_ingestion=n_recorded_pre / n_all if n_all else None,
        post_ingestion=n_recorded_post / n_all if n_all else None,
        n_all_races=n_all,
        n_recorded_races=n_recorded_pre + n_recorded_post,
    )
    return PurchaseComparisonResponse(
        as_of=datetime.datetime.now(datetime.UTC),
        scope=scope,
        include_post_hoc=include_post_hoc,
        series=ComparisonSeries(actual=actual_points, policy=policy_points, no_bet=0.0),
        cumulative=ComparisonCumulative(
            actual=cum_actual,
            policy=cum_policy,
            no_bet=0.0,
            diff_actual_vs_policy=(cum_actual - cum_policy) if policy_any else None,
            diff_actual_vs_no_bet=cum_actual,
        ),
        pending=ComparisonPending(
            n_races=n_pending_races, n_bets=n_pending_bets, amount_yen=pending_amount),
        coverage_rate=coverage,
        n_races=len(comparisons),
        n_estimated_settlements=n_est,
        estimated_amount_yen=est_amount,
        estimator_provenance=ESTIMATOR_PROVENANCE,
        n_post_hoc=n_post_hoc,
        n_corrections=n_corrections,
        n_presentation_unavailable=n_presentation_unavailable,
        notes=list(COMPARISON_NOTES),
    )

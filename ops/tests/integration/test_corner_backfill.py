"""通過順(corner_orders)の穴を、日次更新に相乗りして埋める。

netkeiba はレース当夜 `<td class="PassageRate">` を空で返し、通過順はおよそ 1 日後に入る(保存済み
ページ実測: lag 0 で 2.4% 充足、lag 1 で 99.8%)。`backfill_results` は当該列を NULL のときだけ
埋めるので、**後でもう一度訪れる以外に入る経路が無い**。その訪問を誰も予約していなかったため、
人が手で同じ日を再実行しない限り永久に NULL のままだった。

年齢基準(「lag 1 で取る」)ではなく穴基準にしてある: 2026-08-22 は 24 時間後でもまだ空で、固定
スケジュールなら取り逃してそれきりになっていた。
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import JobStatus, ResultStatus, Source
from horseracing_db.models import Horse, IngestionJob, Race, RaceHorse, RaceResult
from sqlalchemy import select

from horseracing_ops import JOB_TYPE_RACE
from horseracing_ops.enqueue import races_missing_corner_orders
from horseracing_ops.worker import claim_one, release_inflight

pytestmark = pytest.mark.integration

TODAY = datetime.date(2026, 8, 23)


def _race_with_result(session, race_id: str, *, race_date: datetime.date,
                      corners: list[str] | None) -> None:
    session.merge(Race(race_id=race_id, race_number=int(race_id[-2:]),
                       venue_code=race_id[4:6], race_date=race_date))
    hid = f"h-{race_id}"
    session.merge(Horse(horse_id=hid, horse_name=hid))
    session.merge(RaceHorse(race_id=race_id, horse_id=hid, horse_number=1))
    session.merge(RaceResult(race_id=race_id, horse_id=hid, finish_order=1,
                             result_status=ResultStatus.FINISHED, corner_orders=corners))
    session.commit()


def _gaps(session, **over) -> list[str]:
    kw = dict(today=TODAY, lookback_days=14, limit=36)
    kw.update(over)
    return races_missing_corner_orders(session, **kw)


def test_a_race_missing_its_passing_order_is_picked_up(session):
    _race_with_result(session, "202601020101", race_date=TODAY - datetime.timedelta(days=1),
                      corners=None)
    assert _gaps(session) == ["202601020101"]


def test_a_race_that_already_has_it_is_left_alone(session):
    _race_with_result(session, "202601020102", race_date=TODAY - datetime.timedelta(days=1),
                      corners=["3", "3", "2", "2"])
    assert _gaps(session) == []


def test_race_night_is_not_worth_a_request(session):
    """当夜は 2.4% しか入っていない。取りに行っても空が返るだけで、予算を捨てる。"""
    _race_with_result(session, "202601020103", race_date=TODAY, corners=None)
    assert _gaps(session) == []


def test_a_race_with_no_result_yet_is_not_a_gap(session):
    """結果が未確定なら通過順が無いのは当たり前で、穴ではない。"""
    session.merge(Race(race_id="202601020104", race_number=4, venue_code="01",
                       race_date=TODAY - datetime.timedelta(days=1)))
    session.commit()
    assert _gaps(session) == []


def test_the_window_stops_asking_for_races_netkeiba_will_never_fill(session):
    old = TODAY - datetime.timedelta(days=40)
    _race_with_result(session, "202601020105", race_date=old, corners=None)
    assert _gaps(session) == []
    assert _gaps(session, lookback_days=60) == ["202601020105"]


def test_zero_days_disables_it_entirely(session):
    _race_with_result(session, "202601020106", race_date=TODAY - datetime.timedelta(days=1),
                      corners=None)
    assert _gaps(session, lookback_days=0) == []
    assert _gaps(session, limit=0) == []


def test_the_cap_bounds_one_click_and_prefers_the_freshest(session):
    """穴は新しい日から埋める — 古い日ほど netkeiba が結局入れない可能性が高い。"""
    for i, days in enumerate([1, 2, 3], start=1):
        _race_with_result(session, f"20260102010{i}", race_date=TODAY - datetime.timedelta(days=days),
                          corners=None)
    assert _gaps(session, limit=2) == ["202601020101", "202601020102"]


def test_the_day_being_refreshed_is_excluded(session):
    """その日のレースは親の fan-out が既に積んでいる。上限を食わせない。"""
    d = TODAY - datetime.timedelta(days=1)
    _race_with_result(session, "202601020101", race_date=d, corners=None)
    assert _gaps(session, exclude_date=d) == []


# --- 順序: 埋め合わせがその日のレースを待たせてはいけない ---------------------------------------

def test_patch_up_never_claims_ahead_of_the_requested_day(session):
    """fan-out は 1 トランザクションなので子は created_at を共有する。FIFO では分離できず、
    UUID のタイブレークで 36 件の埋め合わせがその日のレースに割り込みうる。その日のオッズと
    exotic 価格はレースが終われば二度と手に入らないので、埋め合わせは常に後ろ。"""
    session.merge(Race(race_id="202601020111", race_number=11, venue_code="01", race_date=TODAY))
    session.merge(Race(race_id="202601020112", race_number=12, venue_code="01", race_date=TODAY))
    session.commit()
    # 埋め合わせを先に、同じ created_at になるよう同一トランザクションで積む
    for rid, origin in (("202601020112", "corner_backfill"), ("202601020111", "daily_bulk")):
        session.add(IngestionJob(
            source=Source.NETKEIBA, job_type=JOB_TYPE_RACE, scope="race", scope_value=rid,
            status=JobStatus.QUEUED, summary={"refresh_origin": origin},
        ))
    session.commit()

    first = claim_one(session, job_types=(JOB_TYPE_RACE,))
    try:
        assert first is not None
        assert first.summary["refresh_origin"] == "daily_bulk"
        assert first.scope_value == "202601020111"
    finally:
        release_inflight(first.ingestion_job_id)


def test_backfill_children_stay_out_of_the_days_batch(session):
    """親の batch は オペレータが頼んだ日を報告し続けなければならない(discovered/total の正直さ)。"""
    from horseracing_ops.enqueue import enqueue_race

    job, _ = enqueue_race(session, "202601020101", origin="corner_backfill")
    session.commit()
    assert job.trace_id is None

    rows = session.scalars(
        select(IngestionJob).where(IngestionJob.trace_id.is_not(None))
    ).all()
    assert all(r.summary.get("refresh_origin") != "corner_backfill" for r in rows if r.summary)

"""exotic の価格グリッドが取れなくなったら、いつかは失敗として出なければならない。

単勝オッズ側には `_has_started` による fail-closed 判定があり、発走済みのレースで値が無ければ
必ず failed で残る。exotic 側にはそれが無く、しかも **あらゆる ParseError を「まだ発売前」に
潰していた** ため、netkeiba がキー名や形を変えたら取得が静かに引退し、失敗ジョブが 1 件も出ない
まま気づけなくなる — 080 の相乗りが 5 日間死んでいたのと同じ形。

ここで固定するのは 2 つ:
  * 未発売(封筒は無事・グリッドが無い/値が入っていない)は今までどおり skipped。
    これは大半のレース生涯にわたる正常な状態なので、error にすると本物の失敗が埋もれる。
  * 形が読めない・封筒が無い、あるいは発走時刻を過ぎてなおグリッドが無いのは error。
"""

from __future__ import annotations

import datetime
import json

import pytest
from horseracing_db.models import Race

from horseracing_scrape.fetch import FixtureFetcher
from horseracing_scrape.pipeline import scrape_exotic_quotes
from horseracing_scrape.urls import exotic_quotes_url

pytestmark = pytest.mark.integration

RID = "202601010101"
BET = "quinella"
URL = exotic_quotes_url(RID, BET)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _seed(session, *, post_time) -> None:
    session.merge(Race(race_id=RID, race_number=1, venue_code=RID[4:6],
                       race_date=_now().date(), post_time=post_time))
    session.commit()


def _run(session, payload: str):
    return scrape_exotic_quotes(session, race_ids=[RID], bet_types=[BET],
                                fetcher=FixtureFetcher({URL: payload}), scope_value=RID)


#: the envelope is intact, this type simply has no grid yet (status="NG" two days out)
NOT_ON_SALE = json.dumps({"status": "NG", "reason": "history odds empty",
                          "data": {"official_datetime": None, "odds": {}}})
#: netkeiba always returns data.odds — its absence means the shape moved
BROKEN_ENVELOPE = json.dumps({"status": "result", "data": {"official_datetime": None}})
#: the grid is there but every row is shaped in a way we cannot read
BROKEN_ROWS = json.dumps({"status": "result",
                          "data": {"official_datetime": None,
                                   "odds": {"4": {"0102": {"odds": "5.0"}}}}})


def test_before_post_time_an_absent_grid_is_skipped(session):
    _seed(session, post_time=_now() + datetime.timedelta(hours=3))
    s = _run(session, NOT_ON_SALE)
    assert s.status == "skipped"
    assert s.errors == 0 and s.skipped == 1


def test_after_post_time_an_absent_grid_is_an_error(session):
    """発走時刻を過ぎれば全券種が発売済みのはず。ここで無いのは「まだ」ではなく異常。"""
    _seed(session, post_time=_now() - datetime.timedelta(minutes=30))
    s = _run(session, NOT_ON_SALE)
    assert s.status == "partial"
    assert s.errors == 1


def test_an_unrecognisable_envelope_fails_loudly_even_before_post_time(session):
    """形が変わったことは時刻と無関係。発走前でも error として出す。"""
    _seed(session, post_time=_now() + datetime.timedelta(hours=3))
    s = _run(session, BROKEN_ENVELOPE)
    assert s.status == "partial" and s.errors == 1


def test_rows_we_cannot_read_fail_loudly_even_before_post_time(session):
    _seed(session, post_time=_now() + datetime.timedelta(hours=3))
    s = _run(session, BROKEN_ROWS)
    assert s.status == "partial" and s.errors == 1


def test_an_unknown_race_fails_closed(session):
    """発走したか言えないレースは「まだ」と読まない(単勝側と同じ規約)。"""
    s = _run(session, NOT_ON_SALE)  # no Race row seeded
    assert s.status == "partial" and s.errors == 1


def test_a_priced_grid_still_lands(session):
    """厳しくした側だけ確認して終わると、正常系が壊れていても気づけない。"""
    _seed(session, post_time=_now() + datetime.timedelta(hours=3))
    payload = json.dumps({"status": "result",
                          "data": {"official_datetime": "2026-01-01 15:50:17",
                                   "odds": {"4": {"0102": ["5.0", "0.0", "1"]}}}})
    s = _run(session, payload)
    assert s.status == "succeeded" and s.errors == 0 and s.written == 1

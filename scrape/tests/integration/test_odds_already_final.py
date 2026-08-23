"""確定済みレースの単勝オッズを「取りに行かない」— ただし本物の欠落は必ず失敗のまま残す。

netkeiba のライブオッズ API はレースが確定すると group "1" 自体を返さなくなる。ops の
``run_one`` は状態に関係なく毎回 entries → results → odds を回すため、過去日を更新するたびに
1 レース 1 リクエストを必ず失敗に捨てていた(この DB の odds 失敗 314 件は全件が結果確定済み)。
しかも成功していたところで無意味である: 確定済みレースの ``update_odds`` は fill-if-null なので、
出走馬が全員値を持っていれば埋める先が無い。

**守りたいのは「静かに飲み込まないこと」**。出走馬に値の無い確定済みレースは今までどおり
リクエストし、今までどおり failed で残す — それは本物の欠落であり、JSON のキーが改名された
ときに現れる形そのものだからである。
"""

from __future__ import annotations

import datetime
import decimal

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Horse, Race, RaceHorse, RaceResult

from horseracing_scrape.fetch import FixtureFetcher
from horseracing_scrape.pipeline import _win_odds_already_final, scrape_odds
from horseracing_scrape.urls import win_odds_url

pytestmark = pytest.mark.integration

RID = "202601010101"
ODDS_PAGE = f"https://race.netkeiba.com/odds/index.html?race_id={RID}"
RACE_DATE = datetime.date(2026, 1, 5)

#: 取りに行ったら必ず落ちる fetcher。ゲートが短絡しなければテストが赤くなる
#: (= 「リクエストを出さない」ことをそのまま表明している)。
NO_FETCH = FixtureFetcher({})

#: 確定後の netkeiba が実際に返すもの: group "1" ごと消える。
SETTLED_JSON = '{"data":{"odds":{}}}'


def _seed_race(session, *, starters: int, priced: int, cancelled: int = 0,
               settled: bool = True) -> None:
    session.merge(Race(race_id=RID, race_number=1, venue_code=RID[4:6], race_date=RACE_DATE))
    n = 0
    for i in range(starters):
        n += 1
        hid = f"h{n}"
        session.merge(Horse(horse_id=hid, horse_name=hid))
        session.merge(RaceHorse(
            race_id=RID, horse_id=hid, horse_number=n,
            entry_status=EntryStatus.STARTED,
            odds=decimal.Decimal("3.4") if i < priced else None,
        ))
        if settled:
            session.merge(RaceResult(
                race_id=RID, horse_id=hid, finish_order=n,
                result_status=ResultStatus.FINISHED,
            ))
    for _ in range(cancelled):
        n += 1
        hid = f"h{n}"
        session.merge(Horse(horse_id=hid, horse_name=hid))
        # 取消馬にオッズが無いのは正常(実測: 2026 年の odds 欠損 108 件は全件 cancelled)
        session.merge(RaceHorse(race_id=RID, horse_id=hid, horse_number=n,
                                entry_status=EntryStatus.CANCELLED, odds=None))
    session.commit()


def test_settled_and_fully_priced_race_is_skipped_without_a_request(session):
    _seed_race(session, starters=3, priced=3)
    s = scrape_odds(session, urls=[ODDS_PAGE], fetcher=NO_FETCH)
    assert s.status == "skipped"      # succeeded でもない: 何も書いていないことが読み取れる
    assert s.errors == 0 and s.written == 0


def test_cancelled_horses_do_not_hold_the_race_open(session):
    """取消馬に値が無いのは欠落ではない。ここを見落とすと現実のレースが 1 件も短絡しない。"""
    _seed_race(session, starters=3, priced=3, cancelled=2)
    assert scrape_odds(session, urls=[ODDS_PAGE], fetcher=NO_FETCH).status == "skipped"


def test_settled_race_with_an_unpriced_starter_still_fetches_and_still_fails(session):
    """本 feature の存在理由。出走馬の欠落は本物なので、今までどおり失敗で残す。"""
    _seed_race(session, starters=3, priced=2)
    f = FixtureFetcher({win_odds_url(RID): SETTLED_JSON})
    assert scrape_odds(session, urls=[ODDS_PAGE], fetcher=f).status == "failed"


def test_pending_race_is_never_skipped(session):
    """未確定なら値が揃っていてもオッズは動く。ここを短絡させたら直前オッズが凍る。"""
    _seed_race(session, starters=3, priced=3, settled=False)
    assert _win_odds_already_final(session, RID) is False
    f = FixtureFetcher({win_odds_url(RID): SETTLED_JSON})
    # 未確定 + post_time 不明 = 発走済み扱い(fail-closed)なので、取りに行って失敗する
    assert scrape_odds(session, urls=[ODDS_PAGE], fetcher=f).status == "failed"


def test_race_with_no_entries_fails_closed(session):
    """出走構成が分からないレースは「揃っている」と言えない -> 短絡しない。"""
    session.merge(Race(race_id=RID, race_number=1, venue_code=RID[4:6], race_date=RACE_DATE))
    session.commit()
    assert _win_odds_already_final(session, RID) is False


def test_unknown_race_id_fails_closed(session):
    assert _win_odds_already_final(session, None) is False

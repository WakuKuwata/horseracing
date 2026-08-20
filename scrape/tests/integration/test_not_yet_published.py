"""発走前の取得を「失敗」と記録しない — ただし本物の破損は必ず失敗のまま残す。

運用は開催日の前(金曜など)に翌日以降のレースを先取りするため、結果表の無い result ページも、
単勝オッズがまだ出ていない JSON も、その時点では正常な状態である。それを failed として記録して
いたため 14 日で 380 行の偽の失敗が積み上がり、本物の失敗が運用画面(052/053)で埋もれていた。

**ここで守りたいのは「静かに飲み込まないこと」**。netkeiba が markup を変えたら、発走済みの
レースは依然 failed で出なければならない。判別できない条件(URL に race_id が無い・レース行が
無い・post_time が naive・post_time が無くレース当日)はすべて「発走済み」側に倒す。
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import Race

from horseracing_scrape.models import NotYetPublished, ParseError
from horseracing_scrape.pipeline import _has_started, scrape_odds, scrape_results
from tests._synth import FixtureFetcher

pytestmark = pytest.mark.integration

RID = "202601010101"
RESULT_URL = f"https://race.netkeiba.com/race/result.html?race_id={RID}"


def _seed(session, *, post_time=None, race_date=None):
    session.merge(Race(race_id=RID, race_number=1, venue_code=RID[4:6],
                       race_date=race_date, post_time=post_time))
    session.commit()


#: 発走前の result URL が返すもの: race_id は載っているが結果表 (table.RaceTable01) がまだ無い。
_PRE_RACE_HTML = (
    f'<html><head><link rel="canonical" href="https://race.netkeiba.com/race/result.html'
    f'?race_id={RID}"></head><body><div class="RaceList_Item">まだ結果はありません</div>'
    f'</body></html>'
)


def _no_result_page() -> FixtureFetcher:
    return FixtureFetcher({RESULT_URL: _PRE_RACE_HTML})


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


# --- 正常系: 未発走は skipped であって failed ではない ---------------------------------------

def test_future_race_is_skipped_not_failed(session):
    _seed(session, post_time=_now() + datetime.timedelta(hours=3),
          race_date=_now().date())
    s = scrape_results(session, urls=[RESULT_URL], fetcher=_no_result_page())
    assert s.status == "skipped"        # succeeded でもない: 何も書いていないことが読み取れる
    assert s.errors == 0
    assert s.skipped == 1


# --- fail-closed: 判別できない/発走済みは必ず失敗のまま ---------------------------------------

def test_started_race_still_fails(session):
    """markup が変わったらここが赤くなる。この 1 件が本 feature の存在理由。"""
    _seed(session, post_time=_now() - datetime.timedelta(hours=3),
          race_date=_now().date())
    s = scrape_results(session, urls=[RESULT_URL], fetcher=_no_result_page())
    assert s.status == "failed"


def test_grace_window_is_not_a_failure(session):
    """発走直後は結果掲載までに数分あるので、猶予内は失敗にしない。"""
    _seed(session, post_time=_now() - datetime.timedelta(minutes=5), race_date=_now().date())
    assert scrape_results(session, urls=[RESULT_URL], fetcher=_no_result_page()).status == "skipped"


def test_unknown_race_fails_closed(session):
    """レース行が無い = 発走したかどうか言えない -> 失敗側。"""
    s = scrape_results(session, urls=[RESULT_URL], fetcher=_no_result_page())
    assert s.status == "failed"


def test_url_without_race_id_fails_closed(session):
    _seed(session, post_time=_now() + datetime.timedelta(hours=3), race_date=_now().date())
    f = FixtureFetcher({"u": _PRE_RACE_HTML})
    assert scrape_results(session, urls=["u"], fetcher=f).status == "failed"


# --- 判別関数そのもの(codex が指摘した 2 つの穴) ----------------------------------------------

def test_race_day_without_post_time_counts_as_started(session):
    """日付は発走時刻ではない。当日を「まだ」と読むと、markup 破損を丸 1 日見逃す。"""
    _seed(session, post_time=None, race_date=_now().date())
    assert _has_started(session, RID, at=_now()) is True


def test_future_date_without_post_time_is_not_started(session):
    _seed(session, post_time=None, race_date=_now().date() + datetime.timedelta(days=2))
    assert _has_started(session, RID, at=_now()) is False


def test_naive_post_time_fails_closed(session):
    """naive な時刻を aware な now と比べるにはゾーンを捏造するしかない。捏造しない。"""
    _seed(session, post_time=None, race_date=_now().date() + datetime.timedelta(days=2))
    session.execute(
        Race.__table__.update().where(Race.race_id == RID).values(
            post_time=datetime.datetime(2030, 1, 1, 0, 0)))
    session.commit()
    row = session.get(Race, RID)
    if row.post_time.tzinfo is None:          # DB が naive を保つ場合のみ意味を持つ
        assert _has_started(session, RID, at=_now()) is True


# --- 例外の型 ---------------------------------------------------------------------------------

def test_not_yet_published_is_a_parse_error():
    """distinction を知らない呼び出し元は従来どおり fail-closed のまま。"""
    assert issubclass(NotYetPublished, ParseError)


# --- odds 側も同じ規約 -------------------------------------------------------------------------

def test_odds_not_on_sale_is_skipped(session):
    from horseracing_scrape.urls import win_odds_url
    _seed(session, post_time=_now() + datetime.timedelta(hours=3), race_date=_now().date())
    page = f"https://race.netkeiba.com/odds/index.html?race_id={RID}"
    f = FixtureFetcher({win_odds_url(RID): '{"data":{"odds":{}}}'})
    s = scrape_odds(session, urls=[page], fetcher=f)
    assert s.status == "skipped"
    assert s.errors == 0


def test_odds_after_post_time_still_fails(session):
    from horseracing_scrape.urls import win_odds_url
    _seed(session, post_time=_now() - datetime.timedelta(hours=1), race_date=_now().date())
    page = f"https://race.netkeiba.com/odds/index.html?race_id={RID}"
    f = FixtureFetcher({win_odds_url(RID): '{"data":{"odds":{}}}'})
    assert scrape_odds(session, urls=[page], fetcher=f).status == "failed"


# --- 馬主・生産者の補完対象選定 --------------------------------------------------------------

def test_horse_missing_only_owner_is_selected_for_completion(session):
    """sex/生年/血統が揃っていても、馬主が欠けていれば補完対象に入ること。

    この条件が無いと netkeiba 由来の 4,149 頭のうち 1 頭しか選ばれない — 他は識別情報が既に
    埋まっているため。対象の 97% は現役なので、通常運用の中で自然に埋まる。
    """
    from horseracing_db.models import Horse

    from horseracing_scrape import SURROGATE_PREFIX
    from horseracing_scrape.pipeline import complete_profiles

    nk = "2020999001"
    session.merge(Horse(horse_id=f"{SURROGATE_PREFIX}{nk}", horse_name="テスト",
                        sex="牡", birth_year=2020, sire_id="X", sire_name="父",
                        owner_name=None, breeder_name=None, data_source="netkeiba"))
    session.commit()

    seen: list[str] = []

    class _Recording:
        def get(self, url):
            seen.append(url)
            raise RuntimeError("fetch stopped — 選定されたことだけ確認する")

    complete_profiles(session, fetcher=_Recording())
    assert any(nk in u for u in seen), "馬主のみ欠けた馬が選ばれなかった"

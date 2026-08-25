"""通過順の patch-up は結果ページ 1 本だけで済ませる。

`corner_backfill` origin の refresh は「netkeiba が翌日ごろに埋める通過順」1 列を回収するために
だけ存在し、対象は結果行を持つ確定済みレースに限られる(選択クエリの条件)。確定済みレースの
出馬表・単勝オッズ・組合せ価格は取り直しても何も増えないので、取得は結果ページ 1 本に絞る。

取得予算は 1 リクエスト/分の共有スロットで、1 週末が生む欠落は約 72 レース。1 レース 3 リクエスト
だと上限 36 件/回 では追いつかない — このテストが守っているのは「追いつけること」そのものである。
"""

from __future__ import annotations

import pytest
from horseracing_db.enums import JobStatus
from horseracing_db.models import RaceResult
from horseracing_scrape.urls import entries_url, result_url, win_odds_url
from sqlalchemy import func, select, update

from horseracing_ops.enqueue import enqueue_race
from horseracing_ops.worker import drain
from tests.conftest import REAL_RID, RID_NO_FIXTURE

pytestmark = pytest.mark.integration


class CountingFetcher:
    def __init__(self, inner):
        self._inner = inner
        self.urls: list[str] = []

    def get(self, url: str, *, use_cache: bool = True) -> str:
        self.urls.append(url)
        return self._inner.get(url, use_cache=use_cache)


def _n(fetcher: CountingFetcher, url: str) -> int:
    return sum(1 for u in fetcher.urls if u == url)


def _corner_rows(session) -> int:
    return session.scalar(
        select(func.count()).select_from(RaceResult)
        .where(RaceResult.race_id == REAL_RID)
        .where(RaceResult.corner_orders.is_not(None))
    )


def test_patch_up_asks_only_for_the_result_page(session, fixture_fetcher):
    """本番と同じ状態から始める: 一度取り込み済みのレースの通過順だけが NULL のとき。

    patch-up の対象選択は「結果行があり、かつ全馬 corner_orders が NULL」なので、取込がまだの
    レースには絶対に掛からない。空 DB から始めると結果を保存する相手(出走馬)がおらず、
    測っているものが本番と別物になる。
    """
    counting = CountingFetcher(fixture_fetcher)

    # (1) 通常の取込(当夜ぶん相当)
    first, _ = enqueue_race(session, REAL_RID, origin="manual_ui")
    session.commit()
    drain(session, fetcher=counting)
    session.refresh(first)

    # (2) netkeiba が当夜は通過順を出さない状況を作る = 全馬 NULL に戻す
    session.execute(
        update(RaceResult).where(RaceResult.race_id == REAL_RID).values(corner_orders=None)
    )
    session.commit()
    assert _corner_rows(session) == 0, "前提: 通過順が全馬 NULL"

    # (3) patch-up
    counting.urls.clear()
    job, reused = enqueue_race(session, REAL_RID, origin="corner_backfill", force=True)
    session.commit()
    assert reused is False
    drain(session, fetcher=counting)
    session.refresh(job)

    assert _corner_rows(session) > 0, "通過順が実際に埋まる"
    assert _n(counting, result_url(REAL_RID)) == 1, "結果ページは 1 回だけ叩く"
    assert _n(counting, entries_url(REAL_RID)) == 0, "確定済みレースの出馬表は取り直さない"
    assert _n(counting, win_odds_url(REAL_RID)) == 0, "確定済みレースのオッズは取り直さない"
    assert len(counting.urls) == 1, "patch-up の総リクエストは 1 本(血統補完も走らせない)"
    assert job.status == JobStatus.SUCCEEDED
    assert (job.summary or {}).get("kind") == "results", "何を取った refresh かが記録に残る"
    assert "predict_job_id" not in (job.summary or {}), "patch-up は予測を先回りしない"
    assert (job.summary or {}).get("corner_state") == "filled", "目的を達したことが記録に残る"
    assert first.status == JobStatus.SUCCEEDED


def test_a_normal_refresh_still_does_the_full_pass(session, fixture_fetcher):
    """分岐は origin 限定であって、通常の refresh の挙動は 1 バイトも変えない。"""
    counting = CountingFetcher(fixture_fetcher)

    job, _ = enqueue_race(session, REAL_RID, origin="manual_ui")
    session.commit()
    drain(session, fetcher=counting)
    session.refresh(job)

    assert _n(counting, entries_url(REAL_RID)) == 1
    assert _n(counting, result_url(REAL_RID)) == 1
    assert _n(counting, win_odds_url(REAL_RID)) == 1
    assert job.status == JobStatus.SUCCEEDED
    assert (job.summary or {}).get("kind") == "entries+results+odds"


def test_a_users_click_upgrades_a_queued_patch_up_to_the_full_pass(session, fixture_fetcher):
    """origin は今や「何を取得するか」を決める。取り違えると利用者の更新が黙って痩せる。

    patch-up が QUEUED で待っているレースを利用者が「更新」した場合、そのジョブは manual_ui に
    昇格して全部を取りに行かなければならない。enqueue 側にこの昇格は元からあるが、これまでは
    predict の origin ラベルを変えるだけだった — この変更で「取得範囲そのもの」を左右するように
    なったので、ここで固定する。
    """
    counting = CountingFetcher(fixture_fetcher)

    patch_up, _ = enqueue_race(session, REAL_RID, origin="corner_backfill")
    session.commit()
    assert patch_up.status == JobStatus.QUEUED

    clicked, reused = enqueue_race(session, REAL_RID, origin="manual_ui")
    session.commit()
    assert reused is True, "同じジョブを引き継ぐ(二重取得はしない)"
    assert clicked.ingestion_job_id == patch_up.ingestion_job_id

    drain(session, fetcher=counting)
    session.refresh(clicked)

    assert _n(counting, entries_url(REAL_RID)) == 1, "利用者の更新は出馬表も取りに行く"
    assert _n(counting, win_odds_url(REAL_RID)) == 1
    assert (clicked.summary or {}).get("kind") == "entries+results+odds"


def test_a_patch_up_that_cannot_get_its_page_is_a_failure_not_a_partial(session, fixture_fetcher):
    """唯一の sub-step が落ちたら FAILED。PARTIAL は「一部は出来た」の意味で、ここでは嘘になる。"""
    counting = CountingFetcher(fixture_fetcher)

    job, _ = enqueue_race(session, RID_NO_FIXTURE, origin="corner_backfill")
    session.commit()
    drain(session, fetcher=counting)
    session.refresh(job)

    assert job.status == JobStatus.FAILED
    assert (job.summary or {}).get("kind") == "results"
    assert (job.summary or {}).get("corner_state") == "still_missing", (
        "取れなかったことが記録に残る — 静かに止まる経路にしない"
    )

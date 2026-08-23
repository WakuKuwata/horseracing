"""出馬表の再取得は事実を「更新」してよいが「消して」はいけない。

`run_one` は結果確定済みのレースでも毎回 `scrape_entries` を回すため、出馬表ページは同じレースに
対して何度も再パースされる。netkeiba が以前は出していた項目を出さなくなる状況(レイアウト変更・
確定後の別バリアント・未公表)は普通に起こり、パーサはそれを None にする。素の上書きだと、その
None が既存の良い値を消す — しかも NULL の列は「かつて値があった」ことを何も語らないので、消えた
ことに気づく手段が無い。

これは仮定の話ではない: 供給元切替で失われた `races.grade`(重賞)を修復した直後、通常の夜間
entries 再取得によって **77 行が巻き戻された**。

一方で本当に変化する事実(取消・乗り替わり・計不→実測体重)は値として来るので、従来どおり上書き
されなければならない。両方をここで固定する。
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import Race, RaceHorse
from sqlalchemy import select

from horseracing_scrape.models import ScrapedEntry, ScrapedEntryHorse, ScrapedRace, ScrapedRaceKey
from horseracing_scrape.upsert import upsert_entries

pytestmark = pytest.mark.integration

KEY = ScrapedRaceKey(year=2026, track_code="01", kai=2, nichime=1, race_no=1)
RID = "202601020101"
NK_HORSE = "2022106098"
RACE_DATE = datetime.date(2026, 1, 5)


def _race(**over) -> ScrapedRace:
    base = dict(
        key=KEY, race_date=RACE_DATE, distance=1600, track_type="turf",
        going="良", weather="晴", race_class="１勝", race_name="テストステークス",
        grade="G3", post_time=None, prize_money=550,
    )
    base.update(over)
    return ScrapedRace(**base)


def _horse(**over) -> ScrapedEntryHorse:
    base = dict(
        netkeiba_horse_id=NK_HORSE, horse_name="テストホース", frame=3, horse_number=5,
        netkeiba_jockey_id="01234", jockey_name="騎手A",
        netkeiba_trainer_id="05678", trainer_name="調教師A",
        weight=484, weight_diff=2, jockey_weight=56.0, sex="牡", age=4,
        entry_status=EntryStatus.STARTED,
    )
    base.update(over)
    return ScrapedEntryHorse(**base)


def _ingest(session, race: ScrapedRace, horse: ScrapedEntryHorse) -> None:
    upsert_entries(session, ScrapedEntry(race=race, horses=(horse,)))
    session.commit()


def _stored(session) -> tuple[Race, RaceHorse]:
    session.expire_all()  # the upsert wrote via Core; drop any stale ORM identity-map state
    race = session.get(Race, RID)
    horse = session.scalars(select(RaceHorse).where(RaceHorse.race_id == RID)).one()
    return race, horse


# --- 消してはいけない --------------------------------------------------------------------------

def test_a_degraded_reparse_does_not_erase_race_facts(session):
    """77 行巻き戻しの再現形: 2 回目のページが grade / race_class を出さない。"""
    _ingest(session, _race(), _horse())

    _ingest(session, _race(grade=None, race_class=None, race_name=None,
                           distance=None, track_type=None, going=None, weather=None), _horse())

    race, _ = _stored(session)
    assert race.grade == "G3"
    assert race.race_class == "１勝"
    assert race.race_name == "テストステークス"
    assert race.distance == 1600 and race.track_type == "turf"
    assert race.going == "良" and race.weather == "晴"


def test_a_degraded_reparse_does_not_erase_horse_facts(session):
    _ingest(session, _race(), _horse())

    _ingest(session, _race(), _horse(frame=None, horse_number=None, weight=None,
                                     weight_diff=None, jockey_weight=None, sex=None, age=None,
                                     netkeiba_jockey_id=None, netkeiba_trainer_id=None))

    _, rh = _stored(session)
    assert rh.frame == 3 and rh.horse_number == 5
    assert rh.weight == 484 and rh.weight_diff == 2
    assert float(rh.jockey_weight) == 56.0
    assert rh.sex == "牡" and rh.age == 4
    assert rh.jockey_id is not None and rh.trainer_id is not None


# --- 更新はできなければならない ----------------------------------------------------------------

def test_real_changes_still_overwrite(session):
    """取消・乗り替わり・計不→実測体重は「値」として来るので従来どおり上書きされる。

    ここが通らないと、消さない代わりに変化も反映されない別のバグになる。
    """
    _ingest(session, _race(), _horse(weight=None, weight_diff=None))  # 計不の状態
    _, rh = _stored(session)
    assert rh.weight is None

    _ingest(session, _race(going="重", weather="雨"),
            _horse(weight=490, weight_diff=6, netkeiba_jockey_id="09999", jockey_name="騎手B",
                   entry_status=EntryStatus.CANCELLED))

    race, rh = _stored(session)
    assert rh.weight == 490 and rh.weight_diff == 6
    assert rh.entry_status == EntryStatus.CANCELLED
    assert race.going == "重" and race.weather == "雨"
    jockey_id = rh.jockey_id
    assert jockey_id is not None and jockey_id.endswith("09999")


def test_prize_money_keeps_its_stronger_protection(session):
    """prize_money は fill_if_null(既存が勝つ)。never_blank に格下げされていないこと。"""
    _ingest(session, _race(prize_money=550), _horse())
    _ingest(session, _race(prize_money=9999), _horse())
    race, _ = _stored(session)
    assert race.prize_money == 550


def test_protection_is_not_pinned_to_a_column_list(session):
    """保護は列名の明示列挙ではなく「供給された全列」であること。

    列挙にすると、値 dict に列を足した次の瞬間に保護が黙って外れる — このテストはその退行を
    ソースの形で止める(挙動テストでは「まだ存在しない列」を検査できないため)。
    """
    import inspect

    src = inspect.getsource(upsert_entries)
    assert src.count("never_blank=NEVER_BLANK_ALL") == 2  # Race と RaceHorse の両方

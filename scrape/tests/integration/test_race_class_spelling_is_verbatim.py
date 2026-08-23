"""取込は `race_class` を供給元が書いたとおりに保存する。正規化してはいけない。

`race_class` はモデルに **生の categorical 文字列** として入る(feature_hash は列名だけを見る)。
つまり取込側で綴りを揃えると、**FEATURE_VERSION も feature_hash も動かないまま学習済みモデルの
入力空間が変わる** — 017 で踏んだのと同じ、値だけが静かに変わる罠になる。

そして揃えること自体に利益が無いことは測定済み(spec 098): 正準化して再学習しても
pooled +0.000443(正準の方がわずかに悪い)CI[−0.0026, +0.0034]、しかも netkeiba 由来が
過半の層では **+0.0053 悪化**。綴りは「どの供給元レジームの行か」という旗として働いており、
2026 年の行は 2019 年の行と実際に違う計測体制から来ている。分裂は半分バグで半分は情報。

`1勝` を `１勝` に(あるいはその逆に)揃える 1 行は、レビューで「明らかな清掃」に見える。
このテストはその 1 行を止めるためだけに存在する。揃えたくなったら、まず 098 の測定をやり直し、
FEATURE_VERSION を bump すること。
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import Race

from horseracing_scrape.models import ScrapedEntry, ScrapedEntryHorse, ScrapedRace, ScrapedRaceKey
from horseracing_scrape.upsert import upsert_entries

pytestmark = pytest.mark.integration

KEY = ScrapedRaceKey(year=2026, track_code="01", kai=2, nichime=1, race_no=1)
RID = "202601020101"

#: 実 DB に共存している同義語ペア。左が JRA-VAN 期、右が netkeiba 期。
CUTOVER_PAIRS = [("1勝", "１勝"), ("2勝", "２勝"), ("3勝", "３勝"), ("ｵｰﾌﾟﾝ", "オープン")]


def _ingest(session, race_class: str) -> str | None:
    race = ScrapedRace(
        key=KEY, race_date=datetime.date(2026, 1, 5), distance=1600, track_type="turf",
        going="良", weather="晴", race_class=race_class, race_name="テスト", grade=None,
        post_time=None, prize_money=550,
    )
    horse = ScrapedEntryHorse(
        netkeiba_horse_id="2022106098", horse_name="テストホース", frame=1, horse_number=1,
        netkeiba_jockey_id="01234", jockey_name="騎手A",
        netkeiba_trainer_id="05678", trainer_name="調教師A",
        weight=480, weight_diff=0, jockey_weight=56.0, sex="牡", age=4,
        entry_status=EntryStatus.STARTED,
    )
    upsert_entries(session, ScrapedEntry(race=race, horses=(horse,)))
    session.commit()
    session.expire_all()
    return session.get(Race, RID).race_class


@pytest.mark.parametrize("race_class", [p for pair in CUTOVER_PAIRS for p in pair])
def test_the_stored_spelling_is_the_one_the_source_wrote(session, race_class):
    assert _ingest(session, race_class) == race_class


def test_the_two_spellings_are_not_collapsed_into_one(session):
    """揃えた瞬間にこのテストが落ちる。落ちたら 098 を読み直すこと。"""
    for old, new in CUTOVER_PAIRS:
        assert _ingest(session, old) == old
        assert _ingest(session, new) == new
        assert old != new  # 同義だが別の文字列のまま


def test_race_class_is_still_a_raw_model_input(session):
    """この保護が要る理由そのもの。registry から外れたなら、このテストごと見直してよい。"""
    from horseracing_features.registry import model_input_features

    assert "race_class" in set(model_input_features())

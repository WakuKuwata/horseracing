"""④ profile completion: identity (profile page) + pedigree (ped page), leak-safe.

Validated against REAL captured netkeiba fixtures (db.netkeiba.com, EUC-JP) — see
fixtures/real/horse_profile_2022103995.html and pedigree_2022103995.html (Giovanni)."""

from __future__ import annotations

import dataclasses

import pytest

from horseracing_scrape.models import ParseError, ScrapedHorseProfile
from horseracing_scrape.parse._profile import parse_horse_pedigree, parse_horse_profile
from tests.conftest import real_fixture

# --- synthetic (focused) ----------------------------------------------------

_PROFILE = """
<div class="horse_title">
  <h1>テストホース</h1>
  <p class="txt_01">現役&nbsp;&nbsp;牡4歳&nbsp;&nbsp;鹿毛</p>
</div>
<table class="db_prof_table">
  <tr><th>生年月日</th><td>2019年3月15日</td></tr>
  <tr><th>通算成績</th><td>20戦8勝 [8-3-2-7]</td></tr>
</table>
<table class="db_h_race_results"><tr><td>2024.12.28</td><td>1着</td></tr></table>
"""

# blood_table: sire line = b_ml, dam line = b_fml; top cells (rowspan 16) are sire & dam;
# damsire = first b_ml after the dam cell.
_PED = """
<table class="blood_table detail">
  <tr>
    <td rowspan="16" class="b_ml"><a href="/horse/2005103461/">父サイアー</a>
      [<a href="/horse/ped/2005103461/">血統</a>]</td>
    <td rowspan="8" class="b_ml"><a href="/horse/1999100001/">父父</a></td>
  </tr>
  <tr><td rowspan="8" class="b_fml"><a href="/horse/1998100002/">父母</a></td></tr>
  <tr>
    <td rowspan="16" class="b_fml"><a href="/horse/2010104000/">母</a>
      [<a href="/horse/ped/2010104000/">血統</a>]</td>
    <td rowspan="8" class="b_ml"><a href="/horse/2000100000/">母父サイアー</a></td>
  </tr>
  <tr><td rowspan="8" class="b_fml"><a href="/horse/2001100003/">母母</a></td></tr>
</table>
"""


def test_parse_profile_identity_only():
    p = parse_horse_profile(_PROFILE, "2019104999")
    assert p.horse_name == "テストホース"
    assert p.sex == "牡"
    assert p.birth_year == 2019
    # pedigree is NOT on the profile page (JS-rendered there) — comes from the ped page
    assert p.netkeiba_sire_id is None and p.netkeiba_dam_id is None


def test_parse_pedigree_sire_dam_damsire():
    sire, dam, damsire = parse_horse_pedigree(_PED, "2019104999")
    assert sire == ("2005103461", "父サイアー")  # first horse link of the b_ml top cell
    assert dam[0] == "2010104000"
    assert damsire == ("2000100000", "母父サイアー")     # first b_ml AFTER the dam cell


def test_profile_carries_no_performance_fields():
    """Leak boundary: the dataclass exposes ONLY identity/attributes — never career stats.

    馬主・生産者はここに含まれる。ページ上では通算成績や獲得賞金と同じ表に並んでいるが性質が違う:
    **その馬の成績ではなく帰属**であり、出走前に確定していて結果に依存しない。特徴側の
    `asof_owner_win_rate` 等は as-of 集計で、strictly-before の担保はそちらが持つ。

    この集合を等値で固定してあるのは、フィールドを足したときに必ずここで止めるため。増やすときは
    「それは属性か成績か」を答えてから通すこと。"""
    fields = {f.name for f in dataclasses.fields(ScrapedHorseProfile)}
    assert fields == {
        "netkeiba_horse_id", "horse_name", "sex", "birth_year",
        "netkeiba_sire_id", "sire_name", "netkeiba_dam_id", "dam_name",
        "netkeiba_damsire_id", "damsire_name",
        "owner_name", "breeder_name",
    }


def test_profile_missing_name_fails_close():
    with pytest.raises(ParseError):
        parse_horse_profile("<div class='db_prof_table'></div>", "x")


def test_pedigree_missing_table_fails_close():
    with pytest.raises(ParseError):
        parse_horse_pedigree("<html><body>no pedigree</body></html>", "x")


# --- REAL fixtures (network-free, EUC-JP decoded) ---------------------------

def test_real_profile_fixture_identity():
    p = parse_horse_profile(real_fixture("horse_profile_2022103995.html"), "2022103995")
    assert p.horse_name == "ジョバンニ"   # EUC-JP decoded correctly (no mojibake)
    assert p.sex == "牡"
    assert p.birth_year == 2022


def test_real_pedigree_fixture():
    sire, dam, damsire = parse_horse_pedigree(
        real_fixture("pedigree_2022103995.html"), "2022103995"
    )
    assert sire[0] == "2010104155"          # エピファネイア
    assert "エピファネイア" in (sire[1] or "")
    assert dam[0] == "000a012102"           # Barefoot Lady (foreign, alphanumeric id)
    assert damsire[0] == "000a0111b5"       # Footstepsinthesand


# --- 馬主・生産者 (供給元切替の穴埋め) ---------------------------------------

_PROFILE_WITH_PARTIES = """
<div class="horse_title">
  <h1>テストホース</h1>
  <p class="txt_01">現役&nbsp;&nbsp;牡4歳&nbsp;&nbsp;鹿毛</p>
</div>
<table class="db_prof_table">
  <tr><th>生年月日</th><td>2019年3月15日</td></tr>
  <tr><th>調教師</th><td>杉山晴紀 (栗東)</td></tr>
  <tr><th>馬主</th><td>テスト商事</td></tr>
  <tr><th>生産者</th><td>テスト牧場</td></tr>
  <tr><th>通算成績</th><td>20戦8勝 [8-3-2-7]</td></tr>
</table>
"""


def test_owner_and_breeder_are_extracted():
    p = parse_horse_profile(_PROFILE_WITH_PARTIES, "nk:1")
    assert p.owner_name == "テスト商事"
    assert p.breeder_name == "テスト牧場"


def test_owner_and_breeder_from_real_fixture():
    """実ページから取れること。この 2 行は生年月日のために既に歩いている表の中にある。"""
    p = parse_horse_profile(real_fixture("horse_profile_2022103995.html"), "nk:2022103995")
    assert p.owner_name == "ＫＲジャパン"
    assert p.breeder_name == "タイヘイ牧場"


def test_missing_parties_are_none_not_empty_string():
    """欠損を空文字で埋めない。空文字だと『馬主なし』という実在の馬主として集計されてしまう。"""
    p = parse_horse_profile(_PROFILE, "nk:1")     # 馬主/生産者の行が無い
    assert p.owner_name is None
    assert p.breeder_name is None


@pytest.mark.parametrize("blank", ["", "-", "--", "―", "   "])
def test_blank_party_values_are_none(blank):
    html = _PROFILE_WITH_PARTIES.replace("<td>テスト商事</td>", f"<td>{blank}</td>")
    assert parse_horse_profile(html, "nk:1").owner_name is None


def test_parties_do_not_pull_in_career_stats():
    """リーク境界: 通算成績・獲得賞金は従来どおり読まない(見出しの部分一致で拾わないこと)。"""
    p = parse_horse_profile(_PROFILE_WITH_PARTIES, "nk:1")
    assert not any("戦" in str(v) or "勝" in str(v) for v in (p.owner_name, p.breeder_name))

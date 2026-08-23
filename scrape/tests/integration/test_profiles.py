"""④ profile completion (integration): fill-NULL-only on surrogate horses, never clobber JRA-VAN,
job audited. Leak-safe — only identity/pedigree written."""

from __future__ import annotations

import pytest
from horseracing_db.enums import JobStatus
from horseracing_db.models import Horse, IngestionJob
from sqlalchemy import select

from horseracing_scrape.fetch import FixtureFetcher
from horseracing_scrape.pipeline import complete_profiles, scrape_entries
from horseracing_scrape.urls import horse_pedigree_url, horse_profile_url
from tests.conftest import real_fixture

pytestmark = pytest.mark.integration

_PROFILE = """
<div class="horse_title"><h1>サラブレッド</h1><p class="txt_01">現役 牡4歳 鹿毛</p></div>
<table class="db_prof_table"><tr><th>生年月日</th><td>2020年4月1日</td></tr></table>
"""

_PED = """
<table class="blood_table detail">
  <tr><td rowspan="16" class="b_ml"><a href="/horse/2005103461/">父サイアー</a></td>
      <td rowspan="8" class="b_ml"><a href="/horse/1999100001/">父父</a></td></tr>
  <tr><td rowspan="16" class="b_fml"><a href="/horse/2010104000/">母</a></td>
      <td rowspan="8" class="b_ml"><a href="/horse/2000100000/">母父</a></td></tr>
</table>
"""


def _fetcher(netkeiba_id: str) -> FixtureFetcher:
    # complete_profiles fetches BOTH the identity page and the pedigree page
    return FixtureFetcher({
        horse_profile_url(netkeiba_id): _PROFILE,
        horse_pedigree_url(netkeiba_id): _PED,
    })


def test_complete_profiles_fills_null_attrs(session):
    session.add(Horse(horse_id="nk:2020100000", horse_name="サラブレッド", data_source="netkeiba"))
    session.commit()

    summary = complete_profiles(
        session, fetcher=_fetcher("2020100000"), netkeiba_horse_ids=["2020100000"]
    )
    assert summary.status == JobStatus.SUCCEEDED
    assert summary.written == 1

    horse = session.get(Horse, "nk:2020100000")
    assert horse.sex == "牡"
    assert horse.birth_year == 2020
    assert horse.sire_id == "nk:2005103461" and horse.sire_name == "父サイアー"
    assert horse.dam_id == "nk:2010104000"
    assert horse.damsire_id == "nk:2000100000"
    # job audited with parser_version
    job = session.scalar(select(IngestionJob).where(IngestionJob.job_type == "horse_profile"))
    assert job is not None and job.summary.get("parser_version")


def test_complete_profiles_never_clobbers_existing(session):
    # an existing (JRA-VAN-sourced) attribute must survive — only NULL columns are filled
    session.add(Horse(horse_id="nk:2020100000", horse_name="サラブレッド",
                      sex="セ", birth_year=2018, data_source="netkeiba"))
    session.commit()

    complete_profiles(session, fetcher=_fetcher("2020100000"),
                      netkeiba_horse_ids=["2020100000"])
    horse = session.get(Horse, "nk:2020100000")
    assert horse.sex == "セ"            # preserved (not overwritten with 牡)
    assert horse.birth_year == 2018     # preserved
    assert horse.sire_id == "nk:2005103461"  # NULL column still filled


_PROFILE_WITH_PARTIES = """
<div class="horse_title"><h1>サラブレッド</h1><p class="txt_01">現役 牡4歳 鹿毛</p></div>
<table class="db_prof_table">
  <tr><th>生年月日</th><td>2020年4月1日</td></tr>
  <tr><th>馬主</th><td>テスト牧場HD</td></tr>
  <tr><th>生産者</th><td>テスト生産牧場</td></tr>
</table>
"""


def test_pedigree_page_is_not_fetched_when_pedigree_already_known(session):
    """A horse that re-enters the pass only for owner/breeder (1e0d0b4) must cost ONE request:
    the fixture deliberately has no pedigree page, so a fetch attempt would surface as an error."""
    session.add(Horse(horse_id="nk:2020100000", horse_name="サラブレッド", data_source="netkeiba",
                      sex="牡", birth_year=2020,
                      sire_id="nk:2005103461", sire_name="父サイアー",
                      dam_id="nk:2010104000", dam_name="母",
                      damsire_id="nk:2000100000", damsire_name="母父"))
    session.commit()

    fetcher = FixtureFetcher({horse_profile_url("2020100000"): _PROFILE_WITH_PARTIES})
    summary = complete_profiles(session, fetcher=fetcher, netkeiba_horse_ids=["2020100000"])

    assert summary.status == JobStatus.SUCCEEDED
    assert summary.errors == 0          # no pedigree request was attempted
    assert summary.written == 1
    horse = session.get(Horse, "nk:2020100000")
    assert horse.owner_name == "テスト牧場HD"
    assert horse.breeder_name == "テスト生産牧場"
    assert horse.sire_id == "nk:2005103461"  # untouched


def test_pedigree_page_is_still_fetched_when_pedigree_unknown(session):
    """Guard must not regress the original path: unknown pedigree -> both pages -> filled."""
    session.add(Horse(horse_id="nk:2020100000", horse_name="サラブレッド", data_source="netkeiba",
                      sire_id="nk:2005103461"))  # partial: dam/damsire still NULL
    session.commit()

    summary = complete_profiles(session, fetcher=_fetcher("2020100000"),
                                netkeiba_horse_ids=["2020100000"])
    assert summary.errors == 0
    horse = session.get(Horse, "nk:2020100000")
    assert horse.dam_id == "nk:2010104000" and horse.damsire_id == "nk:2000100000"


def test_bloodline_line_is_derived_from_existing_horses(session):
    """sire_line/damsire_line never come from netkeiba; they are a function of the sire NAME in
    the horses we already hold, so profile completion derives them locally (zero requests)."""
    session.add(Horse(horse_id="2015100001", horse_name="既存産駒", data_source="jra_van",
                      sire_name="父サイアー", sire_line="サンデーサイレンス系",
                      damsire_name="母父", damsire_line="ノーザンダンサー系"))
    session.add(Horse(horse_id="2015100002", horse_name="別の産駒", data_source="jra_van",
                      sire_name="曖昧な父", sire_line="A系"))
    session.add(Horse(horse_id="2015100003", horse_name="また別の産駒", data_source="jra_van",
                      sire_name="曖昧な父", sire_line="B系"))
    session.add(Horse(horse_id="nk:2020100000", horse_name="サラブレッド", data_source="netkeiba"))
    session.add(Horse(horse_id="nk:2020100001", horse_name="曖昧な子", data_source="netkeiba",
                      sire_name="曖昧な父", sire_id="nk:1", dam_id="nk:2", damsire_id="nk:3"))
    session.commit()

    complete_profiles(session, fetcher=_fetcher("2020100000"), netkeiba_horse_ids=["2020100000"])
    horse = session.get(Horse, "nk:2020100000")
    assert horse.sire_line == "サンデーサイレンス系"       # derived via 父サイアー
    assert horse.damsire_line == "ノーザンダンサー系"      # derived via 母父

    complete_profiles(session, fetcher=FixtureFetcher({horse_profile_url("2020100001"): _PROFILE}),
                      netkeiba_horse_ids=["2020100001"])
    assert session.get(Horse, "nk:2020100001").sire_line is None  # ambiguous name -> never guessed


def test_complete_profiles_skips_horse_not_in_db(session):
    summary = complete_profiles(
        session, fetcher=_fetcher("2020109999"), netkeiba_horse_ids=["2020109999"]
    )
    assert summary.skipped == 1
    assert summary.written == 0


def test_scrape_entries_auto_completes_profiles(session):
    # entries ingestion auto-completes identity/pedigree for the surrogate horses it creates.
    # Horse 2022103995 (Giovanni) is 馬番1 of the entries fixture and has profile+ped fixtures;
    # the other 17 lack fixtures (their fetch fails, isolated) and stay null.
    fetcher = FixtureFetcher({
        "u": real_fixture("entries_202406050911.html"),
        horse_profile_url("2022103995"): real_fixture("horse_profile_2022103995.html"),
        horse_pedigree_url("2022103995"): real_fixture("pedigree_2022103995.html"),
    })
    summary = scrape_entries(session, urls=["u"], fetcher=fetcher)  # auto-complete ON (default)
    assert summary.status == JobStatus.SUCCEEDED   # entries unaffected by per-horse fetch failures

    horse = session.get(Horse, "nk:2022103995")
    assert horse.sex == "牡" and horse.birth_year == 2022
    assert "エピファネイア" in (horse.sire_name or "")   # pedigree auto-filled from the detail pages
    # a horse_profile completion job was recorded (audit)
    assert session.scalar(
        select(IngestionJob).where(IngestionJob.job_type == "horse_profile")
    ) is not None

"""④ profile completion (integration): fill-NULL-only on surrogate horses, never clobber JRA-VAN,
job audited. Leak-safe — only identity/pedigree written."""

from __future__ import annotations

import pytest
from horseracing_db.enums import JobStatus
from horseracing_db.models import Horse, IngestionJob
from sqlalchemy import select

from horseracing_scrape.fetch import FixtureFetcher
from horseracing_scrape.pipeline import complete_profiles
from horseracing_scrape.urls import horse_pedigree_url, horse_profile_url

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


def test_complete_profiles_skips_horse_not_in_db(session):
    summary = complete_profiles(
        session, fetcher=_fetcher("2020109999"), netkeiba_horse_ids=["2020109999"]
    )
    assert summary.skipped == 1
    assert summary.written == 0

"""Feature 139 (T007): ``repair-final-odds`` re-applies archived result pages, offline.

A synthetic archive in the fetcher's exact layout (``race.netkeiba.com/{sha16}/url.txt`` + one
``{UTC stamp}.html.gz`` per fetch) stands in for ``artifacts/scrape_archive``. The repair must read
only those files (no fetcher exists here at all), pick the newest SETTLED copy, write through the
same function as the results job, keep one bad race from touching the others, and write nothing on
``--dry-run``.
"""

from __future__ import annotations

import datetime
import gzip
import hashlib
from decimal import Decimal
from pathlib import Path

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Horse, OfficialWinPayout, Race, RaceHorse, RaceResult
from sqlalchemy import select

from horseracing_scrape import cli
from horseracing_scrape.final_odds_repair import index_result_archive, repair_final_odds
from horseracing_scrape.parse.results import parse_results
from horseracing_scrape.urls import entries_url, result_url
from tests.conftest import real_fixture

pytestmark = pytest.mark.integration

RID = "202602011206"                 # archived, settled
RID_NO_ARCHIVE = "202602011207"      # settled, never archived
RID_BROKEN = "202602011208"          # settled, archive copy is not gzip
RID_PENDING = "202602011209"         # in the window but not run yet (no result row)
DAY = datetime.date(2026, 7, 19)
FIXTURE = "results_202602011206.html"
WINNER = "nk:2023106420"
PRE_RACE = Decimal("4.1")


def _seed_race(session, race_id: str, *, settled: bool = True, race_date=DAY) -> None:
    session.merge(Race(race_id=race_id, race_number=int(race_id[-2:]), race_date=race_date,
                       venue_code="02"))
    if race_id == RID:
        for number, row in enumerate(parse_results(real_fixture(FIXTURE)).rows, start=1):
            horse_id = f"nk:{row.netkeiba_horse_id}"
            session.merge(Horse(horse_id=horse_id, horse_name=horse_id))
            session.add(RaceHorse(race_id=race_id, horse_id=horse_id, horse_number=number,
                                  entry_status=EntryStatus.STARTED, odds=PRE_RACE, popularity=9))
            if settled:
                session.add(RaceResult(race_id=race_id, horse_id=horse_id,
                                       finish_order=row.finish_order,
                                       result_status=ResultStatus.FINISHED))
    else:
        horse_id = f"H{race_id}"
        session.merge(Horse(horse_id=horse_id, horse_name=horse_id))
        session.add(RaceHorse(race_id=race_id, horse_id=horse_id, horse_number=1,
                              entry_status=EntryStatus.STARTED, odds=Decimal("9.9")))
        if settled:
            session.add(RaceResult(race_id=race_id, horse_id=horse_id, finish_order=1,
                                   result_status=ResultStatus.FINISHED))
    session.commit()


def _archive(root: Path, url: str, copies: dict[str, bytes], *, gz: bool = True) -> Path:
    folder = root / "race.netkeiba.com" / hashlib.sha256(url.encode()).hexdigest()[:16]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "url.txt").write_text(url + "\n", encoding="utf-8")
    for stamp, body in copies.items():
        (folder / f"{stamp}.html.gz").write_bytes(gzip.compress(body) if gz else body)
    return folder


def _pre_race_page(race_id: str) -> bytes:
    return (f'<html><head><meta charset="utf-8"><link rel="canonical" '
            f'href="{result_url(race_id)}"/></head><body>race_id={race_id}</body></html>'
            ).encode()


@pytest.fixture
def archive(tmp_path) -> Path:
    settled = real_fixture(FIXTURE).encode()
    older = real_fixture(FIXTURE).replace("340円", "330円").encode()   # an older settled copy
    _archive(tmp_path, result_url(RID), {
        "20260719T010000000000Z": _pre_race_page(RID),   # morning: no result table yet
        "20260719T080000000000Z": older,
        "20260719T090000000000Z": settled,               # the newest settled copy wins
        "20260719T090000000000Z-1": _pre_race_page(RID),  # never settled: passed over
    })
    _archive(tmp_path, result_url(RID_BROKEN), {"20260719T090000000000Z": b"not gzip"}, gz=False)
    # an entries page of the archived race is not a result page and must be ignored
    _archive(tmp_path, entries_url(RID_NO_ARCHIVE), {"20260719T090000000000Z": settled})
    return tmp_path


def _seed_all(session) -> None:
    for race_id in (RID, RID_NO_ARCHIVE, RID_BROKEN):
        _seed_race(session, race_id)
    _seed_race(session, RID_PENDING, settled=False)


def _winner(session) -> tuple:
    return tuple(session.execute(select(RaceHorse.odds, RaceHorse.popularity).where(
        RaceHorse.race_id == RID, RaceHorse.horse_id == WINNER)).one())


def _payouts(session) -> list[tuple]:
    return [tuple(r) for r in session.execute(
        select(OfficialWinPayout.race_id, OfficialWinPayout.horse_number,
               OfficialWinPayout.payout_yen, OfficialWinPayout.observed_at))]


def _run(database_url: str, archive_dir: Path, *extra: str) -> int:
    return cli.main(["repair-final-odds", "--from", "2026-07-19", "--to", "2026-07-19",
                     "--archive-dir", str(archive_dir), "--database-url", database_url, *extra])


def test_index_picks_result_pages_newest_first(archive):
    index = index_result_archive(archive)
    assert set(index) == {RID, RID_BROKEN}          # the entries page is not a result page
    assert [p.name for p in index[RID]] == [
        "20260719T090000000000Z-1.html.gz", "20260719T090000000000Z.html.gz",
        "20260719T080000000000Z.html.gz", "20260719T010000000000Z.html.gz",
    ]


def test_repair_writes_final_odds_and_payouts_from_the_archive(
    session, archive, database_url, capsys
):
    _seed_all(session)

    rc = _run(database_url, archive)

    out = capsys.readouterr().out.strip().splitlines()
    assert rc == 1                                   # the broken copy is an error ...
    assert out[-1] == ("FAILED: races=3 archived=1 odds_updated=16 payouts=1 "
                       "missing_archive=1 errors=1")
    assert any(RID_BROKEN in line for line in out if line.startswith("error:"))
    assert any(RID_NO_ARCHIVE in line for line in out if line.startswith("missing archive:"))
    session.expire_all()
    # ... but it did not stop the archived race (per-race isolation)
    assert _winner(session) == (Decimal("3.4"), 2)
    assert _payouts(session) == [
        (RID, 1, 340, datetime.datetime(2026, 7, 19, 9, 0, tzinfo=datetime.UTC))
    ]
    # the race without an archive kept its stored odds
    assert session.scalar(select(RaceHorse.odds).where(
        RaceHorse.race_id == RID_NO_ARCHIVE)) == Decimal("9.9")


def test_repair_twice_is_a_no_op_and_ok_without_errors(session, tmp_path, database_url, capsys):
    _seed_all(session)
    _archive(tmp_path, result_url(RID), {"20260719T090000000000Z": real_fixture(FIXTURE).encode()})

    assert _run(database_url, tmp_path) == 0
    first = capsys.readouterr().out.strip().splitlines()[-1]
    assert first == ("OK: races=3 archived=1 odds_updated=16 payouts=1 "
                     "missing_archive=2 errors=0")

    assert _run(database_url, tmp_path) == 0
    second = capsys.readouterr().out.strip().splitlines()[-1]
    assert second == ("OK: races=3 archived=1 odds_updated=0 payouts=0 "
                      "missing_archive=2 errors=0")


def test_dry_run_writes_nothing(session, archive, database_url, capsys):
    _seed_all(session)

    rc = _run(database_url, archive, "--dry-run")

    out = capsys.readouterr().out.strip().splitlines()
    assert rc == 1
    assert "# dry-run: nothing was written" in out
    assert out[-1] == ("FAILED: races=3 archived=1 odds_updated=16 payouts=1 "
                       "missing_archive=1 errors=1")
    session.expire_all()
    assert _winner(session) == (PRE_RACE, 9)
    assert _payouts(session) == []


def test_jra_van_era_race_in_the_window_keeps_its_odds(session, tmp_path):
    early = datetime.date(2025, 10, 5)
    _seed_race(session, RID, race_date=early)
    _archive(tmp_path, result_url(RID), {"20251005T090000000000Z": real_fixture(FIXTURE).encode()})

    rep = repair_final_odds(session, date_from=early, date_to=early, archive_dir=tmp_path)

    assert (rep.races, rep.archived, rep.odds_updated, rep.skipped_pre_netkeiba) == (1, 1, 0, 1)
    assert rep.errors == 0
    assert _winner(session) == (PRE_RACE, 9)


def test_page_for_another_race_is_an_error(session, tmp_path):
    _seed_race(session, RID_NO_ARCHIVE)
    # the folder claims RID_NO_ARCHIVE but the page inside is RID's
    _archive(tmp_path, result_url(RID_NO_ARCHIVE),
             {"20260719T090000000000Z": real_fixture(FIXTURE).encode()})

    rep = repair_final_odds(session, date_from=DAY, date_to=DAY, archive_dir=tmp_path)

    assert (rep.archived, rep.errors) == (0, 1)
    assert "is for race 202602011206" in rep.error_messages[0]


def test_relative_or_missing_archive_dir_is_refused(database_url, tmp_path):
    with pytest.raises(SystemExit, match="absolute"):
        _run(database_url, Path("artifacts/scrape_archive"))
    with pytest.raises(SystemExit, match="does not exist"):
        _run(database_url, tmp_path / "nope")


def test_archived_page_without_readable_odds_is_reported_per_race(
    session, tmp_path, database_url, capsys
):
    """R4: an archived settled page whose header no longer names 人気 / 単勝 yields no final odds;
    the stored pre-race odds stay, and the repair names the race instead of reporting a clean
    ``odds_updated=0``."""
    _seed_race(session, RID)
    html = (real_fixture(FIXTURE)
            .replace("<th>人<br>気</th>", "<th>支持</th>")
            .replace('<th class="Odds">単勝<br>オッズ</th>', '<th class="Odds">倍率</th>'))
    _archive(tmp_path, result_url(RID), {"20260719T090000000000Z": html.encode()})

    assert _run(database_url, tmp_path) == 0          # not an error: the payout still landed
    out = capsys.readouterr().out.strip().splitlines()
    assert f"odds missing: {RID}" in out
    assert ("detail: skipped_pre_netkeiba=0 race_unknown=0 payout_missing=0 odds_missing=1 "
            "odds_unreadable=16 odds_unmatched=0") in out
    assert out[-1] == ("OK: races=1 archived=1 odds_updated=0 payouts=1 "
                       "missing_archive=0 errors=0")
    session.expire_all()
    assert _winner(session) == (PRE_RACE, 9)


def test_repair_report_counts_unreadable_and_unmatched_horses(session, tmp_path):
    _seed_race(session, RID)
    session.execute(RaceHorse.__table__.delete().where(
        RaceHorse.race_id == RID, RaceHorse.horse_id == "nk:2023102408"))
    session.commit()
    page = real_fixture(FIXTURE)
    html = page.replace('<span  class="Odds_Ninki">3.4</span>',
                        '<span  class="Odds_Ninki">---</span>', 1)
    assert html != page
    _archive(tmp_path, result_url(RID), {"20260719T090000000000Z": html.encode()})

    rep = repair_final_odds(session, date_from=DAY, date_to=DAY, archive_dir=tmp_path)

    assert (rep.odds_unreadable, rep.odds_unmatched, rep.odds_missing, rep.race_unknown) == (
        1, 1, 0, 0)
    assert rep.odds_updated == 14
    assert rep.odds_missing_race_ids == []
    session.expire_all()
    assert _winner(session) == (PRE_RACE, 9)    # no odds read → neither odds nor popularity move

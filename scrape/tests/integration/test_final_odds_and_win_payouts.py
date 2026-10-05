"""Feature 139 (T006 / SC-002): the results job writes FINAL win odds and the official 単勝 payout.

The bug (R05): ops refreshes entries -> results -> odds; once a result row exists ``update_odds``
only fills NULLs and the settled race's odds endpoint serves no win odds, so a netkeiba-era race
keeps its last PRE-RACE odds. The result page carries the final odds and the 単勝 payout; these
tests pin how they are written (plan D8/D9/D10): netkeiba era only, numbers only, changed rows only,
idempotent, isolated from the stored results.
"""

from __future__ import annotations

import datetime
import hashlib
import re
import uuid
from decimal import Decimal

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import (
    Horse,
    IngestionJob,
    MarketEvPrediction,
    OfficialWinPayout,
    Race,
    RaceHorse,
    RaceResult,
)
from sqlalchemy import func, select

from horseracing_scrape import pipeline
from horseracing_scrape.fetch import FixtureFetcher
from horseracing_scrape.models import ScrapedWinPayout
from horseracing_scrape.upsert import (
    NETKEIBA_FINAL_ODDS_FROM,
    apply_final_odds,
    upsert_official_win_payouts,
)
from tests._synth import H_WINNER, REAL_RID, real_entries_fetcher, real_results_fetcher
from tests.conftest import real_fixture

pytestmark = pytest.mark.integration

RID = "202602011206"           # 函館 6R 2026-07-19 (netkeiba era)
FIXTURE = "results_202602011206.html"
#: netkeiba horse id -> (馬番, final win odds, popularity) as printed on the real result page
FINAL = {
    "2023106420": (1, Decimal("3.4"), 2),
    "2023102408": (9, Decimal("14.0"), 5),
    "2023100924": (10, Decimal("2.8"), 1),
    "2023104140": (2, Decimal("44.7"), 12),
    "2023102783": (5, Decimal("17.4"), 6),
    "2023110027": (8, Decimal("110.7"), 15),
    "2023101318": (12, Decimal("19.8"), 7),
    "2023103478": (3, Decimal("7.1"), 3),
    "2023100794": (6, Decimal("10.2"), 4),
    "2023105777": (4, Decimal("102.8"), 14),
    "2023105284": (11, Decimal("25.0"), 8),
    "2023103518": (16, Decimal("28.9"), 9),
    "2023101873": (7, Decimal("158.9"), 16),
    "2023102586": (14, Decimal("30.0"), 10),
    "2023107515": (15, Decimal("40.5"), 11),
    "2023105278": (13, Decimal("97.8"), 13),
}
WINNER = "nk:2023106420"
SCRATCHED = "nk:2023199999"   # 取消 before the race: not on the result table at all


def _pre_race(odds: Decimal) -> Decimal:
    """A plausible last pre-race quote that differs from the final one."""
    return (odds * Decimal("1.2")).quantize(Decimal("0.1"))


def _seed(session, *, race_date=datetime.date(2026, 7, 19)) -> None:
    session.merge(Race(race_id=RID, race_number=6, race_date=race_date, venue_code="02"))
    for nk_id, (number, final, popularity) in FINAL.items():
        horse_id = f"nk:{nk_id}"
        session.merge(Horse(horse_id=horse_id, horse_name=horse_id))
        session.add(RaceHorse(race_id=RID, horse_id=horse_id, horse_number=number,
                              entry_status=EntryStatus.STARTED, odds=_pre_race(final),
                              popularity=17 - popularity))
    session.merge(Horse(horse_id=SCRATCHED, horse_name=SCRATCHED))
    session.add(RaceHorse(race_id=RID, horse_id=SCRATCHED, horse_number=17,
                          entry_status=EntryStatus.CANCELLED, odds=Decimal("55.5"),
                          popularity=12))
    session.commit()


def _scrape(session, html: str | None = None):
    html = html if html is not None else real_fixture(FIXTURE)
    return pipeline.scrape_results(session, urls=["u"], fetcher=FixtureFetcher({"u": html}))


def _odds(session) -> dict[str, tuple[Decimal | None, int | None]]:
    return {h: (o, p) for h, o, p in session.execute(
        select(RaceHorse.horse_id, RaceHorse.odds, RaceHorse.popularity)
        .where(RaceHorse.race_id == RID))}


def _payouts(session) -> list[tuple[int, int, str]]:
    return [tuple(r) for r in session.execute(
        select(OfficialWinPayout.horse_number, OfficialWinPayout.payout_yen,
               OfficialWinPayout.source)
        .where(OfficialWinPayout.race_id == RID).order_by(OfficialWinPayout.horse_number))]


def _n_results(session, race_id=RID) -> int:
    return session.scalar(select(func.count()).select_from(RaceResult)
                          .where(RaceResult.race_id == race_id))


def _last_results_job(session) -> IngestionJob:
    return session.scalars(select(IngestionJob).where(IngestionJob.job_type == "results")
                           .order_by(IngestionJob.created_at.desc())).first()


def test_pre_race_odds_are_overwritten_by_final_and_payout_is_stored(session):
    _seed(session)
    assert _odds(session)[WINNER] == (Decimal("4.1"), 15)   # the R05 state: pre-race values

    summary = _scrape(session)

    assert summary.status == "succeeded"
    assert _n_results(session) == 16
    odds = _odds(session)
    for nk_id, (_, final, popularity) in FINAL.items():
        assert odds[f"nk:{nk_id}"] == (final, popularity), nk_id
    assert odds[SCRATCHED] == (Decimal("55.5"), 12)          # a non-starter is never touched
    assert _payouts(session) == [(1, 340, "netkeiba_result")]
    stored = session.scalars(select(OfficialWinPayout)).one()
    assert stored.html_sha256 == hashlib.sha256(real_fixture(FIXTURE).encode()).hexdigest()
    assert stored.observed_at.tzinfo is not None
    # the payout and the final odds agree (340円 = 3.4 x 100)
    assert odds[WINNER][0] * 100 == stored.payout_yen

    assert summary.extra["final_odds_updated"] == 16
    assert summary.extra["win_payouts"] == 1
    assert summary.extra["final_odds_errors"] == 0 and summary.extra["win_payout_errors"] == 0
    job = _last_results_job(session)
    assert job.summary["final_odds_updated"] == 16 and job.summary["win_payouts"] == 1


def test_running_twice_changes_nothing(session):
    _seed(session)
    _scrape(session)
    before = {h: u for h, u in session.execute(
        select(RaceHorse.horse_id, RaceHorse.updated_at).where(RaceHorse.race_id == RID))}
    payout_before = session.scalars(select(OfficialWinPayout.updated_at)).one()

    again = _scrape(session)

    assert again.status == "succeeded"
    assert again.extra["final_odds_updated"] == 0 and again.extra["win_payouts"] == 0
    after = {h: u for h, u in session.execute(
        select(RaceHorse.horse_id, RaceHorse.updated_at).where(RaceHorse.race_id == RID))}
    assert after == before                       # no row was rewritten, so no trigger fired
    assert session.scalars(select(OfficialWinPayout.updated_at)).one() == payout_before


def test_frozen_values_elsewhere_do_not_move(session):
    """137/138 freeze the odds they used in their own rows; overwriting race_horses must not
    reach them (the v1 reference settlement stays what it was)."""
    _seed(session)
    session.add(MarketEvPrediction(
        race_id=RID, horse_id=WINNER, model_version="mev-ens15-v1", horse_number=1,
        win_prob=Decimal("0.3"), odds_used=Decimal("4.1"), expected_return=Decimal("1.23"),
        odds_observed_at=datetime.datetime(2026, 7, 19, 3, 0, tzinfo=datetime.UTC),
        result_pending_at_compute=True, booster="b", booster_sha256="0" * 64,
        logic_version="t", run_id=uuid.uuid4(),
    ))
    session.commit()
    _scrape(session)
    assert session.scalars(select(MarketEvPrediction.odds_used)).one() == Decimal("4.1")
    assert _odds(session)[WINNER][0] == Decimal("3.4")


def test_jra_van_era_race_is_never_touched(session):
    """Before NETKEIBA_FINAL_ODDS_FROM the stored odds are JRA-VAN's final odds: a refresh from the
    result page must not replace them, even with the page's own (equally final) numbers."""
    _seed(session, race_date=NETKEIBA_FINAL_ODDS_FROM - datetime.timedelta(days=6))  # 2025-10-05
    before = _odds(session)

    summary = _scrape(session)

    assert _n_results(session) == 16
    assert _odds(session) == before
    assert summary.extra["final_odds_updated"] == 0
    assert summary.extra["final_odds_skipped_pre_netkeiba"] == 1
    assert _payouts(session) == [(1, 340, "netkeiba_result")]   # a payout is a fact either way


def test_real_jra_van_race_keeps_its_odds(session):
    """The real Hopeful S flow (2024-12-28): entries -> stored final odds -> results."""
    ef, eurls = real_entries_fetcher()
    pipeline.scrape_entries(session, urls=eurls, fetcher=ef, complete_profiles_after=False)
    session.execute(RaceHorse.__table__.update().where(RaceHorse.race_id == REAL_RID)
                    .values(odds=Decimal("2.5"), popularity=4))
    session.commit()

    rf, rurls = real_results_fetcher()
    summary = pipeline.scrape_results(session, urls=rurls, fetcher=rf)

    assert summary.status == "succeeded"
    assert summary.extra["final_odds_skipped_pre_netkeiba"] == 1
    assert summary.extra["win_payout_missing"] == 1          # the trimmed page has no 単勝 row
    winner = session.execute(select(RaceHorse.odds, RaceHorse.popularity).where(
        RaceHorse.race_id == REAL_RID, RaceHorse.horse_id == H_WINNER)).one()
    assert tuple(winner) == (Decimal("2.5"), 4)


def test_boundary_day_is_netkeiba_era(session):
    _seed(session, race_date=NETKEIBA_FINAL_ODDS_FROM)
    _scrape(session)
    assert _odds(session)[WINNER] == (Decimal("3.4"), 2)


def _blank_cells(html: str, odds: str, replacement_odds: str, replacement_pop: str) -> str:
    """Replace one horse's 人気 / 単勝 cells (identified by its odds) on the real page."""
    pattern = re.compile(
        r'(<span class="OddsPeople">)\d+(</span>\s*</td>\s*<td class="Odds Txt_R">\s*'
        r'<span[^>]*>)' + re.escape(odds) + r"(</span>)"
    )
    out, n = pattern.subn(rf"\g<1>{replacement_pop}\g<2>{replacement_odds}\g<3>", html)
    assert n == 1, n
    return out


@pytest.mark.parametrize("blank", ["---", ""])
def test_blank_odds_never_null_an_existing_value(session, blank):
    _seed(session)
    html = _blank_cells(real_fixture(FIXTURE), "3.4", blank, blank)

    summary = _scrape(session, html)

    odds = _odds(session)
    assert odds[WINNER] == (Decimal("4.1"), 15)        # kept: no number on the page
    assert odds["nk:2023100924"] == (Decimal("2.8"), 1)  # everyone else moved to final
    assert summary.extra["final_odds_updated"] == 15


def test_cancelled_row_on_the_page_is_not_touched(session):
    _seed(session)
    html = real_fixture(FIXTURE)
    # turn 16着 (馬番13) into 取消: a non-starter has no result row and no odds write
    html, n = re.subn(r'(<div class="Rank">)16(</div>)', r"\g<1>取消\g<2>", html)
    assert n == 1

    _scrape(session, html)

    assert _odds(session)["nk:2023105278"] == (_pre_race(Decimal("97.8")), 4)
    assert _n_results(session) == 15


def test_popularity_without_odds_is_not_written_and_odds_without_popularity_keeps_it(session):
    _seed(session)
    html = _blank_cells(real_fixture(FIXTURE), "14.0", "14.0", "---")

    _scrape(session, html)

    # odds read, popularity unreadable -> odds move, stored popularity stays
    assert _odds(session)["nk:2023102408"] == (Decimal("14.0"), 12)


def test_payout_failure_does_not_break_results_or_final_odds(session):
    _seed(session)
    # 2 payouts for 1 winner: parse_win_payouts must refuse, the rest must still land
    html = real_fixture(FIXTURE).replace(
        '<td class="Payout"><span>340円</span></td>',
        '<td class="Payout"><span>340円<br />120円</span></td>', 1)

    summary = _scrape(session, html)

    assert summary.status == "partial"                 # surfaced, not swallowed
    assert summary.errors == 1
    assert summary.extra["win_payout_errors"] == 1
    assert summary.extra["final_odds_updated"] == 16
    assert _n_results(session) == 16
    assert _odds(session)[WINNER] == (Decimal("3.4"), 2)
    assert _payouts(session) == []
    job = _last_results_job(session)
    assert job.summary["win_payout_errors"] == 1
    assert "win payout failed" in (job.error_message or "")


def test_final_odds_failure_does_not_break_results_or_payout(session, monkeypatch):
    _seed(session)

    def boom(*_a, **_k):
        raise RuntimeError("odds write broke")

    monkeypatch.setattr(pipeline, "apply_final_odds", boom)
    summary = _scrape(session)

    assert summary.status == "partial"
    assert summary.extra["final_odds_errors"] == 1
    assert _n_results(session) == 16
    assert _payouts(session) == [(1, 340, "netkeiba_result")]
    assert _odds(session)[WINNER] == (Decimal("4.1"), 15)


def test_db_level_payout_failure_rolls_back_only_its_savepoint(session, monkeypatch):
    _seed(session)
    real = pipeline.upsert_official_win_payouts

    def write_then_fail(sess, race_id, payouts, **kw):
        real(sess, race_id, payouts, **kw)          # rows written inside the savepoint ...
        raise RuntimeError("constraint exploded")   # ... and then undone with it

    monkeypatch.setattr(pipeline, "upsert_official_win_payouts", write_then_fail)
    summary = _scrape(session)

    assert summary.extra["win_payout_errors"] == 1
    assert _payouts(session) == []
    assert _n_results(session) == 16
    assert _odds(session)[WINNER] == (Decimal("3.4"), 2)


def test_dead_heat_and_correction_follow_the_page(session):
    _seed(session)
    seen = datetime.datetime(2026, 7, 19, 8, 0, tzinfo=datetime.UTC)
    later = seen + datetime.timedelta(hours=1)

    first = upsert_official_win_payouts(
        session, RID, [ScrapedWinPayout(3, 200), ScrapedWinPayout(7, 350)],
        observed_at=seen, html_sha256="a" * 64)
    assert (first.inserted, first.written) == (2, 2)
    # an official correction: 7's payout changes and 3 is no longer a winner
    second = upsert_official_win_payouts(
        session, RID, [ScrapedWinPayout(7, 360)], observed_at=later, html_sha256="b" * 64)
    assert (second.updated, second.removed, second.inserted) == (1, 1, 0)
    session.commit()
    rows = session.execute(select(OfficialWinPayout.horse_number, OfficialWinPayout.payout_yen,
                                  OfficialWinPayout.observed_at, OfficialWinPayout.html_sha256)
                           .where(OfficialWinPayout.race_id == RID)).all()
    assert [tuple(r) for r in rows] == [(7, 360, later, "b" * 64)]

    # the same payout seen again (later fetch, different page bytes) changes nothing
    third = upsert_official_win_payouts(
        session, RID, [ScrapedWinPayout(7, 360)],
        observed_at=later + datetime.timedelta(days=1), html_sha256="c" * 64)
    assert (third.unchanged, third.written) == (1, 0)
    # an empty page is not evidence that nobody won: nothing is deleted
    empty = upsert_official_win_payouts(session, RID, [], observed_at=later)
    assert empty.written == 0
    session.commit()
    assert session.execute(select(OfficialWinPayout.observed_at, OfficialWinPayout.html_sha256)
                           ).one() == (later, "b" * 64)


def test_payout_for_an_unknown_race_is_skipped_not_failed(session):
    out = upsert_official_win_payouts(
        session, "209912319912", [ScrapedWinPayout(1, 150)],
        observed_at=datetime.datetime(2026, 7, 19, tzinfo=datetime.UTC))
    assert (out.no_race, out.written) == (1, 0)


def test_naive_observed_at_is_refused(session):
    _seed(session)
    with pytest.raises(ValueError, match="timezone-aware"):
        upsert_official_win_payouts(session, RID, [ScrapedWinPayout(1, 340)],
                                    observed_at=datetime.datetime(2026, 7, 19, 8, 0))


def test_apply_final_odds_counts(session):
    _seed(session)
    from horseracing_scrape.parse.results import parse_results
    rows = parse_results(real_fixture(FIXTURE)).rows

    first = apply_final_odds(session, RID, rows)
    again = apply_final_odds(session, RID, rows)

    assert (first.updated, first.unchanged, first.unreadable, first.unmatched) == (16, 0, 0, 0)
    assert (again.updated, again.unchanged) == (0, 16)
    assert first.skipped_pre_netkeiba == again.skipped_pre_netkeiba == 0
    assert first.missing == first.race_unknown == 0


# --- R4: a page that yields no final odds is surfaced, never a silent SUCCEEDED ----------------


def test_moved_header_counts_final_odds_missing_and_keeps_stored_odds(session):
    """The result table's header no longer names 人気 / 単勝 (a markup change): the parser reads
    neither column rather than guessing, every horse is unreadable and the pre-race odds stay —
    the R05 state. The job must say so (``final_odds_missing`` per race + a message), not end as a
    plain SUCCEEDED with ``final_odds_updated=0``."""
    _seed(session)
    before = _odds(session)
    html = real_fixture(FIXTURE)
    html, n_pop = re.subn(r"<th>人<br>気</th>", "<th>支持</th>", html)
    html, n_odds = re.subn(r'<th class="Odds">単勝<br>オッズ</th>', '<th class="Odds">倍率</th>',
                           html)
    assert (n_pop, n_odds) == (1, 1)

    summary = _scrape(session, html)

    assert summary.status == "succeeded"                 # the results themselves were stored
    assert _n_results(session) == 16
    assert _odds(session) == before                      # nothing guessed, nothing NULLed
    assert summary.extra["final_odds_updated"] == 0
    assert summary.extra["final_odds_unreadable"] == 16
    assert summary.extra["final_odds_missing"] == 1
    assert summary.extra["final_odds_unmatched"] == 0
    assert _payouts(session) == [(1, 340, "netkeiba_result")]   # the payout table is unaffected
    job = _last_results_job(session)
    assert job.summary["final_odds_missing"] == 1
    assert job.summary["final_odds_unreadable"] == 16
    assert f"final odds missing {RID}" in (job.error_message or "")


def test_horse_without_an_entry_row_counts_final_odds_unmatched(session):
    """An id split (the page's horse resolves to an id without a race_horses row in this race):
    that horse's stored odds cannot be overwritten — counted, not silent."""
    _seed(session)
    session.execute(RaceHorse.__table__.delete().where(
        RaceHorse.race_id == RID, RaceHorse.horse_id == "nk:2023102408"))
    session.commit()

    summary = _scrape(session)

    assert summary.extra["final_odds_unmatched"] == 1
    assert summary.extra["final_odds_updated"] == 15
    assert summary.extra["final_odds_missing"] == 0
    assert f"final odds unmatched {RID}" in (_last_results_job(session).error_message or "")


def test_race_without_a_date_is_race_unknown_not_pre_netkeiba(session):
    from horseracing_scrape.parse.results import parse_results
    rows = parse_results(real_fixture(FIXTURE)).rows

    out = apply_final_odds(session, "209912319912", rows)   # no races row at all

    assert (out.race_unknown, out.skipped_pre_netkeiba, out.updated, out.missing) == (1, 0, 0, 0)


def test_settlement_summary_always_carries_every_counter(session):
    _seed(session)
    summary = _scrape(session)
    for key in pipeline.SETTLEMENT_COUNT_KEYS:
        assert key in summary.extra, key
    assert summary.extra["final_odds_race_unknown"] == 0
    assert summary.extra["final_odds_unreadable"] == summary.extra["final_odds_unmatched"] == 0

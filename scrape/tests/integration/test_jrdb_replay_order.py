"""178 T042 (SC-004): the three writers in two arrival orders on one synthetic race day (2 races).

Order A: JRDB entries → official entries → netkeiba entries → JRDB results (formal) → official
payouts → JRDB payouts.
Order B: official results with no field (nothing written, ``results_race_not_in_db``) → JRDB
entries → official entries → JRDB results → JRDB payouts → official payouts.

* Order-independent items (static values = first writer, payouts = official, final odds) are
  compared between the two final states.
* History-dependent items (results race_atomic, provenance source, payout adoption) are judged
  right after each step against the expectation the rules give for THAT step (codex D18 #8).
* "No product value changed" is a PK-keyed column comparison; "no unneeded write" is an xmin
  comparison (codex D18 #9). Extra cases: an older version arriving later, a held differing file,
  a partially failed file re-ingested.
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import OfficialWinPayout, Race, RaceHorse
from sqlalchemy import select, text

from horseracing_scrape import precedence as P
from horseracing_scrape.jrdb import files as jfiles
from horseracing_scrape.jrdb import upsert_jrdb as W
from horseracing_scrape.jrdb.ids import horse_id_from_blood_no, race_id_from_key
from horseracing_scrape.jrdb.models import JrdbFileMeta
from horseracing_scrape.models import ResolvedEntryHorse, ScrapedWinPayout
from horseracing_scrape.upsert import (
    OFFICIAL_WIN_PAYOUT_SOURCE_JRA,
    upsert_official_win_payouts,
    write_entries,
)
from tests._jrdb_synth import (
    DAY,
    JST,
    YEAR,
    entries_from_results,
    horses_from_results,
    payouts_from_results,
    shifted_programs,
    shifted_results,
    table_snapshot,
)

pytestmark = pytest.mark.integration
T0 = datetime.datetime(2026, 9, 12, 20, 0, tzinfo=JST)
JRA, NK = P.JRA_OFFICIAL, "netkeiba"


def _results():
    return shifted_results(races=2)


def _rids(results):
    return sorted({race_id_from_key(r.key, file_year=YEAR) for r in results})


def _jrdb_entries(session, results, t):
    st = W.RaceStatus()
    c = W.upsert_jrdb_entries(
        session,
        shifted_programs(results),
        entries_from_results(results),
        horses_from_results(results),
        profile="current",
        year=YEAR,
        observed_at=t,
        job_id=None,
        person_map={},
        race_status=st,
    )
    session.commit()
    return c, st


def _route_entries(session, results, *, source, t, distance_delta=0):
    """The official / netkeiba route writing the same field (same canonical ids, a differing
    distance so the static rule is observable)."""

    programs = {race_id_from_key(p.key, file_year=YEAR): p for p in shifted_programs(results)}
    out = []
    for rid in _rids(results):
        rows = [r for r in results if race_id_from_key(r.key, file_year=YEAR) == rid]
        horses = [
            ResolvedEntryHorse(
                horse_id_from_blood_no(r.blood_reg_no, race_year=YEAR),
                r.horse_name,
                (r.horse_number + 1) // 2,
                r.horse_number,
                None,
                None,
                None,
                None,
                None,
                None,
                float(r.jockey_weight) if r.jockey_weight else None,
                "牡",
                3,
                "started" if r.abnormal_code != 1 else "cancelled",
                int(horse_id_from_blood_no(r.blood_reg_no, race_year=YEAR)[:4]),
                source,
            )
            for r in rows
        ]
        values = W.race_values_of(programs[rid], rid)
        values["distance"] = values["distance"] + distance_delta
        out.append(
            write_entries(
                session,
                rid,
                values,
                horses,
                source=source,
                observed_at=t,
                job_id=None,
                page_kind=P.PAGE_ENTRIES,
            )
        )
    session.commit()
    return out


def _jrdb_results(session, results, t):
    st = W.RaceStatus()
    c = W.upsert_jrdb_results(
        session,
        results,
        [],
        profile="current",
        edition="formal",
        year=YEAR,
        observed_at=t,
        job_id=None,
        race_status=st,
    )
    session.commit()
    return c


def _official_payouts(session, results, t):
    for p in payouts_from_results(results):
        rid = race_id_from_key(p.key, file_year=YEAR)
        upsert_official_win_payouts(
            session,
            rid,
            [ScrapedWinPayout(*p.win[0])],
            observed_at=t,
            source=OFFICIAL_WIN_PAYOUT_SOURCE_JRA,
        )
    session.commit()


def _jrdb_payouts(session, results, t):
    st = W.RaceStatus()
    c = W.upsert_jrdb_payouts(
        session,
        payouts_from_results(results),
        profile="current",
        year=YEAR,
        observed_at=t,
        job_id=None,
        race_status=st,
    )
    session.commit()
    return c


def _prov(session, rid, group):
    return session.execute(
        text("SELECT source FROM race_field_provenance WHERE race_id = :r AND field_group = :g"),
        {"r": rid, "g": group},
    ).scalar_one_or_none()


def _final_state(session):
    races, _ = table_snapshot(session, "races", ("race_id",))
    horses, _ = table_snapshot(session, "race_horses", ("race_id", "horse_id"))
    pays, _ = table_snapshot(session, "official_win_payouts", ("race_id", "horse_number"))
    strip = lambda d, cols: {k: {c: v for c, v in row.items() if c in cols} for k, row in d.items()}  # noqa: E731
    return (
        strip(races, {"distance", "track_type", "race_class", "post_time", "prize_money"}),
        strip(horses, {"frame", "horse_number", "odds", "popularity", "entry_status"}),
        strip(pays, {"payout_yen", "source"}),
    )


def _order_a(session, results):
    rids = _rids(results)
    _jrdb_entries(session, results, T0)
    for rid in rids:
        assert _prov(session, rid, "race_static") == P.JRDB
    _route_entries(
        session, results, source=JRA, t=T0 + datetime.timedelta(hours=1), distance_delta=100
    )
    for rid in rids:  # 公式が正: official overwrites JRDB's static value
        assert _prov(session, rid, "race_static") == JRA
    before_vals, before_x = table_snapshot(session, "races", ("race_id",))
    _route_entries(
        session, results, source=NK, t=T0 + datetime.timedelta(hours=2), distance_delta=200
    )
    after_vals, after_x = table_snapshot(session, "races", ("race_id",))
    assert {k: v["distance"] for k, v in before_vals.items()} == {
        k: v["distance"] for k, v in after_vals.items()
    }  # netkeiba keeps
    for rid in rids:
        assert _prov(session, rid, "race_static") == JRA
    c = _jrdb_results(session, results, T0 + datetime.timedelta(days=5))
    assert c.extra.get("results_inserted_races") == 2
    for rid in rids:
        assert _prov(session, rid, "results") == P.JRDB
    _official_payouts(session, results, T0 + datetime.timedelta(days=5, hours=1))
    for rid in rids:
        assert _prov(session, rid, "win_payouts") == JRA
    _jrdb_payouts(session, results, T0 + datetime.timedelta(days=5, hours=2))
    for rid in rids:  # same value from JRDB → the official adoption stays
        assert _prov(session, rid, "win_payouts") == JRA
    return _final_state(session)


def _order_b(session, results):
    rids = _rids(results)
    c = _jrdb_results(session, results, T0 - datetime.timedelta(hours=1))
    assert c.extra.get("results_race_not_in_db") == 2 and c.written == 0  # no field yet → nothing
    _jrdb_entries(session, results, T0)
    _route_entries(
        session, results, source=JRA, t=T0 + datetime.timedelta(hours=1), distance_delta=100
    )
    c = _jrdb_results(session, results, T0 + datetime.timedelta(days=5))
    assert c.extra.get("results_inserted_races") == 2
    _jrdb_payouts(session, results, T0 + datetime.timedelta(days=5, hours=1))
    for rid in rids:
        assert _prov(session, rid, "win_payouts") == P.JRDB
    _official_payouts(session, results, T0 + datetime.timedelta(days=5, hours=2))
    for rid in rids:  # the official route takes the adoption (same values → source moves)
        assert _prov(session, rid, "win_payouts") == JRA
        row = session.execute(
            select(OfficialWinPayout).where(OfficialWinPayout.race_id == rid)
        ).scalar_one()
        assert row.source == OFFICIAL_WIN_PAYOUT_SOURCE_JRA
    return _final_state(session)


def test_two_orders_end_in_the_same_order_independent_state(session, engine):
    results = _results()
    state_a = _order_a(session, results)
    session.rollback()  # release the test session's transaction before the TRUNCATE below
    # wipe and replay in order B on the same empty DB
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = 'replica'"))
        for t in (
            "race_horse_field_provenance",
            "race_field_provenance",
            "official_win_payouts",
            "exotic_odds",
            "race_laps",
            "race_results",
            "race_horses",
            "races",
            "horses",
            "jockeys",
            "trainers",
            "ingestion_jobs",
        ):
            conn.execute(text(f"TRUNCATE TABLE {t} CASCADE"))
    session.expire_all()
    state_b = _order_b(session, results)
    assert state_a == state_b


def test_same_input_twice_changes_no_value_and_writes_nothing(session):
    results = _results()
    _jrdb_entries(session, results, T0)
    _jrdb_results(session, results, T0 + datetime.timedelta(days=5))
    _jrdb_payouts(session, results, T0 + datetime.timedelta(days=5, hours=1))
    tables = (
        ("races", ("race_id",)),
        ("race_horses", ("race_id", "horse_id")),
        ("race_results", ("race_id", "horse_id")),
        ("official_win_payouts", ("race_id", "horse_number")),
        ("exotic_odds", ("race_id", "bet_type", "selection")),
    )
    before = {t: table_snapshot(session, t, pk) for t, pk in tables}
    # same files again (results: race_atomic refuses; payouts: same sets; entries: applied races skipped)
    st = W.RaceStatus()
    for rid in _rids(results):
        st.set(rid, "applied", None, T0)
    W.upsert_jrdb_entries(
        session,
        shifted_programs(results),
        entries_from_results(results),
        horses_from_results(results),
        profile="current",
        year=YEAR,
        observed_at=T0 + datetime.timedelta(days=6),
        job_id=None,
        person_map={},
        race_status=st,
        only_races=W.unapplied_races(st.applied, _rids(results)),
    )
    _jrdb_results(session, results, T0 + datetime.timedelta(days=6))
    _jrdb_payouts(session, results, T0 + datetime.timedelta(days=6))
    session.expire_all()
    after = {t: table_snapshot(session, t, pk) for t, pk in tables}
    for t in before:
        assert before[t][0] == after[t][0], f"{t}: product value changed"
        assert set(before[t][0]) == set(after[t][0]), f"{t}: rows inserted/deleted"
    # unneeded writes: exotic_odds rows move their xmin on a same-value confirmation only when the
    # source changes; here the source is already jrdb → nothing moves
    for t in ("races", "race_horses", "race_results", "official_win_payouts", "exotic_odds"):
        assert before[t][1] == after[t][1], f"{t}: unneeded write (xmin moved)"


def test_older_version_and_differing_version_are_held(session):
    """A back-number (formal) already applied; a daily file with other content arrives later → held;
    a 速報 after the 正式 → held. Nothing is written by a held file (the CLI never calls the writer)."""

    D = DAY
    formal = JrdbFileMeta("SED", "4", D, "archive", "a", 10, 376, edition="formal")
    later_daily = JrdbFileMeta("SED", "4", D, "daily", "b", 10, 376, edition="formal")
    sokuho = JrdbFileMeta("SED", "4", D, "daily", "c", 10, 376, edition="sokuho")
    assert jfiles.classify_version(formal, later_daily) == "held"
    assert jfiles.classify_version(formal, sokuho) == "held"
    row_a, _ = jfiles.record_fetched(
        session, formal, source_url_sha256="u", fetched_at=T0, published_at=None
    )
    row_b, _ = jfiles.record_fetched(
        session, later_daily, source_url_sha256="u", fetched_at=T0, published_at=None
    )
    jfiles.mark_held(session, row_b)
    session.commit()
    assert row_b.status == "held" and row_b.applied_at is None
    assert (
        session.scalar(select(Race.race_id).limit(1)) is None
    )  # nothing reached the product tables


def test_partial_failure_then_reingest_touches_only_the_failed_race(session):
    results = _results()
    rids = _rids(results)
    bad = rids[0]
    session.execute(
        text("INSERT INTO races (race_id, race_date) VALUES (:r, :d)"), {"r": bad, "d": DAY}
    )
    session.execute(text("INSERT INTO horses (horse_id, horse_name) VALUES ('2022999999', '衝突')"))
    session.execute(
        text(
            "INSERT INTO race_horses (race_id, horse_id, horse_number, entry_status) VALUES (:r, '2022999999', 1, 'started')"
        ),
        {"r": bad},
    )
    session.commit()
    _c, st = _jrdb_entries(session, results, T0)
    assert st.applied[bad]["status"] == "failed" and st.applied[rids[1]]["status"] == "applied"
    good_vals, good_x = table_snapshot(
        session, "race_horses", ("race_id", "horse_id"), "WHERE race_id = :r", {"r": rids[1]}
    )
    session.execute(text("DELETE FROM race_horses WHERE race_id = :r"), {"r": bad})
    session.commit()
    todo = W.unapplied_races(st.applied, rids)
    assert todo == [bad]
    c2 = W.upsert_jrdb_entries(
        session,
        shifted_programs(results),
        entries_from_results(results),
        horses_from_results(results),
        profile="current",
        year=YEAR,
        observed_at=T0 + datetime.timedelta(hours=1),
        job_id=None,
        person_map={},
        race_status=st,
        only_races=todo,
    )
    session.commit()
    session.expire_all()
    assert st.applied[bad]["status"] == "applied" and c2.extra.get("races_written") == 1
    again_vals, again_x = table_snapshot(
        session, "race_horses", ("race_id", "horse_id"), "WHERE race_id = :r", {"r": rids[1]}
    )
    assert good_vals == again_vals and good_x == again_x
    n = session.scalar(select(RaceHorse.race_id).where(RaceHorse.race_id == bad).limit(1))
    assert n == bad

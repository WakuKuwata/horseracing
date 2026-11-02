"""176 T027: two writers of one race serialise on the races row; a failed run re-runs cleanly."""

from __future__ import annotations

import threading

import pytest
from horseracing_db.enums import Source
from horseracing_db.models import Race, RaceHorse
from sqlalchemy import select, text

from horseracing_scrape.upsert import lock_race, update_odds, write_entries
from tests._seed176 import RID, T1, T2, entry, odds_payload, race_prov, race_values, seed_race

pytestmark = pytest.mark.integration


def test_second_writer_waits_for_the_first_and_sees_its_result(session, session_factory):
    seed_race(session, n=3)
    gate = threading.Event()
    done = threading.Event()
    order: list[str] = []

    def holder():
        with session_factory() as s, s.begin():
            lock_race(s, RID)  # FOR UPDATE held …
            order.append("A-locked")
            gate.set()
            done.wait(5)  # … until the other writer has tried
            update_odds(
                s,
                RID,
                odds_payload({1: 2.0, 2: 4.0, 3: 6.0}, official_at=T1),
                source=Source.NETKEIBA,
                observed_at=T1,
            )
            order.append("A-wrote")

    def waiter():
        gate.wait(5)
        with session_factory() as s, s.begin():
            order.append("B-start")
            done.set()
            # blocks on the row lock until A commits, then decides against A's state (T2 > T1)
            update_odds(
                s,
                RID,
                odds_payload({1: 2.1, 2: 4.1, 3: 6.1}, official_at=T2),
                source=Source.JRA_OFFICIAL,
                observed_at=T2,
            )
            order.append("B-wrote")

    ta, tb = threading.Thread(target=holder), threading.Thread(target=waiter)
    ta.start()
    tb.start()
    ta.join(10)
    tb.join(10)
    assert order.index("A-wrote") < order.index("B-wrote")
    session.expire_all()
    race = session.get(Race, RID)
    assert race.win_odds_official_at == T2
    odds = session.scalars(
        select(RaceHorse.odds).where(RaceHorse.race_id == RID).order_by(RaceHorse.horse_number)
    ).all()
    assert [float(o) for o in odds] == [2.1, 4.1, 6.1]
    assert race_prov(session)["win_odds"][0] == Source.JRA_OFFICIAL


def test_no_race_lock_is_held_outside_a_writer(session_factory):
    """The HTTP fetch happens before lock_race; between writers no row lock on races survives."""
    with session_factory() as s:
        s.execute(text("SELECT 1"))
        s.commit()
        locks = s.execute(
            text(
                "SELECT count(*) FROM pg_locks l JOIN pg_class c ON c.oid = l.relation "
                "WHERE c.relname = 'races' AND l.mode LIKE '%RowExclusive%' AND l.granted"
            )
        ).scalar_one()
    assert locks == 0


def test_failed_run_then_rerun_equals_a_single_run(session, session_factory):
    seed_race(session, n=2)
    # run 1 fails after the write (simulated crash → rollback)
    with session_factory() as s:
        write_entries(
            s,
            RID,
            race_values(),
            [entry(1, weight=480, weight_diff=2), entry(2)],
            source=Source.JRA_OFFICIAL,
            observed_at=T1,
        )
        s.rollback()
    with session_factory() as s:
        assert (
            s.scalar(
                select(RaceHorse.weight).where(
                    RaceHorse.race_id == RID, RaceHorse.horse_number == 1
                )
            )
            is None
        )
    # run 2 succeeds; the end state equals one clean run
    write_entries(
        session,
        RID,
        race_values(),
        [entry(1, weight=480, weight_diff=2), entry(2)],
        source=Source.JRA_OFFICIAL,
        observed_at=T1,
    )
    session.commit()
    session.expire_all()
    rh = session.scalar(
        select(RaceHorse).where(RaceHorse.race_id == RID, RaceHorse.horse_number == 1)
    )
    assert (rh.weight, rh.weight_diff) == (480, 2)
    from tests._seed176 import horse_prov

    assert horse_prov(session)[(rh.horse_id, "body_weight")] == (Source.JRA_OFFICIAL, T1)

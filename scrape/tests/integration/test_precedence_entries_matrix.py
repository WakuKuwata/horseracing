"""176 T021/T056: the entries transition table against a real PostgreSQL (SC-010, entries part).

Both routes go through ``write_entries``; the netkeiba route is exercised by giving ``source``.
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus, Source

from horseracing_scrape.precedence import PAGE_CHANGES, PAGE_ENTRIES
from horseracing_scrape.upsert import write_entries
from tests._seed176 import (
    RID,
    T0,
    T1,
    T2,
    entry,
    horse_prov,
    horses,
    race_prov,
    race_values,
    seed_race,
)

pytestmark = pytest.mark.integration
JRA = Source.JRA_OFFICIAL
NK = Source.NETKEIBA


def _write(session, rows, *, source=NK, observed_at=T1, page_kind=PAGE_ENTRIES, values=None):
    c = write_entries(
        session,
        RID,
        values or race_values(),
        rows,
        source=source,
        observed_at=observed_at,
        page_kind=page_kind,
    )
    session.commit()
    return c


def test_cancellation_does_not_revert_from_an_older_entries_page(session):
    ids = seed_race(session, n=3)
    rows = [entry(1), entry(2), entry(3)]
    _write(session, rows, observed_at=T0)
    # 取消 arrives (from either route)
    c = _write(
        session,
        [entry(1), entry(2, entry_status=EntryStatus.CANCELLED), entry(3)],
        source=JRA,
        observed_at=T1,
    )
    assert c.extra.get("status_transitions") == 1
    assert horses(session)[ids[1]].entry_status == EntryStatus.CANCELLED
    # a stale entries page (everyone started) is re-ingested afterwards → rejected, not reverted
    c = _write(session, rows, source=NK, observed_at=T2)
    assert c.extra.get("status_rejected") == 1
    assert horses(session)[ids[1]].entry_status == EntryStatus.CANCELLED
    # the official entries page cannot revert either
    c = _write(session, rows, source=JRA, observed_at=T2)
    assert c.extra.get("status_rejected") == 1
    assert horses(session)[ids[1]].entry_status == EntryStatus.CANCELLED


def test_cancel_exclude_correction_only_from_changes_or_results(session):
    ids = seed_race(session, n=2)
    _write(session, [entry(1), entry(2, entry_status=EntryStatus.CANCELLED)])
    _write(session, [entry(1), entry(2, entry_status=EntryStatus.EXCLUDED)], page_kind=PAGE_ENTRIES)
    assert (
        horses(session)[ids[1]].entry_status == EntryStatus.CANCELLED
    )  # entries page cannot correct
    _write(session, [entry(1), entry(2, entry_status=EntryStatus.EXCLUDED)], page_kind=PAGE_CHANGES)
    assert horses(session)[ids[1]].entry_status == EntryStatus.EXCLUDED


def test_same_input_twice_is_idempotent_on_values(session):
    ids = seed_race(session, n=2)
    rows = [entry(1, weight=480, weight_diff=2), entry(2)]
    _write(session, rows, observed_at=T1)
    before = {
        h: (rh.weight, rh.weight_diff, rh.jockey_id, rh.entry_status)
        for h, rh in horses(session).items()
    }
    c = _write(session, rows, observed_at=T2)
    after = {
        h: (rh.weight, rh.weight_diff, rh.jockey_id, rh.entry_status)
        for h, rh in horses(session).items()
    }
    assert before == after and c.errors == 0
    assert horse_prov(session)[(ids[0], "body_weight")][1] == T2  # observation refreshed


def test_body_weight_is_kept_as_a_pair(session):
    ids = seed_race(session, n=1)
    _write(session, [entry(1, weight=480, weight_diff=2)], observed_at=T1)
    _write(session, [entry(1)], observed_at=T2)  # morning page: no weight at all
    rh = horses(session)[ids[0]]
    assert (rh.weight, rh.weight_diff) == (480, 2)
    _write(session, [entry(1, weight=484, weight_diff=None)], observed_at=T2)
    rh = horses(session)[ids[0]]
    assert (rh.weight, rh.weight_diff) == (484, None)  # the pair, not "new weight + old diff"


def test_jockey_change_is_not_rolled_back_by_an_older_observation(session):
    ids = seed_race(session, n=1)
    _write(session, [entry(1, jockey_id="01001")], observed_at=T0)
    c = _write(
        session,
        [entry(1, jockey_id="05339", jockey_name="C.ルメール")],
        source=JRA,
        observed_at=T2,
        page_kind=PAGE_CHANGES,
    )
    assert c.extra.get("jockey_changes") == 1
    assert horses(session)[ids[0]].jockey_id == "05339"
    # an entries page fetched EARLIER than the change (T1 < T2) must not undo it
    _write(session, [entry(1, jockey_id="01001")], source=NK, observed_at=T1)
    assert horses(session)[ids[0]].jockey_id == "05339"
    # a genuinely newer observation may change it again
    _write(
        session,
        [entry(1, jockey_id="01001")],
        source=NK,
        observed_at=T2 + datetime.timedelta(minutes=5),
    )
    assert horses(session)[ids[0]].jockey_id == "01001"


def test_official_static_values_are_not_overwritten_by_netkeiba(session):
    ids = seed_race(session, n=1)
    c = _write(
        session,
        [entry(1, jockey_weight=57.0)],
        source=JRA,
        observed_at=T1,
        values=race_values(distance=1800, post_time=T1),
    )
    assert (
        c.extra.get("static_mismatch", 0) >= 1
    )  # 56.0 → 57.0 and 1600 → 1800 corrected by official
    rh = horses(session)[ids[0]]
    assert float(rh.jockey_weight) == 57.0
    c = _write(
        session,
        [entry(1, jockey_weight=56.0)],
        source=NK,
        observed_at=T2,
        values=race_values(distance=1600, post_time=T2),
    )
    assert c.extra.get("static_mismatch") == 2  # horse static + race static, both kept
    rh = horses(session)[ids[0]]
    assert float(rh.jockey_weight) == 57.0
    session.expire_all()
    from horseracing_db.models import Race

    race = session.get(Race, RID)
    assert race.distance == 1800 and race.post_time == T1
    assert race_prov(session)["race_static"][0] == JRA and race_prov(session)["post_time"][0] == JRA
    assert horse_prov(session)[(ids[0], "entry_static")][0] == JRA

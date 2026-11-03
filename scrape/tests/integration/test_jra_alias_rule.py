"""176 FR-011a (user decision 2026-11-03): official id → the stored row of the same race.

Resolution table (horse / person) and the writers end to end on the real 11/01 fixtures: values
land on the ``nk:`` rows, no master / race_horses row is created, the netkeiba route keeps writing
the same race afterwards, a failure mid-write leaves nothing behind.
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntityType, EntryStatus, MappingStatus, Source
from horseracing_db.models import Horse, IdMapping, Jockey, RaceHorse, Trainer
from sqlalchemy import func, select

from horseracing_scrape.jra import alias as A
from horseracing_scrape.jra.parse.changes import parse_changes
from horseracing_scrape.jra.parse.entries import parse_entries_day
from horseracing_scrape.jra.parse.odds import parse_win_odds
from horseracing_scrape.jra.upsert_jra import upsert_jra_entries, upsert_jra_odds
from horseracing_scrape.upsert import write_entries
from tests._netkeiba_first import netkeiba_first, netkeiba_rows, nk
from tests._seed176 import horse_prov, snapshot_rows
from tests.jra_fixtures import fetched_at, jra_text

pytestmark = pytest.mark.integration
D = datetime.date(2026, 11, 1)
RID = "202605041111"
ENTRIES = "entries_tokyo_20261101.sjis.html"
OBS = fetched_at(ENTRIES)
EARLIER = OBS - datetime.timedelta(hours=12)
JRA = Source.JRA_OFFICIAL


def _page():
    return parse_entries_day(jra_text(ENTRIES), expected_date=D)


def _changes():
    return parse_changes(jra_text("changes_20261101.sjis.html"), expected_date=D)


def _entry():
    return next(b.payload for b in _page().published() if b.race_id == RID)


def _counts(session):
    return tuple(
        session.scalar(select(func.count()).select_from(m))
        for m in (Horse, Jockey, Trainer, RaceHorse)
    )


# --- resolution table -----------------------------------------------------------------------
def _rh(hid, number, jockey=None, trainer=None):
    return RaceHorse(
        race_id=RID,
        horse_id=hid,
        horse_number=number,
        jockey_id=jockey,
        trainer_id=trainer,
        entry_status=EntryStatus.STARTED,
    )


X = "2024101234"


@pytest.mark.parametrize(
    ("stored_ids", "masters", "mapping", "official_name", "reason", "aliased"),
    [
        ([X], [(X, "テスト")], None, "テスト", None, 0),  # X only
        ([nk(X)], [(nk(X), "テスト")], None, "テスト", None, 1),  # nk:X only
        ([nk(X)], [(nk(X), "テスト")], MappingStatus.UNMAPPED, "テスト", None, 1),
        ([X, nk(X)], [(X, "テスト"), (nk(X), "テスト")], None, "テスト", A.BOTH_IN_RACE, 0),
        ([nk(X)], [(X, "テスト"), (nk(X), "テスト")], None, "テスト", A.BOTH_EXIST, 0),
        ([nk(X)], [(nk(X), "テスト")], MappingStatus.MAPPED, "テスト", A.PENDING_REKEY, 0),
        ([nk(X)], [(nk(X), "テスト")], MappingStatus.CONFLICT, "テスト", A.DISPUTED, 0),
        ([nk(X)], [(nk(X), "テスト")], MappingStatus.REJECTED, "テスト", A.DISPUTED, 0),
        ([nk(X)], [(nk(X), "テスト")], None, "べつのうま", A.NAME_MISMATCH, 0),
        ([nk(X)], [(nk(X), None)], None, "テスト", A.NAME_MISMATCH, 0),  # no stored name
        ([nk(X)], [(nk(X), "テ スト")], None, "テスト", None, 1),  # whitespace variant
        ([nk(X)], [(nk(X), "ﾃｽﾄ")], None, "テスト", None, 1),  # half-width (NFKC)
        (["nk:2024101235"], [("nk:2024101235", "テスト")], None, "テスト", A.MISSING, 0),
        (["2024101235"], [("2024101235", "テスト")], None, "テスト", A.MISSING, 0),
        ([nk(X)[:-1] + "0"], [(nk(X)[:-1] + "0", "テスト")], None, "テスト", A.MISSING, 0),
    ],
)
def test_horse_resolution_table(
    session, stored_ids, masters, mapping, official_name, reason, aliased
):
    for hid, name in masters:
        session.add(Horse(horse_id=hid, horse_name=name))
    if mapping is not None:
        session.add(
            IdMapping(
                entity_type=EntityType.HORSE,
                source=Source.NETKEIBA,
                source_id=X,
                canonical_id=X if mapping == MappingStatus.MAPPED else None,
                mapping_status=mapping,
            )
        )
    session.flush()
    stored = {hid: _rh(hid, 1) for hid in stored_ids}
    if len(stored_ids) == 2:
        stored[stored_ids[1]].horse_number = 2
    out = A.resolve_horses(session, stored, [A.OfficialHorse(X, 1, official_name)])
    assert out.reason == reason, out.problems
    assert out.aliased == aliased
    if reason is None:
        assert out.stored_id(X) == stored_ids[0]


def test_empty_field_number_mismatch_and_partial_field(session):
    assert A.resolve_horses(session, {}, [A.OfficialHorse(X, 1, "テスト")]).reason == A.AWAITING
    session.add(Horse(horse_id=nk(X), horse_name="テスト"))
    session.flush()
    stored = {nk(X): _rh(nk(X), 2)}
    out = A.resolve_horses(session, stored, [A.OfficialHorse(X, 1, "テスト")])
    assert out.reason == A.NUMBER_MISMATCH
    # 取消 with a blank 馬番: the stored row supplies it
    out = A.resolve_horses(session, stored, [A.OfficialHorse(X, None, "テスト")])
    assert out.reason is None and out.rows[X].horse_number == 2
    # odds pages (require_all=False): an unknown horse is left out, not a quarantine
    out = A.resolve_horses(
        session,
        stored,
        [A.OfficialHorse(X, 2, "テスト"), A.OfficialHorse("2024109999", 3, "他")],
        require_all=False,
    )
    assert out.reason is None and set(out.rows) == {X}


@pytest.mark.parametrize(
    ("stored", "masters", "mapped_to", "expected"),
    [
        ("05339", [], None, "05339"),  # same person, canonical
        ("nk:05339", [], None, "nk:05339"),  # same person, surrogate (leading zero kept)
        ("nk:5339", ["nk:05339"], None, "nk:05339"),  # leading zero lost ≠ same person
        ("01001", [], "05339", "05339"),  # changed person, approved mapping
        ("01001", ["nk:05339"], None, "nk:05339"),
        ("01001", ["05339"], None, None),  # netkeiba may or may not promote
        ("01001", ["05339", "nk:05339"], None, None),  # both exist → undecidable
        ("01001", [], None, None),  # unknown person → leave as stored
        (None, ["05339"], None, None),
        (None, ["nk:05339"], None, "nk:05339"),
    ],
)
def test_person_resolution_table(session, stored, masters, mapped_to, expected):
    for jid in masters:
        session.add(Jockey(jockey_id=jid, jockey_name="騎手"))
    if mapped_to is not None:
        session.add(
            IdMapping(
                entity_type=EntityType.JOCKEY,
                source=Source.NETKEIBA,
                source_id="05339",
                canonical_id=mapped_to,
                mapping_status=MappingStatus.MAPPED,
            )
        )
    session.flush()
    assert A.resolve_person(session, EntityType.JOCKEY, "05339", stored) == expected


def test_trainer_and_jockey_namespaces_are_separate(session):
    session.add(Trainer(trainer_id="nk:01165", trainer_name="調教師"))
    session.flush()
    assert A.resolve_person(session, EntityType.JOCKEY, "01165", "01001") is None
    assert A.resolve_person(session, EntityType.TRAINER, "01165", "01001") == "nk:01165"


# --- writers end to end ------------------------------------------------------------------------
def test_entries_write_through_the_alias_and_create_nothing(session):
    page, changes = _page(), _changes()
    netkeiba_first(session, page, changes, observed_at=EARLIER, race_ids=[RID])
    before = _counts(session)
    n = len(_entry().horses)
    c = upsert_jra_entries(session, page, changes, observed_at=OBS, race_ids=[RID])
    session.commit()
    assert c.extra.get("alias_horses") == n and not c.extra.get("quarantined_collision")
    assert c.written == n
    assert _counts(session) == before  # no master / race_horses row
    assert (
        session.scalar(select(func.count()).select_from(Horse).where(~Horse.horse_id.like("nk:%")))
        == 0
    )
    pv = horse_prov(session, RID)
    first = nk(_entry().horses[0].horse_id)
    # static items: the official route owns them; an unchanged status keeps netkeiba's provenance
    assert pv[(first, "entry_static")] == (JRA, OBS)
    assert pv[(first, "entry_status")][0] == Source.NETKEIBA
    # jockey / trainer stayed on the stored surrogate ids: no change, no new master
    rh = session.get(RaceHorse, (RID, first))
    assert rh.jockey_id == nk(_entry().horses[0].jockey_id)
    # the same page again changes nothing
    snap = snapshot_rows(session, RID)
    c2 = upsert_jra_entries(session, page, changes, observed_at=OBS, race_ids=[RID])
    session.commit()
    assert not c2.extra.get("quarantined_collision") and snapshot_rows(session, RID) == snap
    # the netkeiba route can still write the race afterwards (no collision either way)
    e = _entry()
    again = write_entries(
        session,
        RID,
        {"race_date": D, "race_number": 11, "venue_code": "05"},
        netkeiba_rows(e, changes),
        source=Source.NETKEIBA,
        observed_at=OBS + datetime.timedelta(minutes=5),
        job_id=None,
        page_kind="entries",
    )
    session.commit()
    assert not again.extra.get("quarantined_collision") and _counts(session) == before


def test_official_first_waits_for_netkeiba(session):
    c = upsert_jra_entries(session, _page(), _changes(), observed_at=OBS)
    session.commit()
    assert c.extra.get(A.AWAITING) == 12 and c.written == 0
    assert _counts(session) == (0, 0, 0, 0)


def test_canonical_rows_stay_canonical(session):
    page, changes = _page(), _changes()
    netkeiba_first(session, page, changes, observed_at=EARLIER, race_ids=[RID], alias=False)
    before = _counts(session)
    c = upsert_jra_entries(session, page, changes, observed_at=OBS, race_ids=[RID])
    session.commit()
    assert not c.extra.get("alias_horses") and c.written == len(_entry().horses)
    assert _counts(session) == before


def test_mixed_field_canonical_and_surrogate(session):
    page, changes = _page(), _changes()
    e = _entry()
    keep = [h.horse_id for h in e.horses[:3]] + [h.jockey_id for h in e.horses[:3]]
    netkeiba_first(session, page, changes, observed_at=EARLIER, race_ids=[RID], canonical=keep)
    c = upsert_jra_entries(session, page, changes, observed_at=OBS, race_ids=[RID])
    session.commit()
    assert c.extra.get("alias_horses") == len(e.horses) - 3
    assert not c.extra.get("quarantined_collision")


def test_jockey_changed_to_an_unknown_person_is_left_and_reported(session):
    page, changes = _page(), _changes()
    e = _entry()
    rows = netkeiba_rows(e, changes)
    first = rows[0]
    rows[0] = type(first)(**{**first.__dict__, "jockey_id": "nk:09999", "jockey_name": "別騎手"})
    write_entries(
        session,
        RID,
        {"race_date": D, "race_number": 11, "venue_code": "05"},
        rows,
        source=Source.NETKEIBA,
        observed_at=EARLIER,
        job_id=None,
        page_kind="entries",
    )
    session.commit()
    jockeys = session.scalar(select(func.count()).select_from(Jockey))
    c = upsert_jra_entries(session, page, changes, observed_at=OBS, race_ids=[RID])
    session.commit()
    assert c.extra.get("person_unresolved") == 1
    assert session.get(RaceHorse, (RID, first.horse_id)).jockey_id == "nk:09999"
    assert session.scalar(select(func.count()).select_from(Jockey)) == jockeys


def test_a_failure_mid_write_leaves_nothing(session, monkeypatch):
    page, changes = _page(), _changes()
    netkeiba_first(session, page, changes, observed_at=EARLIER, race_ids=[RID])
    snap, counts = snapshot_rows(session, RID), _counts(session)
    prov = horse_prov(session, RID)
    import horseracing_scrape.upsert as U

    real = U._write_entry_horse
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom")
        return real(*a, **k)

    monkeypatch.setattr(U, "_write_entry_horse", boom)
    with pytest.raises(RuntimeError):
        upsert_jra_entries(session, page, changes, observed_at=OBS, race_ids=[RID])
    session.rollback()
    assert snapshot_rows(session, RID) == snap and _counts(session) == counts
    assert horse_prov(session, RID) == prov


def test_odds_through_the_alias(session):
    page, changes = _page(), _changes()
    netkeiba_first(session, page, changes, observed_at=EARLIER, race_ids=[RID])
    odds = parse_win_odds(jra_text("odds_tokyo11r_20261101_0822.sjis.html"), expected_date=D)
    before = _counts(session)
    c = upsert_jra_odds(
        session, odds, observed_at=fetched_at("odds_tokyo11r_20261101_0822.sjis.html")
    )
    session.commit()
    assert not c.extra.get("quarantined_collision"), c.error_messages
    started = [r for r in odds.rows if r.entry_status == EntryStatus.STARTED and r.win_odds]
    stored = {
        rh.horse_number: rh
        for rh in session.scalars(select(RaceHorse).where(RaceHorse.race_id == RID))
    }
    for r in started:
        assert float(stored[r.horse_number].odds) == r.win_odds
        assert stored[r.horse_number].horse_id == nk(r.horse_id)
    assert _counts(session) == before


def test_race_static_is_fill_only(session):
    """11/01 replay: the official page spells ダート / 2勝クラス where the DB (and the features) use
    ダ / ２勝 — a stored race static value is kept, a NULL one is filled."""
    from horseracing_db.models import Race

    page, changes = _page(), _changes()
    netkeiba_first(session, page, changes, observed_at=EARLIER, race_ids=[RID])
    official = _entry().race
    session.execute(
        Race.__table__.update()
        .where(Race.race_id == RID)
        .values(race_name="短い名前", prize_money=None)
    )
    session.commit()
    c = upsert_jra_entries(session, page, changes, observed_at=OBS, race_ids=[RID])
    session.commit()
    session.expire_all()
    race = session.get(Race, RID)
    assert race.race_name == "短い名前" and c.extra.get("race_static_kept") == 1
    assert race.prize_money == official.prize_money  # blank number → filled
    assert race.track_type == official.track_type and race.distance == official.distance


def test_blank_text_race_columns_are_not_filled(session):
    from horseracing_db.models import Race

    page, changes = _page(), _changes()
    netkeiba_first(session, page, changes, observed_at=EARLIER, race_ids=[RID])
    session.execute(
        Race.__table__.update().where(Race.race_id == RID).values(track_type=None, race_class=None)
    )
    session.commit()
    upsert_jra_entries(session, page, changes, observed_at=OBS, race_ids=[RID])
    session.commit()
    session.expire_all()
    race = session.get(Race, RID)
    assert race.track_type is None and race.race_class is None


def test_unlinked_jockey_keeps_the_stored_value_and_writes_the_race(session):
    """11/01 京都: 渡辺竜也 騎手 is printed without a link — the race is still written, the jockey
    column is left as stored (a horse without a link still quarantines)."""
    rid = "202605041101"
    html = jra_text(ENTRIES).replace("pw04kmk005339/", "pw04kmk00533/", 1)
    page = parse_entries_day(html, expected_date=D)
    changes = _changes()
    netkeiba_first(session, _page(), changes, observed_at=EARLIER, race_ids=[rid])
    stored = {
        rh.horse_id: rh.jockey_id
        for rh in session.scalars(select(RaceHorse).where(RaceHorse.race_id == rid))
    }
    c = upsert_jra_entries(session, page, changes, observed_at=OBS, race_ids=[rid])
    session.commit()
    assert c.extra.get("person_unlinked") == 1 and not c.extra.get("quarantined_unknown_id")
    assert c.written == len(stored)
    session.expire_all()
    after = {
        rh.horse_id: rh.jockey_id
        for rh in session.scalars(select(RaceHorse).where(RaceHorse.race_id == rid))
    }
    assert after == stored

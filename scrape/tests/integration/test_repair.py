"""Integration tests for identity resolve + physical split repair (Feature 067, T009/T015).

Uses a Savona-shaped split: canonical JRA-VAN horse with past races + a netkeiba surrogate holding
the recent race, prediction, feature snapshot, and a WIN recommendation. Verifies re-key, orphan
delete, recommendation JSON canonicalization, idempotency, dry-run invariance, collision skip, and
the pre-delete attribute gate.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from horseracing_db.enums import (
    EntityType,
    EntryStatus,
    MappingStatus,
    ResultStatus,
    Source,
)
from horseracing_db.models import (
    FeatureSnapshot,
    Horse,
    IdMapping,
    Jockey,
    ModelVersion,
    PredictionRun,
    Race,
    RaceHorse,
    RacePrediction,
    RaceResult,
    Recommendation,
)
from sqlalchemy import text

from horseracing_scrape.repair import repair_splits, resolve_identities

pytestmark = pytest.mark.integration

CID = "2020100734"
SID = f"nk:{CID}"


def _race(session, rid, d):
    session.merge(Race(race_id=rid, race_number=int(rid[-2:]), race_date=d, venue_code=rid[4:6]))


def _seed_split(session, *, with_derived=True):
    """canonical (2 past races) + surrogate (1 recent race + derived rows + win rec)."""
    session.merge(Horse(horse_id=CID, horse_name="サヴォーナ", birth_year=2020, sex="牡",
                        data_source="jra_van"))
    session.merge(Horse(horse_id=SID, horse_name="サヴォーナ", birth_year=2020, sex="牡",
                        data_source="netkeiba"))
    # canonical past races (valid 12-digit race_ids)
    for i, d in enumerate([datetime.date(2022, 10, 2), datetime.date(2023, 1, 15)]):
        rid = f"2022060409{i + 1:02d}"
        _race(session, rid, d)
        session.add(RaceHorse(race_id=rid, horse_id=CID, horse_number=5,
                              entry_status=EntryStatus.STARTED))
        session.add(RaceResult(race_id=rid, horse_id=CID, finish_order=i + 1,
                               result_status=ResultStatus.FINISHED))
    # surrogate recent race
    rid_new = "202603010211"
    _race(session, rid_new, datetime.date(2026, 4, 12))
    session.add(RaceHorse(race_id=rid_new, horse_id=SID, horse_number=8,
                          entry_status=EntryStatus.STARTED))
    session.add(RaceResult(race_id=rid_new, horse_id=SID, finish_order=3,
                           result_status=ResultStatus.FINISHED))
    session.flush()
    if with_derived:
        session.merge(ModelVersion(model_version="lgbm-test", adoption_status="active"))
        run = PredictionRun(race_id=rid_new, model_version="lgbm-test", logic_version="lv=1")
        session.add(run)
        session.flush()
        session.add(RacePrediction(prediction_run_id=run.prediction_run_id, horse_id=SID,
                                   win_prob=Decimal("0.1"), top2_prob=Decimal("0.2"),
                                   top3_prob=Decimal("0.3")))
        session.add(FeatureSnapshot(prediction_run_id=run.prediction_run_id, horse_id=SID,
                                    feature_version="features-016", features={"x": 1}))
        session.add(Recommendation(
            prediction_run_id=run.prediction_run_id, race_id=rid_new, bet_type="win",
            selection={"horse_id": SID, "horse_number": 8}, is_estimated_odds=False,
            logic_version="lv=1",
        ))
    # mapping (already resolved to mapped)
    session.add(IdMapping(entity_type=EntityType.HORSE, source=Source.NETKEIBA, source_id=CID,
                          canonical_id=CID, mapping_status=MappingStatus.MAPPED))
    session.commit()
    return rid_new


def test_repair_rekeys_and_deletes_orphan(session):
    rid_new = _seed_split(session)
    rep = repair_splits(session, entity="horse")
    assert rep.orphans_deleted["horse"] == 1
    assert rep.collisions["horse"] == 0
    assert rep.affected_from == datetime.date(2026, 4, 12)
    # surrogate master gone; recent race rows now under canonical
    assert session.get(Horse, SID) is None
    assert session.scalar(text("select horse_id from race_horses where race_id=:r"),
                          {"r": rid_new}) == CID
    assert session.scalar(text("select horse_id from race_results where race_id=:r"),
                          {"r": rid_new}) == CID
    # derived re-keyed for FK integrity
    assert session.scalar(text("select count(*) from race_predictions where horse_id=:s"),
                          {"s": SID}) == 0
    assert session.scalar(text("select count(*) from feature_snapshots where horse_id=:s"),
                          {"s": SID}) == 0
    # win recommendation JSON canonicalized (backtest matches result horse_id directly)
    sel = session.scalar(text("select selection->>'horse_id' from recommendations"))
    assert sel == CID
    # canonical now sees the recent race → history no longer empty (silent-degradation fix)
    n = session.scalar(text("select count(*) from race_horses where horse_id=:c"), {"c": CID})
    assert n == 3


def test_repair_is_idempotent(session):
    _seed_split(session)
    repair_splits(session, entity="horse")
    rep2 = repair_splits(session, entity="horse")
    assert rep2.pairs_processed == 0  # surrogate already gone → no-op
    assert sum(rep2.rekeyed_rows.values()) == 0


def test_dry_run_leaves_db_unchanged(session):
    _seed_split(session)
    before = session.scalar(text("select count(*) from horses where horse_id like 'nk:%'"))
    before_updated = session.scalar(text("select max(updated_at) from race_horses"))
    rep = repair_splits(session, entity="horse", dry_run=True)
    assert rep.dry_run is True
    assert rep.rekeyed_rows.get("race_horses", 0) >= 1  # counts captured
    # nothing persisted (including timestamps)
    assert session.scalar(text("select count(*) from horses where horse_id like 'nk:%'")) == before
    assert session.get(Horse, SID) is not None
    assert session.scalar(text("select max(updated_at) from race_horses")) == before_updated
    assert session.scalar(text("select horse_id from race_results where race_id='202603010211'")) \
        == SID


def test_collision_skips_whole_pair(session):
    rid_new = _seed_split(session)
    # synthetic collision: a canonical row already exists for the recent race
    session.execute(text("insert into race_horses (race_id, horse_id, horse_number, entry_status) "
                         "values (:r, :c, 99, 'started')"), {"r": rid_new, "c": CID})
    session.commit()
    rep = repair_splits(session, entity="horse")
    assert rep.collisions["horse"] == 1
    assert rep.orphans_deleted["horse"] == 0
    # pair fully skipped: surrogate rows untouched
    assert session.get(Horse, SID) is not None
    assert session.scalar(text("select count(*) from race_results where horse_id=:s"),
                          {"s": SID}) == 1


def test_attribute_gate_holds_pair(session):
    _seed_split(session)
    # canonical missing sex, surrogate has it → deleting surrogate would lose info
    session.execute(text("update horses set sex=null where horse_id=:c"), {"c": CID})
    session.commit()
    rep = repair_splits(session, entity="horse")
    assert rep.held["horse"] == 1
    assert rep.orphans_deleted["horse"] == 0
    assert session.get(Horse, SID) is not None


def test_pedigree_id_reference_rekeyed(session):
    _seed_split(session)
    # another horse points to the surrogate as its sire
    session.merge(Horse(horse_id="OFFSPRING", horse_name="子", sire_id=SID))
    session.commit()
    repair_splits(session, entity="horse")
    assert session.get(Horse, "OFFSPRING").sire_id == CID  # dangling reference healed
    assert session.scalar(text("select count(*) from horses where sire_id=:s"), {"s": SID}) == 0


def test_resolve_identities_maps_conflict_and_dry_run(session):
    # horse: exact name+birth → mapped
    session.merge(Horse(horse_id=CID, horse_name="サヴォーナ", birth_year=2020,
                        data_source="jra_van"))
    session.merge(Horse(horse_id=SID, horse_name="サヴォーナ", birth_year=2020,
                        data_source="netkeiba"))
    session.add(IdMapping(entity_type=EntityType.HORSE, source=Source.NETKEIBA, source_id=CID,
                          mapping_status=MappingStatus.UNMAPPED))
    # jockey: abbreviation-scheme diff → conflict
    session.merge(Jockey(jockey_id="01209", jockey_name="石神深道"))
    session.merge(Jockey(jockey_id="nk:01209", jockey_name="石神道"))
    session.add(IdMapping(entity_type=EntityType.JOCKEY, source=Source.NETKEIBA, source_id="01209",
                          mapping_status=MappingStatus.UNMAPPED))
    session.commit()

    # dry-run: no writes
    rep = resolve_identities(session, dry_run=True)
    assert rep.resolved["horse"] == 1
    assert rep.conflicts["jockey"] == 1
    row = session.get(IdMapping, session.scalar(
        text("select id_mapping_id from id_mappings where source_id=:s"), {"s": CID}))
    assert row.mapping_status == MappingStatus.UNMAPPED  # unchanged by dry-run

    # real run: writes mapped/conflict
    resolve_identities(session)
    session.expire_all()
    horse_map = session.scalar(
        text("select mapping_status from id_mappings where entity_type='horse' and source_id=:s"),
        {"s": CID})
    jockey_map = session.scalar(
        text("select mapping_status from id_mappings where entity_type='jockey' and source_id=:s"),
        {"s": "01209"})
    assert horse_map == MappingStatus.MAPPED
    assert jockey_map == MappingStatus.CONFLICT

    # sticky: re-running does not re-evaluate mapped/conflict
    rep2 = resolve_identities(session)
    assert rep2.resolved["horse"] == 0
    assert rep2.conflicts["jockey"] == 0

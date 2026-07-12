"""Ingest-time identity resolution (Feature 067, T013): resolve_entity must not mint a new
surrogate when a canonical master already exists and evidence corroborates it; evidence-absent
paths and CONFLICT/REJECTED rows stay surrogate/sticky; omitting evidence keeps legacy behavior."""

from __future__ import annotations

import pytest
from horseracing_db.enums import EntityType, MappingStatus, Source
from horseracing_db.models import Horse, IdMapping, Jockey
from sqlalchemy import text

from horseracing_scrape.idmap import resolve_entity

pytestmark = pytest.mark.integration

CID = "2020100734"


def test_canonical_match_returns_canonical_no_surrogate(session):
    session.merge(Horse(horse_id=CID, horse_name="サヴォーナ", birth_year=2020,
                        data_source="jra_van"))
    session.commit()
    got = resolve_entity(session, entity_type="horse", netkeiba_id=CID,
                         candidate_name="サヴォーナ", candidate_birth_year=2020)
    session.commit()
    assert got == CID  # canonical, not nk:
    # a MAPPED mapping was written; no surrogate horse row created
    status = session.scalar(
        text("select mapping_status from id_mappings where entity_type='horse' and source_id=:s"),
        {"s": CID})
    assert status == MappingStatus.MAPPED
    assert session.get(Horse, f"nk:{CID}") is None


def test_evidence_absent_path_stays_surrogate(session):
    # results / pedigree parents call resolve_entity WITHOUT a candidate name → no auto-promotion
    session.merge(Horse(horse_id=CID, horse_name="サヴォーナ", birth_year=2020,
                        data_source="jra_van"))
    session.commit()
    got = resolve_entity(session, entity_type="horse", netkeiba_id=CID)  # no evidence
    session.commit()
    assert got == f"nk:{CID}"
    status = session.scalar(
        text("select mapping_status from id_mappings where source_id=:s"), {"s": CID})
    assert status == MappingStatus.UNMAPPED


def test_conflict_row_is_sticky(session):
    session.merge(Horse(horse_id=CID, horse_name="サヴォーナ", birth_year=2020,
                        data_source="jra_van"))
    session.add(IdMapping(entity_type=EntityType.HORSE, source=Source.NETKEIBA, source_id=CID,
                          mapping_status=MappingStatus.CONFLICT, resolution_note="prior"))
    session.commit()
    got = resolve_entity(session, entity_type="horse", netkeiba_id=CID,
                         candidate_name="サヴォーナ", candidate_birth_year=2020)
    session.commit()
    assert got == f"nk:{CID}"  # sticky — not promoted despite matching evidence
    status = session.scalar(
        text("select mapping_status from id_mappings where source_id=:s"), {"s": CID})
    assert status == MappingStatus.CONFLICT


def test_name_mismatch_does_not_promote(session):
    session.merge(Horse(horse_id=CID, horse_name="べつうま", birth_year=2020,
                        data_source="jra_van"))
    session.commit()
    got = resolve_entity(session, entity_type="horse", netkeiba_id=CID,
                         candidate_name="サヴォーナ", candidate_birth_year=2020)
    session.commit()
    assert got == f"nk:{CID}"  # conflict → surrogate, no false merge


def test_jockey_prefix_promotes(session):
    session.merge(Jockey(jockey_id="05386", jockey_name="戸崎圭太"))
    session.commit()
    got = resolve_entity(session, entity_type="jockey", netkeiba_id="05386",
                         candidate_name="戸崎圭")
    session.commit()
    assert got == "05386"


def test_omitting_evidence_preserves_legacy_new_surrogate(session):
    # brand-new individual (no canonical) → surrogate + UNMAPPED, unchanged legacy behavior
    got = resolve_entity(session, entity_type="horse", netkeiba_id="2099999999",
                         candidate_name="ニューホース", candidate_birth_year=2024)
    session.commit()
    assert got == "nk:2099999999"
    status = session.scalar(
        text("select mapping_status from id_mappings where source_id='2099999999'"))
    assert status == MappingStatus.UNMAPPED

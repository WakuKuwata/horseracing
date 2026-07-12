"""Unit tests for identity-resolution pure functions (Feature 067, T004). Network-free."""

from __future__ import annotations

from dataclasses import dataclass

from horseracing_db.enums import MappingStatus

from horseracing_scrape.identity import classify_identity, normalize_name, strip_markers


@dataclass
class HorseRow:
    horse_name: str | None = None
    birth_year: int | None = None


@dataclass
class PersonRow:
    jockey_name: str | None = None
    trainer_name: str | None = None


def _horse(name, canon_name, cby, jby):
    return classify_identity(
        entity_type="horse", source_id="2020100734", candidate_name=name,
        canonical_row=HorseRow(horse_name=canon_name, birth_year=jby), candidate_birth_year=cby,
    )


def test_horse_exact_name_and_birth_maps():
    r = _horse("サヴォーナ", "サヴォーナ", 2020, 2020)
    assert r.status == MappingStatus.MAPPED
    assert r.canonical_id == "2020100734"


def test_horse_name_mismatch_conflicts():
    assert _horse("サヴォーナ", "べつうま", 2020, 2020).status == MappingStatus.CONFLICT


def test_horse_birth_year_mismatch_conflicts():
    assert _horse("サヴォーナ", "サヴォーナ", 2019, 2020).status == MappingStatus.CONFLICT


def test_horse_missing_name_is_insufficient_unmapped():
    r = _horse(None, "サヴォーナ", 2020, 2020)
    assert r.status == MappingStatus.UNMAPPED
    assert "insufficient" in r.reason


def test_horse_missing_birth_year_is_insufficient_unmapped():
    r = _horse("サヴォーナ", "サヴォーナ", None, 2020)
    assert r.status == MappingStatus.UNMAPPED
    assert "insufficient" in r.reason


def test_no_canonical_is_unmapped():
    r = classify_identity(
        entity_type="horse", source_id="9", candidate_name="X",
        canonical_row=None, candidate_birth_year=2020,
    )
    assert r.status == MappingStatus.UNMAPPED
    assert "no_canonical" in r.reason


def _jockey(name, canon):
    return classify_identity(
        entity_type="jockey", source_id="05386", candidate_name=name,
        canonical_row=PersonRow(jockey_name=canon),
    )


def test_jockey_abbreviated_prefix_maps():
    # netkeiba short name 戸崎圭 is a prefix of JRA-VAN 戸崎圭太
    assert _jockey("戸崎圭", "戸崎圭太").status == MappingStatus.MAPPED
    assert _jockey("江田照", "江田照男").status == MappingStatus.MAPPED


def test_jockey_apprentice_marker_stripped_then_prefix():
    assert _jockey("△長浜", "長浜鴻緒").status == MappingStatus.MAPPED


def test_jockey_abbreviation_scheme_diff_conflicts():
    # netkeiba drops a middle char differently than JV → neither is a prefix of the other
    assert _jockey("石神道", "石神深道").status == MappingStatus.CONFLICT
    assert _jockey("鮫島駿", "鮫島克駿").status == MappingStatus.CONFLICT


def test_trainer_prefix_maps():
    r = classify_identity(
        entity_type="trainer", source_id="01099", candidate_name="友道",
        canonical_row=PersonRow(trainer_name="友道康夫"),
    )
    assert r.status == MappingStatus.MAPPED


def test_person_missing_name_insufficient():
    r = _jockey("", "戸崎圭太")
    assert r.status == MappingStatus.UNMAPPED
    assert "insufficient" in r.reason


def test_normalize_and_strip_markers_boundaries():
    assert normalize_name(None) == ""
    assert normalize_name("　サヴォーナ　") == "サヴォーナ"  # full-width spaces trimmed
    assert strip_markers("△▲☆戸崎") == "戸崎"
    assert strip_markers("＊原") == "原"  # full-width asterisk marker
    assert strip_markers(" 　△ 戸崎 ") == "戸崎"
    assert strip_markers("") == ""
    # NFKC folds full-width alnum
    assert normalize_name("Ｍデムーロ") == "Mデムーロ"

"""2026-11-10: under ``current`` the JRDB writer puts the running style (SED) and the bloodline
lines (UKC) into race_horses / horses in the CURRENT vocabulary (jrdb/vocab.py); JRDB's own codes
still reach only ``jrdb_canonical``. A synthetic translator stands in for the vendor-derived
tables (gitignored)."""

from __future__ import annotations

import dataclasses
import datetime

import pytest
from horseracing_db.models import Horse, RaceHorse
from sqlalchemy import update

from horseracing_scrape.jrdb import upsert_jrdb as W
from horseracing_scrape.jrdb import vocab
from horseracing_scrape.jrdb.ids import horse_id_from_blood_no, race_id_from_key
from tests._jrdb_synth import (
    JST,
    YEAR,
    entries_from_results,
    horses_from_results,
    shifted_programs,
    shifted_results,
)

pytestmark = pytest.mark.integration
OBS = datetime.datetime(2026, 9, 12, 20, 0, tzinfo=JST)
LATER = OBS + datetime.timedelta(hours=1)

#: code 1 → 逃げ, 2 → 先行, 3 → 中団, 4 → 後方 (the real tree also reads the corners)
CODE_TREE = {
    "features": list(vocab.TREE_FEATURES),
    "nodes": [
        {"f": "code", "t": 1.5, "l": 1, "r": 2},
        {"leaf": "逃げ"},
        {"f": "code", "t": 2.5, "l": 3, "r": 4},
        {"leaf": "先行"},
        {"f": "code", "t": 3.5, "l": 5, "r": 6},
        {"leaf": "中団"},
        {"leaf": "後方"},
    ],
}
LABEL = {"1": "逃げ", "2": "先行", "3": "中団", "4": "後方"}


@pytest.fixture
def translator(monkeypatch):
    t = vocab.Translator(tree=CODE_TREE, lines={})
    monkeypatch.setattr(vocab, "translator_or_none", lambda: t)
    return t


def _setup(session, profile, results):
    W.upsert_jrdb_entries(
        session,
        shifted_programs(results),
        entries_from_results(results),
        horses_from_results(results),
        profile=profile,
        year=YEAR,
        observed_at=OBS,
        job_id=None,
        person_map={("jockey", r.jockey_code): f"9{i:04d}" for i, r in enumerate(results)},
        race_status=W.RaceStatus(),
    )
    session.commit()


def _results(session, results, *, profile):
    c = W.upsert_jrdb_results(
        session,
        results,
        [],
        profile=profile,
        edition="formal",
        year=YEAR,
        observed_at=LATER,
        job_id=None,
        race_status=W.RaceStatus(),
    )
    session.commit()
    return c


def _key(r):
    return race_id_from_key(r.key, file_year=YEAR), horse_id_from_blood_no(r.blood_reg_no, race_year=YEAR)


def _style(session, r):
    session.expire_all()
    return session.get(RaceHorse, _key(r)).running_style


def test_current_writes_the_label_over_the_heuristic_and_is_idempotent(session, translator):
    results = shifted_results(races=1, profile="current")
    _setup(session, "current", results)
    # netkeiba's first-corner heuristic already filled every row (the usual order: race night)
    rid = _key(results[0])[0]
    session.execute(update(RaceHorse).where(RaceHorse.race_id == rid).values(running_style="差し"))
    session.commit()
    c = _results(session, results, profile="current")
    assert c.errors == 0
    ran = [r for r in results if r.abnormal_code not in (1, 2)]
    assert [_style(session, r) for r in ran] == [LABEL[r.style_inputs[0]] for r in ran]
    assert c.extra.get("style_written") == sum(LABEL[r.style_inputs[0]] != "差し" for r in ran)
    # a re-ingest writes nothing
    c2 = _results(session, results, profile="current")
    assert not c2.extra.get("style_written") and c2.extra.get("style_unchanged") == len(ran)


def test_scratched_runs_and_runs_without_a_code_keep_their_value(session, translator):
    results = shifted_results(races=1, profile="current")
    _setup(session, "current", results)
    sed = [dataclasses.replace(results[0], abnormal_code=1, finish_order=0),
           dataclasses.replace(results[1], style_inputs=(None, (1, 1, 1)))] + results[2:]
    rid = _key(results[0])[0]
    session.execute(update(RaceHorse).where(RaceHorse.race_id == rid).values(running_style="差し"))
    session.commit()
    c = _results(session, sed, profile="current")
    assert _style(session, results[0]) == "差し" and _style(session, results[1]) == "差し"
    assert c.extra.get("style_no_code") == 1


def test_without_the_tables_nothing_is_translated_and_it_is_reported(session, monkeypatch):
    monkeypatch.setattr(vocab, "translator_or_none", lambda: None)
    results = shifted_results(races=1, profile="current")
    _setup(session, "current", results)
    c = _results(session, results, profile="current")
    assert c.extra.get("style_translation_unavailable") == len(results)
    assert any("translator unavailable" in m for m in c.error_messages)
    assert all(_style(session, r) is None for r in results)


def test_canonical_profile_still_stores_jrdb_codes(session, translator):
    results = shifted_results(races=1)
    _setup(session, "jrdb_canonical", results)
    c = _results(session, results, profile="jrdb_canonical")
    assert c.errors == 0 and not c.extra.get("style_written")
    ran = [r for r in results if r.abnormal_code not in (1, 2) and r.running_style_code]
    assert [_style(session, r) for r in ran] == [r.running_style_code for r in ran]


def test_horse_lines_by_name_first_then_by_code(session, monkeypatch):
    results = shifted_results(races=1, profile="current")
    horses = horses_from_results(results)
    h0, h1 = horses[0], horses[1]
    # a known horse with h0's sire name already carries a line → the name lookup wins
    session.add(Horse(horse_id="2000100001", horse_name="既知馬", sire_name=h0.sire_name,
                      sire_line="ナスルーラ系"))
    session.commit()
    t = vocab.Translator(tree=CODE_TREE, lines={"1206": "ロイヤルチャージャー系"})
    monkeypatch.setattr(vocab, "translator_or_none", lambda: t)
    horses = [dataclasses.replace(h0, line_codes=("1206", None)),
              dataclasses.replace(h1, sire_name="未知の種牡馬", line_codes=("1206", "0000"))] + horses[2:]
    c = W.upsert_jrdb_entries(
        session,
        shifted_programs(results),
        entries_from_results(results),
        horses,
        profile="current",
        year=YEAR,
        observed_at=OBS,
        job_id=None,
        person_map={("jockey", r.jockey_code): f"9{i:04d}" for i, r in enumerate(results)},
        race_status=W.RaceStatus(),
    )
    session.commit()
    assert c.extra.get("line_name_code_disagree") == 1
    hid0 = horse_id_from_blood_no(h0.blood_reg_no, race_year=YEAR)
    hid1 = horse_id_from_blood_no(h1.blood_reg_no, race_year=YEAR)
    session.expire_all()
    assert session.get(Horse, hid0).sire_line == "ナスルーラ系"          # by name (disagrees with code)
    assert session.get(Horse, hid1).sire_line == "ロイヤルチャージャー系"  # by code
    assert session.get(Horse, hid1).damsire_line is None                 # unknown code: nothing


def test_repair_plan_apply_and_rerun(session, monkeypatch, tmp_path):
    """jrdb-vocab-repair: the plan is read only; apply writes the planned rows whose value is
    still the planned old one; a fresh plan afterwards is empty; a stale plan writes nothing."""
    from horseracing_scrape.jrdb import vocab_repair as VR
    from tests._jrdb_synth import write_shifted_archive

    t = vocab.Translator(tree=CODE_TREE, lines={})
    monkeypatch.setattr(vocab, "load_translator", lambda directory=None: t)
    results = shifted_results(races=1, profile="current")
    _setup(session, "current", results)
    rid = _key(results[0])[0]
    session.execute(update(RaceHorse).where(RaceHorse.race_id == rid).values(running_style="差し"))
    session.commit()
    files = write_shifted_archive(tmp_path / "flat", {"SED": YEAR})
    day = results[0].race_date
    (tmp_path / "SED" / day.strftime("%Y%m%d")).mkdir(parents=True)
    files["SED"].rename(tmp_path / "SED" / day.strftime("%Y%m%d") / files["SED"].name)

    plan = VR.build_plan(session, tmp_path, day, day)
    ran = [r for r in results if r.abnormal_code not in (1, 2) and _key(r)[0] == rid]
    want = sum(LABEL[r.style_inputs[0]] != "差し" for r in ran)
    mine = [c for c in plan["style_changes"] if c["race_id"] == rid]
    assert len(mine) == want and all(c["old"] == "差し" for c in mine)
    assert all(_style(session, r) == "差し" for r in ran)  # planning wrote nothing

    out = VR.apply_plan(session, plan)
    session.commit()
    assert out["style_written"] == len(plan["style_changes"]) and out["skipped_changed_since_plan"] == 0
    assert [_style(session, r) for r in ran] == [LABEL[r.style_inputs[0]] for r in ran]
    assert VR.build_plan(session, tmp_path, day, day)["counts"]["style_changes"] == 0
    # the same (now stale) plan again: every row's value is no longer the planned old one
    again = VR.apply_plan(session, plan)
    assert again["style_written"] == 0 and again["skipped_changed_since_plan"] == len(plan["style_changes"])
    # never before the cutover (JRA-VAN values stay)
    with pytest.raises(ValueError):
        VR.build_plan(session, tmp_path, datetime.date(2025, 10, 10), day)

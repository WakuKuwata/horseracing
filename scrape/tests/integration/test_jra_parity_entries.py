"""176 T067 (SC-001): netkeiba (archive) vs official entries for Tokyo 2026-11-01.

The netkeiba side is the archive export (``parity/netkeiba_20261101_tokyo_pre_result.json``, the
newest copy fetched before each race's post time), written through the shared netkeiba writer
with ids as-is. The official page is then written through the official route. Races the official
route quarantines are "not comparable" and kept out of the denominator; comparability and
agreement are asserted separately.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest
from horseracing_db.enums import EntryStatus, Source
from horseracing_db.models import RaceHorse
from sqlalchemy import select

from horseracing_scrape.jra.parse.changes import parse_changes
from horseracing_scrape.jra.parse.entries import parse_entries_day
from horseracing_scrape.jra.upsert_jra import upsert_jra_entries
from horseracing_scrape.models import ResolvedEntryHorse
from horseracing_scrape.upsert import write_entries
from tests.jra_fixtures import fetched_at, jra_text

pytestmark = pytest.mark.integration
D = datetime.date(2026, 11, 1)
FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "jra"
    / "parity"
    / "netkeiba_20261101_tokyo_pre_result.json"
)
FIELDS = ("horse_id", "jockey_id", "trainer_id", "jockey_weight")


def _seed_netkeiba(session, doc):
    for rec in doc["races"]:
        rid = rec["race_id"]
        r = rec["race"]
        values = dict(
            race_date=D,
            race_number=int(rid[-2:]),
            venue_code=rid[4:6],
            distance=r["distance"],
            track_type=r["track_type"],
            going=None,
            weather=None,
            race_class=r["race_class"],
            race_name=None,
            grade=None,
            post_time=datetime.datetime.fromisoformat(r["post_time"]) if r["post_time"] else None,
            prize_money=None,
        )
        horses = [
            ResolvedEntryHorse(
                horse_id=h["netkeiba_horse_id"],
                horse_name=h["horse_name"],
                frame=h["frame"],
                horse_number=h["horse_number"],
                jockey_id=h["netkeiba_jockey_id"],
                jockey_name=h["jockey_name"],
                trainer_id=h["netkeiba_trainer_id"],
                trainer_name=h["trainer_name"],
                weight=h["weight"],
                weight_diff=h["weight_diff"],
                jockey_weight=h["jockey_weight"],
                sex=h["sex"],
                age=h["age"],
                entry_status=h["entry_status"],
                birth_year=None,
                data_source=Source.NETKEIBA,
            )
            for h in rec["horses"]
        ]
        write_entries(
            session,
            rid,
            values,
            horses,
            source=Source.NETKEIBA,
            observed_at=datetime.datetime.fromisoformat(rec["archived_at"]),
            job_id=None,
            page_kind="entries",
        )
    session.commit()


def _nk_rows(rec):
    return {
        h["horse_number"]: (
            h["netkeiba_horse_id"],
            h["netkeiba_jockey_id"],
            h["netkeiba_trainer_id"],
            h["jockey_weight"],
            h["entry_status"],
        )
        for h in rec["horses"]
    }


def test_entries_parity_tokyo_20261101(session):
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    _seed_netkeiba(session, doc)
    page = parse_entries_day(jra_text("entries_tokyo_20261101.sjis.html"), expected_date=D)
    changes = parse_changes(jra_text("changes_20261101.sjis.html"), expected_date=D)
    c = upsert_jra_entries(
        session, page, changes, observed_at=fetched_at("entries_tokyo_20261101.sjis.html")
    )
    session.commit()
    quarantined = {
        m.split()[1].rstrip(":") for m in c.error_messages if m.startswith("quarantined")
    }
    nk = {rec["race_id"]: _nk_rows(rec) for rec in doc["races"]}

    # cancellations announced on the changes page after the netkeiba capture are a time
    # difference, not a disagreement: out of the denominator, but the official status must win
    announced = {
        (ch.race_id, ch.horse_number) for ch in changes if ch.kind in ("cancel", "exclude")
    }
    comparable = mismatched = matched = 0
    excluded_timing = []
    examples = []
    for block in page.published():
        rid = block.race_id
        if rid in quarantined or rid not in nk:
            continue
        stored_num = {
            rh.horse_id: rh.horse_number
            for rh in session.scalars(select(RaceHorse).where(RaceHorse.race_id == rid))
        }
        official = {}
        for h in block.payload.horses:
            num = h.horse_number if h.horse_number is not None else stored_num.get(h.horse_id)
            official[num] = (h.horse_id, h.jockey_id, h.trainer_id, h.jockey_weight, h.entry_status)
        # the field (馬番 → horse) and per-horse fields
        assert set(official) == set(nk[rid]), (rid, sorted(set(official) ^ set(nk[rid])))
        for num, o in official.items():
            for i, f in enumerate((*FIELDS, "entry_status")):
                if f == "entry_status" and (rid, num) in announced and o[i] != nk[rid][num][i]:
                    excluded_timing.append((rid, num))
                    continue
                comparable += 1
                if o[i] == nk[rid][num][i]:
                    matched += 1
                else:
                    mismatched += 1
                    examples.append((rid, num, f, nk[rid][num][i], o[i]))
    n_comparable_races = len([b for b in page.published() if b.race_id not in quarantined])
    print(
        f"\nSC-001: races comparable {n_comparable_races}/12 quarantined {sorted(quarantined)} "
        f"fields {matched}/{comparable} excluded_timing {excluded_timing} examples {examples[:10]}"
    )
    assert n_comparable_races >= 1
    assert mismatched == 0, examples[:20]
    # the excluded ones are exactly the announced cancellations, and the official status was adopted
    assert excluded_timing and set(excluded_timing) <= announced
    for rid, num in excluded_timing:
        rh = session.scalars(
            select(RaceHorse).where(RaceHorse.race_id == rid, RaceHorse.horse_number == num)
        ).one()
        assert rh.entry_status == EntryStatus.CANCELLED
    # no nk: row was created by the official route
    assert not [rh for rh in session.scalars(select(RaceHorse)) if rh.horse_id.startswith("nk:")]

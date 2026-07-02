"""Feature 047: segment-wise p vs q diagnostics — pre-registered band functions (boundaries),
per-axis reconciliation (Σn == total), determinism, and the attribute leak boundary
(changing a race's RESULT must not change segment assignment).
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import RaceResult
from sqlalchemy import update

from horseracing_eval.segment_edge import (
    class_group,
    dist_band,
    evaluate_segment_edge,
    field_band,
    q_band,
    surface_band,
)
from tests._fakepredictor import FakePredictor
from tests._synth import insert_race, make_informative_field

pytestmark = pytest.mark.integration


# --- pre-registered band functions (boundary values fixed by specs/047) -----

def test_band_boundaries():
    assert dist_band(1400) == "sprint(<=1400)" and dist_band(1401) == "mile(<=1800)"
    assert dist_band(1800) == "mile(<=1800)" and dist_band(2200) == "mid(<=2200)"
    assert dist_band(2201) == "long(>2200)" and dist_band(None) == "unknown"
    assert q_band(0.049) == "q<0.05(穴)" and q_band(0.05) == "0.05-0.15"
    assert q_band(0.299) == "0.15-0.30" and q_band(0.30) == "q>=0.30(本命)"
    assert surface_band("芝") == "芝" and surface_band("ダート") == "ダート"
    assert surface_band(None) == "unknown"
    assert class_group("2歳新馬") == "新馬" and class_group("3歳未勝利") == "未勝利"
    assert class_group("オープン") == "OP系" and class_group("G1") == "OP系"
    assert class_group("2勝クラス") == "条件" and class_group(None) == "unknown"
    assert field_band(8) == "small(<=8)" and field_band(9) == "mid(9-13)"
    assert field_band(13) == "mid(9-13)" and field_band(14) == "large(>=14)"


def _seed(session, years=(2007, 2008), races_per_year=6, field=8):
    for year in years:
        for r in range(1, races_per_year + 1):
            insert_race(
                session, race_id=f"{year}0101{r:02d}01",
                race_date=datetime.date(year, 1, 1) + datetime.timedelta(days=r),
                horses=make_informative_field(field, winner=r % field),
            )
    session.commit()


def test_reconciliation_and_determinism(session):
    _seed(session)
    r1 = evaluate_segment_edge(session, predictor=FakePredictor(skill=2.0))
    assert r1.n_horses > 0
    # every axis partitions ALL samples: Σn == total (SC-001)
    for axis in ("surface", "dist_band", "q_band", "race_class", "field_size", "debut"):
        total = sum(row.n for row in r1.rows if row.axis == axis)
        assert total == r1.n_horses, axis
    # deterministic (SC-002)
    r2 = evaluate_segment_edge(session, predictor=FakePredictor(skill=2.0))
    assert [(x.axis, x.segment, x.n) for x in r1.rows] == \
           [(x.axis, x.segment, x.n) for x in r2.rows]
    assert "SECONDARY" in r1.note  # never a gate


def test_result_change_does_not_move_segment_assignment(session):
    # SC-003: segments derive from race statics / prior entries / q — NEVER the race's result.
    _seed(session)
    before = evaluate_segment_edge(session, predictor=FakePredictor(skill=2.0))
    # flip every finish_order in one 2008 race (results change; statics unchanged)
    session.execute(
        update(RaceResult).where(RaceResult.race_id == "200801010101")
        .values(finish_order=RaceResult.finish_order + 50)
    )
    session.commit()
    after = evaluate_segment_edge(session, predictor=FakePredictor(skill=2.0))
    # per-segment n (assignment) identical; only win_rate may move
    assert [(x.axis, x.segment, x.n) for x in before.rows] == \
           [(x.axis, x.segment, x.n) for x in after.rows]

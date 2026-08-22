"""Feature 097 (T019): the pseudo-supply-death mask touches first_3f and nothing else.

Contract (contracts/adoption-gate.md): on/after the cutoff every non-1200m first_3f becomes NULL;
1200m rows and pre-cutoff rows are untouched; no other column and no row count changes; the
symmetry snapshot (scored races / started set / winners) is identical before and after; and the
provenance hash changes exactly when a value that the mask should null still carries a value.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Horse, Race, RaceHorse, RaceResult
from horseracing_eval.provenance import frame_projection_hash
from sqlalchemy import text

from horseracing_training.supply_mask import (
    PROVENANCE_COLS,
    apply_first3f_mask,
    projection_rows,
    provenance_violations,
    symmetry_snapshot,
)

CUTOFF = datetime.date(2022, 1, 1)


def _seed(session):
    rows = [  # (race_id, date, distance, first_3f)
        ("202101010101", datetime.date(2021, 6, 1), 1600, "35.1"),   # pre-cutoff: keep
        ("202201010101", datetime.date(2022, 6, 1), 1600, "35.2"),   # post, non-1200: mask
        ("202201010102", datetime.date(2022, 6, 1), 1200, "34.9"),   # post, 1200: keep
        ("202301010101", datetime.date(2023, 6, 1), 2000, "36.0"),   # post, non-1200: mask
        ("202301010102", datetime.date(2023, 6, 1), None, "35.5"),   # post, unknown dist: mask
    ]
    session.add(Horse(horse_id="H1", horse_name="h1", data_source="jra_van"))
    for rid, d, dist, f3 in rows:
        session.add(Race(race_id=rid, race_number=1, race_date=d, venue_code=rid[4:6],
                         distance=dist))
        session.add(RaceHorse(race_id=rid, horse_id="H1", horse_number=1,
                              entry_status=EntryStatus.STARTED))
        session.add(RaceResult(race_id=rid, horse_id="H1", finish_order=1,
                               result_status=ResultStatus.FINISHED, first_3f=Decimal(f3),
                               last_3f=Decimal("34.0")))
    session.flush()


def _first3f(session):
    return {r[0]: r[1] for r in session.execute(
        text("select race_id, first_3f from race_results order by race_id"))}


def test_mask_nulls_only_post_cutoff_non_1200m(session):
    _seed(session)
    before = _first3f(session)
    n = apply_first3f_mask(session, CUTOFF)
    after = _first3f(session)
    assert n == 3
    assert after["202101010101"] == before["202101010101"]        # pre-cutoff untouched
    assert after["202201010102"] == before["202201010102"]        # 1200m untouched
    assert after["202201010101"] is None
    assert after["202301010101"] is None
    assert after["202301010102"] is None                          # unknown distance is masked too
    assert provenance_violations(session, CUTOFF) == 0


def test_mask_changes_nothing_else(session):
    _seed(session)
    other_before = list(session.execute(text(
        "select race_id, horse_id, finish_order, result_status, last_3f from race_results "
        "order by race_id")))
    n_rows = session.execute(text("select count(*) from race_results")).scalar()
    sym_before = symmetry_snapshot(session)
    apply_first3f_mask(session, CUTOFF)
    other_after = list(session.execute(text(
        "select race_id, horse_id, finish_order, result_status, last_3f from race_results "
        "order by race_id")))
    assert other_after == other_before
    assert session.execute(text("select count(*) from race_results")).scalar() == n_rows
    assert symmetry_snapshot(session) == sym_before                # races / started / winners


def test_mask_is_idempotent(session):
    _seed(session)
    assert apply_first3f_mask(session, CUTOFF) == 3
    assert apply_first3f_mask(session, CUTOFF) == 0


def test_provenance_hash_sees_the_mask(session):
    """A rerun that silently read unmasked bytes would reproduce the pre-mask hash."""
    _seed(session)
    h_before = frame_projection_hash(projection_rows(session), PROVENANCE_COLS)
    assert provenance_violations(session, CUTOFF) == 2 + 1      # the three rows still carrying a value
    apply_first3f_mask(session, CUTOFF)
    h_after = frame_projection_hash(projection_rows(session), PROVENANCE_COLS)
    assert h_after != h_before
    # stable across reads of the same state (what the two arms must agree on)
    assert frame_projection_hash(projection_rows(session), PROVENANCE_COLS) == h_after


def test_projection_hash_row_order_independent():
    cols = list(PROVENANCE_COLS)
    a = [("1", "h", datetime.date(2022, 1, 1), 1600, Decimal("35.2")),
         ("2", "h", datetime.date(2022, 1, 2), 1200, None)]
    assert frame_projection_hash(a, cols) == frame_projection_hash(list(reversed(a)), cols)
    b = [a[0], ("2", "h", datetime.date(2022, 1, 2), 1200, Decimal("34.0"))]
    assert frame_projection_hash(a, cols) != frame_projection_hash(b, cols)

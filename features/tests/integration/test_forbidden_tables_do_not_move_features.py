"""Feature 176 T088 (SC-008, dynamic): rows in the provenance tables (0023) and in
``jra_track_conditions`` (0024) do not move a single feature value nor the source fingerprint.

(a) provenance 2 tables, (b) the track-condition table — the same procedure on one seeded DB.
The before/after comparison of the live DB (active predictions + parquet fingerprint on a fixed
race set) is the rollout step T100/T106, not this test.
"""

from __future__ import annotations

import datetime

import pytest
from pandas.testing import assert_frame_equal
from sqlalchemy import text

from horseracing_features.builder import build_feature_matrix
from horseracing_features.loader import load_frames
from horseracing_features.materialize import source_fingerprint
from tests._synth import insert_run

pytestmark = pytest.mark.integration
H1, H2 = "2020100001", "2020100002"
JST = datetime.timezone(datetime.timedelta(hours=9))


def _seed(session):
    insert_run(session, race_id="202605040901", race_date=datetime.date(2026, 10, 4), horse_id=H1, finish_order=1)
    insert_run(session, race_id="202605040901", race_date=datetime.date(2026, 10, 4), horse_id=H2, finish_order=2)
    insert_run(session, race_id="202605041001", race_date=datetime.date(2026, 10, 31), horse_id=H1, finish_order=3)
    insert_run(session, race_id="202605041001", race_date=datetime.date(2026, 10, 31), horse_id=H2, finish_order=1)
    session.commit()


def _snapshot(session):
    fm = build_feature_matrix(session, start_date=datetime.date(2026, 1, 1))
    fm = fm.sort_values(["race_id", "horse_id"]).reset_index(drop=True)
    return fm, source_fingerprint(load_frames(session))


def _assert_unchanged(session, before):
    fm, fp = _snapshot(session)
    assert fp == before[1]
    assert_frame_equal(before[0], fm, check_exact=True, check_dtype=True)


def test_provenance_rows_do_not_move_features(session):
    _seed(session)
    before = _snapshot(session)
    now = datetime.datetime(2026, 10, 31, 20, 0, tzinfo=JST)
    for rid in ("202605040901", "202605041001"):
        for group in ("race_static", "post_time", "results", "win_odds", "win_payouts"):
            session.execute(text(
                "INSERT INTO race_field_provenance (race_id, field_group, source, official_at, observed_at) "
                "VALUES (:r, :g, 'jra_official', :o, :t)"),
                {"r": rid, "g": group, "t": now, "o": now if group == "win_odds" else None})
        for hid in (H1, H2):
            for group in ("entry_static", "jockey", "trainer", "body_weight", "entry_status"):
                session.execute(text(
                    "INSERT INTO race_horse_field_provenance (race_id, horse_id, field_group, source, observed_at) "
                    "VALUES (:r, :h, :g, 'jra_official', :t)"), {"r": rid, "h": hid, "g": group, "t": now})
    session.commit()
    _assert_unchanged(session, before)


def test_track_condition_rows_do_not_move_features(session):
    _seed(session)
    before = _snapshot(session)
    t = datetime.datetime(2026, 10, 31, 7, 0, tzinfo=JST)
    rows = [("cushion_turf", 9.6, None, "measured"), ("moisture_turf_goal", 12.9, "hard", "measured"),
            ("going_turf", None, "良", "fetched")]
    for item, num, txt, basis in rows:
        session.execute(text(
            "INSERT INTO jra_track_conditions (venue_code, measured_at, item, time_basis, value_num, "
            "value_text, fetched_at, raw_sha256) VALUES ('05', :m, :i, :b, :n, :x, :f, 'sha')"),
            {"m": t, "i": item, "b": basis, "n": num, "x": txt, "f": t + datetime.timedelta(hours=1)})
    session.commit()
    _assert_unchanged(session, before)

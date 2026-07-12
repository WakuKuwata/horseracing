"""Split-repair parity (Feature 067, T010).

Re-keying a netkeiba surrogate's (2026) rows onto the canonical horse must NOT change any
pre-cutoff (pre-2025) feature row — as-of aggregation is strictly-before, so races merged in from
2026 sit *after* every pre-2025 race and cannot enter their as-of window (FR-017). The merge DOES
restore the recent race's history (was empty debut-style, now reflects the real career): the
silent-degradation fix (SC-002).
"""

from __future__ import annotations

import datetime

import pytest
from pandas.testing import assert_frame_equal
from sqlalchemy import text

from horseracing_features.builder import build_feature_matrix
from tests._synth import insert_run

pytestmark = pytest.mark.integration

C = "2020100734"          # canonical (JRA-VAN)
S = "nk:2020100734"       # netkeiba surrogate (same horse, split)


def _seed(session):
    # canonical career (pre-2025)
    insert_run(session, race_id="202206040901", race_date=datetime.date(2022, 10, 2),
               horse_id=C, finish_order=5)
    insert_run(session, race_id="202206040902", race_date=datetime.date(2023, 1, 15),
               horse_id=C, finish_order=1)          # pre-cutoff target row
    insert_run(session, race_id="202306040903", race_date=datetime.date(2023, 7, 16),
               horse_id=C, finish_order=2)
    # recent race under the surrogate (2026) — currently a separate "debut-style" history
    insert_run(session, race_id="202603010211", race_date=datetime.date(2026, 4, 12),
               horse_id=S, finish_order=3)


def test_pre_cutoff_features_unchanged_and_recent_history_restored(session):
    _seed(session)
    before = build_feature_matrix(session, start_date=datetime.date(2022, 1, 1))
    pre_before = before[before.race_id == "202206040902"].reset_index(drop=True)
    recent_before = before[before.race_id == "202603010211"].iloc[0]
    # before repair, the 2026 race sees NO career history (the split degradation)
    assert recent_before.career_starts == 0

    # simulate the physical re-key that repair_splits performs
    session.execute(text("update race_results set horse_id=:c where horse_id=:s"), {"c": C, "s": S})
    session.execute(text("update race_horses set horse_id=:c where horse_id=:s"), {"c": C, "s": S})
    session.execute(text("delete from horses where horse_id=:s"), {"s": S})
    session.commit()

    after = build_feature_matrix(session, start_date=datetime.date(2022, 1, 1))
    pre_after = after[after.race_id == "202206040902"].reset_index(drop=True)
    recent_after = after[after.race_id == "202603010211"].iloc[0]

    # pre-cutoff row byte-identical (strictly-before → 2026 races never enter its as-of window)
    assert_frame_equal(pre_before, pre_after, check_exact=True, check_dtype=True)
    # recent race history restored: now reflects the 3 prior canonical starts
    assert recent_after.career_starts == 3

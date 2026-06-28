"""US1 (SC-002/005): an unmapped (nk:) horse is debut/Unknown in features — no cross-horse leak."""

from __future__ import annotations

import datetime

import pytest
from horseracing_features.builder import build_feature_matrix

from horseracing_scrape.pipeline import scrape_entries
from tests._synth import H_NUM1, H_WINNER, REAL_RID, real_entries_fetcher, seed_finished_race

pytestmark = pytest.mark.integration


def test_unmapped_horse_is_debut(session):
    # prior JRA-VAN finished race (realistic history present) for a DIFFERENT horse
    seed_finished_race(session, race_id="202405020311", horse_id="2019000099",
                       race_date=datetime.date(2024, 6, 1))
    fetcher, urls = real_entries_fetcher()
    scrape_entries(session, urls=urls, fetcher=fetcher,  # all 18 unmapped (nk:)
                   complete_profiles_after=False)

    fm = build_feature_matrix(session)
    rows = fm[fm["race_id"] == REAL_RID].set_index("horse_id")
    assert len(rows) == 18  # all started
    for hid in (H_NUM1, H_WINNER):
        assert int(rows.loc[hid, "career_starts"]) == 0   # debut, not a leaked history
        assert int(rows.loc[hid, "is_debut"]) == 1

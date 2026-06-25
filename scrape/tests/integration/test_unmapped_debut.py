"""US1 (SC-002): an unmapped (nk:) horse is debut/Unknown in features — no cross-horse leak."""

from __future__ import annotations

import datetime

import pytest
from horseracing_features.builder import build_feature_matrix

from horseracing_scrape.pipeline import scrape_entries
from tests._synth import RACE_ID, fixture_fetcher, seed_finished_race

pytestmark = pytest.mark.integration


def test_unmapped_horse_is_debut(session):
    # prior JRA-VAN finished race (realistic history present) for a different horse
    seed_finished_race(session, race_id="202405020311", horse_id="2019000099",
                       race_date=datetime.date(2024, 6, 1))
    fetcher, urls = fixture_fetcher("entries")
    scrape_entries(session, urls=urls, fetcher=fetcher)  # all horses unmapped (nk:)

    fm = build_feature_matrix(session)
    rows = fm[fm["race_id"] == RACE_ID].set_index("horse_id")
    # started population only (H003 cancelled excluded); surrogates have no prior races -> debut
    assert set(rows.index) == {"nk:H001", "nk:H002"}
    for hid in ("nk:H001", "nk:H002"):
        assert int(rows.loc[hid, "career_starts"]) == 0   # debut, not a leaked history
        assert int(rows.loc[hid, "is_debut"]) == 1

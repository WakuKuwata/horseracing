"""US1 (FR-005/003): build_race_id valid-or-None; surrogate non-collision (DB-free)."""

from __future__ import annotations

from horseracing_db.validation import is_valid_race_id

from horseracing_scrape.idmap import surrogate_id
from horseracing_scrape.venues import build_race_id


def test_build_race_id_valid():
    rid = build_race_id(year=2025, track_code="05", kai=2, nichime=3, race_no=11)
    assert rid == "202505020311" and is_valid_race_id(rid)


def test_build_race_id_unknown_venue_is_none():
    assert build_race_id(year=2025, track_code="99", kai=1, nichime=1, race_no=1) is None


def test_build_race_id_pre_2007_is_none():
    assert build_race_id(year=2006, track_code="05", kai=1, nichime=1, race_no=1) is None


def test_surrogate_unique_and_non_colliding():
    a, b = surrogate_id("H001"), surrogate_id("H002")
    assert a != b                      # distinct netkeiba ids -> distinct surrogate (no history share)
    assert a.startswith("nk:")
    assert not is_valid_race_id(a)     # never a 12-digit JRA-VAN id
    assert not a[3:].startswith("nk:") and ":" in a  # namespaced, can't be a numeric canonical id

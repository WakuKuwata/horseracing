"""Feature 034: race-level lap parser, tested network-free on a saved real db.netkeiba fixture."""

from __future__ import annotations

from pathlib import Path

from horseracing_scrape.parse.laps import parse_laps

_FIX = Path(__file__).resolve().parents[1] / "fixtures" / "real" / "db_race_202406050911.html"


def _html() -> str:
    return _FIX.read_text(encoding="utf-8")


def test_parses_lap_profile_from_real_fixture():
    r = parse_laps(_html(), race_id="202406050911")
    assert r is not None
    # 2000m race → 10 × 200m segments
    assert len(r.lap_times) == 10
    assert r.lap_times[0] == 12.6 and r.lap_times[-1] == 11.9
    # テン3F / 上がり3F split from the "(36.0-35.5)" annotation
    assert r.pace_first_3f == 36.0 and r.pace_last_3f == 35.5
    # lap sum matches the final cumulative pace value (120.5)
    assert round(sum(r.lap_times), 1) == 120.5


def test_key_from_race_id():
    r = parse_laps(_html(), race_id="202406050911")
    assert (r.key.year, r.key.track_code, r.key.race_no) == (2024, "06", 11)


def test_returns_none_without_lap_table():
    # a page fragment with no ラップタイム table → None (race with no recorded sectionals)
    assert parse_laps("<html><body>no laps here</body></html>", race_id="202406050911") is None

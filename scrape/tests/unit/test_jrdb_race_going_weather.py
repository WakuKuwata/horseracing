"""178: race-level 馬場状態/天候 from SED rows — majority, deterministic ties, split flag."""

from types import SimpleNamespace

from horseracing_scrape.jrdb.upsert_jrdb import race_going_weather


def _r(going, weather):
    return SimpleNamespace(going_code=going, weather_code=weather)


def test_unanimous_rows():
    gw, split = race_going_weather([_r(11, 1), _r(10, 1), _r(12, 1)])
    assert gw == {"going": "良", "weather": "晴"} and split is False


def test_majority_wins_and_split_is_flagged():
    gw, split = race_going_weather([_r(20, 2), _r(20, 2), _r(30, 4)])
    assert gw == {"going": "稍重", "weather": "曇"} and split is True


def test_ties_are_deterministic_and_missing_is_none():
    gw, _ = race_going_weather([_r(10, None), _r(40, None)])
    assert gw["going"] == min("良", "不良") and gw["weather"] is None
    assert race_going_weather([]) == ({"going": None, "weather": None}, False)

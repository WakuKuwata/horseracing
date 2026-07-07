"""Feature 058 US1: past-market leak boundary — target-race / same-day / future invariance +
a POSITIVE test (past popularity DOES feed the features) + clean naming.

058 is the repo's first feature that INTENTIONALLY uses market data (popularity), so the classic
"no odds/popularity token in the module source" grep guard is NOT applied here. The real protection
is behavioural: the TARGET race's popularity must never change its own features (strictly-before),
while a PAST race's popularity must.
"""

from __future__ import annotations

import pandas as pd

from horseracing_features.past_market_features import (
    PAST_MARKET_COLUMNS,
    build_past_market_features,
)
from tests._frames import make_frames

_TARGET = "200803010101"


def _specs():
    # H has one PAST start (2008-01-01, pop=3, 1st) then the TARGET race (2008-03-01, pop=5).
    return [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "popularity": 3, "finish_order": 1},
            {"horse_id": "X", "horse_number": 2, "popularity": 1, "finish_order": 2}]},
        {"race_id": _TARGET, "race_date": "2008-03-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "popularity": 5, "finish_order": 1},
            {"horse_id": "X", "horse_number": 2, "popularity": 2, "finish_order": 2}]},
    ]


def _target_rows(frames):
    out = build_past_market_features(frames)
    return out[out.race_id == _TARGET].set_index("horse_id").sort_index()


def _assert_same(a, b):
    pd.testing.assert_frame_equal(a, b, check_exact=True)


def test_invariant_to_own_current_race_popularity():
    # INV-L1: mutating the TARGET race's own popularity/finish must not change its features.
    base = _target_rows(make_frames(_specs()))
    specs = _specs()
    specs[1]["horses"][0]["popularity"] = 18
    specs[1]["horses"][0]["finish_order"] = 18
    mutated = _target_rows(make_frames(specs))
    _assert_same(base, mutated)


def test_invariant_to_same_day_other_race():
    # INV-L2: a same-day race (allow_exact_matches=False) must not leak in.
    specs = _specs() + [
        {"race_id": "200803010102", "race_date": "2008-03-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "popularity": 1, "finish_order": 1},
            {"horse_id": "Y", "horse_number": 2, "popularity": 2, "finish_order": 2}]},
    ]
    base = _target_rows(make_frames(_specs()))
    with_sameday = _target_rows(make_frames(specs))
    _assert_same(base, with_sameday.loc[base.index])


def test_invariant_to_future_race():
    # INV-L3: adding a FUTURE race must not change past rows (pool-end independent).
    specs = _specs() + [
        {"race_id": "200812010101", "race_date": "2008-12-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "popularity": 1, "finish_order": 1},
            {"horse_id": "X", "horse_number": 2, "popularity": 2, "finish_order": 2}]},
    ]
    base = _target_rows(make_frames(_specs()))
    with_future = _target_rows(make_frames(specs))
    _assert_same(base, with_future)


def test_past_popularity_actually_feeds_features():
    # INV-P1 (positive): changing a PAST race's popularity MUST change the target's features —
    # proves the aggregation genuinely uses past market rank (not a no-op).
    base = _target_rows(make_frames(_specs()))
    specs = _specs()
    specs[0]["horses"][0]["popularity"] = 12  # H's PAST popularity 3 -> 12
    changed = _target_rows(make_frames(specs))
    assert base.loc["H", "asof_mkt_rank_avg"] != changed.loc["H", "asof_mkt_rank_avg"]


def test_debut_horse_is_nan_not_zero():
    # A horse with NO past start must get NaN past-market features (Unknown != 0).
    # X's only prior start is 2008-01-01, so X HAS history; use a truly debut horse instead:
    specs = _specs()
    specs[1]["horses"].append({"horse_id": "D", "horse_number": 3, "popularity": 4,
                               "finish_order": 3})
    out2 = build_past_market_features(make_frames(specs))
    d = out2[(out2.race_id == _TARGET) & (out2.horse_id == "D")]
    assert d[PAST_MARKET_COLUMNS].isna().all(axis=None)


def test_columns_clean_names_and_registry_group():
    from horseracing_features.registry import FEATURE_GROUPS

    for name in PAST_MARKET_COLUMNS:
        low = name.lower()
        for tok in ("odds", "popularity", "payout", "dividend", "result", "finish_order"):
            assert tok not in low, (name, tok)
    cols = [c for c, g in FEATURE_GROUPS.items() if g == "past_market"]
    assert sorted(cols) == sorted(PAST_MARKET_COLUMNS)

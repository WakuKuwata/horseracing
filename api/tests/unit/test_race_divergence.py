"""Feature 066 US2 axis B: build_race_divergence (pure, no DB).

Covers: canonical_consistent=false / q-missing suppression; the F2 favourite-direction mapping;
F6 top-3 rank agreement; underrated-longshot facts; model_version passthrough (057); and that the
per-horse 040 divergence_band vocabulary is reused unchanged (no re-labelling).
"""

from __future__ import annotations

from horseracing_api.dispersion import build_race_divergence

# favourite (horse 1) has model p WELL BELOW market q -> divergence_band = market_higher -> the
# F2 mapping yields favorite_direction = model_lower. Horse 4 is a market longshot the model likes.
_P = {1: 0.20, 2: 0.25, 3: 0.15, 4: 0.30, 5: 0.10}
_Q = {1: 0.45, 2: 0.25, 3: 0.15, 4: 0.05, 5: 0.10}


def test_suppressed_when_canonical_inconsistent():
    d = build_race_divergence(pmap=_P, qmap=_Q, canonical_consistent=False, model_version="m")
    assert d.available is False
    assert d.summary is None and d.favorite_direction is None
    assert d.underrated_longshots == [] and d.rank_agreement is None
    assert d.model_version == "m"


def test_suppressed_when_q_missing():
    d = build_race_divergence(pmap=_P, qmap={}, canonical_consistent=True, model_version="m")
    assert d.available is False


def test_favorite_direction_maps_market_higher_to_model_lower():
    d = build_race_divergence(pmap=_P, qmap=_Q, canonical_consistent=True, model_version="lgbm-x")
    assert d.available is True
    assert d.favorite_direction == "model_lower"  # F2: market_higher -> model_lower
    assert "低く評価" in (d.summary or "")
    assert d.model_version == "lgbm-x"


def test_underrated_longshot_is_a_neutral_fact():
    d = build_race_divergence(pmap=_P, qmap=_Q, canonical_consistent=True, model_version="m")
    # horse 4: model p=0.30 (its top pick) but market popularity_rank 5 (q=0.05) -> surfaced.
    nums = {ls.horse_number for ls in d.underrated_longshots}
    assert 4 in nums
    ls4 = next(ls for ls in d.underrated_longshots if ls.horse_number == 4)
    assert ls4.popularity_rank == 5 and ls4.p == 0.30 and ls4.q == 0.05


def test_rank_agreement_is_top3_set_overlap_over_three():
    # model top3 by p = {4,2,1}; market top3 by q = {1,2,3}; overlap {1,2} -> 2/3.
    d = build_race_divergence(pmap=_P, qmap=_Q, canonical_consistent=True, model_version="m")
    assert d.rank_agreement == 2 / 3


def test_similar_when_favorite_p_matches_q():
    p = {1: 0.44, 2: 0.30, 3: 0.26}
    q = {1: 0.45, 2: 0.30, 3: 0.25}
    d = build_race_divergence(pmap=p, qmap=q, canonical_consistent=True, model_version="m")
    assert d.favorite_direction == "similar"
    assert d.underrated_longshots == []  # no model-top3 horse outside market top3


def test_no_profit_or_buy_wording_in_summary():
    d = build_race_divergence(pmap=_P, qmap=_Q, canonical_consistent=True, model_version="m")
    for bad in ("買", "妙味", "危険", "儲", "おすすめ", "推奨"):
        assert bad not in (d.summary or "")
